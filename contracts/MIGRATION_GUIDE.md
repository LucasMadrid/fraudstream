# CHB-006 Migration Guide: Interface Contracts

This guide explains how to migrate the processing and scoring layers to use the new interface contracts defined in the `contracts/` package.

## Overview

The contracts package provides three key protocols:

1. **FeatureServingProtocol** - Feature retrieval from online stores
2. **AlertSinkProtocol** - Alert emission to persistent storage  
3. **InferenceClient** - Model inference abstraction

## Current State vs Target State

### Before (Direct Dependencies)

```python
# pipelines/processing/job.py (CURRENT - violates CHB-006)
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.job_extension import wire_rule_evaluator
from pipelines.scoring.rules.loader import RuleLoader

# Direct import from scoring layer creates tight coupling
scoring_config = ScoringConfig()
rules = RuleLoader.load(scoring_config.rules_yaml_path)
wire_rule_evaluator(enriched_stream, scoring_config, rules)
```

```python
# pipelines/scoring/sinks/alert_postgres.py (CURRENT)
from pipelines.shared.alert_protocol import AlertSink  # Protocol exists but limited

class AlertPostgresSink(AlertSink):  # Uses old protocol
    def emit(self, alert: FraudAlert) -> None:
        ...
```

### After (Contract-Based)

```python
# pipelines/processing/job.py (TARGET - CHB-006 compliant)
from contracts import FeatureServingProtocol, AlertSinkProtocol, InferenceClient
from pipelines.shared.interfaces import RuleMetricsPublisher

def build_job(
    config: ProcessorConfig,
    feature_client: FeatureServingProtocol | None = None,
    alert_sink: AlertSinkProtocol | None = None,
) -> StreamExecutionEnvironment:
    """Build job with injected dependencies (testable, decoupled)."""
    # Use provided implementations or defaults
    feature_client = feature_client or FeatureServingClient()
    alert_sink = alert_sink or AlertPostgresSink(config)
    ...
```

```python
# pipelines/scoring/sinks/alert_postgres.py (TARGET)
from contracts import AlertSinkProtocol, FraudAlert

class AlertPostgresSink:
    """PostgreSQL implementation of AlertSinkProtocol."""
    
    def emit(self, alert: FraudAlert) -> None:
        ...
    
    def open(self) -> None:
        ...
    
    def close(self) -> None:
        ...

# Runtime checkable
assert isinstance(AlertPostgresSink(), AlertSinkProtocol)  # True
```

## Step-by-Step Migration

### Step 1: Update Alert Sinks

**File:** `pipelines/scoring/sinks/alert_postgres.py`

```python
# BEFORE
from pipelines.shared.alert_protocol import AlertSink
from pipelines.scoring.types import FraudAlert

class AlertPostgresSink(AlertSink):
    def emit(self, alert: FraudAlert) -> None: ...

# AFTER
from contracts import AlertSinkProtocol
from pipelines.scoring.types import FraudAlert

class AlertPostgresSink:
    """PostgreSQL implementation of AlertSinkProtocol."""
    
    def emit(self, alert: FraudAlert) -> None:
        """Emit alert to PostgreSQL."""
        ...
    
    def open(self) -> None:
        """Open database connection."""
        ...
    
    def close(self) -> None:
        """Close database connection."""
        ...
```

### Step 2: Update Feature Serving Client

**File:** `pipelines/scoring/clients/feature_serving.py`

```python
# BEFORE
from pipelines.scoring.types import FeatureVector, ZERO_FEATURE_VECTOR

class FeatureServingClient:
    def get_features(self, account_id, transaction_id, transaction_timestamp) -> FeatureVector:
        ...

# AFTER
from contracts import FeatureServingProtocol
from pipelines.scoring.types import FeatureVector, ZERO_FEATURE_VECTOR

class FeatureServingClient:
    """Feast-based implementation of FeatureServingProtocol."""
    
    def get_features(
        self,
        account_id: str,
        transaction_id: str,
        transaction_timestamp: int,
    ) -> FeatureVector:
        """Retrieve features with 3ms timeout."""
        ...
    
    def health(self) -> dict[str, Any]:
        """Return feature store health status."""
        return {
            "status": "healthy" if self._store else "unhealthy",
            "latency_ms": self._get_avg_latency(),
            "fallback_rate": self._get_fallback_rate(),
            "timestamp": time.time(),
        }
    
    def open(self) -> None:
        """Initialize Feast connection."""
        ...
    
    def close(self) -> None:
        """Close Feast connection."""
        ...
```

### Step 3: Create Inference Client Implementation

**New File:** `pipelines/scoring/clients/inference.py`

```python
"""Model inference client implementing InferenceClient protocol."""

from contracts import InferenceClient
from pipelines.scoring.types import FeatureVector

class ModelInferenceClient:
    """ML model inference with circuit breaker."""
    
    def predict(self, features: FeatureVector, timeout_ms: int = 50) -> float:
        """Return fraud probability [0.0, 1.0]."""
        ...
    
    def health(self) -> dict[str, Any]:
        """Return model health status."""
        ...
    
    def open(self) -> None:
        """Load model into memory."""
        ...
    
    def close(self) -> None:
        """Unload model."""
        ...
```

