# Tasks: Analytics Consumer Layer (008)

**Input**: Design documents from `/specs/008-analytics-consumer-layer/`  
**Branch**: `008-analytics-consumer-layer`  
**Prerequisites**: plan.md ✅, spec.md ✅, data-model.md ✅, research.md ✅, quickstart.md ✅

**Organization**: Tasks are grouped by user story (5 stories, P1–P5) to enable independent implementation and testing of each increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths included in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, Docker service, Makefile targets, and observability wiring — no app logic yet.

- [X] T001 Add `streamlit>=1.35`, `duckdb>=0.10`, `pandas>=2.0` to `[analytics]` extras in `pyproject.toml`
- [X] T002 Create `infra/analytics/Dockerfile` — Python 3.11 slim, `pip install -e ".[analytics]"`, `streamlit run analytics/app/Home.py` entrypoint
- [X] T003 Add `streamlit` service to `infra/docker-compose.yml` (port 8501, depends_on: kafka, trino; bind-mount analytics/ as volume)
- [X] T004 [P] Add `analytics-up` and `analytics-down` targets to `Makefile` (bring up Trino + Streamlit, leave Core tier untouched)
- [X] T005 [P] Add `:8004` scrape target to `infra/prometheus/prometheus.yml` with job label `analytics`

**Checkpoint**: `make analytics-up` starts without errors; `http://localhost:8501` returns HTTP 200; `:8004` target appears in Prometheus targets page.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Kafka consumer daemon, metrics server, and Streamlit entry point — shared by every user story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 Create `analytics/consumers/__init__.py` (empty)
- [X] T007 Create `analytics/queries/__init__.py` (empty)
- [X] T008 Implement `analytics/consumers/metrics.py` — define `analytics_consumer_lag` Gauge (labels: consumer_group, topic), `analytics_events_consumed_total` Counter (label: topic), `analytics_consumer_restarts_total` Counter; `start_metrics_server(port=8004)` calls `prometheus_client.start_http_server`
- [X] T009 Implement `analytics/consumers/kafka_consumer.py` — `AnalyticsKafkaConsumer` class: daemon `threading.Thread`, consumer group `analytics.dashboard`, `auto.offset.reset=latest`, subscribes to `txn.fraud.alerts`; drains Avro-deserialized `FraudAlertDisplay` dicts to `queue.Queue(maxsize=500)`; reconnects on KafkaException and increments `analytics_consumer_restarts_total`; exposes `is_alive()` and `consumer_lag` property
- [X] T010 Implement `analytics/app/Home.py` — starts consumer thread once via `st.session_state` guard; starts metrics server once via module-level flag; displays health panel: consumer thread alive status, last event timestamp, current consumer lag from metrics
- [X] T011 Write unit tests in `tests/unit/test_analytics_consumer.py`:
  - Buffer eviction: when `queue.Queue` is full, oldest entry is dropped and new entry is accepted
  - `FraudAlertDisplay` deserialization from a synthetic Avro record matches expected field values
  - `analytics_consumer_restarts_total` increments once per simulated `KafkaException` in consumer loop
  - **PII render assertion**: assert deserialized `FraudAlertDisplay.account_id` matches `card_last4` masked pattern (no raw PAN); assert no field in the struct contains a full IPv4 address

**Checkpoint**: `analytics_consumer_lag` and `analytics_consumer_restarts_total` metrics appear at `http://localhost:8004/metrics`; Home page shows consumer health panel; unit tests pass.

---

## Phase 3: User Story 1 — Live Transaction Feed (Priority: P1) 🎯 MVP

**Goal**: Real-time fraud alert feed in the Streamlit UI, consuming `txn.fraud.alerts` with ≤ 2 s p95 latency, bounded 500-event buffer, deduplication, and reconnection indicator.

**Independent Test**: Publish a synthetic event to `txn.fraud.alerts`; verify it appears in `1_live_feed.py` within 2 seconds. Kill Kafka; verify "Reconnecting…" banner appears. Restore Kafka; verify feed resumes.

- [X] T012 [US1] Implement `analytics/app/pages/1_live_feed.py`:
  - Read from `st.session_state["feed_queue"]` (populated by consumer thread in Home.py)
  - Maintain `deque(maxlen=500)` in `st.session_state["feed_buffer"]`; skip events whose `transaction_id` is already in the buffer
  - `st.empty()` loop with `time.sleep(0.5)` polling; call `st.rerun()` when new events are drained
  - Render events as `st.dataframe` with columns: event_timestamp, received_at, transaction_id, account_id, merchant_id, amount, currency, channel, decision, fraud_score, rule_triggers
  - Show `st.warning("Reconnecting…")` banner when `consumer_healthy` is `False`

