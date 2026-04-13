# Feature Specification: Operational Excellence — Architecture Gaps & Partials

**Feature Branch**: `004-operational-excellence`
**Created**: 2026-04-03
**Status**: Draft
**Source**: AWS Well-Architected OE architecture review (2026-04-03)
**Principles addressed**: OE-1 (Team Org), OE-2 (Observability), OE-3 (Automate), OE-4 (Reversible), OE-5 (Refine Ops), OE-6 (Anticipate Failure), OE-7 (Learn), OE-8 (Managed Services)

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — An on-call engineer responds to a DLQ alert without expert help (Priority: P1)

It is 2 AM. The `txn.processing.dlq` alert fires. The on-call engineer has never touched Flink. With only the runbook and standard tooling available, they must identify the root cause, decide whether to replay or escalate, and resolve the incident — without calling anyone.

**Why this priority**: The architecture review identified this as the highest-risk gap. Without runbooks, every incident requires tribal knowledge. A single wrong action (e.g., replaying a malformed event) can cascade into the scoring engine.

**Independent Test**: Give a new engineer (unfamiliar with the system) a pre-seeded DLQ with one schema-invalid event and one late-event-beyond-window event. They must: identify both, correctly classify root causes, replay the schema-invalid one after fixing the producer, and discard the late-event. Measure time to resolution and error rate.

**Acceptance Scenarios**:

1. **Given** a DLQ alert for `txn.processing.dlq`, **When** the engineer follows the DLQ runbook, **Then** they can identify the error type (schema invalid / late event / deserialization failure) from the DLQ message headers within 5 minutes without custom tooling.
2. **Given** a schema-invalid DLQ record, **When** the runbook replay decision tree is followed, **Then** the engineer replays the corrected event to the source topic using `replay-dlq-message.sh` and verifies it processes successfully end-to-end.
3. **Given** a `LATE_EVENT_BEYOND_ALLOWED_LATENESS` DLQ record, **When** the runbook is followed, **Then** the engineer correctly discards the event (does not replay) and documents it in the incident log.
4. **Given** DLQ depth > 100 within 5 minutes, **When** the engineer follows the escalation tree, **Then** they escalate to the owning team before attempting any replay — bulk replay without root cause analysis is explicitly prohibited in the runbook.

---

### User Story 2 — A CI/CD pipeline blocks a breaking schema change from reaching production (Priority: P1)

A developer adds a non-nullable field to `enriched-txn-v1.avsc` and opens a PR. The CI pipeline must detect the breaking change, fail the build with a clear explanation, and prevent the merge until the developer either creates a new schema version or justifies backward compatibility.

**Why this priority**: Manual schema changes are the highest-risk human error in a schema-contract-first architecture. A breaking change reaching production silently breaks all downstream consumers.

**Independent Test**: Open a PR that adds `required string new_field` to an existing Avro schema. Verify: CI fails at the schema-validation stage with message identifying the breaking change; the PR cannot be merged; a passing PR with a new `v2` schema subject passes CI.

**Acceptance Scenarios**:

1. **Given** a PR that modifies an existing registered Avro schema to add a required field, **When** CI runs schema validation, **Then** the pipeline fails with a `BREAKING_CHANGE` error identifying the field and the affected consumers.
2. **Given** a PR that adds a new schema subject (`enriched-txn-v2`) without touching v1, **When** CI runs schema validation, **Then** the pipeline passes the schema stage.
3. **Given** a PR that changes Python source code only (no schema files), **When** CI runs, **Then** the schema validation stage is skipped to avoid unnecessary latency.
4. **Given** a PR that modifies Terraform infrastructure files, **When** CI runs, **Then** `terraform validate` and `terraform plan` run and the plan diff is posted to the PR as a comment before any approval.

---

### User Story 3 — A scoring engine survives an ML model serving outage without dropping decisions (Priority: P1)

The ML model serving layer (MLflow / SageMaker) becomes unresponsive. The scoring engine must detect the failure, open a circuit breaker, fall back to rule-engine-only decisions, and alert on-call — all within the 100ms latency budget — without any manual intervention.

**Why this priority**: Constitution Principle V mandates this fallback. Without an implemented circuit breaker it exists only on paper. ML outages are a realistic failure mode during model deployments and infrastructure events.

**Independent Test**: Stop the ML serving container mid-load. Verify: within 3 consecutive failures, the circuit breaker opens; scoring continues using rule-engine only; `ml_circuit_breaker_state{state="open"}` metric fires; latency p99 stays < 100ms; zero decisions are dropped.

**Acceptance Scenarios**:

1. **Given** the ML serving layer returns 3 consecutive errors within 10 seconds, **When** the circuit breaker detects this, **Then** it transitions to OPEN state, all subsequent ML calls are skipped (fast-fail), and rule-engine-only decisions are issued.
2. **Given** the circuit breaker is OPEN, **When** 30 seconds pass (half-open probe interval), **Then** a single probe request is sent to ML serving; if successful, the circuit transitions to CLOSED; if it fails, the OPEN timer resets.
3. **Given** the circuit breaker is OPEN, **When** a transaction arrives, **Then** the scoring engine issues a decision using only rule triggers within the 100ms budget, and `ml_fallback_decisions_total` counter increments.
4. **Given** the circuit breaker opens, **When** the alert fires, **Then** on-call receives a PagerDuty notification within 60 seconds including the error rate, open duration, and a link to the ML serving runbook.

---

### User Story 4 — A new fraud detection rule is tested in shadow mode before blocking transactions (Priority: P2)

A fraud analyst writes a new rule targeting velocity anomalies on the API channel. Before it blocks real transactions, it runs in shadow mode: the rule fires and is recorded, but the transaction is not blocked. After 24 hours of shadow data showing < 2% false-positive rate, the analyst promotes it to blocking mode via the Streamlit rule config UI.

