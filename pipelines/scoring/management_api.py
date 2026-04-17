"""FastAPI management API for fraud rule engine control (port 8090).

Provides endpoints to:
- Demote/promote rules between active/shadow mode
- Check circuit breaker state
- Report healthz status for Alertmanager

v1 is file-based only (no Kafka); all mode changes write to YAML on disk and maintain
in-memory state.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pipelines.scoring.circuit_breaker import MLCircuitBreaker
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.rules.models import RuleDefinition, RuleMode

logger = logging.getLogger(__name__)

# Module-level state: in-memory rule dictionary and circuit breaker reference
_rules_dict: dict[str, RuleDefinition] = {}
_circuit_breaker: MLCircuitBreaker | None = None
_config: ScoringConfig | None = None


def set_circuit_breaker(cb: MLCircuitBreaker | None) -> None:
    """Set the circuit breaker reference (called by scoring engine at startup)."""
    global _circuit_breaker
    _circuit_breaker = cb


# Response models


class DemotePromoteResponse(BaseModel):
    """Response for demote/promote operations."""

    rule_id: str
    previous_mode: str
    new_mode: str
    config_event_published: bool = False  # v1 always False (no Kafka)


class CircuitBreakerState(BaseModel):
    """Circuit breaker state snapshot."""

    state: str
    failure_count: int
    last_failure_time: str | None
    next_probe_time: str | None


class HealthzResponse(BaseModel):
    """Health check response."""

    status: str


# Startup/shutdown


def _load_rules_from_yaml(yaml_path: str) -> None:
    """Load rules from YAML file into in-memory _rules_dict."""
    global _rules_dict
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        logger.error("Rules YAML file not found: %s", yaml_path)
        raise e
    except yaml.YAMLError as e:
        logger.error("Malformed YAML in %s: %s", yaml_path, e)
        raise e

    if not isinstance(data, list):
        raise ValueError("Rules config must be a YAML list")

    _rules_dict = {}
    for item in data:
        rule = RuleDefinition.model_validate(item)
        _rules_dict[rule.rule_id] = rule
    logger.info("Loaded %d rules from %s", len(_rules_dict), yaml_path)


def _write_rules_to_yaml(yaml_path: str) -> None:
    """Write in-memory _rules_dict back to YAML file."""
    import json as _json

    rules_data = [_json.loads(rule.model_dump_json()) for rule in _rules_dict.values()]
    try:
        with open(yaml_path, "w") as f:
            yaml.dump(rules_data, f, default_flow_style=False)
    except OSError as e:
        logger.error("Failed to write rules to YAML: %s", e)
        raise e


def _extract_trace_id() -> str:
    """Extract trace ID from active OTel span context, or return 'none'."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            return span.get_span_context().trace_id.hex() if span.get_span_context() else "none"
    except Exception:
        pass
    return "none"


def _emit_structured_log(
    event: str,
    rule_id: str,
    previous_mode: str,
    new_mode: str,
    triggered_by: str = "api",
) -> None:
    """Emit structured JSON log for rule mode change."""
    trace_id = _extract_trace_id()
    log_entry = {
        "event": event,
        "rule_id": rule_id,
        "previous_mode": previous_mode,
        "new_mode": new_mode,
        "triggered_by": triggered_by,
        "trace_id": trace_id,
    }
    logger.info(json.dumps(log_entry))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global _config
    _config = ScoringConfig()
    _load_rules_from_yaml(_config.rules_yaml_path)
    yield
    # Cleanup on shutdown if needed


app = FastAPI(
    title="Fraud Rule Management API",
    description="Manage fraud detection rules and circuit breaker state",
    lifespan=lifespan,
)


# Endpoints


@app.post("/rules/{rule_id}/demote")
async def demote_rule(rule_id: str) -> DemotePromoteResponse:
    """Demote a rule from active to shadow mode.

    Args:
        rule_id: The rule ID to demote.

    Returns:
        DemotePromoteResponse with previous_mode and new_mode.

    Raises:
        404: If rule not found.
        409: If rule is already in shadow mode.
        500: If YAML write fails.
    """
    if rule_id not in _rules_dict:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule = _rules_dict[rule_id]
    if rule.mode == RuleMode.shadow:
        raise HTTPException(status_code=409, detail=f"Rule {rule_id} is already in shadow mode")

    previous_mode = rule.mode.value
    rule.mode = RuleMode.shadow

    try:
        _write_rules_to_yaml(_config.rules_yaml_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write rules: {e}") from e

    _emit_structured_log(
        event="rule_mode_change",
        rule_id=rule_id,
        previous_mode=previous_mode,
        new_mode=RuleMode.shadow.value,
        triggered_by="api",
    )

    return DemotePromoteResponse(
        rule_id=rule_id,
        previous_mode=previous_mode,
        new_mode=RuleMode.shadow.value,
        config_event_published=False,
    )


@app.post("/rules/{rule_id}/promote")
async def promote_rule(rule_id: str) -> DemotePromoteResponse:
    """Promote a rule from shadow to active mode.

    Args:
        rule_id: The rule ID to promote.

    Returns:
        DemotePromoteResponse with previous_mode and new_mode.

    Raises:
        404: If rule not found.
        409: If rule is already in active mode.
        500: If YAML write fails.
    """
    if rule_id not in _rules_dict:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule = _rules_dict[rule_id]
    if rule.mode == RuleMode.active:
        raise HTTPException(status_code=409, detail=f"Rule {rule_id} is already in active mode")

    previous_mode = rule.mode.value
    rule.mode = RuleMode.active

    try:
        _write_rules_to_yaml(_config.rules_yaml_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write rules: {e}") from e

    _emit_structured_log(
        event="rule_mode_change",
        rule_id=rule_id,
        previous_mode=previous_mode,
        new_mode=RuleMode.active.value,
        triggered_by="api",
    )

    return DemotePromoteResponse(
        rule_id=rule_id,
        previous_mode=previous_mode,
        new_mode=RuleMode.active.value,
        config_event_published=False,
    )


@app.get("/circuit-breaker/state")
async def get_circuit_breaker_state() -> CircuitBreakerState:
    """Get current circuit breaker state.

    Returns:
        CircuitBreakerState with state name, failure count, and timing info.
    """
    if _circuit_breaker is None:
        return CircuitBreakerState(
            state="unknown",
            failure_count=0,
            last_failure_time=None,
            next_probe_time=None,
        )

    cb = _circuit_breaker._cb
    state = cb.current_state

    # Extract timing info from pybreaker
    last_failure_time = None
    next_probe_time = None

    if hasattr(cb, "_last_failure_time") and cb._last_failure_time is not None:
        last_failure_time = datetime.fromtimestamp(cb._last_failure_time).isoformat()

    if state == "open" and hasattr(cb, "_opened_at") and cb._opened_at is not None:
        next_probe_ms = (cb._opened_at + cb.reset_timeout - datetime.now().timestamp()) * 1000
        if next_probe_ms > 0:
            next_probe_time = datetime.now()
            next_probe_time = datetime.fromtimestamp(
                datetime.now().timestamp() + next_probe_ms / 1000
            ).isoformat()

    return CircuitBreakerState(
        state=state,
        failure_count=cb.fail_counter,
        last_failure_time=last_failure_time,
        next_probe_time=next_probe_time,
    )


@app.get("/healthz")
async def healthz() -> HealthzResponse:
    """Health check endpoint for Alertmanager.

    Returns:
        HealthzResponse with status "ok".
    """
    return HealthzResponse(status="ok")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
