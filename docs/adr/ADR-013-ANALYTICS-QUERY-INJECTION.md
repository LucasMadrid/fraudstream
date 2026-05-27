# Analytics query functions accept injected Arrow tables; Iceberg loading moves to callers

`analytics/queries/duckdb_runner.py` defines `DuckDBQueryRunner` — a reusable wrapper that accepts named Arrow tables, registers them in a temporary DuckDB connection, and executes a query. Despite existing, it is not used by any of the three query modules:

- `analytics/queries/fraud_rate.py` — `_load_decisions()` creates `duckdb.connect()` directly and loads from Iceberg
- `analytics/queries/rule_triggers.py` — `_load_decisions_conn()` same pattern
- `analytics/queries/model_versions.py` — `_load_decisions_conn()` same pattern

Each query file embeds two concerns: (1) Iceberg catalog access and (2) query execution against the loaded tables. Because Iceberg loading is baked in, there is no seam to inject test data — unit tests require a live Iceberg catalog.

Applying the **deletion test**: deleting `DuckDBQueryRunner` has no effect on the running system (nothing imports it). The real gap is that the three query files are shallow — each `_load_decisions_conn()` helper reimplements the same pattern with no reuse.

## Solution

Query functions are changed to accept pre-loaded `pa.Table` arguments:

```python
# Before
def get_fraud_rate_daily(hours: int = 24) -> pd.DataFrame:
    conn = _load_decisions()   # loads from Iceberg internally
    ...

# After
def get_fraud_rate_daily(decisions: pa.Table, hours: int = 24) -> pd.DataFrame:
    conn = duckdb.connect()
    conn.register("decisions", decisions)
    ...
```

The Iceberg load moves to the Streamlit page, which already has catalog access:

```python
# In the Streamlit page
decisions = load_decisions_from_iceberg(catalog, hours=MAX_HOURS)
df = get_fraud_rate_daily(decisions, hours=selected_hours)
```

The `_load_decisions_conn()` helpers are deleted from all three query files. `DuckDBQueryRunner` is also deleted — with the `duckdb.connect()` + `register()` reduced to two lines inline, the wrapper adds no leverage.

## Design decisions

**Arrow tables, not a connection object.** Passing `pa.Table` keeps the query function pure: given the same table, it always returns the same result. Passing a `duckdb.DuckDBPyConnection` would expose session state (registered tables, settings) and make the function's behaviour dependent on the connection's prior history.

**Iceberg loading at the Streamlit page, not a shared loader.** The three pages already call Iceberg via `PyIceberg → Arrow` helpers; moving the `_load_decisions_conn()` call up one level is mechanical. A shared `load_decisions()` helper would be a thin pass-through — the **deletion test** says it would fail to earn its keep.

**`DuckDBQueryRunner` is deleted, not fixed.** One adapter = hypothetical seam. Since no query function used it and none will (the query functions now own their two-line `connect()` + `register()`), the class is dead. Keeping it as a "reusable abstraction" for a use-case that doesn't exist is complexity without leverage.

## Test improvement

Tests can now construct an in-memory Arrow table from `pyarrow` literals and pass it directly:

```python
decisions = pa.table({"transaction_id": [...], "decision": [...], "timestamp": [...]})
df = get_fraud_rate_daily(decisions, hours=24)
assert len(df) == expected_rows
```

No Iceberg catalog, no MinIO, no catalog URI required.

## Considered alternatives

- *Fix `DuckDBQueryRunner` and route all three query files through it* — adds an indirection layer without eliminating the Iceberg-loading problem; tests still cannot inject data without going through the catalog. Rejected.
- *Inject a loader callable `(hours: int) -> pa.Table` instead of `pa.Table` directly* — the callable is still a seam, but tests now inject a function rather than data. More complex for equivalent testability. Rejected in favour of plain data injection.