**Why this priority**: A rule that inadvertently blocks legitimate high-value transactions is a direct revenue impact. Shadow mode is the controlled rollout mechanism. Without it, every rule change is a production risk.

**Independent Test**: Deploy a new rule in shadow mode. Replay 10,000 historical transactions (including 100 known-legitimate). Verify: rule fires on target events; zero transactions are blocked; `rule_shadow_triggers_total{rule_id="...", mode="shadow"}` increments; false-positive rate is calculable from the data.

**Acceptance Scenarios**:

1. **Given** a rule deployed with `mode: shadow`, **When** a matching transaction arrives, **Then** `rule_triggers` in the scoring output includes the rule ID tagged with `shadow`, the decision is unchanged (ALLOW/FLAG based on other active rules), and no BLOCK is issued by this rule alone.
2. **Given** 24 hours of shadow data, **When** the analyst views the Streamlit rule trigger page, **Then** they see shadow trigger count, estimated false-positive rate (shadow triggers on confirmed-legitimate transactions), and a "promote to active" button.
3. **Given** the analyst promotes a rule from shadow to active via the management API (`POST /rules/{rule_id}/promote`), **When** the management API writes the updated rule mode to the YAML rule file on disk (v1 file-based propagation; Kafka-based propagation deferred to TD-008), **Then** the scoring engine reloads and begins applying the rule in active mode within the next polling cycle (≤ 30 seconds).
4. **Given** an active rule causes false-positive rate > 5% within 1 hour of promotion, **When** the alert fires, **Then** the rule is automatically demoted back to shadow mode and on-call is notified. The demotion is owned by the **rule-management Prometheus alert** (`rule_fp_rate_high`) which fires a webhook to the scoring engine's `/rules/{rule_id}/demote` management endpoint; the scoring engine reloads the rule file with `mode: shadow` and publishes a config event to `txn.rules.config`. The Streamlit UI reflects the demotion within one polling cycle (≤ 30 seconds).

---

### User Story 5 — A post-mortem from a production incident feeds back into the architecture (Priority: P2)

A SEV-1 incident occurs: the Flink watermark stalls and 10,000 events are routed to DLQ. After resolution, the incident owner runs the post-mortem process, identifies the missing watermark stall alert as the root cause, opens a PR adding the alert, and updates the runbook. The constitution's Non-Negotiables checklist gains a new item.

**Why this priority**: A system that does not learn from incidents will repeat them. The OE review found zero post-mortem infrastructure; without it, the same failure modes will recur.

**Independent Test**: Use a simulated past incident (watermark stall). Give the engineer the post-mortem template. Verify: template guides them to a root cause within 30 minutes; the resulting action item is a concrete PR (not a vague "improve monitoring"); the PR is merged before the post-mortem is closed.

**Acceptance Scenarios**:

1. **Given** a SEV-1 or SEV-2 incident is resolved, **When** the incident owner opens the post-mortem template, **Then** the template requires: timeline, root cause (5-whys), contributing factors, action items (each with owner, file path, and due date), and a prevention section.
2. **Given** a completed post-mortem, **When** the action items include a runbook update, **Then** the post-mortem cannot be marked "closed" until the runbook PR is merged.
3. **Given** a post-mortem identifies a missing alert, **When** the alert PR is opened, **Then** it must include a test proving the alert would have fired 5 minutes before the incident degraded beyond the SLO.
4. **Given** a closed post-mortem, **When** 30 days pass, **Then** the incident owner reviews whether all action items are complete and whether the fix has been verified in a staging failure scenario.

---

### User Story 6 — An ADR documents why Flink was chosen over a managed alternative (Priority: P3)

A new engineer joins the team and asks why Flink is self-managed rather than AWS Kinesis Data Analytics. The ADR provides a written decision, the constraints that drove it, the alternatives considered, and the migration trigger criteria — so the question is answered without interrupting senior engineers.

**Why this priority**: The OE review found no justification for any self-managed component. Without ADRs, every self-managed service is an implicit technical debt that may never be re-evaluated.

**Independent Test**: Give the ADR to a new engineer. They must be able to answer: what was chosen, why, what alternatives were rejected, and under what conditions the decision should be revisited — without asking anyone.

**Acceptance Scenarios**:

1. **Given** an ADR for Flink vs. Kinesis Data Analytics, **When** a new engineer reads it, **Then** it contains: decision, status, context, options considered (with pros/cons), decision rationale, and consequences.
2. **Given** an ADR is created, **When** the triggering constraint changes (e.g., team grows to 20 engineers, managed service cost becomes viable), **Then** the ADR includes a "review trigger" section specifying conditions under which it should be re-evaluated.
3. **Given** all self-managed services in the local stack (Flink, MinIO, Feast, Trino/DuckDB, Grafana), **When** the ADR set is complete, **Then** each service has a corresponding ADR with managed-cloud alternative named.

---

### Edge Cases

- What if the DLQ runbook replay script is run against the wrong topic? The script must require explicit `--target-topic` and `--dry-run` flags; dry-run mode prints the replay plan without executing it.
- What if a circuit breaker false-positives during a brief ML serving hiccup (single 503)? The circuit breaker threshold must be configurable (default: 3 errors in 10 seconds); a single error must never open the circuit.
- What if a rule in shadow mode has 100% trigger rate (it matches every transaction)? The Streamlit UI must show a "rule fires on every transaction" warning before the promote button is enabled, requiring explicit acknowledgement.
- What if the CI/CD pipeline takes > 20 minutes? The schema validation and unit test stages must run in parallel; latency bench and integration tests run only on main branch PRs, not feature branches.
- What if a post-mortem action item owner leaves the team? The post-mortem process requires a backup owner on every action item; the engineering lead is the default escalation owner.
- What if an ADR is violated by a PR? PRs must include a checklist item confirming which ADRs are relevant; reviewers must check ADR compliance before approving.
- What if the circuit breaker probe request during HALF-OPEN is itself slow (> 30ms)? The probe must use a reduced timeout (5ms) to avoid consuming the scoring latency budget during recovery validation.
- What if IaC `terraform apply` fails mid-way through topic provisioning? All Kafka topic provisioning must be idempotent; partial applies must not leave topics in inconsistent state (use Terraform `create_before_destroy`).
- What if a shadow rule has zero triggers after 24 hours of observation? The Streamlit shadow monitoring page MUST clearly distinguish between "zero triggers recorded" and "0% false-positive rate" — zero triggers may indicate a misconfigured rule condition (too restrictive, wrong channel filter) rather than a well-calibrated rule. The "promote to active" button MUST be disabled and require an explicit acknowledgement when trigger count is zero.
- What if the CI pipeline cannot reach the Schema Registry during the schema validation stage? The schema validation stage MUST fail closed — an unreachable Schema Registry is treated as a failing check, not a skipped stage. The PR is blocked until the registry is reachable and the validation can run. This prevents silent bypass of the compatibility gate during infrastructure outages.

