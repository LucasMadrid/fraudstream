# Data Model: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Phase 1 output** | Branch: `028-circuit-breaker-observability` | Date: 2026-05-27

## Entities

### FraudCircuitBreakerListener (modified — `pipelines/scoring/circuit_breaker.py`)

The listener gains two public attributes and one new callback implementation.

| Field | Type | Initial | When updated | Notes |
|-------|------|---------|--------------|-------|
| `opened_at` | `datetime \| None` | `None` | Inside `state_change` when `new_state == "open"` and `old_state != new_state` | Records the most-recent OPEN transition; overwritten on each subsequent OPEN (never reset on CLOSE) |
| `last_failure_time` | `datetime \| None` | `None` | Inside `failure(cb, exc)` on every call | Records every observed failure regardless of resulting state |

Both attributes are timezone-aware (`datetime.now(timezone.utc)`).

#### Callbacks (overridden from `pybreaker.CircuitBreakerListener`)

| Callback | Behavior |
|---|---|
| `state_change(cb, old_state, new_state)` | (Existing) Updates `ml_circuit_breaker_state` gauge. (New) If `new_state == "open"` AND `old_state != new_state`, sets `self.opened_at = datetime.now(timezone.utc)`. (Existing) Logs the transition. |
| `failure(cb, exc)` | (New) Sets `self.last_failure_time = datetime.now(timezone.utc)`. (Previously inherited as no-op.) |
| `before_call(cb, func, *args, **kwargs)` | (Existing) Increments the **renamed** counter `ml_circuit_open_calls_total` when `cb.current_state == "open"`. Logic unchanged; only the counter name changes. |
| `success(cb)` | Not overridden. Inherited no-op. |

#### Invariants

- `opened_at` MUST NOT be advanced by an idempotent `state_change(cb, "open", "open")` callback (defensive guard per Phase 0 R5).
- `last_failure_time` is advanced unconditionally on every `failure` callback — including failures that do not trigger an OPEN transition (e.g., the first failure when threshold is 3).
- Neither attribute is ever reset to `None` after first being set. The listener does not support "forget history".

---

### MLCircuitBreaker (modified — `pipelines/scoring/circuit_breaker.py`)

Adds one public attribute to expose the listener to external consumers (specifically `management_api.py`).

| Field | Type | Notes |
|-------|------|-------|
| `listener` | `FraudCircuitBreakerListener` | (New, public) The same instance registered with the underlying `pybreaker.CircuitBreaker(listeners=[...])`. Set in `__init__`. Accessible without a leading underscore. |

The breaker's existing public attributes (`client`, `config`) are unchanged.

---

### Prometheus Counter (renamed — `pipelines/scoring/circuit_breaker.py`)

| Before | After |
|---|---|
| Name: `ml_fallback_decisions_total` | Name: `ml_circuit_open_calls_total` |
| Description: "Total ML scoring decisions made in fallback mode (circuit open)" | Description: "Total calls that arrived while the ML circuit was OPEN" |
| Increment trigger: `before_call` when `cb.current_state == "open"` | Increment trigger: unchanged |

The Python module-level identifier MUST also rename to match (`ml_circuit_open_calls_total = Counter(...)`).

---

### CircuitBreakerState (read-side, `pipelines/scoring/management_api.py`)

The management API's existing `CircuitBreakerState` response model (already returns `last_failure_time` and `next_probe_time` as ISO-8601 strings) is **not changed externally**. Only the internal source of those values changes:

| Field | Source (before) | Source (after) |
|-------|----------------|----------------|
| `last_failure_time` | `getattr(cb, "_last_failure_time", None)` then `datetime.fromtimestamp(..., tz=timezone.utc).isoformat()` | `listener.last_failure_time.isoformat()` if not None, else None |
| `next_probe_time` | Derived from `getattr(cb, "_opened_at", None) + cb.reset_timeout` | Derived from `listener.opened_at.timestamp() + cb.reset_timeout` (still uses public `reset_timeout`) |

External response shape: identical (no API contract change).

---

## State transitions

The listener has no formal state machine — both fields are write-only-with-overwrite. The only constraint is the idempotent-OPEN guard (R5).

```text
Field lifecycle for `opened_at`:

  None  ──(OPEN transition)──▶  T₁
   ▲                            │
   │                            │
   │                       (HALF-OPEN, CLOSED transitions — no change)
   │                            │
   │                            ▼
   │                            T₁  ──(OPEN again, later)──▶  T₂
   │                                                            │
   └────(field is never reset to None)──────────────────────────┘

Field lifecycle for `last_failure_time`:

  None  ──(any failure)──▶  T₁  ──(any failure)──▶  T₂  ──(any failure)──▶ Tₙ
```

## Validation rules

- `opened_at` MUST be timezone-aware (`tzinfo is not None`).
- `last_failure_time` MUST be timezone-aware.
- No validation on monotonicity — wall-clock can move backwards (NTP adjustments); the listener does not defend against that.
