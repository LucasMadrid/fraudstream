# FraudStream — Real-Time Fraud Detection at Scale

Stop fraud before it lands. FraudStream processes payment transactions in milliseconds — enriching every event with velocity signals, geolocation, and device fingerprints, then evaluating them against a hot-configurable rule engine — all on Apache Kafka and Flink, with full Prometheus observability out of the box.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
  - [Enrichment Pipeline](#enrichment-pipeline)
  - [Fraud Rule Families](#fraud-rule-families)
  - [Alert Persistence](#alert-persistence)
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

---

## Quick Start

```bash
# 1. Install Python dependencies
pip install -e ".[processing,scoring]"

# 2. Start all infrastructure + create Kafka topics
make bootstrap

# 3. Download GeoIP database (requires MAXMIND_LICENCE_KEY)
make update-geoip

# 4. Start the Flink enrichment + fraud scoring job
make flink-job

# 5. Generate sample transactions (mixed 25% suspicious traffic)
make generate
```

**Prerequisites**: Docker + Docker Compose, Python 3.11, and a free [MaxMind GeoLite2 licence key](https://www.maxmind.com/en/geolite2/signup) exported as `MAXMIND_LICENCE_KEY`.

---

## Architecture

```mermaid
flowchart LR
    Client(["Client / Load Generator"])

    subgraph Ingestion["Ingestion Layer"]
        Producer["Transaction Producer\nPython · Avro"]
        SchemaReg["Schema Registry\n:8081"]
    end

    subgraph Bus["Message Bus — Apache Kafka (KRaft)"]
        direction TB
        RawTopic[["txn.api\nAvro"]]
        EnrichedTopic[["txn.enriched\nJSON"]]
        AlertsTopic[["txn.fraud.alerts\nAvro"]]
        DLQTopic[["*.dlq topics"]]
    end

    subgraph Processing["Stream Processing — Apache Flink (PyFlink 2.x)"]
        direction TB
        Enrichment["Enrichment Pipeline\ndedup · velocity · geo · device"]
        Scoring["Fraud Rule Engine\nVEL · IT · ND families\n8 configurable rules"]
    end

    subgraph Persistence["Persistence Layer"]
        PG[("PostgreSQL\nfraud_alerts")]
        MinIO[("MinIO / S3\nFlink checkpoints")]
    end

    subgraph Observability["Observability"]
        direction TB
        Prometheus["Prometheus\n:9090"]
        Grafana["Grafana\n:3000"]
    end

    Client --> Producer
    Producer -->|"Avro schema"| RawTopic
    SchemaReg -. schema .-> Producer

    RawTopic --> Enrichment
    Enrichment --> Scoring
    Enrichment -->|enriched records| EnrichedTopic
    Enrichment -->|schema errors| DLQTopic
    Scoring -->|fraud alerts| AlertsTopic
    Scoring -->|fraud alerts| PG
    AlertsTopic -->|delivery failures| DLQTopic
    Enrichment -. checkpoints .-> MinIO

    AlertsTopic -->|"metrics bridge"| Prometheus
    EnrichedTopic -->|"metrics bridge"| Prometheus
    Prometheus --> Grafana

    style Processing fill:#1a1a2e,color:#eee,stroke:#4a4a8a
    style Scoring fill:#3b1f4a,color:#eee,stroke:#7a4a9a
    style Ingestion fill:#0d1117,color:#eee,stroke:#30363d
    style Bus fill:#0d1117,color:#eee,stroke:#30363d
    style Persistence fill:#0d1117,color:#eee,stroke:#30363d
    style Observability fill:#0d1117,color:#eee,stroke:#30363d
```

Transactions enter via Kafka (`txn.api`), are processed by the Flink job through four enrichment stages, evaluated against fraud rules, and routed to three outputs: the enriched topic, the fraud alerts topic, and PostgreSQL. A metrics bridge in the job process feeds Prometheus from those two Kafka topics.

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

All services start with `make bootstrap` or `make infra-up`.

---

## Configuration

### Fraud Rules

Edit [`rules/rules.yaml`](rules/rules.yaml) to change thresholds, enable/disable rules, or add new rule entries. The `RuleLoader` reads the file at job startup — threshold changes require a job restart, not a code change. An invalid YAML causes the job to refuse startup with a clear error rather than silently using defaults.

### Environment Variables

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
- **Input validation** — `rule_id` path parameter is validated against `^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$`; invalid IDs return `422`.
- **TOCTOU guard** — Demote/promote use a threading lock to prevent concurrent read-modify-write races on the YAML file.

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

### Dashboards and Alerts

- **Grafana** at `:3000` — pre-provisioned `FraudStream Pipeline` dashboard (auto-loaded from `infra/grafana/provisioning/`)
- **Prometheus alert rules** in `infra/prometheus/alerts/fraud_rule_engine.yml`:
  - `FraudFlagRateZero` — fires when `rule_flags_total` drops to zero for an extended period (possible silent evaluator failure)
  - `DLQDepthHigh` — fires when DLQ topic depth grows above threshold

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
```

**Coverage gate**: 80% required. The CI pipeline runs unit tests first, then integration tests against a live Docker stack. Integration tests require `make infra-up` and `make topics` before running locally.

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
    shared/
      avro_serde.py               # Avro deserialisation + schema validation
      dlq_sink.py                 # DLQ record builder
    schemas/                      # enriched-txn-v1.avsc, processing-dlq-v1.avsc
    kafka_metrics_bridge.py       # Daemon threads → Prometheus counter bridge
    job.py                        # Entry point (build_job + main)
    config.py                     # ProcessorConfig (env-driven dataclass)
    logging_config.py             # Structured JSON logging
    metrics.py                    # checkpoint_failures_total counter
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
    schemas/                      # fraud-alert-v1.avsc, fraud-alert-dlq-v1.avsc
    types.py                      # FraudAlert dataclass
    metrics.py                    # rule_evaluations_total, rule_flags_total
    config.py                     # ScoringConfig (env-driven dataclass)

rules/
  rules.yaml                      # Live rule set — edit thresholds here

scripts/
  generate_transactions.py        # Traffic generator + suspicious injection

infra/
  docker-compose.yml              # Full stack: Kafka, Flink, Prometheus, Grafana,
  │                               #   MinIO, PostgreSQL, Schema Registry
  kafka/
    topics.sh                     # Topic creation + Avro schema registration
  flink/
    Dockerfile                    # PyFlink + scoring extras image
    flink-conf.yaml               # RocksDB, checkpoint dir, Arrow bundle config
    alerts.yaml                   # Flink-level Prometheus alert rules
  postgres/
    migrations/
      001_create_fraud_alerts.sql # fraud_alerts DDL + indexes
  prometheus/
    prometheus.yml                # Scrape config (job :8002, Kafka exporter)
    alerts/
      fraud_rule_engine.yml       # FraudFlagRateZero + DLQDepthHigh alert rules
  grafana/
    provisioning/                 # Auto-provisioned datasource + fraud dashboard
  geoip/                          # GeoLite2-City.mmdb (gitignored; make update-geoip)

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
  contract/                       # Ingestion Avro schema contract tests
  contracts/                      # Fraud alert Avro schema contract tests
  load/
    test_throughput.py            # Throughput benchmarks
    test_memory.py                # State memory benchmarks

specs/
  001-kafka-ingestion-pipeline/   # Spec, research, data model, plan
  002-flink-stream-processor/     # Spec, research, checklists (7 categories)
  003-fraud-rule-engine/          # Spec, plan, tasks, implementation checklists
  004-operational-excellence/     # Spec (in progress)

.github/
  workflows/
    ci.yml                        # Unit tests → integration tests → coverage gate
    geoip-refresh.yml             # On-demand GeoIP database refresh
    weekly-geoip-refresh.yml      # Scheduled weekly GeoIP refresh
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
