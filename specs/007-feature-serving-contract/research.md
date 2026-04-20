# Research: Feature Serving Contract (007)

**Phase 0 Output** | Branch: `007-feature-serving-contract` | Date: 2026-04-19

---

## Decision 1: Feast Online Store Read API

**Decision**: Use `feast.FeatureStore.get_online_features()` as the sole online store read interface.

**Rationale**: Feast's `FeatureStore` class provides a backend-agnostic read API — the same call works against SQLite (local dev), Redis (production), and any other configured backend. This means the `FeatureServingClient` implementation requires no environment-specific branching. The call returns a `feast.online_response.OnlineResponse` object; `.to_dict()` yields `{feature_name: [value]}` (one-element list per feature). Missing features (cache miss) return `None` values in the dict.

**Alternatives considered**:
- Direct Redis client (`redis-py`): Requires knowledge of Feast's internal key encoding (`entity_key_serialization_version: 2`), bypasses TTL logic, and breaks backend abstraction. Rejected.
- Feast async client (not available): Feast 0.62.x has no native async API. Timeout must be enforced externally.

---

## Decision 2: Client-Side Timeout Enforcement (3ms)

**Decision**: Wrap the synchronous Feast call in `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=0.003)`. On `concurrent.futures.TimeoutError`, cancel the future and trigger the zero-value fallback path.

**Rationale**: Feast's `get_online_features()` is a blocking synchronous call. Python has no mechanism to interrupt a blocking thread, but `future.result(timeout=...)` will raise `TimeoutError` at the call site within 3ms — allowing the main thread to proceed with zero-valued features immediately. The background thread completes in its own time (Redis/SQLite response arrives) but its result is discarded. This matches the spec requirement (FR-003): a read that does not complete within 3ms MUST be cancelled immediately and treated as a fallback trigger.

**Alternatives considered**:
- `asyncio.wait_for(asyncio.to_thread(...), timeout=0.003)`: Viable in an async context; the Flink pipeline context is synchronous MapFunction callbacks, so ThreadPoolExecutor is simpler and avoids event loop management.
- `signal.alarm()`: Unix-only, not thread-safe, not suitable for Flink operator context.
- Redis `socket_timeout`: Low-level, Feast abstracts this; does not cover the case where Feast itself adds overhead above the socket read.

**Connection pool note**: The `FeatureStore` instance MUST be created once and reused across calls (not instantiated per transaction). Feast's Redis backend uses `redis-py`'s built-in connection pool. The `FeatureStore` instance should be held as an attribute of the Flink operator and initialized in `open()`.

---

## Decision 3: Zero-Value Fallback Vector

**Decision**: On timeout, unavailability, or cache miss, substitute a statically defined zero-value `FeatureVector` dataclass instance. Never merge partial results — if any field is missing, the entire vector is zeroed.

**Rationale**: Constitution Principle XI explicitly prohibits stale values. A partial merge would require tracking which fields are "fresh" vs. absent, introducing complexity and edge cases. The zero-value is safe: the ML model produces a neutral baseline score, and the rule engine still evaluates deterministic rules on raw transaction fields (spec assumption: "Zero-valued features produce a neutral low-fraud-score ML model output"). The spec (FR-004) says: "MUST NOT use stale values from any local cache or prior retrieval."

**Zero-value definitions** (from Feast schema):
- `Int64` fields → `0`
- `Float64` fields → `0.0`
- `String` fields → `""` (empty string)
- `Bool` fields → `False`

Affected fields:
- Velocity: `vel_count_1m=0`, `vel_amount_1m=0.0`, `vel_count_5m=0`, `vel_amount_5m=0.0`, `vel_count_1h=0`, `vel_amount_1h=0.0`, `vel_count_24h=0`, `vel_amount_24h=0.0`
- Geo: `geo_country=""`, `geo_city=""`, `geo_network_class=""`, `geo_confidence=0.0`
- Device: `device_first_seen=0`, `device_txn_count=0`, `device_known_fraud=False`, `prev_geo_country=""`, `prev_txn_time_ms=0`

---

## Decision 4: Fallback Reason Discrimination (FR-006)

