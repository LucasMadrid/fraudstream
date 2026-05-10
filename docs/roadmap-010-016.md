# FraudStream Roadmap — Specs 010–016

**Version**: 1.0.0
**Created**: 2026-05-09
**Constitution baseline**: v1.7.0
**Current state**: Specs 001–009 complete, version 1.7.0

---

## Executive Summary

This roadmap defines seven specs (010–016) across five phases, ordered by
production readiness risk. The sequencing reflects two constraints:

1. P0 findings from the last architecture review must be resolved before any
   new capability is added (Phase 1).
2. Architecture coupling issues (P0/P1) must be decoupled before ML or scale
   work can proceed safely (Phase 2).

Estimated total effort: 18–24 engineering weeks across all phases.

---

## Open Tech Debt Inventory (inputs to this roadmap)

| ID     | Title                                | Status           | Addressed by |
|--------|--------------------------------------|------------------|--------------|
| TD-001 | Schema Registry compat in CI         | CLOSED           | —            |
| TD-002 | No secrets manager                   | OPEN             | Spec 013     |
| TD-003 | No deploy jobs                       | OPEN             | Spec 014     |
| TD-004 | GeoIP partially closed               | PARTIALLY CLOSED | Spec 014     |
| TD-005 | No ingestion Dockerfile              | OPEN             | Spec 014     |

## P0 Findings (inputs to this roadmap)

| # | Finding                                              | Addressed by |
|---|------------------------------------------------------|--------------|
| 1 | Kafka no auth/encryption                             | Spec 013     |
| 2 | Iceberg sink baked into EnrichedRecordAssembler      | Spec 011     |
| 3 | Records silently dropped when Iceberg table=None     | Spec 010     |
| 4 | Scoring metrics not SafeMetric wrapped               | Spec 010     |
| 5 | AlertKafkaSink missing close()                       | Spec 010     |
| 6 | Reverse dependency processing→scoring/metrics        | Spec 011     |

## P1 Findings (inputs to this roadmap)

| # | Finding                                              | Addressed by |
|---|------------------------------------------------------|--------------|
| 1 | try/except ImportError should be adapter pattern     | Spec 011     |
| 2 | Duplicate config fields                              | Spec 011     |
| 3 | job_extension.py too large                           | Spec 011     |
| 4 | No Postgres reconnection                             | Spec 010     |
| 5 | ThreadPoolExecutor per flush                         | Spec 011     |
| 6 | Kafka metrics bridge thread safety                   | Spec 010     |

---

## Phase 1 — Safety & Correctness

**Goal**: Eliminate silent data loss, resource leaks, and metric blind spots.
No new features — only fixes to existing behaviour that violate constitution
principles.

**Entry criteria**: All specs 001–009 merged to main. CI green.
**Exit criteria**: Zero silent record drops. All scoring metrics SafeMetric-
wrapped. AlertKafkaSink lifecycle correct. Postgres sink reconnects. Kafka
metrics bridge thread-safe. All verified by regression tests.

**Estimated duration**: 2–3 weeks.

---

### Spec 010 — Hot Path Safety Hardening

**Title**: Hot Path Safety Hardening — DLQ routing, metric safety, sink
lifecycle, and reconnection

**Constitution principles addressed**:
- VIII (Observability) — SafeMetric wrapping ensures no silent counter loss
- VIII (Observability) — DLQ depth alert requires records actually reach DLQ
- IX (Analytics-First) — records must never be silently dropped
- VI (Immutable Event Log) — Postgres sink must maintain connection integrity

**Tech debt closed**: None directly (these are P0/P1 architecture review
findings, not tracked as TD items).

**Dependencies**: None. This is the first spec and has no predecessors beyond
009.

**Scope (from P0/P1 findings)**:
1. DLQ routing when Iceberg table=None — records currently silently dropped;
   must route to a DLQ topic with full envelope (original record + error
   reason + timestamp).
2. SafeMetric wrapping for all scoring metrics — any counter/histogram in the
   scoring path that is not wrapped in SafeMetric must be wrapped to prevent
   metric registration failures from crashing the hot path.
3. AlertKafkaSink.close() — implement proper producer flush and close in the
   sink lifecycle; add integration test proving no message loss on job
   shutdown.
4. Postgres AlertPostgresSink reconnection — implement retry with exponential
   backoff (max 3 retries, 1s/2s/4s) on connection loss; DLQ the alert if
   all retries fail.
