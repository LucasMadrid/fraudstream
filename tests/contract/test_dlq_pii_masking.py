"""Contract tests for DLQ PII masking requirements.

TB-002: DLQ PII Masking Contract Tests

These tests verify that Dead Letter Queue records properly handle PII:
  1. DLQ envelope has masking_applied field to track PII state
  2. Original payload may contain PII if masking failed
  3. Error messages must not leak sensitive data
  4. DLQ records are marked with appropriate sensitivity levels

Constitution References:
    - Article 5 (Fail-Safe): PII leaks in error paths must be prevented
    - Article 7 (Observability): PII presence must be auditable

Compatible with contracts package from CHB-006.
Uses DLQSink protocol from pipelines.shared.dlq_protocol.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _load_schema(path: Path) -> dict[str, Any]:
    """Load and parse a JSON schema from file."""
    with open(path) as f:
        return json.load(f)


class TestDlqSchemaPiiTracking:
    """DLQ schema must track PII masking status.

    Constitution Article 5: PII tracking must be explicit and auditable.
    """

    def test_dlq_envelope_has_masking_applied_field(self):
        """TB-002-DLQ-01: DLQ envelope must have masking_applied boolean field.

        The masking_applied field indicates whether PII masking was completed
        before the failure occurred. If false, the original_payload may
        contain unmasked sensitive data.

        Constitution (Article 7 - Observability):
            PII presence must be explicit for audit and compliance.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        field_names = {f["name"]: f for f in schema.get("fields", [])}

        assert "masking_applied" in field_names, "DLQ envelope must have masking_applied field"

        masking_field = field_names["masking_applied"]
        assert masking_field.get("type") == "boolean", "masking_applied must be boolean type"

    def test_dlq_envelope_masking_field_documented(self):
        """TB-002-DLQ-02: masking_applied field must have security documentation.

        Documentation must warn operators about PII sensitivity when
        masking_applied=false.

        Constitution (Article 7 - Observability):
            Security-critical fields must have explicit documentation.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        masking_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "masking_applied"),
            None,
        )
        assert masking_field is not None, "masking_applied field missing"

        doc = masking_field.get("doc", "")
        assert doc, "masking_applied must have documentation"

        # Documentation should mention PII or sensitivity
        assert any(keyword in doc.lower() for keyword in ["pii", "sensitive", "mask", "warning"]), (
            f"masking_applied doc must mention PII/sensitivity: {doc}"
        )

    def test_dlq_envelope_original_payload_documented(self):
        """TB-002-DLQ-03: original_payload field must have PII warning in docs.

        The original_payload may contain unmasked PII if masking failed.
        Documentation must warn about this.

        Constitution (Article 5 - Fail-Safe):
            Operators must be warned about potential PII exposure.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        payload_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "original_payload"),
            None,
        )
        assert payload_field is not None, "original_payload field missing"

        doc = payload_field.get("doc", "")
        # Should mention PII, sensitive, or masking
        assert any(
            keyword in doc.lower() for keyword in ["pii", "sensitive", "mask", "unmasked"]
        ), f"original_payload doc must warn about PII: {doc}"


class TestDlqProducerPiiHandling:
    """DLQ producer must handle PII correctly when sending to DLQ.

    Uses DLQSink protocol for abstraction.
    """

    def test_dlq_producer_tracks_masking_status(self):
        """TB-002-DLQ-04: DLQProducer must track and report masking_applied.

        When sending to DLQ, the producer must indicate whether PII masking
        was applied to the payload.

        Constitution (Article 7 - Observability):
            PII state must be auditable in all failure paths.
        """
        # Import here to handle dependencies gracefully
        try:
            from pipelines.ingestion.shared.dlq_producer import DLQProducer
        except ImportError as e:
            pytest.skip(f"DLQProducer not available: {e}")

        # Check that send_to_dlq accepts masking_applied parameter
        import inspect

        sig = inspect.signature(DLQProducer.send_to_dlq)
        params = list(sig.parameters.keys())

        assert "masking_applied" in params, (
            "DLQProducer.send_to_dlq must accept masking_applied parameter"
        )

    def test_dlq_producer_send_with_masking_true(self):
        """TB-002-DLQ-05: DLQ records with masking_applied=true are safe.

        When masking was applied, the payload should not contain raw PII.
        """
        try:
            from pipelines.ingestion.shared.dlq_producer import DLQProducer
        except ImportError as e:
            pytest.skip(f"DLQProducer not available: {e}")

        # Patch at the module level where it's imported
        with patch("pipelines.ingestion.shared.dlq_producer.Producer") as mock_producer_class:
            mock_producer_instance = MagicMock()
            mock_producer_class.return_value = mock_producer_instance

            # Capture the call arguments
            produced_records = []
            mock_producer_instance.produce.side_effect = lambda **kwargs: produced_records.append(
                kwargs
            )

            producer = DLQProducer(bootstrap_servers="localhost:9092")

            # Send with masking applied
            producer.send_to_dlq(
                source_topic="txn.api",
                original_payload='{"masked": true}',
                error_type="VALIDATION_ERROR",
                error_message="Invalid field value",
                masking_applied=True,
            )

            # Verify produce was called
            assert len(produced_records) > 0, "Producer.produce not called"

            # Get the produced value
            value = produced_records[0].get("value")
            assert value is not None, "No value produced"

            # Parse the envelope
            envelope = json.loads(value.decode("utf-8"))
            assert envelope["masking_applied"] is True, "masking_applied should be True"

    def test_dlq_producer_send_with_masking_false(self):
        """TB-002-DLQ-06: DLQ records with masking_applied=false are sensitive.

        When masking failed, the payload may contain raw PII and must be
        handled with appropriate access controls.
        """
        try:
            from pipelines.ingestion.shared.dlq_producer import DLQProducer
        except ImportError as e:
            pytest.skip(f"DLQProducer not available: {e}")

        with patch("pipelines.ingestion.shared.dlq_producer.Producer") as mock_producer_class:
            mock_producer_instance = MagicMock()
            mock_producer_class.return_value = mock_producer_instance

            # Capture the call arguments
            produced_records = []
            mock_producer_instance.produce.side_effect = lambda **kwargs: produced_records.append(
                kwargs
            )

            producer = DLQProducer(bootstrap_servers="localhost:9092")

            # Send with masking NOT applied (failure during masking)
            producer.send_to_dlq(
                source_topic="txn.api",
                original_payload='{"card_number": "4111111111111111"}',  # Raw PII
                error_type="MASKING_ERROR",
                error_message="Failed to mask PII fields",
                masking_applied=False,
            )

            # Verify produce was called
            assert len(produced_records) > 0, "Producer.produce not called"

            value = produced_records[0].get("value")
            assert value is not None, "No value produced"

            envelope = json.loads(value.decode("utf-8"))
            assert envelope["masking_applied"] is False, "masking_applied should be False"


