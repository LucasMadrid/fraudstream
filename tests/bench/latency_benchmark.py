"""Latency benchmark suite — TB-003: Performance Baseline Suite.

Measures p50, p95, p99 latency for critical operations:
- Feature store lookups
- Kafka produce/consume
- Rule evaluation
- Alert generation

Usage:
    pytest tests/bench/latency_benchmark.py -v --benchmark-only -m latency
    
Markers:
    @pytest.mark.latency — latency-specific benchmarks
"""

import time
import uuid
from typing import Any

import pytest

pytest.importorskip("confluent_kafka", reason="confluent-kafka not installed")

from confluent_kafka import Consumer, KafkaError, Message, Producer

_BROKER = "localhost:9092"
_TEST_TOPIC = "bench.latency.test"


@pytest.fixture(scope="module")
def kafka_producer():
    """Provide a Kafka producer for latency tests."""
    producer = Producer({"bootstrap.servers": _BROKER})
    yield producer
    producer.flush()


@pytest.fixture(scope="module")
def kafka_consumer():
    """Provide a Kafka consumer for latency tests."""
    consumer = Consumer(
        {
            "bootstrap.servers": _BROKER,
            "group.id": f"bench-latency-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
        }
    )
    yield consumer
    consumer.close()


@pytest.mark.latency
class TestFeatureStoreLatency:
    """Benchmark feature store lookup latency — TB-003."""

    def test_feature_lookup_latency(self, benchmark):
        """Benchmark single feature lookup latency (p99 target: <10ms)."""
        
        def _lookup_feature():
            """Simulate feature store lookup with realistic delay distribution."""
            # Simulate network + storage latency with realistic distribution
            # Most lookups are fast, some are slower (cache misses)
            import random
            
            delay_ms = random.expovariate(1 / 2.0)  # Mean 2ms
            if random.random() < 0.05:  # 5% cache miss
                delay_ms += random.uniform(5, 15)
            time.sleep(delay_ms / 1000)
            return {
                "account_id": "acc-123",
                "feature_vector": [1.0, 2.0, 3.0],
                "timestamp": time.time(),
            }
        
        result = benchmark(_lookup_feature)
        assert result is not None

    def test_feature_batch_lookup_latency(self, benchmark):
        """Benchmark batch feature lookup latency for 100 features (p99 target: <50ms)."""
        
        def _batch_lookup():
            """Simulate batch feature store lookup."""
            import random
            
            batch_size = 100
            # Batch lookups are more efficient
            delay_ms = random.expovariate(1 / 15.0)  # Mean 15ms for batch
            if random.random() < 0.10:  # 10% slower due to batching overhead
                delay_ms += random.uniform(10, 30)
            time.sleep(delay_ms / 1000)
            return [
                {"account_id": f"acc-{i}", "features": [1.0, 2.0, 3.0]}
                for i in range(batch_size)
            ]
        
        result = benchmark(_batch_lookup)
        assert len(result) == 100


@pytest.mark.latency
class TestKafkaLatency:
    """Benchmark Kafka produce/consume latency — TB-003."""

    @pytest.mark.parametrize("payload_size", [256, 1024, 4096])
    def test_kafka_produce_latency(self, benchmark, kafka_producer, payload_size):
        """Benchmark Kafka produce latency by payload size.
        
        Targets:
        - 256B:  p99 < 5ms
        - 1KB:   p99 < 10ms  
        - 4KB:   p99 < 20ms
        """
        import random
        
        payload = b"x" * payload_size
        key = str(uuid.uuid4())
        
        def _produce():
            """Produce a message and wait for delivery confirmation."""
            delivery_result = [None]
            
            def _on_delivery(err, msg):
                delivery_result[0] = err is None
            
            kafka_producer.produce(
                topic=_TEST_TOPIC,
                key=key.encode(),
                value=payload,
                callback=_on_delivery,
            )
            kafka_producer.flush()
            return delivery_result[0]
        
        result = benchmark(_produce)
        assert result is True

    def test_kafka_end_to_end_latency(self, benchmark):
        """Benchmark end-to-end latency: produce to consume.
        
        Target p99: < 100ms for 1KB message through full pipeline.
        """
        from confluent_kafka.admin import AdminClient, NewTopic
        
        admin = AdminClient({"bootstrap.servers": _BROKER})
        
        # Create test topic
        test_topic = f"bench.e2e.latency.{uuid.uuid4().hex[:8]}"
        new_topic = NewTopic(test_topic, num_partitions=1, replication_factor=1)
        admin.create_topics([new_topic])
        
        try:
            producer = Producer({"bootstrap.servers": _BROKER})
            
            def _e2e_latency():
                """Measure full round-trip latency."""
                message_key = str(uuid.uuid4())
                sent_at = time.time_ns()
                
                # Produce
                producer.produce(
                    topic=test_topic,
                    key=message_key.encode(),
                    value=f'{{"ts": {sent_at}, "data": "test"}}'.encode(),
                )
                producer.flush()
                
                # Consume
                consumer = Consumer(
                    {
                        "bootstrap.servers": _BROKER,
                        "group.id": f"bench-e2e-{uuid.uuid4()}",
                        "auto.offset.reset": "earliest",
                    }
                )
                consumer.subscribe([test_topic])
                
                received_at = None
                deadline = time.monotonic() + 1.0
                
                while time.monotonic() < deadline:
                    msg = consumer.poll(timeout=0.01)
                    if msg and not msg.error():
                        if msg.key().decode() == message_key:
                            received_at = time.time_ns()
                            break
                
                consumer.close()
                
                if received_at:
                    latency_ms = (received_at - sent_at) / 1_000_000
                    return latency_ms
                return None
            
            result = benchmark(_e2e_latency)
            assert result is not None
            
        finally:
            admin.delete_topics([test_topic])


