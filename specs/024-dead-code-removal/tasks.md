# Tasks: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Input**: Design documents from `/specs/024-dead-code-removal/`
**Branch**: `024-dead-code-removal`

**Organization**: Tasks are grouped by user story (US1 = correctness fix, US2 = structural compliance). US1 is MVP — it directly fixes the production correctness bug.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P] tasks in the same phase (different files, no same-file conflicts)
- **[Story]**: Which user story this task belongs to
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Baseline Verification)

**Purpose**: Confirm research.md line numbers are still accurate before editing

- [x] T001 Grep-verify deletion targets: `grep -n '_FEATURE_ZERO_DEFAULTS\|_FeatureEnrichmentFunction\|_FlinkFeatureEnrichmentFunction\|feature_store_fallback_total' pipelines/scoring/job_extension.py` — confirm lines 12–30, 33–109, 112–138, 121, 128 still match. **If line numbers have shifted**, use T001's actual output to identify the current positions before editing; the numbers in T002–T005 are point-in-time references from the research scan. If a symbol is missing entirely, stop and investigate before proceeding.

---

## Phase 2: Foundational (Blocking Prerequisites)

**No blocking prerequisites** — US1 and US2 deletions are independent of each other at the file level, except that the SC-002 test addition (T006, US1) and the old-test deletion (T007–T008, US2) both touch `tests/unit/scoring/test_job_extension_safety.py`. Run T005 and T006 before T007–T008 to avoid merge conflicts on that file.

**⚠️ File conflict note**: `tests/unit/scoring/test_job_extension_safety.py` is modified by both US1 (T006) and US2 (T007–T008). Complete T006 before starting T007.

---

## Phase 3: User Story 1 — Live Features Reach Evaluator Unmodified (Priority: P1) 🎯 MVP

**Goal**: Remove the `enriched_stream.map(_FlinkFeatureEnrichmentFunction(), ...)` call and all supporting symbols from `job_extension.py` so that live enriched features pass through to `_evaluate` without any intermediate Feast lookup or zero-value substitution.

**Independent Test**: Submit a transaction with `vel_count_5m=10`, `device_known_fraud=True` through `wire_rule_evaluator`; assert `_evaluate` receives those exact values. `TestLiveFeaturesReachEvaluator` (T006) must pass; no Feast import should be present in `job_extension.py`.

### Implementation for User Story 1

- [x] T002 [US1] Delete `_FEATURE_ZERO_DEFAULTS` module-level dict (lines 12–30) from `pipelines/scoring/job_extension.py`
- [x] T003 [US1] Delete `_FeatureEnrichmentFunction` class (lines 33–109) from `pipelines/scoring/job_extension.py`
- [x] T004 [US1] Delete `_FlinkFeatureEnrichmentFunction` inner class and `enriched_stream = enriched_stream.map(...)` reassignment (lines 112–138) from `pipelines/scoring/job_extension.py`
- [x] T005 [US1] Remove `from pipelines.scoring.metrics import feature_store_fallback_total` (line 121) and `feature_store_fallback_total.labels(result="timeout").inc()` (line 128) from `pipelines/scoring/job_extension.py` — do NOT modify `pipelines/scoring/metrics.py`
- [x] T006 [US1] Add `TestLiveFeaturesReachEvaluator` class to `tests/unit/scoring/test_job_extension_safety.py` — two test methods: (1) non-zero features reach `_evaluate` unchanged; (2) no `feast.FeatureStore.get_online_features` call occurs during processing (see `quickstart.md` for the test template)

**Checkpoint**: US1 complete when `grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py` returns nothing and `pytest tests/unit/scoring/test_job_extension_safety.py::TestLiveFeaturesReachEvaluator -v` passes.

---

## Phase 4: User Story 2 — Scoring Path Reflects Architectural Decision (Priority: P2)

**Goal**: Remove all test coverage of the deleted symbols so the test suite contains no broken imports and no references to the removed Feast wiring.

**Independent Test**: `grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' tests/` returns no matches; `pytest tests/ -v` exits 0.

### Implementation for User Story 2

