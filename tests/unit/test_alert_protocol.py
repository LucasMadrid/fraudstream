"""Unit tests for AlertSink protocol and concrete implementations.

C34 — verify structural subtyping and that AlertPostgresSink.emit() delegates
to persist() so both sinks satisfy the shared seam.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Stub heavy optional deps before any import that pulls them in lazily.
for _mod in ("psycopg2", "confluent_kafka"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from pipelines.shared.alert_protocol import AlertSink  # noqa: E402

# ---------------------------------------------------------------------------
# Protocol structural subtyping
# ---------------------------------------------------------------------------


class TestAlertSinkProtocol:
    def test_object_with_emit_satisfies_protocol(self):
        class _Stub:
            def emit(self, alert):
                pass

        assert isinstance(_Stub(), AlertSink)

    def test_object_missing_emit_does_not_satisfy_protocol(self):
        class _NoEmit:
            def persist(self, alert):
                pass

        assert not isinstance(_NoEmit(), AlertSink)

    def test_stub_captures_emit_calls(self):
        calls: list = []

        class _FakeSink:
            def emit(self, alert):
                calls.append(alert)

        sink: AlertSink = _FakeSink()
        sentinel = object()
        sink.emit(sentinel)
        assert calls == [sentinel]


# ---------------------------------------------------------------------------
# AlertKafkaSink satisfies AlertSink protocol
# ---------------------------------------------------------------------------


class TestAlertKafkaSinkProtocol:
    def test_satisfies_alert_sink_protocol(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink

        cfg = MagicMock(spec=ScoringConfig)
        with patch("confluent_kafka.Producer"):
            sink = AlertKafkaSink(cfg)

        assert isinstance(sink, AlertSink)


# ---------------------------------------------------------------------------
# AlertPostgresSink satisfies AlertSink protocol and delegates correctly
# ---------------------------------------------------------------------------


class TestAlertPostgresSinkProtocol:
    def test_satisfies_alert_sink_protocol(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        cfg = MagicMock(spec=ScoringConfig)
        sink = AlertPostgresSink(cfg)

        assert isinstance(sink, AlertSink)

    def test_emit_delegates_to_persist(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        cfg = MagicMock(spec=ScoringConfig)
        sink = AlertPostgresSink(cfg)

        alert = MagicMock()
        with patch.object(sink, "persist") as mock_persist:
            sink.emit(alert)

        mock_persist.assert_called_once_with(alert)
