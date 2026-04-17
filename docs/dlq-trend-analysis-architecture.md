# DLQ Trend Analysis Architecture

**Status**: Architecture Draft — Implementation pending FR-007 completion and Streamlit page scaffolding

**Epic**: FR-018 (Fraud Detection DLQ Trend Analysis)

**Blocked By**: 
- FR-007: Fraud detection runbook completion
- TD-007: Kafka consumer group metrics persistence (avoids Iceberg read complexity)

---

## Overview

The DLQ Trend Analysis job provides daily visibility into Dead Letter Queue (DLQ) health across the fraud detection pipeline. It aggregates error metrics from Kafka consumer group offsets and DLQ topic statistics to compute trending analysis, surfacing error patterns that indicate systemic issues or processing bottlenecks.

**Design Principle**: Compute from native Kafka metrics (consumer group lag, topic offset metadata) rather than persisting to Iceberg, minimizing dependencies and unblocking earlier delivery.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Fraud Detection Pipeline                     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       DLQ Topics (4)                            │
├─────────────────────────────────────────────────────────────────┤
│  txn.ingestion.dlq        │ txn.processing.dlq                  │
│  txn.scoring.dlq          │ txn.fraud.alerts.dlq                │
└─────────────────────────────────────────────────────────────────┘
         ↓                          ↓
┌──────────────────────┬──────────────────────┐
│  Kafka Consumer      │  Kafka Broker        │
│  Group Metrics       │  Topic Metadata      │
│  (lag, committed)    │  (offset, partition) │
└──────────────────────┴──────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────────────┐
│          DLQ Trend Analysis Job (Daily Batch)                    │
├──────────────────────────────────────────────────────────────────┤
│  1. Fetch consumer group metrics from Kafka cluster API          │
│  2. Read DLQ topic offsets and partition counts                 │
│  3. Extract error metadata (tags, headers) from messages         │
│  4. Compute rolling window aggregations (7-day, 30-day)         │
│  5. Persist results to in-memory cache or lightweight store     │
└──────────────────────────────────────────────────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────────────┐
│            Results: DLQ Metrics (JSON/Parquet)                   │
├──────────────────────────────────────────────────────────────────┤
│  • Volume by error type (7/30-day rolling)                       │
│  • Top-5 error types ranked by frequency                        │
│  • Error rate as % of total throughput                          │
│  • Trending indicators (increasing/stable/decreasing)           │
│  • Per-DLQ topic breakdowns                                      │
└──────────────────────────────────────────────────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────────────┐
│         Streamlit DLQ Inspector Page                             │
├──────────────────────────────────────────────────────────────────┤
│  • Reads results via direct file load or API endpoint            │
│  • Visualizes trends, top errors, rate over time                │
│  • Filters by DLQ topic, error type, time window                │
└──────────────────────────────────────────────────────────────────┘
```

---

## DLQ Topics & Error Types

### DLQ Topics

1. **txn.ingestion.dlq** — Errors during transaction ingestion (schema validation, deserialization)
2. **txn.processing.dlq** — Errors during transaction enrichment and processing
3. **txn.scoring.dlq** — Errors during fraud scoring or ML model inference
4. **txn.fraud.alerts.dlq** — Errors during alert generation or downstream notification

### Error Types

| Error Type | Description | Common Cause |
|------------|-------------|--------------|
| `schema_invalid` | Incoming message does not conform to schema | Producer misconfiguration, upstream schema change |
| `deserialization_failure` | Failed to deserialize message (JSON/Avro/Protobuf) | Corrupted message, encoding mismatch, library bug |
| `late_event_beyond_window` | Event arrived after aggregation window closed | Clock skew, network delays, producer batching lag |
| `downstream_unavailable` | Dependent service (enrichment, scoring, database) unreachable | Network partition, service degradation, dependency restart |

---

## Data Collection Strategy

### Source 1: Kafka Consumer Group Metrics

**API**: Kafka Admin Client (or KConsumer metadata API)

**Metrics Extracted**:
- `consumer_group_id` — Consumer group processing this DLQ topic
- `current_offset` — Latest committed offset (messages processed)
- `log_end_offset` — Total messages written to topic
- `lag` — `log_end_offset - current_offset` (messages unprocessed)
- `partition_count` — Number of partitions
- `fetch_timestamp` — When metrics were sampled

**Usage**:
- Lag indicates DLQ accumulation rate (backpressure signal)
- Partition count helps normalize across topics with different parallelism

### Source 2: DLQ Topic Message Sampling

**Method**: Sample recent messages from each DLQ topic (last 1000 or last 24h)

**Message Extraction**:
- Extract `error_type` from message headers or payload field (format TBD in FR-007)
- Extract `timestamp` to tag with processing date
- Extract DLQ topic name
- Optional: `upstream_topic`, `original_key` for correlation

**Processing**:
- Deserialize with error handling (skip unparseable records)
- Group by `(dlq_topic, error_type, date)`
- Count occurrences

---

## Computation Pipeline

### Step 1: Metrics Collection (Start of Job)

```python
# Pseudo-code
def collect_dlq_metrics():
    dlq_topics = [
        "txn.ingestion.dlq",
        "txn.processing.dlq", 
        "txn.scoring.dlq",
        "txn.fraud.alerts.dlq"
    ]
    
    metrics = {}
    for topic in dlq_topics:
        # Fetch consumer group lag
        group_id = f"dlq-consumer-{topic}"
        admin = KafkaAdminClient(...)
        
        group_meta = admin.describe_consumer_group(group_id)
        lag = calculate_lag(group_meta)
        
        # Sample recent messages
        consumer = KafkaConsumer(
            topic, 
            max_poll_records=1000,
            auto_offset_reset='latest'
        )
        messages = consumer.poll(timeout_ms=5000)
        
        metrics[topic] = {
            'lag': lag,
            'messages': messages,
            'timestamp': datetime.utcnow()
        }
    
    return metrics
