# Fraud Rule Engine — Requirements & Implementation Review Checklist

**Purpose**: Requirements-quality review for PR gating, post-implementation audit, and Phase 2 readiness  
**Created**: 2026-04-05  
**Feature**: [003-fraud-rule-engine](../spec.md)  
**Depth**: Comprehensive | **Audience**: Author / PR reviewer / Phase 2 planning

Severity key: `[CRITICAL]` blocks merge · `[HIGH]` should resolve before ship · `[MEDIUM]` address in follow-up · `[LOW]` nice-to-have

---

## Category 1 — Cross-Feature Dependency Requirements

- [x] CHK001 `[HIGH]` — Is the DeviceProfileState extension (feature 002 adding `last_geo_country` + `last_txn_time_ms`) tracked with an owner and target ship date as a blocking dependency? [Gap, Spec §Assumptions §2]
- [x] CHK002 `[HIGH]` — When `prev_geo_country` is absent (feature 002 not yet extended): is it documented as a spec requirement (not just an implementation choice) that IT-001/IT-002 evaluate to `False` rather than raising, and is this validated by automated tests? [Clarity, FR-011, Spec §Assumptions §2]
- [x] CHK003 `[HIGH]` — Are the field-level contracts between feature 002 (enrichment output) and feature 003 (scoring input) formally documented with field names, types, and null semantics? [Completeness, Gap]
- [x] CHK004 `[MEDIUM]` — Are contract tests for `prev_geo_country` and `prev_txn_time_ms` presence in enriched records defined as Phase 2 acceptance criteria? [Coverage, Gap]

---

## Category 2 — Back-Pressure & Failure Mode Requirements

