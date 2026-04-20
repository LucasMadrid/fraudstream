# FraudStream Constitution

## Core Principles

### I. Stream-First (NON-NEGOTIABLE)
Every data flow is designed as an unbounded stream, never as a batch job retrofitted with streaming.  
- All transaction events must be published to Kafka topics before any processing occurs  
- No component may read directly from a source system; the broker is the single entry point  
- Kafka topics are the system of record for raw events — downstream stores are derived views  
- Local and cloud topologies must be functionally equivalent; Docker Compose mirrors the cloud stack 1:1  
- **Producer delivery guarantee**: All Kafka producers MUST use `acks=all` and `enable.idempotence=true`; transactional producers (`beginTransaction` / `commitTransaction`) are prohibited on the hot path — the 5–20ms coordination overhead is incompatible with the 10ms ingestion budget slice  
- **Cross-restart deduplication**: Idempotent delivery covers retries within a single producer session (PID + sequence number); cross-restart deduplication is the responsibility of the Flink `DeduplicationFilter` operator using a `KeyedProcessFunction` with a 48-hour TTL — a raw `ValueState<Set<transaction_id>>` MUST NOT be used because at high volume a per-account set grows unbounded; the implementation MUST cap memory via either (a) a timestamp-bounded `MapState<transaction_id, seen_at_ms>` that evicts entries older than the TTL on each `processElement` call, or (b) a Guava `BloomFilter` per key accepting a ≤ 0.1% false-positive rate in exchange for O(1) space; the chosen strategy must be documented in `specs/002-flink-stream-processor/`

### II. Sub-100ms Decision Budget
The end-to-end latency from event ingestion to block/allow response must not exceed 100ms at p99.  
- Ingestion → Kafka: < 10ms  
- Kafka → feature enrichment: < 20ms  
- Hot store lookup (Redis): < 5ms  
- ML model inference: < 30ms  
- Decision → response: < 15ms  
- Any component that cannot meet its budget slice is a blocker, not a warning  
- Latency SLOs are enforced via Prometheus alerts; breaches page on-call immediately

### III. Schema Contract Enforcement (NON-NEGOTIABLE)
All events crossing a topic boundary must be validated against a registered schema.  
- Avro is the mandatory wire format; `fastavro` is the mandatory Python library — `avro-python3` (deprecated, 5–10× slower) and plain JSON payloads are prohibited on all production topics  
- Schema Registry is mandatory in both local and cloud environments; schema IDs are cached in-process after the first produce — no per-message registry calls on the hot path  
- Breaking schema changes require a new topic version (`txn.web.v2`); old consumers must be migrated before the old topic is retired  
- **Non-breaking schema additions** (adding an optional field with a default value) MUST follow Avro's backward-compatibility rule: new fields MUST declare a `default` so existing consumers can deserialise messages written by the new producer; forward-compatibility (old producer, new consumer) is achieved by the consumer ignoring unknown fields — both directions are verified by the contract tests in `tests/contract/` before any schema version is promoted to the registry  
- The Schema Registry compatibility mode for all FraudStream subjects is set to `BACKWARD_TRANSITIVE`; any attempt to register a schema that fails this check is a build-time error, not a runtime surprise  
- Every field in the transaction event schema must have a `nullable: false` justification or an explicit nullability rationale documented in the schema  
- **Known drift — `txn.enriched`**: This topic currently uses JSON (v1.3 implementation). Migration to Avro is a constitution-compliance task tracked in `specs/002-flink-stream-processor/`; JSON is tolerated on this topic only until the **v2.0 production promotion milestone** — this is a hard deadline, not a rolling exception. If v2.0 ships with `txn.enriched` still in JSON, the release is blocked. All other topics are Avro-only with no exceptions

### IV. Channel Isolation
Each transaction source (POS, web, mobile, API) is treated as an independent channel with its own topic, enrichment job, and threshold configuration.  
- Topics: `txn.pos`, `txn.web`, `txn.mobile`, `txn.api` — no unified raw topic  
- Channel-specific features (device fingerprint for mobile, 3DS result for web) are enriched in the channel's own Flink job  
- Fraud thresholds and model versions are configured per channel; a single global threshold is prohibited  
- Cross-channel aggregations (e.g., velocity across all channels for one account) are computed in a dedicated enrichment step downstream of channel topics

### V. Defense in Depth — Rules Before Models
The scoring pipeline always applies a deterministic rule engine before invoking the ML model.  
- Rules are fast, auditable, and explainable; they catch known fraud patterns with zero inference cost  
- The ML model scores only events that pass the rule engine (no obvious fraud) or are flagged for soft review  
- If the ML serving layer is unavailable, the rule engine alone issues the decision — the pipeline never stalls or passes events silently; the circuit breaker MUST open within **3 consecutive failed inference calls or 5 seconds of sustained failure**, whichever comes first, and MUST be verified by an integration test that kills the model server and asserts rule-only decisions are issued within that window  
- Rule changes require a unit test proving the new rule fires on a crafted fraudulent event and does not fire on a crafted legitimate event  
- **Rule configuration**: Rules are defined in `rules/rules.yaml` and loaded by `RuleLoader` at job startup with full Pydantic validation; an invalid YAML is fatal — the job refuses to start rather than silently using defaults  
- **Operational rule promotion/demotion**: The Management API (`POST /rules/{rule_id}/promote`, `POST /rules/{rule_id}/demote`) is the only permitted mechanism for changing rule mode at runtime without a job restart; direct file edits require a restart  
- **Hot-reload (PLANNED — v2)**: Rule updates via `txn.rules.config` Kafka topic (compacted, one record per rule ID) are deferred to Phase 2; until then, threshold changes require a `RuleLoader` reload triggered via the Management API or a job restart

