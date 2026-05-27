# Per-extras install topology is load-bearing for production

After SD-026 (PR #30) added a `pyarrow>=23.0.1` constraint to the `analytics` optional-dependencies group to remediate `PYSEC-2026-113` (Arrow C++ use-after-free), `uv pip install -e ".[dev,processing,scoring,analytics]"` fails to resolve.

The conflict is in upstream constraints:

| Group | Pulls | Pyarrow constraint |
|---|---|---|
| `processing`, `scoring` | `apache-flink>=2.0` | `pyarrow>=5.0.0,<21.0.0` |
| `analytics` | `pyiceberg[pyarrow,s3fs]>=0.11` + explicit pin | `pyarrow>=23.0.1` |

A single Python environment cannot satisfy `pyarrow<21` AND `pyarrow>=23.0.1`. This is not a bug in `pyproject.toml` — it reflects how the production services already deploy.

## Decision

The two extras families are mutually exclusive in a single venv. Each production service runs in its own container with only its own extras installed:

- The **scoring/processing** service image installs `[processing,scoring]` → pulls `apache-flink` → resolver picks `pyarrow 16.1.0` (forced down by the upstream upper bound). PyFlink's job-submission tooling is `pyarrow`'s only consumer in that image, and it does not exercise the vulnerable Arrow IPC-read path.
- The **analytics** Streamlit image installs `[analytics]` → pulls `pyiceberg` → resolver picks `pyarrow 24.0.0`. The CVE-affected versions are not reachable.

Local development MUST follow the same per-extras pattern. Two install recipes are documented in `README.md` Quick Start — contributors pick one based on the area they're working on. Contributors needing both must use two venvs.

## Consequences

- **Local dev**: `uv pip install -e ".[dev,processing,scoring]"` and `uv pip install -e ".[dev,analytics]"` are the two supported invocations. Any combined install fails — that failure is intentional, not a bug to fix.
- **`uv.lock`**: cannot be regenerated cleanly today because `uv lock` evaluates the combined extras set. Marked **ADVISORY** in a header comment at the top of the file. CI installs via `pip install -e ".[...]"`, not `uv sync`, so this is not a CI blocker.
- **Audit signal**: a `pip-audit` run against the scoring/processing image surfaces `PYSEC-2026-113`. The justification lives in `SECURITY.md` co-located with this repo, with a revisit signal tied to apache-flink upstream lifting the pyarrow ceiling. CI's current `pip-audit` step does not install the project, so no suppression file is required today — when scanner scope expands to per-image audits, the rationale is ready.

## Revisit

When `apache-flink` upstream lifts the `pyarrow<21.0.0` upper bound, revisit this ADR. The constraint may then relax to a recommendation rather than a load-bearing topology. Until then, this is architectural law.

## References

- Issue #31 — the originating finding
- PR #30 (SD-026) — the bump that surfaced the conflict
- `SECURITY.md` — `PYSEC-2026-113` exception rationale and revisit signal
- `pyproject.toml` — the four CVE-pinned dependencies (`avro`, `idna`, `pyarrow`, `starlette`)
