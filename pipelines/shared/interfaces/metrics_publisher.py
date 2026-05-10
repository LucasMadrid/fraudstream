"""Interface contracts for metrics publishing between processing and scoring layers.

This module defines the boundary contract (CHB-006) between the processing
layer (which generates events) and the scoring layer (which defines metrics).
By programming to these interfaces, we avoid the reverse dependency where
processing would need to import from scoring internals.

Architecture:
    Processing Layer ──► Interface Contract ◄── Scoring Layer (implements)

The scoring layer provides concrete implementations at runtime via dependency
injection, while the processing layer operates against the abstract interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from typing import Any


class MetricsPublisher(Protocol):
    """Protocol for publishing metrics with label support.

        This protocol abstracts metric operations without requiring a direct
    dependency on prometheus_client or scoring layer internals.
    """

    def labels(self, *args: Any, **kwargs: Any) -> MetricsChild:
        """Return a child metric with the given labels applied."""
        ...

    def inc(self, amount: float = 1) -> None:
        """Increment the metric by the given amount."""
        ...


class MetricsChild(Protocol):
    """Protocol for labeled metric child operations."""

    def inc(self, amount: float = 1) -> None:
        """Increment the labeled metric by the given amount."""
        ...


class SafeMetricsProvider(ABC):
    """Abstract base class for safe metric providers.

    Implements safe fallbacks when prometheus_client is not available,
    ensuring processing layer metrics never crash the pipeline.
    """

    @abstractmethod
    def get_counter(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
    ) -> MetricsPublisher:
        """Get or create a counter metric.

                Args:
        name: Metric name
        documentation: Metric description
        labelnames: Tuple of label names for this metric

                Returns:
        A MetricsPublisher that supports labels and inc operations
        """
        ...


class RuleMetricsPublisher(ABC):
    """Abstract interface for rule-related metrics publishing.

    This is the primary contract (CHB-006) between processing and scoring.
    The processing layer uses this interface to publish rule evaluation
    and flag metrics without knowing the concrete metric implementations.
    """

    @abstractmethod
    def record_rule_evaluation(self, rule_id: str, rule_family: str) -> None:
        """Record that a rule was evaluated.

                Args:
        rule_id: Unique identifier for the rule
        rule_family: Category/family the rule belongs to
        """
        ...

    @abstractmethod
    def record_rule_flag(
        self,
        rule_id: str,
        rule_family: str,
        severity: str,
    ) -> None:
        """Record that a rule triggered a fraud flag.

                Args:
        rule_id: Unique identifier for the rule
        rule_family: Category/family the rule belongs to
        severity: Severity level of the triggered flag (low, medium, high)
        """
        ...


class NoOpMetricsPublisher:
    """No-op implementation of MetricsPublisher for safe fallbacks.

    Used when no concrete metrics provider is configured, ensuring
    processing layer continues to function without metrics.
    """

    def labels(self, *args: Any, **kwargs: Any) -> NoOpMetricsPublisher:
        """Return self - no-op labeled operations."""
        return self

    def inc(self, amount: float = 1) -> None:
        """No-op increment."""
        pass

    def set(self, value: float) -> None:
        """No-op set."""
        pass

    def observe(self, amount: float) -> None:
        """No-op observe."""
        pass


class NoOpRuleMetricsPublisher(RuleMetricsPublisher):
    """No-op implementation of RuleMetricsPublisher.

    Safe fallback when no rule metrics publisher is configured.
    """

    def record_rule_evaluation(self, rule_id: str, rule_family: str) -> None:
        """No-op rule evaluation recording."""
        pass

    def record_rule_flag(
        self,
        rule_id: str,
        rule_family: str,
        severity: str,
    ) -> None:
        """No-op rule flag recording."""
        pass


# Global provider registry for dependency injection
_metrics_provider: SafeMetricsProvider | None = None
_rule_metrics_publisher: RuleMetricsPublisher | None = None


def set_metrics_provider(provider: SafeMetricsProvider | None) -> None:
    """Set the global metrics provider.

    Called by the scoring layer during initialization to provide
    concrete implementations to the processing layer.
    """
    global _metrics_provider
    _metrics_provider = provider


def get_metrics_provider() -> SafeMetricsProvider:
    """Get the current metrics provider.

        Returns:
    The configured provider, or a no-op implementation if none set
    """
    if _metrics_provider is None:
        return _NoOpSafeMetricsProvider()
    return _metrics_provider


def set_rule_metrics_publisher(publisher: RuleMetricsPublisher | None) -> None:
    """Set the global rule metrics publisher.

    Called by the scoring layer during initialization.
    """
    global _rule_metrics_publisher
    _rule_metrics_publisher = publisher


def get_rule_metrics_publisher() -> RuleMetricsPublisher:
    """Get the current rule metrics publisher.

        Returns:
    The configured publisher, or a no-op implementation if none set
    """
    if _rule_metrics_publisher is None:
        return NoOpRuleMetricsPublisher()
    return _rule_metrics_publisher


class _NoOpSafeMetricsProvider(SafeMetricsProvider):
    """Internal no-op provider returned when none is configured."""

    _no_op = NoOpMetricsPublisher()

    def get_counter(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
    ) -> MetricsPublisher:
        return self._no_op
