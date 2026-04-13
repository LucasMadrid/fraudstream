# Requirements Quality Checklist: Operational Excellence

**Purpose**: Validate completeness, clarity, consistency, and measurability of all 27 FRs in this spec before implementation begins. Audience: PR reviewer.
**Created**: 2026-04-12
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md)

---

## 🔴 Mandatory Gate Items

Items in this section MUST pass before the PR can be approved. They correspond to Constitution ⚠️ WATCH flags and hard blocking dependencies identified in the architecture review.

- [X] CHK001 — Is the 5ms ML probe timeout in FR-014 specified precisely enough that an implementer will reach for `ThreadPoolExecutor(timeout=0.005)` rather than a naive `pybreaker` probe? The spec says "probe timeout 5ms (must not consume scoring latency budget)" but does not name the mechanism — is the implementation path unambiguous without reading research.md? [Clarity, Spec §FR-014, Constitution Principle II ⚠️]

- [X] CHK002 — Does FR-002 explicitly call out that the current CI job sets `BACKWARD` (not `BACKWARD_TRANSITIVE`) and that this gap MUST be closed as part of this spec — not assumed already done? Is the fix-in-flight status of this FR documented so a reviewer knows whether to check the CI diff? [Completeness, Spec §FR-002, Constitution Principle III]

- [X] CHK003 — Are FR-005, FR-019, and SC-006 explicitly marked as **deferred/blocked by TD-007 (Iceberg sinks not implemented)** in the spec text? Is there a stated definition of "done" for this spec that excludes end-to-end PII audit testing — preventing an implementer from mistakenly treating SC-006 as a merge gate for this branch? [Completeness, Spec §FR-005, §SC-006, §TD-007, Gap]

---

## Requirement Completeness

- [X] CHK004 — Are the 7 CI/CD pipeline stages in FR-001 defined with their dependency ordering (which run in parallel, which are sequential gates)? Is "Stages 1–3 MUST run in parallel where independent" precise enough to implement without ambiguity about what "where independent" means? [Completeness, Spec §FR-001]

- [X] CHK005 — Is the structured error format for `BREAKING_CHANGE` CI failures (FR-002) specified? The spec says "a structured error identifying: the affected schema subject, the incompatible field(s), and the required remediation" — are these three fields a minimum, and is the output format (stdout, JSON, PR comment) defined? [Completeness, Spec §FR-002]

- [X] CHK006 — Does FR-004 specify what happens when a deployment script fails mid-execution? Three scripts are required (rolling upgrade, schema migration, checkpoint rollback) — are the failure-mode behaviors (abort, compensate, alert) defined for each? [Completeness, Spec §FR-004, Edge Case]

- [X] CHK007 — Is the PAN pattern to scan for in FR-005 defined precisely (regex? Luhn validation? just 16-digit sequence?)? "Full 16-digit PAN patterns" is ambiguous — a 16-digit number appears in many contexts. Is the false-positive risk addressed? [Clarity, Spec §FR-005, Ambiguity]

- [X] CHK008 — Does FR-007 (DLQ runbook) specify all 4 error type classifications from the acceptance scenario (schema invalid, deserialization failure, late event beyond window, downstream unavailable) in the runbook structure requirement? The FR lists 4 but the acceptance scenario AS-1 references only 3 classification examples — are these aligned? [Consistency, Spec §FR-007, §US-1 AS-1]

- [X] CHK009 — Does FR-010 (observability runbook) define all 4 troubleshooting trees with terminal actions (not just starting questions)? Are all branches in each tree specified, or are implementers expected to design the trees themselves? [Completeness, Spec §FR-010]

- [X] CHK010 — Does FR-011 address the OTel Python SDK limitation that tail-based sampling is not natively supported? The spec requires "tail-based sampling at 100% for any trace containing an error span" — but research.md (RQ-002) documents that this requires an OTel Collector not yet in docker-compose. Is the v1 constraint (head-based only) reflected in the spec text? [Completeness, Spec §FR-011, Assumption]

- [X] CHK011 — Are the label sets for the 3 new Prometheus metrics in FR-013 fully defined (`late_events_within_window_total`, `late_events_beyond_window_total`, `corrected_record_latency_ms`)? The spec defines the metric names but not the label dimensions — are stage, topic, and partition labels expected? [Completeness, Spec §FR-013]

- [X] CHK012 — Does FR-014 specify whether the N-errors-within-T-seconds window is a **sliding** or **tumbling** window? This distinction changes the implementation materially — 3 errors at t=0s, t=9.9s, t=10.1s would open the breaker under sliding but not tumbling. [Clarity, Spec §FR-014, Ambiguity]

