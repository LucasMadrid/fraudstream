# Tasks: Fraud Detection Rule Engine — Phase 1 MVP

**Input**: Design documents from `/specs/003-fraud-rule-engine/`
**Branch**: `003-fraud-rule-engine` | **Date**: 2026-04-03
**Prerequisites**: plan.md ✅, spec.md ✅

**Tests**: Included — Constitution §VIII mandates 80% unit test coverage (non-negotiable gate).
Tests are written before implementation for each user story phase.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.
Note: US2 (YAML Config) precedes US1 (Evaluation) in implementation order — the rule loader is a
hard prerequisite for the evaluator, even though spec priority lists US1 as P1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths are included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package scaffolding, dependency registration, schema files, and infra additions.
No business logic — purely structural.

- [x] T001 Create Python package skeleton: `pipelines/scoring/__init__.py`, `pipelines/scoring/rules/__init__.py`, `pipelines/scoring/rules/families/__init__.py`, `pipelines/scoring/sinks/__init__.py`, `pipelines/scoring/schemas/` directory, `tests/unit/scoring/__init__.py` (empty `__init__.py` files)
- [x] T002 [P] Add `[scoring]` extras to `pyproject.toml`: `pydantic>=2.0`, `pyyaml`, `psycopg2-binary` (add alongside existing `[processing]` extras; scoring co-locates in the same Flink job)
- [x] T003 [P] Create `specs/003-fraud-rule-engine/contracts/` directory and draft `fraud-alert-v1.avsc`: fields `transaction_id` (string), `account_id` (string), `matched_rule_names` (array<string>), `severity` (enum: low/medium/high/critical), `evaluation_timestamp` (long, epoch_ms); register subject `txn.fraud.alerts-value` with `BACKWARD_TRANSITIVE` compatibility
- [x] T004 [P] Draft `specs/003-fraud-rule-engine/contracts/fraud-alert-dlq-v1.avsc`: fields mirror `fraud-alert-v1.avsc` plus `error_type` (string), `error_message` (string), `failed_at` (long, epoch_ms); subject `txn.fraud.alerts.dlq-value`
- [x] T005 [P] Copy both contract schemas into `pipelines/scoring/schemas/` (mirror of `specs/003-.../contracts/` — same pattern as feature 002)

**Checkpoint**: Package importable; schemas drafted; dependency extras installable via `pip install -e ".[scoring]"`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core shared components that ALL user stories depend on. No story work begins until this phase is complete.

**⚠️ CRITICAL**: No user story implementation can start until this phase is complete.

- [x] T006 Implement `ScoringConfig` dataclass in `pipelines/scoring/config.py`: fields `kafka_brokers`, `schema_registry_url`, `fraud_alerts_topic` (default `txn.fraud.alerts`), `fraud_alerts_dlq_topic` (default `txn.fraud.alerts.dlq`), `rules_yaml_path` (default `/opt/rules/rules.yaml`), `fraud_alerts_db_url` (from env `FRAUD_ALERTS_DB_URL`), `pg_pool_size` (default 2) — all env-var driven via `os.environ.get`
- [x] T007 [P] Write PostgreSQL DDL in `infra/postgres/migrations/001_create_fraud_alerts.sql`: `fraud_alerts` table with columns `transaction_id` (TEXT PRIMARY KEY), `account_id` (TEXT NOT NULL), `matched_rule_names` (TEXT[] NOT NULL), `severity` (TEXT NOT NULL CHECK severity IN ('low','medium','high','critical')), `evaluation_timestamp` (BIGINT NOT NULL), `status` (TEXT NOT NULL DEFAULT 'pending' CHECK status IN ('pending','confirmed-fraud','false-positive')), `reviewed_by` (TEXT NULLABLE), `reviewed_at` (TIMESTAMP WITH TIME ZONE NULLABLE), `inserted_at` (TIMESTAMP WITH TIME ZONE DEFAULT NOW()); indexes on `account_id` and `status`
- [x] T008 [P] Extend `infra/kafka/topics.sh` with two new topics: `txn.fraud.alerts` (4 partitions, 7-day retention, `cleanup.policy=delete`) and `txn.fraud.alerts.dlq` (1 partition, 30-day retention)
- [x] T009 [P] Extend `infra/docker-compose.yml` with a `postgres` service (image `postgres:16`, port 5432, `POSTGRES_DB=fraudstream`, `POSTGRES_USER=fraudstream`, `POSTGRES_PASSWORD` from env, volume mount for init migration, health check via `pg_isready`)

