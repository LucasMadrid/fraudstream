# Research: Analytics Consumer Layer (008)

**Branch**: `008-analytics-consumer-layer`  
**Date**: 2026-04-20

---

## Decision 1: Live Feed Concurrency Pattern

**Decision**: Daemon `threading.Thread` + `queue.Queue` for the Kafka consumer; `st.empty()` loop in the Streamlit page for rendering.

**Rationale**: Streamlit re-runs the entire script on each interaction. Placing the Kafka consumer inside the script would reconnect on every rerun. A daemon thread started once in `Home.py` (checked via `st.session_state`) survives reruns, drains events from the live Kafka topic, and writes batches to a thread-safe `queue.Queue` that the page reads via `st.rerun()` polling. This is the established pattern for Streamlit real-time apps.

**Alternatives considered**:
- `asyncio` event loop: Streamlit does not natively support asyncio event loops per-session; workarounds add fragility.
- Streamlit WebSocket streaming (custom component): Significant complexity for marginal latency gain — rejected.
- `streamlit-kafka` third-party lib: Unmaintained, pins old Streamlit versions — rejected.

---

## Decision 2: Historical Query Engine (Trino vs DuckDB)

**Decision**: Trino for all rolling-window queries via `trino>=0.328` (already in `analytics` extras). DuckDB (via `duckdb-iceberg` extension) for interactive Streamlit session drilldown queries and scans ≤ 7 days on the Iceberg tables, per the constitution Analytics tier guidance.

**Rationale**: Trino is already provisioned at `:8083` and has catalog configs for the Iceberg tables. The existing SQL views (`v_fraud_rate_daily`, `v_model_versions`, `v_rule_triggers`, `v_transaction_audit`) are already validated Trino SQL. DuckDB adds sub-second in-process scan capability for recent windows without the Trino container cold-start overhead in development.

**Alternatives considered**:
- Trino only: Suitable for production, but DuckDB is faster for small windows and can run without the Trino service (useful for local dev without full analytics stack). Constitution explicitly lists both.
- DuckDB only: DuckDB-Iceberg extension is local-only; no production path without Iceberg file system access — rejected as the sole engine.

---

## Decision 3: Consumer Lag Metric Exposure

**Decision**: Use `prometheus_client` `Gauge` metric (`analytics_consumer_lag`) exposed via a dedicated HTTP server thread on `:8004/metrics`. This mirrors the pipeline's metrics bridge pattern (DD-9) but runs in the analytics service process only.

**Rationale**: The analytics process is a regular Python process (not PyFlink JVM-spawned). The `PROMETHEUS_MULTIPROC_DIR` constraint from constitution §VIII does not apply here. A simple `start_http_server(8004)` call is sufficient.

**Port assignment**: `:8004` — pipeline uses `:8002`, Flink TaskManager JMX uses `:8003` (internal), avoiding collision.

**Alternatives considered**:
- Push gateway: Adds a stateful middleman; constitution §VIII lists it as prohibited in the pipeline context. Prohibited by pattern for consistency.
- Shared port `:8002/metrics`: Read-only, would mix pipeline and analytics metrics in Prometheus — rejected for isolation.

---

## Decision 4: Existing `3_rule_triggers.py` Conflict Resolution

**Decision**: Rename the existing file to `6_shadow_rules.py`. The existing Shadow Rule Monitor is operational tooling (Prometheus + Management API) that belongs in the analytics app but is misnamed. The historical rule leaderboard (spec Story 3, from Iceberg via Trino) will occupy `3_rule_triggers.py` per the constitution repository layout.

**Rationale**: The Shadow Rule Monitor delivers real value (it already exists and was built in a prior feature). It should not be deleted. Renaming to `6_shadow_rules.py` keeps it accessible in the Streamlit sidebar and avoids overwriting committed work. The historical leaderboard page is a distinct read path from a different data source.

---

## Decision 5: DLQ Consumer Access Pattern

**Decision**: The DLQ inspector uses a short-lived Kafka consumer (created per page load / per user refresh) with `auto.offset.reset = earliest`, consuming from the beginning of each DLQ topic up to a configurable max records (default: 200). No persistent consumer group offset is maintained for DLQ browsing.

**Rationale**: DLQ topics are low-volume by design. Re-reading from the beginning on each load is acceptable (200 records cap prevents memory bloat). A persistent consumer group would interfere with offset management and complicate monitoring. Per FR-002, the analytics consumer group `analytics.dashboard` is reserved for the live feed.

**Alternatives considered**:
- Persistent DLQ consumer group: Would need offset management for a purely browse-only use case — unnecessary complexity.
- Direct Kafka Admin API offset listing: Does not provide record contents — insufficient.

---

## Decision 6: Streamlit Dependency Addition

**Decision**: Add `streamlit>=1.35` and `duckdb>=0.10` to the `analytics` extras in `pyproject.toml`. Add a `pandas>=2.0` pin for DataFrame interop.

**Rationale**: These are not currently in the `analytics` extras group. `streamlit` is the required UI framework. `duckdb` enables in-process Iceberg scans. `pandas` is required by Streamlit DataFrames and Trino result sets.

---

## Decision 7: Docker Compose Integration

**Decision**: Add a `streamlit` service to `infra/docker-compose.yml` with a dedicated `Dockerfile` at `infra/analytics/Dockerfile`. The service depends on `kafka`, `trino`, and `redis` (for Feast-backed views if needed). Use a `COMPOSE_PROFILE` or named service with no profile — always started by `make analytics-up`.

**Rationale**: The Streamlit service belongs in the Analytics tier (started by `make analytics-up`, not `make bootstrap`). The constitution explicitly defines this separation. The Makefile already has `analytics-*` targets; a new `analytics-up` target will bring up Trino + Streamlit together.

---

## Decision 8: PII Masking in the Analytics Layer

**Decision**: The analytics layer displays data exactly as stored in Iceberg (`card_last4`, `ip_subnet`). No reconstruction or additional masking is applied. For the DLQ inspector, the raw Kafka payload may contain fields the enrichment stage has not yet masked — the analytics layer will apply the same masking logic as `pipelines/ingestion/shared/pii_masker/` to any pre-enrichment DLQ payload before rendering.

**Rationale**: FR-022 requires masked values to be shown as-is. FR-019 requires PII to be masked in DLQ payloads. The DLQ may contain raw ingestion events (pre-masking); the analytics layer is the last safety gate before display.
