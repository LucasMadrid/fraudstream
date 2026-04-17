# Tasks: Operational Excellence — Architecture Gaps & Partials

**Input**: Design documents from `specs/004-operational-excellence/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**Organization**: Tasks are grouped by user story to enable independent implementation and testing. The foundational phase (Phase 2) MUST complete before US3 (circuit breaker) and US4 (shadow mode) can begin. US1 and US2 can start immediately after foundation.

**Tests**: No TDD tasks generated (not requested in spec). Unit test gap audit included in US4 (FR-023).

**Blocked FRs** (excluded from implementation tasks):
- FR-005 / SC-006: Blocked by TD-007 (Iceberg sinks not implemented) — architecture note only (T040). **Constitution Principle IX applies**: `iceberg.enriched_transactions` and `iceberg.fraud_decisions` are non-negotiable sinks; these FRs are NOT waived — they are sequenced after a dedicated Iceberg sink spec delivers the required tables.
- FR-019: v1 is architecture-only; deferred implementation (T034). Blocked by same TD-007 dependency (requires `iceberg.fraud_decisions`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: Maps to user story (US1–US6)
- Exact file paths in every description

---

## Phase 1: Setup (Directory Structure)

**Purpose**: Create directory scaffolding so all subsequent tasks can write files to correct locations without mkdir ambiguity.

- [X] T001 Create docs/ subdirectory tree: `docs/runbooks/`, `docs/adr/`, `docs/deployment/`, `docs/postmortems/`
- [X] T002 [P] Create `tests/chaos/` directory and `scripts/` directory at repo root (if not already present)

**Checkpoint**: All output directories exist — subsequent tasks can write files immediately.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infrastructure code that MUST exist before the circuit breaker (US3) and shadow mode (US4) can be implemented. US1 and US2 do NOT depend on this phase and may start in parallel with it.

**⚠️ CRITICAL**: T017 (circuit breaker) and T024 (shadow evaluator) are blocked until this phase completes.

- [X] T003 Extend `pipelines/scoring/config.py` — add 6 new fields to `ScoringConfig` dataclass per `contracts/scoring-config-extension.md`: `ml_serving_url: str` (env: `ML_SERVING_URL`, default `"http://localhost:5001"` per FR-025 — cloud deployments use HTTPS), `cb_error_threshold: int` (env: `CB_ERROR_THRESHOLD`, default `3`, min 1), `cb_error_window_seconds: int` (env: `CB_ERROR_WINDOW_SECONDS`, default `10`, must be > 0 — the rolling window within which N consecutive errors open the circuit per FR-014), `cb_open_seconds: int` (env: `CB_OPEN_SECONDS`, default `30`, must be > 0), `cb_probe_timeout_ms: int` (env: `CB_PROBE_TIMEOUT_MS`, default `5`, range [1, 50]), `redis_url: str` (env: `REDIS_URL`, default `"redis://localhost:6379/0"`). Add dataclass validation (raise `ValueError` on constraint violations).

- [X] T004 [P] Create `pipelines/scoring/ml_client.py` — implement per `contracts/ml-client.md`: `MLClientError(Exception)`, `MLModelClient(ABC)` with `score(self, txn: dict) -> float` abstract method, `StubMLModelClient(MLModelClient)` with `stub_score: float = 0.0` and `fail_on_call: bool = False` constructor params (raises `MLClientError` immediately when `fail_on_call=True`, returns `stub_score` otherwise).

- [X] T005 [P] Add Redis service to `infra/docker-compose.yml` — service name `redis`, image `redis:7-alpine`, port `6379:6379`, named volume `fraudstream_redis_data:/data`, `--save 60 1` persistence flag, network `fraudstream`, healthcheck `redis-cli ping`.

- [X] T006 [P] Extend `pipelines/scoring/rules/models.py` — add `RuleMode(StrEnum)` enum with values `active = "active"` and `shadow = "shadow"`; add `mode: RuleMode = RuleMode.active` field to `RuleDefinition` Pydantic model (preserves `ConfigDict(extra="forbid")`; existing YAML files without `mode:` parse as `active` via Pydantic default). Reference `contracts/rule-definition-extension.md` for the exact field position. **`rule_id` format validation** (per FR-022 path-traversal protection): add a Pydantic `field_validator` on `rule_id` that enforces the pattern `^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$` — raise `ValueError` with message `"rule_id must be 2–64 lowercase alphanumeric characters and hyphens"` on violation; this constraint prevents path traversal when `rule_id` is interpolated into the Alertmanager webhook URL.

- [X] T007 [P] Create `pipelines/scoring/telemetry.py` — bootstrap OTel `TracerProvider` with service name `"fraudstream-scoring"` (per FR-027 — must match exactly; this is the canonical service name used in Prometheus metric labels and Grafana trace queries; parallel to `pipelines/ingestion/api/telemetry.py` and `pipelines/processing/telemetry.py`); expose `fraud_rule_evaluation_span(transaction_id: str, channel: str, rule_count: int)` context manager that sets the following span attributes per FR-027: `fraud.transaction_id` (string, value of `transaction_id` arg), `fraud.channel` (string, value of `channel` arg — e.g. `"API"` / `"ATM"` / `"POS"`), `fraud.rule_count` (int, number of rules evaluated), `fraud.decision` (string, set by the caller after evaluation — `"BLOCK"`, `"FLAG"`, or `"ALLOW"`); also sets `fraud.latency_ms` (float, wall-clock duration in ms) as a supplementary attribute for the sampling decision (referenced in T018). Note: per-rule `rule.id` and `rule.mode` attributes belong on individual child spans inside the evaluator loop — the top-level evaluation span uses the transaction-scoped attributes above. **`fraud.decision` value mapping**: `EvaluationResult.determination` uses `"clean"` / `"flag"` / `"block"` (lowercase); callers MUST map to `"ALLOW"` / `"FLAG"` / `"BLOCK"` (uppercase) before setting this span attribute — the scoring engine decision layer is responsible for this mapping before invoking the context manager. Configure head-based sampling: `DECISION_TYPE_SAMPLE_RATES = {"BLOCK": 1.0, "error": 1.0, "ALLOW": 0.01}` with a `SamplingDecision` implementation that returns `DROP` for ALLOW at 99% and `RECORD_AND_SAMPLE` for BLOCK/error at 100%. Add `trace_sampling_rate{decision_type}` Prometheus gauge to document the configured rates.

**Checkpoint**: Foundation complete — T017 (circuit breaker) and T024 (shadow evaluator) are now unblocked.

---

## Phase 3: User Story 1 — On-Call DLQ Alert Response (Priority: P1)

**Goal**: Give an on-call engineer unfamiliar with the system a complete runbook suite to identify, classify, and resolve DLQ alerts without tribal knowledge or expert escalation.

**Independent Test**: Pre-seed `txn.processing.dlq` with one schema-invalid event and one late-beyond-window event. Give a new engineer only the runbook and `replay-dlq-message.sh`. They must: identify both events (< 5 min), replay the schema-invalid one after fix, discard the late event, document in incident log. Measure time and error rate.

- [X] T008 [US1] Create `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md` — structure per FR-007: (a) 4-class error decision tree with classification steps for each type: `schema_invalid`, `deserialization_failure`, `late_event_beyond_window`, `downstream_unavailable`; (b) per-type remediation procedures with exact commands; (c) replay procedure using `replay-dlq-message.sh` with dry-run mode explained; (d) bulk-replay prohibition: if DLQ depth > 100 within 5 minutes → escalate before any replay; (e) end-to-end verification: confirm replayed event appears in `txn.enriched` topic and scoring output within 60s.

- [X] T009 [P] [US1] Create `scripts/replay-dlq-message.sh` — bash script with: required `--target-topic <topic>` flag (error if omitted), `--message-id <offset>` flag, `--dry-run` flag (prints full replay plan including source offset, target topic, payload preview — exits without producing to Kafka), `--source-topic <topic>` flag (default `txn.processing.dlq`); topic-mismatch guard: if `--target-topic` is the same as `--source-topic`, abort with error "Cannot replay to DLQ source — use original source topic"; requires `KAFKA_BOOTSTRAP_SERVERS` env var.

- [X] T010 [P] [US1] Create `docs/runbooks/CHECKPOINT_RECOVERY.md` — structure per FR-008: (1) list checkpoints in MinIO (`mc ls minio/flink-checkpoints/`) with timestamps and sizes; (2) validate checkpoint integrity (checksum verification command against `.metadata` file); (3) Flink SavePoint restore command with correct `--fromSavepoint` syntax; (4) post-restore lag monitoring: `kafka-consumer-groups --describe` loop with target lag < 5s within 60s; (5) post-recovery checklist confirming velocity window state coherence (compare pre/post checkpoint transaction counts for active windows).

- [X] T011 [P] [US1] Create `docs/runbooks/SCHEMA_MIGRATION.md` — structure per FR-009: (1) create new Schema Registry subject version (`curl -X POST .../subjects/<subject>/versions` with full Avro JSON); (2) consumer group migration procedure: pause consumer → drain in-flight messages → update consumer schema reference → restart; (3) straggler detection: script to list consumer groups still registered on old schema version; (4) topic deprecation 30-day procedure: add deprecation notice to subject description, automated straggler check on day 15 and day 30, retirement checklist (confirm zero consumers, delete subject with `--permanent`).

- [X] T012 [P] [US1] Create `docs/runbooks/OBSERVABILITY_RUNBOOK.md` — structure per FR-010: 4 troubleshooting trees, each with a root question, branching conditions, terminal actions: (a) DLQ depth > 0: which pipeline stage? (`txn.ingestion.dlq` vs `txn.processing.dlq` vs `txn.scoring.dlq`) → error type classification → replay or fix-first decision; (b) enrichment latency > 20ms p99: Kafka consumer lag? → GeoIP lookup timeout? → RocksDB state bloat? → checkpoint pending? → each branch has a specific remediation command; (c) analytics dashboard refresh > 2s: Trino query slow? → Kafka consumer group lag on analytics topic? → Iceberg compaction needed? → each has a specific check command; (d) watermark stall: all-partition stall detection using `flink-sql` watermark status, alert configuration for `flink_watermark_lag_ms > 30000`, resolution steps (task restart vs. job rescaling).

**Checkpoint**: US1 complete — on-call engineer has full DLQ response runbook suite and replay tooling.

---

## Phase 4: User Story 2 — CI/CD Schema Enforcement (Priority: P1)

**Goal**: The CI pipeline automatically detects and blocks Avro breaking changes before merge, with a structured error that tells the developer exactly what to fix.

**Independent Test**: Open a PR adding `required string new_field` to an existing Avro schema. Verify: CI fails at schema-validation stage with `BREAKING_CHANGE` error identifying the subject and field; PR cannot be merged. Then open a PR with a new `v2` subject — verify CI passes.

- [X] T013 [US2] Implement and verify `.github/workflows/ci.yml` — apply conditional logic: if `schema-registry-compat` job is absent, create it; if present, update it to set `BACKWARD_TRANSITIVE` (not `BACKWARD`) for all subjects. Apply same conditional approach to each of the 7 pipeline stages — if stage is missing, create it; if present, update it to match the required configuration: stages 1–3 (lint/typecheck, unit tests, schema validation) run in parallel; stage 4 (integration tests) gates on stages 1–3 passing; stage 5 (latency bench) runs only on main-branch PRs (`if: github.base_ref == 'main'`); stage 6 (manual approval) triggers on schema changes or Flink version bumps; stage 7 (Terraform plan/apply) runs non-prod first. Verify schema validation step produces structured output with `subject`, `incompatible_field`, and `remediation` fields on failure. Do not delete any existing stage — only add or update.

- [X] T014 [P] [US2] Verify and update `.github/PULL_REQUEST_TEMPLATE.md` — ensure 6-item compliance checklist is present and correctly worded per FR-006: `[ ] Unit test coverage ≥ 80% (CI gate)`, `[ ] Schema validation passed (if schema change)`, `[ ] Latency SLO verified for hot-path changes (p99 < 100ms)`, `[ ] PII audit passed (no PAN/IP/ssn field names introduced)`, `[ ] Canary policy applied (if scoring engine or model change)`, `[ ] Relevant ADRs confirmed compliant`. If template does not exist, create it.

- [X] T015 [P] [US2] Create three deployment scripts with `--dry-run` flag per FR-004: (a) `scripts/flink-rolling-upgrade.sh` — preserves state checkpoints; dry-run prints upgrade plan (old version, new version, savepoint path, estimated downtime); (b) `scripts/schema-subject-migrate.sh` — consumer group coordination; dry-run prints migration steps and affected consumer groups; (c) `scripts/checkpoint-rollback.sh` — lag recovery verification; dry-run prints rollback target checkpoint and estimated recovery time. Each script validates required env vars before executing.

- [X] T016 [P] [US2] Create Terraform modules in `infra/terraform/` per FR-003 — scope covers shared/local modules only; cloud provider modules (AWS, GCP) are deferred to TD-010: `modules/kafka-topic/` (variables: topic_name, partitions, replication_factor, retention_ms; resource: `confluent_kafka_topic` for local/dev; add `TODO` comment noting AWS target = MSK topic via `aws_msk_*` resources, GCP target = Pub/Sub or Confluent via provider); `modules/schema-subject/` (variables: subject_name, schema_file, compatibility_level; resource: `confluent_schema_registry_schema`); add empty stub directories `infra/terraform/aws/` and `infra/terraform/gcp/` each with a `README.md` documenting the planned managed services per TD-010 (AWS: MSK, Managed Flink, ElastiCache, S3+Glue; GCP: Confluent/Pub/Sub, Dataflow, Memorystore, GCS+Dataplex) and a shared variable interface (`var.kafka_topic_config`, `var.redis_ttl_hours`, `var.flink_job_jar_path`); add a `TODO` comment block in `modules/` for `redis-cluster/` (FR-016) and `iceberg-catalog/` (TD-007). All modules must pass `terraform validate` in CI. Local dev remains docker-compose — no `terraform apply` required for local.

**Checkpoint**: US2 complete — CI blocks breaking schema changes; deployment scripts enable dry-run rollback procedures.

---

## Phase 5: User Story 3 — ML Serving Outage Resilience (Priority: P1)

**Goal**: The scoring engine detects ML serving failures, opens the circuit breaker within 30 seconds, falls back to rule-engine-only decisions within the 100ms latency budget, and pages on-call — all without manual intervention.

**Independent Test**: Stop the ML serving container mid-load. Verify: after 3 consecutive failures within 10s, circuit breaker opens; scoring continues (rule-engine only); `ml_circuit_breaker_state{state="open"}` gauge = 1; `ml_fallback_decisions_total` increments; latency p99 < 100ms; alert fires within 60s.

- [X] T017 [US3] Create `pipelines/scoring/circuit_breaker.py` — implement `MLCircuitBreaker` per `contracts/ml-client.md` circuit breaker integration diagram: wrap `MLModelClient` with `pybreaker.CircuitBreaker(fail_max=cb_error_threshold, reset_timeout=cb_open_seconds)`; override probe call to use `concurrent.futures.ThreadPoolExecutor` with `timeout=cb_probe_timeout_ms / 1000.0` (NOT pybreaker's built-in timeout — see research.md RQ-001); implement `CircuitBreakerListener` subclass that: (a) updates `ml_circuit_breaker_state{state}` gauge (1 for current state, 0 for others) on every state transition, (b) increments `ml_fallback_decisions_total` counter on every OPEN-state call. Expose `score_with_fallback(txn: dict, fallback_score: float = 0.0) -> tuple[float, bool]` method returning `(score, used_fallback)`.

- [X] T018 [P] [US3] Update `pipelines/scoring/telemetry.py` (created in T007) — add tail-based sampling note per FR-011 v1 limitation (research.md RQ-002): add a module-level comment documenting that tail-based sampling requires OTel Collector (not yet in docker-compose); implement v1 approximation using `ParentBased(root=TraceIdRatioBasedSampler(0.01))` for ALLOW with an `AlwaysOnSampler` override for BLOCK/error decisions detected via span attributes; ensure `trace_sampling_rate{decision_type}` gauge is set to configured rates at startup.

- [X] T019 [P] [US3] Create `infra/grafana/provisioning/dashboards/fraud-detection-main.json` — Grafana dashboard JSON with 6 panels per FR-012: (1) latency budget waterfall: stacked bar chart `ingestion_p99 / enrichment_p99 / scoring_p99 / decision_p99` vs. 100ms budget line; (2) DLQ depth by stage: multi-series time-series `kafka_consumer_group_lag` for each DLQ topic; (3) late events within vs. beyond window: two counters side-by-side `late_events_within_window_total` and `late_events_beyond_window_total`; (4) analytics consumer lag: `kafka_consumer_group_lag{group="analytics"}` time-series; (5) checkpoint duration: histogram panel `flink_checkpoint_duration_ms` p50/p95/p99; (6) ML circuit breaker state: stat panel with three colored states from `ml_circuit_breaker_state{state}` gauge. Dashboard `uid: "fraud-detection-main"`, provisioned at path `infra/grafana/provisioning/dashboards/` (per FR-012 note: NOT `monitoring/grafana-dashboards/`).

- [X] T020 [P] [US3] Add late event metrics per FR-013 across two files: (1) **Declare metrics** in `pipelines/processing/metrics.py` alongside existing counters — add `late_events_within_window_total` (Counter, label: `stage`), `late_events_beyond_window_total` (Counter, labels: `stage`, `topic`), `corrected_record_latency_ms` (Histogram, buckets `[1, 5, 10, 25, 50, 100, 250, 500]`, label: `stage`); (2) **Instrument** in `pipelines/processing/operators/velocity.py` inside `VelocityProcessFunction` — when a late event arrives and `event_time < watermark - allowed_lateness_ms`: increment `late_events_beyond_window_total{stage="velocity", topic="txn.api"}` and route to DLQ; when a late event arrives and `watermark - allowed_lateness_ms <= event_time < watermark`: increment `late_events_within_window_total{stage="velocity"}`; on corrected record emission: record `corrected_record_latency_ms{stage="velocity"}` with `time.time() - event_time_seconds` as the observation value. Import new metrics from `pipelines/processing/metrics.py`.

- [X] T021 [P] [US3] Extend `infra/prometheus/alerts/fraud_rule_engine.yml` — add two alert rules: (1) `MLCircuitBreakerOpen`: `expr: ml_circuit_breaker_state{state="open"} == 1`, `for: 0m` (fire immediately), `labels: {severity: "critical"}`, `annotations: {summary: "ML circuit breaker OPEN — scoring in fallback mode", runbook_url: "docs/runbooks/OBSERVABILITY_RUNBOOK.md"}`, PagerDuty routing via existing receiver; (2) `RuleFPRateHigh`: `expr: rate(rule_active_fp_total[1h]) / rate(rule_triggers_total[1h]) > 0.05`, `for: 5m`, `labels: {severity: "warning", rule_id: "{{ $labels.rule_id }}"}`, `annotations: {summary: "Active rule {{ $labels.rule_id }} FP rate > 5% over 1h — auto-demotion pending"}` — `rule_id` MUST be in `labels:` (not `annotations:`) so Alertmanager's `{{ .GroupLabels.rule_id }}` resolves correctly in the T027 webhook URL; this alert fires the Alertmanager webhook to `/rules/{{ .GroupLabels.rule_id }}/demote`.

- [X] T022 [US3] Create `tests/chaos/FAILURE_SCENARIOS.md` — 5 scenarios per FR-015, each with: **Trigger** (exact command or action), **Expected Behavior** (specific metrics and responses), **Pass/Fail Criteria** (measurable thresholds), **Manual Procedure** (step-by-step for staging): (a) Flink single-node failure: `docker stop flink-taskmanager-1` → verify partition redistribution and lag recovery within 60s; (b) ML serving outage: `docker stop ml-stub` → verify circuit breaker opens after 3 failures, `ml_fallback_decisions_total` increments, p99 < 100ms; (c) Kafka broker unavailability: `docker stop kafka-broker-1` → verify DLQ routing and producer backpressure alerts; (d) Checkpoint storage unavailable: `docker network disconnect fraudstream minio` → verify continued processing, `flink_checkpoint_failure_total` alert fires; (e) Analytics consumer crash: `docker stop streamlit` → verify zero impact on scoring latency and throughput (measured via `scoring_latency_ms` p99).

- [X] T023 [P] [US3] Create `infra/terraform/redis/` module per FR-016 — `main.tf` with two workspace-conditional configurations: `terraform.workspace == "local"` → Redis Sentinel config (1 primary `redis-primary`, 1 replica `redis-replica`, sentinel port `26379`, quorum `1`, primary name `"mymaster"`); `terraform.workspace == "cloud"` → Redis Cluster mode (3 shards, 1 replica per shard, `cluster-enabled yes`). Both configurations: `maxmemory-policy allkeys-lru` (correct for a pure Feast feature cache — all keys carry explicit 24h TTLs per constitution; `allkeys-lru` is the OOM safety valve that evicts any key when memory is full; `volatile-lru` is not needed because Redis holds no durable state — circuit breaker state is in-memory Python, rule config is YAML on disk per TD-008), `notify-keyspace-events "Ex"` for key expiry events. Add `outputs.tf` exporting `redis_url` for scoring engine injection.

**Checkpoint**: US3 complete — circuit breaker implemented and tested; observability stack updated; failure scenarios documented.

---

## Phase 6: User Story 4 — Shadow Rule Canary Mode (Priority: P2)

**Goal**: A fraud analyst can deploy a new rule in shadow mode, observe its behavior on live traffic for 24h without blocking transactions, and promote it via UI — with automatic demotion if FP rate exceeds 5% after promotion.

**Independent Test**: Deploy a rule with `mode: shadow`. Replay 10,000 historical transactions including 100 known-legitimate. Verify: rule fires on matching events; zero transactions blocked; `rule_shadow_triggers_total{rule_id=..., mode="shadow"}` increments; determination field unchanged in EvaluationResult.

- [X] T024 [US4] Update rule evaluator in `pipelines/scoring/rules/` (likely `pipelines/scoring/rules/engine.py` or equivalent) — implement shadow mode evaluation behavior per `contracts/rule-definition-extension.md`: when `rule.mode == RuleMode.shadow` and rule conditions match: (1) increment `rule_shadow_triggers_total{rule_id=rule.rule_id, mode="shadow"}` counter; (2) after final `EvaluationResult.determination` is set, if determination is `"clean"` increment `rule_shadow_fp_total{rule_id=rule.rule_id}` counter (used by T043 Streamlit FP rate calculation); (3) append `f"{rule.rule_id}:shadow"` to `EvaluationResult.matched_rules`; (4) do NOT change `EvaluationResult.determination` (transaction may still be `"clean"`); (5) do NOT issue BLOCK or FLAG decision. When `rule.mode == RuleMode.active` and rule conditions match and the final `EvaluationResult.determination` is `"clean"`: increment `rule_active_fp_total{rule_id=rule.rule_id}` counter (used by T021 `RuleFPRateHigh` alert to detect post-promotion false positives on active rules). When `rule.mode == RuleMode.active` and conditions do not match or determination is not `"clean"`: existing behavior unchanged. Verify no existing tests break.

- [X] T025 [P] [US4] Create `pipelines/scoring/management_api.py` — FastAPI app on port 8090 per `contracts/management-api.md`: `POST /rules/{rule_id}/demote` (writes `mode: shadow` to in-memory rule set + YAML on disk; v1 is file-based only — no Kafka publish (TD-008); returns 409 if already shadow, 404 if rule not found, 500 if disk write fails; `config_event_published` always `false` in response); `POST /rules/{rule_id}/promote` (inverse of demote, triggered by Streamlit UI); `GET /circuit-breaker/state` (returns current `MLCircuitBreaker` state including failure count, last failure timestamp, next probe time); `GET /healthz` (returns `{"status": "ok"}` for Alertmanager health check). Rule reload cycle: scoring engine polls YAML config every 30s; management API triggers immediate reload after mode change. **Structured logging requirement** (Constitution Principle VIII): on every successful demote or promote, emit a structured JSON log entry — `{"event": "rule_mode_change", "rule_id": "<id>", "previous_mode": "<mode>", "new_mode": "<mode>", "triggered_by": "<value>", "trace_id": "<otel_trace_id>"}` — using Python `logging` with `json` serialization; `trace_id` extracted from the active OTel span context (or `"none"` if no span is active).

- [X] T026 [P] [US4] Add management API service to `infra/docker-compose.yml` — service name `scoring-management`, image built from `pipelines/scoring/`, command runs `management_api.py` on port 8090; expose port 8090 only on internal `fraudstream` network (NOT mapped to host: `expose: ["8090"]` without `ports:`); healthcheck: `GET http://localhost:8090/healthz`; depends_on: scoring engine service.

