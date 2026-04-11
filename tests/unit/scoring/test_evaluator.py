"""TDD tests for RuleEvaluator.

Tests dispatch logic, severity ordering, and v1 cross-feature constraints.
"""

from __future__ import annotations

from decimal import Decimal

from pipelines.scoring.rules.evaluator import RuleEvaluator
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, Severity
from pipelines.scoring.types import EvaluationResult


def _make_rule(
    rule_id: str,
    family: RuleFamily,
    severity: Severity,
    conditions: dict | None = None,
    enabled: bool = True,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        name=rule_id,
        family=family,
        severity=severity,
        conditions=conditions or {},
        enabled=enabled,
    )


VEL_HIGH = _make_rule(
    "VEL-001", RuleFamily.velocity, Severity.high, {"field": "vel_count_1m", "count": 5}
)
VEL_MEDIUM = _make_rule(
    "VEL-003", RuleFamily.velocity, Severity.medium, {"field": "vel_count_5m", "count": 10}
)
ND_CRITICAL = _make_rule("ND-003", RuleFamily.new_device, Severity.critical, {})
IT_CRITICAL = _make_rule(
    "IT-001", RuleFamily.impossible_travel, Severity.critical, {"window_ms": 3600000}
)


class TestDispatchCleanTransaction:
    def test_no_rules_triggered_returns_clean(self):
        txn = {"vel_count_1m": 0, "vel_count_5m": 0}
        result = RuleEvaluator([VEL_HIGH, VEL_MEDIUM]).dispatch(txn)
        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None

    def test_empty_rules_list_returns_clean(self):
        result = RuleEvaluator([]).dispatch({})
        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None

    def test_returns_evaluation_result_type(self):
        result = RuleEvaluator([]).dispatch({})
        assert isinstance(result, EvaluationResult)

    def test_evaluation_timestamp_is_int(self):
        result = RuleEvaluator([]).dispatch({})
        assert isinstance(result.evaluation_timestamp, int)
        assert result.evaluation_timestamp > 0

    def test_missing_fields_is_empty_list(self):
        result = RuleEvaluator([]).dispatch({})
        assert result.missing_fields == []


class TestDispatchSuspiciousTransaction:
    def test_single_velocity_rule_triggered(self):
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([VEL_HIGH]).dispatch(txn)
        assert result.determination == "suspicious"
        assert "VEL-001" in result.matched_rules

    def test_matched_rules_contains_triggered_rule_id(self):
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([VEL_HIGH, VEL_MEDIUM]).dispatch(txn)
        assert "VEL-001" in result.matched_rules
        assert "VEL-003" not in result.matched_rules  # not exceeded


class TestSeverityOrdering:
    def test_highest_severity_critical_beats_high(self):
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        result = RuleEvaluator([VEL_HIGH, ND_CRITICAL]).dispatch(txn)
        assert result.highest_severity == "critical"

    def test_highest_severity_high_beats_medium(self):
        txn = {"vel_count_1m": 6, "vel_count_5m": 11}
        result = RuleEvaluator([VEL_HIGH, VEL_MEDIUM]).dispatch(txn)
        assert result.highest_severity == "high"

    def test_single_triggered_rule_severity(self):
        txn = {"vel_count_1m": 6}
        result = RuleEvaluator([VEL_HIGH]).dispatch(txn)
        assert result.highest_severity == "high"

    def test_all_severities_ordering(self):
        low = _make_rule(
            "R-LOW", RuleFamily.velocity, Severity.low, {"field": "vel_count_1m", "count": 0}
        )
        medium = _make_rule(
            "R-MED", RuleFamily.velocity, Severity.medium, {"field": "vel_count_5m", "count": 0}
        )
        high = _make_rule(
            "R-HIGH", RuleFamily.velocity, Severity.high, {"field": "vel_amount_5m", "amount": 0.00}
        )
        txn = {"vel_count_1m": 1, "vel_count_5m": 1, "vel_amount_5m": Decimal("0.01")}
        result = RuleEvaluator([low, medium, high]).dispatch(txn)
        assert result.highest_severity == "high"


class TestImpossibleTravelV1:
    def test_it_rule_does_not_trigger_when_prev_geo_missing(self):
        """v1: prev_geo_country absent — IT rule must not trigger."""
        txn = {"geo_country": "MX", "txn_time_ms": 1000}
        result = RuleEvaluator([IT_CRITICAL]).dispatch(txn)
        assert result.determination == "clean"
        assert result.matched_rules == []

    def test_it_rule_does_not_trigger_empty_txn(self):
        result = RuleEvaluator([IT_CRITICAL]).dispatch({})
        assert result.determination == "clean"


class TestDisabledRules:
    def test_disabled_rule_not_evaluated(self):
        disabled = _make_rule("VEL-DIS", RuleFamily.velocity, Severity.critical,
                               {"field": "vel_count_1m", "count": 0}, enabled=False)
        txn = {"vel_count_1m": 999}
        result = RuleEvaluator([disabled]).dispatch(txn)
        assert result.determination == "clean"
