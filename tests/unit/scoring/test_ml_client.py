"""Tests for MLModelClient, StubMLModelClient, and MLScore."""

from __future__ import annotations

import pytest

from pipelines.scoring.ml_client import MLClientError, MLModelClient, MLScore, StubMLModelClient


class TestMLScore:
    def test_fields(self):
        s = MLScore(fraud_probability=0.7, model_version="v1", latency_ms=1.5)
        assert s.fraud_probability == 0.7
        assert s.model_version == "v1"
        assert s.latency_ms == 1.5

    def test_frozen(self):
        s = MLScore(fraud_probability=0.1, model_version="v1", latency_ms=0.5)
        with pytest.raises(Exception):
            s.fraud_probability = 0.9  # type: ignore[misc]


class TestStubMLModelClient:
    def test_default_score_zero(self):
        client = StubMLModelClient()
        result = client.score({"feature": 1})
        assert result.fraud_probability == 0.0
        assert result.model_version == "stub-v1"
        assert result.latency_ms == 0.5

    def test_custom_stub_score(self):
        client = StubMLModelClient(stub_score=0.85)
        result = client.score({})
        assert result.fraud_probability == 0.85

    def test_fail_on_call_raises(self):
        client = StubMLModelClient(fail_on_call=True)
        with pytest.raises(MLClientError, match="configured to fail"):
            client.score({})

    def test_is_abstract_subclass(self):
        assert issubclass(StubMLModelClient, MLModelClient)

    def test_score_ignores_features_content(self):
        client = StubMLModelClient(stub_score=0.5)
        assert client.score({"a": 1, "b": 2}).fraud_probability == 0.5
        assert client.score({}).fraud_probability == 0.5
