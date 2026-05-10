# FraudStream Testing Strategy v2.0

**Author:** Tester Agent
**Date:** 2026-05-09
**Scope:** Coverage gap analysis, test plan for specs 010-016, test infrastructure, CI changes
**Constitution baseline:** v1.7.0 → v2.0.0 proposed

---

## EXECUTIVE SUMMARY

FraudStream has a solid test foundation: 330+ unit tests at 80% coverage gate,
10 integration tests, 2 contract test files, 2 load tests, 1 performance test,
and documented (but unautomated) chaos scenarios. However, critical gaps exist
that map directly to constitution violations and the upcoming specs 010-016.

Key findings:
- 14 constitution compliance items have zero or partial test coverage
- Chaos test directory is empty (manual FAILURE_SCENARIOS.md only)
- No circuit breaker 3-fail/5s timing integration test exists
- AlertKafkaSink.close() is untested
- Iceberg silent drop on table=None is untested
- Scoring metrics SafeMetric wrapping is untested
- No Postgres reconnection test
- No metrics bridge thread safety test
- Integration tests use fallback Python classes, not real Flink operators
- CI has no contract test stage, no chaos stage, no load test stage

---

## PART 1: COVERAGE GAPS MAPPED TO CONSTITUTION VIOLATIONS

### 1.1 Critical Gaps (P0 — Block Production)

| Gap ID | Constitution Principle | Violation | Current Test State | Required Test |
|--------|----------------------|-----------|-------------------|---------------|
| G-01 | VIII (Observability) | V2: Silent record drops — DLQ wiring commented out | No test verifies records reach DLQ on schema error | Integration: inject invalid Avro, assert DLQ topic receives record within 5s |
| G-02 | VI (Immutable Log) | V4: IcebergSinkBase._flush() silently clears buffer when table=None | No test for table=None path | Unit: sink.invoke() with _table=None, assert DLQ logging and buffer not silently cleared |
| G-03 | VIII (Observability) | Scoring metrics not SafeMetric wrapped | test_metrics.py exists but doesn't verify SafeMetric wrapping behavior under registration failure | Unit: simulate prometheus_client.Counter registration error, assert metric operation doesn't crash |
| G-04 | VIII (Observability) | AlertKafkaSink.close() not implemented/tested | test_alert_kafka.py covers emit/DLQ but no close() test | Unit: sink.close() flushes producer and closes; integration: no message loss on shutdown |
| G-05 | I (Stream-First) | V7: No Kafka authentication | No test | Contract: assert security.protocol in non-local configs; integration: TLS connection test |
| G-06 | V (Defense in Depth) | Circuit breaker 3-fail/5s — no timing test | test_circuit_breaker.py tests state transitions but not the 5s window constraint | Integration: inject 3 failures across 4.9s boundary, assert circuit opens; inject 3 across 5.1s, assert circuit stays closed |

### 1.2 High Priority Gaps (P1)

| Gap ID | Constitution Principle | Issue | Current Test State | Required Test |
|--------|----------------------|-------|-------------------|---------------|
| G-07 | VI (Immutable Log) | Postgres reconnection — no retry logic | test_alert_postgres.py tests emit but not connection loss recovery | Unit: mock psycopg2 connection drop, assert 3 retries with 1/2/4s backoff; assert DLQ on 3rd failure |
| G-08 | VIII (Observability) | Kafka metrics bridge thread safety | test_kafka_metrics_bridge.py exists but no concurrent access test | Unit: 10 threads calling bridge.update() concurrently, assert no race condition or metric corruption |
| G-09 | XVI (Module Boundaries) | V6: processing imports scoring | No import-graph test | Unit: importlib analysis asserting no pipelines.processing → pipelines.scoring import path |
| G-10 | XVI (Module Boundaries) | V5: Iceberg sink embedded in assembler | test_enrichment_pipeline.py tests coupled path | Integration: test assembler produces records WITHOUT sink; test sink consumes independently |
| G-11 | XI (Feature Serving) | Feature store p99 < 2ms under 2x peak | test_feature_serving.py exists but no load test | Load: 20,000 concurrent feature lookups/sec, assert p99 < 2ms |
| G-12 | II (Sub-100ms) | p99 < 100ms under 2x peak | test_sink_hot_path_latency.py exists, latency-bench CI stage exists but points to tests/bench/ which doesn't exist | Performance: create tests/bench/ with @pytest.mark.latency, wire to CI |