---

## Requirements *(mandatory)*

### Functional Requirements

#### OE-3: Automation & CI/CD

- **FR-001**: The repository MUST have a CI/CD pipeline (GitHub Actions) that runs on every PR and every merge to main. The pipeline MUST execute in stages: (1) lint/typecheck, (2) unit tests with coverage gate (≥ 80%), (3) schema validation, (4) integration tests, (5) latency bench (main branch only), (6) manual approval gate for schema changes and Flink job version bumps, (7) Terraform plan/apply (non-prod first). Stages 1–3 MUST run in parallel where independent.

- **FR-002**: Schema validation MUST use the Confluent Schema Registry compatibility API to detect breaking changes before any schema file is merged. A breaking change MUST fail CI with a structured error identifying: the affected schema subject, the incompatible field(s), and the required remediation (create new subject version or add default). The compatibility level MUST be `BACKWARD_TRANSITIVE` for all subjects. **Note**: the current CI job (`schema-registry-compat`) sets `BACKWARD` — this MUST be updated to `BACKWARD_TRANSITIVE` as part of implementing this FR. `BACKWARD` only checks against the latest version; `BACKWARD_TRANSITIVE` checks against all historical versions and is required to detect multi-generation drift. **Fail-closed requirement**: If the Schema Registry is unreachable (network timeout, DNS failure, 5xx response), the schema validation stage MUST fail the build — registry unavailability is treated as a failing check, not a skip. This prevents silent bypass of the compatibility gate during infrastructure outages (see Edge Cases).

- **FR-003**: Infrastructure as Code MUST be defined in Terraform covering at minimum: Kafka topic provisioning (partition count, replication factor, retention), Schema Registry subject registration, Redis cluster configuration and TTL policy, and Iceberg catalog and S3/MinIO bucket configuration. Terraform modules MUST support three deployment targets via workspace or variable: (1) **local/dev** — `infra/docker-compose.yml` is the authoritative local runtime; Terraform is a no-op for services already covered by docker-compose (Kafka, Redis, Flink); (2) **AWS** — MSK (Managed Streaming for Kafka), Amazon Managed Service for Apache Flink, ElastiCache for Redis, S3 + Glue catalog for Iceberg; (3) **GCP** — Confluent Cloud or Pub/Sub for Kafka, Dataflow for stream processing, Memorystore for Redis, GCS + Dataplex for Iceberg. Flink job deployment via self-managed Kubernetes manifests is explicitly out of scope — managed service APIs are used instead (see TD-004, TD-010). All Terraform modules MUST be idempotent and pass `terraform validate` in CI. **Terraform state security**: Remote state MUST use backend encryption — S3 backend with SSE-KMS (AWS target) or Terraform Cloud encrypted state (GCP target). Local `.tfstate` files MUST NOT be committed to the repository (enforced via `.gitignore`). State files that surface even after a `terraform destroy` MUST be purged from git history; committing `.tfstate` is a CI violation.

- **FR-004**: Deployment automation MUST include scripts for: (a) Flink job rolling upgrade preserving state checkpoints, (b) schema subject migration with consumer group coordination, (c) checkpoint-based rollback with lag recovery verification. Each script MUST support a `--dry-run` flag that prints the execution plan without taking action.

- **FR-005** **[BLOCKED — TD-007: Iceberg sinks not implemented; this FR cannot be built or tested until a dedicated Iceberg sink spec is delivered]**: An automated PII audit job MUST run daily against `iceberg.enriched_transactions` and `iceberg.fraud_decisions`. It MUST scan for: full 16-digit PAN patterns, full IPv4/IPv6 addresses outside /24 subnet notation, and any field named `card_number`, `full_ip`, or `ssn`. Any match MUST trigger an immediate alert to the data privacy team and halt writes to the affected Iceberg table until the source of leakage is identified. Results MUST be appended to `audit.pii_scans` Iceberg table (append-only).

- **FR-006**: A PR template (`.github/PULL_REQUEST_TEMPLATE.md`) MUST enforce a checklist including: unit test coverage ≥ 80%, schema validation passed (if schema change), latency SLO verified (if hot path change), PII audit passed, canary policy applied (if scoring/model change), and relevant ADR compliance confirmed. **PII audit N/A condition**: The "PII audit passed" checklist item MUST be marked `N/A` (with a reference to TD-007) until FR-005 (automated PII audit job) is implemented. FR-005 is blocked by TD-007 (Iceberg sinks not yet available); PR authors MUST not leave this item blank — it must be either checked (audit ran and passed) or explicitly marked N/A with the TD-007 reference.

#### OE-5 & OE-2: Operations Runbooks & Observability

