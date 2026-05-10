# FraudStream Refactoring — Top 10 Implementation Notes

Generated: 2026-05-09
Author: ENGINEER agent

---

## Change 1: Extract Iceberg Sink from EnrichedRecordAssembler

### Problem
EnrichedRecordAssembler (enricher.py) owns IcebergEnrichedSink lifecycle directly:
opens it in open(), calls invoke() in flat_map(), closes it in close(). This
couples record assembly (pure transform) with Iceberg I/O (5s flush timeout,
circuit breaker, Feast materialization). Any Iceberg stall blocks flat_map().

### Files to modify
- pipelines/processing/operators/enricher.py — REMOVE all IcebergEnrichedSink
  references (constructor param, open/close lifecycle, invoke calls in flat_map
  and assemble methods)
- pipelines/processing/job.py — ADD a separate .map(_IcebergEnrichedSinkFunction())
  downstream of EnrichedRecordAssembler, same pattern as scoring's
  _IcebergSinkFunction in job_extension.py lines 144-167

### Files to create
- pipelines/processing/operators/iceberg_enriched_sink_function.py — new Flink
  MapFunction wrapper around IcebergEnrichedSink. Follow exact pattern of
  _IcebergSinkFunction in scoring/job_extension.py:

  ```
  try:
      from pyflink.datastream.functions import MapFunction

      class IcebergEnrichedSinkFunction(MapFunction):
          def open(self, runtime_context):
              self._sink = IcebergEnrichedSink()
              self._sink.open(runtime_context)
          def map(self, value):
              self._sink.invoke(value, None)
              return value
          def close(self):
              if self._sink:
                  self._sink.close()
  except ImportError:
      pass
  ```

### Pattern to follow
scoring/job_extension.py lines 144-167 (_IcebergSinkFunction) — identical
lifecycle wrapper pattern. Also shared/iceberg_sink_base.py for the base class
that IcebergEnrichedSink already extends.

### Migration strategy
YES — fully incremental:
1. Create IcebergEnrichedSinkFunction (new file, zero risk)
2. Wire it in job.py after EnrichedRecordAssembler output
3. Remove sink param/lifecycle from EnrichedRecordAssembler
4. Remove sink calls from assemble() test helper
5. Update tests that inject sink=... into EnrichedRecordAssembler

### Test strategy
- Unit: test EnrichedRecordAssembler.flat_map/assemble returns correct dict
  WITHOUT any sink (pure transform test)
- Unit: test IcebergEnrichedSinkFunction.map calls sink.invoke and returns value
  (mock IcebergEnrichedSink)
- Integration: existing Flink integration test should still see records in Iceberg
  table via the new downstream operator

### Risks
- If job.py wires the sink AFTER Kafka producer, Iceberg writes happen later.
  Mitigation: wire it BEFORE Kafka produce, or branch the stream.
- assemble() is used in unit tests — callers that relied on implicit Iceberg
  writes need updating. Low risk: test code only.

### Rollback
Revert 3 files (enricher.py, job.py, new file). No data format changes.

---

## Change 2: Unify Config into BaseConfig / InfraConfig

### Problem
ProcessorConfig (processing/config.py) and ScoringConfig (scoring/config.py) both
define kafka_brokers and schema_registry_url with identical env var names and
defaults. ProcessorConfig has ZERO validation (no __post_init__). shared/config.py
has IcebergSinkConfig and helper functions but no shared infra base.

### Files to modify
- pipelines/shared/config.py — ADD InfraConfig base dataclass:
  ```python
  @dataclass
  class InfraConfig:
      kafka_brokers: str = field(
          default_factory=lambda: os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
      )
      schema_registry_url: str = field(
          default_factory=lambda: os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")
      )
      s3_endpoint: str = field(
          default_factory=lambda: os.environ.get("S3_ENDPOINT", "http://minio:9000")
      )
      s3_access_key: str = field(
          default_factory=lambda: os.environ.get("S3_ACCESS_KEY", "minioadmin")
      )
      s3_secret_key: str = field(
          default_factory=lambda: os.environ.get("S3_SECRET_KEY", "minioadmin")
      )

      def __post_init__(self):
          if not self.kafka_brokers:
              raise ValueError("kafka_brokers must not be empty")
          if not self.schema_registry_url:
              raise ValueError("schema_registry_url must not be empty")
  ```

