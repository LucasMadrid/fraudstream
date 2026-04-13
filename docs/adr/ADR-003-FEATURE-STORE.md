# ADR-003: Feature Store — Redis (v1 cache)

**Date**: 2026-04-12  
**Status**: Accepted  
**Deciders**: Fraud ML Team  
**Context**: specs/004-operational-excellence/spec.md §FR-016, §FR-026  

---

## Context

The scoring engine requires low-latency access to pre-computed features (velocity, device profile, geo signals) for each transaction. These features are computed by the enrichment pipeline and must be available to the scoring engine within the sub-100ms latency budget.

Key requirements:
- Feature read latency < 5ms p99 (Constitution Principle II allocation)
- Features expire after 24h (velocity windows reset daily)
- High availability in production (no feature cache = fallback to rule-only scoring)

## Decision

**Use Redis 7 as the feature cache in v1, with TTL-based expiry. HA topology (Sentinel/Cluster) deferred to cloud deployment (TD-004-10).**

## Alternatives Considered

| Option | Pros | Cons | Rejected Because |
|--------|------|------|-----------------|
| In-memory dict (Flink state) | Zero latency | Not accessible cross-job; lost on restart | Scoring engine is a separate process from enrichment |
| Apache Cassandra | Highly scalable | Operational complexity; p99 latency higher than Redis | Overkill for <100ms feature cache; complex ops |
| DynamoDB / Bigtable | Managed, scalable | Cloud-provider lock-in; higher latency than Redis for small reads | Violates local-dev simplicity principle |
| Postgres cache table | Already present | Not designed for <5ms random reads | p99 latency incompatible with budget |

## Consequences

**Positive:**
- Sub-millisecond read latency (p50 < 0.5ms in local benchmarks)
- TTL-native — no background job needed for feature expiry
- Simple Python client (`redis-py`) with connection pooling
- Already widely used in fraud/ML industry for feature serving

**Negative / Risks:**
- Redis data is NOT encrypted at rest in v1 (documented exception — feature vectors are not raw PII; derived signals only). Addressed in TD-004-7 classification decision.
- Single-node Redis in docker-compose — no HA. ML circuit breaker provides graceful degradation if Redis is unavailable.
- Redis AUTH not configured in local dev (redis://localhost:6379/0). Production must use `rediss://` with AUTH (TD-004-10).

**Technical Debt:**
- TD-004-10 (P0): Production Redis on ElastiCache (AWS) or Memorystore (GCP) with AUTH, TLS, and Sentinel HA. See `infra/terraform/modules/redis/`.

## Compliance

- Constitution Principle II (Sub-100ms): ✅ Redis < 5ms p99 comfortably within budget
- Constitution Principle V (Rules Before Models): ✅ Redis is read-only in scoring; rules always evaluate independently of Redis availability

## Review Schedule

- **owner**: Platform Engineering
- **next_review_date**: 2027-04-13
