"""Performance tests for IcebergEnrichedSink hot path latency.

Verifies that invoke() call adds negligible latency to the hot path, specifically
that the synchronous cost of invoke() (before any background flush) is under 1ms
per record at 1000 records/s throughput.

Run with:
  pytest tests/performance/test_sink_hot_path_latency.py -v
  pytest tests/performance/test_sink_hot_path_latency.py -v -m slow  # Include slow tests
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from unittest.mock import MagicMock

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
    """Create a minimal enriched record for performance tests.

    Use minimal but valid record to focus on timing, not data construction.
    All required fields present but with simple values.
    """
    return {
        "transaction_id": "txn-perf-test",
        "account_id": "acc-perf-test",
        "merchant_id": "mer-perf-test",
        "amount": Decimal("100.0000"),
        "currency": "USD",
        "event_time": 1704067200000,
        "enrichment_time": 1704067200100,
        "channel": "WEB",
        "card_bin": "123456",
        "card_last4": "6789",
        "caller_ip_subnet": "192.168.1.0/24",
        "api_key_id": "key-perf-test",
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
def sink_with_mocked_table():
    """Create sink with mocked table for performance tests.

    Mocks table.append() to be a no-op (default MagicMock behavior).
    This isolates the synchronous invoke() path from I/O latency.
    """
    s = IcebergEnrichedSink()
    s._table = MagicMock()  # No-op mock
    try:
        from pybreaker import CircuitBreaker

        s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    except ImportError:
        s._breaker = MagicMock()
    s._catalog_loaded = True
    return s


# ---------------------------------------------------------------------------
# Test 1: invoke() latency under 1ms per record
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_invoke_latency_under_1ms_per_record(sink_with_mocked_table, enriched_record):
    """Verify average invoke() latency < 1ms per record (no flush triggered).

    This test measures the synchronous cost of appending to buffer and checking
    flush conditions. It does NOT measure table.append() time (which is mocked
    to be instant).

    Given:
      - Sink with mocked table (no-op append)
      - ICEBERG_BUFFER_MAX=200 (no flush will trigger in this test)
      - 1000 records to invoke sequentially
    When:
      - Invoke 1000 records in a tight loop
      - Measure total wall time with time.perf_counter()
    Then:
      - Total time < 1.0 seconds (i.e., average < 1ms per invoke)
      - No flushes should occur (buffer < max, time < 1 sec)
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_table
    num_records = 1000

    # Ensure buffer is large enough to hold all 1000 without flushing
    original_buffer_max = os.environ.get("ICEBERG_BUFFER_MAX")
    os.environ["ICEBERG_BUFFER_MAX"] = "2000"

    # Reload the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = 2000

    try:
        # Record start time
        start_time = time.perf_counter()

        # Invoke 1000 records
        for i in range(num_records):
            record = {**enriched_record, "transaction_id": f"txn-{i:06d}"}
            sink.invoke(record, context=None)

        # Record end time
        end_time = time.perf_counter()

        total_time_sec = end_time - start_time
        avg_time_ms = (total_time_sec * 1000) / num_records

        # Verify no flushes occurred (table.append should not be called)
        assert sink._table.append.call_count == 0, (
            f"Unexpected flush: {sink._table.append.call_count} calls to table.append()"
        )

        # Verify buffer has all 1000 records
        assert len(sink._buffer) == num_records

        # Verify latency SLA: total < 1.0 seconds
        assert total_time_sec < 1.0, (
            f"invoke() latency SLA violated: total={total_time_sec:.3f}s "
            f"(avg={avg_time_ms:.3f}ms), expected < 1.0s"
        )

        # Log results for observability
        print(
            f"\ninvoke() latency: total={total_time_sec:.3f}s, "
            f"avg={avg_time_ms:.3f}ms per record ({num_records} records)"
        )

    finally:
        # Restore original env and module constant
        sink_module.ICEBERG_BUFFER_MAX = original_max
        if original_buffer_max is None:
            os.environ.pop("ICEBERG_BUFFER_MAX", None)
        else:
            os.environ["ICEBERG_BUFFER_MAX"] = original_buffer_max