### 1.3 Medium Priority Gaps (P2)

| Gap ID | Constitution Principle | Issue | Required Test |
|--------|----------------------|-------|---------------|
| G-13 | X (Analytics) | Streamlit lag < 2s | Load: 5 concurrent sessions querying 7-day window, assert response < 2s |
| G-14 | XI (Feature Serving) | Feature staleness alert at 30s | Unit: mock feature_store_staleness_seconds gauge > 30s, assert alert fires |
| G-15 | XI (Feature Serving) | Cold-start zero-valued fallback | Unit: request features for unknown entity, assert FeatureVector with all zeros |
| G-16 | IX (Analytics-First) | Feast materialization < 5s | test_feast_materialization.py exists; verify timing assertion is present |
| G-17 | X (Analytics) | Idempotent consumer bounded eviction | Unit: consumer processes same offset twice, assert no duplicate state; assert eviction occurs after bound |
| G-18 | VII (PII) | PII masking integration test | test_pii_masker.py is unit-only; integration: full ingestion flow asserts no raw PAN/IP in output topic |
| G-19 | III (Schema Contract) | txn.enriched still JSON | Contract: assert txn.enriched uses Avro (currently would fail — tracks V1 violation) |

---

## PART 2: NEW TESTS NEEDED PER PROPOSED SPEC

### 2.1 Spec 010 — Hot Path Safety Hardening

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T010-01 | Unit | IcebergSinkBase._flush() with _table=None routes to DLQ | DLQ logger called with full envelope; buffer NOT silently cleared |
| T010-02 | Unit | IcebergSinkBase._flush() with _table=None retries on next open() | Buffered records survive failed open() and are flushed after successful open() |
| T010-03 | Unit | All scoring metrics use SafeMetric wrapper | Enumerate all Counter/Histogram in scoring/, assert each is SafeMetric |
| T010-04 | Unit | SafeMetric.inc() on failed registration doesn't crash | Mock prometheus_client registry collision, assert no exception |
| T010-05 | Unit | AlertKafkaSink.close() calls producer.flush() and producer.close() | Mock producer, assert flush(timeout=5) and close() called in order |
| T010-06 | Integration | AlertKafkaSink shutdown: no message loss | Emit 100 alerts, call close(), assert all 100 delivered (no pending) |
| T010-07 | Unit | AlertPostgresSink reconnection with exponential backoff | Mock connection drop, assert 3 retries at 1s/2s/4s intervals |
| T010-08 | Unit | AlertPostgresSink DLQ after 3 failed retries | Mock persistent connection failure, assert DLQ produce after 3rd retry |
| T010-09 | Unit | Kafka metrics bridge thread safety | 10 concurrent threads updating shared counters, assert final count correct |
| T010-10 | Unit | Kafka metrics bridge daemon thread doesn't block main thread on crash | Mock consumer.poll() exception, assert main thread continues |

### 2.2 Spec 011 — Pipeline Architecture Decoupling

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T011-01 | Unit | Import graph: processing does NOT import scoring | importlib recursive analysis, assert no path |
| T011-02 | Unit | EnrichedRecordAssembler is a pure data transformer (no sink) | Instantiate assembler without any sink parameter, assert flat_map() returns records |
| T011-03 | Unit | IcebergSinkOperator has independent lifecycle | Instantiate, call open(), invoke(), close() without assembler |
| T011-04 | Unit | PipelineConfig Pydantic model validates all sections | Construct with invalid values, assert ValidationError |
| T011-05 | Unit | AdapterRegistry loads Feast/Iceberg/GeoIP backends | Register mock adapters, assert correct backend returned |
| T011-06 | Unit | AdapterRegistry returns fallback when optional backend missing | Omit Feast from registry, assert fallback returned |
| T011-07 | Unit | job_lifecycle.py start/stop hooks work independently | Call start(), assert metrics bridge started; call stop(), assert cleanup |
| T011-08 | Unit | ThreadPoolExecutor is singleton per sink type | Create 2 IcebergEnrichedSink instances, assert same executor |
| T011-09 | Integration | Full pipeline with decoupled assembler + sink | Enriched records flow through assembler → Kafka → separate sink |
| T011-10 | Contract | Shared protocol interfaces in contracts/ package | Assert FeatureServingProtocol, AlertSinkProtocol defined |

