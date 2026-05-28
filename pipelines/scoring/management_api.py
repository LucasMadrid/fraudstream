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
- Asyncio lock guards demote/promote read-modify-write cycle
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from pipelines.replay.models import (
    DLQSourceConfig,
    IcebergSourceConfig,
    KafkaSourceConfig,
    ReplayConfig,
    ReplaySource,
    ReplayStatus,
)
from pipelines.replay.replay_job import ReplayJob
from pipelines.scoring.circuit_breaker import MLCircuitBreaker
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.rules.models import RuleDefinition, RuleMode
from pipelines.shared.tracing import (
    get_current_trace_id,
    get_tracer,
    init_tracer_provider,
)

logger = logging.getLogger(__name__)

# Initialize tracer for scoring service
init_tracer_provider(
    service_name="fraudstream-scoring",
    sample_rate=float(os.environ.get("OTEL_SAMPLE_RATE", "1.0")),
)

tracer = get_tracer(__name__)

# Module-level state: in-memory rule dictionary, circuit breaker reference, and replay jobs
_rules_dict: dict[str, RuleDefinition] = {}
_circuit_breaker: MLCircuitBreaker | None = None
_config: ScoringConfig | None = None
_rules_lock = asyncio.Lock()

# Replay job state
_replay_jobs: dict[str, ReplayJob] = {}
_replay_jobs_lock = asyncio.Lock()


def _rate_limit_key(request: Request) -> str:
    """Use X-Real-IP if present (set by trusted reverse proxy), else client address.

    Avoids key bypass via X-Forwarded-For when not behind a proxy, while still
    supporting proxy-forwarded IPs when the infra is correctly configured.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


# Rate limiter (in-memory; keyed by X-Real-IP or direct client address)
_limiter = Limiter(key_func=_rate_limit_key)

# API key auth — if MANAGEMENT_API_KEY is not set the check is skipped (dev/test mode)
_API_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)

# Alphanumeric start/end; hyphens and underscores allowed in the middle; 2-64 chars total.
# pydantic_core uses Rust's regex crate (no lookaheads), so consecutive-separator rejection
# is handled at this level only by accepting the minor permissiveness (e.g. "VEL---001").
# The critical security goal — blocking shell metacharacters and path separators — is met.
_RULE_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$"


def set_circuit_breaker(cb: MLCircuitBreaker | None) -> None:
    """Set the circuit breaker reference (called by scoring engine at startup)."""
    global _circuit_breaker
    _circuit_breaker = cb


async def _require_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> None:
    """Enforce X-Api-Key header when MANAGEMENT_API_KEY env var is configured."""
    expected = os.environ.get("MANAGEMENT_API_KEY")
    if expected and (api_key is None or not secrets.compare_digest(expected, api_key)):
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


# Replay request/response models


class IcebergSourceRequest(BaseModel):
    """Iceberg source configuration request."""

    table_name: str
    start_timestamp: str
    end_timestamp: str
    snapshot_id: int | None = None
    filter_expression: str | None = None


class KafkaSourceRequest(BaseModel):
    """Kafka source configuration request."""

    topic: str
    start_offset: int
    end_offset: int
    partition: int = 0
    consumer_group: str | None = None


class DLQSourceRequest(BaseModel):
    """DLQ source configuration request."""

    source_topic: str
    dlq_topic: str = "txn.api.dlq"
    start_time: str | None = None
    end_time: str | None = None
    max_messages: int = 1000


class CreateReplayJobRequest(BaseModel):
    """Request to create a new replay job."""

    source_type: str  # iceberg, kafka, dlq
    iceberg_config: IcebergSourceRequest | None = None
    kafka_config: KafkaSourceRequest | None = None
    dlq_config: DLQSourceRequest | None = None
    rule_set_version: str | None = None
    use_shadow_rules: bool = True
    output_topic: str = "txn.replay.results"
    description: str = ""


class ReplayJobResponse(BaseModel):
    """Response for replay job operations."""

    job_id: str
    source_type: str | None
    status: str
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    progress_percent: float
    total_events: int
    processed_events: int
    failed_events: int
    results_summary: dict
    error_message: str | None
    description: str


class ReplayResultItem(BaseModel):
    """Single replay result item."""

    replay_job_id: str
    original_event_id: str
    original_timestamp: str
    replay_timestamp: str
    original_decision: str | None
    replay_decision: str | None
    original_score: float | None
    replay_score: float | None
    triggered_rules_original: list[str]
    triggered_rules_replay: list[str]
    score_delta: float
    decision_changed: bool
    processing_time_ms: float
    metadata: dict


class ReplayResultsResponse(BaseModel):
    """Response containing replay results."""

    job_id: str
    total_results: int
    results: list[ReplayResultItem]


# Middleware


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Attach strict security headers to every HTTP response.

        This middleware ensures responses include the following security headers:
        ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``,
        ``Strict-Transport-Security: max-age=31536000; includeSubDomains``,
        and ``Content-Security-Policy: default-src 'none'``.

        Returns:
            Response: The downstream response with the security headers added.
        """
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
        os.chmod(yaml_path, 0o600)
    except OSError as e:
        logger.error("Failed to write rules to YAML: errno=%s", e.errno)
        raise OSError(str(e)) from e


