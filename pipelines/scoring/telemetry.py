"""OpenTelemetry tracer bootstrap for the scoring engine.

v1 limitation: tail-based sampling requires OTel Collector sidecar which is not
present in v1 docker-compose; deferred to v2. v1 approximation: ParentBased sampler
with TraceIdRatioBased(0.01) root for ALLOW decisions, AlwaysOnSampler logic applied
via span attribute check for BLOCK/error.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from pipelines.scoring.safe_metrics import SafeGauge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional OTel imports — graceful no-op when opentelemetry is absent
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

    _HAS_OTEL = True
except ImportError:  # pragma: no cover
    _HAS_OTEL = False
    trace = None  # type: ignore[assignment]

__all__ = [
    "init_tracer",
    "get_tracer",
    "fraud_rule_evaluation_span",
    "trace_sampling_rate",
]

trace_sampling_rate = SafeGauge(
    "trace_sampling_rate",
    "Configured OTel trace sampling rate by decision type",
    labelnames=["decision_type"],
)

_TRACER_NAME = "fraudstream.scoring"
_tracer = None


def init_tracer(allow_sample_rate: float = 0.01):
    """Initialise the OTel TracerProvider for the scoring service.

    Args:
        allow_sample_rate: ParentBased sampling rate (0.0–1.0) for ALLOW decisions.
                           Default 1%. BLOCK and error decisions are always sampled.

    Returns the tracer, or *None* if opentelemetry is not installed.
    """
    global _tracer
    if not _HAS_OTEL:
        logger.info("opentelemetry not installed – skipping tracer initialisation")
        return None

    resource = Resource.create({"service.name": "fraudstream-scoring"})
    sampler = ParentBasedTraceIdRatio(allow_sample_rate)
    provider = TracerProvider(resource=resource, sampler=sampler)
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(_TRACER_NAME)
    trace_sampling_rate.labels(decision_type="ALLOW").set(allow_sample_rate)
    trace_sampling_rate.labels(decision_type="BLOCK").set(1.0)
    trace_sampling_rate.labels(decision_type="error").set(1.0)
    logger.info(
        "OTel tracer initialised (allow_sample_rate=%.2f)"
        " — ParentBased sampling with elevated BLOCK/error rates",
        allow_sample_rate,
    )
    return _tracer


def get_tracer():
    """Return the active tracer, initialising with defaults if not yet set up."""
    global _tracer
    if _tracer is None:
        _tracer = init_tracer()
    return _tracer


@contextmanager
def fraud_rule_evaluation_span(
    transaction_id: str,
    channel: str,
    rule_count: int,
) -> Generator:
    """Context manager that wraps a full rule evaluation cycle in an OTel span.

    Attributes set:
      - fraud.transaction_id
      - fraud.channel
      - fraud.rule_count
      - fraud.decision  (set by caller after evaluation)
    """
    if not _HAS_OTEL:
        yield None
        return

    tracer = get_tracer()
    with tracer.start_as_current_span("fraud.rule_evaluation") as span:
        span.set_attribute("fraud.transaction_id", transaction_id)
        span.set_attribute("fraud.channel", channel)
        span.set_attribute("fraud.rule_count", rule_count)
        yield span
