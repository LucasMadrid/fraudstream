"""Unit tests for fraud detection rule engine models and types."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, Severity
from pipelines.scoring.types import EvaluationResult, FraudAlert, FraudAlertRecord


class TestSeverity:
    """Test Severity enum comparisons."""

    def test_severity_values(self) -> None:
        """Verify severity enum has correct integer values."""
        assert Severity.low == 0
        assert Severity.medium == 1
        assert Severity.high == 2
        assert Severity.critical == 3

    def test_severity_ordering(self) -> None:
        """Verify severity ordering comparisons."""
        assert Severity.low < Severity.medium
        assert Severity.medium < Severity.high
        assert Severity.high < Severity.critical
        assert Severity.low < Severity.critical

    def test_severity_greater_than(self) -> None:
        """Verify severity greater-than comparisons."""
        assert Severity.critical > Severity.high
        assert Severity.high > Severity.medium
        assert Severity.medium > Severity.low
        assert Severity.critical > Severity.low

    def test_severity_le_ge(self) -> None:
        """Verify severity less-than-or-equal and greater-than-or-equal."""
        assert Severity.low <= Severity.low
        assert Severity.low <= Severity.medium
        assert Severity.critical >= Severity.critical
        assert Severity.critical >= Severity.low

    def test_severity_with_max(self) -> None:
        """Verify max() works with severity levels."""
        severities = [Severity.low, Severity.critical, Severity.medium, Severity.high]
        assert max(severities) == Severity.critical

    def test_severity_with_min(self) -> None:
        """Verify min() works with severity levels."""
        severities = [Severity.low, Severity.critical, Severity.medium, Severity.high]
        assert min(severities) == Severity.low

    def test_severity_int_comparison(self) -> None:
        """Verify severity can compare with raw integers."""
        assert Severity.low < 1
        assert Severity.critical > 2
        assert Severity.medium <= 1
        assert Severity.high >= 2


class TestRuleFamily:
    """Test RuleFamily enum."""

    def test_rule_family_values(self) -> None:
        """Verify rule family enum values."""
        assert RuleFamily.velocity == "velocity"
        assert RuleFamily.impossible_travel == "impossible_travel"
        assert RuleFamily.new_device == "new_device"

    def test_rule_family_from_string(self) -> None:
        """Verify rule family can be created from string."""
        family = RuleFamily("velocity")
        assert family == RuleFamily.velocity


class TestRuleDefinition:
    """Test RuleDefinition pydantic model."""

    def test_valid_rule_definition(self) -> None:
        """Verify creating a valid RuleDefinition."""
        rule = RuleDefinition(
            rule_id="rule_001",
            name="High Velocity Rule",
            family=RuleFamily.velocity,
            conditions={"threshold": 10, "window_seconds": 300},
            severity=Severity.high,
            enabled=True,
        )
        assert rule.rule_id == "rule_001"
        assert rule.name == "High Velocity Rule"
        assert rule.family == RuleFamily.velocity
        assert rule.severity == Severity.high
        assert rule.enabled is True
        assert rule.conditions["threshold"] == 10

    def test_rule_definition_with_string_family(self) -> None:
        """Verify RuleDefinition accepts family as string."""
        rule = RuleDefinition(
            rule_id="rule_002",
            name="Impossible Travel Rule",
            family="impossible_travel",  # type: ignore
            conditions={"distance_threshold_km": 500},
            severity=Severity.critical,
            enabled=True,
        )
        assert rule.family == RuleFamily.impossible_travel

    def test_rule_definition_forbid_extra_fields(self) -> None:
        """Verify extra fields are forbidden by model_config."""
        with pytest.raises(ValidationError):
            RuleDefinition(
                rule_id="rule_003",
                name="New Device Rule",
                family=RuleFamily.new_device,
                conditions={},
                severity=Severity.medium,
                enabled=True,
                extra_field="should fail",  # type: ignore
            )

    def test_rule_definition_missing_required_field(self) -> None:
        """Verify missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            RuleDefinition(
                rule_id="rule_004",
                name="Incomplete Rule",
                family=RuleFamily.velocity,
                severity=Severity.low,
                enabled=True,
                # Missing 'conditions' field
            )

    def test_rule_definition_complex_conditions(self) -> None:
        """Verify conditions dict can hold complex data."""
        complex_conditions: dict[str, Any] = {
            "threshold": 10,
            "window_seconds": 300,
            "nested": {"key": "value"},
            "list": [1, 2, 3],
        }
        rule = RuleDefinition(
            rule_id="rule_005",
            name="Complex Rule",
            family=RuleFamily.velocity,
            conditions=complex_conditions,
            severity=Severity.medium,
            enabled=False,
        )
        assert rule.conditions == complex_conditions


