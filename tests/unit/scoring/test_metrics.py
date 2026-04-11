"""TDD tests for fraud scoring Prometheus metrics."""

from __future__ import annotations


class TestScoringMetricsExist:
    def test_imports_without_error(self):
        from pipelines.scoring.metrics import (  # noqa: F401
            rule_evaluations_total,
            rule_flags_total,
        )

    def test_rule_evaluations_total_is_counter(self):
        from prometheus_client import Counter

        from pipelines.scoring.metrics import rule_evaluations_total

        assert isinstance(rule_evaluations_total, Counter)

    def test_rule_flags_total_is_counter(self):
        from prometheus_client import Counter

        from pipelines.scoring.metrics import rule_flags_total

        assert isinstance(rule_flags_total, Counter)

    def test_rule_evaluations_has_correct_labels(self):
        from pipelines.scoring.metrics import rule_evaluations_total

        labels = rule_evaluations_total._labelnames
        assert "rule_id" in labels
        assert "rule_family" in labels

    def test_rule_flags_has_correct_labels(self):
        from pipelines.scoring.metrics import rule_flags_total

        labels = rule_flags_total._labelnames
        assert "rule_id" in labels
        assert "rule_family" in labels
        assert "severity" in labels


class TestScoringMetricsIncrement:
    def test_record_evaluation_increments_counter(self):
        from pipelines.scoring.metrics import record_evaluation, rule_evaluations_total

        before = rule_evaluations_total.labels(
            rule_id="VEL-001", rule_family="velocity"
        )._value.get()
        record_evaluation("VEL-001", "velocity")
        after = rule_evaluations_total.labels(
            rule_id="VEL-001", rule_family="velocity"
        )._value.get()
        assert after == before + 1

    def test_record_flag_increments_counter(self):
        from pipelines.scoring.metrics import record_flag, rule_flags_total

        before = rule_flags_total.labels(
            rule_id="ND-003", rule_family="new_device", severity="critical"
        )._value.get()
        record_flag("ND-003", "new_device", "critical")
        after = rule_flags_total.labels(
            rule_id="ND-003", rule_family="new_device", severity="critical"
        )._value.get()
        assert after == before + 1

    def test_record_evaluation_different_labels_independent(self):
        from pipelines.scoring.metrics import record_evaluation, rule_evaluations_total

        record_evaluation("VEL-TEST-A", "velocity")
        record_evaluation("VEL-TEST-B", "velocity")

        a = rule_evaluations_total.labels(rule_id="VEL-TEST-A", rule_family="velocity")._value.get()
        b = rule_evaluations_total.labels(rule_id="VEL-TEST-B", rule_family="velocity")._value.get()
        assert a >= 1
        assert b >= 1
