# simple-streaming-pipeline Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-05-12

## Active Technologies
- Python 3.11 + DuckDB ≥ 1.0 (in-process, in-memory), PyIceberg 0.11.1 (existing), pyarrow (existing, transitive via PyIceberg), streamlit (existing), pandas (existing) (009-streamlit-duckdb-migration)
- Apache Iceberg on MinIO — `iceberg.enriched_transactions`, `iceberg.fraud_decisions` (009-streamlit-duckdb-migration)
- PyIceberg→Arrow→DuckDB query pattern for Streamlit historical pages; replaces trino-python-client in analytics/queries/ (009-streamlit-duckdb-migration)
- Python 3.11 + pytest, ruff, confluent-kafka, cryptography (for cert generation) (023-fix-ci-green)
- N/A (CI/test infrastructure only) (023-fix-ci-green)
- Python 3.11 + pytest 8.x, ruff 0.4.x, confluent-kafka, cryptography (023-fix-ci-green)
- N/A (no storage changes; tests use tmp_path for ephemeral TLS material) (023-fix-ci-green)

- Python 3.11 + Streamlit ≥1.35, DuckDB ≥0.10, pandas ≥2.0, confluent-kafka (Kafka consumer daemon), prometheus-client (metrics at :8004) (008-analytics-consumer-layer)
- Apache Kafka topic `txn.fraud.alerts`, consumer group `analytics.dashboard`, dead-letter queue topic `txn.api.dlq` (008-analytics-consumer-layer)
- Trino-backed historical views (`v_fraud_rate_daily`, `v_rule_triggers`, `v_model_versions`) over Iceberg tables (008-analytics-consumer-layer)

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
- 023-fix-ci-green: Added Python 3.11 + pytest 8.x, ruff 0.4.x, confluent-kafka, cryptography
- 023-fix-ci-green: Added Python 3.11 + pytest, ruff, confluent-kafka, cryptography (for cert generation)
- 009-streamlit-duckdb-migration: Migrated Streamlit query engine from Trino to DuckDB (in-process); PyIceberg→Arrow→DuckDB pattern in analytics/queries/{fraud_rate,rule_triggers,model_versions}.py; MAX_HOURS=720 rolling-window cap; replaces trino-python-client dependency in analytics/queries/


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
