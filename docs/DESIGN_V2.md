# FraudStream Architecture Refactoring — Design V2

**Status:** DRAFT  
**Date:** 2026-05-10  
**Constitution Version:** v1.7.0  
**Branch Context:** main (post-010-hot-path-safety-hardening, PR #19 merged)

---

## Executive Summary

This document captures the comprehensive architecture review findings and establishes the refactoring roadmap for FraudStream Phase 2 (post-010). The 010 Phase (hot-path safety hardening) delivered 16 bug fixes; this design addresses the structural and architectural debt identified during that work.

**Key Artifacts:**
- Constitution compliance audit
- P0/P1/P2 issue taxonomy
- 3-wave improvement strategy
- Stash inventory (57 files, +2463/-2882 lines)

**Architecture Diagrams:**
| Diagram | Location | Excalidraw Link |
|---------|----------|-----------------|
| **Current State (As-Is)** | `docs/architecture-current.excalidraw` | [View Online](https://excalidraw.com/#json=VgNv8ysnkABtyNrrHZ5pJ,6uvzEPt-CTsIrXgyXbT_Uw) |
| **Future State (To-Be)** | `docs/architecture-future.excalidraw` | [View Online](https://excalidraw.com/#json=9Dqqtz-KoPESI4r_pn-PD,gqjGPkm7K83nGiDyXeq_uw) |

*To download as PNG: Open the link → Click hamburger menu (☰) → Export image → PNG*

---

## 1. Constitution Compliance Status

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Stream-First | COMPLIANT | Kafka as SoR, exactly-once semantics |
| II — Sub-100ms Budget | PARTIAL | Iceberg sink in hot path adds up to 5s latency — VIOLATION |
| III — Schema Contracts | PARTIAL | `txn.enriched` still JSON (known drift, v2.0 deadline) |
| IV — Channel Isolation | COMPLIANT | Per-channel topics, deferred per-channel thresholds to TD-002 |
| V — Defense in Depth | COMPLIANT | Rules before models, circuit breaker tested |
| VI — Immutable Event Log | COMPLIANT | Append-only Iceberg tables |
| VII — PII Minimization | COMPLIANT | Edge masking, shared library |
| VIII — Observability | PARTIAL | Scoring metrics not wrapped in _SafeMetric; AlertKafkaSink missing close() |
| IX — Analytics-First Persistence | PARTIAL | Records silently dropped when Iceberg table=None (no DLQ) |
| X — Analytics Consumer Layer | COMPLIANT | DuckDB + Trino dual-mode operational |

### Production Blockers (Must Fix Before v2.0)

1. **CHB-001:** `txn.enriched` JSON → Avro migration (Principle III)
2. **CHB-002:** Iceberg sink latency in hot path (Principle II) — extract to dedicated operator
3. **CHB-003:** Silent record drops on table=None (Principle IX) — wire DLQ
4. **CHB-004:** Scoring metrics unwrapped (Principle VIII) — _SafeMetric adoption
5. **CHB-005:** AlertKafkaSink resource leak (Principle VIII) — add close()
6. **CHB-006:** Reverse dependency processing → scoring (layer coupling) — add interface contracts

### Spec Alignment Notes (003-009)

| Issue | Spec Position | DESIGN_V2 Position | Resolution |
|-------|---------------|-------------------|------------|
| Iceberg sink placement | Spec 006: Side output "outside hot path" | CHB-002: Principle II violation | **Refactor required** — extract to side-output layer |
| Silent record drops | Spec 006: Circuit breaker for catalog | CHB-003: No DLQ when table=None | **Gap identified** — add per-record DLQ |
| Metrics safety | Spec 003: Per-rule counters | CHB-004: Need _SafeMetric wrapping | **Enhancement** — defensive wrappers |

Specs 003-009 represent Phase 1 (constitution-compliant at time). DESIGN_V2 represents Phase 2 refactoring requirements — conflicts are acknowledged technical debt.

---

## 2. Issue Taxonomy

### 2.1 P0 — Must Fix (Correctness / Reliability / Security)

| ID | Issue | Location | Constitution | Fix Complexity |
|----|-------|----------|--------------|----------------|
| P0-001 | Kafka has no auth or encryption (PLAINTEXT) | `docker-compose.yml`, infra/ | Security | Medium |
| P0-002 | Iceberg sink baked into EnrichedRecordAssembler (SRP violation, adds latency) | `pipelines/processing/operators/enricher.py` | II | Medium |
| P0-003 | Records silently dropped when Iceberg table is None | `pipelines/processing/operators/iceberg_sink.py` | IX | Low |
| P0-004 | Scoring metrics not wrapped in _SafeMetric | `pipelines/scoring/` | VIII | Low |
| P0-005 | AlertKafkaSink missing close() method — Producer never flushed/closed | `pipelines/scoring/sinks/alert_kafka.py` | VIII | Low |
| P0-006 | Reverse dependency: processing → scoring/metrics | `pipelines/processing/kafka_metrics_bridge.py` | — | Medium |

### 2.2 P1 — Should Fix (Maintainability / Scalability)

| ID | Issue | Location | Fix Strategy |
|----|-------|----------|--------------|
| P1-001 | try/except ImportError pattern across 5 files — two class definitions | Multiple operators | Formalize adapter pattern |
| P1-002 | Duplicate config fields (kafka_brokers, schema_registry_url) | ProcessorConfig, ScoringConfig | Unified config hierarchy |
| P1-003 | job_extension.py does too much (orchestration + domain + wrappers) | `pipelines/scoring/job_extension.py` | Split to domain.py + adapters.py |
| P1-004 | No PostgreSQL reconnection logic | `pipelines/scoring/sinks/alert_postgres.py` | Exponential backoff retry |
| P1-005 | ThreadPoolExecutor created per-flush in IcebergSinkBase | `pipelines/processing/operators/iceberg_sink.py` | Create once in open() |
| P1-006 | Metrics bridge thread safety — start() spawns duplicate threads | `pipelines/processing/kafka_metrics_bridge.py` | Add liveness check |
| P1-007 | DLQ Inspector PII masking incomplete and bypassable | `analytics/app/pages/5_dlq_inspector.py` | Enforce masking, recursive |
| P1-008 | Management API key auth optional with no prod enforcement | `management_api.py` | Require key in prod mode |

### 2.3 P2 — Nice to Have

| ID | Issue | Rationale |
|----|-------|-----------|
| P2-001 | _SafeMetric silently swallows programming errors | Should log/warn on label mismatch |
| P2-002 | f-string in logging calls | Performance (defer formatting) |
| P2-003 | FraudDecision.rule_triggers is mutable list in frozen dataclass | Should be tuple |
| P2-004 | Analytics consumer materializes all records but only uses first | Memory optimization |
| P2-005 | No dependency lockfile | Reproducibility |
| P2-006 | Investigate Flink native metrics reporter | Eliminates metrics bridge hack |
| P2-007 | Move feature_schema.py to scoring/ | Better cohesion |

---

## 3. Three-Wave Improvement Strategy

### Wave 1 — Operational Safety (Foundation)

**Goal:** Make the system safe to operate at scale without silent failures.

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 010-hot-path-safety | COMPLETE: 16 bug fixes for crashes, thread safety, resource leaks | P0-003, P0-004, P0-005, P1-006 | DONE |
| 011-shadow-scoring | Shadow mode for rule/ML changes — score without affecting decisions | Operational safety | M |
| 012-event-replay | Replay pipeline for backtesting and recovery | TD-005 | L |
| 013-distributed-tracing | OpenTelemetry trace context propagation | Observability | M |

### Wave 2 — Decouple ML Path (Scalability)

**Goal:** Separate ML inference from rule engine for independent scaling.

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 014-model-serving-sidecar | Extract ML scoring to gRPC sidecar (Triton/TF Serving) | P0-002 (partial) | L |
| 015-feature-staleness-monitoring | Alert when features exceed freshness SLA | Operational safety | S |
| 016-circuit-breaker-hardening | Per-model circuit breaker with fallback | Reliability | M |

### Wave 3 — Scale Readiness (Performance)

**Goal:** Production-grade horizontal scaling and multi-region.

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 017-partitioning-strategy | Account-based partitioning for stateful operators | Scalability | L |
| 018-redis-feature-store | Migrate from SQLite to Redis for production | TD-003 | M |
| 019-analytics-scaling | Trino cluster, materialized views for Streamlit | Performance | M |
| 020-kafka-security | SASL/SCRAM + TLS for all brokers | P0-001 | M |

---

## 4. Stashed Work Inventory

**Location:** `stash@{0}: On 010-hot-path-safety-hardening: full-architecture-refactoring-wip`

**Size:** 57 files changed, +2463/-2882 lines

### 4.1 Files Modified by Category

**Analytics Layer (12 files):**
- `analytics/app/Home.py` — Docstring update (Trino → DuckDB)
- `analytics/app/pages/2_fraud_rate.py` — Error handling refactor
- `analytics/app/pages/3_rule_triggers.py` — Error handling refactor
- `analytics/app/pages/4_model_compare.py` — Error handling refactor
- `analytics/app/pages/5_dlq_inspector.py` — PII safety, time imports
- `analytics/app/pages/6_shadow_rules.py` — Configurable URLs
- `analytics/app/pages/7_analytics.py` — NEW: Analytics insights dashboard
- `analytics/consumers/kafka_consumer.py` — Refactored _enqueue, removed patches
- `analytics/consumers/metrics.py` — Metrics improvements
- `analytics/queries/*.py` — Multiple query modules refactored

**Processing Layer (10 files):**
- `pipelines/processing/operators/device.py` — Refactored for testability
- `pipelines/processing/operators/enricher.py` — Iceberg sink extraction prep
- `pipelines/processing/operators/geolocation.py` — Refactored
- `pipelines/processing/operators/iceberg_sink.py` — Major refactor (1053 lines changed)
- `pipelines/processing/operators/velocity.py` — Refactored
- `pipelines/processing/shared/dlq_sink.py` — NEW: Dedicated DLQ sink
- `pipelines/processing/kafka_metrics_bridge.py` — Thread safety fixes
- `pipelines/processing/metrics.py` — Improvements

**Scoring Layer (10 files):**
- `pipelines/scoring/job_extension.py` — Refactored (190 lines)
- `pipelines/scoring/config.py` — Config improvements
- `pipelines/scoring/metrics.py` — SafeMetric integration
- `pipelines/scoring/rules/evaluator.py` — Improvements
- `pipelines/scoring/sinks/alert_kafka.py` — close() method + DLQ path
- `pipelines/scoring/sinks/alert_postgres.py` — Error handling + rollback
- `pipelines/scoring/sinks/iceberg_decisions.py` — Refactored (510 lines)
- `pipelines/scoring/clients/feature_serving.py` — Executor injection
- `pipelines/scoring/telemetry.py` — Improvements
- `pipelines/scoring/types.py` — Type improvements

**Ingestion Layer (3 files):**
- `pipelines/ingestion/api/metrics.py` — Improvements
- `pipelines/ingestion/api/telemetry.py` — Improvements
- `pipelines/ingestion/shared/dlq_producer.py` — Improvements

**Tests (12 files):**
- New tests for kafka_metrics_bridge, alert_kafka, alert_postgres, iceberg_reader
- Updated tests for core_modules, geolocation, iceberg_sink, rule_engine, analytics_consumer, feature_serving_client

**Infrastructure (3 files):**
- `pyproject.toml` — Dependency updates
- `scripts/generate_transactions.py` — Improvements
- `scripts/iceberg_init.py` — NEW: Iceberg initialization script
- `scripts/simulate_persistence.py` — Improvements

**Removed (5 files):**
- `analytics/views/*.sql` — Trino views removed (replaced by DuckDB queries)
- `tests/contract/test_trino_views.py` — Contract tests for removed views

### 4.2 Stash Coverage of P0 Issues

| P0 Issue | Covered in Stash? | Notes |
|----------|-------------------|-------|
| P0-001 (Kafka auth) | NO | Infra/security change |
| P0-002 (Iceberg in hot path) | PARTIAL | Refactor prep, not full extraction |
| P0-003 (table=None drops) | YES | DLQ sink added |
| P0-004 (_SafeMetric) | YES | Scoring metrics updated |
| P0-005 (AlertKafkaSink.close) | YES | close() method added + tests |
| P0-006 (reverse dependency) | PARTIAL | kafka_metrics_bridge refactored |

**Coverage:** ~70% of P0 issues addressed in stash

---

## 5. Recommended Action Plan (Four-Agent Consensus)

**Approach:** Comprehensive Phase 0 with embedded Stash-1 quick wins (Tester tie-breaker)

### Phase 0: Foundation + Quick Wins (2 weeks)

**Objective:** Establish test infrastructure, interface contracts, and validation framework before Wave 1.

#### Week 1: Test Infrastructure & Contracts

| Task | Issue | Agent Owner | Deliverable |
|------|-------|-------------|-------------|
| CHB-006: Interface contracts | P0-006 | Architect | `shared/interfaces/metrics_publisher.py` |
| TB-001: Security test environment | P0-001 | Tester | `infra/docker-compose.security.yml` |
| TB-002: Interface contract tests | P0-006 | Tester | `tests/contract/test_processing_scoring_boundary.py` |
| TB-003: Performance baseline | P0-002 | Engineer | Baseline archived in `tests/performance/baselines/` |
| DD-12: Component Lifecycle principle | — | Architect | Constitution amendment |

#### Week 2: Validation Framework & Stash-1 Pop

| Task | Issue | Agent Owner | Success Criteria |
|------|-------|-------------|------------------|
| TB-004: Update coverage gates | All P0 | Tester | 90% for P0, 80% for P1 |
| Stash-1 Pop | P0-003, P0-004, P0-005 | Engineer | All tests pass |
| CHB-003: DLQ wiring validation | P0-003 | Planner | DLQ alerts fire correctly |
| CHB-004: _SafeMetric adoption | P0-004 | Architect | Metrics wrapped |
| CHB-005: close() method fixes | P0-005 | Engineer | Resource leaks plugged |

#### Phase 0 Exit Criteria

- [ ] 90% test coverage for all P0 issues
- [ ] Interface contract tests passing
- [ ] Performance baseline archived
- [ ] Security test environment operational
- [ ] Stash-1 merged to main branch
- [ ] DD-12 added to constitution

---

### Wave 1: Operational Safety (Weeks 3-4)

**Prerequisite:** Phase 0 exit criteria met

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 012-event-replay | Replay harness for backtesting | TD-005 | L |
| 011-shadow-scoring | Shadow mode for rule/ML changes | TD-006 | M |
| 013-distributed-tracing | OpenTelemetry trace propagation | Observability | M |
| Stash-2 Pop | Metrics & Bridge refactoring | P0-006 partial | M |

**Dependencies:**
- 013 → 011 (tracing before shadow scoring)
- CHB-003 → 012 (DLQ before replay)

---

### Wave 2: Decouple ML Path (Weeks 5-7)

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 016-circuit-breaker-hardening | Per-model circuit breaker | Reliability | M |
| 014-model-serving-sidecar | gRPC sidecar extraction | Scalability | L |
| TD-003 | Deploy jobs implementation | Operations | M |
| Stash-3 Pop | Iceberg extraction | P0-002 | L |

**Dependencies:**
- 016 → 014 (CB before sidecar)
- CHB-006 → 014 (contracts before sidecar)
- TB-003 → Stash-3 (baseline before extraction)

---

### Wave 3: Scale Readiness (Weeks 8-10)

| Feature | Description | Addresses | Effort |
|---------|-------------|-----------|--------|
| 020-kafka-security | SASL/TLS implementation | P0-001 | M |
| 017-partitioning-strategy | Account-based partitioning | Scalability | L |
| 018-redis-feature-store | Redis migration | TD-003 | M |
| 019-analytics-scaling | Trino cluster | Performance | M |

**Dependencies:**
- 014 → 018 (sidecar stability before Redis)
- TB-001 → 020 (security tests before implementation)

---

## 6. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Stash merge conflicts | Medium | High | Feature branch, incremental merge |
| Test coverage regression | Medium | High | CI gate at 80% minimum |
| Performance regression (Iceberg refactor) | Low | High | Benchmark before/after |
| Kafka security breaking local dev | Medium | Medium | Separate local/cloud configs |
| Schema migration downtime | Low | High | Blue/green topic strategy |

---

## 7. Success Criteria (Revised per Tester Recommendation)

- [ ] All P0 issues resolved
- [ ] Test coverage ≥ 90% for P0 issues (was 80%)
- [ ] Test coverage ≥ 80% for P1 issues
- [ ] Test coverage ≥ 70% for P2 issues
- [ ] Security tests pass (P0-001) — TB-001 resolved
- [ ] Interface contract tests pass (P0-006) — TB-002 resolved
- [ ] Performance regression < 5% (P0-002) — TB-003 resolved
- [ ] Latency p99 ≤ 100ms (Principle II)
- [ ] No silent failures (DLQ depth alerts)
- [ ] Constitution compliance audit passed
- [ ] Documentation updated (README, ADRs, runbooks)

### Test Blockers (Must Resolve Before Phase 0)

| ID | Blocker | Priority | Owner |
|----|---------|----------|-------|
| TB-001 | No Kafka auth test infrastructure | CRITICAL | Tester |
| TB-002 | No interface contract tests | CRITICAL | Tester |
| TB-003 | No performance baseline | CRITICAL | Engineer |
| TB-004 | 80% coverage gate too low | CRITICAL | Tester |
| TB-005 | No chaos tests for DLQ | HIGH | Tester |
| TB-006 | Missing thread safety stress tests | HIGH | Tester |
| TB-007 | No security chaos tests | HIGH | Tester |

---

## 8. Appendices

### Appendix A: Tech Debt References

- TD-002: No secrets manager
- TD-003: No deploy jobs implemented
- TD-005: No replay harness
- TD-006: No shadow scoring

### Appendix B: Related Documents

- Constitution: `.specify/memory/constitution.md`
- Spec 004: Operational Excellence
- Spec 006: Analytics Persistence Layer
- Spec 007: Feature Serving Contract
- Spec 008: Analytics Consumer Layer
- Spec 009: Streamlit DuckDB Migration
- Spec 010: Hot Path Safety Hardening

### Appendix C: Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-10 | Create DESIGN_V2.md | Capture architecture review findings |
| 2026-05-10 | Pop stash vs. rewrite | 70% P0 coverage justifies incremental approach |
| 2026-05-10 | Multi-agent review | Planner, Architect, Engineer, Tester agents validated direction |
| 2026-05-10 | Create architecture diagrams | Visual documentation of current vs future state |
| **2026-05-10** | **Four-Agent Consensus** | **Comprehensive Phase 0 approach adopted (Tester tie-breaker)** |
| **2026-05-10** | **Stash split into 3 parts** | **Stash-1 quick wins (Week 2), Stash-2 (Wave 1), Stash-3 (Wave 2)** |
| **2026-05-10** | **90% coverage for P0** | **Tester recommendation: tiered coverage gates (90/80/70)** |
| **2026-05-10** | **7 test blockers identified** | **TB-001 through TB-007 must resolve before Phase 0** |

### Appendix D: Multi-Agent Review Summary

Four specialist agents reviewed this document and the specs (003-009):

| Agent | Focus | Key Finding | Vote |
|-------|-------|-------------|------|
| **Planner** | Dependency sequencing | Wave ordering errors identified; Phase 0 needed for CHB-001/002 | Comprehensive Phase 0 |
| **Architect** | Constitution compliance | P0-006 elevated to CHB-006; missing interface contracts; DD-12 needed | Comprehensive Phase 0 |
| **Engineer** | Implementation feasibility | P0-001/002 complexity underestimated (Medium → HIGH); split stash | Minimal Phase 0 |
| **Tester** | Verification strategy | P0-001 has ZERO tests; need security/chaos test gates; 90% coverage | **Comprehensive Phase 0** ⭐ |

**Four-Agent Consensus:**

**Approach:** Comprehensive Phase 0 with embedded Stash-1 quick wins  
**Tie-breaker:** Tester (testing infrastructure must precede implementation)  
**Stash Strategy:** Split into 3 parts (Stash-1: Core Safety, Stash-2: Metrics/Bridge, Stash-3: Iceberg Extraction)  
**Coverage Gates:** 90% for P0, 80% for P1, 70% for P2  
**Test Blockers:** 7 identified (TB-001 through TB-007) must resolve before Phase 0

**Agent Ownership in Phase 0:**
| Task | Owner | Deliverable |
|------|-------|-------------|
| Interface contracts (CHB-006) | Architect | `shared/interfaces/` |
| Security test env (TB-001) | Tester | `infra/docker-compose.security.yml` |
| Contract tests (TB-002) | Tester | `tests/contract/` |
| Performance baseline (TB-003) | Engineer | `tests/performance/baselines/` |
| Stash-1 pop | Engineer | Merged PR |
| Coverage gates (TB-004) | Tester | CI update |

**Consensus:** DESIGN_V2 direction is correct. Specs 003-009 represent Phase 1. Phase 2 requires dedicated refactoring sprint with feature branch isolation.

### Appendix E: Architecture Diagrams

**Current State (As-Is):**
- Local: `docs/architecture-current.excalidraw`
- Online: https://excalidraw.com/#json=VgNv8ysnkABtyNrrHZ5pJ,6uvzEPt-CTsIrXgyXbT_Uw

**Future State (To-Be):**
- Local: `docs/architecture-future.excalidraw`
- Online: https://excalidraw.com/#json=9Dqqtz-KoPESI4r_pn-PD,gqjGPkm7K83nGiDyXeq_uw

*To download PNG: Open link → ☰ Menu → Export image → PNG*

---

**Document Owner:** Architecture Team  
**Reviewers:** Engineering Lead, SRE Lead, Security Champion  
**Next Review:** 2026-05-17