**Checkpoint**: Foundation complete — config, DDL, Kafka topics, PostgreSQL service, schemas all ready. User story work can now begin.

---

## Phase 3: User Story 2 — Rule Configuration via YAML (Priority: P2)

> **Implementation note**: US2 is implemented before US1 because the `RuleLoader` and `RuleDefinition`
> model are hard prerequisites for the evaluator. US2 is independently testable without the evaluator.

**Goal**: Engineers can define rules in `rules/rules.yaml`; the `RuleLoader` validates and loads the
rule set at job startup; invalid config causes a descriptive fail-fast error before any events are processed.

**Independent Test**: `RuleLoader.load("rules/rules.yaml")` returns 10 `RuleDefinition` objects with
correct fields and thresholds. A malformed YAML raises `RuleConfigError` before the first event is processed.

### Tests for User Story 2

- [x] T010 [P] [US2] Write unit tests for `RuleDefinition` pydantic model in `tests/unit/scoring/test_rule_loader.py`: valid model constructs; missing `severity` raises `ValidationError`; invalid `severity` value raises; unknown extra field raises; `enabled: false` is valid
- [x] T011 [P] [US2] Write unit tests for `RuleLoader` in `tests/unit/scoring/test_rule_loader.py`: valid YAML loads 10 enabled rules; malformed YAML raises `RuleConfigError` with message; missing required field raises `RuleConfigError`; rule with `enabled: false` is excluded from result; YAML referencing unknown field name raises `RuleConfigError` at startup

### Implementation for User Story 2

- [x] T012 [US2] Implement `RuleFamily` enum (`velocity`, `impossible_travel`, `new_device`), `Severity` enum (`low`, `medium`, `high`, `critical`), and `RuleDefinition` pydantic model in `pipelines/scoring/rules/models.py`: fields `rule_id` (str), `name` (str), `family` (RuleFamily), `conditions` (dict[str, Any]), `severity` (Severity), `enabled` (bool); `model_config = ConfigDict(extra='forbid')`
- [x] T037 [US2] Define `EvaluationResult`, `FraudAlert`, and `FraudAlertRecord` dataclasses in `pipelines/scoring/types.py` per spec.md Key Entities: `EvaluationResult(determination: Literal["clean","suspicious"], matched_rules: list[str], highest_severity: str | None, evaluation_timestamp: int, missing_fields: list[str])`; `FraudAlert(transaction_id: str, account_id: str, matched_rule_names: list[str], severity: str, evaluation_timestamp: int)`; `FraudAlertRecord(transaction_id: str, account_id: str, matched_rule_names: list[str], severity: str, evaluation_timestamp: int, status: str = "pending")`; export all three from `pipelines/scoring/__init__.py`
- [x] T013 [US2] Write `rules/rules.yaml` with all 10 default rules (all `enabled: true`): VEL-001 (`vel_count_1m`, count: 5, high), VEL-002 (`vel_amount_5m`, amount: 2000.00, high), VEL-003 (`vel_count_5m`, count: 10, medium), VEL-004 (`vel_amount_24h`, amount: 10000.00, medium), IT-001 (`prev_geo_country`/`geo_country`/`prev_txn_time_ms`, window_ms: 3600000, critical), IT-002 (`vel_count_1h`/`geo_country`/`prev_geo_country`, count: 3, high), ND-001 (`device_txn_count`/`amount`, amount: 500.00, high), ND-002 (`device_txn_count`/`vel_count_1h`, device_count: 3, vel_count: 5, medium), ND-003 (`device_known_fraud`, no threshold, critical), ND-004 (`device_txn_count`/`geo_network_class`, no numeric threshold, high); annotate IT-001 and IT-002 with cross-feature dependency comment
- [x] T014 [US2] Implement `RuleLoader` in `pipelines/scoring/rules/loader.py`: `load(path: str) -> list[RuleDefinition]` reads YAML, validates each entry via `RuleDefinition.model_validate()`, raises `RuleConfigError(message)` on any `ValidationError` or missing file, returns only rules where `enabled: true`; define `RuleConfigError(RuntimeError)` in same module