### 2.3 Spec 012 — ML Model Serving Decoupling & Shadow Scoring

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T012-01 | Unit | InferenceClient protocol defines predict() and health() | ABC instantiation fails without implementing both methods |
| T012-02 | Unit | LocalInferenceClient returns MLScore | predict(features) returns MLScore with fraud_probability and model_version |
| T012-03 | Unit | SidecarInferenceClient timeout after 30ms | Mock HTTP endpoint with 50ms delay, assert timeout exception |
| T012-04 | Unit | SidecarInferenceClient circuit breaker integration | 3 consecutive failures → circuit open → fallback to rule-only |
| T012-05 | Unit | Shadow scoring does NOT affect production decision | Shadow rule matches, assert determination unchanged from active-only |
| T012-06 | Unit | Shadow results appended with ":shadow" suffix | Assert matched_rules contains "VEL-001:shadow" |
| T012-07 | Unit | Shadow metrics recorded separately | Assert rule_shadow_triggers_total incremented, rule_triggers_total NOT |
| T012-08 | Integration | Kill sidecar container, assert rule-only decisions resume | Circuit breaker opens, scoring continues without ML |
| T012-09 | Integration | Shadow decisions written to txn.shadow.decisions | Produce enriched records, assert shadow topic receives shadow results |
| T012-10 | Contract | MLScore schema includes model_version field | Avro schema for shadow decisions includes model_version |
| T012-11 | Load | Shadow scoring overhead < 10% latency increase | Benchmark with/without shadow, assert p99 delta < 10ms |

### 2.4 Spec 013 — Transport Security & Secrets Management

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T013-01 | Unit | SecretProvider protocol ABC | Instantiation requires get_secret() implementation |
| T013-02 | Unit | EnvSecretProvider reads from os.environ | Set env var, assert get_secret() returns value |
| T013-03 | Unit | VaultSecretProvider reads from Vault | Mock Vault client, assert correct path queried |
| T013-04 | Contract | Non-local config includes security.protocol=SASL_SSL | Parse config with FRAUDSTREAM_ENV=staging, assert SASL_SSL |
| T013-05 | Contract | DLQ records have PII masked | Deserialize DLQ envelope, assert no full PAN/IP |
| T013-06 | Integration | Kafka TLS connection succeeds with valid certs | Testcontainers Kafka with TLS, produce/consume |
| T013-07 | Integration | Kafka TLS connection fails with invalid certs | Assert connection refused with wrong cert |
| T013-08 | Unit | Management API rejects requests without API key when ENV!=dev | FastAPI test client, assert 401 without header |
| T013-09 | Unit | Management API rate limiting: 101st request in 1 min rejected | Assert 429 after 100 rapid requests |

### 2.5 Spec 014 — Deployment Pipeline & Containerisation

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T014-01 | Contract | Ingestion Dockerfile builds without error | docker build succeeds |
| T014-02 | Contract | Ingestion container exposes /healthz endpoint | HTTP GET returns 200 |
| T014-03 | Integration | Trivy scan passes with no HIGH/CRITICAL (fixable) | trivy --exit-code 1 returns 0 |
| T014-04 | Contract | Partitioning strategy: txn.* topics use account_id key | Schema + producer code analysis asserts key = account_id |
| T014-05 | Contract | Iceberg tables partitioned by date(event_time) | PyIceberg table spec asserts partition spec |

