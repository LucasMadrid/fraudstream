# Tasks: Restore CI Green Build

**Feature**: 023-fix-ci-green  
**Generated**: 2026-05-12  
**Source**: spec.md, plan.md, research.md, data-model.md

---

## Summary

| Metric | Value |
|--------|-------|
| Total Tasks | 21 |
| User Stories | 4 (P1→P4) |
| Files Modified | 3 |
| Ruff Violations | 13 (all cleared by US1+US2) |

### Dependency Graph

```
Phase 1: Setup (confirm env + baseline violations)
    |
    v
Phase 2: Foundational (read ProcessingDLQSink API — all rewrites depend on this)
    |
    ├──────────────────────────────┐
    v                              v
Phase 3: [US1] DLQ Tests (P1)   Phase 4: [US2] Secrets/Imports (P2)
    |                              |
    └──────────────┬───────────────┘
                   v
          Phase 5: [US3] Linting Verification (P3)
                   |
                   v
          Phase 6: [US4] Stability Check (P4)
                   |
                   v
          Phase 7: Polish & CI Push
```

US1 and US2 can be implemented in parallel (different files, no shared state).

---

## Phase 1: Setup

**Purpose**: Confirm working environment and establish baseline violation count before any changes.

- [X] T001 Confirm branch is `023-fix-ci-green` and working tree is clean (`git status`)
- [X] T002 Run `.venv/bin/ruff check tests/` and confirm exactly 13 violations across 3 files
- [X] T003 [P] Run `python -m pytest tests/unit/processing/test_dlq_sink.py --collect-only 2>&1 | head -30` and confirm collection fails with F821 errors

---

## Phase 2: Foundational

**Purpose**: Lock in the exact `ProcessingDLQSink` API before rewriting tests — prevents test drift.

- [X] T004 Read `pipelines/processing/shared/dlq_sink.py` and record: constructor signature, public method signatures (`send` keyword args, `flush` default), whether `_producer` is None after `__init__`, and which attributes exist on the instance

---

## Phase 3: [US1] DLQ Sink Tests Pass (P1)

**Story Goal**: All 5 test classes in `test_dlq_sink.py` collect and pass; zero F821 errors.

**Independent Test Criteria**: `python -m pytest tests/unit/processing/test_dlq_sink.py -v` exits 0.

- [X] T005 [US1] Rewrite `TestProcessingDLQSinkInit` in `tests/unit/processing/test_dlq_sink.py` — keep class name; replace `test_stores_config` body to assert only attributes that actually exist after `__init__` (drop `assert p._producer is None` — producer is created eagerly)
- [X] T006 [US1] Rewrite `TestProcessingDLQSinkOpen` in `tests/unit/processing/test_dlq_sink.py` — `ProcessingDLQSink` has no `open()` method; replace both tests with a test that verifies `_producer` is set after construction by patching `confluent_kafka.Producer` in the constructor call
- [X] T007 [US1] Replace class `TestDLQKafkaProducerProduce` with `TestProcessingDLQSinkSend` in `tests/unit/processing/test_dlq_sink.py` — rewrite all 4 test methods to use `p.send(source_topic=..., original_payload=..., error_type=..., error_message=...)` (keyword-only); remove all `p.produce(...)` calls; mock `confluent_kafka.Producer` or `p._producer` as appropriate
- [X] T008 [US1] Replace class `TestDLQKafkaProducerFlush` with `TestProcessingDLQSinkFlush` in `tests/unit/processing/test_dlq_sink.py` — rewrite both test methods (`test_flush_delegates`, `test_flush_noop_when_not_open`) using `p.flush()` / `p.flush(timeout=3.0)` with mock `_producer`
- [X] T009 [US1] Remove class `TestDLQKafkaProducerClose` from `tests/unit/processing/test_dlq_sink.py` — `ProcessingDLQSink` has no `close()` method; delete the entire class (2 tests)
- [X] T010 [US1] Run `python -m pytest tests/unit/processing/test_dlq_sink.py --collect-only` — confirm clean collection (0 errors)
- [X] T011 [US1] Run `python -m pytest tests/unit/processing/test_dlq_sink.py -v` — confirm all tests pass or have intentional skips

---

## Phase 4: [US2] Secrets-Free Test Fixtures (P2)

**Story Goal**: Zero F401/E402 in TLS fixture files; zero I001 in security conftest; no PEM material in tracked files.

**Independent Test Criteria**: `.venv/bin/ruff check tests/fixtures/tls/ tests/integration/conftest_security.py` exits 0.

