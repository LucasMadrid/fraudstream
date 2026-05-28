# Implementation Plan: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Branch**: `028-circuit-breaker-observability` | **Date**: 2026-05-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/028-circuit-breaker-observability/spec.md`

## Summary

Add `opened_at: datetime | None` and `last_failure_time: datetime | None` as public attributes on `FraudCircuitBreakerListener`, populated inside pybreaker's existing `state_change` and `failure` callbacks. Rename the Prometheus counter `ml_fallback_decisions_total` → `ml_circuit_open_calls_total` (and its description) and update every Python reference. Single file in `pipelines/scoring/circuit_breaker.py` and its dedicated test file. No new dependencies, no schema changes.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `pybreaker==1.4.1` (pinned in `pyproject.toml`), `prometheus-client>=0.20`
**Storage**: N/A — in-memory listener state only
**Testing**: `pytest` — existing `tests/unit/scoring/test_circuit_breaker.py` is the dedicated test surface; new test cases added there
**Target Platform**: Linux container (existing scoring service)
**Project Type**: Python library module (internal to the scoring pipeline)
**Performance Goals**: No latency regression. Both new attribute writes are O(1) attribute assignments inside the existing pybreaker callbacks; no observable overhead on the score path (which is gated by Constitution Principle II: <100ms p99).
**Constraints**: `MLCircuitBreaker` gains one new public attribute (`listener: FraudCircuitBreakerListener`) so `management_api.py` can reach it; no change to the scoring service's external HTTP/Kafka surfaces.
**Scale/Scope**: 2 source files modified (`pipelines/scoring/circuit_breaker.py`, `pipelines/scoring/management_api.py`), 1 test file modified (`tests/unit/scoring/test_circuit_breaker.py`), 0 new files, 0 dependencies added/removed. The `management_api.py` change is in scope per Phase 0 finding R3.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Stream-First | **PASS** | No stream-processing change |
| II. Sub-100ms Decision Budget | **PASS** | Two attribute assignments inside callbacks that already run on state transitions / failure events — not on the hot path. Score-path cost is unchanged. |
| III. Schema Contract Enforcement | **PASS** | No Avro schema change |
| IV. Channel Isolation | **PASS** | No producer/consumer change |
| V. Defense in Depth | **PASS** | Improves observability of the ML fallback boundary, supportive of the principle |
| VI. Immutable Event Log | **PASS** | No event-log change |
| VII. PII Minimization | **PASS** | Timestamps and counter; no PII surface |
| VIII. Observability | **DIRECTLY SUPPORTIVE** | This entire feature exists to satisfy this principle — operators get correct metric semantics and recoverable transition timestamps |
| IX. Analytics-First Persistence | **PASS** | No persistence change |
| X. Analytics Consumer Layer | **PASS** | No consumer change |
| XI. Feature Serving Contract | **PASS** | No feature store change |
| XII. Component Lifecycle | **PASS** | Listener lifecycle unchanged |

**Constitution Check: PASS.** No violations; the feature is by-design supportive of Principle VIII (Observability as a First-Class Concern).

*Post-design re-check*: Unchanged after Phase 1. No design artifact introduces a runtime hot-path concern.

## Project Structure

### Documentation (this feature)

```text
specs/028-circuit-breaker-observability/
├── plan.md              # This file
├── research.md          # Phase 0 — pybreaker callback signatures, clock-source choice
├── data-model.md        # Phase 1 — listener state fields with semantics
├── quickstart.md        # Phase 1 — author + reviewer walk-through
├── contracts/           # Phase 1 — N/A note (internal Python module)
└── checklists/
    └── requirements.md  # Spec quality checklist (already all-pass)
```

### Source Code (affected files only)

```text
pipelines/scoring/
├── circuit_breaker.py            # MODIFIED
│                                 # - rename Counter ml_fallback_decisions_total → ml_circuit_open_calls_total
│                                 # - update Counter description string
│                                 # - add `opened_at` and `last_failure_time` to FraudCircuitBreakerListener
│                                 # - implement `failure(cb, exc)` callback (currently inherited as no-op)
│                                 # - extend `state_change` to set opened_at on OPEN transitions (guarded against
│                                 #   idempotent OPEN→OPEN re-notification per R5)
│                                 # - MLCircuitBreaker exposes the listener as a public `self.listener` attribute
│                                 # - update __all__ export
│
└── management_api.py             # MODIFIED (per Phase 0 R3)
                                  # - replace `getattr(cb, "_last_failure_time", None)` (line 542) with
                                  #   `listener.last_failure_time` via the new MLCircuitBreaker.listener path
                                  # - replace `getattr(cb, "_opened_at", None)` (line 553) with
                                  #   `listener.opened_at`
                                  # - keep the `next_probe_time` derivation (uses `reset_timeout`, a public attr)
                                  # - retain the existing try/except blocks defensively, but they should no
                                  #   longer catch private-attr-related AttributeError after the refactor

tests/unit/scoring/
└── test_circuit_breaker.py       # MODIFIED
                                  # - import rename
                                  # - add tests covering opened_at semantics (US1 acceptance scenarios 1-5)
                                  # - add tests covering last_failure_time
                                  # - add test asserting MLCircuitBreaker exposes .listener publicly
                                  # - update fallback-counter test to use new name (US2 acceptance scenarios 1-4)
```

**Structure Decision**: Surgical edits to one source file and its dedicated test file. No new modules, no new directories. Follows the established pattern (cf. SD-013, where producer changes lived in a single producer module + its tests).

## Complexity Tracking

No constitution violations — section intentionally empty.
