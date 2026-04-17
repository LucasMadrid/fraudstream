# Runbook: Checkpoint Recovery (OE010)

**Applies to**: Apache Flink streaming jobs in the fraud detection pipeline.  
**Triggers**: Flink job crash, TaskManager OOM, state corruption detected, planned restart from known good state, cluster upgrade.

---

## Overview

Flink uses RocksDB-backed distributed state snapshots called **checkpoints** to guarantee exactly-once processing semantics. When the Flink job is running, it periodically writes checkpoints to persistent storage (MinIO/S3) at `s3a://flink-checkpoints/`. Each checkpoint includes:

- State of all operators (aggregators, joins, windows, custom state backends)
- Consumer group offsets (Kafka source position)
- Watermark progress
- Timer state (for timed side-outputs and expiration)

When the job crashes or is manually cancelled, you can restart it from the most recent valid checkpoint and resume processing from where it left off, avoiding data loss and duplicate processing.

---

## Symptoms

### Symptom 1: Flink job restarts unexpectedly

**Signs**:
- Job shows `RUNNING` → `FAILED` → `RUNNING` in Flink WebUI.
- TaskManager pod (Kubernetes) or container is killed.
- Check Flink logs:
```bash
tail -f /var/log/flink/taskmanager.log | grep -i "exception\|error"
```

**Common causes**:
- TaskManager runs out of memory (OOM killer).
- Checkpoint directory is inaccessible (S3 credentials expired, MinIO down).
- Deserialization error in state backend (corrupted checkpoint).

### Symptom 2: TaskManager OOM

```bash
# Check memory usage on TaskManager pod
kubectl logs <taskmanager-pod> | grep -i "outofmemory\|oom"

# Or on bare metal:
ps aux | grep flink | grep TaskManager
# Look at RSS column; if it's close to -Xmx setting, OOM is likely
```

### Symptom 3: State corruption detected

Flink logs show:
```
java.io.IOException: Failed to restore KeyedState from RocksDB checkpoint
org.apache.flink.runtime.state.CheckpointException: Could not restore state
```

This indicates the checkpoint at `s3a://flink-checkpoints/<checkpoint-id>` is corrupted. You may need to revert to an older checkpoint.

### Symptom 4: Consumer lag grows unbounded

After a restart:
```bash
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group flink-fraud-processor | head -5
```

If `LAG` is very high and not decreasing, the job may have restarted from an old checkpoint. Verify by checking job start time:

```bash
# Check job start time
curl -s http://localhost:8081/jobs/<job-id> | jq '.start-time'
```

Compare against your Prometheus alerting time or recent deployments.

---

## Step-by-step recovery

### Step 1: Check Flink job status

```bash
# Retrieve list of jobs
curl -s http://localhost:8081/jobs | jq '.jobs[] | {id, name, status}'

# Store the job ID for use in subsequent commands
JOB_ID="<job-id-from-above>"
```

Output should show:
```json
{
  "id": "abc123def456",
  "name": "fraud-detection-pipeline",
  "status": "FAILED"
}
```

If status is `RUNNING`, skip to Step 4.

### Step 2: List recent valid checkpoints

```bash
# Get checkpoint history for the failed job
curl -s http://localhost:8081/jobs/${JOB_ID}/checkpoints | jq '.counts'

# Sample output:
# {
#   "restored": 1,
#   "total": 145,
#   "failed": 2,
#   "in_progress": 0,
#   "completed": 143
# }

# Retrieve list of completed checkpoints (most recent first)
curl -s http://localhost:8081/jobs/${JOB_ID}/checkpoints | jq '.history[] | select(.status=="COMPLETED") | {id, trigger_timestamp, completion_timestamp}' | head -20
```

Expected output (truncated):
```json
{
  "id": 144,
  "trigger_timestamp": 1710520680000,
  "completion_timestamp": 1710520681500
}
{
  "id": 143,
  "trigger_timestamp": 1710520640000,
  "completion_timestamp": 1710520641200
}
```

Select the most recent `COMPLETED` checkpoint (highest ID with recent timestamp). Note its `id`.

### Step 3: Identify the checkpoint path in MinIO/S3

```bash
# List checkpoints in S3
aws s3 ls s3://flink-checkpoints/ --recursive | tail -20

# Or, if using MinIO (self-hosted):
mc ls minio/flink-checkpoints/ --recursive | tail -20

# The path format is typically:
# s3a://flink-checkpoints/{job-id}/{checkpoint-id}/
```