### 2.6 Spec 015 — Observability & Replay Infrastructure

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T015-01 | Unit | Trace context injected into Kafka headers | Mock producer, assert traceparent header present |
| T015-02 | Unit | Trace context extracted from Kafka headers | Mock consumer message with traceparent, assert span context restored |
| T015-03 | Integration | End-to-end trace: ingestion → processing → scoring | Assert single trace ID across all 3 service spans |
| T015-04 | Unit | Per-feature-view staleness tracking | Mock feature view with 35s staleness, assert alert metric incremented |
| T015-05 | Unit | Replay CLI reads from Iceberg, writes to replay topic | Mock Iceberg scan, assert records published to txn.replay.enriched |
| T015-06 | Unit | Replay CLI rate limiter caps TPS | Set limit to 100 TPS, assert <= 100 records/second |
| T015-07 | Integration | Replay does NOT write to production topics | Replay run, assert txn.enriched offset unchanged |

### 2.7 Spec 016 — Analytics UX & Rule Management UI

| Test ID | Layer | Description | Acceptance Criteria |
|---------|-------|-------------|-------------------|
| T016-01 | Unit | Per-session DuckDB connections are isolated | 2 concurrent sessions, assert separate connection objects |
| T016-02 | Load | 5 concurrent Streamlit sessions, 7-day query | All complete within 2s (constitution §X) |
| T016-03 | Unit | Rule CRUD API writes to txn.rules.config topic | Mock Kafka producer, assert compacted topic produced |
| T016-04 | Unit | Rule test harness evaluates synthetic transaction | Submit test txn, assert rule trigger list returned |
| T016-05 | Unit | Rule audit log records before/after diff | Change rule threshold, assert audit log entry with old/new values |
| T016-06 | Load | Redis 20,000 feature lookups/sec, p99 < 2ms | Benchmark against Redis cluster, assert latency |
| T016-07 | Unit | DLQ trend analysis computes correct rates | Feed 100 DLQ records across 10 minutes, assert rate = 10/min |

---

## PART 3: TEST PATTERNS

### 3.1 Unit Tests (tests/unit/)

**Current:** 330+ tests, 80% coverage gate. Good patterns: TDD-style with
Given/When/Then docstrings, mock-heavy for external dependencies.

**Issues:**
- Fallback Python classes tested instead of real Flink operators
- No SafeMetric validation pattern
- No import-graph verification

**Recommended patterns:**
```
Pattern: Constitution Compliance Unit Test
- Name: test_constitution_{principle}_{requirement}
- Docstring: Reference constitution principle + section
- Assert: Specific metric/behavior required by constitution
- Example: test_constitution_viii_safe_metric_wrapping()
```

```
Pattern: Import Boundary Test
- Use importlib to recursively resolve imports
- Assert no import path from module A → module B
- Run as part of unit tests (fast, no I/O)
```

```
Pattern: Protocol Conformance Test
- Assert ABC/Protocol subclasses implement all methods
- Use typing.runtime_checkable + isinstance checks
- Prevents missing method implementations
```

### 3.2 Integration Tests (tests/integration/)

**Current:** 10 tests using testcontainers + mocks. Kafka/Schema Registry/MinIO
as Docker services in CI. Good patterns: conftest.py with shared fixtures.

**Issues:**
- Circuit breaker test doesn't validate timing (3-fail/5s window)
- No PII masking end-to-end test
- No sink close/shutdown test
- Continue-on-error on PR (advisory only)

**Recommended patterns:**
```
Pattern: Failure Injection Integration Test
- Use testcontainers to start/stop services mid-test
- Assert recovery behavior with metric verification
- Timeout assertion: behavior within N seconds
- Example: stop Iceberg catalog, assert DLQ receives records within 5s
```

```
Pattern: Lifecycle Integration Test
- Test full open() → invoke() → close() cycle
- Assert no resource leaks (producer.close called, connections released)
- Assert no data loss on orderly shutdown
```

### 3.3 Contract Tests (tests/contract/)

**Current:** 2 files — schema parsing, field presence, Avro round-trip.
Also tests/contracts/test_fraud_alert_schema.py (separate directory).

**Issues:**
- Two contract directories (tests/contract/ and tests/contracts/) — consolidate
- No txn.enriched Avro contract (currently JSON — V1 violation)
- No DLQ PII masking contract
- Not run as separate CI stage

**Recommended patterns:**
```
Pattern: Schema Evolution Contract
- Register schema v1 with BACKWARD_TRANSITIVE
- Register schema v2 (with new optional field)
- Assert compatibility check passes
- Assert v2 reader can read v1 data
```

