"""Tests for rule engine gaps: RuleDefinition validation, Severity comparison,
RuleEvaluator metrics, dispatch edge cases, and RuleEvaluatorProcessFunction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yaml

from pipelines.scoring.rules.evaluator import RuleEvaluator, RuleEvaluatorProcessFunction
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, RuleMode, Severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: str = "VEL-01",
    family: RuleFamily = RuleFamily.velocity,
    severity: Severity = Severity.high,
    conditions: dict | None = None,
    enabled: bool = True,
    mode: RuleMode = RuleMode.active,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        name=rule_id,
        family=family,
        severity=severity,
        conditions=conditions or {"field": "vel_count_1m", "count": 5},
        enabled=enabled,
        mode=mode,
    )


@pytest.fixture
def mock_metrics():
    with (
        patch("pipelines.scoring.rules.evaluator.record_evaluation") as mock_eval,
        patch("pipelines.scoring.rules.evaluator.record_flag") as mock_flag,
        patch("pipelines.scoring.rules.evaluator.record_trigger") as mock_trigger,
        patch("pipelines.scoring.rules.evaluator.record_shadow_trigger") as mock_shadow,
        patch("pipelines.scoring.rules.evaluator.record_shadow_fp") as mock_fp,
    ):
        yield {
            "record_evaluation": mock_eval,
            "record_flag": mock_flag,
            "record_trigger": mock_trigger,
            "record_shadow_trigger": mock_shadow,
            "record_shadow_fp": mock_fp,
        }


# ---------------------------------------------------------------------------
# RuleDefinition.validate_rule_id
# ---------------------------------------------------------------------------


class TestRuleIdValidation:
    def test_valid_minimum_length(self):
        rule = _make_rule(rule_id="a1")
        assert rule.rule_id == "a1"

    def test_valid_maximum_length(self):
        rule_id = "a" + "b" * 62 + "z"  # 64 chars
        rule = _make_rule(rule_id=rule_id)
        assert len(rule.rule_id) == 64

    def test_valid_with_hyphens_and_underscores(self):
        rule = _make_rule(rule_id="VEL-001_HIGH")
        assert rule.rule_id == "VEL-001_HIGH"

    def test_too_short_single_char_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="a")

    def test_too_long_65_chars_raises(self):
        rule_id = "a" * 65
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id=rule_id)

    def test_starts_with_hyphen_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="-abc")

    def test_ends_with_hyphen_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="abc-")

    def test_starts_with_underscore_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="_abc")

    def test_special_characters_raise(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="rule!@#")

    def test_space_in_rule_id_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id="rule 001")

    def test_non_string_raises(self):
        with pytest.raises(Exception, match="rule_id"):
            _make_rule(rule_id=123)  # type: ignore[arg-type]

    def test_none_raises(self):
        with pytest.raises(Exception):
            _make_rule(rule_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Severity comparisons with non-comparable types
# ---------------------------------------------------------------------------


class TestSeverityComparison:
    def test_severity_ordering(self):
        assert Severity.low < Severity.medium < Severity.high < Severity.critical

    def test_gt_with_string_raises_type_error(self):
        with pytest.raises(TypeError):
            _ = Severity.high > "string"  # type: ignore[operator]

    def test_lt_with_string_raises_type_error(self):
        with pytest.raises(TypeError):
            _ = Severity.high < "string"  # type: ignore[operator]

    def test_gt_with_none_raises_type_error(self):
        with pytest.raises(TypeError):
            _ = Severity.high > None  # type: ignore[operator]

    def test_gt_with_float_uses_python_fallback(self):
        # float falls back to float.__lt__ via reflected op — no error, returns False
        assert not (Severity.high > 2.5)

    def test_compare_with_int_works(self):
        assert Severity.high > 1
        assert Severity.low < 3


# ---------------------------------------------------------------------------
# Severity.coerce_severity edge cases
# ---------------------------------------------------------------------------


class TestSeverityCoercion:
    def test_string_lowercase_accepted(self):
        rule = _make_rule(severity="high")  # type: ignore[arg-type]
        assert rule.severity == Severity.high

    def test_string_uppercase_raises(self):
        with pytest.raises(Exception, match="Invalid severity"):
            _make_rule(severity="HIGH")  # type: ignore[arg-type]

    def test_invalid_string_raises(self):
        with pytest.raises(Exception, match="Invalid severity"):
            _make_rule(severity="extreme")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RuleEvaluator metrics: record_trigger and record_evaluation
# ---------------------------------------------------------------------------


class TestRuleEvaluatorMetrics:
    def test_active_match_calls_record_trigger(self, mock_metrics):
        rule = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        RuleEvaluator([rule]).dispatch({"vel_count_1m": 6})
        mock_metrics["record_trigger"].assert_called_once_with("VEL-01")

    def test_multiple_active_matches_each_call_record_trigger(self, mock_metrics):
        r1 = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        r2 = _make_rule(rule_id="VEL-02", conditions={"field": "vel_count_5m", "count": 10})
        RuleEvaluator([r1, r2]).dispatch({"vel_count_1m": 6, "vel_count_5m": 11})
        assert mock_metrics["record_trigger"].call_count == 2
        called = {c[0][0] for c in mock_metrics["record_trigger"].call_args_list}
        assert called == {"VEL-01", "VEL-02"}

    def test_no_match_does_not_call_record_trigger(self, mock_metrics):
        rule = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        RuleEvaluator([rule]).dispatch({"vel_count_1m": 2})
        mock_metrics["record_trigger"].assert_not_called()

    def test_record_evaluation_called_for_every_enabled_rule(self, mock_metrics):
        r1 = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        r2 = _make_rule(rule_id="VEL-02", conditions={"field": "vel_count_5m", "count": 10})
        RuleEvaluator([r1, r2]).dispatch({"vel_count_1m": 2})  # only r2 unmet too
        assert mock_metrics["record_evaluation"].call_count == 2

    def test_record_evaluation_not_called_for_disabled_rule(self, mock_metrics):
        enabled = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        disabled = _make_rule(
            rule_id="VEL-02",
            conditions={"field": "vel_count_5m", "count": 10},
            enabled=False,
        )
        RuleEvaluator([enabled, disabled]).dispatch({"vel_count_1m": 2})
        assert mock_metrics["record_evaluation"].call_count == 1

    def test_shadow_match_does_not_call_record_trigger(self, mock_metrics):
        rule = _make_rule(
            rule_id="VEL-S1",
            conditions={"field": "vel_count_1m", "count": 5},
            mode=RuleMode.shadow,
        )
        RuleEvaluator([rule]).dispatch({"vel_count_1m": 6})
        mock_metrics["record_trigger"].assert_not_called()


# ---------------------------------------------------------------------------
# Unknown rule family is silently skipped
# ---------------------------------------------------------------------------


class TestUnknownFamilySkipped:
    def test_unknown_family_skipped_no_error(self, mock_metrics):
        rule = _make_rule(rule_id="VEL-01", conditions={"field": "vel_count_1m", "count": 5})
        with patch(
            "pipelines.scoring.rules.evaluator._FAMILY_DISPATCH",
            {},  # empty dispatch table — all families unknown
        ):
            result = RuleEvaluator([rule]).dispatch({"vel_count_1m": 99})
        assert result.determination == "clean"
        assert result.matched_rules == []
        mock_metrics["record_evaluation"].assert_not_called()


# ---------------------------------------------------------------------------
# RuleEvaluatorProcessFunction
# ---------------------------------------------------------------------------


class TestRuleEvaluatorProcessFunction:
    def test_open_loads_rules_and_creates_evaluator(self, tmp_path):
        rules_data = [
            {
                "rule_id": "VEL-01",
                "name": "HIGH_FREQ",
                "family": "velocity",
                "severity": "high",
                "enabled": True,
                "mode": "active",
                "conditions": {"field": "vel_count_1m", "count": 5},
            }
        ]
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(yaml.dump(rules_data))

        pf = RuleEvaluatorProcessFunction(str(yaml_path))
        assert pf._evaluator is None
        pf.open()
        assert pf._evaluator is not None
        assert isinstance(pf._evaluator, RuleEvaluator)

    def test_process_element_calls_dispatch(self, tmp_path, mock_metrics):
        rules_data = [
            {
                "rule_id": "VEL-01",
                "name": "HIGH_FREQ",
                "family": "velocity",
                "severity": "high",
                "enabled": True,
                "mode": "active",
                "conditions": {"field": "vel_count_1m", "count": 5},
            }
        ]
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(yaml.dump(rules_data))

        pf = RuleEvaluatorProcessFunction(str(yaml_path))
        pf.open()
        result = pf.process_element({"vel_count_1m": 6})
        assert result.determination == "suspicious"

    def test_process_element_without_open_raises_assertion(self):
        pf = RuleEvaluatorProcessFunction("/nonexistent/rules.yaml")
        with pytest.raises(AssertionError, match="open\\(\\) must be called"):
            pf.process_element({"vel_count_1m": 6})

    def test_process_element_passes_ctx_without_error(self, tmp_path, mock_metrics):
        rules_data = [
            {
                "rule_id": "VEL-01",
                "name": "HIGH_FREQ",
                "family": "velocity",
                "severity": "high",
                "enabled": True,
                "mode": "active",
                "conditions": {"field": "vel_count_1m", "count": 5},
            }
        ]
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(yaml.dump(rules_data))

        pf = RuleEvaluatorProcessFunction(str(yaml_path))
        pf.open()
        ctx = MagicMock()
        result = pf.process_element({"vel_count_1m": 2}, ctx=ctx)
        assert result.determination == "clean"
