# ADR-004: Analytics Query Layer — Streamlit + DuckDB

**Date**: 2026-04-12  
**Status**: Accepted  
**Deciders**: Analytics Team  
**Context**: Constitution Principle X, specs/004-operational-excellence/spec.md §FR-018, §FR-019  

---

## Context

Fraud analysts need a self-service UI to:
- Inspect DLQ messages and identify failure patterns (FR-018)
- Monitor shadow rule trigger rates before promoting rules to active (FR-022, FR-019)
- Query historical fraud decisions from Iceberg (blocked by TD-004-7)

Requirements:
- No engineering involvement for analyst queries
- Fast iteration on visualizations (analysts update the code)
- Must not affect the scoring decision path (Constitution Principle X)

## Decision

**Use Streamlit for the analytics UI, with DuckDB for in-process Iceberg/Parquet queries.**

## Alternatives Considered

| Option | Pros | Cons | Rejected Because |
|--------|------|------|-----------------|
| Jupyter notebooks | Familiar to data scientists | Not self-service; requires notebook server | No auth/sharing; analysts can't run without Python setup |
| Grafana (extend existing) | Already deployed | Limited to time-series metrics; can't query Iceberg tables | Not suitable for ad-hoc fraud investigation queries |
| Apache Superset | Full BI platform | Heavy ops overhead; separate deployment | Overkill for a small fraud analyst team; complex SSO |
| Custom Flask app | Full control | Engineering overhead; analysts can't modify UI | Requires engineering for every new chart |

## Consequences

**Positive:**
- Analysts write Python directly — no SQL-only limitation
- DuckDB reads Iceberg/Parquet directly from MinIO/S3 without a metastore service
- Streamlit hot-reload enables rapid iteration during fraud investigations
- Zero impact on scoring decision path (Principle X satisfied — analytics is read-only)

**Negative / Risks:**
- Streamlit is not a production BI platform — not suitable for CEO-level dashboards
- DuckDB + Iceberg integration requires PyIceberg — adds to Python dependencies
- All Iceberg-dependent Streamlit pages blocked by TD-004-7

**Technical Debt:**
- TD-004-7: DLQ trend analysis page (FR-018) and model feedback page (FR-019) are blocked until Iceberg sinks ship.
- TD-003-3: Self-service rule management UI (Phase 3) will extend Streamlit.

## Current Streamlit Pages (v1)

| Page | Status | Blocked By |
|------|--------|-----------|
| `analytics/app/pages/1_fraud_dashboard.py` | Planned (T043) | None |
| `analytics/app/pages/2_rule_performance.py` | Planned (T044) | None |
| `analytics/app/pages/3_rule_triggers.py` | Planned (T045) | None |
| DLQ trend analysis | Architecture only | TD-004-7 (Iceberg) |
| Model feedback loop | Architecture only | TD-004-7 + TD-004-1 |

## Compliance

- Constitution Principle X (Analytics Consumer): ✅ Streamlit reads from Iceberg/PostgreSQL only — never writes to Kafka or influences scoring

## Review Schedule

- **owner**: Platform Engineering
- **next_review_date**: 2027-04-13