- **FR-007**: A DLQ inspection and recovery runbook MUST exist at `docs/runbooks/DLQ_INSPECTION_AND_RECOVERY.md`. It MUST contain: (a) a decision tree for classifying DLQ message error types (schema invalid, deserialization failure, late event beyond window, downstream unavailable), (b) per-error-type remediation steps, (c) replay procedure using `replay-dlq-message.sh` with dry-run mode, (d) bulk-replay prohibition and escalation threshold (> 100 messages within 5 min → escalate before replaying), and (e) verification steps to confirm replay succeeded end-to-end. **New deliverable**: `replay-dlq-message.sh` does not currently exist in the repository; it MUST be created at `scripts/replay-dlq-message.sh` as part of implementing this FR. The script MUST support `--source-topic`, `--target-topic`, `--message-id`, and `--dry-run` flags; `--dry-run` MUST print the replay plan without executing any Kafka produce call.

- **FR-008**: A checkpoint recovery runbook MUST exist at `docs/runbooks/CHECKPOINT_RECOVERY.md`. It MUST include: listing available checkpoints in MinIO/S3 with timestamps and state sizes, validating checkpoint integrity before restore (checksum verification), the restore command with Flink SavePoint API, monitoring lag recovery post-restore (target: lag < 5s within 60s), and a post-recovery checklist confirming velocity state coherence.

- **FR-009**: A schema migration runbook MUST exist at `docs/runbooks/SCHEMA_MIGRATION.md`. It MUST cover: creating a new Schema Registry subject version, coordinating consumer group migration (pause consumer → update offset → restart), monitoring for straggler consumers still on old schema version, and topic deprecation procedure (30-day notice, automated straggler check, retirement checklist).

- **FR-010**: An observability runbook MUST exist at `docs/runbooks/OBSERVABILITY_RUNBOOK.md` with troubleshooting trees for: (a) DLQ depth > 0 — which stage? error type? replay or fix first?, (b) enrichment latency > 20ms p99 — Kafka lag? GeoIP timeout? state bloat? checkpoint pending?, (c) analytics dashboard refresh > 2s — Trino slow? Kafka consumer lag? Iceberg compaction?, (d) watermark stall — all-partition stall detection, alert configuration, resolution steps.

- **FR-011**: A trace sampling policy MUST be documented and implemented: 100% sampling for BLOCK decisions and all pipeline errors; 1% head-based sampling for ALLOW decisions; tail-based sampling at 100% for any trace containing an error span. The sampling rate MUST be exposed as a Prometheus metric `trace_sampling_rate{decision_type}` to allow dashboard variance explanation. **Prerequisite — FR-027**: A `pipelines/scoring/telemetry.py` module MUST be created (parallel to the existing `pipelines/ingestion/api/telemetry.py` and `pipelines/processing/telemetry.py`) to bootstrap the OTel tracer and configure the sampling policy before this FR is implemented. **v1 limitation — tail-based sampling**: True tail-based sampling (sample 100% of traces that contain an error span, regardless of head sampling decision) requires an OTel Collector sidecar with a tail-sampling processor. This sidecar is not present in the v1 `infra/docker-compose.yml`. In v1, the approximation is: BLOCK decisions and errors use `AlwaysOn` sampler logic applied via span attributes after evaluation; ALLOW decisions use `ParentBasedTraceIdRatio(0.01)` head-based sampling. Full tail-based sampling via OTel Collector is deferred to v2.

- **FR-012**: A Grafana dashboard definition (JSON export) MUST be committed to `infra/grafana/provisioning/dashboards/fraud-detection-main.json` containing at minimum: latency budget waterfall (ingestion / enrichment / scoring / decision — stacked), DLQ depth by stage (multi-series), late events within vs. beyond allowed-lateness window, analytics consumer group lag, checkpoint duration histogram (p50/p95/p99), and ML circuit breaker state (CLOSED / OPEN / HALF-OPEN). **Note**: the Grafana provisioning volume mounts `./grafana/provisioning/dashboards` — files placed outside this path will not be auto-loaded. The path `monitoring/grafana-dashboards/` (previously listed here) is incorrect relative to the docker-compose provisioning config.

- **FR-013**: Late event metrics MUST be added to the stream processor: `late_events_within_window_total` (counter), `late_events_beyond_window_total` (counter, DLQ routed), and `corrected_record_latency_ms` (histogram, time from late event arrival to corrected record emission on output topic). These extend FR-002 and FR-003 in `specs/002-flink-stream-processor/spec.md`.

#### OE-6: Anticipate Failure

- **FR-014**: The scoring engine MUST implement a circuit breaker on all ML serving calls using a configurable policy: OPEN after N consecutive errors (default: 3) within T seconds (default: 10); HALF-OPEN probe after P seconds (default: 30); probe timeout exactly 5ms — MUST be implemented via `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=0.005)`; do NOT use pybreaker's built-in timeout which relies on `threading.Event` and cannot reliably enforce sub-10ms deadlines (see research.md RQ-001). State transitions MUST emit Prometheus metrics: `ml_circuit_breaker_state{state}` (gauge) and `ml_fallback_decisions_total` (counter). OPEN state MUST page on-call within 60 seconds. **Prerequisite — FR-025**: An `MLModelClient` interface (`score(txn: dict) -> float`) MUST exist before the circuit breaker can wrap anything. `pipelines/scoring/` currently contains no ML client. A v1 stub implementation (configurable: returns a fixed score or raises on demand for chaos testing) satisfies this prerequisite. `ScoringConfig` MUST be extended with `ml_serving_url`, `cb_error_threshold` (int, default 3), `cb_open_seconds` (int, default 30), and `cb_probe_timeout_ms` (int, default 5).

- **FR-015**: A failure scenario test plan MUST be documented at `tests/chaos/FAILURE_SCENARIOS.md` and executed in staging before every major release. It MUST cover at minimum: (a) Flink single-node failure — verify partition redistribution and lag recovery within 60s, (b) ML serving outage — verify circuit breaker opens and fallback issues decisions within budget, (c) Kafka broker unavailability — verify DLQ routing and producer backpressure, (d) checkpoint storage unavailable — verify continued processing with alert on checkpoint failure, (e) analytics consumer crash — verify zero impact on scoring latency and throughput.

