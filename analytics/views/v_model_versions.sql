-- v_model_versions: Per model version decision distribution, score stats, latency.
-- APPROX_PERCENTILE is approximate — disclosed per FR-026.
-- Usage: model version comparison, rollout monitoring, performance regression.

CREATE OR REPLACE VIEW iceberg.default.v_model_versions AS
WITH decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.default.fraud_decisions
)
SELECT
    model_version,
    CAST(decision_time_ms AS DATE)      AS decision_date,
    decision,
    COUNT(*)                            AS transaction_count,
    AVG(fraud_score)                    AS avg_fraud_score,
    APPROX_PERCENTILE(fraud_score, 0.5)  AS median_fraud_score,
    APPROX_PERCENTILE(fraud_score, 0.95) AS p95_fraud_score,
    AVG(latency_ms)                     AS avg_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.99) AS p99_latency_ms
FROM decisions
WHERE rn = 1
GROUP BY
    model_version,
    CAST(decision_time_ms AS DATE),
    decision;
