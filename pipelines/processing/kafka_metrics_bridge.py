"""Kafka consumer bridge — aggregates scoring metrics into the main process.

PyFlink Python operators execute in JVM-managed worker subprocesses. Prometheus
counters incremented there (rule_evaluations_total, rule_flags_total) are each
process-local and invisible to the main process's HTTP server.

This bridge runs two daemon threads in the main process:
  • alerts thread   — consumes txn.fraud.alerts (Avro) → rule_flags_total
  • enriched thread — consumes txn.enriched (JSON)     → rule_evaluations_total

Both threads subscribe at the *latest* offset so they count events produced
while the Flink job is running, not historical backlog.

Usage::

    from pipelines.processing import kafka_metrics_bridge
    kafka_metrics_bridge.start(
        brokers="localhost:9092",
        alerts_topic="txn.fraud.alerts",
        enriched_topic="txn.enriched",
        rules_yaml_path="rules/rules.yaml",
    )
"""

from __future__ import annotations

import io
import json
import logging
import threading
from pathlib import Path

import fastavro

from pipelines.shared.interfaces import get_metrics_provider, get_rule_metrics_publisher, RuleMetricsPublisher

logger = logging.getLogger(__name__)

_ALERT_SCHEMA_PATH = Path(__file__).parent.parent / "scoring" / "schemas" / "fraud-alert-v1.avsc"

_stop_event = threading.Event()


def _build_rule_family_map(rules_yaml_path: str) -> dict[str, str]:
    """Return {rule_id → family} for all enabled rules in rules.yaml."""
    try:
        import yaml  # PyYAML — available via scoring/processing extras

        with open(rules_yaml_path) as fh:
            rules = yaml.safe_load(fh)
        return {
            r["rule_id"]: r["family"]
            for r in (rules or [])
            if isinstance(r, dict) and r.get("enabled", True)
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Metrics bridge: could not load rules from %s: %s", rules_yaml_path, exc)
        return {}


def _process_alert_message(
    raw_bytes: bytes,
    parsed_schema: object,
    rule_family_map: dict[str, str],
) -> None:
    """Decode one Avro alert message and increment rule_flags_total counters.

    Raises on malformed bytes — callers should catch and log.
    Shadow rules (rule_id ending in ':shadow') are silently skipped.
    """
    record = next(fastavro.reader(io.BytesIO(raw_bytes), parsed_schema), None)
    if record is None:
        raise ValueError("empty Avro payload — no records")
    # Avro enum comes back as a string with the symbol value (lowercase).
    severity = record.get("severity", "low")
    if not isinstance(severity, str):
        severity = str(severity)
    severity = severity.lower()
    
    # Use interface to publish metrics (Constitution III: Explicit Contracts)
    rule_metrics = get_rule_metrics_publisher()
    
    for rule_id in record.get("matched_rule_names", []):
        if rule_id.endswith(":shadow"):
            continue
        family = rule_family_map.get(rule_id, "unknown")
        rule_metrics.record_rule_flag(rule_id=rule_id, rule_family=family, severity=severity)


def _alerts_consumer_thread(
    brokers: str,
    topic: str,
    rule_family_map: dict[str, str],
) -> None:
    """Consume txn.fraud.alerts and increment rule_flags_total in the main process."""
    try:
        from confluent_kafka import Consumer
    except ImportError as exc:
        logger.warning("Metrics bridge (alerts): missing dependency, skipping: %s", exc)
        return

    parsed_schema = fastavro.parse_schema(json.loads(_ALERT_SCHEMA_PATH.read_text()))

    consumer = Consumer(
        {
            "bootstrap.servers": brokers,
            "group.id": "flink-metrics-bridge-alerts",
            "auto.offset.reset": "latest",
            "enable.auto.commit": "true",
        }
    )
    consumer.subscribe([topic])
    logger.info("Metrics bridge (alerts): subscribed to %s", topic)

    try:
        while not _stop_event.is_set():
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                logger.debug("Metrics bridge (alerts) kafka error: %s", msg.error())
                continue
            try:
                _process_alert_message(msg.value(), parsed_schema, rule_family_map)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Metrics bridge (alerts): could not decode message: %s", exc)
    finally:
        consumer.close()
        logger.info("Metrics bridge (alerts): consumer closed")


def _enriched_consumer_thread(
    brokers: str,
    topic: str,
    rule_family_map: dict[str, str],
) -> None:
    """Consume txn.enriched and increment rule_evaluations_total in the main process.

    Every enriched record triggers evaluation of all enabled rules, so each
    message results in one increment per rule in the rule family map.
    """
    if not rule_family_map:
        logger.warning("Metrics bridge (enriched): no rules loaded — skipping thread")
        return

    try:
        from confluent_kafka import Consumer
    except ImportError as exc:
        logger.warning("Metrics bridge (enriched): missing dependency, skipping: %s", exc)
        return

    consumer = Consumer(
        {
            "bootstrap.servers": brokers,
            "group.id": "flink-metrics-bridge-enriched",
            "auto.offset.reset": "latest",
            "enable.auto.commit": "true",
        }
    )
    consumer.subscribe([topic])
    logger.info("Metrics bridge (enriched): subscribed to %s", topic)

    rule_items = list(rule_family_map.items())  # snapshot for tight inner loop
    
    # Use interface to publish metrics (Constitution III: Explicit Contracts)
    rule_metrics = get_rule_metrics_publisher()

    try:
        while not _stop_event.is_set():
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            # Each enriched record represents one evaluation pass over all rules.
            for rule_id, family in rule_items:
                rule_metrics.record_rule_evaluation(rule_id=rule_id, rule_family=family)
    finally:
        consumer.close()
        logger.info("Metrics bridge (enriched): consumer closed")


def start(
    brokers: str,
    alerts_topic: str,
    enriched_topic: str,
    rules_yaml_path: str,
) -> None:
    """Start the metrics bridge daemon threads.

    Safe to call multiple times — subsequent calls are no-ops if threads are
    already running (stop_event is clear).  Designed to be called from the
    main process after the Prometheus HTTP server is up.
    """
    rule_family_map = _build_rule_family_map(rules_yaml_path)

    _stop_event.clear()

    threading.Thread(
        target=_alerts_consumer_thread,
        args=(brokers, alerts_topic, rule_family_map),
        daemon=True,
        name="metrics-bridge-alerts",
    ).start()

    threading.Thread(
        target=_enriched_consumer_thread,
        args=(brokers, enriched_topic, rule_family_map),
        daemon=True,
        name="metrics-bridge-enriched",
    ).start()

    logger.info(
        "Kafka metrics bridge started (alerts=%s, enriched=%s, rules=%s)",
        alerts_topic,
        enriched_topic,
        rules_yaml_path,
    )


def stop() -> None:
    """Signal bridge threads to stop gracefully (next poll timeout cycle)."""
    _stop_event.set()
