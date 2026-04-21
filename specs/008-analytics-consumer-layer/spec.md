# Feature Specification: Analytics Consumer Layer

**Feature Branch**: `008-analytics-consumer-layer`  
**Created**: 2026-04-20  
**Status**: Draft  
**Input**: User description: "This specification defines the analytics consumer layer for FraudStream — the independent, read-only service and Streamlit application that makes pipeline activity observable to fraud analysts and operations engineers through a live Kafka feed and historical Trino queries over the Iceberg persistence layer, as mandated by Principle X of the FraudStream Constitution."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Live Transaction Feed (Priority: P1)

A fraud analyst opens the analytics dashboard and sees a real-time stream of incoming transactions with their fraud decisions overlaid. New events appear automatically within 2 seconds of being published by the pipeline, without requiring a page refresh. The analyst can see each transaction's channel, amount, decision (ALLOW / FLAG / BLOCK), and the rules that triggered for flagged events.

**Why this priority**: Real-time visibility is the primary value proposition of the analytics layer. Without it, fraud operations are flying blind. All other pages build on this live signal.

**Independent Test**: Can be fully tested by publishing a synthetic transaction event to `txn.fraud.alerts` and verifying it appears in the live feed UI within the latency window, delivering immediate operational value as a standalone feature.

**Acceptance Scenarios**:

1. **Given** the analytics service is running and consuming `txn.fraud.alerts`, **When** a new fraud alert is published, **Then** it appears in the live feed within 2 seconds.
2. **Given** the live feed is displayed, **When** the analyst views an event, **Then** they can see transaction ID, account ID (masked), merchant ID, amount, currency, channel, decision, fraud score, and the names of any triggered rules.
3. **Given** no new events have arrived for 30 seconds, **When** an event is published, **Then** it still appears within 2 seconds without a manual refresh.
4. **Given** the analytics service is unavailable, **When** the pipeline continues processing, **Then** fraud scoring SLOs are unaffected and no pipeline errors are produced.

---

### User Story 2 — Fraud Rate by Channel, Merchant, and Geo (Priority: P2)

A fraud analyst or operations engineer selects a rolling time window (last 1 hour, 24 hours, or 7 days) and views fraud rate broken down by channel, merchant, and geographic region. The figures are sourced from the Iceberg persistence layer via historical queries, ensuring they reflect all decisions including those from before the current session.

**Why this priority**: Understanding fraud concentration across dimensions enables rapid triage and threshold tuning. It is the second most time-sensitive operational need after real-time visibility.

**Independent Test**: Can be tested by querying the historical store with a known dataset and verifying the displayed rates match expected fraud counts and totals for each breakdown dimension.

**Acceptance Scenarios**:

1. **Given** historical decisions are present in the event store, **When** the analyst selects a time window, **Then** fraud rate (fraud events / total events) is displayed per channel (POS, WEB, MOBILE, API).
2. **Given** a time window is selected, **When** the page loads, **Then** top merchants by fraud volume are ranked and their fraud rates are shown.
3. **Given** a time window is selected, **When** the page loads, **Then** a geographic breakdown of fraud events is displayed (at country and city level where data is available).
4. **Given** no events exist for a given channel or merchant in the selected window, **When** the page loads, **Then** that dimension is omitted or shown as zero — no errors are raised.

---

### User Story 3 — Rule Trigger Frequency Leaderboard (Priority: P3)

A fraud analyst views which rules are firing most frequently, along with an estimated false-positive rate per rule over a configurable historical window. This helps identify over-firing rules and guides threshold calibration.

**Why this priority**: Rule effectiveness visibility is important for operational health but does not block real-time monitoring. Analysts can function without it initially.

**Independent Test**: Can be tested by replaying a synthetic event set with known rule triggers and verifying the leaderboard ranking and counts match expected values.

**Acceptance Scenarios**:

1. **Given** historical fraud decisions are present, **When** the rule leaderboard page is loaded, **Then** each rule name is shown with its total trigger count and trigger rate (% of all evaluated transactions) for the selected window.
2. **Given** decisions include both FLAG and ALLOW outcomes for the same rule (triggered but later assessed as benign), **When** false-positive rate is calculated, **Then** it is shown alongside trigger count per rule.
3. **Given** a rule has never fired in the selected window, **When** the page loads, **Then** it appears with a count of zero (not hidden).

---

### User Story 4 — Model Version Comparison (Priority: P4)

An ML engineer or data scientist selects two model versions and a time range, and views the fraud score distribution for each. This enables side-by-side comparison of model behavior over production traffic to support safe model rollouts.

**Why this priority**: Model comparison is a periodic need during deployments, not a daily operational task. Lower priority than ongoing monitoring.

**Independent Test**: Can be tested with synthetic scoring output records tagged with two distinct model versions and verifying the distribution charts reflect the correct score histograms per version.

**Acceptance Scenarios**:

