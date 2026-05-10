"""Shared OTel tracer factory for all pipeline services.

Single entry point: build_tracer(service_name) reads OTEL_EXPORTER_OTLP_ENDPOINT
and wires OTLP export when set, falling back to a no-op provider otherwise.
Accepts an optional sampler so callers with custom sampling (e.g. scoring) can
still use the same provider setup without duplicating OTLP wiring.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Sampler

logger = logging.getLogger(__name__)


def build_tracer(
    service_name: str,
    *,
    sampler: Sampler | None = None,
) -> trace.Tracer:
    """Create and register an OTel TracerProvider, returning a ready Tracer.

    If OTEL_EXPORTER_OTLP_ENDPOINT is set, attaches a BatchSpanProcessor with
    an OTLP HTTP exporter. Otherwise the provider is no-op (no spans exported).

    Args:
        service_name: Value for the ``service.name`` OTel resource attribute.
        sampler: Optional sampler override (e.g. ParentBasedTraceIdRatio).
                 When None, the SDK default sampler (ALWAYS_ON) is used.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    resource = Resource.create({"service.name": service_name})

    provider_kwargs: dict = {"resource": resource}
    if sampler is not None:
        provider_kwargs["sampler"] = sampler

    provider = TracerProvider(**provider_kwargs)

    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        logger.info(
            "otel_tracer_initialized service=%s endpoint=%s",
            service_name,
            endpoint,
        )
    else:
        logger.info(
            "otel_tracer_noop service=%s reason=OTEL_EXPORTER_OTLP_ENDPOINT_not_set",
            service_name,
        )

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