**Checkpoint**: Live feed renders new events within 2 seconds. Buffer eviction occurs at 500 entries. Disconnection banner visible when consumer thread is not alive.

---

## Phase 4: User Story 2 — Fraud Rate Reports (Priority: P2)

**Goal**: Historical fraud rate broken down by channel, merchant, and geo over user-selected rolling windows (1 h, 24 h, 7 d), querying Trino over Iceberg — zero raw Kafka re-consumption.

**Independent Test**: Load the page against a seeded Iceberg test dataset; verify channel fraud rates, top-merchant ranking, and geo breakdown match known expected values for the 24 h window.

- [X] T013 [P] [US2] Implement `analytics/queries/fraud_rate.py`:
  - `get_fraud_rate(window_days: int, trino_conn) -> pd.DataFrame` — queries `iceberg.default.v_fraud_rate_daily` filtered to last N days; returns channel + decision breakdown with transaction_count, avg_fraud_score
  - `get_merchant_fraud_rate(window_days: int, top_n: int, trino_conn) -> pd.DataFrame` — ranks merchants by fraud event volume for the window
  - `get_geo_fraud_rate(window_days: int, trino_conn) -> pd.DataFrame` — geo breakdown from view
- [X] T014 [US2] Implement `analytics/app/pages/2_fraud_rate.py`:
  - `st.selectbox` time window selector: 1 h, 24 h, 7 d (map to `window_days` float for query)
  - Three `st.tabs`: "By Channel", "By Merchant", "By Geography"
  - Cache Trino results with `@st.cache_data(ttl=60)` to avoid re-querying on every rerun
  - Show `st.error("Trino unavailable — historical queries cannot be served.")` when connection fails
  - Show empty-state message per tab when no rows returned for the selected window

**Checkpoint**: All three tabs load with data from the seeded dataset. Trino error state renders correctly when Trino is stopped. Query latency ≤ 5 s for 24 h window.

---

## Phase 5: User Story 3 — Rule Trigger Leaderboard (Priority: P3)

**Goal**: Historical rule leaderboard showing trigger count, trigger rate, and false-positive rate per rule over configurable window — replaces the placeholder `3_rule_triggers.py` slot from the prior feature.

**Independent Test**: Load leaderboard with a synthetic dataset containing known rule trigger counts and ALLOW/FLAG/BLOCK breakdowns; verify FP rate computation matches `ALLOW_count / total_count` per rule.

- [X] T015 Rename `analytics/app/pages/3_rule_triggers.py` → `analytics/app/pages/6_shadow_rules.py` (preserves Shadow Rule Monitor operational tooling from prior feature)
- [X] T016 [P] [US3] Implement `analytics/queries/rule_triggers.py`:
  - `get_rule_leaderboard(window_days: int, trino_conn) -> pd.DataFrame` — queries `iceberg.default.v_rule_triggers` for the window; pivots ALLOW / FLAG+BLOCK trigger_count to compute `fp_rate = allow_count / total_count` per rule; returns rule_name, total_trigger_count, trigger_rate_pct, fp_rate
- [X] T017 [US3] Implement `analytics/app/pages/3_rule_triggers.py` (new file — historical leaderboard):
  - `st.selectbox` for time window (1 h, 24 h, 7 d, 30 d)
  - `st.dataframe` leaderboard table with columns: rule_name, trigger_count, trigger_rate (%), fp_rate (%)
  - `st.bar_chart` of trigger_count per rule
  - Rules with zero triggers in the window shown explicitly as 0 count (not omitted)
  - Trino error state identical to fraud rate page pattern

**Checkpoint**: Leaderboard page renders with correct FP rates. Renamed `6_shadow_rules.py` still accessible via Streamlit sidebar and fully functional.

---

## Phase 6: User Story 4 — Model Version Comparison (Priority: P4)

**Goal**: Side-by-side fraud score distribution histograms per model version for a user-selected time range, with query completion within 10 s for 30-day windows.

**Independent Test**: Load page with synthetic records tagged with two model version labels; verify two histograms rendered with correct bucket distributions. Load with single version; verify single histogram + notice message.

