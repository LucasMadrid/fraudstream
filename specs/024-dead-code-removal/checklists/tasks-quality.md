# Tasks Quality Checklist: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Purpose**: Validate that tasks.md is specific, actionable, and complete enough to execute without additional context — testing the quality of task requirements, not whether the implementation is correct
**Created**: 2026-05-16
**Feature**: [spec.md](../spec.md) | [tasks.md](../tasks.md)
**Audience**: Implementer (self-review before starting T001)

---

## Task Completeness

- [x] CHK001 Does tasks.md cover every symbol named in FR-002 (`_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, `_FEATURE_ZERO_DEFAULTS`) as a distinct, named task? [Completeness, Spec §FR-002]
- [x] CHK002 Does tasks.md explicitly name `tests/integration/test_feature_serving.py` as a deletion target — or does the implementer have to re-derive this from research.md? [Completeness, Gap — tasks.md T009]
- [x] CHK003 Are both required operations on `tests/unit/scoring/test_job_extension_safety.py` — the addition (T006) and the deletion (T007–T008) — represented as separate, sequenced tasks in tasks.md? [Completeness, tasks.md §Phase 3 / Phase 4]
- [x] CHK004 Is the removal of the `feature_store_fallback_total` *import* and *call site* in `job_extension.py` represented as a distinct task (not bundled silently into a broader deletion)? [Completeness, tasks.md T005]
- [x] CHK005 Is there a task requiring the implementer to confirm `metrics.py` is unchanged after all edits — or is FR-005 only represented as a passive note? [Completeness, tasks.md T013 — T013 is a git diff task]

---

## Task Clarity

- [x] CHK006 Are the line numbers cited in T002–T005 documented as point-in-time references (from the research scan) that should be re-verified via T001 before editing — or could an implementer treat them as guaranteed-current? [Clarity, tasks.md T001 — fixed: T001 now states line numbers in T002–T005 are point-in-time references and T001's output takes precedence if they have shifted]
- [x] CHK007 Is T006's instruction to "see quickstart.md for the test template" specific enough — does tasks.md indicate which class name, which method names, and which assertion values to use, or does the implementer need to context-switch to quickstart.md mid-task? [Clarity, tasks.md T006 — class name is in T006; quickstart.md is the canonical template reference; context-switch is by design]
- [x] CHK008 Is "Delete `_FeatureEnrichmentFunction` class (lines 33–109)" clear about whether the docstring, any blank lines above the class, and any trailing blank line after the class are included in the deletion scope? [Clarity, tasks.md T003 — line numbers define the boundary; blank line handling is implementation judgment and does not affect correctness]
- [x] CHK009 Does T009's instruction (`git rm tests/integration/test_feature_serving.py`) specify that this should be staged as a deletion rather than manually deleted — so git history preserves the file's ancestry? [Clarity, tasks.md T009 — `git rm` is explicitly specified]

---

## Task Consistency

- [x] CHK010 Do T002–T005 (all modifying `job_extension.py`) explicitly state that they must be applied sequentially and committed as a unit — not as four separate partial commits that would leave the file in an intermediate broken-import state? [Consistency, tasks.md §Phase 3 — Notes section and Dependencies section both state this]
- [x] CHK011 Are the task IDs in the "Single-Developer Sequence" section consistent with the task IDs in the phase sections — i.e., does the sequence `T001 → T002 → ... → T013` match the actual tasks defined in each phase? [Consistency, tasks.md §Implementation Strategy — all 13 tasks match]
- [x] CHK012 Does tasks.md note that T010–T011 (grep checks) are only meaningful if run against the working tree after T005 is complete and before any other changes — or could an implementer run them at the wrong point and get a false pass? [Consistency, tasks.md §Phase 5 — Phase 5 depends on all Phase 3 and Phase 4 tasks completing first]

---

## Task Measurability (Checkpoint Quality)

- [x] CHK013 Does the Phase 3 checkpoint include both the grep command (SC-001) and the pytest invocation (SC-002) needed to independently verify US1 is complete — or does the implementer need to cross-reference quickstart.md? [Measurability, tasks.md §Phase 3 checkpoint — both commands present in the checkpoint]
- [x] CHK014 Does the Phase 4 checkpoint specify how to determine that no import errors were introduced (SC-004) — e.g., a full `python -c "import pipelines.scoring.job_extension"` or relying solely on pytest? [Measurability, tasks.md §Phase 4 checkpoint — pytest catches import errors; sufficient]
- [x] CHK015 Is the "coverage does not drop below pre-change baseline" condition in US2's independent test criteria (from the spec) reflected as a measurable task in tasks.md — or is it omitted? [Measurability, Gap — pytest passing is the practical proxy; spec now defines baseline; no separate coverage task needed for a deletion]

---

## Task Risk & Edge Cases

- [x] CHK016 Does tasks.md warn against running the full test suite between T002–T004 (partial deletions) — since the test file still imports the symbols being deleted and would produce ImportErrors mid-deletion? [Coverage, Edge Case — Notes section explicitly warns against this]
- [x] CHK017 Is there a task or note covering what to do if T001's grep output does not match the expected line numbers in research.md — i.e., is there a defined recovery path if line numbers have shifted? [Coverage, Exception Flow — fixed: T001 now includes recovery guidance]
- [x] CHK018 Are requirements defined for what happens if `tests/integration/test_feature_serving.py` has been modified on another branch before this branch merges — is a merge conflict on that file considered in-scope? [Coverage, Gap — out of scope for tasks.md; handled by standard git merge conflict resolution]

---

## Notes

- CHK006 is the highest-risk item: stale line numbers treated as current could cause incorrect deletions
- CHK010 is critical for git hygiene: T002–T005 together constitute one logical change and should not be interleaved with unrelated commits
- CHK007 and CHK013 surface the same underlying gap as CHK013 in `implementation.md` — SC-002's test specification lives outside the spec, creating a context-switch burden for the implementer
- Check items off as completed: `[x]`