- **FR-016**: A Redis HA configuration MUST be defined in Terraform. For local dev: Redis Sentinel with 1 primary and 1 replica. For cloud: cluster mode with at least 3 shards and 1 replica per shard. The feature cache TTL policy (24 hours per constitution) MUST be configured as `maxmemory-policy allkeys-lru` with explicit TTL on all written keys. **Note**: `allkeys-lru` is the correct policy for a pure Feast feature cache — all keys are reconstructable and carry explicit 24h TTLs; `allkeys-lru` acts as the OOM safety valve and may evict any key when memory is full. `volatile-lru` is not appropriate here because Redis holds no durable state (circuit breaker state is in-memory Python; rule config is YAML on disk per TD-008). Failover MUST be tested as part of the failure scenario test plan (FR-015). **Prerequisite — FR-026**: Redis is not present in `infra/docker-compose.yml`. A single-node Redis service MUST be added to docker-compose before any HA or Terraform work is meaningful. `ScoringConfig` MUST expose a `redis_url` field. The Sentinel/cluster config is the Terraform-layer concern; docker-compose covers the local dev baseline.

#### OE-7: Learn from Events

- **FR-017**: A post-mortem process MUST be documented at `docs/postmortems/PROCESS.md` and a template at `docs/postmortems/TEMPLATE.md`. The process MUST mandate: post-mortem opened within 24h of SEV-1/SEV-2 resolution, 5-whys root cause analysis, at least one action item that is a code or config change (not documentation only), all action items must have an owner and due date, post-mortem cannot be closed until all action items are merged, 30-day follow-up review.

- **FR-018**: A DLQ trend analysis job MUST run daily and publish results to the Streamlit analytics app (DLQ inspector page). It MUST compute: DLQ message volume by error type over rolling 7/30 days, top 5 error types by frequency, error rate as percentage of total throughput, and trending analysis (is a specific error type increasing over time?). Results feed into the rule refinement and model retraining feedback loops.

- **FR-019** **[BLOCKED — TD-007: Iceberg sinks not implemented; `iceberg.fraud_decisions` required for label join is unavailable until a dedicated Iceberg sink spec is delivered]**: A model performance feedback loop MUST be defined (architecture and data contracts only in v1; implementation deferred to a future spec). The loop MUST: consume confirmed fraud labels from `txn.fraud.labels` topic (future), join with `iceberg.fraud_decisions` on `transaction_id`, compute precision/recall/F1 per model version over 7-day rolling windows, surface these metrics in Streamlit model comparison page, and trigger a retraining recommendation alert when F1 drops > 5% below baseline.

#### OE-1: Team Organization & KPIs

- **FR-020**: A service ownership document MUST exist at `docs/SERVICE_OWNERSHIP.md` defining team boundaries, per-team SLO ownership, and escalation chains for: Ingestion (producer + Schema Registry), Processing (Flink enrichment + feature store), Scoring (rule engine + ML client), Analytics (Streamlit + DuckDB/Trino), and Infrastructure (Kafka/Flink/Redis/Iceberg platform).

- **FR-021**: A KPI targets document MUST exist at `docs/KPI_TARGETS.md` defining measurable business outcomes per channel: minimum fraud detection rate (TP / (TP + FN)), maximum false-positive rate, DLQ recovery SLA, enrichment latency p99 target, and model score distribution baseline (mean ± 1σ). Each KPI MUST map to a Prometheus metric or Iceberg query and a Grafana/Streamlit panel.

#### OE-4: Reversible Changes

- **FR-022**: A canary deployment policy MUST exist at `docs/deployment/CANARY_POLICY.md` covering: model canary (route N% of transactions by `account_id` hash to new model version; default N=1%), rule canary (shadow mode → partial rollout → full rollout with FP rate gate), and automated rollback trigger (fraud rate delta > 5% OR false-positive rate > 5% within 1h of rollout → auto-demote to previous version and page on-call). Additional requirements for v1:
  - **FP rate thresholds**: The pre-promotion shadow gate (< 2% FP) and the post-promotion auto-demotion trigger (> 5% FP) are intentionally different — the tighter pre-promotion threshold is a quality gate; the looser post-promotion threshold allows natural variance before triggering an automated rollback.
  - **Structured audit log**: Every demote and promote operation performed by the management API MUST emit a structured JSON log entry containing: `event` (`rule_mode_change`), `rule_id`, `previous_mode`, `new_mode`, `triggered_by` (`"api"` for manual calls, `"alert"` for Alertmanager webhook), and `trace_id` (from the active OTel span, or `"none"` if no span is active).
  - **`rule_id` format constraint**: `rule_id` values used in management API paths MUST match the pattern `^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$` (alphanumeric lowercase + hyphens, 2–64 characters). This constraint MUST be documented in the canary policy and enforced at rule definition load time. The format restriction prevents path traversal in URL construction (Alertmanager webhook path uses `rule_id` directly).
  - **Management API security model (v1)**: Port 8090 is accessible only within the internal Docker/VPC network; no authentication or TLS is required in v1. mTLS between Alertmanager and the management API is deferred to TD-010 (cloud deployment spec).
  - **Alertmanager webhook failure path**: If the Alertmanager webhook call to `/rules/{rule_id}/demote` fails (network error or 5xx response), Alertmanager retries per its configured `group_interval` and `repeat_interval`. The management API MUST be idempotent — a second `POST /rules/{rule_id}/demote` when the rule is already in shadow mode returns `409 Conflict` (not 5xx), preventing duplicate alerts from accumulating error counts.
  - **Simultaneous model + rule canary**: Running a model canary and a rule canary concurrently on the same traffic is explicitly out of scope for v1. The canary policy MUST document this exclusion and require operators to complete one canary before starting another.

