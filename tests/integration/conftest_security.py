"""Security-focused test fixtures for TLS, SASL, and authentication testing.

TB-001: Security Test Environment Setup
- TLS-enabled Kafka testcontainer
- SecretProvider test harness
- Management API auth fixtures

Usage:
  pytest -m integration tests/integration/test_security_*.py
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from unittest.mock import MagicMock

import pytest

# Disable Ryuk (testcontainers reaper) — required on macOS Docker Desktop
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

if TYPE_CHECKING:
    from collections.abc import Generator

    from confluent_kafka import Consumer, Producer
    from testcontainers.core.container import DockerContainer

# =============================================================================
# TLS Certificate Fixtures (Dynamic Generation)
# =============================================================================

from tests.fixtures.tls import get_tls_cert_path


# =============================================================================
# TLS Certificate Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def tls_certificates() -> dict[str, Path]:
    """Provide paths to dynamically-generated test TLS certificates.

    Certificates are generated on-the-fly to avoid storing private keys
    in version control (security best practice).

    Returns:
        Dictionary with paths to CA cert, server cert/key, client cert/key.
    """
    return {
        "ca_cert": get_tls_cert_path("ca_cert"),
        "ca_key": get_tls_cert_path("ca_key"),
        "server_cert": get_tls_cert_path("server_cert"),
        "server_key": get_tls_cert_path("server_key"),
        "client_cert": get_tls_cert_path("client_cert"),
        "client_key": get_tls_cert_path("client_key"),
    }


@pytest.fixture(scope="session")
def ssl_context_tls12(tls_certificates: dict[str, Path]) -> ssl.SSLContext:
    """Create an SSL context with TLS 1.2 for testing.

    This context is configured for mutual TLS (mTLS) authentication.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2

    # Load CA cert for server verification
    context.load_verify_locations(cafile=str(tls_certificates["ca_cert"]))

    # Load client cert for mutual TLS
    context.load_cert_chain(
        certfile=str(tls_certificates["client_cert"]),
        keyfile=str(tls_certificates["client_key"]),
    )

    context.verify_mode = ssl.CERT_REQUIRED
    return context


# =============================================================================
# TLS-Enabled Kafka Testcontainer
# =============================================================================


@dataclass
class KafkaTLSConfig:
    """Configuration for TLS-enabled Kafka."""

    bootstrap_servers: str
    security_protocol: str = "SSL"
    ssl_ca_location: str | None = None
    ssl_certificate_location: str | None = None
    ssl_key_location: str | None = None
    ssl_key_password: str | None = None
    ssl_endpoint_identification_algorithm: str = "none"

    def to_client_config(self) -> dict[str, str | None]:
        """Convert to Kafka client configuration dict."""
        config = {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": self.security_protocol,
            "ssl.endpoint.identification.algorithm": self.ssl_endpoint_identification_algorithm,
        }
        if self.ssl_ca_location:
            config["ssl.ca.location"] = self.ssl_ca_location
        if self.ssl_certificate_location:
            config["ssl.certificate.location"] = self.ssl_certificate_location
        if self.ssl_key_location:
            config["ssl.key.location"] = self.ssl_key_location
        if self.ssl_key_password:
            config["ssl.key.password"] = self.ssl_key_password
        return config


