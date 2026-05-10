# Test Strategy Critique for DESIGN_V2.md

**Author:** Tester Agent  
**Date:** 2026-05-10  
**Status:** CRITICAL GAPS IDENTIFIED

---

## Executive Summary

The FraudStream project has **significant test gaps** that must be addressed before v2.0. While unit test coverage meets the 80% gate (enforced in Makefile), critical security and integration areas lack automated testing. This critique identifies 4 high-priority gaps and provides specific test requirements.

---

## 1. Test Gaps for P0/P1 Issues

### P0 Issues — Test Coverage Analysis

| ID | Issue | Current Test Coverage | Gap Severity | Required Tests |
|----|-------|----------------------|--------------|----------------|
| **P0-001** | Kafka no auth/encryption (PLAINTEXT) | **NONE** | CRITICAL | SASL/SCRAM auth tests, TLS handshake tests, certificate rotation tests |
| **P0-002** | Iceberg sink in hot path (SRP violation) | PARTIAL (integration tests exist) | MEDIUM | Latency regression tests, operator isolation tests |
| **P0-003** | Silent record drops when table=None | **COVERED** (test_dlq_sink.py) | - | DLQ routing verification tests |
| **P0-004** | Scoring metrics not wrapped in _SafeMetric | **COVERED** (test_safe_metrics.py) | - | SafeMetric graceful degradation tests |
| **P0-005** | AlertKafkaSink missing close() | **COVERED** (test_alert_kafka.py) | - | Resource lifecycle tests, close() idempotency tests |
| **P0-006** | Reverse dependency processing → scoring | PARTIAL (test_kafka_metrics_bridge.py) | MEDIUM | Dependency injection tests, circular dependency detection |

### P1 Issues — Test Coverage Analysis

| ID | Issue | Current Test Coverage | Gap Severity | Required Tests |
|----|-------|----------------------|--------------|----------------|
| **P1-001** | try/except ImportError pattern (5 files) | NONE | MEDIUM | Adapter pattern contract tests, import fallback tests |
| **P1-004** | No PostgreSQL reconnection logic | **COVERED** (test_alert_postgres.py) | - | Retry/backoff tests, connection pool tests |
| **P1-006** | Metrics bridge thread safety | PARTIAL (test_kafka_metrics_bridge.py) | MEDIUM | Concurrent start/stop tests, race condition tests |
| **P1-007** | DLQ Inspector PII masking incomplete | NONE | HIGH | PII masking bypass tests, recursive masking tests |
| **P1-008** | Management API key auth optional | **COVERED** (test_management_api.py) | - | Auth enforcement tests, prod-mode gate tests |

### Critical Test Gaps Identified

1. **P0-001 (Kafka Security)**: ZERO automated tests for Kafka authentication/encryption
2. **P1-007 (PII Masking)**: No tests verifying DLQ Inspector PII masking cannot be bypassed
3. **Chaos Testing**: Manual-only (FAILURE_SCENARIOS.md), no automated chaos tests
4. **Circuit Breaker Chaos**: Tests exist but no automated failure injection

---

## 2. Is 80% Coverage Gate Sufficient?

### Current State

```toml
# pyproject.toml
[tool.coverage.report]
fail_under = 80
show_missing = true
```

```makefile
# Makefile
test-unit:
	pytest tests/unit/ --cov=pipelines/processing --cov=pipelines/scoring --cov-fail-under=80 -v
```

### Assessment: **INSUFFICIENT for Production**

| Aspect | Current | Required | Rationale |
|--------|---------|----------|-----------|
| Unit Coverage | 80% | 80% | Acceptable for business logic |
| Integration Coverage | ~15% | 60% | Critical for stream processing correctness |
| Security Tests | ~5% | 90% | P0-001 is a production blocker |
| Chaos/Failure Tests | 0% (manual only) | 40% | Required for reliability validation |
| End-to-End Coverage | ~10% | 50% | Required for exactly-once semantics |

### Recommendation

**Keep 80% for unit tests** but add **gated integration test requirements**:

```makefile
# Proposed Makefile additions
test-gate: test-unit test-integration test-security test-chaos

test-security:
	pytest tests/security/ -v --tb=short

test-chaos:
	pytest tests/chaos/ -v --tb=line -x
```

---

## 3. Integration Test Requirements

### 3.1 Kafka Auth Integration Tests (P0-001)

**Missing Infrastructure:**
- No testcontainers with SASL/SCRAM enabled
- No TLS certificate generation/rotation tests
- No authentication failure handling tests

