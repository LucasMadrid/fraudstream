# Contract: FeatureServingClient

**File**: `pipelines/scoring/clients/feature_serving.py`  
**Feature**: 007-feature-serving-contract  
**Date**: 2026-04-19

---

## Interface Contract

### Class: `FeatureServingClient`

The sole interface between the scoring pipeline and the Feast online store. Initialized once per Flink operator instance; reused across all transactions processed by that operator.

---

### Constructor

```python
class FeatureServingClient:
    def __init__(
        self,
        feature_store_repo_path: str = "storage/feature_store",
        timeout_seconds: float = 0.003,
        executor_workers: int = 1,
    ) -> None: ...
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `feature_store_repo_path` | `str` | `"storage/feature_store"` | Path to the Feast feature store repository directory containing `feature_store.yaml` |
| `timeout_seconds` | `float` | `0.003` | Client-side timeout in seconds. MUST default to 0.003 (3ms). Constitution Principle XI mandate. |
| `executor_workers` | `int` | `1` | Thread pool size for wrapping the synchronous Feast call |

**Post-construction state**: `FeatureStore` is not yet initialized. Initialization is deferred to `open()` to comply with Flink operator lifecycle (serialization safety).

---

### `open() -> None`

```python
def open(self) -> None: ...
```

Called once when the Flink operator is opened. Initializes the `FeatureStore` instance and `ThreadPoolExecutor`. After this call, `get_features()` MUST be safe to call.

**Side effects**:
- Creates `feast.FeatureStore(repo_path=self._repo_path)`
- Creates `concurrent.futures.ThreadPoolExecutor(max_workers=self._executor_workers)`

**Failure behavior**: If `FeatureStore` initialization fails (e.g., missing `feature_store.yaml`), the exception propagates and the Flink job fails to start. This is intentional — a misconfigured feature store is a startup error, not a runtime fallback scenario.

---

### `get_features(account_id: str, transaction_id: str, transaction_timestamp: int) -> FeatureVector`

```python
def get_features(
    self,
    account_id: str,
    transaction_id: str,
    transaction_timestamp: int,
) -> FeatureVector: ...
```

Retrieves the feature vector for a given account from the online store within the configured timeout.

| Parameter | Type | Description |
|---|---|---|
| `account_id` | `str` | Account identifier — Feast entity key |
| `transaction_id` | `str` | Transaction ID for logging/metrics context |
| `transaction_timestamp` | `int` | Transaction event timestamp (epoch ms) for logging |

**Return value**: Always returns a `FeatureVector`. Never raises an exception to the caller. The caller cannot distinguish a populated vector from a zero-value vector by return type alone — observability is via metrics and logs.

**Behavior by outcome**:

| Outcome | Return | Side effects |
|---|---|---|
| Success (entry exists, within timeout) | Populated `FeatureVector` | Records `feature_store_retrieval_seconds` histogram |
| Miss (entry not found, store reachable, within timeout) | Zero `FeatureVector` | Records histogram; increments `feature_store_miss_total`; logs `feature_store_miss` event with `account_id` and `transaction_timestamp` |
| Timeout (`TimeoutError` at deadline) | Zero `FeatureVector` | Records histogram (capped at `timeout_seconds`); increments `feature_store_fallback_total{reason="timeout"}`; logs fallback event with `reason=TIMEOUT` |
| Unavailable (any other exception) | Zero `FeatureVector` | Records histogram; increments `feature_store_fallback_total{reason="unavailable"}`; logs fallback event with `reason=UNAVAILABLE` |

**Latency contract**: The method MUST return within `timeout_seconds + ε` regardless of online store state. The ThreadPoolExecutor future is submitted; `future.result(timeout=self._timeout_seconds)` enforces the deadline.

**Thread safety**: This method is called from the Flink operator thread. The `ThreadPoolExecutor` handles the blocking Feast call in a worker thread. The method itself is not thread-safe for concurrent calls — Flink operators are single-threaded per operator chain.

---

### `close() -> None`

```python
def close(self) -> None: ...
```

Called when the Flink operator is closed. Shuts down the `ThreadPoolExecutor` gracefully.

**Side effects**:
- `self._executor.shutdown(wait=False)` — does not wait for in-flight calls to complete (consistent with Flink operator teardown semantics)

---

## Feature Request Specification

The `get_online_features()` call MUST request features from all three feature views simultaneously:

```python
features = [
    "velocity_features:vel_count_1m",
    "velocity_features:vel_amount_1m",
    "velocity_features:vel_count_5m",
    "velocity_features:vel_amount_5m",
    "velocity_features:vel_count_1h",
    "velocity_features:vel_amount_1h",
    "velocity_features:vel_count_24h",
    "velocity_features:vel_amount_24h",
    "geo_features:geo_country",
    "geo_features:geo_city",
    "geo_features:geo_network_class",
    "geo_features:geo_confidence",
    "device_features:device_first_seen",
    "device_features:device_txn_count",
    "device_features:device_known_fraud",
    "device_features:prev_geo_country",
    "device_features:prev_txn_time_ms",
]
entity_rows = [{"account_id": account_id}]
response = store.get_online_features(features=features, entity_rows=entity_rows)
```

**Cache miss detection**: The response dict contains `None` for every feature if the account has no entry. A miss is detected when ALL feature values for the account are `None`. Partial `None` values (some features present, some absent) are treated as a full miss — the entire vector is zeroed (spec assumption: "Partial vector responses are treated as full zero-value fallbacks for all missing fields").

---

## Zero FeatureVector Constant

The implementation MUST define a module-level constant for the zero-value vector to avoid repeated instantiation:

```python
ZERO_FEATURE_VECTOR = FeatureVector(
    account_id="",
    vel_count_1m=0, vel_amount_1m=0.0,
    vel_count_5m=0, vel_amount_5m=0.0,
    vel_count_1h=0, vel_amount_1h=0.0,
    vel_count_24h=0, vel_amount_24h=0.0,
    geo_country="", geo_city="", geo_network_class="", geo_confidence=0.0,
    device_first_seen=0, device_txn_count=0, device_known_fraud=False,
    prev_geo_country="", prev_txn_time_ms=0,
)
```

The `account_id` field in the returned zero vector MUST be set to the requested `account_id` (not empty string) so callers can log the actual account.

---

## Staleness Alert Rule Contract

**File**: `infra/prometheus/alerts/feature_serving.yml`

```yaml
groups:
  - name: feature_serving
    rules:
      - alert: FeatureStoreStalenessHigh
        expr: feature_materialization_lag_ms > 30000
        for: 60s
        labels:
          severity: critical
          team: fraud-platform
        annotations:
          summary: "Feature store materialization lag exceeds 30 seconds"
          description: >
            The feature_materialization_lag_ms gauge has exceeded 30,000 ms for
            60 consecutive seconds, indicating the Feast materialization pipeline
            has stalled. Feature vectors in the online store may be stale.
            Scoring engine is operating on potentially outdated features.
          runbook_url: "docs/runbooks/OBSERVABILITY_RUNBOOK.md"
```

**Auto-resolve behavior**: Prometheus resolves the alert automatically when `feature_materialization_lag_ms` drops below 30,000 ms for one full evaluation interval. No manual intervention required (FR-012).

---

## Log Record Formats

### feature_store_miss

```json
{
  "event": "feature_store_miss",
  "account_id": "<account_id>",
  "transaction_id": "<transaction_id>",
  "transaction_timestamp": <epoch_ms>
}
```

### feature_store_fallback

```json
{
  "event": "feature_store_fallback",
  "account_id": "<account_id>",
  "transaction_id": "<transaction_id>",
  "transaction_timestamp": <epoch_ms>,
  "reason": "timeout|unavailable",
  "elapsed_ms": <float>
}
```

Both records MUST be emitted at `WARNING` level via the standard Python logger (`logging.getLogger("feature_serving")`).