### VI. Immutable Event Log
Raw events are never mutated or deleted after they reach the event store.  
- Apache Iceberg tables are append-only; `UPDATE` and `DELETE` operations on raw event tables are prohibited  
- All enrichment and decisions are stored in separate derived tables, joined by `transaction_id`  
- The event store is the source of truth for model retraining, audit, and dispute resolution  
- Retention policy: raw events retained for 7 years (regulatory minimum); hot store (Redis) TTL is 24 hours  
- **Operational review store (narrow exception)**: A PostgreSQL `fraud_alerts` table is permitted as a secondary operational store for alert review status (`pending` / `confirmed-fraud` / `false-positive`) and reviewer workflow. This is NOT the event store — it holds mutable review state only, not raw events. It MUST NOT be used as a substitute for Iceberg for any analytics or training query. Schema: `transaction_id` (PK), `account_id`, `matched_rule_names` (array), `severity`, `evaluation_timestamp`, `status`, `reviewed_by` (nullable), `reviewed_at` (nullable)

### VII. PII Minimization at the Edge
Sensitive fields are masked at the Kafka producer, before the event enters any pipeline component.  
- Card numbers: store only last 4 digits + BIN (first 6); full PANs are never written to any topic or store  
- IP addresses: truncate to /24 subnet for storage; full IP used only transiently during enrichment  
- No component downstream of the producer may reconstruct a full PAN or full IP  
- PII masking logic lives in a shared library (`pipelines/ingestion/shared/pii_masker/`), versioned and tested independently of producers

### VIII. Observability as a First-Class Concern
Every pipeline component emits structured logs, metrics, and traces; silent failures are forbidden.  
- Metrics: throughput (events/sec), latency (p50/p95/p99), fraud rate, block rate, DLQ depth  
- Logs: structured JSON, minimum fields — `transaction_id`, `component`, `timestamp`, `level`, `message`  
- Traces: distributed trace spanning ingestion → enrichment → scoring → decision (OpenTelemetry)  
- A dead letter queue (DLQ) topic exists for each pipeline stage; DLQ depth > 0 triggers an alert within 60 seconds  
- **PyFlink metrics constraint**: PyFlink TaskManager workers are JVM-spawned and cannot share Python `prometheus_client` registry objects with the main-process HTTP server. Metrics are bridged via two daemon consumer threads inside the job process consuming `txn.fraud.alerts` and `txn.enriched` — this introduces ~100–500ms metric lag. `PyFlink MetricGroup`, `PROMETHEUS_MULTIPROC_DIR`, and push gateway are all prohibited as they do not work correctly in this topology  
- **Metrics endpoint**: The job exposes Prometheus metrics at `:8002/metrics`; this endpoint is the authoritative scrape target for Prometheus

### IX. Analytics-First Persistence (NON-NEGOTIABLE)
Every enriched transaction and every fraud decision MUST be durably persisted to the event store and made queryable by downstream analytics, ML training, and audit consumers — the streaming pipeline is not a black box.  
- **Enriched transactions sink**: The stream processor MUST write each `EnrichedTransaction` record to an append-only Iceberg table (`iceberg.enriched_transactions`) in addition to publishing to the Kafka output topic — the write is a mandatory side output, not optional  
- **Fraud decisions sink**: The scoring engine MUST write each `ScoringOutput` record (decision, fraud score, rule triggers, model version, latency) to a separate Iceberg table (`iceberg.fraud_decisions`) keyed by `transaction_id`  
- **Feature materialization**: Computed velocity, geolocation, and device fingerprint features MUST be materialized to the Feast feature store on every enrichment cycle, making them available for both online serving (low-latency lookup during inference) and offline training (point-in-time correct feature snapshots)  
- **Analytics query layer**: An always-on SQL query engine (Trino locally; Athena/BigQuery in cloud) MUST be able to join `enriched_transactions` and `fraud_decisions` on `transaction_id` without requiring custom code — schema evolution in Iceberg tables follows the same Schema Registry versioning discipline as Kafka topics  
- **No analytics-only writes to Kafka**: Iceberg is the analytics source of truth; ad-hoc analytics pipelines MUST NOT re-consume raw Kafka topics as a substitute for a missing persistence layer  
- **Write latency budget**: Iceberg sink writes MUST complete within 5 seconds of the Kafka consumer receipt — this is outside the 100ms scoring hot path and MUST NOT add latency to the decision response

### X. Analytics Consumer Layer
A dedicated, independent consumer component owns all reporting and interactive analytics — it is strictly read-only relative to the pipeline and MUST NOT influence any scoring or enrichment decision.  

