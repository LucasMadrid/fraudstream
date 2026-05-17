"""Prometheus metric definitions for the analytics consumer layer."""

import os
import threading

from prometheus_client import REGISTRY, Counter, Gauge, start_http_server


def _gauge(name: str, doc: str, labels: list[str] | None = None) -> Gauge:
    try:
        return Gauge(name, doc, labels or [])
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


def _counter(name: str, doc: str, labels: list[str] | None = None) -> Counter:
    try:
        return Counter(name, doc, labels or [])
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


analytics_consumer_lag = _gauge(
    "analytics_consumer_lag",
    "Current consumer lag in messages",
    ["consumer_group", "topic"],
)

analytics_events_consumed_total = _counter(
    "analytics_events_consumed_total",
    "Total events consumed since startup",
    ["topic"],
)

analytics_consumer_restarts_total = _counter(
    "analytics_consumer_restarts_total",
    "Number of Kafka consumer thread restarts",
)

_metrics_lock = threading.Lock()
_STARTED_ENV_KEY = "_FRAUDSTREAM_METRICS_SERVER_STARTED"


def start_metrics_server(port: int = 8004) -> None:
    """Start the Prometheus HTTP metrics server once per process."""
    if os.environ.get(_STARTED_ENV_KEY):
        return
    with _metrics_lock:
        if os.environ.get(_STARTED_ENV_KEY):
            return
        try:
            start_http_server(port)
        except OSError:
            pass  # port already bound from a prior start in this process
        os.environ[_STARTED_ENV_KEY] = "1"