- [X] T027 [P] [US4] Create `infra/alertmanager/config.yml` and add Alertmanager service to `infra/docker-compose.yml` — Alertmanager service: image `prom/alertmanager:v0.27.0` (pinned — do not use `latest`; floating tags break reproducible builds), config volume mount `./infra/alertmanager:/etc/alertmanager`; `config.yml` per `contracts/management-api.md` Alertmanager integration section: route `rule_fp_rate_high` to `scoring-engine-webhook` receiver; receiver webhook URL: `"http://scoring-management:8090/rules/{{ .GroupLabels.rule_id }}/demote"`, `send_resolved: false`. Update Prometheus config to point to Alertmanager. **Path convention**: config lives at `infra/alertmanager/` (consistent with `infra/prometheus/`, `infra/grafana/` — all infra config under `infra/`).

- [X] T028 [P] [US4] Create `docs/deployment/CANARY_POLICY.md` per FR-022 — three sections: (1) **Model canary**: route N% (default 1%) of transactions by `account_id` hash (MurmurHash3 mod 100 < N) to new model version; use Python `mmh3` package (`pip install mmh3`) — MurmurHash3 is not in stdlib; specify hash function explicitly (not "any hash"); rollback trigger: **FP rate > 5% within 1h** (implementable via `RuleFPRateHigh` alert — see T021); note: "fraud rate delta > 5%" is listed in FR-022 as a second rollback trigger but requires ground-truth fraud labels (`txn.fraud.labels` topic, TD-001) — document this trigger as **deferred until TD-001 is resolved**; implement only the FP rate trigger in v1; (2) **Rule canary**: shadow mode → full rollout (two stages only in v1); FP gate: < 2% FP over 24h of shadow data required before promotion; note in document: "A hash-gated partial rollout intermediate stage (50% traffic by account_id, RuleMode.partial) is deferred to v2 — see TD-009"; (3) **Automated rollback**: `rule_fp_rate_high` alert → Alertmanager webhook → `/rules/{rule_id}/demote` → scoring engine reloads within 30s; Streamlit UI reflects demotion within one polling cycle.

