"""
Shadow Scoring Analytics — Streamlit page for comparing production vs shadow rule decisions.

Provides:
- Side-by-side production vs shadow decision comparison
- Score delta distribution
- Table of transactions where prod != shadow decision
- Filter by date range, model version, rule set
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import streamlit as st
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import GreaterThanOrEqual, LessThanOrEqual

st.set_page_config(page_title="Shadow Scoring Analytics", page_icon="🔍", layout="wide")
st.title("🔍 Shadow Scoring Analytics")

# ============================================================================
# Configuration & Constants
# ============================================================================

MAX_HOURS = 720  # 30 days rolling window

_EMPTY_SHADOW_COLS = [
    "transaction_id",
    "account_id",
    "production_decision",
    "shadow_determination",
    "score_delta",
    "decision_mismatch",
    "decision_time_ms",
]

# ============================================================================
# Helper Functions
# ============================================================================


def _parse_datetime(dt: datetime | None) -> int:
    """Convert datetime to epoch milliseconds."""
    if dt is None:
        return int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)
    return int(dt.timestamp() * 1000)


def _load_shadow_decisions(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    model_version: str | None = None,
    rule_set_version: str | None = None,
    shadow_rule_set_version: str | None = None,
    only_mismatches: bool = False,
) -> duckdb.DuckDBPyConnection | None:
    """Load shadow decisions from Iceberg with optional filters.

    Args:
        start_time: Filter decisions after this time
        end_time: Filter decisions before this time
        model_version: Filter by ML model version
        rule_set_version: Filter by active rule set version
        shadow_rule_set_version: Filter by shadow rule set version
        only_mismatches: Only return rows where decisions differ

    Returns:
        DuckDB connection with registered shadow_decisions table, or None if no data
    """
    try:
        catalog = load_catalog("iceberg")
    except Exception as exc:
        st.error(f"Unable to connect to Iceberg catalog: {exc}")
        return None

    try:
        shadow_table = catalog.load_table("default.shadow_decisions")
    except Exception as exc:
        st.warning(f"Shadow decisions table not found: {exc}")
        return None

    # Build row filter
    start_ms = _parse_datetime(start_time)
    end_ms = _parse_datetime(end_time) if end_time else int(datetime.now().timestamp() * 1000)

    filters = [
        GreaterThanOrEqual("decision_time_ms", start_ms),
        LessThanOrEqual("decision_time_ms", end_ms),
    ]

    # Scan table
    try:
        shadow_arrow = shadow_table.scan(row_filter=filters[0] & filters[1]).to_arrow()
    except Exception as exc:
        st.error(f"Failed to scan shadow_decisions: {exc}")
        return None

    if shadow_arrow.num_rows == 0:
        return None

    # Convert to pandas for additional filtering
    df = shadow_arrow.to_pandas()

    # Apply string filters
    if model_version and model_version != "All":
        df = df[df["model_version"] == model_version]
    if rule_set_version and rule_set_version != "All":
        df = df[df["rule_set_version"] == rule_set_version]
    if shadow_rule_set_version and shadow_rule_set_version != "All":
        df = df[df["shadow_rule_set_version"] == shadow_rule_set_version]
    if only_mismatches:
        df = df[df["decision_mismatch"] == True]  # noqa: E712

    if len(df) == 0:
        return None

    conn = duckdb.connect()
    conn.register("shadow_decisions", df)
    return conn


def _get_unique_values(conn: duckdb.DuckDBPyConnection, column: str) -> list[str]:
    """Get unique values for a column from the shadow decisions table."""
    try:
        result = conn.execute(
            f"SELECT DISTINCT {column} FROM shadow_decisions ORDER BY {column}"
        ).fetchall()
        return [row[0] for row in result if row[0]]
    except Exception:
        return []


# ============================================================================
# Filters Sidebar
# ============================================================================

st.sidebar.header("Filters")

# Date range filter
col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=1))
with col2:
    start_time_input = st.time_input("Start Time", value=datetime.strptime("00:00", "%H:%M").time())

with col1:
    end_date = st.date_input("End Date", value=datetime.now())
with col2:
    end_time_input = st.time_input("End Time", value=datetime.strptime("23:59", "%H:%M").time())

start_datetime = datetime.combine(start_date, start_time_input)
end_datetime = datetime.combine(end_date, end_time_input)

# Load data to get filter options
conn = _load_shadow_decisions(start_datetime, end_datetime)

model_versions = ["All"]
rule_set_versions = ["All"]
shadow_rule_set_versions = ["All"]

if conn:
    model_versions.extend(_get_unique_values(conn, "model_version"))
    rule_set_versions.extend(_get_unique_values(conn, "rule_set_version"))
    shadow_rule_set_versions.extend(_get_unique_values(conn, "shadow_rule_set_version"))
    conn.close()

# Version filters
model_version = st.sidebar.selectbox("Model Version", model_versions)
rule_set_version = st.sidebar.selectbox("Active Rule Set Version", rule_set_versions)
shadow_rule_set_version = st.sidebar.selectbox("Shadow Rule Set Version", shadow_rule_set_versions)
only_mismatches = st.sidebar.checkbox("Only Show Mismatches", value=False)

# ============================================================================
# Load Data with Filters
# ============================================================================

conn = _load_shadow_decisions(
    start_datetime,
    end_datetime,
    model_version,
    rule_set_version,
    shadow_rule_set_version,
    only_mismatches,
)

if conn is None:
    st.info("No shadow decision data found for the selected filters.")
    st.stop()

# ============================================================================
# Summary Metrics
# ============================================================================

st.header("📊 Summary Metrics")

metrics_cols = st.columns(4)

# Total transactions
total_count = conn.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0]
metrics_cols[0].metric("Total Transactions", f"{total_count:,}")

# Mismatch count and rate
mismatch_count = conn.execute(
    "SELECT COUNT(*) FROM shadow_decisions WHERE decision_mismatch = TRUE"
).fetchone()[0]
mismatch_rate = (mismatch_count / total_count * 100) if total_count > 0 else 0
metrics_cols[1].metric("Decision Mismatches", f"{mismatch_count:,} ({mismatch_rate:.1f}%)")

# Average score delta
avg_delta = conn.execute("SELECT AVG(score_delta) FROM shadow_decisions").fetchone()[0] or 0
metrics_cols[2].metric("Avg Score Delta", f"{avg_delta:.3f}")

# Max absolute delta
max_abs_delta = (
    conn.execute("SELECT MAX(ABS(score_delta)) FROM shadow_decisions").fetchone()[0] or 0
)
metrics_cols[3].metric("Max |Delta|", f"{max_abs_delta:.3f}")

st.markdown("---")

# ============================================================================
# Decision Comparison Charts
# ============================================================================

st.header("🔄 Production vs Shadow Decision Comparison")

chart_cols = st.columns(2)

with chart_cols[0]:
    st.subheader("Decision Distribution")
    decision_dist = conn.execute("""
        SELECT
            production_decision,
            shadow_determination,
            COUNT(*) as count
        FROM shadow_decisions
        GROUP BY production_decision, shadow_determination
        ORDER BY production_decision, shadow_determination
    """).df()

    if not decision_dist.empty:
        # Pivot for stacked bar chart
        pivot_df = decision_dist.pivot(
            index="production_decision", columns="shadow_determination", values="count"
        ).fillna(0)
        st.bar_chart(pivot_df)
    else:
        st.info("No data available")

with chart_cols[1]:
    st.subheader("Score Delta Distribution")
    delta_bins = conn.execute("""
        SELECT
            CASE
                WHEN score_delta < -0.5 THEN '< -0.5'
                WHEN score_delta < -0.2 THEN '-0.5 to -0.2'
                WHEN score_delta < 0 THEN '-0.2 to 0'
                WHEN score_delta = 0 THEN '0'
                WHEN score_delta < 0.2 THEN '0 to 0.2'
                WHEN score_delta < 0.5 THEN '0.2 to 0.5'
                ELSE '> 0.5'
            END as delta_range,
            COUNT(*) as count
        FROM shadow_decisions
        GROUP BY delta_range
        ORDER BY
            CASE delta_range
                WHEN '< -0.5' THEN 1
                WHEN '-0.5 to -0.2' THEN 2
                WHEN '-0.2 to 0' THEN 3
                WHEN '0' THEN 4
                WHEN '0 to 0.2' THEN 5
                WHEN '0.2 to 0.5' THEN 6
                ELSE 7
            END
    """).df()

    if not delta_bins.empty:
        st.bar_chart(delta_bins.set_index("delta_range"))
    else:
        st.info("No data available")

st.markdown("---")

# ============================================================================
# Mismatch Analysis
# ============================================================================

st.header("⚠️ Decision Mismatch Analysis")

# Types of mismatches
mismatch_breakdown = conn.execute("""
    SELECT
        production_decision,
        shadow_determination,
        COUNT(*) as count,
        ROUND(AVG(ABS(score_delta)), 3) as avg_abs_delta
    FROM shadow_decisions
    WHERE decision_mismatch = TRUE
    GROUP BY production_decision, shadow_determination
    ORDER BY count DESC
