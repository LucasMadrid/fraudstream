# Fraud Detection Streaming Pipeline: Chaos Engineering Failure Scenarios

**Document Version**: 1.0  
**Last Updated**: 2026-04-13  
**Status**: Manual Testing (v1)

## Prerequisites

Before running any chaos engineering test, ensure the following are in place:

- **Docker Compose**: All services running (`docker-compose ps` shows no exited containers)
- **Prometheus**: Accessible at `http://localhost:9090` (metrics scraping enabled)
- **Grafana**: Accessible at `http://localhost:3000` (dashboards loaded)
- **Kafka CLI**: `kafka-console-consumer` and `kafka-topics` available in PATH
- **jq**: JSON query tool installed (`brew install jq` on macOS)
- **curl**: HTTP client for triggering API calls
- **Network connectivity**: All services on `fraudstream` network
- **Sufficient disk space**: MinIO storage has >1GB free for checkpoints

### Health Check

Run this command before starting any test:

```bash
docker-compose ps | grep -E "(flink|kafka|prometheus|grafana|minio)" | grep -c "Up"
# Should show 5+ services (exact count depends on your setup)

# Verify Prometheus is scraping
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets | length'
# Should show > 3
```

---

## Running Chaos Tests in CI

**Current Status (v1)**: These scenarios are **manual-only** in this version. No automated chaos injection framework is integrated.

**Future Roadmap (v2)**:
- Integrate Gremlin or Chaos Toolkit for automated injection
- Add synthetic traffic generator to CI environment
- Implement automated rollback on test failure
- Create failure scenario test suite as part of CD pipeline

**Manual Testing Procedure**:
1. Schedule chaos tests during designated maintenance window (off-peak hours)
2. Pre-stage the environment using docker-compose
3. Run each scenario sequentially with 5-minute stabilization window between tests
4. Capture Prometheus metrics snapshots before/after each test
5. Document results in `test-results/chaos-<YYYYMMDD-HHmm>.log`

---

## Scenario 1: Flink TaskManager Single-Node Failure

**Scenario ID**: `CHAOS-001`  
**Category**: Infrastructure Failure  
**Risk Level**: Medium (single point of failure with 2+ TaskManagers)

### Description

Simulate unexpected termination of a single Flink TaskManager. With proper configuration, remaining TaskManagers should assume the failed tasks through Kubernetes/Docker restart policies. This tests task redistribution and consumer lag recovery.

### Trigger

```bash
# List running TaskManagers
docker-compose ps | grep taskmanager

# Stop the first TaskManager
docker stop simple-streaming-pipeline-flink-taskmanager-1-1

# Verify stopped
docker ps | grep taskmanager | grep -v taskmanager-1-1
```

### Expected Behavior

1. **Immediate** (0-5s):
   - Failed TaskManager container enters `Exited` state
   - Kafka consumer lag for partition(s) handled by failed TM starts increasing
   - `flink_taskmanager_status{instance="taskmanager-1", state="failed"}` = 1

2. **Recovery Phase** (5-60s):
   - Orchestrator detects failure and triggers restart (if restart policy enabled) OR remaining TaskManagers trigger rebalance
   - Tasks previously assigned to failed TM are reassigned to operational TaskManagers
   - Consumer lag increases by max 500-1000 messages (backpressure)
   - Lag then begins decreasing as tasks resume processing

3. **Stabilization** (60-120s):
   - Consumer lag returns to baseline
   - Processing resumes at normal throughput
   - `checkpoint_failures_total` may increment by 1 (checkpoint in-flight on failed TM)
   - Checkpoints succeed on recovered state

### Pass/Fail Criteria

| Metric | Threshold | Status | Notes |
|--------|-----------|--------|-------|
| Consumer lag recovery time | < 60 seconds | **PASS** | Lag must return to < 5s within 60s |
| Data loss (DLQ depth) | 0 new messages | **PASS** | No transactions routed to DLQ; `dlq_events_total` unchanged |
| Checkpoint failures | <= 1 increment | **PASS** | One in-flight checkpoint may fail; no cascading failures |
| Scoring latency p99 | < 200ms during recovery | **PASS** | Temporary spike allowed; must not exceed 200ms |
| Enrichment latency p99 | < 50ms during recovery | **PASS** | Baseline + 25ms acceptable during redistribution |
| TaskManager restart completion | < 2 minutes | **PASS** | New TM must be healthy and assigned tasks |

### Manual Procedure

1. **Pre-Test**:
   ```bash
   # Establish baseline metrics
   curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="fraud-detection"}' | jq '.data.result[0].value[1]' > /tmp/baseline_lag.txt
   echo "Baseline consumer lag: $(cat /tmp/baseline_lag.txt)"
   
   # Note current time
   START_TIME=$(date +%s)
   ```

2. **Inject Failure**:
   ```bash
   docker stop simple-streaming-pipeline-flink-taskmanager-1-1
   echo "TaskManager stopped at $(date)"
   ```

3. **Monitor Recovery** (every 10 seconds for 2 minutes):
   ```bash
   for i in {1..12}; do
     LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="fraud-detection"}' | jq '.data.result[0].value[1]')
     FAILED=$(curl -s 'http://localhost:9090/api/v1/query?query=flink_taskmanager_status{state="failed"}' | jq '.data.result | length')
     ELAPSED=$(($(date +%s) - START_TIME))
     echo "[${ELAPSED}s] Lag: ${LAG} | Failed TMs: ${FAILED}"
     sleep 10
   done
   ```

