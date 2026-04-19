-- Iceberg DDL Contract: fraud_decisions v1
-- Source: pipelines/scoring/types.py (FraudDecision dataclass — new type)
-- Catalog: iceberg (REST catalog, MinIO backend)
-- Schema version: 1
-- Partition: decision_date (DATE derived from decision_time_ms), daily

CREATE TABLE IF NOT EXISTS iceberg.fraud_decisions (
    -- Identity
    transaction_id      STRING          NOT NULL,   -- dedup key; join key to enriched_transactions

    -- Decision outcome
    decision            STRING          NOT NULL,   -- ALLOW / FLAG / BLOCK
    fraud_score         DOUBLE          NOT NULL,   -- 0.0–1.0 from MLScore.fraud_probability
    rule_triggers       ARRAY<STRING>   NOT NULL,   -- matched rule names; empty list if none
    model_version       STRING          NOT NULL,   -- from MLScore.model_version

    -- Timing
    decision_time_ms    TIMESTAMP       NOT NULL,   -- drives daily partition
    latency_ms          DOUBLE          NOT NULL,   -- end-to-end from Kafka consumer receipt

    -- Metadata
    schema_version      STRING          NOT NULL    -- default "1"
)
USING iceberg
PARTITIONED BY (days(decision_time_ms))
TBLPROPERTIES (
    'write.parquet.compression-codec'   = 'snappy',
    'write.metadata.compression-codec'  = 'gzip',
    'write.target-file-size-bytes'      = '134217728',  -- 128 MiB
    'history.expire.min-snapshots-to-keep' = '10',
    'format-version'                    = '2'
);