- [X] T018 [P] [US4] Implement `analytics/queries/model_versions.py`:
  - `get_model_score_distribution(window_days: int, trino_conn) -> pd.DataFrame` — queries `iceberg.default.v_model_versions` with date filter; returns model_version, decision, avg_fraud_score, median_fraud_score, p95_fraud_score, transaction_count, avg_latency_ms
- [X] T019 [US4] Implement `analytics/app/pages/4_model_compare.py`:
  - `st.selectbox` for time range (1 d, 7 d, 14 d, 30 d)
  - Derive distinct model versions from query result; render `st.columns(2)` side-by-side when ≥ 2 versions exist
  - Render score distribution as `st.bar_chart` per version (histogram buckets from percentile columns)
  - `st.info("Only one model version found in selected window.")` when < 2 versions
  - Trino error state consistent with other historical pages

**Checkpoint**: Two-version comparison renders correctly. Single-version notice displays. 30-day query completes within 10 s (validated against seeded data).

---

## Phase 7: User Story 5 — DLQ Inspector (Priority: P5)

**Goal**: On-demand DLQ browser consuming up to 200 records per DLQ topic, with PII-masked payload display — no persistent consumer group offset.

**Independent Test**: Publish a malformed event to `txn.api.dlq`; open DLQ inspector; verify it appears with correct source_topic, error_reason, timestamp, and masked payload. Verify all DLQ topics empty → "No DLQ records" message.

- [X] T020 [US5] Implement `analytics/app/pages/5_dlq_inspector.py`:
  - On page load (or explicit "Refresh" button), create a short-lived `confluent_kafka.Consumer` with no persistent group offset (use a unique ephemeral group ID per load, e.g., `dlq-inspector-{uuid4()}`)
  - Poll `txn.api.dlq`, `txn.processing.dlq`, `txn.fraud.alerts.dlq` with `auto.offset.reset=earliest`, consuming up to 200 records per topic (hard cap)
  - Import `pii_masker` from `pipelines/ingestion/shared/pii_masker/` and apply to each payload before rendering
  - Render `st.dataframe` with columns: source_topic, error_reason, timestamp, payload_preview (first 120 chars)
  - `st.expander` per row for full masked payload JSON
  - Show `st.info("No DLQ records found across all monitored topics.")` when all topics yield 0 records

**Checkpoint**: DLQ records appear within 30 s of being written. PII masking verified (no full PAN or IP in display). Empty-state message renders correctly.

---

## Phase 8: Observability & Integration Tests

**Purpose**: Prometheus alert rule, consumer group isolation test, lag metric end-to-end verification.

- [X] T021 [P] Create `infra/prometheus/alerts/analytics_consumer.yml`:
  - Alert `AnalyticsConsumerLagHigh`: expression `analytics_consumer_lag{topic="txn.fraud.alerts"} > 500`, for `60s`, severity `warning`, annotations describing the 2 s live feed SLO impact
  - Validate with `promtool check rules infra/prometheus/alerts/analytics_consumer.yml`
- [X] T022 Write integration tests in `tests/integration/test_analytics_integration.py` (uses testcontainers Kafka):
  - **Isolation test**: produce 50 events with analytics.dashboard group consuming; assert `flink-scoring-job` group offsets are unchanged after consumption completes
  - **Lag metric test**: assert `analytics_consumer_lag` Gauge reads 0 after all 50 events are consumed
  - **Independence test**: run `docker stop` on the analytics container mid-load (or stop the consumer thread in-process); poll the scoring job's Kafka consumer group describe endpoint for 10 s and assert it continues without error and offset lag does not stall

**Checkpoint**: `promtool check rules` exits 0. Integration tests pass. Lag metric reads 0 after full consumption.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final wiring, README updates, and CLAUDE.md technology registration.

