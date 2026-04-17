# Runbook: Observability — Monitoring and Alerting (OE012)

**Applies to**: Monitoring the fraud detection pipeline using Grafana, Prometheus, and OpenTelemetry.  
**Triggers**: Incident response, performance investigation, capacity planning, alert triage.

---

## Quick reference

| Component | URL | User | Password |
|-----------|-----|------|----------|
| Grafana | http://localhost:3000 | admin | (see K8s secret `grafana-admin`) |
| Prometheus | http://localhost:9090 | N/A (no auth) | N/A |
| OpenTelemetry Collector | http://localhost:4317 (gRPC) | N/A | N/A |
| Flink WebUI | http://localhost:8081 | N/A | N/A |
| Kafka Manager | http://localhost:8888 | N/A | N/A |

---

## Grafana dashboards

### Main dashboard: "Fraud Detection — Main"

**URL**: http://localhost:3000/d/fraud-detection-main

**Panels and their meaning**:

#### 1. Rule Evaluation Latency (p50, p99)

```
panel: rule_evaluation_duration_seconds
query: histogram_quantile(0.99, rate(rule_evaluation_duration_seconds_bucket[5m]))
expected: < 50ms (p99)
```

**What it measures**: Time to evaluate a single fraud rule (aggregations, joins, condition checks).

**Healthy behavior**:
- p50 ≈ 5–10ms
- p99 ≈ 20–50ms
- No spikes above 100ms

**Alert**: `RuleEvaluationLatencyHigh` fires if p99 > 100ms.

**Troubleshooting high latency**:
```bash
# 1. Check if it's a specific rule causing slowness
curl -s http://localhost:8081/jobs/<job-id>/metrics | jq '.[] | select(.id | contains("rule")) | {id, value}'

# 2. Check Redis connection latency (rules use Redis for state)
redis-cli PING
redis-cli --latency

# 3. Check Flink Task Manager CPU/memory
kubectl top pod <taskmanager-pod>

# 4. Check if any processing backlog (consumer lag)
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group flink-fraud-processor
```

#### 2. ML Circuit Breaker State

```
panel: ml_circuit_breaker_state
values: 0 = CLOSED (ML enabled), 1 = OPEN (ML disabled), 2 = HALF_OPEN (testing)
expected: 0 (CLOSED)
```

**What it measures**: Health of the ML model serving endpoint. Circuit breaker opens if:
- ML endpoint is unreachable (timeout, 500 error).
- Error rate from ML exceeds threshold (>5% of requests).
- Latency exceeds threshold (>5s per request).

**Healthy behavior**: Always 0 (CLOSED).

**If stuck at 1 (OPEN)**:
```bash
# Check ML serving endpoint
curl -s -I http://<ml-serving-host>:8000/healthz

# Should return 200 OK. If not:
# 1. Check if service is running
kubectl get pod -l app=ml-serving

# 2. Check logs for errors
kubectl logs deployment/ml-serving | tail -50

# 3. Verify network connectivity from Flink to ML service
kubectl exec -it <flink-pod> -- bash -c "curl -I http://ml-serving:8000/healthz"

# 4. If ML service is down, circuit breaker will remain HALF_OPEN until it recovers (5 min recovery window)
```

#### 3. DLQ Depth

```
panel: kafka_consumer_group_lag{topic="txn.fraud.alerts.dlq"}
expected: 0 or very low (< 10)
```

**What it measures**: Number of messages stuck in the Dead Letter Queue (failed writes, schema errors).

**Alert**: `FraudAlertsDLQDepthHigh` fires if lag > 100.

**If spike observed**:
```bash
# 1. Immediately check the runbook
# See: docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md

# 2. Quick diagnosis
kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic txn.fraud.alerts.dlq \
  --max-messages 1 | jq '.'

# 3. If it's a schema error, see: SCHEMA_MIGRATION.md
# If it's a database error, see: DATABASE troubleshooting section below
```

#### 4. Fraud Alerts Rate

```
panel: rate(fraud_alerts_total[5m])
expected: Matches transaction volume (should be smooth, no sudden drops)
```

**What it measures**: Throughput of fraud alerts written to PostgreSQL.

**Healthy behavior**:
- Smooth curve matching incoming transaction volume.
- No sudden drops to 0 (indicates job crashed or is stalled).
- No spikes > 2x baseline (indicates stalled processing or late events arriving).

**If drops to 0**:
```bash
# Check if job is still running
curl -s http://localhost:8081/jobs | jq '.jobs[] | {name, status}'

# If FAILED, see: CHECKPOINT_RECOVERY.md
# If RUNNING, check consumer lag:
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group flink-fraud-processor | grep LAG
```

#### 5. Late Event Rate

