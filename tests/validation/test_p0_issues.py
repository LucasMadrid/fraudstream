"""P0 validation tests for critical issues CHB-003, CHB-004, CHB-005.

These tests validate that high-priority bugs have been fixed and remain fixed.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# CHB-003 (P0-003): IcebergEnrichedSink DLQ when table=None
# =============================================================================


class TestCHB003IcebergSinkDLQOnNoneTable:
    """P0-003: IcebergEnrichedSink must emit DLQ event when table=None.

    Issue: When the Iceberg table is not loaded (table=None), records were
    silently dropped instead of being routed to the DLQ.
    """

    def test_emit_dlq_event_logs_json_to_dlq_logger(self, caplog):
        """Test that _emit_dlq_event logs a valid JSON message to dlq_logger."""
        from pipelines.processing.operators.iceberg_sink import _DLQEvent, _emit_dlq_event

        caplog.set_level(logging.WARNING, logger="dlq")

        event = _DLQEvent(
            transaction_id="txn-test-001",
            reason="iceberg_table_not_loaded",
            batch_size=42,
        )
        _emit_dlq_event(event)

        # Verify DLQ logger recorded the event
        dlq_records = [r for r in caplog.records if r.name == "dlq"]
        assert len(dlq_records) >= 1, "Expected DLQ log record"

        # Verify the message is valid JSON with expected fields
        record = dlq_records[0]
        parsed = json.loads(record.message)
        assert parsed["event"] == "iceberg_sink_dlq"
        assert parsed["transaction_id"] == "txn-test-001"
        assert parsed["reason"] == "iceberg_table_not_loaded"
        assert parsed["batch_size"] == 42

    def test_dlq_event_dataclass_is_frozen(self):
        """Test that _DLQEvent dataclass is immutable."""
        from pipelines.processing.operators.iceberg_sink import _DLQEvent

        event = _DLQEvent(
            transaction_id="txn-001",
            reason="timeout",
            batch_size=10,
        )

        # Should be frozen (immutable)
        with pytest.raises(AttributeError):
            event.transaction_id = "txn-002"

    def test_iceberg_sink_routes_to_dlq_when_table_none(self, caplog):
        """Test that when table is None, records are routed to DLQ not silently dropped."""
        pytest.importorskip("pyarrow")

        from pipelines.processing.operators.iceberg_sink import IcebergEnrichedSink

        caplog.set_level(logging.WARNING, logger="dlq")

        sink = IcebergEnrichedSink()
        sink._table = None  # Simulate table load failure
        sink._breaker = None

        # Add records to buffer
        records = [
            {"transaction_id": f"txn-{i:03d}", "amount": 100.0}
            for i in range(3)
        ]
        sink._buffer = list(records)

        # Flush should route all records to DLQ
        sink._flush()

        # Buffer must be cleared
        assert len(sink._buffer) == 0

        # Verify DLQ events were emitted for each record
        dlq_messages = [
            r.message
            for r in caplog.records
            if "iceberg_sink_dlq" in r.message
        ]

        # Should have 3 DLQ messages (one per record)
        assert len(dlq_messages) == 3, (
            f"Expected 3 DLQ messages (one per record), got {len(dlq_messages)}"
        )

        # Verify all messages have correct reason
        for msg in dlq_messages:
            parsed = json.loads(msg)
            assert parsed["event"] == "iceberg_sink_dlq"
            assert parsed["reason"] == "iceberg_table_not_loaded"
            assert "transaction_id" in parsed


# =============================================================================
# CHB-004 (P0-004): Scoring metrics use SafeMetric wrappers
# =============================================================================


class TestCHB004ScoringMetricsUseSafeWrappers:
    """P0-004: Scoring metrics must use SafeMetric wrappers, not raw Prometheus counters.

    Issue: Raw prometheus_client counters could cause crashes in PyFlink workers
    where prometheus_client is not available. SafeMetric wrappers prevent this.
    """

    def test_rule_evaluations_total_is_safe_counter(self):
        """Test that rule_evaluations_total is a SafeCounter instance."""
        from pipelines.scoring.metrics import rule_evaluations_total
        from pipelines.scoring.safe_metrics import SafeCounter

        assert isinstance(rule_evaluations_total, SafeCounter), (
            f"rule_evaluations_total must be SafeCounter, got {type(rule_evaluations_total)}"
        )

    def test_rule_flags_total_is_safe_counter(self):
        """Test that rule_flags_total is a SafeCounter instance."""
        from pipelines.scoring.metrics import rule_flags_total
        from pipelines.scoring.safe_metrics import SafeCounter

        assert isinstance(rule_flags_total, SafeCounter), (
            f"rule_flags_total must be SafeCounter, got {type(rule_flags_total)}"
        )

    def test_all_scoring_counters_are_safe_wrappers(self):
        """Test that all scoring metrics use Safe wrappers."""
        from pipelines.scoring.metrics import (
            feature_store_fallback_total,
            feature_store_miss_total,
            iceberg_decisions_buffer_overflow_total,
            iceberg_decisions_catalog_unavailable_total,
            evaluation_errors_total,
            rule_active_fp_total,
            rule_flags_total,
            rule_shadow_fp_total,
            rule_shadow_triggers_total,
            rule_triggers_total,
            rule_evaluations_total,
        )
        from pipelines.scoring.safe_metrics import SafeCounter, SafeGauge, SafeHistogram

        # All these should be Safe* instances
        metrics_to_check = [
            ("feature_store_fallback_total", feature_store_fallback_total),
            ("feature_store_miss_total", feature_store_miss_total),
            ("iceberg_decisions_buffer_overflow_total", iceberg_decisions_buffer_overflow_total),
            ("iceberg_decisions_catalog_unavailable_total", iceberg_decisions_catalog_unavailable_total),
            ("evaluation_errors_total", evaluation_errors_total),
            ("rule_active_fp_total", rule_active_fp_total),
            ("rule_flags_total", rule_flags_total),
            ("rule_shadow_fp_total", rule_shadow_fp_total),
            ("rule_shadow_triggers_total", rule_shadow_triggers_total),
            ("rule_triggers_total", rule_triggers_total),
            ("rule_evaluations_total", rule_evaluations_total),
        ]

        for name, metric in metrics_to_check:
            assert isinstance(metric, (SafeCounter, SafeGauge, SafeHistogram)), (
                f"{name} must be a SafeMetric wrapper, got {type(metric)}"
            )

    def test_no_raw_prometheus_counters_used(self):
        """Test that no raw prometheus_client Counter objects are used directly."""
        try:
            from prometheus_client import Counter as PrometheusCounter
        except ImportError:
            pytest.skip("prometheus_client not available")

        from pipelines.scoring.metrics import (
            feature_store_fallback_total,
            feature_store_miss_total,
            rule_evaluations_total,
            rule_flags_total,
        )

        # Ensure these are NOT raw prometheus counters
        assert not isinstance(rule_evaluations_total, PrometheusCounter), (
            "rule_evaluations_total should not be a raw Prometheus Counter"
        )
        assert not isinstance(rule_flags_total, PrometheusCounter), (
            "rule_flags_total should not be a raw Prometheus Counter"
        )
        assert not isinstance(feature_store_fallback_total, PrometheusCounter), (
            "feature_store_fallback_total should not be a raw Prometheus Counter"
        )
        assert not isinstance(feature_store_miss_total, PrometheusCounter), (
            "feature_store_miss_total should not be a raw Prometheus Counter"
        )

    def test_safe_counter_handles_missing_prometheus_gracefully(self):
        """Test that SafeCounter works even when prometheus_client is unavailable."""
        from pipelines.scoring.safe_metrics import SafeCounter

        # Create a counter (should not raise even if prometheus is not available)
        counter = SafeCounter(
            "test_counter_p0",
            "Test counter for P0 validation",
            ["label1"],
        )

        # These operations should not raise - use labels first for labeled counter
        child = counter.labels(label1="value1")
        child.inc()

        # If we get here without exception, the SafeCounter is working correctly
        assert True


# =============================================================================
# CHB-005 (P0-005): AlertKafkaSink.close() works correctly
# =============================================================================


class TestCHB005AlertKafkaSinkClose:
    """P0-005: AlertKafkaSink.close() must flush producer and be idempotent.

    Issue: AlertKafkaSink.close() was not implemented correctly, potentially
    leaving messages unflushed or crashing on second call.
    """

    def test_close_flushes_producer(self):
        """Test that close() flushes the Kafka producer."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)

        mock_producer = MagicMock()
        sink._producer = mock_producer

        sink.close()

        # Producer flush should be called twice:
        # 1. From self.flush() call (no timeout arg)
        # 2. From the explicit flush(timeout=10) in close()
        assert mock_producer.flush.call_count >= 1
        # One of the calls should have timeout=10
        flush_calls = [call for call in mock_producer.flush.call_args_list]
        timeout_call_found = any(
            call.kwargs.get("timeout") == 10 or (len(call.args) > 0 and call.args[0] == 10)
            for call in flush_calls
        )
        assert timeout_call_found, "Expected flush to be called with timeout=10"

    def test_close_nulls_producer(self):
        """Test that close() sets _producer to None after flushing."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)

        mock_producer = MagicMock()
        sink._producer = mock_producer

        sink.close()

        # Producer should be nulled after close
        assert sink._producer is None

    def test_close_is_idempotent(self):
        """Test that close() can be called twice without error."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)

        mock_producer = MagicMock()
        sink._producer = mock_producer

        # First close
        sink.close()

        # Second close should not raise
        sink.close()

        # Third close should also be safe
        sink.close()

    def test_close_handles_none_producer_gracefully(self):
        """Test that close() handles case where _producer is already None."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        config = ScoringConfig()
        sink = AlertKafkaSink(config)

        # Start with None producer
        sink._producer = None

        # Should not raise
        sink.close()

    def test_close_after_emit_sequence(self):
        """Test close() works correctly after normal emit operations."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink
        from pipelines.scoring.types import FraudAlert

        config = ScoringConfig()
        sink = AlertKafkaSink(config)

        mock_producer = MagicMock()
        sink._producer = mock_producer

        # Emit some alerts
        for i in range(3):
            alert = FraudAlert(
                transaction_id=f"txn-{i:03d}",
                account_id="acc-001",
                matched_rule_names=["RULE-001"],
                severity="high",
                evaluation_timestamp=1_700_000_000_000,
            )
            sink.emit(alert)

        # Verify produce was called 3 times
        assert mock_producer.produce.call_count == 3

        # Close should flush
        sink.close()
        # Flush should be called at least once (from flush() and/or close())
        assert mock_producer.flush.call_count >= 1

        # Subsequent close should be safe
        sink.close()