**Data sources (dual-mode):**
- **Real-time feed** — Kafka consumer on `txn.fraud.alerts` for live dashboards (sub-second event visibility); consumer group `analytics.dashboard` with `auto.offset.reset = latest`  
- **Historical store** — Trino queries over `iceberg.enriched_transactions` and `iceberg.fraud_decisions` for reports, drill-down, and ML audit  
- **Note**: `txn.decisions` is a planned topic (see Kafka Topic Inventory). Until it is built, the real-time feed consumes `txn.fraud.alerts` as the live decision signal

**Reporting tools hierarchy:**
| Layer | Tool | Purpose |
|---|---|---|
| Interactive app | **Streamlit** | Primary UI — live fraud dashboard, historical reports, and (future) rule engine config |
| Operational metrics | Grafana | Infrastructure / SLO dashboards (latency, DLQ, throughput) — unchanged |
| Ad-hoc SQL | Trino | Data team exploration and scheduled report queries |
| BI (optional) | Apache Superset | Self-serve dashboards for non-engineering stakeholders |

**Streamlit app responsibilities (v1):**
- Live transaction feed with decision overlay (real-time Kafka consumer, refreshed via `st.empty()` loop)  
- Fraud rate by channel, merchant, and geo over configurable rolling windows (Trino → Iceberg)  
- Rule trigger frequency leaderboard — which rules fire most, false-positive rate per rule  
- Model version comparison — fraud score distribution per model version over a chosen time range  
- DLQ inspector — browse dead-letter records without direct Kafka access

**Streamlit app responsibilities (v2 — rule engine config UI, future spec):**
- CRUD interface for rule definitions (thresholds, conditions, enabled/disabled)  
- Rule test harness — submit a synthetic transaction and show which rules would fire  
- Rule change audit log — who changed what, when, with before/after diff  
- Config changes MUST be written to a `txn.rules.config` Kafka topic consumed by the rule engine; Streamlit MUST NOT write rule state directly to any store the scoring engine reads

**Constraints:**
- The analytics consumer MUST run as an independent process/service — it MUST NOT be co-located with the scoring engine or stream processor  
- It MUST use a separate Kafka consumer group and MUST NOT affect topic offsets consumed by scoring/processing  
- Streamlit sessions MUST NOT cache PII beyond the minimum required to render the current view; masked values from Iceberg are displayed as-is  
- The analytics service is non-critical-path: its unavailability MUST NOT affect fraud scoring SLOs

### XI. Feature Serving Contract (NON-NEGOTIABLE)
The online feature store is a first-class pipeline component — it is read on the scoring hot path and its availability, latency, and correctness directly affect the 100ms decision budget.  
- **Read path SLO**: Online feature retrieval MUST complete within **2ms at p99** per lookup; this is a hard sub-slice of the 5ms hot store allocation in Principle II — a retrieval exceeding 2ms is treated as a budget breach, not a warning  
- **Client timeout and fallback**: The scoring engine MUST set a client-side timeout of **3ms** on every online store read; on timeout or store unavailability, the engine MUST fall back to zero-valued features (not stale features, not a request failure) and MUST increment a `feature_store_fallback_total` counter — silent fallback to stale values is prohibited because stale velocity counts can suppress legitimate fraud signals  
- **Staleness bound**: Features read from the online store MUST reflect an enrichment cycle completed within the last **30 seconds**; if staleness of any feature exceeds this bound (detectable via `feature_materialization_lag_ms`), a Prometheus alert fires before the next model deployment  
- **Write path ownership**: The online store is written exclusively by the Feast materialization push in the enrichment job — no other component may write feature values directly; the scoring engine is strictly read-only relative to the store  
- **Cold-start policy**: On pipeline startup or Flink job recovery, the online store may be empty or stale; the scoring engine MUST treat a cache miss as zero-valued features (not an error) and log each miss as a `feature_store_miss` event — the rule engine still fires on transaction fields available in the record itself  
- **Event timestamp on write**: Feature values MUST be pushed using the Kafka event timestamp as `event_timestamp` (not wall-clock time at flush) — enforcing FR-024 at the constitution level; the Feast push call MUST use `PushMode.ONLINE_AND_OFFLINE` with `event_timestamp_column='event_ts'`  
- **Backend contract**: The online store backend MUST support sub-2ms point lookups by entity key (`account_id`) at expected peak load; Redis (Feast Redis online store) satisfies this contract locally and in cloud — any alternative requires a load test proving p99 < 2ms before production


---

## Data Contracts

### Transaction Event (minimum viable schema)
```json
{
  "transaction_id": "uuid",
  "account_id":     "string",
  "merchant_id":    "string",
  "amount":         "decimal",
  "currency":       "ISO-4217",
  "timestamp":      "epoch_ms",
  "channel":        "enum[POS, WEB, MOBILE, API]",
  "device_id":      "string",
  "ip_subnet":      "string (masked /24)",
  "geo_lat":        "float",
  "geo_lon":        "float",
  "card_bin":       "string (first 6)",
  "card_last4":     "string"
}
```