```
panel: rate(late_events_total[5m])
expected: 0 or very low (< 0.1 per second)
```

**What it measures**: Events arriving with timestamps significantly in the past (outside the event time window).

**Healthy behavior**: Near 0.

**If high**: Messages are arriving late, which may indicate:
- Producer clock skew (producer system time is wrong).
- Kafka partition rebalancing (consumer lag causes replay of old messages).
- Upstream system queuing messages before sending.

**Mitigation**:
```bash
# 1. Check Kafka partition distribution
kafka-topics --bootstrap-server localhost:9092 \
  --describe --topic txn.transactions | head -20

# 2. Check producer lag
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --list | grep -E "producer|ingest"

# 3. Verify system clocks on all machines
# If producer clock is skewed, sync it:
ntpdate -s time.nist.gov  # Linux
sudo sntp -sS time.apple.com  # macOS
```

#### 6. Rule Shadow Triggers

```
panel: rate(rule_shadow_triggers_total{mode="shadow"}[5m])
expected: < 1 per second (low volume; these are experimental rules)
```

**What it measures**: How often shadow (experimental) fraud rules would have triggered if they were enabled.

**Use case**: Validating new rules without impacting production alerts.

**Healthy behavior**: Varies based on rule experimentation. Low values are normal.

---

## Key metrics to monitor (by category)

### Throughput metrics

```
# Transaction ingestion rate
rate(txn_received_total[5m])

# Fraud detection rate
rate(fraud_alerts_total[5m])

# Rule evaluation rate
rate(rule_evaluations_total[5m])
```

**What to watch for**:
- Sudden drop to 0 → job crash or Kafka down.
- Spike above 2x baseline → late events or reprocessing.
- Rule eval rate != Fraud alert rate → rule filtering is working.

### Latency metrics

```
# Rule evaluation latency (percentiles)
histogram_quantile(0.50, rate(rule_evaluation_duration_seconds_bucket[5m]))   # p50
histogram_quantile(0.99, rate(rule_evaluation_duration_seconds_bucket[5m]))   # p99
histogram_quantile(0.999, rate(rule_evaluation_duration_seconds_bucket[5m]))  # p99.9
```

**Expected**:
- p50: 5–10ms
- p99: 20–50ms
- p99.9: 50–100ms

**Red flags**:
- p99 > 100ms → Resource contention (check CPU, Redis, DB connection pool).
- p99.9 > 500ms → GC pauses or task manager memory pressure.

### Error metrics

```
# DLQ message rate
rate(kafka_consumer_group_lag{topic="txn.fraud.alerts.dlq"}[5m])

# Rule evaluation errors
rate(rule_evaluation_errors_total[5m])

# ML circuit breaker state changes
rate(ml_circuit_breaker_state_transitions[5m])

# Database write errors
rate(postgres_write_errors_total[5m])
```

**Healthy baseline**: All near 0.

### Resource metrics

```
# Flink Task Manager memory usage
flink_taskmanager_memory_heap_used_bytes / flink_taskmanager_memory_heap_max_bytes

# Redis connection pool utilization
redis_connected_clients / redis_maxclients

# Kafka producer buffer saturation
kafka_producer_buffer_available_bytes
```

**Healthy**: < 80% utilization. > 90% = approaching limits.

---

## Prometheus alerts and runbooks

### Alert: `FraudAlertsDLQDepthHigh`

**Condition**: `kafka_consumer_group_lag{topic="txn.fraud.alerts.dlq"} > 100`

**Severity**: Warning (Yellow)

**First steps**:
1. Open Grafana "Fraud Detection — Main" → "DLQ Depth" panel.
2. Sample 1 message from DLQ: `kafka-console-consumer ... --max-messages 1`
3. Check if it's a schema error, database constraint, or transient failure.
4. See full runbook: `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md`

---

### Alert: `FraudFlagRateZero`

**Condition**: `rate(fraud_alerts_total[5m]) == 0 for 5 minutes`

**Severity**: Critical (Red)

**First steps**:
1. Verify Flink job is `RUNNING`: `curl http://localhost:8081/jobs | jq '.jobs[] | {name, status}'`
2. Check Kafka consumer lag: `kafka-consumer-groups ... --group flink-fraud-processor`
3. If job is `FAILED`, see: `docs/runbooks/CHECKPOINT_RECOVERY.md`
4. If job is `RUNNING` but lag is high, check Flink Task Manager logs for errors.

---

### Alert: `MLCircuitBreakerOpen`

**Condition**: `ml_circuit_breaker_state == 1 for 2 minutes`

**Severity**: Warning (Yellow)

