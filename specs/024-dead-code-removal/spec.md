# Feature Specification: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Feature Branch**: `024-dead-code-removal`
**Created**: 2026-05-16
**Status**: Draft
**Input**: User description: "Remove dead feature-enrichment code per ADR-005: delete _FeatureEnrichmentFunction, _FlinkFeatureEnrichmentFunction, _FEATURE_ZERO_DEFAULTS from job_extension.py and TestFeatureEnrichmentFallback from tests"

## Context

ADR-005 decided that the co-located scoring path MUST read features from the `EnrichedTransaction` dict and MUST NOT call the online feature store. That decision has not been implemented: `_FlinkFeatureEnrichmentFunction` is still wired into `wire_rule_evaluator` (`job_extension.py:138`) and fires on every transaction in production.

This is an active correctness bug, not cleanup. When Feast times out, `_FEATURE_ZERO_DEFAULTS` overwrites the live feature values already present in the `EnrichedTransaction` — setting `vel_count_5m`, `device_known_fraud`, and all other velocity/geo/device fields to zero. Rules like `BURST_COUNT_5M` then silently miss real fraud signals even though the correct values were already in the record. The zero-value fallback exists to handle a case (absent features) that cannot occur on this code path.

## User Scenarios & Testing

### User Story 1 — Live features reach the rule evaluator unmodified (Priority: P1)

A transaction with known non-zero velocity and device features must produce a fraud evaluation that reflects those values — not the zero-value fallback injected by a timed-out Feast call.

**Why this priority**: This is the correctness regression the current code introduces. `BURST_COUNT_5M` and similar rules that gate on velocity thresholds will silently miss fraud if `vel_count_5m` is zeroed by a Feast timeout. Fixing this is the reason the change exists.

**Independent Test**: Submit a transaction carrying `vel_count_5m=10`, `device_known_fraud=True` through `wire_rule_evaluator`. Verify the `_evaluate` call receives those exact values — not `vel_count_5m=0`, `device_known_fraud=False`.

**Acceptance Scenarios**:

1. **Given** an `EnrichedTransaction` with `vel_count_5m=10` and `device_known_fraud=True`, **When** it passes through `wire_rule_evaluator`, **Then** `_evaluate` receives `vel_count_5m=10` and `device_known_fraud=True` with no intermediate Feast lookup.
2. **Given** the Feast service is unavailable, **When** a transaction is processed, **Then** the rule evaluator still receives the enriched feature values and no zero-value substitution occurs.

---

### User Story 2 — Scoring path reflects architectural decision (Priority: P2)

An engineer reading `job_extension.py` should see only code that executes at runtime. No Feast call, no zero-value fallback that can corrupt live features, no classes contradicting ADR-005.

**Why this priority**: Secondary to the correctness fix but prevents regression — a future engineer must not "fix" the missing Feast call by re-adding it. The deletion is the implementation of ADR-005.

**Independent Test**: `grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py` returns no matches.

**Acceptance Scenarios**:

1. **Given** `job_extension.py` exists, **When** the Feast wiring is removed, **Then** `wire_rule_evaluator` maps enriched transactions directly to `_evaluate` with no intervening `.map(_FlinkFeatureEnrichmentFunction(), ...)` step.
2. **Given** `TestFeatureEnrichmentFallback` is deleted, **When** the test suite runs, **Then** all remaining tests pass and coverage does not drop below the pre-change baseline (baseline = `pytest --cov=pipelines/scoring tests/` result on the main branch immediately before this branch begins).

---

### Edge Cases

- `_FEATURE_ZERO_DEFAULTS` is imported by `TestFeatureEnrichmentFallback` — both must be removed together or the test file will have a broken import.
- `feature_store_fallback_total` is defined in `metrics.py` and must stay there — it is reserved for the future standalone scoring consumer. Only the call site in `job_extension.py` is removed; `metrics.py` is not touched.

## Requirements

### Functional Requirements

- **FR-001**: `wire_rule_evaluator` MUST pass the `EnrichedTransaction` dict directly to the rule evaluator without any intermediate Feast lookup or zero-value substitution.
- **FR-002**: `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and `_FEATURE_ZERO_DEFAULTS` MUST be deleted from `pipelines/scoring/job_extension.py`. The `from pipelines.scoring.metrics import feature_store_fallback_total` import and its call site (`feature_store_fallback_total.labels(result="timeout").inc()`) MUST also be removed from `job_extension.py`.
- **FR-003**: `TestFeatureEnrichmentFallback` and its imports of `_FEATURE_ZERO_DEFAULTS` and `_FeatureEnrichmentFunction` MUST be deleted from `tests/unit/scoring/test_job_extension_safety.py`. A replacement test class `TestLiveFeaturesReachEvaluator` MUST be added to the same file to verify SC-002.
- **FR-004**: No other test, import, or module-level symbol in the project MAY reference the deleted names. The complete set of files in scope for deletion or modification is: `pipelines/scoring/job_extension.py` (symbols deleted), `tests/unit/scoring/test_job_extension_safety.py` (class and imports deleted; SC-002 test added), and `tests/integration/test_feature_serving.py` (entire file deleted — it tests only the deleted symbols). No other files are touched.
- **FR-005**: `metrics.py` MUST NOT be modified — the `feature_store_fallback_total` counter definition remains for future standalone consumers.

## Success Criteria

### Measurable Outcomes

- **SC-001**: `pipelines/scoring/job_extension.py` contains no reference to `FeatureEnrichmentFunction` or `_FEATURE_ZERO_DEFAULTS`. (`feature_store_fallback_total` is deliberately preserved in `metrics.py` and must not be removed.)
- **SC-002**: A transaction with non-zero enriched features reaches the rule evaluator with those same feature values — no zero-value substitution occurs regardless of Feast availability.
- **SC-003**: All CI tests pass after the deletion.
- **SC-004**: No import error is introduced in any remaining test or module file.

## Assumptions

- `metrics.py` retains the `feature_store_fallback_total` counter definition for use by the future standalone scoring consumer — only the call site in `job_extension.py` is in scope for deletion.
- The `EnrichedTransaction` dict always carries populated feature values by the time it reaches `wire_rule_evaluator` — this is the guarantee provided by the upstream enrichment operators and is the premise of ADR-005.
- No branch or PR in-flight re-introduces these classes before this spec ships.
- The removal is purely subtractive: no new logic is added to replace the deleted code. The live path already carries the correct features; the Feast call only adds risk.
