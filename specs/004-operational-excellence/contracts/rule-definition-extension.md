# Contract: RuleDefinition Extension — Shadow Mode Field

**FR**: FR-022 (canary/shadow), US-4  
**File**: `pipelines/scoring/rules/models.py`  
**Type**: Backward-compatible Pydantic model field addition

---

## Change

Add `mode: RuleMode = RuleMode.active` to `RuleDefinition`.

```python
class RuleMode(StrEnum):
    active = "active"
    shadow = "shadow"

class RuleDefinition(BaseModel):
    ...
    mode: RuleMode = RuleMode.active   # NEW
```

## Backward Compatibility

- Existing YAML rule files without `mode:` key parse as `mode: active` — **no file changes required**
- `ConfigDict(extra="forbid")` is preserved; `mode` is an explicit field, not extra
- Schema Registry: `RuleDefinition` is NOT an Avro schema; this change does not trigger a Schema Registry compatibility check

## Evaluator Behaviour Contract

When `rule.mode == RuleMode.shadow`:
1. Rule evaluation logic runs identically to `active` mode (conditions are checked)
2. If the rule matches: increment `rule_shadow_triggers_total{rule_id=..., mode="shadow"}` counter
3. The matched rule ID is appended to `EvaluationResult.matched_rules` with a `":shadow"` suffix (e.g., `"rule_001:shadow"`)
4. `EvaluationResult.determination` is **NOT** affected by shadow rule matches — the transaction may still be `"clean"` even if a shadow rule fires
5. No BLOCK or FLAG decision is issued by a shadow rule alone

When `rule.mode == RuleMode.active`: existing behaviour unchanged.

## YAML Rule File Format (after extension)

```yaml
# rules/rules.yaml — example with shadow mode
rules:
  - rule_id: rule_004
    name: "High Frequency API Channel"
    family: velocity
    conditions:
      threshold: 20
      window_seconds: 60
    severity: high
    enabled: true
    mode: shadow   # omit for active (default)
```

## Promotion / Demotion Events

When mode changes, the scoring engine publishes to `txn.rules.config` topic:

```json
{
  "event_type": "rule_mode_change",
  "rule_id": "rule_004",
  "previous_mode": "shadow",
  "new_mode": "active",
  "timestamp": 1713913200000,
  "triggered_by": "analyst_promotion"
}
```

`triggered_by` values: `"analyst_promotion"` | `"auto_demotion"` | `"manual_override"`
