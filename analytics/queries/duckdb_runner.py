"""Thin DuckDB connection wrapper — owns the lifecycle for a single Arrow-table query."""

from __future__ import annotations

import duckdb
import pandas as pd
import pyarrow as pa


class DuckDBQueryRunner:
    """Accepts named Arrow tables, manages the DuckDB connection, runs one query.

    Usage:
        df = DuckDBQueryRunner(decisions=decisions_tbl).query(sql)
        df = DuckDBQueryRunner(decisions=d, enriched=e).query(sql, [param])
    """

    def __init__(self, **tables: pa.Table) -> None:
        self._tables = tables

    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        conn = duckdb.connect()
        for name, table in self._tables.items():
            conn.register(name, table)
        try:
            if params is not None:
                return conn.execute(sql, params).df()
            return conn.execute(sql).df()
        finally:
            conn.close()
