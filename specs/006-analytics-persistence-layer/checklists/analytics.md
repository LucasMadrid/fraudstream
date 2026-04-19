# Requirements Quality Checklist: Analytics Persistence Layer

**Purpose**: Validate requirements quality across data contracts, write-path completeness, query contract, and compliance before implementation
**Created**: 2026-04-18
**Focus**: All four domains (data contracts / write-path / query contract / compliance)
**Depth**: Formal reviewer gate (pre-staging)
**Scope**: spec.md, plan.md, data-model.md, contracts/

---

## Data Contracts

- [x] CHK001 - Are Avro-to-Iceberg type mappings documented for every field in `enriched_transactions`, including decimal precision and timestamp resolution? [Completeness, data-model.md]
- [x] CHK002 - Are nullable vs. NOT NULL constraints consistent between the Avro schema and Iceberg DDL for all `enriched_transactions` fields? [Consistency, contracts/iceberg-enriched-transactions-v1.sql]
- [x] CHK003 - Is the BACKWARD_TRANSITIVE schema evolution requirement for `prev_geo_country` and `prev_txn_time_ms` specified with explicit `"default": null` constraints, not just as implementation notes? [Completeness, Spec §T013] — resolved: data-model.md Schema Evolution Rules §1 now states this as a hard requirement; FR-007 updated in spec.md
- [x] CHK004 - Is the Iceberg format-version 2 requirement documented with its behavioral implications (row-level delete support vs. v1 copy-on-write)? [Clarity, Gap] — resolved: data-model.md "Iceberg Format-Version 2" section added
- [x] CHK005 - Are partition transform semantics (`days()`) defined for both tables, and is it clear that partition pruning applies to all analyst query patterns? [Clarity, contracts/]
- [x] CHK006 - Is `enrichment_time` (wall-clock at flush) clearly differentiated from `event_time` (Kafka event timestamp) in the requirements — not just in data-model comments? [Clarity, data-model.md]
- [x] CHK007 - Are `rule_triggers ARRAY<STRING>` semantics fully specified: empty list when no rules fire vs. null, and does the Iceberg DDL reflect this (NOT NULL)? [Clarity, contracts/iceberg-fraud-decisions-v1.sql] — resolved: FR-009 updated in spec.md; DDL already has NOT NULL
- [x] CHK008 - Is the `fraud_score` range (0.0–1.0) specified as a validated contract constraint or only an informal documentation note? [Clarity, Ambiguity] — resolved: FR-009 updated to specify range [0.0, 1.0] inclusive
- [x] CHK009 - Is the `DECIMAL(18,4)` precision requirement for `amount` and velocity amount fields justified in requirements with a reference to the upstream source type? [Completeness, Gap] — resolved: data-model.md "DECIMAL(18,4) Precision Justification" section added
- [x] CHK010 - Is the `device_known_fraud` always-null-in-v1 constraint (TD-002) documented as a formal known limitation with a resolution plan? [Completeness, data-model.md] — resolved: data-model.md "Known Technical Debt" section updated with resolution plan

---

## Write-Path Completeness

- [x] CHK011 - Are all three write paths (enriched sink, decisions sink, Feast materialization) specified with independent, measurable latency budgets? [Completeness, spec.md] — resolved: FR-023 added for decisions sink (5s budget); FR-013 covers Feast (5s); FR-004 covers enriched sink
- [x] CHK012 - Is the 5-second enrichment sink latency budget measurable given the batch/flush model (1s or 100 records)? Is worst-case flush delay (just-missed interval + batch fill) within the 5s window? [Measurability, Ambiguity] — resolved: quickstart.md "Flush interval and the 5-second write budget" section added
- [x] CHK013 - Is the buffer overflow behavior defined when `ICEBERG_BUFFER_MAX=1000` is reached while the catalog is unavailable — is this a DLQ event, a backpressure signal, or a drop? [Completeness, Spec §T035] — resolved: edge case added in spec.md (DLQ event with reason "buffer_full", metric `iceberg_buffer_overflow_total`)
- [x] CHK014 - Is the Feast push atomicity requirement ("all views or none") documented as a specification requirement rather than only an implementation task note? [Completeness, Spec §T029]
- [x] CHK015 - Is the deduplication guarantee across all three layers (in-batch set, daily compaction, query-layer ROW_NUMBER) specified with failure-mode behavior between layers (e.g., what happens if compaction runs before ROW_NUMBER is queried)? [Completeness, research.md] — resolved: FR-005 and FR-011 updated with three-layer dedup strategy and ordering independence
- [x] CHK016 - Are DLQ requirements defined for records that exceed the 5-second write budget — specifically, what is retained in the DLQ and what is the consumer contract? [Completeness, Gap] — resolved: edge case in spec.md defines payload + failure reason + 7-year retention for DLQ
- [x] CHK017 - Is `event_timestamp = record["event_time"]` (not wall-clock) for Feast push documented as a non-negotiable requirement with a rationale, not just an implementation detail? [Clarity, Spec §T029] — resolved: FR-024 added to spec.md with rationale (PIT correctness, future leakage prevention)
- [x] CHK018 - Are circuit-breaker thresholds (3 consecutive failures to open, 30s to half-open) specified in the feature requirements or only in the task list? [Coverage, Gap] — resolved: FR-025 added to spec.md
- [x] CHK019 - Is `IcebergDecisionsSink` write behavior specified for all three decision outcomes (ALLOW, FLAG, BLOCK) — are ALLOW decisions explicitly required to be persisted? [Completeness, spec.md §US1]
- [x] CHK020 - Are the Feast push failure metrics (`feast_push_failures_total`) and structured log requirements traceable to a specific observability requirement in the spec or plan? [Traceability, Spec §T030]

