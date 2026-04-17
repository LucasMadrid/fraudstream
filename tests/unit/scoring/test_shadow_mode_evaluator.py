"""Shadow mode unit tests for RuleEvaluator.

Tests shadow mode behavior: shadow rules fire counters and append ":shadow" suffix
to matched_rules but do NOT change the EvaluationResult.determination.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pipelines.scoring.rules.evaluator import RuleEvaluator
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, RuleMode, Severity


def _make_rule(
    rule_id: str,
    family: RuleFamily,
    severity: Severity,
    conditions: dict | None = None,
    enabled: bool = True,
    mode: RuleMode = RuleMode.active,
) -> RuleDefinition:
    """Helper to create RuleDefinition instances."""
    return RuleDefinition(
        rule_id=rule_id,
        name=rule_id,
        family=family,
        severity=severity,
        conditions=conditions or {},
        enabled=enabled,
        mode=mode,
    )


# Shadow mode rules
SHADOW_VEL_HIGH = _make_rule(
    "SHADOW-VEL-001",
    RuleFamily.velocity,
    Severity.high,
    {"field": "vel_count_1m", "count": 5},
    mode=RuleMode.shadow,
)

SHADOW_ND_CRITICAL = _make_rule(
    "SHADOW-ND-001",
    RuleFamily.new_device,
    Severity.critical,
    {},
    mode=RuleMode.shadow,
)

# Active mode rules
ACTIVE_VEL_HIGH = _make_rule(
    "ACTIVE-VEL-001",
    RuleFamily.velocity,
    Severity.high,
    {"field": "vel_count_1m", "count": 10},
    mode=RuleMode.active,
)

ACTIVE_VEL_MEDIUM = _make_rule(
    "ACTIVE-VEL-002",
    RuleFamily.velocity,
    Severity.medium,
    {"field": "vel_count_5m", "count": 20},
    mode=RuleMode.active,
)


@pytest.fixture
def mock_metrics():
    """Mock the metrics module functions."""
    with (
        patch("pipelines.scoring.rules.evaluator.record_evaluation") as mock_eval,
        patch("pipelines.scoring.rules.evaluator.record_flag") as mock_flag,
        patch("pipelines.scoring.rules.evaluator.record_shadow_trigger") as mock_shadow_trigger,
        patch("pipelines.scoring.rules.evaluator.record_shadow_fp") as mock_shadow_fp,
    ):
        yield {
            "record_evaluation": mock_eval,
            "record_flag": mock_flag,
            "record_shadow_trigger": mock_shadow_trigger,
            "record_shadow_fp": mock_shadow_fp,
        }


class TestShadowRuleDoesNotChangeDetermination:
    """Shadow rules fire counters but leave determination as 'clean'."""

    def test_shadow_rule_fires_but_determination_unchanged(self, mock_metrics):
        """When a shadow rule matches, determination remains 'clean'."""
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "clean"
        assert "SHADOW-VEL-001:shadow" in result.matched_rules

    def test_shadow_rule_with_no_active_rules_is_clean(self, mock_metrics):
        """Shadow-only rules do not make a clean transaction suspicious."""
        txn = {"device_known_fraud": True}
        result = RuleEvaluator([SHADOW_ND_CRITICAL]).dispatch(txn)

        assert result.determination == "clean"
        assert "SHADOW-ND-001:shadow" in result.matched_rules


class TestShadowRuleSuffixAppended:
    """Shadow matched rules are appended with ':shadow' suffix."""

    def test_shadow_rule_appends_colon_shadow_suffix(self, mock_metrics):
        """Matched shadow rules get ':shadow' suffix in matched_rules list."""
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert "SHADOW-VEL-001:shadow" in result.matched_rules
        # Ensure the raw rule_id is NOT in the list
        assert "SHADOW-VEL-001" not in result.matched_rules

    def test_multiple_shadow_rules_all_get_suffix(self, mock_metrics):
        """Multiple matched shadow rules each get ':shadow' suffix."""
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        result = RuleEvaluator([SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert "SHADOW-VEL-001:shadow" in result.matched_rules
        assert "SHADOW-ND-001:shadow" in result.matched_rules
        assert len(result.matched_rules) == 2


class TestActiveModeUnchanged:
    """Active rules work as before when mode field is present."""

    def test_active_rule_behavior_unchanged_with_mode_field(self, mock_metrics):
        """Active mode rules determine 'suspicious' as before."""
        txn = {"vel_count_1m": 11}
        result = RuleEvaluator([ACTIVE_VEL_HIGH]).dispatch(txn)

        assert result.determination == "suspicious"
        assert "ACTIVE-VEL-001" in result.matched_rules
        # Active rules should NOT have :shadow suffix
        assert "ACTIVE-VEL-001:shadow" not in result.matched_rules

    def test_active_rule_no_suffix_appended(self, mock_metrics):
        """Active matched rules have no ':shadow' suffix."""
        txn = {"vel_count_1m": 11}
        result = RuleEvaluator([ACTIVE_VEL_HIGH]).dispatch(txn)

        matched_rules = result.matched_rules
        for rule in matched_rules:
            assert not rule.endswith(":shadow"), (
                f"Active rule should not have :shadow suffix: {rule}"
            )


class TestMixedShadowAndActive:
    """Mix of shadow + active: active rule determines outcome, shadow rule adds :shadow entry."""

    def test_active_rules_determine_suspicious_when_shadow_also_fires(self, mock_metrics):
        """When active rule matches, result is 'suspicious', regardless of shadow rules."""
        txn = {"vel_count_1m": 6, "vel_count_5m": 21}
        # SHADOW-VEL-001 will match (threshold 5)
        # ACTIVE-VEL-002 will match (threshold 20)
        result = RuleEvaluator([SHADOW_VEL_HIGH, ACTIVE_VEL_MEDIUM]).dispatch(txn)

        assert result.determination == "suspicious"
        assert "ACTIVE-VEL-002" in result.matched_rules
        assert "SHADOW-VEL-001:shadow" in result.matched_rules

    def test_shadow_rule_appended_with_active_rules(self, mock_metrics):
        """matched_rules contains both active rule IDs and shadow IDs with :shadow."""
        txn = {"vel_count_1m": 11, "vel_count_5m": 21}
        # ACTIVE-VEL-001 will match (threshold 10)
        # ACTIVE-VEL-002 will match (threshold 20)
        # SHADOW-VEL-001 will match (threshold 5)
        result = RuleEvaluator([SHADOW_VEL_HIGH, ACTIVE_VEL_HIGH, ACTIVE_VEL_MEDIUM]).dispatch(txn)

        assert result.determination == "suspicious"
        assert "ACTIVE-VEL-001" in result.matched_rules
        assert "ACTIVE-VEL-002" in result.matched_rules
        assert "SHADOW-VEL-001:shadow" in result.matched_rules


class TestShadowTriggerCounter:
    """Shadow rules increment rule_shadow_triggers_total counter."""

    def test_shadow_rule_increments_shadow_trigger_counter(self, mock_metrics):
        """Matched shadow rule calls record_shadow_trigger."""
        txn = {"vel_count_1m": 6}
        RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        mock_metrics["record_shadow_trigger"].assert_called_once_with("SHADOW-VEL-001")

    def test_multiple_shadow_rules_each_increment_counter(self, mock_metrics):
        """Multiple matched shadow rules each call record_shadow_trigger."""
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        RuleEvaluator([SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert mock_metrics["record_shadow_trigger"].call_count == 2
        calls = mock_metrics["record_shadow_trigger"].call_args_list
        called_rules = {call[0][0] for call in calls}
        assert "SHADOW-VEL-001" in called_rules
        assert "SHADOW-ND-001" in called_rules

    def test_shadow_trigger_not_called_for_unmatched_shadow_rules(self, mock_metrics):
        """Unmatched shadow rules do not call record_shadow_trigger."""
        txn = {"vel_count_1m": 3}  # Below threshold of 5
        RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        mock_metrics["record_shadow_trigger"].assert_not_called()

    def test_active_rules_do_not_call_shadow_trigger(self, mock_metrics):
        """Active rules call record_flag, not record_shadow_trigger."""
        txn = {"vel_count_1m": 11}
        RuleEvaluator([ACTIVE_VEL_HIGH]).dispatch(txn)

        mock_metrics["record_shadow_trigger"].assert_not_called()


class TestShadowFPCounter:
    """Shadow rules increment counters for false positives vs true positives."""

    def test_shadow_rule_matching_clean_transaction_increments_fp_counter(self, mock_metrics):
        """Shadow rule matching a 'clean' transaction increments rule_shadow_fp_total."""
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "clean"
        mock_metrics["record_shadow_fp"].assert_called_once_with("SHADOW-VEL-001")

    def test_multiple_shadow_rules_clean_txn_all_increment_fp_counter(self, mock_metrics):
        """Multiple shadow rules matching clean transaction each increment FP counter."""
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        result = RuleEvaluator([SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert result.determination == "clean"
        assert mock_metrics["record_shadow_fp"].call_count == 2
        calls = mock_metrics["record_shadow_fp"].call_args_list
        called_rules = {call[0][0] for call in calls}
        assert "SHADOW-VEL-001" in called_rules
        assert "SHADOW-ND-001" in called_rules

    def test_shadow_rule_matching_suspicious_transaction_does_not_increment_fp_counter(
        self, mock_metrics
    ):
        """Shadow rule matching with active rule does NOT increment FP counter.

        When determination='suspicious' (due to active rule), the shadow rule
        that also matched is a true positive, not a false positive.
        """
        txn = {"vel_count_1m": 6, "vel_count_5m": 21}
        # SHADOW-VEL-001 will match (threshold 5)
        # ACTIVE-VEL-002 will match (threshold 20) → determination='suspicious'
        result = RuleEvaluator([SHADOW_VEL_HIGH, ACTIVE_VEL_MEDIUM]).dispatch(txn)

        assert result.determination == "suspicious"
        # FP counter should NOT be called because determination is suspicious
        mock_metrics["record_shadow_fp"].assert_not_called()

    def test_shadow_rule_not_matching_does_not_increment_fp_counter(self, mock_metrics):
        """Unmatched shadow rules do not increment FP counter."""
        txn = {"vel_count_1m": 3}  # Below threshold of 5
        result = RuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "clean"
        mock_metrics["record_shadow_fp"].assert_not_called()


class TestHighestSeverityWithShadowRules:
    """Shadow rules do not contribute to highest_severity."""

    def test_shadow_rule_does_not_set_highest_severity(self, mock_metrics):
        """Shadow rules (even critical) do not set highest_severity."""
        txn = {"device_known_fraud": True}
        result = RuleEvaluator([SHADOW_ND_CRITICAL]).dispatch(txn)

        assert result.determination == "clean"
        assert result.highest_severity is None

    def test_active_rule_sets_highest_severity_not_shadow(self, mock_metrics):
        """Only active rules contribute to highest_severity calculation."""
        txn = {"vel_count_1m": 11, "device_known_fraud": True}
        # ACTIVE-VEL-001 (high severity) will match
        # SHADOW-ND-CRITICAL (critical severity) will match
        # But only ACTIVE-VEL-001 should contribute to highest_severity
        result = RuleEvaluator([ACTIVE_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert result.determination == "suspicious"
        assert result.highest_severity == "high"


class TestMissingFieldsWithShadowRules:
    """missing_fields always empty (consistent with existing behavior)."""

    def test_missing_fields_always_empty(self, mock_metrics):
        """missing_fields list is always empty in current implementation."""
        txn = {}
        result = RuleEvaluator([SHADOW_VEL_HIGH, ACTIVE_VEL_HIGH]).dispatch(txn)

        assert result.missing_fields == []
