from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual

from analytics.queries.config import MAX_HOURS

_EMPTY_LEADERBOARD_COLS = ["rule_name", "total_triggers", "avg_score", "p95_score"]
_EMPTY_DAILY_COLS = ["decision_date", "decision", "trigger_count", "avg_fraud_score"]


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


def rule_leaderboard(hours: int = 168, top_n: int = 20) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions_conn(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_LEADERBOARD_COLS)

    try:
        return conn.execute(
            """
            WITH expanded AS (
                SELECT
                    UNNEST(rule_triggers) AS rule_name,
                    fraud_score
                FROM tbl
                WHERE rule_triggers IS NOT NULL
                  AND len(rule_triggers) > 0
            )
            SELECT
                rule_name,
                COUNT(*)                                        AS total_triggers,
                AVG(fraud_score)                                AS avg_score,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY fraud_score) AS p95_score
            FROM expanded
            GROUP BY rule_name
            ORDER BY total_triggers DESC
            LIMIT ?
        """,
            [top_n],
        ).df()
    finally:
        conn.close()


def rule_trigger_daily(rule_name: str, hours: int = 720) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    conn = _load_decisions_conn(hours)
    if conn is None:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    try:
        return conn.execute(
            """
            WITH expanded AS (
                SELECT
                    CAST(epoch_ms(decision_time_ms) AS DATE) AS decision_date,
                    decision,
                    fraud_score,
                    UNNEST(rule_triggers)                    AS rule_name
                FROM tbl
                WHERE rule_triggers IS NOT NULL
                  AND len(rule_triggers) > 0
            )
            SELECT
                decision_date,
                decision,
                COUNT(*)         AS trigger_count,
                AVG(fraud_score) AS avg_fraud_score
            FROM expanded
            WHERE rule_name = ?
            GROUP BY decision_date, decision
            ORDER BY decision_date
        """,
            [rule_name],
        ).df()
    finally:
        conn.close()
