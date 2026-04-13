# Service Ownership

**Last updated**: 2026-04-13 | **Review cycle**: Quarterly

## Team

| Role | Responsibility | Contact |
|------|---------------|---------|
| Platform Team | Infrastructure, CI/CD, Terraform, Kafka | #platform-eng |
| Fraud ML Team | Rule engine, ML model, feature engineering | #fraud-ml |
| Analytics Team | Streamlit dashboards, Iceberg queries | #analytics |

## Services

### Ingestion Pipeline (`pipelines/ingestion/`)

| Item | Value |
|------|-------|
| Owner | Platform Team |
| On-call | Platform on-call rotation |
| SLO | 99.9% availability; < 10ms p99 ingestion latency |
| Alerts | Grafana → PagerDuty (Platform) |
| Runbook | `docs/runbooks/OBSERVABILITY_RUNBOOK.md` |

### Enrichment Pipeline (`pipelines/processing/`)

| Item | Value |
|------|-------|
| Owner | Fraud ML Team |
| On-call | Fraud ML rotation |
| SLO | 99.9% availability; < 20ms p99 enrichment latency |
| Alerts | Grafana → Slack `#fraud-ml-alerts` |
| Runbook | `docs/runbooks/CHECKPOINT_RECOVERY.md` |

### Scoring Engine (`pipelines/scoring/`)

| Item | Value |
|------|-------|
| Owner | Fraud ML Team |
| On-call | Fraud ML rotation |
| SLO | 99.95% availability; < 50ms p99 scoring latency; ML circuit breaker OPEN < 5min/day |
| Alerts | Grafana → PagerDuty (Fraud ML) + Alertmanager webhook for rule demotions |
| Runbook | `docs/runbooks/OBSERVABILITY_RUNBOOK.md` |

### Management API (`pipelines/scoring/management_api.py`)

| Item | Value |
|------|-------|
| Owner | Fraud ML Team |
| Access | Internal network only (port 8090, not host-exposed) |
| Authentication | None in v1 (intentional — internal network trust model; mTLS deferred to cloud) |
| Audit log | JSON structured log on every demote/promote call |

### Kafka Topics

| Topic | Owner | Partitions | Retention |
|-------|-------|-----------|-----------|
| txn.raw | Platform | 6 | 7d |
| txn.enriched | Fraud ML | 6 | 7d |
| txn.fraud.alerts | Fraud ML | 6 | 7d |
| txn.fraud.alerts.dlq | Platform | 1 | 30d |

### Infrastructure

| Component | Owner | Runbook |
|-----------|-------|---------|
| Kafka / Schema Registry | Platform | `docs/runbooks/SCHEMA_MIGRATION.md` |
| Flink cluster | Platform | `docs/runbooks/CHECKPOINT_RECOVERY.md` |
| Redis feature cache | Fraud ML | `docs/runbooks/OBSERVABILITY_RUNBOOK.md` |
| PostgreSQL fraud_alerts | Platform | — |
| MinIO / Iceberg | Platform | Blocked by TD-007 |

## Escalation Path

1. On-call engineer (PagerDuty)
2. Team lead (Slack DM)
3. Engineering Manager (phone)
4. CTO (for SEV-1 customer-facing incidents only)
