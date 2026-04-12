"""TDD tests for AlertPostgresSink.

AlertPostgresSink persists FraudAlert to the fraud_alerts table.
Uses ON CONFLICT (transaction_id) DO NOTHING for idempotency.
"""

from __future__ import annotations

from unittest.mock import MagicMock

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
