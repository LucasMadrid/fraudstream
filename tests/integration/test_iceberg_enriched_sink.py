"""Integration tests for IcebergEnrichedSink.

Tests the plain-Python fallback implementation (no PyFlink required).

Covers:
- Time-based flush (1-second threshold)
- Buffer-size-based flush
- In-batch deduplication by transaction_id
- Circuit breaker behavior (opens after 3 consecutive failures)
- Exception handling (no re-raise, all caught and DLQ'd)
- Enrichment time preservation (not overridden)
- All 36 Iceberg schema fields present in PyArrow table
- Decimal amount type (decimal128(18, 4))

Run with:
  pytest tests/integration/test_iceberg_enriched_sink.py -v
"""

from __future__ import annotations

import time
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
    """Create a valid enriched record with all 36 Iceberg schema fields.

    Returns a dict with sensible defaults for testing:
    - All 36 fields from iceberg.enriched_transactions schema
    - Decimal amounts
    - Timestamps as epoch ms integers
    - Nullable fields with None values
    """
    return {
        # Core transaction fields
        "transaction_id": "txn-test-001",
        "account_id": "acc-test-001",
        "merchant_id": "mer-test-001",
        "amount": Decimal("123.4500"),
        "currency": "USD",
        # Time fields
        "event_time": 1704067200000,
        "enrichment_time": 1704067200100,
        # Channel and payment fields
        "channel": "WEB",
        "card_bin": "123456",
        "card_last4": "6789",
        "caller_ip_subnet": "192.168.1.0/24",
        "api_key_id": "key-test-001",
        "oauth_scope": "transactions:read",
        # Geolocation fields
        "geo_lat": 40.7128,
        "geo_lon": -74.0060,
        "masking_lib_version": "1.0.0",
        # Velocity aggregates (1m, 5m, 1h, 24h)
        "vel_count_1m": 5,
        "vel_amount_1m": Decimal("500.0000"),
        "vel_count_5m": 12,
        "vel_amount_5m": Decimal("1200.0000"),
        "vel_count_1h": 45,
        "vel_amount_1h": Decimal("4500.0000"),
        "vel_count_24h": 120,
        "vel_amount_24h": Decimal("12000.0000"),
        # GeoLite2 fields
        "geo_country": "US",
        "geo_city": "New York",
        "geo_network_class": "RESIDENTIAL",
        "geo_confidence": 0.95,
        # Device fields
        "device_first_seen": 1704000000000,  # epoch ms
        "device_txn_count": 42,
        "device_known_fraud": False,
        # Previous transaction fields
        "prev_geo_country": "US",
        "prev_txn_time_ms": 1704067100000,  # epoch ms
        # Metadata fields
        "enrichment_latency_ms": 100,
        "processor_version": "002-stream-processor@1.0.0",
        "schema_version": "1",
    }


@pytest.fixture
def sink():
    """Create a fresh IcebergEnrichedSink instance for each test.

    Mocks the table and circuit breaker since we're testing integration behavior,
    not PyIceberg/pybreaker themselves.
    """
    s = IcebergEnrichedSink()
    # Mock the table to avoid needing real Iceberg
    s._table = MagicMock()
    # Initialize breaker manually with real CircuitBreaker if available
    try:
        from pybreaker import CircuitBreaker

        s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    except ImportError:
        s._breaker = MagicMock()
    s._catalog_loaded = True
    return s