- pipelines/processing/config.py — ProcessorConfig inherits InfraConfig, REMOVE
  kafka_brokers, schema_registry_url, s3_* fields. ADD __post_init__ calling
  super().__post_init__() plus own validation.

- pipelines/scoring/config.py — ScoringConfig inherits InfraConfig, REMOVE
  kafka_brokers, schema_registry_url.

### Pattern to follow
ScoringConfig.__post_init__ (scoring/config.py lines 50-76) — already validates
circuit-breaker params. Replicate this rigor for ProcessorConfig. Also follow
shared/config.py's _parse_int/_parse_float helpers for type-safe env parsing.

### Migration strategy
YES — incremental:
1. Add InfraConfig to shared/config.py (additive, no breakage)
2. Make ProcessorConfig(InfraConfig) — remove duplicate fields, add super().__post_init__()
3. Make ScoringConfig(InfraConfig) — remove duplicate fields
4. Verify all tests pass (field names unchanged, so all .kafka_brokers etc still work)

### Test strategy
- Unit: test InfraConfig validation (empty brokers raises ValueError)
- Unit: test ProcessorConfig inherits InfraConfig defaults
- Unit: test ScoringConfig inherits InfraConfig defaults
- Regression: existing config tests for both ProcessorConfig/ScoringConfig unchanged

### Risks
- Python dataclass inheritance with default_factory fields can cause MRO issues
  if child adds non-default fields before parent's defaults. Mitigation: all
  InfraConfig fields have defaults; child fields also have defaults (already true).
- IcebergSinkConfig in shared/config.py is separate — leave it; it's Iceberg-specific.

### Rollback
Revert 3 files. No runtime data changes.

---

## Change 3: Replace try/except ImportError with Adapter Pattern

### Problem
4 operator files (velocity.py, geolocation.py, device.py, enricher.py) and
job_extension.py all use try/except ImportError to define Flink vs pure-Python
classes. This duplicates logic, hides import errors, and makes IDE navigation
impossible.

### Files to create
- pipelines/shared/flink_compat.py — adapter module:

  ```python
  """Flink compatibility layer — provides base classes or stubs."""
  from __future__ import annotations
  import logging

  logger = logging.getLogger(__name__)

  try:
      from pyflink.datastream import KeyedProcessFunction, RuntimeContext
      from pyflink.datastream.functions import FlatMapFunction, MapFunction
      from pyflink.common import Types
      from pyflink.common.time import Time
      from pyflink.datastream.state import (
          MapStateDescriptor, StateTtlConfig, ValueStateDescriptor,
      )
      from pyflink.datastream import OutputTag

      FLINK_AVAILABLE = True

  except ImportError:
      FLINK_AVAILABLE = False

      # Stubs that satisfy isinstance checks and let pure-Python tests work
      class KeyedProcessFunction:
          def open(self, runtime_context): pass
          def close(self): pass
          def on_timer(self, timestamp, ctx): pass

      class FlatMapFunction:
          def open(self, runtime_context): pass
          def close(self): pass

      class MapFunction:
          def open(self, runtime_context): pass
          def close(self): pass

      class RuntimeContext: pass
      class Types:
          @staticmethod
          def LONG(): return None
          @staticmethod
          def INT(): return None
          # ... etc

      class Time:
          @staticmethod
          def hours(h): return None
          @staticmethod
          def days(d): return None

      class OutputTag:
          def __init__(self, *a, **kw): pass

      class StateTtlConfig:
          class UpdateType:
              OnCreateAndWrite = None
          @staticmethod
          def new_builder(t):
              return _TtlBuilder()

      class _TtlBuilder:
          def set_update_type(self, _): return self
          def build(self): return None

      class ValueStateDescriptor:
          def __init__(self, *a): pass
          def enable_time_to_live(self, _): pass

      class MapStateDescriptor:
          def __init__(self, *a): pass
          def enable_time_to_live(self, _): pass
  ```

### Files to modify
- pipelines/processing/operators/velocity.py — REMOVE try/except block, import
  from flink_compat, single class definition
