"""Comprehensive TDD tests for rule definition model and rule loader.

Test Coverage:
- T010: RuleDefinition pydantic v2 model validation
- T011: RuleLoader YAML parsing and filtering
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from pipelines.scoring.rules.loader import RuleConfigError, RuleLoader
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, Severity

# =============================================================================
# T010 — RuleDefinition Model Tests
# =============================================================================


class TestRuleDefinitionConstruction:
    """Test RuleDefinition model construction and validation."""

    def test_valid_rule_definition_constructs(self) -> None:
        """A complete valid dict model_validates successfully without error."""
        valid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5, "window_seconds": 60},
            "severity": "high",
            "enabled": True,
        }
        rule = RuleDefinition.model_validate(valid_rule)
        assert rule.rule_id == "VEL-001"
        assert rule.name == "HIGH_FREQUENCY_1M"
        assert rule.family == RuleFamily.velocity
        assert rule.conditions == {"threshold": 5, "window_seconds": 60}
        assert rule.severity == Severity.high
        assert rule.enabled is True

    def test_missing_severity_raises_validation_error(self) -> None:
        """A dict without severity field raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "enabled": True,
            # Missing: severity
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_invalid_severity_value_raises_validation_error(self) -> None:
        """A rule with an invalid severity value raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "catastrophic",  # Invalid enum value
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_unknown_extra_field_raises_validation_error(self) -> None:
        """A rule with an extra field raises ValidationError (extra='forbid')."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": True,
            "unknown_field": "should_fail",  # Extra field not in schema
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_enabled_false_is_valid(self) -> None:
        """A rule with enabled=False constructs without error."""
        disabled_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": False,
        }
        rule = RuleDefinition.model_validate(disabled_rule)
        assert rule.enabled is False

    def test_missing_rule_id_raises_validation_error(self) -> None:
        """A rule without rule_id raises ValidationError."""
        invalid_rule = {
            # Missing: rule_id
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_missing_name_raises_validation_error(self) -> None:
        """A rule without name raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            # Missing: name
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_missing_family_raises_validation_error(self) -> None:
        """A rule without family raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            # Missing: family
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_missing_conditions_raises_validation_error(self) -> None:
        """A rule without conditions raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            # Missing: conditions
            "severity": "high",
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_missing_enabled_raises_validation_error(self) -> None:
        """A rule without enabled raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "velocity",
            "conditions": {"threshold": 5},
            "severity": "high",
            # Missing: enabled
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_invalid_family_value_raises_validation_error(self) -> None:
        """A rule with an invalid family value raises ValidationError."""
        invalid_rule = {
            "rule_id": "VEL-001",
            "name": "HIGH_FREQUENCY_1M",
            "family": "unknown_family",  # Invalid enum value
            "conditions": {"threshold": 5},
            "severity": "high",
            "enabled": True,
        }
        with pytest.raises(ValidationError):
            RuleDefinition.model_validate(invalid_rule)

    def test_empty_conditions_dict_is_valid(self) -> None:
        """A rule with empty conditions dict is valid (for boolean flag rules)."""
        rule_dict = {
            "rule_id": "ND-003",
            "name": "KNOWN_FRAUD_DEVICE",
            "family": "new_device",
            "conditions": {},
            "severity": "critical",
            "enabled": True,
        }
        rule = RuleDefinition.model_validate(rule_dict)
        assert rule.conditions == {}

    def test_conditions_with_complex_values(self) -> None:
        """A rule with complex nested conditions is valid."""
        rule_dict = {
            "rule_id": "CUSTOM-001",
            "name": "COMPLEX_RULE",
            "family": "velocity",
            "conditions": {
                "threshold": 100,
                "window_ms": 3600000,
                "nested": {"key": "value", "count": 5},
                "list": [1, 2, 3],
            },
            "severity": "medium",
            "enabled": True,
        }
        rule = RuleDefinition.model_validate(rule_dict)
        assert rule.conditions["nested"]["key"] == "value"
        assert rule.conditions["list"] == [1, 2, 3]


class TestSeverityOrdering:
    """Test Severity enum comparison operations."""

    def test_severity_critical_greater_than_high(self) -> None:
        """Severity.critical > Severity.high."""
        assert Severity.critical > Severity.high

    def test_severity_high_greater_than_medium(self) -> None:
        """Severity.high > Severity.medium."""
        assert Severity.high > Severity.medium

    def test_severity_medium_greater_than_low(self) -> None:
        """Severity.medium > Severity.low."""
        assert Severity.medium > Severity.low

    def test_severity_critical_greater_than_low(self) -> None:
        """Severity.critical > Severity.low (transitivity)."""
        assert Severity.critical > Severity.low

    def test_severity_ordering_chain(self) -> None:
        """All severity levels are ordered: critical > high > medium > low."""
        assert Severity.critical > Severity.high
        assert Severity.high > Severity.medium
        assert Severity.medium > Severity.low

    def test_severity_equal_to_itself(self) -> None:
        """Severity.high == Severity.high."""
        assert Severity.high == Severity.high

    def test_severity_max_returns_critical(self) -> None:
        """max([Severity.low, Severity.critical, Severity.medium]) == Severity.critical."""
        severities = [Severity.low, Severity.critical, Severity.medium, Severity.high]
        assert max(severities) == Severity.critical

    def test_severity_min_returns_low(self) -> None:
        """min([Severity.low, Severity.critical, Severity.medium]) == Severity.low."""
        severities = [Severity.low, Severity.critical, Severity.medium, Severity.high]
        assert min(severities) == Severity.low

    def test_severity_less_than_comparison(self) -> None:
        """Severity.low < Severity.medium."""
        assert Severity.low < Severity.medium

    def test_severity_less_equal_comparison(self) -> None:
        """Severity.low <= Severity.low and Severity.low <= Severity.medium."""
        assert Severity.low <= Severity.low
        assert Severity.low <= Severity.medium

    def test_severity_greater_equal_comparison(self) -> None:
        """Severity.high >= Severity.high and Severity.high >= Severity.low."""
        assert Severity.high >= Severity.high
        assert Severity.high >= Severity.low


# =============================================================================
# T011 — RuleLoader Tests
# =============================================================================


class TestRuleLoaderValidYAML:
    """Test RuleLoader.load() with valid YAML configurations."""

    def test_load_valid_yaml_returns_enabled_rules(self, tmp_path: Path) -> None:
        """Load YAML with 2 enabled + 1 disabled rule; assert exactly 2 enabled rules returned."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "Rule1"
  family: "velocity"
  conditions: {threshold: 5}
  severity: "high"
  enabled: true
-
  rule_id: "VEL-002"
  name: "Rule2"
  family: "velocity"
  conditions: {threshold: 10}
  severity: "medium"
  enabled: true
-
  rule_id: "VEL-003"
  name: "DisabledRule"
  family: "velocity"
  conditions: {threshold: 15}
  severity: "low"
  enabled: false
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 2
        assert all(r.enabled for r in rules)
        assert rules[0].rule_id == "VEL-001"
        assert rules[1].rule_id == "VEL-002"

    def test_load_returns_only_enabled_rules_filters_disabled(self, tmp_path: Path) -> None:
        """When YAML has only disabled rules, load() returns empty list."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "DisabledRule1"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: false
-
  rule_id: "VEL-002"
  name: "DisabledRule2"
  family: "velocity"
  conditions: {}
  severity: "medium"
  enabled: false
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 0

    def test_load_single_enabled_rule(self, tmp_path: Path) -> None:
        """Load YAML with one enabled rule; assert load() returns list with one rule."""
        yaml_content = """-
  rule_id: "IT-001"
  name: "IMPOSSIBLE_TRAVEL"
  family: "impossible_travel"
  conditions: {window_ms: 3600000}
  severity: "critical"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 1
        assert rules[0].rule_id == "IT-001"
        assert rules[0].family == RuleFamily.impossible_travel
        assert rules[0].severity == Severity.critical

    def test_load_empty_list_returns_empty(self, tmp_path: Path) -> None:
        """Load YAML with empty list; assert load() returns empty list."""
        yaml_content = "[]"
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 0

    def test_load_preserves_rule_properties(self, tmp_path: Path) -> None:
        """Load a rule and verify all properties are correctly assigned."""
        yaml_content = """-
  rule_id: "ND-001"
  name: "NEW_DEVICE_HIGH_AMOUNT"
  family: "new_device"
  conditions: {amount: 500.00, threshold: 10}
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 1
        rule = rules[0]
        assert rule.rule_id == "ND-001"
        assert rule.name == "NEW_DEVICE_HIGH_AMOUNT"
        assert rule.family == RuleFamily.new_device
        assert rule.severity == Severity.high
        assert rule.enabled is True
        assert rule.conditions["amount"] == 500.00

    def test_load_with_empty_conditions(self, tmp_path: Path) -> None:
        """Load a rule with empty conditions dict (boolean flag rule)."""
        yaml_content = """-
  rule_id: "ND-003"
  name: "KNOWN_FRAUD_DEVICE"
  family: "new_device"
  conditions: {}
  severity: "critical"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 1
        assert rules[0].conditions == {}


class TestRuleLoaderErrorHandling:
    """Test RuleLoader.load() error cases."""

    def test_file_not_found_raises_rule_config_error(self) -> None:
        """Non-existent path raises RuleConfigError with path in message."""
        with pytest.raises(RuleConfigError, match="/nonexistent/path"):
            RuleLoader.load("/nonexistent/path/rules.yaml")

    def test_malformed_yaml_raises_rule_config_error(self, tmp_path: Path) -> None:
        """Invalid YAML bytes raises RuleConfigError with 'Malformed' in message."""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("{ invalid yaml: [")  # Invalid YAML syntax

        with pytest.raises(RuleConfigError, match="Malformed"):
            RuleLoader.load(str(yaml_file))

    def test_missing_required_field_raises_rule_config_error(self, tmp_path: Path) -> None:
        """YAML rule missing a required field raises RuleConfigError with index in message."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "IncompleteRule"
  family: "velocity"
  conditions: {}
  # Missing: severity and enabled
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index"):
            RuleLoader.load(str(yaml_file))

    def test_yaml_top_level_not_list_raises_rule_config_error(self, tmp_path: Path) -> None:
        """YAML that is a dict (not list) raises RuleConfigError."""
        yaml_content = """
rules:
  - rule_id: "VEL-001"
    name: "Rule1"
    family: "velocity"
    conditions: {}
    severity: "high"
    enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError):
            RuleLoader.load(str(yaml_file))

    def test_invalid_rule_family_raises_rule_config_error(self, tmp_path: Path) -> None:
        """A rule with invalid family value raises RuleConfigError."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "BadFamilyRule"
  family: "unknown_family"
  conditions: {}
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 0"):
            RuleLoader.load(str(yaml_file))

    def test_invalid_rule_severity_raises_rule_config_error(self, tmp_path: Path) -> None:
        """A rule with invalid severity value raises RuleConfigError."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "BadSeverityRule"
  family: "velocity"
  conditions: {}
  severity: "catastrophic"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 0"):
            RuleLoader.load(str(yaml_file))

    def test_missing_rule_id_in_yaml_raises_rule_config_error(self, tmp_path: Path) -> None:
        """A rule without rule_id in YAML raises RuleConfigError."""
        yaml_content = """-
  name: "MissingIDRule"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 0"):
            RuleLoader.load(str(yaml_file))

    def test_missing_name_in_yaml_raises_rule_config_error(self, tmp_path: Path) -> None:
        """A rule without name in YAML raises RuleConfigError."""
        yaml_content = """-
  rule_id: "VEL-001"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 0"):
            RuleLoader.load(str(yaml_file))

    def test_extra_field_in_yaml_raises_rule_config_error(self, tmp_path: Path) -> None:
        """A rule with an extra field in YAML raises RuleConfigError."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "Rule1"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
  unknown_field: "should_fail"
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 0"):
            RuleLoader.load(str(yaml_file))

    def test_null_content_in_yaml_raises_rule_config_error(self, tmp_path: Path) -> None:
        """YAML with null content (empty file) raises RuleConfigError."""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("")  # Empty file

        with pytest.raises(RuleConfigError):
            RuleLoader.load(str(yaml_file))

    def test_yaml_null_is_not_list_raises_rule_config_error(self, tmp_path: Path) -> None:
        """YAML containing only null raises RuleConfigError."""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("null")

        with pytest.raises(RuleConfigError):
            RuleLoader.load(str(yaml_file))

    def test_yaml_string_is_not_list_raises_rule_config_error(self, tmp_path: Path) -> None:
        """YAML containing only a string raises RuleConfigError."""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("just a string")

        with pytest.raises(RuleConfigError):
            RuleLoader.load(str(yaml_file))


class TestRuleLoaderEdgeCases:
    """Test RuleLoader with edge cases and boundary conditions."""

    def test_load_many_enabled_rules(self, tmp_path: Path) -> None:
        """Load YAML with many rules (10+); assert all enabled rules are returned."""
        rules_list = []
        for i in range(1, 11):
            rules_list.append(
                f"""
-
  rule_id: "VEL-{i:03d}"
  name: "Rule{i}"
  family: "velocity"
  conditions: {{threshold: {i * 10}}}
  severity: "high"
  enabled: true
"""
            )
        yaml_content = "".join(rules_list)
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 10
        rule_ids = [r.rule_id for r in rules]
        assert all(f"VEL-{i:03d}" in rule_ids for i in range(1, 11))

    def test_load_mixed_enabled_disabled_many_rules(self, tmp_path: Path) -> None:
        """Load YAML with 20 rules (half enabled, half disabled); assert 10 enabled returned."""
        rules_list = []
        for i in range(1, 21):
            enabled = i % 2 == 1  # Odd indices enabled, even disabled
            rules_list.append(
                f"""
-
  rule_id: "RULE-{i:03d}"
  name: "Rule{i}"
  family: "velocity"
  conditions: {{}}
  severity: "high"
  enabled: {str(enabled).lower()}
"""
            )
        yaml_content = "".join(rules_list)
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 10
        assert all(r.enabled for r in rules)

    def test_load_rules_with_different_families(self, tmp_path: Path) -> None:
        """Load YAML with rules from all three families; assert all are returned."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "Velocity"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
-
  rule_id: "IT-001"
  name: "ImpossibleTravel"
  family: "impossible_travel"
  conditions: {}
  severity: "critical"
  enabled: true
-
  rule_id: "ND-001"
  name: "NewDevice"
  family: "new_device"
  conditions: {}
  severity: "medium"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 3
        families = {r.family for r in rules}
        assert families == {
            RuleFamily.velocity,
            RuleFamily.impossible_travel,
            RuleFamily.new_device,
        }

    def test_load_rules_with_all_severity_levels(self, tmp_path: Path) -> None:
        """Load YAML with rules at all four severity levels; assert all are returned."""
        yaml_content = """-
  rule_id: "LOW-001"
  name: "LowRule"
  family: "velocity"
  conditions: {}
  severity: "low"
  enabled: true
-
  rule_id: "MED-001"
  name: "MediumRule"
  family: "velocity"
  conditions: {}
  severity: "medium"
  enabled: true
-
  rule_id: "HIGH-001"
  name: "HighRule"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
-
  rule_id: "CRIT-001"
  name: "CriticalRule"
  family: "velocity"
  conditions: {}
  severity: "critical"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 4
        severities = {r.severity for r in rules}
        assert severities == {
            Severity.low,
            Severity.medium,
            Severity.high,
            Severity.critical,
        }

    def test_load_rule_with_complex_nested_conditions(self, tmp_path: Path) -> None:
        """Load a rule with complex nested conditions dict; assert conditions preserved."""
        yaml_content = """-
  rule_id: "COMPLEX-001"
  name: "ComplexRule"
  family: "velocity"
  conditions:
    threshold: 100
    window_ms: 3600000
    nested:
      key1: value1
      key2: 42
    list_values: [1, 2, 3]
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 1
        rule = rules[0]
        assert rule.conditions["threshold"] == 100
        assert rule.conditions["window_ms"] == 3600000
        assert rule.conditions["nested"]["key1"] == "value1"
        assert rule.conditions["nested"]["key2"] == 42
        assert rule.conditions["list_values"] == [1, 2, 3]

    def test_yaml_with_comments_and_whitespace(self, tmp_path: Path) -> None:
        """Load YAML with comments and extra whitespace; assert parsing succeeds."""
        yaml_content = """# This is a comment about velocity rules

-
  rule_id: "VEL-001"  # First velocity rule
  name: "Rule1"
  family: "velocity"
  conditions: {threshold: 5}  # Threshold is 5
  severity: "high"
  enabled: true

# Disabled rule below
-
  rule_id: "VEL-002"
  name: "DisabledRule"
  family: "velocity"
  conditions: {}
  severity: "low"
  enabled: false
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        rules = RuleLoader.load(str(yaml_file))

        assert len(rules) == 1
        assert rules[0].rule_id == "VEL-001"

    def test_error_message_includes_file_path_on_file_not_found(self) -> None:
        """RuleConfigError for missing file includes the file path."""
        missing_path = "/this/path/does/not/exist.yaml"
        with pytest.raises(RuleConfigError, match=missing_path):
            RuleLoader.load(missing_path)

    def test_error_message_includes_index_on_validation_error(self, tmp_path: Path) -> None:
        """RuleConfigError for invalid rule includes the rule index."""
        yaml_content = """-
  rule_id: "VEL-001"
  name: "Valid"
  family: "velocity"
  conditions: {}
  severity: "high"
  enabled: true
-
  rule_id: "VEL-002"
  name: "Invalid"
  family: "invalid_family"
  conditions: {}
  severity: "high"
  enabled: true
"""
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(RuleConfigError, match="index 1"):
            RuleLoader.load(str(yaml_file))
