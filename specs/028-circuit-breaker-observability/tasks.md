---

description: "Task list for SD-028 — circuit breaker observability (opened_at, last_failure_time, metric rename) per SD-024"
---

# Tasks: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Input**: Design documents from `/specs/028-circuit-breaker-observability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests ARE in scope. Spec acceptance scenarios (US1 #1-5, US2 #1-4) and SC-003 / SC-004 each describe a behavior verifiable by a unit test. New test cases are written FIRST (RED) before the listener / management-api implementation, then made GREEN by the implementation tasks. The metric-rename counter tests already exist and are updated mechanically.

**Organization**: Two user stories from the spec. US1 (opened_at + last_failure_time) is the MVP and load-bearing for US2's "operator can correlate" rationale; US2 (metric rename) is independent and ships alongside. The Phase 0 R3 finding (management_api private-attr cleanup) is folded into US1 because the listener's new attributes have no consumer without it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files OR independent edits with no shared state)
- **[Story]**: Which user story this task belongs to (US1, US2)
- Include exact file paths in descriptions

## Path Conventions

This is an existing Python project. All paths are relative to repo root `/Users/lucasmadridbbva/Desktop/repos/streaming/fraudstream/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment is ready. No project initialization — this is surgical edits to two existing source files and one test file.

- [X] T001 Verify clean working tree on branch `028-circuit-breaker-observability` (`git status` shows only `specs/028-circuit-breaker-observability/` plus pre-existing untracked drift such as `storage/feature_store/online_store.db`, `CLAUDE.md`, and other spec drafts)
- [X] T002 [P] Re-read the three files in scope to refresh context: `pipelines/scoring/circuit_breaker.py`, `pipelines/scoring/management_api.py` (lines 520-570 — the circuit-breaker-state endpoint), `tests/unit/scoring/test_circuit_breaker.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Re-verify Phase 0 R1 — that pybreaker's `state_change(cb, old_state, new_state)` and `failure(cb, exc)` callbacks are present in the installed version. A pybreaker version drift between planning and implementation could invalidate the design.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Confirm pybreaker callback API: `.venv/bin/python -c "import inspect, pybreaker; print(inspect.signature(pybreaker.CircuitBreakerListener.state_change)); print(inspect.signature(pybreaker.CircuitBreakerListener.failure))"` returns the two signatures listed in `research.md` R1 (cb, old_state, new_state) and (cb, exc) respectively. If signatures differ, halt and update `research.md` before resuming.

**Checkpoint**: Callback API matches the design.

---

## Phase 3: User Story 1 — Open-state timestamps reachable via listener (Priority: P1) 🎯 MVP

**Goal**: `FraudCircuitBreakerListener` exposes `opened_at` and `last_failure_time` as public attributes, populated via pybreaker's `state_change` and `failure` callbacks. `MLCircuitBreaker` exposes the listener publicly so external consumers can read those attributes. `management_api.py` is refactored to read from the listener instead of pybreaker private attributes.

**Independent Test**: The new tests in T004 pass after T005-T007 land. SC-001, SC-003 from the spec are operationalised by these tests. The management API endpoint returns the same `CircuitBreakerState` shape but the source of `last_failure_time` and `next_probe_time` is now the listener.

### Tests for User Story 1 (write FIRST, ensure they FAIL before implementation)

- [X] T004 [US1] Add 7 new test methods inside `class TestFraudCircuitBreakerListener` in `tests/unit/scoring/test_circuit_breaker.py`, covering US1 acceptance scenarios 1-5 plus the `MLCircuitBreaker.listener` exposure. Use the test scaffolds in `specs/028-circuit-breaker-observability/quickstart.md` Step 3b. Run `.venv/bin/python -m pytest tests/unit/scoring/test_circuit_breaker.py::TestFraudCircuitBreakerListener -v` and confirm all 7 NEW tests fail before continuing (RED phase).

### Implementation for User Story 1

- [X] T005 [US1] Modify `pipelines/scoring/circuit_breaker.py` — `FraudCircuitBreakerListener` class:
  - Add `from datetime import datetime, timezone` import near the top
  - Add `__init__(self) -> None: self.opened_at: datetime | None = None; self.last_failure_time: datetime | None = None`
  - In `state_change`, after the existing gauge update + log, add: `if new_state == "open" and old_state != new_state: self.opened_at = datetime.now(timezone.utc)` (idempotent OPEN guard per R5)
  - Add new `def failure(self, cb, exc) -> None: self.last_failure_time = datetime.now(timezone.utc)` method
  - Add `"FraudCircuitBreakerListener"` to `__all__`
