"""ScoringConfig — all fraud scoring service settings, env-var driven."""

import os
from dataclasses import dataclass, field


@dataclass
class ScoringConfig:
    kafka_brokers: str = field(
        default_factory=lambda: os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    )
    schema_registry_url: str = field(
        default_factory=lambda: os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")
    )
    fraud_alerts_topic: str = field(
        default_factory=lambda: os.environ.get("FRAUD_ALERTS_TOPIC", "txn.fraud.alerts")
    )
    fraud_alerts_dlq_topic: str = field(
        default_factory=lambda: os.environ.get("FRAUD_ALERTS_DLQ_TOPIC", "txn.fraud.alerts.dlq")
    )
    rules_yaml_path: str = field(
        default_factory=lambda: os.environ.get("RULES_YAML_PATH", "/opt/rules/rules.yaml")
    )
    fraud_alerts_db_url: str = field(
        default_factory=lambda: os.environ.get(
            "FRAUD_ALERTS_DB_URL", "postgresql://fraudstream:fraudstream@localhost:5432/fraudstream"
        )
    )
    pg_pool_size: int = field(
        default_factory=lambda: int(os.environ.get("PG_POOL_SIZE", "2"))
    )
