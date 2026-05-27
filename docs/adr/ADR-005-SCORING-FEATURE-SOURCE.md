# Rule evaluator reads features from EnrichedTransaction, not Feast

The scoring pipeline (rule evaluator) runs co-located inside the Flink enrichment job. By the time `wire_rule_evaluator` is called, every enriched record already carries the velocity, geolocation, and device features computed moments earlier by the processing operators. Fetching those same features from the Feast online store is redundant and introduces a silent failure mode: a Feast timeout would replace live features with zero-value defaults, causing rules like `BURST_COUNT_5M` to miss real fraud signals even though the correct values are already in the record.

We removed `_FlinkFeatureEnrichmentFunction` from the scoring path. The rule evaluator reads directly from the enriched transaction dict.

**Considered alternatives**

- *Keep Feast call, fall back to enriched record values* — eliminates the zero-default hazard but still pays the Feast RTT on every transaction with no benefit.
- *Keep as-is* — only defensible if scoring were ever decoupled into a standalone Kafka consumer. That is a future possibility but not the current architecture.

**Consequences**

- If scoring is ever extracted into a standalone service (consuming `txn.enriched` from Kafka without the Flink operators), it will need its own Feast lookup re-added. The Feast 3ms SLA and zero-value fallback contract (ADR-003, Spec 007) still apply to that future path and to all external consumers of the online store.
- Feature materialization to Feast continues unchanged in the processing pipeline.
