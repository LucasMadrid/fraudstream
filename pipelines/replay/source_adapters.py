"""Source adapters for replay job event ingestion.

Provides adapters to read events from Iceberg, Kafka, and DLQ sources
for backtesting and recovery scenarios.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pipelines.replay.models import (
    DLQSourceConfig,
    IcebergSourceConfig,
    KafkaSourceConfig,
)

logger = logging.getLogger(__name__)


class ReplaySourceAdapter(ABC):
    """Abstract base class for replay source adapters."""

    @abstractmethod
    def get_event_count(self) -> int:
        """Return estimated total number of events available."""
        pass

    @abstractmethod
    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Iterate over events from the source.

        Yields:
            Dict containing event data with at minimum:
            - event_id: Unique event identifier
            - timestamp: Event timestamp
            - payload: Full event payload
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close any open connections and cleanup resources."""
        pass

    def __enter__(self) -> ReplaySourceAdapter:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit."""
        self.close()


class IcebergSourceAdapter(ReplaySourceAdapter):
    """Adapter for reading events from Iceberg tables.

    Uses PyIceberg to query historical data with point-in-time semantics.
    Supports time-travel queries via snapshot IDs.
    """

    def __init__(self, config: IcebergSourceConfig) -> None:
        """Initialize Iceberg source adapter.

        Args:
            config: Iceberg source configuration
        """
        self.config = config
        self._table: Any = None
        self._catalog: Any = None

    def _ensure_table(self) -> Any:
        """Lazy load the Iceberg table."""
        if self._table is None:
            try:
                from pyiceberg.catalog import load_catalog

                self._catalog = load_catalog("iceberg")
                self._table = self._catalog.load_table(self.config.table_name)
            except ImportError as e:
                raise RuntimeError("PyIceberg not installed") from e
            except Exception as e:
                raise RuntimeError(f"Failed to load Iceberg table: {e}") from e
        return self._table

    def get_event_count(self) -> int:
        """Estimate event count using table metadata.

        Returns:
            Approximate number of events in the query range
        """
        try:
            table = self._ensure_table()
            # Get snapshot for time range
            if self.config.snapshot_id:
                snapshot = next(
                    (s for s in table.snapshots() if s.snapshot_id == self.config.snapshot_id),
                    None,
                )
                if snapshot:
                    return snapshot.summary.get("total-records", 0)

            # Count via query
            df = self._query_table()
            return len(df)
        except Exception as e:
            logger.warning("Could not get event count: %s", e)
            return 0

    def _query_table(self) -> Any:
        """Execute query against Iceberg table.

        Returns:
            PyArrow table or pandas DataFrame with query results
        """
        table = self._ensure_table()

        # Build time filter
        start_dt = datetime.fromisoformat(self.config.start_timestamp.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(self.config.end_timestamp.replace("Z", "+00:00"))

        # Query using PyIceberg scan
        scan = table.scan()

        # Apply snapshot if specified
        if self.config.snapshot_id:
            scan = scan.use_snapshot(self.config.snapshot_id)

        # Apply time filter if timestamp column exists
        # Assume standard timestamp column names
        timestamp_cols = ["timestamp", "event_time", "created_at", "ts"]
        available_cols = [f.name for f in table.schema().fields]

        timestamp_col = None
        for col in timestamp_cols:
            if col in available_cols:
                timestamp_col = col
                break

        if timestamp_col:
            # Build filter expression
            import pyarrow.compute as pc

            start_scalar = pc.scalar(start_dt)
            end_scalar = pc.scalar(end_dt)

            filter_expr = (pc.field(timestamp_col) >= start_scalar) & (
                pc.field(timestamp_col) < end_scalar
            )
            scan = scan.filter(filter_expr)

        # Apply custom filter if provided
        if self.config.filter_expression:
            # Parse and apply DuckDB-style filter
            logger.debug("Applying custom filter: %s", self.config.filter_expression)

        return scan.to_arrow()

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Iterate over events from Iceberg table.

        Yields:
            Event dictionaries with event_id, timestamp, and payload
        """
        try:
            arrow_table = self._query_table()
            df = arrow_table.to_pandas()

            for _, row in df.iterrows():
                event = self._row_to_event(row)
                if event:
                    yield event
        except Exception as e:
            logger.error("Error iterating Iceberg events: %s", e)
            raise

    def _row_to_event(self, row: Any) -> dict[str, Any] | None:
        """Convert a DataFrame row to event dictionary.

        Args:
            row: pandas Series representing a row

        Returns:
            Event dictionary or None if invalid
        """
        try:
            # Try to extract common fields
            event_id = str(row.get("transaction_id", row.get("id", row.get("event_id", ""))))
            timestamp = row.get("timestamp", row.get("event_time", row.get("created_at")))

            # Convert timestamp to string if needed
            if isinstance(timestamp, datetime):
                timestamp = timestamp.isoformat()

            # Build payload from all columns
            payload = row.to_dict()

            return {
                "event_id": event_id,
                "timestamp": timestamp,
                "payload": payload,
                "source": "iceberg",
                "table": self.config.table_name,
            }
        except Exception as e:
            logger.warning("Failed to convert row to event: %s", e)
            return None

    def close(self) -> None:
        """Cleanup resources."""
        self._table = None
        self._catalog = None


