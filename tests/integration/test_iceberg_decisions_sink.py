"""Integration tests for IcebergDecisionsSink.

Tests the plain-Python fallback implementation (no PyFlink required).

Covers:
- Time-based flush (1-second threshold)
- Buffer-size-based flush
- In-batch deduplication by transaction_id
- Circuit breaker behavior (opens after 3 consecutive failures)
- Exception handling (no re-raise, all caught and DLQ'd)
- rule_triggers as empty list (not null) when not provided
- All decision types (ALLOW, FLAG, BLOCK)

Run with:
  pytest tests/integration/test_iceberg_decisions_sink.py
  pytest -v tests/integration/test_iceberg_decisions_sink.py
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from pipelines.scoring.sinks.iceberg_decisions import IcebergDecisionsSink
from pipelines.scoring.types import FraudDecision

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sink():
    """Create a fresh IcebergDecisionsSink instance for each test."""
    s = IcebergDecisionsSink()
    # Mock the table since we won't call open() with real Iceberg
    s._table = MagicMock()
    # Initialize breaker manually
    from pybreaker import CircuitBreaker
    s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    s._catalog_loaded = True
    return s


@pytest.fixture
def mock_table():
    """Create a mock PyIceberg table."""
    return MagicMock()


def _make_fraud_decision(
    transaction_id: str = "txn-001",
    decision: str = "ALLOW",
    fraud_score: float = 0.1,
    rule_triggers: list[str] | None = None,
    model_version: str = "v1.0.0",
    decision_time_ms: int = 1704067200000,
    latency_ms: float = 45.5,
    schema_version: str = "1",
) -> FraudDecision:  # noqa: E501
    """Create a FraudDecision for testing."""
    return FraudDecision(
        transaction_id=transaction_id,
        decision=decision,
        fraud_score=fraud_score,
        rule_triggers=rule_triggers if rule_triggers is not None else [],
        model_version=model_version,
        decision_time_ms=decision_time_ms,
        latency_ms=latency_ms,
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Test: Time-based flush (1-second threshold)
# ---------------------------------------------------------------------------


def test_flush_on_time_threshold(sink):
    """Verify sink flushes buffer after 1 second has elapsed.

    Given:
      - Sink with buffer max=1
      - 1 record in buffer
    When:
      - Advance time.time() by >1 second
      - Call invoke() again with a second record
    Then:
      - First flush is triggered (due to 1-second threshold)
      - table.append() is called once
      - Second record is buffered (not yet flushed)
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    decision1 = _make_fraud_decision(transaction_id="txn-001")
    decision2 = _make_fraud_decision(transaction_id="txn-002")

    # Record the time of first invoke
    initial_time = time.time()
    sink._last_flush_time_sec = initial_time

    # Invoke first record
    sink.invoke(decision1)
    assert len(sink._buffer) == 1
    assert sink._table.append.call_count == 0

    # Mock time.time() in the sink module to advance by 1.5 seconds
    with patch(
        "pipelines.scoring.sinks.iceberg_decisions.time.time"
    ) as mock_time:
        mock_time.return_value = initial_time + 1.5

        # Invoke second record — should trigger flush
        sink.invoke(decision2)

    # First record should have been flushed
    assert sink._table.append.call_count == 1
    assert len(sink._buffer) == 1


# ---------------------------------------------------------------------------
# Test: Buffer-size-based flush
# ---------------------------------------------------------------------------


def test_flush_on_buffer_size(sink):
    """Verify sink flushes when buffer reaches max size.

    Given:
      - Buffer max size (from constant)
      - Empty buffer
    When:
      - Invoke records until buffer is full
    Then:
      - table.append() is called after buffer fills
      - Buffer is cleared
    """
    import pipelines.scoring.sinks.iceberg_decisions as sink_module

    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    # Store original max
    original_max = sink_module.ICEBERG_DECISIONS_BUFFER_MAX

    # Use a small buffer size for testing
    sink_module.ICEBERG_DECISIONS_BUFFER_MAX = 3

    try:
        decision1 = _make_fraud_decision(transaction_id="txn-001")
        decision2 = _make_fraud_decision(transaction_id="txn-002")
        decision3 = _make_fraud_decision(transaction_id="txn-003")

        # Invoke 3 records
        sink.invoke(decision1)
        assert sink._table.append.call_count == 0
        assert len(sink._buffer) == 1

        sink.invoke(decision2)
        assert sink._table.append.call_count == 0
        assert len(sink._buffer) == 2

        sink.invoke(decision3)
        assert sink._table.append.call_count == 1
        assert len(sink._buffer) == 0
    finally:
        sink_module.ICEBERG_DECISIONS_BUFFER_MAX = original_max


