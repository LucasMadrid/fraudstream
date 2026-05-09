# Feature Specification: Streamlit DuckDB Migration

**Feature Branch**: `009-streamlit-duckdb-migration`  
**Created**: 2026-04-21  
**Status**: Draft  

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fraud Rate Page Loads Reliably (Priority: P1)

A fraud analyst opens the Fraud Rate historical report page to review fraud trends over a configurable rolling window. The page loads the chart without any errors, regardless of whether the external query container is running or reachable.

**Why this priority**: This is the most-used historical report. It previously failed silently with a DNS error whenever the external query container was unavailable, blocking analysts from seeing fraud rate trends during incidents — the exact moment the page is most needed.

**Independent Test**: Navigate to the Fraud Rate page with no external query container running and confirm the chart loads with data from the Iceberg tables.

**Acceptance Scenarios**:

1. **Given** the Streamlit app is running and Iceberg tables contain enriched transaction data, **When** an analyst opens the Fraud Rate page, **Then** fraud rate charts render within 5 seconds for a 24-hour rolling window.
2. **Given** the external query container (Trino) is stopped or unreachable, **When** an analyst opens the Fraud Rate page, **Then** the page still loads data from the Iceberg tables successfully.
3. **Given** no transactions exist in the selected time window, **When** an analyst opens the Fraud Rate page, **Then** the page shows an empty-state message rather than an error.

---

### User Story 2 - Rule Triggers Page Loads Reliably (Priority: P2)

A fraud operations engineer opens the Rule Triggers leaderboard to see which fraud rules are firing most frequently and which have high false-positive rates. The page loads without errors.

**Why this priority**: Rule trigger analysis is used during rule tuning and incident review. Previously failed with the same DNS error pattern as the Fraud Rate page.

**Independent Test**: Navigate to the Rule Triggers page with no external query container running; confirm the leaderboard loads.

**Acceptance Scenarios**:

1. **Given** enriched transaction data with rule trigger fields is present in Iceberg, **When** an analyst opens the Rule Triggers page, **Then** the rule leaderboard renders within 5 seconds.
2. **Given** the external query container is unavailable, **When** the page is accessed, **Then** data loads successfully from Iceberg without any error message.

---

### User Story 3 - Model Compare Page Loads Reliably (Priority: P3)

A data scientist opens the Model Compare page to compare fraud score distributions across model versions. The page loads without errors.

**Why this priority**: Model comparison is used less frequently (after model deployments), but must work without infrastructure dependencies that can fail.

**Independent Test**: Navigate to the Model Compare page with no external query container running; confirm the version comparison chart loads.

**Acceptance Scenarios**:

1. **Given** fraud decision data with model version fields is present in Iceberg, **When** a data scientist opens the Model Compare page, **Then** the score distribution chart renders within 5 seconds.
2. **Given** only one model version exists in the data, **When** the page loads, **Then** it renders a single-version view without error.

---

### Edge Cases

- What happens when the Iceberg catalog is unreachable (MinIO down)? The page shows a clear error message — this is an expected failure mode with actionable messaging.
- What happens when a user tries to select a rolling window longer than 30 days? The UI caps the selectable window at 30 days and displays a note directing analysts to the ad-hoc SQL tool for longer ranges.
- What happens when the Iceberg tables exist but contain no rows? Each page shows an empty-state message rather than an unhandled exception.
- What happens when Streamlit's process memory is constrained during a large scan? Time-window filtering limits data loaded; if the result still exceeds available memory, the page surfaces an actionable error rather than crashing silently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All three historical report pages (Fraud Rate, Rule Triggers, Model Compare) MUST load successfully when the external query container is stopped or unreachable.
- **FR-002**: Each historical page MUST return query results within 5 seconds for rolling windows up to 24 hours under normal data volumes.
- **FR-003**: The maximum selectable rolling window on each historical page MUST be capped at 30 days; selecting a longer range MUST produce a clear explanatory message.
- **FR-004**: Each page MUST show a non-error empty-state message when no data exists for the selected time window.
- **FR-005**: Queries MUST apply time-window filtering before full table materialisation to limit memory usage and improve performance.
- **FR-006**: Fraud rate figures returned by each historical page MUST match figures from a direct Iceberg table scan for the same time window (zero discrepancy).
- **FR-007**: The Streamlit app MUST produce identical output whether or not the external query container is running; its presence or absence MUST NOT affect page results.
- **FR-008**: The live feed page and DLQ inspector page MUST remain completely unaffected by this migration.

### Key Entities

- **Enriched Transaction**: A processed transaction record stored in the enriched transactions Iceberg table, including amount, account, channel, geo, velocity features, and rule trigger flags.
- **Fraud Decision**: A fraud scoring outcome stored in the fraud decisions Iceberg table, including fraud score, model version, decision label, and rule trigger list.
- **Rolling Window**: A user-specified time range (1 hour to 30 days) used to filter Iceberg data for each historical report page.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All three historical report pages load without error when the external query container is not running — verified in a test environment with the container stopped.
- **SC-002**: Each page renders results within 5 seconds for a 24-hour rolling window on data volumes representative of 24 hours of production-equivalent traffic.
- **SC-003**: Zero discrepancy between fraud rate figures shown in the Fraud Rate page and figures from a direct Iceberg table scan over the same time window.
- **SC-004**: No regression in existing pages (live feed, DLQ inspector, home) — all pass their existing test suite unchanged.
- **SC-005**: Memory consumption during a 7-day rolling window query stays within the Streamlit container's configured memory limit with no out-of-memory errors.

## Assumptions

- The Iceberg tables (`enriched_transactions`, `fraud_decisions`) are written by the existing enrichment pipeline and accessible from the Streamlit process via the same object storage endpoint already configured.
- The Streamlit container already has network access to object storage — no new network configuration is required.
- A 30-day rolling window cap is acceptable for all Streamlit use cases; longer queries belong in the external query tool used by the data team.
- The existing page UI (charts, filters, expanders, layout) does not change — only the data retrieval layer is replaced.
- The external query tool (Trino) remains in the stack for data team ad-hoc exploration and is not removed from the infrastructure.
- Feast feature store, live Kafka consumer, and DLQ inspector are unaffected — they do not use the query engine being replaced.