- [X] CHK013 — Are all 5 failure scenarios in FR-015 fully specified with quantitative pass/fail criteria? Reviewing AS-1 through AS-3 in US-3: "within 60s", "within 100ms budget" — but FR-015 says "verify partition redistribution and lag recovery within 60s" — are these criteria consistent across the US and the FR? [Consistency, Spec §FR-015, §US-3]

- [X] CHK014 — Does FR-016 define the Redis Sentinel configuration for local dev with enough specificity to implement (sentinel port, quorum, primary name)? "1 primary and 1 replica" names the topology but not the sentinel configuration. [Completeness, Spec §FR-016]

- [X] CHK015 — Does FR-017 (post-mortem process) specify what happens when the 24-hour deadline for opening a post-mortem is missed — who is escalated to, what the consequence is? The spec states the mandate but not the enforcement mechanism. [Completeness, Spec §FR-017, Edge Case]

- [X] CHK016 — Does FR-019 (model feedback loop) define the `txn.fraud.labels` topic schema, or is it explicitly and visibly deferred? The spec says "future" and TD-001 acknowledges this — but is it clear enough to a reviewer that this FR produces zero testable code in v1? [Completeness, Spec §FR-019, §TD-001]

- [X] CHK017 — Does FR-021 (KPI targets) define baseline values, or does it leave "model score distribution baseline (mean ± 1σ)" to be determined at first measurement? If baselines don't exist yet, is there a process specified for establishing them? [Completeness, Spec §FR-021, Gap]

- [X] CHK018 — Does FR-022 (canary policy) specify who or what executes the automated rollback — is it the management API, Alertmanager, a Flink job, or an operator? The auto-demotion for rules is described in US-4 AS-4 but the model canary rollback mechanism is not specified. [Completeness, Spec §FR-022, Gap]

- [X] CHK019 — Does FR-023 define which rules are in scope for the 10+10 minimum test requirement, given that it's now a gap audit of existing tests? Is there a canonical list of "production rules" against which coverage is measured? [Completeness, Spec §FR-023]

- [X] CHK020 — Does FR-027 define the span attribute set for `fraud_rule_evaluation_span()` — which attributes (rule_id, mode, decision, latency_ms) must be on the span? The spec defines the context manager name but not its observable surface. [Completeness, Spec §FR-027]

---

## Requirement Clarity & Precision

- [X] CHK021 — Is "latency bench (main branch only)" in FR-001 unambiguous — does "main branch" mean PRs targeting main, commits on main, or both? A reviewer needs this to assess whether feature-branch CI is correctly scoped. [Clarity, Spec §FR-001]

- [X] CHK022 — Is the `replay-dlq-message.sh` script in FR-007 a new deliverable or an assumed pre-existing tool? The US-1 Independent Test references it as if it exists. If it's new, it should appear as an explicit deliverable in FR-007 or FR-004. [Clarity, Spec §FR-007, §FR-004, Ambiguity]

- [X] CHK023 — Is the canary traffic split for rules in FR-022 defined with a specific hash function and modular arithmetic? "Route N% by `account_id` hash" is the policy, but without a specified hash function the implementation is non-deterministic across services. [Clarity, Spec §FR-022, Ambiguity]

- [X] CHK024 — Is "100% sampling for BLOCK decisions" in FR-011 defined for all BLOCK types (rule-triggered and ML-threshold-triggered), or only for one? Is a BLOCK by a shadow rule (which by definition cannot issue a BLOCK) excluded? [Clarity, Spec §FR-011]

- [X] CHK025 — Is `maxmemory-policy allkeys-lru` in FR-016 consistent with requiring explicit TTL on all written keys? `allkeys-lru` evicts **any** key (with or without TTL) when memory is full — if explicit TTL is required, `volatile-lru` would be the correct policy. Is this a conflict? [Consistency, Spec §FR-016, Conflict]

---

## Requirement Consistency

- [X] CHK026 — Is the shadow rule FP rate threshold consistent across the spec? US-4 AC-2 implies promotion after "< 2% false-positive rate" (24h of shadow data) while FR-022 auto-demotion fires at "> 5% FP rate within 1h of promotion". Are these thresholds intentionally different (shadow validation vs. post-promotion alert) or an inconsistency? [Consistency, Spec §US-4, §FR-022]

