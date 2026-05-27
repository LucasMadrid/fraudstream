# Research: Document & Contain `apache-flink` ↔ `analytics` pyarrow Conflict (#31)

**Phase 0 output** | Branch: `027-flink-pyarrow-conflict` | Date: 2026-05-27

This feature is documentation-only. The Phase 0 research isn't about library choices — it's about confirming which doc surfaces exist, where similar decisions have landed, and what the *actual* (not assumed) scanner reality is so we don't over-engineer.

---

## R1 — Where should the per-extras topology decision live?

### Decision

Create **`docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`** as the canonical record of the decision. Link to it from `README.md` and from a header comment near `uv.lock`.

### Rationale

- The repo already has 14 ADRs in `docs/adr/` (ADR-001 through ADR-014). The next sequential number is **015** (verified via `ls docs/adr/`).
- Existing ADRs (e.g., `ADR-009-COMPOSITE-ALERT-SINK.md`) follow a "problem narrative → decision" structure with concise scope. The constraint conflict from issue #31 is a perfect fit: it's a load-bearing decision the team has *implicitly* made (by virtue of per-service container topology) without ever recording it.
- ADRs are immutable history. A `CONTRIBUTING.md` section *could* host the explanation, but ADRs better signal "this is intentional and durable".

### Alternatives considered

- **Add as a section in `README.md` only** — rejected. The decision is architectural, not onboarding-trivia. The README link should *point to* the ADR, not contain the rationale.
- **Create `CONTRIBUTING.md`** — rejected. The repo currently has no `CONTRIBUTING.md` and the README serves as the onboarding entry point (Quick Start at `README.md:67`). Introducing a new top-level surface fragments discovery.
- **Wait for the upstream apache-flink fix** — rejected by spec scope (Option A from issue #31 is explicitly out of scope; this feature ships Option D).

---

## R2 — Where should the pyarrow exception rationale live?

### Decision

Create **`SECURITY.md`** at the repo root with a dedicated "Known scanner findings & justifications" section. Within that section, document `PYSEC-2026-113` on the scoring/processing image with: scope, exposure assessment, revisit signal, and link to ADR-015.

### Rationale

- `SECURITY.md` does not yet exist in the repo (verified via `test -f SECURITY.md`). Creating it satisfies a GitHub-conventional surface (GitHub auto-links `SECURITY.md` from the Security tab) and gives future scanner exceptions a single home.
- A scanner-specific ignore file (e.g., `.pip-audit-ignore`) was rejected because **the CI scanner does not currently produce the finding** (see R3 below). Wiring up an ignore file for a finding nobody is currently seeing is premature mechanism — exactly the kind of work the project's instructions warn against ("Don't add features... beyond what the task requires").
- Documentation-only is enough today. When per-image audit lands in CI, that future PR adds the scanner-level suppression and links the rationale block from `SECURITY.md`.

### Alternatives considered

- **`.pip-audit-ignore` file with `PYSEC-2026-113` listed and a comment** — rejected as premature; see above.
- **Inline comment in `pyproject.toml` next to `pyarrow>=23.0.1`** — rejected. Comments inside `pyproject.toml` are easy to miss and not scannable.
- **Update `docs/security-policy.md` (if it exists)** — checked: no such file exists. Falling back to root `SECURITY.md` is the conventional location.

---

## R3 — What does the CI scanner actually see today?

### Decision

**No scanner-level change required for this feature.** CI's `pip-audit` step in `.github/workflows/ci.yml:Stage 4 (Security Scanning)` installs only `pip-audit` itself, not the project. Therefore the scanner today reports no findings about `pyarrow`, `starlette`, `idna`, or `avro` — the four CVEs found in the SD-026 spec were surfaced by **local** audit runs (developer venvs that had all extras installed), not by CI.

### Rationale

- Verified by reading `.github/workflows/ci.yml` Stage 4: only `python -m pip install --upgrade pip` and `pip install pip-audit` happen before the audit. The project itself is not installed.
- The "audit noise" concern in issue #31 (`pip-audit reports PYSEC-2026-113 on the scoring image`) is forward-looking. It describes what *would* happen if the audit were enhanced to scan the actual production images, not what CI currently does.
- The right scope for *this* feature is to **prepare the justification** so it's ready when scanner-scope expands. Adding a suppression file today would be a no-op (the scanner doesn't see the package) and would create maintenance overhead.

### Alternatives considered

- **Expand CI's `pip-audit` step to install the full extras matrix and add a suppression** — rejected as scope creep. That's a separate hardening initiative. This feature documents the rationale so the future scanner-expansion PR is a one-line wire-up.
- **Wire up Trivy or another container-level scanner** — out of scope.

---

## R4 — What's `uv.lock`'s authority status, and how do we communicate it?

### Decision

`uv.lock` is **ADVISORY** as of 2026-05-27. Mark this with a single-line comment at the top of the file (TOML comment syntax) and a sentence in ADR-015. Do not delete the file; do not attempt to regenerate it.

### Rationale

- CI installs via `pip install -e ".[dev,...]"` (verified in `.github/workflows/ci.yml`), which ignores `uv.lock` entirely. The lock has therefore never been load-bearing for green/red CI status.
- Regenerating the lock hits the same `apache-flink` / `pyarrow` resolver conflict that motivated this feature (`uv lock --upgrade-package starlette` failed during SD-026 implementation). Until the upstream constraint lifts, regeneration is impossible.
- Deleting the lock removes a (stale but readable) snapshot of one historically-resolvable dependency tree. An advisory lock is more informative than no lock.
- A comment in the file itself ensures discoverability — anyone opening `uv.lock` sees the status in the first 5 lines, without needing to consult external docs.

### Alternatives considered

- **Mark `uv.lock` as authoritative and figure out how to regenerate** — blocked by R3's root cause. Not feasible without resolving the upstream apache-flink constraint.
- **Delete `uv.lock`** — rejected. Loses historical context; some contributors expect to find a lock file.
- **Move to `requirements*.txt` files generated per-extras** — out of scope. Could be a future feature if the team standardizes on per-extras lock files; would be its own ADR.

---

## R5 — Is there an upstream apache-flink tracking issue?

### Decision

**Search post-merge, link if found.** Do not block the feature on finding/filing an upstream issue.

### Rationale

- The Apache Flink project's issue tracker is JIRA-based (`issues.apache.org/jira/projects/FLINK`), not GitHub. Locating an existing ticket requires manual search outside the scope of this Phase 0.
- The spec (Edge Case: "upstream constraint lifts") and the SECURITY.md revisit signal can both function with a TODO marker plus a dated comment. An upstream link is *nice to have*, not load-bearing.
- If no ticket exists, filing one is a worthwhile follow-up but doesn't belong in *this* feature (it would be a non-codebase action item).

### Alternatives considered

- **Block the feature until an upstream link is established** — rejected. Coupling our doc-quality to external project velocity is unwise.
- **File the upstream ticket as part of this PR** — rejected as out of scope; assign as a follow-up issue if needed.

---

## Open questions

None. All NEEDS CLARIFICATION items from the spec were resolved at spec time.
