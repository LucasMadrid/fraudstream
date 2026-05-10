# FraudStream Constitution Amendment Proposal
## Version 2.0.0 — From v1.7.0

**Author:** Architecture Agent
**Date:** 2026-05-09
**Status:** PROPOSED
**Scope:** 6 amended principles, 5 new principles, 4 amended design decisions, 3 new design decisions, 14 violation remediations

---

## EXECUTIVE SUMMARY

Constitution v1.7.0 has served well through 9 feature iterations but has
accumulated 6 known violations, 4 undocumented gaps, and several principles
that no longer reflect operational reality. This proposal promotes the
constitution to v2.0.0 (major bump) because it introduces 5 new principles
(XII–XVI) and restructures the SRP/module-boundary expectations that affect
every pipeline module.

---

## PART 1: CURRENT CONSTITUTION VIOLATIONS IN CODE

### V1. Principle III Violation — txn.enriched Still JSON
- **Location:** pipelines/processing/job.py:232-236
- **Evidence:** `enriched_stream.map(lambda record: json.dumps(record, default=str), output_type=Types.STRING())`
- **Impact:** txn.enriched topic emits JSON, not Avro. Downstream consumers cannot enforce schema contracts.
- **Constitution text says:** "Avro mandatory" (NON-NEGOTIABLE)
- **Status:** Acknowledged, deadline v2.0. STILL OPEN.
- **Remediation:** See Amendment A-III below.

### V2. Principle VIII Violation — Silent Record Drops
- **Location:** pipelines/processing/job.py:146-166 (deserialise_with_dlq)
- **Evidence:** When SchemaValidationError occurs, the function calls build_dlq_record() but the DLQ record is never actually produced to Kafka (the DLQ_OUTPUT_TAG side output wiring is commented as "in the full implementation"). The record is logged then silently discarded.
- **Impact:** Records disappear without DLQ evidence. Violates "no silent failures."
- **Remediation:** Wire DLQ_OUTPUT_TAG side output properly OR produce to DLQ topic inline.

### V3. Principle VIII Violation — OpenTelemetry Partially Implemented
- **Location:** pipelines/shared/telemetry.py, pipelines/scoring/telemetry.py
- **Evidence:** OTel tracer is implemented for scoring (fraud_rule_evaluation_span) and ingestion, but processing pipeline (Flink job) has NO tracing instrumentation. No trace context propagation across Kafka topics.
- **Impact:** Distributed traces are broken — ingestion spans cannot be correlated with processing/scoring spans.
- **Status:** Constitution says "distributed tracing" but only single-service spans exist.

### V4. Principle VI Violation — IcebergSinkBase Swallows Errors
- **Location:** pipelines/shared/iceberg_sink_base.py:131-134
- **Evidence:** When `self._table is None`, `_flush()` silently returns without writing or DLQ-routing the buffered records, then clears the buffer at line 163. Data is lost.
- **Impact:** If Iceberg catalog is unavailable at startup, ALL records buffered before next open() attempt are silently dropped.

### V5. SRP Violation — EnrichedRecordAssembler Contains Iceberg Sink
- **Location:** pipelines/processing/operators/enricher.py:25-64
- **Evidence:** EnrichedRecordAssembler.__init__ accepts an IcebergEnrichedSink, calls sink.invoke() in flat_map(). Assembly and persistence are coupled in one operator.
- **Impact:** Cannot test assembly without Iceberg, cannot swap sink implementation, violates single-responsibility.

### V6. Module Boundary Violation — scoring imports from processing
- **Location:** pipelines/processing/job.py:198-207
- **Evidence:** Processing job.py imports from pipelines.scoring.config, pipelines.scoring.job_extension, pipelines.scoring.rules.loader. The processing pipeline depends on the scoring package.
- **Impact:** Reverse dependency. Processing should emit to a topic; scoring should consume independently.

### V7. Principle I Violation — No Kafka Authentication
- **Location:** pipelines/ingestion/api/config.py:27-40 (librdkafka_config)
- **Evidence:** No security.protocol, sasl.mechanism, or SSL configuration anywhere in producer or consumer configs.
- **Impact:** Kafka cluster is open. Any network-adjacent process can produce/consume.

