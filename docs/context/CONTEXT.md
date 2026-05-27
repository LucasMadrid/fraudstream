# FraudStream

Real-time fraud detection system that evaluates payment transactions against configurable rules and emits structured outcomes for downstream consumers.

## Language

### Transaction Lifecycle

**RawTransaction**:
A payment event received at the API boundary, PII-masked, published to `txn.api`.
_Avoid_: transaction, event, message

**EnrichedTransaction**:
A RawTransaction extended with computed features (velocity, geolocation, device profile). Published to `txn.enriched`.
_Avoid_: enriched record, enriched event

**EvaluationResult**:
The intermediate output of the rule evaluator for a single EnrichedTransaction. Contains a binary `determination` (clean or suspicious), matched rule names, and highest severity. Never persisted directly.
_Avoid_: result, scoring result

**Alert**:
A notification emitted only when an EvaluationResult is `suspicious`. Carries `matched_rule_names` and `severity`. Routed to Kafka (`txn.fraud.alerts`) and PostgreSQL.
_Avoid_: FraudAlert, fraud alert

**Decision**:
The system's final verdict on every transaction (ALLOW, FLAG, or BLOCK), derived from EvaluationResult + severity mapping. Persisted to Iceberg for every outcome, including clean transactions.
_Avoid_: FraudDecision, fraud decision, verdict

**AlertRecord**:
The durable PostgreSQL form of an Alert. Adds a `status` lifecycle field (`pending` → `confirmed-fraud` | `false-positive`).
_Avoid_: FraudAlertRecord

### Decision Outcomes

**ALLOW**:
A Decision for a transaction where determination is `clean`. `fraud_score = 0.0` (rule-only path).

**FLAG**:
A Decision for a `suspicious` transaction with highest severity `low` or `medium`. `fraud_score = 0.3` (rule-only path heuristic).

**BLOCK**:
A Decision for a `suspicious` transaction with highest severity `high` or `critical`. `fraud_score = 0.8` (rule-only path heuristic).

**RuleOnlyPath**:
The current scoring mode, where `fraud_score` is a hardcoded heuristic (0.0 / 0.3 / 0.8) and `model_version = "rule-only"`. Distinct from the ML-integrated path (not yet implemented).
_Avoid_: rule-based scoring

### Rule System

**RuleDefinition**:
A named, YAML-configured fraud rule with a family, severity, conditions (thresholds), enabled flag, and mode (active or shadow).

**RuleFamily**:
A category grouping rules that share evaluation logic: `velocity`, `impossible_travel`, or `new_device`.

**Determination**:
The binary output of rule evaluation: `clean` (no rules fired) or `suspicious` (at least one active rule fired).
_Avoid_: result, outcome (ambiguous)

**ShadowMode**:
A RuleDefinition mode where the rule is evaluated and its triggers recorded, but the result does NOT influence the Determination or the Alert/Decision. Used for safe rule promotion.

### Infrastructure Concepts

**Channel**:
The payment origin type (`API`, `POS`, `WEB`, `MOBILE`) that a RawTransaction flows through. Each channel has exactly one dedicated producer type; multiple instances of the same producer may run concurrently for horizontal scaling. The `channel` field is producer-assigned at publish time — it is not a caller-supplied request field and must not appear in the request schema. The producer reads its channel identity from config or an environment variable at startup.
_Avoid_: source, origin, platform

**DLQ (Dead-Letter Queue)**:
A Kafka topic receiving events that could not be processed. Three distinct DLQs exist: ingestion (`txn.api.dlq`), processing (`txn.processing.dlq`), and scoring (`txn.fraud.alerts.dlq`).

**ReplayJob**:
A batch re-evaluation of historical EnrichedTransactions against a fixed rule set snapshot. Source is always Iceberg (`enriched_transactions`) — Kafka and DLQ sources cannot be replayed correctly without a full re-enrichment pass that rebuilds velocity state from prior account history. Results are not persisted as live Decisions and do not trigger Alerts. Primary uses: shadow rule promotion validation, incident post-mortems, regression testing after rule changes. The rule set is snapshotted at job creation time and does not change mid-run.
_Avoid_: replay, re-scoring job

**ReplayResult**:
The output artifact of a ReplayJob for a single transaction: `(transaction_id, original_decision, replay_decision, matched_rules, rule_set_version)`. Distinct from Decision — carries both the original verdict and the replayed verdict for comparison. Persisted to the `replay_jobs` PostgreSQL table; not written to Iceberg and not visible to live pipeline consumers.
_Avoid_: replay decision, re-scored decision

**FeatureVector**:
A frozen snapshot of all computed features (velocity, geo, device) for a single transaction, fetched from the online feature store within the 3ms SLA. Zero-valued fallback used on timeout or unavailability.

## Relationships

- A **RawTransaction** is enriched into exactly one **EnrichedTransaction**, or routed to the processing **DLQ**
- An **EnrichedTransaction** produces exactly one **EvaluationResult**
- An **EvaluationResult** produces exactly one **Decision** (all outcomes)
- An **EvaluationResult** produces zero or one **Alert** (suspicious only)
- An **Alert** may be persisted as one **AlertRecord** in PostgreSQL
- A **Decision** is always persisted to Iceberg regardless of outcome
- A **Channel** has exactly one producer type; N producer instances may run concurrently within that channel
- A **ReplayJob** evaluates **EnrichedTransaction**s from Iceberg only; it produces **ReplayResult**s, never live **Decision**s
- The co-located scoring path reads features from the **EnrichedTransaction** dict — it never calls the online feature store (see ADR-005)

---

### SD-008 — Replay job skips feature enrichment and has field name mismatches

**Where**: `pipelines/replay/replay_job.py` lines 247-248, 242-244, 195

**Drift — missing enrichment step**: The replay job passes the raw event payload directly to `scoring_fn` (the rule evaluator) with no feature enrichment step. The rule evaluator expects velocity/geo/device features. The outcome depends on source type:
- **Iceberg source** (`enriched_transactions`): features are pre-computed and stored in the record — point-in-time correct, works as intended.
- **Kafka source** (`txn.api`) or **DLQ source**: raw transactions contain no features. Velocity and device rules silently produce zero-feature evaluations. Every transaction scores as `clean`. This is a correctness hole, not a crash.

