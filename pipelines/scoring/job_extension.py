"""Rule evaluator wiring — extends the enrichment DAG with fraud scoring."""

from __future__ import annotations

import logging

from pipelines.scoring.types import FraudDecision

logger = logging.getLogger(__name__)


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
    from pyflink.datastream.functions import MapFunction

    from pipelines.scoring.rules.evaluator import RuleEvaluator
    from pipelines.scoring.sinks.iceberg_decisions import IcebergDecisionsSink
    from pipelines.scoring.types import FraudAlert

    evaluator = RuleEvaluator(rules)

    def _evaluate(txn: dict):
        """Evaluate transaction and return tuple of (optional alert, decision)."""
        result = evaluator.dispatch(txn)

        # Create FraudAlert only for suspicious transactions
        alert = None
        if result.determination == "suspicious":
            alert = FraudAlert(
                transaction_id=txn.get("transaction_id", ""),
                account_id=txn.get("account_id", ""),
                matched_rule_names=result.matched_rules,
                severity=result.highest_severity or "low",
                evaluation_timestamp=result.evaluation_timestamp,
            )

        # Create FraudDecision for ALL transactions
        decision = _build_fraud_decision(txn, result)

        return (alert, decision)

    # Evaluate every transaction and split into alerts and decisions
    eval_stream = enriched_stream.map(_evaluate, output_type=None)

    # Extract alerts (may be None) and filter
    alert_stream = eval_stream.map(lambda x: x[0], output_type=None).filter(
        lambda alert: alert is not None
    )

    # Extract decisions and wire to Iceberg sink
    decision_stream = eval_stream.map(lambda x: x[1], output_type=None)

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
            self._pg_sink.open()

        def map(self, value):
            # Kafka emit is the primary alert path (FR-010: back-pressure must
            # propagate upstream). Let failures raise — do not catch here.
            self._kafka_sink.emit(value)
            # PostgreSQL is best-effort durability; a transient DB failure must
            # not stall the pipeline or drop the Kafka alert already emitted.
            try:
                self._pg_sink.persist(value)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "PostgreSQL persist failed for txn=%s — alert emitted to Kafka only: %s",
                    value.transaction_id,
                    exc,
                )
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
            self._sink = IcebergDecisionsSink()
            self._sink.open(runtime_context)

        def map(self, value):
            # IcebergDecisionsSink.invoke catches all exceptions internally
            if self._sink and value is not None:
                self._sink.invoke(value, None)
            return value

        def close(self):
            # IcebergDecisionsSink does not expose close, but we follow
            # the pattern for consistency
            pass

    # print() acts as a terminal sink so Flink does not prune the map node.
    alert_stream.map(_AlertSinkFunction(config)).print()

    # Wire Iceberg decisions sink (additive, does not interfere with alerts)
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
