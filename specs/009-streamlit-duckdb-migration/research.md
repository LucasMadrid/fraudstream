# Research: Streamlit DuckDB Migration

## Decision 1: PyIceberg → Arrow → DuckDB query pattern

**Decision**: Use `pyiceberg` to open the Iceberg catalog, call `table.scan(row_filter=...)` with a time-window predicate, convert to a PyArrow `Table` via `to_arrow()`, register the Arrow table as a DuckDB in-memory view, then run SQL.

**Rationale**: PyIceberg already exists in the stack (installed as a dependency of feature 006). The `scan()` call accepts an Iceberg `GreaterThanOrEqual` / `LessThan` expression that maps directly to Iceberg partition pruning — only the relevant Parquet files are fetched from MinIO. DuckDB registers Arrow tables as zero-copy views (no serialisation overhead). This keeps the entire query in-process with no network hop to an external container.

**Alternatives considered**:
- `trino-python-client` — reachable only via Docker DNS (`trino:8080`); DNS breaks after container restarts. Rejected (root cause of the current bug).
- `duckdb-iceberg` extension — DuckDB's native Iceberg extension requires DuckDB ≥ 0.10 and the REST catalog endpoint. Viable path but adds another network dependency (REST catalog) and the extension is still maturing. PyIceberg → Arrow → DuckDB avoids the REST dependency entirely for Streamlit queries.
- Pandas + direct Parquet read — would require manual file enumeration from the MinIO bucket, bypassing Iceberg snapshot isolation and partition metadata. Rejected (correctness concern).

---

## Decision 2: DuckDB version and import pattern

**Decision**: Use `duckdb>=1.0` (currently `1.x` is stable). Import as `import duckdb; conn = duckdb.connect()` (in-memory, no file). Register Arrow table with `conn.register("tbl", arrow_table)` then `conn.execute("SELECT ... FROM tbl WHERE ...")`.

**Rationale**: In-memory connection avoids file locking issues in multi-threaded Streamlit. `conn.register()` is zero-copy. DuckDB 1.x is API-stable; `duckdb.connect()` without a path is the idiomatic in-process pattern.

**Alternatives considered**:
- Persistent DuckDB file on disk — unnecessary for read-only analytics queries; adds disk I/O and file locking complexity. Rejected.
- DuckDB connection pool — Streamlit reruns create fresh connections per invocation; a pool would leak unless explicitly managed. In-memory connection per query is simpler and correct.

---

## Decision 3: PyIceberg catalog configuration inside Streamlit container

**Decision**: Configure PyIceberg via environment variables already present in the Streamlit docker-compose service:
- `PYICEBERG_CATALOG__ICEBERG__URI=http://iceberg-rest:8181` 
- `PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/`
- `PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://minio:9000`
- `PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true`

These are set via docker-compose environment section. The query modules call `load_catalog("iceberg")` which reads these automatically.

**Rationale**: Consistent with how `flink-job-analytics` already configures PyIceberg. No new config keys needed.

**Alternatives considered**:
- Hardcoded catalog config dict passed to `load_catalog()` — less flexible, would need code changes to switch endpoints. Rejected in favour of env-var pattern.

---

## Decision 4: Rolling window predicate for partition pruning

**Decision**: Each query module accepts a `hours` parameter (default 24, max 720 = 30 days). Construct the Iceberg filter using `GreaterThanOrEqual("event_time", start_ms)` where `start_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)`. Pass this to `table.scan(row_filter=...)`.

**Rationale**: `event_time` is the partition column for both Iceberg tables (day-level partitioning). A time-range predicate on this column enables partition pruning — only Parquet files from relevant day-partitions are loaded from MinIO, keeping memory usage bounded.

**Alternatives considered**:
- Scan full table, filter in DuckDB — loads all historical data into Arrow before DuckDB sees it. For a 90-day table this could be hundreds of MB. Rejected (violates FR-005 and SC-005).

---

## Decision 5: 30-day cap enforcement

**Decision**: Add a `max_hours=720` constant in a shared `analytics/queries/config.py` module. Each query function clamps its `hours` argument: `hours = min(hours, max_hours)`. The Streamlit page sliders set `max_value=720`. Add a UI caption below each slider explaining the 30-day limit and pointing to Trino for longer ranges.

**Rationale**: Keeps the cap logic in one place (query layer) rather than scattered across pages. The page slider enforces it at UI level too (belt-and-suspenders).
