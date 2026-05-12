# Research: CI Green Recovery

**Feature**: 023-fix-ci-green  
**Created**: 2026-05-11  
**Purpose**: Document technical decisions and alternatives considered

---

## Decision Log

### Decision 1: Class Name Fix Strategy

**Decision**: Update test imports from `DLQKafkaProducer` to `ProcessingDLQSink`

**Context**: The DLQ sink class was renamed from `DLQKafkaProducer` to `ProcessingDLQSink` in a previous refactor, but test files still reference the old name.

**Alternatives Considered**:

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| A. Update test references | Matches actual code; no tech debt | Requires file edits | ✓ **CHOSEN** |
| B. Create alias/shim | No test file changes | Adds permanent tech debt; confusing | ✗ Rejected |
| C. Revert class rename | No test changes | Loses better naming; breaks other code | ✗ Rejected |

**Rationale**: Option A is the cleanest solution. The test files should reference the actual class names.

---

### Decision 2: TLS Certificate Generation

**Decision**: Use `cryptography` library to generate certificates at test runtime

**Context**: Hardcoded TLS certificates in test fixtures trigger GitGuardian security alerts.

**Alternatives Considered**:

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| A. Runtime generation with `cryptography` | No secrets in repo; clean scans | Adds ~50ms per test module | ✓ **CHOSEN** |
| B. Base64-encoded env vars | No hardcoded files | Still secrets; env management burden | ✗ Rejected |
| C. .gitignore the cert files | Simple | Secrets still in git history | ✗ Rejected |
| D. Use pre-generated test certs from system | No generation overhead | System-dependent; may not exist in CI | ✗ Rejected |

**Rationale**: Option A eliminates secrets completely while being deterministic and fast enough for test use.

**Implementation Pattern**:

```python
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime

@pytest.fixture
def tls_certs(tmp_path):
    """Generate temporary TLS certificates for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    cert = x509.CertificateBuilder().subject_name(
        x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test")])
    ).issuer_name(
        x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test")])
    ).public_key(key.public_key()).serial_number(
        x509.random_serial_number()
    ).not_valid_before(datetime.datetime.utcnow()).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("localhost")]),
        critical=False
    ).sign(key, hashes.SHA256())
    
    cert_path = tmp_path / "test.crt"
    key_path = tmp_path / "test.key"
    
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ))
    
    return {"cert": str(cert_path), "key": str(key_path)}
```

---

### Decision 3: Linting Fix Strategy

**Decision**: Use `ruff check --fix` followed by `ruff format`

**Context**: The codebase has accumulated linting violations: E402, I001, UP037, F401, E501

**Alternatives Considered**:

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| A. `ruff check --fix` + `ruff format` | Automated; deterministic; fast | May need manual review | ✓ **CHOSEN** |
| B. Manual fixes | Precise control | Slow; error-prone | ✗ Rejected |
| C. Disable rules in pyproject.toml | Quick "fix" | Hides real issues; tech debt | ✗ Rejected |

**Rationale**: Option A is efficient and reliable. Ruff's auto-fixes are conservative and safe.

**Rule Breakdown**:

| Rule | Description | Fix Strategy |
|------|-------------|--------------|
| E402 | Module-level import not at top | Move imports to top |
| I001 | Import block is unsorted | Sort imports (isort-compatible) |
| UP037 | Deprecated `Union[X, Y]` vs `X \| Y` | Modernize to `X \| Y` |
| F401 | Unused import | Remove import |
| E501 | Line too long | Wrap lines |

---

### Decision 4: Test Flakiness Mitigation

**Decision**: Identify and fix root causes (race conditions, improper isolation)

**Context**: Security and processing tests show intermittent failures, likely due to shared state or timing issues.

**Alternatives Considered**:

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| A. Fix root cause | Permanent solution | Requires investigation | ✓ **CHOSEN** |
| B. Add retries/sleeps | Quick masking | Hides real bugs; slower tests | ✗ Rejected |
| C. Mark as flaky with pytest-rerunfailures | Easy | Doesn't fix underlying issue | ✗ Rejected |
| D. Skip flaky tests | Fastest | Loses coverage; bugs remain | ✗ Rejected |

**Rationale**: Option A is the only sustainable solution.

**Common Flakiness Causes**:

1. **Shared state between tests**: Use `tmp_path` fixture for file-based state
2. **Non-deterministic timing**: Use `pytest-asyncio` with proper event loop cleanup
3. **Resource leaks**: Ensure `close()` is called on all resources
4. **Database/testcontainers state**: Reset state in fixture teardown

---

## Dependencies

### New Runtime Dependencies

None — this is a fix to existing test infrastructure.

### New Development Dependencies

| Package | Purpose | Version |
|---------|---------|---------|
| cryptography | Runtime TLS cert generation | ^41.0.0 |

---

## Constraints and Limitations

1. **Python 3.9 compatibility**: Type union syntax `X \| Y` requires `from __future__ import annotations` in Python < 3.10
2. **CI environment**: Must work in GitHub Actions Ubuntu runners
3. **Coverage gate**: Changes must not reduce coverage below 80%
4. **No production code changes**: Only tests and fixtures may be modified

---

## References

- Ruff documentation: https://docs.astral.sh/ruff/
- Cryptography library: https://cryptography.io/
- pytest fixtures: https://docs.pytest.org/en/latest/fixture.html
