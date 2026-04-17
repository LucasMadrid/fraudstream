# PII Audit Architecture

**Status: Architecture Draft — Implementation blocked by TD-007**

## Overview

This document specifies the architecture for continuous PII (Personally Identifiable Information) detection and audit scanning within the fraud detection pipeline. The system performs daily automated scans of Apache Iceberg tables to identify and log sensitive data exposure, triggering protective actions when matches are detected.

## Scope

PII audit applies to:
- `iceberg.enriched_transactions` — Transaction records enriched with customer and device data
- `iceberg.fraud_decisions` — Fraud classification decisions and supporting metadata

## Detected PII Patterns

### 1. Payment Account Number (PAN)

**Pattern**: 16-digit credit/debit card numbers with optional separators

**Regex**:
```regex
\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b
```

**Examples matched**:
- `4532123456789012` (no separators)
- `4532-1234-5678-9012` (dash separators)
- `4532 1234 5678 9012` (space separators)

**Coverage**: Visa, Mastercard, American Express (16-digit variants), Discover

### 2. IP Address (Non-RFC1918)

**Pattern**: Full IPv4 and IPv6 addresses outside /24 CIDR notation

**Constraints**:
- Must be outside private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.1/32)
- /24 or broader notation (e.g., `203.0.113.0/24`) is permitted and not flagged
- Individual IPs are flagged (e.g., `203.0.113.42`)

**Examples matched**:
- `203.0.113.42` (public IPv4)
- `2001:db8:85a3::8a2e:370:7334` (public IPv6)
- `198.51.100.100` (TEST-NET-2)

**Examples NOT matched** (compliant):
- `192.168.1.0/24` (CIDR notation)
- `10.0.0.5` (private RFC1918)
- `203.0.113.0/24` (CIDR notation, even public)

### 3. Sensitive Field Names

**Fields flagged by name**:
- `card_number` — Primary card number field
- `full_ip` — Complete IP address (distinguish from `ip_subnet` or `ip_range`)
- `ssn` — US Social Security Number field

**Field detection**: Case-insensitive exact or substring match in schema column definitions

## Daily Scan Logic

### Execution Schedule

- **Frequency**: Daily at 02:00 UTC (off-peak hours to minimize production impact)
- **Lookback window**: Previous 24 hours of data (CURRENT_DATE - 1)
- **Timeout**: 1 hour (must complete within SLA)

### Scan Execution Steps

1. **Discover tables** — Query Iceberg metadata catalog for `enriched_transactions` and `fraud_decisions`
2. **Schema inspection** — Retrieve column definitions; flag any fields matching sensitive names (case-insensitive)
3. **Content scan** — For each table:
   - Apply regex pattern matching for PAN, IP address patterns
   - Filter to records created/updated in the past 24 hours
   - Collect matched records with field name and pattern type
4. **Deduplication** — Group matches by (table_name, matched_field, pattern_type, record_sample_hash) to avoid logging duplicate patterns from the same record
5. **Audit logging** — Append PIIAuditRecord entries to `audit.pii_scans` table
6. **Alert** — If any match is detected:
   - Send alert notification to data privacy team Slack channel
   - Include: table name, field name, pattern type, record count, sample hashes
7. **Write protection** — Mark affected Iceberg table as read-only:
   - Set table property `pii_audit_status = HALTED`
   - Log halt reason and timestamp
   - Require manual review and approval to resume writes
8. **Status reporting** — Return scan summary: total records scanned, matches found, tables halted

## PIIAuditRecord Schema

Appended to `audit.pii_scans` table (Iceberg).

```python
@dataclass
class PIIAuditRecord:
    scan_id: str                    # UUID, unique per daily scan
    scan_timestamp: datetime        # UTC timestamp of scan execution
    table_name: str                 # 'enriched_transactions' or 'fraud_decisions'
    matched_field: str              # Column name where PII was found
    matched_pattern: str            # Pattern type: 'PAN', 'IPv4', 'IPv6', 'FIELD_NAME'
    record_sample_hash: str         # SHA-256 hash of matched record (no PII in hash itself)
    action_taken: str               # 'ALERT_SENT', 'TABLE_HALTED', 'LOG_ONLY'
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `scan_id` | UUID | Unique identifier per scan run. Format: `audit-scan-{YYYYMMDD}-{8-char-random}` |
| `scan_timestamp` | TIMESTAMP(3) UTC | Wall-clock time when scan started |
| `table_name` | VARCHAR(255) | Iceberg table scanned |
| `matched_field` | VARCHAR(255) | Column containing PII (e.g., `card_number`, `customer_ip`) |
| `matched_pattern` | VARCHAR(32) | One of: `PAN`, `IPv4`, `IPv6`, `FIELD_NAME` |
| `record_sample_hash` | VARCHAR(64) | SHA-256 of record content (deterministic, reproducible) |
| `action_taken` | VARCHAR(32) | One of: `ALERT_SENT`, `TABLE_HALTED`, `LOG_ONLY` |

## Alert Notification

When PII is detected, the system sends a structured notification to the data privacy team.

### Slack Alert Format

```
Channel: #data-privacy-alerts
Title: PII Detected in Production Iceberg Table
Content:
  - Scan ID: {scan_id}
  - Table: {table_name}
  - Pattern(s) matched: {matched_patterns} (count: {match_count})
  - Affected fields: {field_list}
  - Record count: {total_scanned}
  - Matches found: {total_matches}
  - Status: TABLE_HALTED (writes disabled)
  - Action required: Review records, approve remediation, re-enable table
  - Runbook: [Link to internal wiki]
