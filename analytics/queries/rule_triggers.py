"""Rule trigger analysis: frequency, score impact, and daily trends
via PyIceberg → Arrow → DuckDB."""

from __future__ import annotations

import pandas as pd

from analytics.queries.config import MAX_HOURS, start_ms
from analytics.queries.duckdb_runner import DuckDBQueryRunner
from analytics.queries.iceberg_reader import IcebergReader, get_reader

_EMPTY_LEADERBOARD_COLS = ["rule_name", "total_triggers", "avg_score", "p95_score"]
_EMPTY_DAILY_COLS = ["decision_date", "decision", "trigger_count", "avg_fraud_score"]


def rule_leaderboard(
    days: int = 7, top_n: int = 20, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """Top rules by trigger frequency with score statistics.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        top_n: Maximum number of top rules to return.
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: rule_name, total_triggers, avg_score, p95_score.
    """
    top_n = max(1, min(top_n, 100))
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_LEADERBOARD_COLS)

    return DuckDBQueryRunner(tbl=decisions).query(
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
    )


def rule_trigger_daily(
    rule_name: str, days: int = 30, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """Daily trigger counts and fraud scores for a specific rule.

    Args:
        rule_name: Name of the rule to analyze.
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision_date, decision, trigger_count, avg_fraud_score.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    return DuckDBQueryRunner(tbl=decisions).query(
        """
        WITH expanded AS (
            SELECT
                CAST(decision_time_ms AS DATE) AS decision_date,
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
    )
