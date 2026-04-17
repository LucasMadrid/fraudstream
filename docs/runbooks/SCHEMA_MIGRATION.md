# Runbook: Schema Migration (OE011)

**Applies to**: Avro schema changes for Kafka topics in the fraud detection pipeline (transactions, enrichments, fraud alerts).  
**Triggers**: Adding new fields to events, changing field types, updating validation rules.

---

## Overview

All Kafka topics in this system use **Avro** for serialization and **Confluent Schema Registry** for schema management. Every schema subject is configured with **BACKWARD_TRANSITIVE** compatibility mode, which enforces these rules:

- **New producers** can write messages conforming to a newer schema.
- **Old producers** can still write messages conforming to the old schema.
- **All consumers** can read both old and new schema versions (backward compatible).
- **No breaking changes**: You cannot remove fields, rename fields, or change field types without breaking consumers.

This runbook ensures migrations are safe and coordinated.

---

## Compatibility modes (for reference)

| Mode | Meaning | Producer | Consumer |
|------|---------|----------|----------|
| `NONE` | No checks | Any schema | Any schema |
| `BACKWARD` | New consumer, old data | Old producer | New consumer only |
| `FORWARD` | Old consumer, new data | New producer | Old consumer only |
| `FULL` | Both directions | Old/new producer | Old/new consumer |
| `TRANSITIVE` | Plus transitive checks | Old/new | Old/new (stricter) |
| `BACKWARD_TRANSITIVE` | (ours) | New producer | All consumers |

In our setup, `BACKWARD_TRANSITIVE` means: **new schemas must be readable by all old versions**, and **we deploy consumers before producers**.

---

## Pre-migration checklist

### 1. Verify current compatibility mode

```bash
# Check Schema Registry configuration for your subject
curl -s http://localhost:8081/config/txn.fraud.alerts-value | jq '.compatibilityLevel'

# Expected output: "BACKWARD_TRANSITIVE"

# If not, check subject-specific override:
curl -s http://localhost:8081/config/txn.fraud.alerts-value/config | jq '.compatibilityLevel'
```

If output is different, contact the schema owner. Do NOT proceed with migration if compatibility mode is `NONE`.

### 2. Review current schema

```bash
# Get the latest version
curl -s http://localhost:8081/subjects/txn.fraud.alerts-value/versions/latest | jq '.schema' | jq -r . | jq '.'

# Sample Avro schema (pretty-printed):
{
  "type": "record",
  "name": "FraudAlert",
  "namespace": "com.example.fraud",
  "fields": [
    {"name": "txn_id", "type": "string"},
    {"name": "risk_score", "type": "double"},
    {"name": "rule_name", "type": "string"},
    {"name": "timestamp", "type": "string"}
  ]
}
```

Store the current schema for comparison later.

### 3. Plan your changes

**Allowed changes** (backward-compatible):
- Add a new **optional** field (has `default` value).
- Add a new **required** field **only if you add a default value** (treated as optional).
- Change a field's doc string (no impact on serialization).
- Add enum values to an existing enum field.

**Breaking changes** (NOT allowed without topic rename):
- Remove a field.
- Rename a field.
- Change a field type (e.g., string → int).
- Reorder fields (affects binary compatibility).
- Make a field required (remove its default).
- Remove enum values.

### 4. Dry-run the schema change

```bash
# Schema Registry allows you to test compatibility without registering
# Create your new schema in a file: new-schema.json

# Test BACKWARD compatibility (new schema readable by old code)
curl -X POST "http://localhost:8081/compatibility/subjects/txn.fraud.alerts-value/versions/latest" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"schema": "'$(cat new-schema.json | jq -c . | sed 's/"/\\"/g')'"}'

# Response: {"is_compatible": true} or {"is_compatible": false, "messages": [...]}
```

If `is_compatible: false`, review the error messages and adjust your schema before proceeding.

---

## Migration steps

### Step 1: Update the Avro schema file

Edit your schema definition file (e.g., `schemas/fraud-alert-value.avsc`):

