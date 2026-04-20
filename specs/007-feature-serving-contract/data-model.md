# Data Model: Feature Serving Contract (007)

**Phase 1 Output** | Branch: `007-feature-serving-contract` | Date: 2026-04-19

---

## Entities

### FeatureVector

Represents the complete set of precomputed features for one account at lookup time. This is the return value of every online store read, whether populated from Feast or zero-filled on fallback/miss.

**Source**: Three Feast feature views — `velocity_features`, `geo_features`, `device_features` — joined on `account_id`.

| Field | Type | Feast Source | Zero Value | Description |
|---|---|---|---|---|
| `account_id` | `str` | Entity key | `""` | Lookup key (not returned by Feast; passed through) |
| `vel_count_1m` | `int` | `velocity_features` | `0` | Transaction count in last 1 minute |
| `vel_amount_1m` | `float` | `velocity_features` | `0.0` | Total amount in last 1 minute |
| `vel_count_5m` | `int` | `velocity_features` | `0` | Transaction count in last 5 minutes |
| `vel_amount_5m` | `float` | `velocity_features` | `0.0` | Total amount in last 5 minutes |
| `vel_count_1h` | `int` | `velocity_features` | `0` | Transaction count in last 1 hour |
| `vel_amount_1h` | `float` | `velocity_features` | `0.0` | Total amount in last 1 hour |
| `vel_count_24h` | `int` | `velocity_features` | `0` | Transaction count in last 24 hours |
| `vel_amount_24h` | `float` | `velocity_features` | `0.0` | Total amount in last 24 hours |
| `geo_country` | `str` | `geo_features` | `""` | ISO country code of last transaction |
| `geo_city` | `str` | `geo_features` | `""` | City of last transaction |
| `geo_network_class` | `str` | `geo_features` | `""` | Network classification (`residential`/`business`/`vpn`) |
| `geo_confidence` | `float` | `geo_features` | `0.0` | Geolocation confidence score [0.0, 1.0] |
| `device_first_seen` | `int` | `device_features` | `0` | Epoch ms when device first seen |
| `device_txn_count` | `int` | `device_features` | `0` | Lifetime transaction count for this device |
| `device_known_fraud` | `bool` | `device_features` | `False` | Device previously associated with confirmed fraud |
| `prev_geo_country` | `str` | `device_features` | `""` | Country of prior transaction (travel anomaly signal) |
| `prev_txn_time_ms` | `int` | `device_features` | `0` | Epoch ms of prior transaction |

**Validation rules**:
- All fields MUST be present; partial population is not permitted (FR-004)
- `geo_confidence` MUST be in `[0.0, 1.0]` when populated; zero-value is `0.0`
- `device_known_fraud` is a boolean; zero-value is `False` (not fraud)
- String zero-values are empty strings `""`, NOT `None` / `null`

---

### FallbackEvent

An observable event emitted when the scoring engine substitutes zero-valued features for a given account because the online store timed out or was unavailable.

**Storage**: Log record (structured JSON) + Prometheus counter increment.

| Field | Type | Values | Description |
|---|---|---|---|
| `account_id` | `str` | — | Account for which features were requested |
| `transaction_id` | `str` | — | Associated transaction identifier |
| `transaction_timestamp` | `int` | epoch ms | Transaction event timestamp |
| `reason` | `enum` | `TIMEOUT`, `UNAVAILABLE` | Why fallback was triggered (FR-006) |
| `elapsed_ms` | `float` | ≥ 0 | Time elapsed before fallback was triggered |
| `feature_store_endpoint` | `str` | — | Online store backend identifier (for diagnostics) |

**State transitions**:
- `TIMEOUT`: `concurrent.futures.TimeoutError` raised at 3ms deadline
- `UNAVAILABLE`: Any connection error, Redis I/O error, or Feast backend exception (non-timeout)

**Prometheus counter**: `feature_store_fallback_total{reason="timeout"|"unavailable"}`

---

### MissEvent

An observable event emitted when the online store is reachable but contains no entry for the requested account. Distinct from `FallbackEvent` — the store is healthy, the account simply has no materialized features.

**Storage**: Log record (structured JSON) + Prometheus counter increment.

| Field | Type | Description |
|---|---|---|
| `account_id` | `str` | Account with no online store entry |
| `transaction_id` | `str` | Associated transaction identifier |
| `transaction_timestamp` | `int` | Transaction event timestamp (epoch ms) |

**Detection**: Feast `get_online_features()` returns a dict where all feature values are `None` (Feast returns `None` for missing entries, not an exception).

**Prometheus counter**: `feature_store_miss_total`

---

### StalenessAlert

An operational signal emitted by Prometheus when the materialization lag exceeds the configured threshold. Not a runtime object — exists as a Prometheus alert rule and fires/clears automatically.

| Property | Value |
|---|---|
| Alert name | `FeatureStoreStalenessHigh` |
| Expression | `feature_materialization_lag_ms > 30000` |
| Threshold | 30,000 ms (30 seconds, per Constitution Principle XI) |
| For duration | `60s` (must exceed threshold for 1 full monitoring interval) |
| Auto-resolves | Yes — when `feature_materialization_lag_ms` drops below threshold |
| File | `infra/prometheus/alerts/feature_serving.yml` |

---

### StalenessMonitor (Gauge)

A Prometheus gauge updated by the materialization pipeline on every successful push. The staleness alert operates on this gauge.

| Property | Value |
|---|---|
| Metric name | `feature_materialization_lag_ms` |
| Type | Gauge |
| Updated by | Feast materialization push sink (Feature 006) |
| Value | `now_ms - last_successful_push_ms` |
| Updated on | Every successful `PushSource` write |

**Note**: This gauge must be added to the materialization pipeline (Feature 006 code) as part of this feature's implementation — the read-path alert depends on it.

---

## Entity Relationships

```
account_id (Entity key)
    │
    ├── velocity_features  (TTL: 48h)  →  8 velocity fields
    ├── geo_features       (TTL: 24h)  →  4 geolocation fields
    └── device_features    (TTL: 14d)  →  5 device fingerprint fields
                                         (total: 17 feature fields + account_id)
```

All three feature views share the same entity key (`account_id`). The `FeatureServingClient` fetches all 17 fields in a single `get_online_features()` call by requesting features from all three views simultaneously.

---

## State Transitions: FeatureVector Retrieval

```
Transaction arrives with account_id
         │
         ▼
  FeatureServingClient.get_features(account_id)
         │
         ├── [success, within 3ms, entry exists]
         │       └── return populated FeatureVector
         │
         ├── [success, within 3ms, no entry for account]
         │       └── emit MissEvent → return zero FeatureVector
         │
         ├── [TimeoutError at 3ms deadline]
         │       └── emit FallbackEvent(reason=TIMEOUT) → return zero FeatureVector
         │
         └── [ConnectionError / IOError / any exception]
                 └── emit FallbackEvent(reason=UNAVAILABLE) → return zero FeatureVector
```

---

## Prometheus Metrics Summary

| Metric | Type | Labels | Source |
|---|---|---|---|
| `feature_store_fallback_total` | Counter | `reason` | `FeatureServingClient` |
| `feature_store_miss_total` | Counter | — | `FeatureServingClient` |
| `feature_store_retrieval_seconds` | Histogram | — | `FeatureServingClient` |
| `feature_materialization_lag_ms` | Gauge | — | Materialization push sink (Feature 006) |
