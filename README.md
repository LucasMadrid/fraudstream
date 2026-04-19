# FraudStream — Real-Time Fraud Detection at Scale

Stop fraud before it lands. FraudStream processes payment transactions in milliseconds — enriching every event with velocity signals, geolocation, and device fingerprints, then evaluating them against a hot-configurable rule engine — all on Apache Kafka and Flink, with full Prometheus observability, durable Iceberg analytics, and a Feast feature store out of the box.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
  - [Enrichment Pipeline](#enrichment-pipeline)
  - [Fraud Rule Families](#fraud-rule-families)
  - [Alert Persistence](#alert-persistence)
  - [Analytics Persistence Layer](#analytics-persistence-layer)
- [Services](#services)
- [Configuration](#configuration)
  - [Fraud Rules](#fraud-rules)
  - [Environment Variables](#environment-variables)
  - [Kafka Topics](#kafka-topics)
- [Management API](#management-api)
  - [Endpoints](#endpoints)
  - [Security](#security)
  - [Example](#example)
- [Observability](#observability)
  - [Key Metrics](#key-metrics)
  - [Dashboards and Alerts](#dashboards-and-alerts)
- [Make Targets](#make-targets)
  - [Infrastructure](#infrastructure)
  - [Running](#running)
  - [Testing](#testing)
- [Development](#development)
- [Project Structure](#project-structure)
- [Design Decisions & Tradeoffs](#design-decisions--tradeoffs)
  - [1. Stream Processing Runtime](#1-stream-processing-runtime--pyflink-over-java-flink-and-bytewax)
  - [2. State Backend](#2-state-backend--rocksdb--incremental-checkpoints)
  - [3. Velocity Windows](#3-velocity-windows--mapstateminute_bucket-over-sliding-event-time-windows)
  - [4. Serialisation](#4-serialisation--avro-fastavro-over-protobuf-and-json)
  - [5. Kafka Producer Guarantees](#5-kafka-producer-guarantees--acksall--idempotent)
  - [6. GeoIP Lookup](#6-geoip-lookup--embedded-maxmind-reader-over-external-api)
  - [7. Fraud Rule Configuration](#7-fraud-rule-configuration--yaml-file-over-database-or-hardcoded-rules)
  - [8. Alert Persistence](#8-alert-persistence--dual-sink-kafka--postgresql)
  - [9. Metrics Bridge](#9-metrics-bridge--daemon-consumer-threads)
  - [10. Analytics Persistence](#10-analytics-persistence--apache-iceberg--pyiceberg-over-flink-iceberg-connector)

---

## Quick Start

```bash
# 1. Install Python dependencies
pip install -e ".[processing,scoring]"

# 2. Start all infrastructure + create Kafka topics
make bootstrap

# 3. Download GeoIP database (requires MAXMIND_LICENCE_KEY)
make update-geoip

# 4. Start the analytics tier (Iceberg REST catalog + Trino)
docker compose up -d iceberg-rest trino

# 5. Initialize Iceberg tables (idempotent)
docker exec -it fraudstream-iceberg-rest bash /opt/iceberg/init-tables.sh

# 6. Register Feast feature views
cd storage/feature_store && feast apply && cd -

# 7. Start the Flink enrichment + fraud scoring job
make flink-job

# 8. Generate sample transactions (mixed 25% suspicious traffic)
make generate
```

**Prerequisites**: Docker + Docker Compose, Python 3.11, and a free [MaxMind GeoLite2 licence key](https://www.maxmind.com/en/geolite2/signup) exported as `MAXMIND_LICENCE_KEY`.

---

## Architecture

```mermaid
flowchart LR
    Client(["Client"])

    subgraph Kafka["Kafka (KRaft)"]
        direction TB
        RawTopic[["txn.api"]]
        EnrichedTopic[["txn.enriched"]]
        AlertsTopic[["txn.fraud.alerts"]]
    end

    subgraph Flink["Apache Flink (PyFlink 2.x)"]
        direction TB
        Enrichment["Enrichment\ndedup · velocity · geo · device"]
        Scoring["Fraud Rule Engine\n8 configurable rules"]
    end

    subgraph Analytics["Analytics Persistence Layer"]
        direction TB
        IcebergEnriched[("enriched_transactions\n≤5s · by event_day")]
        IcebergDecisions[("fraud_decisions\n≤5s · by transaction_id")]
        Feast["Feast\nonline + offline store"]
        Trino["Trino · SQL over Iceberg"]
    end

    PG[("PostgreSQL\nfraud_alerts")]
    Grafana["Prometheus + Grafana"]

    Client --> RawTopic
    RawTopic --> Enrichment
    Enrichment --> Scoring
    Enrichment -->|enriched| EnrichedTopic
    Enrichment --> IcebergEnriched
    Enrichment --> Feast
    Scoring -->|alerts| AlertsTopic
    Scoring --> PG
    Scoring --> IcebergDecisions

    style Flink fill:#1a1a2e,color:#eee,stroke:#4a4a8a
    style Scoring fill:#3b1f4a,color:#eee,stroke:#7a4a9a
    style Analytics fill:#0a2010,color:#eee,stroke:#2a6a3a
```

Transactions enter via Kafka (`txn.api`), are processed by the Flink job through four enrichment stages, evaluated against fraud rules, and routed to five outputs: the enriched Kafka topic, the fraud alerts topic, PostgreSQL, Iceberg (two tables), and the Feast feature store. A metrics bridge in the job process feeds Prometheus from the two Kafka topics. Trino provides a SQL interface over both Iceberg tables for analytics and audit queries without touching the scoring hot path.

---

## How It Works

### Enrichment Pipeline

The Flink job applies four stateful operators in sequence before fraud evaluation:

| # | Operator | Key | What it adds |
|---|---|---|---|
| 1 | **DeduplicationFilter** | `transaction_id` | Drops exact duplicates within 48h TTL |
| 2 | **VelocityEnrichment** | `account_id` | Count and amount windows: 1 min / 5 min / 24 h |
| 3 | **GeolocationEnrichment** | — (stateless) | Country, city, lat/lon from MaxMind GeoLite2 |
| 4 | **DeviceFingerprintEnrich** | `api_key_id` | Tracks known/new device per API key |

### Fraud Rule Families

Rules are loaded from [`rules/rules.yaml`](rules/rules.yaml) at job startup. No code change is needed to adjust thresholds.

| Rule ID | Family | Trigger condition |
|---|---|---|
| VEL-001 | velocity | ≥ 5 transactions in 1 min |
| VEL-002 | velocity | ≥ $2,000 in 5 min |
| VEL-003 | velocity | ≥ 10 transactions in 5 min |
| VEL-004 | velocity | ≥ $10,000 in 24 hours |
| IT-001 | impossible_travel | Country change within 1 hour |
| IT-002 | impossible_travel | ≥ 3 cross-border transactions |
| ND-001 | new_device | New device + amount ≥ $500 |
| ND-002 | new_device | New device + ≥ 5 transactions |

### Alert Persistence

Every flagged transaction is written to two sinks simultaneously:

- **`txn.fraud.alerts`** (Kafka, Avro, `AT_LEAST_ONCE`) — consumed by downstream systems
- **`fraud_alerts`** (PostgreSQL, `ON CONFLICT DO NOTHING`) — queryable store for fraud ops review and status lifecycle

### Analytics Persistence Layer

Every enriched transaction and every fraud decision is durably written to Apache Iceberg tables for analytics, audit, ML training, and regulatory retention (PCI-DSS 10.7, 7-year minimum). The write paths run as side outputs — they add **zero latency** to the 100ms scoring SLA.

**Three write paths**:

| Path | Source | Sink | SLA |
|---|---|---|---|
| Enrichment sink | `IcebergEnrichedSink` (side output from Flink `flat_map`) | `iceberg.enriched_transactions` | ≤ 5 s from Kafka receipt |
| Decisions sink | `IcebergDecisionsSink` (side output from scoring job) | `iceberg.fraud_decisions` | ≤ 5 s from Kafka receipt |
| Feast materialization | Feast Push API on every enrichment flush | SQLite online store + Parquet offline store | Eventual (1–2 s typical) |

**Query interface**: Trino provides ANSI SQL over both Iceberg tables, joinable on `transaction_id`:

```sql
SELECT *
FROM iceberg.v_transaction_audit
WHERE transaction_id = '<id>';
```

Four pre-built analyst views ship in `analytics/views/`:

| View | Description |
|---|---|
| `v_transaction_audit` | Full-row join of enriched features + fraud decision |
| `v_fraud_rate_daily` | Daily fraud flag rate by rule family |
| `v_rule_triggers` | Rule trigger counts by rule ID |
| `v_model_versions` | Decision distribution by model version |

**Deduplication**: Three-layer strategy — in-batch set dedup on `transaction_id`, daily Iceberg compaction (controlled by `RUN_COMPACTION=1` in `init-tables.sh`), and query-layer `ROW_NUMBER()` in Trino views as a final safety net.

**Schema evolution**: The CI pipeline (`schema-evolution.yml`) blocks merges if Avro schema changes break alignment with the Iceberg DDL or Feast feature views, detected by `scripts/evolve_iceberg_schema.py` and `scripts/validate_feast_schemas.py`.

---

## Services

| Service | URL | Credentials |
|---|---|---|
| Kafka broker | `localhost:9092` | — |
| Schema Registry | `http://localhost:8081` | — |
| Flink UI | `http://localhost:8082` | — |
| Prometheus | `http://localhost:9090` | — |
| Grafana | `http://localhost:3000` | admin / admin |
| MinIO console | `http://localhost:9001` | minioadmin / minioadmin |
| PostgreSQL | `localhost:5432` | fraudstream / fraudstream |
| Job metrics endpoint | `http://localhost:8002/metrics` | — |
| Management API | `http://localhost:8090` | `X-Api-Key` header (optional) |
| Iceberg REST catalog | `http://localhost:8181` | — |
| Trino | `http://localhost:8080` | — |

All core services start with `make bootstrap` or `make infra-up`. The analytics tier (`iceberg-rest`, `trino`) starts with `docker compose up -d iceberg-rest trino`.

---

## Configuration

### Fraud Rules

Edit [`rules/rules.yaml`](rules/rules.yaml) to change thresholds, enable/disable rules, or add new rule entries. The `RuleLoader` reads the file at job startup — threshold changes require a job restart, not a code change. An invalid YAML causes the job to refuse startup with a clear error rather than silently using defaults.

### Environment Variables

**Core pipeline**:

| Variable | Default | Description |
|---|---|---|
| `RULES_YAML_PATH` | `rules/rules.yaml` | Path to rules config |
| `MAXMIND_LICENCE_KEY` | — | Required for `make update-geoip` |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana admin password |
| `POSTGRES_PASSWORD` | `fraudstream` | PostgreSQL password |
| `FRAUD_ALERTS_DB_URL` | `postgresql://fraudstream:fraudstream@...` | PostgreSQL connection URL |
| `MANAGEMENT_API_KEY` | — | API key for management endpoints (unset = open in dev) |
| `MANAGEMENT_CORS_ORIGINS` | — | Comma-separated allowed CORS origins (disabled by default) |
| `LOG_LEVEL` | `INFO` | Job log level (`DEBUG`, `INFO`, `WARNING`) |
| `FRAUDSTREAM_ENV` | — | Set to `local` for local dev (enables MinIO defaults, SQLite Feast store) |

**Analytics persistence layer**:

| Variable | Default | Description |
|---|---|---|
| `ICEBERG_REST_URI` | `http://localhost:8181` | REST catalog endpoint |
| `ICEBERG_WAREHOUSE` | `s3://fraudstream-lake/` | MinIO bucket path for Iceberg data files |
| `AWS_S3_ENDPOINT` | `http://localhost:9000` | MinIO S3-compatible endpoint |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | MinIO credentials (local dev only — production must use secrets management) |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin` | MinIO credentials (local dev only — production must use secrets management) |
| `FEAST_REPO_PATH` | `storage/feature_store` | Feast repo root |
| `ICEBERG_FLUSH_INTERVAL_S` | `1` | Seconds between Iceberg sink flushes |
| `ICEBERG_BUFFER_MAX` | `100` | Max records per flush batch (enrichment sink) |
| `ICEBERG_DECISIONS_BUFFER_MAX` | `100` | Max records per flush batch (decisions sink) |

### Kafka Topics

| Topic | Format | Description |
|---|---|---|
| `txn.api` | Avro | Raw incoming transactions |
| `txn.api.dlq` | Avro | Schema validation failures at ingestion |
| `txn.enriched` | JSON | Velocity + geo + device enriched records |
| `txn.processing.dlq` | Avro | Processing errors from enrichment job |
| `txn.fraud.alerts` | Avro | Fraud alerts from rule engine |
| `txn.fraud.alerts.dlq` | Avro | Alert Kafka delivery failures |

---

## Management API

The fraud rule engine exposes a REST management API on **port 8090** for operational control without a job restart.

### Endpoints

| Method | Path | Rate limit | Description |
|--------|------|-----------|-------------|
| `GET` | `/healthz` | — | Health check (used by Alertmanager) |
| `GET` | `/circuit-breaker/state` | 30/min | ML circuit breaker state snapshot |
| `POST` | `/rules/{rule_id}/demote` | 10/min | Demote rule from active → shadow mode |
| `POST` | `/rules/{rule_id}/promote` | 10/min | Promote rule from shadow → active mode |

### Security

- **API key auth** — Set `MANAGEMENT_API_KEY` env var to enforce `X-Api-Key` header on all endpoints except `/healthz`. Unset means open access (dev/test only).
- **Rate limiting** — Per-IP: 10 req/min on mutating endpoints, 30 req/min on reads.
- **Security headers** — Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Strict-Transport-Security`, and `Content-Security-Policy: default-src 'none'`.
- **Input validation** — `rule_id` path parameter must be 2-64 characters matching `^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$`; invalid IDs return `422`.
- **TOCTOU guard** — Demote/promote use an asyncio lock to prevent concurrent read-modify-write races on the YAML file.

### Example

```bash
# Check circuit breaker state (no auth configured)
curl http://localhost:8090/circuit-breaker/state

# Demote a rule (with API key)
curl -X POST http://localhost:8090/rules/VEL-001/demote \
  -H "X-Api-Key: $MANAGEMENT_API_KEY"
```

Mode changes are persisted immediately to `rules/rules.yaml` and take effect in the in-memory rule set. A structured JSON audit log entry is emitted for every mode change.

---

## Observability

The Flink job exposes Prometheus metrics at `:8002/metrics`. A **Kafka metrics bridge** runs as two daemon threads inside the job process, consuming `txn.fraud.alerts` and `txn.enriched` to increment counters in the main process — necessary because PyFlink workers are JVM-spawned and cannot share Python registry objects with the HTTP server.

### Key Metrics

| Metric | Labels | Description |
|---|---|---|
| `rule_evaluations_total` | `rule_id`, `rule_family` | Transactions evaluated per rule |
| `rule_flags_total` | `rule_id`, `rule_family`, `severity` | Fraud flags raised per rule |
| `enrichment_latency_ms` | — | End-to-end enrichment latency histogram |
| `checkpoint_failures_total` | — | Flink checkpoint failures |
| `dlq_events_total` | — | Messages routed to any DLQ topic |
| `iceberg_records_written_total` | `table` | Records committed to each Iceberg table |
| `iceberg_write_errors_total` | `table` | Iceberg write failures (circuit breaker feeds from this) |
| `iceberg_flush_duration_seconds` | `table` | Flush latency histogram (p99 must stay below 4 s) |
| `feast_push_failures_total` | — | Feast Push API call failures |

### Dashboards and Alerts

- **Grafana** at `:3000` — pre-provisioned `FraudStream Pipeline` dashboard (auto-loaded from `infra/grafana/provisioning/`)
- **Prometheus alert rules** in `infra/prometheus/alerts/fraud_rule_engine.yml`:
  - `FraudFlagRateZero` — fires when `rule_flags_total` drops to zero for an extended period (possible silent evaluator failure)
  - `DLQDepthHigh` — fires when DLQ topic depth grows above threshold

Monitor `iceberg_flush_duration_seconds` p99: if it approaches 4 s, reduce `ICEBERG_FLUSH_INTERVAL_S` or scale the MinIO instance.

---

## Make Targets

### Infrastructure

| Target | Description |
|---|---|
| `make bootstrap` | One-shot setup: download JARs + infra-up + topics |
| `make infra-up` | Start all services |
| `make infra-down` | Stop services (preserves volumes) |
| `make infra-clean` | Stop services and delete all volumes |
| `make infra-ps` | Show service status |
| `make infra-logs [SERVICE=x]` | Tail logs (all services or one) |
| `make infra-restart` | Reload Prometheus + Grafana config without full teardown |
| `make topics` | Create Kafka topics and register Avro schemas |
| `make update-geoip` | Download fresh GeoLite2-City.mmdb (requires licence key) |

### Running

| Target | Description |
|---|---|
| `make flink-job` | Start the enrichment + fraud scoring Flink job |
| `make generate` | Generate mixed traffic (25% suspicious by default) |
| `make generate-suspicious` | Generate 100% suspicious transactions |
| `make simulate-alerts` | Continuous wave simulation for dashboard/alert testing |
| `make consume` | Tail `txn.enriched` without producing |

Override traffic defaults:

```bash
COUNT=100 DELAY=200 SUSPICIOUS_RATE=0.5 make generate
```

### Testing

```bash
make test              # unit tests + coverage gate (80%)
make test-unit         # same, verbose
make test-integration  # integration tests (requires Docker)
```

---

## Development

```bash
# Lint
ruff check .

# Fix import ordering
ruff check --select I001 --fix .

# Type check
mypy pipelines/

# Run a single test module
pytest tests/unit/scoring/test_evaluator.py -v

# Schema evolution validation (run before merging .avsc changes)
python scripts/evolve_iceberg_schema.py \
    --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
    --output storage/lake/schemas/enriched_transactions.sql

python scripts/validate_feast_schemas.py \
    --avsc pipelines/processing/schemas/enriched-txn-v1.avsc \
    --feast-repo storage/feature_store
```

**Coverage gate**: 80% required. The CI pipeline runs unit tests first, then integration tests against a live Docker stack. Integration tests require `make infra-up` and `make topics` before running locally. Schema evolution CI runs automatically on `.avsc` file changes via `.github/workflows/schema-evolution.yml`.

---

## Project Structure

```
pipelines/
  ingestion/                      # Feature 001 — Kafka producer
    api/                          # Producer entry point, metrics, telemetry
    shared/
      pii_masker/                 # PAN masking (BIN + last-4 + Luhn validation)
      schema_registry.py          # Schema Registry client wrapper
    schemas/                      # txn_api_v1.avsc, dlq_envelope_v1.avsc
  processing/                     # Feature 002 — Flink enrichment job
    operators/                    # VelocityEnrichment, GeolocationMapFunction,
    │                             #   DeviceProcessFunction, EnrichedRecordAssembler
    │                             #   IcebergEnrichedSink (side output → Iceberg)
    shared/
      avro_serde.py               # Avro deserialisation + schema validation
      dlq_sink.py                 # DLQ record builder
    schemas/                      # enriched-txn-v1.avsc, processing-dlq-v1.avsc
    kafka_metrics_bridge.py       # Daemon threads → Prometheus counter bridge
    job.py                        # Entry point (build_job + main)
    config.py                     # ProcessorConfig (env-driven dataclass)
    logging_config.py             # Structured JSON logging
    metrics.py                    # checkpoint_failures_total, iceberg_flush_duration_seconds
    telemetry.py                  # OpenTelemetry tracer setup
  scoring/                        # Feature 003 — Fraud rule engine
    rules/
      families/                   # velocity.py, new_device.py, impossible_travel.py
      models.py                   # Pydantic rule models (RuleConfig, RuleConditions)
      loader.py                   # RuleLoader — reads rules.yaml at startup
      evaluator.py                # FraudRuleEvaluator — stateless Flink MapFunction
    sinks/
      alert_kafka.py              # AlertKafkaSink — Avro → txn.fraud.alerts
      alert_postgres.py           # AlertPostgresSink — INSERT INTO fraud_alerts
      iceberg_decisions.py        # IcebergDecisionsSink — side output → iceberg.fraud_decisions
    schemas/                      # fraud-alert-v1.avsc, fraud-alert-dlq-v1.avsc
    types.py                      # FraudDecision dataclass (ALLOW/FLAG/BLOCK)
    metrics.py                    # rule_evaluations_total, rule_flags_total
    config.py                     # ScoringConfig (env-driven dataclass)

storage/                          # Feature 006 — Analytics persistence layer
  lake/
    schemas/                      # enriched_transactions.sql, fraud_decisions.sql (Iceberg DDL)
    catalog.properties            # PyIceberg catalog config (REST + S3)
    migrations/                   # Future DDL migrations
  feature_store/                  # Feast repository
    feature_store.yaml            # Backend config (SQLite online, Parquet offline)
    entities/                     # account.py, device.py
    features/                     # velocity_features.py, geo_features.py, device_features.py
    registry/                     # Feast metadata registry

analytics/
  views/                          # Feature 006 — Trino analyst views
    v_transaction_audit.sql       # Full audit row (enriched JOIN decisions)
    v_fraud_rate_daily.sql        # Daily fraud rate by rule family
    v_rule_triggers.sql           # Rule trigger counts by rule ID
    v_model_versions.sql          # Decision distribution by model version

rules/
  rules.yaml                      # Live rule set — edit thresholds here

scripts/
  generate_transactions.py        # Traffic generator + suspicious injection
  evolve_iceberg_schema.py        # Avro → Iceberg DDL alignment validator
  validate_feast_schemas.py       # Avro → Feast feature view alignment validator
  checkpoint-rollback.sh          # Flink checkpoint rollback helper
  flink-rolling-upgrade.sh        # Rolling upgrade script
  replay-dlq-message.sh           # DLQ replay helper

infra/
  docker-compose.yml              # Full stack: Kafka, Flink, Prometheus, Grafana,
  │                               #   MinIO, PostgreSQL, Schema Registry,
  │                               #   Iceberg REST catalog, Trino
  kafka/
    topics.sh                     # Topic creation + Avro schema registration
  flink/
    Dockerfile                    # PyFlink + scoring extras image
    flink-conf.yaml               # RocksDB, checkpoint dir, Arrow bundle config
    alerts.yaml                   # Flink-level Prometheus alert rules
  postgres/
    migrations/
      001_create_fraud_alerts.sql # fraud_alerts DDL + indexes
  iceberg/                        # Feature 006 — Iceberg REST catalog
    catalog-config.properties     # Tabular REST catalog settings
    init-tables.sh                # Idempotent DDL runner (enriched_transactions + fraud_decisions)
  trino/                          # Feature 006 — Trino query engine
    catalog/                      # Iceberg catalog connector config
  prometheus/
    prometheus.yml                # Scrape config (job :8002, Kafka exporter)
    alerts/
      fraud_rule_engine.yml       # FraudFlagRateZero + DLQDepthHigh alert rules
  grafana/
    provisioning/                 # Auto-provisioned datasource + fraud dashboard
  geoip/                          # GeoLite2-City.mmdb (gitignored; make update-geoip)

docs/
  data_catalog.yaml               # Field-level data catalog (all Iceberg + Feast columns)
  schema-evolution.md             # Schema evolution runbook

tests/
  unit/
    processing/                   # Operators, metrics bridge (330 tests total)
    scoring/                      # Rule families, evaluator, sinks, metrics
    test_pii_masker.py            # PII masking unit tests
    test_producer.py              # Producer unit tests
  integration/
    test_api_ingestion.py         # End-to-end ingestion with real Kafka
    test_enrichment_pipeline.py   # Flink operator integration tests
    test_rule_engine_pipeline.py  # Fraud scoring integration smoke test
    test_iceberg_enriched_sink.py # Iceberg enriched sink write verification
    test_iceberg_decisions_sink.py# Iceberg decisions sink write verification
    test_feast_materialization.py # Feast push + PIT feature lookup
    test_circuit_breaker.py       # Iceberg circuit breaker state transitions
    test_catalog_failure.py       # Catalog failure handling
  contract/
    test_schema_contracts.py      # Avro schema contract tests
    test_avro_iceberg_alignment.py# Avro ↔ Iceberg DDL alignment
    test_trino_views.py           # Trino analyst view integration tests
  performance/
    test_sink_hot_path_latency.py # Side output hot-path latency (5s budget)
  load/
    test_throughput.py            # Throughput benchmarks
    test_memory.py                # State memory benchmarks

specs/
  001-kafka-ingestion-pipeline/   # Spec, research, data model, plan
  002-flink-stream-processor/     # Spec, research, checklists (7 categories)
  003-fraud-rule-engine/          # Spec, plan, tasks, implementation checklists
  004-operational-excellence/     # Spec (in progress)
  006-analytics-persistence-layer/# Spec, plan, research, data-model, contracts,
                                  #   quickstart, tasks (44 tasks, all complete)

.github/
  workflows/
    ci.yml                        # Unit tests → integration tests → coverage gate
    geoip-refresh.yml             # On-demand GeoIP database refresh
    weekly-geoip-refresh.yml      # Scheduled weekly GeoIP refresh
    schema-evolution.yml          # Avro schema change gate (blocks on DDL/Feast misalignment)
```

---

## Design Decisions & Tradeoffs

### 1. Stream Processing Runtime — PyFlink over Java Flink and Bytewax

**Chose**: PyFlink 2.x DataStream API

| Alternative | Why rejected |
|---|---|
| Java Flink operators | Abandons the Python 3.11 ML toolchain; Java expertise not available on the team |
| Bytewax (Python-native) | No production-grade exactly-once guarantee in v0.x; no incremental checkpoint support |

**Tradeoff accepted**: Every `state.value()` / `state.update()` call crosses a JVM–Python boundary via Unix socket (Beam Fn API). Overhead is ~100–500 µs per record in isolation, but Arrow batching (`bundle.size=1000`, `bundle.time=15ms`) amortises this to ~10–50 µs per record at throughput.

---

### 2. State Backend — RocksDB + Incremental Checkpoints

**Chose**: `EmbeddedRocksDBStateBackend(incremental=True)` + MinIO S3-compatible storage

| Alternative | Why rejected |
|---|---|
| `HashMapStateBackend` (heap) | Velocity state for millions of accounts would exhaust JVM heap; no disk-spillover |
| Full checkpoints | At 200–800 MB state, full snapshots take 30–120s and violate the 30s checkpoint timeout |

**Tradeoff accepted**: RocksDB introduces ~2–5× read latency vs heap for `state.value()`. Mitigated by keeping hot account state in RocksDB's block cache. Incremental checkpoints require maintaining a chain of ≥ 3 snapshots — deleting intermediate ones breaks recovery.

---

### 3. Velocity Windows — `MapState<minute_bucket>` over Sliding Event-Time Windows

**Chose**: `KeyedProcessFunction` with `MapState<Int, (Long, Double)>` (minute-bucket → count, sum)

| Alternative | Why rejected |
|---|---|
| `SlidingEventTimeWindows(24h, 1min)` | Creates 1,440 panes; each event is copied into all panes — O(window/slide) memory and CPU |
| `ListState<(timestamp, amount)>` | O(N) full scan per event to filter by window boundary |

**Tradeoff accepted**: Custom watermark eviction logic required (timer per account). Late events within the allowed-lateness window are handled by re-firing `process_element` on the correct bucket.

---

### 4. Serialisation — Avro (fastavro) over Protobuf and JSON

**Chose**: Apache Avro with Confluent Schema Registry; `fastavro` (not `avro-python3`)

| Alternative | Why rejected |
|---|---|
| Protobuf | No native Confluent Schema Registry integration; requires separate descriptor management |
| JSON | No schema enforcement; 3–5× larger payloads; no evolution guarantees |
| `avro-python3` (legacy) | Deprecated; 5–10× slower than `fastavro` |
| `AvroProducer` (legacy) | Deprecated Confluent client; uses slow `avro-python3` underneath |

**Tradeoff accepted**: Avro binary requires a schema registry sidecar. Schema IDs are cached in-process after the first produce, so subsequent messages never call the registry.

---

### 5. Kafka Producer Guarantees — `acks=all` + Idempotent

**Chose**: `acks=all`, `enable.idempotence=true`, `linger.ms=5`

| Alternative | Why rejected |
|---|---|
| `acks=1` | Leader failover can lose the last unreplicated batch — a lost fraud event is a regulatory liability |
| Transactional producer | `beginTransaction()` / `commitTransaction()` adds 5–20ms per record — incompatible with 10ms p99 budget |
| `linger.ms=0` | Maximum latency spikes under load; minimal benefit at low throughput |

**Tradeoff accepted**: `acks=all` adds ~1–2ms round-trip per batch. Idempotent delivery deduplicates retried batches within one producer session by PID + sequence number — not across restarts. Cross-restart deduplication is handled by Flink's `DeduplicationFilter` with a 48h `ValueState` TTL.

---

### 6. GeoIP Lookup — Embedded MaxMind Reader over External API

**Chose**: `geoip2.database.Reader` opened once per TaskManager slot + `/24` subnet LRU cache (maxsize=10,000)

| Alternative | Why rejected |
|---|---|
| External GeoIP HTTP API | Network round-trip per transaction (~5–50ms); external dependency in hot path |
| Re-open reader per record | File descriptor exhaustion; C extension open cost is ~1–5ms |

**Tradeoff accepted**: The GeoLite2-City MMDB (~70 MB) must be distributed to every Flink TaskManager (mounted via Docker volume or baked into image). Refreshes require a job restart. Automated weekly refresh is handled by `.github/workflows/weekly-geoip-refresh.yml`.

**Lookup latency**: ~5–20 µs per lookup with the `maxminddb-c` C extension, plus ~0 µs on LRU cache hit for repeated `/24` subnets.

---

### 7. Fraud Rule Configuration — YAML File over Database or Hardcoded Rules

**Chose**: `rules/rules.yaml` loaded by `RuleLoader` at job startup; Pydantic models for validation

| Alternative | Why rejected |
|---|---|
| Hardcoded thresholds | Threshold changes require code review, test cycle, and deployment |
| Database-backed rules | Runtime dependency; adds failure mode and query latency in the hot evaluation path |
| Kafka-consumed rule updates | Hot-reload (planned as TD-001 in Phase 2) — deferred to avoid Phase 1 scope creep |

**Tradeoff accepted**: Rule changes still require a job restart. `RuleConfigError` on invalid YAML is fatal — the job refuses to start rather than silently using defaults.

---

### 8. Alert Persistence — Dual-sink (Kafka + PostgreSQL)

**Chose**: `AlertKafkaSink` (Avro, `AT_LEAST_ONCE`) + `AlertPostgresSink` (`ON CONFLICT DO NOTHING`)

| Alternative | Why rejected |
|---|---|
| Kafka only | No queryable store for fraud ops; `status` lifecycle requires a DB |
| PostgreSQL only | No downstream consumers (risk scoring, ML features) without re-publishing |
| Exactly-once Kafka delivery | `EXACTLY_ONCE` in Flink requires 2PC coordination — adds significant latency |

**Tradeoff accepted**: `AT_LEAST_ONCE` means duplicate alerts are possible on Flink recovery. `ON CONFLICT (transaction_id) DO NOTHING` absorbs duplicates at the DB level. Downstream Kafka consumers must implement their own deduplication if exactly-once alert processing is required.

---

### 9. Metrics Bridge — Daemon Consumer Threads

**Chose**: Two daemon threads in the main job process consuming `txn.fraud.alerts` and `txn.enriched`

| Alternative | Why rejected |
|---|---|
| PyFlink `MetricGroup` | Python metric objects are not shared with the main-process HTTP server — JVM workers and the Python driver have separate `prometheus_client` registries |
| `PROMETHEUS_MULTIPROC_DIR` | PyFlink workers are JVM-spawned and do not write `.db` files to the multiprocess dir |
| Push gateway | Adds a stateful middleman; aggregation semantics differ from pull-based scrape |

**Tradeoff accepted**: ~100–500ms metric lag between a rule firing and the counter being visible in Prometheus (Kafka consumer poll interval). Acceptable for operational dashboards, not suitable for sub-second alerting.

---

### 10. Analytics Persistence — Apache Iceberg + PyIceberg over Flink Iceberg Connector

**Chose**: `pyiceberg` 0.7+ with REST catalog + PyArrow table construction; side output pattern in Flink `flat_map`

| Alternative | Why rejected |
|---|---|
| Flink Iceberg connector (Java) | Requires Java UDF bridge or separate Flink job; incompatible with PyFlink-only operator graph |
| Delta Lake | No native Python writer with REST catalog; heavier Spark dependency for compaction |
| Direct S3 Parquet writes | No transactional guarantees, no schema evolution, no Trino catalog integration |
| Kafka → separate Iceberg consumer | Additional Flink job to maintain; introduces additional end-to-end latency |

**Side output pattern**: The Iceberg and Feast sinks are invoked inside a `flat_map` operator that `yield`s the enriched record **before** calling `self._iceberg_sink.invoke()`. This guarantees zero added latency on the 100ms scoring hot path regardless of Iceberg write performance.

**Circuit breaker**: `pybreaker` wraps each `table.append()` call — 3 consecutive failures open the breaker, protecting the Flink job from cascading failures if MinIO or the catalog becomes unavailable. The breaker moves to HALF_OPEN after 30s and resets on a successful write.

**Tradeoff accepted**: PyIceberg writes are synchronous within the flush call; large batches or slow MinIO I/O can increase flush latency. At `ICEBERG_FLUSH_INTERVAL_S=1` and `ICEBERG_BUFFER_MAX=100`, worst-case flush delay is just under 1 second. The 5-second budget leaves ample margin. Do not increase `ICEBERG_FLUSH_INTERVAL_S` above 4 seconds.
