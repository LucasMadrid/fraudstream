"""Rule evaluator wiring — extends the enrichment DAG with fraud scoring."""

from __future__ import annotations

import logging

from pipelines.scoring.types import FeatureServingProtocol, FraudDecision

logger = logging.getLogger(__name__)


class _FeatureEnrichmentFunction:
    """Flink MapFunction that fetches feature vectors per transaction.

    Holds a FeatureServingClient and merges returned FeatureVector fields
    into the transaction dict before passing downstream to the rule evaluator.
    """

    def __init__(
        self,
        feature_store_repo_path: str = "storage/feature_store",
        client: FeatureServingProtocol | None = None,
    ) -> None:
        self._repo_path = feature_store_repo_path
        self._client: FeatureServingProtocol | None = client

    def open(self, runtime_context=None) -> None:
        if self._client is None:
            from pipelines.scoring.clients.feature_serving import FeatureServingClient

            self._client = FeatureServingClient(feature_store_repo_path=self._repo_path)
            self._client.open()

    def map(self, txn: dict) -> dict:
        account_id = txn.get("account_id", "")
        transaction_id = txn.get("transaction_id", "")
        transaction_timestamp = int(txn.get("event_time", 0))

        fv = self._client.get_features(account_id, transaction_id, transaction_timestamp)

        enriched = dict(txn)
        enriched.update(fv.to_enrichment_dict())
        return enriched

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def _build_fraud_alert(txn: dict, result) -> "FraudAlert | None":
    """Return a FraudAlert for suspicious results, None for clean ones."""
    from pipelines.scoring.types import FraudAlert

    if result.determination != "suspicious":
        return None
    return FraudAlert(
        transaction_id=txn.get("transaction_id", ""),
        account_id=txn.get("account_id", ""),
        matched_rule_names=result.matched_rules,
        severity=result.highest_severity or "low",
        evaluation_timestamp=result.evaluation_timestamp,
    )


def _evaluate_transaction(evaluator, num_rules: int, txn: dict):
    """Evaluate a single enriched transaction dict and return (alert | None, decision).

    Pure Python — no Flink dependency. Callable directly from unit tests.
    """
    from pipelines.scoring.telemetry import fraud_rule_evaluation_span

    txn_id = txn.get("transaction_id", "")
    channel = txn.get("channel", "unknown")

    with fraud_rule_evaluation_span(txn_id, channel, num_rules) as span:
        result = evaluator.dispatch(txn)
        alert = _build_fraud_alert(txn, result)
        decision = _build_fraud_decision(txn, result)
        span.set_attribute("fraud.decision", decision.decision)

    return (alert, decision)


try:  # pragma: no cover
    from pyflink.datastream.functions import MapFunction

    class _FlinkFeatureEnrichmentFunction(MapFunction):
        def __init__(self, feature_repo: str) -> None:
            self._feature_repo = feature_repo

        def open(self, runtime_context):
            self._fn = _FeatureEnrichmentFunction(feature_store_repo_path=self._feature_repo)
            self._fn.open(runtime_context)

        def map(self, value):
            return self._fn.map(value)

        def close(self):
            self._fn.close()

    class _AlertSinkFunction(MapFunction):
        """Combined Kafka + PostgreSQL sink for fraud alerts.

        Implemented as a MapFunction (returning the input unchanged) because
        PyFlink 2.x removed Python subclassing of SinkFunction. The output
        stream is consumed by print() to prevent Flink from pruning the node.
        """

        def __init__(self, sink_config):
            self._config = sink_config
            self._kafka_sink = None
            self._pg_sink = None

        def open(self, runtime_context):
            from pipelines.scoring.sinks.alert_kafka import AlertKafkaSink
            from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

            self._kafka_sink = AlertKafkaSink(self._config)
            self._kafka_sink.open()
            self._pg_sink = AlertPostgresSink(self._config)
            try:
                self._pg_sink.open()
            except Exception as exc:
                logger.warning(
                    "PostgreSQL sink unavailable — alerts will be Kafka-only: %s", exc
                )
                self._pg_sink = None

        def map(self, value):
            # Kafka emit is the primary alert path (FR-010: back-pressure must
            # propagate upstream). Let failures raise — do not catch here.
            self._kafka_sink.emit(value)
            # PostgreSQL is best-effort durability — emit() never raises.
            if self._pg_sink is not None:
                self._pg_sink.emit(value)
            return value

        def close(self):
            if self._kafka_sink:
                self._kafka_sink.flush()
            if self._pg_sink:
                self._pg_sink.close()

    class _IcebergSinkFunction(MapFunction):
        """Wrapper for IcebergDecisionsSink using MapFunction pattern.

        The IcebergDecisionsSink handles errors internally and never raises,
        so we don't need try/catch here. We return the decision unchanged.
        """

        def __init__(self):
            self._sink = None

        def open(self, runtime_context):
            from pipelines.scoring.sinks.iceberg_decisions import IcebergDecisionsSink

            self._sink = IcebergDecisionsSink()
            self._sink.open(runtime_context)

        def map(self, value):
            if self._sink and value is not None:
                self._sink.invoke(value, None)
            return value

        def close(self):
            if self._sink:
                self._sink.close()

