"""ML model client interface and stub implementation for scoring engine."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class MLClientError(Exception):
    """Raised when ML serving call fails or times out."""


@dataclass(frozen=True)
class MLScore:
    """Score returned by the ML model."""
    fraud_probability: float  # 0.0 to 1.0
    model_version: str
    latency_ms: float


class MLModelClient(ABC):
    """Abstract ML model client."""

    @abstractmethod
    def score(self, features: dict) -> MLScore:
        """Score a feature vector. Must return in < cb_probe_timeout_ms ms when circuit is
        HALF_OPEN."""
        ...


class StubMLModelClient(MLModelClient):
    """Stub implementation for local dev and testing. Always returns 0.1 probability."""

    def score(self, features: dict) -> MLScore:
        logger.debug("StubMLModelClient.score called with %d features", len(features))
        return MLScore(fraud_probability=0.1, model_version="stub-v1", latency_ms=0.5)