- [X] T012 [P] [US2] Fix E402 in `tests/fixtures/tls/cert_generator.py` — move `import ipaddress` (currently at line ~244, after function bodies) to the top-of-file import block alongside the other stdlib imports
- [X] T013 [P] [US2] Fix F401×3 in `tests/fixtures/tls/cert_generator.py` — remove the `TYPE_CHECKING` guard block (lines ~17–19) that re-imports `x509`, `serialization`, `rsa`; these are already imported at runtime level; the guarded copies are dead code
- [X] T014 [US2] Fix I001 in `tests/integration/conftest_security.py` — re-order the import block so `from tests.fixtures.tls import get_tls_cert_path` appears before any non-import statements (move it into the contiguous import section at the top of the file)
- [X] T015 [US2] Run `grep -r "BEGIN CERTIFICATE\|BEGIN PRIVATE KEY\|BEGIN RSA PRIVATE KEY" tests/ --include="*.py" --include="*.pem" --include="*.crt"` — confirm zero matches (no hardcoded PEM material in tracked files)

---

## Phase 5: [US3] Linting Checks Pass (P3)

**Story Goal**: `ruff check .` and `ruff format --check .` both exit 0 with no output.

**Independent Test Criteria**: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && echo "CLEAN"` prints CLEAN.

- [X] T016 [US3] Run `.venv/bin/ruff check tests/` — confirm 0 violations; if any remain, fix them before proceeding
- [X] T017 [US3] Run `.venv/bin/ruff format --check tests/` — confirm 0 format violations; if any remain, run `.venv/bin/ruff format tests/` then re-check

---

## Phase 6: [US4] Stable, Non-Flaky Test Suite (P4)

**Story Goal**: `pytest tests/unit/processing/` and `pytest tests/integration/security/` (if runnable) produce identical results across 5 consecutive runs.

**Independent Test Criteria**: All 5 runs exit with the same code (0 or skip-only).

- [X] T018 [US4] Run `python -m pytest tests/unit/processing/ -q` five times consecutively — confirm identical exit codes and test counts each run; if a test is flaky, isolate and fix the non-determinism (likely shared state or missing mock teardown)
- [X] T019 [US4] Run `python -m pytest tests/unit/ --tb=short` and verify overall unit test suite passes; confirm no regressions vs pre-fix baseline (no previously-passing tests now fail)

---

## Phase 7: Polish & CI Verification

**Purpose**: Final full-suite validation and branch push.

- [X] T020 Run the full CI simulation locally: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && python -m pytest tests/unit/ -v --tb=short` — confirm all three steps exit 0
- [ ] T021 Commit all changes with message: `fix(ci): restore green build — update DLQ test interface, fix ruff violations`; then push branch `023-fix-ci-green` and confirm GitHub Actions passes all workflow stages

---

## Implementation Strategy

### MVP Scope
**US1 (P1) + US3 (P3) together** unlock CI stage 1 and stage 2. US1 eliminates all 9 F821 errors; US2 eliminates the remaining 4 ruff errors. Completing both restores a passing CI run.

### Recommended Execution Order

1. **T004** (read API) — 5 min
2. **T005–T009** (rewrite test_dlq_sink.py) + **T012–T014** (fix cert_generator.py + conftest_security.py) — parallel, ~45 min total
3. **T010–T011** (verify DLQ tests) + **T015** (verify no PEM) — parallel
4. **T016–T017** (ruff clean verification) — 5 min
5. **T018–T019** (stability check) — 10 min
6. **T020–T021** (full CI simulation + push) — 10 min

### Parallel Execution

**US1 and US2 are fully parallel** (different files):
- T005–T009: all in `tests/unit/processing/test_dlq_sink.py`
- T012–T014: in `tests/fixtures/tls/cert_generator.py` and `tests/integration/conftest_security.py`

Within US2: T012 and T013 are parallel (both in cert_generator.py but non-overlapping lines).

---

## Success Checklist

- [X] US1: `pytest tests/unit/processing/test_dlq_sink.py -v` passes — 0 F821 errors
- [X] US2: `ruff check tests/fixtures/tls/ tests/integration/conftest_security.py` exits 0
- [X] US3: `ruff check . && ruff format --check .` exits 0 — 0 total violations
- [X] US4: 5 consecutive `pytest tests/unit/processing/ -q` runs identical
- [ ] All: GitHub Actions CI passes on branch push