### V8. Principle VII Partial Violation — PII in Logs
- **Location:** pipelines/ingestion/api/producer.py:277-288
- **Evidence:** transaction_id is logged. While transaction_id itself is not PII, the log entry pattern could be enriched to include account_id inadvertently.
- **No remediation needed** — current code is safe, but constitution should add explicit log-field allowlist.

---

## PART 2: AMENDED PRINCIPLES (changes to existing I–XI)

### Amendment A-I: Principle I — Stream-First (NON-NEGOTIABLE)
**Current text:** "Kafka as single entry point, KRaft, idempotent producers, cross-restart dedup via Flink DeduplicationFilter"

**Proposed addition:**
```
ADDED:
- Kafka authentication MUST be configured in all non-local environments.
  security.protocol=SASL_SSL with SCRAM-SHA-512 minimum.
- Topic ACLs MUST restrict produce/consume per service identity.
- Partitioning strategy: txn.* topics MUST be partitioned by account_id
  to ensure per-account ordering for velocity calculations.
  Minimum 12 partitions for txn.api, txn.enriched; 6 for txn.fraud.alerts.
- Consumer group naming convention: {service}.{function} (e.g. flink-enrichment-processor, analytics.dashboard).
```

**Rationale:** V7 (no Kafka auth) is a security gap. Partitioning by account_id is implicit in the code (producer.py:267 uses account_id as key) but undocumented in the constitution. Making it explicit prevents accidental repartitioning that would break velocity windows.

---

### Amendment A-III: Principle III — Schema Contract Enforcement (NON-NEGOTIABLE)
**Current text:** "Avro mandatory, BACKWARD_TRANSITIVE, known drift: txn.enriched still JSON"

**Proposed change:**
```
CHANGED:
- "known drift" clause REMOVED. v2.0.0 requires txn.enriched to use Avro.
- Deadline: txn.enriched Avro migration MUST complete before v2.1.0.
- All inter-service topics (txn.api, txn.enriched, txn.fraud.alerts,
  txn.fraud.decisions) MUST use Avro with Schema Registry.
- DLQ topics (*.dlq) MAY use JSON for human readability.
- Schema evolution: BACKWARD_TRANSITIVE for value schemas,
  FULL_TRANSITIVE for key schemas.

ADDED:
- Schema validation errors MUST route to DLQ with original payload
  (masked), error type, and source offset. Never silently drop.
```

**Rationale:** The JSON drift in txn.enriched has been "acknowledged" for too long. The v2.0 constitution should set a hard deadline. V2 fixes the silent drop of schema-invalid records.

---

### Amendment A-V: Principle V — Defense in Depth
**Current text:** "rules before models, circuit breaker (3 failures or 5s), hot-configurable YAML rules"

**Proposed addition:**
```
ADDED:
- Rule deployment MUST support shadow mode. New rules MUST be deployed
  in shadow mode first, evaluated against live traffic, with false-positive
  rate tracked via rule_shadow_triggers_total and rule_shadow_fp_total
  metrics. Promotion to active requires explicit API call or config change.
- Minimum shadow observation window: 24 hours or 10,000 transactions
  (whichever comes first) before promotion is permitted.
- Circuit breaker parameters MUST be externalized via environment variables
  (already done: CB_ERROR_THRESHOLD, CB_OPEN_SECONDS, CB_PROBE_TIMEOUT_MS,
  CB_ERROR_WINDOW_SECONDS).
```

**Rationale:** Shadow mode is already implemented (RuleMode.shadow in models.py, shadow tracking in evaluator.py, shadow_rules Streamlit page, management API promote/demote) but NOT constitutionally mandated. Making it a principle prevents bypassing it.

---

### Amendment A-VI: Principle VI — Immutable Event Log
**Current text:** "Iceberg append-only, Postgres fraud_alerts as narrow exception"

**Proposed addition:**
```
ADDED:
- Iceberg sink failures MUST route batch to DLQ, never silently discard.
  When catalog is unavailable at open(), subsequent invoke() calls MUST
  buffer and retry OR route to DLQ — not silently clear buffer.
- Iceberg flush timeout: 5 seconds (ICEBERG_FLUSH_TIMEOUT_SEC).
  Circuit breaker: 3 failures -> open for 30 seconds.
- IcebergSinkBase is the single base class for all Iceberg append sinks.
  No sink logic may be embedded in non-sink operators (see SRP rule below).
```