**Checkpoint**: `RuleLoader.load("rules/rules.yaml")` returns 10 rules; all 5 failure cases raise `RuleConfigError` with descriptive messages; unit tests pass with ≥ 80% coverage on loader + models.

---

## Phase 4: User Story 1 — Real-Time Fraud Evaluation (Priority: P1) 🎯 MVP

**Goal**: Every `EnrichedTransaction` consumed from `txn.enriched` is evaluated against all 10 enabled
rules; matching transactions produce a correct `EvaluationResult`; clean transactions pass through
unmarked; missing fields evaluate as not-met with the absence noted.

**Independent Test**: Synthetic enriched transaction injected into a Flink mini-cluster pipeline is
evaluated against the loaded rule set; `EvaluationResult.determination` is `suspicious` for a record
designed to match VEL-001; `determination` is `clean` for an unmatching record.

### Tests for User Story 1

- [x] T015 [P] [US1] Write unit tests for velocity condition functions in `tests/unit/scoring/test_velocity_rules.py`: above-threshold (match), below-threshold (no-match), boundary value (exact threshold = match), null field → `False` safe default — for each of VEL-001 through VEL-004
- [x] T016 [P] [US1] Write unit tests for impossible travel condition functions in `tests/unit/scoring/test_impossible_travel.py`: `prev_geo_country=None` → `False` for both IT-001 and IT-002; matching country + within time window → `True` for IT-001; matching country + count above threshold → `True` for IT-002; non-matching conditions → `False`
- [x] T017 [P] [US1] Write unit tests for new device condition functions in `tests/unit/scoring/test_new_device_rules.py`: first-seen device (`device_txn_count=1`) + high amount → ND-001 match; `device_known_fraud=True` → ND-003 match; `geo_network_class="HOSTING"` + first device → ND-004 match; null required field → `False` safe default for each rule
- [x] T018 [US1] Write unit tests for `RuleEvaluatorProcessFunction` in `tests/unit/scoring/test_evaluator.py`: single rule match → `EvaluationResult(suspicious, [rule_name], severity)`; multi-rule match → highest severity selected; clean record → `EvaluationResult(clean)`; record missing required field → condition not-met, absence noted in result, remaining rules still evaluated; `rule_evaluations_total` increments for every evaluated rule; `rule_flags_total` increments only for matched rules

### Implementation for User Story 1

