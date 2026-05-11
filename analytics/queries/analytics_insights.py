"""Analytical insight queries — cross-dimensional fraud analysis via PyIceberg → Arrow → DuckDB."""

from __future__ import annotations

import pandas as pd

from analytics.queries.config import MAX_HOURS, start_ms
from analytics.queries.duckdb_runner import DuckDBQueryRunner
from analytics.queries.iceberg_reader import IcebergReader, get_reader

_EMPTY_KPI = [
    "total_txns",
    "block_count",
    "flag_count",
    "allow_count",
    "block_rate_pct",
    "flag_rate_pct",
    "avg_fraud_score",
    "p95_fraud_score",
    "avg_latency_ms",
    "p99_latency_ms",
]
_EMPTY_SCORE_DIST = ["decision", "score_bucket", "count"]
_EMPTY_HOURLY = ["day_of_week", "hour_of_day", "transaction_count", "block_count", "block_rate_pct"]
_EMPTY_AMOUNT = ["decision", "amount_bucket", "txn_count"]
_EMPTY_GEO = [
    "geo_country",
    "total_txns",
    "block_count",
    "flag_count",
    "block_rate_pct",
    "avg_fraud_score",
]
_EMPTY_VEL = [
    "decision",
    "avg_vel_count_1m",
    "avg_vel_count_5m",
    "avg_vel_count_1h",
    "avg_vel_count_24h",
    "avg_vel_amount_1h",
    "avg_vel_amount_24h",
]
_EMPTY_ACCT = [
    "account_id",
    "total_txns",
    "block_count",
    "flag_count",
    "alert_rate_pct",
    "avg_fraud_score",
    "max_fraud_score",
]


def kpi_summary(days: int = 30, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Single-row KPI metrics: counts, rates, avg fraud score, and latency percentiles.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: total_txns, block_count, flag_count, allow_count,
        block_rate_pct, flag_rate_pct, avg_fraud_score, p95_fraud_score,
        avg_latency_ms, p99_latency_ms.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_KPI)

    return DuckDBQueryRunner(decisions=decisions).query("""
        SELECT
            COUNT(*) AS total_txns,
            COUNT(*) FILTER (WHERE decision = 'BLOCK') AS block_count,
            COUNT(*) FILTER (WHERE decision = 'FLAG') AS flag_count,
            COUNT(*) FILTER (WHERE decision = 'ALLOW') AS allow_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE decision = 'BLOCK') / NULLIF(COUNT(*), 0), 2)
                AS block_rate_pct,
            ROUND(100.0 * COUNT(*) FILTER (WHERE decision = 'FLAG') / NULLIF(COUNT(*), 0), 2)
                AS flag_rate_pct,
            ROUND(AVG(fraud_score), 4) AS avg_fraud_score,
            ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY fraud_score), 4)
                AS p95_fraud_score,
            ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
            ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms), 2) AS p99_latency_ms
        FROM decisions
    """)


def score_distribution(days: int = 30, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Fraud score distribution bucketed into 0.1-wide intervals by decision outcome.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision, score_bucket, count.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_SCORE_DIST)

    return DuckDBQueryRunner(decisions=decisions).query("""
        SELECT
            decision,
            ROUND(FLOOR(fraud_score * 10) / 10.0, 1) AS score_bucket,
            COUNT(*)                                  AS count
        FROM decisions
        GROUP BY decision, score_bucket
        ORDER BY score_bucket, decision
    """)