Verify the checkpoint exists and contains the required files (e.g., `_metadata`, `__tf_metadata`):

```bash
aws s3 ls s3://flink-checkpoints/${JOB_ID}/<checkpoint-id>/

# Should show:
# PRE __metadata/
# PRE __tf_metadata/
#    _metadata
```

### Step 4: Cancel the running job gracefully (if running)

If the job is still `RUNNING` and you need to restart from a checkpoint:

```bash
# Trigger a final checkpoint before cancellation (optional but recommended)
SAVEPOINT_DIR="s3a://flink-checkpoints/savepoints"

curl -X POST "http://localhost:8081/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d "{\"target-directory\": \"${SAVEPOINT_DIR}\", \"cancel-job\": true}" | jq '.'

# Monitor savepoint progress (poll every 5 seconds)
REQUEST_ID=$(curl -X POST "http://localhost:8081/jobs/${JOB_ID}/savepoints" \
  -H "Content-Type: application/json" \
  -d "{\"target-directory\": \"${SAVEPOINT_DIR}\", \"cancel-job\": false}" | jq -r '.request_id')

sleep 5
SAVEPOINT_STATUS=$(curl -s "http://localhost:8081/jobs/${JOB_ID}/savepoints/${REQUEST_ID}" | jq '.status')

while [[ "$SAVEPOINT_STATUS" != "COMPLETED" ]]; do
  echo "Savepoint status: $SAVEPOINT_STATUS ... waiting"
  sleep 5
  SAVEPOINT_STATUS=$(curl -s "http://localhost:8081/jobs/${JOB_ID}/savepoints/${REQUEST_ID}" | jq '.status')
done

# Extract savepoint path
SAVEPOINT_PATH=$(curl -s "http://localhost:8081/jobs/${JOB_ID}/savepoints/${REQUEST_ID}" | jq -r '.operation.location')
echo "Savepoint created at: $SAVEPOINT_PATH"
```

If the job is `FAILED` and already stopped, skip this step.

### Step 5: Restart the job from checkpoint

```bash
# For local testing or non-HA deployments, submit the job with checkpoint restore

# Option A: If using Flink CLI with jar uploaded to JobManager
CHECKPOINT_PATH="s3a://flink-checkpoints/${JOB_ID}/<checkpoint-id>"

flink run -s "${CHECKPOINT_PATH}" \
  --allowNonRestoredState \
  /path/to/fraud-detection-pipeline.jar \
  --env production \
  --parallelism 2

# Option B: If using Flink REST API (recommended for Kubernetes)
JAR_ID="<upload-jar-first-or-use-existing-jar-id>"
curl -X POST "http://localhost:8081/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"savepointPath\": \"${CHECKPOINT_PATH}\",
    \"allowNonRestoredState\": false,
    \"parallelism\": 2,
    \"programArgs\": \"--env production\"
  }" | jq '.'

# Option C: For Kubernetes deployments, update the deployment spec:
kubectl set env deployment/flink-jobmanager \
  FLINK_RESTORE_FROM_CHECKPOINT="${CHECKPOINT_PATH}"

kubectl rollout status deployment/flink-jobmanager
```

**Note**: `allowNonRestoredState=false` is strict and will fail if the checkpoint doesn't match the current job topology. Use `true` only if you're certain the topology hasn't changed or you're accepting potential data loss.

### Step 6: Verify the job recovered successfully

```bash
# 1. Check job status becomes RUNNING
curl -s http://localhost:8081/jobs/${JOB_ID} | jq '.status'

# Expected: "RUNNING"

# 2. Check Kafka consumer group lag (should be low and stable)
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group flink-fraud-processor

# Expected: LAG ≈ 0 (or < 100) and not growing

# 3. Verify new alerts are being written to PostgreSQL
psql -h localhost -U fraud_detection -d fraud_db \
  -c "SELECT COUNT(*) FROM fraud_alerts WHERE created_at > NOW() - INTERVAL '1 minute';"

# Expected: A few rows (depending on transaction volume)

# 4. Check Prometheus metrics in Grafana
# Navigate to "Fraud Detection — Main" dashboard
# Verify panels:
#   - Rule Evaluation Latency (p99) < 50ms
#   - ML Circuit Breaker State = 0 (CLOSED)
#   - Fraud Alerts Rate is smooth (not spiking)

# 5. Monitor logs for any warnings
tail -50 /var/log/flink/jobmanager.log | grep -i "warn\|error"
```

