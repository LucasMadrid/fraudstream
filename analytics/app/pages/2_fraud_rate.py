"""Fraud rate historical page — queries Trino v_fraud_rate_daily view."""

import streamlit as st

st.set_page_config(page_title="Fraud Rate", page_icon="📈", layout="wide")
st.title("📈 Historical Fraud Rate")

try:
    import pandas as pd
    from analytics.queries.fraud_rate import fraud_rate_by_channel, fraud_rate_daily
except ImportError as e:
    st.error(f"Missing dependency: {e}")
    st.stop()

days = st.sidebar.slider("Lookback (days)", min_value=1, max_value=90, value=30)

tab_trend, tab_channel, tab_raw = st.tabs(["Trend", "By Channel", "Raw Data"])

# ── Tab 1: trend ──────────────────────────────────────────────────────────────
with tab_trend:
    st.subheader(f"Daily Fraud Rate — last {days} days")
    try:
        df = fraud_rate_daily(days=days)
    except Exception as e:
        st.error(f"Trino query failed: {e}")
        st.stop()

    if df.empty:
        st.info("No data in the selected period.")
    else:
        df["decision_date"] = pd.to_datetime(df["decision_date"])
        fraud_df = (
            df[df["decision"] == "BLOCK"]
            .groupby("decision_date", as_index=False)["transaction_count"]
            .sum()
        )
        total_df = df.groupby("decision_date", as_index=False)["transaction_count"].sum()
        merged = fraud_df.merge(total_df, on="decision_date", suffixes=("_fraud", "_total"))
        merged["fraud_rate"] = merged["transaction_count_fraud"] / merged["transaction_count_total"]
        st.line_chart(merged.set_index("decision_date")["fraud_rate"], use_container_width=True)
        st.caption("Fraud rate = BLOCK decisions / total decisions per day")

# ── Tab 2: by channel ─────────────────────────────────────────────────────────
with tab_channel:
    st.subheader(f"Breakdown by Channel — last {days} days")
    try:
        ch_df = fraud_rate_by_channel(days=days)
    except Exception as e:
        st.error(f"Trino query failed: {e}")
        st.stop()

    if ch_df.empty:
        st.info("No data.")
    else:
        pivot = ch_df.pivot_table(
            index="channel", columns="decision", values="total_txns", fill_value=0
        )
        st.bar_chart(pivot, use_container_width=True)
        st.dataframe(ch_df, use_container_width=True, hide_index=True)

# ── Tab 3: raw ────────────────────────────────────────────────────────────────
with tab_raw:
    st.subheader("Raw View Data")
    try:
        raw = fraud_rate_daily(days=days)
    except Exception as e:
        st.error(f"Trino query failed: {e}")
        st.stop()
    st.dataframe(raw, use_container_width=True, hide_index=True)
    st.caption(
        "⚠️ `avg_fraud_score` and `p99_latency_ms` are computed with "
        "APPROX_PERCENTILE — approximate values."
    )
