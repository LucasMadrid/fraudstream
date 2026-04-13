# Canary Deployment Policy

Policy for safe, staged rollouts of model versions and rule updates in the fraud detection pipeline.

## 1. Model Canary

### Strategy

New model versions are rolled out to a **hash-gated subset of transactions** before full production deployment.

### Traffic Splitting

- **Hash function**: MurmurHash3 of `account_id` modulo 100
- **Default canary percentage**: 1% (configurable)
- **Selection criteria**: `murmurhash3(account_id) % 100 < N`, where N is the canary percentage
  - N=1: accounts 0 routed to new model
  - N=50: accounts 0-49 routed to new model (useful for larger canaries)

### Rollback Triggers

Automated rollback occurs if **either** condition is met within the **1-hour observation window**:

| Metric | Threshold | Action |
|--------|-----------|--------|
| **Fraud rate delta** | > 5 percentage points vs. baseline | Automatic rollback |
| **False positive rate** | > 5% | Automatic rollback |

### Rollout Procedure

1. Deploy new model version to staging cluster
2. Enable canary mode: set `canary_model_version=<version>` and `canary_percentage=1`
3. Collect 1 hour of shadow metrics
4. If no rollback triggers fire, gradually increase canary percentage (e.g., 1% → 5% → 25% → 100%) in 1-hour intervals
5. Monitor fraud rate and FP rate at each stage
6. Upon reaching 100%, promote to primary model

### Monitoring

- Dashboard: Real-time fraud and FP rates by model version
- Alert: `model_fraud_rate_delta_high` (> 5%) triggers rollback webhook
- Alert: `model_fp_rate_high` (> 5%) triggers rollback webhook
- Log all model version changes to audit trail with timestamp and trigger reason

## 2. Rule Canary

### Strategy

Rule updates use a **two-stage promotion process** (shadow mode → full rollout) in v1. Rules are first validated against live traffic without affecting scoring, then promoted to production.

### Stage 1: Shadow Mode

- New rule is **evaluated but not applied** to transaction scoring
- Predictions are logged and compared against baseline rule behavior
- Duration: 24 hours of production traffic minimum
- **Gate for promotion**: False positive rate < 2% over shadow data
  - FP rate calculation: `false_positives / (true_negatives + false_positives)`
  - Assessed against same transactions the old rule would have scored

### Stage 2: Full Rollout

- Rule is applied to all transactions in production
- Old rule is archived but kept available for quick rollback
- Monitoring continues at full scale

### Promotion Workflow

```
New Rule Created
       ↓
Shadow Mode (24h minimum)
  - Track FP rate in logs
  - Compare with baseline rule
       ↓
FP rate < 2% ?
  ├─ YES → Promote to Production
  └─ NO  → Adjust rule → Restart shadow mode
       ↓
Full Rollout
  - Apply to all transactions
  - Monitor production alerts
```

### Deferred Feature: Hash-Gated Partial Rollout

A hash-gated intermediate stage (50% traffic by account_id using MurmurHash3, `RuleMode.partial`) is **deferred to v2**. See [TD-009](../technical-debt/TD-009.md) for details. This would allow smoother escalation between shadow mode and 100% rollout for high-risk rule changes.

### Monitoring

- Dashboard: Rule FP rates in shadow mode vs. production
- Shadow mode logs: Predictions from new rule vs. baseline
- Alert: `rule_fp_rate_high` (> 2% in shadow, or > 5% in production) for promotion gate
- Audit trail: Rule version changes, promotion approvals, and FP rate justifications

## 3. Automated Rollback

### Rule Demotion (Staging Rollback)

When the `rule_fp_rate_high` alert fires in production:

1. **Alert source**: Prometheus / monitoring system detects FP rate > 5%
2. **Alertmanager webhook**: Alert is routed to Alertmanager webhook handler
3. **Demotion request**: Webhook invokes `POST /rules/{rule_id}/demote`
4. **Scoring engine reload**: Scoring engine configuration is reloaded within **30 seconds**
   - Old rule version is restored from archive
   - New rule is disabled in scoring pipeline
5. **UI update**: Streamlit dashboard reflects demotion within **one polling cycle** (typically < 5 seconds)
6. **Audit log**: Demotion reason, timestamp, and triggered alert recorded

### Demotion Endpoint Specification

```
POST /rules/{rule_id}/demote
Content-Type: application/json

{
  "reason": "fp_rate_high",
  "alert_id": "<alertmanager_alert_id>",
  "timestamp": "2025-04-13T10:30:00Z"
}

Response 200 OK:
{
  "rule_id": "<rule_id>",
  "previous_version": 2,
  "restored_version": 1,
  "engine_reload_time_ms": 125,
  "demotion_timestamp": "2025-04-13T10:30:00Z"
}
```

### Rollback SLA

| Component | SLA |
|-----------|-----|
| Alert detection | < 1 minute |
| Webhook delivery | < 10 seconds |
| Scoring engine reload | < 30 seconds |
| UI dashboard update | < 5 seconds |

### Manual Override

Operators may demote a rule manually via the Streamlit UI or CLI:
```bash
curl -X POST http://localhost:8000/rules/{rule_id}/demote \
  -H "Content-Type: application/json" \
  -d '{"reason": "manual_override", "operator": "alice@company.com"}'
```

## References

- **Scoring Engine**: `src/scoring/engine.py`
- **Rule Management**: `src/rules/manager.py`
- **Monitoring Alerts**: `config/alerts/fraud-detection.yml`
- **Alertmanager Configuration**: `config/alertmanager/config.yml`
- **Technical Debt**: See [TD-009](../technical-debt/TD-009.md) for v2 hash-gated partial rollout feature