class KafkaTLSContainer:
    """Custom Kafka container with TLS/SSL enabled.

    Extends the standard testcontainers Kafka with SSL listener support
    for testing encrypted connections.
    """

    def __init__(
        self,
        image: str = "confluentinc/cp-kafka:7.6.1",
        tls_certificates: dict[str, Path] | None = None,
    ) -> None:
        self.image = image
        self.tls_certificates = tls_certificates or {}
        self._container = None
        self._bootstrap_server: str | None = None

    def _create_container(self) -> DockerContainer:
        """Create and configure the Docker container with TLS."""
        from testcontainers.core.container import DockerContainer

        # Create container
        container = DockerContainer(self.image)

        # Generate unique broker ID
        import random

        broker_id = random.randint(1000, 9999)

        # Configure Kafka with SSL
        container.with_env("KAFKA_NODE_ID", str(broker_id))
        container.with_env("KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "PLAINTEXT:PLAINTEXT,SSL:SSL")
        container.with_env("KAFKA_LISTENERS", "PLAINTEXT://0.0.0.0:9092,SSL://0.0.0.0:9093")
        container.with_env(
            "KAFKA_ADVERTISED_LISTENERS",
            "PLAINTEXT://localhost:29092,SSL://localhost:29093",
        )
        container.with_env("KAFKA_PROCESS_ROLES", "broker,controller")
        container.with_env("KAFKA_CONTROLLER_QUORUM_VOTERS", f"{broker_id}@localhost:29093")
        container.with_env("KAFKA_CONTROLLER_LISTENER_NAMES", "SSL")
        container.with_env("KAFKA_INTER_BROKER_LISTENER_NAME", "PLAINTEXT")
        container.with_env("CLUSTER_ID", "test-cluster-001")

        # SSL Configuration
        if self.tls_certificates:
            container.with_env("KAFKA_SSL_KEYSTORE_FILENAME", "kafka.server.keystore.jks")
            container.with_env("KAFKA_SSL_KEYSTORE_CREDENTIALS", "serverpass")
            container.with_env("KAFKA_SSL_KEY_CREDENTIALS", "serverpass")
            container.with_env("KAFKA_SSL_TRUSTSTORE_FILENAME", "kafka.server.truststore.jks")
            container.with_env("KAFKA_SSL_TRUSTSTORE_CREDENTIALS", "serverpass")
            container.with_env("KAFKA_SSL_CLIENT_AUTH", "required")

        # Expose ports
        container.with_exposed_ports(9092, 9093)

        return container

    def __enter__(self) -> KafkaTLSContainer:
        """Start the container and return self."""
        self._container = self._create_container()
        self._container.start()

        # Wait for Kafka to be ready
        from testcontainers.core.waiting_utils import wait_for_logs

        wait_for_logs(self._container, "Kafka Server started", timeout=60)

        # Get bootstrap server
        host = self._container.get_container_host_ip()
        ssl_port = self._container.get_exposed_port(9093)
        self._bootstrap_server = f"{host}:{ssl_port}"

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop the container."""
        if self._container:
            self._container.stop()

    def get_ssl_bootstrap_server(self) -> str:
        """Return the SSL bootstrap server address."""
        if not self._bootstrap_server:
            raise RuntimeError("Container not started")
        return self._bootstrap_server

    def get_config(self) -> KafkaTLSConfig:
        """Get TLS configuration for connecting to this Kafka."""
        return KafkaTLSConfig(
            bootstrap_servers=self.get_ssl_bootstrap_server(),
            security_protocol="SSL",
            ssl_ca_location=str(self.tls_certificates.get("ca_cert")),
            ssl_certificate_location=str(self.tls_certificates.get("client_cert")),
            ssl_key_location=str(self.tls_certificates.get("client_key")),
        )


@pytest.fixture(scope="session")
def kafka_tls_container(
    tls_certificates: dict[str, Path],
) -> Generator[KafkaTLSContainer, None, None]:
    """Provide a TLS-enabled Kafka container for the test session.

    Yields:
        KafkaTLSContainer instance with SSL listener configured.
    """
    with KafkaTLSContainer(tls_certificates=tls_certificates) as container:
        yield container


@pytest.fixture(scope="session")
def kafka_tls_config(kafka_tls_container: KafkaTLSContainer) -> KafkaTLSConfig:
    """Provide TLS configuration for connecting to the test Kafka."""
    return kafka_tls_container.get_config()


# =============================================================================
# SASL/SCRAM Fixtures
# =============================================================================


@dataclass
class SASLConfig:
    """Configuration for SASL/SCRAM authentication."""

    username: str
    password: str
    mechanism: str = "SCRAM-SHA-256"
    security_protocol: str = "SASL_PLAINTEXT"

    def to_client_config(self, bootstrap_servers: str) -> dict[str, str]:
        """Convert to Kafka client configuration dict."""
        return {
            "bootstrap.servers": bootstrap_servers,
            "security.protocol": self.security_protocol,
            "sasl.mechanism": self.mechanism,
            "sasl.username": self.username,
            "sasl.password": self.password,
        }


class KafkaSASLContainer:
    """Custom Kafka container with SASL/SCRAM authentication enabled."""

    def __init__(
        self,
        image: str = "confluentinc/cp-kafka:7.6.1",
        users: dict[str, str] | None = None,
    ) -> None:
        self.image = image
        self.users = users or {
            "admin": "admin-secret",
            "producer": "producer-secret",
            "consumer": "consumer-secret",
            "fraudapp": "fraudapp-secret",
        }
        self._container = None
        self._bootstrap_server: str | None = None

    def __enter__(self) -> KafkaSASLContainer:
        """Start the container with SASL configuration."""
        import random

        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs

        broker_id = random.randint(1000, 9999)

        container = DockerContainer(self.image)
        container.with_env("KAFKA_NODE_ID", str(broker_id))
        container.with_env(
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP",
            "PLAINTEXT:PLAINTEXT,SASL_PLAINTEXT:SASL_PLAINTEXT",
        )
        container.with_env(
            "KAFKA_LISTENERS",
            "PLAINTEXT://0.0.0.0:9092,SASL_PLAINTEXT://0.0.0.0:9093",
        )
        container.with_env(
            "KAFKA_ADVERTISED_LISTENERS",
            "PLAINTEXT://localhost:29092,SASL_PLAINTEXT://localhost:29093",
        )
        container.with_env("KAFKA_SASL_ENABLED_MECHANISMS", "SCRAM-SHA-256,SCRAM-SHA-512")
        container.with_env("KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL", "PLAIN")
        container.with_env("KAFKA_SECURITY_INTER_BROKER_PROTOCOL", "PLAINTEXT")
        container.with_env("KAFKA_PROCESS_ROLES", "broker,controller")
        container.with_env("KAFKA_CONTROLLER_QUORUM_VOTERS", f"{broker_id}@localhost:29093")
        container.with_env("KAFKA_CONTROLLER_LISTENER_NAMES", "PLAINTEXT")
        container.with_env("CLUSTER_ID", "sasl-cluster-001")

        # Expose ports
        container.with_exposed_ports(9092, 9093)

        self._container = container
        self._container.start()

        wait_for_logs(self._container, "Kafka Server started", timeout=60)

        host = self._container.get_container_host_ip()
        sasl_port = self._container.get_exposed_port(9093)
        self._bootstrap_server = f"{host}:{sasl_port}"

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop the container."""
        if self._container:
            self._container.stop()

    def get_bootstrap_server(self) -> str:
        """Return the SASL bootstrap server address."""
        if not self._bootstrap_server:
            raise RuntimeError("Container not started")
        return self._bootstrap_server

    def get_sasl_config(self, username: str, password: str | None = None) -> SASLConfig:
        """Get SASL configuration for a specific user."""
        if password is None:
            password = self.users.get(username, "")
        return SASLConfig(username=username, password=password)


@pytest.fixture(scope="session")
def kafka_sasl_container() -> Generator[KafkaSASLContainer, None, None]:
    """Provide a SASL-enabled Kafka container for the test session."""
    with KafkaSASLContainer() as container:
        yield container


# =============================================================================
# SecretProvider Protocol and Implementations
# =============================================================================


class SecretProvider(Protocol):
    """Protocol for secret retrieval abstraction.

    Implementations:
        - EnvSecretProvider: Reads from environment variables
        - VaultSecretProvider: Reads from HashiCorp Vault
    """

    def get_secret(self, key: str) -> str | None:
        """Retrieve a secret by key.

        Args:
            key: The secret identifier/path

        Returns:
            The secret value or None if not found
        """
        ...


class EnvSecretProvider:
    """Secret provider that reads from environment variables.

    Used for local development and testing.
    """

    def __init__(self, prefix: str = "") -> None:
        """Initialize with optional prefix for environment variable names.

        Args:
            prefix: Prefix added to all secret keys (e.g., "FRAUDSTREAM_")
        """
        self.prefix = prefix

    def get_secret(self, key: str) -> str | None:
        """Get secret from environment variable.

        Args:
            key: The secret name (without prefix)

        Returns:
            The secret value or None if not set
        """
        env_key = f"{self.prefix}{key}"
        return os.environ.get(env_key)

    def get_required_secret(self, key: str) -> str:
        """Get a required secret, raising if not found.

        Args:
            key: The secret name

        Returns:
            The secret value

        Raises:
            SecretNotFoundError: If secret is not set
        """
        value = self.get_secret(key)
        if value is None:
            raise SecretNotFoundError(f"Required secret not found: {self.prefix}{key}")
        return value


class SecretNotFoundError(Exception):
    """Raised when a required secret is not found."""

    pass


class VaultSecretProvider:
    """Secret provider that reads from HashiCorp Vault.

    Used for staging and production environments.
    """

    def __init__(
        self,
        vault_addr: str,
        vault_token: str | None = None,
        vault_role: str | None = None,
        mount_point: str = "secret",
    ) -> None:
        """Initialize Vault connection.

        Args:
            vault_addr: Vault server URL
            vault_token: Vault token for authentication
            vault_role: Kubernetes JWT role for auth (optional)
            mount_point: Secret engine mount point
        """
        self.vault_addr = vault_addr
        self.vault_token = vault_token
        self.vault_role = vault_role
        self.mount_point = mount_point
        self._client: MagicMock | None = None

    def _get_client(self) -> MagicMock:
        """Get or create Vault client."""
        if self._client is None:
            try:
                import hvac

                self._client = hvac.Client(url=self.vault_addr, token=self.vault_token)
            except ImportError:
                # Fallback to mock for testing without hvac
                self._client = MagicMock()
                self._client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {}}}
        return self._client

    def get_secret(self, path: str, key: str = "value") -> str | None:
        """Get secret from Vault KV v2.

        Args:
            path: Secret path (e.g., "fraudstream/kafka")
            key: Key within the secret data

        Returns:
            The secret value or None if not found
        """
        client = self._get_client()
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self.mount_point
            )
            data = response.get("data", {}).get("data", {})
            return data.get(key)
        except Exception:
            return None


