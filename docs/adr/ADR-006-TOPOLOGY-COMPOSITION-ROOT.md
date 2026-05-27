# Flink DAG assembly lives in `pipelines/topology.py`, not in either pipeline package

The enrichment job (`pipelines/processing`) and the scoring extension (`pipelines/scoring`) are independent packages. Neither should import from the other. The current design has `pipelines/processing/job.py` importing `ScoringConfig`, `wire_rule_evaluator`, and `RuleLoader` from `pipelines.scoring` — preventing either package from being tested or deployed in isolation.

We introduce `pipelines/topology.py` as the single composition root. It is the only module that imports from both packages. It owns: Flink env construction (checkpointing, parallelism, JARs), CLI arg parsing, Prometheus metrics server startup, Kafka metrics bridge startup, and the final `env.execute()` call. It is the new entry point (`python -m pipelines.topology`).

`pipelines/processing/job.py` is reduced to `build_enrichment_stream(env, config: ProcessorConfig) -> DataStream` — the operators from KafkaSource through `EnrichedRecordAssembler` only. It has no `main()` and no knowledge of scoring.

`pipelines/scoring/job_extension.py` is unchanged: `wire_rule_evaluator(enriched_stream, config, rules)` remains as-is.

The `ImportError` catch around the scoring import in `processing/job.py` is removed. Scoring is always required; a missing package is a hard startup failure caught at the topology level.

**Considered alternatives**

- *Keep `processing/job.py` as entry point, delegate to topology internally* — less deployment churn (Docker entrypoints unchanged) but the entry point name misleads: a file named `processing/job.py` owns the full topology. Rejected in favour of an honest name.
- *Merge both packages into one* — eliminates the coupling but removes independent deployability as a future option. Rejected.

**Consequences**

- Deployment scripts and Docker `CMD` entries must be updated from `python -m pipelines.processing.job` to `python -m pipelines.topology`.
- `build_enrichment_stream()` becomes independently unit-testable: pass a mock env, assert the returned DataStream, no scoring imports required.
- If scoring is ever extracted into a standalone Kafka consumer, only `topology.py` changes — neither pipeline package is touched.
- Dead code in `job_extension.py` (`_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, `_FEATURE_ZERO_DEFAULTS`) is out of scope here; handled by spec 010 (SD-023).