```

### Step 2: Error Type Extraction & Aggregation

```python
def extract_error_types(metrics):
    """
    Parse message headers/payload for error_type field.
    Expected header: X-Error-Type or payload.error_type
    """
    aggregated = {}
    
    for topic, data in metrics.items():
        error_counts = defaultdict(int)
        
        for msg in data['messages']:
            try:
                error_type = extract_error_type_from_message(msg)
                error_counts[error_type] += 1
            except Exception:
                error_counts['parse_error'] += 1
        
        aggregated[topic] = error_counts
    
    return aggregated
```

### Step 3: Rolling Window Aggregation

**7-Day Rolling Window**:
- Fetch aggregated counts for past 7 days (if available in state store)
- Add today's counts
- Keep only last 7 days of data

**30-Day Rolling Window**:
- Same as 7-day, but retain 30 days

**State Storage**:
- Simple JSON file in shared mount or S3
- Schema: `{ "date": "2026-04-13", "topic": "txn.ingestion.dlq", "error_type": "schema_invalid", "count": 42 }`
- Partitioned by date for easy incremental updates

```python
def compute_rolling_windows(daily_counts, state_dir):
    """
    daily_counts: {topic: {error_type: count}}
    Returns: {topic: {error_type: {7d_count, 30d_count, trend}}}
    """
    today = date.today()
    
    # Load past 30 days
    historical = load_state_window(state_dir, days_back=30)
    historical[today] = daily_counts
    
    windows = {}
    for topic in daily_counts.keys():
        windows[topic] = {}
        for error_type in daily_counts[topic].keys():
            counts_7d = [
                historical.get(d, {}).get(topic, {}).get(error_type, 0)
                for d in date_range(today - timedelta(days=7), today)
            ]
            counts_30d = [
                historical.get(d, {}).get(topic, {}).get(error_type, 0)
                for d in date_range(today - timedelta(days=30), today)
            ]
            
            windows[topic][error_type] = {
                '7d_total': sum(counts_7d),
                '30d_total': sum(counts_30d),
                'today': daily_counts[topic][error_type],
                '7d_avg': sum(counts_7d) / 7,
                'trend': 'increasing' if counts_7d[-1] > sum(counts_7d[:-1])/6 else 'stable'
            }
    
    return windows