**Drift — field name mismatches**: The replay job reads `event.get("payload", {}).get("fraud_decision")` and `event.get("event_id")`, but the enriched transaction schema uses `decision` and `transaction_id` respectively. The `original_decision` field in `ReplayResult` will always be `None` when replaying from Iceberg.

**Recommended amendment**:
- Restrict valid replay source types for rule evaluation to `iceberg` (`enriched_transactions`) only. Kafka and DLQ sources cannot be replayed correctly without a full re-enrichment pass, which requires replaying all prior account transactions to rebuild velocity state — a fundamentally different pipeline, not a simple scoring replay.
- Document this constraint in the replay spec / `ReplayConfig` validation: raise at config time if `source_type != iceberg` and `scoring_fn` is the rule evaluator.
- Fix field name reads: `fraud_decision` → `decision`, `event_id` → `transaction_id`.
- If Kafka/DLQ replay is ever needed, it requires a separate "re-enrichment replay" job that runs the full processing pipeline, not just the scoring function.

---

### SD-007 — `txn.enriched` emits JSON instead of Avro

**Where**: `pipelines/processing/` enrichment output · Constitution violation V1, deadline v2.0

**Drift**: All inter-service topics must use Avro with Schema Registry (Constitution A-III). `txn.enriched` is the only topic still emitting JSON. There are currently **no external consumers** of `txn.enriched` — this is the cheapest possible moment to fix it.

**Recommended amendment**:
- Migrate `txn.enriched` to Avro in v2, before any consumer is built. The `enriched-txn-v1.avsc` schema already exists; this is a producer-side change only.
- Do NOT use a two-phase parallel topic (`txn.enriched.v2`) — that pattern is for protecting existing consumers. With zero consumers, it adds complexity with no benefit.
- Register `enriched-txn-v1.avsc` in Schema Registry under subject `txn.enriched-value` with `BACKWARD_TRANSITIVE` compatibility before the first publish.
- Every future consumer of `txn.enriched` must use the Avro deserializer. Document this as a prerequisite in any spec that introduces a new consumer of this topic.

---

### SD-006 — processing → scoring module boundary violation

**Where**: `pipelines/processing/job.py` imports `pipelines/scoring/job_extension.py` · Constitution violation V6

**Drift**: `pipelines/processing` and `pipelines/scoring` are meant to be independent packages. The current wiring requires processing to import scoring, creating a tight coupling that prevents either package from being tested or deployed in isolation.

**Recommended amendment — Composition Root pattern**:
- Introduce `pipelines/topology.py` (or `pipelines/flink_job.py`) as the single assembly point.
- `topology.py` imports from both `pipelines.processing` and `pipelines.scoring` and wires the Flink DAG. Neither package imports the other.
- `processing` exposes a `build_enrichment_stream(env, config) -> DataStream` function.
- `scoring` exposes `wire_rule_evaluator(stream, config, rules) -> None` (already exists in `job_extension.py`; just moves its import chain).
- `topology.py` calls both and owns the job entry point.
- Benefit: each package is independently unit-testable; extracting scoring into a standalone Kafka consumer in the future requires only changes to `topology.py`, not surgery inside either package.

## Flagged ambiguities

- `fraud_score` appears to be a continuous ML output (`float [0,1]`) but is currently a 3-value heuristic — the continuous contract is aspirational, not live. See SD-003.

## Spec drift & recommended amendments

Each entry names the drift, where it lives today, and what a correct spec or implementation should say.

---

### SD-001 — Feast lookup in co-located scoring path

**Where**: `pipelines/scoring/job_extension.py` · `_FeatureEnrichmentFunction`, `_FEATURE_ZERO_DEFAULTS`

**Drift**: Spec 007 designed the Feast feature-serving contract assuming scoring would be a standalone Kafka consumer, with Feast as its only feature source. When scoring was co-located into the Flink enrichment job, the Feast lookup became redundant — the EnrichedTransaction already carries all computed features. The zero-defaults fallback (`_FEATURE_ZERO_DEFAULTS`) means a Feast timeout silently zeroes out live features, causing rules like `BURST_COUNT_5M` to miss real fraud signals even though the correct values are in the record.

**Recommended amendment**:
- Remove `_FeatureEnrichmentFunction` and `_FEATURE_ZERO_DEFAULTS` from `job_extension.py`; rule evaluator reads features directly from the enriched transaction dict.
- Update Spec 007 to distinguish two consumers of the online store: (a) the co-located scoring path — does NOT call Feast, reads from EnrichedTransaction; (b) future standalone scoring service — MUST call Feast and apply the 3ms SLA + zero-value fallback contract.
- The `feature_store_fallback_total` metric and the Feast 3ms SLA remain valid for path (b) and for external consumers; they are not relevant to path (a).
- Write an ADR recording that the co-located path reads from the enriched record, so a future engineer does not "fix" this by re-adding the Feast call.

---

### SD-002 — `latency_ms` hardcoded to 0.0 on Decision; no wall-clock timer exists in the scoring path

**Where**: `pipelines/scoring/job_extension.py` · `_build_fraud_decision`; `pipelines/scoring/types.py` · `FraudDecision`

**Drift**: Spec 006 (FR-008) lists `latency_ms` as a required field on every FraudDecision. The field exists in the dataclass and Iceberg schema but is hardcoded to `0.0` at write time — and there is no timer to wire in even if you wanted to. `time.perf_counter()` is used in the feature serving client (`clients/feature_serving.py:97,161,179`) to measure Feast retrieval latency, but that elapsed time is observed only into a Prometheus histogram and never returned to the caller. At the `_build_fraud_decision` level, no timing measurement exists anywhere in the call chain.

