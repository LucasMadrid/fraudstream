# ADR-002: Object Storage — Apache Iceberg + MinIO (local) / S3 (cloud)

**Date**: 2026-04-12  
**Status**: Accepted  
**Deciders**: Platform Team, Analytics Team  
**Context**: specs/004-operational-excellence/research.md, TD-004-7  

---

## Context

The pipeline needs durable, queryable storage for enriched transaction data and fraud decisions to support:
- Analyst queries over historical fraud patterns
- ML model training data (with labels from TD-004-1)
- PII audit scanning (FR-005, blocked by TD-004-7)
- Regulatory retention requirements (7-year minimum for financial data)

## Decision

**Use Apache Iceberg as the table format, with MinIO in local dev and S3/GCS in cloud.**

## Alternatives Considered

| Option | Pros | Cons | Rejected Because |
|--------|------|------|-----------------|
| PostgreSQL (operational DB) | Already present, ACID | Not designed for analytical queries at scale; storage cost | Not suitable for TB-scale append-only event log |
| Apache Parquet files on S3 (no catalog) | Simple | No schema evolution, no ACID, no time-travel | Schema evolution required for fraud model iteration |
| Delta Lake | Strong Spark integration | Proprietary ecosystem; less Flink-native | Flink integration inferior to Iceberg |
| Apache Hudi | Good streaming upserts | Complex ops; upserts not needed (append-only) | Overkill for append-only model |

## Consequences

**Positive:**
- ACID transactions with snapshot isolation — safe concurrent Flink writes
- Schema evolution with column add/rename without rewriting data
- Time-travel queries — query fraud decisions as of any past timestamp
- Partition pruning — analysts can query `WHERE event_date = '2026-04-01'` efficiently
- Flink has native Iceberg sink connector

**Negative / Risks:**
- Iceberg sinks NOT yet implemented (TD-004-7) — all Iceberg-dependent features blocked
- Requires a metastore (Hive Metastore or Nessie) in production; local dev uses file-based catalog
- MinIO requires separate ops in docker-compose

**Technical Debt:**
- TD-004-7 (P0): Iceberg sinks must ship before PII audit (FR-005), model feedback (FR-019), or SC-006 testing can proceed. Highest-priority V2 work.

## Compliance

- Constitution Principle VI (Immutable Event Log): ✅ Iceberg tables are append-only by policy (no UPDATE/DELETE)
- Constitution Principle IX (Analytics Persistence): ✅ Iceberg is the mandated analytics store

## Review Schedule

- **owner**: Platform Engineering
- **next_review_date**: 2027-04-13