5. Kafka metrics bridge thread safety — audit and fix shared state access in
   the daemon consumer threads that bridge metrics from Kafka to Prometheus.

**Complexity**: Medium (M). Touches 5 files across 3 packages. Each fix is
small but requires careful regression testing under failure conditions.

**Risk if deferred**: CRITICAL. Silent data loss in production. Scoring
metrics may show incorrect values. AlertKafkaSink leaks Kafka producers on
Flink recovery. Postgres connection loss causes unrecoverable sink failure.
Constitution §VIII and §IX violations.

---

## Phase 2 — Architecture Refactoring

**Goal**: Break coupling between pipeline stages, unify configuration, and
establish the adapter pattern. Makes the codebase safe for independent
evolution of processing, scoring, and analytics.

**Entry criteria**: Spec 010 merged. All P0 safety fixes verified.
**Exit criteria**: Iceberg sink is an independent operator. No reverse
dependency from processing→scoring. Config unified. job_extension.py split
into ≤3 focused modules. Adapter pattern replaces try/except ImportError.
ThreadPoolExecutor is singleton per sink. All verified by import graph
analysis and unit tests.

**Estimated duration**: 3–4 weeks.

---

### Spec 011 — Pipeline Architecture Decoupling

**Title**: Pipeline Architecture Decoupling — Sink extraction, dependency
inversion, config unification, and module decomposition

**Constitution principles addressed**:
- I (Stream-First) — Iceberg sink as independent operator follows stream
  composition model
- III (Schema Contract) — unified config reduces drift between processing and
  scoring schema expectations
- VIII (Observability) — metrics bridge refactoring ensures clean metric
  ownership per component
- IX (Analytics-First) — extracted Iceberg sink is independently testable and
  deployable

**Tech debt closed**: None directly (P0/P1 findings).

**Dependencies**: Spec 010 (DLQ routing must exist before sink extraction,
since the extracted sink needs DLQ as its error path).

**Scope (from P0/P1 findings)**:
1. Extract Iceberg sink from EnrichedRecordAssembler into a standalone
   `IcebergSinkOperator` with its own lifecycle, flush policy, and error
   handling. The assembler becomes a pure data transformer.
2. Fix reverse dependency: processing package must not import from scoring or
   metrics packages. Introduce a shared interface/protocol in a `contracts/`
   package that both processing and scoring depend on.
3. Unify duplicate config fields across processing and scoring into a single
   `PipelineConfig` Pydantic model with per-stage sections.
4. Replace try/except ImportError pattern with an explicit adapter registry
   (`AdapterRegistry`) that loads optional backends (Feast, Iceberg, GeoIP)
   via entry points or config-driven factory.
5. Split job_extension.py into ≤3 modules: `job_lifecycle.py` (start/stop),
   `job_metrics.py` (bridge setup), `job_sinks.py` (sink wiring).
6. ThreadPoolExecutor: use a singleton pool per sink type instead of creating
   a new pool per flush call. Pool size configurable via PipelineConfig.

**Complexity**: Large (L). Touches the core wiring of the Flink job. Requires
careful migration to avoid breaking the existing pipeline. Module extraction
must preserve all existing tests.

**Risk if deferred**: HIGH. The current coupling makes it impossible to test
the Iceberg sink independently, change scoring without risking processing,
or scale sink parallelism. Any future spec (ML decoupling, scale readiness)
will be significantly harder to implement on top of the current architecture.

---

## Phase 3 — ML Decoupling

**Goal**: Decouple ML model serving from the Flink pipeline so models can be
deployed, versioned, and rolled back independently. Enable shadow scoring
for safe rule/model changes.

**Entry criteria**: Spec 011 merged. Import graph clean. Adapter pattern in
place.
**Exit criteria**: ML inference callable via HTTP/gRPC sidecar. Shadow
scoring pipeline operational. Model version A/B comparison possible without
pipeline restart. Circuit breaker verified against sidecar failure.

**Estimated duration**: 4–5 weeks.

---

### Spec 012 — ML Model Serving Decoupling & Shadow Scoring

**Title**: ML Model Serving Decoupling — Sidecar inference, shadow scoring
pipeline, and model version management