**Recommended amendment**:
- Start a `time.perf_counter()` timer at the top of `_evaluate()` in `wire_rule_evaluator`, immediately before `evaluator.dispatch(txn)`. Pass the elapsed ms into `_build_fraud_decision` as a parameter.
- `latency_ms` measures rule-evaluation-only wall-clock time (dispatch call only). End-to-end pipeline latency (`now - event_time`) belongs in a dedicated Prometheus histogram, not in the persisted record.
- Update Spec 006 acceptance criterion SC-001 to explicitly state what clock and what scope `latency_ms` covers.

---

### SD-004 — Investigator UI and alert lifecycle are unbuilt; `FraudAlertRecord` is an orphaned type

**Where**: `pipelines/scoring/types.py` · `FraudAlertRecord`; `pipelines/scoring/sinks/alert_postgres.py` · `AlertPostgresSink`; `fraud_alerts` PostgreSQL table

**Intent**: The intended design is an investigator UI — a triage interface where fraud analysts receive alerts, review them, and mark outcomes. `FraudAlertRecord` was designed as the investigator-facing representation of an alert, moving through a status lifecycle as a human works it.

**Drift**: Four layers are missing and the type is fully orphaned:

1. **`FraudAlertRecord` is never constructed in production.** `AlertPostgresSink` writes `FraudAlert` (the pipeline-internal type, no `status` field) directly via a hard INSERT. No code path ever creates a `FraudAlertRecord` outside of unit tests.

2. **`fraud_alerts` table has no `status` column.** The INSERT SQL in `AlertPostgresSink` writes `(transaction_id, account_id, matched_rule_names, severity, evaluation_timestamp)` only. There is nowhere to persist `status` even if the type were used.

3. **No status transition API.** There is no endpoint, management command, or automated process that issues `UPDATE fraud_alerts SET status = ...`. Every alert that lands in the DB stays at the implicit initial state forever.

4. **No investigator UI.** The Streamlit analytics app has no triage page. Analysts have no surface to review alerts, mark false positives, or escalate.

**Deferred to**: v2 — confirmed intent, not built yet. Spec 003 TD-004 ("False-positive feedback loop") acknowledges this but does not describe the transition protocol or UI contract.

**Recommended amendment for v2 spec**:
- Add `status` column to `fraud_alerts` schema (migration required). Allowed values: `pending`, `reviewed`, `confirmed_fraud`, `false_positive`, `escalated`.
- Define the transition owner: human analyst via UI (primary path), automated ML feedback loop (future). Both paths need to write `status_updated_at` and `status_updated_by` (analyst ID or `system`).
- Specify whether `confirmed_fraud` / `false_positive` are terminal or revisable. Recommend terminal with an override log — prevents silent rewrites while allowing corrections.
- The investigator UI writes back to `fraud_alerts` via a thin API (not directly to Postgres) so the pipeline and the UI share one access layer.
- Until the UI exists, add a code comment on `FraudAlertRecord` marking `status` as write-once (`pending`) in v1 — prevents a future developer assuming transitions are live.

**Source of truth decision**: `fraud_alerts` PostgreSQL table is the triage queue source of truth for the investigator UI. Kafka (`txn.fraud.alerts`) is the transport layer — it delivers alerts out of the Flink job reliably. The `ON CONFLICT (transaction_id) DO NOTHING` pattern in `AlertPostgresSink` is already correct: Kafka at-least-once delivery, Postgres deduplicates. The UI reads from and writes back to Postgres only. Kafka retention policy is a concern for external consumers (SIEM, audit exports), not the investigator UI. Add index on `(status, severity, evaluation_timestamp)` for the triage queue query pattern.

---

### SD-005 — `decision_time_ms` is a timestamp named like a duration

**Where**: `pipelines/scoring/types.py` · `FraudDecision.decision_time_ms`; `pipelines/scoring/job_extension.py` · `_build_fraud_decision`

**Drift**: `decision_time_ms` holds `result.evaluation_timestamp` — an epoch-millisecond timestamp. The `_ms` suffix reads as a duration (like `latency_ms`), creating an active naming confusion in the same field cluster. Any reader will assume it measures how long something took.

**Bundle with**: SD-002 (`latency_ms` hardcoded to 0.0) — both are on the same `FraudDecision` timing fields and should be fixed together.

**Recommended amendment**:
- Rename `decision_time_ms` → `decided_at_ms` everywhere: `FraudDecision` dataclass, Iceberg schema (`fraud_decisions` table), SQL analytics views (`v_fraud_rate_daily`, `v_transaction_audit`, and others referencing this field), and all tests.
- The `_at_ms` suffix aligns with the existing ingestion schema naming convention — all epoch-ms timestamps use `_time` or `_at_ms` suffixes; duration fields use `_ms`.
- Fix SD-002 in the same pass: populate `latency_ms` with actual rule-evaluation wall-clock time (`time.perf_counter()` delta around `evaluator.dispatch()`).
- After the rename, field semantics are unambiguous: `decided_at_ms` = when, `latency_ms` = how long.
- Coordinate as a schema evolution on the Iceberg `fraud_decisions` table (backward-compatible column rename requires a migration).

---

### SD-003 — `fraud_score` contract is aspirational, not live

**Where**: `pipelines/scoring/types.py` · `FraudDecision.fraud_score`; `pipelines/scoring/schemas/shadow-decision-v1.avsc`

**Drift**: `fraud_score` is typed as `float [0,1]` in the Avro schema and dataclass, implying a continuous probability from an ML model. In the RuleOnlyPath it is one of three hardcoded values (0.0 / 0.3 / 0.8). Downstream consumers (e.g. shadow dashboard) may treat it as a real probability.

**Recommended amendment**:
- Add a `scoring_mode` field to `FraudDecision` (`rule-only` | `ml`) so consumers know how to interpret `fraud_score`.
- Document in Spec 004 / Spec 006 that `fraud_score` is a heuristic sentinel in `rule-only` mode and must not be used for threshold-based decisions until the ML path is live.
- The Avro schema comment should note the three sentinel values and that `model_version = "rule-only"` is the signal.

---

### SD-009 — Rules loaded once at startup with no hot-reload; ImportError silently disables all scoring

**Where**: `pipelines/processing/job.py` lines 204–221