- [x] T007 [US2] Delete import lines 11–12 (`_FEATURE_ZERO_DEFAULTS`, `_FeatureEnrichmentFunction`) from `tests/unit/scoring/test_job_extension_safety.py` (depends on T006 being applied first — same file)
- [x] T008 [US2] Delete `TestFeatureEnrichmentFallback` class (lines 91–135) from `tests/unit/scoring/test_job_extension_safety.py`
- [x] T009 [P] [US2] Delete entire file `tests/integration/test_feature_serving.py` via `git rm tests/integration/test_feature_serving.py`

**Checkpoint**: US2 complete when `grep -r 'FeatureEnrichmentFunction' tests/` returns no matches and all tests pass.

---

## Phase 5: Polish & Verification

**Purpose**: End-to-end success criteria validation

- [x] T010 [P] Run SC-001 check: `grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py` — must return empty
- [x] T011 [P] Run SC-004 import check: `grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' tests/` — must return empty
- [x] T012 Run SC-003 full suite: `pytest tests/ -v` — all tests must pass, no ImportError
- [x] T013 Confirm `pipelines/scoring/metrics.py` is unchanged: `git diff pipelines/scoring/metrics.py` — must show no modifications

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1** (T001): No dependencies — start immediately
- **Phase 3** (T002–T006): Can start after T001. T002–T005 are sequential (same file, `job_extension.py`). T006 can be written in parallel with T002–T005 but must be committed after T002–T005 are done (references the post-deletion state).
- **Phase 4** (T007–T009): T007 and T008 must come after T006 (same file). T009 is independent of T006/T007/T008 (different file) — can run in parallel with Phase 3.
- **Phase 5** (T010–T013): Depends on all Phase 3 and Phase 4 tasks being complete.

### User Story Dependencies

- **US1 (P1)**: Independent. Start after baseline verification (T001). No dependency on US2.
- **US2 (P2)**: T007–T008 depend on T006 (same file write order). T009 is independent of all other tasks and can run in parallel with US1.

### Within Each User Story

- T002 → T003 → T004 → T005: Sequential (all edit `job_extension.py`; do not interleave edits)
- T006: Can be drafted in parallel with T002–T005 but applied after T005 is committed
- T007 → T008: Sequential (both edit `test_job_extension_safety.py`)
- T009: Independent of all other tasks

### Parallel Opportunities

```bash
# T009 (delete integration test file) can run immediately alongside US1 work:
git rm tests/integration/test_feature_serving.py  # T009 — no file conflicts

# T010 and T011 can run in parallel once Phases 3 and 4 are complete:
grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py  # T010
grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' tests/                              # T011
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. T001: Verify baseline line numbers
2. T002–T005: Delete Feast symbols from `job_extension.py`
3. T006: Add SC-002 regression test
4. **STOP and VALIDATE**: `pytest tests/unit/scoring/test_job_extension_safety.py::TestLiveFeaturesReachEvaluator -v` passes; `grep` for deleted symbols returns nothing in `job_extension.py`
5. The production correctness bug is fixed at this point — US2 is cleanup

### Incremental Delivery

1. Complete T001 → baseline verified
2. Complete T002–T005 → Feast call removed from pipeline
3. Complete T006 → correctness regression test in place (US1 done)
4. Complete T007–T009 → dead test coverage removed (US2 done)
5. Complete T010–T013 → all success criteria verified

### Single-Developer Sequence

```
T001 → T002 → T003 → T004 → T005 → T009 → T006 → T007 → T008 → T010 → T011 → T012 → T013
```

(T009 moved up since it's independent and clears the integration test file early.)

---

## Notes

- `metrics.py` MUST NOT be modified — `feature_store_fallback_total` is preserved for the future standalone consumer (FR-005)
- `enriched_stream` at line 185 of `job_extension.py` automatically resolves to the upstream enriched stream once the line 138 `.map()` reassignment is removed — no rewiring required
- T002–T005 must all land before any test run; the test suite will have broken imports if only some symbols are removed
- Commit after T005 and again after T006 to keep logical groups separate in git history