1. **Given** scoring output records with at least two distinct model version tags are present, **When** the analyst selects a comparison time range, **Then** score distribution histograms are shown side-by-side per model version.
2. **Given** only one model version exists in the selected window, **When** the page loads, **Then** a single distribution is shown with a notice that only one version is available.
3. **Given** a very large time range is selected, **When** the page loads, **Then** the query completes and renders within 10 seconds.

---

### User Story 5 — Dead Letter Queue Inspector (Priority: P5)

An on-call engineer browses records that failed processing and landed in any dead letter queue (DLQ). Without requiring direct Kafka access, they can view the raw event payload, the error reason, and the pipeline stage where the failure occurred, enabling triage and replay decisions.

**Why this priority**: DLQ inspection is a reactive, incident-driven need. Essential for completeness but rarely used in normal operations.

**Independent Test**: Can be tested by deliberately publishing a malformed event to an ingestion topic, verifying it lands in the corresponding DLQ, and confirming the inspector displays it with the correct error metadata.

**Acceptance Scenarios**:

1. **Given** records exist in any DLQ topic (`txn.api.dlq`, `txn.processing.dlq`, `txn.fraud.alerts.dlq`), **When** the engineer opens the DLQ inspector, **Then** each record is shown with its source topic, error reason, and timestamp.
2. **Given** a DLQ record is selected, **When** the engineer expands it, **Then** the raw event payload is displayed with PII masked consistent with pipeline masking rules.
3. **Given** all DLQ topics are empty, **When** the inspector page loads, **Then** a "No DLQ records" message is shown — no errors are raised.

---

### Edge Cases

- What happens when the Kafka broker is temporarily unreachable? The live feed must degrade gracefully (show a "reconnecting" indicator) without crashing the service or affecting the pipeline.
- What happens when the historical store (Trino/Iceberg) is unavailable? Historical report pages must show a clear error state rather than returning empty results silently.
- What happens when a fraud alert contains a previously unseen rule name? It must be displayed as-is without causing errors.
- How does the system handle very high event volume (>1,000 events/minute) on the live feed? The display must remain responsive — bounded buffer with oldest-first eviction is acceptable.
- What happens when the analytics consumer falls behind on `txn.fraud.alerts`? Consumer lag must be surfaced via a Prometheus metric; it MUST NOT cause offset interference with the scoring or processing consumer groups.
- What happens if a historical query spans a very wide time window? The system must apply a configurable maximum window and prompt the analyst to narrow the range rather than timing out silently.

---

## Requirements *(mandatory)*

### Functional Requirements

**Analytics Service — Process Isolation**

- **FR-001**: The analytics service MUST run as an independent process, completely separate from the scoring engine and stream processor — it MUST NOT be co-located with either.
- **FR-002**: The analytics service MUST use a dedicated Kafka consumer group (`analytics.dashboard`) for the live feed and MUST NOT share consumer group membership with any scoring or processing consumer group. The DLQ Inspector is explicitly exempt: it uses a short-lived, ephemeral consumer group per session (`dlq-inspector-{uuid}`) with no persistent offset and is excluded from the `analytics.dashboard` group.
- **FR-003**: The analytics service's unavailability or crash MUST have zero effect on fraud scoring throughput or decision latency.

**Real-Time Live Feed (P1)**

- **FR-004**: The live feed MUST consume events from `txn.fraud.alerts` with `auto.offset.reset = latest` — it does not replay historical events on startup.
- **FR-005**: New events MUST appear in the live feed within 2 seconds of being published to the topic under normal operating load.
- **FR-006**: Each displayed event MUST include: transaction ID, masked account ID, merchant ID, amount, currency, channel, decision (ALLOW / FLAG / BLOCK), triggered rule names, fraud score, and event timestamp.
- **FR-007**: The live feed MUST maintain a bounded in-memory buffer (maximum 500 most recent events) and evict oldest entries when full.
- **FR-008**: The live feed MUST display a visible indicator when the Kafka connection is interrupted and MUST reconnect automatically without requiring a service restart.

**Historical Reports — Fraud Rate (P2)**

- **FR-009**: The fraud rate page MUST support user-selected rolling windows of at least: 1 hour, 24 hours, and 7 days.
- **FR-010**: Fraud rate MUST be broken down by: (a) channel, (b) top-N merchants by event volume, and (c) geographic region at country and city level.
- **FR-011**: Fraud rate is defined as `(events with decision = FLAG or BLOCK) / (total evaluated events)` for the selected window and dimension.
- **FR-012**: Historical queries MUST use the Iceberg persistence layer — they MUST NOT re-consume raw Kafka topics.

**Historical Reports — Rule Leaderboard (P3)**

- **FR-013**: The rule leaderboard MUST display each rule name, total trigger count, trigger rate (%), and estimated false-positive rate for the selected time window.
- **FR-014**: False-positive rate per rule is estimated as: `events where the rule triggered but final decision was ALLOW / total rule trigger count`.

**Historical Reports — Model Comparison (P4)**

- **FR-015**: The model comparison page MUST render fraud score distribution histograms per model version for a user-selected time range.
- **FR-016**: Comparison queries spanning up to 30 days MUST complete and render within 10 seconds.

**DLQ Inspector (P5)**

