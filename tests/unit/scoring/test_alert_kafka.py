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
