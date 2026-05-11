"""Cached Iceberg catalog access with Arrow-returning time-windowed scans."""

from __future__ import annotations

import functools

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual


@functools.lru_cache(maxsize=1)
def _get_catalog():
    return load_catalog("iceberg")


class IcebergReader:
    def scan_decisions(self, start_ms: int) -> pa.Table:
        try:
            return (
                _get_catalog()
                .load_table("default.fraud_decisions")
                .scan(row_filter=GreaterThanOrEqual("decision_time_ms", start_ms))
                .to_arrow()
            )
        except Exception as exc:
            raise RuntimeError(f"Unable to scan fraud_decisions: {exc}") from exc

    def scan_enriched(self, start_ms: int) -> pa.Table:
        try:
            return (
                _get_catalog()
                .load_table("default.enriched_transactions")
                .scan(row_filter=GreaterThanOrEqual("event_time", start_ms))
                .to_arrow()
            )
        except Exception as exc:
            raise RuntimeError(f"Unable to scan enriched_transactions: {exc}") from exc


_reader: IcebergReader | None = None


def get_reader() -> IcebergReader:
    global _reader
    if _reader is None:
        _reader = IcebergReader()
    return _reader