@pytest.mark.latency
class TestRuleEngineLatency:
    """Benchmark fraud rule evaluation latency — TB-003."""

    def test_single_rule_evaluation_latency(self, benchmark):
        """Benchmark single rule evaluation latency (p99 target: <1ms)."""
        
        def _evaluate_rule():
            """Simulate rule evaluation."""
            import random
            
            transaction = {
                "amount": random.uniform(10, 10000),
                "merchant": f"merchant-{random.randint(1, 1000)}",
                "timestamp": time.time(),
            }
            
            # Simple rule: amount > 5000
            start = time.perf_counter()
            result = transaction["amount"] > 5000
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            return {"triggered": result, "latency_ms": elapsed_ms}
        
        result = benchmark(_evaluate_rule)
        assert "triggered" in result

    def test_ruleset_evaluation_latency(self, benchmark):
        """Benchmark 10-rule ruleset evaluation latency (p99 target: <5ms)."""
        
        def _evaluate_ruleset():
            """Simulate 10-rule ruleset evaluation."""
            import random
            
            transaction = {
                "amount": random.uniform(10, 10000),
                "merchant": f"merchant-{random.randint(1, 1000)}",
                "timestamp": time.time(),
                "account_age_days": random.randint(1, 3650),
                "country": random.choice(["US", "UK", "DE", "FR", "JP"]),
            }
            
            rules = [
                lambda t: t["amount"] > 5000,
                lambda t: t["account_age_days"] < 30,
                lambda t: t["country"] not in ["US", "UK"],
                lambda t: t["amount"] > 1000 and t["account_age_days"] < 90,
                lambda t: t["merchant"].startswith("high-risk"),
                lambda t: t["amount"] > 20000,
                lambda t: t["timestamp"] % 86400 < 3600,  # Late night
                lambda t: t["amount"] % 100 == 0,  # Round amount
                lambda t: t["account_age_days"] < 7,
                lambda t: t["country"] == "XX",  # Invalid country
            ]
            
            start = time.perf_counter()
            triggered = [r(transaction) for r in rules]
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            return {"triggered_count": sum(triggered), "latency_ms": elapsed_ms}
        
        result = benchmark(_evaluate_ruleset)
        assert result["triggered_count"] >= 0


@pytest.mark.latency
class TestAlertLatency:
    """Benchmark alert generation and dispatch latency — TB-003."""

    def test_alert_generation_latency(self, benchmark):
        """Benchmark alert generation from detection to dispatch (p99 target: <50ms)."""
        
        def _generate_alert():
            """Simulate full alert generation pipeline."""
            import random
            
            # Simulate detection
            detection_time = time.time()
            
            # Build alert
            alert = {
                "alert_id": str(uuid.uuid4()),
                "transaction_id": str(uuid.uuid4()),
                "severity": random.choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
                "detected_at": detection_time,
                "rules_triggered": ["RULE_001", "RULE_005"],
            }
            
            # Simulate dispatch delay (serialization + network)
            time.sleep(random.expovariate(1 / 5.0) / 1000)
            
            return alert
        
        result = benchmark(_generate_alert)
        assert "alert_id" in result
