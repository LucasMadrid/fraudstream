from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual

from analytics.queries.config import MAX_HOURS

_EMPTY_DAILY_COLS = [
    "decision_date",
    "channel",
    "decision",
    "transaction_count",
    "total_amount",
    "avg_fraud_score",
]
_EMPTY_CHANNEL_COLS = ["channel", "decision", "total_txns", "total_amount", "avg_score"]


def _start_ms(hours: int) -> int:
    return int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)


def _load_decisions(hours: int) -> duckdb.DuckDBPyConnection | None:
    start_ms = _start_ms(hours)
    try:
        catalog = load_catalog("iceberg")
    except Exception as exc:
        raise RuntimeError(f"Unable to connect to data store: {exc}") from exc

    decisions = catalog.load_table("default.fraud_decisions")
    enriched = catalog.load_table("default.enriched_transactions")

    dec_arrow = decisions.scan(
        row_filter=GreaterThanOrEqual("decision_time_ms", start_ms)
    ).to_arrow()
    enr_arrow = enriched.scan(row_filter=GreaterThanOrEqual("event_time", start_ms)).to_arrow()

    if dec_arrow.num_rows == 0:
        return None

    conn = duckdb.connect()
    conn.register("decisions", dec_arrow)
    conn.register("enriched", enr_arrow)
    return conn


def fraud_rate_daily(hours: int = 720) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    try:
        return conn.execute("""
            SELECT
                CAST(epoch_ms(d.decision_time_ms) AS DATE)  AS decision_date,
                COALESCE(e.channel, 'unknown')               AS channel,
                d.decision,
                COUNT(*)                                     AS transaction_count,
                COALESCE(SUM(CAST(e.amount AS DOUBLE)), 0.0) AS total_amount,
                AVG(d.fraud_score)                           AS avg_fraud_score
            FROM decisions d
            LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
            GROUP BY decision_date, channel, d.decision
            ORDER BY decision_date DESC, channel, d.decision
        """).df()
    finally:
        conn.close()


def fraud_rate_by_channel(hours: int = 168) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_CHANNEL_COLS)

    try:
        return conn.execute("""
            SELECT
                COALESCE(e.channel, 'unknown') AS channel,
                d.decision,
                COUNT(*)                       AS total_txns,
                COALESCE(SUM(CAST(e.amount AS DOUBLE)), 0.0) AS total_amount,
                AVG(d.fraud_score)             AS avg_score
            FROM decisions d
            LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
            GROUP BY channel, d.decision
            ORDER BY total_txns DESC
        """).df()
    finally:
        conn.close()
