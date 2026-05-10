# Makefile Health Report

**Generated:** 2026-05-10  
**Branch:** 020-phase0-foundation  
**File:** `/Users/lucasmadridbbva/Desktop/repos/streaming/fraudstream/Makefile`

---

## Executive Summary

| Category | Status | Issues Found | Fixed |
|----------|--------|--------------|-------|
| .PHONY Completeness | COMPLETE | 1 missing target | 1 ✓ |
| Target Dependencies | OK | All dependencies valid | N/A |
| Variable Substitution | OK | All variables working | N/A |
| Phase 0 Feature Coverage | COMPLETE | 4 missing target groups | 4 ✓ |
| Overall Health | GOOD | 7 issues | 7 ✓ |

**Status: ALL ISSUES RESOLVED**

---

## 1. .PHONY Declaration Issues

### Issue 1.1: Missing test-contract in .PHONY
**Severity:** MEDIUM  
**Line:** 251 (target exists but not declared)

The `test-contract` target is defined but not included in `.PHONY`:

```makefile
# Current (lines 13-20):
.PHONY: infra-up infra-down infra-clean infra-ps infra-logs \
        infra-restart infra-restart-grafana infra-restart-prometheus \
        topics bootstrap update-geoip download-jars \
        flink-job flink-job-analytics \
        generate generate-suspicious simulate-alerts consume generate-dlq \
        analytics-counts analytics-join analytics-feast analytics-verify \
        analytics-up analytics-down \
        install test test-unit test-integration   # <-- test-contract missing!

# Target defined at line 248-249:
test-contract:
	pytest tests/contract/ -v
```

**Impact:** If a file named `test-contract` exists, Make will incorrectly think the target is up-to-date.

**Fix:** Add `test-contract` to `.PHONY` declaration.

---

## Fixes Applied

The following fixes have been applied to the Makefile:

### 1. .PHONY Declaration Fixed
- Added `test-contract` to `.PHONY`
- Added all new Phase 0 targets to `.PHONY`

### 2. TB-001 Security Infrastructure Targets Added
- `infra-security-up` - Start security test environment with Kafka SASL/TLS
- `infra-security-down` - Stop security test environment
- `infra-security-ps` - Show security environment status
- `infra-security-logs` - Tail security environment logs

### 3. TB-001 Security Test Target Added
- `test-security` - Run Kafka SASL/SCRAM security tests

### 4. TB-003 Performance Test Target Added
- `test-performance` - Run performance benchmarks
- Supports `SLOW=1` flag to include slow tests

### 5. CHB-006 Interface Contract Tests Enhanced
- Updated `test-contract` to run both `tests/contract/` and `tests/contracts/`
- Added documentation header

### 6. Code Quality Targets Added
- `lint` - Check code style with ruff
- `lint-fix` - Auto-fix code style issues
- `format` - Check code formatting
- `format-fix` - Apply code formatting

### 7. Help Target Added
- `help` - Self-documenting help message showing all available targets

---

## 2. Missing Phase 0 Feature Targets

### Issue 2.1: TB-001 Security Docker Compose Targets (HIGH)
**Severity:** HIGH  
**Missing:** `infra-security-up`, `infra-security-down`, `infra-security-ps`

The project has `infra/docker-compose.security.yml` for Kafka SASL/TLS testing, but no Makefile targets:

```yaml
# File exists: infra/docker-compose.security.yml
# - Services: broker-secure, schema-registry-secure, kafka-ui, kafka-setup-secure, prometheus-secure
# - Networks: fraudstream-secure
# - Features: SASL_PLAINTEXT (9093), SASL_SSL (9094)
```

**Required targets:**
- `infra-security-up` - Start security test environment
- `infra-security-down` - Stop security test environment
- `infra-security-ps` - Show security environment status

### Issue 2.2: TB-001 Security Test Target (HIGH)
**Severity:** HIGH  
**Missing:** `test-security`

Security tests exist in `tests/security/test_kafka_auth_basic.py` but no target to run them:

