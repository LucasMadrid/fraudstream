"""Sink for replay results.

Handles writing replay results to Kafka and/or Iceberg for
analysis and comparison.
"""

from __future__ import annotations

import json
import logging
import threading
from queue import Queue
from typing import Any

from pipelines.replay.models import ReplayResult

logger = logging.getLogger(__name__)


class ReplayResultSink:
    """Sink for replay results with Kafka and optional Iceberg output.

    Writes replay results to the configured Kafka topic and optionally
    to an Iceberg table for long-term storage and analysis.

    Uses an internal queue and background thread for non-blocking
    result emission.

    Example:
        sink = ReplayResultSink(
            kafka_brokers="localhost:9092",
            topic="txn.replay.results",
            iceberg_table="default.replay_results",
        )
        sink.send_result(replay_result)
        sink.close()
    """

    def __init__(
        self,
        kafka_brokers: str = "localhost:9092",
        topic: str = "txn.replay.results",
        iceberg_table: str | None = None,
        max_queue_size: int = 10000,
        flush_interval_sec: float = 5.0,
    ) -> None:
        """Initialize the replay result sink.

        Args:
            kafka_brokers: Kafka bootstrap servers
            topic: Kafka topic for replay results
            iceberg_table: Optional Iceberg table name for persistence
            max_queue_size: Maximum queue size before blocking
            flush_interval_sec: How often to flush buffered results
        """
        self.kafka_brokers = kafka_brokers
        self.topic = topic
        self.iceberg_table = iceberg_table
        self._queue: Queue[ReplayResult | None] = Queue(maxsize=max_queue_size)
        self._flush_interval = flush_interval_sec

        self._producer: Any = None
        self._flush_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._buffer: list[ReplayResult] = []
        self._lock = threading.Lock()

        self._init_producer()
        self._start_flush_thread()

    def _init_producer(self) -> None:
        """Initialize Kafka producer."""
        try:
            from confluent_kafka import Producer

            self._producer = Producer(
                {
                    "bootstrap.servers": self.kafka_brokers,
                    "client.id": "replay-result-sink",
                    "compression.type": "snappy",
                    "batch.size": 16384,
                    "linger.ms": 100,
                }
            )
            logger.debug("Kafka producer initialized for topic %s", self.topic)
        except ImportError:
            logger.warning("confluent-kafka not installed, Kafka output disabled")
            self._producer = None
        except Exception as e:
            logger.error("Failed to initialize Kafka producer: %s", e)
            self._producer = None

    def _start_flush_thread(self) -> None:
        """Start background thread for periodic flushing."""
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Background loop to flush results periodically."""

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._flush_interval)
            self._flush_buffer()

    def send_result(self, result: ReplayResult) -> None:
        """Send a replay result to the sink.

        Args:
            result: ReplayResult to send
        """
        # Add to queue for background processing
        try:
            self._queue.put(result, timeout=1.0)
        except Exception:
            # Queue full, flush directly
            self._send_to_kafka(result)

        # Also add to buffer for Iceberg flush
        with self._lock:
            self._buffer.append(result)

    def _process_queue(self) -> None:
        """Process items from the queue."""
        while True:
            try:
                result = self._queue.get(timeout=0.1)
                if result is None:
                    break
                self._send_to_kafka(result)
            except Exception:
                break

    def _send_to_kafka(self, result: ReplayResult) -> None:
        """Send result to Kafka topic.

        Args:
            result: ReplayResult to send
        """
        if self._producer is None:
            return

        try:
            message = self._serialize_result(result)
            key = result.replay_job_id.encode("utf-8")

            self._producer.produce(
                topic=self.topic,
                key=key,
                value=json.dumps(message).encode("utf-8"),
                callback=self._delivery_callback,
            )
        except Exception as e:
            logger.warning("Failed to send result to Kafka: %s", e)

    def _delivery_callback(self, err: Any, msg: Any) -> None:
        """Kafka delivery callback.

        Args:
            err: Error if delivery failed
            msg: Message metadata
        """
        if err is not None:
            logger.warning("Message delivery failed: %s", err)

    def _serialize_result(self, result: ReplayResult) -> dict[str, Any]:
        """Serialize ReplayResult to dictionary.

        Args:
            result: ReplayResult to serialize

        Returns:
            Dictionary representation
        """
        return {
            "replay_job_id": result.replay_job_id,
            "original_event_id": result.original_event_id,
            "original_timestamp": result.original_timestamp.isoformat(),
            "replay_timestamp": result.replay_timestamp.isoformat(),
            "original_decision": result.original_decision,
            "replay_decision": result.replay_decision,
            "original_score": result.original_score,
            "replay_score": result.replay_score,
            "triggered_rules_original": result.triggered_rules_original,
            "triggered_rules_replay": result.triggered_rules_replay,
            "score_delta": result.score_delta,
            "decision_changed": result.decision_changed,
            "processing_time_ms": result.processing_time_ms,
            "metadata": result.metadata,
        }

    def _flush_buffer(self) -> None:
        """Flush buffered results to Iceberg."""
        if self.iceberg_table is None:
            return

        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer.copy()
            self._buffer.clear()

        try:
            self._write_to_iceberg(batch)
        except Exception as e:
            logger.warning("Failed to write to Iceberg: %s", e)
            # Put back in buffer for retry
            with self._lock:
                self._buffer.extend(batch)

    def _write_to_iceberg(self, results: list[ReplayResult]) -> None:
        """Write results to Iceberg table.

        Args:
            results: List of ReplayResult to write
        """
        try:
            import pyarrow as pa
            from pyiceberg.catalog import load_catalog

            catalog = load_catalog("iceberg")
            table = catalog.load_table(self.iceberg_table)

            # Convert to PyArrow table
            data = [self._serialize_result(r) for r in results]

            # Build PyArrow arrays
            arrays = {
                "replay_job_id": pa.array([d["replay_job_id"] for d in data], type=pa.string()),
                "original_event_id": pa.array(
                    [d["original_event_id"] for d in data], type=pa.string()
                ),
                "original_timestamp": pa.array(
                    [d["original_timestamp"] for d in data], type=pa.string()
                ),
                "replay_timestamp": pa.array(
                    [d["replay_timestamp"] for d in data], type=pa.string()
                ),
                "original_decision": pa.array(
                    [d["original_decision"] for d in data], type=pa.string()
                ),
                "replay_decision": pa.array([d["replay_decision"] for d in data], type=pa.string()),
                "original_score": pa.array([d["original_score"] for d in data], type=pa.float64()),
                "replay_score": pa.array([d["replay_score"] for d in data], type=pa.float64()),
                "triggered_rules_original": pa.array(
                    [json.dumps(d["triggered_rules_original"]) for d in data], type=pa.string()
                ),
                "triggered_rules_replay": pa.array(
                    [json.dumps(d["triggered_rules_replay"]) for d in data], type=pa.string()
                ),
                "score_delta": pa.array([d["score_delta"] for d in data], type=pa.float64()),
                "decision_changed": pa.array(
                    [d["decision_changed"] for d in data], type=pa.bool_()
                ),
                "processing_time_ms": pa.array(
                    [d["processing_time_ms"] for d in data], type=pa.float64()
                ),
            }

            arrow_table = pa.table(arrays)
            table.append(arrow_table)

            logger.debug("Wrote %d results to Iceberg table %s", len(results), self.iceberg_table)

        except ImportError:
            logger.debug("PyIceberg not installed, skipping Iceberg write")
        except Exception as e:
            raise RuntimeError(f"Failed to write to Iceberg: {e}") from e

    def flush(self) -> None:
        """Flush all pending results."""
        self._process_queue()
        if self._producer:
            self._producer.flush()
        self._flush_buffer()

    def close(self) -> None:
        """Close the sink and cleanup resources."""
        logger.debug("Closing replay result sink")

        # Signal stop
        self._stop_event.set()

        # Flush remaining items
        self.flush()

        # Wait for flush thread
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=10.0)

        # Close producer
        if self._producer:
            try:
                self._producer.flush(timeout=10.0)
            except Exception as e:
                logger.warning("Error flushing Kafka producer: %s", e)
            finally:
                self._producer = None

    def get_stats(self) -> dict[str, Any]:
        """Get sink statistics.

        Returns:
            Dictionary with queue size, buffer size, etc.
        """
        with self._lock:
            buffer_size = len(self._buffer)

        return {
            "queue_size": self._queue.qsize(),
            "buffer_size": buffer_size,
            "topic": self.topic,
            "iceberg_table": self.iceberg_table,
        }
