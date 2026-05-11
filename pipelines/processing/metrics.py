"""Prometheus metrics bridge for the enrichment processor (FR-012)."""

from prometheus_client import Counter, Gauge, Histogram

from pipelines.shared.safe_metric import _SafeMetric

# Enrichment latency histogram — buckets tuned to the <20ms budget slice
enrichment_latency_ms = _SafeMetric(
    Histogram(
        "enrichment_latency_ms",
        "End-to-end enrichment latency from Kafka receipt to enriched record assembly (ms)",
        buckets=[1, 5, 10, 20, 50, 100, 250, 500],
    )
)

# DLQ events counter — labelled by error_type for alert routing
dlq_events_total = _SafeMetric(
    Counter(
        "dlq_events_total",
        "Total number of events routed to the dead-letter queue",
        labelnames=["error_type"],
    )
)

# Consumer lag gauge — records behind the source topic head
consumer_lag_records = _SafeMetric(
    Gauge(
        "consumer_lag_records",
        "Current Kafka consumer lag in number of records behind the source topic head",
    )
)

# Checkpoint duration gauge — last completed checkpoint wall-clock duration
last_checkpoint_duration_ms = _SafeMetric(
    Gauge(
        "last_checkpoint_duration_ms",
        "Duration of the most recently completed Flink checkpoint in milliseconds",
    )
)

# Deduplication skipped counter
dedup_skipped_total = _SafeMetric(
    Counter(
        "dedup_skipped_total",
        "Total number of duplicate transaction_id events skipped by the deduplication filter",
    )
)

# Checkpoint failures counter (for watermark stall alert)
checkpoint_failures_total = _SafeMetric(
    Counter(
        "checkpoint_failures_total",
        "Total number of Flink checkpoint failures",
    )
)

# Last watermark advance epoch (for stall detection)
last_watermark_advance_epoch = _SafeMetric(
    Gauge(
        "last_watermark_advance_epoch",
        "Unix epoch seconds of the most recent watermark advancement across all partitions",
    )
)

# Late event metrics — distinguishes within-window vs beyond-window arrivals (FR-013)
late_events_within_window_total = _SafeMetric(
    Counter(
        "late_events_within_window_total",
        "Events that arrived late but within the allowed lateness window",
        labelnames=["stage"],
    )
)

late_events_beyond_window_total = _SafeMetric(
    Counter(
        "late_events_beyond_window_total",
        "Events that arrived beyond the allowed lateness window, routed to DLQ",
        labelnames=["stage", "topic"],
    )
)

corrected_record_latency_ms = _SafeMetric(
    Histogram(
        "corrected_record_latency_ms",
        "Latency of late-arriving events that were still accepted and corrected (ms)",
        buckets=[1, 5, 10, 25, 50, 100, 250, 500],
        labelnames=["stage"],
    )
)

# Iceberg sink metrics
iceberg_buffer_overflow_total = _SafeMetric(
    Counter(
        "iceberg_buffer_overflow_total",
        "Total Iceberg buffer overflow events (batch size reached max without successful flush)",
    )
)

iceberg_catalog_unavailable_total = _SafeMetric(
    Counter(
        "iceberg_catalog_unavailable_total",
        "Total number of Iceberg catalog connection failures",
    )
)

iceberg_flush_duration_seconds = _SafeMetric(
    Histogram(
        "iceberg_flush_duration_seconds",
        "Time taken to flush enriched transactions to Iceberg",
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )
)

feast_push_failures_total = _SafeMetric(
    Counter(
        "feast_push_failures_total",
        "Total number of Feast feature push failures",
    )
)

# Import shared cross-layer metric to avoid duplicate registration