- [X] T029 [US4] Audit `tests/unit/scoring/` — open `test_velocity_rules.py`, `test_impossible_travel.py`, `test_new_device_rules.py`; for each production rule: count existing positive examples (transactions that MUST trigger) and negative examples (legitimate transactions that MUST NOT trigger); add missing test cases to reach the 10+10 minimum per rule per FR-023; create new test file `tests/unit/scoring/test_shadow_mode_evaluator.py` covering: (a) shadow rule fires but determination unchanged, (b) shadow rule appends ":shadow" suffix to matched_rules, (c) active rule behavior unchanged when mode field present.

- [X] T043 [US4] Create `analytics/app/pages/3_rule_triggers.py` — Streamlit page per US-4 AS-2 and SC-004: (1) **Shadow trigger table**: query `rule_shadow_triggers_total` from Prometheus HTTP API (`http://prometheus:9090/api/v1/query`) grouped by `rule_id`; display as sortable table with columns: Rule ID / Shadow Triggers (24h) / Estimated FP Rate / Status; (2) **FP rate calculation**: estimated FP rate = shadow triggers on transactions with final `determination="clean"` / total shadow triggers over 24h rolling window — source from Prometheus counters `rule_shadow_triggers_total` (all) and a new counter `rule_shadow_fp_total` (triggers where final determination was clean, added to T024 scope); add note: "Ground truth is approximate in v1 — based on rule engine determination, not human review labels"; (3) **Promote button**: per-rule "Promote to active" button (disabled if FP rate ≥ 2% or trigger count < 100); on click: `POST http://scoring-management:8090/rules/{rule_id}/promote`; show success/error inline; refresh table on success; (4) **Demotion status**: highlight rules auto-demoted in the last 24h (detect via management API or Alertmanager alert history). Page title: "Shadow Rule Monitor". Requires management API service running (T025, T026).

