# Feature Specification: Restore CI Green Build

**Feature Branch**: `023-fix-ci-green`  
**Created**: 2026-05-10  
**Status**: Draft  
**Input**: User description: "The CI pipeline has been failing due to multiple test and infrastructure issues that emerged after Phase 0 test infrastructure deployment."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DLQ Sink Tests Pass (Priority: P1)

A developer who worked on the ProcessingDLQSink interface refactor needs all existing DLQ-related tests to recognise the new interface, so the CI build stops failing on test collection.

**Why this priority**: The DLQ class-name mismatch is the most direct cause of test-suite failures; every CI run exits with errors before any meaningful coverage is exercised. Fixing this unblocks the rest of the test suite.

**Independent Test**: Can be fully tested by running the DLQ-related test files in isolation and confirming zero import or attribute errors, delivering a passing test module.

**Acceptance Scenarios**:

1. **Given** the repository contains tests that previously imported `DLQKafkaProducer`, **When** the test suite runs, **Then** all DLQ-related tests collect and execute without unresolvable name errors.
2. **Given** a developer makes no changes beyond updating test references, **When** CI runs the test step, **Then** every DLQ sink test reports a pass or an intentional skip (no failures).
3. **Given** the ProcessingDLQSink interface contract is stable, **When** new tests are added for the DLQ module, **Then** they can import and use the correct interface without additional shim or alias.

---

### User Story 2 - Secrets-Free Test Fixtures (Priority: P2)

A security-conscious team lead needs all TLS certificate material to be generated at test runtime rather than stored in source files, so that no secret-scanning tool flags the repository.

**Why this priority**: Hardcoded secrets in test fixtures create a permanent compliance liability; every future commit re-exposes the material. Eliminating them protects the repository regardless of which CI run is inspected.

**Independent Test**: Can be fully tested by running the security-module test suite on a clean checkout and confirming that no certificate files or key literals exist in tracked source files, delivering a compliant repository state.

**Acceptance Scenarios**:

1. **Given** the repository is freshly cloned, **When** a secret-scanning tool analyses all tracked files, **Then** it reports zero certificate, private-key, or PEM-block findings.
2. **Given** the security test suite runs, **When** tests that require TLS material execute, **Then** they generate the required credentials at runtime and clean up after themselves without writing anything to the working tree.
3. **Given** a developer adds a new TLS-dependent test, **When** they follow the project's test fixture pattern, **Then** they can obtain a valid credential set without checking in any secret material.

---

### User Story 3 - Linting Checks Pass (Priority: P3)

A developer wants the linting step in CI to produce zero warnings or errors, so the code-quality gate is reliable and does not mask real issues with noise.

**Why this priority**: Linting failures are fast to detect and fix in isolation; resolving them immediately reduces CI noise and prevents the pattern of accumulating technical debt.

**Independent Test**: Can be fully tested by running the linter against the affected modules and confirming a clean exit code, delivering a noise-free quality report.

**Acceptance Scenarios**:

1. **Given** the codebase has outstanding import-order, unused-import, and line-length violations, **When** the linter runs across the full source tree, **Then** it exits successfully and produces no diagnostic output.
2. **Given** a developer edits any file in the repository, **When** they run the linter locally before committing, **Then** they receive immediate feedback only on their new changes, not pre-existing issues.
3. **Given** the linting configuration is unchanged, **When** CI runs the format-check step, **Then** it passes without requiring any reformatting.

---

### User Story 4 - Stable, Non-Flaky Test Suite (Priority: P4)

A developer merging a pull request needs confidence that a test failure signals a real regression, not a transient timing issue or environment race condition in the security or processing modules.

**Why this priority**: Flaky tests erode trust in the CI signal; once developers learn to ignore failures they stop acting on them. Stabilising the suite preserves the value of every subsequent green run.

**Independent Test**: Can be fully tested by running the security and processing test modules ten times consecutively and observing consistent pass/fail results, delivering a reliable quality signal.

