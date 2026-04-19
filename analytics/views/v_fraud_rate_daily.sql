-- v_fraud_rate_daily: Daily aggregated fraud rates by channel and decision outcome.
-- APPROX_PERCENTILE is approximate (not exact) — disclosed to analyst consumers per FR-026.
-- Usage: operational dashboards, business fraud reporting.

CREATE OR REPLACE VIEW iceberg.default.v_fraud_rate_daily AS
WITH decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.default.fraud_decisions
),
enriched AS (
    SELECT transaction_id, channel, amount,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY enrichment_time DESC
           ) AS rn
    FROM iceberg.default.enriched_transactions
)
SELECT
    CAST(from_unixtime(d.decision_time_ms / 1000) AS DATE) AS decision_date,
    e.channel,
    d.decision,
    COUNT(*)                            AS transaction_count,
    SUM(e.amount)                       AS total_amount,
    AVG(d.fraud_score)                  AS avg_fraud_score,
    APPROX_PERCENTILE(d.latency_ms, 0.99) AS p99_latency_ms
FROM decisions d
JOIN enriched e
    ON d.transaction_id = e.transaction_id
   AND e.rn = 1
WHERE d.rn = 1
GROUP BY
    CAST(from_unixtime(d.decision_time_ms / 1000) AS DATE),
    e.channel,
    d.decision;
