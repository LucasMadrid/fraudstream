"""Type definitions for fraud detection rule engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class FallbackReason(Enum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FeatureVector:
    account_id: str
    vel_count_1m: int
    vel_amount_1m: float
    vel_count_5m: int
    vel_amount_5m: float
    vel_count_1h: int
    vel_amount_1h: float
    vel_count_24h: int
    vel_amount_24h: float
    geo_country: str
    geo_city: str
    geo_network_class: str
    geo_confidence: float
    device_first_seen: int
    device_txn_count: int
    device_known_fraud: bool
    prev_geo_country: str
    prev_txn_time_ms: int


ZERO_FEATURE_VECTOR = FeatureVector(
    account_id="",
    vel_count_1m=0,
    vel_amount_1m=0.0,
    vel_count_5m=0,
    vel_amount_5m=0.0,
    vel_count_1h=0,
    vel_amount_1h=0.0,
    vel_count_24h=0,
    vel_amount_24h=0.0,
    geo_country="",
    geo_city="",
    geo_network_class="",
    geo_confidence=0.0,
    device_first_seen=0,
    device_txn_count=0,
    device_known_fraud=False,
    prev_geo_country="",
    prev_txn_time_ms=0,
)


@dataclass
class EvaluationResult:
    """Result of rule evaluation for a transaction.

    Attributes:
        determination: Classification outcome ("clean" or "suspicious").
        matched_rules: List of rule IDs that triggered.
        highest_severity: The highest severity level from matched rules, or None if clean.
        evaluation_timestamp: Evaluation time in epoch milliseconds.
        missing_fields: Transaction fields required for rules but not provided.

    Examples:
        >>> result = EvaluationResult(
        ...     determination="suspicious",
        ...     matched_rules=["rule_001", "rule_003"],
        ...     highest_severity="high",
        ...     evaluation_timestamp=1704067200000,
        ...     missing_fields=[],
        ... )
        >>> result.determination
        'suspicious'
    """

    determination: Literal["clean", "suspicious"]
    matched_rules: list[str]
    highest_severity: str | None
    evaluation_timestamp: int
    missing_fields: list[str]


@dataclass
class FraudAlert:
    """Alert for a suspicious transaction.

    Attributes:
        transaction_id: Unique transaction identifier.
        account_id: Account owner identifier.
        matched_rule_names: Names of rules that triggered.
        severity: Highest severity level from matched rules.
        evaluation_timestamp: Alert creation time in epoch milliseconds.

    Examples:
        >>> alert = FraudAlert(
        ...     transaction_id="txn_123",
        ...     account_id="acc_456",
        ...     matched_rule_names=["High Velocity", "New Device"],
        ...     severity="high",
        ...     evaluation_timestamp=1704067200000,
        ... )
        >>> alert.severity
        'high'
    """

    transaction_id: str
    account_id: str
    matched_rule_names: list[str]
    severity: str
    evaluation_timestamp: int


@dataclass
class FraudAlertRecord:
    """Persistent record of a fraud alert in the database.

    Extends FraudAlert with persistence metadata.

    Attributes:
        transaction_id: Unique transaction identifier.
        account_id: Account owner identifier.
        matched_rule_names: Names of rules that triggered.
        severity: Highest severity level from matched rules.
        evaluation_timestamp: Alert creation time in epoch milliseconds.
        status: Alert status ("pending", "reviewed", "resolved", etc.).

    Examples:
        >>> record = FraudAlertRecord(
        ...     transaction_id="txn_123",
        ...     account_id="acc_456",
        ...     matched_rule_names=["High Velocity"],
        ...     severity="medium",
        ...     evaluation_timestamp=1704067200000,
        ...     status="pending",
        ... )
        >>> record.status
        'pending'
    """

    transaction_id: str
    account_id: str
    matched_rule_names: list[str]
    severity: str
    evaluation_timestamp: int
    status: str = "pending"


@dataclass(frozen=True)
class FraudDecision:
    """Permanent record of every fraud scoring decision written to
    iceberg.fraud_decisions.

    Persisted for every ALLOW/FLAG/BLOCK outcome. Never dropped.
    fraud_score is in [0.0, 1.0]. rule_triggers is empty list (not null) when
    no rules fired.
    """

    transaction_id: str
    decision: Literal["ALLOW", "FLAG", "BLOCK"]
    fraud_score: float
    rule_triggers: list[str]
    model_version: str
    decision_time_ms: int
    latency_ms: float
    schema_version: str = "1"
