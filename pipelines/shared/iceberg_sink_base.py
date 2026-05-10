"""Shared base class for Iceberg append sink operators."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import pyarrow as pa
import pybreaker

from pipelines.shared.circuit_breaker import IcebergCircuitBreaker
from pipelines.shared.config import IcebergSinkConfig

logger = logging.getLogger(__name__)
dlq_logger = logging.getLogger("dlq")

ICEBERG_FLUSH_TIMEOUT_SEC = 5

_FLUSH_ERROR_REASONS: dict[type, str] = {
    concurrent.futures.TimeoutError: "timeout",
    pybreaker.CircuitBreakerError: "circuit_open",
}


def _classify_flush_error(exc: Exception) -> str:
    """Map a flush exception to a DLQ reason string.

    Pure function — no I/O, no side effects.  Testable without Iceberg or PyBreaker.
    """
    for exc_type, reason in _FLUSH_ERROR_REASONS.items():
        if isinstance(exc, exc_type):
            return reason
    if isinstance(exc, (ConnectionError, OSError)):
        return "catalog_unavailable"
    return "flush_error"


class _IcebergSinkBase:
    """Buffer-flush-dedup lifecycle for PyFlink-style Iceberg append sinks.

    Subclasses must implement _records_to_arrow_table.
    Subclasses may override hook methods for per-sink metrics and side effects:
      _after_flush_success, _on_buffer_overflow, _on_catalog_unavailable,
      _observe_flush_duration.

    Implements the PyFlink operator duck-type: open / invoke / close.
    No PyFlink dependency — uses stdlib ThreadPoolExecutor for timeout enforcement.
    """

    def __init__(
        self,
        table_name: str,
        buffer_max: int,
        dlq_event_name: str,
        config: IcebergSinkConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._table_name = table_name
        self._buffer_max = buffer_max
        self._dlq_event_name = dlq_event_name
        self._config = config if config is not None else IcebergSinkConfig()
        self._clock = clock
        self._buffer: list[dict] = []
        self._last_flush_time_sec: float = self._clock()
        self._table = None
        self._breaker: IcebergCircuitBreaker | None = None

    def open(self, runtime_context: Any = None) -> None:
        """Load Iceberg catalog and initialize circuit breaker."""
        try:
            from pyiceberg.catalog import load_catalog

            self._table = load_catalog("iceberg").load_table(self._table_name)
            logger.info(f"Loaded Iceberg table: {self._table_name}")
        except Exception as e:
            logger.warning(
                f"Could not load Iceberg catalog in open(): {e} — "
                "Iceberg writes disabled. Set PYICEBERG_CATALOG__ICEBERG__URI to enable."
            )
            self._table = None

        try:
            self._breaker = IcebergCircuitBreaker(self._config)
        except Exception as e:
            logger.warning(f"Could not initialize circuit breaker: {e}")
            self._breaker = None

    def invoke(self, value: Any, context: Any = None) -> None:
        """Append record to buffer and flush if conditions met. Never raises."""
        try:
            now = self._clock()
            if now - self._last_flush_time_sec >= 1.0:
                self._flush()

            if hasattr(value, "__dataclass_fields__"):
                record = asdict(value)
            else:
                record = value

            self._buffer.append(record)

            if len(self._buffer) >= self._buffer_max:
                self._flush()

        except Exception as e:
            logger.error(f"Unhandled exception in invoke(): {e}", exc_info=True)

    def close(self) -> None:
        """Flush remaining buffer on task shutdown."""
        self._flush()

    def _flush(self) -> None:
        """Flush buffer to Iceberg with dedup, timeout, circuit breaker, and DLQ fallback."""
        if not self._buffer:
            return

        start = time.monotonic()
        self._last_flush_time_sec = self._clock()

        deduplicated = self._deduplicate(self._buffer)
        batch_size = len(deduplicated)
        first_txn_id = (
            deduplicated[0].get("transaction_id", "unknown") if deduplicated else "unknown"
        )

        try:
            if self._table is None:
                return

            pa_table = self._records_to_arrow_table(deduplicated)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _exec:
                if self._breaker is not None:
                    future = _exec.submit(self._breaker.call, self._table.append, pa_table)
                else:
                    future = _exec.submit(self._table.append, pa_table)
                future.result(timeout=ICEBERG_FLUSH_TIMEOUT_SEC)

            logger.info(f"Flushed {batch_size} records to {self._table_name}")
            self._after_flush_success(pa_table, deduplicated, batch_size)

        except Exception as e:
            reason = _classify_flush_error(e)
            logger.warning(f"Iceberg flush {reason}: {e}; DLQ'ing batch of {batch_size}")
            if reason == "catalog_unavailable":
                self._on_catalog_unavailable()
            self._emit_dlq(reason, first_txn_id, batch_size)

        finally:
            elapsed = time.monotonic() - start
            self._observe_flush_duration(elapsed)
            if len(self._buffer) >= self._buffer_max:
                self._on_buffer_overflow()
                logger.warning(
                    f"Iceberg buffer reached max size ({self._buffer_max}); DLQ'ing oldest batch"
                )
            self._buffer.clear()

    def _deduplicate(self, records: list[dict]) -> list[dict]:
        """Return records with duplicate transaction_ids removed, keeping the first occurrence."""
        seen: set[str] = set()
        result: list[dict] = []
        for record in records:
            txn_id = record.get("transaction_id", "")
            if txn_id not in seen:
                seen.add(txn_id)
                result.append(record)
        return result

    def _emit_dlq(self, reason: str, transaction_id: str, batch_size: int) -> None:
        dlq_logger.warning(
            json.dumps(
                {
                    "event": self._dlq_event_name,
                    "transaction_id": transaction_id,
                    "reason": reason,
                    "batch_size": batch_size,
                }
            )
        )

    def _records_to_arrow_table(self, records: list[dict]) -> pa.Table:
        raise NotImplementedError(f"{type(self).__name__} must implement _records_to_arrow_table")

    # Hooks with no-op defaults — subclass may override
    def _after_flush_success(
        self, pa_table: pa.Table, records: list[dict], batch_size: int
    ) -> None:
        pass

    def _on_buffer_overflow(self) -> None:
        pass

    def _on_catalog_unavailable(self) -> None:
        pass

    def _observe_flush_duration(self, elapsed_seconds: float) -> None:
        pass
