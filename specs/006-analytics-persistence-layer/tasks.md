# Tasks: Analytics Persistence Layer

**Input**: Design documents from `/specs/006-analytics-persistence-layer/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to ([US1]–[US5])
- Exact file paths are included in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: New files, dependencies, and infra config that every story depends on.

- [X] T001 Add `pyiceberg[pyarrow,s3fs]`, `feast`, `trino` to `pyproject.toml` / `requirements.txt`
- [X] T002 Create `storage/lake/` directory tree: `schemas/`, `migrations/`, `catalog.properties`
- [X] T003 [P] Write `infra/iceberg/catalog-config.properties` (REST catalog URI, MinIO warehouse, S3 endpoint)
- [X] T004 [P] Write `infra/iceberg/init-tables.sh` (idempotent DDL runner for both Iceberg tables at Analytics tier startup)
- [X] T005 [P] Write `storage/lake/schemas/enriched_transactions.sql` from contract `contracts/iceberg-enriched-transactions-v1.sql`
- [X] T006 [P] Write `storage/lake/schemas/fraud_decisions.sql` from contract `contracts/iceberg-fraud-decisions-v1.sql`
- [X] T007 Extend `infra/docker-compose.yml`: add `iceberg-rest` (tabulario/iceberg-rest) service; verify `minio` and `trino` services present
- [X] T008 [P] Write `storage/feature_store/feature_store.yaml` (Feast registry, online SQLite backend, offline parquet store, push source)
- [X] T009 [P] Create `storage/feature_store/entities/transaction.py` (Feast Entity: `account_id`)
- [X] T010 [P] Create `storage/feature_store/features/__init__.py` (empty package init)

**Checkpoint**: Docker Compose starts `iceberg-rest`; `init-tables.sh` creates both Iceberg tables; `feast apply` registers entities without error.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core types and schema gaps that MUST be resolved before any sink can be written.

**⚠️ CRITICAL**: All user story implementation is blocked until this phase is complete.

- [X] T011 Add `FraudDecision` frozen dataclass to `pipelines/scoring/types.py` — fields: `transaction_id`, `decision` (Literal["ALLOW","FLAG","BLOCK"]), `fraud_score`, `rule_triggers`, `model_version`, `decision_time_ms`, `latency_ms`, `schema_version` (default "1")
- [X] T012 Add `last_geo_country: str | None` field to `DeviceProfileState` dataclass in `pipelines/processing/operators/device.py`; update `process_element` to write and pass through `prev_geo_country` and `prev_txn_time_ms` in the returned device dict
- [X] T013 Add `prev_geo_country` (nullable string, `"default": null`) and `prev_txn_time_ms` (nullable long timestamp-millis, `"default": null`) to `pipelines/processing/schemas/enriched-txn-v1.avsc` (BACKWARD_TRANSITIVE)
- [X] T014 Update `_assemble_record()` in `pipelines/processing/operators/enricher.py` to include `prev_geo_country` and `prev_txn_time_ms` from the device dict (pass-through; null if absent)
- [X] T015 Write `scripts/evolve_iceberg_schema.py` — reads `enriched-txn-v1.avsc`, generates idempotent `CREATE TABLE / ALTER TABLE` DDL, writes to `storage/lake/schemas/enriched_transactions.sql`
- [X] T016 [P] Write `scripts/validate_feast_schemas.py` — reads Avro feature fields (velocity/geo/device), checks each appears in corresponding Feast view; exits non-zero on drift
- [X] T017 Write `.github/workflows/schema-evolution.yml` — triggers on `*.avsc` changes; runs `evolve_iceberg_schema.py` and `validate_feast_schemas.py`; fails merge if DDL or Feast views are out of sync

**Checkpoint**: `FraudDecision` importable from `pipelines.scoring.types`; `_assemble_record()` returns `prev_geo_country` and `prev_txn_time_ms` keys; Avro schema validates with `fastavro`; `scripts/evolve_iceberg_schema.py` runs without error.

---

## Phase 3: User Story 1 — Fraud Analyst Audits a Disputed Transaction (Priority: P1) 🎯 MVP

**Goal**: Every processed transaction is queryable in the analytics store by `transaction_id` using standard SQL — enriched features + decision in one join, no custom code.

**Independent Test**: Submit one transaction end-to-end; query `iceberg.v_transaction_audit` by `transaction_id`; receive both enriched feature row and decision row within 10 seconds.

### Implementation

- [X] T018 [US1] Write `pipelines/processing/operators/iceberg_sink.py` — `IcebergEnrichedSink(SinkFunction)`: PyIceberg write path, in-process batch buffer (flush every 1 s or 100 records), `table.append(pa.Table(...))`, in-batch dedup on `transaction_id`, sets `enrichment_time` to wall-clock at flush
- [X] T019 [US1] Wire `IcebergEnrichedSink` as side output in `pipelines/processing/operators/enricher.py` — `EnrichedRecordAssembler.flat_map()` emits to both the main output stream and the Iceberg sink side output; MUST NOT add latency to the 100 ms hot path
- [X] T020 [US1] Write `pipelines/scoring/sinks/iceberg_decisions.py` — `IcebergDecisionsSink(SinkFunction)`: same buffer/flush pattern as enriched sink; writes `FraudDecision` records to `iceberg.fraud_decisions`; in-batch dedup on `transaction_id`
- [X] T021 [US1] Wire `IcebergDecisionsSink` into scoring engine — scoring engine produces `FraudDecision` on every ALLOW/FLAG/BLOCK evaluation and passes it to `IcebergDecisionsSink`; `FraudAlert` and `AlertPostgresSink` remain unchanged
- [X] T022 [US1] Deploy analyst SQL views to Trino — write `analytics/views/v_transaction_audit.sql` (deduplication via `ROW_NUMBER()`, LEFT JOIN enriched → decisions on `transaction_id`) and `analytics/views/v_fraud_rate_daily.sql` from `contracts/trino-analyst-views.sql`
- [X] T023 [US1] Write `tests/integration/test_iceberg_enriched_sink.py` — submit N transactions, query Trino, assert row count matches within 0.1% tolerance; assert `transaction_id` deduplication on duplicate writes
- [X] T024 [US1] Write `tests/integration/test_iceberg_decisions_sink.py` — same reconciliation pattern for `fraud_decisions`; assert ALLOW + FLAG + BLOCK all persisted (not only FLAG/BLOCK)
- [X] T025 [US1] Write `tests/contract/test_avro_iceberg_alignment.py` — reads `enriched-txn-v1.avsc`, reads `enriched_transactions.sql` DDL, asserts every Avro field name appears as an Iceberg column with compatible type

**Checkpoint**: `v_transaction_audit` returns both rows for a test `transaction_id`; reconciliation tests pass; all ALLOW/FLAG/BLOCK decisions present in `fraud_decisions`.

---

## Phase 4: User Story 2 — Data Scientist Generates Point-in-Time Training Dataset (Priority: P2)

**Goal**: Features pushed to the online store during enrichment are retrievable point-in-time correct from the offline store — zero future leakage, values match `enriched_transactions` at enrichment time.

**Independent Test**: Replay a sequence of historical transactions; `get_historical_features()` at each event's timestamp returns values identical to those in `enriched_transactions` at `enrichment_time`; no feature computed after the event timestamp is returned.

### Implementation

- [X] T026 [US2] Write `storage/feature_store/features/velocity.py` — `VelocityFeatureView`: entity `account_id`, TTL 48 h, 8 features mapping from `EnrichedTransaction` velocity fields (`vel_count_1m`, `vel_amount_1m`, `vel_count_5m`, `vel_amount_5m`, `vel_count_1h`, `vel_amount_1h`, `vel_count_24h`, `vel_amount_24h`)
- [X] T027 [P] [US2] Write `storage/feature_store/features/geo.py` — `GeoFeatureView`: entity `account_id`, TTL 24 h, 4 features (`geo_country`, `geo_city`, `geo_network_class`, `geo_confidence`)
- [X] T028 [P] [US2] Write `storage/feature_store/features/device.py` — `DeviceFeatureView`: entity `account_id`, TTL 14 days, 5 features (`device_first_seen`, `device_txn_count`, `device_known_fraud`, `prev_geo_country`, `prev_txn_time_ms`)
- [X] T029 [US2] Extend `IcebergEnrichedSink` (`pipelines/processing/operators/iceberg_sink.py`) — after each `table.append()` flush, call `feast.FeatureStore.push()` with `event_timestamp=record["event_time"]` (NOT wall-clock) for all three feature views; push must be atomic per flush (all views or none — raise on partial failure)
- [X] T030 [US2] Add Feast push failure handling to `IcebergEnrichedSink` — on exception: increment `feast_push_failures_total` metric, emit structured log entry with batch size and exception, re-raise to trigger Flink retry (FR-015, FR-016)
- [X] T031 [US2] Write `tests/integration/test_feast_materialization.py` — push enriched records, call `get_online_features()`, assert values match `enriched_transactions` for same `transaction_id`; call `get_historical_features()` at event timestamps, assert PIT correctness (no future leakage)

**Checkpoint**: `feast apply` succeeds with all three views; `get_online_features()` returns correct velocity/geo/device values within 5 s of enrichment; `get_historical_features()` PIT test passes.

---

## Phase 5: User Story 3 — Platform Engineer Verifies Pipeline Completeness Under Load (Priority: P3)

**Goal**: Row count in both Iceberg tables matches Kafka topic offset within 0.1%; deduplication absorbs AT_LEAST_ONCE duplicates; join on `transaction_id` is standard SQL with no gaps.

**Independent Test**: Publish N known transactions; after processing completes, both table row counts equal N ± 0.1%; duplicate writes produce exactly one row.

### Implementation

- [X] T032 [US3] Write `tests/load/test_sink_throughput.py` — drives 2× peak transaction volume for 60 s; asserts both Iceberg sinks complete all writes within 5 s budget; asserts no duplicates in either table; uses Trino client for row count queries
- [X] T033 [US3] Add sink write latency metric to `IcebergEnrichedSink` and `IcebergDecisionsSink` — emit `iceberg_write_latency_ms` histogram and `iceberg_write_budget_exceeded_total` counter; raise DLQ event when flush exceeds 5 s (Principle VIII / FR-004)
- [X] T034 [US3] Write `storage/lake/migrations/README.md` — schema evolution runbook: step-by-step for adding a new nullable field (Avro → DDL → Feast → CI → catalog)

**Checkpoint**: Load test passes at 2× peak; `iceberg_write_latency_ms` p99 ≤ 5000 ms; no budget-exceeded events during normal load; row count gap ≤ 0.1%.

---

## Phase 6: User Story 4 — Operations Team Verifies Analytics Service Isolation (Priority: P4)

**Goal**: Stopping the analytics query tier has zero effect on fraud scoring latency p99 or throughput; all records written during outage are queryable on restart.

**Independent Test**: Stop `iceberg-rest` and `trino` containers; pipeline continues processing; scoring latency p99 stays under 100 ms; restart analytics tier; all records from the outage window are present.

### Implementation

- [X] T035 [US4] Verify `IcebergEnrichedSink` and `IcebergDecisionsSink` handle catalog `ConnectionError` gracefully — on catalog unavailability: buffer records up to configurable limit (`ICEBERG_BUFFER_MAX=1000`), emit `iceberg_catalog_unavailable_total` metric, do NOT propagate exception to the Flink scoring hot path
- [X] T036 [US4] Add circuit-breaker pattern to both sinks using `pybreaker` — open after 3 consecutive catalog failures; half-open after 30 s; log state transitions; hot path never blocked while breaker is open

**Checkpoint**: With `iceberg-rest` stopped, pipeline processes transactions; scoring latency p99 remains under 100 ms (verified by existing load test harness); after restart, all buffered records flushed to Iceberg.

---

## Phase 7: User Story 5 — Data Analyst Explores Fraud Patterns via SQL (Priority: P3)

**Goal**: Analyst can join `enriched_transactions` and `fraud_decisions` on `transaction_id`, filter by date partition and decision outcome, and receive aggregated results within 60 seconds using only standard SQL.

**Independent Test**: Execute each analyst view query via Trino CLI against populated tables; all four views return results in under 60 s; no UDFs, pre-computed tables, or custom code required.

### Implementation

- [X] T037 [US5] Deploy remaining analyst views to Trino — write `analytics/views/v_rule_triggers.sql` and `analytics/views/v_model_versions.sql` from `contracts/trino-analyst-views.sql`
- [X] T038 [US5] Write `docs/data_catalog.yaml` — field-level entries for all `enriched_transactions` and `fraud_decisions` columns: owner, sensitivity tag, upstream lineage, downstream consumers (match sensitivity table in `data-model.md`)
- [X] T039 [US5] Smoke-test all four analyst views — write `tests/contract/test_trino_views.py`: connects via `trino-python-client`, executes each view with a date-range filter, asserts result schema matches expected columns, asserts query completes in under 60 s

**Checkpoint**: All four views (`v_transaction_audit`, `v_fraud_rate_daily`, `v_rule_triggers`, `v_model_versions`) execute successfully; `data_catalog.yaml` covers all PCI-DSS and PII-tagged fields; smoke tests pass.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Hardening, observability, and CI gate finalization.

- [X] T040 [P] Add Trino-side daily compaction call to `infra/iceberg/init-tables.sh` or a separate cron target — `DELETE FROM ... WHERE transaction_id IN (SELECT transaction_id FROM ... GROUP BY transaction_id HAVING COUNT(*) > 1)` — for cross-batch duplicate removal (research.md §7)
- [X] T041 [P] Verify `.github/workflows/schema-evolution.yml` gate works end-to-end — add a test Avro field to a scratch branch, confirm CI fails; remove it, confirm CI passes
- [X] T042 Update `specs/006-analytics-persistence-layer/quickstart.md` with any environment variables or setup steps discovered during implementation that differ from the draft
- [X] T043 [P] Run `everything-claude-code:python-reviewer` on all new Python files: `iceberg_sink.py`, `iceberg_decisions.py`, `device.py` (DeviceProfileState changes), `enricher.py` (side output wiring), `types.py` (FraudDecision)
- [X] T044 [P] Run `everything-claude-code:security-reviewer` on `iceberg_sink.py` and `iceberg_decisions.py` — verify MinIO credentials are not hardcoded, Iceberg catalog URI is configurable via env vars

**Checkpoint**: CI schema gate blocks on drift; all Python reviewer findings resolved; security reviewer: no hardcoded credentials.

---

## Dependencies

```
Phase 1 (Setup)
    └── Phase 2 (Foundational — T011–T017)
            ├── Phase 3 (US1 — MVP) ─────── independent
            │       └── Phase 4 (US2) ──── independent (needs Feast views from Phase 4 setup)
            │               └── Phase 5 (US3 load test) ── needs US1 sinks complete
            ├── Phase 6 (US4) ─────────── independent (needs US1 sinks for isolation test)
            └── Phase 7 (US5) ─────────── needs analyst views from Phase 3 (T022, T037)
