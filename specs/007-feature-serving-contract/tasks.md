# Tasks: Feature Serving Contract (007)

**Input**: Design documents from `/specs/007-feature-serving-contract/`
**Branch**: `007-feature-serving-contract`
**Date**: 2026-04-19

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. No TDD approach was requested; unit and integration tests are included for the two P1 stories (mandatory contract validation) but kept minimal.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create new package and directory structure before any code is written.

- [X] T001 Create `pipelines/scoring/clients/__init__.py` (empty package init)
- [X] T002 [P] Ensure `tests/unit/` and `tests/integration/` directories exist with `__init__.py` files

**Checkpoint**: Package skeleton in place — all subsequent phases can write to their respective files.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data types and metrics that every user story depends on. MUST be complete before any story implementation begins.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Add `FeatureVector` frozen dataclass (17 fields + `account_id`) and `ZERO_FEATURE_VECTOR` module-level constant to `pipelines/scoring/types.py`
- [X] T004 Add `FallbackReason` enum (`TIMEOUT`, `UNAVAILABLE`) to `pipelines/scoring/types.py` (same file, directly after FeatureVector)
- [X] T005 [P] Add `feature_store_fallback_total` Counter (label: `reason`), `feature_store_miss_total` Counter, and `feature_store_retrieval_seconds` Histogram (buckets: 0.001, 0.002, 0.003, 0.005, 0.010, 0.050) to `pipelines/scoring/metrics.py`
- [X] T006 [P] Add `feature_materialization_lag_ms` Gauge to `pipelines/scoring/metrics.py` (staleness monitor gauge — updated by write path, alerted on by read path)

**Checkpoint**: `FeatureVector`, `FallbackReason`, and all four Prometheus metrics are importable. All user stories can now proceed.

---

## Phase 3: User Story 1 — Scoring Engine Reads Features Within Budget (Priority: P1) 🎯 MVP

**Goal**: The scoring engine retrieves a full, populated feature vector from the Feast online store within 2ms p99.

**Independent Test**: Seed the SQLite online store with velocity, geo, and device features for `acct-test-001`, call `FeatureServingClient.get_features("acct-test-001", ...)`, and assert the returned `FeatureVector` contains all 17 non-zero fields; verify `feature_store_retrieval_seconds` histogram increments and no fallback/miss counters change.

### Implementation for User Story 1

- [X] T007 [US1] Implement `FeatureServingClient.__init__()`, `open()`, and `close()` with `ThreadPoolExecutor` lifecycle in `pipelines/scoring/clients/feature_serving.py`
- [X] T008 [US1] Implement `FeatureServingClient._fetch_from_store(account_id)` calling `feast.FeatureStore.get_online_features()` with all 17 feature strings across 3 views in `pipelines/scoring/clients/feature_serving.py`
- [X] T009 [US1] Implement `FeatureServingClient.get_features()` happy path: submit `_fetch_from_store` to executor, call `future.result(timeout=self._timeout_seconds)`, parse response into `FeatureVector`, record `feature_store_retrieval_seconds` histogram in `pipelines/scoring/clients/feature_serving.py`
- [X] T010 [US1] Write unit tests for happy path (mock `FeatureStore.get_online_features` returning populated dict, assert `FeatureVector` fields match) in `tests/unit/test_feature_serving_client.py`

**Checkpoint**: `FeatureServingClient` can retrieve a populated feature vector from a seeded SQLite store. US1 acceptance scenarios 1 and 2 are verifiable.

---

## Phase 4: User Story 2 — Graceful Degradation on Store Unavailability or Timeout (Priority: P1)

**Goal**: The scoring engine returns a zero-valued `FeatureVector` within 3ms when the online store times out or is unavailable, increments the fallback counter, and logs the fallback event. No error propagates to the caller.

**Independent Test**: Mock `_fetch_from_store` to raise `concurrent.futures.TimeoutError` (or `ConnectionError`); call `get_features()` and assert: zero `FeatureVector` is returned, `feature_store_fallback_total{reason="timeout"}` (or `"unavailable"`) increments, no exception escapes, and elapsed time is < 5ms.

### Implementation for User Story 2

- [X] T011 [US2] Add `concurrent.futures.TimeoutError` catch in `get_features()` → increment `feature_store_fallback_total{reason="timeout"}`, log `feature_store_fallback` WARNING with `account_id`, `transaction_id`, `transaction_timestamp`, `reason`, `elapsed_ms`, `component='feature_serving_client'` (Principle VIII), return zero vector with `account_id` filled in `pipelines/scoring/clients/feature_serving.py`
- [X] T012 [US2] Add generic `Exception` catch in `get_features()` → increment `feature_store_fallback_total{reason="unavailable"}`, log `feature_store_fallback` WARNING with `account_id`, `transaction_id`, `transaction_timestamp`, `reason`, `elapsed_ms`, `component='feature_serving_client'` (Principle VIII), return zero vector with `account_id` filled in `pipelines/scoring/clients/feature_serving.py`
- [X] T013 [US2] Ensure histogram is always recorded (including on timeout/unavailable paths) and that zero vector's `account_id` field is set to the requested `account_id` (not empty string) in `pipelines/scoring/clients/feature_serving.py`
- [X] T014 [US2] Write unit tests for timeout path (mock slow `_fetch_from_store` exceeding 3ms, assert `feature_store_fallback_total{reason="timeout"}` increments, zero vector returned, no exception) in `tests/unit/test_feature_serving_client.py`
- [X] T015 [P] [US2] Write unit tests for unavailability path (mock `_fetch_from_store` raising `ConnectionError`, assert `feature_store_fallback_total{reason="unavailable"}` increments, zero vector returned) in `tests/unit/test_feature_serving_client.py`

