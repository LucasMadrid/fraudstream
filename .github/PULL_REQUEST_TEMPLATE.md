## Summary

<!-- One paragraph: what does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature / pipeline component
- [ ] Schema change (Avro / Protobuf)
- [ ] Infrastructure / IaC change (Terraform / Docker Compose)
- [ ] Scoring / rule engine change
- [ ] Hot-path change (enrichment, scoring, decision — latency-sensitive)
- [ ] Observability / runbook / docs
- [ ] Refactor (no behaviour change)

---

## Constitution Compliance

- [ ] **Stream-first**: No batch processing paths introduced
- [ ] **Sub-100ms budget**: Latency impact assessed (p99 < 100ms end-to-end)
- [ ] **Schema contract**: BACKWARD_TRANSITIVE compatibility verified (`schema-registry-compat` job passes)
- [ ] **PII minimization**: No PII introduced in logs, metrics, or Kafka payloads
- [ ] **Observability**: New metrics/spans added for any new code paths
- [ ] **Immutable event log**: No mutations to Iceberg tables or raw Kafka events

## Security

- [ ] **Secrets are injected via environment variables** (not hardcoded)
- [ ] **No credentials committed to repository**
- [ ] **Port exposure reviewed** (management API port 8090 must stay internal)

## Operational Excellence

- [ ] **Runbook updated** if operational procedure changed
- [ ] **Alerting rule added/updated** if new failure mode introduced
- [ ] **PII audit passed** (manual review until FR-005/TD-007 Iceberg sink ships)

## Testing

- [ ] **Unit tests added/updated** (`pytest --cov-fail-under=80`)
- [ ] **Integration tests pass**
- [ ] **Shadow mode tested** if rule mode changes introduced

---

## Detailed Constitution Compliance (if applicable)

> Every item must be checked or explicitly marked N/A with a reason.
> A PR that conflicts with a Core Principle is blocked until resolved.
> Reference: `.specify/memory/constitution.md`

### Code quality & testing

- [ ] **Unit test coverage ≥ 80%** — CI coverage gate passes (`pytest --cov-fail-under=80`)
- [ ] **New behaviour has unit tests** — positive and negative cases covered
- [ ] **Rule regression suite passes** (if scoring/rule change) — `tests/scoring/` suite shows no regressions; ≥ 10 positive + 10 negative examples per changed rule

### Schema contracts (Principle III)

- [ ] **No schema change** — N/A, skip remaining schema items
- [ ] **Schema Registry compatibility check passes** — CI `schema-registry-compat` job green
- [ ] **Schema integrity check passes** — `specs/*/contracts/` matches `pipelines/*/schemas/` exactly
- [ ] **Breaking change handled** — new subject version created (`txn.enriched.v2`); old consumers have a migration guide and deprecation date
- [ ] **All new fields have nullability justification** — `nullable: false` documented in the schema

### Latency budget (Principle II)

- [ ] **Not a hot-path change** — N/A, skip latency items
- [ ] **p99 latency verified under 2× peak load** — benchmark results attached or linked
- [ ] **Component budget slice respected**: ingestion < 10ms / enrichment < 20ms / Redis < 5ms / ML inference < 30ms / decision < 15ms

### PII & security (Principle VII)

- [ ] **No new PII handling** — N/A, skip PII items
- [ ] **Full PAN never written** — only `card_bin` (6) + `card_last4` (4); integration test confirms
- [ ] **Full IP never stored** — truncated to /24 subnet before any topic or store write
- [ ] **PII masking logic in shared library** — no producer-side PII logic duplicated

### Analytics persistence (Principle IX)

- [ ] **Does not add a processing or scoring side effect** — N/A, skip sink items
- [ ] **Iceberg sink write verified** — `iceberg.enriched_transactions` or `iceberg.fraud_decisions` row count matches Kafka offset within 0.1% in integration test
- [ ] **Iceberg sink latency** — sink write completes within 5 seconds of Kafka consumer receipt (outside scoring hot path)

### Observability (Principle VIII)

- [ ] **No new component or code path** — N/A, skip observability items
- [ ] **Structured logs emitted** — minimum fields: `transaction_id`, `component`, `timestamp`, `level`, `message`
- [ ] **Prometheus metrics defined** — throughput, latency, error counters for any new path
- [ ] **DLQ topic exists** for any new pipeline stage; DLQ depth > 0 alert wired within 60 seconds

### Canary & reversibility (Principle IV / FR-022)

- [ ] **Not a scoring or model change** — N/A, skip canary items
- [ ] **Shadow mode deployed first** — new rule has `mode: shadow`; FP rate < 2% before promotion
- [ ] **Canary policy applied** — model changes routed to N% of traffic by `account_id` hash
- [ ] **Automated rollback configured** — fraud rate delta > 5% OR FP rate > 5% within 1h triggers auto-demote

### Infrastructure / IaC (FR-003)

- [ ] **No infrastructure change** — N/A, skip IaC items
- [ ] **`terraform validate` passes** in CI
- [ ] **Terraform plan diff posted** to this PR as a comment before approval
- [ ] **All Kafka topic changes are idempotent** — `create_before_destroy` applied; partial apply leaves no inconsistent state

### ADR compliance (FR-024)

- [ ] **No architectural decision** — N/A, skip ADR items
- [ ] **Relevant ADRs reviewed**: <!-- list ADR file(s) -->
- [ ] **New self-managed component has an ADR** — managed-cloud alternative named; review trigger documented

---

## Linked spec / issue

<!-- e.g. Closes #42 | Implements FR-014 (specs/004-operational-excellence/spec.md) -->

## Test evidence

<!-- Paste CI run link, coverage report excerpt, latency benchmark output, or integration test log. -->

## Rollback plan

<!-- How do we revert this if it causes a production incident? (feature flag / schema rollback / previous image tag) -->