```

**MVP scope**: Phase 1 + Phase 2 + Phase 3 (T001–T025) — delivers User Story 1 (dispute audit query) end-to-end.

---

## Parallel Execution

Within each phase, tasks marked `[P]` can run simultaneously:

| Phase | Parallel group |
|---|---|
| Phase 1 | T003, T004, T005, T006 in parallel; T008, T009, T010 in parallel |
| Phase 2 | T016 parallel with T015; T011, T012, T013, T014 sequential (type chain) |
| Phase 3 | T018, T019, T020, T021 sequential (sink → wire order); T023, T024, T025 parallel |
| Phase 4 | T026, T027, T028 parallel; T029 after all three; T031 after T029 |
| Phase 5 | T032, T033 parallel |
| Phase 7 | T037, T038, T039 parallel after T037 |
| Phase 8 | T040, T041, T043, T044 fully parallel |

---

## Summary

| Metric | Count |
|---|---|
| Total tasks | 44 |
| Phase 1 — Setup | 10 |
| Phase 2 — Foundational | 7 |
| Phase 3 — US1 (P1 MVP) | 8 |
| Phase 4 — US2 (P2) | 6 |
| Phase 5 — US3 (P3) | 3 |
| Phase 6 — US4 (P4) | 2 |
| Phase 7 — US5 (P3) | 3 |
| Phase 8 — Polish | 5 |
| Parallelizable tasks | 22 |
| Independent test criteria | 5 (one per user story) |

**MVP**: Complete Phase 1 + Phase 2 + Phase 3 (tasks T001–T025) to deliver a fully queryable dispute audit store end-to-end.
