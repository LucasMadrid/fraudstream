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
    """Stub implementation for local dev and testing."""

    def __init__(self, stub_score: float = 0.0, fail_on_call: bool = False) -> None:
        self._stub_score = stub_score
        self._fail_on_call = fail_on_call

    def score(self, features: dict) -> MLScore:
        logger.debug("StubMLModelClient.score called with %d features", len(features))
        if self._fail_on_call:
            raise MLClientError("StubMLModelClient configured to fail")
        return MLScore(fraud_probability=self._stub_score, model_version="stub-v1", latency_ms=0.5)