```

### Step 4: Top-5 Error Types Ranking

```python
def compute_top_errors(windows):
    """
    Across all DLQ topics, rank error types by 7-day volume.
    """
    all_errors = defaultdict(int)
    
    for topic, errors in windows.items():
        for error_type, metrics in errors.items():
            all_errors[error_type] += metrics['7d_total']
    
    top_5 = sorted(
        all_errors.items(),
        key=lambda x: x[1],
        reverse=True
    )[:5]
    
    return [{'error_type': e, '7d_volume': v} for e, v in top_5]
```

### Step 5: Error Rate Calculation

```python
def compute_error_rate(daily_counts, main_topic_metrics):
    """
    main_topic_metrics: throughput from main transaction topics
    (e.g., txn.transactions.ingestion, txn.transactions.scoring)
    
    Error Rate = (sum of all DLQ messages today) / (main topic throughput)
    """
    total_dlq_messages = sum(
        sum(counts.values())
        for counts in daily_counts.values()
    )
    
    # Query Prometheus or consumer group metrics for main topic throughput
    main_throughput = fetch_main_topic_throughput()
    
    error_rate_pct = (total_dlq_messages / main_throughput) * 100 \
        if main_throughput > 0 else 0
    
    return {
        'total_dlq_volume': total_dlq_messages,
        'main_throughput': main_throughput,
        'error_rate_pct': error_rate_pct
    }
```

---

## Output Format

### Primary Output: JSON Report

**Location**: `s3://pipeline-state/dlq-trends/{date}.json` or local shared mount

```json
{
  "job_date": "2026-04-13",
  "job_execution_timestamp": "2026-04-13T02:15:30Z",
  "collection_period_utc": ["2026-04-12T00:00:00Z", "2026-04-13T00:00:00Z"],
  "error_rate": {
    "total_dlq_volume": 1247,
    "main_throughput": 98456,
    "error_rate_pct": 1.27,
    "classification": "normal"
  },
  "top_5_errors": [
    {"error_type": "schema_invalid", "7d_volume": 342, "rank": 1},
    {"error_type": "deserialization_failure", "7d_volume": 198, "rank": 2},
    {"error_type": "downstream_unavailable", "7d_volume": 87, "rank": 3},
    {"error_type": "late_event_beyond_window", "7d_volume": 45, "rank": 4},
    {"error_type": "unknown_error", "7d_volume": 12, "rank": 5}
  ],
  "windows": {
    "txn.ingestion.dlq": {
      "schema_invalid": {
        "today": 45,
        "7d_total": 342,
        "30d_total": 1203,
        "7d_avg": 48.86,
        "trend": "increasing"
      },
      "deserialization_failure": {
        "today": 12,
        "7d_total": 87,
        "30d_total": 298,
        "7d_avg": 12.43,
        "trend": "stable"
      }
    },
    "txn.processing.dlq": { ... },
    "txn.scoring.dlq": { ... },
    "txn.fraud.alerts.dlq": { ... }
  },
  "per_topic_summary": {
    "txn.ingestion.dlq": {
      "total_today": 102,
      "7d_total": 645,
      "30d_total": 2156,
      "lag_messages": 0,
      "dominant_error": "schema_invalid"
    },
    "txn.processing.dlq": { ... },
    "txn.scoring.dlq": { ... },
    "txn.fraud.alerts.dlq": { ... }
  }
}
```

### Secondary Output: Streamlit-Ready Metrics

**Endpoint**: Streamlit app queries JSON directly via `pd.read_json()` or HTTP GET

**Streamlit Components** (scaffolded separately per FR-007):
- **Timeline Chart**: Error rate % over last 30 days
- **Top Errors Table**: Rank, error type, 7/30-day counts
- **Per-Topic Breakdown**: Lag and error composition by DLQ topic
- **Trending Indicators**: Up/down arrows for each error type (7-day vs 30-day)
- **Alerts**: Highlight if error rate exceeds threshold (e.g., >5%)

---

## Job Execution & Scheduling

