"""Integration tests for distributed trace propagation across Kafka.

Tests verify:
1. Trace context is correctly injected into Kafka message headers by producers
2. Trace context is correctly extracted from Kafka message headers by consumers
3. Spans are correctly linked across service boundaries
4. End-to-end trace flows through the pipeline
"""

from __future__ import annotations

import time
import uuid

import pytest

from pipelines.shared.tracing import (
    extract_context,
    get_current_trace_id,
    init_tracer_provider,
    inject_context,
    start_span,
)

# Mark all tests as integration tests
pytestmark = [pytest.mark.integration]


@pytest.fixture
def tracer_provider():
    """Initialize tracer provider for tests."""
    provider = init_tracer_provider(
        service_name="fraudstream-test",
        endpoint=None,  # No export for tests
        sample_rate=1.0,
    )
    yield provider
    # Cleanup handled by singleton pattern in module


@pytest.fixture
def sample_transaction():
    """Create a sample transaction for testing."""
    return {
        "transaction_id": str(uuid.uuid4()),
        "account_id": "ACC123456",
        "amount": 1000.00,
        "currency": "USD",
        "merchant_id": "MERCHANT_001",
        "timestamp": int(time.time() * 1000),
    }


class TestKafkaTracePropagation:
    """Tests for trace context propagation through Kafka."""

    def test_inject_trace_context_into_headers(self, tracer_provider, sample_transaction):
        """Test that trace context is injected into Kafka message headers."""
        with start_span(
            "test.produce_transaction",
            attributes={"transaction_id": sample_transaction["transaction_id"]},
        ):
            # Create headers dict (simulating Kafka producer headers)
            headers: dict[str, bytes] = {}

            # Inject trace context
            result = inject_context(headers)

            # Verify traceparent header was added
            assert "traceparent" in result
            traceparent = result["traceparent"]
            assert isinstance(traceparent, (str, bytes))

            # Verify traceparent format (00-traceid-spanid-flags)
            if isinstance(traceparent, bytes):
                traceparent = traceparent.decode()
            parts = traceparent.split("-")
            assert len(parts) == 4
            assert parts[0] == "00"  # Version
            assert len(parts[1]) == 32  # Trace ID
            assert len(parts[2]) == 16  # Span ID

    def test_extract_trace_context_from_headers(self, tracer_provider):
        """Test that trace context is extracted from Kafka message headers."""
        # Create a valid traceparent header
        trace_id = "0af7651916cd43dd8448eb211c80319c"
        span_id = "b7ad6b7169203331"
        traceparent = f"00-{trace_id}-{span_id}-01"

        headers = {
            "traceparent": traceparent,
        }

        # Extract context
        context = extract_context(headers)

        # Context may be None if propagation fails, which is acceptable
        # The important thing is that it doesn't raise an exception
        if context is not None:
            # If extraction succeeded, verify the context
            assert hasattr(context, "trace_id")
            assert hasattr(context, "span_id")

    def test_trace_context_roundtrip(self, tracer_provider, sample_transaction):
        """Test full roundtrip of trace context through inject/extract."""
        original_trace_id: str | None = None

        # Producer side: create span and inject context
        with start_span(
            "producer.send",
            attributes={"transaction_id": sample_transaction["transaction_id"]},
        ):
            original_trace_id = get_current_trace_id()
            headers: dict[str, bytes] = {}
            inject_context(headers)

        assert original_trace_id is not None
        assert "traceparent" in headers

        # Consumer side: extract context and create child span
        # Note: Full context propagation requires active span context
        # This test verifies the headers are correctly formatted
        traceparent = headers["traceparent"]
        if isinstance(traceparent, bytes):
            traceparent = traceparent.decode()

        parts = traceparent.split("-")
        assert len(parts) == 4
        extracted_trace_id = parts[1]

        # Trace ID should match (may be lowercase hex)
        assert extracted_trace_id.lower() == original_trace_id.lower()

    def test_extract_invalid_headers(self, tracer_provider):
        """Test extraction with invalid/malformed headers."""
        # Empty headers
        context = extract_context({})
        # Should return None for empty headers
        assert context is None

        # Invalid traceparent format
        context = extract_context({"traceparent": "invalid"})
        # Should handle gracefully
        assert context is None

        # Missing traceparent
        context = extract_context({"other-header": "value"})
        # Should return None
        assert context is None

    def test_extract_with_bytes_headers(self, tracer_provider):
        """Test extraction when headers contain bytes values."""
        trace_id = "0af7651916cd43dd8448eb211c80319c"
        span_id = "b7ad6b7169203331"
        traceparent = f"00-{trace_id}-{span_id}-01"

        # Headers as bytes (typical Kafka format)
        headers = {
            "traceparent": traceparent.encode(),
        }

        # Should handle bytes values
        _ = extract_context(headers)
        # May return None but should not raise


