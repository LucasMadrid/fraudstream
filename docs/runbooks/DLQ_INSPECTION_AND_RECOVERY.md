# Runbook: DLQ Inspection and Recovery (OE008)

**Applies to**: Fraud alerts that fail to be written to PostgreSQL, lose messages, or encounter schema validation errors in the fraud detection pipeline.  
**Triggers**: Prometheus alert `FraudAlertsDLQDepthHigh` fires (lag > 100 messages), manual investigation of data loss, replay after schema migration.

---

## What is the DLQ?

The Dead Letter Queue (`txn.fraud.alerts.dlq`) is a Kafka topic that captures messages that cannot be processed by the fraud detection pipeline. Messages land in the DLQ when:

- **Schema validation failure**: Incoming message does not conform to the Avro schema registered with Schema Registry.
- **Kafka producer error**: Attempt to write to `txn.fraud.alerts` topic fails (broker down, quota exceeded, auth failed).
- **PostgreSQL write failure**: Insertion into `fraud_alerts` table fails (constraint violation, connection timeout, row too large).
- **Serialization error**: Message cannot be deserialized by the Flink consumer.

Each DLQ message retains the original payload and can be replayed after root cause is fixed.

---

## Symptoms

### Alert fires: `FraudAlertsDLQDepthHigh`

**Threshold**: Consumer group lag for `txn.fraud.alerts.dlq` consumer > 100 messages  
**Check**: Navigate to Grafana dashboard "Fraud Detection — Main" and view the "DLQ Depth" panel.

```bash
# Manual check: verify lag from command line
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-alerts-dlq-consumer
```

Expected output: `LAG` column should be near 0. If LAG > 100, investigate immediately.

### No new rows in `fraud_alerts` table despite inbound volume

```sql
-- Check if fresh alerts are being written
SELECT COUNT(*) as row_count, MAX(created_at) as latest_alert
FROM fraud_alerts
WHERE created_at > NOW() - INTERVAL '5 minutes';
-- If row_count is 0 or very low, check DLQ depth
```

### Flink task manager logs show serialization errors

Check Flink Task Manager logs for patterns:
```
ERROR org.apache.flink.streaming.runtime.operators.sink.SinkWriter
Failed to write message to txn.fraud.alerts: org.apache.kafka.common.errors.SerializationException
```

---

## Step-by-step investigation

### 1. Check DLQ lag with kafka-consumer-groups

```bash
# List all consumer groups
kafka-consumer-groups --bootstrap-server localhost:9092 --list | grep dlq

# Describe the fraud alerts DLQ consumer group
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-alerts-dlq-consumer
```

Expected output:
```
GROUP                    TOPIC                    PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG
fraud-alerts-dlq-consumer txn.fraud.alerts.dlq   0          45             147             102
```

If `LAG > 100`, proceed to step 2.

### 2. Read a sample DLQ message

```bash
# Read the latest message (no offset reset, read 1 message with timeout)
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts.dlq \
  --max-messages 1 \
  --timeout-ms 5000

# Or, if you need to reset to beginning (warning: can be slow on large DLQs)
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts.dlq \
  --from-beginning \
  --max-messages 1 \
  --timeout-ms 5000
```

The message is printed to stdout. If it's valid JSON, extract and inspect:
```bash
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts.dlq \
  --max-messages 1 \
  --timeout-ms 5000 | jq .
```

### 3. Determine the root cause

#### Scenario A: Schema mismatch

If the message JSON has extra/missing/wrong-type fields:

```bash
# Check current schema version in Schema Registry
curl -s http://localhost:8081/subjects/txn.fraud.alerts-value/versions/latest | jq .schema | jq -r . | jq .

# Compare with the message in the DLQ
# If the message has a field that doesn't exist in the schema, a producer schema mismatch likely occurred
```

**Action**: See `docs/runbooks/SCHEMA_MIGRATION.md` for recovery steps.

#### Scenario B: PostgreSQL write failure

Check PostgreSQL logs and recent connection errors:

```bash
# Query Postgres for any constraint violations or recent errors
psql -h localhost -U fraud_detection -d fraud_db \
  -c "SELECT constraint_name FROM information_schema.table_constraints WHERE table_name='fraud_alerts' AND constraint_type='UNIQUE';"

# Check disk space and table bloat
SELECT table_name, pg_size_pretty(pg_total_relation_size(table_name::regclass)) as size
FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name = 'fraud_alerts'
ORDER BY pg_total_relation_size(table_name::regclass) DESC;
```

**Action**: Fix the constraint violation (e.g., duplicate alert key), increase disk if needed, then replay.

#### Scenario C: Kafka producer quota or auth error

