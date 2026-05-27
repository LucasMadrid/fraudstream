# Iceberg sink implementations inherit from `_IcebergSinkBase`

`pipelines/shared/iceberg_sink_base.py` contains `_IcebergSinkBase`, a complete implementation of the buffer-flush-dedup-circuit-breaker-DLQ lifecycle for PyFlink-style Iceberg append sinks. Both `IcebergDecisionsSink` and `IcebergEnrichedSink` reimplemented this lifecycle independently — 481 lines and ~1016 lines respectively — without using the base. A bug fix or behavioural change to the flush path required two edits, and there was no guarantee they stayed in sync.

Both sinks now inherit `_IcebergSinkBase`. Each subclass implements only `_records_to_arrow_table()` (the Arrow schema specific to its table) and overrides the metric hooks (`_on_buffer_overflow`, `_on_catalog_unavailable`, `_after_flush_success`, `_observe_flush_duration`) with its own Prometheus counters. The dual-class pattern (PyFlink version + plain-Python fallback) is removed from both files; the base handles catalog absence gracefully via `self._table = None`.

**`open()` behaviour differs between the two sinks by design:**

- `IcebergDecisionsSink` overrides `open()` to raise if `self._table is None` after `super().open()`. A misconfigured Iceberg URI must kill the Flink job at startup — silently dropping Decision records (the permanent audit trail for every transaction outcome) is worse than a failed startup.
- `IcebergEnrichedSink` uses the base's `open()` as-is (graceful degradation). Enriched records already exist in Kafka; Iceberg is a secondary store for analytics, not the primary record.

**Test improvement**

Buffer dedup, circuit breaker wiring, timeout enforcement, and DLQ routing are tested once against `_IcebergSinkBase` directly. Each sink's test suite covers only its Arrow schema correctness and metric hook invocations — no mocking of the lifecycle is required.

**Considered alternatives**

- *Keep dual-class pattern, share only flush logic* — still requires two inheritance chains and the PyFlink guard at module level. Rejected: the base already handles PyFlink absence correctly by not importing it.
- *Merge both sinks into one parameterised class* — the Arrow schemas are too different (8 fields for decisions, 30+ for enriched) to merge cleanly without adding a generic mapping layer. Rejected in favour of explicit subclasses.
