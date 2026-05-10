"""Unit tests for shadow scoring functionality.

Tests ShadowRuleEvaluator, ShadowDecisionKafkaSink, and FraudDecision
shadow field extensions.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from pipelines.scoring.rules.evaluator import ShadowRuleEvaluator
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, RuleMode, Severity
from pipelines.scoring.sinks.shadow_decisions_kafka import ShadowDecisionKafkaSink
from pipelines.scoring.types import EvaluationResult, FraudDecision


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


# Shadow rules for testing
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

SHADOW_IT_MEDIUM = _make_rule(
    "SHADOW-IT-001",
    RuleFamily.impossible_travel,
    Severity.medium,
    {"window_ms": 3600000},
    mode=RuleMode.shadow,
)

# Active rule for mixed testing
ACTIVE_VEL_HIGH = _make_rule(
    "ACTIVE-VEL-001",
    RuleFamily.velocity,
    Severity.high,
    {"field": "vel_count_1m", "count": 10},
    mode=RuleMode.active,
)


@pytest.fixture
def mock_metrics():
    """Mock the metrics module functions."""
    with (
        patch("pipelines.scoring.rules.evaluator.record_evaluation") as mock_eval,
        patch("pipelines.scoring.rules.evaluator.record_shadow_trigger") as mock_shadow_trigger,
        patch("pipelines.scoring.rules.evaluator.record_shadow_fp") as mock_shadow_fp,
    ):
        yield {
            "record_evaluation": mock_eval,
            "record_shadow_trigger": mock_shadow_trigger,
            "record_shadow_fp": mock_shadow_fp,
        }


class TestShadowRuleEvaluator:
    """Tests for ShadowRuleEvaluator class."""

    def test_only_evaluates_shadow_rules(self, mock_metrics):
        """ShadowRuleEvaluator should only process rules with mode=shadow."""
        # Mix of active and shadow rules
        rules = [SHADOW_VEL_HIGH, ACTIVE_VEL_HIGH]
        evaluator = ShadowRuleEvaluator(rules)

        # Should only have the shadow rule
        assert len(evaluator._rules) == 1
        assert evaluator._rules[0].rule_id == "SHADOW-VEL-001"

    def test_returns_clean_when_no_shadow_rules_match(self, mock_metrics):
        """When no shadow rules match, determination should be 'clean'."""
        txn = {"vel_count_1m": 3}  # Below threshold of 5
        result = ShadowRuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None

    def test_returns_suspicious_when_shadow_rule_matches(self, mock_metrics):
        """When a shadow rule matches, determination should be 'suspicious'."""
        txn = {"vel_count_1m": 6}  # Above threshold of 5
        result = ShadowRuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "suspicious"
        assert "SHADOW-VEL-001" in result.matched_rules
        assert result.highest_severity == "high"

    def test_multiple_shadow_rules_all_evaluated(self, mock_metrics):
        """Multiple shadow rules should all be evaluated."""
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        result = ShadowRuleEvaluator([SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert result.determination == "suspicious"
        assert "SHADOW-VEL-001" in result.matched_rules
        assert "SHADOW-ND-001" in result.matched_rules
        # Highest severity should be critical
        assert result.highest_severity == "critical"

    def test_shadow_trigger_counter_incremented(self, mock_metrics):
        """Each matched shadow rule should increment the shadow trigger counter."""
        txn = {"vel_count_1m": 6, "device_known_fraud": True}
        ShadowRuleEvaluator([SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]).dispatch(txn)

        assert mock_metrics["record_shadow_trigger"].call_count == 2

    def test_shadow_fp_counter_incremented_on_clean(self, mock_metrics):
        """Shadow FP counter should be incremented when determination is clean."""
        txn = {"vel_count_1m": 3}  # Below threshold
        result = ShadowRuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "clean"
        mock_metrics["record_shadow_fp"].assert_called_once_with("SHADOW-VEL-001")

    def test_shadow_fp_counter_not_incremented_on_suspicious(self, mock_metrics):
        """Shadow FP counter should NOT be incremented when determination is suspicious."""
        txn = {"vel_count_1m": 6}  # Above threshold
        result = ShadowRuleEvaluator([SHADOW_VEL_HIGH]).dispatch(txn)

        assert result.determination == "suspicious"
        mock_metrics["record_shadow_fp"].assert_not_called()

    def test_empty_rules_list_returns_clean(self, mock_metrics):
        """Empty rules list should return clean determination."""
        result = ShadowRuleEvaluator([]).dispatch({})

        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None

    def test_disabled_shadow_rules_not_evaluated(self, mock_metrics):
        """Disabled shadow rules should not be evaluated."""
        disabled_shadow = _make_rule(
            "SHADOW-DISABLED",
            RuleFamily.velocity,
            Severity.high,
            {"field": "vel_count_1m", "count": 0},
            enabled=False,
            mode=RuleMode.shadow,
        )
        txn = {"vel_count_1m": 999}
        result = ShadowRuleEvaluator([disabled_shadow]).dispatch(txn)

        assert result.determination == "clean"


class TestFraudDecisionShadowFields:
    """Tests for FraudDecision shadow field extensions."""

    def test_fraud_decision_has_shadow_determination_field(self):
        """FraudDecision should have shadow_determination field."""
        decision = FraudDecision(
            transaction_id="txn-123",
            decision="ALLOW",
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
            shadow_determination="clean",
        )
        assert decision.shadow_determination == "clean"

    def test_fraud_decision_has_shadow_fraud_score_field(self):
        """FraudDecision should have shadow_fraud_score field."""
        decision = FraudDecision(
            transaction_id="txn-123",
            decision="ALLOW",
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
            shadow_fraud_score=0.8,
        )
        assert decision.shadow_fraud_score == 0.8

    def test_fraud_decision_has_shadow_rule_triggers_field(self):
        """FraudDecision should have shadow_rule_triggers field."""
        decision = FraudDecision(
            transaction_id="txn-123",
            decision="ALLOW",
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
            shadow_rule_triggers=["SHADOW-001", "SHADOW-002"],
        )
        assert decision.shadow_rule_triggers == ["SHADOW-001", "SHADOW-002"]

    def test_shadow_fields_are_optional(self):
        """Shadow fields should default to None when not provided."""
        decision = FraudDecision(
            transaction_id="txn-123",
            decision="ALLOW",
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
        )
        assert decision.shadow_determination is None
        assert decision.shadow_fraud_score is None
        assert decision.shadow_rule_triggers is None


class TestShadowDecisionKafkaSink:
    """Tests for ShadowDecisionKafkaSink class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock ScoringConfig."""
        config = Mock()
        config.kafka_brokers = "localhost:9092"
        config.schema_registry_url = "http://localhost:8081"
        config.shadow_decisions_topic = "txn.shadow.decisions"
        return config

    @pytest.fixture
    def mock_producer(self):
        """Create a mock Kafka producer."""
        return Mock()

    @pytest.fixture
    def mock_fraud_decision(self):
        """Create a mock FraudDecision."""
        return FraudDecision(
            transaction_id="txn-123",
            decision="ALLOW",
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
        )

    @pytest.fixture
    def mock_shadow_result(self):
        """Create a mock EvaluationResult for shadow rules."""
        return EvaluationResult(
            determination="suspicious",
            matched_rules=["SHADOW-VEL-001"],
            highest_severity="high",
            evaluation_timestamp=1234567890,
            missing_fields=[],
        )

    def test_sink_initialization(self, mock_config):
        """ShadowDecisionKafkaSink should initialize with config."""
        sink = ShadowDecisionKafkaSink(mock_config)
        assert sink._config == mock_config
        assert sink._producer is None

    @patch("pipelines.scoring.sinks.shadow_decisions_kafka.json")
    @patch("pipelines.scoring.sinks.shadow_decisions_kafka.Path")
    def test_open_initializes_producer(self, mock_path, mock_json, mock_config):
        """open() should initialize the Kafka producer."""
        mock_json.loads.return_value = {
            "type": "record",
            "name": "ShadowDecision",
            "fields": [],
        }

        with patch("confluent_kafka.Producer") as mock_producer_class:
            sink = ShadowDecisionKafkaSink(mock_config)
            sink.open()
            assert sink._producer is not None
            mock_producer_class.assert_called_once_with({"bootstrap.servers": "localhost:9092"})

    def test_build_record_calculates_decision_mismatch(
        self, mock_config, mock_fraud_decision, mock_shadow_result
    ):
        """_build_record should correctly identify decision mismatches."""
        sink = ShadowDecisionKafkaSink(mock_config)

        record = sink._build_record(
            fraud_decision=mock_fraud_decision,
            shadow_result=mock_shadow_result,
            account_id="acc-123",
            rule_set_version="v1.0",
            shadow_rule_set_version="v2.0",
        )

        # Production ALLOW vs Shadow suspicious = mismatch
        assert record["decision_mismatch"] is True
        assert record["production_decision"] == "ALLOW"
        assert record["shadow_determination"] == "suspicious"

    def test_build_record_no_mismatch_when_same(self, mock_config):
        """_build_record should not flag mismatch when decisions align."""
        sink = ShadowDecisionKafkaSink(mock_config)

        fraud_decision = FraudDecision(
            transaction_id="txn-123",
            decision="BLOCK",
            fraud_score=0.9,
            rule_triggers=["RULE-001"],
            model_version="v1.0",
            decision_time_ms=1234567890,
            latency_ms=10.0,
        )

        shadow_result = EvaluationResult(
            determination="suspicious",
            matched_rules=["SHADOW-VEL-001"],
            highest_severity="high",
            evaluation_timestamp=1234567890,
            missing_fields=[],
        )

        record = sink._build_record(
            fraud_decision=fraud_decision,
            shadow_result=shadow_result,
            account_id="acc-123",
            rule_set_version="v1.0",
            shadow_rule_set_version="v2.0",
        )

        # Production BLOCK/FLAG maps to suspicious, so no mismatch
        assert record["decision_mismatch"] is False

    def test_estimate_shadow_score_clean(self, mock_config):
        """_estimate_shadow_score should return low score for clean determination."""
        sink = ShadowDecisionKafkaSink(mock_config)

        shadow_result = EvaluationResult(
            determination="clean",
            matched_rules=[],
            highest_severity=None,
            evaluation_timestamp=1234567890,
            missing_fields=[],
        )

        score = sink._estimate_shadow_score(shadow_result)
        assert score == 0.1

    def test_estimate_shadow_score_suspicious_by_severity(self, mock_config):
        """_estimate_shadow_score should map severity to score range."""
        sink = ShadowDecisionKafkaSink(mock_config)

        test_cases = [
            ("low", 0.5),
            ("medium", 0.65),
            ("high", 0.8),
            ("critical", 0.95),
        ]

        for severity, expected_score in test_cases:
            shadow_result = EvaluationResult(
                determination="suspicious",
                matched_rules=["RULE-001"],
                highest_severity=severity,
                evaluation_timestamp=1234567890,
                missing_fields=[],
            )
            score = sink._estimate_shadow_score(shadow_result)
            assert score == expected_score, f"Failed for severity={severity}"

    def test_build_record_calculates_score_delta(
        self, mock_config, mock_fraud_decision, mock_shadow_result
    ):
        """_build_record should calculate score_delta correctly."""
        sink = ShadowDecisionKafkaSink(mock_config)

        record = sink._build_record(
            fraud_decision=mock_fraud_decision,
            shadow_result=mock_shadow_result,
            account_id="acc-123",
            rule_set_version="v1.0",
            shadow_rule_set_version="v2.0",
        )

        # score_delta = shadow_score - production_score
        # shadow_score for suspicious/high = 0.8
        # production_score = 0.1
        expected_delta = 0.8 - 0.1
        assert record["score_delta"] == expected_delta

    def test_emit_raises_when_not_opened(
        self, mock_config, mock_fraud_decision, mock_shadow_result
    ):
        """emit() should raise RuntimeError when open() was not called."""
        sink = ShadowDecisionKafkaSink(mock_config)

        with pytest.raises(RuntimeError, match="open\\(\\) must be called before emit\\(\\)"):
            sink.emit(
                fraud_decision=mock_fraud_decision,
                shadow_result=mock_shadow_result,
                account_id="acc-123",
            )

    def test_close_flushes_and_releases_producer(self, mock_config):
        """close() should flush pending messages and release producer."""
        sink = ShadowDecisionKafkaSink(mock_config)
        mock_producer = Mock()
        sink._producer = mock_producer

        sink.close()

        mock_producer.flush.assert_called()
        assert sink._producer is None


class TestShadowScoringIntegration:
    """Integration-style tests for shadow scoring components."""

    def test_full_shadow_evaluation_flow(self, mock_metrics):
        """Test complete flow from evaluation to decision record."""
        # Setup rules
        rules = [SHADOW_VEL_HIGH, SHADOW_ND_CRITICAL]

        # Create evaluator
        evaluator = ShadowRuleEvaluator(rules)

        # Test transaction
        txn = {"vel_count_1m": 6, "device_known_fraud": True}

        # Evaluate
        result = evaluator.dispatch(txn)

        # Verify shadow result
        assert result.determination == "suspicious"
        assert "SHADOW-VEL-001" in result.matched_rules
        assert "SHADOW-ND-001" in result.matched_rules
        assert result.highest_severity == "critical"

        # Create corresponding production decision
        prod_decision = FraudDecision(
            transaction_id=txn.get("transaction_id", "txn-test"),
            decision="ALLOW",  # Production says clean
            fraud_score=0.1,
            rule_triggers=[],
            model_version="v1.0",
            decision_time_ms=result.evaluation_timestamp,
            latency_ms=5.0,
        )

        # Verify decision mismatch
        assert prod_decision.decision == "ALLOW"  # Production: clean
        assert result.determination == "suspicious"  # Shadow: suspicious
        # This would be a decision mismatch