""").df()

if not mismatch_breakdown.empty:
    st.dataframe(
        mismatch_breakdown,
        use_container_width=True,
        hide_index=True,
        column_config={
            "production_decision": "Production Decision",
            "shadow_determination": "Shadow Determination",
            "count": st.column_config.NumberColumn("Count", format="%d"),
            "avg_abs_delta": st.column_config.NumberColumn("Avg |Delta|", format="%.3f"),
        },
    )
else:
    st.info("No decision mismatches found for the selected filters.")

st.markdown("---")

# ============================================================================
# Detailed Transaction Table
# ============================================================================

st.header("📋 Detailed Transactions")

# Pagination
page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=1)

# Get total for pagination
total_rows = conn.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0]
total_pages = max(1, (total_rows + page_size - 1) // page_size)
page = st.number_input("Page", min_value=1, max_value=total_pages, value=1) - 1
offset = page * page_size

# Fetch paginated results
detailed_df = conn.execute(f"""
    SELECT
        transaction_id,
        account_id,
        production_decision,
        production_fraud_score,
        shadow_determination,
        shadow_fraud_score,
        score_delta,
        decision_mismatch,
        model_version,
        rule_set_version,
        shadow_rule_set_version,
        epoch_ms(decision_time_ms) as decision_time
    FROM shadow_decisions
    ORDER BY decision_time_ms DESC
    LIMIT {page_size}
    OFFSET {offset}