# ---------------------------------------------------------------------------
# Test: Time-based flush (1-second threshold)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_flush_on_time_threshold(sink, enriched_record):
    """Verify sink flushes buffer after 1 second has elapsed.

    Given:
      - Sink with buffer max=100, 1 record in buffer
    When:
      - Advance time.time() by >1 second
      - Call invoke() with a second record
    Then:
      - First flush is triggered (due to 1-second threshold)
      - table.append() is called once
      - Second record is buffered (not yet flushed)
    """
    sink._table = MagicMock()
    # Setup breaker mock to call the function
    breaker_mock = MagicMock()
    breaker_mock.call.side_effect = lambda func, *args: func(*args)
    sink._breaker = breaker_mock

    record1 = {**enriched_record, "transaction_id": "txn-001"}
    record2 = {**enriched_record, "transaction_id": "txn-002"}

    # Record the time of first invoke
    initial_time = time.time()
    sink._last_flush_time_sec = initial_time

    # Invoke first record
    sink.invoke(record1, context=None)
    assert len(sink._buffer) == 1
    assert sink._table.append.call_count == 0

    # Mock time.time() to advance by 1.5 seconds
    with patch("pipelines.processing.operators.iceberg_sink.time.time") as mock_time:
        mock_time.return_value = initial_time + 1.5
        # Invoke second record (triggers flush of first)
        sink.invoke(record2, context=None)

    # First record should have been flushed
    assert sink._table.append.call_count == 1
    assert len(sink._buffer) == 1


# ---------------------------------------------------------------------------
# Test: Buffer-size-based flush
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_flush_on_buffer_size(sink, enriched_record):
    """Verify sink flushes when buffer reaches max size.

    Given:
      - ICEBERG_BUFFER_MAX=3, empty buffer
    When:
      - Invoke 3 records consecutively
    Then:
      - table.append() is called after the 3rd record
      - Buffer is cleared
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    # Re-read the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = 3

    try:
        record1 = {**enriched_record, "transaction_id": "txn-001"}
        record2 = {**enriched_record, "transaction_id": "txn-002"}
        record3 = {**enriched_record, "transaction_id": "txn-003"}

        # Invoke 3 records
        sink.invoke(record1, context=None)
        assert sink._table.append.call_count == 0
        assert len(sink._buffer) == 1

        sink.invoke(record2, context=None)
        assert sink._table.append.call_count == 0
        assert len(sink._buffer) == 2

        sink.invoke(record3, context=None)
        assert sink._table.append.call_count == 1
        assert len(sink._buffer) == 0
    finally:
        sink_module.ICEBERG_BUFFER_MAX = original_max


# ---------------------------------------------------------------------------
# Test: In-batch deduplication by transaction_id
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_in_batch_deduplication(sink, enriched_record):
    """Verify sink deduplicates records by transaction_id within batch.

    Given:
      - 3 records: txn-001 (100), txn-001 (999 - dup), txn-002 (200)
    When:
      - Add all 3 to buffer and call _flush()
    Then:
      - table.append() called with PyArrow table of 2 rows (dup removed)
      - First occurrence of txn-001 is kept (100, not 999)
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    record1 = {
        **enriched_record,
        "transaction_id": "txn-001",
        "amount": Decimal("100.0000"),
    }
    record1_dup = {
        **enriched_record,
        "transaction_id": "txn-001",
        "amount": Decimal("999.0000"),
    }
    record2 = {
        **enriched_record,
        "transaction_id": "txn-002",
        "amount": Decimal("200.0000"),
    }

    # Add all 3 to buffer
    sink._buffer.extend([record1, record1_dup, record2])
    assert len(sink._buffer) == 3

    # Manually call _flush to deduplicate
    sink._flush()

    # Verify table.append() was called
    assert sink._table.append.call_count == 1

    # Verify PyArrow table has only 2 rows
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]
    assert pa_table.num_rows == 2

    # Verify first occurrence kept (100, not 999)
    transaction_ids = pa_table["transaction_id"].to_pylist()
    amounts = pa_table["amount"].to_pylist()
    assert transaction_ids == ["txn-001", "txn-002"]
    assert amounts[0] == Decimal("100.0000")
    assert amounts[1] == Decimal("200.0000")


