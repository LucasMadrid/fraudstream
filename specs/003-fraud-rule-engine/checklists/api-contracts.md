# Fraud Rule Engine — API & Schema Contract Requirements Checklist

**Purpose**: Unit tests for requirements — validate completeness and consistency of data contracts, schema specs, and API definitions  
**Created**: 2026-04-08  
**Feature**: [003-fraud-rule-engine](../spec.md)  
**Depth**: Comprehensive | **Audience**: Author / PR reviewer / Platform team

---

## Requirement Completeness

- [x] CHK001 — Are all fields of the `FraudAlert` Avro schema specified with names, types, nullability, and default values? [Completeness, FR-009]
- [x] CHK002 — Is the Schema Registry subject naming convention specified (e.g., `fraud-alert-v1` vs `txn.fraud.alerts-value`)? [Completeness, FR-009]
- [x] CHK003 — Is the `txn.enriched` input contract formally specified for feature 003 — field list, types, and null semantics for all fields consumed by the 3 rule families? [Completeness, FR-001]
- [x] CHK004 — Is the `FraudAlertRecord` PostgreSQL schema formally specified with all column names, types, constraints, and index requirements? [Completeness, FR-014]
- [x] CHK005 — Is a DLQ topic for failed fraud alert deliveries formally defined — name, message format, routing rules, and consumer? [Completeness, FR-010]
- [x] CHK006 — Is the `rules.yaml` schema formally specified — all field names, types, required vs optional, and allowed values per field? [Completeness, FR-005]
- [x] CHK007 — Are the `RuleDefinition.conditions` structure requirements formally specified (field name, operator, threshold format and type)? [Completeness, FR-005]

---

## Requirement Clarity

- [x] CHK008 — Is the `matched_rule_names` field in `FraudAlert` specified with ordering semantics and deduplication requirements (when a transaction matches the same rule twice)? [Clarity, FR-008]
- [x] CHK009 — Is the `highest_severity` derivation rule documented — how is it computed when multiple rules of different severities match? [Clarity, FR-008]
- [x] CHK010 — Is the `evaluation_timestamp` field specified with timezone (UTC assumed?) and precision requirements (ms, µs)? [Clarity, FR-008]
- [x] CHK011 — Is the allowed values list for `severity` in `RuleDefinition` and `FraudAlert` formally specified and guaranteed consistent? [Clarity, FR-008]
- [x] CHK012 — Is the allowed values list for `status` in `FraudAlertRecord` formally constrained (pending / confirmed-fraud / false-positive — no others)? [Clarity, FR-015]
- [x] CHK013 — Is the `family` field in `RuleDefinition` constrained to the three defined families (VEL, IT, ND) with error behavior if unknown? [Clarity, FR-003, FR-007]
- [x] CHK014 — Are threshold field names in `rules.yaml` specified per rule family with expected types (integer, Decimal, milliseconds)? [Clarity, FR-005]
- [x] CHK015 — Is there a requirement specifying what constitutes an "unknown field" that causes FR-007 fail-fast — is it strict or lenient parsing? [Clarity, FR-007]

---

## Requirement Consistency

- [x] CHK016 — Are the `severity` values in `RuleDefinition` (`high`, `medium`, `critical`) consistent with the values in `FraudAlert` and `FraudAlertRecord`? [Consistency, FR-008]
- [x] CHK017 — Is the `transaction_id` field name consistent across `EnrichedTransaction`, `FraudAlert`, and `FraudAlertRecord` — same name, type, and format? [Consistency, FR-008, FR-014]
- [x] CHK018 — Is the `account_id` field name consistent across `EnrichedTransaction`, `FraudAlert`, and `FraudAlertRecord`? [Consistency, FR-008, FR-014]
- [x] CHK019 — Are the `txn.fraud.alerts` topic naming conventions consistent with the existing topic naming pattern (`txn.<stage>`) established in feature 001? [Consistency, FR-009]
- [x] CHK020 — Is the Schema Registry compatibility mode (`BACKWARD_TRANSITIVE`) consistent with the compatibility mode used for existing schemas in the pipeline? [Consistency, FR-009]

---

## Acceptance Criteria Quality

- [x] CHK021 — Is the `fraud_alerts.transaction_id` uniqueness constraint (idempotency) expressed as a schema requirement, not only as an implementation decision? [Measurability, FR-014]
- [x] CHK022 — Is the schema compatibility requirement measurable — is there a CI gate that verifies `BACKWARD_TRANSITIVE` compatibility before merge? [Measurability, FR-009]

---

## Scenario & Edge Case Coverage

- [x] CHK023 — Are requirements defined for Kafka message key on `txn.fraud.alerts` — is it the `transaction_id`, `account_id`, or unkeyed? What are the partitioning implications? [Coverage, FR-009]
- [x] CHK024 — Are requirements defined for what `EnrichedTransaction.transaction_id` guarantees — is it always non-null, always unique within the stream? [Coverage, FR-001]
- [x] CHK025 — Are requirements defined for `FraudAlert` schema evolution — who approves breaking changes, and is a new major version required? [Coverage, Gap]

---

## Non-Functional Requirements

- [x] CHK026 — Is the `txn.fraud.alerts` topic configuration specified — partition count, replication factor, and retention period? [Gap, Non-Functional]
- [x] CHK027 — Are consumer contract requirements defined for downstream consumers of `txn.fraud.alerts` (what they must tolerate, e.g., duplicate delivery, field nullability)? [Gap, Non-Functional]

---

## Notes

- FR-008 lists 5 fields for `FraudAlert` but does not specify field types, nullability, or ordering — CHK001 and CHK008–CHK010 target this.
- The spec says Schema Registry subject is `fraud-alert-v1` in one place but does not define the full naming convention — CHK002 targets this.
- `rules.yaml` schema is described by example in the rule catalogue but not formally as a schema spec — CHK006 and CHK007 target this gap.
