# Fraud Rule Engine — Security & Data Protection Requirements Checklist

**Purpose**: Unit tests for requirements — validate completeness and clarity of security, access control, and compliance specs  
**Created**: 2026-04-08  
**Feature**: [003-fraud-rule-engine](../spec.md)  
**Depth**: Comprehensive | **Audience**: Author / PR reviewer / Security team

---

## Requirement Completeness

- [x] CHK001 — Is a data classification level assigned to `fraud_alerts` records (e.g., PII, sensitive financial, internal)? [Completeness, FR-014]
- [x] CHK002 — Are access control requirements defined for who can read `fraud_alerts` records from the PostgreSQL table? [Completeness, FR-015]
- [x] CHK003 — Are access control requirements defined for who can modify `rules.yaml` threshold values (e.g., mandatory PR review by fraud operations team)? [Completeness, Gap]
- [x] CHK004 — Are Kafka topic ACL requirements specified for `txn.fraud.alerts` (producer and consumer permissions)? [Completeness, FR-009]
- [x] CHK005 — Are requirements for secret management defined — Kafka credentials and DB credentials must not be hardcoded, and a vault/secret store mechanism must be specified? [Completeness, Gap]

---

## Requirement Clarity

- [x] CHK006 — Is the compliance or regulatory driver for the audit trail (FR-014) identified — PCI-DSS, GDPR, SOC 2, or internal policy? [Clarity, FR-014]
- [x] CHK007 — Are RBAC requirements for the `status` field update path (pending → confirmed-fraud / false-positive) specified with named roles? [Clarity, FR-015]
- [x] CHK008 — Is "audit trail" in FR-014 defined with specific fields required for compliance — reviewer identity, timestamp, reason, before/after state? [Clarity, FR-014]
- [x] CHK009 — Are Kafka consumer group ACL requirements defined for downstream consumers of `txn.fraud.alerts`? [Clarity, FR-009]
- [x] CHK010 — Is there a requirement specifying whether `matched_rule_names` in `FraudAlertRecord` should be masked or redacted in logs to prevent rule enumeration? [Clarity, Gap]

---

## Requirement Consistency

- [x] CHK011 — Are security requirements for `txn.fraud.alerts` consistent with the security posture already established for `txn.api` and `txn.enriched` topics? [Consistency, Gap]
- [x] CHK012 — Are PostgreSQL access control requirements for `fraud_alerts` consistent with how other pipeline-adjacent databases are governed? [Consistency, Gap]
- [x] CHK013 — Is there a requirement to ensure rule threshold values in `rules.yaml` are not exposed in structured logs (operational security)? [Consistency, Gap]

---

## Acceptance Criteria Quality

- [x] CHK014 — Can access control requirements (CHK002, CHK003, CHK004) be objectively verified — is there a test or audit mechanism specified? [Measurability, Gap]
- [x] CHK015 — Is the audit trail requirement (FR-014) measurable — is there a specific set of fields whose presence constitutes a valid audit record? [Measurability, FR-014]

---

## Scenario & Edge Case Coverage

- [x] CHK016 — Are requirements defined for what happens if a rule disabling event (`enabled: false`) is not audited — can fraud ops reconstruct rule coverage gaps post-incident? [Coverage, FR-006]
- [x] CHK017 — Are requirements defined for data subject rights — e.g., if a GDPR right-to-erasure request arrives, must `fraud_alerts` records for that account be deleted or anonymized? [Coverage, Gap]
- [x] CHK018 — Are requirements specified for what happens when `rules.yaml` is tampered with between job restarts — is integrity checking (hash/signature) required? [Coverage, Gap]
- [x] CHK019 — Are requirements defined for preventing raw transaction fields (account number, card PAN) from appearing in structured fraud evaluation logs? [Coverage, Gap]

---

## Non-Functional Requirements

- [x] CHK020 — Is there a requirement for encryption at rest for the `fraud_alerts` PostgreSQL table? [Gap, Non-Functional]
- [x] CHK021 — Is there a requirement for TLS encryption in transit between Flink workers and PostgreSQL? [Gap, Non-Functional]
- [x] CHK022 — Is there a requirement for TLS encryption in transit between Flink workers and Kafka brokers? [Gap, Non-Functional]
- [x] CHK023 — Is there a credential rotation requirement for Kafka API keys and PostgreSQL passwords? [Gap, Non-Functional]
- [x] CHK024 — Is there a vulnerability management requirement for the `pipelines/scoring` package (dependency scanning, CVE patching SLA)? [Gap, Non-Functional]

---

## Dependencies & Assumptions

- [x] CHK025 — Is the assumption that "access control at repository/PR level only" (Spec §Assumptions §9) sufficient — or is runtime access control also needed for `rules.yaml`? [Assumption, Gap]
- [x] CHK026 — Are Schema Registry access control requirements defined — who may register new `fraud-alert` schema versions, and is approval required for breaking changes? [Dependency, FR-009]

---

## Notes

- Spec §Assumptions §9 explicitly states "no authorization layer" — CHK007, CHK002, CHK003 probe whether this is intentional or a gap.
- FR-014 mandates a persistent audit trail but does not cite the compliance driver — CHK006 targets this.
- `rules.yaml` governs fraud detection sensitivity; unauthorized changes could suppress detection — CHK003 and CHK018 are high-risk items.
