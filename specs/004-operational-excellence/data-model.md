# Data Model: Operational Excellence

**Phase**: 1 (Design)  
**Date**: 2026-04-12  
**Source**: spec.md entities + research.md findings

---

## Entities

### 1. RuleDefinition (extended)

**File**: `pipelines/scoring/rules/models.py`  
**Change type**: Backward-compatible field addition

```python
class RuleMode(StrEnum):
    active = "active"
    shadow = "shadow"

class RuleDefinition(BaseModel):
    rule_id: str
    name: str
    family: RuleFamily
    conditions: dict[str, Any]
    severity: Severity
    enabled: bool
    mode: RuleMode = RuleMode.active   # NEW — default preserves existing behaviour
    
    model_config = ConfigDict(extra="forbid")
```

**State transitions**: `shadow` → `active` (promotion via Streamlit UI or config reload); `active` → `shadow` (auto-demotion via Alertmanager webhook). Transitions publish to `txn.rules.config` Kafka topic.

**Validation rules**:
- `mode` must be one of `["active", "shadow"]`
- Existing YAML rule files without `mode` field parse as `mode: active` (Pydantic default)
- A rule with `enabled: false` is not evaluated regardless of `mode`

---

### 2. ScoringConfig (extended)

**File**: `pipelines/scoring/config.py`  
**Change type**: Additive field extensions — no breaking changes

```python
@dataclass
class ScoringConfig:
    # Existing fields (unchanged)
    kafka_brokers: str
    schema_registry_url: str
    fraud_alerts_topic: str
    fraud_alerts_dlq_topic: str
    rules_yaml_path: str
    fraud_alerts_db_url: str
    pg_pool_size: int

    # FR-025: ML client
    ml_serving_url: str = field(
        default_factory=lambda: os.environ.get("ML_SERVING_URL", "http://localhost:8080/score")
    )

    # FR-014: Circuit breaker
    cb_error_threshold: int = field(
        default_factory=lambda: int(os.environ.get("CB_ERROR_THRESHOLD", "3"))
    )
    cb_open_seconds: int = field(
        default_factory=lambda: int(os.environ.get("CB_OPEN_SECONDS", "30"))
    )
    cb_probe_timeout_ms: int = field(
        default_factory=lambda: int(os.environ.get("CB_PROBE_TIMEOUT_MS", "5"))
    )

    # FR-026: Redis
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )
```

---

### 3. MLModelClient (new interface)

**File**: `pipelines/scoring/ml_client.py`  
**Purpose**: Defines the contract the circuit breaker wraps; v1 is a stub.

```python
from abc import ABC, abstractmethod

class MLModelClient(ABC):
    @abstractmethod
    def score(self, txn: dict) -> float:
        """Score a transaction. Returns fraud probability [0.0, 1.0].
        
        Raises:
            MLClientError: On any serving failure (timeout, HTTP error, parse error).
        """
        ...

class StubMLModelClient(MLModelClient):
    """v1 stub — returns a fixed score or raises on demand for chaos testing."""
    
    def __init__(self, stub_score: float = 0.0, fail_on_call: bool = False):
        self._stub_score = stub_score
        self._fail_on_call = fail_on_call
    
    def score(self, txn: dict) -> float:
        if self._fail_on_call:
            raise MLClientError("StubMLModelClient configured to fail")
        return self._stub_score

class MLClientError(Exception):
    """Raised by MLModelClient implementations on any serving failure."""
    ...
```

**Relationships**: `MLCircuitBreaker` wraps an `MLModelClient` instance. `ScoringConfig.ml_serving_url` is used by the v2 HTTP implementation (not in this spec).

---

### 4. MLCircuitBreaker (new)

**File**: `pipelines/scoring/circuit_breaker.py`  
**States**: CLOSED → OPEN → HALF-OPEN → CLOSED  
**Library**: `pybreaker.CircuitBreaker`

```
CLOSED (normal)
  │ N consecutive errors within T seconds
  ▼
OPEN (fast-fail; all calls skipped)
  │ P seconds elapsed
  ▼
HALF-OPEN (probe: one call with 5ms timeout)
  │ success → CLOSED
  │ failure → OPEN (reset timer)
```