# =============================================================================
# SecretProvider Fixtures
# =============================================================================


@pytest.fixture
def env_secret_provider() -> EnvSecretProvider:
    """Provide an EnvSecretProvider instance."""
    return EnvSecretProvider()


@pytest.fixture
def prefixed_env_provider() -> EnvSecretProvider:
    """Provide an EnvSecretProvider with prefix."""
    return EnvSecretProvider(prefix="FRAUDSTREAM_")


@pytest.fixture
def vault_secret_provider() -> VaultSecretProvider:
    """Provide a VaultSecretProvider instance with mock client."""
    return VaultSecretProvider(
        vault_addr="http://localhost:8200",
        vault_token="test-token",
    )


@pytest.fixture
def mock_vault_secret_provider() -> Generator[VaultSecretProvider, None, None]:
    """Provide a VaultSecretProvider with pre-configured mock secrets.

    The mock returns secrets for common paths used in testing.
    """
    provider = VaultSecretProvider(
        vault_addr="http://vault-test:8200",
        vault_token="mock-token",
    )

    # Configure mock responses
    mock_secrets = {
        "fraudstream/kafka": {"username": "fraudapp", "password": "test-secret"},
        "fraudstream/postgres": {"host": "localhost", "password": "pg-secret"},
        "fraudstream/api": {"management_key": "test-api-key-123"},
    }

    provider._client = MagicMock()

    def _mock_read_secret(path, mount_point=None):
        return {"data": {"data": mock_secrets.get(path, {})}}

    provider._client.secrets.kv.v2.read_secret_version.side_effect = _mock_read_secret

    yield provider