except ImportError:
    pass


def wire_rule_evaluator(enriched_stream, config, rules):  # pragma: no cover
    """Attach the rule evaluator and alert sinks to the enriched transaction stream.

    Applies stateless fraud rule evaluation to every enriched record.
    Suspicious records are forwarded to AlertKafkaSink and AlertPostgresSink.
    All evaluation outcomes (clean, flag, block) are written to Iceberg as FraudDecision.

    Called from pipelines.processing.job.build_job() after EnrichedRecordAssembler,
    before the Kafka enriched-record sink.

    Args:
        enriched_stream: Flink DataStream of enriched transaction dicts.
        config: ScoringConfig instance (rules_yaml_path, kafka/pg params).
        rules: List[RuleDefinition] loaded at job startup via RuleLoader.
    """
    from pipelines.scoring.rules.evaluator import RuleEvaluator

    feature_repo = getattr(config, "feature_store_repo_path", "storage/feature_store")
    enriched_stream = enriched_stream.map(
        _FlinkFeatureEnrichmentFunction(feature_repo), output_type=None
    )

    evaluator = RuleEvaluator(rules)
    eval_stream = enriched_stream.map(
        lambda txn: _evaluate_transaction(evaluator, len(rules), txn), output_type=None
    )

    alert_stream = eval_stream.map(lambda x: x[0], output_type=None).filter(
        lambda alert: alert is not None
    )
    decision_stream = eval_stream.map(lambda x: x[1], output_type=None)

    # print() acts as a terminal sink so Flink does not prune the map node.
    alert_stream.map(_AlertSinkFunction(config)).print()
    decision_stream.map(_IcebergSinkFunction()).print()


def _build_fraud_decision(txn: dict, result) -> FraudDecision:
    """Map EvaluationResult to FraudDecision record.

    Args:
        txn: Enriched transaction dict (contains transaction_id).
        result: EvaluationResult from rule evaluator.

    Returns:
        FraudDecision with mapped fields and heuristic scores for rule-only path.
    """
    txn_id = txn.get("transaction_id", "")
    is_clean = result.determination == "clean"

    # Map determination to decision
    if is_clean:
        decision = "ALLOW"
    else:
        # Suspicious: check severity to decide FLAG vs BLOCK
        severity = result.highest_severity or "low"
        decision = "BLOCK" if severity in ("high", "critical") else "FLAG"

    # Score: use heuristic if no ML model available
    # (ML integration would come from txn dict if present)
    if is_clean:
        fraud_score = 0.0
    elif decision == "FLAG":
        fraud_score = 0.3
    else:  # BLOCK
        fraud_score = 0.8

    # Rule triggers: empty list for clean, never null
    rule_triggers = result.matched_rules if not is_clean else []

    # Model version: rule-only path (no ML model integrated yet)
    model_version = "rule-only"

    return FraudDecision(
        transaction_id=txn_id,
        decision=decision,
        fraud_score=fraud_score,
        rule_triggers=rule_triggers,
        model_version=model_version,
        decision_time_ms=result.evaluation_timestamp,
        latency_ms=0.0,
        schema_version="1",
    )