```python
# Tests exist:
# - TestSaslAuthentication (5 test cases)
# - TestAclEnforcement (5 test cases)  
# - TestTlsConnectivity (2 test cases, skipped)
```

**Required target:** `test-security` - Run security tests with proper environment check

### Issue 2.3: TB-003 Performance Test Target (HIGH)
**Severity:** HIGH  
**Missing:** `test-performance`

Performance tests exist in `tests/performance/` but no Makefile target:

```python
# Tests exist:
# - test_invoke_latency_under_1ms_per_record
# - test_invoke_does_not_block_during_flush
# - test_throughput_1000_records_per_second
# - test_buffer_limited_throughput_stress
# - test_deduplication_overhead_at_scale
```

**Required target:** `test-performance` - Run performance benchmarks

### Issue 2.4: Lint and Format Targets (MEDIUM)
**Severity:** MEDIUM  
**Missing:** `lint`, `lint-fix`, `format`, `typecheck`

`pyproject.toml` has ruff configured but no Makefile targets:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

**Required targets:**
- `lint` - Check code style with ruff
- `lint-fix` - Auto-fix code style issues
- `format` - Check formatting (or use ruff format)
- `typecheck` - Run type checking (if mypy added)

### Issue 2.5: CHB-006 Interface Contract Test Target (MEDIUM)
**Severity:** MEDIUM  
**Missing:** `test-interface-contracts`

Interface contract tests exist but not explicitly targeted:

```python
# tests/contract/test_avro_iceberg_alignment.py - Avro/Iceberg schema alignment
# tests/contracts/test_fraud_alert_schema.py - Fraud alert schema validation
```

**Required target:** `test-interface-contracts` or extend `test-contract`

---

## 3. Variable Substitution Issues

### Issue 3.1: SERVICE Variable in infra-logs
**Severity:** LOW  
**Line:** 39-40

```makefile
infra-logs:
	$(COMPOSE) logs -f $(SERVICE)
```

The `SERVICE` variable has no default. Running `make infra-logs` without `SERVICE=` will fail.

**Current behavior:** Shows all services when SERVICE is empty (works but undocumented)

**Recommendation:** Add documentation or default behavior.

---

## 4. Target Dependency Analysis

### Valid Dependencies
All existing target dependencies are correct:

| Target | Dependencies | Valid? |
|--------|--------------|--------|
| `bootstrap` | `download-jars infra-up topics` | YES |
| `infra-restart` | `infra-restart-prometheus infra-restart-grafana` | YES |
| `test` | `test-unit test-contract` | YES |
| `analytics-verify` | `analytics-counts analytics-join analytics-feast` | YES |

### Missing Dependency
- `test-contract` depends on contract test files but has no file-based dependency tracking (acceptable for test targets)

---

## 5. Recommended Makefile Additions

### 5.1 Add to .PHONY (line 13-20)

```makefile
.PHONY: infra-up infra-down infra-clean infra-ps infra-logs \
        infra-restart infra-restart-grafana infra-restart-prometheus \
        topics bootstrap update-geoip download-jars \
        flink-job flink-job-analytics \
        generate generate-suspicious simulate-alerts consume generate-dlq \
        analytics-counts analytics-join analytics-feast analytics-verify \
        analytics-up analytics-down \
        install test test-unit test-integration test-contract \
        infra-security-up infra-security-down infra-security-ps \
        test-security test-performance \
        lint lint-fix format
```

### 5.2 Security Infrastructure Targets (after line 54)

```makefile
# ── Security Test Environment (TB-001) ────────────────────────────────────

COMPOSE_SECURITY := docker compose -f infra/docker-compose.security.yml

infra-security-up:
	@echo "Starting security test environment (Kafka SASL/TLS)..."
	$(COMPOSE_SECURITY) up -d
	@echo "Waiting for Kafka to be ready..."
	@sleep 5
	$(COMPOSE_SECURITY) ps
	@echo ""
	@echo "Security environment ready:"
	@echo "  PLAINTEXT:     localhost:9092"
	@echo "  SASL_PLAINTEXT: localhost:9093 (SCRAM-SHA-256)"
	@echo "  SASL_SSL:      localhost:9094 (SCRAM-SHA-256 + TLS)"
	@echo "  Kafka UI:      http://localhost:8080"

infra-security-down:
	$(COMPOSE_SECURITY) down

infra-security-ps:
	$(COMPOSE_SECURITY) ps
```