```
Pattern: Cross-Boundary Contract
- Avro schema → Iceberg table schema alignment (existing)
- Kafka key → partition strategy contract
- DLQ envelope → PII masking contract
- Config → environment contract (non-local requires SASL_SSL)
```

### 3.4 Load Tests (tests/load/)

**Current:** test_throughput.py (5,000 TPS), test_memory.py (1M accounts).
Both marked @pytest.mark.perf, not in CI. Require running infrastructure.

**Issues:**
- No feature store load test (p99 < 2ms at 20K TPS)
- No Streamlit concurrent session test
- No CI stage for load tests
- Throughput test uses simplified byte payload, not real Avro

**Recommended patterns:**
```
Pattern: Constitution Performance Gate
- Name: test_constitution_{principle}_perf_{metric}
- Use real serialization (Avro, not raw bytes)
- Assert p99, not just average
- Include warmup phase (discard first 10% of measurements)
- Report results as pytest-benchmark JSON artifacts
```

### 3.5 Chaos Tests (tests/chaos/)

**Current:** FAILURE_SCENARIOS.md with 7 manual scenarios + .gitkeep. Zero
automated tests.

**Recommended framework:**

```
Framework: pytest-chaos (custom conftest.py)
Directory: tests/chaos/
Marker: @pytest.mark.chaos
CI: Separate workflow, nightly on main

Components:
1. conftest.py with DockerFailureInjector fixture
2. chaos_toolkit.py with primitives:
   - kill_container(name, restart_after_s=None)
   - pause_network(container, duration_s)
   - corrupt_disk(container, path)
   - exhaust_memory(container, mb)
   - inject_latency(container, ms)
3. chaos_assertions.py with recovery checks:
   - assert_metric_recovered(metric, baseline, within_s)
   - assert_no_data_loss(input_count, output_count)
   - assert_dlq_depth_bounded(max_records)
   - assert_consumer_lag_recovered(group, within_s)
```

**Chaos test plan:**

| Test ID | Scenario | Target | Pass Criteria |
|---------|----------|--------|---------------|
| C-01 | TaskManager crash | Flink TM container | Lag < 5s within 60s, 0 data loss |
| C-02 | ML sidecar outage | ML serving container | Circuit open < 30s, fallback decisions incrementing |
| C-03 | Kafka broker down | Kafka container | DLQ alert < 60s, 0 data loss after recovery |
| C-04 | Iceberg catalog timeout | MinIO container | DLQ routing within 5s, no silent drops |
| C-05 | Postgres connection loss | Postgres container | 3 retries with backoff, DLQ on exhaustion |
| C-06 | Schema Registry outage | SR container | Cached schema used, new schemas fail gracefully |
| C-07 | Network partition | Docker network pause | Backpressure propagates, recovery after unpause |
| C-08 | Memory pressure | cgroup limit on Flink | GC pause handling, no OOM data loss |

---

## PART 4: TEST INFRASTRUCTURE IMPROVEMENTS

### 4.1 Chaos Test Framework

```
tests/chaos/
├── conftest.py                    # Docker fixtures, recovery helpers
├── chaos_toolkit.py               # Failure injection primitives
├── chaos_assertions.py            # Recovery assertion library
├── test_taskmanager_crash.py      # C-01
├── test_ml_sidecar_outage.py      # C-02
├── test_kafka_broker_down.py      # C-03
├── test_iceberg_catalog_timeout.py # C-04
├── test_postgres_reconnection.py  # C-05
├── test_schema_registry_outage.py # C-06
├── test_network_partition.py      # C-07
└── test_memory_pressure.py        # C-08
```

**Dependencies to add:**
- pytest-docker-tools (testcontainers lifecycle)
- docker SDK for Python (container manipulation)
- prometheus-api-client (metric assertions)

**Key fixture: DockerFailureInjector**
```python
@pytest.fixture
def failure_injector(docker_client):
    """Injects failures into Docker containers and verifies recovery."""
    class DockerFailureInjector:
        def kill(self, container_name, restart_delay_s=None): ...
        def pause_network(self, container_name, duration_s): ...
        def assert_recovery(self, metric_name, baseline, timeout_s): ...
    return DockerFailureInjector(docker_client)
```

