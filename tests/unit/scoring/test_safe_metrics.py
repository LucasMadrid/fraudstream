"""Tests for SafeMetric wrappers and graceful degradation."""

from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest


class TestSafeCounterWithPrometheus:
    def test_safe_counter_works_with_prometheus(self):
        from pipelines.scoring.safe_metrics import SafeCounter

        c = SafeCounter(
            "test_safe_counter_inc", "test counter", ["env"]
        )
        child = c.labels(env="test")
        child.inc()
        assert child._value.get() >= 1

    def test_safe_counter_noop_without_prometheus(self):
        """When prometheus_client is missing, inc() must not crash."""
        # Temporarily hide prometheus_client from the import machinery.
        saved = sys.modules.get("prometheus_client")
        sys.modules["prometheus_client"] = None  # type: ignore[assignment]
        try:
            # Re-import safe_metrics so the module-level try/except fires.
            import pipelines.scoring.safe_metrics as sm

            sm_reloaded = importlib.reload(sm)
            c = sm_reloaded.SafeCounter("noop_c", "noop counter", ["x"])
            # Must not raise
            c.labels(x="a").inc()
            c.inc()
        finally:
            # Restore
            if saved is not None:
                sys.modules["prometheus_client"] = saved
            else:  # pragma: no cover
                sys.modules.pop("prometheus_client", None)
            importlib.reload(sm)


class TestSafeGauge:
    def test_safe_gauge_set_works(self):
        from pipelines.scoring.safe_metrics import SafeGauge

        g = SafeGauge("test_safe_gauge_set", "test gauge", ["env"])
        child = g.labels(env="prod")
        child.set(42.0)
        assert child._value.get() == pytest.approx(42.0)


class TestSafeHistogram:
    def test_safe_histogram_observe_works(self):
        from pipelines.scoring.safe_metrics import SafeHistogram

        h = SafeHistogram(
            "test_safe_histogram_obs",
            "test histogram",
            buckets=[0.1, 0.5, 1.0],
        )
        # observe must not raise
        h.observe(0.25)


class TestFraudRuleEvaluationSpan:
    def test_fraud_rule_evaluation_span_without_otel(self):
        """The span context manager must not crash when OTel is absent."""
        from pipelines.scoring.metrics import fraud_rule_evaluation_span

        # Patch the import inside the function body to simulate ImportError.
        with mock.patch.dict(
            sys.modules, {"opentelemetry": None, "opentelemetry.trace": None}
        ):
            # Must not raise
            with fraud_rule_evaluation_span("txn-absent") as span:
                assert span is None

    def test_fraud_rule_evaluation_span_propagates_body_exception(self):
        """Exceptions raised inside the with-block must NOT be swallowed."""
        from pipelines.scoring.metrics import fraud_rule_evaluation_span

        with pytest.raises(ValueError, match="boom"):
            with fraud_rule_evaluation_span("txn-err") as _span:
                raise ValueError("boom")