**Drift — no hot-reload**: `RuleLoader.load()` is called once at job startup. If `rules.yaml` changes (e.g. a rule is disabled, thresholds are updated, or a new shadow rule is promoted to active), the Flink job must be fully restarted to pick up the change. There is no live-reload mechanism. A rule change deployed to disk is invisible to the running job until restart — a silent correctness gap with no observable signal.

**Drift — ImportError swallowing**: Lines 219–221 catch `ImportError` and log a warning, allowing the job to continue. If the `pipelines.scoring` package is missing or has a broken dependency (e.g. a bad release, missing transitive import), the entire fraud scoring pipeline is silently disabled. Every transaction flows through as un-scored. There is no metric increment, no alert, no operator notification. The `logger.warning` line is the only signal, and it only fires at startup — not on every transaction.

**Recommended amendment**:
- Document in Spec 003 that rules are static for the lifetime of a job run. Rule changes require a rolling restart of the Flink job.
- Add a Prometheus gauge `scoring_enabled{job="flink"}` (1 = scoring active, 0 = disabled). Set it to 0 in the ImportError branch and alert when it is 0 for more than 60 seconds.
- Consider converting the `ImportError` catch to a hard failure: if the scoring package cannot be imported, the job should abort rather than continue silently. The current silent-continue behavior was designed for incremental feature rollout; document the intent so it is not mistaken for production-safe behavior.
- If live rule reload is ever needed, document it as a distinct operational concern requiring either Flink job update (re-submit with new config) or a management API that reloads the `RuleEvaluator` instance in place.

---

### SD-010 — Shadow scoring path is a stub: evaluated but never persisted

**Where**: `pipelines/scoring/rules/evaluator.py` · `ShadowRuleEvaluator`; `pipelines/scoring/sinks/shadow_decisions_kafka.py` · `ShadowDecisionKafkaSink`; `pipelines/scoring/job_extension.py` · `wire_rule_evaluator`; `analytics/app/pages/08_shadow_scoring.py`

**Drift**: The shadow scoring path has the same structural gap relative to the live path that motivated this session. The live path follows a complete chain: `RuleEvaluator.dispatch()` → `_build_fraud_decision()` → `_IcebergSinkFunction` (writes `iceberg.fraud_decisions`) → analytics. The shadow path has analogous components but none of them are wired into `wire_rule_evaluator`:

- `ShadowRuleEvaluator` (evaluator.py:146) — class exists, unit-tested, **never called** in the live pipeline.
- `ShadowDecisionKafkaSink` (sinks/shadow_decisions_kafka.py) — Kafka producer for `txn.shadow.decisions` exists with a complete Avro schema (`shadow-decision-v1.avsc`), **never called**.
- `IcebergShadowDecisionsSink` — **does not exist**. The analytics page (`08_shadow_scoring.py`) reads from `iceberg.shadow_decisions` via DuckDB/PyIceberg but nothing writes to that table. Every page load returns "No shadow decision data".
- `_build_shadow_record()` pairing function — **does not exist** at the `wire_rule_evaluator` level. `ShadowDecisionKafkaSink._build_record()` pairs a `FraudDecision` with a `ShadowEvaluationResult` internally, but there is no wiring function that calls it.

The result: shadow rules are counted in Prometheus (`record_shadow_trigger`, `record_shadow_fp`) but produce no durable artifact. There is no way to analyse shadow rule performance before promoting a rule to active.

**Recommended amendment — mirror the live path exactly**:

1. **`IcebergShadowDecisionsSink`** (new file: `pipelines/scoring/sinks/iceberg_shadow_decisions.py`): mirrors `IcebergDecisionsSink` structure — buffered writes, circuit breaker (fail_max=3, reset_timeout=30s), in-batch dedup by `transaction_id`, DLQ events on failure, PyArrow schema matching the 15 fields in `shadow-decision-v1.avsc`. Target table: `default.shadow_decisions`.

2. **`_build_shadow_record(txn, fraud_decision, shadow_result, config)` helper** in `job_extension.py`: maps `(FraudDecision, ShadowEvaluationResult)` → shadow record dict. Computes `score_delta = shadow_score - production_score` and `decision_mismatch` flag. This is the equivalent of `_build_fraud_decision()` for the shadow path.

3. **Wire inside `wire_rule_evaluator()`**: after `_evaluate()` produces `(alert, decision)`, run `ShadowRuleEvaluator.dispatch(txn)` on the same enriched transaction, call `_build_shadow_record()`, and feed the result to both `ShadowDecisionKafkaSink` (Kafka transport, for event-driven consumers) and `IcebergShadowDecisionsSink` (durable store, for promotion analysis). Both sinks should follow the same error isolation pattern as `_AlertSinkFunction`: Kafka failures propagate (back-pressure), Iceberg failures are DLQ'd and do not stall the pipeline.

4. **Promotion gate criteria** (spec gap): `08_shadow_scoring.py` exposes mismatch rate and score delta but defines no threshold for promotion. Spec 003 should define: the mismatch rate ceiling and false-positive rate ceiling that make a shadow rule safe to promote to active. Without this, the dashboard has data but no actionable signal.

5. **`rule_set_version` and `shadow_rule_set_version`** are passed as `"unknown"` in `ShadowDecisionKafkaSink.emit()` today. These should be derived from a version field in `rules.yaml` or a hash of the active/shadow rule set — otherwise the version filters in `08_shadow_scoring.py` are useless.

---

### SD-011 — Rule lifecycle has no promotion record; `rule_set_version` is opaque

**Where**: `pipelines/scoring/rules/schemas.py` · `RuleDefinition`; `pipelines/scoring/job_extension.py` · `wire_rule_evaluator`; `rules/rules.yaml`

**Drift**: Two gaps share the same root cause — there is no machine-readable record of when a rule changed state or why.

**Gap A — promotion record**: When a shadow rule is promoted to active, the decision is implicit: someone edits `mode: shadow` → `mode: active` in `rules.yaml` and merges a PR. Git records *what* changed and *when*, but not *why*. The observation data that justified the promotion (mismatch rate, false-positive ratio, transaction volume seen) lives only in the analytics dashboard query window and is not preserved. Six months later the reasoning is unrecoverable.

