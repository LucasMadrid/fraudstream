# `MLCircuitBreaker.get_state()` eliminates private pybreaker introspection

`management_api.py` needs to surface circuit breaker state in the health endpoint. Because `MLCircuitBreaker` exposes only `score_with_fallback()`, the API reaches past the abstraction:

```python
cb = _circuit_breaker._cb                        # line 528 — inner pybreaker object
getattr(cb, "_last_failure_time", None)          # line 542 — private pybreaker attribute
getattr(cb, "_opened_at", None)                  # line 553
getattr(cb, "reset_timeout", None)               # line 554
```

This is three private pybreaker attributes accessed via `getattr` — meaning a pybreaker version bump that renames `_last_failure_time` silently returns `None` in the health endpoint with no error. The abstraction `MLCircuitBreaker` provides does not reach the health-check caller.

## Solution

`MLCircuitBreaker` gains a typed `get_state()` method:

```python
@dataclass
class CircuitBreakerState:
    state: str                    # "closed", "open", "half_open"
    last_failure_time: float | None   # epoch seconds; None if no failure recorded
    opened_at: float | None           # epoch seconds; None if circuit never opened
    reset_timeout: float          # seconds until HALF_OPEN probe is attempted

def get_state(self) -> CircuitBreakerState:
    cb = self._cb
    return CircuitBreakerState(
        state=cb.current_state,
        last_failure_time=getattr(cb, "_last_failure_time", None),
        opened_at=getattr(cb, "_opened_at", None),
        reset_timeout=cb.reset_timeout,
    )
```

The `getattr` calls are contained inside `MLCircuitBreaker`, which owns `_cb`. If pybreaker renames a private attribute, the breakage is in one file, not spread across the API handler. `CircuitBreakerState` is exported from `pipelines/scoring/circuit_breaker.py` alongside `MLCircuitBreaker`.

`management_api.py` replaces the three `getattr` call sites with:

```python
state = _circuit_breaker.get_state()
# state.state, state.last_failure_time, state.opened_at, state.reset_timeout
```

## Design decisions

**Typed dataclass, not dict.** The existing `getattr` pattern already handles missing keys by defaulting to `None`; the dataclass mirrors that with typed optional fields. Dict access at the call site would lose the type annotation and re-introduce the accidental-key-name bug.

**On `MLCircuitBreaker`, not a shared protocol.** `management_api.py` already imports directly from `pipelines.scoring.circuit_breaker`. `IcebergCircuitBreaker` (in `pipelines/shared/circuit_breaker.py`) does not serve health endpoints and should not acquire a `get_state()` method it will never use.

**`reset_timeout` via `cb.reset_timeout`, not `getattr`.** `reset_timeout` is a public pybreaker attribute (set as a constructor arg), unlike `_last_failure_time` and `_opened_at`. Reading it directly is correct; the `getattr` in the current code is unnecessarily defensive.

## Test improvement

`MLCircuitBreaker.get_state()` can be tested by constructing an `MLCircuitBreaker` with a mock `MLModelClient`, forcing failures until the circuit opens, and asserting `get_state()` returns the expected `CircuitBreakerState`. No FastAPI app, no health endpoint fixture required.

## Considered alternatives

- *Re-export `_cb` as a public property* — saves one method but the caller still invokes pybreaker private attributes. Rejected: the introspection stays outside `MLCircuitBreaker`.
- *Switch pybreaker to a version-pinned snapshot* — treats the symptom (attribute rename risk) without fixing the locality problem. Rejected.
- *Add `get_state()` to `IcebergCircuitBreaker` as a shared protocol* — `IcebergCircuitBreaker` exposes only `call()` and `fail_max`; adding state inspection would widen its interface for a caller it doesn't have. Rejected.
