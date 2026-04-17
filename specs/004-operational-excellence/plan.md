# Implementation Plan: Operational Excellence — Architecture Gaps & Partials

**Branch**: `004-operational-excellence` | **Date**: 2026-04-12 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/004-operational-excellence/spec.md`

---

## Summary

Close the eight operational excellence gaps identified in the AWS Well-Architected OE review: CI/CD schema enforcement (BACKWARD_TRANSITIVE), ML circuit breaker, shadow rule mode, Redis baseline, scoring telemetry, runbooks (4), chaos test plan, post-mortem process, canary policy, PII audit job, ADRs (4), and supporting Grafana/Prometheus wiring. The approach is prerequisite-ordered: infra gaps (FR-025/026/027) first, then behavioral features (FR-014/016/022), then documentation (FR-007–010/017/020–024).

---

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**:
- Apache Flink 1.20.3 (PyFlink) — stream processing engine
- Apache Kafka (Confluent 7.9.6) + Confluent Schema Registry — message backbone
- `pybreaker` 1.x — circuit breaker for ML serving calls (FR-014)
- Pydantic v2 + `ConfigDict(extra="forbid")` — rule definition models (must extend for `mode` field)
- Prometheus (existing) + Grafana (existing) — observability
- OpenTelemetry Python SDK — distributed tracing (exists in ingestion + processing; missing in scoring)
- GitHub Actions — CI/CD (6 existing stages; BACKWARD_TRANSITIVE fix is pending — T013 closes this gap; do not treat as already complete)
- Terraform — IaC for Kafka topics, Redis, Schema Registry subjects
- Redis 7 (alpine) — feature cache (absent from docker-compose; FR-026)
- PostgreSQL 15 — operational fraud_alerts store (existing)
- Apache Iceberg + MinIO — event store (declared in constitution; sinks NOT yet implemented — TD-007)
- Streamlit — analytics UI (Principle X; shadow rule and DLQ inspector pages)

**Storage**:
- Kafka topics: event backbone (source of truth for raw events)
- Apache Iceberg/MinIO: `iceberg.enriched_transactions`, `iceberg.fraud_decisions` — **blocked by TD-007**
- PostgreSQL: `fraud_alerts` table (operational mutable state only; schema in `infra/postgres/migrations/`)
- Redis: feature cache, TTL 24h — **not yet in docker-compose (TD-006)**

**Testing**: pytest + pytest-cov; `--cov-fail-under=80` gate in CI  
**Target Platform**: Docker Compose (local dev), AWS managed services (Amazon Managed Flink, MSK, ElastiCache) or GCP managed services (Dataflow, Confluent/Pub/Sub, Memorystore) — cloud provider modules deferred to TD-010  
**Project Type**: Streaming fraud detection pipeline service  
**Performance Goals**: p99 < 100ms end-to-end; ingestion < 10ms / enrichment < 20ms / Redis < 5ms / ML inference < 30ms / decision < 15ms  
**Constraints**:
- Schema BACKWARD_TRANSITIVE compat on all Avro subjects
- PII masked at producer edge (Principle VII)
- Iceberg tables append-only (Principle VI)
- Circuit breaker fast-fail must be O(1) — no I/O in OPEN state (Principle II)
- ML client probe timeout: exactly 5ms (FR-014 edge case requirement)

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Stream-First | ✅ PASS | All OE changes operate on Kafka streams; no batch paths introduced |
| II. Sub-100ms Budget | ⚠️ WATCH | Circuit breaker OPEN path must be O(1). pybreaker probe timeout in Python may not reliably hit 5ms — see Research Q1. OTel span overhead on BLOCK decisions must be verified < 1ms. |
| III. Schema Contract | ✅ PASS | CI now enforces BACKWARD_TRANSITIVE (fixed in ci.yml). `RuleDefinition` extension for `mode` field requires backward-compatible schema change (default value required). |
| IV. Channel Isolation | ✅ PASS | No OE changes alter channel topology. FR-022 canary uses `account_id` hash, which is correct. |
| V. Rules Before Models | ✅ PASS | FR-014 circuit breaker enforces this at runtime — OPEN state = rule-only decisions. MLModelClient is strictly additive. |
| VI. Immutable Event Log | ✅ PASS | FR-005 PII audit is read-only. FR-018 DLQ analysis is read-only. No raw event table mutation. |
| VII. PII Minimization | ✅ PASS | FR-005 enforces detection. FR-006 PR template includes PII checklist gate. |
| VIII. Observability | ✅ PASS | FR-011–013 address all gaps: trace sampling, Grafana dashboard, late event metrics. |
| IX. Analytics Persistence | ⚠️ BLOCKED | **TD-007**: Iceberg sinks not implemented. FR-005 (PII audit) and FR-019 (model feedback) require `iceberg.enriched_transactions` and `iceberg.fraud_decisions`. These FRs are blocked until a separate Iceberg sink spec is written and implemented. FR-005 must be sequenced AFTER the Iceberg sink work. |
| X. Analytics Consumer | ✅ PASS | FR-018/019 add computed data to Streamlit pages without influencing the scoring decision path. |

**Constitution Check Result**: 2 warnings (II, IX). No blocking violations. FR-005 is implementation-blocked (not spec-blocked) by TD-007; implementation sequence must reflect this.

---

## Project Structure

### Documentation (this feature)

```text
specs/004-operational-excellence/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── ml-client.md
│   ├── scoring-config-extension.md
│   ├── rule-definition-extension.md
│   └── management-api.md
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
# Prerequisites (FR-025/026/027) — must ship first
pipelines/scoring/
├── ml_client.py              # FR-025: MLModelClient interface + stub
├── telemetry.py              # FR-027: OTel tracer bootstrap for scoring
└── config.py                 # extend: ml_serving_url, cb_*, redis_url

