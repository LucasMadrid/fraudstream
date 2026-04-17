# Contract: ScoringConfig Extension

**FR**: FR-025 (ml_serving_url, cb_*), FR-026 (redis_url)  
**File**: `pipelines/scoring/config.py`  
**Type**: Additive dataclass field extension — no breaking changes to existing fields

---

## New Fields

| Field | Type | Default | Env Var | FR |
|---|---|---|---|---|
| `ml_serving_url` | str | `"http://localhost:8080/score"` | `ML_SERVING_URL` | FR-025 |
| `cb_error_threshold` | int | `3` | `CB_ERROR_THRESHOLD` | FR-014 |
| `cb_open_seconds` | int | `30` | `CB_OPEN_SECONDS` | FR-014 |
| `cb_probe_timeout_ms` | int | `5` | `CB_PROBE_TIMEOUT_MS` | FR-014 |
| `redis_url` | str | `"redis://localhost:6379/0"` | `REDIS_URL` | FR-026 |

## Validation Constraints

- `cb_error_threshold`: must be >= 1; values < 1 are invalid (single-error circuit opening prohibited per FR-014 edge case)
- `cb_open_seconds`: must be > 0
- `cb_probe_timeout_ms`: must be in range [1, 50]; values > 50ms violate the latency budget
- `redis_url`: must be a valid `redis://` or `rediss://` URI
- `ml_serving_url`: must be a valid `http://` or `https://` URL

## Backward Compatibility

All new fields have defaults. Existing instantiation code (no keyword args for new fields) continues to work unchanged.

## docker-compose Environment Binding

```yaml
# infra/docker-compose.yml (scoring engine service, when added)
environment:
  REDIS_URL: redis://redis:6379/0
  ML_SERVING_URL: http://ml-stub:8080/score   # or omit for StubMLModelClient
  CB_ERROR_THRESHOLD: "3"
  CB_OPEN_SECONDS: "30"
  CB_PROBE_TIMEOUT_MS: "5"
```
