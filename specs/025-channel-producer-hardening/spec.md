# Feature Specification: Channel Producer Hardening (SD-013)

**Feature Branch**: `025-channel-producer-hardening`
**Created**: 2026-05-27
**Status**: Draft
**Origin**: SD-013 + Principle IV (constitution.md) — `channel` must be producer-assigned from config, never caller-supplied.

## Context

The ingestion API currently validates `channel` as a caller-supplied field with a `VALID_CHANNELS` allowlist. The domain decision (Principle IV, constitution.md) is that `channel` is a producer identity — it identifies which system class is publishing, and is set at construction time from service config. A caller that sends `channel` in the request body is violating the API contract. That field must be removed from the request schema, and `VALID_CHANNELS` must be deleted.

`TransactionEventBuilder.build()` must read `self._channel` (injected at construction time) rather than reading the field from the inbound payload or hardcoding `"API"`.

## User Scenarios & Testing

### User Story 1 — Channel is producer identity, not caller data (Priority: P1)

An operator configures the API producer with `channel: "API"`. Every transaction event published by that producer carries `channel: "API"` — regardless of what the HTTP client sends. A client attempting to set its own channel receives HTTP 400.

**Why this priority**: Allowing callers to spoof channel identity breaks the Channel Isolation invariant. Every downstream consumer (rule engine, analytics, audit trail) relies on channel being trustworthy.

**Independent Test**: POST `/transactions` with body `{"channel": "MOBILE", ...}` returns HTTP 400. POST without `channel` succeeds and the published Kafka event carries the producer's configured channel.

**Acceptance Scenarios**:

1. **Given** the API producer is configured with `channel: "API"`, **When** a client POSTs a valid transaction without a `channel` field, **Then** the Kafka event carries `channel: "API"` and the response is HTTP 200/202.
2. **Given** a client POSTs with `"channel": "MOBILE"` in the body, **When** the API producer validates the request, **Then** it returns HTTP 400 with an error indicating `channel` is not an accepted field.
3. **Given** the API producer is configured with `channel: "POS"`, **When** any transaction is published, **Then** every produced Kafka event carries `channel: "POS"` regardless of request body contents.

---

### User Story 2 — VALID_CHANNELS is deleted (Priority: P1)

No allowlist exists in the codebase that enumerates valid channel names against caller input. Channel validation is a deployment-time concern (the config value must be one of the known enum values) not a request-time concern.

**Why this priority**: An allowlist on a field that should not exist in the request schema is a maintenance hazard and a false signal of intent.

**Independent Test**: `grep -r 'VALID_CHANNELS' .` returns no matches.

**Acceptance Scenarios**:

1. **Given** `VALID_CHANNELS` exists in the producer module, **When** it is deleted, **Then** no remaining code references the symbol and all tests pass.

---

### Edge Cases

- If `self._channel` is not set at construction time (misconfigured producer), the error must surface at startup, not per-request.
- `channel` is a required field on the Kafka event schema — it must still be present in the published message; it just must not be read from the inbound HTTP body.

## Requirements

### Functional Requirements

- **FR-001**: The `channel` field MUST NOT appear in the validated HTTP request schema for the ingestion API.
- **FR-002**: A request body containing a `channel` field MUST result in HTTP 400.
- **FR-003**: `VALID_CHANNELS` MUST be deleted from the API producer module.
- **FR-004**: `TransactionEventBuilder.build()` MUST read `channel` from `self._channel`, which is injected at construction time from service config.
- **FR-005**: The Kafka event produced MUST include the `channel` field sourced from config, not from the inbound request.

### Key Entities

- **Channel**: One of `API`, `POS`, `WEB`, `MOBILE`. Producer-assigned identity. Immutable for the lifetime of a producer instance.
- **TransactionEventBuilder**: Builds Kafka event payloads. Receives `channel` at construction time; never reads it from the inbound request.

## Success Criteria

- **SC-001**: `grep -r 'VALID_CHANNELS' pipelines/` returns no matches.
- **SC-002**: POST with `channel` in body returns HTTP 400 — verified by unit test.
- **SC-003**: Published Kafka event contains `channel` equal to the producer's configured value — verified by unit test.
- **SC-004**: All existing ingestion tests pass after the change.

## Assumptions

- The channel enum values (`API`, `POS`, `WEB`, `MOBILE`) are already documented in CONTEXT.md and the constitution.
- Config validation (ensuring `channel` is a known enum value at startup) is in-scope for this spec.
- No downstream schema migration is needed — the Kafka event already carries `channel`; only the source of the value changes.
