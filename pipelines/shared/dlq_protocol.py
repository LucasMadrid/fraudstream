"""DLQ sink protocol — shared seam for ingestion and processing DLQ implementations.

Both concrete implementations satisfy this protocol via structural subtyping:
  - pipelines.ingestion.shared.dlq_producer.DLQProducer  (JSON, txn.api.dlq)
  - pipelines.processing.shared.dlq_sink.ProcessingDLQSink  (Avro, txn.processing.dlq)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DLQSink(Protocol):
    """Minimal interface for routing records to a dead-letter queue.

    Callers type-annotate against DLQSink rather than either concrete class;
    tests inject a stub without touching Kafka.
    """

    def send(
        self,
        *,
        source_topic: str,
        original_payload: bytes,
        error_type: str,
        error_message: str,
    ) -> None: ...
