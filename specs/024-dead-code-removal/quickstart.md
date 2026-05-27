# Quickstart: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Branch**: `024-dead-code-removal`

## What this change does

Removes `_FeatureEnrichmentFunction`, `_FlinkFeatureEnrichmentFunction`, and `_FEATURE_ZERO_DEFAULTS` from the scoring pipeline. These symbols implement a Feast online-store lookup that fires on every transaction and, when Feast times out, overwrites live enriched features with zeros — silently breaking velocity and device fraud rules. Removing them brings `job_extension.py` into compliance with ADR-005.

## Files changed

| File | Change |
|------|--------|
| `pipelines/scoring/job_extension.py` | Delete ~127 lines of Feast wiring and import |
| `tests/unit/scoring/test_job_extension_safety.py` | Delete `TestFeatureEnrichmentFallback`; add `TestLiveFeaturesReachEvaluator` |
| `tests/integration/test_feature_serving.py` | Delete entire file |

**Not touched**: `pipelines/scoring/metrics.py` — `feature_store_fallback_total` stays for the future standalone consumer.

## Step-by-step

### 1. Delete `_FEATURE_ZERO_DEFAULTS` from job_extension.py

Remove lines 12–30: the module-level dict mapping all feature names to zero/False defaults.

### 2. Delete `_FeatureEnrichmentFunction` from job_extension.py

Remove lines 33–109: the class that calls `get_online_features` and falls back to `_FEATURE_ZERO_DEFAULTS` on timeout.

### 3. Delete `_FlinkFeatureEnrichmentFunction` and the `.map()` wiring

Remove lines 112–138: the PyFlink wrapper class and the line:
```python
enriched_stream = enriched_stream.map(_FlinkFeatureEnrichmentFunction(), output_type=None)
```

After this deletion `enriched_stream` at line 185 picks up the upstream enriched stream directly — which is the correct behavior per ADR-005.

### 4. Remove the metrics import and call site

- Line 121: remove `from pipelines.scoring.metrics import feature_store_fallback_total`
- Line 128: remove `feature_store_fallback_total.labels(result="timeout").inc()`

### 5. Clean up test_job_extension_safety.py

- Remove lines 11–12 (imports of `_FEATURE_ZERO_DEFAULTS` and `_FeatureEnrichmentFunction`)
- Remove lines 91–135 (`TestFeatureEnrichmentFallback` class)

### 6. Delete tests/integration/test_feature_serving.py

```bash
git rm tests/integration/test_feature_serving.py
```

### 7. Add the SC-002 regression test

Add `TestLiveFeaturesReachEvaluator` to `tests/unit/scoring/test_job_extension_safety.py`:

```python
class TestLiveFeaturesReachEvaluator:
    def test_non_zero_features_reach_evaluate_unchanged(self, mocker):
        captured = []
        mocker.patch(
            "pipelines.scoring.job_extension._evaluate",
            side_effect=lambda txn: captured.append(dict(txn)) or {},
        )
        txn = {
            "transaction_id": "t1",
            "vel_count_5m": 10,
            "device_known_fraud": True,
            "amount": 250.0,
        }
        wire_rule_evaluator(iter([txn]))
        assert captured[0]["vel_count_5m"] == 10
        assert captured[0]["device_known_fraud"] is True

    def test_no_feast_call_on_processing(self, mocker):
        feast_mock = mocker.patch("feast.FeatureStore.get_online_features")
        txn = {"transaction_id": "t2", "vel_count_5m": 5, "device_known_fraud": False}
        wire_rule_evaluator(iter([txn]))
        feast_mock.assert_not_called()
```

## Verify

```bash
# SC-001: no Feast symbols remain in job_extension.py
grep -r 'FeatureEnrichmentFunction\|_FEATURE_ZERO_DEFAULTS' pipelines/scoring/job_extension.py

# SC-002/SC-003: new test passes
pytest tests/unit/scoring/test_job_extension_safety.py -v

# SC-003/SC-004: full suite passes with no import errors
pytest tests/ -v
```

All four commands should exit 0 with no output from the grep.
