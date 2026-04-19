-- Iceberg DDL: fraud_decisions
-- Format-version: 2 (row-level deletes for efficient compaction)
-- Partitioning: days(decision_time_ms) — daily partitions aligned to decision date
-- Deduplication key: transaction_id
-- Retention: 7 years per PCI-DSS 10.7

CREATE TABLE IF NOT EXISTS iceberg.default.fraud_decisions (
    transaction_id   STRING        NOT NULL,
    decision         STRING        NOT NULL,
    fraud_score      DOUBLE        NOT NULL,
    rule_triggers    ARRAY<STRING> NOT NULL,
    model_version    STRING        NOT NULL,
    decision_time_ms TIMESTAMP(6)  NOT NULL,
    latency_ms       DOUBLE        NOT NULL,
    schema_version   STRING        NOT NULL
)
USING iceberg
PARTITIONED BY (days(decision_time_ms))
TBLPROPERTIES (
    'format-version'                       = '2',
    'write.parquet.compression-codec'      = 'snappy',
    'write.metadata.compression-codec'     = 'gzip',
    'write.target-file-size-bytes'         = '134217728',
    'history.expire.min-snapshots-to-keep' = '10'
);
