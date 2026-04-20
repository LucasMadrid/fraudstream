"""Feast online store client with 3 ms hard timeout and zero-value fallback."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import replace

from pipelines.scoring.metrics import (
    feature_store_fallback_total,
    feature_store_miss_total,
    feature_store_retrieval_seconds,
)
from pipelines.scoring.types import (
    ZERO_FEATURE_VECTOR,
    FallbackReason,
    FeatureVector,
)

logger = logging.getLogger("feature_serving")

_FEATURE_REFS = [
    "velocity_features:vel_count_1m",
    "velocity_features:vel_amount_1m",
    "velocity_features:vel_count_5m",
    "velocity_features:vel_amount_5m",
    "velocity_features:vel_count_1h",
    "velocity_features:vel_amount_1h",
    "velocity_features:vel_count_24h",
    "velocity_features:vel_amount_24h",
    "geo_features:geo_country",
    "geo_features:geo_city",
    "geo_features:geo_network_class",
    "geo_features:geo_confidence",
    "device_features:device_first_seen",
    "device_features:device_txn_count",
    "device_features:device_known_fraud",
    "device_features:prev_geo_country",
    "device_features:prev_txn_time_ms",
]


def _zero_for(account_id: str) -> FeatureVector:
    return replace(ZERO_FEATURE_VECTOR, account_id=account_id)


class FeatureServingClient:
    def __init__(
        self,
        feature_store_repo_path: str = "storage/feature_store",
        timeout_seconds: float = 0.003,
        executor_workers: int = 1,
    ) -> None:
        self._repo_path = feature_store_repo_path
        self._timeout_seconds = timeout_seconds
        self._executor_workers = executor_workers
        self._store = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def open(self) -> None:
        import feast

        self._store = feast.FeatureStore(repo_path=self._repo_path)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._executor_workers
        )

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)

    def _fetch_from_store(self, account_id: str) -> dict:
        response = self._store.get_online_features(
            features=_FEATURE_REFS,
            entity_rows=[{"account_id": account_id}],
        )
        return response.to_dict()

    def get_features(
        self,
        account_id: str,
        transaction_id: str,
        transaction_timestamp: int,
    ) -> FeatureVector:
        if self._executor is None:
            logger.warning(
                "feature_serving_client_not_opened",
                extra={
                    "event": "feature_serving_client_not_opened",
                    "account_id": account_id,
                    "transaction_id": transaction_id,
                    "transaction_timestamp": transaction_timestamp,
                    "component": "feature_serving_client",
                },
            )
            return _zero_for(account_id)

        start = time.perf_counter()
        future = self._executor.submit(self._fetch_from_store, account_id)
        try:
            raw = future.result(timeout=self._timeout_seconds)
            elapsed = time.perf_counter() - start
            feature_store_retrieval_seconds.observe(elapsed)

            # Miss detection: all feature values are None
            values = {k: v[0] for k, v in raw.items() if k != "account_id"}
            all_none = all(v is None for v in values.values())
            any_none = any(v is None for v in values.values())

            if all_none:
                logger.warning(
                    "feature_store_miss",
                    extra={
                        "event": "feature_store_miss",
                        "account_id": account_id,
                        "transaction_id": transaction_id,
                        "transaction_timestamp": transaction_timestamp,
                        "component": "feature_serving_client",
                    },
                )
                feature_store_miss_total.inc()
                return _zero_for(account_id)

            if any_none:
                populated = sum(1 for v in values.values() if v is not None)
                logger.warning(
                    "feature_store_partial_response",
                    extra={
                        "event": "feature_store_partial_response",
                        "account_id": account_id,
                        "transaction_id": transaction_id,
                        "transaction_timestamp": transaction_timestamp,
                        "populated_fields": populated,
                        "component": "feature_serving_client",
                    },
                )
                feature_store_miss_total.inc()
                return _zero_for(account_id)

            return FeatureVector(
                account_id=account_id,
                vel_count_1m=int(values.get("vel_count_1m") or 0),
                vel_amount_1m=float(values.get("vel_amount_1m") or 0.0),
                vel_count_5m=int(values.get("vel_count_5m") or 0),
                vel_amount_5m=float(values.get("vel_amount_5m") or 0.0),
                vel_count_1h=int(values.get("vel_count_1h") or 0),
                vel_amount_1h=float(values.get("vel_amount_1h") or 0.0),
                vel_count_24h=int(values.get("vel_count_24h") or 0),
                vel_amount_24h=float(values.get("vel_amount_24h") or 0.0),
                geo_country=str(values.get("geo_country") or ""),
                geo_city=str(values.get("geo_city") or ""),
                geo_network_class=str(values.get("geo_network_class") or ""),
                geo_confidence=float(values.get("geo_confidence") or 0.0),
                device_first_seen=int(values.get("device_first_seen") or 0),
                device_txn_count=int(values.get("device_txn_count") or 0),
                device_known_fraud=bool(
                    values.get("device_known_fraud") or False
                ),
                prev_geo_country=str(values.get("prev_geo_country") or ""),
                prev_txn_time_ms=int(values.get("prev_txn_time_ms") or 0),
            )

        except concurrent.futures.TimeoutError:
            elapsed = time.perf_counter() - start
            feature_store_retrieval_seconds.observe(elapsed)
            feature_store_fallback_total.labels(
                reason=FallbackReason.TIMEOUT.value
            ).inc()
            logger.warning(
                "feature_store_fallback",
                extra={
                    "event": "feature_store_fallback",
                    "account_id": account_id,
                    "transaction_id": transaction_id,
                    "transaction_timestamp": transaction_timestamp,
                    "reason": FallbackReason.TIMEOUT.value,
                    "elapsed_ms": elapsed * 1000,
                    "component": "feature_serving_client",
                },
            )
            return _zero_for(account_id)

        except Exception as exc:
            elapsed = time.perf_counter() - start
            feature_store_retrieval_seconds.observe(elapsed)
            feature_store_fallback_total.labels(
                reason=FallbackReason.UNAVAILABLE.value
            ).inc()
            logger.warning(
                "feature_store_fallback",
                extra={
                    "event": "feature_store_fallback",
                    "account_id": account_id,
                    "transaction_id": transaction_id,
                    "transaction_timestamp": transaction_timestamp,
                    "reason": FallbackReason.UNAVAILABLE.value,
                    "elapsed_ms": elapsed * 1000,
                    "exception": str(exc),
                    "exception_type": type(exc).__name__,
                    "component": "feature_serving_client",
                },
            )
            return _zero_for(account_id)
