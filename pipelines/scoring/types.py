"""Type definitions for fraud detection rule engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
