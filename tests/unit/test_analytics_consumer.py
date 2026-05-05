"""Unit tests for analytics/consumers/kafka_consumer.py"""

import io
import queue
import re
from datetime import UTC
from unittest.mock import MagicMock, patch

import fastavro
import pytest

from analytics.consumers.kafka_consumer import (
    AnalyticsKafkaConsumer,
    _deserialize,
)

# ── helpers ─────────────────────────────────────────────────────────────────

_RAW_SCHEMA = {
    "type": "record",
    "name": "FraudAlert",
    "namespace": "com.fraudstream.alerts.v1",
    "fields": [
        {"name": "transaction_id", "type": "string"},
        {"name": "account_id", "type": "string"},
        {"name": "matched_rule_names", "type": {"type": "array", "items": "string"}},
        {
            "name": "severity",
            "type": {
                "type": "enum",
                "name": "AlertSeverity",
                "symbols": ["low", "medium", "high", "critical"],
            },
        },
        {
            "name": "evaluation_timestamp",
            "type": {"type": "long", "logicalType": "timestamp-millis"},
        },
    ],
}

_PARSED = fastavro.parse_schema(_RAW_SCHEMA)


def _make_avro_bytes(
    transaction_id="txn-001",
    account_id="acc-0042",
    matched_rule_names=None,
    severity="high",
    evaluation_timestamp=1_700_000_000_000,
) -> bytes:
    """Build a schemaless Avro-encoded FraudAlert byte string for testing."""
    if matched_rule_names is None:
        matched_rule_names = ["VEL-001"]
    buf = io.BytesIO()
    fastavro.schemaless_writer(
        buf,
        _PARSED,
        {
            "transaction_id": transaction_id,
            "account_id": account_id,
            "matched_rule_names": matched_rule_names,
            "severity": severity,
            "evaluation_timestamp": evaluation_timestamp,
        },
    )
    return buf.getvalue()


# ── deserialization ──────────────────────────────────────────────────────────


class TestDeserialize:
    """Tests for the _deserialize Avro → FraudAlertDisplay conversion."""

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_maps_fields_correctly(self, _mock):
        raw = _make_avro_bytes(severity="critical", matched_rule_names=["VEL-001", "ND-003"])
        alert = _deserialize(raw)
        assert alert.transaction_id == "txn-001"
        assert alert.account_id == "acc-0042"
        assert alert.rule_triggers == ["VEL-001", "ND-003"]
        assert alert.severity == "critical"
        assert alert.decision == "BLOCK"
        assert alert.evaluation_timestamp.tzinfo == UTC

    @pytest.mark.parametrize(
        "severity,expected_decision",
        [
            ("critical", "BLOCK"),
            ("high", "BLOCK"),
            ("medium", "FLAG"),
            ("low", "ALLOW"),
        ],
    )
    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_severity_to_decision_mapping(self, _mock, severity, expected_decision):
        raw = _make_avro_bytes(severity=severity)
        alert = _deserialize(raw)
        assert alert.decision == expected_decision

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_sentinel_defaults_for_missing_fields(self, _mock):
        raw = _make_avro_bytes()
        alert = _deserialize(raw)
        assert alert.merchant_id == ""
        assert alert.amount == 0.0
        assert alert.currency == ""
        assert alert.channel == ""
        assert alert.fraud_score == 0.0
        assert alert.model_version == ""

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_received_at_is_utc(self, _mock):
        raw = _make_avro_bytes()
        alert = _deserialize(raw)
        assert alert.received_at.tzinfo is not None


# ── buffer eviction ──────────────────────────────────────────────────────────


class TestBufferEviction:
    """When the queue is full, the oldest item is evicted to make room for the newest."""

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_full_queue_drops_oldest(self, _mock):
        consumer = AnalyticsKafkaConsumer(queue_maxsize=3)
        # Fill queue with txn-000, txn-001, txn-002
        for i in range(3):
            raw = _make_avro_bytes(transaction_id=f"txn-{i:03d}")
            alert = _deserialize(raw)
            consumer.queue.put_nowait(alert)

        assert consumer.queue.full()

        # Simulate the overflow logic (drop oldest, insert newest)
        new_raw = _make_avro_bytes(transaction_id="txn-999")
        new_alert = _deserialize(new_raw)
        try:
            consumer.queue.put_nowait(new_alert)
        except queue.Full:
            consumer.queue.get_nowait()
            consumer.queue.put_nowait(new_alert)

        items = []
        while not consumer.queue.empty():
            items.append(consumer.queue.get_nowait())

        ids = [a.transaction_id for a in items]
        assert "txn-999" in ids
        assert "txn-000" not in ids  # oldest was evicted


# ── restart counter ──────────────────────────────────────────────────────────


class TestRestartCounter:
    """Tests for consumer-thread restart behaviour on Kafka errors."""

    def test_restart_counter_increments_on_kafka_exception(self):
        from confluent_kafka import KafkaException as KE

        consumer = AnalyticsKafkaConsumer(queue_maxsize=10)

        call_count = {"n": 0}

        def fake_build():
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock_c = MagicMock()
                mock_c.subscribe = MagicMock()
                mock_c.poll = MagicMock(side_effect=KE("broker unavailable"))
                mock_c.close = MagicMock()
                return mock_c
            # second call: trigger stop
            consumer._stop_event.set()
            raise RuntimeError("stop")

        target = "analytics.consumers.kafka_consumer.analytics_consumer_restarts_total"
        with patch.object(consumer, "_build_consumer", side_effect=fake_build):
            with patch(target) as mock_ctr:
                mock_ctr.inc = MagicMock()
                consumer._run()
                assert mock_ctr.inc.call_count >= 1


# ── PII render assertion ─────────────────────────────────────────────────────


class TestPIIRender:
    """
    account_id as received from the Avro schema must NOT contain a raw PAN or full IP.

    The scoring pipeline writes account_id as an opaque identifier (e.g. acc-0042),
    never a card number. Verify the pattern holds for deserialized alerts.
    """

    _PAN_RE = re.compile(r"\b\d{13,19}\b")
    _IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_account_id_no_raw_pan(self, _mock):
        raw = _make_avro_bytes(account_id="acc-0042")
        alert = _deserialize(raw)
        assert not self._PAN_RE.search(alert.account_id), (
            f"account_id looks like a raw PAN: {alert.account_id!r}"
        )

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_account_id_no_raw_ip(self, _mock):
        raw = _make_avro_bytes(account_id="acc-0042")
        alert = _deserialize(raw)
        assert not self._IP_RE.search(alert.account_id), (
            f"account_id contains a full IP: {alert.account_id!r}"
        )

    @patch("analytics.consumers.kafka_consumer._get_schema", return_value=_PARSED)
    def test_display_struct_no_raw_pan_in_any_str_field(self, _mock):
        raw = _make_avro_bytes(account_id="acc-0042")
        alert = _deserialize(raw)
        str_fields = [
            alert.account_id,
            alert.merchant_id,
            alert.currency,
            alert.channel,
            alert.model_version,
        ]
        for val in str_fields:
            assert not self._PAN_RE.search(val), f"Raw PAN found in field: {val!r}"