**Rationale:** V4 (silent data loss when catalog unavailable) is a data integrity bug. V5 (Iceberg sink baked into EnrichedRecordAssembler) violates separation of concerns.

---

### Amendment A-VIII: Principle VIII — Observability First-Class
**Current text:** "structured logs, metrics, traces (OpenTelemetry mentioned but not implemented), DLQ alerts <60s"

**Proposed change:**
```
CHANGED:
- "(OpenTelemetry mentioned but not implemented)" REMOVED.
- OpenTelemetry distributed tracing IS implemented for scoring
  (fraud_rule_evaluation_span) and ingestion (api.producer.publish).

ADDED:
- Trace context propagation MUST be implemented across Kafka topic
  boundaries using W3C traceparent headers by v2.1.0.
- Processing pipeline (Flink) MUST instrument: deserialization,
  deduplication, enrichment (velocity/geo/device), and assembly spans.
- Sampling strategy: ParentBased with TraceIdRatioBased(0.01) for
  ALLOW decisions; AlwaysOn for BLOCK and error decisions.
- Log-field allowlist: transaction_id, account_id (hashed), channel,
  rule_id, decision, latency_ms. No PAN, IP, or raw PII in logs.
- Every operator MUST emit at least one Prometheus counter. Silent
  code paths are forbidden.
```

**Rationale:** V3 (partial OTel) and V2 (silent drops) require explicit observability mandates. The sampling strategy is already implemented in scoring/telemetry.py but not constitutionalized.

---

### Amendment A-XI: Principle XI — Feature Serving Contract (NON-NEGOTIABLE)
**Current text:** "2ms p99 read, 3ms timeout, zero-valued fallback, 30s staleness bound"

**Proposed addition:**
```
ADDED:
- Feature serving client MUST be accessed via FeatureServingProtocol
  (abstract interface in pipelines/scoring/types.py). Concrete implementations
  (Feast, Redis-direct, mock) are pluggable.
- Feature materialization is triggered by Iceberg flush success hook,
  NOT by the enrichment operator directly (separation of concerns).
- Staleness monitoring: feature_store_staleness_seconds Prometheus gauge.
  Alert threshold: 30 seconds.
```

**Rationale:** Feature serving protocol exists (FeatureServingProtocol in types.py) but the constitution doesn't mandate its use as the abstraction layer. Making it explicit prevents direct Feast coupling.

---

## PART 3: NEW PRINCIPLES (XII–XVI)

### NEW Principle XII: Safe Rule Deployment (NON-NEGOTIABLE)
```
Every fraud rule change MUST follow the deployment pipeline:
  1. YAML definition with Pydantic validation (RuleDefinition model)
  2. Shadow mode deployment (RuleMode.shadow)
  3. Observation period with FP rate tracking (rule_shadow_fp_total / rule_shadow_triggers_total)
  4. Promotion via Management API (/rules/{rule_id}/promote) or config update
  5. Active mode (RuleMode.active)

Rollback: demote to shadow via /rules/{rule_id}/demote within 5 minutes
of anomalous FP rate increase.

Rule hot-reload: RuleLoader MUST support file-watch reload without
job restart. Current implementation loads at open() only — v2.1.0 must
add periodic reload (every 60 seconds) or config-map watch.
```

**Rationale:** Shadow mode infrastructure exists but the end-to-end deployment lifecycle is not mandated. This principle codifies the shadow->active->rollback flow.

---

### NEW Principle XIII: Event Replay and Reprocessing
```
The system MUST support deterministic event replay:
  1. Kafka retention: minimum 7 days for txn.api, txn.enriched.
  2. Iceberg tables serve as the long-term replay source (append-only,
     time-travel queries via snapshot IDs).
  3. Replay consumer group: replay.{service}.{timestamp} — isolated
     from production consumer groups.
  4. Replay mode MUST NOT write to production sinks. Replay outputs
     go to shadow topics (txn.enriched.replay, txn.fraud.alerts.replay).
  5. Flink savepoints MUST be taken before any topology change.
     Savepoint directory: s3://flink-savepoints/{job-name}/{timestamp}.

Known limitation v2.0: Replay tooling is not yet implemented.
Iceberg time-travel and Kafka retention provide the data foundation.
Implementation target: v2.2.0.
```