### Scoring Output (minimum viable schema)
```json
{
  "transaction_id": "uuid",
  "fraud_score":    "float [0.0–1.0]",
  "decision":       "enum[ALLOW, FLAG, BLOCK]",
  "rule_triggers":  ["string"],
  "severity":       "enum[low, medium, high, critical] (highest severity among matched rules; null if no rules matched)",
  "model_version":  "string",
  "latency_ms":     "int"
}
```

### Iceberg Analytics Tables (write path — mandatory per Principle IX)

**`iceberg.enriched_transactions`** — written by the stream processor as a side output
```json
{
  "transaction_id":    "uuid        (partition key)",
  "account_id":        "string",
  "merchant_id":       "string",
  "amount":            "decimal",
  "currency":          "ISO-4217",
  "event_timestamp":   "epoch_ms   (event time — used for Iceberg partitioning by day)",
  "enrichment_time":   "epoch_ms   (processing time — when sink write occurred)",
  "channel":           "enum[POS, WEB, MOBILE, API]",
  "vel_count_1m":      "int",
  "vel_count_5m":      "int",
  "vel_count_1h":      "int",
  "vel_count_24h":     "int",
  "vel_amount_1m":     "decimal",
  "vel_amount_5m":     "decimal",
  "vel_amount_1h":     "decimal",
  "vel_amount_24h":    "decimal",
  "geo_country":       "string",
  "geo_city":          "string",
  "geo_network_class": "enum[RESIDENTIAL, BUSINESS, HOSTING, MOBILE, UNKNOWN]",
  "geo_confidence":    "float [0.0–1.0]",
  "device_first_seen":  "epoch_ms",
  "device_txn_count":   "int",
  "device_known_fraud": "bool",
  "prev_geo_country":   "string (nullable — null on first transaction per device)",
  "prev_txn_time_ms":   "epoch_ms (nullable — null on first transaction per device)",
  "schema_version":     "string"
}
```
Partitioned by `event_timestamp` (daily). `transaction_id` is the deduplication key.

**`iceberg.fraud_decisions`** — written by the scoring engine
```json
{
  "transaction_id":  "uuid        (join key to enriched_transactions)",
  "decision":        "enum[ALLOW, FLAG, BLOCK]",
  "fraud_score":     "float [0.0–1.0]",
  "rule_triggers":   "array<string>",
  "model_version":   "string",
  "decision_time":   "epoch_ms   (used for Iceberg partitioning by day)",
  "latency_ms":      "int",
  "schema_version":  "string"
}
```
Partitioned by `decision_time` (daily). `transaction_id` is the deduplication key.

---

### Kafka Topic Inventory

| Topic | Producer | Consumer(s) | Purpose | Status |
|---|---|---|---|---|
| `txn.api` | API ingestion producer | Flink enrichment job | Raw API transaction events (primary channel in v1) | Live |
| `txn.pos` | POS ingestion producer | Flink enrichment job | Raw POS transaction events | Planned |
| `txn.web` | Web ingestion producer | Flink enrichment job | Raw web transaction events | Planned |
| `txn.mobile` | Mobile ingestion producer | Flink enrichment job | Raw mobile transaction events | Planned |
| `txn.api.dlq` | Ingestion producer | On-call / DLQ inspector | Schema validation failures at ingestion | Live |
| `txn.processing.dlq` | Flink enrichment job | On-call / DLQ inspector | Processing errors from enrichment job | Live |
| `txn.enriched` | Flink enrichment job | Rule evaluator, scoring engine, analytics consumer | Enriched transactions — **JSON (known drift, migration to Avro pending)** | Live |
| `txn.fraud.alerts` | Rule evaluator (Flink) | Operations queue, case management, analytics consumer | Structured fraud alert per flagged transaction — Avro | Live |
| `txn.fraud.alerts.dlq` | Alert Kafka sink | On-call / DLQ inspector | Alert Kafka delivery failures | Live |
| `txn.decisions` | Scoring engine | Analytics consumer (`analytics.dashboard`) | Final ALLOW/FLAG/BLOCK decisions with fraud score | **Planned** |
| `txn.rules.config` | Rule config UI (Streamlit v2) | Rule evaluator (Flink) | Rule definition updates — compacted topic, one record per rule ID | **Planned — v2** |

---

### Channel-Specific Extra Fields
| Channel | Required extra fields |
|---|---|
| POS     | `terminal_id`, `entry_mode` (chip/swipe/tap), `mcc` |
| WEB     | `session_id`, `user_agent`, `billing_eq_shipping` (bool), `3ds_result` |
| MOBILE  | `app_version`, `os_version`, `rooted_flag` (bool), `gps_accuracy_m` |
| API     | `api_key_id`, `caller_ip_subnet`, `oauth_scope` |

---

## Technology Stack

### Local (development / CI)

Services are tiered. **Core** services start with `make bootstrap` and are required for unit tests, integration tests, and the scoring hot path. **Analytics** services are opt-in (`make analytics-up`) and required only when working on Principle IX/X features. **Optional** services are never started by default.

