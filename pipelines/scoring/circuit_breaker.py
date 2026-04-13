"""ML circuit breaker for fraud detection scoring engine using pybreaker.

Wraps MLModelClient with circuit breaker pattern for resilience. Uses ThreadPoolExecutor
for probe timeout management instead of pybreaker's built-in timeout (which is unreliable).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pybreaker
from prometheus_client import Counter, Gauge

from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.ml_client import MLClientError, MLModelClient

logger = logging.getLogger(__name__)

# Prometheus metrics
ml_circuit_breaker_state = Gauge(
    "ml_circuit_breaker_state",
    "Current ML circuit breaker state (1=current, 0=not current)",
    labelnames=["state"],
)
ml_fallback_decisions_total = Counter(
    "ml_fallback_decisions_total",
    "Total ML scoring decisions made in fallback mode (circuit open)",
)

# Initialize all states to 0
for state in ["closed", "open", "half_open"]:
    ml_circuit_breaker_state.labels(state=state).set(0)


class FraudCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Updates Prometheus metrics on circuit breaker state transitions."""

    def state_change(
        self, cb: pybreaker.CircuitBreaker, old_state: str, new_state: str
    ) -> None:
        """Update gauge when circuit state changes."""
        # Set new state to 1, all others to 0
        for state in ["closed", "open", "half_open"]:
            ml_circuit_breaker_state.labels(state=state).set(1 if state == new_state else 0)
        logger.info("Circuit breaker state changed: %s -> %s", old_state, new_state)

    def before_call(
        self, cb: pybreaker.CircuitBreaker, func, *args, **kwargs
    ) -> None:
        """Increment fallback counter when circuit is open."""
        if cb.current_state == "open":
            ml_fallback_decisions_total.inc()


class MLCircuitBreaker:
    """Circuit breaker for ML model client with fallback support."""

    def __init__(self, client: MLModelClient, config: ScoringConfig) -> None:
        """Initialize circuit breaker with ML client and config.

        Args:
            client: ML model client to wrap
            config: Scoring configuration with cb_error_threshold, cb_open_seconds,
                cb_probe_timeout_ms
        """
        self.client = client
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)

        self._cb = pybreaker.CircuitBreaker(
            fail_max=config.cb_error_threshold,
            reset_timeout=config.cb_open_seconds,
            listeners=[FraudCircuitBreakerListener()],
        )
        # Register the client.score method with the circuit breaker
        self._cb_wrapped_score = self._cb(self.client.score)

    def score_with_fallback(
        self, features: dict, fallback_score: float = 0.0
    ) -> tuple[float, bool]:
        """Score features with fallback to constant when circuit open or call fails.

        Args:
            features: Feature vector to score
            fallback_score: Score to return if circuit is open or call fails

        Returns:
            Tuple of (score, used_fallback) where used_fallback is True if fallback was used.
        """
        try:
            # Use ThreadPoolExecutor for probe timeout management in HALF_OPEN state
            future = self._executor.submit(self._cb_wrapped_score, features)
            score = future.result(timeout=self.config.cb_probe_timeout_ms / 1000.0)
            return (score.fraud_probability, False)
        except (pybreaker.CircuitBreakerError, MLClientError, TimeoutError) as e:
            logger.warning(
                "ML scoring failed, using fallback score: %s", type(e).__name__, exc_info=True
            )
            return (fallback_score, True)


__all__ = [
    "MLCircuitBreaker",
    "ml_circuit_breaker_state",
    "ml_fallback_decisions_total",
]