- [x] T019 [P] [US1] Implement VEL-001 through VEL-004 as pure condition functions `def evaluate(record: dict, thresholds: dict) -> bool` in `pipelines/scoring/rules/families/velocity.py`: each returns `False` if the required velocity field is `None` or absent (FR-011); no side effects, no I/O; **important**: `vel_amount_*` fields are Avro `decimal` (Python `decimal.Decimal` via fastavro) — comparisons must use `Decimal(str(threshold))` not float literals to avoid precision errors; `vel_count_*` fields are plain `int`
- [x] T020 [P] [US1] Implement IT-001 and IT-002 as null-safe condition functions in `pipelines/scoring/rules/families/impossible_travel.py`: IT-001 guards `prev_geo_country is not None` before any comparison; IT-002 guards `prev_geo_country is not None`; both return `False` on null (cross-feature dependency — will evaluate to not-met until feature 002 ships `prev_geo_country`)
- [x] T021 [P] [US1] Implement ND-001 through ND-004 as pure condition functions in `pipelines/scoring/rules/families/new_device.py`: ND-003 (`device_known_fraud == True`) returns `False` if field is `None`; ND-004 checks `geo_network_class == "HOSTING"` — `None` evaluates as not-met
- [x] T022 [US1] Implement `RULE_FAMILY_DISPATCH: dict[RuleFamily, Callable]` mapping in `pipelines/scoring/rules/evaluator.py` that routes each `RuleDefinition.family` to the correct family module's `evaluate()` function; add `dispatch(rule: RuleDefinition, record: dict) -> bool` helper
- [x] T023 [US1] Implement `RuleEvaluatorProcessFunction(ProcessFunction)` in `pipelines/scoring/rules/evaluator.py`: constructor receives `list[RuleDefinition]`; `process_element(record, ctx)` iterates rules, calls `dispatch()`, collects matches and missing fields, assembles `EvaluationResult(determination, matched_rules, highest_severity, evaluation_timestamp, missing_fields)`; increments `rule_evaluations_total` and `rule_flags_total` counters per rule; emits record with attached `EvaluationResult`; routes suspicious records to `OutputTag("fraud-alerts")`
- [x] T024 [US1] Implement `wire_rule_evaluator(enriched_stream, config, rules)` in `pipelines/scoring/job_extension.py`: receives the output `DataStream` from `EnrichmentJoinFunction` in the feature 002 DAG, applies `RuleEvaluatorProcessFunction`, returns `(main_stream, alert_side_output)` tuple; called from `pipelines/processing/job.py` after existing enrichment topology

- [x] T038 [US1] Modify `pipelines/processing/job.py` to wire the rule evaluator into the existing feature 002 DAG: import `wire_rule_evaluator` from `pipelines.scoring.job_extension`; load `ScoringConfig` from env; call `RuleLoader.load(config.rules_yaml_path)` at startup; call `wire_rule_evaluator(enriched_stream, config, rules)` after the existing enrichment topology; route `alert_side_output` to `AlertKafkaSink` and `AlertPostgresSink`

**Checkpoint**: `RuleEvaluatorProcessFunction` produces correct `EvaluationResult` for all 10 rules in both matching and non-matching scenarios; missing-field safe default verified; unit tests ≥ 80% coverage.

---

## Phase 5: User Story 3 — Fraud Alert Emission (Priority: P3)

**Goal**: Every suspicious transaction produces exactly one `FraudAlert` record on `txn.fraud.alerts`
(Avro) and one `FraudAlertRecord` row in PostgreSQL `fraud_alerts`. Clean transactions produce neither.
Failures route to DLQ without stalling the pipeline.

**Independent Test**: A flagged transaction produces a structured alert on `txn.fraud.alerts` that a
test consumer can read and validate; a corresponding row exists in `fraud_alerts`; a clean transaction
produces no alert and no row.

### Tests for User Story 3

- [x] T025 [P] [US3] Write unit tests for `AlertKafkaSink` in `tests/unit/scoring/test_alert_kafka.py`: happy-path `FraudAlert` → serialised Avro bytes produced to `txn.fraud.alerts`; Avro serialisation failure → raw record produced to `txn.fraud.alerts.dlq` with `error_type` header; `transaction_id` is used as partition key
- [x] T026 [P] [US3] Write unit tests for `AlertPostgresSink` in `tests/unit/scoring/test_alert_postgres.py`: happy-path `FraudAlertRecord` → `INSERT INTO fraud_alerts` executed; duplicate `transaction_id` (idempotent retry) → `ON CONFLICT DO NOTHING` does not raise; PostgreSQL unavailability → `FraudAlertRecord` routed to `txn.fraud.alerts.dlq` with `error_type=postgres_unavailable`; pipeline is not stalled (exception is caught, not re-raised)
- [x] T027 [P] [US3] Write contract test in `tests/contracts/test_fraud_alert_schema.py`: register `fraud-alert-v1.avsc` against a live Schema Registry (testcontainers); assert `BACKWARD_TRANSITIVE` compatibility check returns success; assert subject `txn.fraud.alerts-value` exists after registration
- [x] T040 [P] [US3] Write unit test for `AlertKafkaSink` back-pressure behaviour in `tests/unit/scoring/test_alert_kafka.py` (US3 AC-3): assert that a Kafka producer exception (simulated broker unreachable — e.g., `KafkaException`) raised during `produce()` is NOT caught and NOT silently swallowed — it re-raises so Flink propagates back-pressure upstream; contrast with T026 (PostgreSQL unavailability IS caught and routed to DLQ — not re-raised); documents the intentional asymmetry between FR-010 (Kafka failure → back-pressure) and FR-010b (PostgreSQL failure → DLQ)