**Checkpoint**: US4 complete — shadow mode is implemented; analyst can monitor and promote rules safely via Streamlit; auto-demotion is wired end-to-end.

---

## Phase 7: User Story 5 — Post-Mortem & Team Organization (Priority: P2)

**Goal**: Every SEV-1/SEV-2 incident produces a closed post-mortem with a merged code/config fix within 5 business days, and the team has documented ownership, KPIs, and learning feedback loops.

**Independent Test**: Use a simulated watermark-stall incident. Give an engineer the template. Verify: template guides to root cause within 30 minutes; resulting action item is a concrete PR (not "improve monitoring"); post-mortem cannot be closed until that PR is merged.

- [X] T030 [US5] Create `docs/postmortems/PROCESS.md` per FR-017 — document the post-mortem process: (1) open within 24h of SEV-1/SEV-2 resolution (engineering lead is notified if deadline missed); (2) 5-whys root cause analysis required (minimum 3 levels of "why"); (3) at least one action item MUST be a code or config change (not documentation only); (4) every action item must have: owner, backup owner (engineering lead default), file_path, due_date; (5) closure gate: status MUST NOT change to "Closed" until all action items have `status: merged`; (6) 30-day follow-up review: incident owner confirms all items complete and fix verified in staging failure scenario.

- [X] T031 [P] [US5] Create `docs/postmortems/TEMPLATE.md` per FR-017 and data-model.md PostMortem schema — Markdown template with required sections: `incident_id` (SEV level + YYYY-MM-DD + title slug), `timeline` (table: timestamp / event / who detected), `root_cause` (5-whys format: Why 1 → Why 2 → Why 3 → ... → Root Cause), `contributing_factors` (bulleted list), `action_items` (table: action / owner / backup_owner / file_path / due_date / status [`open`/`in_progress`/`merged`]), `prevention` (what architectural/alerting change prevents recurrence), `status` (`Open` / `In-Review` / `Closed` — closure blocked until all action_items `merged`).

