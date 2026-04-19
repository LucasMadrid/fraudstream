# Feature Specification: Analytics Persistence Layer

**Feature Branch**: `006-analytics-persistence-layer`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**: Analytics persistence layer — write paths, table contracts, and query interface mandated by Principle IX of the FraudStream Constitution

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Fraud Analyst Audits a Disputed Transaction (Priority: P1)

A fraud analyst receives a dispute claim for a transaction that was blocked by FraudStream. They need to reconstruct exactly what the system saw: the velocity counts, geolocation data, device fingerprint, and which rules fired — without replaying Kafka or touching the scoring service.

**Why this priority**: Dispute resolution is a regulatory obligation. Without a durable, queryable record of enriched features and the scoring decision, the system cannot defend a block decision in a chargeback process or regulatory audit. This is the core value proposition of the persistence layer.

**Independent Test**: A test transaction is processed end-to-end. The analyst queries the analytics store by `transaction_id` and receives the enriched feature record and the decision record. The query requires no custom code beyond standard SQL.

**Acceptance Scenarios**:

1. **Given** a transaction was processed by the pipeline, **When** the analyst queries the analytics store with that `transaction_id`, **Then** a complete enriched feature record (velocity counts, geo fields, device fingerprint) and a decision record (decision, fraud score, rule triggers, model version, latency) are returned within normal query response time.
2. **Given** a transaction was processed 5 years ago (within the 7-year retention window), **When** the analyst queries the store, **Then** the record is returned unchanged — the data is immutable and partition-accessible.
3. **Given** the pipeline is under load, **When** the analyst submits a query, **Then** the query does not interfere with the scoring hot path and completes independently.

---

### User Story 2 — Data Scientist Generates a Point-in-Time Training Dataset (Priority: P2)

A data scientist wants to retrain the fraud model. They need a training dataset where the features for each historical transaction reflect exactly what the pipeline computed at the time of scoring — no future leakage, no feature drift.

**Why this priority**: Training–serving skew is the most common cause of silent model degradation in streaming ML systems. Without point-in-time correctness, retraining produces models that perform well offline but degrade in production.

**Independent Test**: A data scientist replays a sequence of historical transactions through the offline feature store and confirms that the features returned at each event's timestamp match the values recorded in the enriched transactions table at that moment.

**Acceptance Scenarios**:

1. **Given** a set of historical transactions with known enrichment timestamps, **When** a point-in-time query is issued for those transactions at their respective event timestamps, **Then** the returned features match the values stored in the enriched transactions table at enrichment time — no feature computed after the event timestamp is included.
2. **Given** features were materialized to the online store during enrichment, **When** the same features are retrieved from the offline store for training, **Then** the values are identical to what the online store held at that moment.
3. **Given** the pipeline processes a new transaction, **When** the enrichment cycle completes, **Then** all computed features (velocity, geo, device) are available in the online store within 5 seconds for inference lookups.

---

### User Story 3 — Platform Engineer Verifies Pipeline Completeness Under Load (Priority: P3)

An engineer validating the pipeline before a production release needs to confirm that every transaction processed by the scoring engine has a corresponding persisted record — no silent drops between Kafka consumption and Iceberg write.

**Why this priority**: Silent data loss between the pipeline and the persistence layer is undetectable without an explicit reconciliation check. The pre-production checklist mandates row count accuracy within 0.1% of Kafka topic offset.

**Independent Test**: After processing a known batch of transactions, the engineer queries the analytics store tables and compares the row count against the Kafka topic offset. Any gap exceeding 0.1% is surfaced as a test failure.

**Acceptance Scenarios**:

1. **Given** N transactions were published and fully processed, **When** the engineer queries both analytics tables, **Then** each table contains exactly N rows (within 0.1% tolerance for AT_LEAST_ONCE delivery duplicates absorbed by deduplication).
2. **Given** the analytics sink encounters a transient write failure, **When** the pipeline retries, **Then** the retry does not create a duplicate row — deduplication on `transaction_id` ensures exactly one record per transaction.
3. **Given** the enrichment sink and scoring sink write independently, **When** the engineer joins both tables on `transaction_id`, **Then** all matched rows are present with no custom join logic required beyond standard SQL.

---

### User Story 4 — Operations Team Verifies Analytics Service Isolation (Priority: P4)

An operations engineer deliberately takes down the analytics query service to verify that the scoring hot path is unaffected. SLOs must hold.

**Why this priority**: The constitution mandates that analytics service unavailability must not affect fraud scoring SLOs. This must be verified, not assumed.

**Independent Test**: The analytics services are stopped. The pipeline continues processing transactions. Latency p99 and throughput remain within SLO bounds throughout.

**Acceptance Scenarios**:

