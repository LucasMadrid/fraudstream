# Feature Specification: Document & Contain `apache-flink` ↔ `analytics` pyarrow Conflict (#31)

**Feature Branch**: `027-flink-pyarrow-conflict`
**Created**: 2026-05-27
**Status**: Draft
**Input**: User description: "check the current open issue and create an spec from it" → GitHub issue **#31** (Tech debt: `apache-flink` `pyarrow<21` upper bound conflicts with `analytics` extras after SD-026)

## User Scenarios & Testing *(mandatory)*

The audience here is split between three personas, and each gets one user story. All three should be addressable by this feature; the recommendation from issue #31 is to ship them as a single small change rather than three PRs.

### User Story 1 — Contributor sets up a working local dev environment without confusion (Priority: P1) 🎯 MVP

A new contributor (or any contributor returning to the repo after time away) tries to install the project locally for development. They follow whatever onboarding instructions exist and either run `uv pip install -e ".[dev,processing,scoring,analytics]"` or hit the same instinct of "give me everything". The install fails with a resolver error referencing `apache-flink` and `pyarrow`. Today the failure is unexplained — there is no documentation that combined extras are intentionally mutually exclusive. The contributor wastes time hunting for the cause before discovering the prior SD-026 PR description.

**Why this priority**: This is the only persona that gets *blocked*. The other two suffer only from signal degradation. Unblocking contributors is the highest impact.

**Independent Test**: A reasonably technical engineer who has not seen issue #31 can read the project's onboarding documentation, understand within 60 seconds why a single combined-extras install is not possible, and choose the correct per-extras install pattern for their task without asking another team member.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the repo, **When** the contributor opens the project's onboarding/setup documentation, **Then** they find an explicit, named section explaining why `[analytics]` and `[processing,scoring]` are mutually exclusive in a single venv
2. **Given** that section, **When** the contributor wants to work on analytics code, **Then** the documentation tells them which extras to install and offers a recommended `uv` invocation
3. **Given** that section, **When** the contributor wants to work on processing/scoring code, **Then** the documentation tells them which extras to install and offers a recommended `uv` invocation
4. **Given** that section, **When** the contributor needs both sets simultaneously (a rare-but-real case), **Then** the documentation describes a per-extras venv pattern and provides commands to set it up

---

### User Story 2 — Security reviewer can trust the pip-audit signal on the scoring/processing image (Priority: P1)

A security reviewer (could be human or automated) runs `pip-audit` against the scoring/processing service image. The output reports `PYSEC-2026-113` (the pyarrow use-after-free). The reviewer has no in-image context for whether this is exploitable. Today they have to either accept the finding as load-bearing risk and chase a fix that doesn't exist, or trust an out-of-band claim that the vulnerable code path isn't exercised. Either way, the noise erodes the value of every future `pip-audit` run.

**Why this priority**: Audit hygiene is foundational to defense-in-depth. A scanner whose findings nobody can act on is worse than no scanner at all — it normalises ignoring red.

**Independent Test**: A new security reviewer with no prior context can examine the scoring/processing image's `pip-audit` output, find a clearly documented justification for any unsuppressed-or-suppressed `PYSEC-2026-113` finding, and reach an evidence-based conclusion in under 5 minutes that does not require asking another engineer.

**Acceptance Scenarios**:

1. **Given** a `pip-audit` run on the scoring/processing image, **When** `PYSEC-2026-113` appears (or is suppressed), **Then** there is a co-located, in-repo justification — either an exception file with a written rationale, or a documented decision to leave the finding visible
2. **Given** the justification, **When** the reviewer reads it, **Then** they understand that the vulnerable Arrow IPC-read path is not exercised by the scoring/processing runtime
3. **Given** the justification, **When** the upstream constraint changes (e.g., apache-flink lifts its pyarrow ceiling), **Then** the suppression/justification has an explicit revisit signal (a linked tracking issue or a dated comment) so it does not become permanent tech debt

---

### User Story 3 — Operator can answer "is uv.lock still authoritative?" (Priority: P2)

The repo carries a `uv.lock` file. After SD-026, that lock file cannot be regenerated cleanly — the same `apache-flink` / `pyarrow` conflict that breaks combined installs also breaks `uv lock`. CI today installs via `pip install -e ".[...]"` (which ignores the lock), so the lock is silently auxiliary. An operator opening the file does not know whether to trust it, regenerate it, or delete it.

**Why this priority**: P2 because the lock isn't load-bearing for CI or production. But it is a footgun: a contributor who runs `uv sync` expecting the lock to be authoritative will get the wrong answer.

**Independent Test**: An operator looking at the repo can tell, without asking anyone, whether `uv.lock` is authoritative, advisory, or deprecated, and what to do if they need to regenerate it.

**Acceptance Scenarios**:

1. **Given** the repo, **When** an operator opens `uv.lock` or its directory, **Then** they find a clear in-repo answer (in `CONTRIBUTING.md`, a header comment in `uv.lock` itself, or a `README` near it) describing its current authority status
2. **Given** that documentation, **When** the operator's task requires a different decision (regenerate / drop / freeze), **Then** the steps are documented and reproducible

---

### Edge Cases

