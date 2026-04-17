"""Rule definition models for fraud detection rule engine."""

from __future__ import annotations

import re
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class RuleFamily(StrEnum):
    """Fraud rule families."""

    velocity = "velocity"
    impossible_travel = "impossible_travel"
    new_device = "new_device"


class RuleMode(StrEnum):
    """Rule execution mode."""

    active = "active"
    shadow = "shadow"


class Severity(IntEnum):
    """Rule severity levels with natural ordering support."""

    low = 0
    medium = 1
    high = 2
    critical = 3

    def __lt__(self, other: object) -> bool:
        """Compare severity levels.

        Args:
            other: Another Severity or int value to compare.

        Returns:
            True if this severity is less than other.

        Raises:
            TypeError: If other is not an int or Severity.
        """
        if isinstance(other, (int, Severity)):
            return int(self) < int(other)
        return NotImplemented

    def __le__(self, other: object) -> bool:
        """Compare severity levels.

        Args:
            other: Another Severity or int value to compare.

        Returns:
            True if this severity is less than or equal to other.

        Raises:
            TypeError: If other is not an int or Severity.
        """
        if isinstance(other, (int, Severity)):
            return int(self) <= int(other)
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        """Compare severity levels.

        Args:
            other: Another Severity or int value to compare.

        Returns:
            True if this severity is greater than other.

        Raises:
            TypeError: If other is not an int or Severity.
        """
        if isinstance(other, (int, Severity)):
            return int(self) > int(other)
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        """Compare severity levels.

        Args:
            other: Another Severity or int value to compare.

        Returns:
            True if this severity is greater than or equal to other.

        Raises:
            TypeError: If other is not an int or Severity.
        """
        if isinstance(other, (int, Severity)):
            return int(self) >= int(other)
        return NotImplemented


class RuleDefinition(BaseModel):
    """Pydantic v2 model for fraud rule definitions.

    Attributes:
        rule_id: Unique identifier for the rule.
        name: Human-readable rule name.
        family: Category of the rule (e.g., velocity, impossible_travel).
        conditions: Configuration parameters for rule evaluation.
        severity: Alert severity if rule is triggered.
        enabled: Whether the rule is active.

    Examples:
        >>> rule = RuleDefinition(
        ...     rule_id="rule_001",
        ...     name="High Velocity Rule",
        ...     family=RuleFamily.velocity,
        ...     conditions={"threshold": 10, "window_seconds": 300},
        ...     severity=Severity.high,
        ...     enabled=True,
        ... )
        >>> rule.severity > Severity.medium
        True
    """

    rule_id: str
    name: str

    @field_validator("rule_id", mode="before")
    @classmethod
    def validate_rule_id(cls, v: object) -> str:
        """Enforce safe rule ID format to prevent path traversal in webhook URLs."""
        if not isinstance(v, str):
            raise ValueError("rule_id must be a string")
        pattern = r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$"
        if not re.match(pattern, v):
            raise ValueError(
                "rule_id must be 2–64 chars, alphanumeric/hyphens/underscores, "
                "start and end with alphanumeric"
            )
        return v
    family: RuleFamily
    conditions: dict[str, Any]
    severity: Severity
    enabled: bool
    mode: RuleMode = RuleMode.active

    model_config = ConfigDict(extra="forbid")

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, v: object) -> int:
        """Accept string names (e.g. 'high') in addition to int values."""
        if isinstance(v, str):
            try:
                return Severity[v].value
            except KeyError:
                raise ValueError(f"Invalid severity: {v!r}") from None
        return v  # type: ignore[return-value]