def _emit_structured_log(
    event: str,
    rule_id: str,
    previous_mode: str,
    new_mode: str,
    triggered_by: str = "api",
) -> None:
    """Emit structured JSON log for rule mode change."""
    trace_id = get_current_trace_id() or "none"
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
    with tracer.start_as_current_span("management_api.demote_rule") as span:
        span.set_attribute("rule.id", rule_id)
        span.set_attribute("http.method", "POST")
        span.set_attribute("http.route", "/rules/{rule_id}/demote")

        async with _rules_lock:
            if rule_id not in _rules_dict:
                span.set_attribute("error", True)
                span.set_attribute("error.code", 404)
                raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

            rule = _rules_dict[rule_id]
            if rule.mode == RuleMode.shadow:
                span.set_attribute("error", True)
                span.set_attribute("error.code", 409)
                raise HTTPException(
                    status_code=409, detail=f"Rule {rule_id} is already in shadow mode"
                )

            previous_mode = rule.mode.value
            rule.mode = RuleMode.shadow

            try:
                _write_rules_to_yaml(_config.rules_yaml_path)
            except OSError:
                rule.mode = RuleMode[previous_mode]  # rollback in-memory state
                span.set_attribute("error", True)
                span.set_attribute("error.code", 500)
                raise HTTPException(status_code=500, detail="Internal server error") from None

        span.set_attribute("rule.previous_mode", previous_mode)
        span.set_attribute("rule.new_mode", RuleMode.shadow.value)

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
    """
    Promote a rule from shadow mode to active mode.

    Parameters:
        rule_id (str): Identifier of the rule to promote; must match the service's rule ID pattern.

    Returns:
        DemotePromoteResponse: Details of the rule mode change, including
            `previous_mode`, `new_mode`, and `config_event_published`.

    Raises:
        HTTPException 401: If an API key is required and the request is unauthorized.
        HTTPException 404: If the specified rule does not exist.
        HTTPException 409: If the rule is already in active mode.
        HTTPException 422: If `rule_id` fails validation against the allowed pattern.
        HTTPException 500: If persisting the updated rules to YAML fails
            (the in-memory change is rolled back).
    """
    with tracer.start_as_current_span("management_api.promote_rule") as span:
        span.set_attribute("rule.id", rule_id)
        span.set_attribute("http.method", "POST")
        span.set_attribute("http.route", "/rules/{rule_id}/promote")

        async with _rules_lock:
            if rule_id not in _rules_dict:
                span.set_attribute("error", True)
                span.set_attribute("error.code", 404)
                raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

            rule = _rules_dict[rule_id]
            if rule.mode == RuleMode.active:
                span.set_attribute("error", True)
                span.set_attribute("error.code", 409)
                raise HTTPException(
                    status_code=409, detail=f"Rule {rule_id} is already in active mode"
                )

            previous_mode = rule.mode.value
            rule.mode = RuleMode.active

            try:
                _write_rules_to_yaml(_config.rules_yaml_path)
            except OSError:
                rule.mode = RuleMode[previous_mode]  # rollback in-memory state
                span.set_attribute("error", True)
                span.set_attribute("error.code", 500)
                raise HTTPException(status_code=500, detail="Internal server error") from None

        span.set_attribute("rule.previous_mode", previous_mode)
        span.set_attribute("rule.new_mode", RuleMode.active.value)

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
    """
    Provide a snapshot of the current circuit breaker state.

    Returns:
        CircuitBreakerState: object containing:
            - `state`: current circuit breaker state name
              (e.g., "closed", "open", "half-open", or "unknown")
            - `failure_count`: integer count of recent failures
            - `last_failure_time`: ISO 8601 timestamp of the last failure,
              or `None` if unavailable
            - `next_probe_time`: ISO 8601 timestamp when the next probe is
              expected for an open breaker, or `None`
    """
    with tracer.start_as_current_span("management_api.circuit_breaker_state") as span:
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/circuit-breaker/state")

        if _circuit_breaker is None:
            span.set_attribute("circuit_breaker.state", "unknown")
            return CircuitBreakerState(
                state="unknown",
                failure_count=0,
                last_failure_time=None,
                next_probe_time=None,
            )

        cb = _circuit_breaker._cb
        listener = _circuit_breaker.listener

        # pybreaker public API: current_state, fail_counter, reset_timeout.
        # Transition timestamps come from the listener (SD-024) — never pybreaker private attrs.
        state: str = getattr(cb, "current_state", "unknown")
        failure_count: int = getattr(cb, "fail_counter", 0)

        span.set_attribute("circuit_breaker.state", state)
        span.set_attribute("circuit_breaker.failure_count", failure_count)

        last_failure_time = (
            listener.last_failure_time.isoformat()
            if listener.last_failure_time is not None
            else None
        )

        next_probe_time = None
        opened_at = listener.opened_at
        reset_timeout = getattr(cb, "reset_timeout", None)
        if state == "open" and opened_at is not None and reset_timeout is not None:
            try:
                probe_ts = opened_at.timestamp() + float(reset_timeout)
                if probe_ts > datetime.now(tz=timezone.utc).timestamp():
                    next_probe_time = datetime.fromtimestamp(probe_ts, tz=timezone.utc).isoformat()
            except (OSError, ValueError, TypeError) as e:
                logger.debug(
                    "Could not compute next_probe_time from circuit breaker: %s", type(e).__name__
                )

        return CircuitBreakerState(
            state=state,
            failure_count=failure_count,
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


# Replay job endpoints


def _build_replay_config(request: CreateReplayJobRequest) -> ReplayConfig:
    """Build ReplayConfig from API request."""
    source_type = ReplaySource(request.source_type)

    iceberg_config = None
    kafka_config = None
    dlq_config = None

    if source_type == ReplaySource.iceberg:
        if request.iceberg_config is None:
            raise HTTPException(status_code=422, detail="iceberg_config required")
        iceberg_config = IcebergSourceConfig(
            table_name=request.iceberg_config.table_name,
            start_timestamp=request.iceberg_config.start_timestamp,
            end_timestamp=request.iceberg_config.end_timestamp,
            snapshot_id=request.iceberg_config.snapshot_id,
            filter_expression=request.iceberg_config.filter_expression,
        )
    elif source_type == ReplaySource.kafka:
        if request.kafka_config is None:
            raise HTTPException(status_code=422, detail="kafka_config required")
        kafka_config = KafkaSourceConfig(
            topic=request.kafka_config.topic,
            start_offset=request.kafka_config.start_offset,
            end_offset=request.kafka_config.end_offset,
            partition=request.kafka_config.partition,
            consumer_group=request.kafka_config.consumer_group,
        )
    elif source_type == ReplaySource.dlq:
        if request.dlq_config is None:
            raise HTTPException(status_code=422, detail="dlq_config required")
        dlq_config = DLQSourceConfig(
            source_topic=request.dlq_config.source_topic,
            dlq_topic=request.dlq_config.dlq_topic,
            start_time=request.dlq_config.start_time,
            end_time=request.dlq_config.end_time,
            max_messages=request.dlq_config.max_messages,
        )

    return ReplayConfig(
        source_type=source_type,
        iceberg_config=iceberg_config,
        kafka_config=kafka_config,
        dlq_config=dlq_config,
        rule_set_version=request.rule_set_version,
        use_shadow_rules=request.use_shadow_rules,
        output_topic=request.output_topic,
        description=request.description,
    )


@app.post("/replay/jobs", dependencies=[Depends(_require_api_key)])
@_limiter.limit("10/minute")
async def create_replay_job(
    request: Request,
    body: CreateReplayJobRequest,
) -> ReplayJobResponse:
    """Create and start a new replay job.

    Args:
        body: Replay job configuration

    Returns:
        ReplayJobResponse with job status

    Raises:
        401: If API key required and missing
        422: If configuration is invalid
    """
    with get_tracer(__name__).start_as_current_span("create_replay_job") as span:
        span.set_attribute("source_type", body.source_type)
        span.set_attribute("description", body.description)

        try:
            config = _build_replay_config(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        brokers = os.environ.get("KAFKA_BROKERS", "localhost:9092")
        job = ReplayJob(config, brokers=brokers)

        async with _replay_jobs_lock:
            _replay_jobs[job.job_id] = job

        span.set_attribute("job_id", job.job_id)

        # Start job in background
        async def run_job() -> None:
            # Dummy scoring function - would be replaced with actual scoring
            def dummy_scoring(event: dict) -> dict:
                return {
                    "decision": "review",
                    "score": 0.5,
                    "triggered_rules": ["rule_001"],
                }

            await job.start(dummy_scoring)

        asyncio.create_task(run_job())

        return ReplayJobResponse(**job.get_status().to_dict())


@app.get("/replay/jobs/{job_id}", dependencies=[Depends(_require_api_key)])
@_limiter.limit("30/minute")
async def get_replay_job(
    request: Request,
    job_id: str,
) -> ReplayJobResponse:
    """Get status of a replay job.

    Args:
        job_id: The replay job ID

    Returns:
        ReplayJobResponse with current status

    Raises:
        401: If API key required and missing
        404: If job not found
    """
    async with _replay_jobs_lock:
        job = _replay_jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Replay job {job_id} not found")

    return ReplayJobResponse(**job.get_status().to_dict())


@app.get("/replay/jobs/{job_id}/results", dependencies=[Depends(_require_api_key)])
@_limiter.limit("30/minute")
async def get_replay_results(
    request: Request,
    job_id: str,
    limit: int = 100,
    offset: int = 0,
) -> ReplayResultsResponse:
    """Get results for a replay job.

    Args:
        job_id: The replay job ID
        limit: Maximum results to return (default: 100)
        offset: Offset for pagination (default: 0)

    Returns:
        ReplayResultsResponse with results

    Raises:
        401: If API key required and missing
        404: If job not found
    """
    async with _replay_jobs_lock:
        job = _replay_jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Replay job {job_id} not found")

    results = job.get_results()
    total = len(results)
    paginated = results[offset : offset + limit]

    result_items = [
        ReplayResultItem(
            replay_job_id=r.replay_job_id,
            original_event_id=r.original_event_id,
            original_timestamp=r.original_timestamp.isoformat(),
            replay_timestamp=r.replay_timestamp.isoformat(),
            original_decision=r.original_decision,
            replay_decision=r.replay_decision,
            original_score=r.original_score,
            replay_score=r.replay_score,
            triggered_rules_original=r.triggered_rules_original,
            triggered_rules_replay=r.triggered_rules_replay,
            score_delta=r.score_delta,
            decision_changed=r.decision_changed,
            processing_time_ms=r.processing_time_ms,
            metadata=r.metadata,
        )
        for r in paginated
    ]

    return ReplayResultsResponse(
        job_id=job_id,
        total_results=total,
        results=result_items,
    )


@app.delete("/replay/jobs/{job_id}", dependencies=[Depends(_require_api_key)])
@_limiter.limit("10/minute")
async def cancel_replay_job(
    request: Request,
    job_id: str,
) -> ReplayJobResponse:
    """Cancel a running replay job.

    Args:
        job_id: The replay job ID to cancel

    Returns:
        ReplayJobResponse with updated status

    Raises:
        401: If API key required and missing
        404: If job not found
        409: If job already completed or failed
    """
    async with _replay_jobs_lock:
        job = _replay_jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Replay job {job_id} not found")

    status = job.get_status()
    if status.status in (ReplayStatus.completed, ReplayStatus.failed, ReplayStatus.cancelled):
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is already {status.status.value}",
        )

    job.cancel()

    return ReplayJobResponse(**job.get_status().to_dict())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