---

## Query Contract

- [x] CHK021 - Are all four Trino analyst views referenced in user story acceptance criteria with explicit, measurable success conditions — not only in the tasks list? [Completeness, spec.md §US5] — resolved: US5 acceptance scenarios updated with all 4 view names and measurable conditions
- [x] CHK022 - Is the 60-second query latency SLO specified with a measurement methodology (per-query wall-clock, including dedup window computation, against tables at expected data volume)? [Measurability, spec.md §US5] — resolved: SC-009 added with measurement methodology (per-query wall-clock, 30-day volume, ≤5 concurrent)
- [x] CHK023 - Is the `v_transaction_audit` LEFT JOIN semantics (enriched → decisions) documented as a requirement — i.e., is it specified that enriched records with no decision row are preserved in the view? [Clarity, contracts/trino-analyst-views.sql] — resolved: edge case added in spec.md documenting LEFT JOIN preservation and NULL decision fields
- [x] CHK024 - Is `APPROX_PERCENTILE` usage in `v_fraud_rate_daily` and `v_model_versions` disclosed to analyst consumers as approximate rather than exact? [Clarity, Gap] — resolved: FR-026 added to spec.md; US5 acceptance scenario 5 includes approximation disclosure
- [x] CHK025 - Are the zero-row semantics for `v_rule_triggers` (transactions with empty `rule_triggers` produce no rows in the view) documented as expected behavior? [Coverage, Edge Case] — resolved: edge case added in spec.md
- [x] CHK026 - Are analyst view schema evolution requirements defined — what happens to existing queries when a view column is added or renamed? [Gap] — resolved: quickstart.md "Analyst View Schema Evolution Policy" section added
- [x] CHK027 - Are the query usage patterns (date-range filter, transaction_id lookup, model-version filter) documented with expected result-set size to validate the 60s SLO is realistic? [Measurability, spec.md §US5] — resolved: 50M/30-day volume context added to assumptions in spec.md; SC-009 references this

---

## Compliance / PCI-DSS

- [x] CHK028 - Are all PCI-DSS scope fields in `enriched_transactions` explicitly enumerated with sensitivity tags, masking status, and upstream masking guarantees? [Completeness, data-model.md] — resolved: data-model.md "Data Catalog Reference" table updated with all 4 PCI fields, masking status, and upstream guarantees
- [x] CHK029 - Is the 7-year retention requirement linked to a specific regulatory control (PCI-DSS 10.7) and is it clear whether it applies to data files, metadata snapshots, or both? [Clarity, Spec §intro] — resolved: data-model.md "Retention Scope" section added; both data files and metadata snapshots are explicitly covered
- [x] CHK030 - Are Trino column-level access control requirements for `card_bin` and `card_last4` specified with enough detail to implement (column masking vs. row-level security, which Trino plugin)? [Clarity, quickstart.md] — resolved: quickstart.md specifies column masking (not row-level security), OPA policy or Ranger plugin
- [x] CHK031 - Is the "audit logging on Trino queries against PCI columns" requirement defined with specifics — what is logged (query text, user, columns accessed), where it is retained, and for how long? [Clarity, Gap] — resolved: quickstart.md specifies query text + user identity + columns accessed + timestamp + duration; retained 7 years
- [x] CHK032 - Is the quarterly access review requirement traceable to a specific compliance control, and is the review process (who reviews, what evidence is produced) documented? [Traceability, Gap] — resolved: quickstart.md specifies PCI-DSS 10.7 control, security team reviewer, HR cross-reference, 7-year evidence retention
- [x] CHK033 - Is the local development data policy ("no real card data locally") a formally documented requirement with enforcement guidance, or only a quickstart note? [Coverage, Gap] — resolved: quickstart.md now specifies `FRAUDSTREAM_ENV=local` enforcement; pipeline refuses to start without flag
- [x] CHK034 - Is the MinIO bucket policy requirement specified with the minimum necessary permissions (read-only vs. read-write) and the exact service account identity? [Clarity, quickstart.md] — resolved: quickstart.md specifies read-only for `analytics-svc`; `pipeline-svc` has write; `s3:DeleteObject` denied for non-pipeline identities
- [x] CHK035 - Are the `caller_ip_subnet` masking guarantees (already masked /24 upstream) verified as a contract, not an assumption — i.e., is there a requirement that prevents raw IPs from reaching the Iceberg table? [Completeness, Assumption] — resolved: data-model.md PCI table now defines masking as a formal contract with Schema Registry validation and `masking_lib_version` as enforcement indicator