**Prometheus state exposure**:
- `ml_circuit_breaker_state{state="closed|open|half_open"}` — gauge, 1 = current state
- `ml_fallback_decisions_total` — counter, increments each time OPEN state causes fallback to rule-only

**Relationships**: instantiated in `pipelines/scoring/job_extension.py`; config sourced from `ScoringConfig.cb_*` fields.

---

### 5. PIIAuditRecord

**File**: Iceberg table `audit.pii_scans` (append-only)  
**Blocked by**: TD-007 (Iceberg sinks not yet wired)  
**Schema** (Avro/Iceberg column mapping):

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `audit_id` | string (UUID) | false | PK |
| `audit_timestamp` | long (epoch ms) | false | Scan execution time |
| `scan_scope_table` | string | false | e.g. `iceberg.enriched_transactions` |
| `scan_scope_partition` | string | true | Partition range scanned |
| `match_count` | int | false | 0 = clean |
| `matched_pattern` | string | true | null if match_count = 0 |
| `matched_field` | string | true | null if match_count = 0 |
| `remediation_status` | string | false | `pending` / `resolved` |
| `halt_issued` | boolean | false | true if writes halted |

**State transitions**: `pending` → `resolved` (after data privacy team confirms remediation). `halt_issued = true` is a one-way flag; write halt is lifted only by explicit operator action, not automated.

---

### 6. PostMortem (document schema)

**File**: `docs/postmortems/TEMPLATE.md`  
**Not a database entity** — Markdown document with enforced sections.

Required sections:
- `incident_id` — SEV level + date + short title
- `timeline` — chronological event list (epoch times)
- `root_cause` — 5-whys analysis (minimum 3 levels)
- `contributing_factors` — list
- `action_items` — table: owner / file_path / due_date / status
- `prevention` — what architectural change prevents recurrence
- `status` — `Open | In-Review | Closed`

**Closure gate**: `status` MUST NOT be set to `Closed` until all `action_items` with `status != merged` are resolved.

---

### 7. ADR (document schema)

**File**: `docs/adr/ADR-NNN-NAME.md`  
**Not a database entity** — Markdown document.

Required sections:
- `status` — `Proposed | Accepted | Deprecated`
- `context` — problem statement and constraints
- `options_considered` — table: Option / Pros / Cons
- `decision` — what was chosen and why
- `consequences` — what becomes easier/harder
- `review_triggers` — conditions under which this ADR should be re-evaluated

---

### 8. CanaryDeployment (policy document, not runtime entity)

**File**: `docs/deployment/CANARY_POLICY.md`  
**Runtime implementation**: rule `mode` field + Alertmanager auto-demotion webhook  

Key fields encoded in policy (not a schema, but constrained values):
- `canary_percentage: int` — default 1 for model canary; shadow mode = 0% blocking for rules
- `partition_key: "account_id"` — hash-based traffic split (not random, for reproducibility)
- `rollback_trigger_fraud_rate_delta: float` — default 0.05 (5%)
- `rollback_trigger_fp_rate: float` — default 0.05 (5%)
- `rollback_window_hours: int` — default 1

---

## Entity Relationships

```
ScoringConfig
  ├── MLModelClient (url: ml_serving_url)
  │     └── MLCircuitBreaker wraps MLModelClient
  │           ├── cb_error_threshold  ──┐
  │           ├── cb_open_seconds     ──┤ from ScoringConfig
  │           └── cb_probe_timeout_ms ──┘
  └── redis_url → Redis (feature cache)

RuleDefinition
  └── mode: RuleMode
        ├── active  → EvaluationResult.determination may be "suspicious"
        └── shadow  → EvaluationResult.determination unchanged; rule_shadow_triggers_total++

EvaluationResult
  └── matched_rules: list[str]  (shadow rule IDs tagged with "shadow" suffix)

PIIAuditRecord → iceberg.audit.pii_scans (BLOCKED: TD-007)
PostMortem → docs/postmortems/
ADR → docs/adr/
CanaryDeployment policy → docs/deployment/CANARY_POLICY.md
```
