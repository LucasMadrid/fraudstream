# Data Model: Streamlit DuckDB Migration

## Source Tables (read-only, Iceberg on MinIO)

### `iceberg.enriched_transactions`

Partition: `days(event_time)` — enables day-level partition pruning for rolling window queries.

| Field | Iceberg Type | Used By |
|-------|-------------|---------|
| `transaction_id` | STRING NOT NULL | — (identifier) |
| `account_id` | STRING NOT NULL | fraud_rate aggregations |
| `merchant_id` | STRING NOT NULL | — |
| `amount` | DECIMAL(18,4) NOT NULL | fraud_rate totals |
| `currency` | STRING NOT NULL | — |
| `event_time` | TIMESTAMP(6) NOT NULL | **partition column** — all queries |
| `processing_time` | TIMESTAMP(6) NOT NULL | — |
| `channel` | STRING NOT NULL | fraud_rate by channel |
| `card_bin` | STRING | — |
| `card_last4` | STRING | — |
| `caller_ip_subnet` | STRING | — |
| `geo_lat` | FLOAT (nullable) | — |
| `geo_lon` | FLOAT (nullable) | — |
| `geo_country` | STRING (nullable) | — |
| `geo_city` | STRING (nullable) | — |
| `vel_count_1m` | INT | — |
| `vel_amount_1m` | DECIMAL(18,4) | — |
| `vel_count_24h` | INT | — |
| `vel_amount_24h` | DECIMAL(18,4) | — |
| `processor_version` | STRING | — |
| `schema_version` | STRING | — |

**Iceberg scan filter** (fraud_rate queries):
```python
GreaterThanOrEqual("event_time", start_ms)
```
where `start_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)`

### `iceberg.fraud_decisions`

Partition: `days(decision_time_ms)` inferred from write pattern. Query filters on `decision_time_ms`.

| Field | Iceberg Type | Used By |
|-------|-------------|---------|
| `transaction_id` | STRING NOT NULL | — (identifier) |
| `decision` | STRING NOT NULL (`ALLOW`/`FLAG`/`BLOCK`) | fraud_rate, rule_triggers, model_compare |
| `fraud_score` | FLOAT NOT NULL [0.0, 1.0] | fraud_rate avg score, model_compare distribution |
| `rule_triggers` | LIST<STRING> | rule_triggers leaderboard |
| `model_version` | STRING NOT NULL | model_compare by version |
| `decision_time_ms` | BIGINT NOT NULL (epoch ms) | **partition filter** — all queries |
| `latency_ms` | FLOAT | — |
| `schema_version` | STRING | — |

**Iceberg scan filter** (decisions queries):
```python
GreaterThanOrEqual("decision_time_ms", start_ms)
```

## DuckDB In-Memory Views

Each query function follows this pattern:

```python
import duckdb
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual

def _query(hours: int) -> pd.DataFrame:
    hours = min(hours, MAX_HOURS)
    start_ms = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
    
    catalog = load_catalog("iceberg")
    table = catalog.load_table("default.fraud_decisions")
    arrow_tbl = table.scan(
        row_filter=GreaterThanOrEqual("decision_time_ms", start_ms)
    ).to_arrow()
    
    if arrow_tbl.num_rows == 0:
        return pd.DataFrame(columns=[...])
    
    conn = duckdb.connect()
    conn.register("tbl", arrow_tbl)
    return conn.execute("SELECT ... FROM tbl").df()
```

No DuckDB tables are persisted. All views exist only for the duration of one function call.

## Query Output Schemas

### fraud_rate functions

**`fraud_rate_daily(hours)`** returns:

| Column | Type |
|--------|------|
| `decision_date` | date (truncated from decision_time_ms) |
| `channel` | str |
| `decision` | str (ALLOW/FLAG/BLOCK) |
| `transaction_count` | int64 |
| `total_amount` | float64 |
| `avg_fraud_score` | float64 |

**`fraud_rate_by_channel(hours)`** returns:

| Column | Type |
|--------|------|
| `channel` | str |
| `decision` | str |
| `total_txns` | int64 |
| `total_amount` | float64 |
| `avg_score` | float64 |

### rule_triggers functions

**`rule_leaderboard(hours, top_n)`** returns:

| Column | Type |
|--------|------|
| `rule_name` | str |
| `total_triggers` | int64 |
| `avg_score` | float64 |
| `p95_score` | float64 |

Note: `rule_triggers` is a `LIST<STRING>` in Iceberg/Arrow. DuckDB `UNNEST(rule_triggers)` expands each list to individual rows before aggregation.

**`rule_trigger_daily(rule_name, hours)`** returns:

| Column | Type |
|--------|------|
| `decision_date` | date |
| `decision` | str |
| `trigger_count` | int64 |
| `avg_fraud_score` | float64 |

### model_versions functions

**`model_version_summary(hours)`** returns:

| Column | Type |
|--------|------|
| `model_version` | str |
| `total_txns` | int64 |
| `avg_score` | float64 |
| `median_score` | float64 |
| `p95_score` | float64 |
| `avg_latency_ms` | float64 |

**`model_version_daily(model_version, hours)`** returns:

| Column | Type |
|--------|------|
| `decision_date` | date |
| `decision` | str |
| `transaction_count` | int64 |
| `avg_fraud_score` | float64 |
| `avg_latency_ms` | float64 |

## Config Module

**`analytics/queries/config.py`**

```python
MAX_HOURS: int = 720  # 30-day rolling window cap
```

All query functions clamp their `hours` argument: `hours = min(hours, MAX_HOURS)`.