- [X] CHK027 — Do the circuit breaker error thresholds in FR-014 (3 errors, 10s window) match the acceptance scenario in US-3 AS-1 ("3 consecutive errors within 10 seconds")? The FR says "N consecutive errors within T seconds" — is "consecutive" (strictly sequential with no successes in between) the same as "within T seconds" (time-windowed)? [Consistency, Spec §FR-014, §US-3 AS-1]

- [X] CHK028 — Is the `txn.rules.config` topic assumption consistent throughout the spec? Assumptions say "does not exist yet; v1 is file-based" but FR-022 and US-4 AS-3 describe publishing config changes to `txn.rules.config` as part of promotion — are these consistent, or does v1 file-based mean the topic is not used? [Consistency, Spec §FR-022, §Assumptions, Conflict]

---

## Acceptance Criteria Quality

- [X] CHK029 — Is "zero incorrect replay attempts" in SC-001 measurable? What constitutes an "incorrect" replay attempt — replaying to the wrong topic, replaying without dry-run first, or replaying a late event instead of discarding? Is the definition in the runbook (FR-007) sufficient to make this criterion objective? [Measurability, Spec §SC-001]

- [X] CHK030 — Is SC-003's "within 30 seconds" for circuit breaker opening automatically testable, or does it require manual timing? If the failure scenario test plan (FR-015) covers this, is the test specified with enough precision to be deterministic? [Measurability, Spec §SC-003, §FR-015]

- [X] CHK031 — Is SC-004's "measurable false-positive rate report" defined with required fields? "False-positive rate" is undefined in the spec — is it (shadow triggers on known-legitimate transactions) / (total shadow triggers)? What data source provides the "known-legitimate" ground truth in shadow mode? [Measurability, Spec §SC-004, Ambiguity]

- [X] CHK032 — Is SC-005's "5 business days" defined in terms of timezone and what counts as a business day for this team? Is there a mechanism (GitHub issue label, automation) that enforces this SLA, or is it aspirational? [Measurability, Spec §SC-005, Gap]

- [X] CHK033 — Is SC-008 ("reviewed at least annually") enforced by any mechanism defined in this spec, or is it a policy statement without a tracking artifact? If it relies on a calendar reminder or ADR review section only, is that sufficient? [Measurability, Spec §SC-008, Gap]

---

## Edge Case & Scenario Coverage

- [X] CHK034 — Is the scenario where a shadow rule has **zero triggers** during its shadow period addressed? Is a minimum observation period (or minimum transaction volume) required before promotion is enabled? Without this, a rule could be promoted after processing no matching transactions. [Coverage, Edge Case, Gap]

- [X] CHK035 — Is the scenario where CI schema validation **cannot reach the Schema Registry** (network partition) defined — does CI fail open (pass) or fail closed (block the PR)? The spec requires CI to block breaking changes but doesn't address Registry unavailability. [Coverage, Edge Case, Spec §FR-002]

- [X] CHK036 — Is the concurrent auto-demotion scenario covered — what happens if two rules simultaneously breach the FP rate threshold and Alertmanager fires two simultaneous webhooks to `/rules/{rule_id}/demote`? Is the management API specified as idempotent per rule, and is the 409 response defined for already-shadow state? [Coverage, Edge Case, Spec §FR-022, contracts/management-api.md]

- [X] CHK037 — Does the PR template (FR-006) specify what a reviewer must do when a checklist item is not applicable (e.g., "PII audit passed" on a PR with no data pipeline changes)? Are N/A items permissible, and if so, is documentation of the N/A decision required? [Coverage, Edge Case, Spec §FR-006]

- [X] CHK038 — Are requirements defined for the scenario where `terraform apply` partially fails during Kafka topic provisioning (FR-003)? The edge cases section mentions this and requires idempotency + `create_before_destroy` — but is this a requirement on the Terraform module spec, or just a note? [Coverage, Edge Case, Spec §FR-003]

---

## Dependencies & Assumptions

- [X] CHK039 — Is the assumption that the Streamlit analytics app (from Principle X) currently exists in the codebase validated? FRs 018, 019, and US-4 AS-2 all require adding pages/data to this app. If the app doesn't exist yet, these FRs have an undocumented prerequisite. [Dependency, Assumption, Gap]

- [X] CHK040 — Is TD-003 (rule canary requiring shadow mode from spec-003) tracked as a cross-spec dependency with an explicit owner? If spec-003 ships without shadow mode, FR-022 cannot be implemented — is this risk surfaced in a way a PR reviewer would catch? [Dependency, Spec §TD-003]

- [X] CHK041 — Is the prerequisite chain FR-026 → FR-016 → FR-015 (Redis must exist in docker-compose before HA Terraform before failure scenario tests) explicitly ordered in the spec so an implementer knows which must ship first? [Dependency, Spec §FR-015, §FR-016, §FR-026]

