# Research: Operational Excellence — Architecture Gaps & Partials

**Phase**: 0 (Pre-design)  
**Date**: 2026-04-12  
**Resolves unknowns from**: plan.md Technical Context + Constitution Check

---

## RQ-001: pybreaker probe timeout — can it enforce 5ms?

**Question**: FR-014 requires the circuit breaker HALF-OPEN probe to use a 5ms timeout (must not consume the scoring latency budget during recovery validation). Does `pybreaker` support this natively, or does it require wrapping?

**Finding**: `pybreaker` (v1.x) does not implement call-level timeouts internally. It tracks success/failure counts and state transitions but delegates actual timeout enforcement to the caller. The standard pattern is to wrap the ML client call in `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=0.005)` (5ms). This is the correct approach for a Flink PyFlink operator running on the JVM thread pool.

**Decision**: Implement the probe timeout as a `ThreadPoolExecutor` with `timeout=0.005` in the `MLModelClient.score()` call path. The circuit breaker wraps this call; a `concurrent.futures.TimeoutError` is treated as a failure (same as a network error) and does NOT open the circuit unless it is the Nth consecutive failure.

**Alternatives considered**:
- `asyncio.wait_for` — rejected: PyFlink operators run in a sync context; mixing asyncio requires an event loop per TaskManager thread which adds overhead
- pybreaker built-in timeout — does not exist in pybreaker

**Implication for FR-014**: The `MLCircuitBreaker.call(txn)` method signature must catch both `CircuitBreakerError` (circuit OPEN) and `concurrent.futures.TimeoutError` (probe timeout expired), issuing a fallback decision in both cases.

---

## RQ-002: OpenTelemetry tail-based sampling in Python SDK

**Question**: FR-011 requires 100% tail-based sampling for traces containing error spans. Does the Python OTel SDK support this natively, or is an OTel Collector required?

**Finding**: The Python OTel SDK (`opentelemetry-sdk`) supports only **head-based** sampling decisions made at span creation time. Tail-based sampling (decide after the full trace is complete, based on span content like error status) requires an **OpenTelemetry Collector** with the `tail_sampling` processor.

**Current state**: `infra/docker-compose.yml` does NOT include an OTel Collector service.

**Decision**: **v1 accepts head-based sampling only**. FR-011 will be implemented as:
- 100% sampling for spans where the sampling decision is made at span start AND the decision type is known upfront (BLOCK decisions, pipeline errors caught at span creation)
- 1% `TraceIdRatioBased` sampler for ALLOW decisions
- Tail-based sampling (error spans) is deferred to v2 when an OTel Collector is added to the compose stack

**Document this limitation in FR-011 acceptance criteria**: "v1: head-based sampling only; tail-based requires OTel Collector (future spec)."

**Implication for FR-027**: `pipelines/scoring/telemetry.py` uses `ParentBased(root=TraceIdRatioBased(0.01))` as the base sampler, with a custom `BLOCKDecisionSampler` that always samples when the span attributes contain `decision_type=BLOCK`.

---

## RQ-003: Terraform provider for Kafka topic provisioning

**Question**: FR-003 requires Terraform to manage Kafka topic provisioning. Which provider is standard for this project? No `infra/terraform/` directory exists yet.

**Finding**: Three options evaluated:
1. `confluentinc/confluent` Terraform provider — official, supports Schema Registry subjects, topic config, and ACLs in a single provider. Requires Confluent Cloud API key for cloud; works with local Kafka via `kafka_rest_proxy_url`.
2. `Mongey/kafka` provider — community provider, direct Kafka Admin API. Works with local KRaft broker without REST proxy.
3. `null_resource` calling `infra/kafka/topics.sh` — already exists in the repo; zero new dependencies but not idempotent by default and does not support plan diffs.

**Decision**: Use `confluentinc/confluent` provider for cloud parity (FR-003 explicitly targets the cloud stack too). For local dev, the `topics.sh` script remains for bootstrap; Terraform manages the cloud-target topology. Pin to `confluentinc/confluent >= 2.0`.

**Rationale**: Constitution Principle I requires local and cloud topologies to be functionally equivalent. The Confluent provider manages both environments with the same code; switching providers mid-flight creates drift.

---

## RQ-004: RuleDefinition `mode` field — backward compatibility