### Execution Model

**Trigger**: Daily at 02:00 UTC (after 24h aggregation window closes)

**Duration**: ~5-10 minutes (Kafka API calls + in-memory aggregation)

**Idempotency**: Rerunning same job date overwrites previous report (safe to retry on failure)

### Implementation Technology (Pending FR-007)

- **Language**: Python 3.11 (project standard)
- **Kafka Client**: `kafka-python` or `confluent-kafka`
- **Scheduler**: Airflow DAG or cron job (TBD in runbook)
- **Output**: Parquet or JSON (Streamlit agnostic)

---

## Monitoring & Observability

### Metrics Emitted to Prometheus

- `dlq_trend_job_duration_seconds` — How long job took to run
- `dlq_trend_job_error` — Set to 1 if job failed, 0 if succeeded
- `dlq_trend_total_messages_sampled` — Messages examined across all topics
- `dlq_trend_error_rate_percent` — Computed error rate

### Logging

- Log each DLQ topic's lag at start of job (inform on backpressure)
- Log top 5 errors found in this run
- Log any messages that failed to parse (with error reason)
- Log output file location/URL

### Alerting (Proposed)

- **Alert if error rate >5%**: Possible systemic issue in pipeline
- **Alert if any DLQ lag >10k messages**: Consumer group not keeping up
- **Alert if job runtime >30 minutes**: Possible Kafka API timeout or network issue

---

## Data Retention & Cleanup

**State History**: Keep rolling window state for 30 days

**Output Reports**: Retain JSON report for 90 days (daily, so ~90 files)

**Message Sampling**: Do not persist sampled messages; re-sample daily from current DLQ topic state

**Cleanup Job**: Weekly removal of data older than retention window

---

## Dependencies & Blockers

### Blocking Issues

1. **FR-007 (Fraud Detection Runbook)** — Defines error_type format in DLQ messages
   - Must specify: header name, payload field, or default enum
   - Required to implement Step 2 (error extraction)

2. **TD-007 (Kafka Consumer Group Metrics)** — Metric persistence (avoided in this design)
   - This design computes on-demand from Kafka APIs, not pre-persisted state
   - Unblocks daily batch job while TD-007 is planned

### Streamlit Integration Blocker

3. **Streamlit Page Scaffolding** — DLQ Inspector page structure
   - Component definitions, layout, refresh cadence
   - Planned separately; this job outputs JSON that page will consume

---

## Testing Strategy

### Unit Tests

- `test_extract_error_types()` — Mock message parsing
- `test_compute_rolling_windows()` — Verify aggregation math
- `test_compute_top_errors()` — Rank correctness
- `test_error_rate_calculation()` — Division by zero handling

### Integration Tests

- Connect to staging Kafka cluster, verify consumer group fetch
- Sample messages from staging DLQ topics
- Validate JSON output schema matches expected structure
- (Requires FR-007 completion for realistic error messages)

### Manual Validation

- Run job against production DLQ topics
- Cross-check top errors with support tickets / observability
- Verify Streamlit page renders results correctly

---

## Future Enhancements

**Post-MVP** (after initial deployment):

1. **Anomaly Detection** — ML-based trending (Isolation Forest on error rate timeseries)
2. **Correlation Analysis** — Link DLQ spikes to upstream/downstream service health
3. **Error Root Cause Mapping** — Auto-classify error_type to known issues (e.g., "schema_invalid + timestamp X" = upgrade event)
4. **SLA Dashboard** — Track error rate against agreed SLOs
5. **Historical Trend Export** — Parquet export for BI/analytics teams

---

## Related Specifications

- **FR-007**: Fraud Detection Runbook (error message format, consumer group setup)
- **FR-018**: Fraud Detection DLQ Trend Analysis (this document)
- **TD-007**: Kafka Consumer Group Metrics Persistence (deferred; not required for this job)
- **Streamlit Inspector Page** (scaffolding pending, consumes this job's output)

---

## Sign-Off

**Owner**: Fraud Detection Platform Team

**Last Updated**: 2026-04-13

**Version**: 1.0 (Draft)
