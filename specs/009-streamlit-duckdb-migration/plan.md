# Implementation Plan: Streamlit DuckDB Migration

**Branch**: `009-streamlit-duckdb-migration` | **Date**: 2026-04-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/009-streamlit-duckdb-migration/spec.md`

## Summary

Replace the `trino-python-client` query layer in the three Streamlit historical pages (Fraud Rate, Rule Triggers, Model Compare) with an in-process DuckDB engine that reads from Iceberg tables via PyIceberg → Arrow → DuckDB. This eliminates DNS-dependent container communication and makes all three pages self-contained within the Streamlit process.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: DuckDB ≥ 1.0 (new), PyIceberg 0.11.1 (existing), pyarrow (existing, transitive via PyIceberg), streamlit (existing), pandas (existing)  
**Storage**: Apache Iceberg on MinIO — `iceberg.enriched_transactions`, `iceberg.fraud_decisions`  
**Testing**: pytest (unit); manual smoke test with Trino container stopped  
**Target Platform**: Docker container (Streamlit service in docker-compose)  
**Project Type**: Web application (Streamlit analytics dashboard)  
**Performance Goals**: Query results within 5 seconds for 24-hour rolling window  
**Constraints**: 30-day rolling window cap (720 hours); in-process memory bounded by partition pruning; no new network dependencies for Streamlit  
**Scale/Scope**: 3 query modules, 3 Streamlit pages, 1 shared config module

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P-I: Single streaming backbone (Kafka) | PASS | Unaffected — no Kafka changes |
| P-II: Schema-first (Avro/Iceberg) | PASS | Reading existing Iceberg schemas, no schema changes |
| P-III: Exactly-once semantics | PASS | Read-only queries, no write path |
| P-IV: Observability-first | PASS | Existing Prometheus metrics unchanged |
| P-V: Immutable audit log | PASS | Read-only; Iceberg append-only tables unchanged |
| P-VI: Feature store single source | PASS | Unaffected |
| P-VII: Fail-fast / circuit breaker | PASS | PyIceberg catalog errors surfaced as error messages, not silent failures |
| P-VIII: Security hardening | PASS | No new network endpoints; S3 creds already in env vars |
| P-IX: Analytics query layer = DuckDB for Streamlit | **MANDATED** | This feature fulfills P-IX |
| P-X: Historical store uses DuckDB for Streamlit queries | **MANDATED** | This feature fulfills P-X |
| DD-11: DuckDB replaces Trino in Streamlit process | **IMPLEMENTS** | Direct implementation of this design decision |

**Constitution verdict**: PASS. Feature is constitutionally required.

**Post-design re-check**: No violations introduced by the design. Single shared `config.py` module with `MAX_HOURS=720` avoids duplication across query modules. In-process DuckDB connection per query matches the mandated pattern.

## Project Structure

### Documentation (this feature)

```text
specs/009-streamlit-duckdb-migration/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code Changes

```text
analytics/queries/
├── config.py                  # NEW — MAX_HOURS=720, shared constants
├── fraud_rate.py              # MODIFY — replace trino-python-client with PyIceberg→Arrow→DuckDB
├── rule_triggers.py           # MODIFY — same migration
└── model_versions.py          # MODIFY — same migration

pyproject.toml                 # MODIFY — add duckdb>=1.0 dependency

infra/docker-compose.yml       # MODIFY — add PyIceberg env vars to streamlit service:
                               #   PYICEBERG_CATALOG__ICEBERG__URI
                               #   PYICEBERG_CATALOG__ICEBERG__WAREHOUSE
                               #   PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT
                               #   PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS

tests/unit/
└── test_query_config.py       # NEW — unit tests for config.py clamping logic

tests/integration/
└── test_duckdb_queries.py     # NEW — integration smoke tests (requires running Iceberg)
```

**Pages unmodified** (data layer is isolated in `analytics/queries/`):
- `analytics/app/pages/2_fraud_rate.py` — unchanged
- `analytics/app/pages/3_rule_triggers.py` — unchanged
- `analytics/app/pages/4_model_compare.py` — unchanged

**Structure Decision**: Single project modification. Query modules in `analytics/queries/` are the only layer that changes. Pages call the same function signatures; only the implementations swap from `trino-python-client` to PyIceberg → Arrow → DuckDB. This contains the blast radius to 3 files + 1 new config module.

## Implementation Phases

### Phase 1 — Foundation (no user-visible change)
- Add `duckdb>=1.0` to `pyproject.toml`
- Create `analytics/queries/config.py` with `MAX_HOURS=720` and shared `_iceberg_to_arrow()` helper
- Add PyIceberg env vars to `infra/docker-compose.yml` streamlit service

### Phase 2 — Migrate query modules (FR-001, FR-005, FR-006, FR-007)
- Rewrite `analytics/queries/fraud_rate.py` using PyIceberg → Arrow → DuckDB
- Rewrite `analytics/queries/rule_triggers.py`
- Rewrite `analytics/queries/model_versions.py`

### Phase 3 — Empty-state and cap enforcement (FR-003, FR-004)
- Ensure each query function returns empty DataFrame (not exception) when no rows found
- UI sliders already capped at 720 via page code; add `min(hours, MAX_HOURS)` clamp in query layer

### Phase 4 — Tests and validation (SC-001–SC-005)
- Unit tests for `config.py` clamp logic
- Integration smoke tests with Iceberg running

## Complexity Tracking

No constitution violations. No additional complexity beyond direct replacement.
