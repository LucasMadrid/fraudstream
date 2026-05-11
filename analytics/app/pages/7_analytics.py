"""Analytics Insights dashboard — cross-dimensional fraud analysis."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Analytics Insights", page_icon="📊", layout="wide")
st.title("📊 Analytics Insights")

try:
    from analytics.queries.analytics_insights import (
        amount_by_decision,
        geo_breakdown,
        hourly_volume,
        kpi_summary,
        score_distribution,
        top_risk_accounts,
        velocity_by_decision,
    )
except ImportError as e:
    st.error(f"Missing dependency: {e}")
    st.stop()

from analytics.app.widgets import run_query  # noqa: E402

DECISION_COLORS = {"BLOCK": "#d32f2f", "FLAG": "#f57c00", "ALLOW": "#388e3c"}


def _mask_account(account_id: str) -> str:
    if len(account_id) <= 6:
        return "****"
    return account_id[:4] + "****" + account_id[-2:]


# ── Sidebar controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Configuration")
    days = st.slider("Lookback period (days)", min_value=1, max_value=90, value=30)

    st.subheader("Geographic analysis")
    top_n_geo = st.number_input("Top N countries", min_value=5, max_value=30, value=15, step=5)

    st.subheader("Account analysis")
    top_n_acct = st.number_input(
        "Top N risky accounts", min_value=5, max_value=50, value=20, step=5
    )

# ── KPI row ───────────────────────────────────────────────────────────────────
kpi = run_query(lambda: kpi_summary(days=days), "No data in the selected period.")
if kpi.empty:
    st.stop()

row = kpi.iloc[0]

st.subheader(f"Key Metrics — Last {days} Days")

kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5 = st.columns(5)

with kpi_col1:
    st.metric(
        "Total Transactions",
        f"{int(row['total_txns']):,}",
    )

with kpi_col2:
    st.metric(
        "Block Rate",
        f"{row['block_rate_pct']:.2f}%",
        help="Percentage of transactions blocked (fraud decisions)",
    )

with kpi_col3:
    st.metric(
        "Flag Rate",
        f"{row['flag_rate_pct']:.2f}%",
        help="Percentage of transactions flagged for review",
    )

with kpi_col4:
    st.metric(
        "Avg Fraud Score",
        f"{row['avg_fraud_score']:.4f}",
        help="Mean fraud score across all transactions (0.0–1.0 scale)",
    )

with kpi_col5:
    st.metric(
        "P99 Latency",
        f"{row['p99_latency_ms']:.0f} ms",
        help="99th percentile decision latency",
    )

st.divider()

# ── Additional KPI insights ────────────────────────────────────────────────────
st.markdown("### Decision Breakdown")
detail_col1, detail_col2, detail_col3, detail_col4 = st.columns(4)

with detail_col1:
    st.metric("Blocked", f"{int(row['block_count']):,}")

with detail_col2:
    st.metric("Flagged", f"{int(row['flag_count']):,}")

with detail_col3:
    st.metric("Allowed", f"{int(row['allow_count']):,}")

with detail_col4:
    st.metric("P95 Score", f"{row['p95_fraud_score']:.4f}")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_dist, tab_volume, tab_amount, tab_geo, tab_vel = st.tabs(
    [
        "Score Distribution",
        "Volume Patterns",
        "Amount Analysis",
        "Geographic",
        "Velocity & Risk",
    ]
)

# ── Tab 1: Score Distribution ─────────────────────────────────────────────────
with tab_dist:
    st.subheader(f"Fraud Score Distribution — Last {days} Days")
    dist_df = run_query(
        lambda: score_distribution(days=days),
        "No data available for selected period.",
    )
    if not dist_df.empty:
        pivot = dist_df.pivot_table(
            index="score_bucket", columns="decision", values="count", fill_value=0
        )
        pivot.index = [f"{b:.1f}" for b in pivot.index]

        col_chart, col_info = st.columns([3, 1])

        with col_chart:
            st.bar_chart(pivot, use_container_width=True)

        with col_info:
            st.markdown("""
            **What this shows:**
            - Each bar group represents a 0.1-wide fraud score bucket
            - Bars are split by decision outcome (color-coded)
            - Higher fraud scores cluster with BLOCK decisions
            """)

        st.caption(
            "Fraud score bucketing: Each 0.1-wide interval. "
            "Darker red indicates fraud-bound transactions."
        )

        with st.expander("View raw data"):
            st.dataframe(
                dist_df.sort_values(["score_bucket", "decision"]),
                use_container_width=True,
                hide_index=True,
            )

# ── Tab 2: Volume Patterns ────────────────────────────────────────────────────
with tab_volume:
    st.subheader(f"Transaction Volume Patterns — Last {days} Days")
    hv_df = run_query(
        lambda: hourly_volume(days=days),
        "No data available for selected period.",
    )
    if not hv_df.empty:
        DAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}

        col_h, col_d = st.columns(2)

        with col_h:
            st.markdown("**Volume by Hour of Day**")
            by_hour = (
                hv_df.groupby("hour_of_day", as_index=False)[["transaction_count", "block_count"]]
                .sum()
                .set_index("hour_of_day")
            )
            st.bar_chart(by_hour, use_container_width=True)
            st.caption("Peak traffic hours and corresponding block volumes.")

        with col_d:
            st.markdown("**Volume by Day of Week**")
            by_dow = hv_df.groupby("day_of_week", as_index=False)[
                ["transaction_count", "block_count"]
            ].sum()
            by_dow["day"] = by_dow["day_of_week"].map(DAY_NAMES)
            st.bar_chart(
                by_dow.set_index("day")[["transaction_count", "block_count"]],
                use_container_width=True,
            )
            st.caption("Weekday vs weekend transaction patterns.")

        st.markdown("**Block Rate Heatmap — Hour × Day of Week**")
        heatmap_df = hv_df.pivot_table(
            index="day_of_week", columns="hour_of_day", values="block_rate_pct", fill_value=0.0
        )
        heatmap_df.index = heatmap_df.index.map(DAY_NAMES)
        heatmap_df.columns = [f"{h:02d}h" for h in heatmap_df.columns]

        st.dataframe(
            heatmap_df.style.background_gradient(cmap="Reds", vmin=0, vmax=100),
            use_container_width=True,
        )
        st.caption(
            "Fraud concentration: darker red indicates higher block rates at that hour/day "
            "combination. Use to identify high-risk time windows."
        )

# ── Tab 3: Amount Analysis ────────────────────────────────────────────────────
with tab_amount:
    st.subheader(f"Transaction Amount by Decision — Last {days} Days")
    amt_df = run_query(
        lambda: amount_by_decision(days=days),
        "No data available for selected period.",
    )
    if not amt_df.empty:
        pivot_amt = amt_df.pivot_table(
            index="amount_bucket", columns="decision", values="txn_count", fill_value=0
        )
        bucket_order = ["< $10", "$10-$100", "$100-$500", "$500-$1K", "$1K-$5K", "> $5K", "Unknown"]
        pivot_amt = pivot_amt.reindex([b for b in bucket_order if b in pivot_amt.index])

        col_chart, col_info = st.columns([3, 1])

        with col_chart:
            st.bar_chart(pivot_amt, use_container_width=True)

        with col_info:
            st.markdown("""
            **Interpretation:**
            - Shows how fraud rate varies by transaction size
            - Higher amounts may have different risk profiles
            - Stacked bars show decision distribution per bucket
            """)

        st.caption(
            "Transaction count per amount bracket, grouped by decision. "
            "Identify risk patterns at different price points."
        )

        with st.expander("View detailed table"):
            display_df = amt_df.sort_values(["decision"])
            st.dataframe(display_df, use_container_width=True, hide_index=True)

# ── Tab 4: Geographic ─────────────────────────────────────────────────────────
with tab_geo:
    st.subheader(f"Geographic Risk Analysis — Top {int(top_n_geo)} Countries")
    geo_df = run_query(
        lambda: geo_breakdown(days=days, top_n=int(top_n_geo)),
        "No data available for selected period.",
    )
    if not geo_df.empty:
        col_chart, col_tbl = st.columns([2, 1])

        with col_chart:
            st.markdown("**Transaction Volume by Country**")
            st.bar_chart(
                geo_df.set_index("geo_country")[["total_txns", "block_count", "flag_count"]],
                use_container_width=True,
            )
            st.caption("Total transactions vs fraud actions (blocks + flags) per country.")

        with col_tbl:
            st.markdown("**Risk Metrics**")
            display_cols = ["geo_country", "block_rate_pct", "avg_fraud_score"]
            geo_display = geo_df[display_cols].copy()
            geo_display.columns = ["Country", "Block Rate %", "Avg Score"]

            st.dataframe(
                geo_display.style.background_gradient(
                    subset=["Block Rate %", "Avg Score"], cmap="Reds", vmin=0, vmax=100
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Red shading indicates higher fraud concentration.")

# ── Tab 5: Velocity & Accounts ────────────────────────────────────────────────
with tab_vel:
    col_v, col_a = st.columns(2)

    with col_v:
        st.markdown("#### Velocity Profile by Decision")
        st.markdown(f"Last {days} days")
        vel_df = run_query(
            lambda: velocity_by_decision(days=days),
            "No data available.",
        )
        if not vel_df.empty:
            count_cols = [
                "avg_vel_count_1m",
                "avg_vel_count_5m",
                "avg_vel_count_1h",
                "avg_vel_count_24h",
            ]
            vel_chart_df = vel_df.set_index("decision")[count_cols].copy()
            vel_chart_df.columns = ["1m", "5m", "1h", "24h"]

            st.bar_chart(vel_chart_df, use_container_width=True)
            st.caption(
                "Average transaction count in each velocity window (1m, 5m, 1h, 24h). "
                "Higher counts indicate rapid, potentially fraudulent activity."
            )

            with st.expander("Amount velocity (1h & 24h)"):
                amount_cols = ["avg_vel_amount_1h", "avg_vel_amount_24h"]
                amt_vel_df = vel_df.set_index("decision")[amount_cols].copy()
                amt_vel_df.columns = ["1h Amount", "24h Amount"]
                st.bar_chart(amt_vel_df, use_container_width=True)

            with st.expander("Full velocity table"):
                st.dataframe(vel_df, use_container_width=True, hide_index=True)

    with col_a:
        st.markdown(f"#### Top {int(top_n_acct)} Risky Accounts")
        st.markdown(f"Last {days} days — by avg fraud score")
        acct_df = run_query(
            lambda: top_risk_accounts(days=days, top_n=int(top_n_acct)),
            "No accounts with ≥2 transactions in this period.",
        )
        if not acct_df.empty:
            display_cols = [
                "account_id",
                "total_txns",
                "block_count",
                "flag_count",
                "alert_rate_pct",
                "avg_fraud_score",
                "max_fraud_score",
            ]
            display_df = acct_df[display_cols].copy()
            display_df["account_id"] = display_df["account_id"].apply(_mask_account)
            display_df.columns = [
                "Account",
                "Total Txns",
                "Blocks",
                "Flags",
                "Alert Rate %",
                "Avg Score",
                "Max Score",
            ]

            st.dataframe(
                display_df.style.background_gradient(
                    subset=["Alert Rate %", "Avg Score", "Max Score"], cmap="Reds", vmin=0, vmax=100
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "Red shading shows alert intensity. Accounts ranked by average fraud score. "
                "Investigate high-scoring accounts immediately."
            )