### Implementation for User Story 3

- [x] T028 [US3] Implement `AlertKafkaSink` in `pipelines/scoring/sinks/alert_kafka.py`: serialises `FraudAlert` dict to Avro using `fastavro` and schema from `pipelines/scoring/schemas/fraud-alert-v1.avsc`; produces to `txn.fraud.alerts` with `transaction_id` as partition key; on serialisation failure produces raw bytes to `txn.fraud.alerts.dlq` with header `error_type=avro_serialisation_error`; adds OTel child span `fraud.alert_emit` with attribute `topic=txn.fraud.alerts`
- [x] T029 [US3] Implement `AlertPostgresSink` in `pipelines/scoring/sinks/alert_postgres.py`: module-level connection pool (size=`config.pg_pool_size`); `insert(record: FraudAlertRecord)` executes `INSERT INTO fraud_alerts (...) VALUES (...) ON CONFLICT (transaction_id) DO NOTHING`; on `psycopg2.OperationalError` or pool exhaustion: serialises record to JSON and produces to `txn.fraud.alerts.dlq` with `error_type=postgres_unavailable` — does NOT re-raise (pipeline must not stall per plan constraint)
- [x] T030 [US3] Add Schema Registry startup registration in `pipelines/scoring/sinks/alert_kafka.py`: on `AlertKafkaSink` initialisation, register `fraud-alert-v1.avsc` with `BACKWARD_TRANSITIVE` compatibility; raise `RuntimeError` if registration fails (fail-fast at startup per FR-007 / plan Phase 0 exit gate)

**Checkpoint**: Both sinks write correctly under happy path; DLQ routing verified for both failure modes; idempotent re-insert does not duplicate `fraud_alerts` rows; contract test passes; unit tests ≥ 80% coverage on sinks.

---

## Phase 6: User Story 4 — Per-Rule Observability (Priority: P4)

**Goal**: After processing a controlled batch of events, per-rule `rule_evaluations_total` and
`rule_flags_total` counters reflect expected values; OTel span `fraud.rule_evaluation` is visible in
Jaeger; structured log entries appear for flagged transactions.

**Independent Test**: After processing a controlled batch of 20 test events (10 matching, 10 clean),
Prometheus metrics endpoint returns per-rule counts matching expected values for each rule that was exercised.

### Tests for User Story 4

- [x] T039 [US4] Write unit tests for `pipelines/scoring/metrics.py` in `tests/unit/scoring/test_metrics.py`: `register_rule_counters(rules)` creates counters with correct label names (`rule_id`, `rule_family`, `severity`); incrementing `rule_evaluations_total` for a rule yields label values matching the rule's `rule_id` and `rule_family`; incrementing `rule_flags_total` yields correct `severity` label; calling `register_rule_counters` twice for the same rule set does not raise (idempotent registry); OTel span attributes set by `finish_evaluation_span` include `matched_rule_count`, `highest_severity`, `evaluation_duration_ms`

### Implementation for User Story 4

