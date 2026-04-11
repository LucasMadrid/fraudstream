# Fraud Rule Engine — Operational & Deployment Requirements Checklist

**Purpose**: Unit tests for requirements — validate completeness and clarity of operational, deployment, and runtime specs  
**Created**: 2026-04-08  
**Feature**: [003-fraud-rule-engine](../spec.md)  
**Depth**: Comprehensive | **Audience**: Author / PR reviewer / Platform / SRE

---

## Requirement Completeness

- [x] CHK001 — Are job startup requirements defined — what must succeed (Schema Registry reachable, rules.yaml loaded, Kafka topic exists, PostgreSQL reachable) before the job is declared healthy? [Completeness, Gap]
- [x] CHK002 — Are deployment ordering requirements specified — must `txn.fraud.alerts` topic and `fraud_alerts` table exist before job startup? [Completeness, Spec §Assumptions §3, §6]
- [x] CHK003 — Are requirements defined for the `rules.yaml` file location and mounting strategy (ConfigMap, volume mount, S3 path) in each environment? [Completeness, FR-005]
- [x] CHK004 — Are graceful shutdown requirements defined — what must flush or complete (in-flight Kafka emits, open PostgreSQL writes) before the job stops? [Completeness, Gap]
- [x] CHK005 — Are environment-specific configuration requirements defined — are different `rules.yaml` sets used in dev, staging, and production? [Completeness, FR-005]
- [x] CHK006 — Are capacity planning requirements defined — expected events per second, alert rate, and DB write throughput that the deployment must sustain? [Completeness, Gap]

---

## Requirement Clarity

- [x] CHK007 — Is the fail-fast behavior on invalid `rules.yaml` (FR-007) specified with a definition of "descriptive error" — what fields must the error message contain? [Clarity, FR-007]
- [x] CHK008 — Is the job restart mechanism for config changes (Spec §Assumptions §7) documented as a formal requirement with expected downtime SLA? [Clarity, Spec §Assumptions §7]
- [x] CHK009 — Is there a requirement clarifying whether Schema Registry failure at startup causes job abort (fail-fast) or degraded startup (skip schema registration)? [Clarity, FR-009]
- [x] CHK010 — Is the Kafka consumer group `auto.offset.reset` policy specified — `earliest` or `latest` — and are implications for re-processing documented? [Clarity, Gap]
- [x] CHK011 — Are requirements defined for how `rules.yaml` changes are validated before deployment (dry-run, CI validation step)? [Clarity, FR-007]
- [x] CHK012 — Is there a requirement clarifying what constitutes a successful deployment of the fraud rule engine (health probe, Kafka consumer lag threshold, metric emission)? [Clarity, Gap]

---

## Requirement Consistency

- [x] CHK013 — Are operational requirements for the fraud evaluation stage consistent with those already defined for the enrichment stage (feature 002) — same checkpoint intervals, parallelism model? [Consistency, Gap]
- [x] CHK014 — Is the job restart mechanism (Spec §Assumptions §7) consistent with how feature 001 and feature 002 handle config changes? [Consistency, Spec §Assumptions §7]

---

## Acceptance Criteria Quality

- [x] CHK015 — Is the "job fails at startup" requirement (FR-007) measurable — is there a test that validates the exit code, log message, and absence of partial startup state? [Measurability, FR-007]
- [x] CHK016 — Are requirements for consumer lag acceptable thresholds quantified — what lag is acceptable before the SLO is breached? [Measurability, Gap]

---

## Scenario & Edge Case Coverage

- [x] CHK017 — Are requirements defined for Flink checkpoint recovery after a mid-evaluation crash — which records are re-processed and what guarantees apply to duplicate alert emission? [Coverage, Gap]
- [x] CHK018 — Are requirements defined for what happens to in-flight transactions during a planned job restart for config changes? [Coverage, Spec §Assumptions §7]
- [x] CHK019 — Are requirements defined for what happens when the Flink job starts but `rules.yaml` has zero enabled rules — is this a startup error or a valid (silent) configuration? [Coverage, FR-002, FR-007]
- [x] CHK020 — Are requirements defined for rolling vs blue/green deployment strategy — is a brief dual-processing window acceptable? [Coverage, Gap]
- [x] CHK021 — Are requirements defined for what happens when PostgreSQL becomes unavailable mid-operation after job startup (not at startup — that case is covered by FR-007)? [Coverage, Edge Case]

---

## Non-Functional Requirements

- [x] CHK022 — Is the Flink parallelism requirement for the fraud evaluation stage specified relative to the enrichment job's parallelism? [Gap, Non-Functional]
- [x] CHK023 — Is the Flink task manager memory allocation requirement specified for the scoring stage? [Gap, Non-Functional]
- [x] CHK024 — Are requirements for consumer lag monitoring defined — is there a Prometheus metric or alerting rule for lag on `txn.enriched`? [Gap, Non-Functional]
- [x] CHK025 — Is there a requirement for a job readiness endpoint that CI/CD pipelines can query before declaring a deployment successful? [Gap, Non-Functional]

---

## Dependencies & Assumptions

- [x] CHK026 — Is the PostgreSQL schema migration responsibility formally assigned (Spec §Assumptions §3 says "infrastructure concern") — is there a concrete runbook or automation requirement? [Assumption, Spec §Assumptions §3]
- [x] CHK027 — Is the "Kafka topic pre-provisioned" assumption (Spec §Assumptions §6) backed by a required infrastructure-as-code artifact (Kafka topic config in `infra/`)? [Assumption, Spec §Assumptions §6]
- [x] CHK028 — Are on-call ownership requirements defined for the fraud rule engine — who is paged, what is the escalation path? [Gap, Operational]

---

## Notes

- Spec §Assumptions §7 ("job restart for config changes") is accepted for Phase 1 but is not expressed as a formal NFR with downtime budget — CHK008 targets this.
- FR-007 fail-fast is the key operational safety gate; CHK007 and CHK011 probe whether its definition is precise enough to implement and test consistently.
- CHK019 surfaces a real ambiguity: zero enabled rules is technically valid YAML but operationally dangerous — the spec does not address it.