**Decision**: Use a `FallbackReason` enum (`TIMEOUT`, `UNAVAILABLE`) as a label on the fallback log record and the `feature_store_fallback_total` Prometheus counter. The counter carries a `reason` label: `reason="timeout"` vs. `reason="unavailable"`.

**Rationale**: FR-006 mandates that the log record distinguish timeout fallback from unavailability fallback. A Prometheus label on the counter enables separate alert thresholds if needed (e.g., alert on sustained timeouts even when the store is reachable — indicates latency regression). The exception type determines reason: `concurrent.futures.TimeoutError` → `TIMEOUT`; any connection or IO error from Feast/Redis → `UNAVAILABLE`.

---

## Decision 5: Staleness Detection Mechanism

**Decision**: The staleness monitor is a Prometheus alert rule in `infra/prometheus/alerts/feature_serving.yml` operating on the `feature_materialization_lag_ms` gauge metric. The materialization pipeline (Feature 006) is responsible for updating this gauge on every successful push. The alert fires when `feature_materialization_lag_ms > 30000` (30 seconds, per constitution Principle XI) and auto-resolves when the metric drops below the threshold.

**Rationale**: The spec assumption states "staleness threshold default 5 minutes" — this refers to the *target* materialization cycle frequency, not the alert threshold. The constitution's hard bound of 30 seconds is the alerting threshold (DD-10). The spec says the threshold is configurable without deployment; this is satisfied by Prometheus alert rule editing (no code deployment required).

**Implementation**: The materialization sink (`storage/feature_store/` push path) must update a Prometheus gauge `feature_materialization_lag_ms` set to `(now_ms - last_push_ms)` on each push. The gauge is then scraped by Prometheus. If materialization stalls, the gauge value grows until it exceeds 30,000ms.

**Alternatives considered**:
- Sentinel key in Redis: Requires a separate polling process or Flink operator to read and expose as Prometheus metric. More moving parts than a single gauge updated in-process.
- Heartbeat topic in Kafka: Out of scope for this feature's read-path contract.

---

## Decision 6: Feature Serving Client Integration Point

**Decision**: The `FeatureServingClient` is invoked as the first step in the scoring pipeline's per-transaction processing chain, before rule evaluation. In `pipelines/scoring/job_extension.py`, the enriched transaction record is augmented with the feature vector before `wire_rule_evaluator()` receives it. The client is initialized in the Flink operator's `open()` method.

**Rationale**: Rule evaluation and ML scoring must receive the feature vector as part of the enriched transaction payload. Injecting it before the rule evaluator ensures the evaluator can access all feature fields without modification to its interface. The `FeatureStore` must be initialized once (not per-transaction) to reuse the connection pool.

**New file**: `pipelines/scoring/clients/feature_serving.py` — `FeatureServingClient` class with `get_features(account_id: str) -> FeatureVector` method.

---

## Decision 7: Metrics to Add

**Decision**: Add to `pipelines/scoring/metrics.py`:
1. `feature_store_fallback_total` — Counter, labels: `reason` (`timeout` | `unavailable`)
2. `feature_store_miss_total` — Counter (no labels; miss = store reachable, no entry)
3. `feature_store_retrieval_seconds` — Histogram (buckets at 0.001, 0.002, 0.003, 0.005, 0.010) for p50/p95/p99 latency (FR-014)

**Rationale**: FR-005, FR-007, FR-014 each mandate a specific observable event. The histogram with these buckets allows direct observation of the 2ms p99 SLO compliance.

---

## Decision 8: Local vs. Production Backend

**Decision**: `storage/feature_store/feature_store.yaml` remains SQLite for local development. Production Redis configuration is injected via environment variables using Feast's env-var override mechanism (`FEAST_ONLINE_STORE_TYPE`, etc.) or a separate production `feature_store.yaml`. The `FeatureServingClient` initializes `FeatureStore` with the yaml path from config (defaulting to `storage/feature_store/feature_store.yaml`).

**Rationale**: The spec (assumptions) states "In local development, the online feature store and the hot store share a single Redis instance with separate key namespaces; in production they are separate instances." SQLite for local is already configured in Feature 006. Feast supports environment-variable overrides for online store settings without requiring a separate code path.
