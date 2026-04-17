"""ScoringConfig — all fraud scoring service settings, env-var driven."""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_DB_URL = "postgresql://fraudstream:fraudstream@localhost:5432/fraudstream"


def _parse_int(env_var: str, default: str) -> int:
    """
    Read the environment variable named by `env_var` and parse its value as an integer.
    
    Parameters:
        env_var (str): Name of the environment variable to read.
        default (str): Fallback string to use when the environment variable is not set.
    
    Returns:
        int: Integer parsed from the environment variable value or from `default` when the variable is absent.
    
    Raises:
        ValueError: If the resolved value cannot be converted to an integer.
    """
    raw = os.environ.get(env_var, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {env_var}={raw!r} must be an integer") from None


def _parse_float(env_var: str, default: str) -> float:
    raw = os.environ.get(env_var, default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Environment variable {env_var}={raw!r} must be a float") from None


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
        default_factory=lambda: os.environ.get("FRAUD_ALERTS_DB_URL", _DEFAULT_DB_URL)
    )
    pg_pool_size: int = field(default_factory=lambda: _parse_int("PG_POOL_SIZE", "2"))
    ml_serving_url: str = field(
        default_factory=lambda: os.environ.get("ML_SERVING_URL", "http://localhost:5001")
    )
    cb_error_threshold: int = field(default_factory=lambda: _parse_int("CB_ERROR_THRESHOLD", "3"))
    cb_open_seconds: float = field(default_factory=lambda: _parse_float("CB_OPEN_SECONDS", "30.0"))
    cb_probe_timeout_ms: float = field(
        default_factory=lambda: _parse_float("CB_PROBE_TIMEOUT_MS", "5.0")
    )
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )
    cb_error_window_seconds: int = field(
        default_factory=lambda: _parse_int("CB_ERROR_WINDOW_SECONDS", "10")
    )

    def __post_init__(self) -> None:
        """
        Validate configuration after dataclass initialization.
        
        Logs a warning if the database URL is still the default; raises ValueError for invalid circuit-breaker numeric settings.
        
        Raises:
            ValueError: If any of the following are true:
                - `cb_error_threshold` is less than 1.
                - `cb_open_seconds` is less than or equal to 0.
                - `cb_probe_timeout_ms` is not between 1 and 50 (inclusive).
                - `cb_error_window_seconds` is less than 1.
        """
        if self.fraud_alerts_db_url == _DEFAULT_DB_URL:
            logger.warning(
                "FRAUD_ALERTS_DB_URL is using default credentials — "
                "set FRAUD_ALERTS_DB_URL in production."
            )
        if self.cb_error_threshold < 1:
            raise ValueError("cb_error_threshold must be >= 1")
        if self.cb_open_seconds <= 0:
            raise ValueError("cb_open_seconds must be > 0")
        if not (1 <= self.cb_probe_timeout_ms <= 50):
            raise ValueError("cb_probe_timeout_ms must be between 1 and 50")
        if self.cb_error_window_seconds < 1:
            raise ValueError("cb_error_window_seconds must be >= 1")
