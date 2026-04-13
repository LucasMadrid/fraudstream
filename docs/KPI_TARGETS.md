# KPI Targets — Fraud Detection Pipeline

**Owner**: Fraud ML Team | **Review cycle**: Monthly | **Last updated**: 2026-04-13

## Latency Targets (Constitution Principle II: Sub-100ms Budget)

| Stage | p50 | p99 | Breach Action |
|-------|-----|-----|---------------|
| Ingestion (Kafka produce) | < 2ms | < 10ms | Alert: P1 |
| Enrichment (geo + velocity) | < 5ms | < 20ms | Alert: P2 |
| Redis feature cache read | < 1ms | < 5ms | Alert: P2 |
| ML inference (when CLOSED) | < 10ms | < 30ms | Alert: P2; circuit breaker opens at 3 consecutive failures |
| Rule evaluation | < 5ms | < 15ms | Alert: P3 |
| Decision emit (Kafka) | < 2ms | < 10ms | Alert: P1 |
| **End-to-end** | **< 30ms** | **< 100ms** | **Alert: P1; SLO breach** |

## Reliability Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Pipeline availability | 99.9% | Prometheus `up{job="flink"}` |
| DLQ depth | < 100 messages | `FraudAlertsDLQDepthHigh` alert |
| ML circuit breaker OPEN time | < 5 min/day | `ml_circuit_breaker_state == 1` duration |
| Rule false-positive rate | < 5% (per active rule) | `rule_shadow_fp_total / rule_shadow_triggers_total` |

## Detection Quality (v1 — limited by TD-003-4 false-positive feedback)

| Metric | Target | Blocker |
|--------|--------|---------|
| Rule coverage (transactions evaluated) | 100% | None |
| Shadow rule activation rate | Tracked | TD-003-4 (no FP feedback yet) |
| Model feedback latency | < 24h | TD-004-1 (model feedback loop deferred) |

## Schema Health

| Metric | Target |
|--------|--------|
| BACKWARD_TRANSITIVE compliance | 100% (enforced in CI) |
| Schema registration failures in CI | 0 |

## Operational Targets

| Metric | Target |
|--------|--------|
| Mean time to detect (MTTD) | < 5 minutes (via Prometheus alerting) |
| Mean time to recover (MTTR) | < 30 minutes (via runbooks) |
| Post-mortem completion rate | 100% for SEV-1/SEV-2 |
| Action item close rate | > 80% within 30 days |