- [x] T031 [P] [US4] Implement per-rule Prometheus counter registration in `pipelines/scoring/metrics.py`: `register_rule_counters(rules: list[RuleDefinition]) -> tuple[Counter, Counter]` creates `rule_evaluations_total` (labels: `rule_id`, `rule_family`) and `rule_flags_total` (labels: `rule_id`, `rule_family`, `severity`); uses shared Prometheus registry from feature 002 metrics module (no duplicate registration); called once at job startup from `job_extension.py`
- [x] T032 [US4] Implement OTel span `fraud.rule_evaluation` in `pipelines/scoring/metrics.py`: `start_evaluation_span(ctx, trace_context)` creates child span of `fraud.enrichment` trace context extracted from record headers; `finish_evaluation_span(span, result: EvaluationResult)` sets attributes `matched_rule_count` (int), `highest_severity` (str or "none"), `evaluation_duration_ms` (float); span status `OK` for both clean and flagged; `ERROR` only on evaluator exception; called from `RuleEvaluatorProcessFunction.process_element()`
- [x] T033 [US4] Add structured log entry per flagged transaction in `pipelines/scoring/rules/evaluator.py`: emit one log record at `INFO` level when `determination == SUSPICIOUS` with fields `transaction_id`, `account_id`, `matched_rules` (list), `severity`, `component=rule_evaluator`, `timestamp` (ISO-8601), `level=INFO` — consistent with feature 002 structured log format from `logging_config.py`

**Checkpoint**: Prometheus scrape returns non-zero per-rule counters after a test run; OTel span visible in local Jaeger; structured INFO log emitted for every flagged transaction.

---

## Phase 7: Integration + CI Wiring

**Purpose**: End-to-end integration test and CI pipeline extension. All user stories must be complete before this phase.

- [x] T041 [P] Create Prometheus alerting rules file `infra/prometheus/alerts/fraud_rule_engine.yml` with two alert rules: (1) `FraudAlertsDLQDepthHigh` — fires when `kafka_consumer_group_lag{topic="txn.fraud.alerts.dlq"} > 0` for 60 seconds (constitution §VIII: DLQ depth alert within 60s — non-negotiable gate, resolves C1 and OR-002); (2) `FraudFlagRateZero` — fires when `sum(rate(rule_flags_total[5m])) == 0` sustained for 15 minutes (silent evaluator failure detection); both alerts must include labels `severity`, `team=fraud-platform`, and `runbook_url`
- [x] T034 Write integration test in `tests/integration/test_rule_engine_pipeline.py`: spin up Kafka + Schema Registry + PostgreSQL + Flink mini-cluster via `testcontainers`; inject 20 synthetic `EnrichedTransaction` Avro records (10 designed to match at least one rule, 10 clean); assert: all 10 flagged transactions appear on `txn.fraud.alerts`; corresponding 10 rows in `fraud_alerts` PostgreSQL table; zero clean transactions on `txn.fraud.alerts`; `rule_evaluations_total` counter for VEL-001 equals 20 (all evaluated); `rule_flags_total` for each matched rule equals expected count; mark with `@pytest.mark.integration`
- [x] T035 [P] Add `fraud-alert-v1.avsc` schema pair to `schema-integrity` CI step in `.github/workflows/ci.yml`: add entry `["specs/003-fraud-rule-engine/contracts/fraud-alert-v1.avsc"]="pipelines/scoring/schemas/fraud-alert-v1.avsc"` to the `schema_pairs` associative array
- [x] T036 [P] Add `txn.fraud.alerts-value` to `schema-registry-compat` CI step in `.github/workflows/ci.yml`: add entry `["txn.fraud.alerts-value"]="pipelines/scoring/schemas/fraud-alert-v1.avsc"` to the `subjects` associative array

**Checkpoint**: Integration test passes end-to-end; CI schema-integrity and schema-registry-compat steps include the new fraud alert schema; overall coverage across `pipelines/scoring/` ≥ 80%.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US2 (Phase 3)**: Depends on Phase 2 — models and loader required by all other phases
- **US1 (Phase 4)**: Depends on Phase 3 (US2) — condition functions + evaluator require `RuleDefinition` model and loaded rule set
- **US3 (Phase 5)**: Depends on Phase 4 (US1) — alert sinks receive `EvaluationResult` from evaluator
- **US4 (Phase 6)**: Depends on Phase 4 (US1) and Phase 5 (US3) — counters registered at startup; OTel span emitted inside evaluator
- **Integration + CI (Phase 7)**: Depends on all US phases complete

### User Story Dependencies

- **US2 (P2)**: Can start after Foundational — independently testable via `RuleLoader` unit tests
- **US1 (P1)**: Depends on US2 (needs `RuleDefinition` model and condition function interfaces)
- **US3 (P3)**: Depends on US1 (alert sinks consume `EvaluationResult` side-output)
- **US4 (P4)**: Depends on US1 + US3 (metrics registered at startup; spans inside evaluator; logs from evaluator)

