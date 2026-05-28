# Research: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Phase 0 output** | Branch: `028-circuit-breaker-observability` | Date: 2026-05-27

The spec carried two pending unknowns explicitly tagged for Phase 0:

1. The exact pybreaker callback signatures available in the installed version.
2. Which time source the listener should use.

While answering those, a third finding surfaced that materially expands the feature's scope.

---

## R1 — pybreaker callback API (signatures + behavior)

### Decision

Use the two stock pybreaker callbacks already exposed by `CircuitBreakerListener`:

- **`state_change(cb, old_state, new_state) -> None`** — fires on every state transition. Set `self.opened_at = datetime.now(timezone.utc)` when `new_state == "open"` (string comparison works because pybreaker's `CircuitBreakerState` defines `__eq__` against strings — confirmed below).
- **`failure(cb, exc) -> None`** — fires on every call failure regardless of whether the failure trips the circuit. Set `self.last_failure_time = datetime.now(timezone.utc)` on every invocation.

No custom callback registration; no monkey-patching; no pybreaker fork.

### Rationale

Verified via direct inspection of `pybreaker==1.4.1` (the pinned version in `pyproject.toml`):

```text
class CircuitBreakerListener:
    def before_call(self, cb, func, *args, **kwargs) -> None: ...
    def failure(self, cb: CircuitBreaker, exc: BaseException) -> None: ...
    def success(self, cb: CircuitBreaker) -> None: ...
    def state_change(self, cb, old_state, new_state) -> None: ...
```

These are documented public hooks; `CircuitBreakerListener` is an explicit extension point in pybreaker's design. Subclassing and overriding `failure` and `state_change` is the canonical pattern and survives pybreaker upgrades within its semver range.

The existing test suite at `tests/unit/scoring/test_circuit_breaker.py:80-82` already drives `listener.state_change(mock_cb, "closed", "open")` passing strings directly, confirming string-state semantics work end-to-end with the listener.

### Alternatives considered

- **Hook `before_call` instead of `state_change` for `opened_at`** — rejected. `before_call` only fires when a call is attempted; if no calls happen between OPEN and CLOSE, `opened_at` would not be set. `state_change` fires on the transition itself, regardless of subsequent traffic.
- **Read pybreaker's `_opened_at` directly** — rejected (this is the entire SD-024 anti-pattern).
- **Wrap pybreaker's state machine in a custom class** — rejected. Out of proportion to the problem; the listener subclass mechanism is sufficient.

---

## R2 — Time source

### Decision

Use `datetime.now(timezone.utc)` (timezone-aware UTC datetime) for both `opened_at` and `last_failure_time`.

### Rationale

- The codebase already establishes this pattern: `pipelines/scoring/management_api.py:27` imports `from datetime import datetime, timezone` and uses `datetime.now(tz=timezone.utc)` (`management_api.py:557`).
- Timezone-aware datetimes serialize unambiguously (the management API returns ISO-8601 strings — see `management_api.py:546,558`), critical for SRE timeline correlation across services in different deployments.
- Wall-clock time is correct for operator-facing correlation. Monotonic clocks would defeat the purpose (the SRE wants to compare against deployment events with real timestamps).

### Alternatives considered

- **`time.time()`** (Unix epoch float) — rejected. Less self-describing in logs and harder to compare against ISO-format timestamps elsewhere in the system.
- **Naive `datetime.utcnow()`** — rejected. Deprecated in Python 3.12+ and produces ambiguous values when serialized.
- **Inject a clock interface for testability** — rejected as over-engineered. Tests can freeze time with `freezegun` or assert "is not None + within ±n seconds of now" without a DI seam.

---

## R3 — `management_api.py` still reads pybreaker private attributes ⚠️ scope expansion

### Decision

**Refactor `pipelines/scoring/management_api.py` to consume the listener's `opened_at` and `last_failure_time` instead of reading `cb._opened_at` / `cb._last_failure_time`.** Include this refactor in the feature's scope; it is the canonical completion of SD-024 in this codebase.

### Rationale

Investigation revealed that the management API endpoint (lines 538-562) currently reads pybreaker private attributes:

```python
raw_failure = getattr(cb, "_last_failure_time", None)
...
opened_at = getattr(cb, "_opened_at", None)
```

This is the exact fragility SD-024 was framed around — except the May-13 draft mis-identified the listener as the culprit. The listener was cleaned up at some point between then and now (the listener today only reads `cb.current_state`, which is public). The smell migrated, presumably during a different refactor that touched the management API.

If this feature ships only the listener changes and leaves `management_api.py` reading private attrs, then:
- A pybreaker upgrade can still silently break the management API endpoint
- The new listener attributes become dead code from the perspective of the only existing consumer that needs them
- Spec SC-001 ("SRE handed only the listener instance can answer...") is *technically* satisfied but operationally useless — SREs interact via the management API, not by REPL'ing into the process

### Mechanism

`MLCircuitBreaker` (at `pipelines/scoring/circuit_breaker.py:55-71`) constructs its own listener and stores it inside the `pybreaker.CircuitBreaker(listeners=[FraudCircuitBreakerListener()])`. To let `management_api.py` reach the listener:

- Option A — `MLCircuitBreaker` exposes the listener as a public attribute (e.g., `self.listener = FraudCircuitBreakerListener(); self._cb = pybreaker.CircuitBreaker(listeners=[self.listener])`).
- Option B — Walk `cb.listeners` from `management_api.py` and find the `FraudCircuitBreakerListener` instance by type.

**Choose Option A.** It's explicit, no reflective lookup, and the listener becomes part of `MLCircuitBreaker`'s public surface — which is honest about the dependency.

### Alternatives considered

- **Park `management_api.py` cleanup as a follow-up** — rejected. Splits SD-024 across two PRs; the listener change becomes load-bearing for the second PR with no consumer in the first. Easier to land both together.
- **Add a Prometheus gauge for `opened_at` and let the management API read Prometheus** — rejected. Introduces a runtime dependency between two observability surfaces for no benefit.

### Spec update

The Assumptions section of `spec.md` was updated to reflect this finding (the prior wording "no external service reads circuit-open timestamps today" was wrong). FRs and SCs need no change — the listener attributes are still the load-bearing artifact; the management API refactor is a derivative consumer change.

---

## R4 — Existing tests touching the renamed counter

### Decision

Update three existing tests in `tests/unit/scoring/test_circuit_breaker.py` (lines 13, 89, 91, 97, 98 per grep) by string-substitution `ml_fallback_decisions_total` → `ml_circuit_open_calls_total`. No test logic changes; the assertions remain valid against the renamed counter.

### Rationale

- The tests don't depend on the counter's name semantically — they exercise its increment behavior. A rename + import update is sufficient.
- No test for `opened_at` / `last_failure_time` exists today. New cases are additive (US1 acceptance scenarios 1-5 in the spec).

### Alternatives considered

- **Delete the existing counter tests and rewrite from scratch** — rejected. The current tests cover OPEN-state increment correctly; deletion would lose that coverage temporarily.
- **Skip updating tests and rely on the rename to break them** — rejected. Letting tests fail visibly is fine in TDD; landing a PR with red tests is not.

---

## R5 — Idempotent OPEN re-notification (US1 edge case 5)

### Decision

Guard the `opened_at` write inside `state_change` with a check on `old_state != new_state`. Pybreaker should never call `state_change` with identical old and new state, but the listener is defensively cheap.

### Rationale

- Costs nothing: one extra string compare on a callback that fires once per state transition (~very rare).
- Spec explicitly demands this behavior (US1 acceptance scenario 5).
- Defensive code at boundary layers is good hygiene; this is a boundary between our code and a third-party library's callback contract.

### Alternatives considered

- **Trust pybreaker not to issue spurious re-notifications** — rejected. Cheap to defend; saves a future debugging session if the assumption breaks.

---

## Open questions

None. All NEEDS CLARIFICATION items resolved.
