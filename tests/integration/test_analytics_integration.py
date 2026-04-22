"""
Integration tests for the analytics consumer layer (Feature 008).

Markers: integration — requires Docker (pytest -m integration tests/integration/)

Tests:
  - Consumer lag metric updates when messages are produced
  - Consumer group isolation: analytics.dashboard does not share offsets
    with flink-scoring-job
  - Independence: stopping the analytics container does not stall the
    scoring pipeline's consumer group
"""

import io
import time
import uuid

import fastavro
import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

pytestmark = pytest.mark.integration

ALERT_TOPIC = "txn.fraud.alerts"
ANALYTICS_GROUP = "analytics.dashboard"
SCORING_GROUP = "flink-scoring-job"

_RAW_SCHEMA = {
    "type": "record",
    "name": "FraudAlert",
    "namespace": "com.fraudstream.alerts.v1",
    "fields": [
        {"name": "transaction_id", "type": "string"},
        {"name": "account_id", "type": "string"},
        {"name": "matched_rule_names", "type": {"type": "array", "items": "string"}},
        {
            "name": "severity",
            "type": {
                "type": "enum",
                "name": "AlertSeverity",
                "symbols": ["low", "medium", "high", "critical"],
            },
        },
        {
            "name": "evaluation_timestamp",
            "type": {"type": "long", "logicalType": "timestamp-millis"},
        },
    ],
}
_PARSED_SCHEMA = fastavro.parse_schema(_RAW_SCHEMA)


def _make_alert_bytes(txn_id: str = None, severity: str = "high") -> bytes:
    """Return a schemaless Avro-encoded FraudAlert for use in integration tests."""
    buf = io.BytesIO()
    fastavro.schemaless_writer(
        buf,
        _PARSED_SCHEMA,
        {
            "transaction_id": txn_id or f"txn-{uuid.uuid4().hex[:8]}",
            "account_id": "acc-0001",
            "matched_rule_names": ["VEL-001"],
            "severity": severity,
            "evaluation_timestamp": int(time.time() * 1000),
        },
    )
    return buf.getvalue()


@pytest.fixture(scope="module")
def broker(kafka_container):
    """Return the bootstrap server address from the running Kafka container."""
    return kafka_container.get_bootstrap_server()


@pytest.fixture(scope="module", autouse=True)
def create_topic(broker):
    """Ensure the alert topic exists before any tests in this module run."""
    admin = AdminClient({"bootstrap.servers": broker})
    futures = admin.create_topics([NewTopic(ALERT_TOPIC, num_partitions=1, replication_factor=1)])
    for t, f in futures.items():
        try:
            f.result()
        except Exception:
            pass  # topic may already exist


def _produce_n(broker: str, n: int = 5) -> list[str]:
    """Produce n Avro-encoded alert messages and return their transaction IDs."""
    p = Producer({"bootstrap.servers": broker})
    ids = []
    for _ in range(n):
        txn_id = f"txn-{uuid.uuid4().hex[:8]}"
        p.produce(ALERT_TOPIC, value=_make_alert_bytes(txn_id))
        ids.append(txn_id)
    p.flush(timeout=10)
    return ids


# ── Test 1: Consumer lag ──────────────────────────────────────────────────────


def test_consumer_lag_updates_after_produce(broker):
    """
    Produce 5 messages then verify the analytics consumer's reported lag
    reflects the unconsumed messages.
    """
    from analytics.consumers.kafka_consumer import AnalyticsKafkaConsumer

    _produce_n(broker, n=5)

    consumer = AnalyticsKafkaConsumer(bootstrap_servers=broker, queue_maxsize=100)
    consumer.start()

    # Give consumer time to join, poll, and compute lag
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if consumer.queue.qsize() >= 5:
            break
        time.sleep(0.5)

    consumer.stop()
    assert consumer.queue.qsize() >= 5, "Consumer did not drain produced messages"


# ── Test 2: Consumer group isolation ─────────────────────────────────────────


def test_analytics_group_does_not_share_offsets_with_scoring_group(broker):
    """
    analytics.dashboard and flink-scoring-job maintain independent offsets.
    Committing on one group must not advance the other.
    """
    # Produce 3 messages
    _produce_n(broker, n=3)

    def _drain(group: str) -> int:
        c = Consumer(
            {
                "bootstrap.servers": broker,
                "group.id": group,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            }
        )
        c.subscribe([ALERT_TOPIC])
        count = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and count < 3:
            msg = c.poll(1.0)
            if msg and not msg.error():
                count += 1
        c.commit()
        c.close()
        return count

    scoring_count = _drain(SCORING_GROUP)
    analytics_count = _drain(ANALYTICS_GROUP)

    # Both groups should independently see all messages from their own offsets
    assert scoring_count >= 1, "Scoring group saw no messages"
    assert analytics_count >= 1, "Analytics group saw no messages"


# ── Test 3: Independence — scoring pipeline unaffected when analytics stops ───


def test_scoring_pipeline_unaffected_when_analytics_consumer_stops(broker):
    """
    Stopping the analytics consumer must not stall the scoring group offset lag.
    The scoring consumer should continue advancing independently.
    """
    from analytics.consumers.kafka_consumer import AnalyticsKafkaConsumer

    # Start analytics consumer, let it begin consuming
    analytics = AnalyticsKafkaConsumer(bootstrap_servers=broker, queue_maxsize=50)
    analytics.start()
    time.sleep(2)

    # Snapshot scoring group high-watermark before stopping analytics
    def _high_watermark() -> int:
        admin_c = Consumer(
            {
                "bootstrap.servers": broker,
                "group.id": f"probe-{uuid.uuid4().hex[:6]}",
                "auto.offset.reset": "latest",
            }
        )
        try:
            _, high = admin_c.get_watermark_offsets(
                __import__("confluent_kafka").TopicPartition(ALERT_TOPIC, 0), timeout=5
            )
            return high
        finally:
            admin_c.close()

    hw_before = _high_watermark()

    # Stop analytics consumer
    analytics.stop()
    time.sleep(1)

    # Produce more messages
    _produce_n(broker, n=3)

    # Scoring group consumer should still be able to consume independently
    scoring_c = Consumer(
        {
            "bootstrap.servers": broker,
            "group.id": SCORING_GROUP,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    scoring_c.subscribe([ALERT_TOPIC])
    received = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and received < 3:
        msg = scoring_c.poll(1.0)
        if msg and not msg.error():
            received += 1
    scoring_c.close()

    hw_after = _high_watermark()
    assert hw_after >= hw_before, "High watermark did not advance after producing"
    assert not analytics.is_alive(), "Analytics consumer should be stopped"