| Role | Tool | Tier | Notes |
|---|---|---|---|
| Message broker | Apache Kafka (KRaft, Docker) | **Core** | Single-broker, no Zookeeper |
| Schema registry | Confluent Schema Registry (Docker) | **Core** | `:8081` |
| Stream processing | **PyFlink 2.x DataStream API** | **Core** | Java Flink and Bytewax both rejected — see Design Decisions §1 |
| State backend | **RocksDB (`EmbeddedRocksDBStateBackend`, incremental)** | **Core** | Heap backend rejected — see Design Decisions §2 |
| PostgreSQL | PostgreSQL (Docker) | **Core** | `fraud_alerts` operational store only |
| Observability | Prometheus + Grafana (Docker) | **Core** | Metrics at `:8002/metrics`, dashboards at `:3000` |
| Hot store / online feature store | Redis (Docker) | **Core** | Hot store: 24h TTL velocity keys (`hot:*`); Feast online store: feature vectors (`feast:*`) — shared instance, separate key namespaces |
| Event store | MinIO + Apache Iceberg | **Analytics** | S3-compatible; Flink checkpoints also stored here |
| Analytics query engine | Trino (Docker) | **Analytics** | Bulk queries and >7-day scans over Iceberg on MinIO |
| In-process analytics | DuckDB (`duckdb-iceberg` extension) | **Analytics** | Streamlit session queries, interactive drilldown, <7-day scans |
| Analytics UI | Streamlit (Docker) | **Analytics** | Live dashboard + historical reports |
| Feature store | Feast (local SQLite backend) | **Analytics** | |
| GeoIP | MaxMind GeoLite2-City (embedded) | **Core** | Refreshed weekly via `geoip-refresh.yml` — see Design Decisions §6 |
| Orchestration | Docker Compose + Makefile | **Core** | `make bootstrap` = Core only; `make analytics-up` adds Analytics tier |
| BI | Apache Superset (Docker) | **Optional** | Self-serve SQL dashboards — never started by default |

### Cloud (staging / production)
| Role | Tool |
|---|---|
| Message broker | Confluent Cloud · AWS MSK · GCP Pub/Sub |
| Stream processing | PyFlink on Kubernetes |
| State backend | RocksDB + incremental checkpoints to S3/GCS |
| Hot store | AWS ElastiCache (Redis) · Aerospike — separate instance from online feature store in production |
| Online feature store | AWS ElastiCache (Redis) · GCP Memorystore · Aerospike (Feast connector) — sub-2ms p99 required |
| Event store | S3/GCS + Apache Iceberg · Delta Lake |
| Feature store | Feast on K8s · Hopsworks · Tecton |
| Analytics query engine | AWS Athena · GCP BigQuery · Trino on K8s |
| Analytics UI | Streamlit on K8s (or managed container) |
| BI (optional) | Apache Superset on K8s · AWS QuickSight |
| ML serving | SageMaker · Vertex AI · Seldon |
| Orchestration | Kubernetes · Terraform · Helm |
| Observability | Datadog · Grafana Cloud · AWS CloudWatch |

### Prohibited technology choices
- Batch-only orchestrators (Airflow DAGs) for the scoring path — streaming pipeline only  
- Mutable OLTP databases (Postgres, MySQL) as the primary event store — PostgreSQL is permitted only for the `fraud_alerts` operational review store (Principle VI exception)  
- Synchronous HTTP calls between pipeline components during the scoring hot path  
- Any ML framework that cannot serve inference in < 30ms at p99 on target hardware  
- `avro-python3` (deprecated) — `fastavro` only  
- `AvroProducer` (legacy Confluent client) — use the current `confluent-kafka` producer with manual Avro serialisation  
- Re-consuming raw Kafka topics for analytics purposes — Iceberg is the analytics source of truth  
- `HashMapStateBackend` (heap) for velocity state — use RocksDB to avoid JVM heap exhaustion at scale  
- `SlidingEventTimeWindows` for velocity aggregation — use `MapState<minute_bucket>` (see Design Decisions §3)

---

## Repository Structure