1. **Given** the analytics query service is unavailable, **When** the pipeline processes transactions, **Then** fraud scoring latency p99 remains under 100ms and throughput is unchanged.
2. **Given** the analytics service is restarted after a period of unavailability, **When** it reconnects to the store, **Then** all records written during the outage are present and queryable — no data was lost.

---

### User Story 5 — Data Analyst Explores Fraud Patterns via SQL (Priority: P3)

A data analyst wants to explore fraud patterns — which merchant categories have the highest block rates, which velocity thresholds correlate with confirmed fraud, how fraud rates trend over time — using a standard SQL client without pipeline access, custom ETL, or engineering involvement.

**Why this priority**: The persistence layer's business value extends beyond compliance and model retraining. Analysts need self-service access to both enriched features and decisions to answer operational business questions directly from the store. Without this, every fraud pattern investigation requires an engineering handoff.

**Independent Test**: The analyst opens a SQL client, writes a query joining `enriched_transactions` and `fraud_decisions` on `transaction_id`, applies a date-range filter and a decision-outcome filter, and receives aggregated results. No custom code, no pipeline access, and no pre-computed intermediate tables are required.

**Acceptance Scenarios**:

1. **Given** a date range and a set of decision outcomes, **When** the analyst queries `v_fraud_rate_daily` with a time-range filter on the day partition and a decision filter, **Then** aggregated results are returned within 60 seconds under normal interactive query load (≤5 concurrent analysts, tables populated to 30-day expected volume) using standard SQL only.
2. **Given** the analyst wants to correlate velocity features with fraud outcomes, **When** they query `v_transaction_audit` and group by rule name or velocity bucket using `v_rule_triggers`, **Then** the join and aggregation complete without UDFs, pre-computed tables, or custom code.
3. **Given** multiple analysts query concurrently, **When** queries are submitted, **Then** the query layer's resource isolation ensures no single query causes another to exceed the 60-second interactive budget.
4. **Given** a fraud analyst needs to audit a disputed transaction, **When** they query `v_transaction_audit` by `transaction_id`, **Then** both enriched features and decision fields are returned in a single row within 10 seconds.
5. **Given** an engineer wants to monitor model rollout, **When** they query `v_model_versions` with a model version filter, **Then** decision distribution, score statistics, and latency percentiles (approximate) are returned for that version within 60 seconds. Note: percentile values in `v_fraud_rate_daily`, `v_rule_triggers`, and `v_model_versions` are approximate (computed via `APPROX_PERCENTILE`) — consumers requiring exact percentiles must query the base tables directly.

---

### Edge Cases

- What happens when an enriched record arrives for a `transaction_id` already in the enriched transactions table? Deduplication on `transaction_id` must prevent a duplicate row; the existing record is preserved unchanged.
- What happens when the analytics sink write exceeds the 5-second budget? The enrichment job must surface this as a metric (`iceberg_write_budget_exceeded_total`) and emit a DLQ event containing the full record payload and the failure reason. The DLQ consumer contract: DLQ events MUST retain the complete original record (all fields), the failure timestamp, the failure reason (exception class and message), and the sink that produced the event. DLQ storage MUST satisfy the same 7-year retention requirement as the primary tables. The scoring hot path must not be blocked.
- What happens when the Iceberg buffer reaches `ICEBERG_BUFFER_MAX=1000` while the catalog is unavailable? This is a circuit-breaker overflow: the sink MUST emit a `iceberg_buffer_overflow_total` metric, write the overflow record to the DLQ with reason "buffer_full", and NOT block the Flink operator. Overflow records are eligible for DLQ replay once the catalog recovers.
- What happens when feature materialization fails for a subset of features within one enrichment cycle? Partial materialization is prohibited — the cycle either fully materializes all features or fails atomically and is retried; the failure is logged and metriced.
- What happens when the decisions table receives a record before the corresponding enriched transactions record is committed? Queries must tolerate eventual consistency — both tables are written asynchronously; the query layer must handle the propagation window without error. The `v_transaction_audit` view uses a LEFT JOIN from enriched to decisions, which means enriched records with no corresponding decision row are preserved in the view output with NULL decision fields — this is expected behavior, not an error.
- What happens when a transaction has an empty `rule_triggers` list? The `rule_triggers` field MUST be an empty list (never null). The `v_rule_triggers` analyst view filters on `CARDINALITY(rule_triggers) > 0`, which means transactions with no rules fired produce zero rows in that view — this is expected behavior. Analysts counting all transactions must query `v_transaction_audit` or the base tables.
- What happens when the query layer is unavailable? The scoring pipeline must be unaffected; analysts receive a service-unavailable error from the query layer only, not from the pipeline.

---

## Requirements *(mandatory)*

### Functional Requirements

**Enriched Transactions Write Path**