- **FR-023**: A rule regression test suite MUST exist at `tests/scoring/` containing for each production rule: at minimum 10 positive examples (transactions that MUST trigger the rule) and 10 negative examples (legitimate transactions that MUST NOT trigger). CI MUST run this suite on every change to rule definitions and fail if any regression is detected. **Partial satisfaction**: `tests/unit/scoring/` already contains `test_velocity_rules.py`, `test_impossible_travel.py`, and `test_new_device_rules.py`. Treat FR-023 as a **gap audit** — inventory existing test cases against the 10+10 minimum per rule and add missing cases rather than building a net-new suite from scratch.

#### OE-8: Managed Services ADRs

- **FR-024**: Architecture Decision Records MUST exist for every self-managed component in the local and cloud stack. Required ADRs: (a) `docs/adr/ADR-001-STREAM-PROCESSING.md` — Flink vs. Kinesis Data Analytics vs. Spark Streaming, (b) `docs/adr/ADR-002-OBJECT-STORAGE.md` — MinIO (local) / S3 (cloud) vs. managed Iceberg services, (c) `docs/adr/ADR-003-FEATURE-STORE.md` — Feast vs. Hopsworks vs. Tecton, (d) `docs/adr/ADR-004-ANALYTICS-QUERY.md` — DuckDB vs. Trino vs. Athena decision boundary. Each ADR MUST include a "review trigger" section specifying conditions for re-evaluation. Each ADR MUST also include a `## Review Schedule` section with the following structured fields: `owner:` (team or named individual responsible for the next review), `next_review_date:` (ISO 8601 date, e.g. `2027-04-13`), and `review_interval:` (e.g. `"annually"` or `"on architectural change"`). This metadata enables automated staleness checks without requiring calendar integrations.

#### Prerequisites (blocking infra gaps)

- **FR-025** *(prerequisite for FR-014)*: An `MLModelClient` interface MUST be created at `pipelines/scoring/ml_client.py`. The interface MUST define a single method `score(txn: dict) -> float`. A v1 stub implementation MUST be provided that accepts a configurable `stub_score` (default `0.0`) and a `fail_on_call` flag (`False` by default, set `True` for chaos testing). `ScoringConfig` (in `pipelines/scoring/config.py`) MUST be extended with: `ml_serving_url: str`, `cb_error_threshold: int = 3`, `cb_open_seconds: int = 30`, `cb_probe_timeout_ms: int = 5`. No ML serving integration is required in v1 — the stub satisfies the prerequisite; the real client is the body of FR-014. **Security**: `ml_serving_url` MUST be sourced from an environment variable (`ML_SERVING_URL`); it MUST NOT be hardcoded in source. In local dev the default is `"http://localhost:5001"`. Cloud deployments MUST use HTTPS; mTLS is deferred to TD-010.

- **FR-026** *(prerequisite for FR-016)*: A single-node Redis service MUST be added to `infra/docker-compose.yml` (image: `redis:7-alpine`, port `6379`, named volume for persistence). `ScoringConfig` MUST be extended with `redis_url: str = "redis://localhost:6379/0"`. The Sentinel/cluster HA configuration is the Terraform-layer concern (FR-016); docker-compose covers only the local dev baseline required to make FR-016 meaningful. **Security**: In cloud deployments, `redis_url` MUST use `rediss://` (TLS-enabled scheme) and include AUTH credentials sourced from environment variables. Redis AUTH secret management and mTLS configuration are deferred to TD-010 (cloud deployment spec).

- **FR-027** *(prerequisite for FR-011)*: A `pipelines/scoring/telemetry.py` module MUST be created, parallel to the existing `pipelines/ingestion/api/telemetry.py` and `pipelines/processing/telemetry.py`. It MUST: bootstrap the OTel tracer with the service name `fraudstream-scoring`, expose a `fraud_rule_evaluation_span()` context manager (already referenced in `pipelines/scoring/metrics.py`), and configure the sampling policy described in FR-011 (100% BLOCK/error, 1% head-based ALLOW, 100% tail-based for any error span — with v1 limitation as noted in FR-011). The `fraud_rule_evaluation_span()` context manager MUST set the following OTel span attributes: `fraud.transaction_id` (string), `fraud.channel` (string, e.g. `"API"` / `"ATM"` / `"POS"`), `fraud.rule_count` (int, number of rules evaluated), and `fraud.decision` (string, set by the caller after evaluation — one of `"BLOCK"`, `"FLAG"`, `"ALLOW"`). The tracer MUST expose a `trace_sampling_rate{decision_type}` Prometheus `Gauge` metric initialized to configured values at startup.

---

### Key Entities

- **Runbook**: A structured operational document with decision trees, step-by-step procedures, and verification checklists. Located in `docs/runbooks/`. Version-controlled; updated on every architectural change that affects the described procedure.
- **CircuitBreaker**: A software component wrapping all ML serving calls in the scoring engine. States: CLOSED (normal), OPEN (fallback active), HALF-OPEN (probing recovery). Configured via environment variables; state exposed as Prometheus metrics.
- **FailureScenario**: A documented and executable chaos test targeting a specific failure mode. Stored in `tests/chaos/`. Each scenario has a trigger, expected system behavior, pass/fail criteria, and a manual execution procedure for staging.
- **ADR (Architecture Decision Record)**: A document capturing a significant architectural decision: context, options considered, decision made, consequences, and review triggers. Located in `docs/adr/`. Status: Proposed → Accepted → Deprecated.
- **PostMortem**: A blameless incident analysis document. Contains timeline, root cause (5-whys), contributing factors, action items (each with owner, file path, due date). Status: Open → In-Review → Closed. Cannot close until all action items merged.
- **ShadowRule**: A fraud detection rule deployed with `mode: shadow` — fires and records triggers but does not influence the final BLOCK/FLAG/ALLOW decision. Promoted to `mode: active` only after FP rate validation.
- **CanaryDeployment**: A partial rollout of a new model or rule version to a configurable percentage of transaction traffic, with automated rollback on SLO breach.
- **PIIAuditRecord**: An append-only record in `audit.pii_scans` Iceberg table capturing: audit timestamp, scan scope (table + partition range), match count (0 = clean), matched pattern (if any), and remediation status.