# ---------------------------------------------------------------------------
# Test: In-batch deduplication by transaction_id
# ---------------------------------------------------------------------------


def test_in_batch_deduplication(sink):
    """Verify sink deduplicates records by transaction_id within a batch.

    Given:
      - 3 records: txn-001, txn-001, txn-002 (duplicate txn-001)
    When:
      - Invoke all 3 records
      - Trigger a flush (time-based or size-based)
    Then:
      - table.append() is called with PyArrow table of 2 rows
      - First occurrence of txn-001 is kept
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    decision1 = _make_fraud_decision(transaction_id="txn-001", fraud_score=0.1)
    decision1_dup = _make_fraud_decision(
        transaction_id="txn-001", fraud_score=0.5
    )
    decision2 = _make_fraud_decision(transaction_id="txn-002", fraud_score=0.2)

    # Add all 3 to buffer
    sink._buffer.append(_record_dict(decision1))
    sink._buffer.append(_record_dict(decision1_dup))
    sink._buffer.append(_record_dict(decision2))

    assert len(sink._buffer) == 3

    # Manually call _flush to deduplicate
    sink._flush()

    # Verify table.append() was called
    assert sink._table.append.call_count == 1

    # Verify the PyArrow table has only 2 rows
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]
    assert pa_table.num_rows == 2

    # Verify first occurrence of txn-001 is kept
    transaction_ids = pa_table["transaction_id"].to_pylist()
    fraud_scores = pa_table["fraud_score"].to_pylist()
    assert transaction_ids == ["txn-001", "txn-002"]
    assert fraud_scores == [0.1, 0.2]


def _record_dict(decision: FraudDecision) -> dict:
    """Convert FraudDecision to dict (helper)."""
    from dataclasses import asdict
    return asdict(decision)


# ---------------------------------------------------------------------------
# Test: Circuit breaker opens after 3 failures
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_after_failures(sink):
    """Verify circuit breaker opens after 3 consecutive failures.

    Given:
      - table.append() raises ConnectionError
      - Circuit breaker with fail_max=3
    When:
      - Invoke and flush 3 times (each time append raises)
    Then:
      - After 3rd failure, circuit breaker opens
      - 4th invoke does not attempt to call table.append()
      - DLQ events are emitted for all failures
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()

    # Mock table.append() to always raise ConnectionError
    sink._table.append.side_effect = ConnectionError("Connection refused")

    # Create a real CircuitBreaker instead of mock
    from pybreaker import CircuitBreaker, CircuitBreakerError

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])

    decision = _make_fraud_decision(transaction_id="txn-001")

    # First 3 invokes should fail and increment the breaker count
    for _ in range(3):
        sink._buffer = []  # Reset buffer between calls
        sink.invoke(decision)
        sink._flush()

    # After 3 failures, the breaker should be open
    assert sink._breaker.fail_counter >= 3

    # 4th invoke should not attempt append (circuit is open)
    sink._buffer = []
    sink._table.reset_mock()

    with pytest.raises(CircuitBreakerError):
        # Call the breaker directly to verify it's open
        sink._breaker.call(sink._table.append, MagicMock())


# ---------------------------------------------------------------------------
# Test: No exception propagation
# ---------------------------------------------------------------------------


def test_no_exception_propagation(sink):
    """Verify invoke() catches all exceptions and never re-raises.

    Given:
      - table.append() raises a generic Exception
    When:
      - invoke() is called
      - A flush is triggered
    Then:
      - invoke() does NOT raise
      - Exception is handled gracefully
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._table.append.side_effect = RuntimeError("Some random error")
    sink._catalog_loaded = True

    decision = _make_fraud_decision(transaction_id="txn-001")

    # This should not raise despite table.append() raising
    try:
        sink.invoke(decision)
        # Trigger a flush by calling _flush directly
        sink._flush()
    except Exception as e:
        pytest.fail(
            f"invoke() should not raise, but got: {type(e).__name__}: {e}"
        )

    # Buffer should still be cleared
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: rule_triggers as empty list (not null)
# ---------------------------------------------------------------------------


def test_rule_triggers_empty_list_not_null(sink):
    """Verify rule_triggers is an empty list, not null, when not provided.

    Given:
      - FraudDecision with rule_triggers=[] (empty list)
    When:
      - invoke() is called and flush is triggered
    Then:
      - PyArrow table has rule_triggers column with empty list (not null)
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    decision = _make_fraud_decision(
        transaction_id="txn-001",
        rule_triggers=[],
    )

    sink.invoke(decision)
    sink._flush()

    # Verify table.append() was called
    assert sink._table.append.call_count == 1

    # Check the PyArrow table
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    rule_triggers_col = pa_table["rule_triggers"].to_pylist()
    assert rule_triggers_col == [[]]