- [X] T032 [P] [US5] Create `docs/SERVICE_OWNERSHIP.md` per FR-020 — table of team boundaries with columns: Team / Services Owned / SLO Owned / On-Call Rotation / Escalation Path. Five teams: **Ingestion** (Kafka producer, Schema Registry; SLO: schema rejection rate < 0.1%); **Processing** (Flink enrichment, GeoIP feature store; SLO: enrichment latency p99 < 20ms); **Scoring** (rule engine, ML client, circuit breaker; SLO: scoring latency p99 < 50ms, circuit breaker OPEN duration < 5min); **Analytics** (Streamlit, DuckDB/Trino; SLO: dashboard refresh < 2s); **Infrastructure** (Kafka, Flink cluster, Redis, Iceberg, Grafana; SLO: platform uptime > 99.9%). Include escalation chain for each team and RACI for cross-team incidents.

- [X] T033 [P] [US5] Create `docs/KPI_TARGETS.md` per FR-021 — per-channel KPI table with columns: KPI / Target / Prometheus Metric or Iceberg Query / Grafana/Streamlit Panel / Baseline Source. Include: fraud detection rate (TP / (TP+FN) ≥ 85% per channel); max FP rate (≤ 2% for active rules; ≤ 5% trigger for auto-demotion); DLQ recovery SLA (< 20 min from alert to root cause classification per SC-001); enrichment latency p99 (< 20ms per FR-013); model score distribution baseline (mean ± 1σ — note: "TBD at first measurement; document baseline establishment process"). For baselines that don't yet exist: document the first-measurement process explicitly.

