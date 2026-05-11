# Security Test Environment (TB-001)

This document describes the comprehensive security test environment for FraudStream Phase 0.

## Overview

The security test environment provides:
1. **TLS-enabled Kafka testcontainer fixture** for encrypted broker connections
2. **Test certificates** (CA, server, client) for mTLS testing
3. **SecretProvider test harness** with EnvSecretProvider and mock VaultSecretProvider
4. **Management API auth test fixtures** for API key validation and rate limiting

## Components

### 1. TLS-Enabled Kafka Testcontainer

**File:** `conftest_security.py`

Provides `KafkaTLSContainer` - a testcontainer with SSL/TLS encryption:

```python
@pytest.fixture(scope="session")
def kafka_tls_container() -> KafkaTLSContainer:
    # TLS-enabled Kafka on SSL port 9093
    ...

@pytest.fixture
def kafka_ssl_producer() -> Producer:
    # Pre-configured SSL producer
    ...

@pytest.fixture
def kafka_ssl_consumer() -> Consumer:
    # Pre-configured SSL consumer
    ...
```

**Features:**
- Mutual TLS (mTLS) support
- TLS 1.2 enforcement
- Certificate-based authentication
- End-to-end encrypted messaging

**Usage Example:**
```python
def test_ssl_producer_connects(kafka_ssl_producer: Producer) -> None:
    metadata = kafka_ssl_producer.list_topics(timeout=10)
    assert metadata is not None
```

### 2. Test Certificates

**Directory:** `tests/fixtures/tls/`

| File | Purpose |
|------|---------|
| `ca-cert.pem` | Test CA certificate (trust anchor) |
| `ca-key.pem` | Test CA private key |
| `server-cert.pem` | Kafka broker certificate |
| `server-key.pem` | Kafka broker private key |
| `client-cert.pem` | Client certificate for mTLS |
| `client-key.pem` | Client private key |

**Fixture Access:**
```python
def test_ca_certificate_exists(tls_certificates: dict[str, Path]) -> None:
    assert tls_certificates["ca_cert"].exists()
```

### 3. SecretProvider Test Harness

**Protocol:** `SecretProvider` - abstraction for secret retrieval

**Implementations:**

#### EnvSecretProvider
Reads secrets from environment variables:
```python
provider = EnvSecretProvider(prefix="FRAUDSTREAM_")
secret = provider.get_secret("API_KEY")  # Reads FRAUDSTREAM_API_KEY
```

#### VaultSecretProvider
Reads secrets from HashiCorp Vault:
```python
provider = VaultSecretProvider(
    vault_addr="https://vault.example.com",
    vault_token="...",
)
secret = provider.get_secret("fraudstream/kafka", "password")
```

**Test Harness:**
```python
def test_secret_providers(secret_provider_harness: SecretProviderHarness) -> None:
    secret_provider_harness.test_env_provider_reads_secret(monkeypatch)
    secret_provider_harness.test_vault_provider_reads_secret()
```

### 4. Management API Auth Fixtures

**API Key Validation:**
```python
def test_api_key_validation(auth_harness: ManagementAPIAuthHarness) -> None:
    result = auth_harness.validate_request(api_key="valid-key")
    assert result["valid"] is True
```

**Rate Limiting:**
```python
def test_rate_limit_enforced(strict_auth_harness: ManagementAPIAuthHarness) -> None:
    # Send requests up to limit
    for i in range(5):
        strict_auth_harness.validate_request(api_key="key", client_ip="1.2.3.4")
    
    # Next request should be rate limited (429)
    result = strict_auth_harness.validate_request(api_key="key", client_ip="1.2.3.4")
    assert result["rate_limited"] is True
    assert result["status_code"] == 429
```

## Running Tests

### All Security Tests
```bash
cd /Users/lucasmadridbbva/Desktop/repos/streaming/fraudstream
pytest tests/integration/test_security_tls_kafka.py -v
```

### Integration Tests Only (requires Docker)
```bash
pytest tests/integration/test_security_tls_kafka.py -m integration -v
```

### Specific Test Categories
```bash
# TLS tests only
pytest tests/integration/test_security_tls_kafka.py::TestTLSKafkaConnection -v

# SecretProvider tests
pytest tests/integration/test_security_tls_kafka.py::TestEnvSecretProvider -v

# Auth tests
pytest tests/integration/test_security_tls_kafka.py::TestManagementAPIKeyValidation -v

# Rate limiting tests
pytest tests/integration/test_security_tls_kafka.py::TestManagementAPIRateLimiting -v
```

## Dependencies

Ensure testcontainers is installed:
```bash
pip install testcontainers[kafka]>=4.5
```

Or with uv:
```bash
uv pip install -e ".[dev]"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Security Test Environment                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ TLS Kafka    │  │ SecretProvider│  │ Management API Auth  │  │
│  │ Container    │  │ Test Harness  │  │ Test Harness         │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│         │                 │                       │              │
│         ▼                 ▼                       ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ SSL/mTLS     │  │ EnvSecret    │  │ API Key Validation   │  │
│  │ SASL/SCRAM   │  │ VaultSecret  │  │ Rate Limiting        │  │
│  │              │  │ Mock         │  │                      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐│
│  │ Test Certificates (tests/fixtures/tls/)                    ││
│  │ - ca-cert.pem, server-cert.pem, client-cert.pem           ││
│  └────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Test Coverage

| Component | Tests | Markers |
|-----------|-------|---------|
| TLS Connection | `test_ssl_producer_connects`, `test_ssl_consumer_connects` | `integration` |
| SASL Auth | `test_sasl_producer_authenticates`, `test_sasl_invalid_credentials_fail` | `integration` |
| Certificates | `test_ca_certificate_exists`, `test_server_cert_signed_by_ca` | - |
| EnvSecretProvider | `test_get_secret_from_env`, `test_prefixed_provider` | - |
| VaultSecretProvider | `test_get_secret_from_vault`, `test_missing_path_returns_none` | - |
| API Key Auth | `test_valid_api_key_accepted`, `test_invalid_api_key_rejected` | - |
| Rate Limiting | `test_rate_limit_enforced`, `test_rate_limit_per_client_ip` | - |

## Notes

- **TLS 1.2**: Enforced for all SSL connections (configurable in fixtures)
- **Testcontainers**: Requires Docker to be running
- **Ryuk Disabled**: Required for macOS Docker Desktop compatibility
- **Mock Vault**: Uses MagicMock when hvac library is not available
- **Certificates**: Auto-generated for testing (not production-grade)