### 4.2 Constitution Compliance Automation

Create `tests/constitution/` as a new test category:

```
tests/constitution/
├── conftest.py
├── test_principle_i_stream_first.py      # Kafka auth, partitioning
├── test_principle_ii_latency.py          # p99 < 100ms
├── test_principle_iii_schema.py          # Avro mandatory, BACKWARD_TRANSITIVE
├── test_principle_v_defense.py           # Circuit breaker 3-fail/5s
├── test_principle_vi_immutable.py        # No silent drops
├── test_principle_vii_pii.py             # PII masking
├── test_principle_viii_observability.py   # DLQ alert < 60s, SafeMetric
├── test_principle_ix_analytics.py        # Feast materialization < 5s
├── test_principle_x_consumer.py          # Bounded eviction, lag < 2s
├── test_principle_xi_features.py         # p99 < 2ms, 30s staleness
├── test_principle_xii_safe_deploy.py     # Shadow mode lifecycle
├── test_principle_xvi_boundaries.py      # Import graph
└── compliance_report.py                  # Generates constitution compliance matrix
```

**Key feature: compliance_report.py**
```python
"""
Generates a markdown report mapping each constitution principle to its test
coverage status. Run after pytest with --constitution-report flag.

Output: docs/CONSTITUTION_COMPLIANCE_REPORT.md

Columns:
  Principle | Requirement | Test ID | Status | Last Run | Notes
"""
```

**pytest plugin for constitution markers:**
```python
# conftest.py at repo root
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "constitution(principle, requirement): marks test as constitution compliance"
    )

# Usage in tests:
@pytest.mark.constitution(principle="VIII", requirement="DLQ alert <60s")
def test_dlq_alert_fires_within_60s():
    ...
```

### 4.3 Test Fixture Improvements

**Problem:** Integration tests use Python fallback classes, not real Flink operators.

**Solution:** Create a `tests/fixtures/flink_mini_cluster.py` fixture that:
1. Starts a PyFlink MiniCluster via testcontainers
2. Submits real Flink jobs
3. Validates output via Kafka consumer
4. Tears down after test

**Problem:** Duplicate enriched_record fixture across 5+ test files.

**Solution:** Create `tests/conftest.py` with shared fixtures:
```python
@pytest.fixture
def enriched_record():
    """Standard 36-field enriched record for testing."""
    return { ... }

@pytest.fixture
def fraud_alert():
    """Standard FraudAlert for testing."""
    return FraudAlert(...)

@pytest.fixture
def scoring_config():
    """ScoringConfig with test defaults."""
    return ScoringConfig()
```

### 4.4 Test Data Generators

Create `tests/generators/` for deterministic test data:
```
tests/generators/
├── transactions.py    # random_transaction(seed=) → dict
├── enriched.py        # random_enriched_record(seed=) → dict
├── alerts.py          # random_fraud_alert(seed=) → FraudAlert
└── features.py        # random_feature_vector(seed=) → FeatureVector
```

### 4.5 Consolidate Contract Test Directories

**Current:** tests/contract/ AND tests/contracts/ (two separate directories).
**Action:** Merge tests/contracts/test_fraud_alert_schema.py into tests/contract/
and delete tests/contracts/.

---

## PART 5: CI PIPELINE CHANGES

### 5.1 Current CI Pipeline (6 stages)

```
1. code-quality      (ruff check + format) — BLOCKING
2. unit-tests        (pytest tests/unit/ --cov-fail-under=80) — BLOCKING
3. schema-integrity  (diff specs vs pipelines schemas) — BLOCKING
3b. schema-registry  (SR compat with testcontainers) — BLOCKING
4. security-scan     (pip-audit + Trivy) — BLOCKING
5. integration-tests (pytest tests/integration/) — Advisory on PR, blocking on main
6. build-images      (Docker build + Trivy + GHCR push) — main only
6b. latency-bench    (tests/bench/ — doesn't exist yet) — PR to main only
6c. manual-approval  (schema/Flink changes) — conditional
7-9. deploy-*        (absent — TD-003)
```