**Constitution principles addressed**:
- II (Sub-100ms) — sidecar must meet <30ms inference budget slice
- V (Defense in Depth) — circuit breaker must work against sidecar; shadow
  scoring validates rule changes before promotion
- VIII (Observability) — shadow scoring emits comparison metrics (score
  delta, decision disagreement rate)
- XI (Feature Serving) — decoupled model can be served alongside feature
  store without coupling to Flink operator lifecycle

**Tech debt closed**: None directly (this is a new capability addressing
architecture gaps).

**Dependencies**: Spec 011 (adapter pattern needed for pluggable inference
backends; dependency inversion needed so scoring doesn't import processing).

**Scope**:
1. Define an `InferenceClient` protocol (ABC) with `predict(features) →
   MLScore` and `health() → bool`. Implement two backends:
   a. `LocalInferenceClient` — current in-process model (backward compat).
   b. `SidecarInferenceClient` — HTTP/gRPC call to a model-serving sidecar
      (e.g., Seldon, TorchServe, or a lightweight FastAPI wrapper).
2. Shadow scoring pipeline: duplicate the scoring flow in shadow mode where
   a second model version (or modified rule set) scores the same enriched
   record. Shadow results are written to a `txn.shadow.decisions` Kafka
   topic and an `iceberg.shadow_decisions` table. Shadow results NEVER
   influence the production decision.
3. Shadow comparison metrics: `shadow_score_delta_abs` histogram,
   `shadow_decision_disagreement_total` counter, dashboard page in Streamlit.
4. Model version management: model version tag is injected at sidecar deploy
   time, not compiled into the Flink job. The scoring output schema already
   carries `model_version`.
5. Circuit breaker update: verify the existing 3-failure/5s breaker works
   against the sidecar. Add integration test that kills the sidecar container
   and asserts rule-only decisions resume within the window.
6. Canary deployment for rules: before a rule change is promoted from shadow
   to production, require ≥1 hour of shadow scoring with <1% decision
   disagreement rate vs. current production rules. Documented in
   `docs/deployment/CANARY_POLICY.md`.

**Complexity**: Extra Large (XL). New infrastructure (sidecar), new Kafka
topic, new Iceberg table, new Streamlit page, and significant changes to the
scoring pipeline.

**Risk if deferred**: HIGH. Currently, any model or rule change requires a
full pipeline restart and goes live immediately with no validation. A bad
model version could degrade fraud detection for all traffic until manually
rolled back. Shadow scoring is industry standard for financial ML systems.

---

## Phase 4 — Scale Readiness & Security

**Goal**: Close all remaining tech debt required for production deployment.
Secure Kafka transport. Containerise ingestion. Implement deploy jobs.
Document partitioning strategy.

**Entry criteria**: Spec 012 merged. ML serving decoupled.
**Exit criteria**: Kafka TLS/SASL enabled. Secrets in Vault/equivalent.
Ingestion Dockerfile built and scanned. Deploy jobs functional for dev/
staging. Partitioning strategy documented and tested. TD-002, TD-003,
TD-004, TD-005 closed.

**Estimated duration**: 4–5 weeks (parallelisable: 013 and 014 can run
concurrently).

---

### Spec 013 — Transport Security & Secrets Management

**Title**: Transport Security & Secrets Management — Kafka TLS/SASL, DLQ PII
protection, API key enforcement, and secrets manager integration

**Constitution principles addressed**:
- VII (PII Minimization) — DLQ records must not leak unmasked PII in error
  envelopes
- VIII (Observability) — secure metrics endpoints (optional TLS for
  Prometheus scrape)
- III (Schema Contract) — Schema Registry must use authenticated access in
  non-dev environments
- Pre-production checklist: "Management API secured — MANAGEMENT_API_KEY set
  in all non-dev environments"

**Tech debt closed**:
- TD-002 (No secrets manager) — FULLY CLOSED
- TD-004 (GeoIP) — remaining cloud items closed (S3 upload, rotation via
  secrets manager)

**Dependencies**: Spec 010 (DLQ routing must exist to apply PII filtering on
DLQ envelopes). No dependency on 011 or 012.

**Scope**:
1. Kafka TLS/SASL configuration:
   a. Docker Compose: add `KAFKA_LISTENER_SECURITY_PROTOCOL_MAP` with
      `SASL_SSL` for inter-broker and client connections.
   b. All producers/consumers: configure `security.protocol=SASL_SSL`,
      `sasl.mechanism=PLAIN` (local) / `SCRAM-SHA-512` (cloud).
   c. Schema Registry: configure HTTPS + basic auth.
   d. Add TLS certificate generation to `make bootstrap` (self-signed for
      local dev).
2. Secrets manager integration:
   a. Abstract secret retrieval behind a `SecretProvider` protocol.
   b. Implement `EnvSecretProvider` (current behaviour, for local dev) and
      `VaultSecretProvider` (HashiCorp Vault, for staging/prod).
   c. Migrate: `MAXMIND_LICENCE_KEY`, Kafka credentials, Schema Registry
      credentials, `MANAGEMENT_API_KEY`, PostgreSQL credentials.
   d. Document rotation procedure for each secret.
3. DLQ PII protection:
   a. Before writing to any DLQ topic, apply the same PII masking
      (`pii_masker`) to the original record embedded in the DLQ envelope.
   b. Add contract test: deserialise a DLQ record and assert no full PAN or
      full IP is present.
4. Management API key enforcement:
   a. Enforce `MANAGEMENT_API_KEY` header check on all Management API
      endpoints when `ENV != dev`.
   b. Rate limiting: 100 req/min per API key.

**Complexity**: Large (L). TLS configuration touches every Kafka client in
the codebase. Secrets manager adds a new infrastructure dependency.

**Risk if deferred**: CRITICAL for production. Kafka without auth/encryption
is a regulatory blocker. No secrets manager means credentials in env vars
and Makefiles. DLQ records with unmasked PII are a data protection violation.

---

### Spec 014 — Deployment Pipeline & Containerisation

**Title**: Deployment Pipeline & Containerisation — Ingestion Dockerfile,
CI/CD deploy jobs, GeoIP cloud automation, and partitioning strategy

**Constitution principles addressed**:
- I (Stream-First) — deploy jobs ensure the streaming pipeline is the
  deployed artefact, not a manual process
- IV (Channel Isolation) — partitioning strategy documents how channels map
  to Kafka partitions and Flink parallelism
- VIII (Observability) — deploy jobs include observability gates (metric
  assertion after deploy)
- IX (Analytics-First) — Iceberg table partitioning strategy documented

**Tech debt closed**:
- TD-003 (No deploy jobs) — FULLY CLOSED
- TD-004 (GeoIP remaining items) — FULLY CLOSED (S3 upload, rolling
  TaskManager restart)
- TD-005 (No ingestion Dockerfile) — FULLY CLOSED

**Dependencies**: Spec 013 (deploy jobs need secrets manager for credentials;
Kafka TLS config must be in place before deploying to staging/prod).

**Scope**:
1. Ingestion Dockerfile:
   a. `infra/ingestion/Dockerfile` — Python 3.11 slim, non-root user,
      health-check endpoint at `/healthz`.
   b. CI job: build → Trivy scan → push to GHCR.
   c. Add to `ci-summary` required checks.
2. Deploy jobs (CI/CD):
   a. Restore `deploy-dev`, `deploy-staging`, `deploy-production` stages in
      `ci.yml`.
   b. Flink REST API job submission with env-specific parallelism.
   c. Smoke test: poll `GET /jobs` until status == RUNNING (timeout 60s).
   d. Observability gate: assert `enrichment_latency_ms` returns data within
      30s of startup.
   e. Production: trigger savepoint before new version submission.
3. GeoIP cloud automation:
   a. Upload mmdb to S3 (not workflow artifact).
   b. Rolling TaskManager restart procedure after DB update.
   c. Document in runbook.
4. Partitioning strategy document:
   a. Kafka: partition count per topic, key strategy (account_id for
      ordering guarantees), replication factor.
   b. Flink: parallelism per operator, key-by strategy alignment with Kafka
      partitions.
   c. Iceberg: partition by `event_timestamp` (daily) — already defined in
      constitution but not operationally documented.
   d. Redis: key distribution and memory sizing per namespace.

**Complexity**: Large (L). Deploy jobs require cloud infrastructure
provisioning. Partitioning strategy requires load testing to validate.

**Risk if deferred**: HIGH. Without deploy jobs, every deployment is manual
and error-prone. No ingestion Dockerfile means the ingestion service cannot
be deployed to Kubernetes. GeoIP staleness in production is a compliance
risk. Undocumented partitioning leads to hot partitions under load.

---

## Phase 5 — UX & Analytics Enhancements

**Goal**: Improve analyst experience, add distributed tracing, feature
staleness monitoring, and event replay capability. These are important but
not production blockers.

**Entry criteria**: Specs 013 and 014 merged. Production deploy pipeline
functional.
**Exit criteria**: Distributed tracing operational. Feature staleness
monitored. Event replay tested. Streamlit concurrent access addressed.

**Estimated duration**: 5–6 weeks (specs 015 and 016 can run concurrently).

---

### Spec 015 — Observability & Replay Infrastructure

**Title**: Observability & Replay Infrastructure — Distributed tracing,
feature staleness monitoring, and event replay pipeline

**Constitution principles addressed**:
- VIII (Observability) — distributed tracing is explicitly required: "Traces:
  distributed trace spanning ingestion → enrichment → scoring → decision
  (OpenTelemetry)"
- XI (Feature Serving) — feature staleness bound of 30s requires active
  monitoring beyond the existing alert
- VI (Immutable Event Log) — event replay leverages the append-only Iceberg
  store for debugging and model retraining

**Tech debt closed**: None (new capabilities).

**Dependencies**: Spec 011 (adapter pattern for pluggable tracing backend),
Spec 013 (secure transport for trace export).

**Scope**:
1. Distributed tracing (OpenTelemetry):
   a. Add `opentelemetry-api` + `opentelemetry-sdk` to dependencies.
   b. Instrument: ingestion producer → Kafka header propagation → Flink
      enrichment → scoring → decision sink.
   c. Trace context carried via Kafka headers (`traceparent`).
   d. Export to Jaeger (local Docker) / cloud collector (staging/prod).
   e. Add trace ID to structured log fields.
2. Feature staleness monitoring:
   a. Beyond the existing `feature_materialization_lag_ms` alert, add
      per-feature-view staleness tracking.
   b. Dashboard: Streamlit page showing feature freshness per entity.
   c. Alert: per-feature-view staleness exceeding 30s fires independently.
   d. Runbook: what to do when a specific feature view goes stale.
3. Event replay pipeline:
   a. CLI tool: `fraudstream replay --from <timestamp> --to <timestamp>
      --topic txn.enriched --dest txn.replay.enriched`.
   b. Reads from Iceberg tables (not Kafka — per constitution §IX, Iceberg
      is the analytics source of truth).
   c. Replayed events published to a separate Kafka topic (never the
      production topic).
   d. Use case: model retraining, debugging, and post-mortem analysis.
   e. Replay rate limiting: configurable TPS cap to avoid overwhelming
      downstream consumers.

**Complexity**: Extra Large (XL). OpenTelemetry instrumentation touches every
pipeline component. Event replay requires careful Iceberg scan and Kafka
producer orchestration.

**Risk if deferred**: MEDIUM. No distributed tracing means debugging
cross-component latency issues requires manual log correlation. Feature
staleness monitoring is partially covered by existing alerts. Event replay
is useful but not blocking for production.

---

### Spec 016 — Analytics UX & Rule Management UI

**Title**: Analytics UX — Streamlit concurrency, rule management UI, and
Redis load validation

**Constitution principles addressed**:
- X (Analytics Consumer Layer) — Streamlit v2 responsibilities: CRUD for
  rules, test harness, audit log
- V (Defense in Depth) — rule test harness validates rule changes before
  promotion
- XI (Feature Serving) — Redis load test validates p99 < 2ms under expected
  peak
- VIII (Observability) — DLQ trend analysis dashboard

**Tech debt closed**: None (new capabilities).

**Dependencies**: Spec 012 (shadow scoring needed for rule canary
deployment), Spec 015 (tracing integration for replay-based rule testing).

**Scope**:
1. Streamlit concurrency:
   a. Evaluate and implement session isolation for multiple concurrent
      analysts (Streamlit's single-threaded model).
   b. Options: per-session DuckDB connections, connection pooling, or
      Streamlit's native session state with locking.
   c. Load test: 5 concurrent analysts querying 7-day windows without
      degradation.
2. Rule management UI (constitution §X v2 scope):
   a. CRUD interface for rule definitions (thresholds, conditions,
      enabled/disabled) in Streamlit.
   b. Rule test harness: submit a synthetic transaction, show which rules
      fire.
   c. Rule change audit log: who changed what, when, with before/after diff.
   d. Changes written to `txn.rules.config` Kafka topic (compacted). Streamlit
      MUST NOT write directly to any store the scoring engine reads.
3. Redis load validation:
   a. Load test Redis under 2× expected peak (10,000 TPS × 2 = 20,000
      feature lookups/sec).
   b. Validate p99 < 2ms for feature reads.
   c. Validate memory sizing for both hot store and Feast namespaces.
   d. Document results and capacity planning in `docs/redis-load-test.md`.
4. DLQ trend analysis dashboard:
   a. Implement the architecture described in `docs/dlq-trend-analysis-
      architecture.md`.
   b. Streamlit page: DLQ volume over time, error type breakdown, top
      failing schemas.

**Complexity**: Large (L). Rule management UI is a significant frontend
feature. Redis load testing requires infrastructure setup.

**Risk if deferred**: MEDIUM. Streamlit single-threaded model limits analyst
productivity but doesn't block production. Rule management UI is a v2
feature per constitution. Redis load validation should happen before
production but can be done as an operational task without a full spec.

---

## Dependency Graph

```
Phase 1          Phase 2          Phase 3          Phase 4          Phase 5
                                                  ┌──────┐
                                                  │ 013  │──┐
                                                  │Security│  │
                                                  └──┬───┘  │
                                                     │      │
┌──────┐        ┌──────┐        ┌──────┐          ┌──┴───┐  │   ┌──────┐
│ 010  │───────▶│ 011  │───────▶│ 012  │─────────▶│ 014  │  │──▶│ 015  │
│Safety│        │Decouple│      │ML Svc│          │Deploy │  │   │Trace │
└──────┘        └──────┘        └──────┘          └──────┘  │   └──────┘
                                    │                       │       │
                                    │                       │   ┌──────┐
                                    └───────────────────────┴──▶│ 016  │
                                                                │UX/UI │
                                                                └──────┘
```

Parallelism opportunities:
- Spec 013 can start as soon as Spec 010 is done (no dependency on 011).
- Specs 015 and 016 can run concurrently once their dependencies are met.
- If two engineers are available, 013 and 011 can run in parallel after 010.

---

## Summary Table

| Spec | Title                              | Phase | Complexity | Constitution    | TD Closed       | Depends on | Risk if Deferred |
|------|------------------------------------|-------|------------|-----------------|-----------------|------------|------------------|
| 010  | Hot Path Safety Hardening          | 1     | M          | VIII, IX, VI    | —               | —          | CRITICAL         |
| 011  | Pipeline Architecture Decoupling   | 2     | L          | I, III, VIII,IX | —               | 010        | HIGH             |
| 012  | ML Serving Decoupling & Shadow     | 3     | XL         | II, V, VIII, XI | —               | 011        | HIGH             |
| 013  | Transport Security & Secrets       | 4     | L          | VII, VIII, III  | TD-002, TD-004  | 010        | CRITICAL         |
| 014  | Deployment Pipeline & Containers   | 4     | L          | I, IV, VIII, IX | TD-003/4/5      | 013        | HIGH             |
| 015  | Observability & Replay             | 5     | XL         | VIII, XI, VI    | —               | 011, 013   | MEDIUM           |
| 016  | Analytics UX & Rule Mgmt UI       | 5     | L          | X, V, XI, VIII  | —               | 012, 015   | MEDIUM           |

---

## Pre-Production Checklist Coverage

The following pre-production checklist items from the constitution are NOT
yet covered by specs 001–009 and ARE addressed by this roadmap:

| Checklist item                                    | Addressed by |
|---------------------------------------------------|--------------|
| DLQ — no event silently dropped                   | Spec 010     |
| Management API secured                            | Spec 013     |
| Analytics sink verified (row count match)         | Spec 011     |
| Online feature store read SLO < 2ms under load    | Spec 016     |
| Feature staleness alert verified                  | Spec 015     |
| Production Redis separation                       | Spec 016     |
| Latency budget test under 2× peak load            | Spec 014     |
| Model versioning + instant rollback               | Spec 012     |

---

## Version History

| Version | Date       | Author        | Change                |
|---------|------------|---------------|-----------------------|
| 1.0.0   | 2026-05-09 | Planner Agent | Initial roadmap draft |
