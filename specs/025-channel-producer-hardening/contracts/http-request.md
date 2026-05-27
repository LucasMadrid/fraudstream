# HTTP Contract: POST /v1/transactions

**Service**: API Ingestion Producer  
**Endpoint**: `POST /v1/transactions`  
**Content-Type**: `application/json`

## Request Body — After SD-013

`channel` is NOT an accepted field. Its presence is a contract violation.

### Required Fields

| Field | Type | Constraints |
|-------|------|-------------|
| `transaction_id` | string | UUID v4; auto-generated if absent |
| `account_id` | string | Non-empty |
| `merchant_id` | string | Non-empty |
| `amount` | number | > 0 |
| `currency` | string | 3-character ISO-4217 code |
| `event_time` | integer | Unix timestamp (ms); within 5 min of server time |
| `card_number` | string | Valid PAN — masked before Kafka produce |
| `caller_ip` | string | IPv4 or IPv6 — truncated before Kafka produce |
| `api_key_id` | string | Non-empty |
| `oauth_scope` | string | Non-empty |

### Optional Fields

| Field | Type |
|-------|------|
| `geo_lat` | float or null |
| `geo_lon` | float or null |

### Forbidden Fields

| Field | Response |
|-------|----------|
| `channel` | HTTP 400 — `{"error": "ValidationError", "fields": ["channel is not an accepted request field"]}` |

## Responses

| Status | Condition | Body |
|--------|-----------|------|
| 200 | Published successfully | `{"transaction_id": "...", "status": "accepted", "latency_ms": N}` |
| 400 | `channel` present in body | `{"error": "ValidationError", "fields": [...]}` |
| 400 | Missing required field | `{"error": "ValidationError", "fields": [...]}` |
| 400 | Invalid PAN | `{"error": "InvalidPANError", "detail": "..."}` |
| 400 | Invalid JSON | `{"error": "InvalidJSON"}` |
| 500 | Masking failure | `{"error": "MaskingError", "detail": "..."}` |
| 500 | Unexpected error | `{"error": "InternalError", "detail": "..."}` |

## Channel Assignment

The `channel` field on the published Kafka event is assigned by the producer from `ProducerConfig.channel`, which is read from the `PRODUCER_CHANNEL` environment variable at startup (default: `"API"`). Valid values: `API`, `POS`, `WEB`, `MOBILE`.

Callers have no mechanism to influence the channel value.