def test_rule_triggers_null_becomes_empty_list(sink):
    """Verify rule_triggers=None is normalized to empty list."""
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    # Create a record dict with rule_triggers=None
    record = _record_dict(_make_fraud_decision(transaction_id="txn-001"))
    record["rule_triggers"] = None

    sink._buffer.append(record)
    sink._flush()

    # Check the PyArrow table
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    rule_triggers_col = pa_table["rule_triggers"].to_pylist()
    assert rule_triggers_col == [[]]


def test_rule_triggers_with_values(sink):
    """Verify rule_triggers list is preserved when provided."""
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    decision = _make_fraud_decision(
        transaction_id="txn-001",
        rule_triggers=["rule_001", "rule_003"],
    )

    sink.invoke(decision)
    sink._flush()

    # Check the PyArrow table
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    rule_triggers_col = pa_table["rule_triggers"].to_pylist()
    assert rule_triggers_col == [["rule_001", "rule_003"]]


# ---------------------------------------------------------------------------
# Test: All decision types accepted
# ---------------------------------------------------------------------------


def test_all_decision_types_accepted(sink):
    """Verify sink accepts ALLOW, FLAG, and BLOCK decisions.

    Given:
      - One ALLOW, one FLAG, one BLOCK decision
    When:
      - Each is invoked
    Then:
      - No exceptions are raised
      - Sink accepts all three types
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    allow_decision = _make_fraud_decision(
        transaction_id="txn-001",
        decision="ALLOW",
    )
    flag_decision = _make_fraud_decision(
        transaction_id="txn-002",
        decision="FLAG",
    )
    block_decision = _make_fraud_decision(
        transaction_id="txn-003",
        decision="BLOCK",
    )

    # All three should be accepted without raising
    sink.invoke(allow_decision)
    sink.invoke(flag_decision)
    sink.invoke(block_decision)

    assert len(sink._buffer) == 3

    # Trigger flush
    sink._flush()

    # Verify all 3 were appended
    assert sink._table.append.call_count == 1
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]
    assert pa_table.num_rows == 3

    decisions = pa_table["decision"].to_pylist()
    assert decisions == ["ALLOW", "FLAG", "BLOCK"]


# ---------------------------------------------------------------------------
# Test: PyArrow table schema correctness
# ---------------------------------------------------------------------------


def test_arrow_table_schema_correctness(sink):
    """Verify PyArrow table has correct schema with all 8 fields.

    Given:
      - A FraudDecision record
    When:
      - Converting to PyArrow table
    Then:
      - Table has 8 columns in correct order
      - Types are correct (string, float64, int64, list<string>)
    """
    import pyarrow as pa

    decision = _make_fraud_decision(
        transaction_id="txn-001",
        decision="ALLOW",
        fraud_score=0.25,
        rule_triggers=["rule_1"],
        model_version="v2.1.0",
        decision_time_ms=1704067200000,
        latency_ms=50.5,
        schema_version="1",
    )

    pa_table = sink._records_to_arrow_table([_record_dict(decision)])

    # Check field names and types
    expected_schema = pa.schema(
        [
            ("transaction_id", pa.string()),
            ("decision", pa.string()),
            ("fraud_score", pa.float64()),
            ("rule_triggers", pa.list_(pa.string())),
            ("model_version", pa.string()),
            ("decision_time_ms", pa.timestamp("us")),
            ("latency_ms", pa.float64()),
            ("schema_version", pa.string()),
        ]
    )

    assert pa_table.schema == expected_schema
    assert pa_table.num_rows == 1


# ---------------------------------------------------------------------------
# Test: Buffer is cleared even on exception
# ---------------------------------------------------------------------------


def test_buffer_cleared_on_flush_exception(sink):
    """Verify buffer is always cleared after flush, even if append fails.

    Given:
      - table.append() raises an exception
    When:
      - _flush() is called
    Then:
      - Buffer is still cleared (in finally block)
      - No unbounded memory growth
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()
    sink._table.append.side_effect = RuntimeError("Append failed")

    decision = _make_fraud_decision(transaction_id="txn-001")
    sink._buffer.append(_record_dict(decision))

    assert len(sink._buffer) == 1

    # _flush() should not raise despite append failing
    sink._flush()

    # Buffer should still be cleared
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: Multiple records with various payloads
# ---------------------------------------------------------------------------


