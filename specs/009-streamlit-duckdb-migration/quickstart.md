# Quickstart: Streamlit DuckDB Migration

## Integration Test Scenarios

These scenarios verify the feature against a running Iceberg catalog and MinIO, but with Trino stopped.

### Scenario 1 — Fraud Rate page loads without Trino (SC-001)

**Setup**:
1. Start: `docker compose up -d minio iceberg-rest streamlit`
2. Ensure Trino is NOT running: `docker compose stop trino` (or never started)
3. Seed at least one row in `iceberg.enriched_transactions` and `iceberg.fraud_decisions` within the last 24 hours

**Trigger**: Open `http://localhost:8501/fraud_rate` in a browser

**Expected**: Chart renders with data; no "Failed to resolve 'trino'" error; page loads within 5 seconds

**Verify via pytest**:
```python
from analytics.queries.fraud_rate import fraud_rate_daily
df = fraud_rate_daily(hours=24)
assert df is not None
assert isinstance(df, pd.DataFrame)
```

---

### Scenario 2 — Rule Triggers page loads without Trino (SC-001)

**Setup**: Same as Scenario 1; seed a `fraud_decisions` row with a non-empty `rule_triggers` list

**Trigger**: Open `http://localhost:8501/rule_triggers`

**Expected**: Leaderboard renders; `rule_name` column populated; no Trino error

---

### Scenario 3 — Model Compare page loads without Trino (SC-001)

**Setup**: Same as Scenario 1; seed `fraud_decisions` rows with at least one distinct `model_version`

**Trigger**: Open `http://localhost:8501/model_compare`

**Expected**: Score distribution chart renders; model version in legend; no Trino error

---

### Scenario 4 — Empty state (FR-004)

**Setup**: Query a time window with no data (e.g., `hours=1` when all seeded data is older than 1 hour)

**Expected**: Page shows an empty-state message (e.g., "No data in the selected time window"), not an unhandled exception

**Verify via pytest**:
```python
from analytics.queries.fraud_rate import fraud_rate_daily
df = fraud_rate_daily(hours=0)  # impossible window — always empty
assert len(df) == 0
```

---

### Scenario 5 — 30-day cap (FR-003)

**Verify via pytest**:
```python
from analytics.queries.config import MAX_HOURS
assert MAX_HOURS == 720

from analytics.queries.fraud_rate import fraud_rate_daily
# Passing hours > 720 should clamp silently, not raise
df = fraud_rate_daily(hours=9999)
assert isinstance(df, pd.DataFrame)
```

---

### Scenario 6 — Catalog unreachable (edge case from spec)

**Setup**: Stop MinIO: `docker compose stop minio`

**Trigger**: Open any historical page

**Expected**: Page shows a user-facing error message (e.g., "Unable to connect to data store"); does NOT crash silently or show a Python traceback in the Streamlit UI

---

### Scenario 7 — Live feed and DLQ pages unaffected (FR-008, SC-004)

**Setup**: Normal running stack

**Trigger**: Open `http://localhost:8501/live_feed` and `http://localhost:8501/dlq_inspector`

**Expected**: Both pages load normally; no regressions; existing unit tests pass unchanged

---

## Local Development

```bash
# Install DuckDB into the project venv
uv pip install --python .venv duckdb>=1.0

# Set PyIceberg catalog env vars (mirrors docker-compose streamlit service)
export PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181
export PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/
export PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000
export PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin

# Run unit tests
cd /path/to/fraudstream && pytest tests/unit/test_query_config.py -v

# Run integration smoke test (requires running minio + iceberg-rest)
pytest tests/integration/test_duckdb_queries.py -v
```

## Environment Variables (docker-compose streamlit service)

These must be present in `infra/docker-compose.yml` under the `streamlit` service `environment:` section:

```yaml
PYICEBERG_CATALOG__ICEBERG__URI: http://iceberg-rest:8181
PYICEBERG_CATALOG__ICEBERG__WAREHOUSE: s3://fraudstream-lake/
PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT: http://minio:9000
PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS: "true"
```