```
fraudstream/                          # github.com/LucasMadrid/fraudstream
├── .claude/
│   └── commands/                     # Claude Code slash commands for this project
├── .github/
│   └── workflows/
│       ├── ci.yml                    # unit tests → integration tests → coverage gate (80%)
│       ├── geoip-refresh.yml         # on-demand GeoIP database refresh
│       └── weekly-geoip-refresh.yml  # scheduled weekly GeoIP refresh
├── .specify/                         # Specify design tokens (if applicable)
├── alertmanager/                     # Alertmanager config
├── analytics/
│   ├── app/
│   │   ├── pages/
│   │   │   ├── 1_live_feed.py        # real-time Kafka consumer dashboard
│   │   │   ├── 2_fraud_rate.py       # historical Trino → Iceberg reports
│   │   │   ├── 3_rule_triggers.py    # rule leaderboard and false-positive analysis
│   │   │   ├── 4_model_compare.py    # fraud score distribution per model version
│   │   │   └── 5_dlq_inspector.py    # dead-letter queue browser
│   │   └── Home.py                   # entry point, shared Kafka/Trino client init
│   ├── consumers/                    # Kafka consumer wrapper (group: analytics.dashboard)
│   ├── queries/                      # Trino SQL query library (parameterized, versioned)
│   └── reports/                      # scheduled report definitions (cron + Trino queries)
├── docs/                             # Architecture diagrams, ADRs
├── infra/
│   ├── docker-compose.yml            # full local stack
│   ├── flink/
│   │   ├── Dockerfile                # PyFlink + scoring extras image
│   │   ├── flink-conf.yaml           # RocksDB, checkpoint dir, Arrow bundle config
│   │   └── alerts.yaml               # Flink-level Prometheus alert rules
│   ├── grafana/
│   │   └── provisioning/             # auto-provisioned datasource + fraud dashboard
│   ├── kafka/
│   │   └── topics.sh                 # topic creation + Avro schema registration
│   ├── postgres/
│   │   └── migrations/
│   │       └── 001_create_fraud_alerts.sql
│   ├── prometheus/
│   │   ├── prometheus.yml            # scrape config
│   │   └── alerts/
│   │       └── fraud_rule_engine.yml # FraudFlagRateZero + DLQDepthHigh
│   └── geoip/                        # GeoLite2-City.mmdb (gitignored; make update-geoip)
├── monitoring/
│   ├── dashboards/                   # Grafana JSON (ops metrics: latency, DLQ, throughput)
│   └── alerts/                       # Prometheus alerting rules
├── pipelines/
│   ├── ingestion/                    # Feature 001 — Kafka producer
│   │   ├── api/                      # producer entry point, metrics, telemetry
│   │   └── shared/
│   │       ├── pii_masker/           # PAN masking (BIN + last-4 + Luhn validation)
│   │       └── schema_registry.py    # Schema Registry client wrapper
│   ├── processing/                   # Feature 002 — PyFlink enrichment job
│   │   ├── operators/                # VelocityEnrichment, GeolocationMapFunction,
│   │   │                             #   DeviceProcessFunction, EnrichedRecordAssembler
│   │   └── shared/
│   │       ├── avro_serde.py         # Avro deserialisation + schema validation
│   │       └── dlq_sink.py           # DLQ record builder
│   └── scoring/                      # Feature 003 — Fraud rule engine
│       ├── rules/
│       │   ├── families/             # velocity.py, new_device.py, impossible_travel.py
│       │   ├── models.py             # Pydantic rule models (RuleConfig, RuleConditions)
│       │   ├── loader.py             # RuleLoader — reads rules.yaml at startup
│       │   └── evaluator.py          # FraudRuleEvaluator — stateless Flink MapFunction
│       └── sinks/
│           ├── alert_kafka.py        # AlertKafkaSink — Avro → txn.fraud.alerts
│           └── alert_postgres.py     # AlertPostgresSink — INSERT INTO fraud_alerts
├── rules/
│   └── rules.yaml                    # live rule set — edit thresholds here
├── scripts/
│   └── generate_transactions.py      # traffic generator + suspicious injection
├── specs/                            # per-feature specs (authoritative per-feature ADRs)
│   ├── 001-kafka-ingestion-pipeline/
│   ├── 002-flink-stream-processor/
│   ├── 003-fraud-rule-engine/
│   └── 004-operational-excellence/   # in progress
├── storage/
│   ├── feature_store/                # Feast repo, feature views, data sources
│   └── lake/                         # Iceberg table definitions, schema migrations
├── tests/
│   ├── unit/                         # per-component unit tests (330+ tests, 80% gate)
│   ├── integration/                  # testcontainers-based end-to-end tests
│   ├── contract/                     # Avro schema contract tests
│   └── load/                         # throughput + memory benchmarks
├── CLAUDE.md                         # runtime development guidance for Claude Code
├── Makefile                          # all project targets (bootstrap, flink-job, test, etc.)
├── pyproject.toml                    # Python dependencies and project metadata
└── README.md
```

---

## Design Decisions

These decisions are architectural law — they represent choices made with full awareness of the alternatives. Revisiting them requires the same amendment process as a Core Principle change.

### DD-1. Stream processing runtime — PyFlink 2.x DataStream API
**Rejected**: Java Flink operators (abandons Python 3.11 ML toolchain; Java expertise not available), Bytewax (no production-grade exactly-once in v0.x; no incremental checkpoint support).  
**Tradeoff accepted**: Every `state.value()` / `state.update()` call crosses a JVM–Python boundary via Unix socket (Beam Fn API). Arrow batching (`bundle.size=1000`, `bundle.time=15ms`) amortises overhead to ~10–50µs per record at throughput.

### DD-2. State backend — RocksDB + incremental checkpoints
**Rejected**: `HashMapStateBackend` (heap) — velocity state for millions of accounts exhausts JVM heap; no disk-spillover. Full checkpoints — at 200–800 MB state, full snapshots take 30–120s and violate the 30s checkpoint timeout.  
**Tradeoff accepted**: RocksDB introduces ~2–5× read latency vs heap for `state.value()`. Mitigated by keeping hot account state in RocksDB's block cache. Incremental checkpoints require maintaining a chain of ≥ 3 snapshots — deleting intermediate ones breaks recovery.

