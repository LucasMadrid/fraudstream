# Data Model: Analytics Persistence Layer

**Branch**: `006-analytics-persistence-layer` | **Date**: 2026-04-18

---

## Entities

### EnrichedTransaction

Canonical source: `pipelines/processing/schemas/enriched-txn-v1.avsc`  
Iceberg table: `iceberg.enriched_transactions`  
Partition: `event_date` (DATE derived from `event_time`), daily  
Deduplication key: `transaction_id`  
Write path: `EnrichedRecordAssembler` → `IcebergEnrichedSink` (side output)

| Field | Avro Type | Iceberg Type | Nullable | Notes |
|---|---|---|---|---|
| `transaction_id` | `string` | `string` | No | Partition + dedup key |
| `account_id` | `string` | `string` | No | |
| `merchant_id` | `string` | `string` | No | |
| `amount` | `bytes` (decimal 18,4) | `decimal(18,4)` | No | |
| `currency` | `string` | `string` | No | ISO-4217 |
| `event_time` | `long` (timestamp-millis) | `timestamp` | No | Event time; drives daily partition |
| `enrichment_time` | `long` (timestamp-millis) | `timestamp` | No | Processing time of sink write |
| `channel` | `string` | `string` | No | POS/WEB/MOBILE/API |
| `card_bin` | `string` | `string` | No | PCI-DSS scope |
| `card_last4` | `string` | `string` | No | PCI-DSS scope |
| `caller_ip_subnet` | `string` | `string` | No | Masked /24 |
| `api_key_id` | `string` | `string` | No | Device proxy (v1) |
| `oauth_scope` | `string` | `string` | No | |
| `geo_lat` | `float` (nullable) | `float` | Yes | Raw from API, not GeoLite2 |
| `geo_lon` | `float` (nullable) | `float` | Yes | Raw from API, not GeoLite2 |
| `masking_lib_version` | `string` | `string` | No | |
| `vel_count_1m` | `int` | `int` | No | Velocity window 60s |
| `vel_amount_1m` | `bytes` (decimal 18,4) | `decimal(18,4)` | No | |
| `vel_count_5m` | `int` | `int` | No | Velocity window 5m |
| `vel_amount_5m` | `bytes` (decimal 18,4) | `decimal(18,4)` | No | |
| `vel_count_1h` | `int` | `int` | No | Velocity window 1h |
| `vel_amount_1h` | `bytes` (decimal 18,4) | `decimal(18,4)` | No | |
| `vel_count_24h` | `int` | `int` | No | Velocity window 24h |
| `vel_amount_24h` | `bytes` (decimal 18,4) | `decimal(18,4)` | No | |
| `geo_country` | `string` (nullable) | `string` | Yes | ISO 3166-1 alpha-2 from GeoLite2 |
| `geo_city` | `string` (nullable) | `string` | Yes | |
| `geo_network_class` | `enum` (nullable) | `string` | Yes | RESIDENTIAL/BUSINESS/HOSTING/MOBILE/UNKNOWN |
| `geo_confidence` | `float` (nullable) | `float` | Yes | 0.0–1.0 |
| `device_first_seen` | `long` (nullable, timestamp-millis) | `timestamp` | Yes | Null on first transaction per device |
| `device_txn_count` | `int` (nullable) | `int` | Yes | |
| `device_known_fraud` | `boolean` (nullable) | `boolean` | Yes | Always null in v1 (TD-002) |
| `prev_geo_country` | `string` (nullable) | `string` | Yes | **NEW** — previous geo on this device; null on first txn |
| `prev_txn_time_ms` | `long` (nullable, timestamp-millis) | `timestamp` | Yes | **NEW** — previous txn event time; null on first txn |
| `enrichment_latency_ms` | `int` | `int` | No | |
| `processor_version` | `string` | `string` | No | |
| `schema_version` | `string` | `string` | No | Default "1" |

**Fields NEW vs existing**: `prev_geo_country` and `prev_txn_time_ms` require changes to `DeviceProcessFunction` state and `_assemble_record`. See research.md §6.

---

### FraudDecision

**New type** — does not exist yet in the codebase. See research.md §1.  
Canonical source: `pipelines/scoring/types.py` (new `FraudDecision` dataclass)  
Iceberg table: `iceberg.fraud_decisions`  
Partition: `decision_date` (DATE derived from `decision_time_ms`), daily  
Deduplication key: `transaction_id`  
Write path: Scoring engine → `IcebergDecisionsSink` (every ALLOW/FLAG/BLOCK)

