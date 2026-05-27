# Implementation Plan: Document & Contain `apache-flink` ↔ `analytics` pyarrow Conflict (#31)

**Branch**: `027-flink-pyarrow-conflict` | **Date**: 2026-05-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/027-flink-pyarrow-conflict/spec.md`

## Summary

Ship a documentation-only change that makes the per-extras install topology explicit, records the apache-flink ↔ pyarrow constraint conflict as an architectural decision (ADR-015), provides a co-located rationale for the pyarrow exposure on the scoring/processing image, and clarifies the current authority status of `uv.lock`. Zero source-code changes; zero CI-config changes (deliberately — see Phase 0 Research note about CI's current pip-audit scope).

## Technical Context

**Language/Version**: N/A — documentation work
**Primary Dependencies**: None added. The feature *documents* dependency choices made in SD-026.
**Storage**: N/A
**Testing**: Manual readability check + existing CI must continue to pass (no behavior change → trivially preserved)
**Target Platform**: Repository documentation surface (Markdown, GitHub-rendered)
**Project Type**: documentation / governance
**Performance Goals**: SC-001 / SC-002 from spec — "5-minute comprehension" gates
**Constraints**: No new tools introduced; reuse the existing ADR pattern in `docs/adr/`; no impact on CI green/red status
**Scale/Scope**: ~5 files touched (1 new ADR, 1 README section, 1 new SECURITY.md, 1 header comment near `uv.lock`, 1 task list)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Stream-First | **PASS** | No runtime change |
| II. Sub-100ms Decision Budget | **PASS** | No runtime change |
| III. Schema Contract Enforcement | **PASS** | No schema change |
| IV. Channel Isolation | **PASS** | No producer/consumer change |
| V. Defense in Depth | **PASS** | Documentation *strengthens* audit-signal hygiene (US2 deliverable) — directly supportive of this principle |
| VI. Immutable Event Log | **PASS** | No event-log change |
| VII. PII Minimization | **PASS** | No PII surface change |
| VIII. Observability | **PASS** | No telemetry change |
| IX. Analytics-First Persistence | **PASS** | The deployment topology being *documented* is what already exists on `main`; this feature codifies an existing reality, doesn't change it |
| X. Analytics Consumer Layer | **PASS** | No consumer change |
| XI. Feature Serving Contract | **PASS** | No feature store change |
| XII. Component Lifecycle | **PASS** | No lifecycle change |

**Constitution Check: PASS.** Documentation features are non-architectural by definition; the constitutional principles are validated by virtue of *no code change*. The only principle this feature even brushes is V (Defense in Depth), and it strengthens rather than weakens that posture.

*Post-design re-check*: Unchanged after Phase 1. No design artifact introduces a runtime concern.

## Project Structure

### Documentation (this feature)

```text
specs/027-flink-pyarrow-conflict/
├── plan.md              # This file
├── research.md          # Phase 0 — verified scanner scope, doc surface inventory
├── data-model.md        # Phase 1 — N/A note
├── quickstart.md        # Phase 1 — author + reviewer walk-through
├── contracts/           # Phase 1 — N/A note (no external interfaces)
└── checklists/
    └── requirements.md  # Spec quality checklist (already all-pass)
```

### Source artifacts (affected files only)

```text
docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md   # NEW — records the load-bearing
                                                  #       decision that {analytics}
                                                  #       and {processing, scoring}
                                                  #       are mutually exclusive
                                                  #       in a single venv

README.md                                         # MODIFIED — add a short
                                                  #            "Installing for local
                                                  #            development" section
                                                  #            with per-task `uv pip
                                                  #            install` recipes;
                                                  #            link to ADR-015

SECURITY.md                                       # NEW — co-located exception
                                                  #       rationale for
                                                  #       PYSEC-2026-113 on the
                                                  #       scoring/processing image;
                                                  #       includes a dated revisit
                                                  #       signal linked to upstream
                                                  #       apache-flink tracking

uv.lock                                           # MODIFIED — single-line header
                                                  #            comment marking the
                                                  #            lock as ADVISORY,
                                                  #            with link to ADR-015
                                                  #            for the why
```

**Structure Decision**: Reuse the existing `docs/adr/` numbering (next is **ADR-015** per `ls docs/adr/`). The repo already has 14 ADRs and a clear style — adding another fits the pattern. No new directory or top-level structure introduced. `CONTRIBUTING.md` is **not** being created (the spec allowed it as an option but the README already serves as onboarding entry point per `ls -la`); a section inside `README.md` is the lower-friction surface.

## Complexity Tracking

No constitution violations — section intentionally empty.
