# Quickstart: Feature Serving Contract (007)

**Phase 1 Output** | Branch: `007-feature-serving-contract` | Date: 2026-04-19

---

## Overview

This document covers four integration scenarios for the `FeatureServingClient`:

1. **Happy path** — populated store, feature retrieval within 2ms p99
2. **Cold-start / cache miss** — store is up but empty (or no entry for account)
3. **Store outage / timeout** — store unavailable or exceeds 3ms timeout

Each scenario includes a seed procedure and the observable output to verify.

---

## Prerequisites

```bash
# Start local stack (Redis + Feast SQLite)
make up

# Install dependencies
uv pip install --python .venv -e ".[dev]"

# Activate virtualenv
source .venv/bin/activate
```

The `feature_store.yaml` uses SQLite online store locally (`storage/feature_store/online_store.db`). All scenarios work against SQLite. Production Redis path is tested in integration tests.

---

## Scenario 1: Happy Path — Populated Store

### Seed

Materialize features for a test account by pushing directly via Feast:

```python
from feast import FeatureStore
import pandas as pd

store = FeatureStore(repo_path="storage/feature_store")

# Push velocity features
store.push("velocity_push_source", pd.DataFrame([{
    "account_id": "acct-test-001",
    "event_timestamp": pd.Timestamp.now(tz="UTC"),
    "vel_count_1m": 3,
    "vel_amount_1m": 150.0,
    "vel_count_5m": 8,
    "vel_amount_5m": 420.0,
    "vel_count_1h": 15,
    "vel_amount_1h": 900.0,
    "vel_count_24h": 42,
    "vel_amount_24h": 3200.0,
}]))

# Push geo features
store.push("geo_push_source", pd.DataFrame([{
    "account_id": "acct-test-001",
    "event_timestamp": pd.Timestamp.now(tz="UTC"),
    "geo_country": "US",
    "geo_city": "New York",
    "geo_network_class": "residential",
    "geo_confidence": 0.92,
}]))

# Push device features
store.push("device_push_source", pd.DataFrame([{
    "account_id": "acct-test-001",
    "event_timestamp": pd.Timestamp.now(tz="UTC"),
    "device_first_seen": 1700000000000,
    "device_txn_count": 37,
    "device_known_fraud": False,
    "prev_geo_country": "US",
    "prev_txn_time_ms": 1700000100000,
}]))
```

### Invoke

```python
from pipelines.scoring.clients.feature_serving import FeatureServingClient

client = FeatureServingClient()
client.open()

vector = client.get_features(
    account_id="acct-test-001",
    transaction_id="txn-quickstart-001",
    transaction_timestamp=1700000200000,
)
client.close()

print(vector)
```

### Expected Output

```text
FeatureVector(
    account_id='acct-test-001',
    vel_count_1m=3, vel_amount_1m=150.0,
    vel_count_5m=8, vel_amount_5m=420.0,
    vel_count_1h=15, vel_amount_1h=900.0,
    vel_count_24h=42, vel_amount_24h=3200.0,
    geo_country='US', geo_city='New York',
    geo_network_class='residential', geo_confidence=0.92,
    device_first_seen=1700000000000, device_txn_count=37,
    device_known_fraud=False,
    prev_geo_country='US', prev_txn_time_ms=1700000100000,
)
```

**Verifications**:
- `feature_store_retrieval_seconds` histogram increments (p99 should be < 0.002)
- No `feature_store_fallback_total` increment
- No `feature_store_miss_total` increment
- No WARNING log lines

---

## Scenario 2: Cold-Start / Cache Miss

### Seed

No setup required — use an account ID that has never been materialized, or delete the SQLite database:

```bash
rm -f storage/feature_store/online_store.db
```

### Invoke

```python
from pipelines.scoring.clients.feature_serving import FeatureServingClient
import logging
logging.basicConfig(level=logging.DEBUG)

client = FeatureServingClient()
client.open()

vector = client.get_features(
    account_id="acct-new-999",
    transaction_id="txn-quickstart-002",
    transaction_timestamp=1700000300000,
)
client.close()

print(vector)
```

### Expected Output

```text
FeatureVector(
    account_id='acct-new-999',
    vel_count_1m=0, vel_amount_1m=0.0,
    vel_count_5m=0, vel_amount_5m=0.0,
    vel_count_1h=0, vel_amount_1h=0.0,
    vel_count_24h=0, vel_amount_24h=0.0,
    geo_country='', geo_city='',
    geo_network_class='', geo_confidence=0.0,
    device_first_seen=0, device_txn_count=0,
    device_known_fraud=False,
    prev_geo_country='', prev_txn_time_ms=0,
)
```

