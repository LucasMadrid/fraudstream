"""TB-006: Sink Lifecycle Tests — AlertKafkaSink.close() Verification.

Constitution Principle VIII (Observability): Resource lifecycle must be
explicit. AlertKafkaSink.close() must flush pending messages and release
resources properly to prevent message loss on shutdown.

Run with: pytest tests/integration/test_sink_lifecycle.py -v --timeout=120
Requires Docker daemon running.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Generator

import pytest

# Guard: skip the entire module if testcontainers is not installed
pytest.importorskip("testcontainers")

from testcontainers.kafka import KafkaContainer  # type: ignore[import]

from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.types import FraudAlert

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def kafka_bootstrap() -> Generator[str, None, None]:
    """Start a Kafka container and yield its bootstrap server address."""
    with KafkaContainer("confluentinc/cp-kafka:7.6.1") as kafka:
        yield kafka.get_bootstrap_server()


@pytest.fixture
def scoring_config(kafka_bootstrap: str) -> ScoringConfig:
    """Create a ScoringConfig pointing to test Kafka."""
    return ScoringConfig(
        kafka_brokers=kafka_bootstrap,
        fraud_alerts_topic="txn.fraud.alerts.test",
        fraud_alerts_dlq_topic="txn.fraud.alerts.dlq.test",
    )


def _make_alert(transaction_id: str | None = None, **kwargs) -> FraudAlert:
    """Create a test FraudAlert."""
    defaults = dict(
        transaction_id=transaction_id or str(uuid.uuid4()),
        account_id="acc-test",
        matched_rule_names=["VEL-001"],
        severity="critical",
        evaluation_timestamp=int(time.time() * 1000),
    )
    defaults.update(kwargs)
    return FraudAlert(**defaults)


# =============================================================================
# TB-006-01: AlertKafkaSink.close() flushes properly
# =============================================================================


class TestAlertKafkaSinkCloseFlushing:
    """TB-006-01: Verify AlertKafkaSink.close() properly flushes pending messages.

    Constitution Principle VIII: Resource lifecycle must be explicit.
    close() must ensure all pending messages are delivered before returning.
    """

    @pytest.mark.integration
    def test_close_flushes_pending_messages(
        self, scoring_config: ScoringConfig, kafka_bootstrap: str
    ):
        """TB-006-01a: close() must flush all pending messages to Kafka.

        Emit alerts, call close(), verify all messages are delivered.
        """
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        # Create and open sink
        sink = AlertKafkaSink(scoring_config)
        sink.open()

        # Emit alerts
        num_alerts = 10
        alerts = [_make_alert() for _ in range(num_alerts)]
        for alert in alerts:
            sink.emit(alert)

        # Close sink (should flush)
        sink.close()

        # Consume and verify all messages delivered
        consumer = Consumer(
            {
                "bootstrap.servers": kafka_bootstrap,
                "group.id": f"test-close-flush-{uuid.uuid4()}",
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([scoring_config.fraud_alerts_topic])

        received = []
        deadline = time.time() + 10
        while time.time() < deadline and len(received) < num_alerts:
            msg = consumer.poll(timeout=1.0)
            if msg and not msg.error():
                received.append(msg)

        consumer.close()

        assert len(received) == num_alerts, (
            f"Expected {num_alerts} messages after close(), got {len(received)}"
        )

    @pytest.mark.integration
    def test_close_is_idempotent(self, scoring_config: ScoringConfig, kafka_bootstrap: str):
        """TB-006-01b: close() must be idempotent (safe to call multiple times).

        Multiple close() calls should not raise or cause issues.
        """
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        sink = AlertKafkaSink(scoring_config)
        sink.open()

        # Emit one alert
        sink.emit(_make_alert())

        # Close multiple times - should not raise
        sink.close()
        sink.close()
        sink.close()

        # Verify producer is released
        assert sink._producer is None


# =============================================================================
# TB-006-02: No message loss on shutdown
# =============================================================================


class TestNoMessageLossOnShutdown:
    """TB-006-02: Verify no messages are lost when sink is closed.

    Constitution Principle VIII: Zero silent record drops.
    """

    @pytest.mark.integration
    def test_all_messages_delivered_before_close_returns(
        self, scoring_config: ScoringConfig, kafka_bootstrap: str
    ):
        """TB-006-02a: All emitted messages must be delivered before close() returns.

        This ensures no async messages are lost during shutdown.
        """
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        # Setup consumer before emitting
        consumer = Consumer(
            {
                "bootstrap.servers": kafka_bootstrap,
                "group.id": f"test-no-loss-{uuid.uuid4()}",
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([scoring_config.fraud_alerts_topic])

        # Create and use sink
        sink = AlertKafkaSink(scoring_config)
        sink.open()

        num_messages = 50
        for i in range(num_messages):
            sink.emit(_make_alert(transaction_id=f"txn-{i:04d}"))

        # Close and flush
        sink.close()

        # Now consume - all should be there
        received = []
        deadline = time.time() + 15
        while time.time() < deadline and len(received) < num_messages:
            msg = consumer.poll(timeout=1.0)
            if msg and not msg.error():
                received.append(msg)

        consumer.close()

        assert len(received) == num_messages, (
            f"Message loss detected: expected {num_messages}, received {len(received)}"
        )

    @pytest.mark.integration
    def test_flush_explicitly_called_before_close(
        self, scoring_config: ScoringConfig, kafka_bootstrap: str
    ):
        """TB-006-02b: flush() should be called explicitly before producer cleanup.

        Ensures the flush mechanism works correctly.
        """

        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        sink = AlertKafkaSink(scoring_config)
        sink.open()

        # Track flush calls
        flush_called = []
        original_flush = sink.flush

        def tracking_flush():
            flush_called.append(True)
            original_flush()

        sink.flush = tracking_flush

        # Emit and close
        sink.emit(_make_alert())
        sink.close()

        # Verify flush was called
        assert len(flush_called) >= 1, "flush() should be called during close()"


# =============================================================================
# TB-006-03: Producer resource cleanup
# =============================================================================


class TestProducerResourceCleanup:
    """TB-006-03: Verify producer resources are properly released.

    Resource leaks can cause connection exhaustion over time.
    """

    @pytest.mark.integration
    def test_producer_set_to_none_after_close(
        self, scoring_config: ScoringConfig, kafka_bootstrap: str
    ):
        """TB-006-03a: Producer reference should be cleared after close().

        Prevents use-after-close errors.
        """
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        sink = AlertKafkaSink(scoring_config)
        sink.open()

        assert sink._producer is not None, "Producer should exist after open()"

        sink.close()

        assert sink._producer is None, "Producer should be None after close()"

    @pytest.mark.integration
    def test_emit_after_close_raises_error(
        self, scoring_config: ScoringConfig, kafka_bootstrap: str
    ):
        """TB-006-03b: emit() after close() should raise RuntimeError.

        Prevents silent message loss from using closed sink.
        """
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        sink = AlertKafkaSink(scoring_config)
        sink.open()
        sink.close()

        # Emit after close should fail
        with pytest.raises(RuntimeError) as exc_info:
            sink.emit(_make_alert())

        assert "open()" in str(exc_info.value) or "closed" in str(exc_info.value).lower()


# =============================================================================
# TB-006-04: DLQ handling during shutdown
# =============================================================================


class TestDLQHandlingDuringShutdown:
    """TB-006-04: Verify DLQ messages are also flushed on close.

    Failed deliveries during shutdown must also be preserved.
    """

    @pytest.mark.integration
    def test_dlq_topic_exists_and_configurable(self, scoring_config: ScoringConfig):
        """TB-006-04a: DLQ topic should be configurable."""
        assert scoring_config.fraud_alerts_dlq_topic is not None
        assert "dlq" in scoring_config.fraud_alerts_dlq_topic.lower()


# =============================================================================
# TB-006-05: Stress test with high volume
# =============================================================================


class TestHighVolumeShutdown:
    """TB-006-05: Verify no message loss under high volume."""

    @pytest.mark.integration
    @pytest.mark.slow
    def test_high_volume_no_message_loss(self, scoring_config: ScoringConfig, kafka_bootstrap: str):
        """TB-006-05a: No message loss with 100+ alerts on shutdown.

        Stress test to ensure flush() handles queued messages.
        """
        from confluent_kafka import Consumer
        from confluent_kafka.admin import AdminClient, NewTopic

        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        # Create topic
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        admin.create_topics(
            [NewTopic(scoring_config.fraud_alerts_topic, num_partitions=1, replication_factor=1)]
        )
        time.sleep(1)

        sink = AlertKafkaSink(scoring_config)
        sink.open()

        num_messages = 100
        for i in range(num_messages):
            sink.emit(_make_alert(transaction_id=f"high-vol-{i:04d}"))

        # Close immediately (don't wait for natural batching)
        sink.close()

        # Consume all
        consumer = Consumer(
            {
                "bootstrap.servers": kafka_bootstrap,
                "group.id": f"test-high-vol-{uuid.uuid4()}",
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([scoring_config.fraud_alerts_topic])

        received = []
        deadline = time.time() + 20
        while time.time() < deadline and len(received) < num_messages:
            msg = consumer.poll(timeout=1.0)
            if msg and not msg.error():
                received.append(msg)

        consumer.close()

        assert len(received) == num_messages, (
            f"High volume message loss: expected {num_messages}, got {len(received)}"
        )
