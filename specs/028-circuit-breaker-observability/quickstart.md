# Quickstart: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Branch**: `028-circuit-breaker-observability`

Walk-through for the contributor implementing the feature, and the reviewer verifying it.

## What changes

Three files modified, no new files.

| Action | File | Status |
|---|---|---|
| Extend listener + rename counter + expose listener on `MLCircuitBreaker` | `pipelines/scoring/circuit_breaker.py` | MODIFIED |
| Consume listener attrs instead of pybreaker private attrs | `pipelines/scoring/management_api.py` | MODIFIED |
| Counter rename + new behavioral tests | `tests/unit/scoring/test_circuit_breaker.py` | MODIFIED |

## Author walk-through

### Step 1 — `pipelines/scoring/circuit_breaker.py`

#### 1a. Add datetime imports

```python
from datetime import datetime, timezone
```

#### 1b. Rename the Counter (module-level identifier + name + description)

```python
ml_circuit_open_calls_total = Counter(
    "ml_circuit_open_calls_total",
    "Total calls that arrived while the ML circuit was OPEN",
)
```

#### 1c. Extend `FraudCircuitBreakerListener`

```python
class FraudCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Updates Prometheus metrics on circuit breaker state transitions
    and records the most-recent open transition + last failure timestamps.
    """

    def __init__(self) -> None:
        self.opened_at: datetime | None = None
        self.last_failure_time: datetime | None = None

    def state_change(self, cb, old_state, new_state) -> None:
        # (existing) update the per-state gauge
        for state in ["closed", "open", "half_open"]:
            ml_circuit_breaker_state.labels(state=state).set(1 if state == new_state else 0)
        logger.info("Circuit breaker state changed: %s -> %s", old_state, new_state)

        # (new) record opened_at on entry to OPEN; defend against idempotent re-notify
        if new_state == "open" and old_state != new_state:
            self.opened_at = datetime.now(timezone.utc)

    def failure(self, cb, exc) -> None:
        # (new) record every observed failure, regardless of resulting state
        self.last_failure_time = datetime.now(timezone.utc)

    def before_call(self, cb, func, *args, **kwargs) -> None:
        # (existing) increment the renamed counter
        if cb.current_state == "open":
            ml_circuit_open_calls_total.inc()
```

#### 1d. Expose the listener on `MLCircuitBreaker`

```python
class MLCircuitBreaker:
    def __init__(self, client: MLModelClient, config: ScoringConfig) -> None:
        self.client = client
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)

        self.listener = FraudCircuitBreakerListener()  # ← was inline in the listeners= arg
        self._cb = pybreaker.CircuitBreaker(
            fail_max=config.cb_error_threshold,
            reset_timeout=config.cb_open_seconds,
            listeners=[self.listener],
        )
        self._cb_wrapped_score = self._cb(self.client.score)
```

#### 1e. Update `__all__`

```python
__all__ = [
    "FraudCircuitBreakerListener",
    "MLCircuitBreaker",
    "ml_circuit_breaker_state",
    "ml_circuit_open_calls_total",
]
```

Add `FraudCircuitBreakerListener` to `__all__` if it's now considered part of the module's public surface (tests already import it; making it public matches reality).

### Step 2 — `pipelines/scoring/management_api.py`

Replace the private-attribute reads in the circuit-breaker-state endpoint (around lines 538-562). The handler currently receives `cb` (the breaker); it needs the listener too. Two ways:

- **Recommended**: pass the `MLCircuitBreaker` instance and read `breaker.listener.opened_at` / `breaker.listener.last_failure_time` directly.
- **Alternative**: walk `cb.listeners` to find the `FraudCircuitBreakerListener` by type. More reflective; reject if the recommended path is feasible.

Verify by reading the surrounding function to determine which option fits naturally. Maintain the existing `try/except` shape — but the caught exceptions should no longer fire after this change (the failure modes shift from `AttributeError` on private-attr access to "listener attr is None", which is normal-path data).

Concrete edit shape:

```python
# BEFORE
raw_failure = getattr(cb, "_last_failure_time", None)
if raw_failure is not None:
    last_failure_time = datetime.fromtimestamp(float(raw_failure), tz=timezone.utc).isoformat()

# AFTER
if listener.last_failure_time is not None:
    last_failure_time = listener.last_failure_time.isoformat()
```

```python
# BEFORE
opened_at = getattr(cb, "_opened_at", None)
reset_timeout = getattr(cb, "reset_timeout", None)
if state == "open" and opened_at is not None and reset_timeout is not None:
    probe_ts = float(opened_at) + float(reset_timeout)
    ...

# AFTER
opened_at = listener.opened_at
reset_timeout = getattr(cb, "reset_timeout", None)  # reset_timeout is public, keep getattr defensiveness
if state == "open" and opened_at is not None and reset_timeout is not None:
    probe_ts = opened_at.timestamp() + float(reset_timeout)
    ...
```

### Step 3 — `tests/unit/scoring/test_circuit_breaker.py`

