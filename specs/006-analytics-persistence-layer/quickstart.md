# Quickstart: Analytics Persistence Layer

**Branch**: `006-analytics-persistence-layer` | **Date**: 2026-04-18

---

## Prerequisites

These services must already be running before starting the analytics tier:

| Service | Tier | Purpose |
|---|---|---|
| Kafka + Schema Registry | Core | Source of enriched transactions |
| MinIO | Core / Analytics | S3-compatible object storage for Iceberg data |
| Trino | Analytics | SQL query engine over Iceberg |
| PyFlink enrichment job | Processing | Produces `EnrichedTransaction` records |
| Scoring engine | Scoring | Produces `FraudDecision` records |

---

## Local Stack Setup

### 1. Start the Analytics Tier

```bash
docker compose up -d iceberg-rest trino
```

The `iceberg-rest` service (Tabular REST catalog) must be healthy before Trino or PyIceberg can resolve table metadata.

### 2. Initialize Iceberg Tables

```bash
docker exec -it fraudstream-iceberg-rest bash /opt/iceberg/init-tables.sh
```

This script runs the DDL contracts idempotently:

- `storage/lake/schemas/enriched_transactions.sql`
- `storage/lake/schemas/fraud_decisions.sql`

If the tables already exist the script is a no-op. Verify:

```bash
docker exec -it fraudstream-trino trino --execute "SHOW TABLES IN iceberg.default"
```

Expected output:

```
enriched_transactions
fraud_decisions
```

### 3. Initialize the Feast Feature Store

```bash
cd storage/feature_store
feast apply
```

This registers the entities and feature views from `storage/feature_store/features/` against the local SQLite registry. Run `feast teardown && feast apply` to reset.

---

## Running the Sinks

The Iceberg and Feast sinks are wired as side outputs inside the existing enrichment and scoring jobs — they start automatically when the jobs start.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ICEBERG_REST_URI` | `http://localhost:8181` | REST catalog endpoint |
| `ICEBERG_WAREHOUSE` | `s3://fraudstream-lake/` | MinIO bucket path |
| `AWS_S3_ENDPOINT` | `http://localhost:9000` | MinIO S3-compatible endpoint |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | MinIO credentials (local only) |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin` | MinIO credentials (local only) |
| `FEAST_REPO_PATH` | `storage/feature_store` | Feast repo root |
| `ICEBERG_FLUSH_INTERVAL_S` | `1` | Seconds between sink flushes |
| `ICEBERG_BUFFER_MAX` | `100` | Max records per flush batch (enrichment sink) |
| `ICEBERG_DECISIONS_BUFFER_MAX` | `100` | Max records per flush batch (decisions sink) |

**Flush interval and the 5-second write budget**: The 5-second budget (FR-004, FR-023) is measured from Kafka consumer receipt to Iceberg commit confirmation. At `ICEBERG_FLUSH_INTERVAL_S=1`, worst-case flush delay is just under 1 second (a record arriving immediately after a flush must wait for the next interval). The actual write — PyIceberg `table.append()` + S3 PUT to MinIO — adds network and storage latency. In practice this should be under 2 seconds at normal volume, leaving margin within the 5-second budget. At 2× peak volume with larger batches, monitor `iceberg_write_latency_ms` p99. If p99 approaches 4 seconds, reduce `ICEBERG_FLUSH_INTERVAL_S` or scale the MinIO instance. Do not increase `ICEBERG_FLUSH_INTERVAL_S` above 4 seconds — it eliminates the budget margin.

### Start the Enrichment Job (with Iceberg + Feast side outputs)

```bash
uv pip install --python .venv pyiceberg[pyarrow,s3fs] feast
cd pipelines/processing
python -m flink run enrichment_job.py
```

### Start the Scoring Job (with Iceberg decisions side output)

```bash
cd pipelines/scoring
python -m flink run scoring_job.py
```

---

## Schema Validation

Run these before merging any Avro schema change:

```bash
# Generate/validate Iceberg DDL from Avro schema
python scripts/evolve_iceberg_schema.py \
    --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
    --output storage/lake/schemas/enriched_transactions.sql

# Validate Feast feature views match Avro fields
python scripts/validate_feast_schemas.py \
    --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
    --feast-repo storage/feature_store
```

CI runs both scripts automatically on `.avsc` file changes (`.github/workflows/schema-evolution.yml`). The merge gate blocks if either script exits non-zero.

---

## Validating the Write Paths

### Check Iceberg Row Counts

```sql
-- Run via Trino CLI or any Trino-compatible client
SELECT COUNT(*) FROM iceberg.enriched_transactions;
SELECT COUNT(*) FROM iceberg.fraud_decisions;
```

### Verify a Specific Transaction

```sql
SELECT *
FROM iceberg.v_transaction_audit
WHERE transaction_id = '<your-transaction-id>';
```

Both the enriched features and the decision record should appear in a single row. If the decision row is null, the decisions sink may be slightly behind — retry after a few seconds (eventual consistency window ≤ 5 s).

### Point-in-Time Feature Lookup (Offline Store)

```python
from feast import FeatureStore
import pandas as pd

