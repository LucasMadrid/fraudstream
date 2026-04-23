# Implementation Plan: Analytics Consumer Layer

**Branch**: `008-analytics-consumer-layer` | **Date**: 2026-04-20 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/008-analytics-consumer-layer/spec.md`

---

## Summary

Build the independent analytics consumer service mandated by Constitution Principle X: a Streamlit application with a background Kafka consumer thread (consumer group `analytics.dashboard`) for a real-time fraud alert feed, and five Trino-backed historical report pages over the existing Iceberg persistence layer. The service runs in the Analytics Docker Compose tier, is strictly read-only relative to the pipeline, and emits consumer lag metrics on `:8004/metrics`. An existing misplaced file (`3_rule_triggers.py` — Shadow Rule Monitor from a prior feature) is renamed to `6_shadow_rules.py`; the new `3_rule_triggers.py` will be the historical rule leaderboard from Iceberg.

---

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Streamlit ≥ 1.35, confluent-kafka ≥ 2.3, trino ≥ 0.328, fastavro ≥ 1.9, prometheus-client ≥ 0.20, duckdb ≥ 0.10, pandas ≥ 2.0 (all via `analytics` extras in `pyproject.toml`)  
**Storage**: Iceberg (read-only via Trino at `:8083`); Kafka topics `txn.fraud.alerts` + DLQ topics (read-only consumer)  
**Testing**: pytest + testcontainers (Kafka); existing CI gate (80% coverage)  
**Target Platform**: Docker container (linux/amd64), local dev via `make analytics-up`  
**Performance Goals**: Live feed ≤ 2s p95 latency; historical queries ≤ 5s (24h window), ≤ 10s (30d window)  
**Constraints**: Independent process — zero coupling to scoring engine; read-only; consumer group `analytics.dashboard` must not interfere with pipeline offsets; `:8004/metrics` for Prometheus scraping  
**Scale/Scope**: Single Streamlit instance; bounded 500-event in-memory feed buffer; no horizontal scaling required in v1

---

## Constitution Check

*GATE: Must pass before implementation. Re-checked after design phase.*

| Principle | Gate | Status |
|-----------|------|--------|
| **X — Analytics Consumer Layer** | Independent process, dedicated consumer group `analytics.dashboard`, read-only, non-critical-path | ✅ Plan enforces all constraints |
| **II — Sub-100ms Decision Budget** | Analytics service must not add latency to scoring hot path | ✅ Independent process — zero hot-path coupling |
| **VIII — Observability** | Consumer lag metric exposed via Prometheus; consumer restarts counted | ✅ `:8004/metrics` with `analytics_consumer_lag` gauge |
| **IX — Analytics-First Persistence** | Historical queries MUST use Iceberg, not re-consume raw Kafka | ✅ All historical pages query Trino views over Iceberg |
| **VII — PII Minimization** | No full PAN or IP displayed; DLQ payloads masked before render | ✅ pii_masker applied to DLQ payloads; Iceberg stores masked values |
| **III — Schema Contract** | Consumer reads Avro from `txn.fraud.alerts`; fastavro only | ✅ fastavro deserialization; avro-python3 prohibited |

**No violations. Plan may proceed.**

---

## Project Structure

### Documentation (this feature)

```text
specs/008-analytics-consumer-layer/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
analytics/
├── app/
│   ├── Home.py                     # NEW — entry point, starts consumer thread, health checks
│   └── pages/
│       ├── 1_live_feed.py          # NEW — real-time fraud alert feed (FR-004 to FR-008)
│       ├── 2_fraud_rate.py         # NEW — fraud rate by channel/merchant/geo (FR-009 to FR-012)
│       ├── 3_rule_triggers.py      # NEW — rule leaderboard from Iceberg (FR-013 to FR-014)
│       │                           #       (replaces existing file — see Complexity Tracking)
│       ├── 4_model_compare.py      # NEW — model version comparison (FR-015 to FR-016)
│       ├── 5_dlq_inspector.py      # NEW — DLQ browser (FR-017 to FR-019)
│       └── 6_shadow_rules.py       # RENAMED from existing 3_rule_triggers.py
├── consumers/
│   ├── __init__.py                 # NEW
│   ├── kafka_consumer.py           # NEW — daemon thread + queue.Queue bridge
│   └── metrics.py                  # NEW — prometheus_client, start_http_server(:8004)
├── queries/
│   ├── __init__.py                 # NEW
│   ├── fraud_rate.py               # NEW — Trino query wrappers for v_fraud_rate_daily
│   ├── rule_triggers.py            # NEW — Trino query wrappers for v_rule_triggers
│   └── model_versions.py           # NEW — Trino query wrappers for v_model_versions
└── views/                          # EXISTING — Trino SQL view definitions (no changes)

