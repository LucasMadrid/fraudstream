# Research: Analytics Persistence Layer

**Branch**: `006-analytics-persistence-layer` | **Date**: 2026-04-18

---

## §1 — Design Gap: FraudDecision Type Missing from Scoring Engine

**Finding**: The current scoring pipeline has `EvaluationResult` (determination: clean/suspicious, matched_rules) and `MLScore` (fraud_probability, model_version, latency_ms) and `FraudAlert` (suspicious-only, no ALLOW records). There is no unified `FraudDecision` type that combines these into the ALLOW/FLAG/BLOCK format the constitution requires for `iceberg.fraud_decisions`.

**Decision**: Add `FraudDecision` as a new frozen dataclass to `pipelines/scoring/types.py`. Fields: `transaction_id`, `decision` (Literal["ALLOW", "FLAG", "BLOCK"]), `fraud_score`, `rule_triggers`, `model_version`, `decision_time_ms`, `latency_ms`, `schema_version`. The scoring engine produces a `FraudDecision` on every evaluation (not only suspicious ones) and passes it to `IcebergDecisionsSink`. This is additive — `FraudAlert` and `AlertPostgresSink` are unchanged.

**Alternatives considered**:
- Reuse `FraudAlert` and add fields: rejected — `FraudAlert` is suspicious-only by design; extending it forces null fields on ALLOW records and confuses the PostgreSQL sink.
- Write to Iceberg from a separate consumer of `txn.fraud.alerts`: rejected — this misses ALLOW decisions (alerts are only raised for FLAG/BLOCK) and violates FR-008.

---

## §2 — Iceberg Write Path: PyIceberg vs Flink Iceberg Connector

**Decision**: Use **PyIceberg 0.7+** directly from Python, called as a synchronous side-output sink inside a `SinkFunction` wrapper. Write mode: `append` with `write.parquet.compression-codec=snappy`.

**Rationale**: The PyFlink enrichment job is Python 3.11. The Java Flink Iceberg connector (`flink-connector-iceberg`) requires Java operators and does not interoperate with PyFlink's Python DataStream API without a UDF bridge. PyIceberg gives a pure-Python API, is actively maintained by the Apache Iceberg project, and supports the MinIO (S3-compatible) catalog out of the box via the `pyiceberg[pyarrow,s3fs]` extras.

**Write pattern**: Batch records in-process (configurable flush interval, default 1 s or 100 records, whichever comes first) and call `table.append(pa.Table(...))`. This keeps per-record write overhead off the Flink checkpoint cycle and stays well within the 5-second budget at expected throughput.

**Alternatives considered**:
- Java Flink Iceberg connector: rejected — requires Java operators; incompatible with PyFlink Python DataStream API without cross-language plumbing that adds complexity and maintenance cost.
- Flink SQL `CREATE TABLE ... WITH ('connector' = 'iceberg')`: rejected — PyFlink SQL API does not support custom Python UDFs in the same execution graph as the DataStream operators; would require a separate job.
- Direct MinIO write via boto3 + Parquet: rejected — bypasses Iceberg catalog, breaks schema enforcement, and loses partition management.

---

## §3 — Iceberg Catalog: REST Catalog vs Hive vs Hadoop