### Within Each User Story

- Tests written and FAILING before implementation (TDD per constitution)
- Models/enums before services/functions
- Condition functions before evaluator
- Evaluator before sinks
- Sinks before integration test

### Parallel Opportunities

- T002, T003, T004, T005 (Phase 1) — all parallelizable
- T007, T008, T009 (Phase 2) — all parallelizable
- T010, T011 (US2 tests) — parallelizable with each other
- T015, T016, T017 (US1 condition tests) — all parallelizable
- T019, T020, T021 (US1 condition implementations) — all parallelizable
- T025, T026, T027 (US3 tests) — all parallelizable
- T031, T032 (US4) — parallelizable with each other
- T035, T036 (CI wiring) — parallelizable with each other

---

## Parallel Example: User Story 1 (Condition Functions)

```bash
# Once US2 is complete, launch all three condition function test files together:
T015 tests/unit/scoring/test_velocity_rules.py
T016 tests/unit/scoring/test_impossible_travel.py
T017 tests/unit/scoring/test_new_device_rules.py

# Then implement all three condition modules in parallel:
T019 pipelines/scoring/rules/families/velocity.py
T020 pipelines/scoring/rules/families/impossible_travel.py
T021 pipelines/scoring/rules/families/new_device.py
```

---

## Implementation Strategy

### MVP Scope (User Stories 1 + 2 only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US2 — Rule Config (prerequisite for evaluator)
4. Complete Phase 4: US1 — Evaluation (core evaluation loop)
5. **STOP and VALIDATE**: Unit tests pass; evaluator produces correct `EvaluationResult` for all 10 rules
6. Integration test with Flink mini-cluster (Phase 7 subset — no Kafka/PostgreSQL required)

### Incremental Delivery

1. Setup + Foundational → infrastructure ready
2. US2 → rule config loads and validates → commit
3. US1 → evaluation works → commit (MVP — detection works, alerting manual)
4. US3 → alerts emitted → commit (operational handoff complete)
5. US4 → observability → commit (production-ready)
6. Integration + CI → full gate → PR to main

### Blocking Dependency (IT-001 / IT-002)

IT-001 and IT-002 load correctly and appear in metrics but evaluate to `not-met` until feature 002
ships `prev_geo_country` + `prev_txn_time_ms` on `EnrichedTransaction`. No special-casing required —
null-safe condition functions return `False` on null fields per FR-011. Track as OR-001 in plan.md.

---

## Open Requirements Reference

| OR ID | Impact on Tasks | Status |
|---|---|---|
| OR-001 | IT-001/IT-002 conditions always return False until feature 002 extended | Tracked in plan.md — no task needed here |
| OR-002 | DLQ depth and flag-rate Prometheus alerting rules | **Resolved** — T041 creates `infra/prometheus/alerts/fraud_rule_engine.yml` |
| OR-003 | OTel span attribute spec | Resolved in T032 |
| OR-004 | Per-channel thresholds | Phase 2 — out of scope |
| OR-005 | Hot-reload without restart | Phase 2 — out of scope |
| OR-006 | SC-002 (≤10ms p99) cannot be validated with a 20-event mini-cluster test | **Pre-production SLO**: T034 validates functional correctness only; SC-002 must be measured in a staging environment at production-scale throughput before production promotion; this is a tracking item, not a blocker for the feature branch |

---

## Notes

- `[P]` tasks target different files and have no dependencies on incomplete tasks in the same phase
- `[Story]` label maps each task to a specific user story for traceability
- Each user story is independently testable via its unit tests before the next story begins
- Constitution §VIII coverage gate (80%) applies to all `pipelines/scoring/` modules
- Commit after each phase checkpoint; do not bundle multiple checkpoints in a single commit
- IT-001 and IT-002 must NOT be removed from the rule set — they are loaded, counted, and log correct metrics; they simply never fire until the feature 002 prerequisite ships
