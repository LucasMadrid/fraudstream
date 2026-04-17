"""Tests for OTel telemetry bootstrap."""

from __future__ import annotations

import pytest

import pipelines.scoring.telemetry as telemetry_module
from pipelines.scoring.telemetry import fraud_rule_evaluation_span, get_tracer, init_tracer


class TestInitTracer:
    def test_returns_tracer(self):
        tracer = init_tracer(allow_sample_rate=0.05)
        assert tracer is not None

    def test_sets_module_tracer(self):
        init_tracer(allow_sample_rate=0.1)
        assert telemetry_module._tracer is not None

    def test_sampling_rate_gauge_set(self):
        init_tracer(allow_sample_rate=0.02)
        from pipelines.scoring.telemetry import trace_sampling_rate

        assert trace_sampling_rate.labels(decision_type="ALLOW")._value.get() == pytest.approx(0.02)
        assert trace_sampling_rate.labels(decision_type="BLOCK")._value.get() == pytest.approx(1.0)
        assert trace_sampling_rate.labels(decision_type="error")._value.get() == pytest.approx(1.0)


class TestGetTracer:
    def test_returns_existing_tracer(self):
        init_tracer()
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_initializes_if_none(self):
        telemetry_module._tracer = None
        tracer = get_tracer()
        assert tracer is not None


class TestFraudRuleEvaluationSpan:
    def test_span_yields_span_object(self):
        init_tracer()
        with fraud_rule_evaluation_span("txn-1", "web", 3) as span:
            assert span is not None

    def test_span_attributes_set(self):
        init_tracer()
        with fraud_rule_evaluation_span("txn-99", "mobile", 5) as span:
            span.set_attribute("fraud.decision", "clean")
