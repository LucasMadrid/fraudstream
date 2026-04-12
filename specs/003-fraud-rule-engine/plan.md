# Implementation Plan: Fraud Detection Rule Engine — Phase 1 MVP

**Branch**: `003-fraud-rule-engine` | **Date**: 2026-04-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/003-fraud-rule-engine/spec.md`

---

## Summary

A stateless `ProcessFunction` co-located in the existing PyFlink enrichment job (feature 002)
evaluates every `EnrichedTransaction` from `txn.enriched` against a YAML-defined rule set. When one
or more rules match, a `FraudAlert` is emitted to `txn.fraud.alerts` (Avro, Schema Registry) and a
`FraudAlertRecord` is written to a PostgreSQL `fraud_alerts` table for reviewer workflow. Per-rule
evaluation and flag counters are exposed via the existing Prometheus metrics infrastructure.

**Technical approach:**
- **Co-location strategy**: The rule evaluator is added as a downstream `ProcessFunction` in the
  feature 002 Flink DAG, receiving the `EnrichedTransaction` stream output from `EnrichmentJoinFunction`.
  No new Flink job is created; no new Kafka consumer group is required for Phase 1.
- **Rule execution model**: Stateless — all cross-event state (velocity windows, device history,
  previous geo) is pre-computed by the enrichment stage and provided as fields on the
  `EnrichedTransaction` record. The evaluator reads fields, evaluates conditions, and produces output.
- **Rule config**: YAML file (`rules/rules.yaml`) mounted as a volume. Loaded at job startup via
  `RuleLoader` with fail-fast schema validation using `pydantic`. Invalid config aborts startup.
- **10 default rules**: 4 velocity (VEL-001–004), 2 impossible travel (IT-001–002), 4 new device
  (ND-001–004). IT-001 and IT-002 depend on `prev_geo_country` + `prev_txn_time_ms` — these require
  a prerequisite extension to feature 002's `DeviceProfileState` (see cross-feature dependency).
- **Output**: `FraudAlert` → `txn.fraud.alerts` (Avro, `BACKWARD_TRANSITIVE` compatibility);
  `FraudAlertRecord` → PostgreSQL `fraud_alerts` (psycopg2, synchronous write within Flink operator).
- **Metrics**: Two Prometheus counters per rule (`rule_evaluations_total{rule_id}`,
  `rule_flags_total{rule_id}`) registered at job startup from the loaded rule set.

---

## Technical Context

**Language/Version**: Python 3.11 (established — aligns with ML/data team tooling; same runtime as
feature 002)

**Primary Dependencies**:
- `apache-flink>=1.20` (PyFlink 1.20.3 DataStream API — co-located within the feature 002 Flink job; matches `apache/flink:1.20.3-scala_2.12` base image)
- `pydantic>=2.0` (YAML rule config schema validation — fail-fast at startup per FR-007)
- `pyyaml` (YAML rule config loading)
- `confluent-kafka[schema-registry]` (Schema Registry client for `txn.fraud.alerts` Avro schema
  registration and validation)
- `fastavro` (Avro serialisation for `txn.fraud.alerts` output)
- `psycopg2-binary` (synchronous PostgreSQL writes for `FraudAlertRecord` — acceptable within Flink
  operator; async not required as PostgreSQL write is outside the 100ms hot path)
- `prometheus-client` (per-rule counters; shared with feature 002 metrics registry)
- `opentelemetry-sdk` (trace span continuation — propagate existing enrichment trace context into
  rule evaluation span)
- `pytest` + `pytest-cov` (unit and integration tests; ≥ 80% coverage per constitution)

**Storage**:
- **Input**: `txn.enriched` Kafka topic (read-only; produced by feature 002)
- **Output topic**: `txn.fraud.alerts` — Avro-encoded `FraudAlert` records; Schema Registry subject
  `txn.fraud.alerts-value`; `BACKWARD_TRANSITIVE` compatibility; min 4 partitions, 7-day retention
- **Operational store**: PostgreSQL `fraud_alerts` table (narrow exception per constitution
  Principle VI); schema provisioned by infrastructure before job deploys; JDBC connection string
  injected via environment variable `FRAUD_ALERTS_DB_URL`
- **DLQ**: `txn.fraud.alerts.dlq` — unserializable or unwritable alert records; mirrors the DLQ
  pattern from features 001 and 002

**Testing**: `pytest` (unit + integration); Flink mini-cluster for operator-level unit tests;
`testcontainers` (Kafka + PostgreSQL) for integration tests; no load tests required for Phase 1
(rule evaluation adds < 10ms to existing pipeline — SC-002)

**Target Platform**: Same as feature 002 — Linux x86-64, Docker Compose (local dev); Kubernetes
with Flink Operator (cloud production)

**Performance Goals**:
- Rule evaluation latency addition: < 10ms p99 over the existing enrichment pipeline (SC-002)
- Zero silent alert drops: every flagged transaction produces exactly one record on `txn.fraud.alerts`
  (SC-006)
- 100% of enriched transactions evaluated; no events skipped (SC-001)

**Constraints**:
- The evaluator MUST be stateless — it must not open or read Flink state stores. All required fields
  must be present on `EnrichedTransaction`. Any missing field evaluates the condition as not-met.
- IT-001 and IT-002 are **blocked** until feature 002's `DeviceProfileState` emits `prev_geo_country`
  and `prev_txn_time_ms`. These two rules must be loaded but will always evaluate to not-met until
  the prerequisite is shipped. They MUST NOT be skipped or removed from the rule set.
- PostgreSQL writes are synchronous within the Flink operator. If PostgreSQL is unavailable, the
  operator must route the `FraudAlertRecord` to `txn.fraud.alerts.dlq` rather than stalling the
  pipeline (back-pressure on the scoring hot path is prohibited).
- All Avro schemas for `txn.fraud.alerts` must be registered in Schema Registry before the job
  starts; startup fails fast if registration fails.

**Scale/Scope**:
- Same throughput as feature 002: up to 10,000 TPS at peak (constitution §II load target)
- Rule set size: 10 rules in Phase 1; evaluator must handle up to 100 rules without latency
  regression (forward compatibility requirement)
- PostgreSQL write volume: proportional to fraud rate; expected < 1% of total transactions flagged
  at typical thresholds

---

## Constitution Check

*GATE: Must pass before implementation begins.*

| Principle | Status | Assessment |
|---|---|---|
| **I — Stream-First** | ✅ PASS | Consumes `txn.enriched` as an unbounded stream; emits to `txn.fraud.alerts`; no batch processing path |
| **II — Sub-100ms Decision Budget** | ✅ PASS | Stateless condition evaluation; < 10ms budget slice (SC-002); no I/O on hot path except PostgreSQL write (outside 100ms budget per constitution) |
| **III — Schema Contract Enforcement** | ✅ PASS | `fraud-alert-v1.avsc` registered in Schema Registry; `BACKWARD_TRANSITIVE` compatibility; startup fails if registry unreachable |
| **IV — Channel Isolation** | ⚠️ PHASE 1 EXCEPTION | Constitution §IV states "a single global threshold is **prohibited**." Phase 1 uses one global threshold per rule (OR-004). **Formal exception**: this feature operates on `txn.api` only (the API channel is the sole implemented channel in Phase 1); per-channel thresholds are deferred to TD-002. This exception is valid while only one channel feeds `txn.enriched`. When a second channel is added, per-channel threshold support (TD-002) becomes a hard prerequisite before that channel goes to production. This exception does NOT require a constitution amendment — it is scope-limited by the single-channel deployment reality. |
| **V — Defense in Depth — Rules Before Models** | ✅ PASS | This feature IS the rule engine mandated by Principle V; ML scoring is downstream and not implemented yet |
| **VI — Immutable Event Log** | ✅ PASS | `txn.fraud.alerts` is append-only; `FraudAlertRecord` is inserted once and updated only by downstream review tooling (outside this feature's write path); PostgreSQL use is the documented narrow exception |
| **VII — PII Minimization** | ✅ PASS | Receives already-masked fields from feature 002 (subnet /24, BIN + last4); no new PII handling; `FraudAlert` and `FraudAlertRecord` contain only masked identifiers |
| **VIII — Observability as First Class** | ⚠️ PARTIAL | Per-rule `rule_evaluations_total` and `rule_flags_total` counters defined (FR-012). **Open gaps**: No alerting thresholds defined for rule flag rate anomalies; no Grafana panel specified for rule trigger leaderboard (covered by analytics layer — Principle X); OTel span boundaries not fully specified. Must be resolved before production. |
| **IX — Analytics-First Persistence** | ✅ PASS | `FraudAlert` written to `txn.fraud.alerts`; analytics consumer (Principle X) reads this topic. `iceberg.fraud_decisions` is the scoring engine's responsibility (future spec) — out of scope here |
| **X — Analytics Consumer Layer** | ✅ PASS | `txn.fraud.alerts` is in the canonical topic inventory; analytics consumer `analytics.dashboard` subscribes for real-time feed and DLQ inspector |

**Constitution verdict**: Feature may proceed with one **open partial** (Principle VIII — alerting
thresholds and OTel span spec). Must be resolved before production promotion.

---

## Cross-Feature Dependency

> **BLOCKER for IT-001 and IT-002**: Feature 002's `DeviceProfileState` must be extended to track
> `last_geo_country` (ISO 3166-1 alpha-2) and `last_txn_time_ms` (epoch ms) and emit them as
> `prev_geo_country` and `prev_txn_time_ms` on `EnrichedTransaction` before these rules can fire.
>
> **Resolution path**: Open a task against feature 002 to extend `device_profile.py` and
> `enriched-txn-v1.avsc`. Ship as a schema version bump (`enriched-txn-v2.avsc` with additive
> fields — backward compatible). IT-001 and IT-002 evaluate to not-met until then.
>
> **Phase 1 handling**: Both rules are loaded from YAML, counted in metrics, and appear in the rule
> catalogue. When `prev_geo_country` is null (first transaction per device), the condition evaluates
> to not-met per FR-011. No special-casing in code required.

---

## Project Structure

### Documentation (this feature)

```text
specs/003-fraud-rule-engine/
├── plan.md               # This file
├── spec.md               # Feature specification
├── checklists/
│   └── requirements.md   # Existing requirements checklist
├── contracts/
│   ├── fraud-alert-v1.avsc        # Output schema: txn.fraud.alerts
│   └── fraud-alert-dlq-v1.avsc   # DLQ schema: txn.fraud.alerts.dlq
└── tasks.md              # Implementation task breakdown (created separately)
```

### Source Code

```text
pipelines/
└── scoring/                              # Rule engine root (constitution repo structure)
    ├── __init__.py
    ├── job_extension.py                  # Wires RuleEvaluatorProcessFunction into the feature 002
    │                                     #   Flink DAG after EnrichmentJoinFunction
    ├── config.py                         # ScoringConfig dataclass (DB URL, schema registry URL,
    │                                     #   rules YAML path — all from env vars)
    ├── rules/
    │   ├── __init__.py
    │   ├── loader.py                     # RuleLoader: reads rules.yaml, validates via pydantic,
    │   │                                 #   fails fast on invalid config (FR-007)
    │   ├── models.py                     # RuleDefinition, RuleFamily enum, Severity enum
    │   ├── evaluator.py                  # RuleEvaluatorProcessFunction (stateless ProcessFunction):
    │   │                                 #   iterates enabled rules, assembles EvaluationResult,
    │   │                                 #   routes to alert sink or passes through clean
    │   └── families/
    │       ├── __init__.py
    │       ├── velocity.py               # VEL-001–004 condition functions
    │       ├── impossible_travel.py      # IT-001–002 condition functions (null-safe)
    │       └── new_device.py             # ND-001–004 condition functions
    ├── sinks/
    │   ├── __init__.py
    │   ├── alert_kafka.py                # AlertKafkaSink: Avro-serialises FraudAlert →
    │   │                                 #   txn.fraud.alerts; DLQ on serialisation failure
    │   └── alert_postgres.py             # AlertPostgresSink: psycopg2 INSERT INTO fraud_alerts;
    │                                     #   DLQ routing on PostgreSQL unavailability
    ├── schemas/
    │   ├── fraud-alert-v1.avsc           # Mirror of specs/003-.../contracts/fraud-alert-v1.avsc
    │   └── fraud-alert-dlq-v1.avsc
    └── metrics.py                        # Per-rule counter registration; OTel span helpers