- pipelines/processing/operators/geolocation.py — same
- pipelines/processing/operators/device.py — same
- pipelines/processing/operators/enricher.py — same
- pipelines/scoring/job_extension.py — same for _Flink* wrapper classes

### Pattern to follow
This is a new pattern but inspired by:
- shared/dlq_protocol.py — Protocol-based abstraction
- scoring/types.py — clean dataclass definitions without Flink deps
The key insight: define stubs ONCE centrally, not N times in each operator.

### Migration strategy
YES — incremental per file:
1. Create flink_compat.py (zero risk, additive)
2. Migrate ONE operator (e.g., device.py — simplest) to validate approach
3. Migrate remaining operators one-by-one
4. Each migration is a standalone PR

### Test strategy
- Unit: test FLINK_AVAILABLE flag in both environments
- Unit: test stub classes have expected methods (open, close, etc.)
- Unit: existing operator tests pass WITHOUT pyflink installed
- Integration: Flink job tests pass WITH pyflink installed (stubs not used)

### Risks
- Stub classes may not perfectly match Flink API surface. Mitigation: stubs only
  need methods that operator code actually calls. Add methods as needed.
- Type checkers may complain about stub classes. Mitigation: use TYPE_CHECKING
  guard for type annotations.

### Rollback
Per-file revert. Old try/except pattern still works.

---

## Change 4: Add Kafka TLS/SASL Configuration

### Problem
All Kafka Producer/Consumer instances use only {"bootstrap.servers": ...} with
no TLS/SASL config. Production Kafka clusters require SASL_SSL.

### Files to modify
- pipelines/shared/config.py — ADD KafkaSecurityConfig:
  ```python
  @dataclass
  class KafkaSecurityConfig:
      security_protocol: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
      )
      sasl_mechanism: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SASL_MECHANISM", "")
      )
      sasl_username: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SASL_USERNAME", "")
      )
      sasl_password: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SASL_PASSWORD", "")
      )
      ssl_ca_location: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SSL_CA_LOCATION", "")
      )
      ssl_cert_location: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SSL_CERT_LOCATION", "")
      )
      ssl_key_location: str = field(
          default_factory=lambda: os.environ.get("KAFKA_SSL_KEY_LOCATION", "")
      )

      def to_confluent_config(self) -> dict:
          """Return librdkafka config dict for confluent-kafka Producer/Consumer."""
          cfg = {}
          if self.security_protocol != "PLAINTEXT":
              cfg["security.protocol"] = self.security_protocol
          if self.sasl_mechanism:
              cfg["sasl.mechanism"] = self.sasl_mechanism
              cfg["sasl.username"] = self.sasl_username
              cfg["sasl.password"] = self.sasl_password
          if self.ssl_ca_location:
              cfg["ssl.ca.location"] = self.ssl_ca_location
          if self.ssl_cert_location:
              cfg["ssl.certificate.location"] = self.ssl_cert_location
          if self.ssl_key_location:
              cfg["ssl.key.location"] = self.ssl_key_location
          return cfg
  ```

- pipelines/shared/config.py — ADD to InfraConfig (from Change 2):
  ```python
  kafka_security: KafkaSecurityConfig = field(default_factory=KafkaSecurityConfig)
  ```

### Files to create
- pipelines/shared/kafka_producer_factory.py:
  ```python
  def build_producer_config(infra: InfraConfig, **overrides) -> dict:
      cfg = {"bootstrap.servers": infra.kafka_brokers}
      cfg.update(infra.kafka_security.to_confluent_config())
      cfg.update(overrides)
      return cfg
  ```

### Files to modify (consumers)
- pipelines/scoring/sinks/alert_kafka.py line 45 — use build_producer_config
- pipelines/processing/shared/dlq_sink.py line 116 — use build_producer_config
- pipelines/processing/kafka_metrics_bridge.py lines 104-111, 154-160 — use
  build_producer_config (consumer variant)

### Pattern to follow
dlq_sink.py's Producer config dict (lines 116-123) — already structured, just
needs security fields merged in via to_confluent_config().

