# Alert fan-out logic extracted into `CompositeAlertSink`

The dual-sink fan-out (Kafka primary + PostgreSQL best-effort) lives inside `_AlertSinkFunction`, an inner `MapFunction` class in `pipelines/scoring/job_extension.py`. The error-handling contracts — Kafka serialisation errors are caught per-record; Kafka back-pressure exceptions propagate; PostgreSQL failures are caught and logged — are buried inside a PyFlink operator that cannot be exercised without a Flink runtime. `AlertKafkaSink` has an `emit()` method but does not declare the `AlertSink` protocol, so the shared protocol (`pipelines/shared/alert_protocol.py`) is only half-used.

`CompositeAlertSink` is extracted to `pipelines/scoring/sinks/composite_alert.py`. `AlertKafkaSink` declares `AlertSink` alongside `AlertPostgresSink` (which already does).

## Interface

```python
class CompositeAlertSink(AlertSink):
    def __init__(self, kafka: AlertKafkaSink, postgres: AlertPostgresSink) -> None: ...
    def open(self) -> None: ...           # delegates open() to both sinks
    def emit(self, alert: FraudAlert) -> None: ...
    def close(self) -> None: ...          # delegates close() to both sinks
```

`emit()` preserves the existing contracts:
- `AlertKafkaSink.emit()` — `ValueError`, `TypeError`, `AttributeError` caught per-record (serialisation failures); `KafkaException`/`BufferError` propagate (back-pressure must reach Flink).
- `AlertPostgresSink.persist()` — all exceptions caught and logged; a DB failure must not stall the pipeline or drop the Kafka alert already emitted.

`_AlertSinkFunction.open()` constructs both sinks from config and passes them to `CompositeAlertSink`. `_AlertSinkFunction.map()` becomes three lines.

## Design decisions

**Injectable sinks, not config-constructed internally.** Both `AlertKafkaSink` and `AlertPostgresSink` take `ScoringConfig` and have their own `open()`/`close()` lifecycles. If `CompositeAlertSink` constructed them internally it would be impossible to inject test doubles — tests would require a real Kafka broker and PostgreSQL. The composite receives already-constructed sinks; `_AlertSinkFunction` remains the construction site.

**`AlertKafkaSink` declares `AlertSink`.** The protocol existed but was only declared on the PostgreSQL sink. Both sinks now satisfy `AlertSink`; the protocol becomes a real seam (two adapters) rather than a hypothetical one (one adapter).

**`CompositeAlertSink` also declares `AlertSink`.** The composite can be passed anywhere a single `AlertSink` is expected — testable as a unit without its Flink wrapper.

## Test improvement

The error-handling contracts are now testable directly: construct `CompositeAlertSink` with a mock `AlertKafkaSink` that raises `KafkaException`, assert it propagates; raise `psycopg2.Error` from the postgres mock, assert it is swallowed. No Flink runtime, no `MapFunction`, no PyFlink imports required.

## Considered alternatives

- *Keep fan-out in `_AlertSinkFunction`, extract error handling into helpers* — locality improves slightly but the seam is still inside a PyFlink operator. Tests still require Flink. Rejected.
- *Merge both sinks into one class* — breaks the single-responsibility of each transport adapter and makes the Kafka-vs-PostgreSQL error contracts harder to reason about independently. Rejected.
