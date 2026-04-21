"""Produce synthetic DLQ messages to txn.api.dlq for DLQ Inspector testing."""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid

from confluent_kafka import Producer

TOPIC = "txn.api.dlq"
SOURCE_TOPICS = ["txn.api", "txn.enriched"]
ERRORS = [
    ("ValidationError", "Missing required field: amount"),
    ("SchemaError", "Avro deserialization failed: unexpected end of data"),
    ("BrokerError", "KafkaError{code=REQUEST_TIMED_OUT,val=7}"),
    ("ProcessingError", "NullPointerException in enrichment stage"),
    ("TimeoutError", "Consumer poll timeout after 30000ms"),
]


def _make_payload() -> str:
    return json.dumps(
        {
            "transaction_id": str(uuid.uuid4()),
            "amount": round(random.uniform(1.0, 5000.0), 2),
            "account_id": f"acc-{random.randint(1, 999):04d}",
            "card_number": f"4{random.randint(100_000_000_000_000, 999_999_999_999_999)}",
            "ip_address": (
                f"{random.randint(1,255)}.{random.randint(0,255)}"
                f".{random.randint(0,255)}.{random.randint(1,254)}"
            ),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--brokers", default="localhost:9092")
    args = parser.parse_args()

    producer = Producer({"bootstrap.servers": args.brokers})

    for _ in range(args.count):
        error_type, error_message = random.choice(ERRORS)
        msg = {
            "dlq_id": str(uuid.uuid4()),
            "source_topic": random.choice(SOURCE_TOPICS),
            "original_payload": _make_payload(),
            "error_type": error_type,
            "error_message": error_message,
            "failed_at": int(time.time() * 1000),
            "producer_host": f"producer-{random.randint(1, 3)}",
            "masking_applied": True,
        }
        producer.produce(
            TOPIC,
            key=str(uuid.uuid4()).encode(),
            value=json.dumps(msg).encode(),
        )

    producer.flush()
    print(f"  Produced {args.count} DLQ messages to {TOPIC}")


if __name__ == "__main__":
    main()