### 5.2 Proposed CI Pipeline (12 stages)

```
STAGE 1: code-quality              (unchanged)
  └─ ruff check + format

STAGE 2: unit-tests                (ENHANCED)
  └─ pytest tests/unit/ --cov-fail-under=80
  └─ NEW: Include tests/constitution/ (unit-level constitution checks)
  └─ NEW: Generate constitution compliance report artifact

STAGE 3a: schema-integrity         (unchanged)
STAGE 3b: schema-registry-compat   (unchanged)

STAGE 4: contract-tests            (NEW — BLOCKING)
  └─ pytest tests/contract/ -v
  └─ Consolidate tests/contract/ and tests/contracts/
  └─ Include: Avro schema parsing, field presence, PII masking, Iceberg alignment
  └─ Include: DLQ PII masking contract (T013-05)
  └─ Include: Config security.protocol contract (T013-04)

STAGE 5: security-scan             (unchanged)

STAGE 6: integration-tests         (ENHANCED — BLOCKING on main)
  └─ pytest tests/integration/ -m integration --timeout=120
  └─ NEW: Circuit breaker timing test (3-fail/5s)
  └─ NEW: PII masking e2e test
  └─ NEW: Sink lifecycle test (close/shutdown)
  └─ NEW: Postgres reconnection test

STAGE 7: latency-bench             (ENHANCED — PR to main)
  └─ Create tests/bench/ directory
  └─ pytest tests/bench/ -m latency
  └─ Assert p99 < 100ms (Constitution Principle II)
  └─ Upload benchmark results as artifact

STAGE 8: build-images              (ENHANCED — main only)
  └─ Add ingestion Dockerfile (spec 014)
  └─ Matrix: flink-worker, producer-api, load-generator, ingestion-api

STAGE 9: load-tests                (NEW — nightly + PR to main)
  └─ pytest tests/load/ -m perf
  └─ Requires: Docker Compose stack
  └─ Tests: throughput (5K TPS), memory (1M accounts), feature store (20K TPS)
  └─ Upload results as benchmark artifacts
  └─ NOT BLOCKING on PR (advisory), BLOCKING on release tags

STAGE 10: chaos-tests              (NEW — nightly on main)
  └─ pytest tests/chaos/ -m chaos --timeout=600
  └─ Requires: Full Docker Compose stack
  └─ Tests: C-01 through C-08
  └─ Upload Prometheus metric snapshots as artifacts
  └─ NOT BLOCKING (advisory) — results posted to Slack/notification channel

STAGE 11: deploy-dev               (NEW — spec 014)
  └─ Flink REST API job submission
  └─ Smoke test: poll /jobs until RUNNING
  └─ Observability gate: enrichment_latency_ms returns data within 30s

STAGE 12: constitution-compliance   (NEW — BLOCKING on release)
  └─ Run full constitution compliance suite
  └─ Generate CONSTITUTION_COMPLIANCE_REPORT.md
  └─ Assert zero FAIL items for NON-NEGOTIABLE principles (I, III, IX, XI, XII)
  └─ Advisory for non-NON-NEGOTIABLE principles
  └─ Artifact: compliance report attached to release

ci-summary                          (ENHANCED)
  └─ Add contract-tests, load-tests, chaos-tests to summary table
  └─ Add constitution-compliance result for releases
```

### 5.3 CI Configuration Changes

**New workflow files needed:**
```
.github/workflows/ci.yml                    # Enhanced (stages 1-8, 12)
.github/workflows/nightly-load-chaos.yml    # New (stages 9-10, nightly)
.github/workflows/deploy.yml               # New (stage 11, spec 014)
```

**pytest.ini / pyproject.toml markers to add:**
```toml
[tool.pytest.ini_options]
markers = [
    "perf: Performance/load tests (deselect with -m 'not perf')",
    "chaos: Chaos engineering tests (deselect with -m 'not chaos')",
    "integration: Integration tests requiring Docker",
    "latency: Latency benchmark tests",
    "constitution: Constitution compliance tests",
    "slow: Tests taking > 30s",
]
```

