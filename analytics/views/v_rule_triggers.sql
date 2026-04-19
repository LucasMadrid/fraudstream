-- v_rule_triggers: Rule trigger frequency and score distribution per day.
-- Transactions with empty rule_triggers produce NO rows (WHERE CARDINALITY > 0).
-- APPROX_PERCENTILE is approximate — disclosed per FR-026.
-- Usage: rule effectiveness analysis, threshold tuning.

CREATE OR REPLACE VIEW iceberg.default.v_rule_triggers AS
WITH decisions AS (
    SELECT transaction_id, decision, fraud_score, decision_time_ms, rule_triggers,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.default.fraud_decisions
),
exploded AS (
    SELECT
        transaction_id,
        decision,
        fraud_score,
        CAST(from_unixtime(decision_time_ms / 1000) AS DATE) AS decision_date,
        rule_name
    FROM decisions
    CROSS JOIN UNNEST(rule_triggers) AS r(rule_name)
    WHERE rn = 1
      AND CARDINALITY(rule_triggers) > 0
)
SELECT
    decision_date,
    rule_name,
    decision,
    COUNT(*)                            AS trigger_count,
    AVG(fraud_score)                    AS avg_fraud_score,
    APPROX_PERCENTILE(fraud_score, 0.5) AS median_fraud_score,
    APPROX_PERCENTILE(fraud_score, 0.95) AS p95_fraud_score
FROM exploded
GROUP BY
    decision_date,
    rule_name,
    decision;