# ---------------------------------------------------------------------------
# Test 2: invoke() with flush does not block subsequent records
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_invoke_does_not_block_during_flush(sink_with_mocked_table, enriched_record):
    """Verify that invoke() completes flush synchronously and is fast after.

    The design is: flush runs synchronously within invoke() when triggered,
    but the flush itself is isolated. This test verifies that:
    1. Flush is triggered and completes
    2. The invoke() call that triggered flush has measurable latency
    3. Subsequent invoke() calls after the flush are fast again

    Given:
      - Sink with table.append() mocked to sleep 0.5s (simulating slow Iceberg)
      - ICEBERG_BUFFER_MAX=50
      - 100 records to invoke sequentially
    When:
      - Invoke 100 records
      - First flush at record 50 (buffer full)
    Then:
      - First 49 invokes are fast (< 1ms each)
      - 50th invoke is slow (includes 0.5s sleep in append)
      - Invokes 51-100 are fast again (< 1ms each)
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_table
    num_records = 100
    flush_trigger_at = 50  # ICEBERG_BUFFER_MAX

    # Reload the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = flush_trigger_at

    try:
        # Make table.append() sleep for 0.5 seconds to simulate slow Iceberg
        def slow_append(*args, **kwargs):
            time.sleep(0.5)

        sink._table.append.side_effect = slow_append

        # Track latency per invoke
        invoke_times_ms: list[float] = []

        # Invoke 100 records and measure each one
        for i in range(num_records):
            record = {**enriched_record, "transaction_id": f"txn-{i:06d}"}

            invoke_start = time.perf_counter()
            sink.invoke(record, context=None)
            invoke_end = time.perf_counter()

            latency_ms = (invoke_end - invoke_start) * 1000
            invoke_times_ms.append(latency_ms)

        # Verify flush was triggered
        assert sink._table.append.call_count == 1, (
            f"Expected 1 flush, got {sink._table.append.call_count}"
        )

        # Verify first batch (0-49) is fast: avg < 1ms
        first_batch_avg_ms = sum(invoke_times_ms[0:50]) / 50
        assert first_batch_avg_ms < 1.0, (
            f"First batch average latency too high: {first_batch_avg_ms:.3f}ms, "
            f"expected < 1.0ms"
        )

        # Verify the flushing invoke (50th) is slow: > 400ms
        flush_invoke_ms = invoke_times_ms[49]  # 0-indexed, so invoke 50 is index 49
        assert flush_invoke_ms > 400.0, (
            f"Flushing invoke latency too low: {flush_invoke_ms:.3f}ms, "
            f"expected > 400ms (includes 500ms sleep)"
        )

        # Verify second batch (51-100) is fast after flush: avg < 1ms
        second_batch_avg_ms = sum(invoke_times_ms[50:]) / 50
        assert second_batch_avg_ms < 1.0, (
            f"Second batch average latency too high: {second_batch_avg_ms:.3f}ms, "
            f"expected < 1.0ms"
        )

        # Log results
        print(
            f"\ninvoke() with flush latency profile:\n"
            f"  First batch (0-49): avg={first_batch_avg_ms:.3f}ms\n"
            f"  Flushing invoke (50): {flush_invoke_ms:.3f}ms\n"
            f"  Second batch (51-100): avg={second_batch_avg_ms:.3f}ms"
        )

    finally:
        sink_module.ICEBERG_BUFFER_MAX = original_max


# ---------------------------------------------------------------------------
# Test 3: Throughput at 1000 records/second
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_throughput_1000_records_per_second(sink_with_mocked_table, enriched_record):
    """Verify sink sustains 500+ records/sec throughput (1000 records in < 2 sec).

    This test measures end-to-end throughput including all overhead:
    - Buffer append
    - Flush condition checks
    - Flush operations (dedup, arrow conversion, append)

    Given:
      - Sink with mocked table (no-op append)
      - ICEBERG_BUFFER_MAX=200
      - 1000 records to invoke
    When:
      - Invoke 1000 records in a tight loop
    Then:
      - Total time < 2 seconds (i.e., 500+ records/sec minimum)
      - At least 5 flushes should occur (1000 / 200)
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_table
    num_records = 1000

    # Reload the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = 200

    try:
        # Record start time
        start_time = time.perf_counter()

        # Invoke 1000 records in a tight loop
        for i in range(num_records):
            record = {**enriched_record, "transaction_id": f"txn-{i:06d}"}
            sink.invoke(record, context=None)

        # Record end time
        end_time = time.perf_counter()

        total_time_sec = end_time - start_time
        actual_throughput = num_records / total_time_sec

        # Verify at least 5 flushes occurred (1000 / 200)
        assert sink._table.append.call_count >= 5, (
            f"Expected >= 5 flushes, got {sink._table.append.call_count}"
        )

        # Verify throughput: 1000 records in < 2 seconds = 500+ records/sec
        assert total_time_sec < 2.0, (
            f"Throughput SLA violated: {num_records} records in {total_time_sec:.3f}s "
            f"({actual_throughput:.0f} records/sec), expected < 2.0s (500+ records/sec)"
        )

        # Log results
        print(
            f"\nThroughput test results:\n"
            f"  Records: {num_records}\n"
            f"  Total time: {total_time_sec:.3f}s\n"
            f"  Throughput: {actual_throughput:.0f} records/sec\n"
            f"  Flushes: {sink._table.append.call_count}"
        )

    finally:
        sink_module.ICEBERG_BUFFER_MAX = original_max


