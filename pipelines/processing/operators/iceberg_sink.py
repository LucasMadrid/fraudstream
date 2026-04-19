"""IcebergEnrichedSink — writes enriched transaction dicts to Iceberg table.

Receives enriched transaction dicts from EnrichedRecordAssembler and writes them
to iceberg.enriched_transactions via PyIceberg 0.7+ with:
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
from decimal import Decimal

logger = logging.getLogger(__name__)
dlq_logger = logging.getLogger("dlq")

# Configuration from environment
ICEBERG_BUFFER_MAX = int(os.environ.get("ICEBERG_BUFFER_MAX", "100"))
ICEBERG_REST_URI = os.environ.get("ICEBERG_REST_URI", "http://localhost:8181")
ICEBERG_WAREHOUSE = os.environ.get("ICEBERG_WAREHOUSE", "s3://warehouse")
AWS_S3_ENDPOINT = os.environ.get("AWS_S3_ENDPOINT", "http://localhost:9000")
ICEBERG_FLUSH_TIMEOUT_SEC = 5


@dataclass(frozen=True)
class _DLQEvent:
    """Dead-letter queue event for failed writes."""

    transaction_id: str
    reason: str  # "timeout", "circuit_open", "buffer_full"
    batch_size: int


def _emit_dlq_event(event: _DLQEvent) -> None:
    """Log a structured DLQ event to stderr."""
    dlq_logger.warning(
        json.dumps(
            {
                "event": "iceberg_sink_dlq",
                "transaction_id": event.transaction_id,
                "reason": event.reason,
                "batch_size": event.batch_size,
            }
        )
    )


def _increment_counter(counter_name: str) -> None:
    """Safely increment a Prometheus counter, swallowing import errors."""
    try:
        from pipelines.processing.metrics import (
            feast_push_failures_total,
            iceberg_buffer_overflow_total,
            iceberg_catalog_unavailable_total,
        )

        if counter_name == "iceberg_buffer_overflow_total":
            iceberg_buffer_overflow_total.inc()
        elif counter_name == "iceberg_catalog_unavailable_total":
            iceberg_catalog_unavailable_total.inc()
        elif counter_name == "feast_push_failures_total":
            feast_push_failures_total.inc()
    except Exception:
        # Metrics unavailable — continue processing
        pass


def _observe_flush_duration(duration_seconds: float) -> None:
    """Safely observe flush duration in Prometheus histogram, swallowing import errors."""
    try:
        from pipelines.processing.metrics import iceberg_flush_duration_seconds

        iceberg_flush_duration_seconds.observe(duration_seconds)
    except Exception:
        # Metrics unavailable — continue processing
        pass


try:  # pragma: no cover
    import pyflink.datastream  # noqa: F401

    _HAS_PYFLINK = True

    class IcebergEnrichedSink:
        """PyFlink sink operator — writes to Iceberg table.

        Attributes:
            _buffer: List of enriched transaction dicts
            _last_flush_time_sec: Wall-clock timestamp of last flush
            _table: PyIceberg Table handle (loaded in open())
            _breaker: pybreaker.CircuitBreaker for flush operations
        """

        def __init__(self) -> None:
            self._buffer: list[dict] = []
            self._last_flush_time_sec: float = time.time()
            self._table = None
            self._breaker = None
            self._feast_store = None

        def open(self, runtime_context) -> None:  # type: ignore[no-untyped-def]
            """Load Iceberg catalog, circuit breaker, and Feast store."""
            try:
                from pyiceberg.catalog import load_catalog

                self._table = load_catalog("iceberg").load_table("default.enriched_transactions")
                logger.info("Loaded Iceberg table: iceberg.enriched_transactions")
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

            # Initialize Feast store (optional — skip silently if not installed)
            try:
                from feast import FeatureStore

                feast_repo_path = os.environ.get("FEAST_REPO_PATH", "storage/feature_store")
                self._feast_store = FeatureStore(repo_path=feast_repo_path)
                logger.info(f"Loaded Feast FeatureStore from {feast_repo_path}")
            except ImportError:
                logger.debug("Feast not installed; skipping feature materialization")
            except Exception as e:
                logger.debug(
                    f"Could not initialize Feast store: {e}; skipping feature materialization"
                )

        def close(self) -> None:
            """Flush remaining buffer on task shutdown."""
            self._flush()

        def invoke(self, value: dict, context) -> None:  # type: ignore[no-untyped-def]
            """Append record to buffer and flush if conditions met.

            Never raises — all exceptions are caught and DLQ'd.
            """
            try:
                # Check if 1 second has elapsed since last flush
                now = time.time()
                if now - self._last_flush_time_sec >= 1.0:
                    self._flush()

                # Append to buffer
                self._buffer.append(value)

                # Flush if buffer is full
                if len(self._buffer) >= ICEBERG_BUFFER_MAX:
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

            start = time.monotonic()
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
                    logger.info(f"Flushed {batch_size} records to iceberg.enriched_transactions")

                    # Push to Feast after successful Iceberg write (best-effort; failures
                    # are logged and counted but do not fail the Iceberg flush).
                    try:
                        self._push_to_feast(pa_table, deduplicated)
                    except Exception as e:
                        logger.error(
                            json.dumps(
                                {
                                    "event": "feast_push_failure",
                                    "batch_size": batch_size,
                                    "error": str(e),
                                }
                            )
                        )
                        self._increment_feast_counter()

                except concurrent.futures.TimeoutError:
                    logger.warning(
                        f"Iceberg flush timed out after {ICEBERG_FLUSH_TIMEOUT_SEC}s "
                        f"for batch of {batch_size} records"
                    )
                    _emit_dlq_event(
                        _DLQEvent(
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
                            transaction_id=first_txn_id,
                            reason="circuit_open",
                            batch_size=batch_size,
                        )
                    )
                elif isinstance(e, (ConnectionError, OSError)):
                    logger.warning(f"Iceberg catalog connection error: {e}; DLQ'ing batch")
                    _increment_counter("iceberg_catalog_unavailable_total")
                    _emit_dlq_event(
                        _DLQEvent(
                            transaction_id=first_txn_id,
                            reason="catalog_unavailable",
                            batch_size=batch_size,
                        )
                    )
                else:
                    logger.warning(f"Iceberg flush failed: {e}; DLQ'ing batch of {batch_size}")
                    _emit_dlq_event(
                        _DLQEvent(
                            transaction_id=first_txn_id,
                            reason="flush_error",
                            batch_size=batch_size,
                        )
                    )

            finally:
                # Always clear the buffer to prevent unbounded memory growth
                # If we hit buffer_full condition, also increment the counter
                elapsed = time.monotonic() - start
                _observe_flush_duration(elapsed)
                if len(self._buffer) >= ICEBERG_BUFFER_MAX:
                    _increment_counter("iceberg_buffer_overflow_total")
                    logger.warning(
                        f"Iceberg buffer reached max size ({ICEBERG_BUFFER_MAX}); "
                        "DLQ'ing oldest batch"
                    )
                self._buffer.clear()

        def _records_to_arrow_table(self, records: list[dict]):  # type: ignore[no-untyped-def]
            """Convert list of enriched record dicts to PyArrow Table.

            Maps all 36 Iceberg schema fields. Converts Decimal strings to float
            where needed and handles None values correctly.
            """
            import pyarrow as pa

            # Field definitions matching iceberg-enriched-transactions-v1.sql
            # Order matches schema_version = "1"
            schema = pa.schema(
                [
                    pa.field("transaction_id", pa.string(), nullable=False),
                    pa.field("account_id", pa.string(), nullable=False),
                    pa.field("merchant_id", pa.string(), nullable=False),
                    pa.field("amount", pa.decimal128(18, 4), nullable=False),
                    pa.field("currency", pa.string(), nullable=False),
                    pa.field("event_time", pa.timestamp("us"), nullable=False),
                    pa.field("enrichment_time", pa.timestamp("us"), nullable=False),
                    pa.field("channel", pa.string(), nullable=False),
                    pa.field("card_bin", pa.string(), nullable=False),
                    pa.field("card_last4", pa.string(), nullable=False),
                    pa.field("caller_ip_subnet", pa.string(), nullable=False),
                    pa.field("api_key_id", pa.string(), nullable=False),
                    pa.field("oauth_scope", pa.string(), nullable=False),
                    pa.field("geo_lat", pa.float32(), nullable=True),
                    pa.field("geo_lon", pa.float32(), nullable=True),
                    pa.field("masking_lib_version", pa.string(), nullable=False),
                    pa.field("vel_count_1m", pa.int32(), nullable=False),
                    pa.field("vel_amount_1m", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_5m", pa.int32(), nullable=False),
                    pa.field("vel_amount_5m", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_1h", pa.int32(), nullable=False),
                    pa.field("vel_amount_1h", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_24h", pa.int32(), nullable=False),
                    pa.field("vel_amount_24h", pa.decimal128(18, 4), nullable=False),
                    pa.field("geo_country", pa.string(), nullable=True),
                    pa.field("geo_city", pa.string(), nullable=True),
                    pa.field("geo_network_class", pa.string(), nullable=True),
                    pa.field("geo_confidence", pa.float32(), nullable=True),
                    pa.field("device_first_seen", pa.timestamp("us"), nullable=True),
                    pa.field("device_txn_count", pa.int32(), nullable=True),
                    pa.field("device_known_fraud", pa.bool_(), nullable=True),
                    pa.field("prev_geo_country", pa.string(), nullable=True),
                    pa.field("prev_txn_time_ms", pa.timestamp("us"), nullable=True),
                    pa.field("enrichment_latency_ms", pa.int32(), nullable=False),
                    pa.field("processor_version", pa.string(), nullable=False),
                    pa.field("schema_version", pa.string(), nullable=False),
                ]
            )

            # Extract and normalize values in schema order
            field_names = [field.name for field in schema]
            columns: dict[str, list] = {name: [] for name in field_names}

            for record in records:
                for field_name in field_names:
                    value = record.get(field_name)

                    # Convert Decimal to decimal128 for Iceberg compatibility
                    if isinstance(value, Decimal):
                        columns[field_name].append(value)
                    # Iceberg timestamp(6) = microseconds; values are epoch-ms → multiply by 1000
                    elif field_name in (
                        "event_time",
                        "enrichment_time",
                        "device_first_seen",
                        "prev_txn_time_ms",
                    ):
                        columns[field_name].append(int(value) * 1000 if value is not None else None)
                    # Preserve integers for int32 fields
                    elif field_name in (
                        "vel_count_1m",
                        "vel_count_5m",
                        "vel_count_1h",
                        "vel_count_24h",
                        "device_txn_count",
                        "enrichment_latency_ms",
                    ):
                        columns[field_name].append(int(value) if value is not None else None)
                    # Preserve float values
                    elif field_name in (
                        "geo_lat",
                        "geo_lon",
                        "geo_confidence",
                    ):
                        columns[field_name].append(float(value) if value is not None else None)
                    # Handle boolean field
                    elif field_name == "device_known_fraud":
                        if value is None:
                            columns[field_name].append(None)
                        elif isinstance(value, bool):
                            columns[field_name].append(value)
                        elif isinstance(value, str):
                            columns[field_name].append(
                                value.lower() in ("true", "1", "yes")
                            )
                        else:
                            columns[field_name].append(bool(value))
                    # Everything else is a string or None
                    else:
                        columns[field_name].append(str(value) if value is not None else None)

            # Build arrow arrays and table
            arrays = [pa.array(columns[name], type=schema.field(name).type) for name in field_names]
            return pa.table({name: arr for name, arr in zip(field_names, arrays)}, schema=schema)

        def _increment_feast_counter(self) -> None:
            """Safely increment Feast failure counter."""
            _increment_counter("feast_push_failures_total")

        def _push_to_feast(self, pa_table, records: list[dict]) -> None:
            """Push velocity, geo, and device features to Feast.

            Args:
                pa_table: PyArrow table with all enriched transaction fields
                records: List of deduplicated enriched record dicts

            Raises:
                Exception: Re-raises Feast push failures for Flink to retry
            """
            if self._feast_store is None:
                return

            import pyarrow as pa

            # Extract event_timestamp from records (transaction event time, not wall-clock)
            event_timestamps = [int(record.get("event_time", 0)) for record in records]

            # Velocity features push
            try:
                velocity_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "vel_count_1m": pa.array(
                        [int(r.get("vel_count_1m") or 0) for r in records], type=pa.int32()
                    ),
                    "vel_amount_1m": pa.array(
                        [float(r.get("vel_amount_1m") or 0) for r in records], type=pa.float64()
                    ),
                    "vel_count_5m": pa.array(
                        [int(r.get("vel_count_5m") or 0) for r in records], type=pa.int32()
                    ),
                    "vel_amount_5m": pa.array(
                        [float(r.get("vel_amount_5m") or 0) for r in records], type=pa.float64()
                    ),
                    "vel_count_1h": pa.array(
                        [int(r.get("vel_count_1h") or 0) for r in records], type=pa.int32()
                    ),
                    "vel_amount_1h": pa.array(
                        [float(r.get("vel_amount_1h") or 0) for r in records], type=pa.float64()
                    ),
                    "vel_count_24h": pa.array(
                        [int(r.get("vel_count_24h") or 0) for r in records], type=pa.int32()
                    ),
                    "vel_amount_24h": pa.array(
                        [float(r.get("vel_amount_24h") or 0) for r in records], type=pa.float64()
                    ),
                }
                velocity_table = pa.table(velocity_columns).to_pandas()
                from feast.data_source import PushMode

                self._feast_store.push(
                    "velocity_push_source", velocity_table, to=PushMode.ONLINE
                )
            except Exception as e:
                logger.error(f"Failed to push velocity features to Feast: {e}", exc_info=True)

            # Geo features push
            try:
                geo_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "geo_country": pa.array([r.get("geo_country") or "" for r in records]),
                    "geo_city": pa.array([r.get("geo_city") or "" for r in records]),
                    "geo_network_class": pa.array(
                        [r.get("geo_network_class") or "" for r in records]
                    ),
                    "geo_confidence": pa.array(
                        [float(r.get("geo_confidence") or 0) for r in records], type=pa.float64()
                    ),
                    "geo_lat": pa.array(
                        [float(r.get("geo_lat") or 0) for r in records], type=pa.float32()
                    ),
                    "geo_lon": pa.array(
                        [float(r.get("geo_lon") or 0) for r in records], type=pa.float32()
                    ),
                }
                geo_table = pa.table(geo_columns).to_pandas()
                self._feast_store.push("geo_push_source", geo_table, to=PushMode.ONLINE)
            except Exception as e:
                logger.error(f"Failed to push geo features to Feast: {e}", exc_info=True)

            # Device features push
            try:
                device_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "device_first_seen": pa.array(
                        [
                            int(r.get("device_first_seen") or 0)
                            if r.get("device_first_seen")
                            else None
                            for r in records
                        ],
                        type=pa.int64(),
                    ),
                    "device_txn_count": pa.array(
                        [int(r.get("device_txn_count") or 0) for r in records], type=pa.int64()
                    ),
                    "device_known_fraud": pa.array(
                        [bool(r.get("device_known_fraud", False)) for r in records], type=pa.bool_()
                    ),
                    "prev_geo_country": pa.array(
                        [r.get("prev_geo_country") or "" for r in records]
                    ),
                    "prev_txn_time_ms": pa.array(
                        [
                            int(r.get("prev_txn_time_ms") or 0)
                            if r.get("prev_txn_time_ms")
                            else None
                            for r in records
                        ],
                        type=pa.int64(),
                    ),
                }
                device_table = pa.table(device_columns).to_pandas()
                self._feast_store.push(
                    "device_push_source", device_table, to=PushMode.ONLINE
                )
            except Exception as e:
                logger.error(f"Failed to push device features to Feast: {e}", exc_info=True)

except ImportError:
    # PyFlink not installed — plain-Python fallback for unit tests
    _HAS_PYFLINK = False

    class IcebergEnrichedSink:  # type: ignore[no-redef]  # pragma: no cover
        """Plain-Python stand-in for unit tests."""

        def __init__(self) -> None:
            self._buffer: list[dict] = []
            self._last_flush_time_sec: float = time.time()
            self._table = None
            self._breaker = None
            self._catalog_loaded = False
            self._feast_store = None

        def open(self, runtime_context=None) -> None:  # type: ignore[no-untyped-def]
            """Load catalog (or mock it for tests), circuit breaker, and Feast store."""
            try:
                from pyiceberg.catalog import load_catalog

                self._table = load_catalog("iceberg").load_table("default.enriched_transactions")
                self._catalog_loaded = True
                logger.info("Loaded Iceberg table: iceberg.enriched_transactions")
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

            # Initialize Feast store (optional — skip silently if not installed)
            try:
                from feast import FeatureStore

                feast_repo_path = os.environ.get("FEAST_REPO_PATH", "storage/feature_store")
                self._feast_store = FeatureStore(repo_path=feast_repo_path)
                logger.info(f"Loaded Feast FeatureStore from {feast_repo_path}")
            except ImportError:
                logger.debug("Feast not installed; skipping feature materialization")
            except Exception as e:
                logger.debug(
                    f"Could not initialize Feast store: {e}; skipping feature materialization"
                )

        def close(self) -> None:
            """Flush remaining buffer on task shutdown."""
            self._flush()

        def invoke(self, value: dict, context=None) -> None:  # type: ignore[no-untyped-def]
            """Append record to buffer and flush if conditions met."""
            try:
                now = time.time()
                if now - self._last_flush_time_sec >= 1.0:
                    self._flush()

                self._buffer.append(value)

                if len(self._buffer) >= ICEBERG_BUFFER_MAX:
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

            start = time.monotonic()
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
                    logger.info(f"Flushed {batch_size} records to iceberg.enriched_transactions")

                    # Push to Feast after successful Iceberg write (best-effort; failures
                    # are logged and counted but do not fail the Iceberg flush).
                    try:
                        self._push_to_feast(pa_table, deduplicated)
                    except Exception as e:
                        logger.error(
                            json.dumps(
                                {
                                    "event": "feast_push_failure",
                                    "batch_size": batch_size,
                                    "error": str(e),
                                }
                            )
                        )
                        self._increment_feast_counter()

            except Exception as e:
                if "CircuitBreakerError" in type(e).__name__:
                    logger.warning(
                        f"Iceberg circuit breaker is OPEN; DLQ'ing batch of {batch_size}"
                    )
                    _emit_dlq_event(
                        _DLQEvent(
                            transaction_id=first_txn_id,
                            reason="circuit_open",
                            batch_size=batch_size,
                        )
                    )
                elif isinstance(e, (ConnectionError, OSError)):
                    logger.warning(f"Iceberg catalog connection error: {e}; DLQ'ing batch")
                    _increment_counter("iceberg_catalog_unavailable_total")
                    _emit_dlq_event(
                        _DLQEvent(
                            transaction_id=first_txn_id,
                            reason="catalog_unavailable",
                            batch_size=batch_size,
                        )
                    )
                else:
                    logger.warning(f"Iceberg flush failed: {e}; DLQ'ing batch of {batch_size}")
                    _emit_dlq_event(
                        _DLQEvent(
                            transaction_id=first_txn_id,
                            reason="flush_error",
                            batch_size=batch_size,
                        )
                    )

            finally:
                elapsed = time.monotonic() - start
                _observe_flush_duration(elapsed)
                if len(self._buffer) >= ICEBERG_BUFFER_MAX:
                    _increment_counter("iceberg_buffer_overflow_total")
                self._buffer.clear()

        def _records_to_arrow_table(self, records: list[dict]):  # type: ignore[no-untyped-def]
            """Convert records to PyArrow Table (same as PyFlink version)."""
            import pyarrow as pa

            schema = pa.schema(
                [
                    pa.field("transaction_id", pa.string(), nullable=False),
                    pa.field("account_id", pa.string(), nullable=False),
                    pa.field("merchant_id", pa.string(), nullable=False),
                    pa.field("amount", pa.decimal128(18, 4), nullable=False),
                    pa.field("currency", pa.string(), nullable=False),
                    pa.field("event_time", pa.timestamp("us"), nullable=False),
                    pa.field("enrichment_time", pa.timestamp("us"), nullable=False),
                    pa.field("channel", pa.string(), nullable=False),
                    pa.field("card_bin", pa.string(), nullable=False),
                    pa.field("card_last4", pa.string(), nullable=False),
                    pa.field("caller_ip_subnet", pa.string(), nullable=False),
                    pa.field("api_key_id", pa.string(), nullable=False),
                    pa.field("oauth_scope", pa.string(), nullable=False),
                    pa.field("geo_lat", pa.float32(), nullable=True),
                    pa.field("geo_lon", pa.float32(), nullable=True),
                    pa.field("masking_lib_version", pa.string(), nullable=False),
                    pa.field("vel_count_1m", pa.int32(), nullable=False),
                    pa.field("vel_amount_1m", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_5m", pa.int32(), nullable=False),
                    pa.field("vel_amount_5m", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_1h", pa.int32(), nullable=False),
                    pa.field("vel_amount_1h", pa.decimal128(18, 4), nullable=False),
                    pa.field("vel_count_24h", pa.int32(), nullable=False),
                    pa.field("vel_amount_24h", pa.decimal128(18, 4), nullable=False),
                    pa.field("geo_country", pa.string(), nullable=True),
                    pa.field("geo_city", pa.string(), nullable=True),
                    pa.field("geo_network_class", pa.string(), nullable=True),
                    pa.field("geo_confidence", pa.float32(), nullable=True),
                    pa.field("device_first_seen", pa.timestamp("us"), nullable=True),
                    pa.field("device_txn_count", pa.int32(), nullable=True),
                    pa.field("device_known_fraud", pa.bool_(), nullable=True),
                    pa.field("prev_geo_country", pa.string(), nullable=True),
                    pa.field("prev_txn_time_ms", pa.timestamp("us"), nullable=True),
                    pa.field("enrichment_latency_ms", pa.int32(), nullable=False),
                    pa.field("processor_version", pa.string(), nullable=False),
                    pa.field("schema_version", pa.string(), nullable=False),
                ]
            )

            field_names = [field.name for field in schema]
            columns: dict[str, list] = {name: [] for name in field_names}

            for record in records:
                for field_name in field_names:
                    value = record.get(field_name)

                    if isinstance(value, Decimal):
                        columns[field_name].append(value)
                    elif field_name in (
                        "event_time",
                        "enrichment_time",
                        "device_first_seen",
                        "prev_txn_time_ms",
                    ):
                        # Iceberg timestamp(6) = microseconds; values are epoch-ms → multiply by 1000
                        columns[field_name].append(int(value) * 1000 if value is not None else None)
                    elif field_name in (
                        "vel_count_1m",
                        "vel_count_5m",
                        "vel_count_1h",
                        "vel_count_24h",
                        "device_txn_count",
                        "enrichment_latency_ms",
                    ):
                        columns[field_name].append(int(value) if value is not None else None)
                    elif field_name in (
                        "geo_lat",
                        "geo_lon",
                        "geo_confidence",
                    ):
                        columns[field_name].append(float(value) if value is not None else None)
                    elif field_name == "device_known_fraud":
                        # Handle boolean field: convert string/bool to bool
                        if value is None:
                            columns[field_name].append(None)
                        elif isinstance(value, bool):
                            columns[field_name].append(value)
                        elif isinstance(value, str):
                            columns[field_name].append(
                                value.lower() in ("true", "1", "yes")
                            )
                        else:
                            columns[field_name].append(bool(value))
                    else:
                        columns[field_name].append(str(value) if value is not None else None)

            arrays = [pa.array(columns[name], type=schema.field(name).type) for name in field_names]
            return pa.table({name: arr for name, arr in zip(field_names, arrays)}, schema=schema)

        def _increment_feast_counter(self) -> None:
            """Safely increment Feast failure counter."""
            _increment_counter("feast_push_failures_total")

        def _push_to_feast(self, pa_table, records: list[dict]) -> None:
            """Push velocity, geo, and device features to Feast.

            Args:
                pa_table: PyArrow table with all enriched transaction fields
                records: List of deduplicated enriched record dicts

            Raises:
                Exception: Re-raises Feast push failures for Flink to retry
            """
            if self._feast_store is None:
                return

            import pyarrow as pa

            # Extract event_timestamp from records (transaction event time)
            event_timestamps = [int(record.get("event_time", 0)) for record in records]

            # Velocity features push
            try:
                velocity_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "vel_count_1m": pa.array(
                        [int(r.get("vel_count_1m") or 0) for r in records],
                        type=pa.int32(),
                    ),
                    "vel_amount_1m": pa.array(
                        [float(r.get("vel_amount_1m") or 0) for r in records],
                        type=pa.float64(),
                    ),
                    "vel_count_5m": pa.array(
                        [int(r.get("vel_count_5m") or 0) for r in records],
                        type=pa.int32(),
                    ),
                    "vel_amount_5m": pa.array(
                        [float(r.get("vel_amount_5m") or 0) for r in records],
                        type=pa.float64(),
                    ),
                    "vel_count_1h": pa.array(
                        [int(r.get("vel_count_1h") or 0) for r in records],
                        type=pa.int32(),
                    ),
                    "vel_amount_1h": pa.array(
                        [float(r.get("vel_amount_1h") or 0) for r in records],
                        type=pa.float64(),
                    ),
                    "vel_count_24h": pa.array(
                        [int(r.get("vel_count_24h") or 0) for r in records],
                        type=pa.int32(),
                    ),
                    "vel_amount_24h": pa.array(
                        [float(r.get("vel_amount_24h") or 0) for r in records],
                        type=pa.float64(),
                    ),
                }
                velocity_table = pa.table(velocity_columns).to_pandas()
                from feast.data_source import PushMode

                self._feast_store.push(
                    "velocity_push_source",
                    velocity_table,
                    to=PushMode.ONLINE,
                )
            except Exception as e:
                logger.error(
                    f"Failed to push velocity features to Feast: {e}",
                    exc_info=True,
                )
                raise

            # Geo features push
            try:
                geo_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "geo_country": pa.array([r.get("geo_country") or "" for r in records]),
                    "geo_city": pa.array([r.get("geo_city") or "" for r in records]),
                    "geo_network_class": pa.array(
                        [r.get("geo_network_class") or "" for r in records]
                    ),
                    "geo_confidence": pa.array(
                        [float(r.get("geo_confidence") or 0) for r in records],
                        type=pa.float64(),
                    ),
                    "geo_lat": pa.array(
                        [float(r.get("geo_lat") or 0) for r in records],
                        type=pa.float32(),
                    ),
                    "geo_lon": pa.array(
                        [float(r.get("geo_lon") or 0) for r in records],
                        type=pa.float32(),
                    ),
                }
                geo_table = pa.table(geo_columns).to_pandas()
                self._feast_store.push("geo_push_source", geo_table, to=PushMode.ONLINE)
            except Exception as e:
                logger.error(
                    f"Failed to push geo features to Feast: {e}",
                    exc_info=True,
                )
                raise

            # Device features push
            try:
                device_columns = {
                    "account_id": pa.array([r.get("account_id") for r in records]),
                    "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                    "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
                    "device_first_seen": pa.array(
                        [
                            (
                                int(r.get("device_first_seen") or 0)
                                if r.get("device_first_seen")
                                else None
                            )
                            for r in records
                        ],
                        type=pa.int64(),
                    ),
                    "device_txn_count": pa.array(
                        [int(r.get("device_txn_count") or 0) for r in records],
                        type=pa.int64(),
                    ),
                    "device_known_fraud": pa.array(
                        [bool(r.get("device_known_fraud", False)) for r in records],
                        type=pa.bool_(),
                    ),
                    "prev_geo_country": pa.array(
                        [r.get("prev_geo_country") or "" for r in records]
                    ),
                    "prev_txn_time_ms": pa.array(
                        [
                            (
                                int(r.get("prev_txn_time_ms") or 0)
                                if r.get("prev_txn_time_ms")
                                else None
                            )
                            for r in records
                        ],
                        type=pa.int64(),
                    ),
                }
                device_table = pa.table(device_columns).to_pandas()
                self._feast_store.push(
                    "device_push_source",
                    device_table,
                    to=PushMode.ONLINE,
                )
            except Exception as e:
                logger.error(
                    f"Failed to push device features to Feast: {e}",
                    exc_info=True,
                )
                raise
