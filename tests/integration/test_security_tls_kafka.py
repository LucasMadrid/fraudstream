"""
TB-001: TLS Kafka Connection Tests

Example tests demonstrating TLS-enabled Kafka connections and security fixtures.
These tests validate:
- TLS/SSL encrypted connections to Kafka
- mTLS (mutual TLS) authentication
- SASL/SCRAM authentication over TLS
- Certificate validation

Usage:
  pytest tests/integration/test_security_tls_kafka.py -v
  pytest tests/integration/test_security_tls_kafka.py -m integration -v
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Producer

    from tests.integration.conftest_security import (
        APIKeyConfig,
        EnvSecretProvider,
        KafkaSASLContainer,
        KafkaTLSConfig,
        KafkaTLSContainer,
        ManagementAPIAuthHarness,
        RateLimitConfig,
        SecretProviderHarness,
        VaultSecretProvider,
    )


# =============================================================================
# TLS Connection Tests
# =============================================================================


@pytest.mark.integration
class TestTLSKafkaConnection:
    """Test TLS-encrypted connections to Kafka."""

    def test_tls_container_starts(self, kafka_tls_container: KafkaTLSContainer) -> None:
        """Verify TLS-enabled Kafka container starts successfully."""
        bootstrap = kafka_tls_container.get_ssl_bootstrap_server()
        assert bootstrap is not None
        assert ":" in bootstrap  # host:port format

    def test_tls_config_provided(
        self,
        kafka_tls_config: KafkaTLSConfig,
        tls_certificates: dict[str, Path],
    ) -> None:
        """Verify TLS configuration includes certificate paths."""
        assert kafka_tls_config.security_protocol == "SSL"
        assert kafka_tls_config.ssl_ca_location == str(tls_certificates["ca_cert"])
        assert kafka_tls_config.ssl_certificate_location == str(tls_certificates["client_cert"])
        assert kafka_tls_config.ssl_key_location == str(tls_certificates["client_key"])

    def test_ssl_producer_connects(
        self,
        kafka_ssl_producer: Producer,
        kafka_tls_container: KafkaTLSContainer,
    ) -> None:
        """Test that SSL producer can connect to TLS Kafka."""
        # List topics to verify connection
        metadata = kafka_ssl_producer.list_topics(timeout=10)
        assert metadata is not None
        assert metadata.orig_broker_name() is not None

    def test_ssl_consumer_connects(
        self,
        kafka_ssl_consumer: Consumer,
        kafka_tls_container: KafkaTLSContainer,
    ) -> None:
        """Test that SSL consumer can connect to TLS Kafka."""
        metadata = kafka_ssl_consumer.list_topics(timeout=10)
        assert metadata is not None
        assert metadata.orig_broker_name() is not None

    def test_ssl_end_to_end_message(
        self,
        kafka_ssl_producer: Producer,
        kafka_ssl_consumer: Consumer,
        kafka_tls_container: KafkaTLSContainer,
    ) -> None:
        """Test producing and consuming a message over TLS.

        Validates the complete encrypted message flow.
        """
        import uuid

        from tests.integration.conftest import wait_for_messages

        topic = f"test-tls-{uuid.uuid4().hex[:8]}"
        test_message = b"Hello, TLS Kafka!"
        test_key = b"test-key"

        try:
            # Create topic by producing to it
            kafka_ssl_producer.produce(topic, key=test_key, value=test_message)
            kafka_ssl_producer.flush(timeout=10)

            # Consume the message
            kafka_ssl_consumer.subscribe([topic])
            messages = wait_for_messages(kafka_ssl_consumer, count=1, timeout_s=30)

            assert len(messages) == 1
            assert messages[0].value() == test_message
            assert messages[0].key() == test_key

        finally:
            kafka_ssl_consumer.unsubscribe()


# =============================================================================
# SASL/SCRAM over TLS Tests
# =============================================================================


@pytest.mark.integration
class TestSASLTLSAuthentication:
    """Test SASL/SCRAM authentication (can be combined with TLS)."""

    def test_sasl_container_starts(self, kafka_sasl_container: KafkaSASLContainer) -> None:
        """Verify SASL-enabled Kafka container starts successfully."""
        bootstrap = kafka_sasl_container.get_bootstrap_server()
        assert bootstrap is not None
        assert ":" in bootstrap

    def test_sasl_producer_authenticates(
        self,
        kafka_sasl_producer: Producer,
    ) -> None:
        """Test that SASL producer authenticates successfully."""
        metadata = kafka_sasl_producer.list_topics(timeout=10)
        assert metadata is not None
        assert metadata.orig_broker_name() is not None

    def test_sasl_consumer_authenticates(
        self,
        kafka_sasl_consumer: Consumer,
    ) -> None:
        """Test that SASL consumer authenticates successfully."""
        metadata = kafka_sasl_consumer.list_topics(timeout=10)
        assert metadata is not None

    def test_sasl_invalid_credentials_fail(
        self,
        kafka_sasl_container: KafkaSASLContainer,
    ) -> None:
        """Test that invalid SASL credentials are rejected."""
        from confluent_kafka import KafkaError, KafkaException, Producer

        # Try to connect with invalid credentials
        sasl_config = kafka_sasl_container.get_sasl_config("admin", "wrong-password")
        config = sasl_config.to_client_config(kafka_sasl_container.get_bootstrap_server())

        with pytest.raises(KafkaException) as exc_info:
            producer = Producer(config)
            producer.list_topics(timeout=5)
            producer.flush(timeout=5)

        # Should get authentication failure
        error = exc_info.value.args[0]
        assert (
            error.code()
            in [
                KafkaError._AUTHENTICATION,
                KafkaError._SSL,
                KafkaError._TRANSPORT,
            ]
            or "authentication" in str(error).lower()
        )


# =============================================================================
# SecretProvider Tests
# =============================================================================


class TestEnvSecretProvider:
    """Test environment variable secret provider."""

    def test_get_secret_from_env(
        self,
        env_secret_provider: EnvSecretProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reading secrets from environment variables."""
        monkeypatch.setenv("KAFKA_PASSWORD", "secret123")

        result = env_secret_provider.get_secret("KAFKA_PASSWORD")
        assert result == "secret123"

    def test_get_secret_missing_returns_none(
        self,
        env_secret_provider: EnvSecretProvider,
    ) -> None:
        """Test that missing secrets return None."""
        result = env_secret_provider.get_secret("NONEXISTENT_VAR")
        assert result is None

    def test_prefixed_provider(
        self,
        prefixed_env_provider: EnvSecretProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that prefix is applied correctly."""
        monkeypatch.setenv("FRAUDSTREAM_API_KEY", "prefixed-value")

        result = prefixed_env_provider.get_secret("API_KEY")
        assert result == "prefixed-value"

    def test_required_secret_raises_when_missing(
        self,
        env_secret_provider: EnvSecretProvider,
    ) -> None:
        """Test that required secrets raise exception when missing."""
        from tests.integration.conftest_security import SecretNotFoundError

        with pytest.raises(SecretNotFoundError) as exc_info:
            env_secret_provider.get_required_secret("MISSING_SECRET")

        assert "MISSING_SECRET" in str(exc_info.value)


class TestVaultSecretProvider:
    """Test HashiCorp Vault secret provider."""

    def test_get_secret_from_vault(
        self,
        mock_vault_secret_provider: VaultSecretProvider,
    ) -> None:
        """Test reading secrets from Vault."""
        result = mock_vault_secret_provider.get_secret("fraudstream/kafka", "username")
        assert result == "fraudapp"

    def test_get_different_secret_keys(
        self,
        mock_vault_secret_provider: VaultSecretProvider,
    ) -> None:
        """Test reading different keys from the same secret path."""
        password = mock_vault_secret_provider.get_secret("fraudstream/kafka", "password")
        assert password == "test-secret"

    def test_missing_path_returns_none(
        self,
        mock_vault_secret_provider: VaultSecretProvider,
    ) -> None:
        """Test that non-existent paths return None."""
        result = mock_vault_secret_provider.get_secret("nonexistent/path", "key")
        assert result is None


class TestSecretProviderHarness:
    """Test the SecretProvider test harness itself."""

    def test_harness_env_tests(
        self,
        secret_provider_harness: SecretProviderHarness,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify harness can test EnvSecretProvider."""
        secret_provider_harness.test_env_provider_reads_secret(monkeypatch)
        secret_provider_harness.test_env_provider_returns_none_for_missing()

    def test_harness_vault_tests(
        self,
        secret_provider_harness: SecretProviderHarness,
    ) -> None:
        """Verify harness can test VaultSecretProvider."""
        secret_provider_harness.test_vault_provider_reads_secret()
        secret_provider_harness.test_vault_provider_returns_none_for_missing()


# =============================================================================
# Management API Auth Tests
# =============================================================================


class TestManagementAPIKeyValidation:
    """Test API key validation for Management API."""

    def test_valid_api_key_accepted(
        self,
        auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that valid API keys are accepted."""
        result = auth_harness.validate_request(api_key="test-key-123")

        assert result["valid"] is True
        assert result["error"] is None
        assert result["status_code"] == 200

    def test_invalid_api_key_rejected(
        self,
        auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that invalid API keys are rejected."""
        result = auth_harness.validate_request(api_key="invalid-key")

        assert result["valid"] is False
        assert result["error"] == "Invalid or missing API key"
        assert result["status_code"] == 401

    def test_missing_api_key_rejected(
        self,
        auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that missing API keys are rejected."""
        result = auth_harness.validate_request(api_key=None)

        assert result["valid"] is False
        assert result["status_code"] == 401

    def test_dev_mode_accepts_any_key(
        self,
        dev_api_key_config: APIKeyConfig,
        rate_limit_config: RateLimitConfig,
    ) -> None:
        """Test that dev mode accepts any non-empty key."""
        from tests.integration.conftest_security import ManagementAPIAuthHarness

        harness = ManagementAPIAuthHarness(dev_api_key_config, rate_limit_config)

        result = harness.validate_request(api_key="any-key-works")
        assert result["valid"] is True

    def test_dev_mode_optional_auth(
        self,
        dev_api_key_config: APIKeyConfig,
        rate_limit_config: RateLimitConfig,
    ) -> None:
        """Test that dev mode allows requests without auth."""
        from tests.integration.conftest_security import ManagementAPIAuthHarness

        harness = ManagementAPIAuthHarness(dev_api_key_config, rate_limit_config)

        result = harness.validate_request(api_key=None)
        assert result["valid"] is True


class TestManagementAPIRateLimiting:
    """Test rate limiting for Management API."""

    def test_requests_under_limit_accepted(
        self,
        auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that requests under the rate limit are accepted."""
        auth_harness.reset_rate_limit("127.0.0.1")

        for i in range(5):
            result = auth_harness.validate_request(
                api_key="test-key-123",
                client_ip="127.0.0.1",
            )
            assert result["rate_limited"] is False, f"Request {i + 1} should not be rate limited"

    def test_rate_limit_enforced(
        self,
        strict_auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that rate limit is enforced after threshold."""
        strict_auth_harness.reset_rate_limit("10.0.0.1")

        # Send requests up to the limit
        for i in range(5):
            result = strict_auth_harness.validate_request(
                api_key="test-key-123",
                client_ip="10.0.0.1",
            )
            assert result["rate_limited"] is False

        # Next request should be rate limited
        result = strict_auth_harness.validate_request(
            api_key="test-key-123",
            client_ip="10.0.0.1",
        )
        assert result["rate_limited"] is True
        assert result["status_code"] == 429
        assert "Rate limit exceeded" in result["error"]

    def test_rate_limit_per_client_ip(
        self,
        strict_auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that rate limits are tracked per client IP."""
        strict_auth_harness.reset_rate_limit()

        # Exhaust limit for one IP
        for i in range(5):
            strict_auth_harness.validate_request(
                api_key="test-key-123",
                client_ip="192.168.1.1",
            )

        # Different IP should not be rate limited
        result = strict_auth_harness.validate_request(
            api_key="test-key-123",
            client_ip="192.168.1.2",
        )
        assert result["rate_limited"] is False

    def test_rate_limit_reset(
        self,
        strict_auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Test that rate limit can be reset."""
        client_ip = "192.168.1.100"

        # Exhaust limit
        for i in range(5):
            strict_auth_harness.validate_request(
                api_key="test-key-123",
                client_ip=client_ip,
            )

        # Verify rate limited
        result = strict_auth_harness.validate_request(
            api_key="test-key-123",
            client_ip=client_ip,
        )
        assert result["rate_limited"] is True

        # Reset and try again
        strict_auth_harness.reset_rate_limit(client_ip)
        result = strict_auth_harness.validate_request(
            api_key="test-key-123",
            client_ip=client_ip,
        )
        assert result["rate_limited"] is False


# =============================================================================
# Certificate Validation Tests
# =============================================================================


class TestCertificateValidation:
    """Test TLS certificate validation."""

    def test_ca_certificate_exists(self, tls_certificates: dict[str, Path]) -> None:
        """Verify CA certificate file exists."""
        assert tls_certificates["ca_cert"].exists()
        assert tls_certificates["ca_cert"].stat().st_size > 0

    def test_server_certificate_exists(self, tls_certificates: dict[str, Path]) -> None:
        """Verify server certificate file exists."""
        assert tls_certificates["server_cert"].exists()
        assert tls_certificates["server_key"].exists()

    def test_client_certificate_exists(self, tls_certificates: dict[str, Path]) -> None:
        """Verify client certificate file exists."""
        assert tls_certificates["client_cert"].exists()
        assert tls_certificates["client_key"].exists()

    def test_ssl_context_creation(
        self,
        ssl_context_tls12: ssl.SSLContext,
    ) -> None:
        """Test SSL context is properly configured."""
        assert ssl_context_tls12.minimum_version == ssl.TLSVersion.TLSv1_2
        assert ssl_context_tls12.maximum_version == ssl.TLSVersion.TLSv1_2
        assert ssl_context_tls12.verify_mode == ssl.CERT_REQUIRED

    def test_server_cert_signed_by_ca(
        self,
        tls_certificates: dict[str, Path],
    ) -> None:
        """Verify server certificate is signed by the test CA."""
        import subprocess

        # Verify certificate chain
        result = subprocess.run(
            [
                "openssl",
                "verify",
                "-CAfile",
                str(tls_certificates["ca_cert"]),
                str(tls_certificates["server_cert"]),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "OK" in result.stdout


# =============================================================================
# Integration Smoke Test
# =============================================================================


@pytest.mark.integration
@pytest.mark.smoke
class TestSecuritySmokeTests:
    """Quick smoke tests for security infrastructure."""

    def test_all_fixtures_available(
        self,
        kafka_tls_container: KafkaTLSContainer,
        kafka_sasl_container: KafkaSASLContainer,
        tls_certificates: dict[str, Path],
        env_secret_provider: EnvSecretProvider,
        mock_vault_secret_provider: VaultSecretProvider,
        auth_harness: ManagementAPIAuthHarness,
    ) -> None:
        """Verify all security fixtures are available and functional."""
        # TLS container
        assert kafka_tls_container.get_ssl_bootstrap_server()

        # SASL container
        assert kafka_sasl_container.get_bootstrap_server()

        # Certificates
        assert all(cert.exists() for cert in tls_certificates.values())

        # Secret providers
        assert env_secret_provider is not None
        assert mock_vault_secret_provider is not None

        # Auth harness
        result = auth_harness.validate_request(api_key="test-key-123")
        assert result["valid"] is True