**Question**: `RuleDefinition` uses `ConfigDict(extra="forbid")` in Pydantic v2. Adding a `mode: Literal["active", "shadow"]` field requires all existing YAML rule files to be valid. What is the correct default?

**Finding**: Pydantic v2 supports `Optional` fields with defaults even under `extra="forbid"`. Adding `mode: Literal["active", "shadow"] = "active"` with a default satisfies backward compatibility — existing YAML files that omit `mode` will parse as `"active"`, preserving current behaviour.

**Decision**: Add `mode: Literal["active", "shadow"] = "active"` to `RuleDefinition`. No change to existing YAML rule files required. The evaluator must check `rule.mode == "shadow"` before issuing a BLOCK/FLAG decision; shadow rules contribute to `rule_shadow_triggers_total` counter but do not modify `EvaluationResult.determination`.

**Schema Registry impact**: `RuleDefinition` is a Python dataclass used internally; it is NOT an Avro schema registered in Schema Registry. No BACKWARD_TRANSITIVE compatibility check applies. Only `pipelines/scoring/rules/models.py` and the evaluator need updating.

---

## RQ-005: Prometheus Alertmanager → scoring engine webhook for auto-demotion

**Question**: US-4 AS-4 specifies that `rule_fp_rate_high` alert fires a webhook to `/rules/{rule_id}/demote`. Does the existing Alertmanager support this, and what does the endpoint need to be?

**Finding**: The project uses Prometheus directly (`infra/prometheus/prometheus.yml`) but does NOT currently deploy Alertmanager — alerts are configured in `fraud_rule_engine.yml` but evaluated by Prometheus native alerting rules only. PagerDuty/Slack receivers would require Alertmanager.

**Decision**: The auto-demotion webhook (US-4 AS-4) requires:
1. Deploy Alertmanager as a new service in docker-compose (required anyway for FR-003/SC-003 on-call paging)
2. Add `webhook_configs` route in Alertmanager pointing to `http://scoring-engine:8090/rules/{rule_id}/demote`
3. The scoring engine needs a lightweight management HTTP server (FastAPI or Flask, port 8090) with a `POST /rules/{rule_id}/demote` endpoint that reloads the rule file with `mode: shadow` and publishes a config event to `txn.rules.config`

**Implication**: Alertmanager must be added to docker-compose. The management HTTP server is a new entrypoint for the scoring engine — not on the Flink hot path.

---

## RQ-006: Redis service naming convention in docker-compose

**Question**: FR-026 adds Redis to docker-compose. What is the naming convention for services, volumes, and ports in the existing compose file?

**Finding from `infra/docker-compose.yml`**: Services use lowercase names (`broker`, `schema-registry`, `prometheus`, `grafana`, `flink-jobmanager`, `flink-taskmanager`, `minio`, `postgres`). Volumes use `${COMPOSE_PROJECT_NAME}_<service>-data` or named volumes like `postgres-data`. Port bindings follow host:container pattern.

**Decision**: Add service named `redis` with image `redis:7-alpine`, port `6379:6379`, named volume `redis-data`, and `command: redis-server --appendonly yes` for persistence. Network: existing `fraudstream` network. Health check: `redis-cli ping`.

`ScoringConfig.redis_url` defaults to `redis://localhost:6379/0` for tests; in docker-compose, consumed services reference `redis://redis:6379/0`.

---

## Summary of Resolved Unknowns

| ID | Question | Resolution |
|---|---|---|
| RQ-001 | pybreaker 5ms probe timeout | Use `ThreadPoolExecutor(timeout=0.005)` wrapper; `TimeoutError` = failure |
| RQ-002 | OTel tail-based sampling | v1: head-based only; tail-based deferred (needs OTel Collector) |
| RQ-003 | Terraform Kafka provider | `confluentinc/confluent >= 2.0`; `topics.sh` remains for local bootstrap |
| RQ-004 | `RuleDefinition.mode` backward compat | `mode: Literal["active","shadow"] = "active"`; no existing YAML changes needed |
| RQ-005 | Alertmanager webhook for demotion | Add Alertmanager to compose; scoring management server on port 8090 |
| RQ-006 | Redis docker-compose naming | Service: `redis`, image: `redis:7-alpine`, volume: `redis-data`, port `6379:6379` |