| Field | Python Type | Iceberg Type | Nullable | Notes |
|---|---|---|---|---|
| `transaction_id` | `str` | `string` | No | Join key to `enriched_transactions` |
| `decision` | `Literal["ALLOW","FLAG","BLOCK"]` | `string` | No | |
| `fraud_score` | `float` | `double` | No | 0.0–1.0, from `MLScore.fraud_probability` |
| `rule_triggers` | `list[str]` | `list<string>` | No | Empty list if no rules matched |
| `model_version` | `str` | `string` | No | From `MLScore.model_version` |
| `decision_time_ms` | `int` | `timestamp` | No | Drives daily partition |
| `latency_ms` | `float` | `double` | No | End-to-end: from Kafka consumer receipt |
| `schema_version` | `str` | `string` | No | Default "1" |

---

### FeatureSet (Feast)

Three Feast feature views, one per feature family. Entity: `account_id`.  
Online backend: SQLite (local), Redis/DynamoDB (production).  
Offline backend: Parquet files (local), BigQuery/Redshift (production).  
Materialization: Push API (`feast.FeatureStore.push()`) called per flush in `IcebergEnrichedSink`.

#### VelocityFeatureView

Source: `EnrichedTransaction` fields.  
Entity: `account_id` | TTL: 48h (matches DeduplicationFilter window)

| Feature | Type | Source Field |
|---|---|---|
| `vel_count_1m` | `Int64` | `vel_count_1m` |
| `vel_amount_1m` | `Float64` | `vel_amount_1m` (decimal → float) |
| `vel_count_5m` | `Int64` | `vel_count_5m` |
| `vel_amount_5m` | `Float64` | `vel_amount_5m` |
| `vel_count_1h` | `Int64` | `vel_count_1h` |
| `vel_amount_1h` | `Float64` | `vel_amount_1h` |
| `vel_count_24h` | `Int64` | `vel_count_24h` |
| `vel_amount_24h` | `Float64` | `vel_amount_24h` |

#### GeoFeatureView

Entity: `account_id` | TTL: 24h

| Feature | Type | Source Field |
|---|---|---|
| `geo_country` | `String` | `geo_country` |
| `geo_city` | `String` | `geo_city` |
| `geo_network_class` | `String` | `geo_network_class` |
| `geo_confidence` | `Float64` | `geo_confidence` |

#### DeviceFeatureView

Entity: `account_id` | TTL: 14 days (matches `DeviceProcessFunction` idle TTL)

| Feature | Type | Source Field |
|---|---|---|
| `device_first_seen` | `Int64` | `device_first_seen` (epoch ms) |
| `device_txn_count` | `Int64` | `device_txn_count` |
| `device_known_fraud` | `Bool` | `device_known_fraud` |
| `prev_geo_country` | `String` | `prev_geo_country` |
| `prev_txn_time_ms` | `Int64` | `prev_txn_time_ms` (epoch ms) |

---

## Relationships

```
EnrichedTransaction (transaction_id) ──1:1──► FraudDecision (transaction_id)
EnrichedTransaction (account_id)     ──N:1──► FeatureSet (account_id)
```

- Join is `transaction_id` — standard SQL, no UDF, no pre-computed table.
- Analyst view `v_transaction_audit.sql` materialises this join with deduplication window.
- Eventual consistency window: both Iceberg sinks write asynchronously; queries must tolerate a propagation gap of up to 5 seconds per side.

---

## Schema Evolution Rules

1. New nullable field in `enriched-txn-v1.avsc` → MUST declare `"default": null` in the Avro schema. This is a hard requirement for BACKWARD_TRANSITIVE compatibility — not an implementation suggestion. Existing consumers of the topic must be able to deserialize records that omit the new field using the default value. Fields without `"default": null` that are added to the schema WILL break existing consumers and MUST be rejected by the CI schema gate.
2. Run `scripts/evolve_iceberg_schema.py` → generates `ALTER TABLE enriched_transactions ADD COLUMN ...` statement → commit to `storage/lake/schemas/enriched_transactions.sql`.
3. Run `scripts/validate_feast_schemas.py` → if field is a feature (velocity/geo/device), update the relevant Feast view in `storage/feature_store/features/`.
4. CI blocks merge until all three (Avro, Iceberg DDL, Feast views) are in sync.
5. Update `docs/data_catalog.yaml` with new field entry (owner, sensitivity, lineage).

### Iceberg Format-Version 2

