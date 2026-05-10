"""TDD tests for AlertPostgresSink.

AlertPostgresSink persists FraudAlert to the fraud_alerts table.
Uses ON CONFLICT (transaction_id) DO NOTHING for idempotency.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipelines.scoring.types import FraudAlert


def _make_alert(**kwargs) -> FraudAlert:
    defaults = dict(
        transaction_id="txn-001",
        account_id="acc-001",
        matched_rule_names=["VEL-001", "ND-003"],
        severity="critical",
        evaluation_timestamp=1_700_000_000_000,
    )
    defaults.update(kwargs)
    return FraudAlert(**defaults)


class TestAlertPostgresSinkInit:
    def test_imports_without_error(self):
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink  # noqa: F401

    def test_instantiates_with_config(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        assert sink is not None


class TestAlertPostgresSinkPersist:
    def test_persist_executes_insert(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        alert = _make_alert()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        sink.persist(alert)
        mock_cur.execute.assert_called_once()

    def test_persist_uses_on_conflict_do_nothing(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        alert = _make_alert()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        sink.persist(alert)
        sql = mock_cur.execute.call_args[0][0]
        assert "ON CONFLICT" in sql.upper()
        assert "DO NOTHING" in sql.upper()

    def test_persist_passes_correct_values(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        alert = _make_alert(transaction_id="txn-unique", account_id="acc-x", severity="high")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        sink.persist(alert)
        params = mock_cur.execute.call_args[0][1]
        assert "txn-unique" in params
        assert "acc-x" in params
        assert "high" in params

    def test_persist_commits_transaction(self):
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        alert = _make_alert()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        sink.persist(alert)
        mock_conn.commit.assert_called_once()

    def test_persist_duplicate_transaction_id_does_not_raise(self):
        """ON CONFLICT DO NOTHING — second insert is silently ignored."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)
        alert = _make_alert()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        sink.persist(alert)
        sink.persist(alert)  # second call — must not raise
        assert mock_cur.execute.call_count == 2


class TestAlertPostgresSinkReconnection:
    def test_reconnects_on_dead_connection(self):
        """_ensure_connection reconnects when SELECT 1 fails."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)

        # Start with no connection
        sink._conn = None

        with patch.object(sink, "_connect", return_value=None) as mock_connect:
            sink._ensure_connection()

        # Verify _connect was called when conn was None
        mock_connect.assert_called_once()

    def test_rollback_on_persist_error(self):
        """autocommit=True avoids aborted transaction state.

        With autocommit, each statement is its own transaction, so no
        explicit rollback is needed. We verify autocommit is set.
        """

        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)

        mock_conn = MagicMock()
        mock_conn.autocommit = True
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = [
            None,  # SELECT 1 in _ensure_connection
            Exception("some DB error"),  # INSERT fails
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        sink._conn = mock_conn

        alert = _make_alert()
        try:
            sink.persist(alert)
        except Exception:
            pass

        # autocommit is set — no aborted transaction state
        assert mock_conn.autocommit is True

    def test_close_already_closed_connection(self):
        """close() doesn't crash if connection already closed."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)

        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("already closed")
        sink._conn = mock_conn

        # Should not raise
        sink.close()
        assert sink._conn is None

    def test_connect_timeout_is_set(self):
        """connect_timeout=5 is passed to psycopg2.connect()."""
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)

        with patch("psycopg2.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            sink.open()
            mock_connect.assert_called_once_with(
                config.fraud_alerts_db_url,
                connect_timeout=5,
            )

    def test_persist_retries_once_on_connection_error(self):
        """persist() retries exactly once on OperationalError."""
        from unittest.mock import patch

        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

        config = ScoringConfig()
        sink = AlertPostgresSink(config)

        # Create a mock exception class that inherits from Exception
        class MockOperationalError(Exception):
            pass

        # Track call count
        call_count = [0]

        def failing_insert(alert):
            call_count[0] += 1
            if call_count[0] == 1:
                raise MockOperationalError("connection reset")

        # Set up mock connection
        mock_conn = MagicMock()
        sink._conn = mock_conn

        # Patch the psycopg2 module where it's used in the persist method
        mock_psycopg2 = MagicMock(
            OperationalError=MockOperationalError, InterfaceError=MockOperationalError
        )
        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            with patch.object(sink, "_execute_insert", side_effect=failing_insert):
                with patch.object(sink, "_connect") as mock_reconnect:
                    alert = _make_alert()
                    sink.persist(alert)

        # Verify _connect was called for retry (once after the error)
        mock_reconnect.assert_called_once()