""").df()

if not detailed_df.empty:
    st.dataframe(
        detailed_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "transaction_id": "Transaction ID",
            "account_id": "Account",
            "production_decision": "Production",
            "production_fraud_score": st.column_config.NumberColumn("Prod Score", format="%.3f"),
            "shadow_determination": "Shadow",
            "shadow_fraud_score": st.column_config.NumberColumn("Shadow Score", format="%.3f"),
            "score_delta": st.column_config.NumberColumn("Delta", format="%.3f"),
            "decision_mismatch": "Mismatch",
            "model_version": "Model",
            "rule_set_version": "Rule Set",
            "shadow_rule_set_version": "Shadow Rule Set",
            "decision_time": st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm:ss"),
        },
    )
    st.caption(f"Showing page {page + 1} of {total_pages} ({total_rows} total rows)")
else:
    st.info("No transactions found")

# ============================================================================
# Rule Trigger Analysis
# ============================================================================

st.markdown("---")
st.header("🎯 Rule Trigger Analysis")

rule_cols = st.columns(2)

with rule_cols[0]:
    st.subheader("Production Rule Triggers")
    prod_rules = conn.execute("""
        SELECT
            rule_id,
            COUNT(*) as trigger_count
        FROM (
            SELECT UNNEST(production_rule_triggers) as rule_id
            FROM shadow_decisions
        )
        GROUP BY rule_id
        ORDER BY trigger_count DESC
        LIMIT 10
    """).df()

    if not prod_rules.empty:
        st.dataframe(
            prod_rules,
            use_container_width=True,
            hide_index=True,
            column_config={
                "rule_id": "Rule ID",
                "trigger_count": st.column_config.NumberColumn("Triggers", format="%d"),
            },
        )
    else:
        st.info("No production rule triggers")

with rule_cols[1]:
    st.subheader("Shadow Rule Triggers")
    shadow_rules = conn.execute("""
        SELECT
            rule_id,
            COUNT(*) as trigger_count
        FROM (
            SELECT UNNEST(shadow_rule_triggers) as rule_id
            FROM shadow_decisions
        )
        GROUP BY rule_id
        ORDER BY trigger_count DESC
        LIMIT 10
    """).df()

    if not shadow_rules.empty:
        st.dataframe(
            shadow_rules,
            use_container_width=True,
            hide_index=True,
            column_config={
                "rule_id": "Rule ID",
                "trigger_count": st.column_config.NumberColumn("Triggers", format="%d"),
            },
        )
    else:
        st.info("No shadow rule triggers")

conn.close()

# ============================================================================
# Footer
# ============================================================================

st.divider()
st.caption(
    "💾 Data sourced from Iceberg table `default.shadow_decisions`. "
    "Shadow decisions capture both production and shadow rule outcomes for comparison. "
    "Last updated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
)
