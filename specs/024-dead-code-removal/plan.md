# Implementation Plan: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Branch**: `024-dead-code-removal` | **Date**: 2026-05-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/024-dead-code-removal/spec.md`

## Summary

Remove `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and `_FEATURE_ZERO_DEFAULTS` from `pipelines/scoring/job_extension.py`, and delete the test files that cover them. The Feast call is LIVE (not dead code) and fires on every transaction; when Feast times out it overwrites live enriched features with zero-value defaults, silently breaking fraud rules that gate on velocity/device signals. This is a purely subtractive change: delete the three symbols, their import, the `.map()` wiring at line 138, and the test coverage that validated that (now-incorrect) behavior. Add one new test verifying SC-002 (non-zero features reach the rule evaluator unchanged).

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: PyFlink 2.x (DataStream API), pytest  
**Storage**: N/A  
**Testing**: pytest  
**Target Platform**: Linux (PyFlink job / CI)  
**Project Type**: Streaming pipeline (PyFlink job)  
**Performance Goals**: N/A — subtractive change  
**Constraints**: Purely subtractive; no new logic introduced. `metrics.py` MUST NOT be modified.  
**Scale/Scope**: 3 files modified/deleted; ~130 lines removed; 1 new test added

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle XI (NON-NEGOTIABLE)**: Co-located scoring path MUST read features from `EnrichedTransaction` dict and MUST NOT call the online feature store. ADR-005 mandates this.

**Gate status — PASS**. This spec *implements* Principle XI. The Feast call currently in `job_extension.py` is the exact violation Principle XI prohibits. Removing it brings the codebase into compliance. No violations introduced; no Complexity Tracking needed.

**Post-design re-check — PASS**. The design is purely subtractive with one additional correctness test. No new dependencies, no new abstractions, no external calls.

## Project Structure

### Documentation (this feature)

```text
specs/024-dead-code-removal/
├── plan.md              # This file
├── research.md          # Phase 0 — deletion scope, symbol inventory
├── quickstart.md        # Phase 1 — step-by-step implementation guide
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit.tasks — not yet created)
```

### Source Code (affected files)

```text
pipelines/scoring/
└── job_extension.py          # DELETE: _FEATURE_ZERO_DEFAULTS (lines 12-30),
                              #         _FeatureEnrichmentFunction (lines 33-109),
                              #         _FlinkFeatureEnrichmentFunction (lines 112-138),
                              #         import feature_store_fallback_total (line 121),
                              #         fallback counter call (line 128),
                              #         enriched_stream.map() reassignment (line 138)

tests/unit/scoring/
└── test_job_extension_safety.py  # DELETE: TestFeatureEnrichmentFallback (lines 91-135),
                                  #         its imports (lines 11-12)
                                  # ADD: TestLiveFeaturesReachEvaluator (SC-002)

tests/integration/
└── test_feature_serving.py   # DELETE ENTIRE FILE — tests _FeatureEnrichmentFunction only
```

**Structure Decision**: Single project, flat source layout. No new files. The subtractive nature means the only structural addition is the replacement test class in the existing unit test file.

## Phase 0: Research

**Status**: Complete. See [research.md](research.md).

All unknowns resolved by grep scan:
- Deletion scope fully identified across 3 files
- `enriched_stream` at line 185 resolves correctly after the line 138 reassignment is removed
- `metrics.py` counter definition confirmed preserved; only job_extension.py call sites removed
- `tests/integration/test_feature_serving.py` confirmed as full-file deletion candidate (tests only the deleted symbol)

## Phase 1: Design & Contracts

**No data model changes** — purely subtractive; `EnrichedTransaction` dict schema is unchanged.

**No external interface contracts** — no API surfaces modified; internal PyFlink topology only.

### Implementation Steps

#### Step 1 — Delete `_FEATURE_ZERO_DEFAULTS` (job_extension.py lines 12–30)

Delete the module-level dict `_FEATURE_ZERO_DEFAULTS`. It is only referenced within `_FeatureEnrichmentFunction` (to be deleted next) and in `TestFeatureEnrichmentFallback` (to be deleted). No surviving callers.

#### Step 2 — Delete `_FeatureEnrichmentFunction` class (lines 33–109)

Delete the entire class. It wraps a Feast `get_online_features` call with `_FEATURE_ZERO_DEFAULTS` fallback on timeout. No callers remain after Step 3.

#### Step 3 — Delete `_FlinkFeatureEnrichmentFunction` and the `.map()` wiring (lines 112–138)

Delete the inner `_FlinkFeatureEnrichmentFunction` class and the `enriched_stream = enriched_stream.map(_FlinkFeatureEnrichmentFunction(), output_type=None)` reassignment. After deletion, `enriched_stream` at line 185 resolves to the correct upstream `EnrichedTransaction` stream produced by the upstream enrichment operators.

#### Step 4 — Remove `feature_store_fallback_total` import and call site

- Line 121: delete `from pipelines.scoring.metrics import feature_store_fallback_total`
- Line 128: delete `feature_store_fallback_total.labels(result="timeout").inc()`

`metrics.py` is NOT modified.

#### Step 5 — Delete `TestFeatureEnrichmentFallback` from test_job_extension_safety.py

Delete lines 91–135 (the `TestFeatureEnrichmentFallback` class) and lines 11–12 (its imports of `_FEATURE_ZERO_DEFAULTS` and `_FeatureEnrichmentFunction`).

#### Step 6 — Delete `tests/integration/test_feature_serving.py`

Delete the entire file. It exists solely to test `_FeatureEnrichmentFunction`.

#### Step 7 — Add SC-002 test: `TestLiveFeaturesReachEvaluator`

Add a new test class to `tests/unit/scoring/test_job_extension_safety.py` that:

1. Constructs an `EnrichedTransaction`-like dict with `vel_count_5m=10`, `device_known_fraud=True` and other non-zero feature values.
2. Calls `wire_rule_evaluator` (or patches `_evaluate` to capture its input).
3. Asserts the captured input matches the original enriched dict — no zero-value substitution occurred.
4. Asserts no Feast import or `FeatureServingClient` is invoked during the call.

This test directly validates SC-002 and provides regression protection against re-introduction of the Feast call.

### Verification

After all steps:

```bash
grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py
# → no output (SC-001)

pytest tests/unit/scoring/test_job_extension_safety.py -v
# → all pass including TestLiveFeaturesReachEvaluator (SC-002, SC-003)

pytest tests/ -v
# → all pass; no ImportError from deleted names (SC-003, SC-004)
```
