"""OpenTelemetry distributed tracing utilities for fraudstream pipelines.

Provides:
- TracerProvider configuration with OTLP export
- KafkaPropagator for trace context propagation in message headers
- @trace decorator for automatic span creation
- get_current_trace_id() helper for logging correlation
- inject_context/extract_context for Kafka message handling
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar

from opentelemetry import propagate
from opentelemetry import trace as trace_api
from opentelemetry.propagators.textmap import CarrierT, TextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio, Sampler
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Global tracer cache
_tracers: dict[str, trace_api.Tracer] = {}
_provider: TracerProvider | None = None

# Kafka header keys for trace context
TRACEPARENT_KEY = "traceparent"
TRACESTATE_KEY = "tracestate"


class KafkaPropagator(TextMapPropagator):
    """TextMapPropagator for Kafka message headers.

    Implements the W3C Trace Context specification for propagating
    trace context through Kafka message headers.
    """

    def fields(self) -> set[str]:
        """Return the set of field names used by this propagator.

        Returns:
            Set of header field names
        """
        return {TRACEPARENT_KEY, TRACESTATE_KEY}

    def extract(
        self,
        carrier: CarrierT,
        getter: Callable[[CarrierT, str], list[str] | None] | None = None,
    ) -> trace_api.SpanContext:
        """Extract trace context from Kafka message headers."""
        if getter is None:

            def getter(carrier: CarrierT, key: str) -> list[str] | None:
                val = carrier.get(key)
                if val is None:
                    return None
                if isinstance(val, list):
                    return [v.decode() if isinstance(v, bytes) else v for v in val]
                return [val.decode() if isinstance(val, bytes) else val]

        # Use the standard TraceContextTextMapPropagator for actual extraction
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        return TraceContextTextMapPropagator().extract(carrier, getter)

    def inject(
        self,
        carrier: CarrierT,
        context: Any | None = None,
        setter: Any | None = None,
    ) -> None:
        """Inject trace context into Kafka message headers."""
        # Use the standard TraceContextTextMapPropagator for actual injection
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        TraceContextTextMapPropagator().inject(carrier, context, setter)


def setup_kafka_propagation() -> None:
    """Register the KafkaPropagator with the global propagator."""
    propagate.set_global_textmap(KafkaPropagator())


def init_tracer_provider(
    service_name: str,
    endpoint: str | None = None,
    sample_rate: float = 1.0,
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Initialize the global TracerProvider.

    Args:
        service_name: Name of the service for resource attribution
        endpoint: OTLP HTTP endpoint URL (defaults to OTEL_EXPORTER_OTLP_ENDPOINT env var)
        sample_rate: Sampling rate from 0.0 to 1.0 (1.0 = always sample)
        exporter: Optional custom span exporter (defaults to OTLPSpanExporter)

    Returns:
        Configured TracerProvider instance
    """
    global _provider

    if _provider is not None:
        return _provider

    # Use environment variable or default endpoint
    if endpoint is None:
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    # Create resource with service name
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("SERVICE_VERSION", "0.1.0"),
            "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "development"),
        }
    )

    # Configure sampler
    sampler: Sampler = ParentBasedTraceIdRatio(sample_rate)

    # Create provider
    _provider = TracerProvider(resource=resource, sampler=sampler)

    # Add exporter if endpoint is configured
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            span_exporter = exporter or OTLPSpanExporter(endpoint=endpoint)
            _provider.add_span_processor(BatchSpanProcessor(span_exporter))
            logger.info(
                "TracerProvider initialized with OTLP export",
                extra={
                    "service_name": service_name,
                    "endpoint": endpoint,
                    "sample_rate": sample_rate,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to configure OTLP exporter: {e}")
    else:
        logger.info(
            "TracerProvider initialized (no exporter)",
            extra={"service_name": service_name, "sample_rate": sample_rate},
        )

    trace_api.set_tracer_provider(_provider)
    setup_kafka_propagation()

    return _provider


def get_tracer(name: str) -> trace_api.Tracer:
    """Get or create a tracer for the given name.

    Args:
        name: Tracer name (typically __name__)

    Returns:
        Tracer instance
    """
    if name not in _tracers:
        _tracers[name] = trace_api.get_tracer(name)
    return _tracers[name]


def get_current_trace_id() -> str | None:
    """Get the current span's trace ID for logging correlation.

    Returns:
        Hex string of trace ID, or None if no active span
    """
    try:
        span = trace_api.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx and ctx.is_valid:
                return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return None


def get_current_span_context() -> trace_api.SpanContext | None:
    """Get the current span context for propagation.

    Returns:
        SpanContext of current span, or None if no active span
    """
    try:
        span = trace_api.get_current_span()
        if span:
            ctx = span.get_span_context()
            if ctx and ctx.is_valid:
                return ctx
    except Exception:
        pass
    return None


def trace_span(
    span_name: str | None = None,
    attributes: dict[str, Any] | None = None,
    set_error_on_exception: bool = True,
) -> Callable[[F], F]:
    """Decorator to create a span for a function.

    Args:
        span_name: Name for the span (defaults to function name)
        attributes: Static attributes to set on the span
        set_error_on_exception: Whether to mark span as error on exception

    Returns:
        Decorated function
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer(func.__module__)
            name = span_name or func.__name__

            with tracer.start_as_current_span(name) as span:
                # Set static attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                # Set function attributes
                span.set_attribute("function.name", func.__name__)
                span.set_attribute("function.module", func.__module__)

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    if set_error_on_exception:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        span.record_exception(e)
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def start_span(
    name: str,
    tracer_name: str = "pipelines.shared.tracing",
    attributes: dict[str, Any] | None = None,
    parent_context: trace_api.SpanContext | None = None,
):
    """Context manager for creating a span.

    Args:
        name: Span name
        tracer_name: Tracer name to use
        attributes: Attributes to set on the span
        parent_context: Optional parent span context for linking

    Yields:
        The created span
    """
    tracer = get_tracer(tracer_name)

    ctx = None
    if parent_context:
        from opentelemetry.trace import NonRecordingSpan, set_span_in_context

        ctx = set_span_in_context(NonRecordingSpan(parent_context))

    with tracer.start_as_current_span(name, context=ctx) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


def inject_context(carrier: dict[str, Any]) -> dict[str, Any]:
    """Inject current trace context into a carrier dict for Kafka headers.

    Args:
        carrier: Dict to inject context into (modified in place)

    Returns:
        The carrier dict with trace context injected
    """
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    TraceContextTextMapPropagator().inject(carrier)
    return carrier


def extract_context(carrier: dict[str, Any]) -> trace_api.SpanContext | None:
    """Extract trace context from Kafka message headers.

    Args:
        carrier: Dict of Kafka headers

    Returns:
        Extracted SpanContext, or None if no valid context found
    """
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        context = TraceContextTextMapPropagator().extract(carrier)
        if context:
            span = trace_api.get_current_span(context)
            if span:
                ctx = span.get_span_context()
                if ctx and ctx.is_valid:
                    return ctx
    except Exception as e:
        logger.debug(f"Failed to extract trace context: {e}")
    return None


def set_span_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span.

    Args:
        key: Attribute key
        value: Attribute value
    """
    try:
        span = trace_api.get_current_span()
        if span and span.is_recording():
            span.set_attribute(key, value)
    except Exception:
        pass


def record_exception(exception: Exception) -> None:
    """Record an exception on the current span.

    Args:
        exception: Exception to record
    """
    try:
        span = trace_api.get_current_span()
        if span and span.is_recording():
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))
    except Exception:
        pass


def add_event(name: str, attributes: dict[str, Any] | None = None) -> None:
    """Add an event to the current span.

    Args:
        name: Event name
        attributes: Event attributes
    """
    try:
        span = trace_api.get_current_span()
        if span and span.is_recording():
            span.add_event(name, attributes)
    except Exception:
        pass
