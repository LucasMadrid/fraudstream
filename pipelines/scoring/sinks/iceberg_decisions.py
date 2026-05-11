"""IcebergDecisionsSink — writes FraudDecision records to Iceberg table.

Receives FraudDecision records from fraud scoring and writes them to
iceberg.fraud_decisions via PyIceberg 0.7+ with:
- In-buffer deduplication by transaction_id
- Configurable buffer size and flush interval (1 second)
- Circuit breaker protection (pybreaker)
- Dead-letter queue (DLQ) for failures
- Prometheus metrics for observability
"""

from __future__ import annotations

import logging
import os

import pyarrow as pa

from pipelines.scoring.metrics import (
    iceberg_decisions_buffer_overflow_total,
    iceberg_decisions_catalog_unavailable_total,
)
from pipelines.shared.arrow_utils import build_arrow_table
from pipelines.shared.iceberg_sink_base import _IcebergSinkBase

logger = logging.getLogger(__name__)

ICEBERG_DECISIONS_BUFFER_MAX = int(os.environ.get("ICEBERG_DECISIONS_BUFFER_MAX", "100"))

_DECISIONS_SCHEMA = pa.schema(
    [
        pa.field("transaction_id", pa.string(), nullable=False),
        pa.field("decision", pa.string(), nullable=False),
        pa.field("fraud_score", pa.float64(), nullable=False),
        pa.field(
            "rule_triggers",
            pa.list_(pa.field("item", pa.string(), nullable=False)),
            nullable=False,
        ),
        pa.field("model_version", pa.string(), nullable=False),
        pa.field("decision_time_ms", pa.timestamp("us"), nullable=False),
        pa.field("latency_ms", pa.float64(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
    ]
)


def _coerce_decision(field_name: str, value: object) -> object:
    if field_name == "fraud_score":
        return float(value) if value is not None else 0.0
    if field_name == "decision_time_ms":
        return int(value) * 1000 if value is not None else 0
    if field_name == "latency_ms":
        return float(value) if value is not None else 0.0
    if field_name == "rule_triggers":
        return value if isinstance(value, list) else []
    return str(value) if value is not None else ""


class IcebergDecisionsSink(_IcebergSinkBase):
    """Writes fraud decisions to iceberg.fraud_decisions."""

    def __init__(self) -> None:
        super().__init__(
            table_name="default.fraud_decisions",
            buffer_max=ICEBERG_DECISIONS_BUFFER_MAX,
            dlq_event_name="iceberg_decisions_sink_dlq",
        )

    def _on_buffer_overflow(self) -> None:
        iceberg_decisions_buffer_overflow_total.inc()

    def _on_catalog_unavailable(self) -> None:
        iceberg_decisions_catalog_unavailable_total.inc()

    def _records_to_arrow_table(self, records: list[dict]) -> pa.Table:
        return build_arrow_table(_DECISIONS_SCHEMA, records, _coerce_decision)
