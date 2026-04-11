"""Rule evaluator wiring — extends the enrichment DAG with fraud scoring."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def wire_rule_evaluator(enriched_stream, config, rules):  # pragma: no cover
    """Attach the rule evaluator and alert sinks to the enriched transaction stream.

    Applies stateless fraud rule evaluation to every enriched record.
    Suspicious records are forwarded to AlertKafkaSink and AlertPostgresSink.

    Called from pipelines.processing.job.build_job() after EnrichedRecordAssembler,
    before the Kafka enriched-record sink.

    Args:
        enriched_stream: Flink DataStream of enriched transaction dicts.
        config: ScoringConfig instance (rules_yaml_path, kafka/pg params).
        rules: List[RuleDefinition] loaded at job startup via RuleLoader.
    """
    from pyflink.datastream.functions import MapFunction

    from pipelines.scoring.rules.evaluator import RuleEvaluator
    from pipelines.scoring.types import FraudAlert

    evaluator = RuleEvaluator(rules)

    def _evaluate(txn: dict):
        result = evaluator.dispatch(txn)
        if result.determination == "suspicious":
            return FraudAlert(
                transaction_id=txn.get("transaction_id", ""),
                account_id=txn.get("account_id", ""),
                matched_rule_names=result.matched_rules,
                severity=result.highest_severity or "low",
                evaluation_timestamp=result.evaluation_timestamp,
            )
        return None

    alert_stream = enriched_stream.map(_evaluate, output_type=None).filter(
        lambda alert: alert is not None
    )

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

    # print() acts as a terminal sink so Flink does not prune the map node.
    alert_stream.map(_AlertSinkFunction(config)).print()
