# Research: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Branch**: `024-dead-code-removal` | **Date**: 2026-05-16

## Summary

All research questions are resolved. This is a purely subtractive change with no ambiguity — the symbols to delete and the files to touch are fully identified by grep.

---

## Decision: Deletion Scope

**Decision**: Three files are affected. One gets symbols deleted; two entire test files are deleted.

| File | Action | Reason |
|------|--------|--------|
| `pipelines/scoring/job_extension.py` | Delete symbols + wiring | Contains `_FEATURE_ZERO_DEFAULTS`, `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and `enriched_stream.map(...)` call at line 138 |
| `tests/unit/scoring/test_job_extension_safety.py` | Delete `TestFeatureEnrichmentFallback` class (lines 91–135) and its imports (lines 11–12) | Tests the deleted symbols |
| `tests/integration/test_feature_serving.py` | Delete entire file | Entire file imports and tests `_FeatureEnrichmentFunction` from job_extension — has no remaining purpose |

**Rationale**: Grep confirms these are the only references in the codebase. `tests/integration/test_feature_serving.py` was not named in the original spec description but is fully within FR-004 scope ("No other test, import, or module-level symbol MAY reference the deleted names").

**Alternatives considered**: Replacing `_FlinkFeatureEnrichmentFunction` with a passthrough — rejected because `enriched_stream` already flows to `eval_stream` at line 185 via a direct `.map(_evaluate, ...)`. The intermediate `.map()` at line 138 is pure overhead. Deletion is the correct action per ADR-005.

---

## Decision: metrics.py

**Decision**: `metrics.py` is NOT touched.

**Rationale**: `feature_store_fallback_total` is defined in `metrics.py` for future use by the standalone scoring consumer. The spec (FR-005, SC-001, Assumptions) explicitly preserves this. Only the call site in `job_extension.py` (lines 121 and 128) is removed.

---

## Decision: Import chain after deletion

**Decision**: After removing `_FlinkFeatureEnrichmentFunction` and the `.map()` call at line 138, `enriched_stream` at line 185 still holds the correct `EnrichedTransaction` stream. No rewiring is required.

**Rationale**: `enriched_stream` is assigned at line 87 from the upstream enrichment operator. The Feast `.map()` at line 138 reassigns the same variable (`enriched_stream = enriched_stream.map(...)`). Deleting that reassignment means `enriched_stream` at line 185 resolves to the original enriched output — which is exactly what ADR-005 requires.

---

## Decision: Test additions

**Decision**: A new test must be added to verify SC-002: that non-zero enriched features reach the rule evaluator with those same values regardless of Feast availability.

**Rationale**: The existing `TestFeatureEnrichmentFallback` class tested the (now-deleted) fallback behavior. After deletion, the test suite needs a replacement test that validates the forward invariant: `wire_rule_evaluator` passes `EnrichedTransaction` dict directly to `_evaluate` without mutation.

**Placement**: `tests/unit/scoring/test_job_extension_safety.py` — in place of the deleted `TestFeatureEnrichmentFallback`. No new file needed.

---

## Symbol inventory (grep-confirmed)

### `job_extension.py` — symbols to delete

| Lines | Symbol | Type |
|-------|--------|------|
| 12–30 | `_FEATURE_ZERO_DEFAULTS` | module-level dict |
| 33–109 | `_FeatureEnrichmentFunction` | class |
| 112–138 | `_FlinkFeatureEnrichmentFunction` | inner class + `.map()` reassignment |
| 121 | `from pipelines.scoring.metrics import feature_store_fallback_total` | import |
| 128 | `feature_store_fallback_total.labels(...).inc()` | call site |

### `test_job_extension_safety.py` — symbols to delete

| Lines | Symbol | Type |
|-------|--------|------|
| 11–12 | `import _FEATURE_ZERO_DEFAULTS, _FeatureEnrichmentFunction` | import |
| 91–135 | `TestFeatureEnrichmentFallback` | test class |

### `test_feature_serving.py` — entire file deleted

The entire file imports and tests `_FeatureEnrichmentFunction` with no surviving purpose.

### `metrics.py` — NOT touched

| Lines | Symbol | Disposition |
|-------|--------|-------------|
| 9–10 | `feature_store_fallback_total` | PRESERVED — future standalone consumer |
