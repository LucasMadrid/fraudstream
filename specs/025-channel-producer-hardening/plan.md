# Implementation Plan: Channel Producer Hardening (SD-013)

**Branch**: `025-channel-producer-hardening` | **Date**: 2026-05-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/025-channel-producer-hardening/spec.md`

## Summary

Remove `channel` from the HTTP request schema so callers can no longer spoof producer identity. `VALID_CHANNELS` is deleted. `ProducerConfig` gains a `channel` field read from `PRODUCER_CHANNEL` env var at startup; `TransactionEventBuilder` receives it at construction and writes it into the Kafka event. Requests that include `channel` in the body are rejected with HTTP 400.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: confluent-kafka, avro (txn_api_v1.avsc), http.server (stdlib), pytest  
**Storage**: Kafka topic `txn.api` (Avro schema — no schema change required; `channel` field already present)  
**Testing**: pytest — unit tests in `tests/unit/test_producer.py` and `tests/unit/test_producer_extended.py`  
**Target Platform**: Linux server (Docker container)  
**Project Type**: HTTP web-service (ingestion API → Kafka producer)  
**Performance Goals**: No latency regression — validation is O(1) field-presence check  
**Constraints**: No Avro schema migration; `channel` is already a required string field in `txn_api_v1.avsc`  
**Scale/Scope**: 4 change sites in `producer.py`, 1 new field in `config.py`, 2 test files updated

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **Principle IV** — `channel` is producer-assigned identity, set from config | **PASS** | This spec exists specifically to enforce Principle IV |
| **Principle I** — PII masking at producer boundary, before Kafka | **PASS** | No changes to masking logic |
| **Principle III** — Immutable events, no post-publish mutations | **PASS** | No event mutation; only source-of-truth for channel changes |
| **Principle VII** — No silent data loss or degradation | **PASS** | Startup validation ensures channel is known before any request is served |

**Constitution Check: PASS.** All gates clear. Proceed to Phase 0.

*Post-design re-check*: The design (startup enum validation + construction-time injection) satisfies the edge case noted in the spec: if `self._channel` is not set at construction time the error surfaces at startup, not per-request. No violations introduced.

## Project Structure

### Documentation (this feature)

```text
specs/025-channel-producer-hardening/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── http-request.md
└── checklists/
    └── requirements.md
```

### Source Code (affected files only)

```text
pipelines/ingestion/api/
├── config.py            # Add channel: str field (PRODUCER_CHANNEL env var, default "API")
└── producer.py          # Delete VALID_CHANNELS; remove "channel" from required list;
                         # delete channel validation in validate_field_values();
                         # add caller-supplied-channel rejection guard in do_POST();
                         # TransactionEventBuilder.__init__ gains channel: str param;
                         # build() uses self._channel instead of hardcoded "API";
                         # ProducerService passes config.channel to builder

tests/unit/
├── test_producer.py          # Update channel-related assertions
└── test_producer_extended.py # Update / add SC-002 and SC-003 tests
```

## Complexity Tracking

No constitution violations. No complexity justification required.