```json
{
  "type": "record",
  "name": "FraudAlert",
  "namespace": "com.example.fraud",
  "fields": [
    {"name": "txn_id", "type": "string"},
    {"name": "risk_score", "type": "double"},
    {"name": "rule_name", "type": "string"},
    {"name": "timestamp", "type": "string"},
    {"name": "ml_score", "type": ["null", "double"], "default": null}  // NEW: optional ML score
  ]
}
```

**Key points**:
- New field has `"default": null` (makes it optional).
- Use `["null", "double"]` (union) for nullable fields in Avro.
- Add a descriptive doc string if the field is complex.

### Step 2: Register new schema version

```bash
# Register the new schema
curl -X POST "http://localhost:8081/subjects/txn.fraud.alerts-value/versions" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"schema": "'$(cat schemas/fraud-alert-value.avsc | jq -c . | sed 's/"/\\"/g')'"}'

# Response:
# {"id": 42, "version": 3, "subject": "txn.fraud.alerts-value"}

# Note the version number (3 in this example)
```

### Step 3: Verify compatibility

```bash
# Re-run the compatibility check (should now pass automatically)
curl -s http://localhost:8081/subjects/txn.fraud.alerts-value/versions | jq '.[-2:] | .[] | {version, id}'

# Expected: Last two versions shown with their IDs
# Verify the new schema is listed

# Check specific version compatibility against all predecessors
curl -s http://localhost:8081/subjects/txn.fraud.alerts-value/versions/3 | jq '.schema' | head -5
```

### Step 4: Deploy consumers first

Consumers must be updated before producers, so they can read both old and new schema versions.

**For Flink jobs** (reads from Kafka):

```bash
# Option A: Rolling restart if schema registry is referenced dynamically
# (Flink automatically pulls latest schema on next read)
kubectl rollout restart deployment/flink-jobmanager
kubectl rollout restart deployment/flink-taskmanager

# Wait for rollout:
kubectl rollout status deployment/flink-jobmanager
kubectl rollout status deployment/flink-taskmanager

# Option B: If Flink schema is baked into the job JAR
# Rebuild the job jar with the new schema and redeploy
```

**For other consumers** (e.g., PostgreSQL loader, analytics jobs):

```bash
# Restart the consumer service
systemctl restart fraud-alerts-writer

# Or for containerized:
docker restart fraud-alerts-writer
```

**Verification**:
```bash
# Check consumer logs for any deserialization errors
tail -20 /var/log/fraud-alerts-writer.log | grep -i "schema\|error"

# No errors should appear
```

### Step 5: Deploy producers last

Producers should be updated after consumers, to ensure readers are ready for new fields.

**For Flink jobs** (writes to Kafka):

```bash
# Rebuild producer job jar with new schema
cd src && gradle build -x test

# Deploy new jar to Flink
flink run /path/to/new-fraud-detection-pipeline.jar --env production --parallelism 2
```

**For other producers** (e.g., transaction ingestors):

```bash
# Deploy new code that populates the new field
git pull && npm run build && npm restart fraud-transaction-producer
```

**Verification**:
```bash
# Check that new messages contain the new field
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts \
  --max-messages 5 | jq '.ml_score'

# Should show values like: null, 0.75, 0.82, etc.
```

### Step 6: Monitor and validate

```bash
# Check for any schema validation errors (should be zero)
curl -s http://localhost:8081/subjects/txn.fraud.alerts-value/versions | jq length
# (number of versions should increase by 1)

# Monitor for 5-10 minutes to ensure no DLQ messages from schema errors
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-alerts-dlq-consumer | grep "txn.fraud.alerts.dlq"

# Expected: LAG stays at 0 (no new DLQ messages)

# Sample new messages
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts \
  --max-messages 10 | jq '.' | head -30
```

---

## Rollback procedure

Since `BACKWARD_TRANSITIVE` is enforced, rolling back is safe:

### If new producer introduced bad data:

