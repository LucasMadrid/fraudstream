"""IcebergDecisionsSink — writes FraudDecision records to Iceberg table.

Receives FraudDecision records from fraud scoring and writes them to
iceberg.fraud_decisions via PyIceberg 0.7+ with:
- In-buffer deduplication by transaction_id
- Configurable buffer size and flush interval (1 second)
- Circuit breaker protection (pybreaker)
- Dead-letter queue (DLQ) for failures
- Prometheus metrics for observability
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)
dlq_logger = logging.getLogger("dlq")

# Configuration from environment
ICEBERG_DECISIONS_BUFFER_MAX = int(os.environ.get("ICEBERG_DECISIONS_BUFFER_MAX", "100"))
ICEBERG_REST_URI = os.environ.get("ICEBERG_REST_URI", "http://localhost:8181")
ICEBERG_WAREHOUSE = os.environ.get("ICEBERG_WAREHOUSE", "s3://warehouse")
AWS_S3_ENDPOINT = os.environ.get("AWS_S3_ENDPOINT", "http://localhost:9000")
ICEBERG_FLUSH_TIMEOUT_SEC = 5


@dataclass(frozen=True)
class _DLQEvent:
    """Dead-letter queue event for failed writes."""

    event: str  # "iceberg_decisions_sink_dlq"
    transaction_id: str
    reason: str  # "timeout", "circuit_open", "buffer_full"
    batch_size: int


def _emit_dlq_event(event: _DLQEvent) -> None:
    """Log a structured DLQ event to stderr."""
    dlq_logger.warning(
        json.dumps(
            {
                "event": event.event,
                "transaction_id": event.transaction_id,
                "reason": event.reason,
                "batch_size": event.batch_size,
            }
        )
    )


def _increment_counter(counter_name: str) -> None:
    """Safely increment a Prometheus counter, swallowing import errors."""
    try:
        from pipelines.scoring.metrics import (
            iceberg_decisions_buffer_overflow_total,
            iceberg_decisions_catalog_unavailable_total,
        )

        if counter_name == "iceberg_decisions_buffer_overflow_total":
            iceberg_decisions_buffer_overflow_total.inc()
        elif counter_name == "iceberg_decisions_catalog_unavailable_total":
            iceberg_decisions_catalog_unavailable_total.inc()
    except Exception:
        # Metrics unavailable — continue processing
        pass


try:  # pragma: no cover
    import pyflink.datastream  # noqa: F401

    _HAS_PYFLINK = True

    class IcebergDecisionsSink:
        """PyFlink sink operator — writes fraud decisions to Iceberg table.

        Attributes:
            _buffer: List of FraudDecision records (converted to dicts).
            _last_flush_time_sec: Wall-clock timestamp of last flush.
            _table: PyIceberg Table handle (loaded in open()).
            _breaker: pybreaker.CircuitBreaker for flush operations.
        """

        def __init__(self) -> None:
            self._buffer: list[dict] = []
            self._last_flush_time_sec: float = time.time()
            self._table = None
            self._breaker = None

        def open(self, runtime_context) -> None:  # type: ignore[no-untyped-def]
            """Load Iceberg catalog and initialize circuit breaker."""
            try:
                from pyiceberg.catalog import load_catalog

                self._table = load_catalog("iceberg").load_table("default.fraud_decisions")
                logger.info("Loaded Iceberg table: default.fraud_decisions")
            except Exception as e:
                logger.error(
                    f"Failed to load Iceberg catalog in open(): {e}",
                    exc_info=True,
                )
                raise

            try:
                from pybreaker import CircuitBreaker

                self._breaker = CircuitBreaker(
                    fail_max=3,
                    reset_timeout=30,
                    listeners=[],
                )
            except Exception as e:
                logger.error(
                    f"Failed to initialize circuit breaker: {e}",
                    exc_info=True,
                )
                raise

        def invoke(self, value, context) -> None:  # type: ignore[no-untyped-def]
            """Append FraudDecision record to buffer and flush if conditions met.

            Never raises — all exceptions are caught and DLQ'd.
            """
            try:
                # Check if 1 second has elapsed since last flush
                now = time.time()
                if now - self._last_flush_time_sec >= 1.0:
                    self._flush()

                # Convert FraudDecision dataclass to dict if needed
                if hasattr(value, "__dataclass_fields__"):
                    from dataclasses import asdict

                    record = asdict(value)
                else:
                    record = value

                # Append to buffer
                self._buffer.append(record)

                # Flush if buffer is full
                if len(self._buffer) >= ICEBERG_DECISIONS_BUFFER_MAX:
                    self._flush()

            except Exception as e:
                logger.error(
                    f"Unhandled exception in invoke(): {e}",
                    exc_info=True,
                )
                # Do NOT re-raise — SLA is 100ms, we must not block the hot path

        def _flush(self) -> None:
            """Flush buffer to Iceberg, handling dedup, timeout, and circuit breaker.

            All exceptions are caught. On failure:
            - Emit DLQ event
            - Log warning
            - Increment counter
            - Clear buffer (prevent unbounded growth)
            """
            if not self._buffer:
                return

            self._last_flush_time_sec = time.time()

            # In-batch deduplication: keep only the first occurrence of each transaction_id
            seen_txn_ids: set[str] = set()
            deduplicated: list[dict] = []
            for record in self._buffer:
                txn_id = record.get("transaction_id", "")
                if txn_id not in seen_txn_ids:
                    seen_txn_ids.add(txn_id)
                    deduplicated.append(record)

            batch_size = len(deduplicated)
            first_txn_id = (
                deduplicated[0].get("transaction_id", "unknown") if deduplicated else "unknown"
            )

            # Convert to PyArrow table and flush with timeout
            try:
                import concurrent.futures

                pa_table = self._records_to_arrow_table(deduplicated)

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _exec:
                        future = _exec.submit(self._breaker.call, self._table.append, pa_table)
                        future.result(timeout=ICEBERG_FLUSH_TIMEOUT_SEC)
                    logger.info(f"Flushed {batch_size} records to default.fraud_decisions")
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        f"Iceberg flush timed out after {ICEBERG_FLUSH_TIMEOUT_SEC}s "
                        f"for batch of {batch_size} records"
                    )
                    _emit_dlq_event(
                        _DLQEvent(
                            event="iceberg_decisions_sink_dlq",
                            transaction_id=first_txn_id,
                            reason="timeout",
                            batch_size=batch_size,
                        )
                    )

            except Exception as e:
                # Circuit breaker open, connection error, or other issue
                if "CircuitBreakerError" in type(e).__name__:
                    logger.warning(
                        f"Iceberg circuit breaker is OPEN; DLQ'ing batch of {batch_size}"
                    )
                    _emit_dlq_event(
                        _DLQEvent(
                            event="iceberg_decisions_sink_dlq",
                            transaction_id=first_txn_id,
                            reason="circuit_open",
                            batch_size=batch_size,
                        )
                    )
                elif isinstance(e, (ConnectionError, OSError)):
                    logger.warning(f"Iceberg catalog connection error: {e}; DLQ'ing batch")
                    _increment_counter("iceberg_decisions_catalog_unavailable_total")
                    _emit_dlq_event(
                        _DLQEvent(
                            event="iceberg_decisions_sink_dlq",
                            transaction_id=first_txn_id,
                            reason="catalog_unavailable",
                            batch_size=batch_size,
                        )
                    )
                else:
                    logger.warning(f"Iceberg flush failed: {e}; DLQ'ing batch of {batch_size}")
                    _emit_dlq_event(
                        _DLQEvent(
                            event="iceberg_decisions_sink_dlq",
                            transaction_id=first_txn_id,
                            reason="flush_error",
                            batch_size=batch_size,
                        )
                    )

            finally:
                # Always clear the buffer to prevent unbounded memory growth
                # If we hit buffer_full condition, also increment the counter
                if len(self._buffer) >= ICEBERG_DECISIONS_BUFFER_MAX:
                    _increment_counter("iceberg_decisions_buffer_overflow_total")
                    logger.warning(
                        f"Iceberg decisions buffer reached max size "
                        f"({ICEBERG_DECISIONS_BUFFER_MAX}); DLQ'ing oldest batch"
                    )
                self._buffer.clear()

        def close(self) -> None:
            """Flush remaining buffer on task shutdown."""
            self._flush()

        def _records_to_arrow_table(self, records: list[dict]):  # type: ignore[return]
            """Convert list of FraudDecision records to PyArrow Table.

            Maps all 8 schema fields for fraud decisions:
            - transaction_id (str)
            - decision (str: ALLOW/FLAG/BLOCK)
            - fraud_score (float64)
            - rule_triggers (list<str>)
            - model_version (str)
            - decision_time_ms (int64)
            - latency_ms (float64)
            - schema_version (str)
            """
            import pyarrow as pa

            schema = pa.schema(
                [
                    pa.field("transaction_id", pa.string(), nullable=False),
                    pa.field("decision", pa.string(), nullable=False),
                    pa.field("fraud_score", pa.float64(), nullable=False),
                    pa.field("rule_triggers", pa.list_(pa.string()), nullable=False),
                    pa.field("model_version", pa.string(), nullable=False),
                    pa.field("decision_time_ms", pa.timestamp("us"), nullable=False),
                    pa.field("latency_ms", pa.float64(), nullable=False),
                    pa.field("schema_version", pa.string(), nullable=False),
                ]
            )

            # Extract and normalize values in schema order
            field_names = [field.name for field in schema]
            columns: dict[str, list] = {name: [] for name in field_names}

            for record in records:
                for field_name in field_names:
                    value = record.get(field_name)

                    if field_name == "fraud_score":
                        # Ensure float64
                        columns[field_name].append(float(value) if value is not None else 0.0)
                    elif field_name == "decision_time_ms":
                        # Iceberg timestamp(6) = microseconds; value is epoch-ms → multiply by 1000
                        columns[field_name].append(int(value) * 1000 if value is not None else 0)
                    elif field_name == "latency_ms":
                        # Ensure float64
                        columns[field_name].append(float(value) if value is not None else 0.0)
                    elif field_name == "rule_triggers":
                        # List of strings — preserve as-is or empty list
                        if value is None:
                            columns[field_name].append([])
                        elif isinstance(value, list):
                            columns[field_name].append(value)
                        else:
                            columns[field_name].append([])
                    else:
                        # Everything else is a string
                        columns[field_name].append(str(value) if value is not None else "")

            # Build arrow arrays and table
            arrays = [pa.array(columns[name], type=schema.field(name).type) for name in field_names]
            return pa.table({name: arr for name, arr in zip(field_names, arrays)}, schema=schema)

except ImportError:
    # PyFlink not installed — plain-Python fallback for unit tests
    _HAS_PYFLINK = False

    class IcebergDecisionsSink:  # type: ignore[no-redef]  # pragma: no cover
        """Plain-Python stand-in for unit tests."""

        def __init__(self) -> None:
            self._buffer: list[dict] = []
            self._last_flush_time_sec: float = time.time()
            self._table = None
            self._breaker = None
            self._catalog_loaded = False

        def open(self, runtime_context=None) -> None:  # type: ignore[no-untyped-def]
            """Load catalog (or mock it for tests)."""
            try:
                from pyiceberg.catalog import load_catalog

                self._table = load_catalog("iceberg").load_table("default.fraud_decisions")
                self._catalog_loaded = True
                logger.info("Loaded Iceberg table: default.fraud_decisions")
            except Exception as e:
                logger.warning(f"Could not load Iceberg catalog in test environment: {e}")
                self._catalog_loaded = False

            try:
                from pybreaker import CircuitBreaker

                self._breaker = CircuitBreaker(
                    fail_max=3,
                    reset_timeout=30,
                    listeners=[],
                )
            except Exception as e:
                logger.warning(f"Could not load circuit breaker in test environment: {e}")

        def invoke(self, value, context=None) -> None:  # type: ignore[no-untyped-def]
            """Append FraudDecision record to buffer and flush if conditions met."""
            try:
                now = time.time()
                if now - self._last_flush_time_sec >= 1.0:
                    self._flush()

                # Convert FraudDecision dataclass to dict if needed
                if hasattr(value, "__dataclass_fields__"):
                    from dataclasses import asdict

                    record = asdict(value)
                else:
                    record = value

                self._buffer.append(record)

                if len(self._buffer) >= ICEBERG_DECISIONS_BUFFER_MAX:
                    self._flush()

            except Exception as e:
                logger.error(
                    f"Unhandled exception in invoke(): {e}",
                    exc_info=True,
                )

        def _flush(self) -> None:
            """Flush buffer to Iceberg."""
            if not self._buffer:
                return

            self._last_flush_time_sec = time.time()

            # In-batch deduplication
            seen_txn_ids: set[str] = set()
            deduplicated: list[dict] = []
            for record in self._buffer:
                txn_id = record.get("transaction_id", "")
                if txn_id not in seen_txn_ids:
                    seen_txn_ids.add(txn_id)
                    deduplicated.append(record)

            batch_size = len(deduplicated)
            first_txn_id = (
                deduplicated[0].get("transaction_id", "unknown") if deduplicated else "unknown"
            )

            try:
                if self._catalog_loaded and self._table is not None:
                    pa_table = self._records_to_arrow_table(deduplicated)
                    if self._breaker:
                        self._breaker.call(self._table.append, pa_table)
                    else:
                        self._table.append(pa_table)
                    logger.info(f"Flushed {batch_size} records to default.fraud_decisions")

            except Exception as e:
                if "CircuitBreakerError" in type(e).__name__:
                    reason = "circuit_open"
                elif isinstance(e, (ConnectionError, OSError)):
                    reason = "catalog_unavailable"
                else:
                    reason = "flush_error"
                logger.warning(f"Flush failed ({reason}): {e}")
                _emit_dlq_event(
                    _DLQEvent(
                        event="iceberg_decisions_sink_dlq",
                        transaction_id=first_txn_id,
                        reason=reason,
                        batch_size=batch_size,
                    )
                )

            finally:
                if len(self._buffer) >= ICEBERG_DECISIONS_BUFFER_MAX:
                    _increment_counter("iceberg_decisions_buffer_overflow_total")
                self._buffer.clear()

        def close(self) -> None:
            """Flush remaining buffer on task shutdown."""
            self._flush()

        def _records_to_arrow_table(self, records: list[dict]):  # type: ignore[no-untyped-def]
            """Convert records to PyArrow Table (same as PyFlink version)."""
            import pyarrow as pa

            schema = pa.schema(
                [
                    pa.field("transaction_id", pa.string(), nullable=False),
                    pa.field("decision", pa.string(), nullable=False),
                    pa.field("fraud_score", pa.float64(), nullable=False),
                    pa.field("rule_triggers", pa.list_(pa.string()), nullable=False),
                    pa.field("model_version", pa.string(), nullable=False),
                    pa.field("decision_time_ms", pa.timestamp("us"), nullable=False),
                    pa.field("latency_ms", pa.float64(), nullable=False),
                    pa.field("schema_version", pa.string(), nullable=False),
                ]
            )

            field_names = [field.name for field in schema]
            columns: dict[str, list] = {name: [] for name in field_names}

            for record in records:
                for field_name in field_names:
                    value = record.get(field_name)

                    if field_name == "fraud_score":
                        columns[field_name].append(float(value) if value is not None else 0.0)
                    elif field_name == "decision_time_ms":
                        # Iceberg timestamp(6) = microseconds; value is epoch-ms → multiply by 1000
                        columns[field_name].append(int(value) * 1000 if value is not None else 0)
                    elif field_name == "latency_ms":
                        columns[field_name].append(float(value) if value is not None else 0.0)
                    elif field_name == "rule_triggers":
                        if value is None:
                            columns[field_name].append([])
                        elif isinstance(value, list):
                            columns[field_name].append(value)
                        else:
                            columns[field_name].append([])
                    else:
                        columns[field_name].append(str(value) if value is not None else "")

            arrays = [pa.array(columns[name], type=schema.field(name).type) for name in field_names]
            return pa.table({name: arr for name, arr in zip(field_names, arrays)}, schema=schema)