**Checkpoint**: All three fallback scenarios (timeout, unavailability, recovery) work. `feature_store_fallback_total` increments correctly by reason label. US2 acceptance scenarios 1–3 are verifiable.

---

## Phase 5: User Story 3 — Cold-Start and Cache Miss Handling (Priority: P2)

**Goal**: When the online store is reachable but contains no entry for the account, the engine returns a zero-valued `FeatureVector`, logs a `feature_store_miss` WARNING, and increments `feature_store_miss_total`. No fallback counter increments.

**Independent Test**: Call `get_features()` with a mock Feast response where all 17 feature values are `None`; assert zero `FeatureVector` is returned, `feature_store_miss_total` increments by 1, `feature_store_fallback_total` does NOT increment, and the log contains a `feature_store_miss` event with the correct `account_id` and `transaction_timestamp`.

### Implementation for User Story 3

- [X] T016 [US3] Add miss detection in `get_features()` success path: after parsing the Feast response dict, check if all 17 feature values are `None`; if so, log `feature_store_miss` WARNING with `account_id`, `transaction_id`, `transaction_timestamp`, `component='feature_serving_client'` (Principle VIII), increment `feature_store_miss_total`, return zero vector with `account_id` set in `pipelines/scoring/clients/feature_serving.py`
- [X] T017 [US3] Handle partial miss case (some values `None`, some populated): treat entire vector as zero (no partial merge) — enforce this in the response parsing logic; log a `feature_store_partial_response` WARNING with `account_id`, `transaction_id`, `transaction_timestamp`, `populated_fields=<count of non-None fields>`, `component='feature_serving_client'` (Principle VIII — partial conversions must not be silent); increment `feature_store_miss_total` (partial responses share the miss counter, distinguished by the log event name) in `pipelines/scoring/clients/feature_serving.py`
- [X] T018 [US3] Write unit tests for cache miss (mock Feast returning all-None dict, assert miss counter increments, fallback counter stays at 0, zero vector returned with correct account_id) in `tests/unit/test_feature_serving_client.py`

**Checkpoint**: Cold-start and cache miss are handled silently. US3 acceptance scenarios 1–3 are verifiable.

---

## Phase 6: User Story 4 — Staleness Alerting Closes the Write-Read Loop (Priority: P2)

**Goal**: When materialization has not updated the online store for > 30 seconds, a `FeatureStoreStalenessHigh` Prometheus alert fires. It resolves automatically when materialization resumes.

**Independent Test**: Create the Prometheus alert rule file; manually set `feature_materialization_lag_ms` to 35,000 and verify the alert expression evaluates to `true`; set it to 10,000 and verify it evaluates to `false`. Verify the gauge is updated in the Feature 006 push path.

### Implementation for User Story 4

- [X] T019 [US4] Create `infra/prometheus/alerts/feature_serving.yml` with the `FeatureStoreStalenessHigh` alert rule (`expr: feature_materialization_lag_ms > 30000`, `for: 60s`, `severity: critical`, `team: fraud-platform`, full annotations and `runbook_url`); after creating the file, smoke-verify the alert expression: set `feature_materialization_lag_ms` to 35,000 and confirm the expression evaluates to true; set it to 10,000 and confirm false (SC-005 smoke check)
- [X] T020 [US4] Update the Feast push path in `pipelines/processing/operators/iceberg_sink.py` to import `feature_materialization_lag_ms` gauge from `pipelines/scoring/metrics` and set it to `(current_epoch_ms - last_push_epoch_ms)` after every successful `store.push()` call

**Checkpoint**: The alert rule file exists and is syntactically valid. The gauge is updated on every successful materialization push. US4 acceptance scenarios 1–3 are verifiable via Prometheus UI.

---

## Phase 7: Pipeline Integration

**Purpose**: Wire the `FeatureServingClient` into the live scoring pipeline before the rule evaluator.

- [X] T021 Add `_FeatureEnrichmentFunction` Flink `MapFunction` subclass to `pipelines/scoring/job_extension.py` that holds a `FeatureServingClient`, calls `client.get_features()` per transaction, and merges the returned `FeatureVector` fields into the transaction dict before passing downstream
- [X] T022 Modify `wire_rule_evaluator()` (or its upstream chain) in `pipelines/scoring/job_extension.py` to insert `_FeatureEnrichmentFunction` before the rule evaluator step; ensure `FeatureServingClient.open()` is called in the operator's `open()` and `close()` in `close()`
- [X] T023 Write integration test: seed SQLite store, instantiate `_FeatureEnrichmentFunction`, call `map()` with a sample transaction dict, assert feature fields are present in the output dict in `tests/integration/test_feature_serving.py`

