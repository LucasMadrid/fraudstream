# Feature Specification: Fraud Detection Rule Engine (Phase 1 MVP)

**Feature Branch**: `003-fraud-rule-engine`  
**Created**: 2026-04-03  
**Status**: Draft  
**Input**: User description: "Now we should work on the Rule engine for fraud detection as consumer of the apache flink"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Real-Time Transaction Fraud Evaluation (Priority: P1)

A fraud analyst or the automated compliance system relies on every transaction enriched by the Flink job to be evaluated against the active rule set in real time. The rule evaluator runs as a step co-located within the same Flink job, immediately after enrichment. When a transaction satisfies one or more rules it is flagged so that downstream systems (payment blocking, case management) can act on it without delay.

**Why this priority**: This is the core value proposition. Without real-time evaluation, fraud goes undetected until a manual review cycle. All other stories build on a working evaluation loop.

**Independent Test**: A synthetic enriched transaction injected into the Flink pipeline is evaluated against the YAML-loaded rule set; the result (clean or flagged) is verifiable by inspecting the output topic without any UI or alerting infrastructure.

**Acceptance Scenarios**:

1. **Given** an enriched transaction record is produced by the Flink enrichment stage, **When** the record matches at least one active rule, **Then** the evaluator marks it as suspicious, records all matching rule names and a severity label, and emits a fraud alert.
2. **Given** an enriched transaction record is produced, **When** it matches no active rules, **Then** the evaluator marks it as clean; no alert is emitted.
3. **Given** multiple rules match the same transaction, **When** evaluation completes, **Then** all matching rule names are recorded and the highest severity label is applied.
4. **Given** an enriched record is missing a field required by a rule condition, **When** that rule is evaluated, **Then** the condition is treated as not-met (safe default), the remaining rules are still evaluated, and the missing field is noted in the evaluation result.

---

### User Story 2 - Threshold and Rule Configuration via YAML (Priority: P2)

Engineers and fraud analysts with repository access can adjust rule thresholds and enable or disable rules by editing a YAML configuration file and redeploying the Flink job. No code change is required to tune a threshold or swap a rule in or out.

**Why this priority**: Fraud patterns change faster than engineering sprints. Separating thresholds from code means a threshold change is a config PR, not a code review — reducing the time to respond to a new fraud vector from days to hours.

**Independent Test**: An engineer changes a velocity threshold in the YAML file, restarts the job, and verifies through test events that the new threshold is applied without any source code modification.

**Acceptance Scenarios**:

1. **Given** a YAML config file defining rule names, conditions, thresholds, and severity labels, **When** the Flink job starts, **Then** all rules declared as enabled in the config are loaded and used for evaluation; disabled rules are ignored.
2. **Given** a threshold is updated in the YAML and the job is restarted, **When** events arrive after restart, **Then** evaluations reflect the new threshold value.
3. **Given** the YAML file is malformed or a required field is missing, **When** the job attempts to start, **Then** it fails fast at startup with a descriptive error identifying the invalid configuration — it does not start with a partially loaded rule set.
4. **Given** a rule is marked `enabled: false` in the YAML, **When** the job runs, **Then** that rule is never evaluated against any transaction.

---

### User Story 3 - Fraud Alert Emission (Priority: P3)

When a transaction is flagged, a structured alert is emitted to the `txn.fraud.alerts` Kafka topic so that downstream consumers (operations queue, case management) can act on it.

**Why this priority**: Detection without notification has no operational value. The alert topic is the handoff point between the rule engine and all downstream action systems.

**Independent Test**: A flagged transaction produces a structured alert on `txn.fraud.alerts` that a test consumer can read and validate for completeness and correctness without any live downstream system.

**Acceptance Scenarios**:

