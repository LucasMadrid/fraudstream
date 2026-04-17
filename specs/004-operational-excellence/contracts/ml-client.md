# Contract: MLModelClient Interface

**FR**: FR-025  
**File**: `pipelines/scoring/ml_client.py`  
**Type**: Python ABC (internal interface contract)

---

## Interface

```python
class MLModelClient(ABC):
    def score(self, txn: dict) -> float: ...
```

### Input: `txn: dict`

Minimum required keys (derived from `EnrichedTransaction` Avro schema):

| Key | Type | Required | Notes |
|---|---|---|---|
| `transaction_id` | str | yes | UUID |
| `account_id` | str | yes | |
| `amount` | float | yes | Transaction amount in USD |
| `merchant_category` | str | yes | MCC code |
| `card_bin` | str | yes | First 6 digits |
| `card_last4` | str | yes | Last 4 digits |
| `ip_subnet` | str | yes | /24 truncated (Principle VII) |
| `device_fingerprint` | str | no | Mobile channel only |
| `event_timestamp` | int | yes | Epoch ms |

Additional enriched fields passed through transparently; the interface does not validate `txn` keys.

### Output: `float`

- Range: `[0.0, 1.0]` — fraud probability score
- `0.0` = definitely legitimate; `1.0` = definitely fraudulent
- The calling code (circuit breaker + evaluator) applies a threshold to convert to a binary signal

### Exceptions

| Exception | When raised |
|---|---|
| `MLClientError` | Any serving failure: HTTP error, timeout, parse failure, auth failure |

`MLClientError` is caught by the circuit breaker. Any other exception propagates as a pipeline error.

---

## v1 Stub Behaviour

`StubMLModelClient`:
- `stub_score: float = 0.0` — returned unconditionally when `fail_on_call=False`
- `fail_on_call: bool = False` — raises `MLClientError` immediately when `True` (chaos testing)
- Zero network I/O; safe to use in unit tests without mocking

---

## Circuit Breaker Integration

The `MLCircuitBreaker` wraps an `MLModelClient` instance:

```
txn arrives
  → circuit CLOSED? → call client.score(txn) with ThreadPoolExecutor(timeout=cb_probe_timeout_ms/1000)
      → success → return score
      → TimeoutError or MLClientError → increment failure counter
          → failures >= cb_error_threshold → OPEN circuit
  → circuit OPEN? → raise CircuitBreakerError immediately (no I/O)
  → circuit HALF-OPEN? → send one probe call with 5ms timeout
      → success → CLOSE circuit; return score
      → failure → re-OPEN circuit; reset timer
```

---

## v2 HTTP Implementation (future, out of scope for FR-025)

The real HTTP client (targeting MLflow/SageMaker) will implement `MLModelClient` and be injected via `ScoringConfig.ml_serving_url`. Swap is transparent to the circuit breaker and evaluator.