#### 3a. Counter rename (mechanical)

```python
# Change line 13:
from pipelines.scoring.circuit_breaker import (
    FraudCircuitBreakerListener,
    MLCircuitBreaker,
    ml_circuit_breaker_state,
    ml_circuit_open_calls_total,  # was: ml_fallback_decisions_total
)

# Update all three references in the test methods (lines 89, 91, 97, 98).
```

#### 3b. Add tests covering US1 acceptance scenarios

Place inside `class TestFraudCircuitBreakerListener:`.

```python
def test_initial_state_has_no_timestamps(self):
    listener = FraudCircuitBreakerListener()
    assert listener.opened_at is None
    assert listener.last_failure_time is None

def test_failure_callback_sets_last_failure_time(self):
    listener = FraudCircuitBreakerListener()
    listener.failure(MagicMock(), Exception("boom"))
    assert listener.last_failure_time is not None
    assert listener.last_failure_time.tzinfo is not None  # timezone-aware

def test_state_change_to_open_sets_opened_at(self):
    listener = FraudCircuitBreakerListener()
    listener.state_change(MagicMock(), "closed", "open")
    assert listener.opened_at is not None
    assert listener.opened_at.tzinfo is not None

def test_state_change_to_closed_preserves_opened_at(self):
    listener = FraudCircuitBreakerListener()
    listener.state_change(MagicMock(), "closed", "open")
    first_open = listener.opened_at
    listener.state_change(MagicMock(), "open", "half_open")
    listener.state_change(MagicMock(), "half_open", "closed")
    assert listener.opened_at == first_open  # not reset on CLOSE

def test_subsequent_open_overwrites_opened_at(self):
    import time
    listener = FraudCircuitBreakerListener()
    listener.state_change(MagicMock(), "closed", "open")
    first_open = listener.opened_at
    time.sleep(0.01)  # ensure measurable delta
    listener.state_change(MagicMock(), "open", "half_open")
    listener.state_change(MagicMock(), "half_open", "closed")
    listener.state_change(MagicMock(), "closed", "open")
    assert listener.opened_at > first_open

def test_idempotent_open_does_not_reset_opened_at(self):
    listener = FraudCircuitBreakerListener()
    listener.state_change(MagicMock(), "closed", "open")
    first_open = listener.opened_at
    # Defensive: pybreaker shouldn't issue OPEN→OPEN, but if it does, we hold steady
    listener.state_change(MagicMock(), "open", "open")
    assert listener.opened_at == first_open

def test_ml_circuit_breaker_exposes_listener(self):
    from pipelines.scoring.ml_client import StubMLModelClient
    cb = MLCircuitBreaker(StubMLModelClient(stub_score=0.1), _config())
    assert isinstance(cb.listener, FraudCircuitBreakerListener)
```

## Reviewer walk-through

1. **Counter rename** — `grep -rn 'ml_fallback_decisions_total' pipelines/ tests/` returns 0 matches (SC-002).
2. **Listener attrs** — open `pipelines/scoring/circuit_breaker.py`, confirm `FraudCircuitBreakerListener.__init__` initialises both fields to `None` and the `state_change` / `failure` callbacks set them.
3. **Idempotent guard** — confirm `state_change` checks `old_state != new_state` before advancing `opened_at`.
4. **Management API consumption** — open `pipelines/scoring/management_api.py` and confirm no `getattr(cb, "_..."...)` lines remain in the circuit-breaker-state path; the values now come from `listener.opened_at` / `listener.last_failure_time`.
5. **External API unchanged** — `git diff main -- pipelines/scoring/management_api.py` shows no change to function signatures, no change to the response model.
6. **Tests** — `pytest tests/unit/scoring/test_circuit_breaker.py -v` passes; new test cases enumerate US1 acceptance scenarios.

## Verification commands

```bash
# Counter rename clean (SC-002)
grep -rn 'ml_fallback_decisions_total' pipelines/ tests/   # → 0 matches

# Listener attrs in place
grep -nE 'opened_at|last_failure_time' pipelines/scoring/circuit_breaker.py

# Private attrs gone from management_api
grep -nE 'getattr\(cb, "_' pipelines/scoring/management_api.py   # → 0 matches

# Full test suite
.venv/bin/python -m pytest tests/unit/scoring/test_circuit_breaker.py -v
.venv/bin/python -m pytest tests/unit/ --cov=pipelines --cov-fail-under=20

# Lint
.venv/bin/ruff check pipelines/scoring/circuit_breaker.py pipelines/scoring/management_api.py tests/unit/scoring/test_circuit_breaker.py
.venv/bin/ruff format --check pipelines/scoring/circuit_breaker.py pipelines/scoring/management_api.py tests/unit/scoring/test_circuit_breaker.py
```

## Closing the issue

No existing GitHub issue for SD-024 (verified via `gh issue list --state all --search 'SD-024'`). The PR description MUST reference SD-024 explicitly (FR-007) so it can be retroactively linked if/when an issue is filed.
