"""Interface implementations for scoring layer.

This module provides concrete implementations of the shared interfaces,
bridging them to the scoring layer's concrete metrics.
"""

from __future__ import annotations

from pipelines.scoring.metrics import rule_evaluations_total, rule_flags_total
from pipelines.scoring.safe_metrics import SafeCounter
from pipelines.shared.interfaces import (
    MetricsPublisher,
    RuleMetricsPublisher,
    SafeMetricsProvider,
    set_metrics_provider,
    set_rule_metrics_publisher,
)


class _SafeCounterMetricsPublisher:
    """Adapter from SafeCounter to MetricsPublisher interface."""

    def __init__(self, counter: SafeCounter) -> None:
        self._counter = counter

    def labels(self, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        return _LabeledMetricsChild(self._counter.labels(*args, **kwargs))

    def inc(self, amount: float = 1) -> None:
        self._counter.inc(amount)


class _LabeledMetricsChild:
    """Adapter for labeled metric child operations."""

    def __init__(self, child) -> None:  # noqa: ANN001
        self._child = child

    def inc(self, amount: float = 1) -> None:
        self._child.inc(amount)


class SafeMetricsProviderImpl(SafeMetricsProvider):
    """Concrete implementation of SafeMetricsProvider using scoring metrics."""

    def get_counter(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
    ) -> MetricsPublisher:
        """Create a SafeCounter and wrap it in the interface."""
        counter = SafeCounter(name, documentation, list(labelnames))
        return _SafeCounterMetricsPublisher(counter)


class RuleMetricsPublisherImpl(RuleMetricsPublisher):
    """Concrete implementation of RuleMetricsPublisher using scoring metrics."""

    def record_rule_evaluation(self, rule_id: str, rule_family: str) -> None:
        """Record rule evaluation via the concrete metric."""
        rule_evaluations_total.labels(
            rule_id=rule_id,
            rule_family=rule_family,
        ).inc()

    def record_rule_flag(
        self,
        rule_id: str,
        rule_family: str,
        severity: str,
    ) -> None:
        """Record rule flag via the concrete metric."""
        rule_flags_total.labels(
            rule_id=rule_id,
            rule_family=rule_family,
            severity=severity,
        ).inc()


def register_interface_implementations() -> None:
    """Register the concrete implementations with the global registry.

    This should be called once during scoring layer initialization
    to enable the processing layer to use scoring metrics via interfaces.
    """
    set_metrics_provider(SafeMetricsProviderImpl())
    set_rule_metrics_publisher(RuleMetricsPublisherImpl())
