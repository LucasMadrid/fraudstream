-- v_transaction_audit: Full audit join — enriched features + decision per transaction.
-- Deduplication: ROW_NUMBER() absorbs AT_LEAST_ONCE duplicates before compaction.
-- LEFT JOIN preserves enriched records with no matching decision (decision fields null).
-- Usage: dispute resolution, regulatory audit, transaction-level investigation.

CREATE OR REPLACE VIEW iceberg.default.v_transaction_audit AS
WITH dedup_enriched AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY enrichment_time DESC
           ) AS rn
    FROM iceberg.default.enriched_transactions
),
dedup_decisions AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY transaction_id
               ORDER BY decision_time_ms DESC
           ) AS rn
    FROM iceberg.default.fraud_decisions
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