**Coverage increase path:**
```
Current:  80% (CI gate)
Target after spec 010: 82% (new unit tests for gaps G-01 through G-10)
Target after spec 011: 85% (decoupled modules are independently testable)
Target after spec 013: 87% (security config tests)
Target after spec 015: 90% (tracing instrumentation tests)
Final target: 90% CI gate by v2.1.0
```

---

## PART 6: IMPLEMENTATION PRIORITY

### Phase 1 — Immediate (with Spec 010)

1. Write tests T010-01 through T010-10 (closes G-01, G-02, G-03, G-04, G-07, G-08)
2. Create tests/constitution/ framework with markers
3. Write G-06 circuit breaker timing test
4. Add contract-tests stage to CI
5. Consolidate tests/contract/ and tests/contracts/
6. Create tests/bench/ directory with latency benchmark (closes G-12)
7. Create shared test fixtures in tests/conftest.py

### Phase 2 — With Spec 011

8. Write T011-01 through T011-10 (closes G-09, G-10)
9. Write import-graph verification test
10. Create tests/generators/ for test data factories

### Phase 3 — With Spec 012

11. Write T012-01 through T012-11
12. Create chaos test framework (conftest.py, chaos_toolkit.py)
13. Implement C-02 (ML sidecar outage) as first chaos test

### Phase 4 — With Specs 013-014

14. Write T013-01 through T013-09 (closes G-05, G-18)
15. Write T014-01 through T014-05
16. Add nightly-load-chaos.yml workflow
17. Implement C-03, C-04, C-05 chaos tests

### Phase 5 — With Specs 015-016

18. Write T015-01 through T015-07
19. Write T016-01 through T016-07 (closes G-11, G-13)
20. Implement remaining chaos tests (C-01, C-06, C-07, C-08)
21. Create constitution compliance report generator
22. Add constitution-compliance stage to release workflow
23. Raise coverage gate to 90%

---

## APPENDIX A: PRE-PRODUCTION CHECKLIST TEST MAPPING

| Checklist Item | Test ID(s) | Status |
|---------------|-----------|--------|
| Idempotent consumers bounded eviction | T010-09, G-17 | NOT TESTED |
| DLQ alert < 60s | C-03 | MANUAL ONLY |
| Circuit breaker 3-fail/5s | G-06, test_circuit_breaker.py (partial) | PARTIAL |
| PII masking integration test | G-18, T013-05 | NOT TESTED |
| p99 < 100ms under 2x peak | G-12, latency-bench | NOT WIRED |
| 80% coverage | CI gate (unit-tests) | PASSING |
| Feast materialization < 5s | test_feast_materialization.py | EXISTS |
| Point-in-time correctness | test_feast_materialization.py (partial) | PARTIAL |
| Analytics isolation | test_analytics_integration.py | EXISTS |
| Streamlit lag < 2s | G-13, T016-02 | NOT TESTED |
| Feature store p99 < 2ms under 2x peak | G-11, T016-06 | NOT TESTED |
| Feature staleness alert at 30s | G-14, T015-04 | NOT TESTED |
| Cold-start zero-valued fallback | G-15, test_feature_serving_client.py (partial) | PARTIAL |

---

## APPENDIX B: TEST FILE INVENTORY

| Directory | Files | Tests (est.) | In CI | Notes |
|-----------|-------|-------------|-------|-------|
| tests/unit/ | 28 .py | 330+ | YES (blocking) | 80% coverage gate |
| tests/unit/scoring/ | 12 .py | ~150 | YES | Good coverage |
| tests/unit/processing/ | 8 .py | ~100 | YES | Fallback classes, not Flink |
| tests/integration/ | 10 .py | ~40 | YES (advisory PR) | testcontainers |
| tests/contract/ | 2 .py | ~15 | NO | Not in CI! |
| tests/contracts/ | 1 .py | ~5 | NO | Duplicate dir, not in CI |
| tests/load/ | 2 .py | 3 | NO | @pytest.mark.perf |
| tests/performance/ | 1 .py | 1 | NO | sink hot path |
| tests/chaos/ | 0 .py | 0 | NO | FAILURE_SCENARIOS.md only |
| tests/bench/ | — | — | NO | Directory doesn't exist |
| tests/constitution/ | — | — | NO | Proposed new |
