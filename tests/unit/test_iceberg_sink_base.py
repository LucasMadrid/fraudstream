"""Unit tests for _classify_flush_error and _IcebergSinkBase flush routing.

C35 — verify that flush error classification is testable as a pure function,
independent of Iceberg catalog, PyBreaker, or any I/O.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging

import pybreaker

from pipelines.shared.iceberg_sink_base import _classify_flush_error, _IcebergSinkBase

# ---------------------------------------------------------------------------
# _classify_flush_error — pure function, no mocking required
# ---------------------------------------------------------------------------


class TestClassifyFlushError:
    def test_timeout_error_maps_to_timeout(self):
        assert _classify_flush_error(concurrent.futures.TimeoutError()) == "timeout"

    def test_circuit_breaker_error_maps_to_circuit_open(self):
        assert _classify_flush_error(pybreaker.CircuitBreakerError()) == "circuit_open"

    def test_connection_error_maps_to_catalog_unavailable(self):
        assert _classify_flush_error(ConnectionError("refused")) == "catalog_unavailable"

    def test_os_error_maps_to_catalog_unavailable(self):
        assert _classify_flush_error(OSError("broken pipe")) == "catalog_unavailable"

    def test_generic_exception_maps_to_flush_error(self):
        assert _classify_flush_error(RuntimeError("boom")) == "flush_error"

    def test_value_error_maps_to_flush_error(self):
        assert _classify_flush_error(ValueError("bad schema")) == "flush_error"

    def test_subclass_of_connection_error_maps_to_catalog_unavailable(self):
        # BrokenPipeError is a subclass of ConnectionError
        assert _classify_flush_error(BrokenPipeError()) == "catalog_unavailable"


# ---------------------------------------------------------------------------
# _IcebergSinkBase._flush() — verify DLQ routing via hook/log without Iceberg
# ---------------------------------------------------------------------------


class _StubSink(_IcebergSinkBase):
    """Minimal concrete subclass — table is always None (Iceberg disabled)."""

    def _records_to_arrow_table(self, records):
        import pyarrow as pa

        return pa.table({"transaction_id": [r.get("transaction_id", "") for r in records]})


class _ErrorSink(_IcebergSinkBase):
    """Subclass that exposes a configurable exception from _records_to_arrow_table."""

    def __init__(self, exc_to_raise: Exception, **kwargs):
        super().__init__(
            table_name="test.table",
            buffer_max=100,
            dlq_event_name="test_dlq",
            **kwargs,
        )
        self._exc_to_raise = exc_to_raise
        self._on_catalog_unavailable_called = False
        self._table = object()  # non-None so _flush() doesn't early-return

    def _records_to_arrow_table(self, records):
        raise self._exc_to_raise

    def _on_catalog_unavailable(self):
        self._on_catalog_unavailable_called = True


class TestFlushDLQRouting:
    def _sink_with_records(self, exc: Exception) -> _ErrorSink:
        sink = _ErrorSink(exc_to_raise=exc)
        sink._buffer = [{"transaction_id": "txn-x"}]
        return sink

    def test_catalog_unavailable_calls_hook(self):
        sink = self._sink_with_records(ConnectionError("refused"))
        sink._flush()
        assert sink._on_catalog_unavailable_called

    def test_non_catalog_error_does_not_call_hook(self):
        sink = self._sink_with_records(RuntimeError("boom"))
        sink._flush()
        assert not sink._on_catalog_unavailable_called

    def test_flush_emits_dlq_log_with_reason(self, caplog):
        sink = self._sink_with_records(RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="dlq"):
            sink._flush()

        dlq_entries = [r for r in caplog.records if r.name == "dlq"]
        assert dlq_entries, "Expected at least one dlq log entry"
        payload = json.loads(dlq_entries[-1].getMessage())
        assert payload["reason"] == "flush_error"
        assert payload["event"] == "test_dlq"

    def test_catalog_unavailable_emits_correct_dlq_reason(self, caplog):
        sink = self._sink_with_records(ConnectionError("no route to host"))
        with caplog.at_level(logging.WARNING, logger="dlq"):
            sink._flush()

        dlq_entries = [r for r in caplog.records if r.name == "dlq"]
        assert dlq_entries
        payload = json.loads(dlq_entries[-1].getMessage())
        assert payload["reason"] == "catalog_unavailable"

    def test_buffer_cleared_after_flush_error(self):
        sink = self._sink_with_records(RuntimeError("boom"))
        assert len(sink._buffer) == 1
        sink._flush()
        assert sink._buffer == []