4. **Verify Stability**:
   ```bash
   # Final lag check
   FINAL_LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="fraud-detection"}' | jq '.data.result[0].value[1]')
   BASELINE=$(cat /tmp/baseline_lag.txt)
   
   if (( $(echo "$FINAL_LAG < 5" | bc -l) )); then
     echo "PASS: Lag recovered to ${FINAL_LAG}s"
   else
     echo "FAIL: Lag stuck at ${FINAL_LAG}s (baseline: ${BASELINE}s)"
   fi
   ```

5. **Grafana Verification**:
   - Navigate to: `http://localhost:3000/d/fraud-pipeline-health`
   - Panels to inspect:
     - **Consumer Lag (1h window)**: Should show spike and recovery
     - **TaskManager Status**: Should show one TM missing, then restored
     - **Checkpoint Duration**: May show spike; should recover
     - **Scoring Latency (p99)**: Should remain < 200ms

6. **Cleanup** (if restart policy didn't auto-restart):
   ```bash
   docker-compose up -d flink-taskmanager-1-1
   docker-compose logs -f flink-taskmanager-1-1 | grep "Started TaskManager"
   ```

### Recovery Steps

1. **Automatic** (preferred):
   - Docker restart policy or Kubernetes will automatically restart the TaskManager
   - Flink will re-orchestrate tasks within 30-60 seconds
   - Monitor `docker-compose logs -f` for recovery messages

2. **Manual** (if automatic restart is disabled):
   ```bash
   # Bring TaskManager back online
   docker-compose up -d simple-streaming-pipeline-flink-taskmanager-1-1
   
   # Wait for it to register
   sleep 30
   
   # Verify task distribution
   docker-compose logs flink-taskmanager-1-1 | grep "Register.*selves"
   
   # Verify consumer lag is recovering
   curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag' | jq '.data.result[0].value[1]'
   ```

3. **Verify Clean State**:
   ```bash
   # Check no tasks are in error state
   curl -s 'http://localhost:9090/api/v1/query?query=flink_taskmanager_task_operator_watermark' | jq '.data.result | length'
   # Should match expected task count
   ```

---

## Scenario 2: ML Serving Outage (Circuit Breaker Test)

**Scenario ID**: `CHAOS-002`  
**Category**: Dependency Failure  
**Risk Level**: Medium (graceful degradation expected)

### Description

ML model serving service becomes unavailable. Circuit breaker should trigger after configured error threshold, circuit opens, and scoring requests are routed to fallback mode (rule-based scoring). This tests resilience pattern implementation and monitoring.

### Trigger

```bash
# Identify ML serving container
docker-compose ps | grep ml-stub

# Stop the ML serving service
docker stop simple-streaming-pipeline-ml-stub-1

# Verify stopped
docker ps | grep ml-stub
# Should show no running instances
```

### Expected Behavior

1. **Error Accumulation Phase** (0-30s):
   - First 3 scoring requests fail with connection timeout
   - `ml_service_errors_total{error_type="connection_refused"}` increments by 3
   - `ml_service_latency_ms{quantile="p99"}` spikes to 5000+ (timeout threshold)

2. **Circuit Breaker Transition** (30-45s):
   - After 3 consecutive failures, circuit breaker transitions to OPEN state
   - `ml_circuit_breaker_state{state="open"}` changes from 0 to 1
   - `MLCircuitBreakerOpen` alert fires (if Alertmanager configured)
   - Scoring requests immediately route to fallback mode (no more connection attempts)

3. **Fallback Mode** (45-until recovery):
   - `ml_fallback_decisions_total` increments for every scoring decision
   - Latency returns to normal: `scoring_latency_ms{quantile="p99"}` < 100ms
   - Fallback mode uses rule-based scoring (no ML inference)
   - No data loss; transactions are scored and persisted
   - DLQ remains empty (no failures routed to DLQ)

### Pass/Fail Criteria

| Metric | Threshold | Status | Notes |
|--------|-----------|--------|-------|
| Circuit breaker open latency | < 30 seconds | **PASS** | Must open within 30s of first error |
| Alert fire time | < 60 seconds | **PASS** | Alert must fire within 60s of circuit open |
| Fallback mode latency p99 | < 100ms | **PASS** | Scoring must complete in fallback mode |
| Fallback decisions counter | > 10 | **PASS** | Increments for each decision in fallback |
| DLQ depth change | 0 | **PASS** | No transactions lost during fallback |
| Scoring throughput | >= 90% baseline | **PASS** | Throughput may decrease slightly but must stay > 90% |

### Manual Procedure

1. **Pre-Test Setup**:
   ```bash
   # Get baseline metrics
   curl -s 'http://localhost:9090/api/v1/query?query=ml_circuit_breaker_state{state="open"}' | jq '.data.result[0].value[1] // "0"'
   # Should return 0 (circuit closed)
   
   BASELINE_THROUGHPUT=$(curl -s 'http://localhost:9090/api/v1/query?query=increase(scoring_total[5m])' | jq '.data.result[0].value[1]')
   echo "Baseline throughput (5m): ${BASELINE_THROUGHPUT} events"
   ```

2. **Start Traffic Generator** (in separate terminal):
   ```bash
   # Send 1 transaction per second to trigger scoring
   while true; do
     curl -X POST http://localhost:8000/api/transactions \
       -H "Content-Type: application/json" \
       -d '{
         "transaction_id": "txn-'$(date +%s%N)'",
         "amount": 100.0,
         "merchant_id": "MERC-001",
         "card_token": "4532-****-****-1234",
         "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
       }' 2>/dev/null
     sleep 1
   done
   ```

3. **Inject Failure**:
   ```bash
   docker stop simple-streaming-pipeline-ml-stub-1
   echo "ML service stopped at $(date)"
   FAILURE_TIME=$(date +%s)
   ```

4. **Monitor Circuit Breaker Transition** (every 5 seconds for 90 seconds):
   ```bash
   for i in {1..18}; do
     ELAPSED=$(($(date +%s) - FAILURE_TIME))
     
     # Check circuit state
     CIRCUIT_STATE=$(curl -s 'http://localhost:9090/api/v1/query?query=ml_circuit_breaker_state{state="open"}' | jq '.data.result[0].value[1] // "0"')
     
     # Check fallback counter
     FALLBACK=$(curl -s 'http://localhost:9090/api/v1/query?query=ml_fallback_decisions_total' | jq '.data.result[0].value[1] // "0"')
     
     # Check latency
     LATENCY=$(curl -s 'http://localhost:9090/api/v1/query?query=scoring_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "N/A"')
     
     echo "[${ELAPSED}s] Circuit Open: ${CIRCUIT_STATE} | Fallback Decisions: ${FALLBACK} | p99 Latency: ${LATENCY}ms"
     
     sleep 5
   done
   ```

5. **Grafana Verification**:
   - Navigate to: `http://localhost:3000/d/ml-circuit-breaker-panel`
   - Inspect panels:
     - **Circuit Breaker State**: Should transition from CLOSED to OPEN
     - **ML Service Errors**: Should show 3 errors, then stop
     - **Fallback Decisions**: Should show increasing counter
     - **Scoring Latency**: Should show spike then recovery to < 100ms
     - **Alerts Panel**: Should show `MLCircuitBreakerOpen` firing

### Recovery Steps

1. **Restart ML Service**:
   ```bash
   docker-compose up -d simple-streaming-pipeline-ml-stub-1
   
   # Wait for service to be ready
   sleep 10
   
   # Verify health
   curl -s http://localhost:8010/health | jq '.'
   ```

2. **Monitor Circuit Breaker Reset**:
   ```bash
   # Circuit breaker should remain in OPEN state for configured window (typically 60s)
   # After timeout, it transitions to HALF_OPEN
   # First request in HALF_OPEN will either close (success) or re-open (failure)
   
   for i in {1..60}; do
     CIRCUIT_STATE=$(curl -s 'http://localhost:9090/api/v1/query?query=ml_circuit_breaker_state' | jq '.data.result[] | select(.metric.state != "open") | .value[1]')
     if [[ "$CIRCUIT_STATE" == "0" ]]; then
       echo "Circuit transitioned to CLOSED at $i seconds"
       break
     fi
     echo "[${i}s] Still in OPEN state..."
     sleep 1
   done
   ```

3. **Verify Normal Operation**:
   ```bash
   # Confirm fallback decisions stop incrementing
   FALLBACK_FINAL=$(curl -s 'http://localhost:9090/api/v1/query?query=ml_fallback_decisions_total' | jq '.data.result[0].value[1]')
   
   # Confirm scoring uses ML inference again
   curl -s 'http://localhost:9090/api/v1/query?query=ml_inference_total' | jq '.data.result[0].value[1]'
   # Should show > 0
   
   # Latency should return to < 20ms
   curl -s 'http://localhost:9090/api/v1/query?query=scoring_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1]'
   ```

---

## Scenario 3: Kafka Broker Unavailability

**Scenario ID**: `CHAOS-003`  
**Category**: Data Platform Failure  
**Risk Level**: High (affects all producers/consumers)

### Description

Kafka broker becomes unavailable due to network partition or crash. Producers should backpressure or use DLQ routing. Consumers should continue processing from buffered state. This tests message durability and error handling.

### Trigger

```bash
# Option A: Hard stop (simulates crash)
docker stop simple-streaming-pipeline-broker-1

# Option B: Network pause (simulates partition)
docker pause simple-streaming-pipeline-broker-1

# Verify broker is down
docker ps | grep broker-1 | grep -E "(Exited|Paused)"
```

### Expected Behavior

1. **Immediate Impact** (0-5s):
   - Kafka connections timeout
   - Producer for enriched events experiences backpressure
   - `kafka_producer_record_error_total` increments for in-flight messages
   - DLQ routing is triggered for undeliverable transactions

2. **DLQ Routing Phase** (5-30s):
   - `FraudAlertsDLQDepthHigh` alert fires (if threshold > 10 messages)
   - `dlq_events_total{error_type="downstream_unavailable"}` increments
   - Consumer group lag accumulates for the unavailable topic(s)
   - Flink backpressure causes source lag to increase

3. **Broker Recovery** (broker restart):
   - Kafka broker re-registers with ZooKeeper
   - Producer reconnects and drains queued messages
   - Consumer group rebalances
   - DLQ messages may be replayed or retained depending on configuration

### Pass/Fail Criteria

| Metric | Threshold | Status | Notes |
|--------|-----------|--------|-------|
| No transaction loss | 100% recovery | **PASS** | All messages routed to DLQ or retried; zero loss |
| DLQ alert fire time | < 60 seconds | **PASS** | Alert must fire when DLQ depth > threshold |
| Consumer lag accumulation | < 10,000 messages | **PASS** | Lag accumulates but stays bounded; monitored |
| Producer error total | > 0 | **PASS** | At least some errors recorded for backpressure |
| Message replay time | < 5 minutes after recovery | **PASS** | DLQ/buffered messages replayed within 5min |
| Scoring latency p99 | < 200ms after recovery | **PASS** | Temporary degradation acceptable; recovers |

### Manual Procedure

1. **Pre-Test Setup**:
   ```bash
   # Verify broker is healthy
   docker-compose ps | grep broker-1
   
   # Get baseline metrics
   DLQ_BASELINE=$(curl -s 'http://localhost:9090/api/v1/query?query=dlq_events_total' | jq '.data.result | length')
   echo "Baseline DLQ event count: ${DLQ_BASELINE}"
   
   PRODUCER_ERRORS_BASELINE=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_producer_record_error_total' | jq '.data.result[0].value[1] // "0"')
   echo "Baseline producer errors: ${PRODUCER_ERRORS_BASELINE}"
   ```

2. **Start Transaction Traffic** (in separate terminal):
   ```bash
   # Generate transactions continuously
   for i in {1..300}; do
     curl -X POST http://localhost:8000/api/transactions \
       -H "Content-Type: application/json" \
       -d '{
         "transaction_id": "txn-'$(date +%s%N%-$RANDOM)'",
         "amount": '$((RANDOM % 1000)).50',
         "merchant_id": "MERC-'$((RANDOM % 5))'",
         "card_token": "4532-****-****-'$(printf "%04d" $((RANDOM % 9999)))'",
         "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
       }' 2>/dev/null
     sleep 1
   done
   ```

3. **Inject Failure**:
   ```bash
   docker stop simple-streaming-pipeline-broker-1
   echo "Broker stopped at $(date)"
   FAILURE_TIME=$(date +%s)
   ```

4. **Monitor Degradation** (every 10 seconds for 3 minutes):
   ```bash
   for i in {1..18}; do
     ELAPSED=$(($(date +%s) - FAILURE_TIME))
     
     # Producer errors
     PROD_ERR=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_producer_record_error_total' | jq '.data.result[0].value[1] // "0"')
     
     # DLQ depth
     DLQ=$(curl -s 'http://localhost:9090/api/v1/query?query=dlq_events_total' | jq '.data.result[0].value[1] // "0"')
     
     # Consumer lag
     LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag' | jq '.data.result[0].value[1] // "0"')
     
     echo "[${ELAPSED}s] Producer Errors: ${PROD_ERR} | DLQ Depth: ${DLQ} | Consumer Lag: ${LAG}"
     sleep 10
   done
   ```

5. **Grafana Verification**:
   - Navigate to: `http://localhost:3000/d/kafka-health-broker`
   - Inspect:
     - **Broker Status**: Should show broker-1 as DOWN
     - **Producer Errors**: Should show spike
     - **DLQ Depth**: Should show accumulation
     - **Consumer Lag**: Should show accumulation
     - **Alerts**: `FraudAlertsDLQDepthHigh` should fire

6. **Prepare for Recovery**:
   ```bash
   # Note down metrics for comparison
   echo "Final degradation state captured at $(date)"
   ```

5. **Restart Broker**:
   ```bash
   docker-compose up -d simple-streaming-pipeline-broker-1
   echo "Broker restart initiated at $(date)"
   RECOVERY_START=$(date +%s)
   ```

6. **Monitor Recovery** (every 10 seconds for 5 minutes):
   ```bash
   for i in {1..30}; do
     ELAPSED=$(($(date +%s) - RECOVERY_START))
     
     # Broker connectivity
     BROKER_UP=$(docker ps | grep -c "broker-1.*Up")
     
     # Consumer lag recovery
     LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag' | jq '.data.result[0].value[1] // "N/A"')
     
     # Producer errors (should stop increasing)
     PROD_ERR=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_producer_record_error_total' | jq '.data.result[0].value[1] // "0"')
     
     echo "[${ELAPSED}s] Broker Up: ${BROKER_UP} | Consumer Lag: ${LAG} | Producer Errors (total): ${PROD_ERR}"
     sleep 10
   done
   ```

### Recovery Steps

1. **Verify Broker Readiness**:
   ```bash
   # Wait for broker to fully start
   docker-compose logs -f broker-1 2>&1 | grep -m 1 "Kafka Server started"
   ```

2. **Trigger Consumer Group Rebalance**:
   ```bash
   # Rebalance by restarting consumers (optional; usually auto-triggered)
   docker-compose restart flink-jobmanager
   ```

3. **Replay DLQ Messages** (if implemented):
   ```bash
   # Check DLQ topic for messages
   kafka-console-consumer --bootstrap-server localhost:9092 \
     --topic fraud-alerts-dlq \
     --from-beginning \
     --max-messages 20 | head -20
   
   # If replay mechanism exists, trigger it
   curl -X POST http://localhost:8000/api/dlq/replay \
     -H "Content-Type: application/json" \
     -d '{"limit": 100}'
   ```

4. **Verify Clean State**:
   ```bash
   # Confirm producer errors stop accumulating
   PROD_ERR_FINAL=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_producer_record_error_total' | jq '.data.result[0].value[1]')
   
   # Confirm consumer lag returns to normal (< 5s)
   LAG_FINAL=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag' | jq '.data.result[0].value[1]')
   
   if (( $(echo "$LAG_FINAL < 5" | bc -l) )); then
     echo "PASS: Broker recovery complete, lag recovered"
   else
     echo "WARN: Consumer lag still elevated at ${LAG_FINAL}s"
   fi
   ```

---

## Scenario 4: Checkpoint Storage Unavailable (MinIO)

**Scenario ID**: `CHAOS-004`  
**Category**: State Management Failure  
**Risk Level**: Medium (processing continues; recovery mode active)

### Description

MinIO (distributed checkpoint storage) becomes unreachable due to network partition. Flink checkpoint operations fail, but processing continues in at-least-once mode (degraded exactly-once semantics). Alerts fire; administrator must monitor recovery.

### Trigger

```bash
# Option A: Network disconnect (simulates partition)
docker network disconnect fraudstream simple-streaming-pipeline-minio-1

# Option B: Container stop
docker stop simple-streaming-pipeline-minio-1

# Verify disconnected/stopped
docker network inspect fraudstream | grep -A 10 "minio" | grep -c "simpl"
# Should return 0 for disconnected; docker ps shows Exit for stopped
```

### Expected Behavior

1. **Immediate** (0-5s):
   - Flink checkpoint operation initiates; MinIO write times out
   - `flink_checkpoint_duration_ms{quantile="p99"}` spikes to > 30000 (timeout threshold)
   - `checkpoint_failures_total` increments (async, doesn't block processing)

2. **Retry Phase** (5-30s):
   - Flink retries checkpoint writes (default: up to 3 times)
   - `checkpoint_failures_total` continues incrementing for each attempt
   - Processing continues without blocking (checkpointing is async)
   - Consumer lag remains stable (processing not blocked)

3. **Failure State** (30s-until recovery):
   - Checkpoint operation eventually fails after retries exhausted
   - `CheckpointFailureHigh` alert fires (if enabled)
   - Processing continues in at-least-once mode (degraded)
   - Exactly-once semantics temporarily unavailable
   - Watermark progress continues normally

### Pass/Fail Criteria

| Metric | Threshold | Status | Notes |
|--------|-----------|--------|-------|
| Processing continues | Never stops | **PASS** | Flink must continue processing despite checkpoint failure |
| Checkpoint failure alert | Fires within 2min | **PASS** | Alert must fire when checkpoint failures detected |
| Exactly-once guarantee | Degraded to at-least-once | **PASS** | Expected behavior; recovery restores exactly-once |
| Consumer lag | Remains stable | **PASS** | Lag should not increase during checkpoint failure |
| Scoring latency p99 | < 100ms | **PASS** | Latency unchanged (checkpointing is async) |
| Enrichment latency p99 | < 20ms | **PASS** | Latency unchanged; same processing path |
| Watermark progress | Continues normally | **PASS** | Event time watermark must advance despite checkpoint failure |

### Manual Procedure

1. **Pre-Test Setup**:
   ```bash
   # Verify MinIO is accessible
   curl -s http://localhost:9000/minio/health/live
   # Should return 200 OK
   
   # Get baseline checkpoint metrics
   curl -s 'http://localhost:9090/api/v1/query?query=flink_checkpoint_duration_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"' > /tmp/baseline_checkpoint_duration.txt
   echo "Baseline checkpoint duration p99: $(cat /tmp/baseline_checkpoint_duration.txt)ms"
   
   BASELINE_FAILURES=$(curl -s 'http://localhost:9090/api/v1/query?query=checkpoint_failures_total' | jq '.data.result[0].value[1] // "0"')
   echo "Baseline checkpoint failures: ${BASELINE_FAILURES}"
   ```

2. **Inject Failure** (network disconnect):
   ```bash
   docker network disconnect fraudstream simple-streaming-pipeline-minio-1
   echo "MinIO disconnected from fraudstream network at $(date)"
   FAILURE_TIME=$(date +%s)
   ```

3. **Monitor Checkpoint Failures** (every 15 seconds for 3 minutes):
   ```bash
   for i in {1..12}; do
     ELAPSED=$(($(date +%s) - FAILURE_TIME))
     
     # Checkpoint duration spike
     CHECKPOINT_DUR=$(curl -s 'http://localhost:9090/api/v1/query?query=flink_checkpoint_duration_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
     
     # Failures counter
     FAILURES=$(curl -s 'http://localhost:9090/api/v1/query?query=checkpoint_failures_total' | jq '.data.result[0].value[1] // "0"')
     
     # Consumer lag (should remain stable)
     LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag' | jq '.data.result[0].value[1] // "0"')
     
     # Watermark progress
     WATERMARK=$(curl -s 'http://localhost:9090/api/v1/query?query=flink_operator_watermark' | jq '.data.result[-1].value[1] // "0"')
     
     echo "[${ELAPSED}s] Checkpoint p99: ${CHECKPOINT_DUR}ms | Failures: ${FAILURES} | Lag: ${LAG}s | Watermark: ${WATERMARK}"
     sleep 15
   done
   ```

4. **Verify Processing Continues**:
   ```bash
   # Check that Flink tasks are still running
   docker-compose logs -f flink-jobmanager --tail 30 | grep -i "task.*running"
   
   # Monitor Grafana in separate browser tab
   # Navigate to: http://localhost:3000/d/flink-checkpoint-health
   # Panels:
   #   - Checkpoint Duration p99: Should spike > 30s
   #   - Checkpoint Failures: Should show increasing counter
   #   - Consumer Lag: Should remain flat (< 1s increase)
   #   - Scoring Latency: Should remain < 100ms
   ```

5. **Reconnect MinIO**:
   ```bash
   docker network connect fraudstream simple-streaming-pipeline-minio-1
   echo "MinIO reconnected at $(date)"
   RECOVERY_START=$(date +%s)
   ```

6. **Monitor Checkpoint Recovery** (every 10 seconds for 2 minutes):
   ```bash
   for i in {1..12}; do
     ELAPSED=$(($(date +%s) - RECOVERY_START))
     
     # Checkpoint duration should return to normal
     CHECKPOINT_DUR=$(curl -s 'http://localhost:9090/api/v1/query?query=flink_checkpoint_duration_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
     
     # Failures should stop increasing
     FAILURES=$(curl -s 'http://localhost:9090/api/v1/query?query=checkpoint_failures_total' | jq '.data.result[0].value[1] // "0"')
     
     # Successful checkpoints should resume
     COMPLETED=$(curl -s 'http://localhost:9090/api/v1/query?query=flink_checkpoint_completed' | jq '.data.result[0].value[1] // "0"')
     
     echo "[${ELAPSED}s] Duration: ${CHECKPOINT_DUR}ms | Total Failures: ${FAILURES} | Completed: ${COMPLETED}"
     sleep 10
   done
   ```

### Recovery Steps

1. **Verify MinIO Connectivity**:
   ```bash
   # Check if MinIO is still running
   docker ps | grep minio | grep -i "up"
   
   # If down, restart
   docker-compose up -d simple-streaming-pipeline-minio-1
   
   # Verify health
   curl -s http://localhost:9000/minio/health/live
   ```

2. **Reconnect Network** (if disconnected):
   ```bash
   docker network connect fraudstream simple-streaming-pipeline-minio-1
   sleep 5
   
   # Verify connectivity from Flink container
   docker-compose exec flink-jobmanager curl -s http://minio:9000/minio/health/live
   ```

3. **Monitor Checkpoint Recovery**:
   ```bash
   # Flink will automatically retry checkpoints
   # Monitor success rate
   for i in {1..12}; do
     COMPLETED=$(curl -s 'http://localhost:9090/api/v1/query?query=increase(flink_checkpoint_completed[30s])' | jq '.data.result[0].value[1] // "0"')
     echo "Last 30s: ${COMPLETED} successful checkpoints"
     sleep 10
   done
   ```

4. **Verify Exactly-Once Semantics Restored**:
   ```bash
   # Check checkpoint completion rate
   curl -s 'http://localhost:9090/api/v1/query?query=increase(flink_checkpoint_completed[5m]) / increase(flink_checkpoint_initiated[5m])' | jq '.data.result[0].value[1]'
   # Should return >= 0.95 (95% success rate) once recovered
   
   # Confirm no alert firing
   curl -s 'http://localhost:9090/api/v1/query?query=ALERTS{alertname="CheckpointFailureHigh"}' | jq '.data.result | length'
   # Should return 0 once recovered
   ```

---

## Scenario 5: Analytics Consumer Crash (Streamlit)

**Scenario ID**: `CHAOS-005`  
**Category**: Ancillary Service Failure  
**Risk Level**: Low (no impact on scoring)

### Description

Streamlit analytics dashboard consumer crashes. Since analytics is decoupled from fraud scoring, there should be zero impact on fraud detection latency or throughput. Analytics consumer lag accumulates until recovery. This tests isolation of non-critical components.

### Trigger

```bash
# Stop the Streamlit dashboard service
docker stop simple-streaming-pipeline-streamlit-1

# Verify stopped
docker ps | grep streamlit
# Should show no running instances or "Exited" status
```

### Expected Behavior

1. **Immediate** (0-5s):
   - Streamlit consumer group stops consuming messages
   - `kafka_consumer_group_lag{group="analytics"}` starts increasing
   - Scoring pipeline continues unaffected
   - No alert fires (this is expected, not a failure condition)

2. **Steady State** (5s-until recovery):
   - Analytics consumer lag accumulates
   - Kafka retains messages for replay window (typically 7 days)
   - Fraud scoring latency unchanged
   - Enrichment latency unchanged
   - Dashboard becomes unavailable (http://localhost:8501)

3. **Recovery Phase** (upon restart):
   - Streamlit restarts
   - Consumer group resumes from last committed offset
   - Consumer lag drains as backlog is replayed
   - Dashboard becomes available again

### Pass/Fail Criteria

| Metric | Threshold | Status | Notes |
|--------|-----------|--------|-------|
| Scoring p99 latency | < 100ms (unchanged) | **PASS** | Zero impact on fraud detection |
| Enrichment p99 latency | < 20ms (unchanged) | **PASS** | Zero impact on enrichment path |
| Scoring throughput | >= 100% baseline | **PASS** | No throughput degradation |
| FraudFlagRateZero alert | Does NOT fire | **PASS** | Analytics crash doesn't affect scoring |
| Analytics lag accumulation | Monotonically increasing | **PASS** | Expected behavior |
| Analytics lag recovery time | < 5 minutes | **PASS** | Lag drains within 5 minutes of restart |

### Manual Procedure

1. **Pre-Test Setup**:
   ```bash
   # Verify analytics consumer is healthy
   docker-compose ps | grep streamlit
   # Should show "Up"
   
   # Get baseline metrics
   SCORING_P99_BASELINE=$(curl -s 'http://localhost:9090/api/v1/query?query=scoring_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
   echo "Baseline scoring p99: ${SCORING_P99_BASELINE}ms"
   
   ENRICHMENT_P99_BASELINE=$(curl -s 'http://localhost:9090/api/v1/query?query=enrichment_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
   echo "Baseline enrichment p99: ${ENRICHMENT_P99_BASELINE}ms"
   
   ANALYTICS_LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="analytics"}' | jq '.data.result[0].value[1] // "0"')
   echo "Baseline analytics lag: ${ANALYTICS_LAG}s"
   ```

2. **Start Continuous Scoring Traffic** (in separate terminal):
   ```bash
   # Send consistent transaction stream
   for i in {1..180}; do
     curl -X POST http://localhost:8000/api/transactions \
       -H "Content-Type: application/json" \
       -d '{
         "transaction_id": "txn-'$(date +%s%N)'",
         "amount": '$((RANDOM % 1000)).50',
         "merchant_id": "MERC-'$((RANDOM % 10))'",
         "card_token": "4532-****-****-'$(printf "%04d" $((RANDOM % 9999)))'",
         "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
       }' 2>/dev/null &
     
     sleep 1
   done
   ```

3. **Inject Failure**:
   ```bash
   docker stop simple-streaming-pipeline-streamlit-1
   echo "Streamlit stopped at $(date)"
   FAILURE_TIME=$(date +%s)
   ```

4. **Monitor Analytics Impact** (every 10 seconds for 2 minutes):
   ```bash
   for i in {1..12}; do
     ELAPSED=$(($(date +%s) - FAILURE_TIME))
     
     # Analytics lag (should increase)
     ANALYTICS_LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="analytics"}' | jq '.data.result[0].value[1] // "0"')
     
     # Scoring latency (should remain unchanged)
     SCORING_P99=$(curl -s 'http://localhost:9090/api/v1/query?query=scoring_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
     
     # Enrichment latency (should remain unchanged)
     ENRICHMENT_P99=$(curl -s 'http://localhost:9090/api/v1/query?query=enrichment_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1] // "0"')
     
     # Fraud flag rate (should remain normal)
     FLAG_RATE=$(curl -s 'http://localhost:9090/api/v1/query?query=fraud_flags_total' | jq '.data.result[0].value[1] // "0"')
     
     echo "[${ELAPSED}s] Analytics Lag: ${ANALYTICS_LAG}s | Scoring p99: ${SCORING_P99}ms | Enrichment p99: ${ENRICHMENT_P99}ms | Fraud Flags: ${FLAG_RATE}"
     
     sleep 10
   done
   ```

5. **Verify No Impact on Dashboard Metrics** (Grafana):
   ```bash
   # Open: http://localhost:3000/d/fraud-scoring-latency
   # Panels should show:
   #   - Scoring Latency p99: Flat line (no spike)
   #   - Enrichment Latency p99: Flat line (no spike)
   #   - Throughput: Flat line (no change)
   #   - Fraud Flag Rate: Stable (no alert)
   ```

6. **Verify Dashboard is Unavailable**:
   ```bash
   curl -s http://localhost:8501 > /dev/null 2>&1
   if [ $? -ne 0 ]; then
     echo "PASS: Dashboard is unavailable (expected)"
   else
     echo "FAIL: Dashboard is still accessible (unexpected)"
   fi
   ```

7. **Restart Streamlit**:
   ```bash
   docker-compose up -d simple-streaming-pipeline-streamlit-1
   echo "Streamlit restart initiated at $(date)"
   RECOVERY_START=$(date +%s)
   ```

8. **Monitor Analytics Recovery** (every 10 seconds for 3 minutes):
   ```bash
   for i in {1..18}; do
     ELAPSED=$(($(date +%s) - RECOVERY_START))
     
     # Analytics lag (should decrease)
     ANALYTICS_LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="analytics"}' | jq '.data.result[0].value[1] // "0"')
     
     # Dashboard should become available
     DASHBOARD_UP=$(curl -s http://localhost:8501 > /dev/null 2>&1 && echo "1" || echo "0")
     
     echo "[${ELAPSED}s] Analytics Lag: ${ANALYTICS_LAG}s | Dashboard Up: ${DASHBOARD_UP}"
     
     sleep 10
   done
   ```

### Recovery Steps

1. **Restart Streamlit Service**:
   ```bash
   docker-compose up -d simple-streaming-pipeline-streamlit-1
   
   # Wait for startup
   sleep 10
   
   # Verify it's running
   docker-compose logs -f streamlit --tail 20 | grep -E "(Streamlit|Started|listening)"
   ```

2. **Verify Dashboard Access**:
   ```bash
   # Wait for Streamlit to be ready (up to 30 seconds)
   for i in {1..30}; do
     if curl -s http://localhost:8501 > /dev/null 2>&1; then
       echo "Dashboard is accessible"
       break
     fi
     echo "Waiting for dashboard... ($i/30)"
     sleep 1
   done
   ```

3. **Monitor Consumer Lag Drain**:
   ```bash
   # Lag should steadily decrease
   for i in {1..30}; do
     LAG=$(curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag{group="analytics"}' | jq '.data.result[0].value[1] // "0"')
     echo "Analytics lag: ${LAG}s"
     
     if (( $(echo "$LAG < 5" | bc -l) )); then
       echo "PASS: Analytics lag recovered"
       break
     fi
     
     sleep 10
   done
   ```

4. **Verify Full Recovery**:
   ```bash
   # Confirm all metrics are back to baseline
   FINAL_SCORING_P99=$(curl -s 'http://localhost:9090/api/v1/query?query=scoring_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1]')
   FINAL_ENRICHMENT_P99=$(curl -s 'http://localhost:9090/api/v1/query?query=enrichment_latency_ms{quantile="p99"}' | jq '.data.result[0].value[1]')
   
   echo "Final scoring p99: ${FINAL_SCORING_P99}ms (baseline: ${SCORING_P99_BASELINE}ms)"
   echo "Final enrichment p99: ${FINAL_ENRICHMENT_P99}ms (baseline: ${ENRICHMENT_P99_BASELINE}ms)"
   ```

---

## Summary Test Matrix

| Scenario | Duration | Risk | Impact Zone | Alert Expected |
|----------|----------|------|-------------|-----------------|
| CHAOS-001: TaskManager Failure | 2-3 min | Medium | Flink compute | Task redistribution logs |
| CHAOS-002: ML Service Outage | 3-5 min | Medium | Scoring path | MLCircuitBreakerOpen |
| CHAOS-003: Kafka Broker Down | 3-5 min | High | All streams | FraudAlertsDLQDepthHigh |
| CHAOS-004: MinIO Unavailable | 3-5 min | Medium | State mgmt | CheckpointFailureHigh |
| CHAOS-005: Analytics Crash | 2-3 min | Low | Dashboard only | None (expected) |

---

## Post-Test Checklist

After running any scenario, verify:

- [ ] All services return to healthy state (`docker-compose ps` shows all Up)
- [ ] Consumer lag returns to baseline (< 5 seconds)
- [ ] No unexpected alerts are firing (`http://localhost:9090/alerts`)
- [ ] Prometheus scrape targets are all Green (`http://localhost:9090/targets`)
- [ ] Grafana dashboards load without errors
- [ ] No errors in service logs (`docker-compose logs | grep -i error`)
- [ ] DLQ is empty or at expected baseline
- [ ] Checkpoints are completing successfully

---

## Appendix: Useful Commands

### Monitoring One-Liners

```bash
# Real-time lag monitoring
watch -n 5 'curl -s "http://localhost:9090/api/v1/query?query=kafka_consumer_group_lag" | jq ".data.result[] | {group: .metric.group, lag: .value[1]}"'

# Circuit breaker status
curl -s 'http://localhost:9090/api/v1/query?query=ml_circuit_breaker_state' | jq '.data.result[] | {state: .metric.state, value: .value[1]}'

# Checkpoint health
curl -s 'http://localhost:9090/api/v1/query?query={checkpoint_completed,checkpoint_failures_total}' | jq '.data.result[] | {metric: .metric.__name__, value: .value[1]}'

# Producer errors
curl -s 'http://localhost:9090/api/v1/query?query=kafka_producer_record_error_total' | jq '.data.result[0].value[1]'

# DLQ depth by error type
curl -s 'http://localhost:9090/api/v1/query?query=dlq_events_total' | jq '.data.result[] | {error_type: .metric.error_type, total: .value[1]}'
```

### Service Health Checks

```bash
# All services
docker-compose ps

# Kafka broker connectivity
docker exec simple-streaming-pipeline-kafka-1 kafka-broker-api-versions --bootstrap-server localhost:9092

# Flink JobManager health
curl -s http://localhost:8081/v1/overview | jq '{status: .status}'

# MinIO health
curl -s http://localhost:9000/minio/health/live

# Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets | length'
```

---

## Related Documentation

- **Flink Deployment Guide**: See `.github/docs/flink-deployment.md`
- **Monitoring Stack Setup**: See `.github/docs/monitoring-setup.md`
- **Runbook for Alerts**: See `ops/runbooks/` directory
- **Incident Response**: See `ops/incident-procedures.md`