- [X] CHK042 — Is the assumption that "GitHub Actions is the CI/CD platform" validated against the existing `.github/workflows/` setup? The spec assumes this but should confirm CI is already functional (not a net-new setup). [Assumption, Spec §Assumptions]

---

## Glossary Completeness & Consistency

*(Added 2026-04-12 — covers the Glossary section introduced during speckit.analyze remediation)*

- [X] CHK043 — Does the Glossary define all domain terms that appear in the spec without prior context? Specifically: is `DLQ` (dead-letter queue) defined, is `watermark` (Flink event-time watermark — the mechanism that drives late-event detection) defined, and is `EvaluationResult` (referenced in the `determination` definition) defined or cross-referenced to a source-of-truth contract? [Completeness, Spec §Glossary, Gap]

- [X] CHK044 — Is the Glossary `determination` entry consistent with its usage throughout the spec? FR-014 and FR-022 use the term `decision` to mean both the routing action (Glossary definition) and the circuit breaker output in some places. Are all occurrences of "decision" in the spec unambiguously pointing to the Glossary definition, or could a reader interpret it as `determination` in some contexts? [Consistency, Spec §Glossary, §FR-014, §FR-022, Conflict]

---

## Gaps Revealed by Recent Requirement Edits

- [X] CHK045 — Is the unit mismatch between `cb_probe_timeout_ms` (config field: integer, milliseconds, default `5`) and the implementation mechanism (`future.result(timeout=0.005)`, seconds) documented in the spec? An implementer reading FR-014 and FR-025 in isolation will see a field named `_ms` with default `5` and a required seconds value of `0.005` — is the conversion explicitly noted to prevent an off-by-1000 error? [Clarity, Spec §FR-014, §FR-025, Ambiguity]

- [X] CHK046 — Are the span attributes that `fraud_rule_evaluation_span()` MUST set (`rule.id`, `rule.mode`, `fraud.decision`, `fraud.latency_ms`) specified in the spec (FR-027), or do they appear only in tasks.md T007? If a reviewer is evaluating spec completeness without reading tasks.md, is the observable surface of this context manager fully defined? [Completeness, Spec §FR-027, Gap]

- [X] CHK047 — Is the structured JSON logging requirement for the Management API demote/promote endpoints (logging `{"event": "rule_mode_change", "rule_id": ..., ...}` on every state change) expressed as a requirement in spec.md, or only as an implementation note in tasks.md T025? Constitution Principle VIII mandates observability on all state mutations — should this be a stated FR (or annotation on FR-022) rather than a task-level note? [Completeness, Spec §FR-022, Constitution Principle VIII, Gap]

- [X] CHK048 — Does FR-024 define what a compliant ADR `## Review Schedule` section must contain (named `owner:` field, `next_review_date:` in ISO format), or is the format only specified in tasks.md T035–T038 and the T042 gate? If the Review Schedule structure is a spec requirement (SC-008 depends on it), should FR-024 enumerate the required fields so a reviewer can validate ADR completeness without reading tasks.md? [Completeness, Spec §FR-024, §SC-008, Gap]

---

## Exception Flow Coverage

- [X] CHK049 — Is the failure mode for the Alertmanager → Management API webhook path defined? If the `POST /rules/{rule_id}/demote` call fails (scoring engine down, returns 500, or network partition), does Alertmanager retry? Is there a dead-letter path for missed auto-demotions — i.e., if the webhook silently fails, does the high-FP rule remain active indefinitely? [Coverage, Exception Flow, Spec §FR-022, contracts/management-api.md, Gap]

- [X] CHK050 — Does the spec reflect that TD-003 ("rule canary requires `mode: shadow` support from spec-003") is resolved? TD-003 in spec.md says "spec-003, not yet written" but spec-003 has been implemented with shadow mode. Is TD-003 stale, and if so, should it be marked `**Retired**` (consistent with how TD-004 was retired) to prevent a reviewer from flagging it as an open blocker? [Consistency, Spec §TD-003, Gap]

- [X] CHK051 — Is the scenario where a model canary and a rule canary are simultaneously active addressed? FR-022 defines auto-rollback for each independently (fraud rate delta > 5% for model; FP rate > 5% for rules) — but if both are active, could overlapping rollback triggers produce a conflict (e.g., model rolled back but rule promoted, or dual demote events racing the management API)? Is this concurrency scenario in scope or explicitly excluded? [Coverage, Edge Case, Spec §FR-022, Gap]