**Gap B — `rule_set_version` opaque**: Both `ShadowDecisionKafkaSink` and `IcebergDecisionsSink` emit `rule_set_version = "unknown"`. Every decision record in Iceberg and Kafka carries an empty version tag, making the version-filter UI in `08_shadow_scoring.py` non-functional. There is no mechanism to tie a decision record back to the exact rule snapshot that produced it.

**Recommended amendment**:

1. **`promotion_record` optional block in `rules.yaml`**: Add an optional `promotion_record` mapping to `RuleDefinition`. `RuleLoader` ignores absent blocks (backward-compatible). At promotion time, the analyst appends the block to the YAML entry and commits it together with the mode change — the promotion evidence is permanently co-located with the rule definition in git.

   Minimal schema:
   ```yaml
   promotion_record:
     promoted_at: "YYYY-MM-DD"
     shadow_transactions_seen: <int>
     shadow_duration_days: <int>
     shadow_mismatch_rate: <float>   # fraction, e.g. 0.012
     reviewed_by: "<analyst>"
     notes: "<free text>"
   ```

   This is a v1 artifact: no service, no DB, no schema migration required. The YAML diff in git is the audit trail. Promotion gate thresholds defined in Spec 003 (SD-010 item 4) determine whether the values in `promotion_record` clear the bar.

2. **`rule_set_version` via git SHA**: At job startup, derive `rule_set_version` as the git object hash of `rules.yaml`:
   ```python
   import subprocess
   rule_set_version = subprocess.check_output(
       ["git", "hash-object", scoring_config.rules_yaml_path],
       text=True,
   ).strip()
   ```
   This is zero overhead, always unique per file content, and fully reproducible — `git show <sha>:rules/rules.yaml` recovers the exact rule snapshot. Pass `rule_set_version` into `wire_rule_evaluator` so both sinks (`ShadowDecisionKafkaSink`, `IcebergDecisionsSink`) stamp every record with it instead of `"unknown"`.

3. **Deferred to v2**: A dedicated rule-lifecycle service (state machine, approval workflow, DB-backed audit log) is valid if a compliance or governance requirement demands it. At the current stage it is over-engineering — git history plus `promotion_record` in the YAML gives an immutable, auditable trail without introducing a new operational dependency.

---

### SD-012 — Scoring pipeline DLQ is a log sink, not a Kafka sink; failed decisions are unrecoverable

**Where**: `pipelines/scoring/sinks/iceberg_decisions.py` · `_emit_dlq_event`; `pipelines/replay/source_adapters.py` · `DLQSourceAdapter`; `pipelines/replay/models.py` · `DLQSourceConfig`

**Intent**: The DLQ exists to capture failed records with enough context to understand why they failed and reprocess them — either into Iceberg (historical storage) or back through the scoring pipeline after the root cause is fixed.

**Drift**: `_emit_dlq_event` in `IcebergDecisionsSink` writes a structured JSON line to stderr and stops there. When an Iceberg write fails (timeout, circuit open, buffer overflow), the `FraudDecision` records in that batch are permanently lost. There is no mechanism to recover or reprocess them.

The replay pipeline already has the infrastructure to consume from a Kafka DLQ topic (`DLQSourceAdapter`, `DLQSourceConfig`). The ingestion pipeline uses this path: failures write to `txn.api.dlq`, the analytics dashboard has a DLQ Inspector page, and the replay job can re-drive those events. The scoring sink has a structural lookalike (`_DLQEvent`, `_emit_dlq_event`) that mimics the pattern but emits to a logger only — it never reaches Kafka.

**Specific consequence**: the circuit breaker in `IcebergDecisionsSink` trips after 3 consecutive failures and stays open for 30 seconds. During a 30-second outage every `FraudDecision` record silently disappears. There is no way to reconstruct which transactions were affected or replay them.

**Recommended amendment**:

1. **Wire scoring failures to Kafka DLQ**: `_emit_dlq_event` should produce to a Kafka topic instead of (or in addition to) logging. Use `txn.scoring.dlq` as a separate topic — keeps ingestion failures (schema/validation errors) distinct from scoring failures (Iceberg I/O errors) so ops can triage them independently and the replay strategy differs.

2. **DLQ envelope contract**: The envelope written to `txn.scoring.dlq` must carry enough to reprocess: `transaction_id`, `original_batch` (serialized decision records), `reason` (timeout / circuit_open / buffer_full), `failed_at` timestamp, and `rule_set_version`. The `DLQSourceAdapter` in the replay pipeline already expects `source_topic` and `original_topic` fields — match that envelope shape.

3. **Historical storage for DLQ events**: Failed decisions that land in `txn.scoring.dlq` should eventually be written to an Iceberg table (`default.scoring_dlq`) so they are queryable even after Kafka retention expires. This is the same pattern as the ingestion pipeline's DLQ Inspector reading from its Iceberg-backed view.

4. **Replay path**: `DLQSourceConfig.dlq_topic` currently defaults to `txn.api.dlq`. Add `txn.scoring.dlq` as a supported source in the replay job so operators can re-drive failed decisions after an Iceberg outage is resolved.

5. **Prometheus alert**: Add a `scoring_dlq_events_total` counter (already a pattern in `pipelines/processing/metrics.py`). Alert when rate > 0 for more than 60 seconds — that indicates the circuit breaker is open or Iceberg is persistently unavailable.

---

### SD-013 — `channel` field validated from payload but hardcoded "API" in published event

**Where**: `pipelines/ingestion/api/producer.py` · `validate_field_values` (line 98–100); `TransactionEventBuilder.build` (line 163)

**Drift**: `validate_field_values` validates the caller-supplied `channel` field against `VALID_CHANNELS = {"POS", "WEB", "MOBILE", "API"}` — implying the pipeline is designed to handle transactions from multiple channels. But `TransactionEventBuilder.build()` overwrites the validated value with the hardcoded string `"API"` when constructing the Kafka event. The published event always carries `channel = "API"` regardless of what the caller sent. Downstream systems — enrichment operators, rule evaluation, Iceberg analytics — cannot distinguish POS from WEB from MOBILE transactions; channel-based fraud rules or analytics filters would silently operate on wrong data.