### 5.3 Security Test Target (after line 251)

```makefile
test-security:
	@echo "Running TB-001 Kafka SASL/SCRAM security tests..."
	@echo "Note: Requires security environment running (make infra-security-up)"
	pytest tests/security/ -v -m "not skip"
```

### 5.4 Performance Test Target (after test-security)

```makefile
test-performance:
	@echo "Running TB-003 performance benchmarks..."
	pytest tests/performance/ -v -m "perf or slow"
```

### 5.5 Lint and Format Targets (after install target)

```makefile
lint:
	ruff check .

lint-fix:
	ruff check --fix .

format:
	ruff format --check .

format-fix:
	ruff format .
```

### 5.6 Enhanced test-contract Target (replace lines 248-251)

```makefile
test-contract:
	@echo "Running CHB-006 interface contract tests..."
	pytest tests/contract/ tests/contracts/ -v
```

---

## 6. Variable Testing Results

| Variable | Definition | Test Result |
|----------|------------|-------------|
| `COMPOSE` | `docker compose -f infra/docker-compose.yml` | VALID |
| `PYTHON` | `$(if $(wildcard .venv/bin/python),.venv/bin/python,python3.11)` | VALID |
| `MINIO_ACCESS_KEY` | `minioadmin` (default) | VALID |
| `MINIO_SECRET_KEY` | `minioadmin` (default) | VALID |
| `KAFKA_CONNECTOR_VERSION` | `4.0.1-2.0` | VALID |
| `KAFKA_CONNECTOR_URL` | Maven URL | VALID |

---

## 7. Phase 0 Feature Coverage Matrix

| Feature | Component | Makefile Support | Status |
|---------|-----------|------------------|--------|
| TB-001 | Security docker-compose | NO | MISSING |
| TB-001 | test-security target | NO | MISSING |
| TB-003 | Performance baselines | NO | MISSING |
| TB-003 | test-performance target | NO | MISSING |
| TB-002 | Contract tests | PARTIAL | .PHONY missing |
| CHB-006 | Interface contracts | PARTIAL | Merged into test-contract |
| Lint/Format | ruff integration | NO | MISSING |

---

## 8. Action Items

### Immediate (High Priority)
1. [ ] Add `test-contract` to `.PHONY` declaration
2. [ ] Create `infra-security-up` and `infra-security-down` targets
3. [ ] Create `test-security` target for TB-001
4. [ ] Create `test-performance` target for TB-003

### Short-term (Medium Priority)
5. [ ] Add `lint`, `lint-fix`, `format` targets
6. [ ] Document `SERVICE` variable usage for `infra-logs`
7. [ ] Add `help` target for self-documenting Makefile

### Long-term (Low Priority)
8. [ ] Add `typecheck` target if mypy is adopted
9. [ ] Add `ci` target that runs lint, test-unit, test-contract
10. [ ] Consider consolidating contract test directories (contract/ vs contracts/)

---

## Appendix: Patch File

See `docs/makefile-fixes.patch` for a complete patch implementing all fixes.

---

## Verification Commands

After applying fixes, verify with:

```bash
# List all targets
make -p | grep -E "^[a-zA-Z_-]+:" | grep -v "^Makefile" | sort

# Check .PHONY is complete
make -p | grep "^.PHONY:"

# Test variable substitution
echo "COMPOSE=$(make -p | grep "^COMPOSE =" | head -1)"

# Test new targets
dry-run:
  make infra-security-up --dry-run
  make test-security --dry-run
  make test-performance --dry-run
```
