"""Integration tests for circuit breaker behavior in IcebergEnrichedSink.

Tests the pybreaker.CircuitBreaker integration:
- Circuit opens after 3 consecutive failures
- Circuit open prevents append() calls (DLQ'ing instead)
- Circuit transitions to HALF_OPEN after reset_timeout (30s)
- Circuit allows retry in HALF_OPEN state
- Successful operation in HALF_OPEN closes the circuit
- No exceptions propagate to the hot path

Run with:
  pytest tests/integration/test_circuit_breaker.py -v
"""

from __future__ import annotations

import json
import logging
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


def _has_pybreaker() -> bool:
    """Check if pybreaker is available."""
    try:
        from pybreaker import CircuitBreaker  # noqa: F401
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
def sink_with_breaker():
    """Create IcebergEnrichedSink with real CircuitBreaker."""
    try:
        from pybreaker import CircuitBreaker
    except ImportError:
        pytest.skip("pybreaker not installed")

    s = IcebergEnrichedSink()
    s._table = MagicMock()
    s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    s._catalog_loaded = True
    return s


# ---------------------------------------------------------------------------
# Test: Circuit opens after 3 failures
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_opens_after_three_failures(sink_with_breaker, enriched_record):
    """Verify circuit breaker opens after 3 consecutive failures.

    Given:
      - CircuitBreaker with fail_max=3
      - table.append() raises ConnectionError on every call
    When:
      - Invoke and flush 3 times
    Then:
      - After 3rd failure, circuit breaker is OPEN
      - circuit_breaker.current_state == "open"
    """
    from pybreaker import CircuitBreakerError

    sink = sink_with_breaker
    sink._table.append.side_effect = ConnectionError("Connection refused")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # Trigger 3 failures by flushing 3 times
    for i in range(3):
        sink._buffer = [record]
        try:
            sink._flush()
        except Exception:
            pass  # Expected to be caught in _flush()

    # After 3 failures, the circuit should be OPEN
    assert sink._breaker.current_state == "open"


# ---------------------------------------------------------------------------
# Test: Circuit open skips append
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_open_skips_append(sink_with_breaker, enriched_record):
    """Verify circuit open prevents table.append() calls.

    Given:
      - Circuit breaker is already OPEN
      - table.append() is mocked
    When:
      - Try to flush more records
    Then:
      - table.append() is NOT called
      - CircuitBreakerError is raised by breaker.call()
    """
    from pybreaker import CircuitBreakerError

    sink = sink_with_breaker

    # Force circuit to open state
    sink._breaker.open()
    assert sink._breaker.current_state == "open"

    sink._table.reset_mock()
    sink._table.append.side_effect = Exception("Should not be called")

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]

    # _flush() will catch the CircuitBreakerError internally
    sink._flush()

    # Verify append() was never called (because circuit was open)
    assert sink._table.append.call_count == 0


# ---------------------------------------------------------------------------
# Test: Circuit half-open allows retry
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_half_open_allows_retry(sink_with_breaker, enriched_record):
    """Verify circuit in HALF_OPEN state allows one retry attempt.

    Given:
      - Circuit breaker is in HALF_OPEN state
      - table.append() succeeds
    When:
      - Flush a record
    Then:
      - table.append() is called (circuit allows one attempt)
      - Circuit transitions to CLOSED after success
    """
    sink = sink_with_breaker

    # Manually set circuit to HALF_OPEN state
    # (in real scenario, this happens after reset_timeout expires)
    sink._breaker.open()
    # Simulate reset_timeout expiring by setting last_failure_time in the past
    import time
    sink._breaker._last_failure_time = time.time() - 31  # 31 seconds ago

    # Reset mock and make append() succeed
    sink._table.reset_mock()
    sink._table.append.side_effect = None

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]
    sink._flush()

    # Verify append() was called (circuit allowed the attempt)
    assert sink._table.append.call_count == 1
    # After successful call, circuit should be CLOSED
    assert sink._breaker.current_state == "closed"


