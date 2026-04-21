from __future__ import annotations

import io
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import fastavro
from confluent_kafka import Consumer, KafkaException, TopicPartition

from analytics.consumers.metrics import (
    analytics_consumer_lag,
    analytics_consumer_restarts_total,
    analytics_events_consumed_total,
)

logger = logging.getLogger(__name__)

_SEVERITY_TO_DECISION = {
    "critical": "BLOCK",
    "high": "BLOCK",
    "medium": "FLAG",
    "low": "ALLOW",
}

CONSUMER_GROUP = "analytics.dashboard"
TOPIC = "txn.fraud.alerts"
QUEUE_MAX = 500
POLL_TIMEOUT_S = 1.0
LAG_REFRESH_S = 5.0


@dataclass
class FraudAlertDisplay:
    transaction_id: str
    account_id: str
    rule_triggers: list[str]
    severity: str
    decision: str
    evaluation_timestamp: datetime
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    merchant_id: str = ""
    amount: float = 0.0
    currency: str = ""
    channel: str = ""
    fraud_score: float = 0.0
    model_version: str = ""


def _deserialize(raw_bytes: bytes) -> FraudAlertDisplay:
    records = list(fastavro.reader(io.BytesIO(raw_bytes)))
    if not records:
        raise ValueError("Empty Avro container — no records")
    rec = records[0]
    eval_ts = rec["evaluation_timestamp"]
    if isinstance(eval_ts, int):
        eval_ts = datetime.fromtimestamp(eval_ts / 1000.0, tz=UTC)
    elif eval_ts.tzinfo is None:
        eval_ts = eval_ts.replace(tzinfo=UTC)
    severity: str = rec["severity"]
    return FraudAlertDisplay(
        transaction_id=rec["transaction_id"],
        account_id=rec["account_id"],
        rule_triggers=list(rec["matched_rule_names"]),
        severity=severity,
        decision=_SEVERITY_TO_DECISION.get(severity, "UNKNOWN"),
        evaluation_timestamp=eval_ts,
    )


class AnalyticsKafkaConsumer:
    """
    Daemon consumer that drains txn.fraud.alerts into an in-memory queue.

    Owned by a single Streamlit session or the Home page initializer.
    Reconnects automatically on KafkaException and increments the restart counter.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        queue_maxsize: int = QUEUE_MAX,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self.queue: queue.Queue[FraudAlertDisplay] = queue.Queue(
            maxsize=queue_maxsize
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lag: int = 0
        self._lag_lock = threading.Lock()
        self._last_lag_refresh: float = 0.0

    # ── public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="analytics-consumer"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def consumer_lag(self) -> int:
        with self._lag_lock:
            return self._lag

    # ── internal loop ───────────────────────────────────────────────────────

    def _build_consumer(self) -> Consumer:
        return Consumer(
            {
                "bootstrap.servers": self._bootstrap,
                "group.id": CONSUMER_GROUP,
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
                "session.timeout.ms": 10_000,
            }
        )

    def _refresh_lag(self, consumer: Consumer) -> None:
        now = time.monotonic()
        if now - self._last_lag_refresh < LAG_REFRESH_S:
            return
        self._last_lag_refresh = now
        try:
            meta = consumer.list_topics(TOPIC, timeout=2.0)
            if TOPIC not in meta.topics:
                return
            partitions = [
                TopicPartition(TOPIC, p)
                for p in meta.topics[TOPIC].partitions
            ]
            lo_hi = [
                consumer.get_watermark_offsets(tp, timeout=2.0)
                for tp in partitions
            ]
            committed = consumer.committed(partitions, timeout=2.0)
            total_lag = 0
            for i, _ in enumerate(partitions):
                _, high = lo_hi[i]
                committed_off = committed[i].offset
                if committed_off < 0:
                    committed_off = lo_hi[i][0]
                total_lag += max(0, high - committed_off)
            with self._lag_lock:
                self._lag = total_lag
            analytics_consumer_lag.labels(
                consumer_group=CONSUMER_GROUP, topic=TOPIC
            ).set(total_lag)
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop_event.is_set():
            consumer: Consumer | None = None
            try:
                consumer = self._build_consumer()
                consumer.subscribe([TOPIC])
                logger.info(
                    "AnalyticsKafkaConsumer subscribed to %s", TOPIC
                )
                while not self._stop_event.is_set():
                    msg = consumer.poll(POLL_TIMEOUT_S)
                    if msg is None:
                        self._refresh_lag(consumer)
                        continue
                    if msg.error():
                        raise KafkaException(msg.error())
                    try:
                        alert = _deserialize(msg.value())
                        try:
                            self.queue.put_nowait(alert)
                        except queue.Full:
                            try:
                                self.queue.get_nowait()
                            except queue.Empty:
                                pass
                            self.queue.put_nowait(alert)
                        analytics_events_consumed_total.labels(
                            topic=TOPIC
                        ).inc()
                    except Exception as de:
                        logger.warning("Deserialization error: %s", de)
                    self._refresh_lag(consumer)
            except KafkaException as ke:
                logger.error(
                    "KafkaException in analytics consumer: %s — restarting",
                    ke,
                )
                analytics_consumer_restarts_total.inc()
                time.sleep(2.0)
            except Exception as exc:
                logger.error(
                    "Unexpected error in analytics consumer: %s — restarting",
                    exc,
                )
                analytics_consumer_restarts_total.inc()
                time.sleep(2.0)
            finally:
                if consumer is not None:
                    try:
                        consumer.close()
                    except Exception:
                        pass
