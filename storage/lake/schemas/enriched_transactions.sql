-- Iceberg DDL: enriched_transactions
-- Managed by: scripts/evolve_iceberg_schema.py
-- Format-version: 2 (row-level deletes for efficient compaction)
-- Partitioning: days(event_time) — daily partitions aligned to event date
-- Deduplication key: transaction_id
-- Retention: 7 years per PCI-DSS 10.7

CREATE TABLE IF NOT EXISTS iceberg.default.enriched_transactions (
    transaction_id      STRING        NOT NULL,
    account_id          STRING        NOT NULL,
    merchant_id         STRING        NOT NULL,
    amount              DECIMAL(18,4) NOT NULL,
    currency            STRING        NOT NULL,
    event_time          TIMESTAMP(6)  NOT NULL,
    enrichment_time     TIMESTAMP(6)  NOT NULL,
    channel             STRING        NOT NULL,
    card_bin            STRING        NOT NULL,
    card_last4          STRING        NOT NULL,
    caller_ip_subnet    STRING        NOT NULL,
    api_key_id          STRING        NOT NULL,
    oauth_scope         STRING        NOT NULL,
    geo_lat             FLOAT,
    geo_lon             FLOAT,
    masking_lib_version STRING        NOT NULL,
    vel_count_1m        INT           NOT NULL,
    vel_amount_1m       DECIMAL(18,4) NOT NULL,
    vel_count_5m        INT           NOT NULL,
    vel_amount_5m       DECIMAL(18,4) NOT NULL,
    vel_count_1h        INT           NOT NULL,
    vel_amount_1h       DECIMAL(18,4) NOT NULL,
    vel_count_24h       INT           NOT NULL,
    vel_amount_24h      DECIMAL(18,4) NOT NULL,
    geo_country         STRING,
    geo_city            STRING,
    geo_network_class   STRING,
    geo_confidence      FLOAT,
    device_first_seen   TIMESTAMP(6),
    device_txn_count    INT,
    device_known_fraud  BOOLEAN,
    prev_geo_country    STRING,
    prev_txn_time_ms    TIMESTAMP(6),
    enrichment_latency_ms INT         NOT NULL,
    processor_version   STRING        NOT NULL,
    schema_version      STRING        NOT NULL
)
USING iceberg
PARTITIONED BY (days(event_time))
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'snappy',
    'write.target-file-size-bytes' = '134217728'
);