# ---------------------------------------------------------------------------
# Test: Circuit open emits DLQ event
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_open_emits_dlq_event(sink_with_breaker, enriched_record):
    """Verify circuit open causes DLQ event emission.

    Given:
      - Circuit breaker is OPEN
      - DLQ logger is captured
    When:
      - Flush a record
    Then:
      - DLQ logger receives a log record with "circuit_open" in the message
    """
    sink = sink_with_breaker

    # Force circuit to open
    sink._breaker.open()
    assert sink._breaker.current_state == "open"

    record = {**enriched_record, "transaction_id": "txn-circuit-001"}
    sink._buffer = [record]

    # Capture DLQ logs
    with patch(
        "pipelines.processing.operators.iceberg_sink.dlq_logger"
    ) as mock_dlq_logger:
        sink._flush()
        # Verify DLQ logger was called
        mock_dlq_logger.warning.assert_called()

        # Get the log call arguments
        call_args = mock_dlq_logger.warning.call_args
        if call_args:
            logged_message = call_args[0][0]
            # Parse the JSON log to verify "circuit_open" is in the reason
            dlq_event = json.loads(logged_message)
            assert dlq_event["reason"] == "circuit_open"


# ---------------------------------------------------------------------------
# Test: Circuit open does not raise exception
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_open_does_not_raise(sink_with_breaker, enriched_record):
    """Verify circuit open does not propagate exceptions.

    Given:
      - Circuit breaker is OPEN
    When:
      - Call _flush()
    Then:
      - No exception is raised to the caller
      - Buffer is cleared
    """
    sink = sink_with_breaker

    # Force circuit to open
    sink._breaker.open()
    assert sink._breaker.current_state == "open"

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]

    # This should NOT raise any exception
    try:
        sink._flush()
    except Exception as e:
        pytest.fail(f"_flush() raised unexpected exception: {e}")

    # Verify buffer was cleared
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: Circuit resets after successful flush
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_circuit_resets_after_successful_flush(sink_with_breaker, enriched_record):
    """Verify circuit stays CLOSED after successful operations.

    Given:
      - table.append() initially fails, then succeeds
    When:
      - Flush twice (first fails, second succeeds)
    Then:
      - Circuit remains in CLOSED state (hasn't reached fail_max=3)
    """
    sink = sink_with_breaker

    record = {**enriched_record, "transaction_id": "txn-001"}

    # First flush: simulate failure
    sink._table.append.side_effect = ConnectionError("Transient error")
    sink._buffer = [record]
    sink._flush()

    # Second flush: success (side_effect removed)
    sink._table.append.side_effect = None
    sink._table.reset_mock()
    sink._buffer = [record]
    sink._flush()

    # Circuit should still be CLOSED (only 1 failure, need 3)
    assert sink._breaker.current_state == "closed"
    # Verify append was called on second flush
    assert sink._table.append.call_count == 1


# ---------------------------------------------------------------------------
# Test: Multiple failures trigger circuit breaker state change
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_has_pyarrow() and _has_pybreaker()),
    reason="pyarrow and pybreaker required",
)
def test_multiple_failures_trigger_breaker(sink_with_breaker, enriched_record):
    """Verify fail_counter increments correctly.

    Given:
      - CircuitBreaker with fail_max=3
      - table.append() raises ConnectionError
    When:
      - Flush 3 times
    Then:
      - fail_counter == 3
      - current_state == "open"
    """
    sink = sink_with_breaker

    sink._table.append.side_effect = ConnectionError("Connection refused")

    record = {**enriched_record, "transaction_id": "txn-001"}

    # Flush 3 times to trigger the circuit breaker
    for _ in range(3):
        sink._buffer = [record]
        sink._flush()

    # After 3 failures, the breaker should be open
    assert sink._breaker.fail_counter >= 3
    assert sink._breaker.current_state == "open"