```bash
# Revert producer code to previous version
git revert <commit-hash>  # or git checkout <previous-tag>

# Rebuild and redeploy
flink run /path/to/old-fraud-detection-pipeline.jar --env production --parallelism 2

# Old producer writes using old schema version
# Consumers can still read both old and new data
```

### If consumers are failing on new schema:

```bash
# Revert consumer code
git revert <commit-hash>

# Redeploy
kubectl rollout restart deployment/fraud-alerts-writer
```

### If you need to drop the new schema version from Schema Registry:

```bash
# WARNING: Only do this if the new version was never used in production
curl -X DELETE "http://localhost:8081/subjects/txn.fraud.alerts-value/versions/3"
```

**Note**: You cannot delete a version that's currently in use. Shut down all producers/consumers using that version first.

---

## Danger zones

### Breaking change: Removing a field

**Scenario**: You want to remove `timestamp` field (it's redundant).

**Why it breaks**: Old producers will write messages with `timestamp`. New consumers with schema v2 (no `timestamp` field) cannot parse old messages.

**Solution**:
1. Create a **new topic** `txn.fraud.alerts-v2` with the updated schema.
2. Deploy new consumers to read from `txn.fraud.alerts-v2`.
3. Deploy new producers to write to `txn.fraud.alerts-v2`.
4. Migrate all downstream systems.
5. Only after all systems are migrated, stop writing to old topic `txn.fraud.alerts`.
6. Mark old topic as deprecated.

### Breaking change: Changing field type

**Scenario**: You want to change `risk_score` from `double` to `int` (to save space).

**Why it breaks**: Old producers write doubles. New consumers expecting ints cannot parse the data.

**Solution**: Same as removing a field — use a new topic and migrate all systems.

### Breaking change: Adding a required field without default

**Scenario**: You add `alert_id: string` without a default value.

**Why it breaks**: Old producers don't know about `alert_id` and won't write it. New consumers expect it to be present.

**Solution**: Always provide a default value for new fields:
```json
{"name": "alert_id", "type": "string", "default": ""}
```

---

## Common schema changes and how to implement them

### 1. Add an optional field

```json
// Before
{"name": "rule_confidence", "type": "double"}

// After (add new field)
{"name": "rule_confidence", "type": "double"},
{"name": "rule_version", "type": "string", "default": "1.0"}  // NEW
```

Producers can write the new field or leave it as the default.

### 2. Add a nullable field

```json
{"name": "ml_score", "type": ["null", "double"], "default": null}
```

Producers can set it to `null` or a `double` value. Consumers ignore it if null.

### 3. Rename a field (safe if both names coexist)

```json
// Before
{"name": "txn_amount", "type": "double"}

// After (add new field, keep old for compatibility)
{"name": "txn_amount", "type": "double"},
{"name": "transaction_amount", "type": "double", "default": 0.0}

// Producers write both (or just the new one with a mapper)
// Consumers read the new field, ignore the old
```

After 2-3 versions, you can safely remove the old field in a major version bump.

### 4. Add enum values

```json
// Before
{"name": "alert_level", "type": {"type": "enum", "name": "AlertLevel", "symbols": ["LOW", "HIGH"]}}

// After (add new symbol)
{"name": "alert_level", "type": {"type": "enum", "name": "AlertLevel", "symbols": ["LOW", "MEDIUM", "HIGH"]}}
```

Old consumers reading `MEDIUM` may fail unless they handle unknown symbols gracefully. This is only safe if you control all consumers.

---

## When to involve Schema Registry team

- Schema Registry is returning 500 errors or is down.
- A subject is locked or read-only.
- You need to change the global compatibility level (affects all subjects).
- You suspect a schema version is corrupted.
- You're unsure if a planned change is backward-compatible.

---

## Related

- `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md` — if migration causes schema validation errors and messages land in DLQ.
- `docs/runbooks/CHECKPOINT_RECOVERY.md` — if a schema change requires Flink state migration.
- `docs/tech-debt.md` — known schema management limitations.
- Confluent Schema Registry docs: https://docs.confluent.io/platform/current/schema-registry/avro.html
