"""Null-safe Prometheus metric proxy shared across all pipeline packages.

SafeMetric wrappers ensure metric operations never raise exceptions,
preventing metric registration/operation failures from crashing the hot path.
Constitution Article VIII (Observability): Metrics must be safe to use.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _NullMetric:
    """Null object pattern for metrics that fail to initialize."""

    def __init__(self, name: str = "unknown", error: Exception | None = None) -> None:
        self._name = name
        self._error = error

    def __call__(self, *args, **kwargs) -> _NullMetric:
        return self

    def labels(self, *args, **kwargs) -> _NullMetric:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def observe(self, value: float) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **kw: self


class SafeCounter:
    """Safe Counter wrapper that never raises on metric operations.

    If prometheus_client is unavailable or registration fails, silently
    degrades to a no-op null metric. This ensures the hot path never
    crashes due to metric infrastructure issues.

    Usage:
        counter = SafeCounter("name", "description", ["label1"])
        counter.labels(label1="value").inc()
    """

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | None = None,
    ) -> None:
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._metric = self._create_metric()

    def _create_metric(self) -> Any:
        """Create the underlying prometheus Counter, or null on failure."""
        try:
            from prometheus_client import Counter

            return Counter(self._name, self._documentation, self._labelnames)
        except Exception as e:
            logger.debug(f"SafeCounter '{self._name}' using null metric: {e}")
            return _NullMetric(self._name, e)

    def labels(self, *args, **kwargs) -> SafeCounter:
        """Return a labeled version of this counter."""
        try:
            labeled = self._metric.labels(*args, **kwargs)
            wrapper = SafeCounter.__new__(SafeCounter)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._metric = labeled
            return wrapper
        except Exception as e:
            logger.debug(f"SafeCounter '{self._name}' labels failed: {e}")
            wrapper = SafeCounter.__new__(SafeCounter)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._metric = _NullMetric(self._name, e)
            return wrapper

    def inc(self, amount: float = 1) -> None:
        """Increment the counter by amount (default 1)."""
        try:
            if hasattr(self._metric, "inc"):
                self._metric.inc(amount)
        except Exception as e:
            logger.debug(f"SafeCounter '{self._name}' inc failed: {e}")


class SafeHistogram:
    """Safe Histogram wrapper that never raises on metric operations.

    If prometheus_client is unavailable or registration fails, silently
    degrades to a no-op null metric.

    Usage:
        hist = SafeHistogram("name", "description", buckets=[...])
        hist.observe(0.5)
    """

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | None = None,
        buckets: list[float] | None = None,
    ) -> None:
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._buckets = buckets
        self._metric = self._create_metric()

    def _create_metric(self) -> Any:
        """Create the underlying prometheus Histogram, or null on failure."""
        try:
            from prometheus_client import Histogram

            kwargs: dict[str, Any] = {}
            if self._buckets:
                kwargs["buckets"] = self._buckets

            return Histogram(self._name, self._documentation, self._labelnames, **kwargs)
        except Exception as e:
            logger.debug(f"SafeHistogram '{self._name}' using null metric: {e}")
            return _NullMetric(self._name, e)

    def labels(self, *args, **kwargs) -> SafeHistogram:
        """Return a labeled version of this histogram."""
        try:
            labeled = self._metric.labels(*args, **kwargs)
            wrapper = SafeHistogram.__new__(SafeHistogram)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._buckets = self._buckets
            wrapper._metric = labeled
            return wrapper
        except Exception as e:
            logger.debug(f"SafeHistogram '{self._name}' labels failed: {e}")
            wrapper = SafeHistogram.__new__(SafeHistogram)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._buckets = self._buckets
            wrapper._metric = _NullMetric(self._name, e)
            return wrapper

    def observe(self, value: float) -> None:
        """Observe a value in the histogram."""
        try:
            if hasattr(self._metric, "observe"):
                self._metric.observe(value)
        except Exception as e:
            logger.debug(f"SafeHistogram '{self._name}' observe failed: {e}")


class SafeGauge:
    """Safe Gauge wrapper that never raises on metric operations.

    If prometheus_client is unavailable or registration fails, silently
    degrades to a no-op null metric.

    Usage:
        gauge = SafeGauge("name", "description", ["label1"])
        gauge.labels(label1="value").set(42)
    """

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | None = None,
    ) -> None:
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._metric = self._create_metric()

    def _create_metric(self) -> Any:
        """Create the underlying prometheus Gauge, or null on failure."""
        try:
            from prometheus_client import Gauge

            return Gauge(self._name, self._documentation, self._labelnames)
        except Exception as e:
            logger.debug(f"SafeGauge '{self._name}' using null metric: {e}")
            return _NullMetric(self._name, e)

    def labels(self, *args, **kwargs) -> SafeGauge:
        """Return a labeled version of this gauge."""
        try:
            labeled = self._metric.labels(*args, **kwargs)
            wrapper = SafeGauge.__new__(SafeGauge)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._metric = labeled
            return wrapper
        except Exception as e:
            logger.debug(f"SafeGauge '{self._name}' labels failed: {e}")
            wrapper = SafeGauge.__new__(SafeGauge)
            wrapper._name = self._name
            wrapper._documentation = self._documentation
            wrapper._labelnames = self._labelnames
            wrapper._metric = _NullMetric(self._name, e)
            return wrapper

    def set(self, value: float) -> None:
        """Set the gauge to a specific value."""
        try:
            if hasattr(self._metric, "set"):
                self._metric.set(value)
        except Exception as e:
            logger.debug(f"SafeGauge '{self._name}' set failed: {e}")

    def inc(self, amount: float = 1) -> None:
        """Increment the gauge by amount."""
        try:
            if hasattr(self._metric, "inc"):
                self._metric.inc(amount)
        except Exception as e:
            logger.debug(f"SafeGauge '{self._name}' inc failed: {e}")

    def dec(self, amount: float = 1) -> None:
        """Decrement the gauge by amount."""
        try:
            if hasattr(self._metric, "dec"):
                self._metric.dec(amount)
        except Exception as e:
            logger.debug(f"SafeGauge '{self._name}' dec failed: {e}")


class _SafeMetric:
    """Legacy proxy that makes any Prometheus metric call guaranteed to not raise.

    Deprecated: Use SafeCounter, SafeHistogram, SafeGauge instead.
    """

    __slots__ = ("_m",)

    def __init__(self, metric) -> None:
        object.__setattr__(self, "_m", metric)

    def __getattr__(self, name: str):
        m = object.__getattribute__(self, "_m")
        if m is None:
            return lambda *a, **kw: _SafeMetric(None)
        attr = getattr(m, name, None)
        if attr is None:
            return lambda *a, **kw: _SafeMetric(None)
        if not callable(attr):
            return attr

        def _call(*args, **kwargs):
            try:
                return _SafeMetric(attr(*args, **kwargs))
            except Exception:
                return _SafeMetric(None)

        return _call


# Shared cross-layer metrics
# These metrics are defined here to avoid duplicate registration
# when imported by both processing and scoring layers

feature_materialization_lag_ms = SafeGauge(
    "feature_materialization_lag_ms",
    "Feature store materialization lag in milliseconds",
)