Check Flink Task Manager logs for:
```
org.apache.kafka.common.errors.QuotaExceededException
org.apache.kafka.common.errors.AuthenticationException
```

**Action**: Verify broker connectivity and credentials in Flink job configuration. Restart Flink job with corrected credentials.

---

## Replay procedure

### Dry-run: Inspect what will be replayed

```bash
# Read 5 messages from DLQ without replaying
scripts/replay-dlq-message.sh --dry-run --max-messages 5
```

Output:
```
[2025-03-15T10:45:23Z] Consuming 5 message(s) from txn.fraud.alerts.dlq on localhost:9092
[DRY RUN] Would replay to: txn.fraud.alerts
[DRY RUN] Message: {"txn_id":"123","risk_score":0.92,"rule_name":"high_amount","timestamp":"2025-03-15T10:45:00Z"}
[DRY RUN] Message: {"txn_id":"124","risk_score":0.88,"rule_name":"velocity_spike","timestamp":"2025-03-15T10:45:01Z"}
...
```

Review the messages. If they look correct, proceed to live replay. If they look corrupted, do NOT replay — investigate further (see step 3 above).

### Live replay: Single message

```bash
# Replay 1 message to the main topic
scripts/replay-dlq-message.sh --max-messages 1 --target-topic txn.fraud.alerts

# Output:
# [2025-03-15T10:46:00Z] Consuming 1 message(s) from txn.fraud.alerts.dlq on localhost:9092
# Replaying 1 message(s) to txn.fraud.alerts
# [2025-03-15T10:46:01Z] Replay complete.
```

### Live replay: Batch

```bash
# Replay 50 messages
scripts/replay-dlq-message.sh --max-messages 50 --target-topic txn.fraud.alerts
```

### Live replay: Custom broker

```bash
# If using a non-local Kafka cluster
scripts/replay-dlq-message.sh \
  --broker kafka-broker-1:9092,kafka-broker-2:9092,kafka-broker-3:9092 \
  --dlq-topic txn.fraud.alerts.dlq \
  --target-topic txn.fraud.alerts \
  --max-messages 10
```

---

## Recovery validation

### 1. Monitor DLQ lag drop

After replay, the DLQ lag should decrease as the consumer processes the replayed messages:

```bash
# Run every 10 seconds to watch lag decrease
watch -n 10 'kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group fraud-alerts-dlq-consumer | grep txn.fraud.alerts.dlq'
```

Expected: `LAG` decreases from 100 → 50 → 0 over 1-2 minutes (depending on volume).

### 2. Check fraud_alerts table for new rows

```sql
-- Query for alerts created in the last 5 minutes
SELECT txn_id, risk_score, rule_name, created_at
FROM fraud_alerts
WHERE created_at > NOW() - INTERVAL '5 minutes'
ORDER BY created_at DESC
LIMIT 10;
```

You should see the replayed messages as new rows.

### 3. Verify Prometheus metrics normalize

In Grafana "Fraud Detection — Main" dashboard:
- **DLQ Depth** panel should return to 0 or near-zero.
- **Fraud Alerts Rate** should show consistent inbound throughput (not spiking).
- **Rule Evaluation Latency (p99)** should remain < 50ms.

### 4. Verify no new DLQ entries

```bash
# Monitor for 30 seconds; should see no new messages
timeout 30 kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts.dlq \
  --timeout-ms 5000 || echo "No new messages — recovery successful."
```

---

## When to escalate

### Escalate to SRE if:

1. **DLQ depth > 1000 messages** and replay does not reduce it (indicates systematic failure).
2. **Replay fails repeatedly** with "producer error" — indicates downstream system issue (Kafka or PostgreSQL is down, not message content).
3. **Root cause unclear after investigation** — DLQ messages are valid, but replay still fails.
4. **DLQ lag growing faster than your replay rate** — new failures are arriving faster than you can replay; indicates active outage, not backlog cleanup.

### Escalation steps:

1. Page on-call SRE (Slack: `#fraud-detection-incidents`).
2. Provide:
   - Screenshot of `kafka-consumer-groups describe` output.
   - Sample DLQ message (from step 2 above).
   - Root cause hypothesis (schema / DB / Kafka).
   - Time DLQ depth became > 100.
3. SRE will investigate infrastructure and determine if manual intervention (REINDEX Postgres, restart Kafka broker, roll back schema change) is needed.

---

## Related

- `docs/runbooks/SCHEMA_MIGRATION.md` — if root cause is schema incompatibility.
- `docs/runbooks/CHECKPOINT_RECOVERY.md` — if Flink job needs restart.
- `docs/tech-debt.md` — known DLQ limitations and planned mitigations.
- `scripts/replay-dlq-message.sh` — the replay script itself.