rules/
└── rules.yaml                            # Default rule set: 10 rules, all enabled: true

infra/
├── kafka/
│   └── topics.sh                         # Extended: add txn.fraud.alerts, txn.fraud.alerts.dlq
├── postgres/
│   └── migrations/
│       └── 001_create_fraud_alerts.sql   # fraud_alerts table DDL (provisioned before job deploy)
└── docker-compose.yml                    # Extended: add postgres service (if not already present)
```

### Tests

```text
tests/
├── unit/
│   └── scoring/
│       ├── test_rule_loader.py           # Valid YAML, malformed YAML, missing fields, disabled rules
│       ├── test_evaluator.py             # EvaluationResult assembly; missing field safe default;
│       │                                 #   highest severity selection; clean pass-through
│       ├── test_velocity_rules.py        # VEL-001–004: above/below threshold, boundary values
│       ├── test_impossible_travel.py     # IT-001–002: null prev_geo_country safe default,
│       │                                 #   matching/non-matching country + time window
│       └── test_new_device_rules.py      # ND-001–004: first-seen device, known fraud device flag
├── integration/
│   └── test_rule_engine_pipeline.py      # testcontainers: Flink mini-cluster + Kafka + PostgreSQL;
│                                         #   end-to-end: enriched record in → alert on topic + DB row
└── contracts/
    └── test_fraud_alert_schema.py        # Schema Registry compat check: fraud-alert-v1.avsc
                                          #   BACKWARD_TRANSITIVE validation