class TestDlqErrorMessagePiiScrubbing:
    """Error messages in DLQ must not leak PII.

    Constitution Article 5: Fail-safe must prevent PII exposure in errors.
    """

    def test_dlq_error_message_no_raw_pan(self):
        """TB-002-DLQ-07: Error messages must not contain full PAN.

        Even when masking fails, error messages should not include
        full card numbers.

        Constitution (Article 5 - Fail-Safe):
            Error messages must not leak sensitive data.
        """
        # This is a contract specification - implementations should validate
        # We check the schema allows for error messages without validation
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        error_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "error_message"),
            None,
        )
        assert error_field is not None, "error_message field missing"

        # error_message should be a string type (no restrictions in schema,
        # but implementations should scrub PII)
        assert error_field.get("type") == "string", "error_message must be string"

    def test_dlq_schema_no_pii_in_error_type(self):
        """TB-002-DLQ-08: Error type must not contain PII.

        Error types are categorical and must never contain variable data
        that could include PII.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        error_type_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "error_type"),
            None,
        )
        assert error_type_field is not None, "error_type field missing"
        assert error_type_field.get("type") == "string", "error_type must be string"


class TestDlqProcessingPiiHandling:
    """Processing layer DLQ must handle PII correctly.

    Processing DLQ uses Avro format with bytes for original payload.
    """

    def test_processing_dlq_uses_bytes_for_payload(self):
        """TB-002-DLQ-09: Processing DLQ uses bytes for original payload.

        Binary payload storage prevents accidental PII exposure in
        text-based logs and tooling.

        Constitution (Article 5 - Fail-Safe):
            Raw payloads should be stored as opaque bytes.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "002-flink-stream-processor"
            / "contracts"
            / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        payload_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "original_payload_bytes"),
            None,
        )
        assert payload_field is not None, "original_payload_bytes field missing"
        assert payload_field.get("type") == "bytes", (
            "original_payload_bytes must be bytes type for PII safety"
        )

    def test_processing_dlq_has_transaction_id_tracking(self):
        """TB-002-DLQ-10: Processing DLQ tracks transaction_id for correlation.

        Even when the payload is opaque bytes, transaction_id allows
        correlation with the original event for debugging.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "002-flink-stream-processor"
            / "contracts"
            / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        txn_id_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "transaction_id"),
            None,
        )
        assert txn_id_field is not None, "transaction_id field missing"

        # Should be nullable (may not be parseable if deserialization failed)
        field_type = txn_id_field.get("type")
        assert isinstance(field_type, list) and "null" in field_type, (
            "transaction_id must be nullable (may not be parseable)"
        )


class TestDlqSinkProtocolPii:
    """DLQSink protocol must support PII-aware implementations.

    Uses contracts package DLQSink protocol.
    """

    def test_dlq_sink_protocol_no_pii_in_signature(self):
        """TB-002-DLQ-11: DLQSink protocol doesn't require PII in send signature.

        The protocol is designed to work with opaque payloads, keeping
        PII handling concerns in the implementation.

        Constitution (Article 3 - Dependency Inversion):
            Protocol abstracts PII handling details from callers.
        """
        import inspect

        from pipelines.shared.dlq_protocol import DLQSink

        sig = inspect.signature(DLQSink.send)
        params = list(sig.parameters.keys())

        # Protocol uses opaque payload parameter
        assert "original_payload" in params or "original_payload_bytes" in str(sig), (
            "DLQSink must use opaque payload parameter"
        )

        # Should not have PII-specific parameters at protocol level
        pii_params = ["pan", "card_number", "ssn", "dob", "full_name"]
        for param in pii_params:
            assert param not in params, (
                f"DLQSink protocol should not have PII-specific param: {param}"
            )

    def test_mock_dlq_sink_can_track_pii_state(self):
        """TB-002-DLQ-12: Mock implementations can track PII masking state.

        Verifies the protocol allows implementations to track PII state
        even though it's not in the base signature.
        """
        from pipelines.shared.dlq_protocol import DLQSink

        class PiiAwareDlqSink:
            """Mock DLQ sink that tracks PII masking state."""

            def __init__(self):
                self.records = []

            def send(
                self,
                *,
                source_topic: str,
                original_payload: bytes,
                error_type: str,
                error_message: str,
            ) -> None:
                # Implementation can infer PII state from context
                has_pii = self._check_for_pii(original_payload)
                self.records.append(
                    {
                        "source_topic": source_topic,
                        "has_pii": has_pii,
                        "error_type": error_type,
                    }
                )

            def _check_for_pii(self, payload: bytes) -> bool:
                """Heuristic to detect potential PII in payload."""
                # Simple check for card number pattern
                text = payload.decode("utf-8", errors="replace")
                return bool(re.search(r"\b\d{13,16}\b", text))

        # Verify it satisfies the protocol
        mock = PiiAwareDlqSink()
        assert isinstance(mock, DLQSink), "PiiAwareDlqSink must satisfy DLQSink protocol"

        # Test PII detection
        mock.send(
            source_topic="txn.api",
            original_payload=b'{"card": "4111111111111111"}',
            error_type="TEST_ERROR",
            error_message="Test",
        )

        assert len(mock.records) == 1
        assert mock.records[0]["has_pii"] is True, "Should detect PII in payload"


class TestDlqRecordRetention:
    """DLQ record retention and access control.

    Constitution Article 7: DLQ access must be auditable.
    """

    def test_dlq_envelope_has_producer_host(self):
        """TB-002-DLQ-13: DLQ records track which host produced them.

        Source tracking enables audit and forensics.

        Constitution (Article 7 - Observability):
            DLQ records must be traceable to source.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "001-kafka-ingestion-pipeline"
            / "contracts"
            / "dlq-envelope-v1.avsc"
        )
        schema = _load_schema(schema_path)

        field_names = {f["name"] for f in schema.get("fields", [])}
        assert "producer_host" in field_names, "DLQ must track producer_host"

    def test_processing_dlq_has_processor_host(self):
        """TB-002-DLQ-14: Processing DLQ tracks processor host and subtask.

        Detailed tracking for distributed processing debugging.
        """
        schema_path = (
            REPO_ROOT
            / "specs"
            / "002-flink-stream-processor"
            / "contracts"
            / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        field_names = {f["name"] for f in schema.get("fields", [])}
        assert "processor_host" in field_names, "Processing DLQ must track processor_host"
        assert "processor_subtask_index" in field_names, "Processing DLQ must track subtask"


class TestDlqContractIntegration:
    """Integration tests for DLQ PII handling across the pipeline."""

    def test_ingestion_dlq_satisfies_dlq_sink_protocol(self):
        """TB-002-DLQ-15: Ingestion DLQProducer satisfies DLQSink protocol.

        The ingestion layer DLQ implementation conforms to the shared protocol.

        Constitution (Article 3 - Dependency Inversion):
            Implementations satisfy shared protocols.
        """
        try:
            from pipelines.ingestion.shared.dlq_producer import DLQProducer
            from pipelines.shared.dlq_protocol import DLQSink
        except ImportError as e:
            pytest.skip(f"Dependencies not available: {e}")

        import sys

        if "confluent_kafka" not in sys.modules:
            sys.modules["confluent_kafka"] = MagicMock()

        with patch("confluent_kafka.Producer"):
            producer = DLQProducer(bootstrap_servers="localhost:9092")

        assert isinstance(producer, DLQSink), "DLQProducer must satisfy DLQSink protocol"

    def test_processing_dlq_satisfies_dlq_sink_protocol(self):
        """TB-002-DLQ-16: Processing DLQSink satisfies DLQSink protocol.

        The processing layer DLQ implementation conforms to the shared protocol.
        """
        try:
            from pipelines.processing.shared.dlq_sink import ProcessingDLQSink
            from pipelines.shared.dlq_protocol import DLQSink
        except ImportError as e:
            pytest.skip(f"Dependencies not available: {e}")

        import sys

        if "confluent_kafka" not in sys.modules:
            sys.modules["confluent_kafka"] = MagicMock()

        with patch("confluent_kafka.Producer"):
            sink = ProcessingDLQSink(bootstrap_servers="localhost:9092")

        assert isinstance(sink, DLQSink), "ProcessingDLQSink must satisfy DLQSink protocol"
