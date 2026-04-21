# Quickstart: Analytics Consumer Layer (008)

**Branch**: `008-analytics-consumer-layer`

---

## Prerequisites

The Analytics tier depends on the Core tier being healthy. Start Core first:

```bash
make bootstrap          # Kafka, Schema Registry, Flink, Redis, Postgres, Prometheus
make flink-job-analytics  # Start enrichment job with Iceberg + Feast side-outputs
```

---

## Start the Analytics Service

```bash
make analytics-up       # Trino + Streamlit (adds to the running Core stack)
```

The Streamlit app will be available at **http://localhost:8501**.

---

## Pages

| URL | Page | Data Source |
|-----|------|-------------|
| `/` (Home) | Overview + health indicators | Kafka consumer status, Prometheus |
| `/1_live_feed` | Real-time fraud alert feed | `txn.fraud.alerts` (Kafka) |
| `/2_fraud_rate` | Fraud rate by channel / merchant / geo | Trino → `v_fraud_rate_daily` |
| `/3_rule_triggers` | Rule leaderboard + false-positive rates | Trino → `v_rule_triggers` |
| `/4_model_compare` | Fraud score distribution per model version | Trino → `v_model_versions` |
| `/5_dlq_inspector` | Dead letter queue browser | DLQ Kafka topics |
| `/6_shadow_rules` | Shadow rule monitor + promote/demote | Prometheus + Management API |

---

## Verify Consumer Group Isolation

Confirm the analytics consumer group does not interfere with pipeline offsets:

```bash
# Check consumer group offsets — analytics.dashboard must be separate
docker exec fraudstream-kafka kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --describe --group analytics.dashboard

# Compare with pipeline consumer groups
docker exec fraudstream-kafka kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --describe --group flink-scoring-job
```

---

## Check Analytics Metrics

Consumer lag and health metrics are exposed separately from the pipeline:

```bash
curl http://localhost:8004/metrics | grep analytics_consumer
```

Expected output (after consuming some events):
```
analytics_consumer_lag{consumer_group="analytics.dashboard",topic="txn.fraud.alerts"} 0.0
analytics_events_consumed_total{topic="txn.fraud.alerts"} 42.0
analytics_consumer_restarts_total 0.0
```

---

## Generate Test Traffic

Use the existing traffic generator to populate the live feed and Iceberg tables:

```bash
python scripts/generate_transactions.py --count 100 --inject-suspicious 10
```

Events will appear in the live feed within 2 seconds and in Trino historical views after the next Iceberg commit (within ~60 seconds).

---

## Stop Analytics Tier

```bash
make analytics-down     # Stops Trino + Streamlit only; Core tier remains running
```

---

## Troubleshooting

**Live feed not updating**:
- Check consumer thread health on the Home page status indicator.
- Verify `txn.fraud.alerts` topic has recent messages: `docker exec fraudstream-kafka kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic txn.fraud.alerts --from-beginning --max-messages 3`

**Trino queries failing**:
- Confirm Trino is healthy: `curl http://localhost:8083/v1/info`
- Confirm Iceberg tables have data: `make analytics-counts`

**DLQ inspector empty**:
- DLQ topics may have no records under normal operation. Use `make test-dlq` (to be added) or publish a deliberately malformed event to trigger DLQ population.

**Consumer lag alert firing**:
- Check `http://localhost:8004/metrics` for `analytics_consumer_lag`.
- If Streamlit is overloaded, reduce polling frequency in `analytics/consumers/kafka_consumer.py` (BATCH_POLL_TIMEOUT_MS).