- [X] T034 [P] [US5] Create `docs/model-feedback-loop-architecture.md` per FR-019 (v1 = architecture only, deferred implementation) — document the intended data flow: `txn.fraud.labels` topic (future) → join with `iceberg.fraud_decisions` on `transaction_id` → compute precision/recall/F1 per model version over 7-day rolling windows → surface in Streamlit model comparison page → trigger retraining recommendation when F1 drops > 5% below baseline. Include schema sketch for `txn.fraud.labels` Avro record. Mark entire document as `Status: Architecture Draft — Implementation deferred (TD-001, TD-007 must be resolved first)`.

**Checkpoint**: US5 complete — post-mortem infrastructure ready; team ownership and KPIs documented.

---

## Phase 8: User Story 6 — Architecture Decision Records (Priority: P3)

**Goal**: Every self-managed component has a written ADR so new engineers can answer "why this?" without interrupting senior staff.

**Independent Test**: Give each ADR to a new engineer. They must answer: what was chosen, why, what alternatives were rejected, and under what conditions to revisit — without asking anyone.

- [X] T035 [P] [US6] Create `docs/adr/ADR-001-STREAM-PROCESSING.md` per FR-024a — sections per data-model.md ADR schema: **Status**: Accepted; **Context**: need for stateful windowed stream processing at < 100ms end-to-end latency with exactly-once semantics; **Options**: Flink (self-managed, rich windowing/state, PyFlink) vs. Kinesis Data Analytics (managed, limited Python support in 2024) vs. Spark Streaming (micro-batch, higher latency); **Decision**: Apache Flink 1.20.3 — only option supporting sub-100ms stateful processing with Python API; **Consequences**: operational burden of managing Flink cluster; **Review Triggers**: team grows to ≥ 15 engineers; KDA adds full Python stateful API; total Flink operational cost > 2x managed alternative. **Review Schedule** (required section): `owner: <named engineer>`, `next_review_date: 2027-04-16` (12 months from branch creation date; `review_interval: "annually"`).

