"""Integration tests for graceful catalog failure in IcebergEnrichedSink.

Tests failure handling when the Iceberg catalog is unavailable:
- Catalog connection errors increment iceberg_catalog_unavailable_total counter
- Connection failures do not crash the Flink job (no exceptions propagate)
- Failed writes emit structured DLQ logs with batch_size and reason fields
- Buffer is cleared after failed flush (prevents memory leaks)
- Multiple catalog failures eventually open the circuit breaker

Run with:
  pytest tests/integration/test_catalog_failure.py -v
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from pipelines.processing.operators.iceberg_sink import IcebergEnrichedSink


def _has_pyarrow() -> bool:
    """Check if pyarrow is available."""
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enriched_record():
    """Create a valid enriched record with all 36 Iceberg schema fields."""
    return {
        "transaction_id": "txn-test-001",
        "account_id": "acc-test-001",
        "merchant_id": "mer-test-001",
        "amount": Decimal("123.4500"),
        "currency": "USD",
        "event_time": 1704067200000,
        "enrichment_time": 1704067200100,
        "channel": "WEB",
        "card_bin": "123456",
        "card_last4": "6789",
        "caller_ip_subnet": "192.168.1.0/24",
        "api_key_id": "key-test-001",
        "oauth_scope": "transactions:read",
        "geo_lat": 40.7128,
        "geo_lon": -74.0060,
        "masking_lib_version": "1.0.0",
        "vel_count_1m": 5,
        "vel_amount_1m": Decimal("500.0000"),
        "vel_count_5m": 12,
        "vel_amount_5m": Decimal("1200.0000"),
        "vel_count_1h": 45,
        "vel_amount_1h": Decimal("4500.0000"),
        "vel_count_24h": 120,
        "vel_amount_24h": Decimal("12000.0000"),
        "geo_country": "US",
        "geo_city": "New York",
        "geo_network_class": "RESIDENTIAL",
        "geo_confidence": 0.95,
        "device_first_seen": 1704000000000,
        "device_txn_count": 42,
        "device_known_fraud": False,
        "prev_geo_country": "US",
        "prev_txn_time_ms": 1704067100000,
        "enrichment_latency_ms": 100,
        "processor_version": "002-stream-processor@1.0.0",
        "schema_version": "1",
    }


@pytest.fixture
def sink():
    """Create a fresh IcebergEnrichedSink instance."""
    s = IcebergEnrichedSink()
    s._table = MagicMock()
    try:
        from pybreaker import CircuitBreaker

        s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    except ImportError:
        s._breaker = MagicMock()
        s._breaker.call.side_effect = lambda func, *args: func(*args)
    s._catalog_loaded = True
    return s


# ---------------------------------------------------------------------------
# Test: Catalog unavailable increments metric
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_catalog_unavailable_increments_metric(sink, enriched_record):
    """Verify catalog connection error increments iceberg_catalog_unavailable_total.

    Given:
      - table.append() raises ConnectionError("catalog down")
    When:
      - Trigger flush via invoke()
    Then:
      - _increment_counter("iceberg_catalog_unavailable_total") is called
    """
    sink._table.append.side_effect = ConnectionError("catalog down")

    record = {**enriched_record, "transaction_id": "txn-catalog-001"}
    sink._buffer = [record]

    with patch(
        "pipelines.processing.operators.iceberg_sink._increment_counter"
    ) as mock_counter:
        sink._flush()
        # Verify the catalog unavailable counter was incremented
        mock_counter.assert_called_with("iceberg_catalog_unavailable_total")


# ---------------------------------------------------------------------------
# Test: Catalog failure does not crash job
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_catalog_failure_does_not_crash_job(sink, enriched_record):
    """Verify catalog connection failure does not raise to hot path.

    Given:
      - table.append() raises ConnectionError
    When:
      - Call invoke()
    Then:
      - invoke() does NOT raise any exception
      - Job continues processing
    """
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # This should NOT raise
    try:
        sink.invoke(record, context=None)
        # Second invoke triggers flush of first record
        sink.invoke(record, context=None)
    except Exception as e:
        pytest.fail(f"invoke() raised exception: {e}")


# ---------------------------------------------------------------------------
# Test: Catalog failure emits structured DLQ log
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_catalog_failure_emits_structured_dlq_log(sink, enriched_record):
    """Verify catalog failure DLQ log contains batch_size and reason.

    Given:
      - table.append() raises ConnectionError
      - DLQ logger is mocked
    When:
      - Flush the record
    Then:
      - DLQ logger.warning() is called
      - Log message is valid JSON
      - JSON contains "batch_size", "reason", "transaction_id" fields
    """
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    record = {**enriched_record, "transaction_id": "txn-dlq-001"}
    sink._buffer = [record]

    with patch(
        "pipelines.processing.operators.iceberg_sink.dlq_logger"
    ) as mock_dlq_logger:
        sink._flush()

        # Verify DLQ logger was called
        mock_dlq_logger.warning.assert_called()

        # Extract the logged message
        call_args = mock_dlq_logger.warning.call_args
        logged_message = call_args[0][0]

        # Parse JSON log
        dlq_event = json.loads(logged_message)

        # Verify required fields
        assert "batch_size" in dlq_event
        assert "reason" in dlq_event
        assert "transaction_id" in dlq_event
        assert dlq_event["batch_size"] == 1
        assert dlq_event["reason"] == "catalog_unavailable"  # ConnectionError → catalog_unavailable


# ---------------------------------------------------------------------------
# Test: Buffer cleared on catalog failure
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_buffer_cleared_on_catalog_failure(sink, enriched_record):
    """Verify buffer is cleared after failed flush (no memory leak).

    Given:
      - 5 records in buffer
      - table.append() raises ConnectionError
    When:
      - Call _flush()
    Then:
      - Buffer is empty after flush (cleared in finally block)
      - No memory leak risk
    """
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    records = [
        {**enriched_record, "transaction_id": f"txn-{i:03d}"}
        for i in range(5)
    ]
    sink._buffer = records
    assert len(sink._buffer) == 5

    # Flush (will fail but catch exception)
    sink._flush()

    # Buffer should be cleared
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: Multiple catalog failures open circuit
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_multiple_catalog_failures_open_circuit(sink, enriched_record):
    """Verify repeated catalog failures eventually open circuit breaker.

    Given:
      - CircuitBreaker with fail_max=3
      - table.append() always raises ConnectionError
    When:
      - Flush 3 times
    Then:
      - Circuit breaker opens after 3 failures
      - fail_counter >= 3
    """
    try:
        from pybreaker import CircuitBreaker
    except ImportError:
        pytest.skip("pybreaker not installed")

    # Use real CircuitBreaker
    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # Flush 3 times
    for _ in range(3):
        sink._buffer = [record]
        sink._flush()

    # Circuit should be open after 3 failures
    assert sink._breaker.fail_counter >= 3
    assert sink._breaker.current_state == "open"


# ---------------------------------------------------------------------------
# Test: OSError treated same as ConnectionError
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_os_error_increments_catalog_counter(sink, enriched_record):
    """Verify OSError (network-related) also increments catalog_unavailable counter.

    Given:
      - table.append() raises OSError (e.g., timeout)
    When:
      - Flush the record
    Then:
      - iceberg_catalog_unavailable_total is incremented
    """
    sink._table.append.side_effect = OSError("Network timeout")

    record = {**enriched_record, "transaction_id": "txn-os-001"}
    sink._buffer = [record]

    with patch(
        "pipelines.processing.operators.iceberg_sink._increment_counter"
    ) as mock_counter:
        sink._flush()
        # Verify catalog unavailable counter was incremented
        mock_counter.assert_called_with("iceberg_catalog_unavailable_total")


# ---------------------------------------------------------------------------
# Test: Partial batch on failure (dedup respected)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_catalog_failure_after_dedup(sink, enriched_record):
    """Verify deduplication happens before catalog failure check.

    Given:
      - 5 records with 2 duplicates (total 3 unique)
      - table.append() raises ConnectionError
    When:
      - Flush the batch
    Then:
      - Deduplication happens first
      - DLQ event shows batch_size=3 (deduplicated count)
      - No exception raised
    """
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    # Create records with duplicates
    record_a = {**enriched_record, "transaction_id": "txn-a"}
    record_b = {**enriched_record, "transaction_id": "txn-b"}
    record_a_dup = {**enriched_record, "transaction_id": "txn-a"}  # duplicate
    record_c = {**enriched_record, "transaction_id": "txn-c"}
    record_b_dup = {**enriched_record, "transaction_id": "txn-b"}  # duplicate

    sink._buffer = [record_a, record_b, record_a_dup, record_c, record_b_dup]

    with patch(
        "pipelines.processing.operators.iceberg_sink.dlq_logger"
    ) as mock_dlq_logger:
        sink._flush()

        # Verify DLQ was called
        mock_dlq_logger.warning.assert_called()

        # Extract logged message
        call_args = mock_dlq_logger.warning.call_args
        logged_message = call_args[0][0]
        dlq_event = json.loads(logged_message)

        # batch_size should be 3 (after dedup), not 5
        assert dlq_event["batch_size"] == 3


# ---------------------------------------------------------------------------
# Test: Invoke with time-based flush on catalog error
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_invoke_with_time_based_flush_on_catalog_error(sink, enriched_record):
    """Verify invoke() with time-based flush handles catalog errors gracefully.

    Given:
      - Record in buffer, time threshold exceeded
      - table.append() raises ConnectionError
    When:
      - invoke() is called (triggers time-based flush)
    Then:
      - Exception is caught in _flush()
      - invoke() does not raise
      - New record is added to (now empty) buffer
    """
    import time

    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    record1 = {**enriched_record, "transaction_id": "txn-001"}
    record2 = {**enriched_record, "transaction_id": "txn-002"}

    # Add first record and set time in past
    sink._buffer = [record1]
    sink._last_flush_time_sec = time.time() - 2.0  # 2 seconds ago

    # invoke() should trigger time-based flush
    sink.invoke(record2, context=None)

    # Verify no exception raised and second record was added
    assert len(sink._buffer) == 1
    assert sink._buffer[0]["transaction_id"] == "txn-002"


# ---------------------------------------------------------------------------
# Test: Catalog error does not increment buffer_overflow counter
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_catalog_error_does_not_increment_buffer_overflow(sink, enriched_record):
    """Verify catalog error only increments catalog counter, not buffer_overflow.

    Given:
      - Batch size < ICEBERG_BUFFER_MAX
      - table.append() raises ConnectionError
    When:
      - Flush the batch
    Then:
      - Only iceberg_catalog_unavailable_total is incremented
      - iceberg_buffer_overflow_total is NOT incremented
    """
    sink._table.append.side_effect = ConnectionError("Catalog unavailable")

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]

    with patch(
        "pipelines.processing.operators.iceberg_sink._increment_counter"
    ) as mock_counter:
        sink._flush()

        # Get all calls to _increment_counter
        calls = [call[0][0] for call in mock_counter.call_args_list]

        # Verify catalog unavailable was called
        assert "iceberg_catalog_unavailable_total" in calls
        # Verify buffer_overflow was NOT called
        assert "iceberg_buffer_overflow_total" not in calls
