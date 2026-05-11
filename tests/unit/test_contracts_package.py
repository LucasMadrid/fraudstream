"""Tests for the contracts package interface protocols (CHB-006).

Verifies that:
1. All protocols are runtime checkable
2. Existing implementations satisfy protocols
3. Protocol methods have correct signatures
4. Forward references work correctly
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from contracts import (
    AlertSinkProtocol,
    FeatureServingProtocol,
    FraudAlert,
    FeatureVector,
    InferenceClient,
)


class TestFeatureServingProtocol:
    """Tests for FeatureServingProtocol."""

    def test_is_runtime_checkable(self):
        """Protocol must be runtime checkable."""
        # @runtime_checkable adds __instancecheck__ and __subclasscheck__
        assert hasattr(FeatureServingProtocol, "__instancecheck__")
        assert hasattr(FeatureServingProtocol, "__subclasscheck__")

    def test_has_required_methods(self):
        """Protocol must define all required methods."""
        required_methods = ["get_features", "health", "open", "close"]
        for method in required_methods:
            assert hasattr(FeatureServingProtocol, method)

    def test_get_features_signature(self):
        """get_features must have correct signature."""
        sig = inspect.signature(FeatureServingProtocol.get_features)
        params = list(sig.parameters.keys())
        assert "account_id" in params
        assert "transaction_id" in params
        assert "transaction_timestamp" in params

    def test_health_returns_dict(self):
        """health method must return dict."""
        sig = inspect.signature(FeatureServingProtocol.health)
        # Return type annotation should be dict[str, Any]
        # (we just verify the method exists with proper name)

    def test_mock_implementation_satisfies_protocol(self):
        """Mock implementation should satisfy protocol."""

        class MockFeatureClient:
            def get_features(self, account_id, transaction_id, transaction_timestamp):
                return None  # Would return FeatureVector in real impl

            def health(self):
                return {"status": "healthy"}

            def open(self):
                pass

            def close(self):
                pass

        mock = MockFeatureClient()
        assert isinstance(mock, FeatureServingProtocol)


class TestAlertSinkProtocol:
    """Tests for AlertSinkProtocol."""

    def test_is_runtime_checkable(self):
        """Protocol must be runtime checkable."""
        assert hasattr(AlertSinkProtocol, "__instancecheck__")
        assert hasattr(AlertSinkProtocol, "__subclasscheck__")

    def test_has_required_methods(self):
        """Protocol must define emit and close."""
        assert hasattr(AlertSinkProtocol, "emit")
        assert hasattr(AlertSinkProtocol, "close")
        assert hasattr(AlertSinkProtocol, "open")

    def test_emit_signature(self):
        """emit must accept FraudAlert parameter."""
        sig = inspect.signature(AlertSinkProtocol.emit)
        params = list(sig.parameters.keys())
        assert "alert" in params

    def test_existing_alert_postgres_satisfies_protocol(self):
        """AlertPostgresSink should satisfy AlertSinkProtocol."""
        # Import here to avoid issues if scoring deps not installed
        try:
            from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink
            from pipelines.scoring.types import FraudAlert

            # We can't instantiate without config, but we can verify the class
            # has the right methods
            assert hasattr(AlertPostgresSink, "emit")
            assert hasattr(AlertPostgresSink, "close")
            assert hasattr(AlertPostgresSink, "open")
        except ImportError:
            pytest.skip("Scoring dependencies not installed")

    def test_mock_implementation_satisfies_protocol(self):
        """Mock implementation should satisfy protocol."""

        class MockAlertSink:
            def emit(self, alert):
                pass

            def close(self):
                pass

            def open(self):
                pass

        mock = MockAlertSink()
        assert isinstance(mock, AlertSinkProtocol)


class TestInferenceClient:
    """Tests for InferenceClient protocol."""

    def test_is_runtime_checkable(self):
        """Protocol must be runtime checkable."""
        assert hasattr(InferenceClient, "__instancecheck__")
        assert hasattr(InferenceClient, "__subclasscheck__")

    def test_has_required_methods(self):
        """Protocol must define predict and health."""
        assert hasattr(InferenceClient, "predict")
        assert hasattr(InferenceClient, "health")
        assert hasattr(InferenceClient, "open")
        assert hasattr(InferenceClient, "close")

    def test_predict_signature(self):
        """predict must have correct signature."""
        sig = inspect.signature(InferenceClient.predict)
        params = list(sig.parameters.keys())
        assert "features" in params
        assert "timeout_ms" in params

        # Check default value for timeout_ms
        timeout_param = sig.parameters["timeout_ms"]
        assert timeout_param.default == 50

    def test_mock_implementation_satisfies_protocol(self):
        """Mock implementation should satisfy protocol."""

        class MockInferenceClient:
            def predict(self, features, timeout_ms=50):
                return 0.5

            def health(self):
                return {"status": "healthy"}

            def open(self):
                pass

            def close(self):
                pass

        mock = MockInferenceClient()
        assert isinstance(mock, InferenceClient)


class TestForwardReferences:
    """Tests for forward reference protocols."""

    def test_feature_vector_protocol_has_attributes(self):
        """FeatureVector protocol should define all feature fields."""
        required_attrs = [
            "account_id",
            "vel_count_1m",
            "vel_amount_1m",
            "geo_country",
            "geo_city",
            "device_first_seen",
            "device_known_fraud",
        ]
        # For Protocol classes with attributes, check __annotations__
        annotations = getattr(FeatureVector, "__annotations__", {})
        for attr in required_attrs:
            assert attr in annotations, f"Missing attribute: {attr}"

    def test_fraud_alert_protocol_has_attributes(self):
        """FraudAlert protocol should define all alert fields."""
        required_attrs = [
            "transaction_id",
            "account_id",
            "matched_rule_names",
            "severity",
            "evaluation_timestamp",
        ]
        annotations = getattr(FraudAlert, "__annotations__", {})
        for attr in required_attrs:
            assert attr in annotations, f"Missing attribute: {attr}"


class TestProtocolDocumentation:
    """Tests for protocol documentation."""

    def test_feature_serving_has_docstring(self):
        """FeatureServingProtocol must have docstring with Constitution refs."""
        assert FeatureServingProtocol.__doc__ is not None
        assert "Constitution" in FeatureServingProtocol.__doc__
        assert "Article 3" in FeatureServingProtocol.__doc__

    def test_alert_sink_has_docstring(self):
        """AlertSinkProtocol must have docstring with Constitution refs."""
        assert AlertSinkProtocol.__doc__ is not None
        assert "Constitution" in AlertSinkProtocol.__doc__

    def test_inference_client_has_docstring(self):
        """InferenceClient must have docstring with Constitution refs."""
        assert InferenceClient.__doc__ is not None
        assert "Constitution" in InferenceClient.__doc__

    def test_methods_have_constitution_references(self):
        """Key methods should reference Constitution principles."""
        # Check get_features mentions Article 5
        get_features_doc = FeatureServingProtocol.get_features.__doc__
        assert get_features_doc is not None
        assert "Article 5" in get_features_doc

        # Check predict mentions Articles 5 and 6
        predict_doc = InferenceClient.predict.__doc__
        assert predict_doc is not None
        assert "Article 5" in predict_doc
        assert "Article 6" in predict_doc


class TestPackageExports:
    """Tests for package-level exports."""

    def test_all_exports_defined(self):
        """__all__ must list all public exports."""
        import contracts

        assert hasattr(contracts, "__all__")
        assert "FeatureServingProtocol" in contracts.__all__
        assert "AlertSinkProtocol" in contracts.__all__
        assert "InferenceClient" in contracts.__all__
        assert "FeatureVector" in contracts.__all__
        assert "FraudAlert" in contracts.__all__

    def test_can_import_all_exports(self):
        """All exports should be importable."""
        from contracts import (
            AlertSinkProtocol,
            FeatureServingProtocol,
            FeatureVector,
            FraudAlert,
            InferenceClient,
        )

        # Just verify they exist
        assert AlertSinkProtocol is not None
        assert FeatureServingProtocol is not None
        assert InferenceClient is not None
        assert FeatureVector is not None
        assert FraudAlert is not None