- **The upstream `apache-flink` constraint lifts after this work ships**: a contributor reads the documentation, learns the install is split intentionally, and follows the recommended per-extras pattern — even though the underlying conflict no longer exists. This is a transient inefficiency, not a correctness problem. The documentation should carry a "revisit when upstream lifts the pyarrow ceiling" signal so it doesn't outlive its usefulness.
- **A new transitive constraint introduces a *different* conflict**: this spec's scope is the `apache-flink` ↔ pyarrow conflict only. New conflicts are out of scope and get their own issues.
- **A contributor edits `pyproject.toml` to add a fifth dep group that conflicts with one of the existing four**: the documentation should phrase the per-extras topology as a *pattern* (per-service container = per-extras install), not as an enumeration of the four current groups.
- **CI changes to use `uv sync` instead of `pip install -e ".[...]"` later**: the lock-authority decision documented in US3 must remain coherent. If CI starts depending on the lock, the lock's status flips from "advisory" to "authoritative" and the documentation needs to flip with it. Cross-link from CI config to the authority statement so future-CI-author has a single place to update.
- **`pip-audit` upstream renames or splits `PYSEC-2026-113`**: any suppression must use a syntax that survives advisory-ID drift, or include both PYSEC and CVE forms.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository MUST contain a single, discoverable section of contributor-facing documentation that explains the per-extras install topology. "Discoverable" means linked from `README.md` or named in a file a new contributor would naturally open (`CONTRIBUTING.md`, `docs/getting-started.md`, or equivalent).
- **FR-002**: The documentation MUST state, for each named extras group (`dev`, `processing`, `scoring`, `analytics`), which `uv pip install` invocation is recommended for working on code that touches that group.
- **FR-003**: The documentation MUST explain the underlying cause of the constraint (apache-flink upper-bounds pyarrow; analytics requires the post-CVE-fix lower bound) in plain language, without requiring the reader to chase external links to understand the *what*.
- **FR-004**: The repository MUST carry an evidence-based justification for the `PYSEC-2026-113` finding on the scoring/processing image. Format options (a `pip-audit` ignore file, a `SECURITY.md` exceptions section, or equivalent) are a planning concern; the requirement is that the justification is co-located with the codebase and not buried in chat history or external tickets.
- **FR-005**: If `PYSEC-2026-113` is suppressed at the scanner level, the suppression MUST carry a revisit signal — a linked tracking issue (e.g., #31) or an explicit "revisit when apache-flink lifts pyarrow ceiling" note — so the suppression does not become indefinite.
- **FR-006**: The repository MUST clearly communicate the current authority status of `uv.lock`. Whatever the status (authoritative / advisory / frozen / deprecated), it MUST be a single answer and it MUST be findable.
- **FR-007**: If the decision is to keep `uv.lock` in the repo despite it being non-regenerable, the documentation MUST explain how to handle the case where a future contributor needs to regenerate it (and what blocks that today).
- **FR-008**: Closing GitHub issue #31 MUST be achievable by the PR that implements this feature (one PR = full close). Partial closure is not in scope.

### Key Entities *(include if feature involves data)*

Not applicable — this feature is documentation + scanner-policy. No new runtime entities.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An engineer who has never seen issue #31 can, within 5 minutes of cloning the repo, identify the correct `uv pip install` invocation for the part of the codebase they want to work on, without consulting another team member, external tickets, or chat history.
- **SC-002**: A security reviewer running `pip-audit` on the scoring/processing image reaches a justified accept/escalate decision on `PYSEC-2026-113` within 5 minutes of reading the in-repo justification, without needing to ask "is this exploitable?"
- **SC-003**: GitHub issue #31 transitions to `closed` state at PR-merge time, with all four checkboxes in its Acceptance Criteria checked.
- **SC-004**: No new instances of a contributor opening a duplicate of issue #31 are filed in the first 90 days after merge. (Measured by tag review — if a new issue arises about the same conflict, the documentation is failing its job.)
- **SC-005**: The decision recorded for `uv.lock` authority is unambiguous: an outside reader can answer "should I run `uv sync`?" with a single yes/no/explanation, without further investigation.

## Assumptions

- The recommendation from issue #31 is **Option D** (document + scoped scanner justification + watch for upstream fix). This spec adopts that recommendation as scope. Options A, B, and C from the issue are explicitly out of scope and require their own features if pursued.
- The repository's existing onboarding entry points (`README.md`, possibly `CONTRIBUTING.md` if one exists or is created) are the right surface for the contributor-facing documentation. If neither exists, creating `CONTRIBUTING.md` is in scope; restructuring how onboarding is organized more broadly is not.
- The scoring/processing service deployment topology (separate container per extras group) is stable and load-bearing. This spec assumes that topology persists; if it changes (e.g., services merge), the suppression rationale would need re-evaluation as a separate concern.
- An upstream `apache-flink` tracking issue may or may not exist. If one is found during planning, it gets linked into the documentation; if not, the revisit signal can still be expressed as a dated TODO without an upstream link.
- The 90-day duplicate-issue window in SC-004 starts at the merge date and is a soft signal — one duplicate within the window does not invalidate the feature, but a pattern (≥2) suggests the documentation needs revision.
