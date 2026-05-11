"""Unit tests for IcebergEnrichedSink operator."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipelines.processing.operators.iceberg_sink import IcebergEnrichedSink


class TestIcebergEnrichedSinkInit:
    """Test IcebergEnrichedSink initialization."""

    def test_initializes_with_empty_buffer(self):
        sink = IcebergEnrichedSink()
        assert sink._buffer == []
        assert isinstance(sink._last_flush_time_sec, float)
        assert sink._table is None
        assert sink._breaker is None
        assert sink._materializer is None

    def test_buffer_type_is_list_of_dicts(self):
        sink = IcebergEnrichedSink()
        assert isinstance(sink._buffer, list)


class TestIcebergEnrichedSinkInvoke:
    """Test IcebergEnrichedSink.invoke() method."""

    def test_appends_record_to_buffer(self):
        sink = IcebergEnrichedSink()
        record = {
            "transaction_id": "txn-001",
            "account_id": "acc-001",
            "amount": Decimal("100.00"),
        }
        sink.invoke(record, context=None)
        assert len(sink._buffer) == 1
        assert sink._buffer[0] == record

    def test_multiple_records_accumulate(self):
        sink = IcebergEnrichedSink()
        for i in range(5):
            record = {
                "transaction_id": f"txn-{i:03d}",
                "account_id": f"acc-{i:03d}",
            }
            sink.invoke(record, context=None)
        assert len(sink._buffer) == 5

    def test_does_not_raise_on_exception(self):
        sink = IcebergEnrichedSink()
        # Mock open() to fail
        sink._table = None
        sink._breaker = None

        record = {"transaction_id": "txn-001"}
        # Should not raise even if _flush fails
        sink.invoke(record, context=None)


class TestIcebergEnrichedSinkDeduplication:
    """Test in-batch deduplication logic."""

    def test_deduplicates_within_buffer(self):
        sink = IcebergEnrichedSink()
        records = [
            {"transaction_id": "txn-001", "amount": Decimal("100.00")},
            {"transaction_id": "txn-002", "amount": Decimal("200.00")},
            {"transaction_id": "txn-001", "amount": Decimal("150.00")},  # duplicate
        ]

        deduplicated = sink._deduplicate(records)

        assert len(deduplicated) == 2
        assert deduplicated[0]["transaction_id"] == "txn-001"
        assert deduplicated[0]["amount"] == Decimal("100.00")  # First occurrence kept
        assert deduplicated[1]["transaction_id"] == "txn-002"

    def test_dedup_preserves_order(self):
        sink = IcebergEnrichedSink()
        records = [
            {"transaction_id": "a"},
            {"transaction_id": "b"},
            {"transaction_id": "c"},
            {"transaction_id": "a"},  # duplicate
            {"transaction_id": "b"},  # duplicate
        ]

        deduplicated = sink._deduplicate(records)

        assert [r["transaction_id"] for r in deduplicated] == ["a", "b", "c"]


class TestIcebergEnrichedSinkArrowTableConversion:
    """Test PyArrow table conversion logic."""

    def test_converts_decimal_amounts(self):
        pytest.importorskip("pyarrow")

        sink = IcebergEnrichedSink()
        records = [
            {
                "transaction_id": "txn-001",
                "account_id": "acc-001",
                "merchant_id": "mer-001",
                "amount": Decimal("123.45"),
                "currency": "USD",
                "event_time": 1700000000000,
                "enrichment_time": 1700000000100,
                "channel": "WEB",
                "card_bin": "123456",
                "card_last4": "6789",
                "caller_ip_subnet": "192.168.1.0",
                "api_key_id": "key-001",
                "oauth_scope": "transactions:read",
                "geo_lat": 40.7128,
                "geo_lon": -74.0060,
                "masking_lib_version": "1.0.0",
                "vel_count_1m": 5,
                "vel_amount_1m": Decimal("500.00"),
                "vel_count_5m": 12,
                "vel_amount_5m": Decimal("1200.00"),
                "vel_count_1h": 45,
                "vel_amount_1h": Decimal("4500.00"),
                "vel_count_24h": 120,
                "vel_amount_24h": Decimal("12000.00"),
                "geo_country": "US",
                "geo_city": "New York",
                "geo_network_class": "RESIDENTIAL",
                "geo_confidence": 0.95,
                "device_first_seen": 1699000000000,
                "device_txn_count": 42,
                "device_known_fraud": False,
                "prev_geo_country": "US",
                "prev_txn_time_ms": 1699999900000,
                "enrichment_latency_ms": 100,
                "processor_version": "002-stream-processor@1.0.0",
                "schema_version": "1",
            }
        ]

        pa_table = sink._records_to_arrow_table(records)
        assert pa_table.num_rows == 1
        assert pa_table.num_columns == 36

        # Verify amount field is Decimal128
        amount_col = pa_table.column("amount")
        assert str(amount_col.type) == "decimal128(18, 4)"

    def test_handles_none_values(self):
        pytest.importorskip("pyarrow")

        sink = IcebergEnrichedSink()
        records = [
            {
                "transaction_id": "txn-001",
                "account_id": "acc-001",
                "merchant_id": "mer-001",
                "amount": Decimal("100.00"),
                "currency": "USD",
                "event_time": 1700000000000,
                "enrichment_time": 1700000000100,
                "channel": "WEB",
                "card_bin": "123456",
                "card_last4": "6789",
                "caller_ip_subnet": "192.168.1.0",
                "api_key_id": "key-001",
                "oauth_scope": "scope",
                "geo_lat": None,  # nullable
                "geo_lon": None,  # nullable
                "masking_lib_version": "1.0.0",
                "vel_count_1m": 1,
                "vel_amount_1m": Decimal("100.00"),
                "vel_count_5m": 1,
                "vel_amount_5m": Decimal("100.00"),
                "vel_count_1h": 1,
                "vel_amount_1h": Decimal("100.00"),
                "vel_count_24h": 1,
                "vel_amount_24h": Decimal("100.00"),
                "geo_country": None,  # nullable
                "geo_city": None,  # nullable
                "geo_network_class": None,  # nullable
                "geo_confidence": None,  # nullable
                "device_first_seen": None,  # nullable
                "device_txn_count": None,  # nullable
                "device_known_fraud": None,  # nullable
                "prev_geo_country": None,  # nullable
                "prev_txn_time_ms": None,  # nullable
                "enrichment_latency_ms": 50,
                "processor_version": "002@1.0.0",
                "schema_version": "1",
            }
        ]

        pa_table = sink._records_to_arrow_table(records)
        assert pa_table.num_rows == 1
        assert pa_table.column("geo_lat")[0].as_py() is None
        assert pa_table.column("device_known_fraud")[0].as_py() is None


class TestIcebergEnrichedSinkFlushBehavior:
    """Test buffer flushing behavior."""

    def test_flush_clears_buffer_on_success(self):
        pytest.importorskip("pyiceberg")

        sink = IcebergEnrichedSink()
        sink._buffer = [
            {
                "transaction_id": "txn-001",
                "account_id": "acc-001",
                "merchant_id": "mer-001",
                "amount": Decimal("100.00"),
                "currency": "USD",
                "event_time": 1700000000000,
                "enrichment_time": 1700000000100,
                "channel": "WEB",
                "card_bin": "123456",
                "card_last4": "6789",
                "caller_ip_subnet": "192.168.1.0",
                "api_key_id": "key-001",
                "oauth_scope": "scope",
                "geo_lat": None,
                "geo_lon": None,
                "masking_lib_version": "1.0.0",
                "vel_count_1m": 1,
                "vel_amount_1m": Decimal("100.00"),
                "vel_count_5m": 1,
                "vel_amount_5m": Decimal("100.00"),
                "vel_count_1h": 1,
                "vel_amount_1h": Decimal("100.00"),
                "vel_count_24h": 1,
                "vel_amount_24h": Decimal("100.00"),
                "geo_country": None,
                "geo_city": None,
                "geo_network_class": None,
                "geo_confidence": None,
                "device_first_seen": None,
                "device_txn_count": None,
                "device_known_fraud": None,
                "prev_geo_country": None,
                "prev_txn_time_ms": None,
                "enrichment_latency_ms": 50,
                "processor_version": "002@1.0.0",
                "schema_version": "1",
            }
        ]

        sink._table = MagicMock()
        sink._breaker = MagicMock()
        sink._breaker.call = MagicMock()

        sink._flush()

        # Buffer should be cleared after flush
        assert len(sink._buffer) == 0

    def test_flush_no_op_on_empty_buffer(self):
        sink = IcebergEnrichedSink()
        assert sink._buffer == []

        # Should return early without error
        sink._flush()
        assert sink._buffer == []

    def test_flush_with_none_table_emits_dlq(self, caplog):
        """When self._table is None, every record must be DLQ'd — no silent drops."""
        caplog.set_level(logging.WARNING)

        sink = IcebergEnrichedSink()
        sink._table = None  # simulate catalog load failure
        sink._catalog_loaded = False
        sink._breaker = None

        records = [{"transaction_id": f"txn-{i:03d}"} for i in range(3)]
        sink._buffer = list(records)

        sink._flush()

        # Buffer must be cleared
        assert sink._buffer == []

        # DLQ log entries with iceberg_table_not_loaded reason
        dlq_messages = [
            r.message
            for r in caplog.records
            if "iceberg_sink_dlq" in r.message and "iceberg_table_not_loaded" in r.message
        ]
        assert len(dlq_messages) == 3, (
            f"Expected 3 DLQ messages, got {len(dlq_messages)}. "
            f"All log messages: {[r.message for r in caplog.records]}"
        )
        for msg in dlq_messages:
            parsed = json.loads(msg)
            assert parsed["reason"] == "iceberg_table_not_loaded"
            assert parsed["event"] == "iceberg_sink_dlq"