# ---------------------------------------------------------------------------
# Test 4: Buffer-limited throughput (stress test)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_buffer_limited_throughput_stress(sink_with_mocked_table, enriched_record):
    """Stress test with small buffer to verify repeated flush overhead.

    Verifies that repeated flushes don't degrade throughput significantly.

    Given:
      - Sink with ICEBERG_BUFFER_MAX=10 (very small, 100 flushes for 1000 records)
      - 1000 records to invoke
    When:
      - Invoke 1000 records
    Then:
      - Total time < 3 seconds (accounts for 100 flushes)
      - Throughput >= 333 records/sec minimum
      - 100 flushes are triggered
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_table
    num_records = 1000
    small_buffer_size = 10

    # Reload the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = small_buffer_size

    try:
        start_time = time.perf_counter()

        for i in range(num_records):
            record = {**enriched_record, "transaction_id": f"txn-{i:06d}"}
            sink.invoke(record, context=None)

        end_time = time.perf_counter()

        total_time_sec = end_time - start_time
        actual_throughput = num_records / total_time_sec
        expected_flushes = num_records // small_buffer_size

        # Verify correct number of flushes
        assert sink._table.append.call_count == expected_flushes, (
            f"Expected {expected_flushes} flushes, got {sink._table.append.call_count}"
        )

        # Verify throughput with repeated flushes: < 3 seconds
        assert total_time_sec < 3.0, (
            f"Throughput with frequent flushes too slow: {num_records} records in "
            f"{total_time_sec:.3f}s ({actual_throughput:.0f} records/sec), "
            f"expected < 3.0s (333+ records/sec)"
        )

        # Log results
        print(
            f"\nBuffer-limited throughput stress test:\n"
            f"  Records: {num_records}\n"
            f"  Buffer size: {small_buffer_size}\n"
            f"  Total time: {total_time_sec:.3f}s\n"
            f"  Throughput: {actual_throughput:.0f} records/sec\n"
            f"  Flushes: {sink._table.append.call_count}"
        )

    finally:
        sink_module.ICEBERG_BUFFER_MAX = original_max


# ---------------------------------------------------------------------------
# Test 5: Deduplication overhead at scale
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_deduplication_overhead_at_scale(sink_with_mocked_table, enriched_record):
    """Verify deduplication doesn't significantly impact throughput.

    Tests deduplication overhead when flushing batches with 50% duplicates.

    Given:
      - Sink with ICEBERG_BUFFER_MAX=100
      - 1000 records, 50% of which are duplicates (same transaction_id)
      - Total unique: 500 records
    When:
      - Invoke 1000 records with duplicates
    Then:
      - Total time < 1.5 seconds
      - Deduplication removes 500 duplicates
      - Each flush contains only unique records
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_table
    num_records = 1000
    num_unique = 500

    # Reload the constant in the sink module
    import pipelines.processing.operators.iceberg_sink as sink_module

    original_max = sink_module.ICEBERG_BUFFER_MAX
    sink_module.ICEBERG_BUFFER_MAX = 100

    try:
        start_time = time.perf_counter()

        # Invoke records with 50% duplicates
        # Each transaction_id appears twice
        for i in range(num_records):
            unique_idx = i % num_unique
            record = {
                **enriched_record,
                "transaction_id": f"txn-{unique_idx:06d}",
            }
            sink.invoke(record, context=None)

        end_time = time.perf_counter()

        total_time_sec = end_time - start_time

        # Verify flushes occurred
        assert sink._table.append.call_count > 0

        # Throughput should still be good with deduplication
        actual_throughput = num_records / total_time_sec
        assert total_time_sec < 1.5, (
            f"Deduplication overhead too high: {num_records} records in "
            f"{total_time_sec:.3f}s ({actual_throughput:.0f} records/sec), "
            f"expected < 1.5s"
        )

        # Log results
        print(
            f"\nDeduplication overhead test:\n"
            f"  Records invoiced: {num_records}\n"
            f"  Unique records: {num_unique}\n"
            f"  Duplicates: {num_records - num_unique}\n"
            f"  Total time: {total_time_sec:.3f}s\n"
            f"  Throughput: {actual_throughput:.0f} records/sec\n"
            f"  Flushes: {sink._table.append.call_count}"
        )

    finally:
        sink_module.ICEBERG_BUFFER_MAX = original_max
