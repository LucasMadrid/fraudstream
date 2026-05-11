"""TDD tests for AlertKafkaSink.

AlertKafkaSink serialises FraudAlert to Avro and produces to txn.fraud.alerts.
DLQ failures go to txn.fraud.alerts.dlq.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from confluent_kafka import KafkaException

from pipelines.scoring.types import FraudAlert


def _make_alert(**kwargs) -> FraudAlert:
    defaults = dict(
        transaction_id="txn-001",
        account_id="acc-001",
        matched_rule_names=["VEL-001"],
        severity="high",
        evaluation_timestamp=1_700_000_000_000,
    )
    defaults.update(kwargs)
    return FraudAlert(**defaults)


class TestAlertKafkaSinkInit:
    def test_imports_without_error(self):
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink  # noqa: F401

    def test_instantiates_with_config(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        assert sink is not None


class TestAlertKafkaSinkEmit:
    def test_emit_calls_producer_produce(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        mock_producer = MagicMock()
        sink._producer = mock_producer

        sink.emit(alert)
        mock_producer.produce.assert_called_once()

    def test_emit_uses_correct_topic(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        mock_producer = MagicMock()
        sink._producer = mock_producer

        sink.emit(alert)
        call_kwargs = mock_producer.produce.call_args
        topic = call_kwargs[1].get("topic") or call_kwargs[0][0]
        assert topic == config.fraud_alerts_topic

    def test_emit_uses_transaction_id_as_key(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert(transaction_id="txn-key-test")

        mock_producer = MagicMock()
        sink._producer = mock_producer

        sink.emit(alert)
        call_kwargs = mock_producer.produce.call_args
        key = call_kwargs[1].get("key")
        assert key == b"txn-key-test"

    def test_emit_on_delivery_error_routes_to_dlq(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        # Simulate delivery error callback
        mock_err = MagicMock()
        mock_err.__bool__ = lambda self: True
        mock_msg = MagicMock()

        mock_producer = MagicMock()
        sink._producer = mock_producer

        # Call the on_delivery callback with an error
        sink._on_delivery(mock_err, mock_msg, alert)
        mock_producer.produce.assert_called_once()
        call_kwargs = mock_producer.produce.call_args
        topic = call_kwargs[1].get("topic") or call_kwargs[0][0]
        assert topic == config.fraud_alerts_dlq_topic


class TestAlertKafkaSinkBackPressure:
    """T040 — US3 AC-3: Kafka producer failure must propagate (FR-010).

    Intentional asymmetry:
    - Kafka produce() raises → NOT caught → re-raises → Flink back-pressure upstream
    - PostgreSQL insert fails → caught, routed to DLQ (see TestAlertPostgresSink)
    """

    def test_kafka_exception_is_not_swallowed(self):
        """A KafkaException from produce() MUST propagate to the caller.

        This documents FR-010: back-pressure from an unavailable
        txn.fraud.alerts topic MUST propagate upstream through the Flink
        pipeline. The exception is intentionally NOT caught in emit().
        """
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        mock_producer = MagicMock()
        mock_producer.produce.side_effect = KafkaException("broker unreachable")
        sink._producer = mock_producer

        with pytest.raises(KafkaException):
            sink.emit(alert)

    def test_kafka_exception_not_routed_to_dlq(self):
        """When produce() raises KafkaException, DLQ produce is NOT called.

        DLQ routing is reserved for PostgreSQL failures (FR-010b).
        Kafka failures must surface as exceptions for back-pressure — not
        be silently absorbed into the DLQ path.
        """
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        mock_producer = MagicMock()
        mock_producer.produce.side_effect = KafkaException("broker unreachable")
        sink._producer = mock_producer

        call_count_before = mock_producer.produce.call_count
        with pytest.raises(KafkaException):
            sink.emit(alert)

        # Only the original produce() call happened — no second DLQ produce()
        assert mock_producer.produce.call_count == call_count_before + 1


class TestAlertKafkaSinkSerialisation:
    def test_serialise_produces_bytes(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)
        alert = _make_alert()

        payload = sink._serialise(alert)
        assert isinstance(payload, bytes)
        assert len(payload) > 0


class TestAlertKafkaSinkDlq:
    """Unit tests for the _on_delivery → _serialise_dlq → DLQ produce path."""

    def _make_sink(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        sink = AlertKafkaSink(ScoringConfig())
        sink._producer = MagicMock()
        return sink

    def _truthy_err(self, message: str = "broker timeout"):
        err = MagicMock()
        err.__bool__ = lambda _: True
        err.__str__ = lambda _: message
        return err

    def test_on_delivery_success_does_not_produce_to_dlq(self):
        """err=None (successful delivery) must never trigger a DLQ produce."""
        sink = self._make_sink()
        alert = _make_alert()

        sink._on_delivery(None, MagicMock(), alert)

        sink._producer.produce.assert_not_called()

    def test_on_delivery_error_uses_transaction_id_as_dlq_key(self):
        """DLQ record key must be transaction_id encoded as bytes."""
        sink = self._make_sink()
        alert = _make_alert(transaction_id="txn-dlq-key")

        sink._on_delivery(self._truthy_err(), MagicMock(), alert)

        call_kwargs = sink._producer.produce.call_args[1]
        assert call_kwargs["key"] == b"txn-dlq-key"

    def test_on_delivery_error_routes_to_dlq_topic(self):
        """DLQ produce must target fraud_alerts_dlq_topic, not alerts topic."""
        from pipelines.scoring.config import ScoringConfig

        config = ScoringConfig()
        sink = self._make_sink()
        alert = _make_alert()

        sink._on_delivery(self._truthy_err(), MagicMock(), alert)

        call_kwargs = sink._producer.produce.call_args[1]
        assert call_kwargs["topic"] == config.fraud_alerts_dlq_topic

    def test_serialise_dlq_returns_decodable_avro(self):
        """_serialise_dlq must return valid Avro bytes decodable against the DLQ schema."""
        import io

        import fastavro

        sink = self._make_sink()
        alert = _make_alert()

        payload = sink._serialise_dlq(alert, error_type="DELIVERY_FAILURE", error_message="oops")

        assert isinstance(payload, bytes)
        records = list(fastavro.reader(io.BytesIO(payload)))
        assert len(records) == 1

    def test_serialise_dlq_payload_contains_error_type(self):
        """DLQ payload must include error_type=DELIVERY_FAILURE set by _on_delivery."""
        import io

        import fastavro

        sink = self._make_sink()
        alert = _make_alert()

        payload = sink._serialise_dlq(alert, error_type="DELIVERY_FAILURE", error_message="x")

        record = list(fastavro.reader(io.BytesIO(payload)))[0]
        assert record["error_type"] == "DELIVERY_FAILURE"

    def test_serialise_dlq_payload_contains_error_message(self):
        """DLQ payload must carry the original error string from the delivery callback."""
        import io

        import fastavro

        sink = self._make_sink()
        alert = _make_alert()
        error_msg = "Broker: Message size too large"

        payload = sink._serialise_dlq(alert, error_type="DELIVERY_FAILURE", error_message=error_msg)

        record = list(fastavro.reader(io.BytesIO(payload)))[0]
        assert record["error_message"] == error_msg

    def test_serialise_dlq_payload_preserves_alert_fields(self):
        """DLQ payload must preserve all original FraudAlert fields."""
        import io

        import fastavro

        sink = self._make_sink()
        alert = _make_alert(
            transaction_id="txn-preserve",
            account_id="acc-preserve",
            matched_rule_names=["VEL-001", "ND-003"],
            severity="critical",
            evaluation_timestamp=1_700_000_000_000,
        )

        payload = sink._serialise_dlq(alert, error_type="DELIVERY_FAILURE", error_message="err")

        from datetime import datetime, timezone

        record = list(fastavro.reader(io.BytesIO(payload)))[0]
        assert record["transaction_id"] == "txn-preserve"
        assert record["account_id"] == "acc-preserve"
        assert record["matched_rule_names"] == ["VEL-001", "ND-003"]
        assert record["severity"] == "critical"
        expected_ts = datetime.fromtimestamp(1_700_000_000_000 / 1000, tz=timezone.utc)
        assert record["evaluation_timestamp"] == expected_ts

    def test_serialise_dlq_payload_includes_failed_at_timestamp(self):
        """DLQ payload must include a failed_at epoch-ms timestamp."""
        import io
        import time
        from datetime import datetime

        import fastavro

        sink = self._make_sink()
        alert = _make_alert()

        before_ms = int(time.time() * 1000)
        payload = sink._serialise_dlq(alert, error_type="DELIVERY_FAILURE", error_message="err")
        after_ms = int(time.time() * 1000)

        record = list(fastavro.reader(io.BytesIO(payload)))[0]
        assert isinstance(record["failed_at"], datetime)
        record_ms = int(record["failed_at"].timestamp() * 1000)
        assert before_ms <= record_ms <= after_ms

    def test_on_delivery_error_dlq_value_is_avro_bytes(self):
        """The value produced to DLQ must be non-empty bytes (Avro-encoded)."""
        sink = self._make_sink()
        alert = _make_alert()

        sink._on_delivery(self._truthy_err("kafka err"), MagicMock(), alert)

        call_kwargs = sink._producer.produce.call_args[1]
        value = call_kwargs["value"]
        assert isinstance(value, bytes)
        assert len(value) > 0
