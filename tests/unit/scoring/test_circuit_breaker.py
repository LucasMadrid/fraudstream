"""Tests for MLCircuitBreaker and FraudCircuitBreakerListener."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pybreaker
import pytest

from pipelines.scoring.circuit_breaker import (
    FraudCircuitBreakerListener,
    MLCircuitBreaker,
    ml_circuit_breaker_state,
    ml_fallback_decisions_total,
)
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.ml_client import MLClientError, MLScore, StubMLModelClient


def _config(**kwargs) -> ScoringConfig:
    defaults = dict(cb_error_threshold=3, cb_open_seconds=30.0, cb_probe_timeout_ms=5.0)
    defaults.update(kwargs)
    return ScoringConfig(**defaults)


class TestMLCircuitBreakerSuccess:
    def test_returns_score_no_fallback(self):
        client = StubMLModelClient(stub_score=0.42)
        cb = MLCircuitBreaker(client, _config())
        score, used_fallback = cb.score_with_fallback({"f": 1})
        assert score == pytest.approx(0.42)
        assert used_fallback is False

    def test_custom_fallback_not_used_on_success(self):
        client = StubMLModelClient(stub_score=0.1)
        cb = MLCircuitBreaker(client, _config())
        score, used_fallback = cb.score_with_fallback({}, fallback_score=0.99)
        assert score == pytest.approx(0.1)
        assert used_fallback is False


class TestMLCircuitBreakerFallback:
    def test_ml_client_error_uses_fallback(self):
        client = StubMLModelClient(fail_on_call=True)
        cb = MLCircuitBreaker(client, _config(cb_error_threshold=5))
        score, used_fallback = cb.score_with_fallback({}, fallback_score=0.5)
        assert score == pytest.approx(0.5)
        assert used_fallback is True

    def test_circuit_open_uses_fallback(self):
        client = StubMLModelClient(fail_on_call=True)
        cfg = _config(cb_error_threshold=2, cb_open_seconds=60.0)
        cb = MLCircuitBreaker(client, cfg)
        # Trip circuit: call twice to hit threshold
        cb.score_with_fallback({})
        cb.score_with_fallback({})
        # Now circuit is open, next call uses fallback
        score, used_fallback = cb.score_with_fallback({}, fallback_score=0.77)
        assert score == pytest.approx(0.77)
        assert used_fallback is True

    def test_timeout_uses_fallback(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        client = StubMLModelClient(stub_score=0.3)
        cb = MLCircuitBreaker(client, _config())

        with patch.object(cb._executor, "submit") as mock_submit:
            mock_future = MagicMock()
            mock_future.result.side_effect = FuturesTimeoutError()
            mock_submit.return_value = mock_future

            score, used_fallback = cb.score_with_fallback({}, fallback_score=0.0)
            assert used_fallback is True


class TestFraudCircuitBreakerListener:
    def test_state_change_updates_gauge(self):
        listener = FraudCircuitBreakerListener()
        mock_cb = MagicMock()
        listener.state_change(mock_cb, "closed", "open")
        assert ml_circuit_breaker_state.labels(state="open")._value.get() == 1.0
        assert ml_circuit_breaker_state.labels(state="closed")._value.get() == 0.0
        assert ml_circuit_breaker_state.labels(state="half_open")._value.get() == 0.0

    def test_before_call_increments_fallback_when_open(self):
        listener = FraudCircuitBreakerListener()
        mock_cb = MagicMock()
        mock_cb.current_state = "open"
        before = ml_fallback_decisions_total._value.get()
        listener.before_call(mock_cb, lambda: None)
        assert ml_fallback_decisions_total._value.get() == before + 1.0

    def test_before_call_no_increment_when_closed(self):
        listener = FraudCircuitBreakerListener()
        mock_cb = MagicMock()
        mock_cb.current_state = "closed"
        before = ml_fallback_decisions_total._value.get()
        listener.before_call(mock_cb, lambda: None)
        assert ml_fallback_decisions_total._value.get() == before
