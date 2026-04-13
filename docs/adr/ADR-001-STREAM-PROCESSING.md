# ADR-001: Stream Processing Engine — Apache Flink

**Date**: 2026-04-12  
**Status**: Accepted  
**Deciders**: Platform Team, Fraud ML Team  
**Context**: specs/004-operational-excellence/research.md  

---

## Context

The fraud detection pipeline requires sub-100ms end-to-end latency for real-time transaction scoring. We need a stream processing engine capable of:
- Stateful feature computation (velocity windows, account history)
- Exactly-once semantics for fraud alert emission
- Horizontal scalability for peak transaction volumes
- Integration with Kafka as the event backbone

## Decision

**Use Apache Flink 1.20.3 (PyFlink) as the stream processing engine.**

## Alternatives Considered

| Option | Pros | Cons | Rejected Because |
|--------|------|------|-----------------|
| Apache Spark Streaming | Large ecosystem, good ML integration | Micro-batch (not true streaming), higher latency | Micro-batch latency incompatible with <100ms budget |
| Kafka Streams | Native Kafka integration, simpler ops | JVM-only, limited Python support, weaker stateful ops | Python 3.11 requirement; fraud team is Python-first |
| Apache Beam / Dataflow | Portable, cloud-native | Abstraction overhead, GCP-centric | Vendor lock-in risk; local dev complexity |
| Custom asyncio consumer | Maximum control | No built-in state management, fault tolerance requires reimplementation | Engineering cost too high for reliability guarantees |

## Consequences

**Positive:**
- True event-time processing with watermarks — correct for out-of-order transactions
- RocksDB-backed state with exactly-once via Flink checkpoints (stored in MinIO)
- PyFlink enables fraud team to use Python ecosystem (pandas, sklearn for feature engineering)
- Scales horizontally by adding TaskManagers

**Negative / Risks:**
- PyFlink is less mature than Java Flink API; some operators require Java interop
- JVM startup overhead means cold-start latency is higher than native Python
- Checkpoint recovery requires coordination (see `docs/runbooks/CHECKPOINT_RECOVERY.md`)

**Technical Debt:**
- TD-004-4 (RETIRED): K8s Flink operator replaced by managed services (Amazon Managed Flink / GCP Dataflow) in production — see TD-004-10

## Compliance

- Constitution Principle I (Stream-First): ✅ Flink enforces stream-first processing
- Constitution Principle II (Sub-100ms): ✅ Flink achieves <10ms enrichment p99 in benchmarks
- Constitution Principle VI (Immutable Event Log): ✅ Flink reads Kafka immutably; only appends to Iceberg

## Review Schedule

- **owner**: Platform Engineering
- **next_review_date**: 2027-04-13
