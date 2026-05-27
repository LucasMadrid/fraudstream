---

description: "Task list for SD-013 Channel Producer Hardening"
---

# Tasks: Channel Producer Hardening (SD-013)

**Input**: Design documents from `/specs/025-channel-producer-hardening/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests ARE requested — Success Criteria SC-002 and SC-003 explicitly require unit-test verification, and SC-004 requires the existing test suite to pass.

**Organization**: Tasks are grouped by user story. Both US1 and US2 are P1 and tightly coupled (US2 deletes the allowlist that US1 makes obsolete) — they should typically ship in the same commit, but are tracked separately for traceability.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files / non-overlapping line ranges, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2)
- Include exact file paths in descriptions

## Path Conventions

This is an existing Python project. All paths are relative to repo root `/Users/lucasmadridbbva/Desktop/repos/streaming/fraudstream/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment is ready. No project initialization needed — this is a surgical edit to an existing module.

- [X] T001 Verify clean working tree on branch `025-channel-producer-hardening` (run `git status` — only expected modifications are the spec artifacts)
- [X] T002 Verify Python environment: `uv pip list --python .venv | grep -E '(confluent-kafka|avro|pytest)'` returns all three

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the new config field that downstream code (US1) depends on. Must complete before US1 implementation starts.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Add `channel: str` field to `ProducerConfig` in `pipelines/ingestion/api/config.py` — default factory reads `PRODUCER_CHANNEL` env var with fallback `"API"`
- [X] T004 Add `__post_init__` method to `ProducerConfig` in `pipelines/ingestion/api/config.py` that asserts `self.channel in {"API", "POS", "WEB", "MOBILE"}`, raising `ValueError` otherwise (startup-time validation per spec edge case)
- [X] T005 [P] Add unit test for `ProducerConfig` channel validation in `tests/unit/test_producer.py` — covers: default `"API"` when env unset, accepts each of `{"API", "POS", "WEB", "MOBILE"}`, raises `ValueError` for `"INVALID"`

**Checkpoint**: `ProducerConfig` now carries an authoritative `channel` value. Story work may begin.

---

## Phase 3: User Story 1 — Channel is producer identity, not caller data (Priority: P1) 🎯 MVP

**Goal**: The API producer stamps every Kafka event with its configured channel. Any HTTP client attempting to set `channel` in the request body is rejected with HTTP 400.

**Independent Test**: POST `/v1/transactions` with body `{"channel": "MOBILE", ...}` returns HTTP 400. POST without `channel` succeeds and the published Kafka event carries the producer's configured channel.

### Tests for User Story 1 (write FIRST, ensure they FAIL before implementation)

- [X] T006 [P] [US1] Add `test_post_with_channel_in_body_returns_400` in `tests/unit/test_producer_extended.py` — covers SC-002: payload with `"channel": "MOBILE"` → HTTP 400 with `ValidationError` and field message indicating channel is not accepted
- [X] T007 [P] [US1] Add `test_event_channel_sourced_from_config` in `tests/unit/test_producer_extended.py` — covers SC-003: `TransactionEventBuilder(masking_cfg, "POS").build(valid_payload)["channel"] == "POS"`
- [X] T008 [P] [US1] Add `test_event_channel_ignores_payload_channel_field` in `tests/unit/test_producer_extended.py` — even if `channel` somehow reaches build() in payload, builder ignores it and uses `self._channel`

### Implementation for User Story 1

- [X] T009 [US1] Update `TransactionEventBuilder.__init__` in `pipelines/ingestion/api/producer.py` (line 128) to accept `channel: str` as second positional arg; store as `self._channel`
- [X] T010 [US1] Replace hardcoded `"channel": "API"` in `TransactionEventBuilder.build()` at `pipelines/ingestion/api/producer.py:163` with `"channel": self._channel`
- [X] T011 [US1] Update `ProducerService.__init__` in `pipelines/ingestion/api/producer.py:206` to pass `self._config.channel` as second arg to `TransactionEventBuilder(...)`
- [X] T012 [US1] Update OpenTelemetry span attribute at `pipelines/ingestion/api/producer.py:252` — replace hardcoded `span.set_attribute("channel", "API")` with `span.set_attribute("channel", self._config.channel)`
- [X] T013 [US1] Add caller-supplied-channel rejection guard in `_RequestHandler.do_POST` in `pipelines/ingestion/api/producer.py` — immediately after `payload = json.loads(body)` (around line 415) and before `validate_required_fields(payload)`: if `"channel" in payload` then `self._respond(400, {"error": "ValidationError", "fields": ["channel is not an accepted request field"]})` and `return`. Increment `ERRORS_TOTAL` and `SCHEMA_VALIDATION_ERRORS` with `error_type="ValidationError"` for observability parity with the existing validation path.

**Checkpoint**: User Story 1 fully functional. Run T006/T007/T008 — they must PASS now.

---

## Phase 4: User Story 2 — VALID_CHANNELS is deleted (Priority: P1)

**Goal**: No allowlist exists in the codebase that enumerates valid channel names against caller input. Channel validation is a deployment-time concern (handled by T004), not a request-time concern.

**Independent Test**: `grep -r 'VALID_CHANNELS' .` returns no matches in `pipelines/` (SC-001).

### Tests for User Story 2

- [X] T014 [US2] Update or remove existing channel-validation tests in `tests/unit/test_producer.py` and `tests/unit/test_producer_extended.py` that assert on `VALID_CHANNELS`-based error messages (e.g., `"channel must be one of"`) — they no longer apply since the field is rejected at the request layer (T013)
- [X] T015 [P] [US2] Remove `"channel"` from any test fixture payload dicts across `tests/unit/test_producer.py` and `tests/unit/test_producer_extended.py` that were previously sending it as required input

