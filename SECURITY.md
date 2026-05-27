# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities via GitHub's private vulnerability reporting (Security tab → "Report a vulnerability"). Do not file public issues for unpatched vulnerabilities.

## Known scanner findings & justifications

This section documents `pip-audit` (or equivalent) findings that surface against this repository's images and the rationale for accepting each one as a known, evidence-based exception. Each entry MUST carry a revisit signal so exceptions do not become permanent tech debt.

### PYSEC-2026-113 / CVE-2026-25087 — `pyarrow` use-after-free on the scoring/processing image

**Affected image**: any image built from the `[processing]` or `[scoring]` optional-dependency groups (the scoring service container and the PyFlink job-submission container both fall in this set).

**Resolved version on image**: `pyarrow==16.1.0`. This is forced down by `apache-flink>=2.0`'s declared `pyarrow<21.0.0` upper bound. The `analytics` Streamlit image, which uses a separate optional-dependency group, gets `pyarrow==24.0.0` and is **not** affected by this finding.

**Why not fixed**: The advisory's fix version is `pyarrow>=23.0.1`. That floor is unreachable while `apache-flink>=2.0` remains pinned to `pyarrow<21.0.0`. Lifting the constraint requires either an upstream Apache Flink release that raises its pyarrow ceiling, or migrating off `apache-flink` for the streaming runtime — a large architectural change tracked separately (not in scope for SD-027).

**Exposure assessment**: The vulnerable code path is `pyarrow`'s Arrow IPC file reader with pre-buffering enabled. The scoring/processing runtime does **not** read Arrow IPC files — `pyarrow` is present only as a transitive dependency of `apache-flink`'s PyFlink job-submission tooling, which uses Parquet (via `pyiceberg`) for persistent IO and Avro (via `fastavro`) for Kafka serialization. The vulnerable surface is not exercised in the running container.

**Revisit signal**: When `apache-flink` upstream lifts the `pyarrow<21.0.0` constraint, re-evaluate this exception. The expected change is for a future `apache-flink` 2.x release to widen the upper bound; we should track that release and refresh this entry within 30 days of its publication. If no upstream change has occurred by **2026-11-27** (six months from creation), this exception MUST be reviewed and either renewed with updated rationale or escalated.

**Last reviewed**: 2026-05-27.

**See also**: `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`, GitHub issue #31, PR #30 (SD-026).