infra/
└── analytics/
    └── Dockerfile                  # NEW — Streamlit service image

infra/
├── docker-compose.yml              # MODIFIED — add streamlit service
└── prometheus/
    ├── prometheus.yml              # MODIFIED — add :8004 scrape target
    └── alerts/
        └── analytics_consumer.yml  # NEW — AnalyticsConsumerLagHigh alert

tests/
├── unit/
│   └── test_analytics_consumer.py      # NEW — buffer eviction, PII masking, consumer thread
└── integration/
    └── test_analytics_integration.py   # NEW — consumer group isolation, lag metric

pyproject.toml                          # MODIFIED — add streamlit, duckdb, pandas to analytics extra
Makefile                                # MODIFIED — add analytics-up / analytics-down targets
```

---

## Implementation Phases

### Phase 1 — Dependency & Infrastructure Setup

**Goal**: Get the analytics service running in Docker with a healthy Kafka consumer. No UI yet.

**Tasks**:
1. Add `streamlit>=1.35`, `duckdb>=0.10`, `pandas>=2.0` to `pyproject.toml` `analytics` extras.
2. Create `infra/analytics/Dockerfile` — Python 3.11 slim + `pip install -e ".[analytics]"` + `streamlit` entry point.
3. Add `streamlit` service to `infra/docker-compose.yml` (depends on `kafka`, `trino`; port `8501`).
4. Add `analytics-up` and `analytics-down` Makefile targets.
5. Add `:8004` scrape target to `infra/prometheus/prometheus.yml`.

**Deliverable**: `make analytics-up` starts without errors. `http://localhost:8501` returns 200.

---

### Phase 2 — Kafka Consumer + Metrics

**Goal**: Background consumer thread consuming `txn.fraud.alerts`, exposing lag metric on `:8004`.

**Tasks**:
1. Implement `analytics/consumers/kafka_consumer.py`:
   - `AnalyticsKafkaConsumer` class with `start()` / `stop()`.
   - Daemon thread consuming `txn.fraud.alerts`, consumer group `analytics.dashboard`, `auto.offset.reset=latest`.
   - Writes deserialized `FraudAlertDisplay` dicts to a `queue.Queue(maxsize=500)`.
   - Reconnect on Kafka errors; increment `analytics_consumer_restarts_total`.
2. Implement `analytics/consumers/metrics.py`:
   - `analytics_consumer_lag` Gauge, `analytics_events_consumed_total` Counter, `analytics_consumer_restarts_total` Counter.
   - `start_metrics_server(port=8004)` starts `prometheus_client.start_http_server`.
3. Implement `analytics/app/Home.py`:
   - Starts consumer thread once per session via `st.session_state` guard.
   - Starts metrics server once (process-level flag).
   - Displays service health: consumer thread alive, last event received timestamp, consumer lag.
4. Write unit tests in `tests/unit/test_analytics_consumer.py`:
   - Buffer eviction at maxsize (oldest entry removed when full).
   - `FraudAlertDisplay` deserialization from Avro record.
   - Consumer restart counter increments on simulated Kafka error.

**Deliverable**: `analytics_consumer_lag` metric visible at `:8004/metrics`. Home page shows consumer health.

---

### Phase 3 — Live Feed Page (P1)

**Goal**: `1_live_feed.py` displaying real-time `txn.fraud.alerts` events.

**Tasks**:
1. Implement `analytics/app/pages/1_live_feed.py`:
   - Reads from `st.session_state["feed_queue"]` (populated by consumer thread).
   - Maintains a `deque(maxlen=500)` in `st.session_state["feed_buffer"]`.
   - `st.empty()` loop with `time.sleep(0.5)` polling; `st.rerun()` on new events.
   - Renders events as a `st.dataframe` with columns: time, transaction_id, account_id, merchant_id, amount, currency, channel, decision, fraud_score, rule_triggers.
   - Shows "Reconnecting…" banner when `consumer_healthy` is `False`.
2. Add deduplication: skip event if `transaction_id` already in buffer (handles at-least-once delivery).

**Deliverable**: Live feed renders events within 2 seconds of publication. Buffer is capped at 500 entries. Disconnection shows indicator.

---

### Phase 4 — Historical Report Pages (P2–P4)

**Goal**: `2_fraud_rate.py`, `3_rule_triggers.py`, `4_model_compare.py` querying Trino.