- [X] T036 [P] [US6] Create `docs/adr/ADR-002-OBJECT-STORAGE.md` per FR-024b — **Status**: Accepted; **Context**: need for cost-effective Iceberg catalog and object storage, local dev + cloud portability; **Options**: MinIO + local Iceberg (self-managed, S3-compatible, open source) vs. AWS S3 + Glue Catalog (managed, cost scales with usage) vs. Databricks Unity Catalog (managed, strong Iceberg support, higher cost); **Decision**: MinIO (local) / S3 (cloud) — same S3-compatible API; zero lock-in; **Review Triggers**: monthly S3 costs exceed $5k; Databricks becomes primary analytics tool. **Review Schedule** (required section): `owner: <named engineer>`, `next_review_date: 2027-04-16` (12 months from branch creation date; `review_interval: "annually"`).

- [X] T037 [P] [US6] Create `docs/adr/ADR-003-FEATURE-STORE.md` per FR-024c — **Status**: Accepted; **Context**: need for low-latency feature lookup (< 5ms) for fraud scoring enrichment; **Options**: Feast (open source, Redis backend, Python-native) vs. Hopsworks (managed, HSFS API) vs. Tecton (SaaS, managed, expensive); **Decision**: Feast with Redis backend — open source, matches existing Redis investment (FR-026), Python-native; **Review Triggers**: feature count exceeds 500; team adds ML platform team who prefers managed solution. **Review Schedule** (required section): `owner: <named engineer>`, `next_review_date: 2027-04-16` (12 months from branch creation date; `review_interval: "annually"`).

- [X] T038 [P] [US6] Create `docs/adr/ADR-004-ANALYTICS-QUERY.md` per FR-024d — **Status**: Accepted; **Context**: need for ad-hoc SQL over Iceberg tables for Streamlit analytics app; **Options**: DuckDB (embedded, fast for single-node analytics, Iceberg read support) vs. Trino (distributed, higher operational burden, scales to PB) vs. AWS Athena (managed, serverless, cost per query); **Decision**: DuckDB for local dev / Trino for cloud — same SQL surface; DuckDB embedded zero-ops for < 1TB; migrate to Trino when dataset outgrows single node; **Review Triggers**: Iceberg dataset > 1TB; query latency p95 > 30s; team adopts Databricks. **Review Schedule** (required section): `owner: <named engineer>`, `next_review_date: 2027-04-16` (12 months from branch creation date; `review_interval: "annually"`).

**Checkpoint**: US6 complete — all self-managed components have ADRs with named alternatives and review triggers.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Deferred/blocked items and verification pass.

- [X] T039 [P] Create `docs/dlq-trend-analysis-architecture.md` per FR-018 (blocked by FR-007 runbook completion and TD-007) — document daily DLQ trend job design: compute from Kafka consumer group metrics + DLQ topic offsets (not Iceberg — avoids TD-007 block); output: volume by error type over rolling 7/30 days, top-5 error types, error rate as % of total throughput, trending (error type increasing?); results published to Streamlit DLQ inspector page. Mark as `Status: Architecture Draft — Implementation pending FR-007 completion and Streamlit page scaffolding`.

- [X] T040 [P] Create `docs/pii-audit-architecture.md` per FR-005 (blocked by TD-007 Iceberg sinks) — document intended daily scan logic: scan `iceberg.enriched_transactions` and `iceberg.fraud_decisions` for: 16-digit PAN patterns (regex `\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`), full IPv4/IPv6 outside /24 notation, fields named `card_number`/`full_ip`/`ssn`; on match: alert data privacy team, halt writes to affected Iceberg table; append to `audit.pii_scans` table (PIIAuditRecord schema from data-model.md). Mark as `Status: Architecture Draft — Implementation blocked by TD-007`.

