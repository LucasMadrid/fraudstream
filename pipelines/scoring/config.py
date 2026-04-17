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
    pg_pool_size: int = field(default_factory=lambda: int(os.environ.get("PG_POOL_SIZE", "2")))
    ml_serving_url: str = field(
        default_factory=lambda: os.environ.get("ML_SERVING_URL", "http://localhost:5001")
    )
    cb_error_threshold: int = field(
        default_factory=lambda: int(os.environ.get("CB_ERROR_THRESHOLD", "3"))
    )
    cb_open_seconds: float = field(
        default_factory=lambda: float(os.environ.get("CB_OPEN_SECONDS", "30.0"))
    )
    cb_probe_timeout_ms: float = field(
        default_factory=lambda: float(os.environ.get("CB_PROBE_TIMEOUT_MS", "5.0"))
    )
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )
    cb_error_window_seconds: int = field(
        default_factory=lambda: int(os.environ.get("CB_ERROR_WINDOW_SECONDS", "10"))
    )

    def __post_init__(self) -> None:
        """Validate numeric fields after dataclass initialization."""
        if self.cb_error_threshold < 1:
            raise ValueError("cb_error_threshold must be >= 1")
        if self.cb_open_seconds <= 0:
            raise ValueError("cb_open_seconds must be > 0")
        if not (1 <= self.cb_probe_timeout_ms <= 50):
            raise ValueError("cb_probe_timeout_ms must be between 1 and 50")
        if self.cb_error_window_seconds < 1:
            raise ValueError("cb_error_window_seconds must be >= 1")
