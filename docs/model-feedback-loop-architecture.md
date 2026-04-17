# Model Feedback Loop — Architecture

**Status**: Architecture definition only. Implementation blocked by **TD-004-1** (no label ingestion pipeline).

**Feature**: FR-019 | **Spec**: `specs/004-operational-excellence/spec.md`

---

## Problem

The scoring engine emits fraud decisions but receives no ground-truth signal. Without feedback:
- ML model cannot be retrained on production patterns
- Per-rule false-positive rates are unmeasurable (TD-003-4)
- Shadow mode canary decisions lack quantitative basis

## Target Architecture

```text
[Chargeback / Label Source]
         │ (CSV, webhook, or API)
         ▼
[Label Ingestion Job] ──► txn.fraud.labels (Kafka topic, Avro)
         │
         ▼
[Label Join Job (Flink)] ◄── txn.fraud.decisions (Kafka)
         │ Joins on transaction_id, 24h window
         ▼
[iceberg.fraud_labels] ──► [Model Retraining Pipeline]
         │
         ▼
[Feedback Metrics] ──► Prometheus + Grafana
```

## Components

### `txn.fraud.labels` Topic (Kafka)

Avro schema (to be defined in dedicated spec):
```json
{
  "type": "record",
  "name": "FraudLabel",
  "namespace": "com.fraudstream.labels",
  "fields": [
    {"name": "transaction_id", "type": "string"},
    {"name": "label", "type": {"type": "enum", "name": "LabelType", "symbols": ["fraud", "legitimate", "disputed"]}},
    {"name": "label_source", "type": "string"},
    {"name": "label_timestamp_ms", "type": "long"},
    {"name": "chargeback_id", "type": ["null", "string"], "default": null}
  ]
}
```

### Label Join Logic

- Join key: `transaction_id`
- Join window: 24–72 hours (chargebacks typically arrive 24–48h after transaction)
- Partial labels: Transactions without labels are treated as TN (conservative assumption)

### Ground-Truth Latency Challenge

Chargebacks arrive days after fraud events. This means:
- Model retraining must use a delayed label window (not real-time)
- Per-rule FP rate metrics lag the actual rule behavior by 1-3 days
- Near-real-time feedback signals (analyst review) are higher-priority for rule demotion

## Blockers

| Blocker | Resolution |
|---------|-----------|
| TD-004-1: No `txn.fraud.labels` topic | Requires dedicated label ingestion spec |
| TD-004-7: No Iceberg sinks | `iceberg.fraud_labels` requires Iceberg sink first |
| TD-003-4: No FP feedback loop | Analyst review UI required before FP rate is meaningful |

## V2 Milestone

This architecture ships in V2 after:
1. Iceberg sinks spec (TD-004-7)
2. Label ingestion spec (TD-004-1)
3. Analyst review workflow (TD-003-4)