**Expected log line** (WARNING level):

```json
{"event": "feature_store_miss", "account_id": "acct-new-999", "transaction_id": "txn-quickstart-002", "transaction_timestamp": 1700000300000}
```

**Verifications**:
- `feature_store_miss_total` counter increments by 1
- `feature_store_retrieval_seconds` histogram increments (low latency — SQLite miss is fast)
- No `feature_store_fallback_total` increment (miss ≠ fallback)
- No scoring error raised

---

## Scenario 3: Store Unavailable / Timeout Fallback

### Simulate Unavailability

Remove the SQLite database file AND patch the client to use a non-existent path to force an I/O error:

```python
from unittest.mock import patch
from concurrent.futures import TimeoutError as FuturesTimeoutError
import logging
logging.basicConfig(level=logging.DEBUG)

from pipelines.scoring.clients.feature_serving import FeatureServingClient

# Simulate a store that always raises ConnectionError
with patch(
    "pipelines.scoring.clients.feature_serving.FeatureServingClient._fetch_from_store",
    side_effect=ConnectionError("Redis connection refused"),
):
    client = FeatureServingClient()
    client.open()

    vector = client.get_features(
        account_id="acct-test-001",
        transaction_id="txn-quickstart-003",
        transaction_timestamp=1700000400000,
    )
    client.close()

print(vector)
```

### Expected Output

```text
FeatureVector(
    account_id='acct-test-001',
    vel_count_1m=0, vel_amount_1m=0.0,
    ... (all zeros)
)
```

**Expected log line** (WARNING level):

```json
{"event": "feature_store_fallback", "account_id": "acct-test-001", "transaction_id": "txn-quickstart-003", "transaction_timestamp": 1700000400000, "reason": "unavailable", "elapsed_ms": 0.4}
```

**Verifications**:
- `feature_store_fallback_total{reason="unavailable"}` increments by 1
- `feature_store_retrieval_seconds` histogram increments
- No exception propagated to caller
- Scoring pipeline can proceed to rule evaluation with zero-value vector

---

## Scenario 4: Timeout Simulation

```python
import time
from unittest.mock import patch

from pipelines.scoring.clients.feature_serving import FeatureServingClient

def slow_fetch(*args, **kwargs):
    time.sleep(0.010)  # 10ms — exceeds 3ms timeout

with patch(
    "pipelines.scoring.clients.feature_serving.FeatureServingClient._fetch_from_store",
    side_effect=slow_fetch,
):
    client = FeatureServingClient(timeout_seconds=0.003)
    client.open()

    t0 = time.monotonic()
    vector = client.get_features(
        account_id="acct-test-001",
        transaction_id="txn-quickstart-004",
        transaction_timestamp=1700000500000,
    )
    elapsed = time.monotonic() - t0
    client.close()

assert elapsed < 0.005, f"Client took {elapsed*1000:.2f}ms — timeout not enforced"
print(f"get_features() returned in {elapsed*1000:.2f}ms (within timeout budget)")
print(vector)
```

**Expected log line** (WARNING level):

```json
{"event": "feature_store_fallback", "account_id": "acct-test-001", "transaction_id": "txn-quickstart-004", "transaction_timestamp": 1700000500000, "reason": "timeout", "elapsed_ms": 3.1}
```

**Verifications**:
- `feature_store_fallback_total{reason="timeout"}` increments by 1
- `elapsed` < 5ms (3ms timeout + margin)
- Zero-value vector returned

---

## Running the Integration Test Suite

```bash
# Run feature serving tests only
pytest tests/integration/test_feature_serving.py -v

# Run with metrics output
pytest tests/integration/test_feature_serving.py -v -s 2>&1 | grep -E "feature_store|PASS|FAIL"
```

---

## Staleness Alert Test

To verify the Prometheus staleness alert fires:

1. Start the stack: `make up`
2. Let materialization run normally (alert should be silent)
3. Stop the materialization pipeline or freeze the push source
4. Wait 60 seconds
5. Check Prometheus UI at `http://localhost:9090/alerts` for `FeatureStoreStalenessHigh`
6. Resume materialization — alert should clear within the next evaluation interval (60s)