- [x] CHK005 `[HIGH]` — Is "back-pressure propagates upstream" (FR-010) specified concretely — does it mean Kafka producer blocks, returns an error, or triggers Flink task failure? [Clarity, FR-010]
- [x] CHK006 `[MEDIUM]` — Is maximum tolerable alert latency defined for the back-pressure scenario (i.e., does SC-002's ≤10ms p99 hold when the producer is back-pressured)? [Coverage, SC-002]
- [x] CHK007 `[LOW]` — Is the checkpoint recovery behavior under concurrent sink failures (Kafka and PostgreSQL failing simultaneously) documented — or is standard Flink checkpoint semantics sufficient? [Edge Case, Gap]
- [x] CHK008 `[MEDIUM]` — Is a DLQ topic for failed fraud alert deliveries (separate from `txn.api.dlq`) defined in the spec, with message format and routing rules? [Consistency, FR-010]
- [x] CHK009 `[HIGH]` — Is it specified whether the Flink job should restart or continue when `AlertKafkaSink.open()` fails (e.g., Schema Registry unreachable at startup)? [Clarity, Gap]
- [x] CHK010 `[MEDIUM]` — Are requirements defined for how many Kafka delivery retries occur before routing to DLQ, and what the timeout threshold is? [Completeness, Gap]

---

## Category 3 — Persistent Store Requirements

- [x] CHK011 `[HIGH]` — Is the `fraud_alerts` DDL schema formally versioned and does it align with all `FraudAlertRecord` fields including optional `reviewed_by` and `reviewed_at`? [Consistency, FR-014]
- [x] CHK012 `[MEDIUM]` — Is a retention policy for `fraud_alerts` records specified with a minimum retention period for audit compliance? [Gap, FR-014]
- [x] CHK013 `[MEDIUM]` — Is idempotent re-processing behavior (no duplicate persists on replay) defined as a requirement, and are its implications for re-processing acknowledged? [Clarity, Assumption]
- [x] CHK014 `[MEDIUM]` — Is there a performance requirement for querying `fraud_alerts` by `status` (FR-015), and is an index on `(account_id, status)` mandated? [Coverage, FR-015]
- [x] CHK015 `[HIGH]` — Is it specified who can update `status` from `pending` to `confirmed-fraud` or `false-positive`, and via what mechanism? [Gap, FR-015]
- [x] CHK016 `[LOW]` — Is the PostgreSQL connection pool configuration (max connections, timeout, retry policy) specified for Flink workers? [Gap, Non-Functional]

---

## Category 4 — Tech Debt Scope Boundaries

- [x] CHK017 `[MEDIUM]` — Are Phase 1 in-scope boundaries clearly demarcated from TD-001 through TD-005? Specifically: is TD-004 (false-positive feedback loop) explicitly out of scope for Phase 1, with `status` field updates deferred to Phase 2? [Clarity, TD-001–TD-005]
- [x] CHK018 `[LOW]` — Is TD-005 (extraction into standalone consumer) documented with enough interface context to avoid premature abstraction in Phase 1? [Gap, TD-005]
- [x] CHK019 `[MEDIUM]` — Is TD-002 (declarative rule DSL) noted as a forward-compatibility concern — i.e., does the Phase 1 YAML schema avoid breaking changes when TD-002 ships? [Gap, TD-002]

---

## Category 5 — Observability & Alerting Requirements

- [x] CHK020 `[MEDIUM]` — Are the exact Prometheus counter names for per-rule evaluation and flag counts specified in the spec (FR-012), or left to implementer discretion? [Clarity, FR-012]
- [x] CHK021 `[MEDIUM]` — Is SC-005 quantified with specific expected counter values in test assertions, not just "observable via metrics"? [Measurability, SC-005]
- [x] CHK022 `[MEDIUM]` — Is there a requirement for a Prometheus alert rule when per-rule flag counters drop to zero for an extended period (possible silent evaluator failure)? Note: exact counter name is implementation-defined; the requirement is for the alerting rule to exist. [Gap, FR-012]
- [x] CHK023 `[LOW]` — Is OTel span granularity specified — per transaction, per rule evaluation, or per batch? [Clarity, Gap]
- [x] CHK024 `[LOW]` — Are structured log field names for fraud evaluation logs standardized and consistent with the enrichment pipeline's existing log schema? [Consistency, Gap]
- [x] CHK025 `[LOW]` — Is there a requirement for a Flink job health-check or readiness indicator confirming rules were loaded successfully at startup? [Gap, Non-Functional]

---

## Category 6 — Acceptance Criteria Quality

- [x] CHK026 `[HIGH]` — Is SC-002 ("≤10ms p99 latency") testable in the current environment, or does it require a production-scale benchmark to be meaningful? If the latter, should it be re-framed as a production SLO? [Measurability, SC-002]
- [x] CHK027 `[HIGH]` — Is SC-006 ("exactly 1 alert per flagged transaction") reconciled with AT_LEAST_ONCE Kafka delivery — are consumers expected to handle duplicate alerts idempotently? [Consistency, FR-009, SC-006]
- [x] CHK028 `[MEDIUM]` — Is SC-001 ("100% of enriched transactions evaluated") monitored in production via a metric or audit trail, not just asserted in unit tests? [Measurability, SC-001]
- [x] CHK029 `[MEDIUM]` — Are the 4 US1 and 3 US3 acceptance scenarios captured as automated test cases (not just prose)? [Completeness, US1, US3]
- [x] CHK030 `[LOW]` — Do test function names map back to acceptance scenario IDs (e.g., `test_us1_scenario_2_multi_rule_match`) so traceability is explicit? [Completeness, US1, US3]

---

## Category 7 — Phase 2 Readiness

- [x] CHK031 `[MEDIUM]` — Is the `RuleLoader` interface abstracted and documented to allow Phase 2 swapping (Kafka consumer, AppConfig) without changing caller code? [Coverage, TD-001, TD-002]
- [x] CHK032 `[LOW]` — Is the self-service rule management UI (TD-003) tracked with a backlog item or spec stub to prevent ad-hoc scope creep into Phase 1? [Clarity, TD-003]

---

## Category 8 — Non-Functional Requirements Coverage

- [x] CHK033 `[LOW]` — Is there a requirement for maximum rule set size (number of rules) the evaluator must handle without violating SC-002 latency? [Gap, Non-Functional]
- [x] CHK034 `[LOW]` — Is the Flink parallelism for the fraud evaluation stage specified relative to the enrichment job's parallelism? [Gap, Non-Functional]
- [x] CHK035 `[MEDIUM]` — Is there a security requirement governing who may commit threshold changes to `rules.yaml` (e.g., mandatory PR review by fraud operations team)? [Gap, Non-Functional]
- [x] CHK036 `[HIGH]` — Is FR-004 (≥10 rules across 3 families) validated by an automated test that counts the actual rules loaded from `rules.yaml`, not just by unit tests for individual rule logic? [Coverage, FR-004]
- [x] CHK037 `[HIGH]` — Is the Avro schema for `txn.fraud.alerts` specified to cover all `FraudAlert` fields, and is the Schema Registry subject name and compatibility mode (`BACKWARD_TRANSITIVE`) formally documented? [Completeness, FR-009]

---

## Notes

- **IT-001/IT-002 known gap**: Both rules load and appear in metrics but always evaluate `False` in Phase 1 — this is intentional and documented in Spec §Assumptions §2.
- **AT_LEAST_ONCE vs exactly-once**: CHK027 surfaces a real consistency gap between SC-006 and the delivery guarantee. Downstream consumer idempotency is likely required but not specified.
- **SC-002 testability**: 10ms p99 at Flink scale cannot be validated locally; this warrants either a benchmark environment or a re-framing of SC-002 as a production SLO.
- **CHK022 metric name**: The alert rule requirement is independent of the specific counter name chosen at implementation time.
