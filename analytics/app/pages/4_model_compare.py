"""Model version comparison — historical stats from Trino v_model_versions."""

import streamlit as st

st.set_page_config(page_title="Model Compare", page_icon="🤖", layout="wide")
st.title("🤖 Model Version Comparison")

try:
    import pandas as pd
    from analytics.queries.model_versions import model_version_daily, model_version_summary
except ImportError as e:
    st.error(f"Missing dependency: {e}")
    st.stop()

days = st.sidebar.slider("Lookback (days)", min_value=1, max_value=90, value=30)

try:
    summary = model_version_summary(days=days)
except Exception as e:
    st.error(f"Trino query failed: {e}")
    st.stop()

if summary.empty:
    st.info("No model version data found for the selected period.")
    st.stop()

# ── Summary table ─────────────────────────────────────────────────────────────
st.subheader(f"Model Versions — last {days} days")
st.dataframe(
    summary.rename(
        columns={
            "model_version": "Version",
            "total_txns": "Total Txns",
            "avg_score": "Avg Score",
            "median_score": "Median Score",
            "p95_score": "P95 Score",
            "avg_latency_ms": "Avg Latency (ms)",
            "p99_latency_ms": "P99 Latency (ms)",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

# ── Side-by-side comparison ───────────────────────────────────────────────────
versions = summary["model_version"].tolist()
if len(versions) >= 2:
    st.markdown("---")
    st.subheader("Side-by-Side Comparison")
    col_a, col_b = st.columns(2)
    v_a = col_a.selectbox("Version A", versions, index=0)
    v_b = col_b.selectbox("Version B", versions, index=min(1, len(versions) - 1))

    try:
        da = model_version_daily(v_a, days=days)
        db = model_version_daily(v_b, days=days)
    except Exception as e:
        st.error(f"Trino query failed: {e}")
    else:
        da["decision_date"] = pd.to_datetime(da["decision_date"])
        db["decision_date"] = pd.to_datetime(db["decision_date"])

        da_agg = (
            da.groupby("decision_date", as_index=False)["avg_fraud_score"]
            .mean()
            .rename(columns={"avg_fraud_score": v_a})
        )
        db_agg = (
            db.groupby("decision_date", as_index=False)["avg_fraud_score"]
            .mean()
            .rename(columns={"avg_fraud_score": v_b})
        )
        merged = da_agg.merge(db_agg, on="decision_date", how="outer").sort_values("decision_date")
        st.line_chart(merged.set_index("decision_date"), use_container_width=True)
        st.caption(
            "Avg fraud score over time — lower is better (fewer high-confidence fraud decisions)"
        )

st.caption("⚠️ Score percentiles are approximate (APPROX_PERCENTILE).")