### Step 4: Update Processing Layer

**File:** `pipelines/processing/job.py`

```python
# BEFORE - Direct scoring imports
from pipelines.scoring.config import ScoringConfig
from pipelines.scoring.job_extension import wire_rule_evaluator
from pipelines.scoring.rules.loader import RuleLoader

# AFTER - Contract-based
def build_job(
    config: ProcessorConfig,
    feature_client: FeatureServingProtocol | None = None,
    alert_sink: AlertSinkProtocol | None = None,
    inference_client: InferenceClient | None = None,
) -> StreamExecutionEnvironment:
    """Build job with dependency injection."""
    
    # Initialize defaults if not provided
    feature_client = feature_client or FeatureServingClient()
    alert_sink = alert_sink or AlertPostgresSink(config)
    inference_client = inference_client or ModelInferenceClient()
    
    # Open connections
    feature_client.open()
    alert_sink.open()
    inference_client.open()
    
    try:
        # Build topology with injected dependencies
        enriched_stream = build_enrichment_topology(env, config)
        scored_stream = enriched_stream.map(
            lambda txn: score_transaction(txn, feature_client, inference_client)
        )
        alerts = scored_stream.filter(lambda s: s.is_suspicious)
        alerts.add_sink(alert_sink)
    finally:
        # Cleanup (in real Flink job, use RichFunction lifecycle)
        pass
```

### Step 5: Update Tests

**File:** `tests/unit/scoring/test_alert_postgres.py`

```python
# BEFORE - Tests concrete implementation
from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

def test_emit_persists_alert():
    sink = AlertPostgresSink(mock_config)
    sink.emit(FraudAlert(...))

# AFTER - Tests against protocol
from contracts import AlertSinkProtocol
from pipelines.scoring.sinks.alert_postgres import AlertPostgresSink

def test_implements_protocol():
    """Verify AlertPostgresSink satisfies AlertSinkProtocol."""
    sink = AlertPostgresSink(mock_config)
    assert isinstance(sink, AlertSinkProtocol)  # Runtime checkable

def test_emit_persists_alert():
    """Test concrete behavior through protocol interface."""
    sink: AlertSinkProtocol = AlertPostgresSink(mock_config)
    sink.open()
    sink.emit(FraudAlert(...))
    sink.close()
```

## Dependency Injection Pattern

For testing and modular deployment:

```python
# tests/integration/test_contracts.py
from unittest.mock import MagicMock
from contracts import FeatureServingProtocol, InferenceClient

def test_processing_with_mock_clients():
    """Verify processing layer works with mock implementations."""
    
    # Create mock implementations
    mock_features = MagicMock(spec=FeatureServingProtocol)
    mock_features.get_features.return_value = FeatureVector(...)
    
    mock_inference = MagicMock(spec=InferenceClient)
    mock_inference.predict.return_value = 0.85
    
    # Build job with mocks
    env = build_job(
        config=test_config,
        feature_client=mock_features,
        inference_client=mock_inference,
    )
    
    # Verify interactions
    mock_features.get_features.assert_called()
    mock_inference.predict.assert_called()
```

## NoOp Implementations

For graceful degradation when services are unavailable:

```python
# contracts/backends.py (future file)
from contracts import FeatureServingProtocol, InferenceClient

class NoOpFeatureClient:
    """Returns zero features - useful when feature store is down."""
    
    def get_features(self, account_id, txn_id, ts):
        return ZERO_FEATURE_VECTOR
    
    def health(self):
        return {"status": "unhealthy", "reason": "NoOp implementation"}
    
    def open(self): pass
    def close(self): pass

class FallbackInferenceClient:
    """Returns neutral score (0.5) - flags for manual review."""
    
    def predict(self, features, timeout_ms=50):
        return 0.5  # Neutral score triggers review
    
    def health(self):
        return {"status": "degraded", "reason": "Fallback mode"}
    
    def open(self): pass
    def close(self): pass
```

## Verification Checklist

- [ ] AlertPostgresSink passes `isinstance(sink, AlertSinkProtocol)`
- [ ] FeatureServingClient passes `isinstance(client, FeatureServingProtocol)`
- [ ] ModelInferenceClient passes `isinstance(client, InferenceClient)`
- [ ] Processing layer has no direct imports from `pipelines.scoring.*`
- [ ] All protocol methods have proper type hints
- [ ] All protocol methods have docstrings with Constitution references
- [ ] Tests use mock implementations of protocols
- [ ] Integration tests verify real implementations satisfy protocols

## Rollback Plan

If issues arise:

1. Keep old imports as comments for quick restoration
2. Maintain parallel implementations during transition
3. Use feature flags to toggle between old/new code paths
4. Monitor error rates and latency metrics during rollout

## Constitution Compliance

This migration enforces:

- **Article 3 (Dependency Inversion)**: High-level processing depends on contracts, not implementations
- **Article 4 (Testability)**: Protocols enable mock-based unit testing
- **Article 5 (Fail-Safe)**: All protocols define graceful degradation
- **Article 6 (Performance)**: Protocols preserve timing guarantees (3ms features, 10ms inference)
- **Article 7 (Observability)**: Health methods enable monitoring and alerting
