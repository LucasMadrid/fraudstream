# Data Model: Restore CI Green Build

**Feature**: 023-fix-ci-green  
**Date**: 2026-05-10  
**Purpose**: Document the key entities involved in this fix and their relationships

---

## Key Entities

This feature is a test-infrastructure fix with no persistent data model changes. The entities below represent the logical contracts that tests must correctly reflect.

---

### Entity 1: ProcessingDLQSink

**Source**: `pipelines/processing/shared/dlq_sink.py`  
**Role**: The current DLQ sink implementation. All test references must target this class.

| Field / Method | Type | Notes |
|----------------|------|-------|
| `_bootstrap_servers` | `str` | Set in `__init__`; producer created immediately (no lazy init) |
| `_dlq_topic` | `str` | Kafka topic for DLQ messages |
| `_producer` | `confluent_kafka.Producer` | Non-None after `__init__` (eager creation) |
| `send(source_topic, original_payload, error_type, error_message)` | `None` | Keyword-only args; builds DLQ record and produces to topic |
| `flush(timeout=5.0)` | `None` | Delegates to underlying producer |

**Not present** (do not test): `produce()`, `open()`, `close()` — these belonged to the defunct `DLQKafkaProducer`.

---

### Entity 2: DLQSink Protocol

**Source**: `pipelines/shared/dlq_protocol.py`  
**Role**: Structural interface that `ProcessingDLQSink` must satisfy. Used for type-checking.

| Method | Signature |
|--------|-----------|
| `send` | `send(source_topic, original_payload, error_type, error_message) -> None` |

**Relationship**: `ProcessingDLQSink` satisfies `DLQSink` via structural subtyping (no explicit inheritance required).

---

### Entity 3: Runtime Credential Fixture

**Source**: `tests/fixtures/tls/` (`cert_generator.py` + `__init__.py`)  
**Role**: Generates ephemeral PKI material at test runtime; never written to the tracked working tree.

| Output Key | Type | Description |
|------------|------|-------------|
| `ca_cert` | `Path` | CA certificate (PEM) in a process-scoped temp dir |
| `ca_key` | `Path` | CA private key (PEM) |
| `server_cert` | `Path` | Server certificate signed by CA |
| `server_key` | `Path` | Server private key |
| `client_cert` | `Path` | Client certificate signed by CA |
| `client_key` | `Path` | Client private key |

**Lifecycle**: Created on first call to `get_tls_cert_path()`, removed by `atexit` handler at process exit. Tests retrieve paths via `get_tls_cert_path(name)`.

---

### Entity 4: CI Workflow Gate

**Source**: `.github/workflows/ci.yml`  
**Role**: The pipeline of checks that must all exit 0 for a build to be green.

| Stage | Check | Dependency |
|-------|-------|-----------|
| code-quality | `ruff check .` + `ruff format --check .` | None (runs first) |
| unit-tests | `pytest tests/unit/ --cov=pipelines --cov-fail-under=20` | Needs code-quality |
| schema-integrity | schema contract comparison | Needs unit-tests |
| schema-registry-compat | Kafka + Schema Registry spin-up | Needs unit-tests |
| security-scan | pip-audit + Trivy | Independent |
| integration-tests | Docker Compose full stack | Needs schema-integrity |

**Key constraint**: code-quality failing blocks unit-tests, which blocks everything downstream. Clearing the 13 ruff violations unblocks the entire pipeline.

---

## State Transitions

### Test File Fix State Machine

```
test_dlq_sink.py
    BROKEN (DLQKafkaProducer undefined, 9×F821)
        → [rename class refs + rewrite test bodies]
    PASSING (all 5 test classes use ProcessingDLQSink.send()/flush())
```

### Ruff Violation State Machine

```
cert_generator.py
    FAILING (1×E402 + 3×F401)
        → [move import ipaddress; remove TYPE_CHECKING duplicates]
    CLEAN

conftest_security.py
    FAILING (1×I001)
        → [re-order import block]
    CLEAN
```

---

## No New Entities

This fix introduces no new classes, tables, topics, or schemas. The entities above are pre-existing; the fix aligns test code to match reality.