**Tasks**:
1. Implement `analytics/queries/fraud_rate.py`:
   - `get_fraud_rate(window_days: int, trino_conn) -> pd.DataFrame` — queries `v_fraud_rate_daily` filtered to last N days; returns channel/decision breakdown.
   - `get_merchant_fraud_rate(window_days: int, top_n: int, trino_conn) -> pd.DataFrame`.
   - `get_geo_fraud_rate(window_days: int, trino_conn) -> pd.DataFrame`.
2. Implement `analytics/queries/rule_triggers.py`:
   - `get_rule_leaderboard(window_days: int, trino_conn) -> pd.DataFrame` — queries `v_rule_triggers`, pivots ALLOW/FLAG+BLOCK to compute FP rate per rule.
3. Implement `analytics/queries/model_versions.py`:
   - `get_model_score_distribution(window_days: int, trino_conn) -> pd.DataFrame` — queries `v_model_versions` with date filter.
4. Implement `analytics/app/pages/2_fraud_rate.py`:
   - Time window selector (1h, 24h, 7d).
   - Three `st.tabs` for channel, merchant, geo breakdowns.
   - Error state when Trino unreachable.
5. Implement `analytics/app/pages/3_rule_triggers.py` (replaces Shadow Rule Monitor):
   - Rule leaderboard table + FP rate column.
   - `st.bar_chart` of trigger count per rule.
6. Rename existing `analytics/app/pages/3_rule_triggers.py` → `6_shadow_rules.py` before implementing the new file.
7. Implement `analytics/app/pages/4_model_compare.py`:
   - Model version selector; score distribution histograms side-by-side.
   - Note when only one version is available.

**Deliverable**: All three historical pages load with test data. Trino queries execute within 5s for 24h window.

---

### Phase 5 — DLQ Inspector (P5)

**Goal**: `5_dlq_inspector.py` browsing dead-letter records.

**Tasks**:
1. Implement `analytics/app/pages/5_dlq_inspector.py`:
   - Creates a short-lived Kafka consumer (no consumer group persistence) on page load.
   - Polls `txn.api.dlq`, `txn.processing.dlq`, `txn.fraud.alerts.dlq` with `auto.offset.reset=earliest`, max 200 records per topic.
   - Applies `pii_masker` to payload before display (imports from `pipelines/ingestion/shared/pii_masker/`).
   - Renders table with source_topic, error_reason, timestamp, masked payload preview.
   - Expandable row for full payload.
   - "No DLQ records" state when all topics are empty.

**Deliverable**: DLQ inspector shows records within 30s when DLQ topics have content. PII masking test passes.

---

### Phase 6 — Prometheus Alert + Consumer Group Isolation Test

**Goal**: Alert fires on lag > 500; integration test verifies consumer group isolation.

**Tasks**:
1. Create `infra/prometheus/alerts/analytics_consumer.yml` with `AnalyticsConsumerLagHigh` alert (expression: `analytics_consumer_lag{topic="txn.fraud.alerts"} > 500`, for: 60s, severity: warning).
2. Write `tests/integration/test_analytics_integration.py`:
   - Test: consume 50 events with `analytics.dashboard` group; assert `flink-scoring-job` group offsets are unchanged.
   - Test: `analytics_consumer_lag` metric returns 0 after all events consumed.
   - Test: analytics service kill does not raise errors in scoring job (FR-003, SC-002).

**Deliverable**: Integration tests pass. Alert rule validated via `promtool check rules`.

---

## Complexity Tracking

| Decision | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| Rename existing `3_rule_triggers.py` → `6_shadow_rules.py` | The existing file is the Shadow Rule Monitor (operational tooling, Prometheus + Management API). The spec requires `3_rule_triggers.py` to be the historical rule leaderboard from Iceberg — different data source, different purpose. | Deleting the Shadow Rule Monitor loses operational tooling that was built in a prior feature and actively used. Keeping both under distinct page numbers is the minimal-impact resolution. |
| Separate metrics port `:8004` | Pipeline uses `:8002`; analytics metrics must not mix with pipeline metrics in Prometheus scrape configs | Shared port would require relabeling rules in Prometheus and risk metric name collisions if pipeline and analytics both emit same metric names in future. |
| Daemon thread + `queue.Queue` for live feed | Streamlit re-runs the script on every interaction — a consumer created inside the script would reconnect on every rerun | `asyncio` event loop is not natively supported per-Streamlit-session; third-party `streamlit-kafka` is unmaintained. Thread is the established Streamlit real-time pattern. |