---

## Success Criteria *(mandatory)*

- **SC-001**: An on-call engineer unfamiliar with the system resolves a DLQ alert (root cause: schema-invalid event) within 20 minutes using only the runbook and `replay-dlq-message.sh`, with zero incorrect replay attempts.
- **SC-002**: The CI pipeline blocks 100% of PRs containing Avro breaking changes before merge, with a structured error identifying the affected subject and field within 3 minutes of PR creation.
- **SC-003**: The ML circuit breaker transitions to OPEN within 30 seconds of the ML serving layer becoming unresponsive, and the scoring engine continues issuing rule-engine-only decisions with p99 latency < 100ms throughout the outage.
- **SC-004**: A new fraud rule deployed in shadow mode for 24 hours produces a measurable false-positive rate report in Streamlit with zero blocked legitimate transactions during the shadow period.
- **SC-005**: Every SEV-1/SEV-2 incident produces a closed post-mortem with at least one merged code/config change within 5 business days of the incident.
- **SC-006** **[EXCLUDED from this branch's merge gate — blocked by TD-007; Iceberg sinks must be implemented in a separate spec before SC-006 can be verified]**: The daily PII audit job detects a synthetically injected full PAN in `iceberg.enriched_transactions` within 24 hours and halts writes to the affected table within 1 minute of detection.
- **SC-007**: All 5 failure scenarios in `tests/chaos/FAILURE_SCENARIOS.md` pass in staging, proving graceful degradation within defined recovery windows, before any major release is promoted to production.
- **SC-008**: All self-managed components have ADRs with named managed alternatives; each ADR includes review trigger criteria and is reviewed at least annually.

---

## Assumptions

- The scoring engine is a Python service (consistent with project Python 3.11 standard); `pybreaker` or equivalent is a viable circuit breaker library.
- GitHub Actions is the CI/CD platform; Terraform is the IaC standard as referenced in the constitution tech stack.
- The `docs/` directory does not exist yet; all runbooks, ADRs, and process documents are new.
- Failure scenario tests are manual-trigger in v1 (run by a human in staging before release); automated chaos engineering (e.g., Chaos Monkey) is deferred to v2.
- The Streamlit app from Principle X (Constitution v1.2.0) is the UI surface for shadow rule monitoring, DLQ inspector, and model comparison — this spec adds data requirements to those pages but does not redesign them.
- `txn.rules.config` Kafka topic (referenced in Principle X v2) does not exist yet; shadow rule deployment in v1 is file-based (rule definition files in the scoring engine config directory, deployed via CI); Kafka-based config is deferred to the rule engine config UI spec.
- The `txn.fraud.labels` topic (FR-019) does not exist yet; the feedback loop data contract is specified here but implementation is deferred to a future spec.
- **Management API rate limiting**: The management API (port 8090) does not implement rate limiting in v1. This is intentional: demote/promote operations are idempotent (redundant calls return 409, not 5xx), Alertmanager deduplicates firing alerts by group key, and the API is only reachable within the internal Docker/VPC network. Rate limiting is deferred to v2 when mTLS is added (TD-010).
- **Dependency vulnerability scanning**: Automated dependency scanning (e.g., `pip-audit`, Dependabot, Snyk) is not a gating CI check in v1. Developers SHOULD run `pip-audit` locally before opening PRs, but the check is advisory only. Automated supply-chain gating is deferred to v2.

---

## Tech Debt

- **TD-001** *(FR-019)*: The model performance feedback loop requires a `txn.fraud.labels` topic fed by human review outcomes or chargeback signals. Neither the label ingestion pipeline nor the label schema is defined. This spec defines the architecture and metric contracts only; full implementation requires a dedicated spec covering label ingestion, ground-truth latency (chargebacks arrive days after the transaction), and partial label handling.

- **TD-002** *(FR-015)*: Failure scenario tests are manual-trigger in v1. Automated chaos injection (kill a Flink TaskManager pod on a schedule, inject latency into ML serving) requires a chaos engineering platform (Chaos Mesh, Litmus). This is deferred to v2; the manual test plan in `tests/chaos/FAILURE_SCENARIOS.md` is the v1 substitute.

- ~~**TD-003**~~ *(FR-022)*: ~~The rule canary policy requires `mode: shadow` support in the scoring engine rule evaluation loop. The current rule engine spec (003, not yet written) must include this field in the rule definition schema. If 003 ships without shadow mode, FR-022 cannot be implemented until 003 is amended.~~ **Retired** — spec-003 shipped with full shadow mode support (`mode: shadow` in `RuleDefinition`, `RuleMode` StrEnum, shadow evaluation loop in `pipelines/scoring/rules/engine.py`). The dependency is resolved; FR-022 can be implemented as written.

- ~~**TD-004** *(FR-003)*~~: ~~Terraform modules for Flink on Kubernetes require Helm charts for Flink operator deployment.~~ **Retired** — self-managed Kubernetes is no longer in scope. Flink is deployed via Amazon Managed Service for Apache Flink (AWS) or Dataflow (GCP). K8s operator Helm chart pinning is not required. See TD-010 for cloud provider module structure.

- **TD-005** *(FR-025 → FR-014)*: `pipelines/scoring/` contains no ML client — no file, no interface, no stub. The circuit breaker (FR-014) has nothing to wrap until FR-025 is shipped. The scoring engine currently issues decisions using rule logic only. Any work on ML-augmented scoring is blocked until `pipelines/scoring/ml_client.py` exists.

- **TD-006** *(FR-026 → FR-016)*: Redis is absent from `infra/docker-compose.yml`. The scoring engine, failure scenario tests (FR-015), and HA Terraform work (FR-016) all presuppose a running Redis. Local development cannot exercise the feature store path until FR-026 is shipped.

- **TD-007** *(FR-005 / SC-006)*: Apache Iceberg sinks (`iceberg.enriched_transactions`, `iceberg.fraud_decisions`) are referenced throughout the constitution and this spec but are not implemented in any pipeline. The PII audit job (FR-005), the model feedback loop (FR-019), and SC-006 all read from these tables. A dedicated spec for Iceberg sink wiring is needed before any of these FRs can be tested end-to-end.

- **TD-008** *(FR-022 / contracts/management-api.md)*: The management API (`POST /rules/{rule_id}/demote` and `/promote`) propagates rule mode changes via YAML file only in v1 (polling cycle ≤ 30s). The intended design publishes a `rule_mode_change` event to the `txn.rules.config` Kafka topic on every mode change, enabling real-time propagation to rule engine replicas, audit log, and analytics consumers. Full implementation requires: (1) `txn.rules.config` topic provisioned in Terraform (FR-003 scope extension); (2) Avro schema for `rule_mode_change` registered in Schema Registry; (3) Kafka producer wired in `pipelines/scoring/management_api.py`; (4) removal of the `config_event_published: false` v1 stub. Defer to the rule engine config UI spec.

- **TD-010** *(FR-003)*: Cloud provider Terraform modules are deferred to a dedicated cloud-deployment spec. The module structure will be: `infra/terraform/aws/` (MSK, Managed Flink application definition, ElastiCache cluster, S3 buckets + Glue catalog, IAM roles) and `infra/terraform/gcp/` (Confluent Cloud or Pub/Sub, Dataflow job template, Memorystore instance, GCS buckets + Dataplex catalog, IAM service accounts). Both provider modules share a common variable interface (`var.kafka_topic_config`, `var.redis_ttl_hours`, `var.flink_job_jar_path`) so application config does not change across targets. Local dev remains docker-compose — no Terraform apply needed for local. Defer to cloud-deployment spec.

- **TD-009** *(FR-022 / contracts/rule-definition-extension.md)*: The rule canary policy in v1 is two-stage only (shadow → full rollout). A hash-gated partial rollout intermediate stage is deferred. Full implementation requires: (1) `RuleMode.partial` added to `RuleMode` StrEnum; (2) `rollout_percentage: int = 100` field added to `RuleDefinition` (valid range 1–100; ignored when `mode != partial`); (3) evaluator behavior contract: when `rule.mode == RuleMode.partial`, apply rule only when `MurmurHash3(transaction_id) % 100 < rollout_percentage`; (4) `POST /rules/{rule_id}/promote-partial` endpoint added to management API; (5) YAML rule file format extended with `rollout_percentage` field. Defer to a dedicated canary/progressive-delivery spec.

---

## Glossary

Terms used throughout this spec that carry precise technical meanings distinct from their everyday usage.

| Term | Definition | Distinguished From |
|------|------------|--------------------|
| **determination** | The output of the rule engine evaluation stored in `EvaluationResult.determination`. Possible values: `"clean"` (no rules matched or no thresholds exceeded), `"flag"` (at least one rule matched; transaction flagged for review), `"block"` (at least one active blocking rule matched; transaction must be declined). The determination is produced by rule logic and ML score combined. | `decision` |
| **decision** | The downstream routing action taken by the scoring engine based on the `determination`. Maps `"clean"` → pass through, `"flag"` → route to review queue, `"block"` → decline and emit alert. The decision layer can be configured independently of the rule evaluation logic. | `determination` |
| **shadow rule** | A rule with `mode: shadow` in its `RuleDefinition`. It evaluates against live traffic and records trigger counts, but its `determination` contribution is suppressed — it cannot change a `"clean"` determination to `"flag"` or `"block"`. Used for canary validation before promoting a rule to `active`. | `active rule` |
| **active rule** | A rule with `mode: active` in its `RuleDefinition`. Its `determination` contribution is applied — it can change the evaluation outcome to `"flag"` or `"block"`. | `shadow rule` |
| **circuit breaker** | The `MLCircuitBreaker` in `pipelines/scoring/circuit_breaker.py` that wraps the ML model client. When the ML service fails ≥ N times within T seconds, the breaker opens and scoring falls back to rule-engine-only mode. Not related to Redis circuit breaking or Kafka producer retries. | — |
| **false positive (FP)** | A transaction that a shadow or active rule flagged or would-block, but whose final `determination` was `"clean"` (i.e., no other rule or ML score corroborated the trigger). Used to compute FP rate for the auto-demotion alert. Ground truth in v1 is approximated from the rule engine determination, not human review labels. | — |
| **DLQ** | Dead Letter Queue. A Kafka topic (`txn.processing.dlq`) that receives messages the pipeline cannot process successfully (schema-invalid, deserialization failure, event beyond allowed lateness, downstream unavailable). DLQ messages carry error-type headers used by the runbook decision tree. Not to be confused with the Kafka built-in dead-letter mechanism — this is an application-level DLQ topic. | — |
| **watermark** | A Flink event-time watermark — a monotonically increasing timestamp that tells the engine "all events with `event_time` ≤ watermark have arrived." Events arriving after the watermark advances beyond their `event_time` are classified as "late events." A stalled watermark (no advance within a configured interval) causes windows to never close, triggering a dedicated alert. | — |
| **EvaluationResult** | The output struct returned by the rule engine after evaluating all active and shadow rules against a transaction. Contains: `determination` (string: `"clean"` / `"flag"` / `"block"`), `rule_triggers` (list of triggered rule IDs with mode tag), `ml_score` (float, from `MLCircuitBreaker.score_with_fallback()`), and `used_ml_fallback` (bool). Consumed by the scoring engine to issue the final decision. | `determination`, `decision` |