class TestEvaluationResult:
    """Test EvaluationResult dataclass."""

    def test_evaluation_result_clean(self) -> None:
        """Verify creating a clean EvaluationResult."""
        result = EvaluationResult(
            determination="clean",
            matched_rules=[],
            highest_severity=None,
            evaluation_timestamp=1704067200000,
            missing_fields=[],
        )
        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None
        assert result.evaluation_timestamp == 1704067200000
        assert result.missing_fields == []

    def test_evaluation_result_suspicious(self) -> None:
        """Verify creating a suspicious EvaluationResult."""
        result = EvaluationResult(
            determination="suspicious",
            matched_rules=["rule_001", "rule_003"],
            highest_severity="high",
            evaluation_timestamp=1704067200000,
            missing_fields=[],
        )
        assert result.determination == "suspicious"
        assert result.matched_rules == ["rule_001", "rule_003"]
        assert result.highest_severity == "high"
        assert len(result.missing_fields) == 0

    def test_evaluation_result_with_missing_fields(self) -> None:
        """Verify EvaluationResult tracks missing transaction fields."""
        result = EvaluationResult(
            determination="suspicious",
            matched_rules=["rule_002"],
            highest_severity="medium",
            evaluation_timestamp=1704067200000,
            missing_fields=["location", "device_id"],
        )
        assert result.missing_fields == ["location", "device_id"]


class TestFraudAlert:
    """Test FraudAlert dataclass."""

    def test_fraud_alert_creation(self) -> None:
        """Verify creating a FraudAlert."""
        alert = FraudAlert(
            transaction_id="txn_123",
            account_id="acc_456",
            matched_rule_names=["High Velocity", "New Device"],
            severity="high",
            evaluation_timestamp=1704067200000,
        )
        assert alert.transaction_id == "txn_123"
        assert alert.account_id == "acc_456"
        assert alert.matched_rule_names == ["High Velocity", "New Device"]
        assert alert.severity == "high"
        assert alert.evaluation_timestamp == 1704067200000

    def test_fraud_alert_single_rule(self) -> None:
        """Verify FraudAlert with a single matched rule."""
        alert = FraudAlert(
            transaction_id="txn_456",
            account_id="acc_789",
            matched_rule_names=["Impossible Travel"],
            severity="critical",
            evaluation_timestamp=1704067300000,
        )
        assert len(alert.matched_rule_names) == 1
        assert alert.matched_rule_names[0] == "Impossible Travel"


class TestFraudAlertRecord:
    """Test FraudAlertRecord dataclass."""

    def test_fraud_alert_record_default_status(self) -> None:
        """Verify FraudAlertRecord defaults status to pending."""
        record = FraudAlertRecord(
            transaction_id="txn_789",
            account_id="acc_012",
            matched_rule_names=["High Velocity"],
            severity="medium",
            evaluation_timestamp=1704067400000,
        )
        assert record.status == "pending"
        assert record.transaction_id == "txn_789"
        assert record.account_id == "acc_012"

    def test_fraud_alert_record_custom_status(self) -> None:
        """Verify FraudAlertRecord accepts custom status."""
        record = FraudAlertRecord(
            transaction_id="txn_999",
            account_id="acc_345",
            matched_rule_names=["New Device"],
            severity="low",
            evaluation_timestamp=1704067500000,
            status="reviewed",
        )
        assert record.status == "reviewed"

    def test_fraud_alert_record_multiple_statuses(self) -> None:
        """Verify FraudAlertRecord can have different status values."""
        statuses = ["pending", "reviewed", "resolved", "false_positive"]
        for status in statuses:
            record = FraudAlertRecord(
                transaction_id=f"txn_{status}",
                account_id="acc_multi",
                matched_rule_names=[],
                severity="low",
                evaluation_timestamp=1704067600000,
                status=status,
            )
            assert record.status == status


class TestPackageExports:
    """Test that types are correctly exported from scoring package."""

    def test_evaluation_result_import_from_package(self) -> None:
        """Verify EvaluationResult is exported from scoring package."""
        # Already imported at top of file
        result = EvaluationResult(
            determination="clean",
            matched_rules=[],
            highest_severity=None,
            evaluation_timestamp=1704067200000,
            missing_fields=[],
        )
        assert result.determination == "clean"

    def test_fraud_alert_import_from_package(self) -> None:
        """Verify FraudAlert is exported from scoring package."""
        # Already imported at top of file
        alert = FraudAlert(
            transaction_id="txn_pkg",
            account_id="acc_pkg",
            matched_rule_names=[],
            severity="low",
            evaluation_timestamp=1704067700000,
        )
        assert alert.transaction_id == "txn_pkg"

    def test_fraud_alert_record_import_from_package(self) -> None:
        """Verify FraudAlertRecord is exported from scoring package."""
        # Already imported at top of file
        record = FraudAlertRecord(
            transaction_id="txn_rec",
            account_id="acc_rec",
            matched_rule_names=[],
            severity="low",
            evaluation_timestamp=1704067800000,
        )
        assert record.status == "pending"