1. **Given** a transaction is flagged, **When** the evaluation stage completes, **Then** a structured alert record is written to `txn.fraud.alerts` containing: transaction ID, account ID, matched rule names, severity label, and evaluation timestamp.
2. **Given** a clean transaction, **When** evaluation completes, **Then** no alert is written to `txn.fraud.alerts`.
3. **Given** `txn.fraud.alerts` is temporarily unavailable, **When** the evaluator attempts to emit, **Then** back-pressure propagates upstream (consistent with the Flink enricher's existing back-pressure behaviour) and no alert is silently dropped.

---

### User Story 4 - Per-Rule Observability (Priority: P4)

Engineers and analysts can see how many transactions each rule has evaluated and flagged via observable metrics, giving them the signal needed to tune thresholds or retire ineffective rules without waiting for a full analytics pipeline.

**Why this priority**: Without per-rule counters, it is impossible to know whether a rule is firing too broadly (analyst fatigue) or not at all (missed fraud). Basic metrics are the minimum feedback loop for Phase 1.

**Independent Test**: After processing a controlled batch of test events, the per-rule evaluation count and flag count metrics reflect the expected values for each rule.

**Acceptance Scenarios**:

1. **Given** a rule has processed at least one transaction, **When** metrics are scraped, **Then** the rule's evaluation count and flag count are non-zero and consistent with the test input.
2. **Given** a rule is disabled in the YAML and the job is restarted, **When** metrics are scraped, **Then** that rule's evaluation count does not increment.

---

### Edge Cases

- What happens when the job restarts mid-window for a velocity-based rule (e.g., "more than 5 transactions in 10 minutes")? The velocity state is recovered from the Flink checkpoint — no special handling is required in the rule evaluator.
- What if two rules produce conflicting determinations on the same attribute? The highest severity among all matching rules wins; no rule can suppress another.
- What if the YAML config references a field name that was renamed in the enriched schema? The job fails at startup during rule loading, before any events are processed.
- What happens during a Flink checkpoint when a rule evaluation is in flight? Flink's standard checkpoint barrier semantics handle this; the rule evaluator is a stateless `ProcessFunction` and introduces no additional consistency concerns.

## Rule Catalogue

The following 10 rules form the default active rule set for Phase 1. All threshold values are YAML-configurable; values shown are defaults.

### Velocity Family

| Rule ID | Name | Condition | Severity | Default Thresholds |
|---------|------|-----------|----------|--------------------|
| VEL-001 | HIGH_FREQUENCY_1M | `vel_count_1m ≥ threshold` | high | `count: 5` |
| VEL-002 | HIGH_AMOUNT_5M | `vel_amount_5m ≥ threshold` | high | `amount: 2000.00` |
| VEL-003 | BURST_COUNT_5M | `vel_count_5m ≥ threshold` | medium | `count: 10` |
| VEL-004 | DAILY_AMOUNT_SPIKE | `vel_amount_24h ≥ threshold` | medium | `amount: 10000.00` |

### Impossible Travel Family

> **Cross-feature dependency**: Rules IT-001 and IT-002 require `prev_geo_country` and `prev_txn_time_ms` on the enriched record. These fields are not currently produced by feature 002. Before Phase 1 ships, feature 002's `DeviceProfileState` must be extended to track `last_geo_country` and emit both fields as pre-update values on `EnrichedTransaction`. See Assumptions.

| Rule ID | Name | Condition | Severity | Default Thresholds |
|---------|------|-----------|----------|--------------------|
| IT-001 | IMPOSSIBLE_TRAVEL | `prev_geo_country ≠ null AND geo_country ≠ prev_geo_country AND (event_time − prev_txn_time_ms) ≤ threshold_ms` | critical | `window_ms: 3600000` (1 hour) |
| IT-002 | CROSS_BORDER_VELOCITY | `vel_count_1h ≥ threshold AND geo_country ≠ prev_geo_country` | high | `count: 3` |

### New Device Family

| Rule ID | Name | Condition | Severity | Default Thresholds |
|---------|------|-----------|----------|--------------------|
| ND-001 | NEW_DEVICE_HIGH_AMOUNT | `device_txn_count = 1 AND amount ≥ threshold` | high | `amount: 500.00` |
| ND-002 | NEW_DEVICE_RAPID_TXN | `device_txn_count ≤ threshold_count AND vel_count_1h ≥ threshold_vel` | medium | `device_count: 3, vel_count: 5` |
| ND-003 | KNOWN_FRAUD_DEVICE | `device_known_fraud = true` | critical | _(no threshold — boolean flag)_ |
| ND-004 | NEW_DEVICE_FOREIGN | `device_txn_count = 1 AND geo_network_class = "HOSTING"` | high | _(no numeric threshold)_ |

All 10 rules are `enabled: true` in the default YAML configuration. Total: 4 velocity + 2 impossible travel + 4 new device.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The rule evaluator MUST run as a stage co-located within the Flink enrichment job, consuming the enriched transaction stream from the `txn.enriched` Kafka topic.
- **FR-002**: The evaluator MUST evaluate every enriched transaction against all rules marked `enabled: true` in the YAML configuration.
- **FR-003**: The evaluator MUST cover at minimum three rule families: velocity (transaction count or amount over a rolling time window), impossible travel (geographic distance between consecutive transactions relative to elapsed time), and new device (first-time device identifier on an account).
- **FR-004**: The active rule set MUST include at least 10 rules across the three families defined in FR-003.
- **FR-005**: Rule thresholds (amounts, counts, time windows, distances) MUST be defined in a YAML configuration file and loaded at job startup — not hard-coded in source.
- **FR-006**: Rules MUST be individually togglable via an `enabled` flag in the YAML without modifying source code.
- **FR-007**: The job MUST fail at startup with a descriptive error if the YAML config is missing, malformed, or references unknown field names — a partially loaded rule set is not acceptable.
- **FR-008**: For each flagged transaction, the evaluator MUST record: transaction ID, account ID, all matching rule names, the highest severity label, and evaluation timestamp.
- **FR-009**: The evaluator MUST emit a structured alert record to the `txn.fraud.alerts` Kafka topic for every flagged transaction.
- **FR-010**: Alerts MUST NOT be silently dropped; back-pressure from an unavailable `txn.fraud.alerts` topic MUST propagate upstream through the Flink pipeline.
- **FR-010b**: If the PostgreSQL persistent store is unavailable when a `FraudAlertRecord` write is attempted, the record MUST be routed to the `txn.fraud.alerts.dlq` DLQ topic rather than stalling the pipeline or silently dropping the record. The Kafka alert emission (FR-009) MUST proceed regardless of PostgreSQL availability — the two output paths are independent.
- **FR-011**: If a rule condition references a field absent from the enriched record, the condition MUST evaluate to not-met (safe default) and the absence MUST be noted in the evaluation result; evaluation of remaining rules MUST continue.
- **FR-012**: The evaluator MUST expose per-rule counters: evaluation count and flag count, observable via the existing metrics infrastructure used by the Flink enricher.
- **FR-013**: The evaluator MUST be stateless with respect to rule definitions — all rule state (velocity windows, device history) is owned by the enrichment stage and provided as fields on the enriched record; the evaluator MUST NOT maintain its own independent state stores.
- **FR-014**: For every flagged transaction, the evaluator (or a co-located sink stage) MUST write a `FraudAlertRecord` to a durable persistent store in addition to emitting the Kafka alert. The persistent record serves as the audit trail and as the foundation for the false-positive feedback loop (TD-004).
- **FR-015**: The persistent store MUST support querying by `transaction_id`, `account_id`, and `status` (pending / confirmed-fraud / false-positive). No other query patterns are required in Phase 1.

### Key Entities

- **RuleDefinition**: A named rule loaded from YAML — contains rule name, family (velocity / impossible-travel / new-device), conditions with threshold values, severity label (low / medium / high / critical), and an `enabled` flag.
- **EnrichedTransaction**: The record produced by the Flink enrichment stage — contains all original transaction fields plus velocity features, geolocation context, and device profile fields. This is the evaluator's input.
- **EvaluationResult**: The output of evaluating one EnrichedTransaction — includes determination (clean / suspicious), list of matched rule names, highest severity, and evaluation timestamp. Attached to the record flowing downstream.
- **FraudAlert**: A structured record written to `txn.fraud.alerts` for every suspicious transaction — contains transaction ID, account ID, matched rule names, severity, and evaluation timestamp.
- **FraudAlertRecord**: The durable form of a fraud alert, persisted to the relational store (FR-014). Fields: `transaction_id` (PK), `account_id`, `matched_rule_names` (array), `severity`, `evaluation_timestamp`, `status` (pending / confirmed-fraud / false-positive), `reviewed_by` (nullable), `reviewed_at` (nullable). Status defaults to `pending` at insertion. Updates to `status` are performed by downstream review tooling and are outside this feature's write path.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of enriched transactions are evaluated; no events are silently skipped or dropped by the rule evaluator.
- **SC-002**: Rule evaluation adds no more than 10 milliseconds of additional latency to the existing enrichment pipeline at p99, measured from enrichment stage output to evaluator output.
- **SC-003**: The rule set covers all three families (velocity, impossible travel, new device) with at least 10 rules active in the default configuration.
- **SC-004**: A threshold change in the YAML configuration is reflected in evaluations after the next job restart, with no source code change required.
- **SC-005**: Per-rule evaluation and flag counts are observable via metrics; after a controlled test run the counts match the expected values for each rule.
- **SC-006**: Zero alerts are silently dropped; every flagged transaction produces at least one alert record on `txn.fraud.alerts`. Kafka AT_LEAST_ONCE delivery semantics apply — duplicate delivery is possible on retry. Consumers of `txn.fraud.alerts` MUST handle duplicates idempotently. The `fraud_alerts` PostgreSQL table enforces write-side deduplication via `ON CONFLICT (transaction_id) DO NOTHING`.

## Assumptions

- The enrichment stage (feature 002) outputs all fields required by the three rule families — velocity aggregates, geolocation coordinates and timestamps for impossible-travel checks, and device identifier with first-seen flag for new-device checks. If any of these fields are absent from the 002 output schema, the relevant rules cannot be implemented and that gap must be resolved before Phase 1 ships.
- **Cross-feature dependency (impossible travel)**: Feature 002's `DeviceProfileState` must be extended before Phase 1 ships to track `last_geo_country` (ISO 3166-1 alpha-2) and `last_txn_time_ms` (epoch ms of the previous transaction from the same device). These pre-update values must be emitted as `prev_geo_country` and `prev_txn_time_ms` on `EnrichedTransaction`. Rules IT-001 and IT-002 are blocked until this extension is in production. This is a required change to feature 002, not a Phase 2 item.
- A PostgreSQL-compatible relational database (PostgreSQL 14+) is available in the deployment environment and accessible from the Flink job's worker nodes. The `fraud_alerts` table is provisioned before the job is deployed; schema migration is an infrastructure concern outside this feature's scope. Use of PostgreSQL here is explicitly permitted under the narrow exception in Principle VI of the constitution — this is a secondary operational store for mutable review state only, not a substitute for the Iceberg event store.
- The rule evaluator is a stateless `ProcessFunction`; all cross-event state (velocity windows, device history) is maintained by the enrichment stage and provided as pre-computed fields on the enriched record.
- Retroactive evaluation (re-running rules against historical events) is out of scope; rules apply only to events processed after the job restarts with the updated config.
- The fraud alerts Kafka topic is provisioned before the job is deployed; topic creation is an infrastructure concern outside this feature's scope.
- A job restart (rolling or full) is the accepted mechanism for applying rule or threshold changes in Phase 1. Sub-minute hot-reload without restart is a Phase 2 goal (see Tech Debt).
- Python 3.11 is the established runtime; all rule condition logic is written in Python and loaded from the YAML config at job startup.
- No authorization layer for rule changes is required in Phase 1 — access control is enforced at the repository/PR level.

## Tech Debt

Accepted limitations for Phase 1 MVP. Each item is a prerequisite before the capability it describes can be considered production-ready.

- **TD-001 — Hot-reloadable rule config (Phase 2)**: In Phase 1, applying a rule or threshold change requires a job restart. Phase 2 should migrate rule definitions to a dynamic config store (AWS AppConfig, Consul, or a Kafka compacted topic) and implement hot-reload so rules become active within seconds of a config update, with no job restart. At that point FR-006 (runtime enable/disable) and the 60-second activation target become achievable.

- **TD-002 — Structured rule definitions with validation (Phase 2)**: Phase 1 rules are hand-coded condition logic in Python with thresholds parameterised by YAML. Phase 2 should replace hand-coded conditions with a declarative rule DSL (YAML-expressed conditions with AND/OR/NOT composition) and a schema validator that rejects invalid rule definitions before they reach production.

- **TD-003 — Self-service rule management UI (Phase 3)**: Phase 2 still requires repository access to change rules. Phase 3 should add a thin internal UI that allows fraud operations analysts to write rule YAML, validate it against the schema, and commit it to the config store — triggering a hot reload without any engineering involvement. This is the point at which Drools or an equivalent rule engine library becomes worth evaluating.

- **TD-004 — False-positive feedback loop**: Phase 1 metrics are limited to evaluation count and flag count. There is no mechanism for a reviewer to mark an alert as a false positive and have that signal flow back to per-rule accuracy metrics. A feedback topic and review workflow are required before false-positive rate becomes a meaningful operational metric.

- **TD-005 — Separation into a standalone consumer (future)**: Phase 1 co-locates the rule evaluator inside the Flink enrichment job for simplicity. If the rule set grows significantly in complexity or the teams operating enrichment and fraud detection diverge, the evaluator should be extracted into a separate Kafka Streams or standalone consumer service that reads from the `txn.enriched` topic independently. The alert emission interface (FR-009) is designed to be topology-neutral to make this extraction straightforward.