### Migration strategy
YES — incremental + backward compatible:
1. Add KafkaSecurityConfig with PLAINTEXT defaults (zero behavior change)
2. Add kafka_producer_factory.py
3. Migrate producers/consumers one at a time to use factory
4. When all migrated, set KAFKA_SECURITY_PROTOCOL=SASL_SSL in production

### Test strategy
- Unit: test KafkaSecurityConfig.to_confluent_config() returns correct dict for
  PLAINTEXT (empty), SASL_SSL (full), SSL-only (partial)
- Unit: test build_producer_config merges security + overrides
- Integration: existing tests unaffected (PLAINTEXT default)

### Risks
- Certificate file paths must exist at runtime. Mitigation: validate in
  __post_init__ only when security_protocol != PLAINTEXT.
- Password in env vars. Mitigation: document that KAFKA_SASL_PASSWORD should
  come from a secrets manager / k8s secret, not plain env.

### Rollback
Revert factory file + config additions. Producers fall back to bare bootstrap.servers.

---

## Change 5: Add OpenTelemetry Trace Propagation Through Kafka Headers

### Problem
shared/telemetry.py creates OTel tracers but traces do not propagate across
Kafka topic boundaries. A transaction's trace stops at the producer and a new
unlinked trace starts at the consumer.

### Files to create
- pipelines/shared/kafka_otel.py:
  ```python
  """OTel context propagation via Kafka headers."""
  from opentelemetry import context, trace
  from opentelemetry.propagators import textmap
  from opentelemetry.propagate import get_global_textmap, inject, extract

  class KafkaHeaderCarrier(textmap.Getter, textmap.Setter):
      """Adapter between OTel TextMap propagation and Kafka message headers.

      Kafka headers: list[tuple[str, bytes]]
      OTel expects: Mapping[str, str]
      """

      def get(self, carrier: list, key: str) -> list[str] | None:
          return [v.decode() for k, v in carrier if k == key]

      def keys(self, carrier: list) -> list[str]:
          return list({k for k, _ in carrier})

      def set(self, carrier: list, key: str, value: str) -> None:
          carrier.append((key, value.encode()))

  _carrier = KafkaHeaderCarrier()

  def inject_trace_headers(headers: list[tuple[str, bytes]] | None = None) -> list[tuple[str, bytes]]:
      """Inject current OTel context into Kafka headers list."""
      if headers is None:
          headers = []
      inject(headers, setter=_carrier)
      return headers

  def extract_trace_context(headers: list[tuple[str, bytes]] | None) -> context.Context:
      """Extract OTel context from Kafka message headers."""
      if not headers:
          return context.get_current()
      return extract(headers, getter=_carrier)
  ```

### Files to modify
- pipelines/scoring/sinks/alert_kafka.py — in emit(), inject headers:
  ```python
  from pipelines.shared.kafka_otel import inject_trace_headers
  headers = inject_trace_headers()
  self._producer.produce(..., headers=headers)
  ```

- pipelines/processing/kafka_metrics_bridge.py — in consumer threads, extract:
  ```python
  from pipelines.shared.kafka_otel import extract_trace_context
  ctx = extract_trace_context(msg.headers())
  with trace.get_tracer(__name__).start_as_current_span("process_alert", context=ctx):
      _process_alert_message(...)
  ```

- pipelines/processing/shared/dlq_sink.py — in send(), inject headers into
  DLQ messages for traceability

### Pattern to follow
shared/telemetry.py — already sets up OTel tracer provider. This change adds
the propagation layer that connects traces across services. The W3C TraceContext
propagator (default in opentelemetry-api) is used.

### Migration strategy
YES — fully incremental:
1. Create kafka_otel.py (additive, no behavior change)
2. Add inject to producers (adds headers; consumers ignore unknown headers)
3. Add extract to consumers (reads headers; missing headers = new trace)
4. Each step is independently deployable

### Test strategy
- Unit: test inject_trace_headers produces traceparent header
- Unit: test extract_trace_context reconstructs span context
- Unit: test roundtrip inject -> extract preserves trace_id + span_id
- Integration: end-to-end test that a trace from ingestion API appears in
  scoring consumer spans (requires Jaeger/Zipkin in CI)

