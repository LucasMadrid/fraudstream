# Implementation Plan: Analytics Persistence Layer

**Branch**: `006-analytics-persistence-layer` | **Date**: 2026-04-18 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/006-analytics-persistence-layer/spec.md`

## Summary

Add three mandatory write paths and one query contract to the existing FraudStream pipeline:

1. **Iceberg enriched-transactions sink** — side output from `EnrichedRecordAssembler`, writing every enriched record to `iceberg.enriched_transactions` on MinIO within 5 seconds of Kafka consumer receipt.
2. **Iceberg fraud-decisions sink** — wired into the scoring engine after the ALLOW/FLAG/BLOCK decision is issued, writing every decision to `iceberg.fraud_decisions`. Requires a new `FraudDecision` type (gap in current scoring types — see research.md §1).
3. **Feast feature materialization** — push API called on every enrichment cycle, materializing velocity, geo, and device features to the online store with accurate event timestamps for point-in-time correct offline training.
4. **Query contract** — Trino SQL interface over both Iceberg tables on MinIO; analyst SQL views and a YAML data catalog maintain schema coherence across all layers. A CI gate enforces Avro → Iceberg DDL → Feast alignment on every schema change.

Data modeling strategy: Avro schemas in the Schema Registry are the canonical source of truth. The Iceberg DDL is generated from the Avro schemas and tracked in git. Feast feature view definitions are validated against the Avro schemas in CI. A `docs/data_catalog.yaml` file documents lineage, ownership, and sensitivity.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: PyFlink 2.x DataStream API (existing), PyIceberg 0.7+ (new), Feast 0.40+ (new), fastavro (existing), trino-python-client (new, for integration tests)  
**Storage**: Apache Iceberg on MinIO (append-only analytics tables), Feast SQLite online backend (local), Feast dask offline store (local)  
**Testing**: pytest (existing), testcontainers (existing), new integration tests for Iceberg sink, Feast materialization, Trino query contract  
**Target Platform**: Linux container (Docker Compose Analytics tier); cloud target is S3 + Athena or BigQuery  
**Project Type**: Stream processing sinks + columnar storage layer + SQL query interface  
**Performance Goals**: Iceberg writes ≤ 5 s from Kafka consumer receipt; Feast online store write within 5 s of enrichment; Trino query ≤ 10 s at p99 under interactive load  
**Constraints**: Sinks are side outputs — MUST NOT add latency to the 100 ms hot path; tables are append-only; `transaction_id` is the deduplication key in both Iceberg tables  
**Scale/Scope**: 2 Iceberg tables, 3 Feast feature views, 4 analyst SQL views, 1 CI validation gate, 1 YAML data catalog

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Stream-First | PASS | Iceberg sinks are side outputs from existing Flink operators — not separate batch jobs. Kafka remains the system of record. |
| II. Sub-100ms Budget | PASS | Iceberg/Feast writes run in a 5-second side-output budget, fully decoupled from the 100 ms scoring hot path. |
| III. Schema Contract | PASS | `iceberg.enriched_transactions` derived from `enriched-txn-v1.avsc`; `iceberg.fraud_decisions` derived from constitution Data Contracts. CI gate enforces BACKWARD_TRANSITIVE alignment. |
| IV. Channel Isolation | PASS | Both Iceberg tables carry the `channel` field; per-channel analytics queries are native SQL. |
| V. Defense in Depth | PASS | No change to rule engine or ML serving path. |
| VI. Immutable Event Log | PASS | Both Iceberg tables are append-only. Deduplication on `transaction_id` prevents double-writes. Review state stays in PostgreSQL. |
| VII. PII Minimization | ⚠️ SCOPE | `iceberg.enriched_transactions` carries `card_bin`, `card_last4`, `caller_ip_subnet`, `api_key_id` — all masked upstream per Principle VII. PCI-DSS scope extends to Iceberg access. MinIO bucket policies and Trino access control must restrict reads. Documented in quickstart.md. |
| VIII. Observability | PASS | Sink write latency, Feast materialization failures, and row-count reconciliation metrics emitted per Principle VIII. DLQ event raised when 5-second write budget is exceeded. |
| IX. Analytics-First Persistence | PASS | This feature IS the Principle IX implementation. |
| X. Analytics Consumer | PASS | Trino + Streamlit are Analytics tier services. Isolation verified by User Story 4. |

**Result: PASS** — PII scope is acknowledged, not a violation. Data is already masked at the producer per Principle VII; access governance is an operational concern documented in quickstart.md.

## Project Structure

### Documentation (this feature)

```text
specs/006-analytics-persistence-layer/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions, rationale, alternatives
├── data-model.md        # Phase 1 — entity definitions, field contracts
├── quickstart.md        # Phase 1 — local setup, make targets, validation steps
├── contracts/
│   ├── iceberg-enriched-transactions-v1.sql   # Iceberg DDL contract
│   ├── iceberg-fraud-decisions-v1.sql          # Iceberg DDL contract
│   └── trino-analyst-views.sql                 # Analyst view definitions
└── tasks.md             # Phase 2 output (created by /speckit.tasks — not this command)
```

### Source Code (repository root)

```text
pipelines/
├── processing/
│   ├── operators/
│   │   ├── enricher.py           # MODIFY — wire IcebergEnrichedSink as side output
│   │   └── iceberg_sink.py       # NEW — IcebergEnrichedSink (PyIceberg write path)
│   └── schemas/
│       ├── enriched-txn-v1.avsc  # EXISTING (canonical schema source, unchanged)
│       └── iceberg-enriched-v1.sql  # NEW — generated DDL, tracked in git
├── scoring/
│   ├── types.py                  # MODIFY — add FraudDecision dataclass
│   └── sinks/
│       ├── alert_kafka.py        # EXISTING (unchanged)
│       ├── alert_postgres.py     # EXISTING (unchanged)
│       └── iceberg_decisions.py  # NEW — IcebergDecisionsSink

