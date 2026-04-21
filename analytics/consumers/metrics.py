"""Prometheus metric definitions for the analytics consumer layer."""

import threading

from prometheus_client import Counter, Gauge, start_http_server

analytics_consumer_lag = Gauge(
    "analytics_consumer_lag",
    "Current consumer lag in messages",
    ["consumer_group", "topic"],
)

analytics_events_consumed_total = Counter(
    "analytics_events_consumed_total",
    "Total events consumed since startup",
    ["topic"],
)

analytics_consumer_restarts_total = Counter(
    "analytics_consumer_restarts_total",
    "Number of Kafka consumer thread restarts",
)

_metrics_server_started = False
_metrics_lock = threading.Lock()


def start_metrics_server(port: int = 8004) -> None:
    """Start the Prometheus HTTP metrics server once; subsequent calls are no-ops."""
    global _metrics_server_started
    with _metrics_lock:
        if not _metrics_server_started:
            start_http_server(port)
            _metrics_server_started = True
