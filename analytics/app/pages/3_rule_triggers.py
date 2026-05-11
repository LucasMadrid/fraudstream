"""Rule trigger leaderboard — historical rule frequency via PyIceberg → DuckDB."""

import streamlit as st

st.set_page_config(page_title="Rule Triggers", page_icon="🔍", layout="wide")
st.title("🔍 Rule Trigger Leaderboard")

try:
    import pandas as pd

    from analytics.queries.rule_triggers import rule_leaderboard, rule_trigger_daily
except ImportError as e:
    st.error(f"Missing dependency: {e}")
    st.stop()

from analytics.app.widgets import run_query  # noqa: E402

days = st.sidebar.slider("Lookback (days)", min_value=1, max_value=90, value=7)
top_n = st.sidebar.slider("Top N rules", min_value=5, max_value=50, value=20)

# ── Leaderboard ───────────────────────────────────────────────────────────────
st.subheader(f"Top {top_n} Rules — last {days} days")
lb = run_query(
    lambda: rule_leaderboard(days=days, top_n=top_n),
    "No rule trigger data found for the selected period.",
)
if not lb.empty:
    st.bar_chart(lb.set_index("rule_name")["total_triggers"], use_container_width=True)
    st.dataframe(
        lb.rename(
            columns={
                "rule_name": "Rule",
                "total_triggers": "Triggers",
                "avg_score": "Avg Score",
                "median_score": "Median Score",
                "p95_score": "P95 Score",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

# ── Drill-down ────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Rule Drill-down")

rule_options = lb["rule_name"].tolist() if not lb.empty else []
if rule_options:
    selected = st.selectbox("Select rule", rule_options)
    daily = run_query(
        lambda: rule_trigger_daily(rule_name=selected, days=days),
        "No daily data for this rule.",
    )
    if not daily.empty:
        daily["decision_date"] = pd.to_datetime(daily["decision_date"])
        st.line_chart(
            daily.set_index("decision_date")["trigger_count"],
            use_container_width=True,
        )
        st.dataframe(daily, use_container_width=True, hide_index=True)
else:
    st.info("No rules to drill into.")

st.caption("⚠️ Score percentiles are approximate (APPROX_PERCENTILE).")
