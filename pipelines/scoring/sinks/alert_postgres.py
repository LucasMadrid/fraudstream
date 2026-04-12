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

        self._conn = psycopg2.connect(self._config.fraud_alerts_db_url)

    def persist(self, alert: FraudAlert) -> None:
        """Insert a FraudAlert into fraud_alerts, ignoring duplicates.

        Args:
            alert: The FraudAlert to persist.

        Raises:
            psycopg2.Error: On unexpected database errors (not duplicate key).
        """
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
        logger.debug("Persisted fraud alert for txn=%s", alert.transaction_id)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