@pytest.fixture
def secret_provider_harness(
    env_secret_provider: EnvSecretProvider,
    mock_vault_secret_provider: VaultSecretProvider,
) -> SecretProviderHarness:
    """Provide a test harness for SecretProvider implementations."""
    return SecretProviderHarness(
        env_provider=env_secret_provider,
        vault_provider=mock_vault_secret_provider,
    )


class SecretProviderHarness:
    """Test harness for validating SecretProvider implementations."""

    def __init__(
        self,
        env_provider: EnvSecretProvider,
        vault_provider: VaultSecretProvider,
    ) -> None:
        self.env_provider = env_provider
        self.vault_provider = vault_provider

    def test_env_provider_reads_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that EnvSecretProvider reads from environment."""
        monkeypatch.setenv("TEST_SECRET", "secret-value")
        provider = EnvSecretProvider()
        assert provider.get_secret("TEST_SECRET") == "secret-value"

    def test_env_provider_returns_none_for_missing(self) -> None:
        """Test that EnvSecretProvider returns None for missing secrets."""
        provider = EnvSecretProvider()
        assert provider.get_secret("NONEXISTENT_SECRET") is None

    def test_env_provider_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that EnvSecretProvider respects prefix."""
        monkeypatch.setenv("FRAUDSTREAM_API_KEY", "prefixed-value")
        provider = EnvSecretProvider(prefix="FRAUDSTREAM_")
        assert provider.get_secret("API_KEY") == "prefixed-value"

    def test_vault_provider_reads_secret(self) -> None:
        """Test that VaultSecretProvider reads from Vault."""
        result = self.vault_provider.get_secret("fraudstream/kafka", "username")
        assert result == "fraudapp"

    def test_vault_provider_returns_none_for_missing(self) -> None:
        """Test that VaultSecretProvider returns None for missing secrets."""
        result = self.vault_provider.get_secret("nonexistent/path", "key")
        assert result is None


