"""Kafka sink — serialises FraudAlert to Avro, produces to txn.fraud.alerts."""

from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.types import FraudAlert

logger = logging.getLogger(__name__)

_SCHEMA_PATH = (
    Path(__file__).parent.parent / "schemas" / "fraud-alert-v1.avsc"
)
_DLQ_SCHEMA_PATH = (
    Path(__file__).parent.parent / "schemas" / "fraud-alert-dlq-v1.avsc"
)


class AlertKafkaSink:
    """Produces FraudAlert records to the fraud alerts Kafka topic.

    On delivery failure, routes to the DLQ topic with error metadata.
    Schema Registry registration happens at open() time (T030).
    """

    def __init__(self, config: ScoringConfig) -> None:
        self._config = config
        self._producer = None
        self._parsed_schema = None
        self._dlq_schema = None

    def open(self) -> None:
        """Initialise Kafka producer and parse Avro schemas."""
        import fastavro
        from confluent_kafka import Producer

        self._producer = Producer(
            {"bootstrap.servers": self._config.kafka_brokers}
        )
        self._parsed_schema = fastavro.parse_schema(
            json.loads(_SCHEMA_PATH.read_text())
        )
        self._dlq_schema = fastavro.parse_schema(
            json.loads(_DLQ_SCHEMA_PATH.read_text())
        )
        self._register_schema()

    def _register_schema(self) -> None:
        """Register fraud-alert-v1 schema with Schema Registry (T030)."""
        try:
            from confluent_kafka.schema_registry import (  # noqa: PLC0415
                Schema,
                SchemaRegistryClient,
            )

            client = SchemaRegistryClient(
                {"url": self._config.schema_registry_url}
            )
            schema_str = _SCHEMA_PATH.read_text()
            subject = "txn.fraud.alerts-value"
            client.register_schema(subject, Schema(schema_str, "AVRO"))
            logger.info("Registered schema subject: %s", subject)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Schema Registry registration failed (non-fatal): %s", exc
            )

    def _load_schemas(self) -> None:
        """Parse Avro schemas on demand if open() was not called."""
        import fastavro

        if self._parsed_schema is None:
            self._parsed_schema = fastavro.parse_schema(
                json.loads(_SCHEMA_PATH.read_text())
            )
        if self._dlq_schema is None:
            self._dlq_schema = fastavro.parse_schema(
                json.loads(_DLQ_SCHEMA_PATH.read_text())
            )

    def _serialise(self, alert: FraudAlert) -> bytes:
        """Serialise FraudAlert to Avro bytes."""
        import fastavro

        self._load_schemas()
        record = {
            "transaction_id": alert.transaction_id,
            "account_id": alert.account_id,
            "matched_rule_names": alert.matched_rule_names,
            "severity": alert.severity,
            "evaluation_timestamp": alert.evaluation_timestamp,
        }
        buf = io.BytesIO()
        fastavro.writer(buf, self._parsed_schema, [record])
        return buf.getvalue()

    def _serialise_dlq(
        self, alert: FraudAlert, error_type: str, error_message: str
    ) -> bytes:
        """Serialise a failed alert to DLQ Avro bytes."""
        import fastavro

        self._load_schemas()
        record = {
            "transaction_id": alert.transaction_id,
            "account_id": alert.account_id,
            "matched_rule_names": alert.matched_rule_names,
            "severity": alert.severity,
            "evaluation_timestamp": alert.evaluation_timestamp,
            "error_type": error_type,
            "error_message": error_message,
            "failed_at": int(time.time() * 1000),
        }
        buf = io.BytesIO()
        fastavro.writer(buf, self._dlq_schema, [record])
        return buf.getvalue()

    def _on_delivery(self, err, msg, alert: FraudAlert) -> None:  # noqa: ARG002
        """Delivery report callback — routes failures to DLQ."""
        if err:
            logger.error(
                "Delivery failed for txn=%s: %s — routing to DLQ",
                alert.transaction_id,
                err,
            )
            dlq_payload = self._serialise_dlq(
                alert,
                error_type="DELIVERY_FAILURE",
                error_message=str(err),
            )
            self._producer.produce(
                topic=self._config.fraud_alerts_dlq_topic,
                key=alert.transaction_id.encode(),
                value=dlq_payload,
            )

    def emit(self, alert: FraudAlert) -> None:
        """Produce a FraudAlert to the fraud alerts topic."""
        payload = self._serialise(alert)
        self._producer.produce(
            topic=self._config.fraud_alerts_topic,
            key=alert.transaction_id.encode(),
            value=payload,
            on_delivery=lambda err, msg: self._on_delivery(err, msg, alert),
        )
        self._producer.poll(0)

    def flush(self) -> None:
        if self._producer:
            self._producer.flush()
