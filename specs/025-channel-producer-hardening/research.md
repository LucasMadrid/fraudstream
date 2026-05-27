# Research: Channel Producer Hardening (SD-013)

**Phase 0 output** | Branch: `025-channel-producer-hardening` | Date: 2026-05-27

## Summary

All unknowns resolved. This is a surgical change to a single Python module and its config dataclass. No framework choices, no new dependencies, no schema migration.

---

## R-001: How to reject caller-supplied `channel` in the HTTP request body

**Decision**: In `_RequestHandler.do_POST()`, before calling `validate_required_fields`, check `if "channel" in payload` and immediately return HTTP 400.

**Rationale**: The check must precede any other validation so the rejection is unconditional — even a payload that is otherwise perfectly valid must be rejected if it carries `channel`. A dedicated guard before the shared validation helpers makes the intent explicit and avoids entangling it with the existing `ValidationError` path.

**Alternatives considered**:
- Delete `channel` from the dict before validation: silently swallows the field; FR-002 requires 400, not silent strip.
- Raise `ValidationError` inside `validate_required_fields`: that function checks for *missing* required fields, not *unexpected* fields — wrong semantic layer.

---

## R-002: How to inject channel into TransactionEventBuilder at construction time

**Decision**: Add `channel: str` as the second positional argument to `TransactionEventBuilder.__init__`. Store as `self._channel`. Replace `"channel": "API"` at line 163 with `"channel": self._channel`.

**Rationale**: Constructor injection is the simplest form of dependency injection — no registry, no service locator. The channel value is immutable for the lifetime of a producer instance, so setting it once at construction is correct by design.

**Alternatives considered**:
- Pass channel as an argument to `build()`: violates the spec — build() must not read it from the request payload or from any per-call argument.
- Read from env var inside `build()`: bypasses `ProducerConfig`, making config non-authoritative.

---

## R-003: How to add `channel` to ProducerConfig with startup validation

**Decision**: Add `channel: str = field(default_factory=lambda: os.environ.get("PRODUCER_CHANNEL", "API"))` to `ProducerConfig`. Add a `__post_init__` that asserts `self.channel in {"API", "POS", "WEB", "MOBILE"}` and raises `ValueError` if not. This assertion runs at `ProducerConfig()` instantiation time (startup), not per-request.

**Rationale**: The spec edge case states: "if `self._channel` is not set at construction time (misconfigured producer), the error must surface at startup, not per-request." `__post_init__` on a dataclass is exactly startup-time validation. Using a default of `"API"` preserves backward compatibility for deployments that do not set the env var — the API producer has always been the `"API"` channel.

**Alternatives considered**:
- `os.environ["PRODUCER_CHANNEL"]` (required, no default): would break existing deployments that rely on the implicit `"API"` channel. The spec does not require making the env var mandatory.
- Validate in `ProducerService.__init__`: too late if the config object is used elsewhere.

---

## R-004: Impact on Avro schema (`txn_api_v1.avsc`)

**Decision**: No schema change required.

**Rationale**: The `channel` field is already present in `txn_api_v1.avsc` as a required string with doc `"Valid values: POS, WEB, MOBILE, API"`. The source of the value is changing (config instead of request body), but the Avro contract is unchanged. Downstream consumers see no difference.

---

## R-005: Impact on existing tests

**Affected files**:
- `tests/unit/test_producer.py`: Currently passes `"channel": "API"` in request body and expects it to succeed → must be updated to omit `channel` from request body; channel in Kafka event still `"API"` (from config default).
- `tests/unit/test_producer_extended.py`: Similar channel-in-body assertions → update. Also the channel validation tests against `VALID_CHANNELS` → delete or replace with FR-002 (channel-in-body → 400) and FR-003 (no VALID_CHANNELS).

**New tests needed**:
- SC-002: POST with `"channel": "MOBILE"` → HTTP 400
- SC-003: `TransactionEventBuilder("POS")` → produced event has `channel: "POS"`
- SC-001 verification is a grep, not a runtime test.
