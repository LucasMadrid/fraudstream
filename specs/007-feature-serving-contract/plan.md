# Implementation Plan: Feature Serving Contract

**Branch**: `007-feature-serving-contract` | **Date**: 2026-04-19 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/007-feature-serving-contract/spec.md`

## Summary

Implement the read-path feature serving layer for FraudStream's scoring pipeline. The scoring engine must retrieve precomputed velocity, geolocation, and device fingerprint features from the Feast online store for every transaction, within a 2ms p99 SLO, with a hard 3ms client-side timeout, zero-value fallback on unavailability/timeout, `feature_store_miss` handling for cold-start accounts, and a Prometheus-based staleness alert for the write→read observability loop. Constitution Principle XI mandates all of these constraints.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Feast 0.62.0 (online store read), concurrent.futures (timeout enforcement), prometheus-client (metrics), PyFlink 2.x (pipeline integration)  
**Storage**: Feast SQLite online backend (local dev), Feast Redis online backend (production); `storage/feature_store/feature_store.yaml`  
**Testing**: pytest (unit + integration), `concurrent.futures` mocking for timeout simulation  
**Target Platform**: Linux server (Flink pipeline operator)  
**Project Type**: Library module within streaming pipeline  
**Performance Goals**: p99 feature retrieval < 2ms; full `get_features()` call (including fallback) < 3ms + ε  
**Constraints**: 3ms hard timeout per read (Constitution Principle XI); zero stale values; no fallback to local cache; `feature_store_fallback_total` counter required  
**Scale/Scope**: One `FeatureServingClient` per Flink operator instance; per-transaction read on every scored transaction

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **Principle XI — Feature Serving Contract** | PASS | 2ms p99 SLO, 3ms client timeout, zero-value fallback, `feature_store_fallback_total`, `feature_store_miss` log, staleness alert — all addressed in design |
| **Principle II — Hot Path Latency** | PASS | 3ms for feature retrieval fits within the 5ms hot store allocation; overall 100ms end-to-end budget preserved |
| **Principle VII — Observability** | PASS | Three metrics (`fallback_total`, `miss_total`, `retrieval_seconds`), two structured log event types, one new Prometheus alert rule |
| **Principle X — Analytics Consumer** | N/A | Analytics consumer (Streamlit) is a separate future feature; this feature is purely read-path |
| **DD-10 — Online Feature Store** | PASS | Feast + Redis (prod) / SQLite (local); expected p99 0.8–1.5ms confirmed against 2ms SLO with ~0.5ms headroom |
| **Single project constraint** | PASS | All new code lives in existing `pipelines/scoring/` module; no new top-level project |
| **No new frameworks** | PASS | Feast 0.62.0 already declared in `pyproject.toml` (Feature 006); `concurrent.futures` is stdlib |

**Post-design re-check**: All gates pass. No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/007-feature-serving-contract/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── feature-serving-client.md  # Phase 1 output
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit.specify)
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code

```text
pipelines/scoring/
├── clients/
│   ├── __init__.py              # NEW — package init
│   └── feature_serving.py       # NEW — FeatureServingClient class
├── job_extension.py             # MODIFY — integrate feature retrieval before rule eval
├── metrics.py                   # MODIFY — add feature store metrics
└── types.py                     # MODIFY — add FeatureVector dataclass

infra/prometheus/alerts/
└── feature_serving.yml          # NEW — FeatureStoreStalenessHigh alert rule

storage/feature_store/
└── (no changes — write path owned by Feature 006)

tests/
├── unit/
│   └── test_feature_serving_client.py   # NEW — unit tests (mock Feast)
└── integration/
    └── test_feature_serving.py          # NEW — integration tests (real SQLite backend)
```

**Structure Decision**: Single-project layout extending `pipelines/scoring/`. New `clients/` sub-package for the feature serving client, keeping it separate from sinks and operators. All changes are additive (no deletion of existing code).

---

## Phase 0: Research Findings

See [research.md](research.md) for full decisions. Summary:

1. **Feast read API**: `FeatureStore.get_online_features()` — backend-agnostic, returns dict with `None` values for missing entries (miss detection: all values are `None`)
2. **Timeout enforcement**: `ThreadPoolExecutor` + `future.result(timeout=0.003)` — raises `concurrent.futures.TimeoutError` at 3ms; main thread returns zero vector immediately
3. **Zero-value vector**: Statically defined `ZERO_FEATURE_VECTOR` constant; partial results → full zero (no partial merge); no stale values under any path
4. **Fallback reason discrimination**: `FallbackReason` enum (`TIMEOUT`, `UNAVAILABLE`) as Prometheus counter label and log field
5. **Staleness detection**: `feature_materialization_lag_ms` Prometheus gauge (updated by Feature 006 push sink) + Prometheus alert at > 30,000ms for 60s
6. **Integration point**: `get_features()` called at top of scoring chain, before rule evaluator; `FeatureStore` initialized once in operator `open()`
7. **New metrics**: `feature_store_fallback_total{reason}`, `feature_store_miss_total`, `feature_store_retrieval_seconds` histogram (buckets: 0.001, 0.002, 0.003, 0.005, 0.010)

---

## Phase 1: Design

### FeatureVector Dataclass (`pipelines/scoring/types.py`)

Add `FeatureVector` dataclass with 17 feature fields + `account_id`. All fields typed (`int`, `float`, `str`, `bool`). Frozen dataclass for immutability.

### FeatureServingClient (`pipelines/scoring/clients/feature_serving.py`)

```text
FeatureServingClient
├── __init__(repo_path, timeout_seconds=0.003, executor_workers=1)
├── open() → initializes FeatureStore + ThreadPoolExecutor
├── get_features(account_id, transaction_id, transaction_timestamp) → FeatureVector
│   ├── submits _fetch_from_store() to executor
│   ├── future.result(timeout=0.003)
│   │   ├── success → parse response → FeatureVector or zero (on miss)
│   │   ├── TimeoutError → log + counter(TIMEOUT) → zero FeatureVector
│   │   └── Exception → log + counter(UNAVAILABLE) → zero FeatureVector
│   └── always records retrieval_seconds histogram
├── _fetch_from_store(account_id) → dict  [runs in worker thread]
└── close() → executor.shutdown(wait=False)
```

### Metrics additions (`pipelines/scoring/metrics.py`)

```python
feature_store_fallback_total = Counter(
    "feature_store_fallback_total",
    "Feature store zero-value fallbacks",
    ["reason"],  # "timeout" | "unavailable"
)
feature_store_miss_total = Counter(
    "feature_store_miss_total",
    "Feature store cache misses (account not found)",
)
feature_store_retrieval_seconds = Histogram(
    "feature_store_retrieval_seconds",
    "Feature store retrieval latency",
    buckets=[0.001, 0.002, 0.003, 0.005, 0.010, 0.050],
)
```

### Staleness Alert (`infra/prometheus/alerts/feature_serving.yml`)

```yaml
- alert: FeatureStoreStalenessHigh
  expr: feature_materialization_lag_ms > 30000
  for: 60s
  labels:
    severity: critical
    team: fraud-platform
```

### `job_extension.py` modification

The `wire_rule_evaluator()` function receives enriched transactions. A new `_FeatureEnrichmentFunction` (Flink `MapFunction`) will be inserted in the processing chain before the rule evaluator. It holds a `FeatureServingClient` instance and calls `get_features()` per transaction, merging the result into the transaction dict before passing downstream.

---

## Complexity Tracking

No complexity violations. All work is additive within the existing `pipelines/scoring/` module. No new top-level projects, no new databases, no new external services (Feast + Redis already declared in Feature 006).