pipelines/scoring/rules/
└── models.py                 # extend RuleDefinition: mode field (shadow/active)

infra/
└── docker-compose.yml        # FR-026: add redis service

# CI/CD (FR-001/002) — already partially complete
.github/
├── workflows/ci.yml          # DONE: BACKWARD_TRANSITIVE, schema gates
└── PULL_REQUEST_TEMPLATE.md  # DONE: constitution compliance checklist

# Observability (FR-011/012/013/014)
infra/grafana/provisioning/dashboards/
└── fraud-detection-main.json # FR-012: full latency waterfall + circuit breaker panel
infra/prometheus/alerts/
└── fraud_rule_engine.yml     # extend: rule_fp_rate_high, ml_circuit_breaker alerts

# Behavioral features (FR-014/016/022)
pipelines/scoring/
└── circuit_breaker.py        # FR-014: pybreaker wrapper + Prometheus hooks

# Documentation (FR-007–010/015/017/020–024)
docs/
├── runbooks/
│   ├── DLQ_INSPECTION_AND_RECOVERY.md   # FR-007
│   ├── CHECKPOINT_RECOVERY.md           # FR-008
│   ├── SCHEMA_MIGRATION.md              # FR-009
│   └── OBSERVABILITY_RUNBOOK.md         # FR-010
├── adr/
│   ├── ADR-001-STREAM-PROCESSING.md     # FR-024a
│   ├── ADR-002-OBJECT-STORAGE.md        # FR-024b
│   ├── ADR-003-FEATURE-STORE.md         # FR-024c
│   └── ADR-004-ANALYTICS-QUERY.md       # FR-024d
├── deployment/
│   └── CANARY_POLICY.md                 # FR-022
├── postmortems/
│   ├── PROCESS.md                       # FR-017
│   └── TEMPLATE.md                      # FR-017
├── SERVICE_OWNERSHIP.md                 # FR-020
└── KPI_TARGETS.md                       # FR-021
tests/
├── scoring/                             # FR-023: gap audit of existing tests
└── chaos/
    └── FAILURE_SCENARIOS.md             # FR-015

# DLQ tooling (FR-007)
scripts/
└── replay-dlq-message.sh                # FR-007: dry-run DLQ replay
```

---

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Concern | Why Accepted | Simpler Alternative Rejected Because |
|---|---|---|
| pybreaker + custom probe timeout | FR-014 requires 5ms probe timeout; pybreaker's built-in timeout uses thread sleeps which can't guarantee sub-10ms precision | Writing a bare `asyncio.wait_for` circuit breaker from scratch introduces untested concurrency bugs in a production hot path |
| OTel tail-based sampling requires Collector | Python SDK head-based sampling is native; tail-based requires OTEL Collector sidecar | Accept head-based-only for v1 if Collector is not already in compose; document as FR-011 v1 limitation |
| Iceberg sinks absent (TD-007) | FR-005/019 are implementation-blocked | Cannot resolve in this spec; requires dedicated Iceberg sink spec |

---

## Implementation Sequence

Phase ordering is driven by prerequisite dependencies:

```
[P0] Research (this phase)
  → resolves: pybreaker 5ms probe, OTel Collector availability, Terraform provider choice,
              RuleDefinition mode field backward compat, Alertmanager webhook routing

[P1] Design & Contracts
  → MLModelClient interface contract
  → ScoringConfig extension contract
  → RuleDefinition extension contract
  → /rules/{rule_id}/demote management API contract

[P2] Tasks (speckit.tasks — separate command)
  Tier 1 (prerequisites — unblock everything else):
    FR-025: pipelines/scoring/ml_client.py
    FR-026: Redis in docker-compose + redis_url in ScoringConfig
    FR-027: pipelines/scoring/telemetry.py
    models.py: add mode field to RuleDefinition

  Tier 2 (behavioral features — depend on Tier 1):
    FR-014: circuit_breaker.py + ScoringConfig cb_* fields
    FR-011: trace sampling policy in telemetry.py
    FR-012: fraud-detection-main.json Grafana dashboard
    FR-013: late event metrics in processing

  Tier 3 (documentation — can parallelise with Tier 2):
    FR-007/008/009/010: runbooks
    FR-015: chaos test plan
    FR-017: post-mortem process + template
    FR-020/021: service ownership + KPI targets
    FR-022: canary policy
    FR-023: rule regression test gap audit
    FR-024: all 4 ADRs

  Tier 4 (blocked by external work):
    FR-005/019: blocked by TD-007 (Iceberg sinks)
    FR-016: Redis HA Terraform — blocked by FR-026 completion
    FR-018: DLQ trend analysis — blocked by FR-007 runbook
```
