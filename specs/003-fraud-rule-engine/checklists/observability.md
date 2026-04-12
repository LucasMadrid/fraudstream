# Fraud Rule Engine — Observability & Alerting Requirements Checklist

**Purpose**: Unit tests for requirements — validate clarity, completeness, and measurability of observability specs  
**Created**: 2026-04-08  
**Feature**: [003-fraud-rule-engine](../spec.md)  
**Depth**: Comprehensive | **Audience**: Author / PR reviewer / SRE

---

## Requirement Completeness

- [x] CHK001 — Are the exact Prometheus counter names for per-rule evaluation count specified in the spec, or left to implementer discretion? [Completeness, FR-012]
- [x] CHK002 — Are the exact Prometheus counter names for per-rule flag count specified in the spec? [Completeness, FR-012]
- [x] CHK003 — Are metric label dimensions (e.g., `rule_id`, `family`, `severity`) for per-rule counters formally listed? [Completeness, FR-012]
- [x] CHK004 — Are requirements defined for distinguishing evaluation errors (field missing, exception) from rule non-matches in the metric output? [Completeness, FR-012]
- [x] CHK005 — Is the "existing metrics" system referenced in FR-012 formally identified (Prometheus, Graphite, OTel collector)? [Clarity, FR-012]

---

## Requirement Clarity

- [x] CHK006 — Is OTel span granularity specified — per-transaction, per-rule evaluation, or per-batch? [Clarity, Gap]
- [x] CHK007 — Are structured log field names for fraud evaluation events documented with a canonical schema? [Clarity, Gap]
- [x] CHK008 — Are log level requirements defined — what severity for rule matches, evaluation errors, and startup events? [Clarity, Gap]
- [x] CHK009 — Is the alert condition for zero-flag-rate quantified (duration window, threshold count, cooldown period)? [Clarity, Gap]
- [x] CHK010 — Is there a requirement specifying the OTel trace sampling strategy (head-based, tail-based, rate)? [Clarity, Gap]

---

## Requirement Consistency

- [x] CHK011 — Are metric naming conventions for the scoring stage consistent with those established in the enrichment pipeline (feature 002)? [Consistency, Gap]
- [x] CHK012 — Are structured log field names for fraud evaluation consistent with the enrichment stage's existing log schema? [Consistency, Gap]
- [x] CHK013 — Is the observability approach (pull-based Prometheus vs. push-based) consistent across enrichment and scoring stages? [Consistency, Gap]

---

## Acceptance Criteria Quality

- [x] CHK014 — Is SC-005 ("per-rule eval & flag counts observable via metrics; match expected values in test runs") quantified with specific expected counter values, not just "observable"? [Measurability, SC-005]
- [x] CHK015 — Are the US4 acceptance scenarios (AC-1: counters increment, AC-2: metrics visible) expressed as objectively verifiable assertions with exact expected values? [Measurability, US4]
- [x] CHK016 — Can SC-005 be validated in the unit test environment without a running Prometheus instance — is the test mechanism specified? [Measurability, SC-005]

---

## Scenario & Edge Case Coverage

- [x] CHK017 — Are requirements defined for what happens to metrics when a rule is disabled (`enabled: false`) — should its counters still appear (at zero) or be absent? [Coverage, FR-006, FR-012]
- [x] CHK018 — Are requirements defined for the cardinality risk of per-rule Prometheus labels as the rule set grows beyond 10 rules? [Coverage, FR-004]
- [x] CHK019 — Are requirements defined for metric retention — how long must per-rule counters be stored and queryable? [Coverage, Gap]
- [x] CHK020 — Are requirements for OTel span attributes specified (what data must each span carry — rule ID, family, determination, severity)? [Completeness, Gap]

---

## Non-Functional Requirements

- [x] CHK021 — Is there a requirement for alerting SLA — how quickly must a zero-flag-rate alert reach on-call (PagerDuty/Slack)? [Gap, Non-Functional]
- [x] CHK022 — Are Grafana dashboard or metrics visibility requirements defined, or is metric existence sufficient? [Gap, Non-Functional]
- [x] CHK023 — Is a metric scrape interval requirement specified for the fraud evaluation stage? [Gap, Non-Functional]
- [x] CHK024 — Are alert routing requirements defined — who receives fraud evaluator alerts and via which channel? [Gap, Non-Functional]

---

## Dependencies & Assumptions

- [x] CHK025 — Is the dependency on an existing metrics collection infrastructure (Prometheus, OTel collector) formally documented as an assumption, with a version requirement? [Dependencies, Gap]
- [x] CHK026 — Is there a requirement specifying what "healthy" vs "degraded" evaluator state looks like through the metrics interface — so on-call knows what to page on? [Gap, Assumption]

---

## Notes

- FR-012 references "existing metrics" without naming the system — CHK005 and CHK011 target this gap.
- SC-005 is the sole measurable observability success criterion; CHK014–CHK016 probe whether it's actually measurable in practice.
- Per-rule Prometheus labels are a standard pattern but carry cardinality risk at scale (CHK018).
