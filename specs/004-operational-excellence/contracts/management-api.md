# Contract: Scoring Engine Management API

**FR**: US-4 AS-4, FR-014 (on-call notification), FR-022 (auto-demotion)  
**File**: New lightweight HTTP server in the scoring engine (port 8090)  
**Type**: Internal HTTP management API (not exposed externally; Alertmanager → internal network only)

---

## Overview

The scoring engine exposes a management HTTP server (FastAPI, port 8090) separate from the Flink job's data plane. It is NOT in the 100ms hot path. It handles:
- Rule mode changes (promotion/demotion)
- Circuit breaker state query
- Health check for Alertmanager webhook integration

---

## Endpoints

### `POST /rules/{rule_id}/demote`

Demotes an active rule to shadow mode.

**Triggered by**: Alertmanager `webhook_configs` when `rule_fp_rate_high` alert fires.

**Request**:
```
POST /rules/rule_001/demote
Content-Type: application/json

{
  "triggered_by": "auto_demotion",
  "alert_name": "rule_fp_rate_high",
  "fp_rate": 0.063,
  "timestamp": 1713913200000
}
```

**Response**:
```json
{
  "rule_id": "rule_001",
  "previous_mode": "active",
  "new_mode": "shadow",
  "config_event_published": false,
  "reload_timestamp": 1713913201000
}
```
> `config_event_published` is always `false` in v1 (Kafka topic deferred — TD-008).

**Side effects**:
1. Rewrites the rule's `mode` field in the loaded rule set (in-memory; persists to YAML on disk)
2. ~~Publishes a `rule_mode_change` event to `txn.rules.config`~~ — **deferred to v2** (see TD-008). In v1, config propagation is file-based only; the scoring engine polls the YAML config every 30s.
3. Scoring engine picks up change within the next rule reload cycle (≤ 30s)

> **TD-008**: Kafka-based config propagation via `txn.rules.config` is deferred. The `txn.rules.config` topic does not exist in v1. `config_event_published` is always `false` in v1 responses.

**Error responses**:
- `404` — rule_id not found
- `409` — rule already in shadow mode (idempotent; returns current state)
- `500` — internal error writing YAML to disk

---

### `POST /rules/{rule_id}/promote`

Promotes a shadow rule to active. Called by Streamlit analyst UI.

**Request**: same shape as `/demote` with `triggered_by: "analyst_promotion"`  
**Response**: same shape with `new_mode: "active"`

---

### `GET /circuit-breaker/state`

Returns current ML circuit breaker state for ops dashboards.

**Response**:
```json
{
  "state": "open",
  "failure_count": 3,
  "last_failure_at": 1713913100000,
  "open_since": 1713913110000,
  "next_probe_at": 1713913140000
}
```

---

### `GET /healthz`

Liveness probe for Alertmanager and docker-compose health checks.

**Response**: `200 OK` with `{"status": "ok"}`

---

## Alertmanager Integration

```yaml
# alertmanager/config.yml
route:
  routes:
    - match:
        alertname: rule_fp_rate_high
      receiver: scoring-engine-webhook

receivers:
  - name: scoring-engine-webhook
    webhook_configs:
      - url: "http://scoring-engine:8090/rules/{{ .GroupLabels.rule_id }}/demote"
        send_resolved: false
```

**Note**: Alertmanager must be added to `infra/docker-compose.yml` (see RQ-005 in research.md).

---

## Security

- Port 8090 is NOT exposed to the host in docker-compose; accessible only within the `fraudstream` Docker network
- No authentication in v1 (internal network only); mTLS deferred to cloud deployment
