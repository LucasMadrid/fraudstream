# Each scoring consumer narrows its dependency from `ScoringConfig` to the fields it reads

`ScoringConfig` has 16 fields. Each direct consumer reads only a small slice:

| Consumer | Fields used | Fields ignored |
|---|---|---|
| `AlertKafkaSink` | `kafka_brokers`, `schema_registry_url`, `fraud_alerts_topic`, `fraud_alerts_dlq_topic` | 12/16 |
| `AlertPostgresSink` | `fraud_alerts_db_url` | 15/16 |
| `ShadowDecisionKafkaSink` | `kafka_brokers`, `schema_registry_url`, `shadow_decisions_topic` | 13/16 |
| `MLCircuitBreaker` | `cb_error_threshold`, `cb_open_seconds`, `cb_probe_timeout_ms` | 13/16 |

Four fields (`redis_url`, `ml_serving_url`, `pg_pool_size`, `cb_error_window_seconds`) are defined in `ScoringConfig` but accessed by no consumer. They are dead.

Passing the full config to each consumer obscures which configuration each one requires. A test that wants to construct `AlertPostgresSink` must populate 15 unrelated fields to satisfy the type, or mock `ScoringConfig` — adding indirection without gaining locality.

## Solution

Three typed sub-configs are introduced in `pipelines/scoring/config.py`, derived from `ScoringConfig`:

```python
@dataclass
class AlertKafkaConfig:
    brokers: str
    schema_registry_url: str
    topic: str
    dlq_topic: str

@dataclass
class CircuitBreakerConfig:
    error_threshold: int
    open_seconds: float
    probe_timeout_ms: float

# AlertPostgresSink already accepts a single db_url; no sub-config required.
```

`ScoringConfig` gains two factory methods:

```python
def alert_kafka_config(self) -> AlertKafkaConfig: ...
def circuit_breaker_config(self) -> CircuitBreakerConfig: ...
```

Each consumer is updated to accept the narrowed type:

```python
class AlertKafkaSink:
    def __init__(self, config: AlertKafkaConfig) -> None: ...

class MLCircuitBreaker:
    def __init__(self, client: MLModelClient, config: CircuitBreakerConfig) -> None: ...
```

`AlertPostgresSink` is updated to accept `db_url: str` directly — a single string field does not justify a wrapper dataclass.

`_AlertSinkFunction` and `management_api.py` (the construction sites) call the factory methods and pass sub-configs. `ScoringConfig` remains the top-level config object assembled at startup; it is not deleted.

The four unused fields (`redis_url`, `ml_serving_url`, `pg_pool_size`, `cb_error_window_seconds`) are removed from `ScoringConfig`.

## Design decisions

**Factory methods on `ScoringConfig`, not standalone constructors.** Sub-configs are derived from `ScoringConfig` at call sites where the full config is available. Standalone constructors would require the caller to manually pass each field — repeating the field name twice (once in `ScoringConfig`, once in the `AlertKafkaConfig()` call). The factory method is defined once and is the single place a field rename propagates.

**`ShadowDecisionKafkaSink` reuses `AlertKafkaConfig`.** Its three fields (`kafka_brokers`, `schema_registry_url`, `shadow_decisions_topic`) overlap with `AlertKafkaSink` except for the topic name. Introducing a `ShadowKafkaConfig` for one different field adds a type for no structural benefit. `shadow_decisions_topic` is passed separately.

**Dead fields removed now, not later.** `redis_url`, `ml_serving_url`, `pg_pool_size`, `cb_error_window_seconds` are read by no consumer. Keeping them widens `ScoringConfig`'s interface and implies they matter. They are removed in the same pass.

## Test improvement

`AlertPostgresSink` can be constructed with a plain `db_url: str`. `MLCircuitBreaker` can be tested with a `CircuitBreakerConfig(error_threshold=2, open_seconds=5.0, probe_timeout_ms=500)` without constructing a `ScoringConfig`. Tests no longer carry dead fields or require config mocks.

## Considered alternatives

- *One sub-config per field (single-field wrappers)* — `DbUrlConfig(db_url: str)` adds a type where a plain string is already self-documenting. Rejected.
- *Protocol-based structural narrowing* — callers declare `Protocol` classes with only the fields they read; `ScoringConfig` satisfies them implicitly. Avoids explicit sub-configs but requires a Protocol definition per consumer (equivalent verbosity) and is harder to trace statically. Rejected in favour of explicit dataclasses.
- *Delete `ScoringConfig` and assemble sub-configs at the top* — the top-level config (env vars, YAML) maps cleanly to one flat dataclass; decomposing it at the source adds parsing complexity for no benefit. Rejected.
