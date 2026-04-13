"""Prometheus metrics and OpenTelemetry instrumentation for the fraud scoring pipeline."""

from __future__ import annotations

from contextlib import contextmanager

from prometheus_client import Counter

rule_evaluations_total = Counter(
    "rule_evaluations_total",
    "Total number of rule evaluations performed",
    ["rule_id", "rule_family"],
)

rule_flags_total = Counter(
    "rule_flags_total",
    "Total number of rules that triggered a fraud flag",
    ["rule_id", "rule_family", "severity"],
)


def record_evaluation(rule_id: str, rule_family: str) -> None:
    """Increment the rule evaluations counter for a given rule."""
    rule_evaluations_total.labels(rule_id=rule_id, rule_family=rule_family).inc()


def record_flag(rule_id: str, rule_family: str, severity: str) -> None:
    """Increment the rule flags counter for a triggered rule."""
    rule_flags_total.labels(rule_id=rule_id, rule_family=rule_family, severity=severity).inc()


rule_shadow_triggers_total = Counter(
    "rule_shadow_triggers_total",
    "Shadow rule triggers (rule fired but determination not changed)",
    ["rule_id", "mode"],
)

rule_shadow_fp_total = Counter(
    "rule_shadow_fp_total",
    "Shadow rule triggers where final determination was clean (estimated false positives)",
    ["rule_id"],
)


def record_shadow_trigger(rule_id: str) -> None:
    """Increment the shadow trigger counter for a shadow rule that fired."""
    rule_shadow_triggers_total.labels(rule_id=rule_id, mode="shadow").inc()


def record_shadow_fp(rule_id: str) -> None:
    """Increment the shadow false positive counter for a shadow rule that didn't change
    determination."""
    rule_shadow_fp_total.labels(rule_id=rule_id).inc()


@contextmanager
def fraud_rule_evaluation_span(transaction_id: str):
    """OTel span context manager for fraud.rule_evaluation (T032).

    Wraps a rule evaluation in an OpenTelemetry span named 'fraud.rule_evaluation'.
    Falls back to a no-op if opentelemetry-sdk is not available.
    """
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("pipelines.scoring")
        with tracer.start_as_current_span("fraud.rule_evaluation") as span:
            span.set_attribute("transaction.id", transaction_id)
            yield span
    except Exception:  # noqa: BLE001
        yield None