### Risks
- Header size: W3C traceparent is ~55 bytes. Negligible vs message payload.
- OTel SDK not installed: inject/extract degrade to no-op (safe by design).
- Kafka header format varies (confluent-kafka uses list[tuple[str, bytes]],
  kafka-python uses different format). Mitigation: we only use confluent-kafka.

### Rollback
Remove kafka_otel.py + revert 3 call sites. No data format changes.

---

## Change 6: Fix Scoring Metrics with SafeMetric Wrapper

### Problem
scoring/metrics.py lines 9-65 define raw Counter/Histogram without _SafeMetric
wrapper. If prometheus_client raises (e.g., duplicate registration), scoring
pipeline crashes. processing/metrics.py wraps everything in _SafeMetric (correct).
Some scoring metrics (lines 73-81) DO use _SafeMetric — inconsistent.

### Files to modify
- pipelines/scoring/metrics.py — wrap ALL bare Counter/Histogram with _SafeMetric:

  ```python
  # BEFORE:
  feature_store_fallback_total = Counter("feature_store_fallback_total", ...)
  # AFTER:
  feature_store_fallback_total = _SafeMetric(Counter("feature_store_fallback_total", ...))
  ```

  Affected metrics (lines 9-65):
  - feature_store_fallback_total
  - feature_store_miss_total
  - feature_store_retrieval_seconds
  - rule_evaluations_total
  - rule_flags_total
  - rule_shadow_triggers_total
  - rule_shadow_fp_total
  - rule_triggers_total
  - rule_active_fp_total

### Pattern to follow
processing/metrics.py — EVERY metric is wrapped:
  enrichment_latency_ms = _SafeMetric(Histogram(...))
  dlq_events_total = _SafeMetric(Counter(...))

### Migration strategy
YES — single-file change. All callers already use .labels().inc() / .observe()
which _SafeMetric proxies transparently.

### Test strategy
- Unit: verify all scoring metric objects are _SafeMetric instances
- Unit: verify .labels().inc() chain works (already tested via _SafeMetric tests)
- Regression: record_evaluation(), record_flag() etc. still work (they call
  .labels().inc() which SafeMetric supports)

### Risks
- VERY LOW. _SafeMetric is a transparent proxy. Only behavioral change: errors
  are swallowed instead of raised.
- Callers that check isinstance(metric, Counter) will fail. Grep shows no such
  checks exist in the codebase.

### Rollback
Revert scoring/metrics.py. One file.

---

## Change 7: AlertKafkaSink close() + AlertPostgresSink Reconnection

### Problem
A) alert_kafka.py has no close() method. Producer is never flushed on shutdown
   (flush() exists but close() is missing — _AlertSinkFunction.close() calls
   flush() but not a proper close).
B) alert_postgres.py catches persist errors and rolls back, but if the connection
   itself is dead (e.g., TCP reset), all subsequent persist() calls fail
   permanently. No reconnection logic.

### Files to modify
- pipelines/scoring/sinks/alert_kafka.py — ADD close():
  ```python
  def close(self) -> None:
      if self._producer:
          self._producer.flush(timeout=10)
          self._producer = None
  ```

- pipelines/scoring/sinks/alert_postgres.py — ADD reconnection in persist():
  ```python
  def persist(self, alert: FraudAlert) -> None:
      try:
          self._ensure_connection()
          with self._conn.cursor() as cur:
              cur.execute(_INSERT_SQL, (...))
          self._conn.commit()
      except Exception as exc:
          logger.warning(...)
          self._try_rollback()
          self._conn = None  # force reconnect next call
          dlq_logger.warning(...)

  def _ensure_connection(self) -> None:
      if self._conn is None or self._conn.closed:
          import psycopg2
          self._conn = psycopg2.connect(self._config.fraud_alerts_db_url)
          logger.info("Reconnected to PostgreSQL")
  ```

### Pattern to follow
- dlq_sink.py ProcessingDLQSink — has clean constructor + send pattern
- iceberg_sink_base.py — has proper open/close lifecycle with error handling

### Migration strategy
YES — two independent changes:
1. Add close() to AlertKafkaSink (additive)
2. Add reconnection to AlertPostgresSink (behavioral change but safe — currently
   all errors after connection loss are silently logged anyway)

