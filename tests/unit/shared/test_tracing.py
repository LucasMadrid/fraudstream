"""Unit tests for pipelines.shared.tracing module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Import opentelemetry modules directly for patching
from opentelemetry import trace as trace_api

from pipelines.shared import tracing as tracing_module
from pipelines.shared.tracing import (
    KafkaPropagator,
    add_event,
    extract_context,
    get_current_span_context,
    get_current_trace_id,
    get_tracer,
    init_tracer_provider,
    inject_context,
    record_exception,
    set_span_attribute,
    start_span,
    trace_span,
)


class TestKafkaPropagator:
    """Tests for KafkaPropagator class."""

    def test_fields_returns_expected_headers(self):
        """Test that fields() returns traceparent and tracestate."""
        propagator = KafkaPropagator()
        fields = propagator.fields()

        assert "traceparent" in fields
        assert "tracestate" in fields

    def test_extract_with_bytes_values(self):
        """Test extracting trace context from Kafka headers with bytes values."""
        propagator = KafkaPropagator()
        carrier = {
            "traceparent": [b"00-12345678901234567890123456789012-1234567890123456-01"],
        }

        with patch.object(tracing_module.propagate, "get_global_textmap") as mock_get:
            mock_propagator = MagicMock()
            mock_get.return_value = mock_propagator

            propagator.extract(carrier)

            # Verify extract was called with carrier
            call_args = mock_propagator.extract.call_args
            assert call_args[0][0] == carrier

    def test_extract_with_string_values(self):
        """Test extracting trace context from Kafka headers with string values."""
        propagator = KafkaPropagator()
        carrier = {
            "traceparent": ["00-12345678901234567890123456789012-1234567890123456-01"],
        }

        with patch.object(tracing_module.propagate, "get_global_textmap") as mock_get:
            mock_propagator = MagicMock()
            mock_get.return_value = mock_propagator

            propagator.extract(carrier)

            call_args = mock_propagator.extract.call_args
            assert call_args[0][0] == carrier

    def test_inject_with_bytes_carrier(self):
        """Test injecting trace context into a carrier with bytes encoding."""
        propagator = KafkaPropagator()
        carrier: dict[str, bytes] = {}

        with patch.object(tracing_module.propagate, "get_global_textmap") as mock_get:
            mock_propagator = MagicMock()
            mock_get.return_value = mock_propagator

            propagator.inject(carrier)

            # Verify inject was called with carrier
            call_args = mock_propagator.inject.call_args
            assert call_args[0][0] == carrier

    def test_inject_with_dict_carrier(self):
        """Test injecting trace context into a dict carrier."""
        propagator = KafkaPropagator()
        carrier: dict[str, str] = {}

        with patch.object(tracing_module.propagate, "get_global_textmap") as mock_get:
            mock_propagator = MagicMock()
            mock_get.return_value = mock_propagator

            propagator.inject(carrier)

            call_args = mock_propagator.inject.call_args
            assert call_args[0][0] == carrier


class TestInitTracerProvider:
    """Tests for init_tracer_provider function."""

    def test_init_without_endpoint(self):
        """Test initialization without OTLP endpoint."""
        # Reset provider to allow re-initialization
        tracing_module._provider = None

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(tracing_module, "TracerProvider") as mock_provider:
                with patch.object(trace_api, "set_tracer_provider"):
                    with patch.object(tracing_module, "setup_kafka_propagation"):
                        init_tracer_provider("test-service")

                        # Verify provider was created with resource
                        call_kwargs = mock_provider.call_args[1]
                        assert "resource" in call_kwargs

    def test_init_with_endpoint(self):
        """Test initialization with OTLP endpoint."""
        tracing_module._provider = None

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}):
            with patch.object(tracing_module, "TracerProvider"):
                with patch.object(trace_api, "set_tracer_provider"):
                    with patch.object(tracing_module, "setup_kafka_propagation"):
                        with patch(
                            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
                        ) as mock_exporter:
                            mock_instance = MagicMock()
                            mock_exporter.return_value = mock_instance

                            provider = init_tracer_provider("test-service")

                            assert provider is not None

    def test_init_with_custom_sample_rate(self):
        """Test initialization with custom sample rate."""
        tracing_module._provider = None

        with patch.object(tracing_module, "TracerProvider") as mock_provider:
            with patch.object(trace_api, "set_tracer_provider"):
                with patch.object(tracing_module, "setup_kafka_propagation"):
                    init_tracer_provider("test-service", sample_rate=0.5)

                    call_kwargs = mock_provider.call_args[1]
                    assert "sampler" in call_kwargs

    def test_singleton_provider(self):
        """Test that provider is only initialized once."""
        tracing_module._provider = None

        with patch.object(tracing_module, "TracerProvider") as mock_provider:
            with patch.object(trace_api, "set_tracer_provider"):
                with patch.object(tracing_module, "setup_kafka_propagation"):
                    _ = init_tracer_provider("test-service")
                    _ = init_tracer_provider("test-service")

                    # Provider should be created only once
                    mock_provider.assert_called_once()


class TestGetTracer:
    """Tests for get_tracer function."""

    def test_get_tracer_caches_instances(self):
        """Test that tracers are cached by name."""
        # Clear tracer cache
        tracing_module._tracers.clear()

        with patch.object(trace_api, "get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_get_tracer.return_value = mock_tracer

            tracer1 = get_tracer("test.module")
            tracer2 = get_tracer("test.module")

            # Should only call get_tracer once for same name
            mock_get_tracer.assert_called_once_with("test.module")
            assert tracer1 is tracer2

    def test_get_tracer_different_names(self):
        """Test getting tracers with different names."""
        # Clear tracer cache
        tracing_module._tracers.clear()

        with patch.object(trace_api, "get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_get_tracer.return_value = mock_tracer

            get_tracer("module1")
            get_tracer("module2")

            # Should call get_tracer for each unique name
            assert mock_get_tracer.call_count == 2


class TestGetCurrentTraceId:
    """Tests for get_current_trace_id function."""

    def test_get_trace_id_with_active_span(self):
        """Test getting trace ID when there's an active span."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_span.get_span_context.return_value = MagicMock(
                trace_id=0x12345678901234567890123456789012,
                is_valid=True,
            )
            mock_get.return_value = mock_span

            trace_id = get_current_trace_id()

            assert trace_id == "12345678901234567890123456789012"

    def test_get_trace_id_no_span(self):
        """Test getting trace ID when there's no active span."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_get.return_value = None

            trace_id = get_current_trace_id()

            assert trace_id is None

    def test_get_trace_id_not_recording(self):
        """Test getting trace ID when span is not recording."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = False
            mock_get.return_value = mock_span

            trace_id = get_current_trace_id()

            assert trace_id is None

    def test_get_trace_id_exception_handling(self):
        """Test that exceptions are handled gracefully."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_get.side_effect = RuntimeError("Test error")

            trace_id = get_current_trace_id()

            assert trace_id is None


class TestContextPropagation:
    """Tests for context propagation functions."""

    def test_inject_context(self):
        """Test injecting context into carrier."""
        carrier: dict[str, str] = {}

        with patch.object(KafkaPropagator, "inject") as mock_inject:
            result = inject_context(carrier)

            mock_inject.assert_called_once()
            assert result is carrier

    def test_extract_context(self):
        """Test extracting context from carrier."""
        carrier = {"traceparent": "test-value"}

        with patch.object(KafkaPropagator, "extract") as mock_extract:
            mock_span = MagicMock()
            mock_span.get_span_context.return_value = MagicMock(is_valid=True)
            mock_context = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_span)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_extract.return_value = mock_context

            # The extract function should work with the mocked context
            with patch.object(trace_api, "get_current_span") as mock_get:
                mock_get.return_value = mock_span
                _ = extract_context(carrier)

                mock_extract.assert_called_once_with(carrier)

    def test_extract_context_no_valid_context(self):
        """Test extracting context when no valid context exists."""
        carrier = {}

        with patch.object(KafkaPropagator, "extract") as mock_extract:
            mock_extract.side_effect = Exception("No context")

            result = extract_context(carrier)

            assert result is None


class TestSpanOperations:
    """Tests for span operation functions."""

    def test_set_span_attribute(self):
        """Test setting span attribute."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_get.return_value = mock_span

            set_span_attribute("test.key", "test-value")

            mock_span.set_attribute.assert_called_once_with("test.key", "test-value")

    def test_set_span_attribute_no_span(self):
        """Test setting attribute when no span is active."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_get.return_value = None

            # Should not raise exception
            set_span_attribute("test.key", "test-value")

    def test_record_exception(self):
        """Test recording exception on span."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_get.return_value = mock_span

            test_exception = ValueError("Test error")
            record_exception(test_exception)

            mock_span.record_exception.assert_called_once_with(test_exception)

    def test_add_event(self):
        """Test adding event to span."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_get.return_value = mock_span

            add_event("test_event", {"key": "value"})

            mock_span.add_event.assert_called_once_with("test_event", {"key": "value"})


class TestTraceDecorator:
    """Tests for trace decorator."""

    def test_trace_decorator_creates_span(self):
        """Test that trace decorator creates a span."""
        with patch.object(tracing_module, "get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
                return_value=mock_span
            )
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_tracer.return_value = mock_tracer

            @trace_span(span_name="test_operation")
            def test_function():
                return "result"

            result = test_function()

            assert result == "result"
            mock_tracer.start_as_current_span.assert_called_once_with("test_operation")

    def test_trace_decorator_with_exception(self):
        """Test that trace decorator handles exceptions."""
        with patch.object(tracing_module, "get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
                return_value=mock_span
            )
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_tracer.return_value = mock_tracer

            @trace_span(span_name="test_operation")
            def test_function():
                raise ValueError("Test error")

            with pytest.raises(ValueError):
                test_function()

            # Span should have error status set
            mock_span.set_status.assert_called()


class TestStartSpanContextManager:
    """Tests for start_span context manager."""

    def test_start_span_creates_span(self):
        """Test that start_span creates a span."""
        with patch.object(tracing_module, "get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
                return_value=mock_span
            )
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_tracer.return_value = mock_tracer

            with start_span("test_span", attributes={"key": "value"}) as span:
                assert span is mock_span

            mock_tracer.start_as_current_span.assert_called_once()


class TestGetCurrentSpanContext:
    """Tests for get_current_span_context function."""

    def test_get_span_context_with_valid_span(self):
        """Test getting span context with valid active span."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_context = MagicMock(is_valid=True)
            mock_span.get_span_context.return_value = mock_context
            mock_get.return_value = mock_span

            result = get_current_span_context()

            assert result is mock_context

    def test_get_span_context_no_span(self):
        """Test getting span context when no span is active."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_get.return_value = None

            result = get_current_span_context()

            assert result is None

    def test_get_span_context_invalid_context(self):
        """Test getting span context when context is invalid."""
        with patch.object(trace_api, "get_current_span") as mock_get:
            mock_span = MagicMock()
            mock_context = MagicMock(is_valid=False)
            mock_span.get_span_context.return_value = mock_context
            mock_get.return_value = mock_span

            result = get_current_span_context()

            assert result is None