```

### Email Notification (Optional)

- Recipients: data-privacy@company.internal, security-ops@company.internal
- Subject: `[URGENT] PII Audit Alert: {table_name}`
- Includes audit log download link (CSV, hashed records only)

## Table Write Halting

### Mechanism

When PII is detected, the write protection workflow:

1. **Iceberg table property**: Set `pii_audit_status = HALTED`
2. **Application-level guard**: All write requests check this property before executing
3. **Error response**: Return `403 Forbidden: Table halted due to PII audit — contact security@`
4. **Logging**: Log all attempted writes during halt period with requester identity

### Resume Procedure

1. **Data Privacy Review**: Team reviews flagged records and audit log
2. **Remediation**: If PII is confirmed:
   - Purge affected records or mask PII fields
   - Execute data anonymization pipeline
   - Archive original records to encrypted backup
3. **Approval**: Security team approves table re-enablement via signature
4. **Re-enable**: Set `pii_audit_status = ACTIVE`, increment `audit_version`
5. **Verification**: Run full table scan again to confirm no PII remains

## Implementation Considerations

### Performance & Scale

- **Scan strategy**: Partition table scan by date to parallelize across workers
- **Batch size**: Process records in chunks of 10,000 to manage memory
- **Sampling**: For tables >100M rows, use statistical sampling (95% confidence, 5% margin) after full schema scan
- **Caching**: Cache schema metadata for 24 hours to avoid repeated catalog queries

### Data Privacy

- **No PII logging**: Never log matched PII values; use cryptographic hashes only
- **Audit log encryption**: `audit.pii_scans` table is encrypted at rest
- **Access control**: Only data privacy and security teams can read audit logs
- **Retention**: Audit records retained for 7 years (regulatory requirement)

### Regex Optimization

- **Compile once**: Pre-compile all regex patterns at startup, reuse across records
- **Short-circuit**: Stop scanning a record after first PII match to reduce overhead
- **Anchored patterns**: Use word boundaries and anchors to reduce false positives

### False Positive Handling

- **Test data**: Maintain a test data registry (e.g., `test_visa_4532123456789012`) to mark as known non-production
- **Whitelisting**: Allow data privacy team to whitelist specific record hashes as verified non-sensitive
- **Manual review**: All matches sent to data privacy team (not auto-remediated)

## Blocking Condition

**This architecture is blocked by TD-007**: Iceberg sinks must be implemented before PII audit can be deployed. Required for:
- Reliable daily write of PIIAuditRecord to `audit.pii_scans`
- Transactional consistency when halting table writes
- Backup/recovery of audit logs

## Implementation Roadmap

1. **Phase 1 (blocked by TD-007)**: Deploy Iceberg sinks for audit tables
2. **Phase 2**: Implement regex pattern matching and field name detection
3. **Phase 3**: Build daily scan orchestration and alert notifications
4. **Phase 4**: Implement table write halting and recovery procedures
5. **Phase 5**: Add statistical sampling for large tables and performance tuning
6. **Phase 6**: Integrate with data privacy team workflows and SIEM

## Testing Strategy

### Unit Tests

- Regex pattern matching (PAN, IPv4/IPv6 patterns) with known test vectors
- Field name detection (case-insensitive matching)
- Record hashing (deterministic, reproducible)

### Integration Tests

- End-to-end scan against test tables with known PII patterns
- Alert notification delivery and format validation
- Table write halting and resumption workflows
- Audit log insertion and query verification

### Production Safeguards

- Dry-run mode: Scan and log without sending alerts or halting tables
- Gradual rollout: Enable for `enriched_transactions` first, then `fraud_decisions`
- Monitoring: Track scan duration, match rate, false positive ratio weekly

## Related Documents

- **FR-005** — Fraud Detection Functional Requirements
- **TD-007** — Iceberg Sinks Technical Debt
- **Data Privacy Policy** — Organization PII handling standards
- **GDPR Compliance Guidelines** — Regulatory framework for PII audit