```

---

## Implementation Phases

### Phase 0 — Schema + Config Design *(prerequisite — no code)*

Resolve all design decisions before writing implementation code.

1. **Draft `fraud-alert-v1.avsc`** — define all fields of `FraudAlert`: `transaction_id`,
   `account_id`, `matched_rule_names` (array<string>), `severity` (enum), `evaluation_timestamp`
   (epoch_ms). Register in Schema Registry. Verify `BACKWARD_TRANSITIVE` compatibility.
2. **Draft `rules.yaml`** — define all 10 default rules with thresholds, severity labels, and
   `enabled: true`. Annotate IT-001 and IT-002 with cross-feature dependency comment.
3. **Define `RuleDefinition` pydantic model** — fields: `rule_id`, `name`, `family`, `conditions`
   (dict of threshold keys→values), `severity`, `enabled`. Validation: unknown fields rejected,
   `severity` must be enum value, `enabled` required.
4. **Confirm PostgreSQL connection approach** — synchronous psycopg2 within Flink operator is
   acceptable for Phase 1 given < 1% flag rate. Document decision.

**Exit gate**: `fraud-alert-v1.avsc` registered in local Schema Registry; `rules.yaml` loads without
error from `RuleLoader`; all 10 rules parse into valid `RuleDefinition` objects.

---

### Phase 1 — Rule Loader + Models

Files: `pipelines/scoring/rules/loader.py`, `models.py`, `rules/rules.yaml`

1. Implement `RuleDefinition` pydantic model with all fields and validators.
2. Implement `RuleLoader.load(path: str) -> list[RuleDefinition]`:
   - Read YAML, parse into list of `RuleDefinition`
   - Validate field names against `EnrichedTransaction` schema (detect renamed fields — FR-007)
   - Raise `RuleConfigError` with descriptive message on any failure
   - Return only rules where `enabled: true`
3. Write `rules/rules.yaml` with all 10 rules.
4. Unit tests: valid config loads all 10 rules; malformed YAML raises at startup; missing required
   field raises; `enabled: false` rule excluded; unknown field name raises.

**Exit gate**: `RuleLoader` loads all 10 rules from `rules.yaml`; all 4 failure cases raise with
descriptive messages; unit tests pass with ≥ 80% coverage on loader + models.

---

### Phase 2 — Rule Condition Functions

Files: `pipelines/scoring/rules/families/velocity.py`, `impossible_travel.py`, `new_device.py`

Implement one pure function per rule: `def evaluate(record: dict, thresholds: dict) -> bool`.
Each function must:
- Access only the fields declared in its `RuleDefinition.conditions`
- Return `False` (not-met) if a required field is `None` or absent (FR-011)
- Have no side effects and no I/O

Rules:
- `VEL-001`: `vel_count_1m >= count`
- `VEL-002`: `vel_amount_5m >= amount`
- `VEL-003`: `vel_count_5m >= count`
- `VEL-004`: `vel_amount_24h >= amount`
- `IT-001`: `prev_geo_country is not None AND geo_country != prev_geo_country AND (event_time - prev_txn_time_ms) <= window_ms`
- `IT-002`: `vel_count_1h >= count AND geo_country != prev_geo_country AND prev_geo_country is not None`
- `ND-001`: `device_txn_count == 1 AND amount >= amount_threshold`
- `ND-002`: `device_txn_count <= device_count AND vel_count_1h >= vel_count`
- `ND-003`: `device_known_fraud == True`
- `ND-004`: `device_txn_count == 1 AND geo_network_class == "HOSTING"`

Unit tests: above-threshold, below-threshold, boundary value, null field safe default for every rule.

**Exit gate**: All 10 condition functions implemented; null-field tests pass for all rules with
nullable dependencies; ≥ 80% coverage.

---

### Phase 3 — Rule Evaluator ProcessFunction

Files: `pipelines/scoring/rules/evaluator.py`

Implement `RuleEvaluatorProcessFunction(ProcessFunction)`:
1. Constructor receives `list[RuleDefinition]` (pre-loaded and filtered to enabled rules).
2. `process_element(record, ctx)`:
   - Iterate all enabled rules; call the corresponding condition function
   - Collect matching rules; record missing fields per FR-011
   - If any match: assemble `EvaluationResult(determination=SUSPICIOUS, matched_rules=[...], severity=max_severity, evaluation_timestamp=now_ms)`
   - If no match: `EvaluationResult(determination=CLEAN)`
   - Increment `rule_evaluations_total{rule_id}` for every evaluated rule
   - Increment `rule_flags_total{rule_id}` for every matched rule
   - Emit downstream (both clean and suspicious records continue in the DAG)
3. Suspicious records are side-output to the alert sink; clean records continue to the main output.

Unit tests: single rule match; multi-rule match (highest severity selected); clean pass-through;
missing field noted but remaining rules evaluated; disabled rule never increments metrics.

**Exit gate**: Evaluator produces correct `EvaluationResult` for all 10 rules in both matching and
non-matching scenarios; metrics increment correctly; unit tests pass ≥ 80% coverage.

---

### Phase 4 — Output Sinks

Files: `pipelines/scoring/sinks/alert_kafka.py`, `alert_postgres.py`

**`AlertKafkaSink`**:
1. Serialize `FraudAlert` to Avro using `fastavro` + `fraud-alert-v1.avsc`
2. Produce to `txn.fraud.alerts` with `transaction_id` as partition key
3. On serialisation failure: produce raw record to `txn.fraud.alerts.dlq` with `error_type` header
4. OTel span: child of enrichment trace context; records `matched_rule_count`, `severity` as span attributes

**`AlertPostgresSink`**:
1. psycopg2 `INSERT INTO fraud_alerts (...) VALUES (...)` with `ON CONFLICT (transaction_id) DO NOTHING` (idempotent retry safety)
2. On PostgreSQL unavailability: route `FraudAlertRecord` as JSON to `txn.fraud.alerts.dlq` with `error_type=postgres_unavailable`; do NOT stall pipeline
3. Connection acquired from a module-level connection pool (size=2 per TaskManager slot)

Unit tests: successful Kafka produce; Avro serialisation failure → DLQ; PostgreSQL insert;
PostgreSQL unavailability → DLQ (not pipeline stall).

**Exit gate**: Both sinks write correctly under happy path; DLQ routing verified for both failure
modes; idempotent re-insert does not duplicate `fraud_alerts` rows.

---

### Phase 5 — Observability

Files: `pipelines/scoring/metrics.py` (extend shared metrics module)

1. Register per-rule counters at job startup from loaded rule set:
   - `rule_evaluations_total` (labels: `rule_id`, `rule_family`)
   - `rule_flags_total` (labels: `rule_id`, `rule_family`, `severity`)
2. Add OTel span `fraud.rule_evaluation` as child of `fraud.enrichment` span:
   - Attributes: `matched_rule_count`, `highest_severity`, `evaluation_duration_ms`
   - Span status: `OK` for clean; `OK` for flagged (not an error); `ERROR` only on evaluator exception
3. Structured log entry per flagged transaction:
   - Fields: `transaction_id`, `account_id`, `matched_rules`, `severity`, `component=rule_evaluator`, `timestamp`, `level`

**Exit gate**: Metrics scrape returns per-rule counters after a test run; OTel span visible in local
Jaeger; structured log entries emitted for flagged transactions.

---

### Phase 6 — Integration Test + Contract Test

Files: `tests/integration/test_rule_engine_pipeline.py`, `tests/contracts/test_fraud_alert_schema.py`

1. **Integration test** (`testcontainers`):
   - Spin up Kafka + Schema Registry + PostgreSQL + Flink mini-cluster
   - Inject 20 synthetic `EnrichedTransaction` records (10 designed to match at least one rule, 10 clean)
   - Assert: all 10 flagged transactions appear on `txn.fraud.alerts`; corresponding rows in `fraud_alerts` PostgreSQL table; zero clean transactions on `txn.fraud.alerts`; per-rule counters reflect expected counts
2. **Contract test**: Register `fraud-alert-v1.avsc`; verify `BACKWARD_TRANSITIVE` compat check passes

**Exit gate**: Integration test passes end-to-end; contract test validates schema compat; overall
coverage ≥ 80% across `pipelines/scoring/`.

---

### Phase 7 — Kafka Topic + Infrastructure Provisioning

Files: `infra/kafka/topics.sh`, `infra/postgres/migrations/001_create_fraud_alerts.sql`,
`infra/docker-compose.yml`

1. Add `txn.fraud.alerts` topic: 4 partitions, 7-day retention, `cleanup.policy=delete`
2. Add `txn.fraud.alerts.dlq` topic: 1 partition, 30-day retention
3. Add `fraud_alerts` table DDL migration
4. Extend `docker-compose.yml` with PostgreSQL service (if not already present from feature 002)

**Exit gate**: `make infra-up` starts full stack including PostgreSQL; topics visible in Kafka UI;
`fraud_alerts` table exists and is queryable.

---

## Open Requirements

| ID | Description | Blocking? | Resolution |
|---|---|---|---|
| OR-001 | IT-001 and IT-002 require feature 002 `DeviceProfileState` extension | Blocks IT-001/IT-002 only | Open task against feature 002; both rules load and evaluate to not-met until resolved |
| OR-002 | Alerting thresholds for rule flag rate anomalies not defined | **Resolved** | `infra/prometheus/alerts/fraud_rule_engine.yml` — T041 adds `FraudAlertsDLQDepthHigh` (60s DLQ depth, §VIII gate) and `FraudFlagRateZero` (15m silent evaluator detection) |
| OR-003 | OTel span attribute spec for rule evaluation not fully documented | Pre-production | Document in Phase 5; validate with team before production |
| OR-004 | Per-channel threshold config not supported in Phase 1 | Phase 2 | Accepted — single global threshold per rule in Phase 1 (TD-002) |
| OR-005 | Hot-reload of rule config without job restart not supported | Phase 2 | Accepted — restart required in Phase 1 (TD-001) |
| OR-006 | SC-002 (≤10ms p99) not measurable with unit/integration tests | Pre-production SLO | T034 validates correctness only; p99 latency must be measured in staging at production-scale throughput before production promotion |