- [X] T041 [P] Run `pytest` from repo root and verify `--cov-fail-under=80` passes after foundational changes (T003-T007). Fix any test failures introduced by new code. Run `ruff check pipelines/scoring/` to verify no lint errors in new files.

- [X] T042 [P] Verify all new directories and files created in T001–T040 are tracked by git (`git status` shows no untracked files in docs/, tests/chaos/, scripts/, infra/terraform/redis/, infra/grafana/, alertmanager/). Add `.gitkeep` files to empty directories if needed. Verify each ADR created in T035–T038 contains a `## Review Schedule` section with both `owner:` and `next_review_date:` fields populated — reject (do not merge) any ADR missing this section.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS US3 (T017) and US4 (T024)
- **Phase 3 (US1)**: Can start after Phase 1 — independent of Phase 2
- **Phase 4 (US2)**: Can start after Phase 1 — independent of Phase 2
- **Phase 5 (US3)**: Depends on Phase 2 completion — T017 (circuit breaker) needs T003/T004; T018 needs T007
- **Phase 6 (US4)**: Depends on Phase 2 completion — T024 needs T006 (RuleMode); T025 needs T017
- **Phase 7 (US5)**: Depends on Phase 1 only — fully independent documentation
- **Phase 8 (US6)**: Depends on Phase 1 only — fully independent documentation
- **Phase 9 (Polish)**: Depends on all preceding phases

### User Story Dependencies

| Story | Depends On | Can Parallelize With |
|-------|------------|----------------------|
| US1 (Phase 3) | Phase 1 | US2, US5, US6 |
| US2 (Phase 4) | Phase 1 | US1, US5, US6 |
| US3 (Phase 5) | Phase 2 | US4 partial (docs tasks), US5, US6 |
| US4 (Phase 6) | Phase 2 + T017 for T025 | US5, US6 |
| US5 (Phase 7) | Phase 1 | US1, US2, US6 |
| US6 (Phase 8) | Phase 1 | US1, US2, US5 |

### Within-Phase Parallel Opportunities

**Phase 2 (Foundational)**: T004, T005, T006, T007 all touch different files — run in parallel after T003 (ScoringConfig).

**Phase 3 (US1)**: T009, T010, T011, T012 are all independent docs/scripts — run in parallel after T008 is drafted (to ensure consistent terminology).

**Phase 5 (US3)**: T018, T019, T020, T021, T023 are all different files — run in parallel after T017 (circuit breaker needs to exist for T018 and T021 to reference).

**Phase 6 (US4)**: T026, T027, T028 are independent — run in parallel after T025 (management API must exist before docker-compose references it).

**Phase 8 (US6)**: T035, T036, T037, T038 are all independent docs — run fully in parallel.

---

## Parallel Execution Examples

### Fastest Path to US1 (DLQ Runbooks — P1)

```bash
# Phase 1 first
T001 → T002 [parallel]

# Then US1 immediately (no Phase 2 needed)
T008 → [T009 + T010 + T011 + T012 in parallel]
```

### Fastest Path to US3 (Circuit Breaker — P1)

```bash
# Phase 1 + Phase 2 (foundational)
T001 → [T003 → T004 + T005 + T006 + T007 in parallel]

# Then US3
T017 → [T018 + T019 + T020 + T021 + T023 in parallel] → T022
```

### Full Parallel Team Strategy (3 developers)

```bash
# All start Phase 1 together
T001 → T002

# Phase 2: all work foundational together
T003 → [T004 + T005 + T006 + T007 in parallel]

# Split after foundation:
# Dev A: US1 (P1) → US5 (P2)
# Dev B: US2 (P1) → US6 (P3)  
# Dev C: US3 (P1) → US4 (P2)
```

---

## Implementation Strategy

### MVP (US1 + US2 — DLQ Runbooks + CI Enforcement)

Both P1 stories that do NOT require Phase 2 foundation:

1. Complete Phase 1 (Setup)
2. Complete Phase 3 (US1 runbooks) and Phase 4 (US2 CI) in parallel
3. **STOP and VALIDATE**: New engineer resolves simulated DLQ incident; CI blocks a test breaking-change PR
4. Merge MVP — team immediately benefits from reduced incident response time

### Full Delivery Order (by risk reduction priority)

1. Phase 1: Setup
2. Phase 2: Foundation (unblocks US3 + US4)
3. Phase 3 (US1) + Phase 4 (US2) in parallel ← MVP gate
4. Phase 5 (US3) — circuit breaker — highest code complexity
5. Phase 6 (US4) — shadow mode — depends on T017
6. Phase 7 (US5) + Phase 8 (US6) in parallel — documentation
7. Phase 9: Polish + verification

---

## Notes

- `[P]` = task touches different files with no shared state — safe to run concurrently
- `[Story]` maps each task to its user story for traceability back to spec.md acceptance criteria
- FR-005, FR-019 blocked by TD-007 — tracked in T040, T034 as architecture notes only; no implementation tasks created
- FR-016 Redis HA Terraform (T023) depends on T005 (docker-compose Redis) — Terraform work only meaningful after local dev baseline exists
- FR-018 DLQ trend analysis (T039) depends on FR-007 runbook (T008) — Streamlit page references runbook error taxonomy
- Commit after each task or logical group; stop at any phase checkpoint to validate independently