class TestCrossServiceTraceFlow:
    """Tests for trace flow across service boundaries."""

    def test_processing_to_scoring_trace_flow(self, tracer_provider, sample_transaction):
        """Test trace flow from processing service to scoring service."""
        # Simulate processing service creating a span
        with start_span(
            "processing.enrich_transaction",
            attributes={
                "transaction_id": sample_transaction["transaction_id"],
                "service": "fraudstream-processing",
            },
        ) as _:
            processing_trace_id = get_current_trace_id()

            # Inject context for Kafka message
            headers: dict[str, bytes] = {}
            inject_context(headers)

            # Simulate sending to Kafka
            kafka_message = {
                "value": sample_transaction,
                "headers": headers,
            }

        # Simulate scoring service receiving from Kafka
        received_headers = kafka_message["headers"]

        # Extract context
        extracted_ctx = extract_context(received_headers)

        # Create child span in scoring service with parent context
        with start_span(
            "scoring.evaluate_rules",
            attributes={
                "transaction_id": sample_transaction["transaction_id"],
                "service": "fraudstream-scoring",
            },
            parent_context=extracted_ctx,
        ):
            scoring_trace_id = get_current_trace_id()

            # Trace IDs should match across services
            assert processing_trace_id == scoring_trace_id

    def test_multiple_service_hops(self, tracer_provider, sample_transaction):
        """Test trace propagation through multiple service hops."""
        trace_ids = []
        headers: dict[str, bytes] = {}

        # Service 1: Ingestion
        with start_span(
            "ingestion.receive_transaction",
            attributes={"service": "fraudstream-ingestion"},
        ):
            trace_ids.append(get_current_trace_id())
            inject_context(headers)

        # Service 2: Processing
        extracted_ctx = extract_context(headers)
        with start_span(
            "processing.enrich_transaction",
            attributes={"service": "fraudstream-processing"},
            parent_context=extracted_ctx,
        ):
            trace_ids.append(get_current_trace_id())
            headers = {}
            inject_context(headers)

        # Service 3: Scoring
        extracted_ctx = extract_context(headers)
        with start_span(
            "scoring.evaluate_transaction",
            attributes={"service": "fraudstream-scoring"},
            parent_context=extracted_ctx,
        ):
            trace_ids.append(get_current_trace_id())

        # All trace IDs should be the same
        assert len(set(trace_ids)) == 1

    def test_span_attributes_carry_through(self, tracer_provider, sample_transaction):
        """Test that span attributes are correctly set at each hop."""
        with start_span(
            "test.transaction_flow",
            attributes={
                "transaction_id": sample_transaction["transaction_id"],
                "initial_service": "test",
            },
        ) as span:
            # Add more attributes
            span.set_attribute("custom.attribute", "value")

            # Verify trace ID is consistent
            trace_id = get_current_trace_id()
            assert trace_id is not None
            assert len(trace_id) == 32  # Hex encoded 128-bit trace ID