**Consequence**: The validation is meaningless for channel — it checks a value that is immediately discarded. Any rule that conditions on `channel` (e.g., "flag high-value MOBILE transactions") would never fire for non-API channels even if the raw transaction originated from one.

**Architecture clarification**: The platform uses one producer per channel. Multiple producer instances may run within the same channel for horizontal scaling. Therefore:

- The hardcoded `"channel": "API"` in `TransactionEventBuilder.build()` is **correct** — this is the API channel producer.
- The `VALID_CHANNELS = {"POS", "WEB", "MOBILE", "API"}` check is the **bug** — it implies this producer accepts all channels and silently discards whatever the caller sends.

**Recommended amendment**:
- Remove `channel` from the validated request fields entirely — callers have no business sending it. The producer assigns it from config or an env var at startup.
- Delete `VALID_CHANNELS` from the API producer (and all channel producers). There is nothing to validate: the channel is not caller-supplied.
- `TransactionEventBuilder.build()` reads `self._channel` (set at construction time from config) instead of hardcoding `"API"`. This makes the same builder reusable across all channel producers without modification.
- Document the producer-assigned channel contract in `TransactionEventBuilder`'s docstring.

---

### SD-014 — Management API promotes/demotes rules in YAML only; running Flink job is unaffected

**Where**: `pipelines/scoring/management_api.py` · `promote_rule`, `demote_rule`, `DemotePromoteResponse`; `pipelines/scoring/rules/evaluator.py` · `RuleEvaluator`; `pipelines/processing/job.py` · `wire_rule_evaluator`

**Intent**: Promote and demote should take effect immediately in the running Flink pipeline. An operator calling `POST /rules/BURST_COUNT_5M/promote` expects that rule to start influencing decisions and generating alerts without a Flink job restart.

