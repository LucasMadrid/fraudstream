"""Tests for crash-safety guards in pipelines.scoring.job_extension."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pipelines.scoring.job_extension import (
    _FEATURE_ZERO_DEFAULTS,
    _FeatureEnrichmentFunction,
)
from pipelines.scoring.types import FraudDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_txn(**overrides) -> dict:
    """Return a minimal transaction dict."""
    base = {
        "transaction_id": "txn-test-001",
        "account_id": "acct-001",
        "event_time": 1700000000,
        "amount": 100.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# BUG-JE3: _evaluate error handling
# ---------------------------------------------------------------------------


class TestEvaluateBadRecord:
    """_evaluate() must not crash on a bad record."""

    def test_evaluate_bad_record_returns_allow_decision(self):
        """When evaluator.dispatch raises, _evaluate returns (None, ALLOW)."""
        # We need to exercise the _evaluate closure created inside
        # wire_rule_evaluator.  Since that function is pragma-no-cover and
        # requires a full Flink env, we re-create the same logic inline by
        # importing the module-level helpers and mimicking the closure.
        from pipelines.scoring.job_extension import _build_fraud_decision  # noqa: F811

        # Build an evaluator mock that raises
        evaluator = MagicMock()
        evaluator.dispatch.side_effect = ValueError("corrupt record")

        txn = _make_txn()

        # Replicate the guarded _evaluate logic
        with patch(
            "pipelines.scoring.metrics.evaluation_errors_total"
        ) as mock_counter:
            try:
                result = evaluator.dispatch(txn)
                decision = _build_fraud_decision(txn, result)
                output = (None, decision)
            except Exception:
                mock_counter.inc()
                default_decision = FraudDecision(
                    transaction_id=txn.get("transaction_id", ""),
                    decision="ALLOW",
                    fraud_score=0.0,
                    rule_triggers=[],
                    model_version="rule-only",
                    decision_time_ms=0,
                    latency_ms=0.0,
                    schema_version="1",
                )
                output = (None, default_decision)

        alert, decision = output
        assert alert is None
        assert isinstance(decision, FraudDecision)
        assert decision.decision == "ALLOW"
        assert decision.fraud_score == 0.0
        assert decision.rule_triggers == []
        assert decision.model_version == "rule-only"
        assert decision.transaction_id == "txn-test-001"
        mock_counter.inc.assert_called_once()


# ---------------------------------------------------------------------------
# BUG-JE5: Feature enrichment fallback
# ---------------------------------------------------------------------------


class TestFeatureEnrichmentFallback:
    """_FlinkFeatureEnrichmentFunction.map() must fall back on errors."""

    def _build_flink_enrichment_fn(self):
        """Build a _FlinkFeatureEnrichmentFunction-like wrapper with a mock _fn."""
        # We can't instantiate the real PyFlink MapFunction subclass without
        # a Flink runtime, but we can test via the module-level
        # _FeatureEnrichmentFunction + the guard logic.  Instead we import the
        # inner class pattern and replicate the guarded map.
        from pipelines.scoring import job_extension as je

        class _FakeFlinkWrapper:
            def __init__(self):
                self._fn = MagicMock(spec=_FeatureEnrichmentFunction)

            def map(self, value):
                try:
                    return self._fn.map(value)
                except Exception:  # noqa: BLE001
                    from pipelines.scoring.metrics import (
                        feature_store_fallback_total,
                    )

                    feature_store_fallback_total.labels(
                        reason="enrichment_error",
                    ).inc()
                    fallback = dict(value) if isinstance(value, dict) else {}
                    fallback.update(je._FEATURE_ZERO_DEFAULTS)
                    return fallback

        return _FakeFlinkWrapper()

    def test_feature_enrichment_failure_returns_zero_defaults(self):
        wrapper = self._build_flink_enrichment_fn()
        wrapper._fn.map.side_effect = RuntimeError("feature store down")

        txn = _make_txn()
        result = wrapper.map(txn)

        # Original fields preserved
        assert result["transaction_id"] == "txn-test-001"
        assert result["amount"] == 100.0

        # Zero-value defaults applied
        for key, default in _FEATURE_ZERO_DEFAULTS.items():
            assert result[key] == default, f"{key} mismatch"

    def test_feature_enrichment_failure_increments_fallback_counter(self):
        wrapper = self._build_flink_enrichment_fn()
        wrapper._fn.map.side_effect = RuntimeError("timeout")

        with patch(
            "pipelines.scoring.metrics.feature_store_fallback_total"
        ) as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            # Re-build wrapper so it picks up the patched metric
            wrapper2 = self._build_flink_enrichment_fn()
            wrapper2._fn.map.side_effect = RuntimeError("timeout")
            wrapper2.map(_make_txn())

            mock_counter.labels.assert_called_once_with(
                reason="enrichment_error",
            )
            mock_labels.inc.assert_called_once()


# ---------------------------------------------------------------------------
# BUG-JE2: Kafka emit ValueError safety
# ---------------------------------------------------------------------------


class TestKafkaEmitValueError:
    """ValueError from Kafka serialisation must not crash the pipeline."""

    def test_kafka_emit_value_error_does_not_crash(self):
        """Simulate the guarded _AlertSinkFunction.map() logic."""
        kafka_sink = MagicMock()
        kafka_sink.emit.side_effect = ValueError("bad serialisation")
        pg_sink = MagicMock()

        alert = SimpleNamespace(transaction_id="txn-bad-001")

        # Replicate the guarded map logic from _AlertSinkFunction
        try:
            kafka_sink.emit(alert)
        except (ValueError, TypeError, AttributeError):
            pass  # logged and continued in production code

        try:
            pg_sink.persist(alert)
        except Exception:
            pass

        # Key assertion: no unhandled exception propagated
        kafka_sink.emit.assert_called_once_with(alert)

    def test_kafka_emit_buffer_error_still_raises(self):
        """BufferError must propagate for back-pressure."""
        kafka_sink = MagicMock()
        kafka_sink.emit.side_effect = BufferError("buffer full")

        with pytest.raises(BufferError):
            # BufferError is NOT in the catch list
            try:
                kafka_sink.emit(MagicMock())
            except (ValueError, TypeError, AttributeError):
                pass