**Checkpoint**: Feature retrieval is live in the scoring pipeline. Enriched transactions carry the feature vector before rule evaluation.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T024 [P] Run all 4 quickstart.md scenarios manually against local SQLite store and verify output matches expected results documented in `specs/007-feature-serving-contract/quickstart.md`; additionally call `get_features()` 1,000× against the seeded store and inspect `feature_store_retrieval_seconds` histogram to confirm p99 < 2ms (SC-001 verification)
- [X] T025 [P] Add `feature_serving.yml` to any Prometheus scrape config or Docker Compose alert rules mount in `infra/docker-compose.yml` so the alert is loaded by the local Prometheus instance
- [X] T026 Verify `pyproject.toml` lists `feast>=0.62.0` and `prometheus-client` as dependencies (no new additions required — already declared in Feature 006)

**Note — SC-006 (out of scope)**: SC-006 (E2E latency < 100ms p99 even when feature retrieval exhausts the full 3ms timeout) spans the entire scoring pipeline and cannot be verified within this feature's unit/integration test suite. It is tracked as a system-level SLO in `tests/load/` and is verified as part of the pre-production checklist.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — no dependency on US2/US3/US4
- **US2 (Phase 4)**: Depends on Phase 3 (extends `get_features()` from US1)
- **US3 (Phase 5)**: Depends on Phase 4 (extends the same `get_features()` method)
- **US4 (Phase 6)**: Depends on Phase 2 only — can be worked in parallel with US1–US3
- **Pipeline Integration (Phase 7)**: Depends on Phase 5 (needs complete `get_features()`)
- **Polish (Phase 8)**: Depends on Phases 6 and 7

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational
- **US2 (P1)**: Extends US1's `get_features()` — must follow US1
- **US3 (P2)**: Extends US2's `get_features()` — must follow US2
- **US4 (P2)**: Independent after Foundational — can run in parallel with US1–US3

### Within Each Phase

- T003 before T004 (same file, sequential)
- T005 and T006 can run in parallel (same file section, but no conflicts)
- T007 before T008 before T009 (sequential — same class)
- T011, T012, T013 sequential (same method, same file)
- T014 and T015 can run in parallel (same test file, different test functions)

### Parallel Opportunities

- T002 ‖ T001 (different files)
- T005 ‖ T006 (same file, adjacent sections)
- T014 ‖ T015 (same test file, independent test functions)
- US4 (T019, T020) ‖ US1–US3 (different files)

---

## Parallel Example: Foundational Phase

```bash
# Run in parallel (independent files / sections):
Task T005: "Add feature store metrics to pipelines/scoring/metrics.py"
Task T006: "Add feature_materialization_lag_ms gauge to pipelines/scoring/metrics.py"
Task T002: "Ensure tests/ directories exist"
```

## Parallel Example: US4 alongside US1

```bash
# Once Phase 2 is complete, can start simultaneously:
Task T007 (US1): "Implement FeatureServingClient.__init__/open/close"
Task T019 (US4): "Create infra/prometheus/alerts/feature_serving.yml"
Task T020 (US4): "Update iceberg_sink.py to set feature_materialization_lag_ms gauge"
```

---

## Implementation Strategy

### MVP First (US1 + US2 — Full P1 Scope)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (FeatureVector, metrics)
3. Complete Phase 3: US1 (happy path retrieval)
4. Complete Phase 4: US2 (timeout + unavailability fallback)
5. **STOP and VALIDATE**: Run unit tests, verify fallback counter increments, verify p99 < 2ms
6. Complete Phase 5: US3 (cache miss)
7. Complete Phase 6: US4 (staleness alert)
8. Complete Phase 7: Pipeline integration
9. Complete Phase 8: Polish

### Incremental Delivery

- After Phase 3: `FeatureServingClient` is usable standalone (no fallback yet)
- After Phase 4: Full fallback contract satisfied — P1 complete
- After Phase 5: Cold-start safe — P2 complete for US3
- After Phase 6: Full observability loop closed — P2 complete for US4
- After Phase 7: Feature is live in the scoring pipeline

---

## Notes

- `[P]` tasks = different files or independent sections, no blocking dependencies
- `[US#]` label maps each task to its user story for traceability
- US2 and US3 both modify `get_features()` in `feature_serving.py` — implement sequentially to avoid merge conflicts
- US4 (T019, T020) touches completely different files and can be worked in parallel with US1–US3
- The `feature_materialization_lag_ms` gauge is defined in `pipelines/scoring/metrics.py` but updated by the processing pipeline (`iceberg_sink.py`) — import it across package boundary
- Commit after each phase checkpoint before moving to the next phase
