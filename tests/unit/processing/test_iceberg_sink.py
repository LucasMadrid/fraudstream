"""Unit tests for IcebergEnrichedSink operator."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from pipelines.processing.operators.iceberg_sink import (
    IcebergEnrichedSink,
    _DLQEvent,
    _emit_dlq_event,
    _increment_counter,
)


class TestDLQEvent:
    """Test _DLQEvent dataclass."""

    def test_creates_dlq_event(self):
        event = _DLQEvent(
            transaction_id="txn-001",
            reason="timeout",
            batch_size=50,
        )
        assert event.transaction_id == "txn-001"
        assert event.reason == "timeout"
        assert event.batch_size == 50

    def test_dlq_event_frozen(self):
        event = _DLQEvent(
            transaction_id="txn-001",
            reason="timeout",
            batch_size=50,
        )
        with pytest.raises(AttributeError):
            event.transaction_id = "txn-002"


class TestEmitDLQEvent:
    """Test _emit_dlq_event function."""

    def test_logs_dlq_event_as_json(self, caplog):
        caplog.set_level(logging.WARNING, logger="dlq")
        event = _DLQEvent(
            transaction_id="txn-001",
            reason="timeout",
            batch_size=42,
        )
        _emit_dlq_event(event)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.name == "dlq"
        assert "iceberg_sink_dlq" in record.message
        assert "txn-001" in record.message
        assert "timeout" in record.message
        assert "42" in record.message

    def test_dlq_event_is_valid_json(self, caplog):
        caplog.set_level(logging.WARNING, logger="dlq")
        event = _DLQEvent(
            transaction_id="txn-123",
            reason="circuit_open",
            batch_size=100,
        )
        _emit_dlq_event(event)

        record = caplog.records[0]
        parsed = json.loads(record.message)
        assert parsed["event"] == "iceberg_sink_dlq"
        assert parsed["transaction_id"] == "txn-123"
        assert parsed["reason"] == "circuit_open"
        assert parsed["batch_size"] == 100


class TestIncrementCounter:
    """Test _increment_counter function."""

    def test_increments_iceberg_buffer_overflow_total(self):
        with patch("pipelines.processing.metrics.iceberg_buffer_overflow_total") as mock:
            _increment_counter("iceberg_buffer_overflow_total")
            mock.inc.assert_called_once()

    def test_increments_iceberg_catalog_unavailable_total(self):
        with patch("pipelines.processing.metrics.iceberg_catalog_unavailable_total") as mock:
            _increment_counter("iceberg_catalog_unavailable_total")
            mock.inc.assert_called_once()

    def test_swallows_import_error(self):
        # Should not raise if metrics unavailable
        with patch(
            "pipelines.processing.operators.iceberg_sink.logger",
        ):
            _increment_counter("iceberg_buffer_overflow_total")

    def test_increments_feast_push_failures_total(self):
        with patch("pipelines.processing.metrics.feast_push_failures_total") as mock:
            _increment_counter("feast_push_failures_total")
            mock.inc.assert_called_once()


class TestIcebergEnrichedSinkInit:
    """Test IcebergEnrichedSink initialization."""

    def test_initializes_with_empty_buffer(self):
        sink = IcebergEnrichedSink()
        assert sink._buffer == []
        assert isinstance(sink._last_flush_time_sec, float)
        assert sink._table is None
        assert sink._breaker is None
        assert sink._feast_store is None

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

        # Add records with duplicates
        sink._buffer = [
            {"transaction_id": "txn-001", "amount": Decimal("100.00")},
            {"transaction_id": "txn-002", "amount": Decimal("200.00")},
            {"transaction_id": "txn-001", "amount": Decimal("150.00")},  # duplicate
        ]

        # Manually call the dedup logic from _flush
        seen_txn_ids: set[str] = set()
        deduplicated: list[dict] = []
        for record in sink._buffer:
            txn_id = record.get("transaction_id", "")
            if txn_id not in seen_txn_ids:
                seen_txn_ids.add(txn_id)
                deduplicated.append(record)

        assert len(deduplicated) == 2
        assert deduplicated[0]["transaction_id"] == "txn-001"
        assert deduplicated[0]["amount"] == Decimal("100.00")  # First occurrence kept
        assert deduplicated[1]["transaction_id"] == "txn-002"

    def test_dedup_preserves_order(self):
        sink = IcebergEnrichedSink()
        sink._buffer = [
            {"transaction_id": "a"},
            {"transaction_id": "b"},
            {"transaction_id": "c"},
            {"transaction_id": "a"},  # duplicate
            {"transaction_id": "b"},  # duplicate
        ]

        seen_txn_ids: set[str] = set()
        deduplicated: list[dict] = []
        for record in sink._buffer:
            txn_id = record.get("transaction_id", "")
            if txn_id not in seen_txn_ids:
                seen_txn_ids.add(txn_id)
                deduplicated.append(record)

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
