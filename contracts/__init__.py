"""Interface Contracts package for FraudStream pipeline decoupling.

This package defines protocol interfaces that decouple the processing and scoring
layers, enabling independent evolution and testing. Following the Dependency
Inversion Principle (Constitution Article 3), high-level modules depend on
abstractions rather than concrete implementations.

Architecture (CHB-006 - Processing/Scoring Boundary):
    Processing Layer ──► Interface Contract ◄── Scoring Layer (implements)

Key Contracts:
    - FeatureServingProtocol: Feature retrieval from online stores
    - AlertSinkProtocol: Alert emission to persistent storage
    - InferenceClient: Model inference abstraction

Usage:
    from contracts import FeatureServingProtocol, AlertSinkProtocol, InferenceClient

    def process_transaction(
        feature_client: FeatureServingProtocol,
        alert_sink: AlertSinkProtocol,
        inference_client: InferenceClient,
    ) -> Decision:
        features = feature_client.get_features(account_id, txn_id, timestamp)
        score = inference_client.predict(features)
        if score > threshold:
            alert_sink.emit(FraudAlert(...))
        return Decision(...)

Constitution Principles:
    - Article 3 (Dependency Inversion): High-level modules depend on abstractions
    - Article 4 (Testability): Protocols enable mock implementations for testing
    - Article 5 (Fail-Safe): All protocols define graceful degradation paths
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any


# =============================================================================
# Feature Serving Protocol
# =============================================================================


@runtime_checkable
class FeatureServingProtocol(Protocol):
    """Protocol for feature retrieval from online feature stores.

    Abstracts the feature store implementation (Feast, Redis, in-memory)
    from the scoring layer. Guarantees sub-3ms retrieval with fallback
    to zero-values on timeout or unavailability.

    Constitution References:
        - Article 3 (Dependency Inversion): Processing layer depends on this
          abstraction, not concrete Feast implementation
        - Article 5 (Fail-Safe): Always returns FeatureVector, never raises

    Example:
        >>> client: FeatureServingProtocol = FeatureServingClient()
        >>> client.open()  # Initialize connections
        >>> vector = client.get_features("acc_123", "txn_456", 1704067200000)
        >>> vector.vel_count_1m  # Always available (zero if fallback)
        5
        >>> client.close()  # Cleanup resources
    """

    def get_features(
        self,
        account_id: str,
        transaction_id: str,
        transaction_timestamp: int,
    ) -> "FeatureVector":
        """Retrieve feature vector for a transaction.

        Must return within 3ms (configurable). On timeout, store outage,
        or cache miss, returns zero-filled FeatureVector and increments
        appropriate metrics.

        Args:
            account_id: Unique account identifier (entity key)
            transaction_id: Transaction identifier for logging/debugging
            transaction_timestamp: Transaction timestamp (epoch milliseconds)

        Returns:
            FeatureVector: Populated features or zero-values on failure

        Constitution (Article 5):
            Never raises exceptions. Always returns valid FeatureVector.
        """
        ...

    def health(self) -> dict[str, Any]:
        """Return health status of the feature store connection.

        Returns:
            Dictionary with keys:
                - status: "healthy" | "degraded" | "unhealthy"
                - latency_ms: Average retrieval latency (last 60s)
                - fallback_rate: Ratio of fallbacks to total calls
                - last_error: Optional last error message
                - timestamp: Health check timestamp

        Constitution (Article 7 - Observability):
            Health checks enable circuit breakers and alerting.
        """
        ...

    def open(self) -> None:
        """Initialize connections to the feature store.

        Must be called before get_features(). Idempotent - safe to call
        multiple times.
        """
        ...

    def close(self) -> None:
        """Close connections and release resources.

        Safe to call multiple times. Should not raise exceptions.
        """
        ...


# =============================================================================
# Alert Sink Protocol
# =============================================================================


@runtime_checkable
class AlertSinkProtocol(Protocol):
    """Protocol for emitting fraud alerts to persistent storage.

    Decouples alert persistence from the scoring layer. Implementations
    may write to PostgreSQL, Kafka, or other backends. Guarantees
    at-least-once delivery with idempotency on duplicate transaction_ids.

    Constitution References:
        - Article 3 (Dependency Inversion): Processing layer emits alerts
          through this abstraction, not direct PostgreSQL/Kafka calls
        - Article 5 (Fail-Safe): Emits are non-blocking; failures are logged

    Example:
        >>> sink: AlertSinkProtocol = AlertPostgresSink(config)
        >>> sink.open()
        >>> sink.emit(FraudAlert(
        ...     transaction_id="txn_123",
        ...     account_id="acc_456",
        ...     matched_rule_names=["High Velocity"],
        ...     severity="high",
        ...     evaluation_timestamp=1704067200000,
        ... ))
        >>> sink.close()
    """

    def emit(self, alert: "FraudAlert") -> None:
        """Emit a fraud alert to the sink.

        Implementations must handle retries and connection management.
        Should not raise exceptions - failures are logged and metrics
        are incremented.

        Args:
            alert: The fraud alert to persist

        Constitution (Article 5):
            Never blocks indefinitely. Never raises - failures are logged.
        """
        ...

    def close(self) -> None:
        """Close the sink and release resources.

        Flushes any buffered alerts. Safe to call multiple times.
        Should not raise exceptions.
        """
        ...

    def open(self) -> None:
        """Open connections required by the sink.

        Must be called before emit(). Idempotent.
        """
        ...


# =============================================================================
# Inference Client Protocol
# =============================================================================


@runtime_checkable
class InferenceClient(Protocol):
    """Protocol for model inference operations.

    Abstracts the ML model implementation from the scoring layer.
    Supports both synchronous and asynchronous inference with
    circuit breaker integration for fault tolerance.

    Constitution References:
        - Article 3 (Dependency Inversion): Scoring rules depend on this
          abstraction, not concrete model implementations
        - Article 5 (Fail-Safe): Returns fallback scores on model failure
        - Article 6 (Performance): p99 inference latency < 10ms

    Example:
        >>> client: InferenceClient = ModelInferenceClient(model_path)
        >>> client.open()
        >>> score = client.predict(feature_vector, timeout_ms=50)
        >>> decision = "BLOCK" if score > 0.9 else "FLAG" if score > 0.7 else "ALLOW"
        >>> client.close()
    """

    def predict(
        self,
        features: "FeatureVector",
        timeout_ms: int = 50,
    ) -> float:
        """Execute model inference on the provided features.

        Returns fraud probability score in range [0.0, 1.0].
        On timeout or model failure, returns fallback score (typically 0.5)
        and increments error metrics.

        Args:
            features: Feature vector from FeatureServingProtocol
            timeout_ms: Maximum wait time for inference (default 50ms)

        Returns:
            float: Fraud probability score [0.0, 1.0]

        Constitution (Article 5):
            Never raises. Returns fallback score on any error.
        Constitution (Article 6):
            Must complete within timeout_ms (p99 < 10ms at steady state).
        """
        ...

    def health(self) -> dict[str, Any]:
        """Return health status of the inference client.

        Returns:
            Dictionary with keys:
                - status: "healthy" | "degraded" | "unhealthy"
                - model_version: Currently loaded model version
                - inference_latency_ms: p99 latency (last 60s)
                - error_rate: Ratio of errors to total calls
                - circuit_breaker_state: "closed" | "open" | "half-open"
                - timestamp: Health check timestamp

        Constitution (Article 7 - Observability):
            Health checks enable automatic failover and alerting.
        """
        ...

    def open(self) -> None:
        """Initialize the inference client and load model.

        Must be called before predict(). Idempotent.
        """
        ...

    def close(self) -> None:
        """Close the inference client and release resources.

        Safe to call multiple times. Should not raise exceptions.
        """
        ...


# =============================================================================
# Forward References for Type Checking
# =============================================================================

# These are forward references to avoid circular imports.
# Implementations should import the actual types from pipelines.scoring.types


class FeatureVector(Protocol):
    """Forward reference for FeatureVector type.

    Actual implementation is in pipelines.scoring.types.FeatureVector.
    This protocol ensures structural typing compatibility.
    """

    account_id: str
    vel_count_1m: int
    vel_amount_1m: float
    vel_count_5m: int
    vel_amount_5m: float
    vel_count_1h: int
    vel_amount_1h: float
    vel_count_24h: int
    vel_amount_24h: float
    geo_country: str
    geo_city: str
    geo_network_class: str
    geo_confidence: float
    device_first_seen: int
    device_txn_count: int
    device_known_fraud: bool
    prev_geo_country: str
    prev_txn_time_ms: int


class FraudAlert(Protocol):
    """Forward reference for FraudAlert type.

    Actual implementation is in pipelines.scoring.types.FraudAlert.
    This protocol ensures structural typing compatibility.
    """

    transaction_id: str
    account_id: str
    matched_rule_names: list[str]
    severity: str
    evaluation_timestamp: int


# =============================================================================
# Package Exports
# =============================================================================

__all__ = [
    # Core protocols
    "FeatureServingProtocol",
    "AlertSinkProtocol",
    "InferenceClient",
    # Forward references
    "FeatureVector",
    "FraudAlert",
]
