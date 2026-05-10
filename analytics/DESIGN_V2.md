# FraudStream Analytics — v2 Redesign Document

**Author:** Designer Agent  
**Date:** 2026-05-09  
**Status:** PROPOSAL  
**Scope:** Specs 010–016, UX overhaul, performance, migration path

---

## Table of Contents

1. [UX Problems & Fixes](#1-ux-problems--fixes)
2. [New Pages for Specs 010–016](#2-new-pages-for-specs-010016)
3. [Performance Improvements](#3-performance-improvements)
4. [Component Architecture](#4-component-architecture)
5. [Migration Path](#5-migration-path-toward-fastapistreamlit)
6. [Navigation Flow](#6-navigation-flow)
7. [Component Hierarchy](#7-component-hierarchy)

---

## 1. UX Problems & Fixes

### 1.1 Critical Issues Found

| # | Problem | Impact | Fix |
|---|---------|--------|-----|
| P1 | **Per-session Kafka consumer** — each browser tab spawns a new `AnalyticsKafkaConsumer` thread (Home.py:27). 10 analysts = 10 consumer group members = partition rebalances. | High: rebalance storms, missed messages | Extract to shared singleton process (see §3.1) |
| P2 | **Live feed uses `time.sleep` + `st.rerun()`** (1_live_feed.py:97-98) — blocks the Streamlit thread, wastes server resources, and prevents interaction during sleep. | High: UI freezes 1s per cycle | Replace with `st.fragment` auto-refresh (Streamlit ≥1.37) or `st_autorefresh` component |
| P3 | **No query caching** — DuckDBQueryRunner creates a new connection per call, re-scans Iceberg on every page load. | Medium: slow page transitions, DuckDB memory spikes | Add `@st.cache_data(ttl=N)` per query tier (see §4.2) |
| P4 | **Duplicate `set_page_config` calls** — every page calls `st.set_page_config()` independently; Streamlit only honors the first one per session, rest are silently ignored or error. | Low: confusing code | Move to Home.py only; remove from all pages |
| P5 | **No role-based access** — any user can promote shadow rules to production (6_shadow_rules.py:334). No confirmation dialog. | High: accidental production changes | Add confirmation modal + role gate via session state / env-var allowlist |
| P6 | **Account IDs visible in session state** — `live_feed_buffer` stores `account_id` in `FraudAlertDisplay` objects in Streamlit session state (memory). | Medium: PII in process memory violates constitution | Mask account_id at consumer level before enqueue; store only masked form |
| P7 | **No deep-linking** — analysts cannot share a URL that points to a specific rule, model version, or transaction. | Medium: collaboration friction | Use `st.query_params` for filter state persistence |
| P8 | **Monolithic sidebar** — all pages share the same sidebar but each page puts different controls there, causing layout jumps. | Low: visual inconsistency | Standardize sidebar layout component (see §4.1) |
| P9 | **No loading skeletons** — pages show blank white while queries run; analysts think the app is broken. | Medium: perceived performance | Add `st.skeleton` / spinner wrappers in widgets.py |

### 1.2 Analyst Workflow Gaps

- **No alerting from dashboard** — analysts monitor Live Feed passively; no ability to set threshold alerts.
- **No investigation flow** — clicking a transaction in Live Feed doesn't drill into trace/detail view.
- **No export** — no CSV/PDF export for compliance reports.
- **No annotation** — analysts can't mark false positives or add notes to flagged transactions.

---

## 2. New Pages for Specs 010–016

### 2.1 Shadow Scoring Comparison (Spec 010)

**Purpose:** Side-by-side production vs. shadow model verdicts.

**URL:** `/Shadow_Scoring`  
**File:** `app/pages/08_shadow_scoring.py`

**Wireframe (text):**
```
+------------------------------------------------------------------+
| SHADOW SCORING COMPARISON                                        |
+------------------------------------------------------------------+
| [Date Range ▼]  [Model Filter ▼]  [Decision Filter ▼]  [Refresh]|
+------------------------------------------------------------------+
| KPI ROW                                                          |
| ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌────────────────┐      |
| │ Agreement │ │ Disagree │ │ Shadow    │ │ Shadow Avg     │      |
| │ Rate: 94% │ │ Count:312│ │ Better: 61│ │ Latency: 12ms  │      |
| └──────────┘ └──────────┘ └───────────┘ └────────────────┘      |
+------------------------------------------------------------------+
| TABS: [Score Scatter] [Decision Matrix] [Disagreement Feed]      |
|                                                                  |
| Score Scatter:                                                   |
|   X-axis: Production score  Y-axis: Shadow score                 |
|   Color: decision agreement (green=agree, red=disagree)          |
|   Hover: txn_id (masked), rules triggered, latency               |
|                                                                  |
| Decision Matrix (confusion-style):                               |
|              Shadow→BLOCK  Shadow→FLAG  Shadow→ALLOW             |
|   Prod→BLOCK     1204         38           12                    |
|   Prod→FLAG        22        891           45                    |
|   Prod→ALLOW        5         19         8764                    |
|                                                                  |
| Disagreement Feed:                                               |
|   Table of txns where prod != shadow decision                    |
|   Columns: timestamp, masked_account, amount, prod_decision,     |
|            shadow_decision, prod_score, shadow_score, rules       |
|   Click row → opens Trace Viewer (deep link)                     |
+------------------------------------------------------------------+
```

**Data source:** `iceberg.fraud_decisions` joined on `transaction_id` where `model_version` is shadow tag.  
**Query tier:** DuckDB (≤30d), Trino (>30d).

---

### 2.2 Replay Results Viewer (Specs 011/015)

**Purpose:** Browse replayed transactions, compare original vs. replay decisions.

**URL:** `/Replay_Results`  
**File:** `app/pages/09_replay_results.py`

**Wireframe:**
```
+------------------------------------------------------------------+
| REPLAY RESULTS VIEWER                                            |
+------------------------------------------------------------------+
| Replay Job Selector:                                             |
| [Job ID ▼: replay-2026-05-09-001]  Status: COMPLETED  3/3 done  |
+------------------------------------------------------------------+
| SUMMARY BAR                                                      |
| ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐             |
| │ Total    │ │ Same     │ │ Changed  │ │ New      │             |
| │ Txns: 50K│ │ Decision │ │ Decision │ │ Blocks   │             |
| │          │ │ 47,200   │ │ 2,800    │ │ +340     │             |
| └──────────┘ └──────────┘ └──────────┘ └──────────┘             |
+------------------------------------------------------------------+
| TABS: [Decision Diff] [Score Histogram] [Rule Impact]            |
|                                                                  |
| Decision Diff:                                                   |
|   Filterable table: original_decision, replay_decision,          |
|   original_score, replay_score, delta_score, rules_added,        |
|   rules_removed                                                  |
|   Sort by |delta_score| descending                               |
|                                                                  |
| Score Histogram:                                                 |
|   Overlay: original score distribution vs replay distribution    |
|                                                                  |
| Rule Impact:                                                     |
|   Bar chart: rules that changed most between original/replay     |
|   Shows: rule_id, times_added, times_removed, net_impact         |
+------------------------------------------------------------------+
```

**Data source:** Replay results Kafka topic → Iceberg `replay_results` table.  
**New query module:** `queries/replay_results.py`.

---

### 2.3 Trace Viewer (Spec 015)

**Purpose:** End-to-end transaction journey with latency breakdown.

**URL:** `/Trace_Viewer`  
**File:** `app/pages/10_trace_viewer.py`

**Wireframe:**
```
+------------------------------------------------------------------+
| TRACE VIEWER                                                     |
+------------------------------------------------------------------+
| Search: [Transaction ID _______________] [Search]                |
| (also accessible via deep-link: ?txn_id=xxx)                     |
+------------------------------------------------------------------+
| TRANSACTION SUMMARY CARD                                         |
| Amount: $452.00 | Channel: POS | Country: ES                    |
| Decision: BLOCK | Score: 0.87 | Model: v2.3.1                   |
| Total Latency: 145ms                                             |
+------------------------------------------------------------------+
| WATERFALL TIMELINE (horizontal bar chart)                        |
|                                                                  |
| API Gateway    ████░░░░░░░░░░░░░░░░░░░░░░░  12ms                |
| Enrichment     ░░░░████████░░░░░░░░░░░░░░░  38ms                |
|   └ Feast Get  ░░░░░░██████░░░░░░░░░░░░░░░  29ms                |
| ML Scoring     ░░░░░░░░░░░░████████░░░░░░░  42ms                |
| Rule Engine    ░░░░░░░░░░░░░░░░░░░░████░░░  18ms                |
| Decision Write ░░░░░░░░░░░░░░░░░░░░░░░░██░  8ms                 |
| Kafka Produce  ░░░░░░░░░░░░░░░░░░░░░░░░░░█  5ms                 |
+------------------------------------------------------------------+
| DETAIL PANELS (expandable)                                       |
| ▸ Features Used (table: feature_name, value, staleness)          |
| ▸ Rules Evaluated (table: rule_id, matched, mode, exec_time)     |
| ▸ Model Details (version, input_hash, raw_output)                |
| ▸ Kafka Events (topic, partition, offset, timestamp)             |
+------------------------------------------------------------------+
```

**Data source:** Tracing spans from `iceberg.trace_spans` table (new).  
**Latency source:** Prometheus histograms + span metadata.  
**PII handling:** Account ID always masked; transaction ID is operational, not PII.

---

### 2.4 Model A/B Dashboard (Spec 012)

**Purpose:** Challenger vs. champion model comparison with statistical rigor.

**URL:** `/Model_AB`  
**File:** `app/pages/11_model_ab.py`

**Wireframe:**
```
+------------------------------------------------------------------+
| MODEL A/B TESTING DASHBOARD                                      |
+------------------------------------------------------------------+
| Active Experiment: [Experiment ▼]  Status: RUNNING (day 4/14)    |
+------------------------------------------------------------------+
| CHAMPION vs CHALLENGER SUMMARY                                   |
| ┌─────────────────────┐  ┌─────────────────────┐                |
| │ CHAMPION: v2.3.1    │  │ CHALLENGER: v2.4.0  │                |
| │ Traffic: 90%        │  │ Traffic: 10%        │                |
| │ Avg Score: 0.042    │  │ Avg Score: 0.038    │                |
| │ Block Rate: 2.1%    │  │ Block Rate: 1.8%    │                |
| │ P99 Latency: 45ms   │  │ P99 Latency: 52ms   │                |
| │ FP Rate*: 0.3%      │  │ FP Rate*: 0.2%      │                |
| └─────────────────────┘  └─────────────────────┘                |
|                                                                  |
| Statistical Significance: p=0.034 (significant at α=0.05)       |
| Recommendation: Challenger shows 14% fewer false positives       |
+------------------------------------------------------------------+
| TABS: [Score Over Time] [Latency CDF] [Segment Breakdown]        |
|                                                                  |
| Score Over Time:                                                 |
|   Dual line chart: champion avg score vs challenger avg score    |
|   Shaded confidence intervals                                    |
|                                                                  |
| Latency CDF:                                                    |
|   P50/P90/P95/P99 comparison bars, side by side                 |
|                                                                  |
| Segment Breakdown:                                               |
|   Table: channel × model → block_rate, avg_score, volume         |
|   Highlights segments where challenger significantly differs     |
+------------------------------------------------------------------+
```

**Data source:** `iceberg.fraud_decisions` partitioned by model_version + experiment_id.  
**New query module:** `queries/model_ab.py`.  
**Statistical engine:** scipy.stats in query layer (not in Streamlit thread).

---

### 2.5 Feature Staleness Dashboard (Spec 015)

**Purpose:** Real-time feature freshness monitoring.

**URL:** `/Feature_Staleness`  
**File:** `app/pages/12_feature_staleness.py`

**Wireframe:**
```
+------------------------------------------------------------------+
| FEATURE STALENESS DASHBOARD                                      |
+------------------------------------------------------------------+
| Overall Health: 🟢 42/45 features fresh | 🟡 2 stale | 🔴 1 down |
+------------------------------------------------------------------+
| FEATURE TABLE (sortable)                                         |
| ┌────────────────┬──────────┬───────────┬──────────┬───────────┐ |
| │ Feature        │ Source   │ Max Age   │ Current  │ Status    │ |
| │                │          │ (SLA)     │ Age      │           │ |
| ├────────────────┼──────────┼───────────┼──────────┼───────────┤ |
| │ vel_count_1h   │ Feast    │ 5min      │ 2min     │ 🟢 Fresh  │ |
| │ avg_amount_24h │ Feast    │ 15min     │ 18min    │ 🟡 Stale  │ |
| │ geo_risk_score │ External │ 1h        │ ERROR    │ 🔴 Down   │ |
| └────────────────┴──────────┴───────────┴──────────┴───────────┘ |
+------------------------------------------------------------------+
| STALENESS TREND (line chart, last 24h)                           |
|   One line per feature group; Y-axis = avg staleness (seconds)   |
|   Threshold line at SLA boundary                                 |
+------------------------------------------------------------------+
| IMPACT PANEL                                                     |
|   "Stale features affected N transactions in last hour"          |
|   "Fallback values used: vel_count_1h → default=0 (23 txns)"    |
+------------------------------------------------------------------+
```

**Data source:** Prometheus metrics `feature_store_staleness_seconds`, `feature_store_fallback_total`.  
**Refresh:** Auto-refresh every 30s via `@st.fragment`.

---

### 2.6 Rule Deployment Control Panel (Spec 016 / v2 Rule CRUD)

**Purpose:** Canary rollout, percentage splitting, rule lifecycle management.

**URL:** `/Rule_Control`  
**File:** `app/pages/13_rule_control.py`

**Wireframe:**
```
+------------------------------------------------------------------+
| RULE DEPLOYMENT CONTROL PANEL                                    |
+------------------------------------------------------------------+
| TABS: [Active Rules] [Deploy New] [Canary Monitor] [Audit Log]   |
+------------------------------------------------------------------+
| Active Rules:                                                    |
| ┌──────────┬────────┬─────────┬─────────┬────────┬─────────────┐|
| │ Rule ID  │ Mode   │ Traffic │ Triggers│ FP Rate│ Actions     │|
| │          │        │ %       │ (24h)   │        │             │|
| ├──────────┼────────┼─────────┼─────────┼────────┼─────────────┤|
| │ R-001    │ ACTIVE │ 100%    │ 1,204   │ 0.3%   │ [Edit][Off] │|
| │ R-045    │ CANARY │ 10%     │ 45      │ 1.1%   │ [↑50%][Off] │|
| │ R-102    │ SHADOW │ 100%*   │ 892     │ 2.4%   │ [Promote]   │|
| └──────────┴────────┴─────────┴─────────┴────────┴─────────────┘|
|                                                                  |
| Deploy New:                                                      |
|   Rule Definition (YAML/JSON editor)                             |
|   Initial Mode: [SHADOW ▼]                                      |
|   Traffic %: [10% ▼] (only for CANARY mode)                     |
|   [Validate] → dry-run against last 1000 txns                   |
|   [Deploy] → publishes to txn.rules.config Kafka topic           |
|                                                                  |
| Canary Monitor:                                                  |
|   Selected rule: R-045                                           |
|   ┌─ Canary Timeline ──────────────────────────────────────────┐|
|   │ 10% ──────── 25% ──────── 50% ──────── (next: 100%)       │|
|   │ ✓ day 1      ✓ day 3      ● day 5      ○ day 7            │|
|   └────────────────────────────────────────────────────────────┘|
|   Auto-rollback threshold: FP > 3% → revert to SHADOW           |
|                                                                  |
| Audit Log:                                                       |
|   Table: timestamp, user, action, rule_id, details               |
|   Filterable by date range, action type, user                    |
+------------------------------------------------------------------+
```

**Data source:** Management API for rule state; `txn.rules.config` Kafka topic for mutations; Prometheus for FP metrics.  
**Write path:** All mutations go through `txn.rules.config` topic (spec constraint).  
**Confirmation:** All destructive actions require typed confirmation ("type DEPLOY to confirm").

---

## 3. Performance Improvements

### 3.1 Shared Consumer Singleton (Critical)

**Problem:** Each Streamlit session creates its own `AnalyticsKafkaConsumer` → N sessions = N consumer group members → constant rebalancing.

**Solution:** Extract consumer to a **separate long-lived process** that writes to a shared SQLite WAL or Redis stream.

```
┌─────────────────────────────────┐
│  Consumer Sidecar Process       │
│  (single process, single group  │
│   member: analytics.dashboard)  │
│                                 │
│  Kafka → deserialize → write    │
│         to shared ring buffer   │
│         (SQLite WAL or Redis)   │
└────────────┬────────────────────┘
             │ read
    ┌────────┴────────┐
    │   Streamlit     │
    │   Session 1..N  │  (read-only from buffer)
    └─────────────────┘
```

**Implementation steps:**
1. New module: `consumers/sidecar.py` — standalone process with its own `if __name__` entry point.
2. Writes last 1000 alerts to SQLite file with WAL mode (concurrent reads safe).
3. Streamlit sessions read from SQLite instead of owning a Kafka consumer.
4. Remove `consumer` from `st.session_state`.
5. Docker Compose: add `analytics-consumer` service, separate from `analytics-app`.

### 3.2 Query Caching Strategy

| Query Type | TTL | Cache Key | Invalidation |
|-----------|-----|-----------|-------------|
| KPI summary | 60s | `kpi_{days}` | Time-based |
| Fraud rate daily | 120s | `fraud_rate_{days}` | Time-based |
| Rule triggers | 60s | `rule_triggers_{days}` | Time-based |
| Model versions | 300s | `model_{days}` | Time-based |
| Iceberg table scan | 300s | `iceberg_{table}_{hours}` | Time-based |
| Prometheus queries | 30s | `prom_{query_hash}` | Time-based |
| Replay results | 600s | `replay_{job_id}` | Immutable after job complete |
| Trace spans | 3600s | `trace_{txn_id}` | Immutable |

**Implementation:**
- Wrap all query functions with `@st.cache_data(ttl=N)`.
- For cross-session caching, use `@st.cache_resource` for DuckDB connection pool.
- Add cache-clear button in sidebar for analysts who need fresh data.

### 3.3 DuckDB Connection Pool

**Problem:** `DuckDBQueryRunner` creates and destroys a connection per query.

**Solution:**
```python
@st.cache_resource
def get_duckdb_pool() -> duckdb.DuckDBPyConnection:
    """Single in-process DuckDB instance shared across sessions."""
    conn = duckdb.connect()  # in-memory
    conn.execute("SET memory_limit='512MB'")
    conn.execute("SET threads=2")
    return conn
```

- Use `conn.cursor()` for concurrent query isolation.
- Cap memory at 512MB to prevent OOM with multiple sessions.
- Limit to 2 threads to avoid starving Streamlit's event loop.

### 3.4 Iceberg Scan Optimization

- Cache Arrow tables from `iceberg_reader.py` with `@st.cache_data(ttl=300)`.
- Add partition pruning: filter by `decision_date` before scanning.
- Use `selected_fields` in PyIceberg scan to project only needed columns.

### 3.5 Fragment-Based Auto-Refresh

Replace `time.sleep()` + `st.rerun()` pattern with Streamlit fragments:

```python
@st.fragment(run_every=timedelta(seconds=2))
def live_feed_fragment():
    alerts = read_from_shared_buffer(limit=100)
    render_alert_table(alerts)
```

Benefits: only the fragment re-executes, not the entire page.

---

## 4. Component Architecture

### 4.1 Widget Library (`app/widgets.py` expansion)

```python
# Current: only run_query()
# Proposed widget library:

widgets/
├── __init__.py          # re-exports
├── query.py             # run_query(), cached_query()
├── sidebar.py           # StandardSidebar (date range, refresh, cache clear)
├── kpi_row.py           # KPIRow(metrics: list[KPIMetric])
├── alert_card.py        # AlertCard(alert: FraudAlertDisplay)
├── decision_matrix.py   # DecisionMatrix(df, row_col, col_col, val_col)
├── waterfall.py         # WaterfallChart(spans: list[TraceSpan])
├── confirmation.py      # ConfirmAction(label, confirm_text) -> bool
├── data_table.py        # FilterableTable(df, filters, export=True)
├── status_badge.py      # StatusBadge(state: str) -> colored indicator
└── deep_link.py         # deep_link(page, **params) -> URL string
```

### 4.2 Caching Architecture

```
┌─────────────────────────────────────────────────┐
│                  CACHE LAYERS                    │
├─────────────────────────────────────────────────┤
│ L1: st.cache_data (per-session, serialized)      │
│     - Query results (TTL: 30-600s by type)       │
│     - Prometheus metrics (TTL: 30s)              │
│                                                  │
│ L2: st.cache_resource (cross-session, singleton)  │
│     - DuckDB connection pool                     │
│     - PyIceberg catalog instance                 │
│     - Shared alert buffer reader                 │
│                                                  │
│ L3: External (survives restarts)                 │
│     - SQLite WAL (consumer sidecar buffer)       │
│     - Redis (future: session state, query cache) │
└─────────────────────────────────────────────────┘
```

**PII constraint:** No PII in any cache layer. Account IDs masked before caching. Transaction IDs are operational identifiers, not PII — allowed in L1/L2.

### 4.3 Session State Schema

```python
# Minimal session state — no Kafka consumer, no PII
session_state = {
    # User preferences (persisted via query params)
    "default_lookback_days": int,       # 30
    "auto_refresh_enabled": bool,       # True
    "theme": str,                       # "light" | "dark"

    # Page-specific transient state
    "live_feed_last_seen_id": str,      # cursor for buffer reads
    "trace_viewer_txn_id": str | None,  # current trace
    "rule_control_pending_action": dict | None,  # confirmation state

    # REMOVED from session state:
    # - "consumer" (moved to sidecar process)
    # - "live_feed_buffer" (moved to shared SQLite)
    # - "live_feed_seen" (moved to shared SQLite)
}
```

### 4.4 Error Handling Strategy

```
┌─ Query Error ─────────────────────────────────────┐
│ 1. Log at ERROR with traceback                    │
│ 2. Show st.error() with user-friendly message     │
│ 3. Show "Retry" button                            │
│ 4. If Iceberg/DuckDB: suggest reducing date range │
│ 5. Never expose stack traces to UI                │
└───────────────────────────────────────────────────┘

┌─ Consumer Sidecar Down ───────────────────────────┐
│ 1. Banner at top of Live Feed: "Data may be stale"│
│ 2. Show last-known-good timestamp                 │
│ 3. Historical pages unaffected                    │
└───────────────────────────────────────────────────┘

┌─ Management API Down ─────────────────────────────┐
│ 1. Rule Control Panel: read-only mode             │
│ 2. Disable all mutation buttons                   │
│ 3. Show "Management API unavailable" banner       │
└───────────────────────────────────────────────────┘
```

---

## 5. Migration Path Toward FastAPI + Streamlit

### Phase 1: Backend Extraction (v2.1, ~2 weeks)

Extract query logic into a FastAPI service; Streamlit becomes a pure UI client.

```
BEFORE (current):
  Streamlit ──→ DuckDB (in-process)
  Streamlit ──→ PyIceberg (in-process)
  Streamlit ──→ Kafka (in-process consumer)
  Streamlit ──→ Prometheus (direct HTTP)
  Streamlit ──→ Management API (direct HTTP)

AFTER (Phase 1):
  ┌──────────────────────────────────────────────────┐
  │  FastAPI Service (analytics-api)                 │
  │  ├─ GET /api/v1/kpi?days=30                      │
  │  ├─ GET /api/v1/fraud-rate?days=30               │
  │  ├─ GET /api/v1/rules/triggers?days=30           │
  │  ├─ GET /api/v1/models/compare?days=30           │
  │  ├─ GET /api/v1/models/ab/{experiment_id}        │
  │  ├─ GET /api/v1/shadow/comparison?days=7         │
  │  ├─ GET /api/v1/replay/{job_id}/results          │
  │  ├─ GET /api/v1/trace/{txn_id}                   │
  │  ├─ GET /api/v1/features/staleness               │
  │  ├─ GET /api/v1/rules                            │
  │  ├─ POST /api/v1/rules/{id}/deploy               │
  │  ├─ GET /api/v1/alerts/live?cursor=X&limit=100   │
  │  └─ GET /api/v1/dlq?limit=50                     │
  │                                                  │
  │  Owns: DuckDB pool, PyIceberg catalog,           │
  │        consumer sidecar, Prometheus client        │
  └──────────────────────────────────────────────────┘
           │
           │ HTTP/JSON
           ▼
  ┌──────────────────────────────────────────────────┐
  │  Streamlit App (analytics-ui)                    │
  │  Pure rendering — no DB connections, no Kafka    │
  │  All data via: requests.get(API_URL + path)      │
  └──────────────────────────────────────────────────┘
```

**Migration steps:**
1. Create `analytics/api/` directory with FastAPI app.
2. Move all `queries/*.py` functions into FastAPI route handlers.
3. Move consumer sidecar management into FastAPI lifespan.
4. Streamlit pages call `httpx.get(API_BASE + "/api/v1/...")`.
5. Add response caching in FastAPI with `cachetools` (TTL-based).
6. Docker Compose: `analytics-api` (FastAPI, port 8005) + `analytics-ui` (Streamlit, port 8501).

### Phase 2: Horizontal Scaling (v2.2, ~2 weeks)

- FastAPI behind gunicorn with multiple workers (handles concurrent analysts).
- Streamlit instances are stateless — can run N replicas behind a load balancer.
- Shared state moves to Redis (alert buffer, session preferences).
- DuckDB replaced by shared MotherDuck or Trino for multi-worker queries.

### Phase 3: Real-Time Push (v2.3, future)

- Replace polling with WebSocket push for Live Feed.
- FastAPI `@app.websocket("/ws/alerts")` → Streamlit `st.connection("ws")`.
- Server-Sent Events (SSE) as fallback for simpler integration.

---

## 6. Navigation Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  SIDEBAR NAVIGATION (grouped by domain)                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  🏠 Home (status dashboard)                                     │
│                                                                 │
│  ── MONITORING ──                                               │
│  ⚡ Live Feed                                                    │
│  📈 Fraud Rate                                                   │
│  📊 Analytics Insights                                           │
│                                                                 │
│  ── RULES ──                                                    │
│  🔔 Rule Triggers                                                │
│  🛡️ Shadow Rules                                                 │
│  🎛️ Rule Control Panel        ← NEW (016)                       │
│                                                                 │
│  ── MODELS ──                                                   │
│  🤖 Model Compare                                                │
│  🔬 Shadow Scoring             ← NEW (010)                      │
│  🧪 Model A/B Testing          ← NEW (012)                      │
│                                                                 │
│  ── INVESTIGATION ──                                            │
│  🔍 Trace Viewer               ← NEW (015)                      │
│  🔄 Replay Results             ← NEW (011/015)                  │
│                                                                 │
│  ── OPERATIONS ──                                               │
│  📡 Feature Staleness           ← NEW (015)                     │
│  🗑️ DLQ Inspector                                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

CROSS-PAGE DEEP LINKS:
  Live Feed alert row  ──click──→  Trace Viewer (?txn_id=X)
  Shadow Scoring disagreement ──→  Trace Viewer (?txn_id=X)
  Replay Results row  ──click──→  Trace Viewer (?txn_id=X)
  Rule Triggers rule  ──click──→  Rule Control Panel (?rule_id=X)
  Model Compare version ──click→  Model A/B (?model=X)
  Feature Staleness alert ──────→  Trace Viewer (filtered by stale feature)
```

---

## 7. Component Hierarchy

```
analytics/
├── api/                              ← NEW: FastAPI backend (Phase 1)
│   ├── __init__.py
│   ├── main.py                       # FastAPI app, lifespan, CORS
│   ├── routes/
│   │   ├── alerts.py                 # /api/v1/alerts/live
│   │   ├── fraud_rate.py
│   │   ├── kpi.py
│   │   ├── models.py                 # compare + A/B
│   │   ├── replay.py
│   │   ├── rules.py                  # CRUD + canary
│   │   ├── shadow.py
│   │   ├── staleness.py
│   │   └── trace.py
│   └── deps.py                       # DuckDB pool, Iceberg catalog DI
│
├── app/
│   ├── Home.py                       # Simplified: status only, no consumer
│   ├── widgets/                      ← EXPANDED from single file
│   │   ├── __init__.py
│   │   ├── query.py
│   │   ├── sidebar.py
│   │   ├── kpi_row.py
│   │   ├── alert_card.py
│   │   ├── decision_matrix.py
│   │   ├── waterfall.py
│   │   ├── confirmation.py
│   │   ├── data_table.py
│   │   ├── status_badge.py
│   │   └── deep_link.py
│   └── pages/
│       ├── 01_live_feed.py           # REFACTORED: fragment-based, no sleep
│       ├── 02_fraud_rate.py          # + caching
│       ├── 03_rule_triggers.py       # + deep links to Rule Control
│       ├── 04_model_compare.py       # + caching
│       ├── 05_dlq_inspector.py
│       ├── 06_shadow_rules.py        # + confirmation dialogs
│       ├── 07_analytics.py           # + caching
│       ├── 08_shadow_scoring.py      ← NEW (010)
│       ├── 09_replay_results.py      ← NEW (011/015)
│       ├── 10_trace_viewer.py        ← NEW (015)
│       ├── 11_model_ab.py            ← NEW (012)
│       ├── 12_feature_staleness.py   ← NEW (015)
│       └── 13_rule_control.py        ← NEW (016)
│
├── consumers/
│   ├── kafka_consumer.py             # Unchanged internally
│   ├── sidecar.py                    ← NEW: standalone process
│   ├── metrics.py
│   └── shared_buffer.py              ← NEW: SQLite WAL read/write
│
├── queries/
│   ├── config.py
│   ├── duckdb_runner.py              # REFACTORED: connection pool
│   ├── iceberg_reader.py             # + partition pruning, field projection
│   ├── fraud_rate.py                 # + @st.cache_data
│   ├── rule_triggers.py              # + @st.cache_data
│   ├── model_versions.py            # + @st.cache_data
│   ├── analytics_insights.py        # + @st.cache_data
│   ├── model_ab.py                   ← NEW
│   ├── replay_results.py            ← NEW
│   ├── shadow_scoring.py            ← NEW
│   ├── trace_spans.py               ← NEW
│   └── feature_staleness.py         ← NEW
│
└── DESIGN_V2.md                      ← THIS DOCUMENT
```

---

## Appendix A: Priority & Sequencing

| Priority | Item | Effort | Dependency |
|----------|------|--------|-----------|
| P0 | Consumer sidecar extraction (§3.1) | 3d | None |
| P0 | Query caching (§3.2) | 2d | None |
| P0 | DuckDB connection pool (§3.3) | 1d | None |
| P0 | Fragment-based refresh (§3.5) | 1d | Consumer sidecar |
| P1 | Trace Viewer (§2.3) | 3d | Iceberg trace_spans table |
| P1 | Shadow Scoring page (§2.1) | 3d | Shadow model deployment |
| P1 | Feature Staleness page (§2.5) | 2d | Prometheus metrics exist |
| P1 | Model A/B page (§2.4) | 3d | Experiment framework |
| P2 | Replay Results page (§2.2) | 3d | Replay engine (spec 011) |
| P2 | Rule Control Panel (§2.6) | 5d | Management API + txn.rules.config |
| P2 | Widget library extraction (§4.1) | 2d | None |
| P3 | FastAPI extraction (§5 Phase 1) | 10d | All P0/P1 done |
| P3 | Horizontal scaling (§5 Phase 2) | 10d | Phase 1 done |

---

## Appendix B: Constitution Compliance Checklist

- [x] Independent process: analytics app is read-only observer, never in scoring path
- [x] No PII caching: account_id masked before any cache/buffer storage
- [x] Non-critical-path: analytics failure does not affect transaction processing
- [x] Live feed <2s lag: consumer sidecar + fragment refresh ≤2s end-to-end
- [x] DuckDB ≤30d / Trino >30d: query modules enforce MAX_HOURS=720 for DuckDB
- [x] Rule mutations via txn.rules.config topic: Rule Control Panel publishes to Kafka, not direct DB
