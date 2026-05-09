"""PostgreSQL sink for fraud alerts — persists to fraud_alerts table."""

from __future__ import annotations

import logging

from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.types import FraudAlert

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO fraud_alerts (
    transaction_id,
    account_id,
    matched_rule_names,
    severity,
    evaluation_timestamp
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (transaction_id) DO NOTHING
"""


class AlertPostgresSink:
    """Persists FraudAlert records to the fraud_alerts PostgreSQL table.

    Uses ON CONFLICT (transaction_id) DO NOTHING for idempotency — safe to
    call multiple times for the same transaction_id without raising or duplicating.
    """

    def __init__(self, config: ScoringConfig) -> None:
        self._config = config
        self._conn = None

    def open(self) -> None:
        """Open database connection."""
        import psycopg2

        self._conn = psycopg2.connect(
            self._config.fraud_alerts_db_url,
            connect_timeout=5,
        )
        self._conn.autocommit = True

    def _ensure_connection(self) -> None:
        """Ping DB with SELECT 1; reconnect if dead."""
        if self._conn is None:
            self._connect()
            return
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            logger.warning("DB connection lost, reconnecting...")
            self._connect()

    def _connect(self) -> None:
        """Create a fresh psycopg2 connection."""
        import psycopg2

        self._conn = psycopg2.connect(
            self._config.fraud_alerts_db_url,
            connect_timeout=5,
        )
        self._conn.autocommit = True

    def persist(self, alert: FraudAlert) -> None:
        """Insert a FraudAlert into fraud_alerts, ignoring duplicates.

        Retries once on connection errors (OperationalError/InterfaceError).

        Args:
            alert: The FraudAlert to persist.

        Raises:
            psycopg2.Error: On unexpected database errors (not duplicate key).
        """
        import psycopg2

        self._ensure_connection()
        try:
            self._execute_insert(alert)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning(
                "Connection error during persist, retrying once..."
            )
            self._connect()
            self._execute_insert(alert)

    def _execute_insert(self, alert: FraudAlert) -> None:
        """Execute the INSERT statement for a single alert."""
        with self._conn.cursor() as cur:
            cur.execute(
                _INSERT_SQL,
                (
                    alert.transaction_id,
                    alert.account_id,
                    alert.matched_rule_names,
                    alert.severity,
                    alert.evaluation_timestamp,
                ),
            )
        self._conn.commit()
        logger.debug(
            "Persisted fraud alert for txn=%s", alert.transaction_id
        )

    def close(self) -> None:
        """Close DB connection safely — tolerates already-closed state."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Connection already closed or close failed.")
            finally:
                self._conn = None