**Rationale:** No replay capability exists. The Iceberg tables and Kafka retention provide the data, but no tooling or process exists to replay events through the pipeline safely.

---

### NEW Principle XIV: ML Model Serving Independence
```
ML model inference MUST be decoupled from the rule evaluation pipeline:
  1. MLModelClient (abstract, in pipelines/scoring/ml_client.py) is the
     sole interface for model scoring. Concrete implementations:
     - StubMLModelClient (testing, local dev)
     - HTTPMLModelClient (production, calls ML_SERVING_URL)
  2. Model serving is an independent service (not embedded in Flink).
     Communication via HTTP/gRPC with circuit breaker protection.
  3. Circuit breaker: CB_ERROR_THRESHOLD failures in CB_ERROR_WINDOW_SECONDS
     window -> open for CB_OPEN_SECONDS. Half-open probe timeout:
     CB_PROBE_TIMEOUT_MS.
  4. Model version tracking: every FraudDecision records model_version.
     Current rule-only path uses "rule-only" sentinel.
  5. Model A/B testing: score() returns MLScore with fraud_probability
     and model_version. Multiple models can score the same transaction;
     only the primary model's score drives the decision.
  6. Latency budget: ML inference MUST complete within 30ms (Principle II
     ML slice). Circuit breaker probe timeout: 5ms.

Current state: StubMLModelClient only. No production ML integration.
FraudDecision uses heuristic scores (0.0/0.3/0.8) instead of model scores.
Implementation target: v2.1.0.
```

**Rationale:** ml_client.py defines the abstraction but only a stub exists. The constitution should mandate the decoupling pattern before a real model is integrated, preventing tight coupling.

---

### NEW Principle XV: Horizontal Scaling and Partitioning Strategy
```
FraudStream MUST scale horizontally without architectural changes:
  1. Kafka partitioning: account_id as partition key for all txn.* topics.
     This ensures per-account event ordering for velocity window correctness.
  2. Flink parallelism: configurable via PARALLELISM env var. Each operator
     parallelism equals job parallelism (no per-operator override in v2.0).
  3. Flink state: RocksDB incremental checkpoints (DD-2). State key
     distribution follows Kafka partition key (account_id for velocity,
     api_key_id for device fingerprinting).
  4. Stateless operators (geolocation, assembly) scale linearly.
  5. Stateful operators (velocity, device) scale with state redistribution
     on parallelism change — requires savepoint + restart.
  6. Feature store: Redis cluster mode for production. Feast online store
     read latency: 2ms p99 regardless of cluster size.
  7. Iceberg: partition by date(event_time) for enriched_transactions,
     date(decision_time_ms) for fraud_decisions.

Anti-patterns:
  - DO NOT use transaction_id as Kafka partition key (breaks account ordering).
  - DO NOT embed state in operator constructor (breaks serialization).
  - DO NOT use global state (MapState is scoped to key, not global).
```

**Rationale:** Partitioning strategy is implicit in the code but not documented. The anti-patterns section prevents common mistakes when scaling.

---

### NEW Principle XVI: Module Boundaries and Dependency Direction
```
Dependency direction MUST flow: ingestion -> processing -> scoring -> analytics.
No reverse dependencies.

Module boundaries:
  ingestion/  — Kafka producers, PII masking, schema validation
  processing/ — Flink job, enrichment operators, Iceberg enriched sink
  scoring/    — Rule evaluation, ML scoring, alert sinks, Iceberg decisions sink
  analytics/  — Consumer layer, Streamlit UI, query engine
  shared/     — Cross-cutting: config, telemetry, circuit_breaker, protocols

Communication between modules:
  - ingestion -> processing: via Kafka topic txn.api (Avro)
  - processing -> scoring: via Kafka topic txn.enriched (Avro, post v2.0)
  - scoring -> analytics: via Kafka topic txn.fraud.alerts (Avro)

VIOLATION IN CURRENT CODE: processing/job.py imports from scoring/
(config, job_extension, rules.loader). This MUST be refactored:
  - scoring should be a separate Flink job consuming txn.enriched
  - OR scoring wiring should be injected via a plugin/extension point
    that does not create a compile-time dependency from processing->scoring.

Target: v2.1.0 for full separation. Interim: document the coupling
as a known violation with ADR.
```