---

## Checkpoint recovery decision tree

```
Job failed or needs restart
  ├─ Is checkpoint corrupted?
  │   └─ Yes → Restore from older checkpoint (Step 3: pick older completed checkpoint)
  │   └─ No → Restore from most recent checkpoint (Step 3: use highest completed checkpoint ID)
  │
  ├─ How old is the checkpoint?
  │   └─ < 1 min old → Safe to restore, minimal lag catch-up
  │   └─ 5-60 min old → Safe, lag catch-up will take a few minutes
  │   └─ > 1 hour old AND high-volume traffic → Consider reprocessing from Kafka instead
  │       (reason: consumer lag may grow faster than catch-up rate)
  │
  ├─ Has the job topology changed? (operators added/removed/reordered)
  │   └─ No → Use allowNonRestoredState=false (strict)
  │   └─ Yes → Use allowNonRestoredState=true (permissive, may skip some state)
  │
  └─ Proceed with Step 5 (restart from checkpoint)
```

---

## Verification

### Full recovery validation checklist

```bash
#!/bin/bash
# Run this script to validate checkpoint recovery

JOB_ID="<your-job-id>"
BROKER="localhost:9092"
CONSUMER_GROUP="flink-fraud-processor"

echo "=== Job Status ==="
curl -s http://localhost:8081/jobs/${JOB_ID} | jq '.status'

echo "=== Kafka Consumer Lag ==="
kafka-consumer-groups --bootstrap-server ${BROKER} \
  --describe --group ${CONSUMER_GROUP} | tail -1

echo "=== Recent Alerts in DB ==="
psql -h localhost -U fraud_detection -d fraud_db \
  -c "SELECT COUNT(*) as alert_count FROM fraud_alerts WHERE created_at > NOW() - INTERVAL '5 minutes';"

echo "=== DLQ Depth ==="
kafka-consumer-groups --bootstrap-server ${BROKER} \
  --describe --group fraud-alerts-dlq-consumer | tail -1

echo "=== Flink Metrics (sample) ==="
curl -s http://localhost:8081/jobs/${JOB_ID} | jq '.tasks[0] | {name, status}'

echo "✓ Recovery validation complete"
```

All checks should show:
- Job status: `RUNNING`
- Kafka lag: < 100 messages and stable
- Alert count: growing (not stuck at 0)
- DLQ depth: 0 or very low
- Task status: `RUNNING`

---

## When NOT to restore from checkpoint

### Scenario: Checkpoint is older than 1 hour during high-volume processing

If you have 1000s of transactions/sec and the checkpoint is 1+ hours old, catch-up lag can exceed available buffer sizes and cause downstream failures. Instead:

1. **Reprocess from Kafka retention**:
```bash
# Reset consumer group to 1 hour ago
kafka-consumer-groups --bootstrap-server ${BROKER} \
  --group ${CONSUMER_GROUP} \
  --reset-offsets \
  --to-datetime "2025-03-15T09:00:00.000" \
  --execute

# Restart job WITHOUT checkpoint restore
flink run /path/to/fraud-detection-pipeline.jar --env production
```

2. **Accept reprocessing**: Duplicate alerts may be generated for the 1-hour window. Merge with existing PostgreSQL rows by `(txn_id, rule_name)` composite key.

### Scenario: Checkpoint contains incompatible state

If the checkpoint is from a version of the job that had different state schema:
```bash
# Use allowNonRestoredState=true to allow discarding incompatible state
curl -X POST "http://localhost:8081/jars/${JAR_ID}/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"savepointPath\": \"${CHECKPOINT_PATH}\",
    \"allowNonRestoredState\": true,
    \"parallelism\": 2
  }"
```

**Warning**: This loses state (e.g., aggregations, windows) from the old checkpoint. You may see a spike in late events or missed alerts during the recovery window.

---

## Related

- `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md` — if recovery causes DLQ messages to spike.
- `docs/runbooks/SCHEMA_MIGRATION.md` — if state schema is incompatible.
- `docs/runbooks/OBSERVABILITY_RUNBOOK.md` — monitoring checkpoints and job health.
- `specs/002-flink-stream-processor/design.md` — Flink architecture and checkpointing strategy.