- **FR-001**: The stream processor MUST write every enriched transaction record to the enriched transactions table as a mandatory side output — this write is not optional and not conditional on any downstream state.
- **FR-002**: Each enriched record MUST include all computed features: velocity counts (1m, 5m, 1h, 24h), velocity amounts (1m, 5m, 1h, 24h), geolocation fields (country, city, network class, confidence), and device fingerprint fields (first seen, transaction count, known fraud flag, previous geo country, previous transaction time).
- **FR-003**: Each enriched record MUST carry both event time (used for table partitioning by day) and processing time (when the sink write occurred).
- **FR-004**: The sink MUST complete each write within 5 seconds of Kafka consumer receipt — this budget is outside the 100ms scoring hot path and MUST NOT add latency to the decision response.
- **FR-005**: `transaction_id` MUST be the deduplication key — if a record with the same `transaction_id` is written more than once (AT_LEAST_ONCE delivery), only one row must appear in the table. Deduplication is enforced at three layers: (1) in-batch dedup via a `transaction_id` set before each flush, (2) daily Trino compaction to remove cross-batch duplicates, and (3) query-layer `ROW_NUMBER()` in analyst views to absorb duplicates not yet removed by compaction. If compaction runs after the ROW_NUMBER query is evaluated, the query-layer dedup ensures correctness — no ordering dependency exists between layers.
- **FR-006**: The table MUST be partitioned by event day to enable efficient time-range queries without full-table scans.
- **FR-007**: A schema version field MUST be included in every record; schema evolution follows the same backward-transitive discipline applied to Kafka topics. New optional fields introduced via schema evolution MUST carry an Avro `"default": null` value to maintain BACKWARD_TRANSITIVE compatibility with existing consumers.

**Fraud Decisions Write Path**

- **FR-008**: The scoring engine MUST write every decision record to the fraud decisions table — every decision (ALLOW, FLAG, BLOCK) must be persisted, not only flagged or blocked ones.
- **FR-009**: Each decision record MUST include: transaction identifier, decision outcome, fraud probability score (in the range [0.0, 1.0] inclusive), list of matched rule names (an empty list when no rules fire — the field is never null), model version, decision timestamp (used for table partitioning by day), end-to-end latency in milliseconds, and schema version.
- **FR-010**: The two tables MUST be joinable via standard SQL on `transaction_id` without custom code, UDFs, or pre-computed intermediate tables.
- **FR-011**: `transaction_id` MUST be the deduplication key in the decisions table — duplicate writes on pipeline recovery must produce at most one row. The same three-layer deduplication strategy applies as in FR-005 (in-batch set, daily compaction, query-layer ROW_NUMBER).

**Feature Materialization Write Path**

- **FR-012**: Computed features MUST be materialized to the online feature store on every enrichment cycle — not batched, not deferred.
- **FR-013**: Materialized features MUST be available in the online store within 5 seconds of the enrichment event being processed, to support sub-millisecond inference lookups.
- **FR-014**: The same features pushed to the online store MUST be persisted to the offline store in a point-in-time correct manner — the offline snapshot for any event timestamp must match the values the online store held at that moment, with no future leakage.
- **FR-015**: Feature materialization failure MUST be surfaced as a metric increment and a structured log entry — the enrichment job must not silently swallow materialization errors.
- **FR-016**: Partial materialization within a single enrichment cycle is prohibited — each cycle either fully materializes all features or fails atomically and is retried.

**Query Contract**

- **FR-017**: A standard SQL query engine MUST be able to join the enriched transactions table and the fraud decisions table on `transaction_id` without custom code, UDFs, or pre-computed intermediate tables.
- **FR-018**: The query layer MUST support time-range queries over both tables using the day-level partitions.
- **FR-019**: The query layer MUST return all records for a given `transaction_id` within 10 seconds under normal interactive query load.
- **FR-020**: The analytics query layer MUST be isolated from the scoring hot path — queries MUST use separate compute resources and MUST NOT share execution context with the enrichment or scoring jobs.

**Retention and Compliance**

- **FR-021**: Both tables MUST retain data for a minimum of 7 years, satisfying PCI-DSS 10.7 and applicable regulatory minimums.
- **FR-022**: Both tables are append-only — mutation and deletion of existing records are prohibited; mutable review state lives in the operational review store, not in these analytics tables.
- **FR-023**: The fraud decisions sink MUST complete each write within 5 seconds of the scoring engine producing a `FraudDecision` record — the same budget constraint as the enriched transactions sink (FR-004). This budget is outside the 100ms scoring hot path and MUST NOT add latency to the decision response.
- **FR-024**: Feast feature materialization MUST use the record's `event_time` field (the Kafka event timestamp) as the `event_timestamp` for all push operations — NOT the wall-clock time at flush. This is a non-negotiable requirement for point-in-time correctness: using wall-clock time would produce future leakage in offline training snapshots and violate FR-014.
- **FR-025**: Both Iceberg sinks MUST implement a circuit-breaker pattern (using `pybreaker` or equivalent) around catalog operations — the breaker MUST open after 3 consecutive catalog failures, transition to half-open after 30 seconds, and log all state transitions. While the breaker is open, the sink MUST buffer records up to `ICEBERG_BUFFER_MAX` and MUST NOT propagate exceptions to the Flink scoring hot path.
- **FR-026**: Analyst views that use `APPROX_PERCENTILE` (`v_fraud_rate_daily`, `v_rule_triggers`, `v_model_versions`) MUST be documented as returning approximate values — not exact percentiles. This approximation is acceptable for operational dashboards and rule tuning, but consumers requiring exact percentiles must query the underlying tables directly.