**First steps**:
1. Check ML service health: `curl http://<ml-serving-host>:8000/healthz`
2. Check ML service logs: `kubectl logs deployment/ml-serving | tail -100`
3. Verify network from Flink to ML service: `kubectl exec <flink-pod> -- curl http://ml-serving:8000/healthz`
4. If ML service is down, alert will clear once it recovers and circuit breaker enters HALF_OPEN (5 min recovery window).
5. Check Flink logs for actual ML errors: `tail /var/log/flink/taskmanager.log | grep -i "ml\|circuit"`

---

### Alert: `RuleFPRateHigh`

**Condition**: `rule_false_positive_rate{rule_name=...} > 0.10 for 10 minutes`

**Severity**: Info (Blue) — Informational, not actionable immediately

**First steps**:
1. Open Grafana "Rule Performance — Advanced" dashboard.
2. Filter by the rule name in alert.
3. Check if FP rate spike correlates with:
   - Schema changes (new field added).
   - ML model update (scores shifted).
   - Transaction pattern change (seasonal spike).
4. If FP rate > 0.20 (20%), consider:
   - Tuning rule threshold.
   - Disabling shadow rule if it's still experimental.
   - Rolling back recent code changes.

---

### Alert: `RuleEvaluationLatencyHigh`

**Condition**: `histogram_quantile(0.99, rate(rule_evaluation_duration_seconds_bucket[5m])) > 100ms for 10 minutes`

**Severity**: Warning (Yellow)

**First steps**:
1. Check Flink Task Manager resource usage: `kubectl top pod <taskmanager-pod>`
2. Check Redis latency: `redis-cli --latency`
3. Check Kafka producer latency: Monitor "Producer Latency" panel in Grafana.
4. If CPU > 80%, consider scaling up Task Manager (increase parallelism).
5. If memory > 80%, check for memory leaks in custom operators.

---

## OpenTelemetry traces

### What are OTel traces?

OTel traces capture the full execution path of a single transaction through the pipeline:

```
Transaction arrives
  ├─ fraud.txn_received (span: deserialize)
  ├─ fraud.rules_evaluation (span: per-rule evaluation)
  │   ├─ fraud.rule_velocity_spike (10ms)
  │   ├─ fraud.rule_high_amount (5ms)
  │   └─ fraud.rule_low_country (8ms)
  ├─ fraud.ml_scoring (span: ML model inference)
  └─ fraud.alert_written (span: PostgreSQL insert)
```

### Trace configuration

- **Sample rate**: 10% (1 in 10 transactions are traced, to reduce overhead).
- **Export destination**: Console logs (v1 limitation; future: Jaeger / Tempo).
- **Trace format**: JSON in Flink Task Manager logs.

### How to find traces

All OTel traces are logged to Flink Task Manager logs. Search for the transaction ID:

```bash
# Search logs for a specific transaction
txn_id="abc123"
grep -r "$txn_id" /var/log/flink/taskmanager*.log | jq '.'

# Or, stream logs and wait for a trace
tail -f /var/log/flink/taskmanager.log | grep "fraud\."
```

Example OTel log (pretty-printed):

```json
{
  "timestamp": "2025-03-15T10:45:23.123Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "parent_span_id": "00f067aa0ba902b6",
  "operation": "fraud.rule_evaluation",
  "rule_name": "velocity_spike",
  "duration_ms": 12.5,
  "attributes": {
    "txn_id": "abc123",
    "user_id": "user456",
    "rule_triggered": true
  }
}
```

### Analyzing performance from traces

**Find slow transactions**:
```bash
# Extract all trace_ids and their total duration
grep "fraud\." /var/log/flink/taskmanager.log \
  | jq 'select(.duration_ms > 100)' \
  | jq '.trace_id, .duration_ms' | head -20
```

**Find failed transactions**:
```bash
# Look for error spans or exceptions
grep "fraud\." /var/log/flink/taskmanager.log \
  | jq 'select(.status == "ERROR" or .attributes.exception != null)'
```

**Profile rule latencies**:
```bash
# Average latency per rule
grep "fraud.rule_evaluation" /var/log/flink/taskmanager.log \
  | jq -s 'group_by(.attributes.rule_name) | .[] | {rule: .[0].attributes.rule_name, avg_duration: (map(.duration_ms) | add / length)}'
```

---

## Common issues and diagnostics

### Issue: Grafana shows no data for a panel

**Symptom**: Empty graph or "No data" message.