**Acceptance Scenarios**:

1. **Given** the security test module is run repeatedly in CI, **When** no code changes are made between runs, **Then** the result is identical across at least ten consecutive executions.
2. **Given** the processing test module contains timing-sensitive assertions, **When** the suite runs under normal CI load, **Then** no test fails due to a race condition or resource contention.
3. **Given** a test is intentionally skipped for a known infrastructure dependency, **When** CI runs, **Then** the skip is recorded with a clear reason and does not count as a failure.

---

### Edge Cases

- What happens when credential generation fails at runtime due to a missing system dependency?
- How does the test suite behave if the DLQ topic is unavailable during test execution?
- What if a linting rule conflict exists between the format-check step and the lint-check step?
- How are tests that were previously masked by import errors categorised once those errors are resolved — pre-existing failures vs. newly discovered regressions?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The test suite MUST locate and invoke all DLQ-related test cases without encountering unresolvable name references to the former interface.
- **FR-002**: Every tracked source file MUST be free of hardcoded certificate material, private keys, or any other secret literal detectable by an automated scanning tool.
- **FR-003**: Tests that require TLS material MUST obtain it through a runtime generation mechanism that produces and disposes of the material within the test lifecycle.
- **FR-004**: The linter MUST report zero violations across all source files when run with the project's standard configuration, covering import ordering, module-level imports after code, deprecated type annotations, unused imports, and line-length limits.
- **FR-005**: The formatter MUST confirm that all source files conform to the project's formatting rules without requiring any changes.
- **FR-006**: The security and processing test modules MUST produce identical pass/fail results across repeated, unmodified runs in the CI environment.
- **FR-007**: All three CI workflow steps (test execution, lint check, format check) MUST exit with success codes on every push to the main branch.
- **FR-008**: The main branch MUST maintain a consecutive green build record of at least five commits following the completion of this work.

### Key Entities

- **CI Workflow**: The automated pipeline that runs test, lint, and format checks on every push and pull-request event; its exit status is the primary success signal.
- **DLQ Sink Interface**: The current public contract for dead-letter-queue message handling; all test references must align with this interface name.
- **Runtime Credential Fixture**: A temporary TLS credential set generated during test setup and discarded after teardown; must not persist in the repository.
- **Linting Rule Set**: The configured set of code-quality rules applied uniformly across the source tree; violations block the CI workflow.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All automated quality checks (tests, lint, format) pass on the first CI run after this work is merged, with no manual intervention required.
- **SC-002**: The automated secret-scanning tool reports zero findings across all tracked source files after the changes are merged.
- **SC-003**: The main branch records at least five consecutive green builds following this fix, confirming the solution is durable and not a one-off pass.
- **SC-004**: Repeated runs of the security and processing test modules produce consistent results, with a flakiness rate of 0% over ten consecutive executions in a stable environment.
- **SC-005**: The linter exits clean with zero diagnostics on the full source tree, eliminating all previously reported categories of violation.
- **SC-006**: No existing passing test is inadvertently broken by the changes introduced in this feature; the overall test-pass count is equal to or higher than before.

## Assumptions

- The `ProcessingDLQSink` interface is stable and will not change again before this fix is merged; if it changes, test references will need a further update.
- The CI environment provides the system libraries necessary for on-the-fly TLS credential generation; no additional infrastructure provisioning is required.
- The five categories of linting violations (import ordering, module-level imports after code, deprecated type annotations, unused imports, line length) are the complete set; no new categories are introduced by other in-flight branches.
- Flakiness in the security and processing modules is caused by non-deterministic test setup or teardown, not by bugs in the production code under test; fixing test isolation is sufficient.
- The main branch is the target for this work and remains unblocked for direct merge after review.
- GitGuardian is the authoritative secret-scanning tool in use; satisfying its rules is the definition of "no hardcoded secrets".