**Rationale:** V6 (reverse dependency) is a structural issue. The current monolithic job (processing + scoring in one Flink pipeline) was a pragmatic choice but should not be the long-term architecture.

---

## PART 4: AMENDED DESIGN DECISIONS

### DD-2 Amendment: RocksDB Incremental Checkpoints
**Add:**
```
Savepoint strategy: before any topology change, take a named savepoint.
Savepoints are stored at s3://flink-savepoints/{job-name}/{ISO-timestamp}.
Checkpoint interval: 60 seconds (CHECKPOINT_INTERVAL_MS env var).
State TTL: 48 hours for dedup state, configurable for velocity/device.
```

### DD-5 Amendment: acks=all Idempotent
**Add:**
```
Kafka authentication: SASL_SSL with SCRAM-SHA-512 in non-local environments.
Configuration via KAFKA_SECURITY_PROTOCOL, KAFKA_SASL_MECHANISM,
KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD environment variables.
Local dev: PLAINTEXT (no auth) permitted when FRAUDSTREAM_ENV=local.
```

### DD-8 Amendment: Dual-Sink Kafka+PG
**Change:**
```
CHANGED: PostgreSQL sink is best-effort (emit() never raises).
Kafka sink is the primary alert path — failures propagate upstream.
ADDED: Iceberg decisions sink is the third sink for all fraud decisions
(not just alerts). IcebergDecisionsSink writes ALLOW, FLAG, and BLOCK
decisions to iceberg.fraud_decisions.
```

### DD-9 Amendment: Daemon Consumer Threads for Metrics
**Change:**
```
CHANGED: DD-9 is acknowledged as a workaround for PyFlink's JVM subprocess
architecture. The Kafka metrics bridge (kafka_metrics_bridge.py) runs
daemon threads in the main process to read txn.fraud.alerts and txn.enriched
topics and increment Prometheus counters.

EVOLUTION PATH: When scoring becomes a separate Flink job (Principle XVI),
each job will have its own Prometheus endpoint and the metrics bridge
will be retired. Target: v2.2.0.
```

---

## PART 5: NEW DESIGN DECISIONS

### DD-12: Trace Context Propagation via Kafka Headers
```
W3C traceparent header injected by Kafka producers, extracted by consumers.
Implementation: opentelemetry-instrumentation-confluent-kafka for ingestion,
manual header injection/extraction for PyFlink (no auto-instrumentation).
Trace context crosses: ingestion -> processing -> scoring.
Analytics consumer does NOT propagate traces (read-only, no side effects).
```

### DD-13: Shadow Scoring Pipeline
```
Shadow rules are evaluated in the same RuleEvaluator.dispatch() call as
active rules. Shadow matches do NOT affect the determination (always returns
the active-rules-only determination). Shadow metrics are recorded separately:
  - rule_shadow_triggers_total (counter, labels: rule_id)
  - rule_shadow_fp_total (counter, labels: rule_id)
Shadow results are appended to matched_rules with ":shadow" suffix for
audit trail in Iceberg fraud_decisions table.
Promotion/demotion via Management API (FastAPI, /rules/{id}/promote|demote).
```

### DD-14: IcebergSinkBase as Shared Base Class
```
All Iceberg append sinks extend _IcebergSinkBase (pipelines/shared/iceberg_sink_base.py).
Base provides: buffer management, dedup, circuit breaker, DLQ routing,
flush timeout (ThreadPoolExecutor), and hook methods.
Subclasses implement: _records_to_arrow_table() and optional hooks
(_after_flush_success, _on_buffer_overflow, etc.).
Current subclasses: IcebergEnrichedSink, IcebergDecisionsSink.
```

---