store = FeatureStore(repo_path="storage/feature_store")

entity_df = pd.DataFrame({
    "account_id": ["acct-001"],
    "event_timestamp": [pd.Timestamp("2026-04-18T10:00:00Z")],
})

features = store.get_historical_features(
    entity_df=entity_df,
    features=[
        "velocity_features:vel_count_1h",
        "geo_features:geo_country",
        "device_features:prev_geo_country",
    ],
).to_df()
print(features)
```

The returned values must match what is stored in `iceberg.enriched_transactions` for the same `account_id` at the specified `event_timestamp`.

---

## Reconciliation Check (Pre-Production Checklist)

After processing a known batch of N transactions:

```bash
python tests/integration/test_iceberg_enriched_sink.py --expected-count N
python tests/integration/test_iceberg_decisions_sink.py --expected-count N
```

Both tests compare Kafka topic offset to Iceberg row count. The acceptable gap is ≤ 0.1% (AT_LEAST_ONCE delivery absorbed by deduplication). Any larger gap is a test failure.

---

## PCI-DSS Access Governance

`iceberg.enriched_transactions` contains `card_bin`, `card_last4`, `caller_ip_subnet`, and `api_key_id`. These fields are already masked upstream (per Principle VII and the masking contract in `data-model.md`), but the Iceberg table is still in PCI-DSS scope.

**Local development**: No access controls — all fields visible. **Requirement**: Real card data MUST NOT be used in local development environments. This is a formal requirement, not advisory — enforcement is via environment flag: the local Feast and Iceberg stack MUST be started with `FRAUDSTREAM_ENV=local`, and any pipeline that detects `FRAUDSTREAM_ENV != local` with a SQLite online store MUST refuse to start. If a developer's pipeline starts without this flag set, it will attempt to connect to the production catalog and fail loudly.

**Production**:
- **MinIO bucket policy**: Restrict to read-only access for the analytics service account (`analytics-svc`) only. No write access for any non-pipeline identity. Bucket policy must deny `s3:DeleteObject` and `s3:PutObject` for all identities except the pipeline service account (`pipeline-svc`).
- **Trino column masking**: Apply column masking (not row-level security) for `card_bin` and `card_last4` for all roles except `pci-analyst`. Column masking is implemented via Trino's built-in column masking rules (OPA policy or Ranger plugin — choose based on production deployment). Non-PCI roles see `card_bin` as `'XXXXXX'` and `card_last4` as `'XXXX'`.
- **Audit logging**: All Trino queries against `card_bin`, `card_last4`, `caller_ip_subnet`, and `api_key_id` columns MUST be logged. Each audit log entry MUST include: query text (full SQL), user identity (Trino principal), columns accessed, timestamp, and query duration. Audit logs MUST be retained for 7 years, co-located with the Iceberg table retention policy.
- **Access review**: Quarterly review of all identities with `pci-analyst` role access. Review process: security team pulls the Trino role membership list, cross-references against HR active employee list, removes departed employees, documents findings in the security runbook under "PCI Access Review". Review evidence (the membership diff and approver sign-off) is retained for 7 years per PCI-DSS 10.7.

### Analyst View Schema Evolution Policy

When adding or renaming columns in analyst views (`v_transaction_audit`, `v_fraud_rate_daily`, `v_rule_triggers`, `v_model_versions`):
- **Adding a column**: Backward-compatible — existing queries ignore new columns. Deploy the new view definition; existing queries continue to work.
- **Renaming a column**: Breaking change — existing queries referencing the old column name will fail. Process: (1) Add the new column name alongside the old name in the same view, (2) Communicate the deprecation window (minimum 30 days) to analyst consumers, (3) Remove the old column after the window. Never rename a column without a deprecation window.
- **Removing a column**: Breaking change — same process as renaming.
- **View DDL changes**: All view changes MUST be deployed via `analytics/views/` SQL files and re-applied via Trino DDL runner. Do not modify views directly in the Trino catalog without committing the SQL file.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `NoSuchTableError` on PyIceberg write | Tables not initialized | Run `init-tables.sh` |
| `ConnectionRefused` on Feast push | Feast registry not initialized | Run `feast apply` |
| Row count gap > 0.1% | Flink job restarted mid-batch | Wait for daily compaction or trigger manually |
| Trino query returns 0 rows | Iceberg metadata cache stale | `CALL iceberg.system.refresh_metadata_cache('default', 'enriched_transactions')` |
| `prev_geo_country` always null | New device (first transaction) | Expected — nullable by design |
| Feast online store returns stale values | Push API skipped on flush failure | Check `feast_push_failures_total` metric; retry from DLQ |
