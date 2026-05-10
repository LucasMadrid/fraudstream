"""Interface contracts for decoupling pipeline layers.

This module defines abstract interfaces that allow communication between
pipeline layers without creating circular dependencies. Following the
dependency inversion principle, higher-level layers depend on these
abstractions rather than concrete implementations.
"""

from pipelines.shared.interfaces.metrics_publisher import (
    MetricsPublisher,
    MetricsChild,
    RuleMetricsPublisher,
    SafeMetricsProvider,
    NoOpMetricsPublisher,
    NoOpRuleMetricsPublisher,
    set_metrics_provider,
    get_metrics_provider,
    set_rule_metrics_publisher,
    get_rule_metrics_publisher,
)

__all__ = [
    "MetricsPublisher",
    "MetricsChild",
    "RuleMetricsPublisher",
    "SafeMetricsProvider",
    "NoOpMetricsPublisher",
    "NoOpRuleMetricsPublisher",
    "set_metrics_provider",
    "get_metrics_provider",
    "set_rule_metrics_publisher",
    "get_rule_metrics_publisher",
]
