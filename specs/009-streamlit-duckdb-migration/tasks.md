# Tasks: Streamlit DuckDB Migration

**Input**: Design documents from `/specs/009-streamlit-duckdb-migration/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Organization**: Tasks grouped by user story. Each story phase is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1=Fraud Rate, US2=Rule Triggers, US3=Model Compare)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add DuckDB dependency and shared config module

- [X] T001 Add `duckdb>=1.0` to `[project.dependencies]` in `pyproject.toml`
- [X] T002 Create `analytics/queries/config.py` with `MAX_HOURS: int = 720` constant

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add PyIceberg catalog environment variables to the Streamlit docker-compose service. All three user stories depend on these env vars being present.

**⚠️ CRITICAL**: No user story query module can be tested in Docker until this phase is complete.

- [X] T003 Add PyIceberg env vars to the `streamlit` service in `infra/docker-compose.yml`: `PYICEBERG_CATALOG__ICEBERG__URI=http://iceberg-rest:8181`, `PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/`, `PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://minio:9000`, `PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true`

**Checkpoint**: Foundation ready — all three user story query modules can now be implemented in parallel.

---

## Phase 3: User Story 1 — Fraud Rate Page Loads Reliably (Priority: P1) 🎯 MVP

**Goal**: Replace `trino-python-client` in `fraud_rate.py` with PyIceberg → Arrow → DuckDB; Fraud Rate page loads without Trino running.

**Independent Test**: `from analytics.queries.fraud_rate import fraud_rate_daily; df = fraud_rate_daily(hours=24)` succeeds with Iceberg running and Trino stopped.

- [X] T004 [US1] Rewrite `analytics/queries/fraud_rate.py` — replace `trino.dbapi.connect()` and all Trino SQL with PyIceberg `load_catalog("iceberg")` → `table.scan(row_filter=GreaterThanOrEqual("decision_time_ms", start_ms)).to_arrow()` → `duckdb.connect()` → `conn.register("tbl", arrow_tbl)` → `conn.execute(SQL).df()` for both `fraud_rate_daily(hours)` and `fraud_rate_by_channel(hours)` functions; import and apply `MAX_HOURS` clamp from `analytics/queries/config.py`; return empty `pd.DataFrame` (not exception) when no rows found; wrap catalog load in try/except and re-raise with user-facing message on catalog unreachable

---

## Phase 4: User Story 2 — Rule Triggers Page Loads Reliably (Priority: P2)

**Goal**: Replace `trino-python-client` in `rule_triggers.py` with PyIceberg → Arrow → DuckDB; Rule Triggers page loads without Trino running.

**Independent Test**: `from analytics.queries.rule_triggers import rule_leaderboard; df = rule_leaderboard(hours=24)` succeeds with Iceberg running and Trino stopped.

- [X] T005 [P] [US2] Rewrite `analytics/queries/rule_triggers.py` — replace `trino.dbapi.connect()` and all Trino SQL with PyIceberg scan on `iceberg.fraud_decisions` (using `GreaterThanOrEqual("decision_time_ms", start_ms)`), convert to Arrow, register in DuckDB, and run `UNNEST(rule_triggers)` SQL to expand the list column before aggregation; implement `rule_leaderboard(hours, top_n)` and `rule_trigger_daily(rule_name, hours)`; apply `MAX_HOURS` clamp; return empty DataFrame when no rows

---

## Phase 5: User Story 3 — Model Compare Page Loads Reliably (Priority: P3)

**Goal**: Replace `trino-python-client` in `model_versions.py` with PyIceberg → Arrow → DuckDB; Model Compare page loads without Trino running.

**Independent Test**: `from analytics.queries.model_versions import model_version_summary; df = model_version_summary(hours=24)` succeeds with Iceberg running and Trino stopped.

- [X] T006 [P] [US3] Rewrite `analytics/queries/model_versions.py` — replace `trino.dbapi.connect()` and all Trino SQL with PyIceberg scan on `iceberg.fraud_decisions` (using `GreaterThanOrEqual("decision_time_ms", start_ms)`), convert to Arrow, register in DuckDB; implement `model_version_summary(hours)` and `model_version_daily(model_version, hours)`; apply `MAX_HOURS` clamp; return empty DataFrame when no rows

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Unit tests, validation, and CLAUDE.md update

- [X] T007 [P] Write `tests/unit/test_query_config.py` — test `MAX_HOURS == 720`; test that calling each query function with `hours=9999` does not raise and returns a DataFrame; mock `load_catalog` for unit isolation
- [X] T008 Run `ruff check analytics/queries/ --fix` and `ruff format analytics/queries/` to confirm code style passes CI gate
- [X] T009 Update `CLAUDE.md` Active Technologies section: add entry for `009-streamlit-duckdb-migration` — `DuckDB>=1.0 (in-process, in-memory), PyIceberg→Arrow→DuckDB query pattern for Streamlit historical pages; replaces trino-python-client in analytics/queries/`

---

## Dependencies

```
T001 (pyproject.toml) ──► T004, T005, T006 (query modules need duckdb installed)
T002 (config.py)      ──► T004, T005, T006 (query modules import MAX_HOURS)
T003 (docker-compose) ──► integration testing of T004, T005, T006

T004 (fraud_rate)  — US1 MVP, no dependency on US2/US3
T005 (rule_triggers) — independent of T004, T006 (different file)
T006 (model_versions) — independent of T004, T005 (different file)

T007 (unit tests) ──► T002 (tests import config.py)
T008 (ruff) ──► T004, T005, T006 (lint after code written)
T009 (CLAUDE.md) — no dependencies, can run any time
```

## Parallel Execution

After T001, T002, T003 complete:
- T004, T005, T006 can run in parallel (different files)
- T007, T008, T009 can run in parallel after T004–T006

## Implementation Strategy

**MVP**: Complete Phase 1 → Phase 2 → Phase 3 (T001–T004). This delivers US1 (Fraud Rate page). The page is the most critical (P1) and validates the full PyIceberg → Arrow → DuckDB pattern before replicating to US2 and US3.

**Full delivery**: T005, T006 reuse the same pattern established in T004 — copy the catalog/scan/register/execute structure, change table name and SQL. Estimated low effort once T004 is proven.
