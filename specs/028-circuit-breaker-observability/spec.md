# Feature Specification: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Feature Branch**: `028-circuit-breaker-observability`
**Created**: 2026-05-27
**Status**: Draft
**Origin**: SD-024. Re-baselined from the May-13 draft at `specs/012-circuit-breaker-observability-sd024/spec.md` — the "stop reading pybreaker private attrs" concern has already been addressed by intervening changes (the current listener at `pipelines/scoring/circuit_breaker.py:39-49` only touches public APIs). Two operational gaps remain: missing transition timestamps on the listener, and a counter name that misleads operators.

## Context

When the ML circuit breaker opens, SREs need two things they cannot currently get from the system:

1. **When did it open?** The Prometheus gauge `ml_circuit_breaker_state{state="open"}` flips to 1, but nothing records the moment it flipped. Without that timestamp, an on-call cannot correlate the trip with a deployment, a Kafka rebalance, or any other upstream event in their incident timeline.

2. **Why is this counter called *fallback decisions*?** The metric `ml_fallback_decisions_total` increments inside `before_call()` whenever a call lands on an open circuit. It counts **calls that hit an open circuit**, not **scoring decisions made in fallback mode**. A decision that successfully used the ML model and returned `(score, False)` is not a fallback decision; a call that bypassed the model because the breaker was open is. The name describes the consequence, not the cause — operators reading the counter get the wrong mental model, and any alert threshold tuned against it is tuned against the wrong semantic.

This spec addresses both gaps with surgical changes: add two timestamp fields to the listener, rename one Prometheus counter, and update every call site. No new dependencies, no architectural change, no schema migration.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — On-call SRE correlates circuit-open with upstream events (Priority: P1) 🎯 MVP

An SRE is paged because `ml_circuit_breaker_state{state="open"}` has been 1 for over 5 minutes. They open the incident dashboard and need to answer: "When did this circuit open, and when was the most recent failure that contributed to opening it?" These two timestamps are the anchor points for every subsequent correlation step — matching against deployment events, Kafka consumer rebalances, model-server logs, etc. Today both are unavailable from outside the breaker.

**Why this priority**: This is the primary operational hazard called out in SD-024. Without `opened_at`, MTTR measurement on circuit-open incidents is impossible from the listener-visible state. Every other improvement in this feature is downstream of being able to answer "when".

**Independent Test**: A unit test can drive the listener through a CLOSED → failures → OPEN transition and assert that `listener.opened_at` is non-`None` and equals the transition time, and that `listener.last_failure_time` is non-`None` and equals the time of the last failure that contributed to opening. The same test asserts that the listener exposes both values as public attributes (no leading underscore).

**Acceptance Scenarios**:

1. **Given** the listener is freshly constructed with no failures observed, **When** `listener.opened_at` and `listener.last_failure_time` are read, **Then** both return `None` (not zero, not epoch, not the current time).
2. **Given** the circuit is CLOSED and starts receiving failures, **When** each `failure` callback fires, **Then** `listener.last_failure_time` advances to the time of that failure regardless of whether the circuit opens.
3. **Given** the circuit is CLOSED, **When** failures cause a transition to OPEN, **Then** `listener.opened_at` is set to the transition timestamp at the moment of the state change.
4. **Given** the circuit transitions OPEN → HALF-OPEN → CLOSED → OPEN over time, **When** the second OPEN transition occurs, **Then** `listener.opened_at` is overwritten with the newer transition timestamp (it records "when did the circuit *most recently* open", not "when did it first ever open").
5. **Given** the circuit is OPEN, **When** the listener receives an OPEN→OPEN re-notification (defensive — should not happen but the listener must tolerate it), **Then** `listener.opened_at` is preserved (not advanced) so an idempotent callback does not reset the SRE's reference point.

---

### User Story 2 — Operator can read the metric name and understand what it counts (Priority: P1)

The Prometheus counter `ml_fallback_decisions_total` is defined in `pipelines/scoring/circuit_breaker.py:26` and incremented inside `before_call()` whenever the circuit is already OPEN. Its description string in the metrics registry says "Total ML scoring decisions made in fallback mode (circuit open)" — that's two different things welded together. An operator querying the counter expects to see decisions; what they actually see is calls-to-open-circuit.

Rename to `ml_circuit_open_calls_total` and update every Python reference + tests. Document the rename in the PR so dashboard/alert owners can sync (no dashboard JSON lives in this repo — that update is operator responsibility).

**Why this priority**: Wrong metric name → wrong alert thresholds → wrong postmortem timelines. The fix is cheap (rename + propagate); the cost of leaving it is silent miscalibration.

**Independent Test**: `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` returns zero matches. The counter `ml_circuit_open_calls_total` increments by exactly 1 when a call arrives at an OPEN circuit (existing test can be adapted to use the new name).

**Acceptance Scenarios**:

1. **Given** the codebase before this change, **When** `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` is run, **Then** all matches are in the change set being removed.
2. **Given** the codebase after this change, **When** the same grep is run, **Then** zero matches are found.
3. **Given** a circuit that is OPEN, **When** a call is attempted (which `before_call` intercepts), **Then** `ml_circuit_open_calls_total` increments by 1.
4. **Given** a circuit that is CLOSED or HALF-OPEN, **When** any call is attempted, **Then** `ml_circuit_open_calls_total` does **not** increment.
5. **Given** the renamed counter, **When** the PR is opened, **Then** the PR description explicitly notes the rename and the prior name so dashboard/alert owners can grep their out-of-tree configs.