# =============================================================================
# Management API Auth Fixtures
# =============================================================================


@dataclass
class APIKeyConfig:
    """Configuration for API key authentication."""

    header_name: str = "X-Api-Key"
    valid_keys: set[str] | None = None
    require_auth: bool = True

    def is_valid_key(self, key: str | None) -> bool:
        """Check if an API key is valid."""
        if not self.require_auth:
            return True
        if key is None:
            return False
        if self.valid_keys is None:
            # In dev mode, any non-empty key is valid
            return len(key) > 0
        return key in self.valid_keys


class RateLimitConfig:
    """Configuration for rate limiting.

    Uses slowapi-style configuration.
    """

    def __init__(
        self,
        requests_per_minute: int = 100,
        burst_size: int | None = None,
        key_func: str = "client_ip",
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size or requests_per_minute
        self.key_func = key_func

    def get_limit_string(self) -> str:
        """Get rate limit string for slowapi decorator."""
        return f"{self.requests_per_minute}/minute"


@pytest.fixture
def api_key_config() -> APIKeyConfig:
    """Provide default API key configuration."""
    return APIKeyConfig(
        valid_keys={"test-key-123", "test-key-456", "prod-key-789"},
        require_auth=True,
    )


@pytest.fixture
def dev_api_key_config() -> APIKeyConfig:
    """Provide development API key configuration (less strict)."""
    return APIKeyConfig(
        valid_keys=None,  # Any key is valid in dev
        require_auth=False,  # Auth optional
    )


@pytest.fixture
def rate_limit_config() -> RateLimitConfig:
    """Provide default rate limiting configuration."""
    return RateLimitConfig(
        requests_per_minute=100,
        burst_size=120,
        key_func="client_ip",
    )


@pytest.fixture
def strict_rate_limit_config() -> RateLimitConfig:
    """Provide strict rate limiting for testing limits."""
    return RateLimitConfig(
        requests_per_minute=5,
        burst_size=5,
        key_func="client_ip",
    )


class ManagementAPIAuthHarness:
    """Test harness for Management API authentication and rate limiting."""

    def __init__(
        self,
        api_key_config: APIKeyConfig,
        rate_limit_config: RateLimitConfig,
    ) -> None:
        self.api_key_config = api_key_config
        self.rate_limit_config = rate_limit_config
        self._request_counts: dict[str, list[float]] = {}

    def validate_request(self, api_key: str | None, client_ip: str = "127.0.0.1") -> dict:
        """Validate an API request.

        Args:
            api_key: The API key from the request header
            client_ip: Client IP address for rate limiting

        Returns:
            Validation result with keys: valid (bool), error (str|None), rate_limited (bool)
        """
        import time

        # Check API key
        if not self.api_key_config.is_valid_key(api_key):
            return {
                "valid": False,
                "error": "Invalid or missing API key",
                "rate_limited": False,
                "status_code": 401,
            }

        # Check rate limit
        now = time.time()
        window_start = now - 60  # 1 minute window

        # Get or initialize request history for this client
        requests = self._request_counts.get(client_ip, [])
        requests = [t for t in requests if t > window_start]

        if len(requests) >= self.rate_limit_config.requests_per_minute:
            return {
                "valid": True,
                "error": "Rate limit exceeded",
                "rate_limited": True,
                "status_code": 429,
                "retry_after": 60 - int(now - requests[0]),
            }

        # Record this request
        requests.append(now)
        self._request_counts[client_ip] = requests

        return {
            "valid": True,
            "error": None,
            "rate_limited": False,
            "status_code": 200,
        }

    def reset_rate_limit(self, client_ip: str | None = None) -> None:
        """Reset rate limit counters."""
        if client_ip:
            self._request_counts.pop(client_ip, None)
        else:
            self._request_counts.clear()


@pytest.fixture
def auth_harness(
    api_key_config: APIKeyConfig,
    rate_limit_config: RateLimitConfig,
) -> ManagementAPIAuthHarness:
    """Provide a Management API auth test harness."""
    return ManagementAPIAuthHarness(api_key_config, rate_limit_config)


@pytest.fixture
def strict_auth_harness(
    api_key_config: APIKeyConfig,
    strict_rate_limit_config: RateLimitConfig,
) -> ManagementAPIAuthHarness:
    """Provide a strict auth test harness for limit testing."""
    return ManagementAPIAuthHarness(api_key_config, strict_rate_limit_config)


# =============================================================================
# Integration Test Helpers
# =============================================================================


@pytest.fixture
def kafka_ssl_producer(kafka_tls_config: KafkaTLSConfig) -> Producer:
    """Create a Kafka producer with SSL configuration.

    Yields:
        Configured Kafka Producer instance.
    """
    from confluent_kafka import KafkaException, Producer

    config = kafka_tls_config.to_client_config()

    def delivery_callback(err, msg):
        if err:
            raise KafkaException(err)

    producer = Producer(config)
    yield producer
    producer.flush(30)


@pytest.fixture
def kafka_ssl_consumer(kafka_tls_config: KafkaTLSConfig) -> Consumer:
    """Create a Kafka consumer with SSL configuration.

    Yields:
        Configured Kafka Consumer instance.
    """
    from confluent_kafka import Consumer

    config = kafka_tls_config.to_client_config()
    config["group.id"] = "test-ssl-consumer"
    config["auto.offset.reset"] = "earliest"

    consumer = Consumer(config)
    yield consumer
    consumer.close()


@pytest.fixture
def kafka_sasl_producer(kafka_sasl_container: KafkaSASLContainer) -> Producer:
    """Create a Kafka producer with SASL/SCRAM authentication.

    Uses the fraudapp user which has full permissions.
    """
    from confluent_kafka import Producer

    sasl_config = kafka_sasl_container.get_sasl_config("fraudapp")
    config = sasl_config.to_client_config(kafka_sasl_container.get_bootstrap_server())

    producer = Producer(config)
    yield producer
    producer.flush(30)


@pytest.fixture
def kafka_sasl_consumer(kafka_sasl_container: KafkaSASLContainer) -> Consumer:
    """Create a Kafka consumer with SASL/SCRAM authentication.

    Uses the consumer user with read-only permissions.
    """
    from confluent_kafka import Consumer

    sasl_config = kafka_sasl_container.get_sasl_config("consumer")
    config = sasl_config.to_client_config(kafka_sasl_container.get_bootstrap_server())
    config["group.id"] = "test-sasl-consumer"
    config["auto.offset.reset"] = "earliest"

    consumer = Consumer(config)
    yield consumer
    consumer.close()
