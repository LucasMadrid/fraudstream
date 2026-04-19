-- Trino Analyst Views: Analytics Persistence Layer
-- Catalog: iceberg | Schema: default
-- Deploy target: analytics/views/ (each view is a separate .sql file)

-- ─────────────────────────────────────────────────────────────────────────────
-- v_transaction_audit
-- Full audit join: enriched features + decision per transaction.
-- Deduplication: ROW_NUMBER() absorbs AT_LEAST_ONCE duplicates before compaction.
-- Usage: dispute resolution, regulatory audit, transaction-level investigation.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW iceberg.v_transaction_audit AS
WITH dedup_enriched AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY enrichment_time DESC
           ) AS rn
    FROM iceberg.enriched_transactions
),
dedup_decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.fraud_decisions
)
SELECT
    e.transaction_id,
    e.account_id,
    e.merchant_id,
    e.amount,
    e.currency,
    e.channel,
    e.event_time,
    e.enrichment_time,
    e.geo_country,
    e.geo_city,
    e.geo_network_class,
    e.geo_confidence,
    e.device_txn_count,
    e.device_known_fraud,
    e.prev_geo_country,
    e.prev_txn_time_ms,
    e.vel_count_1m,
    e.vel_amount_1m,
    e.vel_count_5m,
    e.vel_amount_5m,
    e.vel_count_1h,
    e.vel_amount_1h,
    e.vel_count_24h,
    e.vel_amount_24h,
    e.enrichment_latency_ms,
    d.decision,
    d.fraud_score,
    d.rule_triggers,
    d.model_version,
    d.decision_time_ms,
    d.latency_ms          AS decision_latency_ms,
    d.schema_version      AS decision_schema_version
FROM dedup_enriched e
LEFT JOIN dedup_decisions d
    ON e.transaction_id = d.transaction_id
WHERE e.rn = 1
  AND (d.rn = 1 OR d.rn IS NULL);


-- ─────────────────────────────────────────────────────────────────────────────
-- v_fraud_rate_daily
-- Daily aggregated fraud rates by channel and decision outcome.
-- Usage: operational dashboards, business fraud reporting.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW iceberg.v_fraud_rate_daily AS
WITH decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.fraud_decisions
),
enriched AS (
    SELECT transaction_id, channel, amount,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY enrichment_time DESC
           ) AS rn
    FROM iceberg.enriched_transactions
)
SELECT
    CAST(d.decision_time_ms AS DATE)    AS decision_date,
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
    CAST(d.decision_time_ms AS DATE),
    e.channel,
    d.decision;


-- ─────────────────────────────────────────────────────────────────────────────
-- v_rule_triggers
-- Rule trigger frequency and associated score distribution per day.
-- Usage: rule effectiveness analysis, threshold tuning.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW iceberg.v_rule_triggers AS
WITH decisions AS (
    SELECT transaction_id, decision, fraud_score, decision_time_ms, rule_triggers,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.fraud_decisions
    WHERE CARDINALITY(rule_triggers) > 0
),
exploded AS (
    SELECT
        transaction_id,
        decision,
        fraud_score,
        CAST(decision_time_ms AS DATE) AS decision_date,
        rule_name
    FROM decisions
    CROSS JOIN UNNEST(rule_triggers) AS t(rule_name)
    WHERE rn = 1
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


-- ─────────────────────────────────────────────────────────────────────────────
-- v_model_versions
-- Per model version: decision distribution, score stats, latency.
-- Usage: model version comparison, rollout monitoring, performance regression.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW iceberg.v_model_versions AS
WITH decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.fraud_decisions
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
