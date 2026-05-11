"""Model version performance comparison: summary stats and daily trends
via PyIceberg → Arrow → DuckDB."""

from __future__ import annotations

import pandas as pd

from analytics.queries.config import MAX_HOURS, start_ms
from analytics.queries.duckdb_runner import DuckDBQueryRunner
from analytics.queries.iceberg_reader import IcebergReader, get_reader

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


def model_version_summary(days: int = 30, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Aggregated metrics by model version: score and latency percentiles.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: model_version, total_txns, avg_score, median_score,
        p95_score, avg_latency_ms.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_SUMMARY_COLS)

    return DuckDBQueryRunner(tbl=decisions).query("""
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
    """)


def model_version_daily(
    model_version: str, days: int = 30, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """Daily performance metrics for a specific model version.

    Args:
        model_version: Model version identifier to analyze.
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision_date, decision, transaction_count,
        avg_fraud_score, avg_latency_ms.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    return DuckDBQueryRunner(tbl=decisions).query(
        """
        SELECT
            CAST(decision_time_ms AS DATE) AS decision_date,
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
    )
