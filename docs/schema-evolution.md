# Schema Evolution Guide

**Last Updated**: 2026-04-18  
**Applies To**: FraudStream Analytics Persistence Layer (Feature 006)

---

## Overview

The FraudStream analytics persistence layer implements a **three-layer schema contract** to maintain backward compatibility across streaming and batch contexts:

1. **Avro** (Kafka schema, event source of truth)  
   Format: `pipelines/processing/schemas/enriched-txn-v1.avsc`  
   Enforced: BACKWARD_TRANSITIVE (consumers reading old messages must not break)

2. **Iceberg DDL** (append-only analytics table on MinIO)  
   Generated from: `scripts/evolve_iceberg_schema.py`  
   Location: `storage/lake/schemas/enriched_transactions.sql`  
   Partitioned: by `days(event_time)` for efficient time-range queries

3. **Trino** (SQL query layer, read-only)  
   Connects: to Iceberg catalog on MinIO  
   Deduplicates: via daily compaction + query-layer `ROW_NUMBER()` window

**Key Contract**: New fields MUST carry an Avro `"default": null` to allow existing code reading old records without crashing. Iceberg reads this and generates nullable columns.

---

## Adding a New Field (Step-by-Step)

### 1. Update Avro Schema

Edit `pipelines/processing/schemas/enriched-txn-v1.avsc`:

```json
{
  "name": "new_field_name",
  "type": ["null", "string"],
  "default": null,
  "doc": "Field description. BACKWARD_TRANSITIVE: default null required."
}
```

**Rules**:
- Use a **union** with `"null"` first: `["null", "<actual-type>"]`
- Always set `"default": null`
- Add a docstring explaining the field's purpose and nullability
- Field name must be lowercase with underscores (snake_case)

**Example**: Adding a device fingerprint hash:

```json
{
  "name": "device_fingerprint_hash",
  "type": ["null", "string"],
  "default": null,
  "doc": "SHA256 hash of device attributes (UA, IP, accept-language). Null if device fingerprinting failed. BACKWARD_TRANSITIVE: default null required."
}
```

### 2. Update Iceberg DDL

Run the evolution script — it reads Avro and generates `ALTER TABLE` statements:

```bash
python scripts/evolve_iceberg_schema.py \
  --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
  --output storage/lake/schemas/enriched_transactions.sql
```