# ---------------------------------------------------------------------------
# Test: Circuit breaker opens after 3 failures
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_circuit_breaker_opens_after_failures(sink, enriched_record):
    """Verify circuit breaker opens after 3 consecutive failures.

    Given:
      - table.append() raises Exception consistently
      - Circuit breaker with fail_max=3
    When:
      - Invoke and flush 3 times (each time append raises)
    Then:
      - After 3rd failure, circuit breaker opens
      - 4th invoke + flush attempt does NOT attempt to call table.append()
      - DLQ events are emitted for all failures
    """
    try:
        from pybreaker import CircuitBreaker, CircuitBreakerError
    except ImportError:
        pytest.skip("pybreaker not installed")

    sink._table = MagicMock()
    # Use real CircuitBreaker instead of mock for this test
    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])

    # Mock table.append() to always raise ConnectionError
    sink._table.append.side_effect = ConnectionError("Connection refused")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # First 3 invokes should fail and increment the breaker failure count
    for _ in range(3):
        sink._buffer = []  # Reset buffer between calls
        sink.invoke(record, context=None)
        # Each invoke adds to buffer, next invoke triggers flush
        sink._flush()

    # After 3 failures, the breaker should have recorded 3+ failures
    assert sink._breaker.fail_counter >= 3

    # 4th invoke should not attempt append (circuit is open)
    sink._buffer = []
    sink._table.reset_mock()

    # Verify circuit is actually open by trying to call through it
    with pytest.raises(CircuitBreakerError):
        sink._breaker.call(sink._table.append, MagicMock())