**Required Test Suite:**

```python
# tests/integration/test_kafka_security.py (MISSING)

class TestKafkaSASLAuth:
    """P0-001: Kafka SASL/SCRAM authentication tests."""
    
    def test_producer_authenticates_with_sasl_scram(self):
        """Producer must authenticate successfully with valid credentials."""
        pass
    
    def test_producer_rejects_invalid_credentials(self):
        """Producer must fail fast with invalid credentials."""
        pass
    
    def test_consumer_group_authz_enforced(self):
        """Consumer must have ACL permissions for topic and group."""
        pass

class TestKafkaTLS:
    """P0-001: Kafka TLS encryption tests."""
    
    def test_tls_handshake_succeeds_with_valid_cert(self):
        """Connection must establish TLS with valid certs."""
        pass
    
    def test_tls_fails_with_expired_certificate(self):
        """Connection must fail with expired/invalid certs."""
        pass
    
    def test_certificate_rotation_without_downtime(self):
        """Certs can be rotated without restart."""
        pass
```

**Implementation Blocker:**
- Current `docker-compose.yml` has no auth configuration
- Need separate `docker-compose.secure.yml` for integration tests
- Testcontainers kafka module needs SASL_SCRAM security protocol config

### 3.2 Iceberg Sinks Integration Tests

**Current State:**
- `test_iceberg_decisions_sink.py`: 461 lines, comprehensive
- `test_iceberg_enriched_sink.py`: Exists (IcebergEnrichedSink)
- `test_iceberg_sink.py`: Unit tests for sink base class

**Gap Analysis:**

| Test Case | Status | Notes |
|-----------|--------|-------|
| Time-based flush (1s threshold) | **COVERED** | test_iceberg_decisions_sink.py:82 |
| Buffer-size-based flush | **COVERED** | test_iceberg_decisions_sink.py:131 |
| In-batch deduplication | **COVERED** | test_iceberg_decisions_sink.py:183 |
| Circuit breaker opens after 3 failures | **COVERED** | test_iceberg_decisions_sink.py:242 |
| No exception propagation | **COVERED** | test_iceberg_decisions_sink.py:291 |
| Table=None handling (P0-003) | **COVERED** | test_dlq_sink.py |
| MinIO/S3 failure scenarios | **MISSING** | Need storage backend failure tests |
| Schema evolution handling | **MISSING** | Add column/backward compatibility tests |
| Concurrent flush under load | **MISSING** | Race condition tests needed |

**Required Additions:**

```python
# tests/integration/test_iceberg_failure_modes.py (MISSING)

class TestIcebergStorageFailures:
    """Test Iceberg sink behavior when storage backend fails."""
    
    def test_minio_unavailable_routes_to_dlq(self):
        """When MinIO is down, records must go to DLQ."""
        pass
    
    def test_partial_write_recovery(self):
        """Partial writes must be retryable without duplicates."""
        pass
    
    def test_schema_mismatch_handling(self):
        """Schema drift must be detected and handled."""
        pass
```

---

## 4. Chaos Testing Needs

### Current State: Manual Only

From `tests/chaos/FAILURE_SCENARIOS.md`:
- **CHAOS-001**: Flink TaskManager failure (manual)
- **CHAOS-002**: ML Serving outage / circuit breaker (manual)
- **CHAOS-003**: Kafka broker unavailability (manual)

**Document States:**
> "These scenarios are **manual-only** in this version. No automated chaos injection framework is integrated."

### Required Automated Chaos Tests

#### 4.1 Circuit Breaker Verification (Critical for P0-002, Wave 2)

**Current Tests:** `test_circuit_breaker.py` (393 lines) — Unit/integration only

**Missing: Automated Failure Injection**

```python
# tests/chaos/test_circuit_breaker_chaos.py (MISSING)

class TestCircuitBreakerChaos:
    """016-circuit-breaker-hardening chaos tests."""
    
    @pytest.mark.chaos
    def test_ml_service_outage_triggers_fallback(self):
        """CHAOS-002: ML service outage -> circuit opens -> fallback scoring.
        
        Metrics to verify:
        - ml_circuit_breaker_state{state="open"} == 1
        - ml_fallback_decisions_total increments
        - scoring_latency_ms p99 < 100ms (fallback mode)
        """
        pass
    
    @pytest.mark.chaos  
    def test_circuit_half_open_recovery(self):
        """Circuit transitions from OPEN -> HALF_OPEN -> CLOSED.
        
        Steps:
        1. Trip circuit (3 failures)
        2. Wait reset_timeout (30s)
        3. Verify HALF_OPEN state
        4. Successful request closes circuit
        """
        pass
    
    @pytest.mark.chaos
    def test_circuit_breaker_prevents_cascading_failure(self):
        """Circuit breaker must prevent upstream backpressure.
        
        When circuit is open:
        - No connection attempts to ML service
        - Immediate fallback response
        - No thread pool exhaustion
        """
        pass
```

