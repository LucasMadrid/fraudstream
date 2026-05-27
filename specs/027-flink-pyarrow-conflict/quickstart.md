# Quickstart: Document & Contain `apache-flink` ↔ `analytics` pyarrow Conflict (#31)

**Branch**: `027-flink-pyarrow-conflict`

A walk-through for the contributor implementing this feature, and for the reviewer verifying it.

## What changes

Four documentation artifacts; zero source-code changes; zero CI-config changes.

| Action | File | Status |
|---|---|---|
| Create new ADR | `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md` | NEW |
| Add install-topology section | `README.md` | MODIFIED |
| Create security exceptions doc | `SECURITY.md` | NEW |
| Add advisory-lock header comment | `uv.lock` | MODIFIED (1 line) |

## Author walk-through

### Step 1 — Write `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`

Use the established ADR style (see `ADR-009` or `ADR-014`). Required content:

- **Title**: short, e.g. "Per-extras install topology is load-bearing for production"
- **Problem statement**: `apache-flink` (in `processing`/`scoring` extras) requires `pyarrow<21`. `analytics` (post SD-026) requires `pyarrow>=23.0.1`. A single venv cannot satisfy both.
- **Decision**: Each production service runs in its own container with only its own extras; local development MUST follow the same per-extras pattern. The two extras families (`[processing,scoring]` vs `[analytics]`) are mutually exclusive in a single Python environment.
- **Consequences**:
  - Contributors must use one of two install recipes (one per area of work) — documented in `README.md`.
  - `uv.lock` cannot be regenerated cleanly; treated as advisory until the upstream constraint lifts.
  - `pip-audit` against a hypothetical scoring/processing image surfaces `PYSEC-2026-113`; the documented rationale lives in `SECURITY.md`.
- **Revisit signal**: When `apache-flink` upstream lifts the `pyarrow<21` ceiling, revisit this ADR; the constraint may relax to "load-bearing for now, simplify later".
- **References**: link to GitHub issue #31, PR #30 (SD-026), and `SECURITY.md`.

### Step 2 — Update `README.md`

Modify the Quick Start section near `README.md:67-71` to:

1. Keep the existing first instruction but clarify it's the **processing/scoring** install
2. Add a sibling instruction for the **analytics** workflow
3. Add a single sentence: "These extras groups are mutually exclusive in a single venv — see ADR-015 for why."
4. Hyperlink ADR-015

Suggested patch shape:

```markdown
## 🚀 Quick Start

```bash
# 1a. Install Python deps — pick ONE of these depending on what you're working on
#     (these extras families are mutually exclusive in a single venv;
#      see docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md for why)

# For processing / scoring / ingestion work:
pip install -e ".[dev,processing,scoring]"

# OR for analytics / Streamlit work:
pip install -e ".[dev,analytics]"
```
```

### Step 3 — Create `SECURITY.md` at repo root

Use a tight, scannable structure:

```markdown
# Security Policy

## Reporting a vulnerability
<short note: prefer GitHub Security Advisories>

## Known scanner findings & justifications

### PYSEC-2026-113 / CVE-2026-25087 — `pyarrow` use-after-free on scoring/processing image

**Affected image**: scoring/processing service (any image built from
`[processing]` or `[scoring]` extras).
**Resolved version on image**: `pyarrow==16.1.0` (forced down by
`apache-flink>=2.0`'s declared `pyarrow<21.0.0` upper bound).
**Why not fixed**: `apache-flink>=2.0` declares `pyarrow<21.0.0`. The fix
version (`pyarrow>=23.0.1`) is not reachable without dropping or forking
`apache-flink`.
**Exposure assessment**: The vulnerable code path is `pyarrow`'s Arrow IPC
file reader with pre-buffering enabled. The scoring/processing runtime
does not read Arrow IPC files — `pyarrow` is present only as a transitive
dependency of `apache-flink`'s PyFlink job-submission tooling. The
vulnerable surface is not exercised in the running container.
**Revisit signal**: When `apache-flink` upstream lifts the `pyarrow<21`
constraint, re-evaluate. Tracking: <link to upstream JIRA if found>.
**Last reviewed**: 2026-05-27.
**See also**: `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`, issue #31.
```

### Step 4 — Add header comment to `uv.lock`

Prepend a single TOML comment block to `uv.lock` (lock-file format accepts comments):

```toml
# STATUS: ADVISORY (as of 2026-05-27)
#
# This lock file cannot currently be regenerated because the combined
# extras set hits an unresolvable apache-flink / pyarrow constraint
# conflict. CI installs via `pip install -e ".[...]"` and does not
# consult this file. See docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md.
```

Do not modify any other lines in `uv.lock`.

## Reviewer walk-through

1. **ADR-015 sanity check**: open it, confirm the four required fields (Problem / Decision / Consequences / Revisit) are present and concise.
2. **README install section**: from a fresh terminal, follow each `pip install` command and confirm it resolves (one will need `[processing,scoring]`, the other `[analytics]`). The SC-001 5-minute comprehension gate is the test.
3. **`SECURITY.md`**: open it cold (without prior context). Does the `PYSEC-2026-113` block let you answer "is this exploitable in our scoring runtime?" in under 5 minutes? If not, revise.
4. **`uv.lock` header**: top of file should carry the ADVISORY comment; no other lines changed (verify via `git diff uv.lock | head -20`).
5. **Issue #31 acceptance criteria**: re-read the four checkboxes on the issue; each should be addressable by the PR.

## Verification commands

```bash
# All four ADR-required fields appear in ADR-015
grep -E '^## (Problem|Decision|Consequences|Revisit)' docs/adr/ADR-015-*.md

# README has the new install section
grep -A2 'mutually exclusive in a single venv' README.md

# SECURITY.md documents PYSEC-2026-113 with all required subsections
grep -E 'PYSEC-2026-113|Exposure assessment|Revisit signal|Last reviewed' SECURITY.md

# uv.lock carries the advisory header
head -5 uv.lock | grep 'ADVISORY'

# CI still passes (no behavior change → trivially preserved)
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/unit/ --cov=pipelines --cov-fail-under=20
```

## Closing the issue

The PR's commit message MUST include `Closes #31` so GitHub auto-closes the originating issue on merge.