def hourly_volume(days: int = 7, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Transaction volume and block rate by hour-of-day and day-of-week.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: day_of_week, hour_of_day, transaction_count,
        block_count, block_rate_pct.
    """
    _reader = reader or get_reader()
    decisions = _reader.scan_decisions(start_ms(min(days * 24, MAX_HOURS)))
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_HOURLY)

    return DuckDBQueryRunner(decisions=decisions).query("""
        SELECT
            CAST(date_part('dow',  CAST(decision_time_ms AS TIMESTAMP)) AS INTEGER)
                AS day_of_week,
            CAST(date_part('hour', CAST(decision_time_ms AS TIMESTAMP)) AS INTEGER)
                AS hour_of_day,
            COUNT(*)                                               AS transaction_count,
            COUNT(*) FILTER (WHERE decision = 'BLOCK')            AS block_count,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE decision = 'BLOCK') / NULLIF(COUNT(*), 0),
                2
            )                                                      AS block_rate_pct
        FROM decisions
        GROUP BY day_of_week, hour_of_day
        ORDER BY day_of_week, hour_of_day
    """)


def amount_by_decision(days: int = 30, *, reader: IcebergReader | None = None) -> pd.DataFrame:
    """Transaction counts by amount bracket and decision outcome.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision, amount_bucket, txn_count.
    """
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_AMOUNT)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query("""
        SELECT
            d.decision,
            CASE
                WHEN e.amount IS NULL                    THEN 'Unknown'
                WHEN CAST(e.amount AS DOUBLE) < 10       THEN '< $10'
                WHEN CAST(e.amount AS DOUBLE) < 100      THEN '$10-$100'
                WHEN CAST(e.amount AS DOUBLE) < 500      THEN '$100-$500'
                WHEN CAST(e.amount AS DOUBLE) < 1000     THEN '$500-$1K'
                WHEN CAST(e.amount AS DOUBLE) < 5000     THEN '$1K-$5K'
                ELSE                                          '> $5K'
            END                                          AS amount_bucket,
            COUNT(*)                                     AS txn_count
        FROM decisions d
        LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
        GROUP BY d.decision, amount_bucket
        ORDER BY d.decision,
            CASE amount_bucket
                WHEN '< $10'     THEN 0
                WHEN '$10-$100'  THEN 1
                WHEN '$100-$500' THEN 2
                WHEN '$500-$1K'  THEN 3
                WHEN '$1K-$5K'   THEN 4
                WHEN '> $5K'     THEN 5
                ELSE                  6
            END
    """)


def geo_breakdown(
    days: int = 30, top_n: int = 15, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """Top countries by transaction volume with block rate and fraud score metrics.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        top_n: Maximum number of top countries to return.
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: geo_country, total_txns, block_count, flag_count,
        block_rate_pct, avg_fraud_score.
    """
    top_n = max(1, min(top_n, 100))
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_GEO)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query(
        """
        SELECT
            COALESCE(e.geo_country, 'Unknown') AS geo_country,
            COUNT(*) AS total_txns,
            COUNT(*) FILTER (WHERE d.decision = 'BLOCK') AS block_count,
            COUNT(*) FILTER (WHERE d.decision = 'FLAG') AS flag_count,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE d.decision = 'BLOCK') / NULLIF(COUNT(*), 0),
                2
            ) AS block_rate_pct,
            ROUND(AVG(d.fraud_score), 4) AS avg_fraud_score
        FROM decisions d
        LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
        GROUP BY geo_country
        ORDER BY total_txns DESC
        LIMIT ?
        """,
        [top_n],
    )


def velocity_by_decision(
    days: int = 30, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """Average velocity features (1m, 5m, 1h, 24h) grouped by decision outcome.

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: decision, avg_vel_count_1m, avg_vel_count_5m,
        avg_vel_count_1h, avg_vel_count_24h, avg_vel_amount_1h, avg_vel_amount_24h.
    """
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_VEL)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query("""
        SELECT
            d.decision,
            ROUND(COALESCE(AVG(CAST(e.vel_count_1m   AS DOUBLE)), 0.0), 2) AS avg_vel_count_1m,
            ROUND(COALESCE(AVG(CAST(e.vel_count_5m   AS DOUBLE)), 0.0), 2) AS avg_vel_count_5m,
            ROUND(COALESCE(AVG(CAST(e.vel_count_1h   AS DOUBLE)), 0.0), 2) AS avg_vel_count_1h,
            ROUND(COALESCE(AVG(CAST(e.vel_count_24h  AS DOUBLE)), 0.0), 2) AS avg_vel_count_24h,
            ROUND(COALESCE(AVG(CAST(e.vel_amount_1h  AS DOUBLE)), 0.0), 2) AS avg_vel_amount_1h,
            ROUND(COALESCE(AVG(CAST(e.vel_amount_24h AS DOUBLE)), 0.0), 2) AS avg_vel_amount_24h
        FROM decisions d
        LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
        GROUP BY d.decision
        ORDER BY d.decision
    """)


def top_risk_accounts(
    days: int = 7, top_n: int = 20, *, reader: IcebergReader | None = None
) -> pd.DataFrame:
    """High-risk accounts ranked by avg fraud score (minimum 2 transactions).

    Args:
        days: Number of days to include (capped by MAX_HOURS=720).
        top_n: Maximum number of top accounts to return.
        reader: Optional IcebergReader override (default: module singleton).

    Returns:
        DataFrame with columns: account_id, total_txns, block_count, flag_count,
        alert_rate_pct, avg_fraud_score, max_fraud_score.
    """
    top_n = max(1, min(top_n, 100))
    _reader = reader or get_reader()
    s = start_ms(min(days * 24, MAX_HOURS))
    decisions = _reader.scan_decisions(s)
    if decisions.num_rows == 0:
        return pd.DataFrame(columns=_EMPTY_ACCT)

    enriched = _reader.scan_enriched(s)
    return DuckDBQueryRunner(decisions=decisions, enriched=enriched).query(
        """
        SELECT
            e.account_id,
            COUNT(*) AS total_txns,
            COUNT(*) FILTER (WHERE d.decision = 'BLOCK') AS block_count,
            COUNT(*) FILTER (WHERE d.decision = 'FLAG') AS flag_count,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE d.decision IN ('BLOCK', 'FLAG'))
                    / NULLIF(COUNT(*), 0),
                2
            ) AS alert_rate_pct,
            ROUND(AVG(d.fraud_score), 4) AS avg_fraud_score,
            ROUND(MAX(d.fraud_score), 4) AS max_fraud_score
        FROM decisions d
        LEFT JOIN enriched e ON d.transaction_id = e.transaction_id
        WHERE e.account_id IS NOT NULL
        GROUP BY e.account_id
        HAVING COUNT(*) >= 2
        ORDER BY avg_fraud_score DESC
        LIMIT ?
        """,
        [top_n],
    )