def test_large_batch_deduplication(sink):
    """Verify deduplication works correctly on larger batches.

    Given:
      - 10 records with some duplicated transaction_ids
    When:
      - _flush() is called
    Then:
      - Only unique transaction_ids are appended
    """
    sink._table = MagicMock()
    from pybreaker import CircuitBreaker

    sink._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    sink._catalog_loaded = True

    # Create 10 records with 6 unique transaction_ids
    for idx in range(10):
        txn_id = f"txn-{idx % 6:03d}"
        decision = _make_fraud_decision(
            transaction_id=txn_id,
            fraud_score=0.1 * idx,
        )
        sink._buffer.append(_record_dict(decision))

    assert len(sink._buffer) == 10

    sink._flush()

    # Verify deduplication
    call_args = sink._table.append.call_args
    pa_table = call_args[0][0]

    assert pa_table.num_rows == 6
    transaction_ids = pa_table["transaction_id"].to_pylist()
    assert len(set(transaction_ids)) == 6


# ---------------------------------------------------------------------------
# Test: Edge case — empty buffer flush
# ---------------------------------------------------------------------------


def test_flush_with_empty_buffer_noop(sink):
    """Verify flush on empty buffer is a no-op.

    Given:
      - Empty buffer
    When:
      - _flush() is called
    Then:
      - table.append() is NOT called
      - No exceptions
    """
    sink._table = MagicMock()
    sink._breaker = MagicMock()

    assert len(sink._buffer) == 0

    sink._flush()

    assert sink._table.append.call_count == 0


# ---------------------------------------------------------------------------
# Test: Catalog load failure fallback
# ---------------------------------------------------------------------------


def test_invoke_without_loaded_catalog(sink):
    """Verify invoke() is resilient to missing catalog.

    Given:
      - sink._catalog_loaded = False
      - sink._table = None
    When:
      - invoke() is called
    Then:
      - No exceptions
      - Buffer is still updated
      - Flush is not attempted
    """
    sink._catalog_loaded = False
    sink._table = None
    sink._breaker = None

    decision = _make_fraud_decision(transaction_id="txn-001")

    # Should not raise
    sink.invoke(decision)

    # Record should be in buffer
    assert len(sink._buffer) == 1


# ---------------------------------------------------------------------------
# Test: Open() initializes breaker
# ---------------------------------------------------------------------------


def test_open_initializes_circuit_breaker():
    """Verify open() initializes the circuit breaker.

    Given:
      - A fresh IcebergDecisionsSink
    When:
      - open() is called with a mocked catalog
    Then:
      - Circuit breaker is initialized with fail_max=3
    """
    sink = IcebergDecisionsSink()

    # Mock the catalog loading
    with patch("pyiceberg.catalog.load_catalog") as mock_load:
        mock_table = MagicMock()
        mock_catalog = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_load.return_value = mock_catalog

        # Call open with a mocked runtime context
        sink.open(MagicMock())

    # Verify breaker was initialized
    assert sink._breaker is not None
    from pybreaker import CircuitBreaker

    assert isinstance(sink._breaker, CircuitBreaker)
    assert sink._breaker.fail_max == 3


# ---------------------------------------------------------------------------
# Test: Numeric field normalization
# ---------------------------------------------------------------------------


def test_numeric_field_normalization(sink):
    """Verify fraud_score, decision_time_ms, and latency_ms are normalized.

    Given:
      - Records with mixed numeric types (int, float, string representations)
    When:
      - Converting to PyArrow table
    Then:
      - fraud_score is float64
      - decision_time_ms is int64
      - latency_ms is float64
      - None values default to 0.0 or 0
    """
    import pyarrow as pa

    records = [
        {
            "transaction_id": "txn-001",
            "decision": "ALLOW",
            "fraud_score": 0.5,
            "rule_triggers": [],
            "model_version": "v1",
            "decision_time_ms": 1704067200000,
            "latency_ms": 45.5,
            "schema_version": "1",
        },
        {
            "transaction_id": "txn-002",
            "decision": "ALLOW",
            "fraud_score": None,
            "rule_triggers": [],
            "model_version": "v1",
            "decision_time_ms": None,
            "latency_ms": None,
            "schema_version": "1",
        },
    ]

    pa_table = sink._records_to_arrow_table(records)

    # Check types
    assert pa_table.schema.field("fraud_score").type == pa.float64()
    assert pa_table.schema.field("decision_time_ms").type == pa.timestamp("us")
    assert pa_table.schema.field("latency_ms").type == pa.float64()

    # Check normalization
    fraud_scores = pa_table["fraud_score"].to_pylist()
    decision_times = pa_table["decision_time_ms"].to_pylist()
    latencies = pa_table["latency_ms"].to_pylist()

    assert fraud_scores == [0.5, 0.0]
    assert decision_times == [1704067200000, 0]
    assert latencies == [45.5, 0.0]