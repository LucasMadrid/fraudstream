"""Unit tests for DLQKafkaProducer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipelines.processing.shared.dlq_sink import DLQKafkaProducer


class TestDLQKafkaProducerInit:
    """Test DLQKafkaProducer construction."""

    def test_stores_config(self):
        p = DLQKafkaProducer("localhost:9092", "txn.processing.dlq")
        assert p._bootstrap_servers == "localhost:9092"
        assert p._dlq_topic == "txn.processing.dlq"
        assert p._producer is None


class TestDLQKafkaProducerOpen:
    """Test DLQKafkaProducer.open()."""

    @patch(
        "pipelines.processing.shared.dlq_sink.DLQKafkaProducer.open"
    )
    def test_open_creates_producer(self, mock_open):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        p.open()
        mock_open.assert_called_once()

    def test_open_sets_producer_via_mock(self):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        mock_producer = MagicMock()
        with patch(
            "confluent_kafka.Producer", return_value=mock_producer
        ):
            p.open()
        assert p._producer is mock_producer


class TestDLQKafkaProducerProduce:
    """Test DLQKafkaProducer.produce()."""

    def test_produce_before_open_logs_error(self, caplog):
        import logging

        caplog.set_level(
            logging.ERROR,
            logger="pipelines.processing.shared.dlq_sink",
        )
        p = DLQKafkaProducer("localhost:9092", "dlq")
        p.produce(b"data")
        assert any(
            "called before open()" in r.message
            for r in caplog.records
        )

    def test_produce_delegates_to_kafka_producer(self):
        p = DLQKafkaProducer("localhost:9092", "my.dlq")
        mock_prod = MagicMock()
        p._producer = mock_prod

        p.produce(b"\x00\x01\x02", key="txn-123")
        mock_prod.produce.assert_called_once_with(
            "my.dlq",
            value=b"\x00\x01\x02",
            key=b"txn-123",
        )

    def test_produce_with_none_key(self):
        p = DLQKafkaProducer("localhost:9092", "my.dlq")
        mock_prod = MagicMock()
        p._producer = mock_prod

        p.produce(b"data", key=None)
        mock_prod.produce.assert_called_once_with(
            "my.dlq", value=b"data", key=None
        )

    def test_produce_swallows_exception(self, caplog):
        import logging

        caplog.set_level(
            logging.ERROR,
            logger="pipelines.processing.shared.dlq_sink",
        )
        p = DLQKafkaProducer("localhost:9092", "my.dlq")
        mock_prod = MagicMock()
        mock_prod.produce.side_effect = RuntimeError("boom")
        p._producer = mock_prod

        # Should NOT raise
        p.produce(b"data")
        assert any(
            "Failed to produce" in r.message for r in caplog.records
        )


class TestDLQKafkaProducerFlush:
    """Test DLQKafkaProducer.flush()."""

    def test_flush_delegates(self):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        mock_prod = MagicMock()
        p._producer = mock_prod

        p.flush(timeout=3.0)
        mock_prod.flush.assert_called_once_with(3.0)

    def test_flush_noop_when_not_open(self):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        # Should not raise
        p.flush()


class TestDLQKafkaProducerClose:
    """Test DLQKafkaProducer.close()."""

    def test_close_flushes_and_nulls(self):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        mock_prod = MagicMock()
        p._producer = mock_prod

        p.close()
        mock_prod.flush.assert_called_once_with(timeout=10.0)
        assert p._producer is None

    def test_close_noop_when_not_open(self):
        p = DLQKafkaProducer("localhost:9092", "dlq")
        # Should not raise
        p.close()
        assert p._producer is None