---

### Edge Cases

- **`opened_at` after CLOSE**: spec is explicit that `opened_at` records the *most recent* open transition. After OPEN → CLOSED the field is **NOT** reset to `None`; it remains as the historical anchor of the last open. Only a fresh OPEN transition advances it. This intentionally differs from the May-13 draft (which said "preserved" on CLOSE without specifying whether subsequent OPENs overwrite) — clarity matters here.
- **Clock source**: timestamps use the same wall-clock time source as the existing `logger.info(...)` calls in the listener. No new clock-source decision; deliberately leaves room for future migration to a single project-wide time provider if observability work demands it.
- **Public attribute names**: `opened_at` and `last_failure_time` are the canonical names. They must be accessible on the listener instance without the underscore prefix; tests and any future Prometheus exporter (out of scope here) read them directly.
- **Concurrent state transitions**: pybreaker's callbacks are serialized by its internal lock. This feature inherits that guarantee — no additional synchronization required on the listener fields.
- **Renamed metric on a running production system**: there is a brief window where the old counter stops emitting before any operator dashboard is updated. This is unavoidable and accepted; mitigation is the explicit PR-description callout (US2 acceptance scenario 5).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `FraudCircuitBreakerListener` MUST expose a public attribute `opened_at: datetime | None`. Initial value is `None`. It transitions to the wall-clock time of the OPEN transition each time the circuit becomes OPEN. CLOSED and HALF-OPEN transitions do not modify it.
- **FR-002**: `FraudCircuitBreakerListener` MUST expose a public attribute `last_failure_time: datetime | None`. Initial value is `None`. It transitions to the wall-clock time of every failure callback the listener receives, regardless of the resulting state.
- **FR-003**: Both `opened_at` and `last_failure_time` MUST be set inside the corresponding pybreaker public callback (`state_change` for the former, `failure` for the latter). No pybreaker private attribute or internal state lookup may be introduced.
- **FR-004**: The Prometheus counter currently named `ml_fallback_decisions_total` MUST be renamed to `ml_circuit_open_calls_total`. The counter's description string MUST be updated to "Total calls that arrived while the ML circuit was OPEN".
- **FR-005**: Every Python reference to `ml_fallback_decisions_total` (imports, increment sites, `__all__` entries, test assertions) MUST be updated to the new name.
- **FR-006**: The PR that lands this change MUST describe the metric rename in human-readable form, listing both the old and new names, so dashboard / alert owners can synchronise their out-of-tree configurations.
- **FR-007**: Closing GitHub issue (if one exists for SD-024) MUST be linked from the PR via a `Closes #N` trailer. If no issue exists, the PR description MUST reference SD-024 explicitly so it can be retroactively linked.

### Key Entities *(include if feature involves data)*

Not applicable — this feature changes observable surface (Python attributes, one Prometheus metric name), not data entities. No schema changes, no event-shape changes, no persisted state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An on-call SRE handed only the listener instance (e.g. in a Python REPL attached to a running process) can answer "when did the circuit open?" and "when was the last failure?" in under 30 seconds, without consulting source code or pybreaker internals.
- **SC-002**: After the change, `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` returns **zero** matches.
- **SC-003**: After the change, a unit test that drives a CLOSED → OPEN transition asserts `listener.opened_at is not None` and `listener.last_failure_time is not None`, and the test passes.
- **SC-004**: After the change, a unit test asserts that `ml_circuit_open_calls_total` increments by exactly 1 per call arriving at an OPEN circuit (test exists today against the old name and is updated as part of this change).
- **SC-005**: The full CI pipeline on the merge commit shows the same set of green stages as the pre-feature `main` baseline. No new failures, no regressed coverage.
- **SC-006**: No production incident attributable to the metric rename is filed in the first 14 days after merge. (Soft signal — primary mitigation is the PR description's rename callout per FR-006.)

## Assumptions

- pybreaker's public callback API exposes a `state_change(old_state, new_state)` hook and a `failure` hook with enough information to derive both timestamps. Phase 0 planning will verify the exact callback signatures available in the installed pybreaker version (currently `1.4.1` per `pyproject.toml`).
- The listener at `pipelines/scoring/circuit_breaker.py:36-49` is the load-bearing producer of these attributes. **Phase 0 research revealed** that `pipelines/scoring/management_api.py:542,553` *also* synthesizes these timestamps today by reading pybreaker private attributes (`_last_failure_time`, `_opened_at`) — the exact fragility SD-024 was framed around, just in a different module than the May-13 draft assumed. This feature therefore extends to refactoring `management_api.py` to consume the listener's new public attributes instead of pybreaker internals. Exposing the values via a future Prometheus exporter (gauge) remains out of scope.
- No Grafana dashboard JSON lives in-tree; dashboard alignment with the new metric name is an operator task and is documented in the PR description, not automated by this feature.
- The brief observability gap during the metric rename (between old counter going silent and new counter being recognized by dashboards) is acceptable and inherent to renames. Aliasing both names temporarily is **explicitly out of scope** — it would double the maintenance surface for a transient benefit.
- The May-13 draft at `specs/012-circuit-breaker-observability-sd024/spec.md` is treated as historical input, not as the spec to be implemented. Its claims about pybreaker-private-attribute access do not match today's code at `pipelines/scoring/circuit_breaker.py` and are not reproduced here.
