# simple-streaming-pipeline Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-19

## Active Technologies

- Python 3.11 + concurrent.futures (stdlib, timeout enforcement), Feast 0.62.0 online store read API, prometheus-client (metrics), PyFlink 2.x (pipeline integration) (007-feature-serving-contract)
- Feast SQLite online backend (local dev), Feast Redis online backend (production) (007-feature-serving-contract)

- Python 3.11 + PyFlink 2.x DataStream API (existing), PyIceberg 0.11.1 (new), Feast 0.62.0 (new), fastavro (existing), trino-python-client (new, for integration tests) (006-analytics-persistence-layer)
- Apache Iceberg on MinIO (append-only analytics tables), Feast SQLite online backend (local), Feast dask offline store (local) (006-analytics-persistence-layer)

- Python 3.11 (004-operational-excellence)

## Project Structure

```text
src/
tests/
```

## Commands

```bash
cd src && pytest && ruff check .
```

## Code Style

Python 3.11: Follow standard conventions

## Recent Changes
- 007-feature-serving-contract: Added concurrent.futures (timeout enforcement), Feast 0.62.0 online read API, FeatureServingClient in pipelines/scoring/clients/, FeatureVector dataclass, feature store metrics (fallback_total, miss_total, retrieval_seconds histogram), FeatureStoreStalenessHigh Prometheus alert

- 006-analytics-persistence-layer: Added Python 3.11 + PyFlink 2.x DataStream API (existing), PyIceberg 0.11.1 (new), Feast 0.62.0 (new), fastavro (existing), trino-python-client (new, for integration tests)

- 004-operational-excellence: Added Python 3.11

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
