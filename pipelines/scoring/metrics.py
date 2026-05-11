"""Prometheus metrics for the fraud scoring pipeline.

All metrics use SafeMetric wrappers to ensure failures don't crash the hot path.
"""

from __future__ import annotations

from pipelines.scoring.safe_metrics import SafeCounter, SafeHistogram

feature_store_fallback_total = SafeCounter(
    "feature_store_fallback_total",
    "Feature store zero-value fallbacks",
    ["reason"],
)

feature_store_miss_total = SafeCounter(
    "feature_store_miss_total",
    "Feature store cache misses (account not found)",
)

feature_store_retrieval_seconds = SafeHistogram(
    "feature_store_retrieval_seconds",
    "Feature store retrieval latency",
    buckets=[0.001, 0.002, 0.003, 0.005, 0.010, 0.050, 0.100, 0.250, 0.500, 1.0],
)

# Import shared cross-layer metric from shared module
# This metric is defined in shared to avoid duplicate Prometheus registration
# when both processing and scoring layers import it
from pipelines.shared.safe_metric import (  # noqa: E402, F401
    feature_materialization_lag_ms,  # Re-exported for backwards compatibility
)

evaluation_errors_total = SafeCounter(
    "evaluation_errors_total",
    "Total number of evaluation errors",
    ["error_type"],
)

rule_evaluations_total = SafeCounter(
    "rule_evaluations_total",
    "Total number of rule evaluations performed",
    ["rule_id", "rule_family"],
)

rule_flags_total = SafeCounter(
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


rule_shadow_triggers_total = SafeCounter(
    "rule_shadow_triggers_total",
    "Shadow rule triggers (rule fired but determination not changed)",
    ["rule_id", "mode"],
)

rule_shadow_fp_total = SafeCounter(
    "rule_shadow_fp_total",
    "Shadow rule triggers where final determination was clean (estimated false positives)",
    ["rule_id"],
)

rule_triggers_total = SafeCounter(
    "rule_triggers_total",
    "Total rule evaluations for active rules (denominator for FP rate alert)",
    ["rule_id"],
)

rule_active_fp_total = SafeCounter(
    "rule_active_fp_total",
    "Active rule triggers where final determination was clean (false positives)",
    ["rule_id"],
)

iceberg_decisions_buffer_overflow_total = SafeCounter(
    "iceberg_decisions_buffer_overflow_total",
    "Total number of times the fraud decisions Iceberg buffer reached max capacity",
)

iceberg_decisions_catalog_unavailable_total = SafeCounter(
    "iceberg_decisions_catalog_unavailable_total",
    "Total number of Iceberg catalog connection errors for fraud decisions",
)


def record_shadow_trigger(rule_id: str) -> None:
    """Increment the shadow trigger counter for a shadow rule that fired."""
    rule_shadow_triggers_total.labels(rule_id=rule_id, mode="shadow").inc()


def record_shadow_fp(rule_id: str) -> None:
    """Increment the shadow false positive counter for a shadow rule that didn't change
    determination."""
    rule_shadow_fp_total.labels(rule_id=rule_id).inc()


def record_trigger(rule_id: str) -> None:
    """Increment the active rule trigger counter (denominator for FP rate)."""
    rule_triggers_total.labels(rule_id=rule_id).inc()


def record_active_fp(rule_id: str) -> None:
    """Increment the active false positive counter when active rule fired but txn was clean."""
    rule_active_fp_total.labels(rule_id=rule_id).inc()
