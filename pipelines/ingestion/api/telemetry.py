"""OpenTelemetry tracer for the API channel producer."""

from __future__ import annotations

from opentelemetry.trace import Tracer

from pipelines.shared.telemetry import build_tracer

_tracer: Tracer | None = None


def init_tracer(service_name: str = "api-producer") -> Tracer:
    """Initialize the OTel tracer. Safe to call multiple times (idempotent)."""
    global _tracer
    if _tracer is not None:
        return _tracer
    _tracer = build_tracer(service_name)
    return _tracer


def get_tracer() -> Tracer:
    """Return the initialized tracer, initializing with no-op if not yet set up."""
    global _tracer
    if _tracer is None:
        return init_tracer()
    return _tracer
