# Data Model: Analytics Consumer Layer (008)

**Branch**: `008-analytics-consumer-layer`  
**Date**: 2026-04-20

---

## Runtime Entities (in-process, not persisted)

### LiveFeedBuffer

Held in `st.session_state["feed_buffer"]` per Streamlit session.

| Field | Type | Description |
|-------|------|-------------|
| `events` | `deque[FraudAlertDisplay]` | Bounded deque, maxlen=500, newest at left |
| `last_updated` | `float` | Unix timestamp of last consumer batch drain |
| `consumer_healthy` | `bool` | True if Kafka consumer thread is alive |

### FraudAlertDisplay

Deserialized from an Avro record on `txn.fraud.alerts`. PII fields pre-masked by Feast/enrichment layer; rendered as-is.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `transaction_id` | `str` | Kafka record | UUID |
| `account_id` | `str` | Kafka record | Masked by enrichment |
| `merchant_id` | `str` | Kafka record | |
| `amount` | `float` | Kafka record | |
| `currency` | `str` | Kafka record | ISO-4217 |
| `channel` | `str` | Kafka record | POS / WEB / MOBILE / API |
| `decision` | `str` | Kafka record | ALLOW / FLAG / BLOCK |
| `fraud_score` | `float` | Kafka record | 0.0 – 1.0 |
| `rule_triggers` | `list[str]` | Kafka record | May be empty list |
| `model_version` | `str` | Kafka record | |
| `event_timestamp` | `datetime` | Kafka record | Converted from epoch_ms |
| `received_at` | `datetime` | Consumer thread | Wall-clock when record was consumed |

### DLQRecord

Consumed from DLQ topics on demand. Fields depend on which stage produced the DLQ record.

| Field | Type | Notes |
|-------|------|-------|
| `source_topic` | `str` | e.g., `txn.api.dlq` |
| `error_reason` | `str` | From record headers or exception message field |
| `event_timestamp` | `datetime` | From Kafka record timestamp |
| `payload` | `dict` | Raw JSON / Avro-deserialized payload, PII-masked before display |
| `offset` | `int` | Kafka partition offset |
| `partition` | `int` | Kafka partition |

---

## Trino View Contracts (read-only, already created)

These views exist in `analytics/views/` and are deployed to Trino. The analytics app queries them via `trino-python-client`. The app MUST NOT issue DDL or DML against any Trino-accessible table.

### `iceberg.default.v_fraud_rate_daily`

Used by: `2_fraud_rate.py`

| Column | Type | Description |
|--------|------|-------------|
| `decision_date` | `DATE` | Daily partition |
| `channel` | `VARCHAR` | POS / WEB / MOBILE / API |
| `decision` | `VARCHAR` | ALLOW / FLAG / BLOCK |
| `transaction_count` | `BIGINT` | Events in this date+channel+decision cell |
| `total_amount` | `DOUBLE` | Sum of amounts |
| `avg_fraud_score` | `DOUBLE` | Average fraud score |
| `p99_latency_ms` | `DOUBLE` | Approx p99 scoring latency (APPROX_PERCENTILE) |

**Note**: This view deduplicates on `transaction_id` via `ROW_NUMBER()` before aggregation. `APPROX_PERCENTILE` is disclosed to analyst consumers as approximate.

### `iceberg.default.v_rule_triggers`

Used by: `3_rule_triggers.py`

| Column | Type | Description |
|--------|------|-------------|
| `decision_date` | `DATE` | Daily partition |
| `rule_name` | `VARCHAR` | Name of the triggered rule |
| `decision` | `VARCHAR` | ALLOW / FLAG / BLOCK |
| `trigger_count` | `BIGINT` | Times this rule fired on this date+decision |
| `avg_fraud_score` | `DOUBLE` | Average score for transactions where this rule fired |
| `median_fraud_score` | `DOUBLE` | Approx median |
| `p95_fraud_score` | `DOUBLE` | Approx p95 |

**FP rate derivation**: Computed in app layer as `trigger_count(decision=ALLOW) / total trigger_count` per rule.

### `iceberg.default.v_model_versions`

Used by: `4_model_compare.py`

| Column | Type | Description |
|--------|------|-------------|
| `model_version` | `VARCHAR` | Model version tag |
| `decision_date` | `DATE` | Daily partition |
| `decision` | `VARCHAR` | ALLOW / FLAG / BLOCK |
| `transaction_count` | `BIGINT` | |
| `avg_fraud_score` | `DOUBLE` | |
| `median_fraud_score` | `DOUBLE` | Approx median (APPROX_PERCENTILE) |
| `p95_fraud_score` | `DOUBLE` | Approx p95 |
| `avg_latency_ms` | `DOUBLE` | Average scoring latency |
| `p99_latency_ms` | `DOUBLE` | Approx p99 scoring latency |

### `iceberg.default.v_transaction_audit`

Used by: `5_dlq_inspector.py` (transaction-level lookup for cross-reference), future audit pages.

Full join of `enriched_transactions` + `fraud_decisions` on `transaction_id`. See `analytics/views/v_transaction_audit.sql` for full column list.

---

## Prometheus Metrics Emitted by Analytics Service

Exposed at `:8004/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `analytics_consumer_lag` | Gauge | `consumer_group`, `topic` | Current consumer lag in messages |
| `analytics_events_consumed_total` | Counter | `topic` | Total events consumed since startup |
| `analytics_consumer_restarts_total` | Counter | — | Number of Kafka consumer thread restarts |

---

## Prometheus Alert

**`AnalyticsConsumerLagHigh`**  
Expression: `analytics_consumer_lag{topic="txn.fraud.alerts"} > 500`  
For: 60s  
Severity: warning  
Description: Analytics consumer lag exceeds 500 events — live feed may be delayed beyond 2s SLO.

---

## Source Directory Layout

```text
analytics/
├── app/
│   ├── Home.py                  # Entry point — starts consumer thread, shared config
│   └── pages/
│       ├── 1_live_feed.py       # FR-004 to FR-008 — real-time Kafka feed
│       ├── 2_fraud_rate.py      # FR-009 to FR-012 — fraud rate by channel/merchant/geo
│       ├── 3_rule_triggers.py   # FR-013 to FR-014 — rule leaderboard (REPLACES shadow monitor)
│       ├── 4_model_compare.py   # FR-015 to FR-016 — model version comparison
│       ├── 5_dlq_inspector.py   # FR-017 to FR-019 — DLQ browser
│       └── 6_shadow_rules.py    # Renamed from existing 3_rule_triggers.py (operational tooling)
├── consumers/
│   ├── __init__.py
│   ├── kafka_consumer.py        # Background daemon thread, queue.Queue bridge to Streamlit
│   └── metrics.py               # prometheus_client metrics + HTTP server on :8004
├── queries/
│   ├── __init__.py
│   ├── fraud_rate.py            # Parameterized Trino queries wrapping v_fraud_rate_daily
│   ├── rule_triggers.py         # Parameterized Trino queries wrapping v_rule_triggers
│   └── model_versions.py        # Parameterized Trino queries wrapping v_model_versions
└── views/                       # (existing) Trino SQL view definitions
    ├── v_fraud_rate_daily.sql
    ├── v_model_versions.sql
    ├── v_rule_triggers.sql
    └── v_transaction_audit.sql

infra/
└── analytics/
    └── Dockerfile               # Streamlit service image

tests/
├── unit/
│   └── test_analytics_consumer.py   # Unit tests for consumer thread, buffer eviction, PII masking
└── integration/
    └── test_analytics_integration.py # Consumer group isolation, lag metric, live feed e2e
```
