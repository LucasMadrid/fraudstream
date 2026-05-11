"""Fraud rate metrics by date and channel via PyIceberg → Arrow → DuckDB."""

from __future__ import annotations

import pandas as pd

from analytics.queries.config import MAX_HOURS, start_ms
from analytics.queries.duckdb_runner import DuckDBQueryRunner
from analytics.queries.iceberg_reader import IcebergReader, get_reader

_EMPTY_DAILY_COLS = [
    "decision_date",
    "channel",
    "decision",
    "transaction_count",
    "total_amount",
    "avg_fraud_score",
]
_EMPTY_CHANNEL_COLS = ["channel", "decision", "total_txns", "total_amount", "avg_score"]


def fraud_rate_daily(days: int = 30, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Daily fraud metrics by channel and decision (ALLOW/FLAG/BLOCK).

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision_date, channel, decision, transaction_count,
        total_amount, avg_fraud_score.
    """
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_DAILY_COLS)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query("""
        SELECT
            CAST(d.decision_time_ms AS DATE)             AS decision_date,
            COALESCE(e.channel, 'unknown')               AS channel,
            d.decision,
            COUNT(*)                                     AS transaction_count,
            COALESCE(SUM(CAST(e.amount AS DOUBLE)), 0.0) AS total_amount,
            AVG(d.fraud_score)                           AS avg_fraud_score
        FROM decisions d
        LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
        GROUP BY decision_date, channel, d.decision
        ORDER BY decision_date DESC, channel, d.decision
    """)


def fraud_rate_by_channel(days: int = 7, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Total transaction counts, amounts, and fraud scores grouped by channel.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: channel, decision, total_txns, total_amount, avg_score.
    """
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_CHANNEL_COLS)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query("""
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
    """)
