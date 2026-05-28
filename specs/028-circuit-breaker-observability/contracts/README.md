# Contracts: Circuit Breaker Observability — Open-State Timestamps & Metric Rename (SD-024)

**Phase 1 output** | Branch: `028-circuit-breaker-observability`

## External interface changes

This feature is mostly internal but **does** touch two externally-observable surfaces:

### 1. Prometheus metric rename

| Before | After |
|---|---|
| `ml_fallback_decisions_total` (Counter) | `ml_circuit_open_calls_total` (Counter) |

This is a **breaking change** for any external dashboard, alert, or scrape consumer that references the old name. Mitigation is the PR description callout (FR-006); aliasing is explicitly out of scope (spec Assumptions). No grace period.

### 2. Management API response — internal source change only

The management API endpoint that returns the circuit breaker state (`pipelines/scoring/management_api.py`) returns a `CircuitBreakerState` model with `last_failure_time` and `next_probe_time` fields. **The external response shape and field names do not change.** Only the internal computation path changes — from pybreaker private-attribute access to the listener's new public attributes. Consumers of the API endpoint see no difference.

## What does NOT change

- HTTP routes — no new endpoints, no removed endpoints
- Kafka topics or schemas — none touched
- The Avro `txn-api-v1.avsc` and related event schemas — none touched
- `MLCircuitBreaker.score_with_fallback()` signature and return type
- The on-disk shape of any persisted state — there is none
- Environment variable names — none added or removed
- Container image build inputs — `pyproject.toml` unchanged

## Compatibility notes

The metric rename is the only externally-visible breaking change. The two pre-merge actions that mitigate it:

1. PR description lists the old and new metric names so dashboard/alert owners can update their out-of-tree configs (FR-006).
2. Spec SC-006 sets a 14-day post-merge incident-watch window. A new dashboard/alert misfire attributed to the rename is the primary signal that mitigation was insufficient.