#### 4.2 Network Partition Tests

```python
# tests/chaos/test_network_partition.py (MISSING)

class TestNetworkPartition:
    """Network partition chaos scenarios."""
    
    @pytest.mark.chaos
    def test_kafka_partition_does_not_cause_data_loss(self):
        """CHAOS-003: Kafka broker partitioned -> DLQ routing -> recovery."""
        pass
    
    @pytest.mark.chaos
    def test_iceberg_catalog_partition_handling(self):
        """Catalog service partition -> graceful degradation."""
        pass
```

#### 4.3 Required Chaos Testing Infrastructure

| Component | Current | Required |
|-----------|---------|----------|
| Chaos framework | None | Chaos Toolkit or Gremlin |
| Synthetic traffic generator | None | Continuous transaction generator |
| Automated metric validation | None | Prometheus query assertions |
| Failure injection hooks | None | Docker/network-level controls |
| Rollback automation | None | Automatic environment restore |

---

## 5. Specific Recommendations

### Immediate Actions (Week 1)

1. **Create `tests/security/test_kafka_auth.py`**
   - Blocked by: P0-001 implementation (SASL/SCRAM config)
   - Priority: CRITICAL (production blocker)

2. **Create `tests/chaos/test_circuit_breaker_chaos.py`**
   - Blocked by: Chaos toolkit integration
   - Priority: HIGH (Wave 2 requirement)

3. **Extend `tests/unit/scoring/test_management_api.py`**
   - Add: P1-008 prod-mode enforcement tests
   - Priority: MEDIUM

### Short-term (Weeks 2-4)

4. **Create `tests/security/test_pii_masking.py`**
   - Test P1-007: DLQ Inspector masking cannot be bypassed
   - Test recursive masking of nested PII fields

5. **Extend `tests/integration/test_iceberg_*.py`**
   - Add MinIO failure scenario tests
   - Add schema evolution tests

6. **Create `tests/contract/test_schema_contracts.py`**
   - Verify Avro schema compatibility
   - Test JSON -> Avro migration (CHB-001)

### CI/CD Integration

```yaml
# .github/workflows/test.yml additions
jobs:
  security-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Kafka Security Tests
        run: pytest tests/security/ -v
        env:
          KAFKA_SECURITY_ENABLED: true
  
  chaos-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Chaos Tests
        run: pytest tests/chaos/ -v --maxfail=1
        timeout-minutes: 30
```

---

## 6. Summary Matrix

| Requirement | Status | Risk Level | Effort |
|-------------|--------|------------|--------|
| P0-001 Kafka auth tests | **MISSING** | CRITICAL | Medium |
| P0-002 Iceberg hot path tests | PARTIAL | MEDIUM | Medium |
| P0-003 Silent drop tests | **COVERED** | - | - |
| P0-004 SafeMetric tests | **COVERED** | - | - |
| P0-005 AlertKafkaSink.close tests | **COVERED** | - | - |
| P0-006 Reverse dependency tests | PARTIAL | MEDIUM | Medium |
| P1-007 PII masking tests | **MISSING** | HIGH | Small |
| Circuit breaker chaos tests | **MISSING** | HIGH | Large |
| Kafka broker chaos tests | **MISSING** | MEDIUM | Large |
| 80% coverage gate | **MET** | - | - |
| Integration test coverage | **INSUFFICIENT** | HIGH | Large |

---

## 7. Conclusion

The **80% unit test coverage gate is necessary but NOT sufficient** for production deployment of FraudStream v2.0. The following must be addressed:

1. **P0-001 (Kafka Security)** has **ZERO automated tests** — this is a production blocker
2. **Chaos testing is manual-only** — automated resilience validation is required for Wave 2
3. **Circuit breaker verification** needs automated failure injection
4. **PII masking bypass tests** are missing for P1-007

**Recommendation:** Add a second test gate requiring:
- Minimum 60% integration test coverage for critical paths
- Automated chaos tests for circuit breaker and Kafka failures  
- Security test suite for authentication/authorization
- PII masking verification that cannot be bypassed

---

**Document Owner:** Testing Team  
**Next Review:** 2026-05-17