class KafkaSourceAdapter(ReplaySourceAdapter):
    """Adapter for reading events from Kafka topic with offset range.

        Reads a specific range of offsets from a Kafka partition for
    deterministic replay of events.
    """

    def __init__(self, config: KafkaSourceConfig, brokers: str = "localhost:9092") -> None:
        """Initialize Kafka source adapter.

        Args:
            config: Kafka source configuration
            brokers: Kafka bootstrap servers
        """
        self.config = config
        self.brokers = brokers
        self._consumer: Any = None

    def _ensure_consumer(self) -> Any:
        """Lazy initialize Kafka consumer."""
        if self._consumer is None:
            try:
                from confluent_kafka import Consumer, TopicPartition

                group_id = self.config.consumer_group or f"replay-{self.config.topic}"

                self._consumer = Consumer(
                    {
                        "bootstrap.servers": self.brokers,
                        "group.id": group_id,
                        "auto.offset.reset": "earliest",
                        "enable.auto.commit": False,
                    }
                )

                # Assign specific partition and starting offset
                tp = TopicPartition(
                    self.config.topic,
                    self.config.partition,
                    self.config.start_offset,
                )
                self._consumer.assign([tp])

            except ImportError as e:
                raise RuntimeError("confluent-kafka not installed") from e
            except Exception as e:
                raise RuntimeError(f"Failed to create Kafka consumer: {e}") from e

        return self._consumer

    def get_event_count(self) -> int:
        """Calculate event count from offset range.

        Returns:
            Number of events between start and end offsets
        """
        end_offset = self.config.end_offset
        if end_offset < 0:
            # Get latest offset
            try:
                consumer = self._ensure_consumer()
                partitions = consumer.assignment()
                if partitions:
                    watermarks = consumer.get_watermark_offsets(partitions[0])
                    if watermarks:
                        end_offset = watermarks[1]  # high watermark
            except Exception as e:
                logger.warning("Could not get latest offset: %s", e)
                return 0

        return max(0, end_offset - self.config.start_offset)

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Iterate over Kafka events in offset range.

        Yields:
            Event dictionaries with offset, key, value, and metadata
        """
        try:
            consumer = self._ensure_consumer()
            end_offset = self.config.end_offset

            # Get latest offset if not specified
            if end_offset < 0:
                partitions = consumer.assignment()
                if partitions:
                    watermarks = consumer.get_watermark_offsets(partitions[0])
                    if watermarks:
                        end_offset = watermarks[1]

            current_offset = self.config.start_offset

            while current_offset < end_offset:
                msg = consumer.poll(timeout=5.0)

                if msg is None:
                    logger.warning("No message received, breaking")
                    break

                if msg.error():
                    logger.error("Kafka error: %s", msg.error())
                    break

                current_offset = msg.offset()

                if current_offset >= end_offset:
                    break

                # Parse message value
                try:
                    value = json.loads(msg.value().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    value = {"raw": msg.value().decode("utf-8", errors="replace")}

                event = {
                    "event_id": str(msg.key().decode("utf-8", errors="replace"))
                    if msg.key()
                    else f"{self.config.topic}-{msg.partition()}-{msg.offset()}",
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "payload": value,
                    "source": "kafka",
                    "topic": self.config.topic,
                    "partition": msg.partition(),
                    "offset": msg.offset(),
                    "key": msg.key().decode("utf-8", errors="replace") if msg.key() else None,
                }

                yield event

        except Exception as e:
            logger.error("Error iterating Kafka events: %s", e)
            raise

    def close(self) -> None:
        """Close Kafka consumer."""
        if self._consumer:
            try:
                self._consumer.close()
            except Exception as e:
                logger.warning("Error closing Kafka consumer: %s", e)
            finally:
                self._consumer = None


class DLQSourceAdapter(ReplaySourceAdapter):
    """Adapter for reading events from Dead Letter Queue for recovery testing.

    Useful for replaying failed messages to test fixes and verify
    recovery procedures.
    """

    def __init__(self, config: DLQSourceConfig, brokers: str = "localhost:9092") -> None:
        """Initialize DLQ source adapter.

        Args:
            config: DLQ source configuration
            brokers: Kafka bootstrap servers
        """
        self.config = config
        self.brokers = brokers
        self._consumer: Any = None
        self._events: list[dict[str, Any]] = []

    def _ensure_consumer(self) -> Any:
        """Lazy initialize Kafka consumer for DLQ."""
        if self._consumer is None:
            try:
                from confluent_kafka import Consumer

                self._consumer = Consumer(
                    {
                        "bootstrap.servers": self.brokers,
                        "group.id": f"replay-dlq-{self.config.source_topic}",
                        "auto.offset.reset": "earliest",
                        "enable.auto.commit": False,
                    }
                )
                self._consumer.subscribe([self.config.dlq_topic])

            except ImportError as e:
                raise RuntimeError("confluent-kafka not installed") from e
            except Exception as e:
                raise RuntimeError(f"Failed to create DLQ consumer: {e}") from e

        return self._consumer

    def get_event_count(self) -> int:
        """Get estimated DLQ message count.

        Returns:
            Number of messages in DLQ (capped by max_messages config)
        """
        return min(self.config.max_messages, self._count_dlq_messages())

    def _count_dlq_messages(self) -> int:
        """Count messages in DLQ topic."""
        try:
            consumer = self._ensure_consumer()
            # Poll briefly to get assignment
            consumer.poll(timeout=1.0)

            partitions = consumer.assignment()
            total = 0
            for partition in partitions:
                watermarks = consumer.get_watermark_offsets(partition)
                if watermarks:
                    total += watermarks[1] - watermarks[0]

            return total
        except Exception as e:
            logger.warning("Could not count DLQ messages: %s", e)
            return 0

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Iterate over DLQ events.

        Filters by source topic and time range if configured.

        Yields:
            Event dictionaries with original event data and DLQ metadata
        """
        try:
            consumer = self._ensure_consumer()
            count = 0

            # Parse time filters
            start_time = None
            end_time = None
            if self.config.start_time:
                start_time = datetime.fromisoformat(self.config.start_time.replace("Z", "+00:00"))
            if self.config.end_time:
                end_time = datetime.fromisoformat(self.config.end_time.replace("Z", "+00:00"))

            while count < self.config.max_messages:
                msg = consumer.poll(timeout=2.0)

                if msg is None:
                    break

                if msg.error():
                    logger.error("DLQ Kafka error: %s", msg.error())
                    continue

                try:
                    value = json.loads(msg.value().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    value = {"raw": msg.value().decode("utf-8", errors="replace")}

                # Check if this message is for our source topic
                msg_source_topic = value.get("source_topic", value.get("original_topic", ""))
                if msg_source_topic and msg_source_topic != self.config.source_topic:
                    continue

                # Check time range if specified
                if start_time or end_time:
                    msg_time = value.get("timestamp", value.get("error_time"))
                    if msg_time:
                        try:
                            msg_dt = datetime.fromisoformat(str(msg_time).replace("Z", "+00:00"))
                            if start_time and msg_dt < start_time:
                                continue
                            if end_time and msg_dt > end_time:
                                continue
                        except (ValueError, TypeError):
                            pass

                event = {
                    "event_id": value.get("original_id", f"dlq-{msg.offset()}"),
                    "timestamp": value.get(
                        "original_timestamp", datetime.now().astimezone().isoformat()
                    ),
                    "payload": value.get("original_payload", value),
                    "source": "dlq",
                    "dlq_topic": self.config.dlq_topic,
                    "source_topic": self.config.source_topic,
                    "error_reason": value.get("error_reason", value.get("error", "unknown")),
                    "retry_count": value.get("retry_count", 0),
                    "dlq_timestamp": value.get("timestamp"),
                    "offset": msg.offset(),
                }

                yield event
                count += 1

        except Exception as e:
            logger.error("Error iterating DLQ events: %s", e)
            raise

    def close(self) -> None:
        """Close DLQ consumer."""
        if self._consumer:
            try:
                self._consumer.close()
            except Exception as e:
                logger.warning("Error closing DLQ consumer: %s", e)
            finally:
                self._consumer = None


def create_source_adapter(
    source_type: str,
    config: Any,
    **kwargs: Any,
) -> ReplaySourceAdapter:
    """Factory function to create appropriate source adapter.

    Args:
        source_type: Type of source ('iceberg', 'kafka', 'dlq')
        config: Source-specific configuration object
        **kwargs: Additional arguments (e.g., brokers)

    Returns:
        Configured ReplaySourceAdapter instance

    Raises:
        ValueError: If source_type is not recognized
    """
    brokers = kwargs.get("brokers", "localhost:9092")

    if source_type == "iceberg":
        if not isinstance(config, IcebergSourceConfig):
            raise TypeError("config must be IcebergSourceConfig for iceberg source")
        return IcebergSourceAdapter(config)
    elif source_type == "kafka":
        if not isinstance(config, KafkaSourceConfig):
            raise TypeError("config must be KafkaSourceConfig for kafka source")
        return KafkaSourceAdapter(config, brokers=brokers)
    elif source_type == "dlq":
        if not isinstance(config, DLQSourceConfig):
            raise TypeError("config must be DLQSourceConfig for dlq source")
        return DLQSourceAdapter(config, brokers=brokers)
    else:
        raise ValueError(f"Unknown source type: {source_type}")
