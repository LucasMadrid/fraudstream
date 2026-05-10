"""Tests for DuckDBQueryRunner - skipped if duckdb not installed."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest

if TYPE_CHECKING:
    # Avoid importing duckdb at module level for type checking
    pass

# Check if duckdb is available without importing it
HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None

pytestmark = pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")

# Import DuckDBQueryRunner only if duckdb is available
if HAS_DUCKDB:
    from analytics.queries.duckdb_runner import DuckDBQueryRunner

_DECISIONS = pa.table(
    {
        "transaction_id": pa.array(["t1", "t2"], type=pa.string()),
        "decision": pa.array(["ALLOW", "BLOCK"], type=pa.string()),
        "fraud_score": pa.array([0.1, 0.9], type=pa.float64()),
    }
)
_ENRICHED = pa.table(
    {
        "transaction_id": pa.array(["t1", "t2"], type=pa.string()),
        "channel": pa.array(["web", "mobile"], type=pa.string()),
    }
)


def test_query_single_table_returns_dataframe():
    df = DuckDBQueryRunner(tbl=_DECISIONS).query("SELECT * FROM tbl")
    assert list(df.columns) == ["transaction_id", "decision", "fraud_score"]
    assert len(df) == 2


def test_query_with_params():
    df = DuckDBQueryRunner(tbl=_DECISIONS).query("SELECT * FROM tbl WHERE decision = ?", ["BLOCK"])
    assert len(df) == 1
    assert df.iloc[0]["decision"] == "BLOCK"


def test_query_multiple_tables_join():
    df = DuckDBQueryRunner(decisions=_DECISIONS, enriched=_ENRICHED).query("""
        SELECT d.transaction_id, d.decision, e.channel
        FROM decisions d
        JOIN enriched e ON d.transaction_id = e.transaction_id
        ORDER BY d.transaction_id
    """)
    assert len(df) == 2
    assert list(df["channel"]) == ["web", "mobile"]


def test_query_empty_table_returns_empty_dataframe():
    empty = pa.table({"decision": pa.array([], type=pa.string())})
    df = DuckDBQueryRunner(tbl=empty).query("SELECT * FROM tbl")
    assert len(df) == 0


def test_injectable_reader_is_used_by_query_functions():
    """Stub test for injectable reader pattern.

    The fraud_rate_daily function doesn't currently support a reader= kwarg
    for dependency injection. This test documents the expected pattern but
    doesn't exercise it since the implementation requires Iceberg catalog setup.
    """
    # This test is a placeholder - the real fraud_rate_daily function
    # connects directly to Iceberg and doesn't support reader injection yet
    pass