- [X] T023 [P] Run `.specify/scripts/bash/update-agent-context.sh claude` to add new analytics stack (Streamlit ≥ 1.35, duckdb ≥ 0.10, pandas ≥ 2.0, confluent-kafka analytics pattern) to `CLAUDE.md`
- [X] T024 [P] Update top-level `README.md` analytics tier section: add `make analytics-up` command, page table (Home / 1_live_feed / 2_fraud_rate / 3_rule_triggers / 4_model_compare / 5_dlq_inspector / 6_shadow_rules), and consumer group isolation note
- [X] T025 Validate end-to-end using `quickstart.md`: run `make analytics-up`, generate test traffic with `python scripts/generate_transactions.py --count 100 --inject-suspicious 10`, verify all 6 pages load and live feed shows events within 2 s
- [X] T026 [P] Ensure `ruff check analytics/` and `ruff format analytics/` pass (add `analytics/` to CI ruff scope in `pyproject.toml` if not already present)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 completion — BLOCKS all user story phases
- **Phase 3–7 (User Stories)**: All depend on Phase 2 completion; stories can proceed in priority order (P1 → P2 → P3 → P4 → P5) or in parallel if staffed
- **Phase 8 (Observability)**: Depends on Phase 2 (metrics module must exist); alert rule (T021) is independent of story phases; integration tests (T022) depend on T009
- **Phase 9 (Polish)**: Depends on all desired stories being complete

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 only — Home.py consumer thread must exist (T010)
- **US2 (P2)**: Depends on Phase 2 only — Trino connection pattern established
- **US3 (P3)**: Depends on T015 (rename existing file before creating new 3_rule_triggers.py)
- **US4 (P4)**: Depends on Phase 2 only — independent of US2/US3
- **US5 (P5)**: Depends on Phase 2 only — independent of all other stories

### Within Each User Story

- Query module (analytics/queries/) → Page implementation (analytics/app/pages/)
- T015 (rename) MUST precede T017 (create new 3_rule_triggers.py)
- T008 (metrics) MUST precede T009 (consumer — it imports metrics)
- T009 (consumer) MUST precede T010 (Home.py — it starts the consumer)
- T010 (Home.py) MUST precede T012 (live feed page — it reads feed_queue from session_state)

### Parallel Opportunities

All [P]-marked tasks within a phase can run concurrently:
- T004 + T005 (Makefile targets + Prometheus scrape config)
- T006 + T007 (empty __init__.py files)
- T013 + T018 (fraud_rate.py + model_versions.py query modules — independent files)
- T016 + T018 (rule_triggers.py + model_versions.py query modules — independent files)
- T021 + T023 + T024 + T026 (alert rule + CLAUDE.md + README + ruff config — independent files)

---

## Parallel Example: Phase 2 (Foundational)

```bash
# These two init files can be created simultaneously:
Task T006: "Create analytics/consumers/__init__.py (empty)"
Task T007: "Create analytics/queries/__init__.py (empty)"

# After T006+T007, metrics and consumer are sequentially dependent:
Task T008: analytics/consumers/metrics.py  →  Task T009: analytics/consumers/kafka_consumer.py  →  Task T010: analytics/app/Home.py
```

## Parallel Example: Phase 4 + Phase 6 (after Phase 2 complete)

```bash
# Query modules for US2 and US4 can be built concurrently (different files):
Task T013: "analytics/queries/fraud_rate.py"
Task T018: "analytics/queries/model_versions.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup → `make analytics-up` green
2. Complete Phase 2: Foundational → consumer thread running, metrics on :8004
3. Complete Phase 3: US1 Live Feed → real-time fraud alerts visible
4. **STOP and VALIDATE**: Publish test events, verify feed, check consumer group isolation
5. This is a shippable analytics tier increment — no historical queries needed for MVP

### Incremental Delivery

1. Setup + Foundational → `make analytics-up` green, metrics endpoint live
2. US1 (Live Feed) → Real-time fraud visibility — DEMO ready
3. US2 (Fraud Rate) → Historical channel/merchant/geo reports — DEMO ready
4. US3 (Rule Leaderboard) → Operational rule health visible — DEMO ready
5. US4 (Model Comparison) → ML engineer model rollout tooling — DEMO ready
6. US5 (DLQ Inspector) → Incident response tooling complete
7. Phase 8 (Observability) + Phase 9 (Polish) → Production-ready

### Key Risk: File Rename (T015)

T015 (rename `3_rule_triggers.py` → `6_shadow_rules.py`) MUST be committed before T017 creates the new `3_rule_triggers.py`. A failed/partial rename risks overwriting the Shadow Rule Monitor built in a prior feature. Verify git shows the rename correctly before proceeding.

---

## Notes

- [P] tasks = different files, no shared state, can run in parallel
- [Story] label maps each task to its user story for traceability
- T015 (file rename) is a one-way operation — verify git rename before creating new file
- `analytics.dashboard` consumer group must never appear in `flink-scoring-job` describe output
- Each Trino query wrapper module must accept a `trino_conn` parameter (not create its own) to enable test injection
- `pii_masker` import in T020 uses existing module from `pipelines/ingestion/shared/pii_masker/` — do not duplicate masking logic
- Commit after each phase checkpoint to isolate rollback scope