- [X] T006 [US1] Modify `pipelines/scoring/circuit_breaker.py` — `MLCircuitBreaker.__init__`:
  - Extract the inline `FraudCircuitBreakerListener()` into `self.listener = FraudCircuitBreakerListener()` (public attribute, no underscore prefix)
  - Update the `pybreaker.CircuitBreaker(listeners=[self.listener])` call to reference the new attribute
- [X] T007 [US1] Refactor `pipelines/scoring/management_api.py` (circuit-breaker-state endpoint, around lines 538-562) to consume listener attributes instead of pybreaker private attributes:
  - Locate the breaker / `MLCircuitBreaker` instance available at the endpoint and obtain `listener = breaker.listener`
  - Replace `getattr(cb, "_last_failure_time", None)` + epoch-to-datetime conversion with `listener.last_failure_time.isoformat() if listener.last_failure_time is not None else None`
  - Replace `getattr(cb, "_opened_at", None)` with `listener.opened_at` (already a `datetime`; use `.timestamp()` for the probe-time arithmetic)
  - Keep `getattr(cb, "reset_timeout", None)` — `reset_timeout` is a public pybreaker attribute and the defensive `getattr` is fine
  - Preserve the surrounding `try/except` blocks but expect them to be effectively no-ops post-refactor (no more `AttributeError` on private attrs)
  - Verify `grep -nE 'getattr\(cb, "_' pipelines/scoring/management_api.py` returns zero matches after the edit

**Checkpoint**: T004's 7 tests now pass (GREEN). The management API endpoint returns the same `CircuitBreakerState` external shape sourced from the listener. SC-001 + SC-003 verified.

---

## Phase 4: User Story 2 — Metric rename (Priority: P1)

**Goal**: The Prometheus counter `ml_fallback_decisions_total` is renamed to `ml_circuit_open_calls_total` across the codebase. The counter's description string is updated to match. Every Python reference (declaration, increment, import, `__all__`, test assertions) is updated. The PR description records the rename so dashboard / alert owners can sync out-of-tree configs.

**Independent Test**: SC-002 from spec — `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` returns zero matches.

### Implementation for User Story 2

- [X] T008 [US2] Modify `pipelines/scoring/circuit_breaker.py`:
  - Rename the module-level Counter identifier from `ml_fallback_decisions_total` to `ml_circuit_open_calls_total` (lines 26-29)
  - Change the Counter's first argument (the metric name string) to `"ml_circuit_open_calls_total"`
  - Change the Counter's description string to `"Total calls that arrived while the ML circuit was OPEN"`
  - Update the `before_call` increment call site (line 49) to reference `ml_circuit_open_calls_total`
  - Update the `__all__` export list (line 102) — replace the old name with the new
- [X] T009 [US2] Update `tests/unit/scoring/test_circuit_breaker.py`:
  - Update the import line (line 13) to `ml_circuit_open_calls_total` instead of `ml_fallback_decisions_total`
  - Update all references in test bodies (lines 89, 91, 97, 98) — pure string substitution, test logic unchanged

**Checkpoint**: `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` returns zero matches (SC-002). The existing counter-increment tests pass against the new name (SC-004).

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Verify each spec criterion, confirm no CI regression, and ship.

- [X] T010 [P] Stale-reference grep — also updated in-tree `infra/prometheus/alerts/fraud_rule_engine.yml:48` and `tests/chaos/FAILURE_SCENARIOS.md` (3 refs) to use the new metric name — `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/ infra/` returns zero matches (SC-002 final check across a wider tree)
- [X] T011 [P] No-private-attr grep — `grep -rnE 'getattr\(cb, "_|cb\._opened_at|cb\._last_failure_time|cb\._state\b' pipelines/scoring/` returns zero matches (SD-024 root cause closed; R3 verified)
- [X] T012 Run the circuit-breaker test file: `.venv/bin/python -m pytest tests/unit/scoring/test_circuit_breaker.py -v` — all tests pass (the 7 new US1 tests + the 3 updated US2 tests + the existing 7 success/fallback tests, ≈17 total)
- [X] T013 Run the full unit suite with coverage gate: `.venv/bin/python -m pytest tests/unit/ --cov=pipelines --cov-fail-under=20` — passes; coverage at least at the pre-feature baseline (SC-005)
- [X] T014 [P] Lint check: `.venv/bin/ruff check pipelines/scoring/circuit_breaker.py pipelines/scoring/management_api.py tests/unit/scoring/test_circuit_breaker.py` and `.venv/bin/ruff format --check ...` — both clean
- [ ] T015 Bundle changes into a single commit. Commit message MUST reference SD-024 explicitly (FR-007). No GitHub issue exists for SD-024 today (verified during planning), so no `Closes #N` trailer; the commit body cites SD-024 as the originating decision.
- [ ] T016 Push branch `028-circuit-breaker-observability` and open a PR titled `feat(scoring): SD-024 circuit breaker observability — opened_at, last_failure_time, metric rename`. The PR description MUST include a "Metric rename" section listing the old name (`ml_fallback_decisions_total`) and the new name (`ml_circuit_open_calls_total`) so dashboard / alert owners can update their out-of-tree configs (FR-006). Wait for the full CI run; all stages must be green (same set as `main` baseline, SC-005).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — pybreaker callback signatures must be re-verified before relying on them
- **US1 (Phase 3)**: Depends on Phase 2
- **US2 (Phase 4)**: Depends on Phase 2 only (independent of US1 functionally, but both edit `circuit_breaker.py` so they sequence within an editor session)
- **Polish (Phase 5)**: Depends on US1 and US2 both complete