**Decision**: **Iceberg REST Catalog** (Tabular's open-source implementation, Docker image `tabulario/iceberg-rest`) for local development; S3 Tables catalog or Glue catalog in cloud.

**Rationale**: The Hive Metastore requires a full Hive installation (heavy for local dev). The Hadoop catalog stores metadata directly in MinIO paths — no atomic rename on S3-compatible storage (MinIO does not support atomic directory rename). The REST catalog is a lightweight HTTP server that handles catalog operations and is the standard for S3-compatible storage in 2025. PyIceberg supports it natively as `catalog.rest`.

**Alternatives considered**:
- Hadoop catalog on MinIO: rejected — atomic rename not supported on S3-compatible storage; catalog metadata corruption risk on concurrent writes.
- Hive Metastore: rejected — adds PostgreSQL + Hive dependency to Analytics tier; ~300 MB overhead for a catalog that manages 2 tables.
- DynamoDB catalog: rejected — not available locally without mocking; cloud-only.

---

## §4 — Feast Configuration: Online and Offline Backends

**Decision**:
- **Online store**: SQLite (Feast built-in, zero infra). Production replacement: Redis or DynamoDB — feature view definitions are backend-agnostic.
- **Offline store**: Parquet files on local filesystem. Production replacement: BigQuery or Redshift offline store.
- **Registry**: SQLite file (`feature_store/registry.db`). Production: S3-backed registry.
- **Materialization**: Push API (`feast.FeatureStore.push()`) called synchronously from the `IcebergEnrichedSink` after each flush. This is the correct pattern for streaming materialization — the batch `materialize` CLI command is for historical backfill only.

**Rationale**: The spec mandates features available in online store within 5 seconds of enrichment event. Push API is the only mechanism that achieves this on a per-event basis without a separate materialization job. The SQLite/parquet combo is zero-infra for local dev and production backends are swapped via `feature_store.yaml` config only.

**Point-in-time correctness**: Feast's push API accepts `event_timestamp` per record. Setting this to the `event_time` from the enriched record (not the wall clock at push time) is the mechanism that makes offline snapshots point-in-time correct. The offline parquet store sorts by `event_timestamp`; Feast's `get_historical_features()` filters by `timestamp` column to prevent future leakage.

**Alternatives considered**:
- Separate Feast materialization Flink job consuming `txn.enriched`: rejected — adds a second Flink job, doubles operational surface, and introduces an extra hop that widens the <5-second window.
- Redis online backend locally: rejected — adds Redis to the Analytics tier (it is already in Core tier, but for velocity state; sharing a Redis instance between velocity state and Feast online store creates coupling and TTL conflicts).

---

## §5 — Data Modeling: Schema Coherence Strategy

**Decision**: Hybrid schema-first approach.

- **Avro → Iceberg DDL**: Automated. `scripts/evolve_iceberg_schema.py` reads `enriched-txn-v1.avsc` from the Schema Registry (or local file) and generates idempotent `CREATE TABLE` / `ALTER TABLE` statements. The generated DDL is committed to `storage/lake/schemas/` and reviewed in PRs. CI runs this script on every `avsc` file change and fails if the checked-in DDL is out of sync.
- **Avro → Feast feature views**: Manual Python + CI validation. Avro encodes field names and types; Feast needs entity key, TTL, and aggregation semantics that have no Avro equivalent. Feast views are maintained in Python (`storage/feature_store/features/`). `scripts/validate_feast_schemas.py` checks that every Avro field name that maps to a feature (velocity, geo, device fields) appears as a feature in the corresponding Feast view. Blocks merge if any field is missing.
- **Trino views**: 4 hand-authored SQL views in `analytics/views/`. No dbt — the 2-table model does not justify the overhead. Views are reviewed and deployed as part of Trino init.
- **Data catalog**: `docs/data_catalog.yaml` — Git-tracked, code-reviewed YAML with field-level ownership, sensitivity tags, and upstream/downstream lineage. Upgraded to Amundsen or DataHub only when table count exceeds ~10.

**Alternatives considered**: See data engineer agent report in session context.

---

## §6 — Field Gap: `prev_geo_country` and `prev_txn_time_ms`

**Finding**: The constitution's `iceberg.enriched_transactions` schema includes `prev_geo_country` (nullable string) and `prev_txn_time_ms` (nullable epoch ms). These fields are NOT present in the current `EnrichedRecordAssembler._assemble_record()` output or in `enriched-txn-v1.avsc`. The `DeviceProcessFunction` tracks `last_seen_ms` but does not track the previous geo country.

**Decision**: Add `prev_geo_country` and `prev_txn_time_ms` to:
1. `DeviceProcessFunction` state (`DeviceProfileState` dataclass — add `last_geo_country: str | None` and replace `last_seen_ms` access with an output field).
2. `_assemble_record()` in `enricher.py` — pass through from `device_dict`.
3. `enriched-txn-v1.avsc` — add as nullable fields with `"default": null` (BACKWARD_TRANSITIVE compliant).
4. `storage/lake/schemas/enriched_transactions.sql` — include in Iceberg DDL.

These fields are nullable (null on first transaction per device), so adding them is a non-breaking schema change per Principle III.

---

## §7 — Deduplication in Iceberg

**Decision**: Iceberg does not have native `ON CONFLICT DO NOTHING` semantics. Deduplication is enforced by:

1. **Sink-level deduplication**: `IcebergEnrichedSink` and `IcebergDecisionsSink` maintain a short-lived in-memory set of `transaction_id` values seen in the current flush batch. Duplicates within a batch are dropped before the `table.append()` call.
2. **Table-level compaction**: An Iceberg maintenance job (run daily, separate from the hot path) uses `DELETE FROM ... WHERE transaction_id IN (SELECT transaction_id FROM ... GROUP BY transaction_id HAVING COUNT(*) > 1)` to remove any cross-batch duplicates introduced by Flink recovery. This is the accepted pattern for AT_LEAST_ONCE Iceberg sinks.
3. **Query-layer deduplication view**: The analyst view `v_transaction_audit.sql` uses `ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY enrichment_time DESC) = 1` to present a deduplicated view to analysts even before compaction runs.

**Alternatives considered**:
- Iceberg MERGE INTO: requires Iceberg v2 with position-delete support and Trino write path — adds complexity and is not needed if compaction is run regularly.
- Upsert mode in PyIceberg: not stable in PyIceberg 0.7; MERGE INTO requires Iceberg format v2 table with equality deletes.