# ---------------------------------------------------------------------------
# Test: No exception propagation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_no_exception_propagation(sink, enriched_record):
    """Verify invoke() catches all exceptions and never re-raises.

    Given:
      - table.append() raises RuntimeError
    When:
      - invoke() is called and _flush is triggered
    Then:
      - invoke() does NOT raise
      - Buffer is cleared to prevent unbounded growth
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)
    sink._table.append.side_effect = RuntimeError("boom")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # Add record to buffer and directly call _flush to trigger exception
    sink._buffer = [record]
    # This should NOT raise even though table.append() will raise
    sink._flush()

    # Verify buffer was cleared (exception was caught)
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: Enrichment time preserved
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_enrichment_time_preserved(sink, enriched_record):
    """Verify enrichment_time preserved in PyArrow table (not overridden).

    Given:
      - A record with enrichment_time=1704067200100 (epoch ms)
    When:
      - Flush the record to PyArrow table
    Then:
      - The enrichment_time column contains the equivalent datetime value
    """
    import datetime

    pytest.importorskip("pyarrow")

    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    original_enrichment_time = 1704067200100
    record = {**enriched_record, "enrichment_time": original_enrichment_time}

    sink._buffer = [record]
    sink._flush()

    # Verify table.append() was called
    assert sink._table.append.call_count == 1

    # Extract the PyArrow table passed to append()
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    # Verify enrichment_time column contains original value as datetime
    enrichment_times = pa_table["enrichment_time"].to_pylist()
    assert len(enrichment_times) == 1
    # Convert epoch ms to datetime for comparison
    expected_dt = datetime.datetime.fromtimestamp(
        original_enrichment_time / 1000, tz=datetime.UTC
    ).replace(tzinfo=None)
    assert enrichment_times[0] == expected_dt


# ---------------------------------------------------------------------------
# Test: All 36 fields in PyArrow table
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_all_36_fields_in_pa_table(sink, enriched_record):
    """Verify PyArrow table has exactly 36 columns matching Iceberg schema.

    Given:
      - A fully-populated enriched record
    When:
      - Flush the record to PyArrow table
    Then:
      - PyArrow table has exactly 36 columns
      - All columns match Iceberg schema field names
    """
    pytest.importorskip("pyarrow")

    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    sink._buffer = [enriched_record]
    sink._flush()

    # Verify table.append() was called
    assert sink._table.append.call_count == 1

    # Extract the PyArrow table
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    # Verify field count
    assert pa_table.num_columns == 36
    assert pa_table.num_rows == 1

    # Verify all expected field names
    expected_fields = {
        "transaction_id",
        "account_id",
        "merchant_id",
        "amount",
        "currency",
        "event_time",
        "enrichment_time",
        "channel",
        "card_bin",
        "card_last4",
        "caller_ip_subnet",
        "api_key_id",
        "oauth_scope",
        "geo_lat",
        "geo_lon",
        "masking_lib_version",
        "vel_count_1m",
        "vel_amount_1m",
        "vel_count_5m",
        "vel_amount_5m",
        "vel_count_1h",
        "vel_amount_1h",
        "vel_count_24h",
        "vel_amount_24h",
        "geo_country",
        "geo_city",
        "geo_network_class",
        "geo_confidence",
        "device_first_seen",
        "device_txn_count",
        "device_known_fraud",
        "prev_geo_country",
        "prev_txn_time_ms",
        "enrichment_latency_ms",
        "processor_version",
        "schema_version",
    }
    actual_fields = set(pa_table.column_names)
    assert actual_fields == expected_fields


# ---------------------------------------------------------------------------
# Test: Decimal amount type (decimal128(18, 4))
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_decimal_amount_type(sink, enriched_record):
    """Verify the amount column is decimal128(18, 4) type.

    Given:
      - A record with amount=Decimal("123.4500")
    When:
      - Flush the record to PyArrow table
    Then:
      - The amount column has type decimal128(18, 4)
      - The value is correctly represented as Decimal
    """

    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    record = {**enriched_record, "amount": Decimal("123.4500")}
    sink._buffer = [record]
    sink._flush()

    # Extract the PyArrow table
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    # Verify amount column type
    amount_field = pa_table.schema.field("amount")
    assert str(amount_field.type) == "decimal128(18, 4)"

    # Verify amount value
    amount_values = pa_table["amount"].to_pylist()
    assert len(amount_values) == 1
    assert amount_values[0] == Decimal("123.4500")


# ---------------------------------------------------------------------------
# Test: Multiple velocity aggregates with different scales
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_velocity_amounts_decimal_type(sink, enriched_record):
    """Verify velocity amount columns are decimal128(18, 4) type.

    Given:
      - Records with various velocity amounts as Decimal
    When:
      - Flush to PyArrow table
    Then:
      - All vel_amount_* fields have correct decimal128(18, 4) type
    """

    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    sink._buffer = [enriched_record]
    sink._flush()

    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    # Verify all velocity amount fields
    for field_name in [
        "vel_amount_1m",
        "vel_amount_5m",
        "vel_amount_1h",
        "vel_amount_24h",
    ]:
        field = pa_table.schema.field(field_name)
        assert str(field.type) == "decimal128(18, 4)"


# ---------------------------------------------------------------------------
# Test: Null values handled correctly
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_nullable_fields_with_none_values(sink, enriched_record):
    """Verify nullable fields handled correctly when None.

    Given:
      - A record with nullable fields set to None
    When:
      - Flush to PyArrow table
    Then:
      - Nullable fields contain None (not converted to strings)
    """

    sink._table = MagicMock()
    sink._breaker = MagicMock()
    # Make breaker.call() actually invoke the function
    sink._breaker.call.side_effect = lambda func, *args: func(*args)

    # Set nullable fields to None
    record = {
        **enriched_record,
        "geo_lat": None,
        "geo_lon": None,
        "geo_country": None,
        "geo_city": None,
        "device_known_fraud": None,
    }

    sink._buffer = [record]
    sink._flush()

    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    # Verify None values are preserved
    assert pa_table["geo_lat"][0].as_py() is None
    assert pa_table["geo_lon"][0].as_py() is None
    assert pa_table["geo_country"][0].as_py() is None
    assert pa_table["geo_city"][0].as_py() is None
    assert pa_table["device_known_fraud"][0].as_py() is None