**What it does**:
- Parses Avro fields
- Maps Avro types to Iceberg SQL (e.g., `["null", "string"]` → `STRING`)
- Detects existing fields (idempotent — won't re-add fields already in DDL)
- Appends `ALTER TABLE ADD COLUMN` if new fields are found
- If no existing DDL, generates `CREATE TABLE` instead

**Output**: Script appends to the `.sql` file. Example:

```sql
ALTER TABLE enriched_transactions ADD COLUMN device_fingerprint_hash STRING;
```

### 3. Run the Evolution Script in CI

The CI gate (`.github/workflows/schema-evolution.yml`) automatically validates alignment:

```bash
# CI runs this on every .avsc push or PR:
python scripts/evolve_iceberg_schema.py \
  --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
  --output storage/lake/schemas/enriched_transactions.sql

# Also validates Feast schema alignment:
python scripts/validate_feast_schemas.py \
  --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
  --feast-repo storage/feature_store
```

**If CI fails**: The Avro schema and Iceberg DDL are misaligned. Fix the Avro schema and re-run locally before pushing.

### 4. Materialize to Feature Store (if needed)

If the new field is a **computed feature** (velocity, geo, device):

1. Add a feature view to `storage/feature_store/` (Feast definition)
2. Update the enrichment job to compute and materialize the feature:
   ```python
   feast_registry.push_features_from_df(
       feature_df,
       feature_view="my_features",
       event_timestamp="event_time"  # CRITICAL: use event_time, not wall-clock
   )
   ```
3. Verify both online (SQLite) and offline (Parquet) stores contain the feature

### 5. Validate End-to-End

**Before deploying to production:**

```bash
# 1. Start local stack (Flink, Kafka, MinIO, Trino, Feast)
docker-compose up -d

# 2. Run integration test
pytest tests/integration/test_schema_evolution.py -v

# 3. Verify Trino query
trino-cli --file tests/integration/queries/test_new_field.sql

# 4. Check Feast offline store
python -c "
from feast import FeatureStore
store = FeatureStore('storage/feature_store')
df = store.get_historical_features(
    entity_df=pd.read_csv(...),
    features=['enrichment:new_field_name']
)
print(df)
"
```

---

## Running the Evolution Script

### Command

```bash
python scripts/evolve_iceberg_schema.py \
  --avsc <path-to-avsc-file> \
  --output <path-to-output-sql>
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--avsc` | Path to Avro schema `.avsc` file (e.g., `pipelines/processing/schemas/enriched-txn-v1.avsc`) |
| `--output` | Path to output SQL file (e.g., `storage/lake/schemas/enriched_transactions.sql`) |

### Environment Variables

**None required** — the script is stateless. It reads Avro, parses types, and generates SQL.

### What It Does

1. **Parses Avro schema** — extracts field names, types, nullability
2. **Maps types** — Avro `["null", "string"]` → Iceberg `STRING`
3. **Detects existing fields** — reads current `.sql` file if present
4. **Generates DDL** — `CREATE TABLE` for first run, `ALTER TABLE ADD COLUMN` for subsequent evolutions
5. **Idempotent** — won't re-add fields already in the schema

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success — schema aligned or DDL generated |
| `1` | Error — missing Avro file, invalid JSON, file write failure |

---

## CI Gate: Schema Evolution Workflow

**Triggered**: On every push/PR that modifies `**.avsc` files  
**Location**: `.github/workflows/schema-evolution.yml`  
**SLA**: Must pass before merge (blocking check)

### Steps

1. **Checkout** — clone repo
2. **Setup Python 3.11**
3. **Install dependencies** — `pyiceberg[pyarrow]`, `fastavro`
4. **Validate Iceberg alignment** — run `evolve_iceberg_schema.py`
   - Detects missing/misaligned fields
   - Fails if Avro and Iceberg diverge
5. **Validate Feast alignment** — run `validate_feast_schemas.py`
   - Ensures feature views match Avro schema

**What blocks merge**:
- Avro schema missing or invalid JSON
- Type mismatch between Avro and Iceberg (e.g., Avro says `int`, DDL has `BIGINT`)
- New field in Avro without corresponding Iceberg column
- Feature view missing from Feast repository

---

## Forbidden Changes (Schema Breaking)

**DO NOT**:

| Change | Why | Impact |
|--------|-----|--------|
| Remove a field | Consumers reading old records will get `NOT NULL` violation | Kafka consumers crash; Trino queries fail |
| Change a field type | `int` → `string` breaks binary deserialization | Silent data corruption or deserialization failure |
| Remove `"default": null` | Consumers reading records from old producers will fail | New code breaks on old Kafka messages |
| Change union order (e.g., `["string", "null"]` → `["null", "string"]`) | Breaks Avro binary compatibility | Deserialization fails silently |
| Make nullable field non-null | Existing null values in Kafka will violate constraint | Kafka → Iceberg write fails; pipeline halts |

**Instead**:
- Add a **new field** with `"default": null` (backward compatible)
- Migrate data by adding a **new table** in Iceberg (schema v2) and promoting it after a grace period
- Document deprecation in field docstring; consumers can ignore old fields

---

## Retention Policy: PCI-DSS Compliance

### Minimum Retention: 7 Years

Both `enriched_transactions` and `fraud_decisions` tables:

- **Creation**: Automatic on first write
- **Minimum lifetime**: 7 years from event date (PCI-DSS 10.7)
- **Purge window**: After 7 years, partitions are eligible for deletion
- **Partition strategy**: Daily partitions by `days(event_time)` enable efficient purge

### Partition Pruning After Retention Window

**Before querying**:

```sql
-- BAD: Full table scan (includes 7-year-old data)
SELECT COUNT(*) FROM enriched_transactions;

-- GOOD: Prune partitions outside retention window
SELECT COUNT(*) FROM enriched_transactions
WHERE event_time >= CURRENT_DATE - INTERVAL '7 year';
```

**Automated cleanup** (scheduled, not yet implemented):

```bash
# Daily cron job: remove partitions older than 7 years
trino --execute "
ALTER TABLE iceberg.default.enriched_transactions DROP PARTITION
WHERE event_time < DATE(CURRENT_TIMESTAMP - INTERVAL '7' YEAR);
"
```

### Storage Estimate

- **Transaction volume**: ~10k/s peak = 864M/day = ~315B/year
- **7-year retention**: ~2.2T raw data
- **Compression (Snappy)**: ~350–400G compressed
- **Replication (3× on MinIO)**: ~1.1T managed storage

---

## Troubleshooting

### "Schema file not found" error

```
Error: Schema file not found: pipelines/processing/schemas/enriched-txn-v1.avsc
```

**Fix**: Verify the `.avsc` file exists and path is absolute:

```bash
ls -la pipelines/processing/schemas/enriched-txn-v1.avsc
python scripts/evolve_iceberg_schema.py \
  --avsc $(pwd)/pipelines/processing/schemas/enriched-txn-v1.avsc \
  --output $(pwd)/storage/lake/schemas/enriched_transactions.sql
```

### "JSON decode error" during validation

```
Error parsing schema JSON: Expecting value: line 1 column 1
```

**Fix**: Validate Avro JSON syntax:

```bash
python -m json.tool pipelines/processing/schemas/enriched-txn-v1.avsc
```

If invalid, fix the syntax (missing comma, unmatched quote) before pushing.

### CI gate fails: "Type mismatch between Avro and Iceberg"

**Cause**: Avro schema was updated but DDL wasn't regenerated.

**Fix**:

```bash
# Run evolution script locally
python scripts/evolve_iceberg_schema.py \
  --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
  --output storage/lake/schemas/enriched_transactions.sql

# Commit the updated DDL
git add storage/lake/schemas/enriched_transactions.sql
git commit -m "chore(schema): evolve enriched_transactions for new field"
```

### Trino query returns "Column not found"

```
Query failed: COLUMN_NOT_FOUND: line 1:1: SELECT * ...
Unknown column 'new_field_name'
```

**Cause**: Iceberg table exists in catalog but hasn't refreshed metadata. MinIO/Trino are eventually consistent.

**Fix**:

```sql
-- Refresh Iceberg metadata
CALL iceberg.system.refresh_table('iceberg.default.enriched_transactions');

-- Re-run query
SELECT new_field_name FROM enriched_transactions LIMIT 1;
```

---

## References

- **Avro Spec**: https://avro.apache.org/docs/current/specification/ (backward compatibility)
- **Iceberg DDL**: https://iceberg.apache.org/docs/latest/sql-create-table/ (CREATE TABLE, ALTER TABLE)
- **Feast Feature Views**: `storage/feature_store/` (feature definitions and materialization)
- **Spec Reference**: `specs/006-analytics-persistence-layer/spec.md` (FR-001 through FR-026)
