# Feature Specification: CI Green Recovery

**Feature Branch**: `022-ci-green-recovery`  
**Created**: 2026-05-11  
**Status**: Draft  
**Input**: User description: "Fix test collection errors and achieve green CI"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tests Can Be Collected (Priority: P1)

As a developer, I want to run `make test` and have pytest collect all tests without import errors.

**Why this priority**: Without test collection, no tests can run. This is a complete blocker for all development.

**Independent Test**: Run `python -m pytest tests/ --collect-only` and verify 0 collection errors.

**Acceptance Scenarios**:

1. **Given** the Python 3.9 environment is activated, **When** I run `python -m pytest tests/ --collect-only`, **Then** all 800+ tests are collected with 0 errors
2. **Given** dependencies are installed, **When** importing test modules, **Then** no ImportError exceptions occur

---

### User Story 2 - CI Code Quality Gate Passes (Priority: P1)

As a developer, I want PR #20 to pass the Code Quality Gate so it can be merged.

**Why this priority**: The PR is blocked and cannot be merged until quality checks pass.

**Independent Test**: Run `ruff check .` and `ruff format .` with exit code 0.

**Acceptance Scenarios**:

1. **Given** the codebase, **When** running `ruff check .`, **Then** no linting errors are reported
2. **Given** the codebase, **When** running `ruff format --check .`, **Then** all files are properly formatted

---

### User Story 3 - Docker Infrastructure is Healthy (Priority: P2)

As a developer, I want `make infra-up` to start all containers in a healthy state.

**Why this priority**: Integration tests require healthy infrastructure. Without it, we can't validate the full system.

**Independent Test**: Run `make infra-up` and verify all containers report healthy status.

**Acceptance Scenarios**:

1. **Given** Docker is running, **When** I run `make infra-up`, **Then** jobmanager, taskmanager, broker, and schema-registry containers start and report healthy
2. **Given** containers are running, **When** I run `docker compose ps`, **Then** all services show healthy status

---

### Edge Cases

- What happens when a test module references a class that was renamed? (ImportError - needs test update)
- How does the system handle missing optional dependencies? (Graceful skip or clear error message)
- What if Python version has incompatible syntax? (Use compatible syntax for target version)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All test modules MUST be importable without ImportError
- **FR-002**: DLQKafkaProducer references MUST be updated to ProcessingDLQSink  
- **FR-003**: `_FEATURE_ZERO_DEFAULTS` references MUST be resolved or removed
- **FR-004**: Missing dependencies (httpx, pandas) MUST be installable
- **FR-005**: Code MUST pass ruff linting checks
- **FR-006**: Code MUST pass ruff format checks
- **FR-007**: Docker containers MUST start and report healthy status
- **FR-008**: Coverage gate MAY be temporarily lowered to unblock CI

### Key Entities *(include if feature involves data)*

- **Test Module**: Python file containing pytest test cases
- **Docker Service**: Containerized component (jobmanager, taskmanager, broker, schema-registry)
- **CI Gate**: GitHub Actions workflow check (Code Quality, Security, Unit Tests)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `python -m pytest tests/ --collect-only` completes with 0 errors
- **SC-002**: `ruff check .` exits with code 0
- **SC-003**: `ruff format --check .` exits with code 0
- **SC-004**: `make infra-up` results in all containers showing healthy status
- **SC-005**: PR #20 CI status changes from BLOCKED to MERGEABLE
- **SC-006**: Test collection time is under 10 seconds for 800+ tests

## Assumptions

- Python 3.9 is the target environment (CI uses 3.9, local may vary)
- Docker Desktop is available and running for local development
- The `specify` CLI is installed for feature workflow
- Test failures after collection are acceptable for this feature (fixing collection is the goal)
- Some test updates may be needed if production code APIs changed
