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
from pipelines.shared.feature_schema import FEATURE_REFS as _FEATURE_REFS

logger = logging.getLogger("feature_serving")


def _zero_for(account_id: str) -> FeatureVector:
    return replace(ZERO_FEATURE_VECTOR, account_id=account_id)


class FeatureServingClient:
    def __init__(
        self,
        feature_store_repo_path: str = "storage/feature_store",
        timeout_seconds: float = 0.003,
        executor_workers: int = 1,
        executor: concurrent.futures.Executor | None = None,
    ) -> None:
        self._repo_path = feature_store_repo_path
        self._timeout_seconds = timeout_seconds
        self._executor_workers = executor_workers
        self._store = None
        self._executor: concurrent.futures.Executor | None = executor
        self._owns_executor: bool = executor is None

    def open(self) -> None:
        import feast

        self._store = feast.FeatureStore(repo_path=self._repo_path)
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._executor_workers
            )

    def close(self) -> None:
        if self._executor is not None and self._owns_executor:
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
        if self._executor is None or self._store is None:
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

            return FeatureVector.from_feast_dict(account_id, values)

        except concurrent.futures.TimeoutError:
            elapsed = time.perf_counter() - start
            feature_store_retrieval_seconds.observe(elapsed)
            feature_store_fallback_total.labels(reason=FallbackReason.TIMEOUT.value).inc()
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
            feature_store_fallback_total.labels(reason=FallbackReason.UNAVAILABLE.value).inc()
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
