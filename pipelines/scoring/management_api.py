"""FastAPI management API for fraud rule engine control (port 8090).

Provides endpoints to:
- Demote/promote rules between active/shadow mode
- Check circuit breaker state
- Report healthz status for Alertmanager

v1 is file-based only (no Kafka); all mode changes write to YAML on disk and maintain
in-memory state.

Security:
- API key auth via X-Api-Key header (enforced when MANAGEMENT_API_KEY env var is set)
- Rate limiting: 10 req/min on mutating endpoints, 30 req/min on read endpoints
- CORS origins configurable via MANAGEMENT_CORS_ORIGINS (comma-separated, default: none)
- Security headers on every response (X-Content-Type-Options, X-Frame-Options, HSTS, CSP)
- Threading lock guards demote/promote read-modify-write cycle
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from pipelines.scoring.circuit_breaker import MLCircuitBreaker
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.rules.models import RuleDefinition, RuleMode

logger = logging.getLogger(__name__)

# Module-level state: in-memory rule dictionary and circuit breaker reference
_rules_dict: dict[str, RuleDefinition] = {}
_circuit_breaker: MLCircuitBreaker | None = None
_config: ScoringConfig | None = None
_rules_lock = threading.Lock()

# Rate limiter (in-memory; swap key_func for per-user limiting if auth is bearer-based)
_limiter = Limiter(key_func=get_remote_address)

# API key auth — if MANAGEMENT_API_KEY is not set the check is skipped (dev/test mode)
_API_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)

_RULE_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$"


def set_circuit_breaker(cb: MLCircuitBreaker | None) -> None:
    """Set the circuit breaker reference (called by scoring engine at startup)."""
    global _circuit_breaker
    _circuit_breaker = cb


async def _require_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> None:
    """Enforce X-Api-Key header when MANAGEMENT_API_KEY env var is configured."""
    expected = os.environ.get("MANAGEMENT_API_KEY")
    if expected and api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


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


# Middleware


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response


# Startup/shutdown


def _load_rules_from_yaml(yaml_path: str) -> None:
    """Load rules from YAML file into in-memory _rules_dict."""
    global _rules_dict
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        logger.error("Rules YAML file not found: %s", yaml_path)
        raise FileNotFoundError(yaml_path) from e
    except yaml.YAMLError as e:
        logger.error("Malformed YAML in %s: %s", yaml_path, e)
        raise yaml.YAMLError(str(e)) from e

    if not isinstance(data, list):
        raise ValueError("Rules config must be a YAML list")

    _rules_dict = {}
    for item in data:
        rule = RuleDefinition.model_validate(item)
        _rules_dict[rule.rule_id] = rule
    logger.info("Loaded %d rules from %s", len(_rules_dict), yaml_path)


def _write_rules_to_yaml(yaml_path: str) -> None:
    """Write in-memory _rules_dict back to YAML file."""
    rules_data = [json.loads(rule.model_dump_json()) for rule in _rules_dict.values()]
    try:
        with open(yaml_path, "w") as f:
            yaml.dump(rules_data, f, default_flow_style=False)
    except OSError as e:
        logger.error("Failed to write rules to YAML: %s", e)
        raise OSError(str(e)) from e


def _extract_trace_id() -> str:
    """Extract trace ID from active OTel span context, or return 'none'."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            return span.get_span_context().trace_id.hex() if span.get_span_context() else "none"
    except (ImportError, AttributeError, RuntimeError):
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

# Register rate limiter and its 429 exception handler
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — origins must be explicitly configured; disabled by default
_cors_origins_raw = os.environ.get("MANAGEMENT_CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["POST", "GET"],
        allow_credentials=False,
    )

app.add_middleware(_SecurityHeadersMiddleware)


# Endpoints


@app.post("/rules/{rule_id}/demote", dependencies=[Depends(_require_api_key)])
@_limiter.limit("10/minute")
async def demote_rule(
    request: Request,
    rule_id: Annotated[str, Path(pattern=_RULE_ID_PATTERN)],
) -> DemotePromoteResponse:
    """Demote a rule from active to shadow mode.

    Args:
        rule_id: The rule ID to demote (2-64 alphanumeric/hyphens/underscores).

    Returns:
        DemotePromoteResponse with previous_mode and new_mode.

    Raises:
        401: If API key is required and missing/wrong.
        404: If rule not found.
        409: If rule is already in shadow mode.
        422: If rule_id does not match the allowed pattern.
        500: If YAML write fails.
    """
    with _rules_lock:
        if rule_id not in _rules_dict:
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

        rule = _rules_dict[rule_id]
        if rule.mode == RuleMode.shadow:
            raise HTTPException(status_code=409, detail=f"Rule {rule_id} is already in shadow mode")

        previous_mode = rule.mode.value
        rule.mode = RuleMode.shadow

        try:
            _write_rules_to_yaml(_config.rules_yaml_path)
        except OSError:
            rule.mode = RuleMode[previous_mode]  # rollback in-memory state
            raise HTTPException(status_code=500, detail="Internal server error") from None

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


@app.post("/rules/{rule_id}/promote", dependencies=[Depends(_require_api_key)])
@_limiter.limit("10/minute")
async def promote_rule(
    request: Request,
    rule_id: Annotated[str, Path(pattern=_RULE_ID_PATTERN)],
) -> DemotePromoteResponse:
    """Promote a rule from shadow to active mode.

    Args:
        rule_id: The rule ID to promote (2-64 alphanumeric/hyphens/underscores).

    Returns:
        DemotePromoteResponse with previous_mode and new_mode.

    Raises:
        401: If API key is required and missing/wrong.
        404: If rule not found.
        409: If rule is already in active mode.
        422: If rule_id does not match the allowed pattern.
        500: If YAML write fails.
    """
    with _rules_lock:
        if rule_id not in _rules_dict:
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

        rule = _rules_dict[rule_id]
        if rule.mode == RuleMode.active:
            raise HTTPException(status_code=409, detail=f"Rule {rule_id} is already in active mode")

        previous_mode = rule.mode.value
        rule.mode = RuleMode.active

        try:
            _write_rules_to_yaml(_config.rules_yaml_path)
        except OSError:
            rule.mode = RuleMode[previous_mode]  # rollback in-memory state
            raise HTTPException(status_code=500, detail="Internal server error") from None

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


@app.get("/circuit-breaker/state", dependencies=[Depends(_require_api_key)])
@_limiter.limit("30/minute")
async def get_circuit_breaker_state(request: Request) -> CircuitBreakerState:
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

    last_failure_time = None
    next_probe_time = None

    try:
        if getattr(cb, "_last_failure_time", None) is not None:
            last_failure_time = datetime.fromtimestamp(cb._last_failure_time).isoformat()
    except (AttributeError, OSError, ValueError):
        pass

    try:
        if state == "open" and getattr(cb, "_opened_at", None) is not None:
            next_probe_ms = (cb._opened_at + cb.reset_timeout - datetime.now().timestamp()) * 1000
            if next_probe_ms > 0:
                next_probe_time = datetime.fromtimestamp(
                    datetime.now().timestamp() + next_probe_ms / 1000
                ).isoformat()
    except (AttributeError, OSError, ValueError):
        pass

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