**Diagnostics**:
```bash
# 1. Check Prometheus scrape config
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job, lastScrapeTime}'

# Expected: Multiple targets with recent lastScrapeTime (within last 1 min)

# 2. Check if metrics exist in Prometheus
curl -s 'http://localhost:9090/api/v1/query?query=rule_evaluation_duration_seconds' | jq '.data.result'

# Should return a non-empty result. If empty, the metric isn't being emitted.

# 3. Check Flink metrics export
# Flink exports metrics via Prometheus reporter
# Verify in Flink config: conf/flink-conf.yaml
#   metrics.reporter.prometheus.class: org.apache.flink.metrics.prometheus.PrometheusReporter
#   metrics.reporter.prometheus.port: 9249

# 4. Check firewall / network access
curl -s http://localhost:9249/metrics | head -20
# Should return Prometheus metrics. If connection refused, Flink metrics aren't exposed.
```

**Resolution**:
1. Verify Flink metrics are being exported (check above).
2. Restart Prometheus: `systemctl restart prometheus` or `kubectl rollout restart deployment/prometheus`
3. Wait 2-3 minutes for metrics to populate (Prometheus scrape interval is typically 30s).

### Issue: Circuit breaker stays OPEN

**Symptom**: `ml_circuit_breaker_state == 1` continuously.

**Diagnostics**:
```bash
# 1. Check ML serving endpoint availability
curl -v http://<ml-serving-host>:8000/healthz

# Expected: 200 OK

# 2. Check if endpoint URL is correct in Flink config
grep -r "ml.serving.url" /path/to/flink/config

# 3. Check ML service logs
kubectl logs deployment/ml-serving | tail -100 | grep -i "error\|exception"

# 4. Check network from Flink pod to ML service
kubectl exec -it <flink-pod> -- bash -c "nc -zv ml-serving 8000"
# Should return "succeeded"

# 5. Check Flink logs for actual error from ML service
tail -100 /var/log/flink/taskmanager.log | grep -A5 "ml.serving\|circuit breaker"
```

**Resolution**:
1. Fix ML service or connectivity issue.
2. Circuit breaker will automatically transition to HALF_OPEN after 5 minutes.
3. Once ML service returns 200, circuit breaker will close within 1-2 minutes.
4. If still stuck, manually restart Flink job: `flink cancel <job-id> && flink run ...`

### Issue: High rule evaluation latency (p99 > 100ms)

**Symptom**: `rule_evaluation_duration_seconds` p99 > 100ms.

**Diagnostics**:
```bash
# 1. Check Task Manager resource usage
kubectl top pod <taskmanager-pod>
# If CPU > 80% or memory > 80%, resource contention is likely

# 2. Check Redis latency (rules use Redis for state)
redis-cli --latency
# Expected: < 1ms per operation

# 3. Check Kafka producer latency
# Monitor "Producer Latency" panel in Grafana
# Or query: histogram_quantile(0.99, rate(kafka_producer_latency_seconds_bucket[5m]))

# 4. Check if there's a specific slow rule
curl -s http://localhost:8081/jobs/<job-id>/metrics | jq '.[] | select(.id | contains("rule")) | {id, value}'

# 5. Check database connection pool saturation
# Query Prometheus: pg_stat_activity (if pg_exporter is available)
# Or check Flink logs for "connection pool full" messages
```

**Resolution** (in order of likelihood):
1. **Redis latency high**: Check Redis CPU/memory, flush unnecessary keys, upgrade Redis instance.
2. **Kafka producer latency high**: Check broker CPU, network, partition rebalancing.
3. **Task Manager resource constrained**: Scale up by increasing parallelism or pod memory limit.
4. **Specific slow rule**: Optimize rule logic (reduce state lookups, batch operations).

---

## Performance SLOs and error budgets

| Metric | SLO | Error Budget |
|--------|-----|--------------|
| Rule Evaluation Latency (p99) | < 50ms | 10% of requests can exceed |
| Fraud Alert Availability | 99.9% (8.7 hours downtime/month) | 43 minutes/month |
| DLQ Depth | 0 for 99% of time | 7.2 hours/month allowed lag |
| ML Circuit Breaker availability | 99% uptime | 7.2 hours/month allowed downtime |

---

## Setting up custom alerts

### Example: Alert on high fraud rate (potential attack)

```yaml
# prometheus-rules.yaml
groups:
  - name: fraud_detection
    interval: 30s
    rules:
      - alert: HighFraudRateDetected
        expr: rate(fraud_alerts_total[5m]) > 100  # >100 alerts/sec
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Fraud rate unusually high: {{ $value }} alerts/sec"
          description: "Investigate potential fraud wave or false positives"
```

---

## Related

- `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md` — for DLQ-related alerts.
- `docs/runbooks/CHECKPOINT_RECOVERY.md` — for job crash recovery.
- `docs/runbooks/SCHEMA_MIGRATION.md` — for schema-related errors.
- Prometheus docs: https://prometheus.io/docs/
- Grafana docs: https://grafana.com/docs/
- OpenTelemetry docs: https://opentelemetry.io/docs/
