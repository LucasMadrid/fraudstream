"""Feast feature materializer — pushes enriched record batches to the online store.

Owns the per-group Arrow construction and push logic so IcebergEnrichedSink stays
focused on Iceberg lifecycle. Injectable via __init__, making the push logic
independently testable without the full Iceberg sink lifecycle.
"""

from __future__ import annotations

import logging
import time

import pyarrow as pa

from pipelines.shared.feature_schema import FEATURE_GROUPS, ColumnSpec

logger = logging.getLogger(__name__)


class FeatureMaterializer:
    """Pushes a batch of enriched records to Feast online store feature groups.

    Accepts any object that implements the Feast FeatureStore.push() interface,
    so tests can inject a stub without spinning up a real Feast repo.

    Per-group push errors are logged and not re-raised — a geo push failure
    must not block velocity or device updates.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._last_push_ms: float = time.time() * 1000

    def materialize(self, records: list[dict]) -> None:
        """Push velocity, geo, and device feature groups for a batch of enriched records."""
        if not records:
            return

        event_timestamps = [int(r.get("event_time", 0)) for r in records]

        for source_name, columns in FEATURE_GROUPS:
            self._push_feature_group(source_name, columns, records, event_timestamps)

        self._update_staleness_gauge()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _push_feature_group(
        self,
        source_name: str,
        columns: list[ColumnSpec],
        records: list[dict],
        event_timestamps: list[int],
    ) -> None:
        try:
            from feast.data_source import PushMode

            data: dict[str, pa.Array] = {
                "account_id": pa.array([r.get("account_id") for r in records]),
                "transaction_id": pa.array([r.get("transaction_id") for r in records]),
                "event_timestamp": pa.array(event_timestamps, type=pa.timestamp("ms")),
            }
            for col in columns:
                data[col.field] = pa.array([col.extract(r) for r in records], type=col.arrow_type)
            self._store.push(source_name, pa.table(data).to_pandas(), to=PushMode.ONLINE)
        except Exception as exc:
            logger.error("Failed to push %s to Feast: %s", source_name, exc, exc_info=True)

    def _update_staleness_gauge(self) -> None:
        try:
            from pipelines.processing.metrics import feature_materialization_lag_ms

            now_ms = time.time() * 1000
            feature_materialization_lag_ms.set(now_ms - self._last_push_ms)
            self._last_push_ms = now_ms
        except Exception as exc:
            logger.debug("feature_materialization_lag_ms update failed: %s", exc)