### DD-3. Velocity windows — `MapState<minute_bucket>` over sliding event-time windows
**Rejected**: `SlidingEventTimeWindows(24h, 1min)` — creates 1,440 panes; each event copied into all panes — O(window/slide) memory and CPU. `ListState<(timestamp, amount)>` — O(N) full scan per event.  
**Tradeoff accepted**: Custom watermark eviction logic required (timer per account). Late events within allowed-lateness window are handled by re-firing `process_element` on the correct bucket.

### DD-4. Serialisation — Avro (`fastavro`) over Protobuf and JSON
**Rejected**: Protobuf (no native Confluent Schema Registry integration), JSON (no schema enforcement; 3–5× larger payloads), `avro-python3` (deprecated; 5–10× slower), `AvroProducer` legacy client (deprecated; wraps `avro-python3`).  
**Tradeoff accepted**: Avro binary requires a schema registry sidecar. Schema IDs are cached in-process after the first produce.

### DD-5. Kafka producer guarantees — `acks=all` + idempotent
**Rejected**: `acks=1` (leader failover can lose the last unreplicated batch — a lost fraud event is a regulatory liability). Transactional producer (5–20ms per record — incompatible with 10ms budget slice).  
**Tradeoff accepted**: `acks=all` adds ~1–2ms round-trip per batch. Cross-restart deduplication is handled by Flink `DeduplicationFilter` with a 48h TTL — not by Kafka offset management alone.  
**Memory constraint**: A naive `ValueState<Set<transaction_id>>` grows unbounded at high volume (a busy account with 10 txn/min over 48h accumulates ~28,800 IDs in state). The implementation MUST use timestamp-bounded eviction (`MapState<transaction_id, seen_at_ms>` with per-element eviction on `processElement`) or a probabilistic bloom filter (≤ 0.1% false-positive rate). The chosen approach must include a memory budget estimate in `specs/002-flink-stream-processor/`.

### DD-6. GeoIP lookup — embedded MaxMind reader + LRU cache
**Rejected**: External GeoIP HTTP API (5–50ms network round-trip in hot path), re-open reader per record (file descriptor exhaustion).  
**Tradeoff accepted**: GeoLite2-City MMDB (~70 MB) must be distributed to every Flink TaskManager (mounted via Docker volume). Refreshes require a job restart. Automated weekly refresh via `.github/workflows/weekly-geoip-refresh.yml`. Lookup latency ~5–20µs with `maxminddb-c` C extension; ~0µs on LRU cache hit for repeated /24 subnets.

### DD-7. Fraud rule configuration — YAML file + Pydantic validation
**Rejected**: Hardcoded thresholds (threshold changes require full deploy), database-backed rules (runtime dependency; query latency in hot path), Kafka-consumed rule updates (deferred to v2 as TD-001).  
**Tradeoff accepted**: Rule threshold changes require a job restart unless applied via the Management API promote/demote endpoints. `RuleConfigError` on invalid YAML is fatal.

### DD-8. Alert persistence — dual-sink (Kafka + PostgreSQL)
**Rejected**: Kafka-only (no queryable store for fraud ops; `status` lifecycle requires a DB), PostgreSQL-only (no downstream consumers without re-publishing), exactly-once Kafka delivery (2PC coordination adds significant latency).  
**Tradeoff accepted**: `AT_LEAST_ONCE` delivery means duplicate alerts are possible on Flink recovery. `ON CONFLICT (transaction_id) DO NOTHING` absorbs duplicates at the DB level.

### DD-9. Metrics bridge — daemon consumer threads
**Rejected**: `PyFlink MetricGroup` (Python metric objects not shared with main-process HTTP server — JVM workers and Python driver have separate `prometheus_client` registries), `PROMETHEUS_MULTIPROC_DIR` (PyFlink workers are JVM-spawned and do not write `.db` files to the multiprocess dir), push gateway (stateful middleman; aggregation semantics differ).  
**Tradeoff accepted**: ~100–500ms metric lag between a rule firing and the counter being visible in Prometheus. Acceptable for operational dashboards; not suitable for sub-second alerting.

### DD-10. Online feature store backend — Redis via Feast
**Rejected**: PostgreSQL as online store (10–50ms query latency — incompatible with 2ms p99 SLO). In-memory dict in the Flink operator (lost on TaskManager restart; not shared across parallel scoring instances). Re-computing features at scoring time (duplicates enrichment work; adds 20–50ms to hot path; violates single-enrichment-cycle principle). Hopsworks/Tecton locally (no lightweight Docker image; operational overhead incompatible with Analytics tier isolation).  
**Chose**: Feast with Redis online backend — locally the same Redis Docker instance as the hot store using a separate `feast:` key namespace; in production a dedicated Redis-compatible instance (AWS ElastiCache, GCP Memorystore, or Aerospike with Feast connector).  
**Tradeoff accepted**: Locally, Redis is a shared dependency — hot store velocity keys and Feast feature vectors co-exist on the same instance. Key namespace isolation (`feast:` prefix enforced by Feast) prevents collision, but a Redis OOM event affects both. Mitigation: `maxmemory-policy allkeys-lru` with a memory cap sized for both namespaces combined. In production these MUST be separate Redis instances — the feature serving SLO (2ms p99) and the hot store TTL semantics are operationally incompatible under shared memory pressure.  
**Read latency profile**: Redis GET on a ~1KB feature vector: ~0.1–0.3ms local socket, ~0.5–1ms cross-AZ in cloud. Feast client serialisation/deserialisation overhead: ~0.2–0.5ms. Expected p99 read latency: ~0.8–1.5ms — leaves ~0.5ms headroom against the 2ms SLO.  
**Cold-start and miss handling**: On Flink recovery, the online store may be partially stale. Scoring engine treats every cache miss as zero-valued features and logs a `feature_store_miss` event — this is safer than using stale velocity counts which could suppress a fraud signal that should fire. Zero-valued features cause the ML model to produce a neutral score; the rule engine still evaluates deterministic rules against the raw transaction fields.


