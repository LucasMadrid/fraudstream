# Quickstart: Channel Producer Hardening (SD-013)

**Branch**: `025-channel-producer-hardening`

## What changes

Four surgical edits across two files, plus test updates.

## Change 1 — `pipelines/ingestion/api/config.py`

Add `channel` field to `ProducerConfig` with startup validation:

```python
channel: str = field(default_factory=lambda: os.environ.get("PRODUCER_CHANNEL", "API"))

def __post_init__(self) -> None:
    valid = {"API", "POS", "WEB", "MOBILE"}
    if self.channel not in valid:
        raise ValueError(f"PRODUCER_CHANNEL must be one of {sorted(valid)}, got {self.channel!r}")
```

## Change 2 — `pipelines/ingestion/api/producer.py`

### 2a. Delete `VALID_CHANNELS` (line 41)

```python
# DELETE this line:
VALID_CHANNELS = {"POS", "WEB", "MOBILE", "API"}
```

### 2b. Remove `"channel"` from `validate_required_fields` required list (line 68)

```python
# Remove "channel" from the list
required = [
    "transaction_id",
    "account_id",
    "merchant_id",
    "amount",
    "currency",
    "event_time",
    # "channel" — removed: not a caller-supplied field
    "card_number",
    "caller_ip",
    "api_key_id",
    "oauth_scope",
]
```

### 2c. Delete channel validation in `validate_field_values` (lines 98-100)

```python
# DELETE these lines:
channel = payload.get("channel", "")
if channel not in VALID_CHANNELS:
    errors.append(f"channel must be one of {sorted(VALID_CHANNELS)}")
```

### 2d. Add caller-channel rejection guard in `_RequestHandler.do_POST`

Insert immediately after `payload = json.loads(body)` and before `validate_required_fields`:

```python
if "channel" in payload:
    self._respond(400, {"error": "ValidationError", "fields": ["channel is not an accepted request field"]})
    return
```

### 2e. Update `TransactionEventBuilder.__init__` to accept channel

```python
def __init__(self, cfg: MaskingConfig, channel: str) -> None:
    self._cfg = cfg
    self._channel = channel
```

### 2f. Replace hardcoded `"API"` in `build()` (line 163)

```python
"channel": self._channel,
```

### 2g. Pass channel from config in `ProducerService.__init__` (line 206)

```python
self._builder = TransactionEventBuilder(self._masking_cfg, self._config.channel)
```

## Change 3 — `pipelines/ingestion/api/producer.py` (telemetry, line 252)

Also update the span attribute (non-functional but accurate):

```python
span.set_attribute("channel", self._config.channel)
```

## Tests to add / update

**`tests/unit/test_producer.py`** and **`tests/unit/test_producer_extended.py`**:
- Remove `"channel"` from all request body dicts in test fixtures
- Delete tests that verify channel validation against `VALID_CHANNELS`
- Add SC-002: POST with `"channel": "MOBILE"` in body → `assert response_code == 400`
- Add SC-003: `TransactionEventBuilder(masking_cfg, "POS").build(payload)["channel"] == "POS"`

## Verification

```bash
# SC-001: no VALID_CHANNELS anywhere in pipelines/
grep -r 'VALID_CHANNELS' pipelines/   # must return empty

# SC-002 / SC-003 / SC-004:
make test-unit   # or: uv run pytest tests/unit/test_producer*.py -v
```

## Environment variable

| Var | Default | Effect |
|-----|---------|--------|
| `PRODUCER_CHANNEL` | `API` | Sets the channel value stamped on every published event |

Invalid values cause `ValueError` at startup (before the HTTP server binds).
