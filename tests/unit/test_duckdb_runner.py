from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa

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
    """IcebergReader passed as reader= kwarg is used instead of the singleton."""
    from analytics.queries.fraud_rate import fraud_rate_daily

    stub = MagicMock()
    stub.scan_decisions.return_value = pa.table({"transaction_id": pa.array([], type=pa.string())})

    df = fraud_rate_daily(days=7, reader=stub)
    stub.scan_decisions.assert_called_once()
    assert df.empty
