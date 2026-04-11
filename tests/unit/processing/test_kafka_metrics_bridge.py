"""Unit tests for pipelines.processing.kafka_metrics_bridge."""

from __future__ import annotations

import io
import json
import textwrap
import threading
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _build_rule_family_map
# ---------------------------------------------------------------------------
class TestBuildRuleFamilyMap:
    def test_returns_rule_id_to_family_map(self, tmp_path):
        from pipelines.processing.kafka_metrics_bridge import _build_rule_family_map

        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
                - rule_id: VEL-001
                  family: velocity
                  enabled: true
                - rule_id: VEL-002
                  family: velocity
                  enabled: true
                - rule_id: ND-001
                  family: new_device
                  enabled: true
            """)
        )
        result = _build_rule_family_map(str(yaml_file))
        assert result == {
            "VEL-001": "velocity",
            "VEL-002": "velocity",
            "ND-001": "new_device",
        }

    def test_excludes_disabled_rules(self, tmp_path):
        from pipelines.processing.kafka_metrics_bridge import _build_rule_family_map

        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(
            textwrap.dedent("""\
                - rule_id: VEL-001
                  family: velocity
                  enabled: true
                - rule_id: VEL-002
                  family: velocity
                  enabled: false
            """)
        )
        result = _build_rule_family_map(str(yaml_file))
        assert "VEL-001" in result
        assert "VEL-002" not in result

    def test_returns_empty_dict_on_missing_file(self):
        from pipelines.processing.kafka_metrics_bridge import _build_rule_family_map

        result = _build_rule_family_map("/nonexistent/path/rules.yaml")
        assert result == {}

    def test_returns_empty_dict_on_empty_yaml(self, tmp_path):
        from pipelines.processing.kafka_metrics_bridge import _build_rule_family_map

        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("")
        result = _build_rule_family_map(str(yaml_file))
        assert result == {}


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------
class TestStartStop:
    def test_start_spawns_two_daemon_threads(self, tmp_path):
        from pipelines.processing import kafka_metrics_bridge

        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("- rule_id: VEL-001\n  family: velocity\n  enabled: true\n")

        # Use idents (unique per thread), not names — prior tests may have left
        # threads named "metrics-bridge-*" alive from TestJobMain.main() calls.
        before_idents = {t.ident for t in threading.enumerate()}

        kafka_metrics_bridge.start(
            brokers="localhost:9092",
            alerts_topic="txn.fraud.alerts",
            enriched_topic="txn.enriched",
            rules_yaml_path=str(yaml_file),
        )

        new_threads = [t for t in threading.enumerate() if t.ident not in before_idents]
        new_names = {t.name for t in new_threads}
        assert "metrics-bridge-alerts" in new_names
        assert "metrics-bridge-enriched" in new_names

        # Verify daemon flag on the newly spawned threads only
        for t in new_threads:
            if t.name in ("metrics-bridge-alerts", "metrics-bridge-enriched"):
                assert t.daemon is True

        kafka_metrics_bridge.stop()

    def test_stop_sets_stop_event(self):
        from pipelines.processing import kafka_metrics_bridge

        kafka_metrics_bridge._stop_event.clear()
        kafka_metrics_bridge.stop()
        assert kafka_metrics_bridge._stop_event.is_set()
        kafka_metrics_bridge._stop_event.clear()  # reset for other tests

    def test_start_clears_stop_event(self, tmp_path):
        from pipelines.processing import kafka_metrics_bridge

        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text("")

        kafka_metrics_bridge._stop_event.set()  # pre-set
        kafka_metrics_bridge.start(
            brokers="localhost:9092",
            alerts_topic="txn.fraud.alerts",
            enriched_topic="txn.enriched",
            rules_yaml_path=str(yaml_file),
        )
        assert not kafka_metrics_bridge._stop_event.is_set()
        kafka_metrics_bridge.stop()


# ---------------------------------------------------------------------------
# _alerts_consumer_thread — counter increments
# ---------------------------------------------------------------------------
class TestAlertsConsumerThread:
    def _make_avro_bytes(self, record: dict) -> bytes:
        import fastavro

        from pipelines.processing.kafka_metrics_bridge import _ALERT_SCHEMA_PATH

        parsed = fastavro.parse_schema(json.loads(_ALERT_SCHEMA_PATH.read_text()))
        buf = io.BytesIO()
        fastavro.writer(buf, parsed, [record])
        return buf.getvalue()

    def test_increments_rule_flags_for_matched_rules(self):
        from pipelines.processing.kafka_metrics_bridge import _alerts_consumer_thread
        from pipelines.scoring.metrics import rule_flags_total

        avro_bytes = self._make_avro_bytes(
            {
                "transaction_id": "txn-001",
                "account_id": "acc-001",
                "matched_rule_names": ["VEL-001", "VEL-002"],
                "severity": "high",
                "evaluation_timestamp": 1_700_000_000_000,
            }
        )

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = avro_bytes
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        rule_family_map = {"VEL-001": "velocity", "VEL-002": "velocity"}

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            _alerts_consumer_thread("localhost:9092", "txn.fraud.alerts", rule_family_map)

        vel001 = rule_flags_total.labels(
            rule_id="VEL-001", rule_family="velocity", severity="high"
        )._value.get()
        vel002 = rule_flags_total.labels(
            rule_id="VEL-002", rule_family="velocity", severity="high"
        )._value.get()
        assert vel001 >= 1
        assert vel002 >= 1

    def test_skips_malformed_messages_without_crashing(self):
        from pipelines.processing.kafka_metrics_bridge import _alerts_consumer_thread

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"not-avro-garbage"
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            # Must not raise
            _alerts_consumer_thread("localhost:9092", "txn.fraud.alerts", {"VEL-001": "velocity"})

        mock_consumer.close.assert_called_once()

    def test_skips_kafka_error_messages(self):
        from pipelines.processing.kafka_metrics_bridge import _alerts_consumer_thread

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = "kafka partition error"
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            _alerts_consumer_thread("localhost:9092", "txn.fraud.alerts", {"VEL-001": "velocity"})

        mock_consumer.close.assert_called_once()

    def test_severity_normalised_to_lowercase(self):
        from pipelines.processing.kafka_metrics_bridge import _alerts_consumer_thread
        from pipelines.scoring.metrics import rule_flags_total

        # Build Avro with lowercase "critical" (Avro enum symbol)
        avro_bytes = self._make_avro_bytes(
            {
                "transaction_id": "txn-002",
                "account_id": "acc-002",
                "matched_rule_names": ["VEL-003"],
                "severity": "critical",
                "evaluation_timestamp": 1_700_000_000_001,
            }
        )

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = avro_bytes
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            _alerts_consumer_thread("localhost:9092", "txn.fraud.alerts", {"VEL-003": "velocity"})

        val = rule_flags_total.labels(
            rule_id="VEL-003", rule_family="velocity", severity="critical"
        )._value.get()
        assert val >= 1


# ---------------------------------------------------------------------------
# _enriched_consumer_thread — counter increments
# ---------------------------------------------------------------------------
class TestEnrichedConsumerThread:
    def test_increments_evaluations_per_rule_per_message(self):
        from pipelines.processing.kafka_metrics_bridge import _enriched_consumer_thread
        from pipelines.scoring.metrics import rule_evaluations_total

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = None
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        rule_family_map = {"VEL-001": "velocity", "ND-001": "new_device"}

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            _enriched_consumer_thread("localhost:9092", "txn.enriched", rule_family_map)

        vel_val = rule_evaluations_total.labels(
            rule_id="VEL-001", rule_family="velocity"
        )._value.get()
        nd_val = rule_evaluations_total.labels(
            rule_id="ND-001", rule_family="new_device"
        )._value.get()
        assert vel_val >= 1
        assert nd_val >= 1

    def test_skips_thread_when_no_rules(self):
        """Empty rule_family_map exits immediately — no Consumer created."""
        from pipelines.processing.kafka_metrics_bridge import _enriched_consumer_thread

        with patch("confluent_kafka.Consumer") as mock_cls:
            _enriched_consumer_thread("localhost:9092", "txn.enriched", {})

        mock_cls.assert_not_called()

    def test_skips_kafka_error_messages(self):
        from pipelines.processing.kafka_metrics_bridge import _enriched_consumer_thread

        stop_event = threading.Event()
        call_count = [0]

        def fake_poll(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                msg = MagicMock()
                msg.error.return_value = "some error"
                return msg
            stop_event.set()
            return None

        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = fake_poll

        with patch(
            "pipelines.processing.kafka_metrics_bridge._stop_event", stop_event
        ), patch("confluent_kafka.Consumer", return_value=mock_consumer):
            _enriched_consumer_thread("localhost:9092", "txn.enriched", {"VEL-001": "velocity"})

        mock_consumer.close.assert_called_once()
