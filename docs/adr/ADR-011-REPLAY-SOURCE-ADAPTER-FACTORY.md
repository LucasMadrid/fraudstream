# `create_source_adapter` takes `ReplayConfig`; `original_decision` removed from event payload

Two friction points in the replay pipeline:

## 1. Dual pattern-match on source type

`replay_job._run_replay()` calls `_get_source_config()` to extract the right sub-config from `ReplayConfig`, then passes both `source_type` and that config to `create_source_adapter()`. This is two pattern-matches on the same enum that must stay in sync. Adding a fourth source type requires edits in both `_get_source_config()` and `create_source_adapter()`.

`create_source_adapter` is changed to accept `ReplayConfig` directly:

```python
def create_source_adapter(config: ReplayConfig, brokers: str = "localhost:9092") -> ReplaySourceAdapter:
```

The factory extracts the right sub-config internally. `_get_source_config()` is deleted. `_run_replay` collapses to one line:

```python
adapter = create_source_adapter(self.config, brokers=self.brokers)
```

## 2. `original_decision` is always `None` (SD-008)

`_process_event` reads `event.get("payload", {}).get("fraud_decision")` to populate `original_decision`. This is wrong for two reasons:

- The only valid replay source for rule re-scoring is `iceberg.enriched_transactions` (or `txn.enriched` from Kafka). Enriched transaction records carry no decision field — they are the *input* to the rule evaluator, not its output.
- Even if replaying from `iceberg.fraud_decisions`, the field is `decision`, not `fraud_decision`. But replaying a decision record through the rule evaluator is semantically wrong: the evaluator expects an enriched transaction dict, not a decision dict.

Comparing new vs. original decisions requires a join between `ReplayResult.original_event_id` and `iceberg.fraud_decisions` — it is not a field on the source event.

`original_decision` and `original_score` are removed from `_process_event`. `ReplayResult.original_decision` and `ReplayResult.original_score` remain as optional fields (they may be populated by the caller after a separate `fraud_decisions` lookup), but `_process_event` no longer attempts to read them from the event payload.

## Considered alternatives

- *Fix field name to `decision` and keep reading from payload* — only works if replaying from `fraud_decisions` table, which is the wrong source for re-scoring. Rejected.
- *Keep `_get_source_config()`, add type overloads to factory* — doesn't eliminate the dual pattern-match; just moves it. Rejected.