**Drift**: The management API and the Flink job run as separate OS processes with no inter-process communication. `promote_rule` updates `_rules_dict` (the API's own in-memory state) and writes `rules.yaml` to disk. The Flink job's `RuleEvaluator` instance — loaded once at startup from `RuleLoader.load()` — is never notified and never updated. Rule changes are only picked up on the next Flink job restart.

The codebase already signals this gap: `DemotePromoteResponse.config_event_published` is always `False` with the comment `# v1 always False (no Kafka)` — the designers anticipated a Kafka-based config event path for v2 but did not build it.

**Consequence**: An operator promotes a rule, receives a 200 OK, and believes the pipeline is now blocking the targeted fraud pattern. The pipeline is not. This is an operational safety hazard: missed fraud during the window between the API call and the next Flink restart.

**Recommended amendment**:

1. **Kafka control topic** (`txn.rule.config`): The management API publishes a `rule_mode_change` event to this topic after every successful YAML write. The Flink job has a control stream that consumes from `txn.rule.config` and calls a `reload()` method on the `RuleEvaluator` instance inside `_rules_lock`. This is the architecture the `config_event_published` field anticipates. When implemented, flip `config_event_published = True` in the response.

2. **`RuleEvaluator.reload(rules: list[RuleDefinition])` method**: Adds an atomic swap of the internal rule list under a lock. The Flink operator that wraps the evaluator calls this when a control event arrives. The method must be thread-safe — the Flink task thread and the control consumer thread both access the evaluator.

3. **Until v2**: Document explicitly in the management API docstring that mode changes take effect on the next Flink job restart, not immediately. Remove the ambiguity — operators should not expect immediate effect.

4. **Prometheus gauge `rule_evaluator_generation`**: Increments on each reload. Makes it observable when the running Flink job has picked up a config change, and when it hasn't.

---

### SD-015 — `_write_rules_to_yaml` strips `promotion_record` blocks on every write

**Where**: `pipelines/scoring/management_api.py` · `_write_rules_to_yaml` (line 289); `pipelines/scoring/rules/models.py` · `RuleDefinition`

**Drift**: `_write_rules_to_yaml` serializes rules via `rule.model_dump_json()` → `json.loads()` → `yaml.dump()`. This round-trip through the Pydantic model means only fields declared in `RuleDefinition` survive. Any YAML block not in the model — including the `promotion_record` block defined in SD-011 — is silently stripped on every promote/demote call.

A manual workflow breaks: analyst adds `promotion_record` to `rules.yaml`, commits it. Then another analyst calls `POST /rules/SOME_RULE/demote` six months later. The YAML write in the management API rewrites the file without `promotion_record`. The promotion history is gone.

**Recommended amendment**:
- Add `promotion_record` as an optional field to `RuleDefinition` (Pydantic model). `RuleLoader.load()` can ignore absent blocks; the management API's round-trip will then preserve the field because it's part of the model.
- Alternatively, use a read-modify-write strategy: load the raw YAML, update only the `mode` field for the affected rule, and write back — preserving all unrecognised fields. This is more fragile but doesn't require a model change.
- The Pydantic approach is strongly preferred: it makes `promotion_record` a first-class part of the rule schema, queryable and validatable, rather than a free-form YAML annotation.

---

### SD-016 — Replay job uses a hardcoded `dummy_scoring` function; all replay results are fabricated

**Where**: `pipelines/scoring/management_api.py` · `create_replay_job` (lines 672–683)

**Drift**: The `create_replay_job` endpoint starts every replay job with a hardcoded `dummy_scoring` function:
```python
def dummy_scoring(event: dict) -> dict:
    return {"decision": "review", "score": 0.5, "triggered_rules": ["rule_001"]}
```
Every event in every replay job receives `decision = "review"`, `score = 0.5`, and `triggered_rules = ["rule_001"]` regardless of the transaction content or the configured rule set. The replay feature is non-functional: it stores fabricated results, not actual rule evaluations. The `rule_set_version` parameter in `CreateReplayJobRequest` and `use_shadow_rules` flag have no effect.

**Recommended amendment**:
- `create_replay_job` must build a real `scoring_fn` from the current `_rules_dict` and pass it to `ReplayJob.start()`. The scoring function should instantiate a `RuleEvaluator` with the rules loaded at the time the replay job is created (snapshot semantics — the replay evaluates against a fixed rule set, not a moving target).
- If `use_shadow_rules` is True, also run `ShadowRuleEvaluator` and include shadow results in the replay output.
- The `dummy_scoring` path should raise `NotImplementedError` rather than silently returning plausible-looking fabricated data — fabricated results are worse than an obvious error.

---

### SD-017 — `MANAGEMENT_API_KEY` unset silently disables authentication on all mutating endpoints

**Where**: `pipelines/scoring/management_api.py` · `verify_api_key` dependency

**Drift**: `MANAGEMENT_API_KEY` is read from the environment at startup with no default assertion. When the variable is not set, the dependency resolves to `None` and the header comparison `secrets.compare_digest(api_key, MANAGEMENT_API_KEY)` evaluates as `compare_digest(None, None)` — every caller is authenticated. All mutating endpoints (promote, demote, circuit breaker override, replay creation) become publicly accessible without any key. There is no startup log warning, no health check exposure, and no placeholder that forces operators to set a real value before deploying. The failure mode is silent.

**Operational excellence gap** (network-level controls are the primary defence; this is a defence-in-depth and ops-safety concern, not an immediate runtime risk).

**Recommended amendment**:
- Add a startup assertion in the FastAPI `lifespan` context:
  ```python
  if not MANAGEMENT_API_KEY:
      raise RuntimeError("MANAGEMENT_API_KEY env var is required — service refuses to start unauthenticated")
  ```
- Log `"Management API authentication enabled"` (without the key value) at INFO on startup so operators can confirm the guard is active.
- Document the required env var in the service `README` and Docker Compose example.

---

### SD-018 — `_replay_jobs` is in-memory only; server restart silently loses all replay job state

**Where**: `pipelines/scoring/management_api.py` · module-level `_replay_jobs: dict[str, ReplayJob] = {}`

**Drift**: Replay job state (job ID, status, progress, results) lives exclusively in the process-local `_replay_jobs` dict. A pod restart, OOM kill, or rolling deployment wipes every job. The `GET /replay/jobs/{job_id}` endpoint immediately returns 404 for any job created before the restart — indistinguishable from a job that never existed. Operators have no way to recover results or determine whether a replay job completed successfully.

**Recommended amendment**:
- Persist replay job metadata (job_id, status, rule_set_version, source_topic, event_count, created_at, completed_at, error) to PostgreSQL (`replay_jobs` table) — same DB used by `AlertPostgresSink`.
- `GET /replay/jobs/{job_id}` reads from DB first; in-memory state supplements with live progress only.
- v1 minimum: write row on creation, update status on completion/failure — no streaming progress required.

---

### SD-019 — Broker delivery failures write unrepayable DLQ records (`original_payload="{}"`)

**Where**: `pipelines/ingestion/api/producer.py` · `_delivery_callback` (line 323)

**Drift**: The `confluent_kafka` async delivery callback fires after `produce()` returns. At that point the original masked event dict is no longer in scope, so `send_to_dlq` is called with `original_payload="{}"`. The DLQ record contains the error type and source topic but no event data. An operator inspecting `txn.api.dlq` after a broker failure cannot reconstruct or replay the affected transactions — they know *some* event failed but not which one.

**Consequence**: Replay (via `DLQSourceAdapter`) requires `original_payload` to re-drive the event through the pipeline. An empty payload makes broker-failure DLQ records effectively unrecoverable.

**Recommended amendment**:
- Capture the masked event dict at `produce()` time and bind it into the callback via a closure:
  ```python
  import json, functools

  serialized = json.dumps({k: v for k, v in event.items() if k not in ("card_number",)}, default=str)
  callback = functools.partial(self._delivery_callback, serialized_payload=serialized)
  self._producer.produce(topic=TOPIC, key=..., value=event, on_delivery=callback)
  ```
- `_delivery_callback` signature becomes `(self, err, msg, *, serialized_payload: str)` and passes `serialized_payload` to `send_to_dlq` instead of `"{}"`.
- The payload at this point is already masked (PAN → BIN6/last4, IP → subnet), so writing it to the DLQ is safe.

---

### SD-020 — Ingestion API uses single-threaded stdlib `HTTPServer`; cannot scale under concurrent load

**Where**: `pipelines/ingestion/api/producer.py` · `run_server` (line 466); `_RequestHandler` (line 399)

**Drift**: The API producer uses Python's `http.server.HTTPServer` — single-threaded, blocking I/O. Every incoming request is processed serially: a second connection waits at the OS socket buffer until the first completes. There is no request timeout on `rfile.read()` — a slow or stalled client stalls the entire server. Under the intended load profile (multiple producer instances sending high-volume message transfers concurrently) this architecture will serialize requests and degrade throughput to a single-connection rate.

**Intent**: The ingestion API must be as scalable as possible — async I/O, configurable connection limits, and request timeouts are all required.

**Recommended amendment**:
- Migrate to **FastAPI + uvicorn** (already used in `management_api.py` — no new dependencies). The existing `validate_required_fields`, `validate_field_values`, `TransactionEventBuilder`, and `ProducerService` are all reusable without modification.
- Run with `uvicorn` + multiple workers (via `gunicorn -k uvicorn.workers.UvicornWorker`) for CPU-bound parallelism across cores.
- Set `uvicorn` `timeout_keep_alive`, `limit_concurrency`, and `limit_max_requests` to bound connection lifetime and back-pressure under spike load.
- Add a `POST /v1/transactions` FastAPI route that mirrors the existing `_RequestHandler.do_POST` logic — validation → build → publish → return `PublishResult`.
- Expose liveness (`GET /healthz`) and readiness (`GET /readyz`) endpoints so Kubernetes can drain connections cleanly before pod termination.

---

### SD-021 — Ingestion API has no SIGTERM handler; buffered messages are lost on container shutdown

**Where**: `pipelines/ingestion/api/producer.py` · `run_server` (lines 491–494)

**Drift**: `run_server` handles `KeyboardInterrupt` (SIGINT) and calls `service.flush()`. In container environments (Kubernetes, Docker) pods are terminated with `SIGTERM`. `HTTPServer.serve_forever()` has no SIGTERM handler — the process is killed immediately, and any messages buffered in librdkafka's internal queue are silently dropped. Every rolling deployment or pod eviction is a potential message loss event.

**Recommended amendment**:
```python
import signal

def _shutdown(service: ProducerService, server: HTTPServer, *_):
    service.flush()
    server.shutdown()

signal.signal(signal.SIGTERM, lambda *a: _shutdown(service, server, *a))
```
Register before `server.serve_forever()`. The `KeyboardInterrupt` handler remains for local dev. When migrating to FastAPI + uvicorn (SD-020), uvicorn's `--graceful-timeout` handles this natively — no manual signal wiring required.

---

### SD-022 — Feature serving timeout abandons threads; single-worker executor queues under sustained Feast latency

**Where**: `pipelines/scoring/clients/feature_serving.py` · `get_features` (lines 98–100); `FeatureServingClient.__init__` (`executor_workers=1`)

**Drift**: `future.result(timeout=0.003)` raises `TimeoutError` after 3 ms but does not cancel the running thread — `future.cancel()` is never called. The `ThreadPoolExecutor` has `max_workers=1`. Under sustained Feast latency the single thread stays occupied by an abandoned fetch, and every subsequent `get_features` call submits a new future that queues behind it. The queue is unbounded, so under pressure memory grows and new submissions eventually block waiting for the single thread to become available. The 3 ms SLA is a technical best-guess, not a measured Feast Redis P99.

**Recommended amendment**:
- Call `future.cancel()` in the `TimeoutError` handler immediately after the timeout fires. This prevents queue build-up for futures that haven't started yet.
- Increase `executor_workers` to at least 2 (one running, one ready) so a single abandoned future does not stall the next transaction.
- Cap the executor queue by wrapping submission in a try/except for `RuntimeError` or by using a `BoundedSemaphore` to reject new fetches when the executor is saturated — fail fast to zero-defaults rather than accumulating backlog.
- Derive the timeout from a measured Feast Redis P99 (target: ≤ 3 ms at P99, with 5 ms as the hard timeout). Expose the timeout as a `FEATURE_STORE_TIMEOUT_MS` env var so it can be tuned per environment without a code change.
- **Note**: SD-023 supersedes this entry for the scoring path — `FeatureServingClient` should be deleted from `job_extension.py` entirely per ADR-005. SD-022 remains relevant only if `FeatureServingClient` is used in future standalone consumers.

---

### SD-023 — `_FeatureEnrichmentFunction` and Feast call in scoring path are dead code contradicting ADR-005

**Where**: `pipelines/scoring/job_extension.py` · `_FeatureEnrichmentFunction` (lines 33–85); `_FlinkFeatureEnrichmentFunction` (lines 112–138); `_FEATURE_ZERO_DEFAULTS` (lines 12–30)

**Drift**: ADR-005 documents the decision to remove `_FlinkFeatureEnrichmentFunction` from the scoring path. The rule evaluator runs co-located inside the Flink enrichment job; by the time `wire_rule_evaluator` is called, the enriched transaction dict already carries all velocity, geolocation, and device features. Fetching those same features from Feast is redundant and introduces a failure mode: a Feast timeout replaces live features with zero-value defaults, causing rules to miss real fraud signals even though correct values are already in the record.

The code in `job_extension.py` was not updated to reflect ADR-005. `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and `_FEATURE_ZERO_DEFAULTS` remain in the file and are wired into `wire_rule_evaluator`. The Feast call fires on every transaction in production.

**Recommended amendment**:
- Delete `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and the `enriched_stream.map(_FlinkFeatureEnrichmentFunction(), ...)` line from `wire_rule_evaluator`.
- Delete `_FEATURE_ZERO_DEFAULTS` from `job_extension.py` — zero defaults for the scoring path are no longer needed since the enriched dict always carries populated values.
- Update `tests/unit/scoring/test_job_extension_safety.py` — the `TestFeatureEnrichmentFallback` class tests the deleted code path and should be removed.
- `FeatureServingClient` in `pipelines/scoring/clients/` remains for future standalone consumers but must not be imported or called from the scoring pipeline.

---

### SD-024 — Circuit breaker state endpoint reads private pybreaker attributes; brittle across version upgrades

**Where**: `pipelines/scoring/management_api.py` · `get_circuit_breaker_state` (lines 528–568); `pipelines/scoring/circuit_breaker.py` · `FraudCircuitBreakerListener`

**Drift**: `get_circuit_breaker_state` retrieves `last_failure_time` and `next_probe_time` by reading pybreaker private attributes (`cb._last_failure_time`, `cb._opened_at`) via defensive `getattr` with a `try/except`. The comments acknowledge these are private and version-dependent. A pybreaker minor version bump that renames or removes these attributes silently degrades the endpoint: both fields return `null` with no indication the data is unavailable rather than genuinely absent. The endpoint is informational today; degraded silently, operators would not notice until they act on stale state during an incident.

The fix is already in scope: `FraudCircuitBreakerListener` receives `state_change` and `failure` callbacks from pybreaker's public listener API, which is stable across versions.

**Recommended amendment**:
- Add `opened_at: float | None = None` and `last_failure_time: float | None = None` as instance attributes on `FraudCircuitBreakerListener`.
- Populate `opened_at = time.time()` inside `state_change` when `new_state == "open"`; clear it when transitioning to `"closed"`.
- Implement the `failure` callback (`def failure(self, cb, exc)`) and record `self.last_failure_time = time.time()`.
- Store a reference to the listener instance on `MLCircuitBreaker` (e.g., `self._listener`).
- `get_circuit_breaker_state` reads `_circuit_breaker._listener.opened_at` and `_circuit_breaker._listener.last_failure_time` — no private pybreaker access, no `try/except` required.
- As a secondary fix: rename `ml_fallback_decisions_total` → `ml_circuit_open_calls_total` to match what it actually measures (calls attempted while the circuit was open, not fallback decisions produced).