Both tables use Iceberg format-version 2. Behavioral implications vs. format-version 1:
- **Row-level deletes**: format-version 2 supports delete files (position deletes and equality deletes), enabling efficient row-level operations without full file rewrite. This is used by Trino compaction to remove cross-batch duplicates (DELETE WHERE transaction_id IN ...).
- **Copy-on-write (format-version 1)**: format-version 1 required rewriting entire data files for any row modification. Using format-version 2 ensures compaction is cost-effective at the expected table volume (50M+ rows).
- **Append-only constraint**: Despite format-version 2 supporting deletes, the tables are append-only per FR-022. Deletes are permitted ONLY by the compaction job for deduplication — not by any other consumer.

### DECIMAL(18,4) Precision Justification

`amount` and all velocity amount fields (`vel_amount_1m`, `vel_amount_5m`, `vel_amount_1h`, `vel_amount_24h`) use `DECIMAL(18,4)`. Rationale: the upstream transaction source produces monetary amounts as Avro `bytes` with logical type `decimal(18,4)`, matching the 4-decimal-place precision required for financial amounts in currencies like JPY (0 decimal places, handled by zero-padding) through KWD (3 decimal places). The 18-digit precision supports cumulative velocity amounts up to 99,999,999,999,999.9999 without overflow. This type mapping is a contract inherited from the upstream Avro schema — any change requires a schema evolution cycle.

### Known Technical Debt

**TD-002**: `device_known_fraud` is always null in v1. The `DeviceProcessFunction` state does not currently populate this field — it requires a feed of confirmed fraud labels from the operational review store, which is out of scope for this feature. Resolution plan: In the feature that implements the fraud outcome feedback loop (tentatively feature 007 or 008), `DeviceProcessFunction` will be updated to subscribe to the review store's confirmed-fraud event stream and update device state accordingly. Until then, `device_known_fraud` MUST remain nullable and MUST be documented as always-null in the data catalog.

---

## Data Catalog Reference

Full catalog at `docs/data_catalog.yaml`. PCI-DSS scope fields and masking status:

| Field | Table | Sensitivity | Masking Status | Upstream Masking Guarantee |
|---|---|---|---|---|
| `card_bin` | `enriched_transactions` | PCI-DSS | Partial (first 6 digits only) | Masked by API gateway before reaching pipeline — raw PAN never appears in Kafka topic |
| `card_last4` | `enriched_transactions` | PCI-DSS | Partial (last 4 digits only) | Masked by API gateway before reaching pipeline — raw PAN never appears in Kafka topic |
| `caller_ip_subnet` | `enriched_transactions` | PII-masked | /24 subnet (last octet zeroed) | **Contract**: The masking library (`masking-lib`, tracked via `masking_lib_version`) applies /24 truncation before any Kafka write. The pipeline MUST NOT receive or store raw IP addresses. This is enforced by: (1) Schema Registry validation rejecting records that do not match the `/24` subnet pattern; (2) `masking_lib_version` field presence being mandatory (NOT NULL) — absence of this field is a masking pipeline failure indicator. If raw IPs appear in `iceberg.enriched_transactions`, it is a masking pipeline failure and a PCI-DSS incident. |
| `api_key_id` | `enriched_transactions` | Sensitive-linkage | Not masked (opaque token) | API key IDs are opaque tokens — they do not contain PAN or PII. They enable cross-account device tracking and are in scope for access control but not for PCI-DSS masking. |
| `account_id` | Both tables | Internal | Not masked | Not a financial identifier; account-level aggregation only |
| `geo_country`, `geo_city` | `enriched_transactions` | Low | Not masked | Derived from the masked /24 subnet via GeoLite2 — city-level granularity, no raw IP exposure |
| All velocity fields | `enriched_transactions` | Internal | Not masked | No PII, no PCI scope |
| `fraud_score`, `rule_triggers`, `decision` | `fraud_decisions` | Internal | Not masked | No PII, no PCI scope |

### Retention Scope

The 7-year retention requirement (FR-021, PCI-DSS 10.7) applies to:
- **Data files**: All Parquet/ORC data files in the MinIO warehouse (`s3://fraudstream-lake/`)
- **Iceberg metadata snapshots**: All `metadata/` JSON files tracking table history, including manifest files and snapshot logs

Both must be retained for 7 years. The MinIO bucket policy MUST NOT include lifecycle rules that expire metadata snapshots before the 7-year window, even if data files are retained. Iceberg metadata compaction (via `expire_snapshots`) is permitted but MUST be configured to retain at minimum 7 years of snapshot history to support time-travel queries for audit purposes.
