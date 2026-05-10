"""Unit tests for DLQSink protocol and concrete implementations.

C32 — verify structural subtyping and that send() delegates correctly.

confluent_kafka is not installed in the unit-test environment; both DLQ
implementations import it lazily so we patch "confluent_kafka.Producer"
directly after ensuring the stub module is in sys.modules.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Stub confluent_kafka before any import that lazily pulls it in.
if "confluent_kafka" not in sys.modules:
    sys.modules["confluent_kafka"] = MagicMock()

from pipelines.shared.dlq_protocol import DLQSink  # noqa: E402

# ---------------------------------------------------------------------------
# Protocol structural subtyping
# ---------------------------------------------------------------------------


class TestDLQSinkProtocol:
    def test_object_with_send_satisfies_protocol(self):
        class _Stub:
            def send(self, *, source_topic, original_payload, error_type, error_message):
                pass

        assert isinstance(_Stub(), DLQSink)

    def test_object_missing_send_does_not_satisfy_protocol(self):
        class _NoSend:
            def write(self, data):
                pass

        assert not isinstance(_NoSend(), DLQSink)

    def test_stub_captures_calls_as_dlq_sink(self):
        calls: list[dict] = []

        class _FakeSink:
            def send(self, *, source_topic, original_payload, error_type, error_message):
                calls.append(
                    dict(
                        source_topic=source_topic,
                        original_payload=original_payload,
                        error_type=error_type,
                        error_message=error_message,
                    )
                )

        sink: DLQSink = _FakeSink()
        sink.send(
            source_topic="txn.api",
            original_payload=b"{}",
            error_type="SCHEMA_ERROR",
            error_message="missing field",
        )
        assert len(calls) == 1
        assert calls[0]["error_type"] == "SCHEMA_ERROR"


# ---------------------------------------------------------------------------
# DLQProducer.send() — satisfies protocol and delegates correctly
# ---------------------------------------------------------------------------


class TestDLQProducerSend:
    def test_satisfies_dlq_sink_protocol(self):
        from pipelines.ingestion.shared.dlq_producer import DLQProducer

        with patch("confluent_kafka.Producer"):
            producer = DLQProducer(bootstrap_servers="localhost:9092")

        assert isinstance(producer, DLQSink)

    def test_send_delegates_to_send_to_dlq(self):
        from pipelines.ingestion.shared.dlq_producer import DLQProducer

        with patch("confluent_kafka.Producer"):
            producer = DLQProducer(bootstrap_servers="localhost:9092")

        with patch.object(producer, "send_to_dlq") as mock_send:
            producer.send(
                source_topic="txn.api",
                original_payload=b'{"id": "t1"}',
                error_type="VALIDATION_ERROR",
                error_message="bad field",
            )

        mock_send.assert_called_once_with(
            source_topic="txn.api",
            original_payload='{"id": "t1"}',
            error_type="VALIDATION_ERROR",
            error_message="bad field",
            masking_applied=False,
        )

    def test_send_decodes_bytes_to_str(self):
        from pipelines.ingestion.shared.dlq_producer import DLQProducer

        with patch("confluent_kafka.Producer"):
            producer = DLQProducer(bootstrap_servers="localhost:9092")

        with patch.object(producer, "send_to_dlq") as mock_send:
            producer.send(
                source_topic="t",
                original_payload=b"hello",
                error_type="E",
                error_message="m",
            )

        _, kwargs = mock_send.call_args
        assert isinstance(kwargs["original_payload"], str)
        assert kwargs["original_payload"] == "hello"


# ---------------------------------------------------------------------------
# ProcessingDLQSink.send() — satisfies protocol and delegates correctly
# ---------------------------------------------------------------------------


class TestProcessingDLQSink:
    def test_satisfies_dlq_sink_protocol(self):
        from pipelines.processing.shared.dlq_sink import ProcessingDLQSink

        with patch("confluent_kafka.Producer"):
            sink = ProcessingDLQSink(bootstrap_servers="localhost:9092")

        assert isinstance(sink, DLQSink)

    def test_send_calls_build_and_serialise(self):
        from pipelines.processing.shared.dlq_sink import ProcessingDLQSink

        with patch("confluent_kafka.Producer"):
            sink = ProcessingDLQSink(bootstrap_servers="localhost:9092")

        with (
            patch("pipelines.processing.shared.dlq_sink.build_dlq_record") as mock_build,
            patch("pipelines.processing.shared.dlq_sink.serialise_dlq_record") as mock_ser,
        ):
            mock_build.return_value = {"dlq_id": "x"}
            mock_ser.return_value = b"\x00\x00\x00\x00\x00avro"

            sink.send(
                source_topic="txn.api",
                original_payload=b"raw",
                error_type="LATE_EVENT",
                error_message="behind watermark",
            )

        mock_build.assert_called_once_with(
            source_topic="txn.api",
            source_partition=0,
            source_offset=0,
            original_payload_bytes=b"raw",
            error_type="LATE_EVENT",
            error_message="behind watermark",
        )
        mock_ser.assert_called_once_with({"dlq_id": "x"}, 0)

    def test_send_produces_to_kafka(self):
        from pipelines.processing.shared.dlq_sink import ProcessingDLQSink

        mock_kafka = MagicMock()
        with patch("confluent_kafka.Producer", return_value=mock_kafka):
            sink = ProcessingDLQSink(bootstrap_servers="localhost:9092", topic="my.dlq")

        with (
            patch("pipelines.processing.shared.dlq_sink.build_dlq_record", return_value={}),
            patch(
                "pipelines.processing.shared.dlq_sink.serialise_dlq_record",
                return_value=b"bytes",
            ),
        ):
            sink.send(
                source_topic="t",
                original_payload=b"p",
                error_type="E",
                error_message="m",
            )

        mock_kafka.produce.assert_called_once_with(topic="my.dlq", value=b"bytes")
        mock_kafka.poll.assert_called_once_with(0)

    def test_custom_schema_id_forwarded(self):
        from pipelines.processing.shared.dlq_sink import ProcessingDLQSink

        with patch("confluent_kafka.Producer"):
            sink = ProcessingDLQSink(bootstrap_servers="localhost:9092", schema_id=42)

        with (
            patch("pipelines.processing.shared.dlq_sink.build_dlq_record", return_value={}),
            patch("pipelines.processing.shared.dlq_sink.serialise_dlq_record") as mock_ser,
        ):
            mock_ser.return_value = b"x"
            sink.send(source_topic="t", original_payload=b"p", error_type="E", error_message="m")

        assert mock_ser.call_args[0][1] == 42