### Within Each User Story

- **US1**: T004 (RED tests) → T005 (listener changes) → T006 (`MLCircuitBreaker.listener` exposure) → T007 (`management_api.py` consumes listener attrs)
  - T005 must precede T006 (the listener class needs the new attributes before MLCircuitBreaker stores it as a public attribute — well, technically not required, but logically sequential within the same file)
  - T006 must precede T007 (the `management_api` refactor reads `breaker.listener`, which doesn't exist until T006)
  - T004 must precede T005-T007 (TDD red phase before green)
- **US2**: T008 (rename in production code) → T009 (rename in tests). Order matters because running tests between T008 and T009 would surface ImportError; do both in one editor session.

### Parallel Opportunities

- **T002** (re-read files) is `[P]` — read-only
- **T010, T011, T014** in polish are all `[P]` — different file sets, read-only or write-different-files
- **Across stories**: NONE within the source files (both stories edit `circuit_breaker.py`). The test file edits (T004 vs T009) touch the same file at different test methods, so they're parallel-safe in principle but realistically sequential.

### Same-File Constraint

- `pipelines/scoring/circuit_breaker.py` is edited by T005, T006 (US1), and T008 (US2). All three sequence in a single editor session.
- `tests/unit/scoring/test_circuit_breaker.py` is edited by T004 (US1) and T009 (US2). Same.

---

## Parallel Example: Polish gates

```bash
# After the implementation lands, the polish verifications can run concurrently
# in separate terminals:
Task: "Run stale-reference grep (T010)"
Task: "Run no-private-attr grep (T011)"
Task: "Run ruff check + format (T014)"

# Sequential, depend on the polish gates passing:
Task: "Commit (T015)"
Task: "Push + open PR + watch CI (T016)"
```

---

## Implementation Strategy

### MVP (single PR — recommended)

US1 and US2 are intertwined enough that splitting them is more work than they're worth:

- Both edit the same source file (`circuit_breaker.py`) and the same test file
- US2's metric rename touches a one-line increment site that benefits from the same review pass as US1's listener changes
- A single PR closes SD-024 completely; no transient state where half of SD-024 is on `main`

Ship order:

1. Phase 1 (Setup) → T001-T002
2. Phase 2 (Foundational) → T003
3. Phase 3 (US1) → T004 (RED) → T005-T007 (GREEN)
4. Phase 4 (US2) → T008-T009
5. Phase 5 (Polish) → T010-T016
6. Open PR titled `feat(scoring): SD-024 circuit breaker observability — opened_at, last_failure_time, metric rename`

### Alternative: split US1 / US2

If reviewer load preference demands it, US2 (metric rename) is self-contained and trivially splittable into a separate PR. US1 cannot be cleanly split — the listener changes (T005), the `MLCircuitBreaker.listener` exposure (T006), and the `management_api` refactor (T007) form one indivisible operational improvement.

### Why ship together

- Same files, same review pass
- One PR closes SD-024 — clean audit trail
- The metric rename's "Metric rename" section in the PR description doubles as the dashboard-owner ping (FR-006)

---

## Notes

- This feature touches NO Avro schemas, NO Kafka topics, NO external HTTP routes (the management API response shape is unchanged externally). The only externally-visible breaking change is the Prometheus metric rename.
- The commit message and PR description MUST explicitly reference SD-024 (FR-007) so the work can be retroactively linked if a GitHub issue is filed.
- The 14-day post-merge incident watch (SC-006) is the operator's concern, not a task in this list. If a dashboard misfires due to the metric rename, the rollback path is straightforward (revert the rename portion only).
- T004's RED phase is non-negotiable: skipping it removes the TDD-quality signal and risks landing tests that silently pass against unrelated state.
- The `getattr(cb, "_..."...)` grep in T011 is the load-bearing root-cause check for SD-024; do not weaken it during review.