### Test strategy
- Unit: test AlertKafkaSink.close() calls producer.flush()
- Unit: test AlertPostgresSink reconnects after connection failure
- Unit: mock psycopg2.connect to simulate connection loss + recovery
- Integration: kill PostgreSQL mid-stream, verify alerts resume after restart

### Risks
- Reconnection could cause brief duplicate alerts if original commit succeeded
  but ACK was lost. Mitigation: ON CONFLICT DO NOTHING already handles this.
- flush(timeout=10) could block shutdown. Mitigation: 10s is reasonable for
  graceful shutdown.

### Rollback
Revert 2 files. Behavior returns to current (no close, no reconnect).

---

## Change 8: Fix Reverse Dependency processing -> scoring

### Problem
kafka_metrics_bridge.py (processing/) imports rule_flags_total and
rule_evaluations_total from scoring/metrics.py. This creates a reverse dependency
(processing depends on scoring). The metrics bridge conceptually belongs in
scoring or shared.

### Files to modify
- MOVE kafka_metrics_bridge.py from pipelines/processing/ to pipelines/shared/
  OR pipelines/scoring/

  Recommendation: Move to pipelines/shared/kafka_metrics_bridge.py because it
  bridges BOTH pipelines.

- pipelines/processing/job.py (or wherever start() is called) — update import:
  ```python
  # BEFORE:
  from pipelines.processing import kafka_metrics_bridge
  # AFTER:
  from pipelines.shared import kafka_metrics_bridge
  ```

### Alternative (lighter touch)
Keep the file in processing/ but extract the scoring metric imports to be lazy:
```python
# Instead of top-level:
from pipelines.scoring.metrics import rule_flags_total
# Use:
def _get_rule_flags_total():
    from pipelines.scoring.metrics import rule_flags_total
    return rule_flags_total
```
This already partially happens (line 149 does lazy import). Make line 35 also lazy.

### Pattern to follow
The file itself already does lazy imports in _enriched_consumer_thread (line 149).
Just make _alerts_consumer_thread's usage of rule_flags_total also lazy (move
the import from line 35 into the function body at line 83).

### Migration strategy
YES — two options:
A) Quick fix: move line 35 import into _process_alert_message body (5 min)
B) Full fix: move entire file to shared/ (requires updating all import sites)

Recommend A first, B in a follow-up.

### Test strategy
- Unit: test _process_alert_message works without top-level scoring import
- Import test: verify `import pipelines.processing` does not transitively
  import pipelines.scoring

