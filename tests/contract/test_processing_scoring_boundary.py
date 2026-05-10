"""Contract tests for P0-006: processing → scoring boundary (CHB-006).

TB-002: Interface contract tests ensuring the processing layer communicates
with the scoring layer only through well-defined interfaces, without direct
dependencies on scoring internals.

Architecture Contract (CHB-006):
    Processing Layer ──► Interface Contract ◄── Scoring Layer (implements)

The processing layer (pipelines/processing) must not import from scoring
internals directly. Instead, it uses:
    - RuleMetricsPublisher interface for rule-related metrics
    - SafeMetricsProvider interface for general metrics

These interfaces are defined in pipelines/shared/interfaces/ and implemented
by the scoring layer at runtime via dependency injection.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from pipelines.shared.interfaces import (
    NoOpRuleMetricsPublisher,
    RuleMetricsPublisher,
    get_metrics_provider,
    get_rule_metrics_publisher,
    set_metrics_provider,
    set_rule_metrics_publisher,
)


# =============================================================================
# Test: kafka_metrics_bridge.py doesn't import scoring internals directly
# =============================================================================
class TestProcessingLayerIndependence:
    """Verify processing layer independence from scoring implementation."""

    def test_kafka_metrics_bridge_no_direct_scoring_imports(self):
        """TB-002-01: kafka_metrics_bridge.py must not import from pipelines.scoring.

        The processing layer should only use the shared interface contracts,
        never directly import scoring internals like metrics or safe_metrics.
        """
        bridge_path = (
            Path(__file__).parent.parent.parent
            / "pipelines"
            / "processing"
            / "kafka_metrics_bridge.py"
        )
        assert bridge_path.exists(), f"kafka_metrics_bridge.py not found at {bridge_path}"

        source = bridge_path.read_text()
        tree = ast.parse(source)

        # Collect all import statements
        scoring_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("pipelines.scoring"):
                        scoring_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("pipelines.scoring"):
                    module_name = node.module
                    imported_names = [alias.name for alias in node.names]
                    scoring_imports.append(f"{module_name}: {imported_names}")

        # The only allowed import is pipelines.scoring.interfaces (which provides interface
        # implementations). But ideally, processing layer shouldn't even import that - it should
        # just use shared.interfaces
        allowed_patterns = [
            "pipelines.scoring.interfaces",  # Interface registration only
        ]

        # Filter out allowed patterns
        disallowed_imports = [
            imp
            for imp in scoring_imports
            if not any(imp.startswith(allowed) for allowed in allowed_patterns)
        ]

        assert not disallowed_imports, (
            f"kafka_metrics_bridge.py imports scoring internals directly: {disallowed_imports}\n"
            "Processing layer must only use interfaces from pipelines.shared.interfaces"
        )

    def test_processing_layer_uses_interface_imports_only(self):
        """TB-002-02: kafka_metrics_bridge.py must import from shared.interfaces.

        The bridge should use get_metrics_provider and get_rule_metrics_publisher
        from pipelines.shared.interfaces.
        """
        bridge_path = (
            Path(__file__).parent.parent.parent
            / "pipelines"
            / "processing"
            / "kafka_metrics_bridge.py"
        )
        source = bridge_path.read_text()
        tree = ast.parse(source)

        # Check for interface imports
        has_shared_interfaces_import = False
        imported_names = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "pipelines.shared.interfaces":
                    has_shared_interfaces_import = True
                    imported_names.extend([alias.name for alias in node.names])

        assert has_shared_interfaces_import, (
            "kafka_metrics_bridge.py must import from pipelines.shared.interfaces"
        )

        # Verify key interface functions are imported
        required_names = {"get_metrics_provider", "get_rule_metrics_publisher"}
        missing = required_names - set(imported_names)
        assert not missing, f"kafka_metrics_bridge.py missing required imports: {missing}"


# =============================================================================
# Test: Metrics exposed via RuleMetricsPublisher interface
# =============================================================================
class TestRuleMetricsPublisherInterface:
    """Verify RuleMetricsPublisher interface contract is properly used."""

    def test_rule_metrics_publisher_is_abstract_base_class(self):
        """TB-002-03: RuleMetricsPublisher must be an ABC with required methods."""
        import inspect

        # Verify it's an abstract class
        assert hasattr(RuleMetricsPublisher, "__abstractmethods__")
        abstract_methods = RuleMetricsPublisher.__abstractmethods__
        assert "record_rule_evaluation" in abstract_methods
        assert "record_rule_flag" in abstract_methods

        # Verify method signatures
        sig_eval = inspect.signature(RuleMetricsPublisher.record_rule_evaluation)
        params_eval = list(sig_eval.parameters.keys())
        assert "rule_id" in params_eval
        assert "rule_family" in params_eval

        sig_flag = inspect.signature(RuleMetricsPublisher.record_rule_flag)
        params_flag = list(sig_flag.parameters.keys())
        assert "rule_id" in params_flag
        assert "rule_family" in params_flag
        assert "severity" in params_flag

    def test_noop_rule_metrics_publisher_implements_interface(self):
        """TB-002-04: NoOpRuleMetricsPublisher must implement RuleMetricsPublisher."""
        noop = NoOpRuleMetricsPublisher()

        # Should not raise - all abstract methods implemented
        noop.record_rule_evaluation("rule-1", "velocity")
        noop.record_rule_flag("rule-1", "velocity", "high")

        # Verify it's a concrete implementation (not abstract)
        assert not getattr(NoOpRuleMetricsPublisher, "__abstractmethods__", None)

    def test_rule_metrics_publisher_can_be_set_and_retrieved(self):
        """TB-002-05: set_rule_metrics_publisher and get_rule_metrics_publisher work."""
        # Store original
        original = get_rule_metrics_publisher()

        try:
            # Create a mock publisher
            mock_publisher = MagicMock(spec=RuleMetricsPublisher)
            set_rule_metrics_publisher(mock_publisher)

            # Retrieve and verify
            retrieved = get_rule_metrics_publisher()
            assert retrieved is mock_publisher

            # Verify methods can be called
            retrieved.record_rule_evaluation("rule-1", "velocity")
            mock_publisher.record_rule_evaluation.assert_called_once_with("rule-1", "velocity")

            retrieved.record_rule_flag("rule-1", "velocity", "high")
            mock_publisher.record_rule_flag.assert_called_once_with("rule-1", "velocity", "high")
        finally:
            # Restore original
            set_rule_metrics_publisher(
                original if not isinstance(original, NoOpRuleMetricsPublisher) else None
            )

    def test_get_rule_metrics_publisher_returns_noop_when_none_set(self):
        """TB-002-06: get_rule_metrics_publisher returns NoOpRuleMetricsPublisher by default."""
        # Store original
        original = get_rule_metrics_publisher()

        try:
            # Clear the publisher
            set_rule_metrics_publisher(None)

            # Should get NoOp implementation
            publisher = get_rule_metrics_publisher()
            assert isinstance(publisher, NoOpRuleMetricsPublisher)

            # Should be callable without error
            publisher.record_rule_evaluation("rule-1", "velocity")
            publisher.record_rule_flag("rule-1", "velocity", "high")
        finally:
            # Restore original
            set_rule_metrics_publisher(
                original if not isinstance(original, NoOpRuleMetricsPublisher) else None
            )


# =============================================================================
# Test: Processing layer independent of scoring implementation
# =============================================================================
class TestProcessingScoringIndependence:
    """Verify processing layer works without scoring layer present."""

    def test_processing_layer_runs_without_scoring_registration(self):
        """TB-002-07: Processing layer must function without scoring registration.

        When scoring layer hasn't registered implementations, the processing
        layer should gracefully fall back to NoOp implementations.
        """
        # Store originals
        original_metrics_provider = get_metrics_provider()
        original_rule_publisher = get_rule_metrics_publisher()

        try:
            # Clear all registrations (simulate scoring layer not loaded)
            set_metrics_provider(None)
            set_rule_metrics_publisher(None)

            # Get providers - should return NoOp implementations
            metrics_provider = get_metrics_provider()
            rule_publisher = get_rule_metrics_publisher()

            # Both should be callable without error
            counter = metrics_provider.get_counter("test_counter", "A test counter", ("label1",))
            counter.labels(label1="value1").inc()

            rule_publisher.record_rule_evaluation("RULE-001", "velocity")
            rule_publisher.record_rule_flag("RULE-001", "velocity", "high")

            # No exceptions should be raised
            assert True
        finally:
            # Restore originals
            set_metrics_provider(
                original_metrics_provider
                if not isinstance(original_metrics_provider, type(get_metrics_provider()))
                else None
            )
            set_rule_metrics_publisher(
                original_rule_publisher
                if not isinstance(original_rule_publisher, NoOpRuleMetricsPublisher)
                else None
            )

    def test_kafka_metrics_bridge_uses_interface_not_concrete_metrics(self):
        """TB-002-08: Verify bridge uses interface methods, not direct metric access.

        The kafka_metrics_bridge.py should call record_rule_evaluation and
        record_rule_flag through the interface, not directly access scoring metrics.
        """
        bridge_path = (
            Path(__file__).parent.parent.parent
            / "pipelines"
            / "processing"
            / "kafka_metrics_bridge.py"
        )
        source = bridge_path.read_text()

        # Should use get_rule_metrics_publisher() to get the publisher
        assert "get_rule_metrics_publisher()" in source, (
            "kafka_metrics_bridge.py must use get_rule_metrics_publisher()"
        )

        # Should call record_rule_evaluation through the interface
        assert "rule_metrics.record_rule_evaluation(" in source, (
            "kafka_metrics_bridge.py must call record_rule_evaluation() through interface"
        )

        # Should call record_rule_flag through the interface
        assert "rule_metrics.record_rule_flag(" in source, (
            "kafka_metrics_bridge.py must call record_rule_flag() through interface"
        )

        # Should NOT directly reference scoring layer metric names in the consumer threads
        # (except for bridge-specific metrics which are fine)
        forbidden_patterns = [
            "from pipelines.scoring.metrics import",
            "from pipelines.scoring.safe_metrics import",
            "rule_evaluations_total.labels(",  # Direct metric access
            "rule_flags_total.labels(",  # Direct metric access
        ]

        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"kafka_metrics_bridge.py contains forbidden pattern: {pattern}\n"
                "Must use interface methods instead of direct metric access"
            )

    def test_interface_methods_accept_required_parameters(self):
        """TB-002-09: Interface methods accept all required parameters."""
        noop = NoOpRuleMetricsPublisher()

        # Test record_rule_evaluation accepts rule_id and rule_family
        noop.record_rule_evaluation(rule_id="RULE-001", rule_family="velocity")
        noop.record_rule_evaluation("RULE-002", "amount")

        # Test record_rule_flag accepts rule_id, rule_family, and severity
        noop.record_rule_flag(rule_id="RULE-001", rule_family="velocity", severity="high")
        noop.record_rule_flag("RULE-002", "amount", "medium")
        noop.record_rule_flag("RULE-003", "device", severity="low")


# =============================================================================
# Test: SafeMetricsProvider interface contract
# =============================================================================
class TestSafeMetricsProviderInterface:
    """Verify SafeMetricsProvider interface contract."""

    def test_safe_metrics_provider_has_get_counter_method(self):
        """TB-002-10: SafeMetricsProvider must define get_counter method."""
        import inspect

        from pipelines.shared.interfaces import SafeMetricsProvider

        assert hasattr(SafeMetricsProvider, "get_counter")

        sig = inspect.signature(SafeMetricsProvider.get_counter)
        params = list(sig.parameters.keys())

        assert "name" in params
        assert "documentation" in params
        assert "labelnames" in params

    def test_metrics_provider_returns_publisher_with_labels_and_inc(self):
        """TB-002-11: Metrics from provider must support labels().inc() pattern."""
        provider = get_metrics_provider()

        counter = provider.get_counter("test_metric", "Test metric", ("label1", "label2"))

        # Must have labels method that returns something with inc method
        labeled = counter.labels(label1="value1", label2="value2")
        assert hasattr(labeled, "inc")

        # Must be callable
        labeled.inc()
        labeled.inc(5)


# =============================================================================
# Integration: End-to-end contract verification
# =============================================================================
class TestProcessingScoringContractIntegration:
    """Integration tests verifying the complete contract flow."""

    def test_full_metrics_flow_through_interface(self):
        """TB-002-12: Complete metrics flow from processing through interface.

        Simulates how kafka_metrics_bridge.py uses the interface.
        """
        # Create a mock implementation that tracks calls
        calls = []

        class MockRuleMetricsPublisher(RuleMetricsPublisher):
            def record_rule_evaluation(self, rule_id: str, rule_family: str) -> None:
                calls.append(("evaluation", rule_id, rule_family))

            def record_rule_flag(self, rule_id: str, rule_family: str, severity: str) -> None:
                calls.append(("flag", rule_id, rule_family, severity))

        # Register mock
        mock_impl = MockRuleMetricsPublisher()
        set_rule_metrics_publisher(mock_impl)

        try:
            # Simulate processing layer activity (like kafka_metrics_bridge)
            rule_metrics = get_rule_metrics_publisher()

            # Simulate enriched message processing
            rule_family_map = {"VEL-001": "velocity", "AMT-001": "amount"}
            for rule_id, family in rule_family_map.items():
                rule_metrics.record_rule_evaluation(rule_id=rule_id, rule_family=family)

            # Simulate alert processing
            rule_metrics.record_rule_flag(
                rule_id="VEL-001", rule_family="velocity", severity="high"
            )

            # Verify calls were tracked
            assert ("evaluation", "VEL-001", "velocity") in calls
            assert ("evaluation", "AMT-001", "amount") in calls
            assert ("flag", "VEL-001", "velocity", "high") in calls

        finally:
            set_rule_metrics_publisher(None)

    def test_bridge_thread_functionality_with_interface(self):
        """TB-002-13: Bridge threads can use interface in simulated environment."""
        import threading
        import time

        calls = []
        call_lock = threading.Lock()

        class TrackingRuleMetricsPublisher(RuleMetricsPublisher):
            def record_rule_evaluation(self, rule_id: str, rule_family: str) -> None:
                with call_lock:
                    calls.append(("eval", rule_id, rule_family))

            def record_rule_flag(self, rule_id: str, rule_family: str, severity: str) -> None:
                with call_lock:
                    calls.append(("flag", rule_id, rule_family, severity))

        # Set tracking implementation
        set_rule_metrics_publisher(TrackingRuleMetricsPublisher())

        try:
            rule_metrics = get_rule_metrics_publisher()

            # Simulate what happens in the bridge threads
            def simulate_enriched_thread():
                for i in range(3):
                    rule_metrics.record_rule_evaluation(f"RULE-{i}", "velocity")
                    time.sleep(0.01)

            def simulate_alerts_thread():
                for i in range(2):
                    rule_metrics.record_rule_flag(f"RULE-{i}", "velocity", "high")
                    time.sleep(0.01)

            # Run threads
            t1 = threading.Thread(target=simulate_enriched_thread)
            t2 = threading.Thread(target=simulate_alerts_thread)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Verify calls were made through interface
            with call_lock:
                assert len(calls) == 5  # 3 evaluations + 2 flags

        finally:
            set_rule_metrics_publisher(None)
