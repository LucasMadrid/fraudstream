# Replay lifecycle extracted into `ReplayOrchestrator` in `pipelines/replay/`

`pipelines/scoring/management_api.py` is a god module (814 lines) that owns rule CRUD, circuit breaker state, replay orchestration, and health checks. The replay portion — `_replay_jobs: dict[str, ReplayJob]`, `_replay_jobs_lock`, and the route handlers for `POST /replay`, `GET /replay/{id}`, `DELETE /replay/{id}` — is a distinct lifecycle that cannot be tested without standing up the full FastAPI app. Job state is held in module-level memory: a process restart loses all in-progress jobs (SD-018).

`ReplayOrchestrator` is extracted to `pipelines/replay/orchestrator.py`. It owns the full replay lifecycle: job creation, status tracking, cancellation, and PostgreSQL-backed durability.

## Interface

```python
class ReplayOrchestrator:
    def __init__(self, db_conn) -> None: ...          # psycopg2 connection
    def create_job(self, config, scoring_fn) -> ReplayJob: ...
    def get_status(self, job_id: str) -> ReplayJob | None: ...
    def cancel(self, job_id: str) -> bool: ...
    def list_jobs(self) -> list[ReplayJob]: ...
```

## Design decisions

**scoring_fn ownership**: The management API constructs a `scoring_fn` closure from `_rules_dict` (snapshotted at job-creation time using `RuleEvaluator`) and passes it to `create_job`. The orchestrator knows nothing about rule evaluation — it receives a callable and forwards it to `ReplayJob.start()`. This keeps `ReplayOrchestrator` independent of `pipelines/scoring`.

**Persistence**: Dual-layer — an in-memory `_jobs: dict[str, ReplayJob]` for fast access and live thread handles (required for `cancel()`), plus PostgreSQL for durability. On startup the orchestrator reads job rows from the DB; any row with `status = running` is marked `aborted` (the thread is gone). SD-018 is closed: a restart no longer silently drops job history.

**Interface shape**: Sync methods (`psycopg2`, already in the dependency tree). The management API wraps the three route-handler calls in `run_in_executor`. Replay jobs are low-frequency; async-native (`asyncpg`) would add a second postgres driver with no benefit on a non-hot path.

**Module location**: `pipelines/replay/` — alongside `ReplayJob`. `ScoringConfig` is passed in at call sites; no cross-package import from `pipelines/scoring`.

## What the management API retains

Rule CRUD (`_rules_dict`, `_rules_lock`), circuit breaker state (`_circuit_breaker`), `_config`, and health-check endpoints. It holds a single `ReplayOrchestrator` instance constructed at app lifespan startup. On `POST /replay` it snapshots `_rules_dict` into a `scoring_fn` and delegates to the orchestrator.

## Test improvement

`ReplayOrchestrator` is testable directly against a real or in-memory-SQLite test DB — no FastAPI app, no asyncio fixtures required. The management API is testable with a mock orchestrator; the seam between them is now real (two callers: the route handler and the test).

## Considered alternatives

- *Keep replay in management_api.py, add PostgreSQL there* — persistence is added but locality is not. The god module grows; the replay lifecycle remains untestable in isolation. Rejected.
- *Async-native orchestrator with asyncpg* — cleaner call sites in async route handlers, but adds a second postgres driver and requires asyncio fixtures in tests. Not justified on a low-frequency, non-hot path. Rejected.
- *Pure DB, no in-memory dict* — every status read hits PostgreSQL; more importantly, `cancel()` loses the thread handle across in-process calls (not just restarts). Does not close SD-018 properly. Rejected.
