"""Kafka sink for shadow decisions — serializes shadow decision records to txn.shadow.decisions."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipelines.scoring.types import EvaluationResult, FraudDecision

from pipelines.scoring.config import ScoringConfig

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "shadow-decision-v1.avsc"


class ShadowDecisionKafkaSink:
    """Produces shadow decision records to the txn.shadow.decisions Kafka topic.

    Captures both production and shadow rule outcomes for comparison,
    enabling shadow mode analytics and rule performance evaluation.
    """

    def __init__(self, config: ScoringConfig) -> None:
        self._config = config
        self._producer = None
        self._parsed_schema = None

    def open(self) -> None:
        """Initialize Kafka producer and parse Avro schema."""
        import fastavro
        from confluent_kafka import Producer

        self._producer = Producer({"bootstrap.servers": self._config.kafka_brokers})
        self._parsed_schema = fastavro.parse_schema(json.loads(_SCHEMA_PATH.read_text()))
        self._register_schema()

    def _register_schema(self) -> None:
        """Register shadow-decision-v1 schema with Schema Registry."""
        try:
            from confluent_kafka.schema_registry import (  # noqa: PLC0415
                Schema,
                SchemaRegistryClient,
            )

            client = SchemaRegistryClient({"url": self._config.schema_registry_url})
            schema_str = _SCHEMA_PATH.read_text()
            subject = "txn.shadow.decisions-value"
            client.register_schema(subject, Schema(schema_str, "AVRO"))
            logger.info("Registered schema subject: %s", subject)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Schema Registry registration failed (non-fatal): %s", exc)

    def _load_schema(self) -> None:
        """Parse Avro schema on demand if open() was not called."""
        import fastavro

        if self._parsed_schema is None:
            self._parsed_schema = fastavro.parse_schema(json.loads(_SCHEMA_PATH.read_text()))

    def _serialize(self, record: dict) -> bytes:
        """Serialize shadow decision record to Avro bytes."""
        import fastavro

        self._load_schema()
        buf = io.BytesIO()
        fastavro.writer(buf, self._parsed_schema, [record])
        return buf.getvalue()

    def _build_record(
        self,
        fraud_decision: FraudDecision,
        shadow_result: EvaluationResult,
        account_id: str,
        rule_set_version: str,
        shadow_rule_set_version: str,
    ) -> dict:
        """Build the shadow decision record from production and shadow results."""
        production_decision = fraud_decision.decision
        production_score = fraud_decision.fraud_score
        shadow_determination = shadow_result.determination
        shadow_score = self._estimate_shadow_score(shadow_result)

        # Determine if decisions mismatch
        # Production: ALLOW = clean, FLAG/BLOCK = suspicious
        # Shadow: clean, suspicious
        prod_as_determination = "clean" if production_decision == "ALLOW" else "suspicious"
        decision_mismatch = prod_as_determination != shadow_determination

        return {
            "transaction_id": fraud_decision.transaction_id,
            "account_id": account_id,
            "production_decision": production_decision,
            "production_fraud_score": production_score,
            "production_rule_triggers": fraud_decision.rule_triggers,
            "shadow_determination": shadow_determination,
            "shadow_fraud_score": shadow_score,
            "shadow_rule_triggers": shadow_result.matched_rules,
            "model_version": fraud_decision.model_version,
            "rule_set_version": rule_set_version,
            "shadow_rule_set_version": shadow_rule_set_version,
            "decision_time_ms": fraud_decision.decision_time_ms,
            "score_delta": shadow_score - production_score,
            "decision_mismatch": decision_mismatch,
            "schema_version": "1",
        }

    def _estimate_shadow_score(self, shadow_result: EvaluationResult) -> float:
        """Estimate a fraud score from shadow rule results.

        Maps shadow determination and matched rules to a score in [0.0, 1.0].
        - clean: 0.0 - 0.3 range
        - suspicious: 0.5 - 1.0 range based on highest severity
        """
        if shadow_result.determination == "clean":
            return 0.1

        # Suspicious - map severity to score range
        severity_scores = {
            "low": 0.5,
            "medium": 0.65,
            "high": 0.8,
            "critical": 0.95,
        }
        return severity_scores.get(shadow_result.highest_severity or "medium", 0.65)

    def _on_delivery(self, err, msg, transaction_id: str) -> None:  # noqa: ARG002
        """Delivery report callback."""
        if err:
            logger.error(
                "Shadow decision delivery failed for txn=%s: %s",
                transaction_id,
                err,
            )

    def emit(
        self,
        fraud_decision: FraudDecision,
        shadow_result: EvaluationResult,
        account_id: str,
        rule_set_version: str = "unknown",
        shadow_rule_set_version: str = "unknown",
    ) -> None:
        """Produce a shadow decision record to the shadow decisions topic.

        Args:
            fraud_decision: The production fraud decision
            shadow_result: The shadow rule evaluation result
            account_id: The account ID for the transaction
            rule_set_version: Version of the active rule set
            shadow_rule_set_version: Version of the shadow rule set
        """
        if self._producer is None:
            raise RuntimeError("ShadowDecisionKafkaSink.open() must be called before emit()")

        record = self._build_record(
            fraud_decision,
            shadow_result,
            account_id,
            rule_set_version,
            shadow_rule_set_version,
        )
        payload = self._serialize(record)

        self._producer.produce(
            topic=self._config.shadow_decisions_topic,
            key=fraud_decision.transaction_id.encode(),
            value=payload,
            on_delivery=lambda err, msg: self._on_delivery(err, msg, fraud_decision.transaction_id),
        )
        self._producer.poll(0)

    def flush(self) -> None:
        """Flush pending messages."""
        if self._producer:
            self._producer.flush()

    def close(self) -> None:
        """Flush pending messages and close the producer."""
        self.flush()
        if self._producer is not None:
            self._producer.flush(timeout=10)
            self._producer = None
