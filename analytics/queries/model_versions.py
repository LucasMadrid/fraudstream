from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual

from analytics.queries.config import MAX_HOURS

_EMPTY_SUMMARY_COLS = [
    "model_version",
    "total_txns",
    "avg_score",
    "median_score",
    "p95_score",
    "avg_latency_ms",
]
_EMPTY_DAILY_COLS = [
    "decision_date",
    "decision",
    "transaction_count",
    "avg_fraud_score",
    "avg_latency_ms",
]


def _start_ms(hours: int) -> int:
    return int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)


def _load_decisions_conn(hours: int) -> duckdb.DuckDBPyConnection | None:
    start_ms = _start_ms(hours)
    try:
        catalog = load_catalog("iceberg")
    except Exception as exc:
        raise RuntimeError(f"Unable to connect to data store: {exc}") from exc

    table = catalog.load_table("default.fraud_decisions")
    arrow_tbl = table.scan(row_filter=GreaterThanOrEqual("decision_time_ms", start_ms)).to_arrow()

    if arrow_tbl.num_rows == 0:
        return None

    conn = duckdb.connect()
    conn.register("tbl", arrow_tbl)
    return conn


def model_version_summary(hours: int = 720) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions_conn(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_SUMMARY_COLS)

    try:
        return conn.execute("""
            SELECT
                model_version,
                COUNT(*)                                                       AS total_txns,
                AVG(fraud_score)                                               AS avg_score,
                PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY fraud_score)     AS median_score,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY fraud_score)     AS p95_score,
                AVG(latency_ms)                                                AS avg_latency_ms
            FROM tbl
            GROUP BY model_version
            ORDER BY model_version
        """).df()
    finally:
        conn.close()


def model_version_daily(model_version: str, hours: int = 720) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions_conn(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    try:
        return conn.execute(
            """
            SELECT
                CAST(epoch_ms(decision_time_ms) AS DATE) AS decision_date,
                decision,
                COUNT(*)                                 AS transaction_count,
                AVG(fraud_score)                         AS avg_fraud_score,
                AVG(latency_ms)                          AS avg_latency_ms
            FROM tbl
            WHERE model_version = ?
            GROUP BY decision_date, decision
            ORDER BY decision_date
        """,
            [model_version],
        ).df()
    finally:
        conn.close()