- **FR-017**: The DLQ inspector MUST display records from all active DLQ topics: `txn.api.dlq`, `txn.processing.dlq`, `txn.fraud.alerts.dlq`.
- **FR-018**: Each displayed DLQ record MUST show: source topic, error reason or exception message, event timestamp, and masked raw payload.
- **FR-019**: PII in DLQ payloads MUST be masked consistent with the pipeline masking rules — full PANs and full IPs MUST NOT be displayed.

**Privacy & Security**

- **FR-020**: The analytics service MUST NOT cache or persist PII beyond the minimum required to render the current view.
- **FR-021**: The analytics service MUST be strictly read-only relative to the pipeline — it MUST NOT write to any Kafka topic, Iceberg table, or feature store that the scoring engine or stream processor reads.
- **FR-022**: Masked values from Iceberg (card_last4, ip_subnet) MUST be displayed as-is — the analytics layer MUST NOT attempt to reconstruct or further enrich them.

**Observability**

- **FR-023**: The analytics service MUST expose a Prometheus metric for Kafka consumer lag on the `analytics.dashboard` consumer group.
- **FR-024**: Consumer lag exceeding a configurable threshold (default: 500 events) MUST trigger a Prometheus alert specific to the analytics service, independent of pipeline alerts.

### Key Entities

- **FraudAlertDisplay**: A pipeline-produced decision event consumed from `txn.fraud.alerts` and deserialized into the analytics display model. Contains: transaction_id, account_id (masked), merchant_id, amount, currency, channel, decision, rule_triggers, fraud_score, model_version, event_timestamp, received_at.
- **EnrichedTransaction**: A record from the historical event store for enriched transactions. Carries velocity features, geo fields, device fields, and schema_version alongside the base transaction fields.
- **FraudDecision**: A record from the historical event store for scoring output. Contains: transaction_id, decision, fraud_score, rule_triggers, model_version, decision_time, latency_ms, schema_version.
- **DLQRecord**: A dead-letter record from any DLQ topic. Contains: source_topic, error_reason, event_timestamp, masked_payload.
- **RuleTriggerSummary**: A derived aggregate of FraudDecision records. Contains: rule_name, trigger_count, trigger_rate, false_positive_rate, window_start, window_end.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new fraud alert published to the pipeline appears in the live feed within **2 seconds** at p95 under normal operating load.
- **SC-002**: The analytics service failing or restarting has **zero measurable impact** on fraud scoring latency or decision throughput — verified by a load test with the analytics service killed mid-run.
- **SC-003**: Historical fraud rate, rule leaderboard, and model comparison reports for a 24-hour window load and render within **5 seconds** under normal conditions.
- **SC-004**: Historical queries for up to a 30-day window complete within **10 seconds**.
- **SC-005**: The `analytics.dashboard` consumer group offsets do **not interfere** with scoring or processing consumer group offsets under sustained load — verified by offset inspection after a 10-minute load run.
- **SC-006**: DLQ records appear in the inspector within **30 seconds** of being written to a DLQ topic.
- **SC-007**: Automated tests confirm that no full PAN or full IP address is rendered in any analytics view — all PII masking rules are enforced end-to-end.
- **SC-008**: Fraud rate figures from the analytics reports match figures from a direct Iceberg table scan for the same time window with **zero discrepancy** (deterministic query, same data).

---

## Assumptions

- The `txn.fraud.alerts` topic is the real-time decision feed for v1. The `txn.decisions` topic (planned per the Kafka Topic Inventory) is not yet available. The analytics consumer subscribes to `txn.fraud.alerts` as the primary live signal until `txn.decisions` is built.
- The Iceberg persistence layer (`iceberg.enriched_transactions` and `iceberg.fraud_decisions`) is populated by the stream processor and scoring engine (Features 006 and 007) and is queryable via Trino locally and in cloud. This is a hard prerequisite for all historical report pages (P2–P4).
- The analytics service runs in the **Analytics tier** (`make analytics-up`) — it is never part of the Core tier (`make bootstrap`) and is not required for pipeline unit or integration tests.
- The Streamlit application is the primary UI for v1. Grafana remains the authoritative infrastructure / SLO dashboard and is not duplicated here.
- Authentication and access control for the Streamlit UI are **out of scope for v1** — the service is assumed to run on an internal network or behind an existing network-layer access control.
- The analytics consumer does not need exactly-once delivery semantics. At-least-once consumption with idempotent rendering (de-duplicate by `transaction_id` in the display buffer) is acceptable.
- Streamlit v2 (rule engine config UI) is **explicitly out of scope** for this feature, per the Constitution.
- The `analytics/` directory structure described in the Constitution repository layout is the authoritative target for this feature's implementation layout.
- The Trino container and DuckDB in-process engine are already provisioned in the Analytics tier Docker Compose. This feature adds the Streamlit container and Kafka consumer wrapper; it does not re-provision existing infrastructure.
- Consumer lag alerting fires via Prometheus (already running in the Core tier). The analytics service exposes its metrics endpoint at a port separate from the pipeline's `:8002/metrics` endpoint.
