# Implementation Plan: Restore CI Green Build

**Branch**: `023-fix-ci-green` | **Date**: 2026-05-10 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/023-fix-ci-green/spec.md`

## Summary

Three CI stages are failing on every push: (1) ruff reports 13 violations (9×F821 undefined names, 3×F401 unused imports, 1×I001 import ordering, 1×E402 misplaced import) blocking stage 1; (2) DLQ unit tests reference a defunct class `DLQKafkaProducer` — the class was renamed to `ProcessingDLQSink` but tests were not updated; (3) the import violations in `tests/fixtures/tls/cert_generator.py` were previously triggering GitGuardian-adjacent scan noise.

The fix touches only 3 test/fixture files. No production code changes required. All violations are mechanical: rename the broken test class references, rewrite the test bodies to match the new `ProcessingDLQSink` API (`send()` / `flush()`), and move two misplaced imports.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: pytest 8.x, ruff 0.4.x, confluent-kafka, cryptography  
**Storage**: N/A (no storage changes; tests use tmp_path for ephemeral TLS material)  
**Testing**: pytest + ruff (check + format); coverage gate ≥ 20% (CI requirement)  
**Target Platform**: GitHub Actions Ubuntu runner (linux/amd64)  
**Project Type**: Test infrastructure fix (no library or service changes)  
**Performance Goals**: N/A (test suite must complete within CI timeout — existing baseline)  
**Constraints**: No production code changes; no new runtime dependencies; ruff exits 0; 0% flakiness over 10 runs  
**Scale/Scope**: 3 files, 13 ruff violations, 5 test classes requiring rewrite

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applicable? | Status | Notes |
|-----------|-------------|--------|-------|
| I. Stream-First | No | ✅ PASS | No production code changes; no Kafka producer changes |
| II. Sub-100ms Decision Budget | No | ✅ PASS | No hot-path code touched |
| III. Schema Contract Enforcement | No | ✅ PASS | No schema changes |
| IV. Channel Isolation | No | ✅ PASS | No topology changes |
| V. Defense in Depth — Rules Before Models | No | ✅ PASS | No rule or model changes |
| VI. Immutable Event Log | No | ✅ PASS | No Iceberg writes |
| VII. PII Minimization | No | ✅ PASS | No PII-handling code touched |
| VIII. Observability | No | ✅ PASS | No metrics/tracing changes |
| IX. Analytics-First Persistence | No | ✅ PASS | No analytics sink changes |
| X. Analytics Consumer Layer | No | ✅ PASS | No consumer changes |
| XI. Feature Serving Contract | No | ✅ PASS | No feature store changes |
| XII. Component Lifecycle | Partial | ✅ PASS | `ProcessingDLQSink` creates its Kafka producer eagerly in `__init__` (not lazy); tests must not assert `_producer is None` after construction. This is pre-existing behaviour in production code — not introduced by this fix. |

**Constitution Gate**: ✅ All applicable principles pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/023-fix-ci-green/
├── plan.md              # This file
├── research.md          # Phase 0 output — root cause analysis and fix decisions
├── data-model.md        # Phase 1 output — key entities
├── quickstart.md        # Phase 1 output — local verification steps
├── checklists/
│   └── requirements.md  # Spec quality checklist (all passed)
└── tasks.md             # Phase 2 output — actionable task list
```

### Source Code (files changed by this feature)

```text
tests/
├── unit/
│   └── processing/
│       └── test_dlq_sink.py      # REWRITE: 5 test classes, 3 use defunct DLQKafkaProducer
└── fixtures/
    └── tls/
        ├── cert_generator.py     # FIX: move import ipaddress to top (E402), remove TYPE_CHECKING duplicates (F401×3)
        └── __init__.py           # No changes needed
tests/integration/
└── conftest_security.py          # FIX: re-order import block (I001)
```

**Structure Decision**: Single-project layout. This is a pure test-infrastructure fix — no source tree changes. The three affected files are all under `tests/`.

## Complexity Tracking

> No constitution violations. Section not applicable.
