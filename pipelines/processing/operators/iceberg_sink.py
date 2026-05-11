"""IcebergEnrichedSink — writes enriched transaction dicts to Iceberg table.

Receives enriched transaction dicts from EnrichedRecordAssembler and writes them
to iceberg.enriched_transactions via PyIceberg 0.7+ with:
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

from pipelines.processing.metrics import (
    iceberg_buffer_overflow_total,
    iceberg_catalog_unavailable_total,
    iceberg_flush_duration_seconds,
)
from pipelines.shared.arrow_utils import build_arrow_table
from pipelines.shared.iceberg_sink_base import _IcebergSinkBase

logger = logging.getLogger(__name__)

ICEBERG_BUFFER_MAX = int(os.environ.get("ICEBERG_BUFFER_MAX", "100"))

_ENRICHED_SCHEMA = pa.schema(
    [
        pa.field("transaction_id", pa.string(), nullable=False),
        pa.field("account_id", pa.string(), nullable=False),
        pa.field("merchant_id", pa.string(), nullable=False),
        pa.field("amount", pa.decimal128(18, 4), nullable=False),
        pa.field("currency", pa.string(), nullable=False),
        pa.field("event_time", pa.timestamp("us"), nullable=False),
        pa.field("enrichment_time", pa.timestamp("us"), nullable=False),
        pa.field("channel", pa.string(), nullable=False),
        pa.field("card_bin", pa.string(), nullable=False),
        pa.field("card_last4", pa.string(), nullable=False),
        pa.field("caller_ip_subnet", pa.string(), nullable=False),
        pa.field("api_key_id", pa.string(), nullable=False),
        pa.field("oauth_scope", pa.string(), nullable=False),
        pa.field("geo_lat", pa.float32(), nullable=True),
        pa.field("geo_lon", pa.float32(), nullable=True),
        pa.field("masking_lib_version", pa.string(), nullable=False),
        pa.field("vel_count_1m", pa.int32(), nullable=False),
        pa.field("vel_amount_1m", pa.decimal128(18, 4), nullable=False),
        pa.field("vel_count_5m", pa.int32(), nullable=False),
        pa.field("vel_amount_5m", pa.decimal128(18, 4), nullable=False),
        pa.field("vel_count_1h", pa.int32(), nullable=False),
        pa.field("vel_amount_1h", pa.decimal128(18, 4), nullable=False),
        pa.field("vel_count_24h", pa.int32(), nullable=False),
        pa.field("vel_amount_24h", pa.decimal128(18, 4), nullable=False),
        pa.field("geo_country", pa.string(), nullable=True),
        pa.field("geo_city", pa.string(), nullable=True),
        pa.field("geo_network_class", pa.string(), nullable=True),
        pa.field("geo_confidence", pa.float32(), nullable=True),
        pa.field("device_first_seen", pa.timestamp("us"), nullable=True),
        pa.field("device_txn_count", pa.int32(), nullable=True),
        pa.field("device_known_fraud", pa.bool_(), nullable=True),
        pa.field("prev_geo_country", pa.string(), nullable=True),
        pa.field("prev_txn_time_ms", pa.timestamp("us"), nullable=True),
        pa.field("enrichment_latency_ms", pa.int32(), nullable=False),
        pa.field("processor_version", pa.string(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
    ]
)

_TIMESTAMP_MS_FIELDS = frozenset(
    {"event_time", "enrichment_time", "device_first_seen", "prev_txn_time_ms"}
)
_INT_FIELDS = frozenset(
    {"vel_count_1m", "vel_count_5m", "vel_count_1h", "vel_count_24h", "device_txn_count",
     "enrichment_latency_ms"}
)
_FLOAT_FIELDS = frozenset({"geo_lat", "geo_lon", "geo_confidence"})


def _coerce_enriched(field_name: str, value: object) -> object:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return value
    if field_name in _TIMESTAMP_MS_FIELDS:
        return int(value) * 1000 if value is not None else None
    if field_name in _INT_FIELDS:
        return int(value) if value is not None else None
    if field_name in _FLOAT_FIELDS:
        return float(value) if value is not None else None
    if field_name == "device_known_fraud":
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    return str(value) if value is not None else None


class IcebergEnrichedSink(_IcebergSinkBase):
    """Writes enriched transactions to iceberg.enriched_transactions.

    Extends the base lifecycle with Feast feature materialization after each
    successful Iceberg flush (_after_flush_success hook).
    """

    def __init__(self) -> None:
        super().__init__(
            table_name="default.enriched_transactions",
            buffer_max=ICEBERG_BUFFER_MAX,
            dlq_event_name="iceberg_sink_dlq",
        )
        self._materializer = None

    def open(self, runtime_context=None) -> None:  # type: ignore[override]
        super().open(runtime_context)
        try:
            from feast import FeatureStore

            from pipelines.processing.feature_materializer import FeatureMaterializer

            feast_repo_path = os.path.realpath(
                os.environ.get("FEAST_REPO_PATH", "storage/feature_store")
            )
            store = FeatureStore(repo_path=feast_repo_path)
            self._materializer = FeatureMaterializer(store)
            logger.info("Loaded Feast FeatureStore from %s", feast_repo_path)
        except ImportError:
            logger.debug("Feast not installed; skipping feature materialization")
        except Exception as exc:
            logger.debug("Could not initialize Feast store: %s; skipping feature materialization", exc)

    def _on_buffer_overflow(self) -> None:
        iceberg_buffer_overflow_total.inc()

    def _on_catalog_unavailable(self) -> None:
        iceberg_catalog_unavailable_total.inc()

    def _observe_flush_duration(self, elapsed_seconds: float) -> None:
        iceberg_flush_duration_seconds.observe(elapsed_seconds)

    def _after_flush_success(
        self, _pa_table: pa.Table, records: list[dict], batch_size: int
    ) -> None:
        if self._materializer is not None:
            self._materializer.materialize(records)

    def _records_to_arrow_table(self, records: list[dict]) -> pa.Table:
        return build_arrow_table(_ENRICHED_SCHEMA, records, _coerce_enriched)