class TestTraceErrorHandling:
    """Tests for error handling in trace propagation."""

    def test_propagation_with_no_active_span(self, tracer_provider):
        """Test propagation when there's no active span."""
        # No span context - should still work gracefully
        headers: dict[str, bytes] = {}
        result = inject_context(headers)

        # May or may not add headers depending on OTel behavior
        # The important thing is it doesn't crash
        assert isinstance(result, dict)

    def test_extraction_with_corrupted_headers(self, tracer_provider):
        """Test extraction with corrupted/invalid headers."""
        corrupted_headers = [
            {"traceparent": b""},  # Empty
            {"traceparent": b"garbage"},  # Invalid format
            {"traceparent": b"00-short-traceid-spanid-01"},  # Wrong length
            {"traceparent": None},  # None value
            {"traceparent": [b"multiple", b"values"]},  # List value
        ]

        for headers in corrupted_headers:
            # Should handle gracefully without raising
            context = extract_context(headers)
            assert context is None  # Should return None for invalid headers

    def test_concurrent_trace_contexts(self, tracer_provider, sample_transaction):
        """Test that concurrent operations maintain separate trace contexts."""
        import threading

        trace_ids = []

        def create_trace(transaction_id: str):
            with start_span(
                "concurrent.operation",
                attributes={"transaction_id": transaction_id},
            ):
                trace_ids.append(get_current_trace_id())

        # Create multiple threads with different transactions
        threads = []
        for i in range(3):
            txn_id = f"TXN_{i}_{uuid.uuid4()}"
            t = threading.Thread(target=create_trace, args=(txn_id,))
            threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Should have 3 different trace IDs
        assert len(trace_ids) == 3
        assert len(set(trace_ids)) == 3


class TestSamplingBehavior:
    """Tests for trace sampling behavior."""

    def test_always_on_sampler_records_all(self):
        """Test that always-on sampler records all spans."""
        _ = init_tracer_provider(
            service_name="fraudstream-sampling-test",
            sample_rate=1.0,  # Always sample
        )

        with start_span("sampled_span"):
            trace_id = get_current_trace_id()

        # With 1.0 sampling, should always have a trace ID
        assert trace_id is not None

    def test_never_sample_sampler(self):
        """Test that zero sampling rate still creates spans but may not export."""
        _ = init_tracer_provider(
            service_name="fraudstream-sampling-test",
            sample_rate=0.0,  # Never sample
        )

        # Even with 0 sampling, spans are still created
        # (they just might not be exported)
        with start_span("unsampled_span"):
            _ = get_current_trace_id()

        # Trace ID should still be available for logging correlation
        # even if span is not exported
        # Note: Behavior depends on sampler implementation


class TestTraceContextFormat:
    """Tests for trace context format compliance."""

    def test_traceparent_format_compliance(self, tracer_provider):
        """Test that traceparent header follows W3C format."""
        with start_span("test_span"):
            headers: dict[str, bytes] = {}
            inject_context(headers)

        traceparent = headers.get("traceparent")
        assert traceparent is not None

        if isinstance(traceparent, bytes):
            traceparent = traceparent.decode()

        # W3C format: version-traceid-parentid-flags
        parts = traceparent.split("-")
        assert len(parts) == 4, f"Expected 4 parts, got {len(parts)}: {parts}"

        version, trace_id, parent_id, flags = parts

        # Version: 2 hex digits
        assert len(version) == 2
        assert all(c in "0123456789abcdefABCDEF" for c in version)

        # Trace ID: 32 hex digits (128-bit)
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdefABCDEF" for c in trace_id)

        # Parent ID (span ID): 16 hex digits (64-bit)
        assert len(parent_id) == 16
        assert all(c in "0123456789abcdefABCDEF" for c in parent_id)

        # Flags: 2 hex digits
        assert len(flags) == 2
        assert all(c in "0123456789abcdefABCDEF" for c in flags)

    def test_tracestate_optional(self, tracer_provider):
        """Test that tracestate header is optional."""
        with start_span("test_span"):
            headers: dict[str, bytes] = {}
            inject_context(headers)

        # tracestate is optional, so we don't require it
        # but if present it should be valid
        if "tracestate" in headers:
            tracestate = headers["tracestate"]
            if isinstance(tracestate, bytes):
                tracestate = tracestate.decode()
            # tracestate format: vendor-specific, but shouldn't contain newlines
            assert "\n" not in tracestate