### Risks
- LOW. The bridge is daemon threads — moving it doesn't affect data flow.
- If moved to shared/, need to verify shared/ doesn't import processing/ or
  scoring/ at module level (it doesn't currently).

### Rollback
Move file back / revert import change.

---

## Change 9: Split job_extension.py

### Problem
job_extension.py (255 lines) contains: _FeatureEnrichmentFunction (domain logic),
_build_fraud_alert (domain logic), _evaluate_transaction (domain logic), 3 Flink
wrapper classes (_FlinkFeatureEnrichmentFunction, _AlertSinkFunction,
_IcebergSinkFunction), wire_rule_evaluator (orchestration), _build_fraud_decision
(domain logic). Too many responsibilities.

### Files to create
- pipelines/scoring/evaluation.py — extract pure functions:
  - _build_fraud_alert (line 50)
  - _build_fraud_decision (line 210)
  - _evaluate_transaction (line 65)

- pipelines/scoring/operators/feature_enrichment.py — extract:
  - _FeatureEnrichmentFunction (line 12)

- pipelines/scoring/operators/flink_wrappers.py — extract Flink wrappers:
  - _FlinkFeatureEnrichmentFunction (line 87)
  - _AlertSinkFunction (line 101)
  - _IcebergSinkFunction (line 144)

### Files to modify
- pipelines/scoring/job_extension.py — becomes thin orchestrator:
  - wire_rule_evaluator() remains, imports from new modules

### Pattern to follow
- processing/operators/ directory — each operator in its own file
- processing/operators/enricher.py — separates pure _assemble_record from Flink class
- scoring/types.py — clean domain types in dedicated file

### Migration strategy
YES — incremental:
1. Create evaluation.py with pure functions, add re-exports in job_extension.py
2. Create operators/ files, add re-exports
3. Update wire_rule_evaluator imports
4. Remove re-exports once all callers updated

### Test strategy
- Unit: existing tests for _evaluate_transaction etc. should just need import
  path updates
- Smoke: wire_rule_evaluator still callable with same args/behavior

### Risks
- Import cycles if evaluation.py imports from job_extension.py. Mitigation:
  evaluation.py imports only from types.py and telemetry.py (no cycles).
- External callers importing from job_extension.py break. Mitigation: add
  re-exports during transition.

### Rollback
Delete new files, revert job_extension.py re-exports.

---

## Change 10: Thread Safety in kafka_metrics_bridge.py

### Problem
start() (line 182) can be called multiple times. It clears _stop_event and
spawns new threads each time without checking if threads are already running.
This creates duplicate consumer threads competing for the same consumer group,
causing rebalance storms.

### Files to modify
- pipelines/processing/kafka_metrics_bridge.py (or shared/ after Change 8):

  Add a module-level lock and thread references:
  ```python
  _lock = threading.Lock()
  _threads: list[threading.Thread] = []

  def start(brokers, alerts_topic, enriched_topic, rules_yaml_path) -> None:
      with _lock:
          # If threads are alive, no-op
          if _threads and all(t.is_alive() for t in _threads):
              logger.info("Metrics bridge already running — skipping start()")
              return

          rule_family_map = _build_rule_family_map(rules_yaml_path)
          _stop_event.clear()
          _threads.clear()

          t1 = threading.Thread(
              target=_alerts_consumer_thread,
              args=(brokers, alerts_topic, rule_family_map),
              daemon=True,
              name="metrics-bridge-alerts",
          )
          t2 = threading.Thread(
              target=_enriched_consumer_thread,
              args=(brokers, enriched_topic, rule_family_map),
              daemon=True,
              name="metrics-bridge-enriched",
          )
          t1.start()
          t2.start()
          _threads.extend([t1, t2])

          logger.info("Kafka metrics bridge started")

  def stop() -> None:
      with _lock:
          _stop_event.set()
          for t in _threads:
              t.join(timeout=5.0)
          _threads.clear()
  ```

### Pattern to follow
Standard Python daemon thread lifecycle pattern. The existing code already has
_stop_event; this adds the missing guard.

### Migration strategy
YES — single file, backward compatible. Callers already call start() once; this
just makes repeated calls safe.

### Test strategy
- Unit: call start() twice, assert only 2 threads created (not 4)
- Unit: call stop(), assert _stop_event is set and threads are joined
- Unit: call start() after stop(), assert threads restart

### Risks
- VERY LOW. Only adds a guard and tracking. Daemon threads still auto-exit
  when main process exits.
- join(timeout=5.0) could delay shutdown by 5s if threads are stuck in poll().
  Acceptable for graceful shutdown.

### Rollback
Revert single file.

---

## Summary: Dependency Graph and Execution Order

Recommended execution order (respects dependencies):

  Phase 1 (no dependencies, parallel-safe):
    Change 3  — flink_compat.py adapter (unblocks cleaner operator code)
    Change 6  — SafeMetric in scoring/metrics.py (5 min, zero risk)
    Change 10 — Thread safety in kafka_metrics_bridge (5 min, zero risk)

  Phase 2 (depends on Phase 1):
    Change 2  — Unify config (InfraConfig base class)
    Change 8  — Fix reverse dependency (quick: lazy import)
    Change 7  — AlertKafkaSink close + Postgres reconnection

  Phase 3 (depends on Phase 2):
    Change 4  — Kafka TLS/SASL (needs InfraConfig from Change 2)
    Change 1  — Extract Iceberg sink from EnrichedRecordAssembler
    Change 9  — Split job_extension.py

  Phase 4 (depends on Phase 3):
    Change 5  — OTel trace propagation (benefits from TLS config, split files)

Total estimated effort: ~3-4 engineering days for all 10 changes.
Each change is independently deployable and revertible.
No data migration or schema changes required.
