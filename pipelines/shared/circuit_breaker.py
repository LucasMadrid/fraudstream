"""Iceberg circuit breaker with Prometheus state observability."""

from __future__ import annotations

import logging

import pybreaker
from prometheus_client import Counter, Gauge

from pipelines.shared.config import IcebergSinkConfig

logger = logging.getLogger(__name__)

iceberg_circuit_breaker_state = Gauge(
    "iceberg_circuit_breaker_state",
    "Current Iceberg circuit breaker state (1=current, 0=not current)",
    labelnames=["state"],
)
iceberg_circuit_breaker_open_total = Counter(
    "iceberg_circuit_breaker_open_total",
    "Total number of times the Iceberg circuit breaker transitioned to open state",
)

for _state in ["closed", "open", "half_open"]:
    iceberg_circuit_breaker_state.labels(state=_state).set(0)


class _IcebergCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb: pybreaker.CircuitBreaker, old_state, new_state) -> None:
        for state in ["closed", "open", "half_open"]:
            iceberg_circuit_breaker_state.labels(state=state).set(
                1 if state == str(new_state) else 0
            )
        if str(new_state) == "open":
            iceberg_circuit_breaker_open_total.inc()
        logger.info("Iceberg circuit breaker: %s -> %s", old_state, new_state)


class IcebergCircuitBreaker:
    """Circuit breaker for Iceberg sink writes, with Prometheus state observability."""

    def __init__(self, config: IcebergSinkConfig) -> None:
        self.config = config
        self._cb = pybreaker.CircuitBreaker(
            fail_max=config.cb_fail_max,
            reset_timeout=config.cb_reset_timeout_sec,
            listeners=[_IcebergCircuitBreakerListener()],
        )

    def call(self, fn, *args):
        """Call fn(*args) through the circuit breaker."""
        return self._cb.call(fn, *args)

    @property
    def fail_max(self) -> int:
        return self._cb.fail_max


__all__ = [
    "IcebergCircuitBreaker",
    "iceberg_circuit_breaker_state",
    "iceberg_circuit_breaker_open_total",
]