## PART 6: VIOLATION REMEDIATION PLAN

| ID | Violation | Severity | Remediation | Target |
|----|-----------|----------|-------------|--------|
| V1 | txn.enriched JSON | P0 | Add Avro serializer to processing job output | v2.0.0 |
| V2 | Silent record drops | P0 | Wire DLQ_OUTPUT_TAG side output in job.py | v2.0.0 |
| V3 | Partial OTel tracing | P1 | Add spans to Flink operators, trace propagation | v2.1.0 |
| V4 | Iceberg silent data loss | P0 | Fix _flush() to DLQ when table is None | v2.0.0 |
| V5 | SRP: sink in assembler | P1 | Extract IcebergEnrichedSink to separate operator | v2.0.0 |
| V6 | Reverse dependency | P1 | Split scoring into separate job or use plugin | v2.1.0 |
| V7 | No Kafka auth | P0 | Add SASL_SSL config, enforce in non-local envs | v2.0.0 |
| V8 | Log field safety | P2 | Add log-field allowlist to Principle VIII | v2.0.0 |

---

## PART 7: VERSION BUMP JUSTIFICATION

**Why v2.0.0 (major) instead of v1.8.0:**
1. Five new principles (XII–XVI) expand the constitution scope significantly.
2. Principle XVI (module boundaries) requires architectural refactoring that
   may break existing deployment assumptions.
3. The removal of the "known drift" exception in Principle III is a
   backward-incompatible constitutional change — code that was previously
   "in compliance with known exception" is now in violation.
4. New non-negotiable principle (XII: Safe Rule Deployment) imposes
   requirements on all future rule changes.

**Migration path:**
- v2.0.0: Document all amendments, fix P0 violations (V1, V2, V4, V7),
  extract Iceberg sink from assembler (V5).
- v2.1.0: Trace context propagation (V3), scoring job separation (V6),
  ML model integration (Principle XIV).
- v2.2.0: Event replay tooling (Principle XIII), metrics bridge retirement (DD-9).

---

## APPENDIX: CONSTITUTION v2.0.0 PRINCIPLE INDEX

| # | Principle | Status | NON-NEGOTIABLE |
|---|-----------|--------|----------------|
| I | Stream-First | AMENDED | YES |
| II | Sub-100ms Decision Budget | UNCHANGED | NO |
| III | Schema Contract Enforcement | AMENDED | YES |
| IV | Channel Isolation | UNCHANGED | NO |
| V | Defense in Depth | AMENDED | NO |
| VI | Immutable Event Log | AMENDED | NO |
| VII | PII Minimization at Edge | UNCHANGED | NO |
| VIII | Observability First-Class | AMENDED | NO |
| IX | Analytics-First Persistence | UNCHANGED | YES |
| X | Analytics Consumer Layer | UNCHANGED | NO |
| XI | Feature Serving Contract | AMENDED | YES |
| XII | Safe Rule Deployment | NEW | YES |
| XIII | Event Replay and Reprocessing | NEW | NO |
| XIV | ML Model Serving Independence | NEW | NO |
| XV | Horizontal Scaling and Partitioning | NEW | NO |
| XVI | Module Boundaries and Dependency Direction | NEW | NO |

| # | Design Decision | Status |
|---|-----------------|--------|
| DD-1 | PyFlink over Java/Bytewax | UNCHANGED |
| DD-2 | RocksDB incremental checkpoints | AMENDED |
| DD-3 | MapState minute buckets | UNCHANGED |
| DD-4 | Avro fastavro | UNCHANGED |
| DD-5 | acks=all idempotent | AMENDED |
| DD-6 | embedded MaxMind | UNCHANGED |
| DD-7 | YAML+Pydantic rules | UNCHANGED |
| DD-8 | dual-sink Kafka+PG (+Iceberg) | AMENDED |
| DD-9 | daemon consumer threads | AMENDED |
| DD-10 | Redis via Feast | UNCHANGED |
| DD-11 | DuckDB over Trino | UNCHANGED |
| DD-12 | Trace context propagation | NEW |
| DD-13 | Shadow scoring pipeline | NEW |
| DD-14 | IcebergSinkBase shared base | NEW |
