"""Unit tests for ProcessingDLQSink."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipelines.processing.shared.dlq_sink import ProcessingDLQSink


class TestProcessingDLQSinkInit:
    """Test ProcessingDLQSink construction."""

    @patch("confluent_kafka.Producer")
    def test_stores_config(self, mock_producer_class):
        """Verify _producer, _topic, _schema_id are set after construction."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "txn.processing.dlq", schema_id=42)

        assert p._producer is mock_producer
        assert p._topic == "txn.processing.dlq"
        assert p._schema_id == 42


class TestProcessingDLQSinkProducerCreation:
    """Test that _producer is set after construction."""

    @patch("confluent_kafka.Producer")
    def test_producer_created_on_init(self, mock_producer_class):
        """Verify confluent_kafka.Producer is created with correct config."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "dlq", schema_id=5)

        # Verify Producer was called with the expected config
        mock_producer_class.assert_called_once()
        call_kwargs = mock_producer_class.call_args[0][0]
        assert call_kwargs["bootstrap.servers"] == "localhost:9092"
        assert call_kwargs["acks"] == 1
        assert call_kwargs["linger.ms"] == 5
        assert "client.id" in call_kwargs

        # Verify producer is stored
        assert p._producer is mock_producer


class TestProcessingDLQSinkSend:
    """Test ProcessingDLQSink.send()."""

    @patch("confluent_kafka.Producer")
    def test_send_with_all_kwargs(self, mock_producer_class):
        """Verify send() calls producer with correct args."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "my.dlq", schema_id=1)
        p.send(
            source_topic="txn.input",
            original_payload=b"\x00\x01\x02",
            error_type="ValidationError",
            error_message="Invalid transaction format",
        )

        # Verify producer.produce and producer.poll were called
        mock_producer.produce.assert_called_once()
        mock_producer.poll.assert_called_once_with(0)

    @patch("confluent_kafka.Producer")
    def test_send_serializes_avro_record(self, mock_producer_class):
        """Verify send() serializes a proper DLQ record."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "dlq", schema_id=7)
        p.send(
            source_topic="txn.events",
            original_payload=b"event_data",
            error_type="ParseError",
            error_message="Unable to parse JSON",
        )

        # Capture the produced value and verify it starts with Confluent magic byte
        call_args = mock_producer.produce.call_args
        produced_value = call_args[1]["value"]

        # Confluent wire format: magic byte (0x00) + 4-byte big-endian schema ID
        assert produced_value[:1] == b"\x00"
        schema_id_bytes = produced_value[1:5]
        assert int.from_bytes(schema_id_bytes, "big") == 7

    @patch("confluent_kafka.Producer")
    def test_send_uses_correct_topic(self, mock_producer_class):
        """Verify send() writes to the configured topic."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "custom.dlq.topic", schema_id=0)
        p.send(
            source_topic="input",
            original_payload=b"data",
            error_type="Error",
            error_message="msg",
        )

        call_kwargs = mock_producer.produce.call_args[1]
        assert call_kwargs["topic"] == "custom.dlq.topic"


class TestProcessingDLQSinkFlush:
    """Test ProcessingDLQSink.flush()."""

    @patch("confluent_kafka.Producer")
    def test_flush_with_default_timeout(self, mock_producer_class):
        """Verify flush() delegates to producer with default 5.0 timeout."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "dlq")
        p.flush()

        mock_producer.flush.assert_called_once_with(5.0)

    @patch("confluent_kafka.Producer")
    def test_flush_with_custom_timeout(self, mock_producer_class):
        """Verify flush(timeout=X) delegates with custom timeout."""
        mock_producer = MagicMock()
        mock_producer_class.return_value = mock_producer

        p = ProcessingDLQSink("localhost:9092", "dlq")
        p.flush(timeout=3.0)

        mock_producer.flush.assert_called_once_with(3.0)