### Key Entities

- **EnrichedTransaction**: A processed transaction record augmented with all computed features — velocity aggregations across four time windows, geolocation classification, and device fingerprint state. Identified by `transaction_id`; partitioned by event day.
- **FraudDecision**: The system's decision record for a transaction — the final outcome, the fraud probability score, the list of matched rule names, the model version, and the end-to-end decision latency. Identified by `transaction_id`; joins to EnrichedTransaction.
- **FeatureSet**: The collection of velocity, geo, and device features materialized to the feature store — the bridge between online inference (sub-millisecond lookup) and offline training (point-in-time snapshots). Feature values are immutable after the enrichment cycle that produced them.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every enriched transaction processed by the pipeline is queryable in the analytics store within 5 seconds of processing — confirmed by a reconciliation test comparing consumed offset to persisted row count; gap must not exceed 0.1%.
- **SC-002**: Every fraud decision (ALLOW, FLAG, and BLOCK) has a corresponding persisted record — 100% coverage verified by the same reconciliation test.
- **SC-003**: A fraud analyst can retrieve the complete enriched feature record and decision record for any transaction using a single SQL JOIN, with no custom code, within 10 seconds under normal query load.
- **SC-004**: Point-in-time feature snapshots for a replayed event sequence match the values observed at enrichment time — zero leakage detected in the validation test defined in the pre-production checklist.
- **SC-005**: Stopping the analytics query service has zero measurable effect on fraud scoring latency p99 or throughput — verified by a load test run while the analytics tier is offline.
- **SC-006**: Computed features for any newly enriched transaction are available in the online feature store within 5 seconds of the enrichment event, enabling low-latency inference lookups without stale data.
- **SC-007**: Both analytics tables remain queryable for records up to 7 years old — retention policy is enforced and verified before production promotion.
- **SC-008**: Analytics sink writes complete within the 5-second budget at 2× expected peak transaction volume — verified by the load test suite.
- **SC-009**: Interactive analyst queries against `v_fraud_rate_daily`, `v_rule_triggers`, and `v_model_versions` complete within 60 seconds — measured as per-query wall-clock time including dedup window computation, against tables populated with 30 days of expected transaction volume, with up to 5 concurrent analyst queries. The 10-second SLO for `v_transaction_audit` single-transaction lookups (SC-003) applies independently.

---

## Assumptions

- The enrichment job and scoring engine are already implemented (Features 002 and 003); this feature adds Iceberg sinks and Feast materialization as mandatory side outputs to existing operators, not as new standalone services.
- The local object storage service is already provisioned in the Analytics tier of the local stack — this feature adds the Iceberg catalog and table definitions on top of the existing instance.
- The SQL query engine is already provisioned in the Analytics tier — this feature adds the Iceberg connector configuration and the project catalog pointing to local object storage.
- The feature store repository structure exists — this feature adds feature view definitions for velocity, geo, and device features and the materialization pipeline.
- The enriched transactions and fraud decisions table schemas are defined in the FraudStream Constitution (Data Contracts section) and are used as-is without modification by this feature.
- AT_LEAST_ONCE delivery semantics apply to both Iceberg sinks; deduplication on `transaction_id` at the table level absorbs duplicates introduced by pipeline recovery.
- The 5-second write budget is measured from Kafka consumer receipt to table commit confirmation — storage and network latency are included in this budget.
- Point-in-time correctness is the responsibility of the offline feature store — the pipeline is responsible only for materializing feature values with accurate event timestamps at enrichment time.
- Both Iceberg sinks are implemented as pipeline side outputs (not separate microservices), sharing the enrichment job's checkpoint cycle — recovery is coordinated through the checkpoint mechanism.
- Feature store online backend in local development uses a lightweight embedded store; in production it is replaced by a managed low-latency key-value store. Feature view definitions are backend-agnostic.
- Expected table volume for query SLO validation: approximately 50 million rows across 30 days in `enriched_transactions` and `fraud_decisions` at normal peak; 2× peak is 100 million rows. The 60-second interactive query SLO (SC-009) and 10-second lookup SLO (SC-003) are specified against this volume with ≤5 concurrent analyst queries.