storage/
├── feature_store/
│   ├── feature_store.yaml        # NEW — Feast repo config (registry, backends)
│   ├── entities/
│   │   └── transaction.py        # NEW — Feast Entity: account_id
│   └── features/
│       ├── __init__.py
│       ├── velocity.py           # NEW — VelocityFeatureView
│       ├── geo.py                # NEW — GeoFeatureView
│       └── device.py             # NEW — DeviceFeatureView
└── lake/
    ├── catalog.properties        # NEW — Iceberg REST catalog config (MinIO endpoint)
    ├── schemas/
    │   ├── enriched_transactions.sql  # NEW — canonical Iceberg DDL
    │   └── fraud_decisions.sql        # NEW — canonical Iceberg DDL
    └── migrations/
        └── README.md             # NEW — schema evolution runbook

analytics/
└── views/
    ├── v_transaction_audit.sql        # NEW — enriched_transactions JOIN fraud_decisions
    ├── v_fraud_rate_daily.sql         # NEW
    ├── v_rule_triggers.sql            # NEW
    └── v_model_versions.sql           # NEW

docs/
└── data_catalog.yaml             # NEW — field-level lineage, ownership, sensitivity

scripts/
├── evolve_iceberg_schema.py      # NEW — Avro → Iceberg DDL generator (idempotent ALTER TABLE)
└── validate_feast_schemas.py     # NEW — CI: Avro fields ⊆ Feast feature view fields

.github/
└── workflows/
    └── schema-evolution.yml      # NEW — blocks merge when Avro/Iceberg/Feast drift

infra/
├── docker-compose.yml            # MODIFY — add Iceberg REST catalog; confirm MinIO/Trino
└── iceberg/
    ├── catalog-config.properties  # NEW
    └── init-tables.sh             # NEW — idempotent DDL at Analytics tier startup

tests/
├── integration/
│   ├── test_iceberg_enriched_sink.py    # NEW — write → Trino reconciliation
│   ├── test_iceberg_decisions_sink.py   # NEW
│   └── test_feast_materialization.py    # NEW — online store + PIT correctness
├── contract/
│   └── test_avro_iceberg_alignment.py   # NEW — CI gate: Avro fields ⊆ Iceberg columns
└── load/
    └── test_sink_throughput.py          # NEW — 2× peak volume, ≤ 5 s budget
```

## Complexity Tracking

No constitution violations — no complexity justification required.