---

## Non-Negotiables (Pre-Production Checklist)

- [ ] Idempotent consumers — `DeduplicationFilter` uses timestamp-bounded eviction or bloom filter (NOT raw `ValueState<Set>`); memory budget documented in specs; tested under Flink recovery
- [ ] Dead letter queue per stage — no event is silently dropped; DLQ depth alert fires in < 60s
- [ ] Model versioning — every deployed model carries a tagged version; previous version stays live for instant rollback
- [ ] Circuit breaker on ML service — breaker opens within 3 consecutive failures or 5 seconds, verified by integration test that kills the model server and confirms rule-only decisions resume within that window
- [ ] PII masking — full PAN and full IP never reach any topic; Luhn validation enforced; verified by automated integration test
- [ ] Latency budget test — p99 < 100ms verified under 2× expected peak load before promotion to production
- [ ] Schema migration plan — every schema change ships with a consumer migration guide and a deprecation date for the old topic
- [ ] Fraud rate baseline — 24-hour rolling mean established in staging; production alert fires on > 3σ deviation
- [ ] Every component tested at ≥ 80% code coverage — enforced by CI gate
- [ ] Analytics sink verified — `iceberg.enriched_transactions` and `iceberg.fraud_decisions` tables queryable via Trino/Athena before production promotion; row count must match Kafka topic offset within 0.1%
- [ ] Feast materialization verified — feature values for a test account retrievable from online store within 5 seconds of enrichment event published to Kafka
- [ ] Point-in-time correctness — offline Feast feature snapshots for a replayed event sequence match the values observed at enrichment time (no future leakage)
- [ ] Analytics consumer isolation verified — `analytics.dashboard` consumer group offset does not interfere with scoring or processing consumer groups under load
- [ ] Streamlit live feed lag — real-time dashboard reflects a new alert within 2 seconds of it being published to `txn.fraud.alerts` under normal load
- [ ] Streamlit historical report accuracy — fraud rate figures from Streamlit/Trino match figures from a direct Iceberg table scan for the same time window (zero discrepancy)
- [ ] Analytics service failure isolation — taking down the Streamlit service and Trino container has zero effect on fraud scoring latency and throughput SLOs
- [ ] Management API secured — `MANAGEMENT_API_KEY` set in all non-dev environments; rate limits verified under load
- [ ] GeoIP database current — `GeoLite2-City.mmdb` present and < 30 days old before any production deployment
- [ ] Schema compatibility mode — Schema Registry compatibility set to `BACKWARD_TRANSITIVE` for all FraudStream subjects; verified before first production topic registration
- [ ] Non-breaking schema additions tested — `tests/contract/` passes for both backward (new producer, old consumer) and forward (old producer, new consumer) directions before any schema version is promoted
- [ ] `txn.enriched` Avro migration complete — JSON drift resolved; topic produces Avro before v2.0 production promotion (hard deadline per Principle III)
- [ ] Online feature store read SLO — Feast Redis read p99 < 2ms verified under 2× expected peak load; `feature_store_fallback_total` counter is zero under normal operation
- [ ] Feature staleness alert — `feature_materialization_lag_ms` Prometheus alert fires correctly when lag exceeds 30 seconds; verified in staging
- [ ] Cold-start behaviour verified — pipeline recovery test confirms `feature_store_miss` events are logged and zero-valued fallback is applied without scoring errors or latency spikes
- [ ] Production Redis separation — online feature store and hot store run on separate Redis instances in staging/production; shared-instance configuration is local-only

---

## Governance

This constitution supersedes all other architectural decisions, ADRs, and team conventions for the FraudStream project. Any component, PR, or design that conflicts with a Core Principle is blocked until resolved. Per-feature specs live in `specs/` and are authoritative for implementation detail; the constitution governs cross-cutting principles only.

**Amendments** require:
1. A written rationale explaining why the principle is insufficient
2. An impact assessment covering affected components and latency budget
3. Approval from at least two senior engineers and the data engineering lead
4. A migration plan with a completion date before the amendment takes effect

All pull requests must include a checklist item confirming compliance with the relevant principle(s). Complexity must be justified — if a simpler approach meets the latency budget and data contract requirements, the simpler approach wins. For runtime development guidance used by Claude Code, refer to `CLAUDE.md`.

---

**Version**: 1.6.0 | **Ratified**: 2026-03-30 | **Last Amended**: 2026-04-18