### Implementation for User Story 2

- [X] T016 [US2] Delete `VALID_CHANNELS = {"POS", "WEB", "MOBILE", "API"}` at `pipelines/ingestion/api/producer.py:41`
- [X] T017 [US2] Remove `"channel"` from the `required` list in `validate_required_fields` at `pipelines/ingestion/api/producer.py:68`
- [X] T018 [US2] Delete the three-line channel validation block in `validate_field_values` at `pipelines/ingestion/api/producer.py:98-100` (`channel = payload.get(...)` and the `if channel not in VALID_CHANNELS` assertion)

**Checkpoint**: `grep -r 'VALID_CHANNELS' pipelines/` returns empty (SC-001 passes).

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and cleanup.

- [X] T019 Run `grep -rn 'VALID_CHANNELS' pipelines/ tests/` — must return zero matches (SC-001)
- [X] T020 Run `uv run --python .venv pytest tests/unit/test_producer.py tests/unit/test_producer_extended.py -v` — all tests pass (SC-002, SC-003, SC-004 for unit tests)
- [X] T021 Run `uv run --python .venv pytest tests/ -v` — full suite passes; no regressions outside the producer module (SC-004)
- [X] T022 [P] Run `uv run --python .venv ruff check pipelines/ingestion/api/ tests/unit/test_producer*.py` — lint clean
- [X] T023 [P] Document `PRODUCER_CHANNEL` env var in deployment docs/compose files if any reference `KAFKA_BOOTSTRAP_SERVERS` and similar producer env vars (search: `grep -rn 'KAFKA_BOOTSTRAP_SERVERS' infra/ docker-compose*.yml 2>/dev/null` and add `PRODUCER_CHANNEL: API` alongside for the API producer service)
- [X] T024 Walk through `specs/025-channel-producer-hardening/quickstart.md` end-to-end against the final code — every code snippet shown there must match the committed implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (US1 builder needs `config.channel`)
- **User Story 1 (Phase 3)**: Depends on Phase 2 (T003, T004) — needs `ProducerConfig.channel`
- **User Story 2 (Phase 4)**: Depends on Phase 3 (T013) — caller-rejection guard must exist before deleting the body-level validation, otherwise there is a transient window where channel-in-body is silently accepted
- **Polish (Phase 5)**: Depends on Phases 3 and 4 complete

### Within Each User Story

- Tests first (T006–T008 for US1, T014–T015 for US2)
- Then implementation
- US1 implementation order: T009 → T010 → T011 → T012 → T013 (T009 is a signature change; T010–T011 depend on it; T012 and T013 are independent edits in the same file)
- US2 implementation order: T016, T017, T018 are independent edits in the same file but to different functions — can be committed together

### Parallel Opportunities

- T005 (config validation test) runs in parallel with T003/T004 once those signatures land
- T006, T007, T008 (US1 test stubs) can be written in parallel before T009–T013 begin
- T015 (fixture cleanup) is parallel to T016–T018 (production code changes) — different file sets
- T022, T023 (polish lint + docs) parallel with each other

### Same-File Constraint

T009–T013 and T016–T018 all edit `pipelines/ingestion/api/producer.py`. They are sequential within an editing session but the line ranges do not overlap, so a single developer can sequence them safely.

---

## Parallel Example: User Story 1 Tests

```bash
# Write all three US1 test stubs in parallel:
Task: "Add test_post_with_channel_in_body_returns_400 in tests/unit/test_producer_extended.py"
Task: "Add test_event_channel_sourced_from_config in tests/unit/test_producer_extended.py"
Task: "Add test_event_channel_ignores_payload_channel_field in tests/unit/test_producer_extended.py"
```

(All three target the same file but can be drafted in parallel as separate test functions; merge into a single commit.)

---

## Implementation Strategy

### MVP (single PR)

Because US1 and US2 are both P1 and tightly coupled, ship them in a single PR:

1. Phase 1 (Setup) → T001–T002
2. Phase 2 (Foundational) → T003–T005
3. Phase 3 (US1) → T006–T013
4. Phase 4 (US2) → T014–T018
5. Phase 5 (Polish) → T019–T024
6. Open PR titled `feat(ingestion): SD-013 channel is producer identity, not caller data`

### Why not split

Splitting US1 and US2 across PRs creates a transient state where either:
- US1 ships without US2 → `VALID_CHANNELS` and the channel-in-required-list dead code lingers, contradicting the spec.
- US2 ships without US1 → `validate_required_fields` no longer requires `channel`, but `TransactionEventBuilder` still hardcodes `"API"` — the producer would publish events with channel `"API"` for any producer instance, regardless of `PRODUCER_CHANNEL`. Misleading.

Single PR is the correct increment.

---

## Notes

- [P] tasks = different files OR independent edits — see same-file constraint above
- This is a security-relevant change (channel spoofing prevention) — request `everything-claude-code:security-reviewer` after implementation
- Verify the `validation_failed` log line at `pipelines/ingestion/api/producer.py:427` continues to fire for the new caller-channel rejection path (T013 should preserve this log structure)
- The `KAFKA_BOOTSTRAP_SERVERS` env var grep in T023 may be a no-op if there are no compose files referencing the API producer config — that's fine, the env var has a safe default
- Commit each phase as a logical group; do not amend the foundational commit after starting story work
