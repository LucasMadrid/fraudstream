"""Rule evaluator — dispatches transactions to family rule functions."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from pipelines.scoring.metrics import (
    record_evaluation,
    record_flag,
    record_shadow_fp,
    record_shadow_trigger,
    record_trigger,
)
from pipelines.scoring.rules.families.impossible_travel import (
    evaluate_impossible_travel,
)
from pipelines.scoring.rules.families.new_device import evaluate_new_device
from pipelines.scoring.rules.families.velocity import evaluate_velocity
from pipelines.scoring.rules.models import RuleDefinition, RuleFamily, RuleMode, Severity
from pipelines.scoring.types import EvaluationResult

logger = logging.getLogger(__name__)

_FAMILY_DISPATCH: dict[RuleFamily, Callable[[dict, dict], bool]] = {
    RuleFamily.velocity: evaluate_velocity,
    RuleFamily.impossible_travel: evaluate_impossible_travel,
    RuleFamily.new_device: evaluate_new_device,
}


class RuleEvaluator:
    """Evaluates a transaction against a set of fraud detection rules."""

    def __init__(self, rules: list[RuleDefinition]) -> None:
        self._rules = [r for r in rules if r.enabled]

    def dispatch(self, txn: dict) -> EvaluationResult:
        """Evaluate all enabled rules against the transaction.

        Args:
            txn: Avro-decoded enriched transaction dict.

        Returns:
            EvaluationResult with determination, matched rules, and highest
            severity.
        """
        active_matched: list[str] = []
        shadow_matched: list[str] = []
        triggered_severities: list[Severity] = []

        for rule in self._rules:
            evaluate_fn = _FAMILY_DISPATCH.get(rule.family)
            if evaluate_fn is None:
                continue
            record_evaluation(rule.rule_id, rule.family.value)
            if evaluate_fn(txn, rule.conditions):
                if rule.mode == RuleMode.shadow:
                    shadow_matched.append(rule.rule_id)
                    record_shadow_trigger(rule.rule_id)
                else:
                    active_matched.append(rule.rule_id)
                    triggered_severities.append(rule.severity)
                    record_flag(rule.rule_id, rule.family.value, rule.severity.name)
                    record_trigger(rule.rule_id)

        # matched_rules includes active rule IDs and shadow rule IDs with ":shadow" suffix
        matched_rules = active_matched + [f"{r}:shadow" for r in shadow_matched]
        determination = "suspicious" if active_matched else "clean"
        highest_severity = max(triggered_severities).name if triggered_severities else None
        evaluation_timestamp = int(time.time() * 1000)

        if determination == "clean":
            for rule_id in shadow_matched:
                record_shadow_fp(rule_id)

        if active_matched:
            logger.info(
                "Fraud flag raised",
                extra={
                    "transaction_id": txn.get("transaction_id"),
                    "matched_rules": active_matched,
                    "highest_severity": highest_severity,
                    "evaluation_timestamp": evaluation_timestamp,
                },
            )

        return EvaluationResult(
            determination=determination,
            matched_rules=matched_rules,
            highest_severity=highest_severity,
            evaluation_timestamp=evaluation_timestamp,
            missing_fields=[],
        )


class RuleEvaluatorProcessFunction:
    """Flink ProcessFunction wrapper around RuleEvaluator.

    Stateless — loads rules once at open() and evaluates per element.
    Emits FraudAlert to the main output for suspicious transactions.
    """

    def __init__(self, rules_yaml_path: str) -> None:
        self._rules_yaml_path = rules_yaml_path
        self._evaluator: RuleEvaluator | None = None

    def open(self) -> None:
        from pipelines.scoring.rules.loader import RuleLoader

        rules = RuleLoader.load(self._rules_yaml_path)
        self._evaluator = RuleEvaluator(rules)

    def process_element(self, txn: dict, ctx: object = None) -> EvaluationResult:  # noqa: ARG002
        assert self._evaluator is not None, "open() must be called before process_element()"
        return self._evaluator.dispatch(txn)
