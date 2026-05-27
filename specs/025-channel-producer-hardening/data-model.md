# Data Model: Channel Producer Hardening (SD-013)

**Phase 1 output** | Branch: `025-channel-producer-hardening` | Date: 2026-05-27

## Entities

### Channel (value type)

| Property | Value |
|----------|-------|
| Type | `str` enum |
| Valid values | `"API"`, `"POS"`, `"WEB"`, `"MOBILE"` |
| Ownership | Producer — assigned from `ProducerConfig.channel` at startup |
| Mutability | Immutable for the lifetime of a producer instance |
| Source | `PRODUCER_CHANNEL` env var (default: `"API"`) |
| Validation | At `ProducerConfig.__post_init__` — startup, not per-request |

**Invariant**: A `channel` value present in an HTTP request body is a contract violation and MUST be rejected with HTTP 400.

---

### ProducerConfig (dataclass, `pipelines/ingestion/api/config.py`)

**Change**: Add one field.

| Field | Type | Source | Default | Validation |
|-------|------|--------|---------|------------|
| `channel` | `str` | `PRODUCER_CHANNEL` env var | `"API"` | `__post_init__`: must be in `{"API", "POS", "WEB", "MOBILE"}` |

All other fields unchanged.

---

### TransactionEventBuilder (class, `pipelines/ingestion/api/producer.py`)

**Change**: Constructor signature and `build()` implementation.

| Aspect | Before | After |
|--------|--------|-------|
| `__init__` params | `(cfg: MaskingConfig)` | `(cfg: MaskingConfig, channel: str)` |
| `self._channel` | absent | `str` — injected at construction |
| `build()` — channel source | hardcoded `"API"` | `self._channel` |

**State transitions**: None — channel is set once at construction and never mutated.

---

### HTTP Request Schema (inbound to `POST /v1/transactions`)

**Change**: `channel` is removed from the accepted field set.

**Before** (required fields as validated by `validate_required_fields`):
```
transaction_id, account_id, merchant_id, amount, currency,
event_time, channel, card_number, caller_ip, api_key_id, oauth_scope
```

**After** (required fields):
```
transaction_id, account_id, merchant_id, amount, currency,
event_time, card_number, caller_ip, api_key_id, oauth_scope
```

**Rejected fields**: `channel` — presence in request body → HTTP 400.

---

### Kafka Event Schema (outbound to `txn.api`, Avro `txn_api_v1.avsc`)

**No change.** `channel` remains a required string field. Its value is now sourced from `ProducerConfig.channel` rather than from the inbound request body. Downstream consumers are unaffected.

---

### Deleted Symbol

| Symbol | Location | Reason |
|--------|----------|--------|
| `VALID_CHANNELS` | `producer.py:41` | Channel is not a caller-supplied value; runtime allowlist validation against request input is wrong by design |
