"""Replay Results — browse and analyze replay job results for backtesting.

Provides:
- Replay job browser with status and progress
- Original vs replay decision comparison
- Score delta visualization
- Rules that changed most analysis
"""

import streamlit as st

st.set_page_config(page_title="Replay Results", page_icon="🔁", layout="wide")
st.title("🔁 Replay Results & Backtesting")

# Try to import required dependencies
try:
    import pandas as pd
    import requests
except ImportError as e:
    st.error(f"Missing dependency: {e}")
    st.stop()

# Configuration
MANAGEMENT_API_URL = "http://scoring-management:8090"
KAFKA_BROKERS = "kafka:9092"


@st.cache_data(ttl=30)
def fetch_replay_jobs() -> list[dict]:
    """Fetch list of replay jobs from management API."""
    try:
        resp = requests.get(f"{MANAGEMENT_API_URL}/replay/jobs", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.warning(f"Could not fetch replay jobs: {e}")
        return []


def fetch_replay_job(job_id: str) -> dict | None:
    """Fetch specific replay job status."""
    try:
        resp = requests.get(f"{MANAGEMENT_API_URL}/replay/jobs/{job_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Failed to fetch job {job_id}: {e}")
        return None


def fetch_replay_results(job_id: str, limit: int = 1000) -> list[dict]:
    """Fetch replay results for a job."""
    try:
        resp = requests.get(
            f"{MANAGEMENT_API_URL}/replay/jobs/{job_id}/results",
            params={"limit": limit, "offset": 0},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.RequestException as e:
        st.error(f"Failed to fetch results for job {job_id}: {e}")
        return []


def render_job_browser() -> str | None:
    """Render the replay job browser sidebar.

    Returns:
        Selected job ID or None
    """
    st.sidebar.header("Replay Jobs")

    # Refresh button
    if st.sidebar.button("🔄 Refresh"):
        st.cache_data.clear()

    # For now, we'll show a manual job ID input since the list endpoint
    # isn't implemented yet in management API
    job_id = st.sidebar.text_input(
        "Job ID",
        placeholder="Enter replay job ID",
        help="Enter the UUID of a replay job to analyze",
    )

    # Job status filter (used in sidebar UI)
    st.sidebar.multiselect(
        "Filter by Status",
        ["pending", "running", "completed", "failed", "cancelled"],
        default=["completed"],
    )

    return job_id if job_id else None


def render_job_status(job: dict) -> None:
    """Render job status dashboard."""
    st.subheader(f"Job: {job.get('job_id', 'Unknown')}")

    # Status metrics
    col1, col2, col3, col4 = st.columns(4)

    status = job.get("status", "unknown")
    status_color = {
        "completed": "🟢",
        "running": "🟡",
        "failed": "🔴",
        "pending": "⚪",
        "cancelled": "⚫",
    }.get(status, "⚪")

    col1.metric("Status", f"{status_color} {status}")
    col2.metric("Progress", f"{job.get('progress_percent', 0):.1f}%")
    col3.metric("Processed", f"{job.get('processed_events', 0):,}")
    col4.metric("Failed", f"{job.get('failed_events', 0):,}")

    # Progress bar
    st.progress(min(job.get("progress_percent", 0) / 100, 1.0))

    # Description and metadata
    if job.get("description"):
        st.caption(f"Description: {job['description']}")

    if job.get("error_message"):
        st.error(f"Error: {job['error_message']}")


def render_comparison_summary(summary: dict) -> None:
    """Render the comparison summary statistics."""
    st.markdown("---")
    st.subheader("📊 Comparison Summary")

    if not summary:
        st.info("No summary statistics available yet.")
        return

    col1, col2, col3, col4 = st.columns(4)

    total = summary.get("total_compared", 0)
    changed = summary.get("decisions_changed", 0)
    change_rate = summary.get("decision_change_rate", 0)

    col1.metric("Total Compared", f"{total:,}")
    col2.metric("Decisions Changed", f"{changed:,}")
    col3.metric("Change Rate", f"{change_rate:.2f}%")
    col4.metric("Avg Score Delta", f"{summary.get('avg_score_delta', 0):.4f}")

    # Direction breakdown
    direction = summary.get("direction_breakdown", {})
    if direction:
        st.markdown("**Decision Change Direction:**")
        direction_df = pd.DataFrame([{"Direction": k, "Count": v} for k, v in direction.items()])
        st.dataframe(direction_df, hide_index=True, use_container_width=True)


def render_score_delta_chart(results: list[dict]) -> None:
    """Render score delta visualization."""
    st.markdown("---")
    st.subheader("📈 Score Delta Distribution")

    if not results:
        st.info("No results available for visualization.")
        return

    df = pd.DataFrame(results)

    if "score_delta" not in df.columns or df["score_delta"].isna().all():
        st.info("No score delta data available.")
        return

    # Histogram of score deltas
    import plotly.express as px

    fig = px.histogram(
        df,
        x="score_delta",
        nbins=50,
        title="Distribution of Score Differences (Replay - Original)",
        labels={"score_delta": "Score Delta"},
        color_discrete_sequence=["#1f77b4"],
    )
    fig.add_vline(x=0, line_dash="dash", line_color="red")
    st.plotly_chart(fig, use_container_width=True)

    # Box plot by decision change
    if "decision_changed" in df.columns:
        df["Decision Changed"] = df["decision_changed"].map({True: "Yes", False: "No"})
        fig2 = px.box(
            df,
            x="Decision Changed",
            y="score_delta",
            title="Score Delta by Decision Change",
            color="Decision Changed",
        )
        st.plotly_chart(fig2, use_container_width=True)


def render_rules_changed(summary: dict) -> None:
    """Render rules that changed most analysis."""
    st.markdown("---")
    st.subheader("🔄 Rules That Changed Most")

    rules_changed = summary.get("rules_changed_most", [])

    if not rules_changed:
        st.info("No rule trigger changes detected.")
        return

    # Convert to DataFrame
    df = pd.DataFrame(rules_changed, columns=["Rule ID", "Change Count"])

    # Bar chart
    import plotly.express as px

    fig = px.bar(
        df,
        x="Rule ID",
        y="Change Count",
        title="Rules with Most Trigger Behavior Changes",
        color="Change Count",
        color_continuous_scale="Viridis",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Table
    st.dataframe(df, hide_index=True, use_container_width=True)


def render_detailed_results(results: list[dict]) -> None:
    """Render detailed results table."""
    st.markdown("---")
    st.subheader("📋 Detailed Results")

    if not results:
        st.info("No detailed results available.")
        return

    df = pd.DataFrame(results)

    # Select columns to display
    display_cols = [
        "original_event_id",
        "original_decision",
        "replay_decision",
        "original_score",
        "replay_score",
        "score_delta",
        "decision_changed",
        "processing_time_ms",
    ]

    available_cols = [c for c in display_cols if c in df.columns]
    display_df = df[available_cols].copy()

    # Rename columns for display
    rename_map = {
        "original_event_id": "Event ID",
        "original_decision": "Original Decision",
        "replay_decision": "Replay Decision",
        "original_score": "Original Score",
        "replay_score": "Replay Score",
        "score_delta": "Score Delta",
        "decision_changed": "Changed?",
        "processing_time_ms": "Processing Time (ms)",
    }
    display_df = display_df.rename(columns=rename_map)

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        show_changed_only = st.checkbox("Show changed decisions only", value=False)
    with col2:
        min_delta = st.number_input("Min |Score Delta|", value=0.0, step=0.01)

    # Apply filters
    if show_changed_only and "Changed?" in display_df.columns:
        display_df = display_df[display_df["Changed?"] is True]

    if min_delta > 0 and "Score Delta" in display_df.columns:
        display_df = display_df[display_df["Score Delta"].abs() >= min_delta]

    st.dataframe(display_df, hide_index=True, use_container_width=True)

    # Download button
    csv = display_df.to_csv(index=False)
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name="replay_results.csv",
        mime="text/csv",
    )


# Main page flow
st.sidebar.markdown("---")
selected_job_id = render_job_browser()

if selected_job_id:
    # Fetch job details
    job = fetch_replay_job(selected_job_id)

    if job:
        render_job_status(job)

        # Show results if job is completed
        if job.get("status") == "completed":
            summary = job.get("results_summary", {})
            render_comparison_summary(summary)

            # Fetch detailed results
            with st.spinner("Loading detailed results..."):
                results = fetch_replay_results(selected_job_id)

            if results:
                render_score_delta_chart(results)
                render_rules_changed(summary)
                render_detailed_results(results)
        elif job.get("status") == "running":
            st.info("⏳ Job is still running. Refresh to see updated progress.")
            # Auto-refresh option
            if st.button("🔄 Auto-refresh (10s)"):
                import time

                time.sleep(10)
                st.rerun()
        elif job.get("status") == "failed":
            st.error(f"❌ Job failed: {job.get('error_message', 'Unknown error')}")
        elif job.get("status") == "cancelled":
            st.warning("⚠️ Job was cancelled")
    else:
        st.error(f"Could not fetch job {selected_job_id}")
else:
    # Welcome message
    st.info(
        """
    ### Welcome to Replay Results Analysis

    This page allows you to analyze the results of replay jobs for backtesting fraud
    detection rules.

    **Features:**
    - Browse replay jobs and their status
    - Compare original vs replay decisions
    - Visualize score deltas
    - Identify rules with changed trigger behavior
    - Export detailed results

    **To get started:**
    1. Enter a replay job ID in the sidebar
    2. View the comparison summary and visualizations
    3. Export results for further analysis

    **API Endpoints:**
    - `POST /replay/jobs` - Start a new replay job
    - `GET /replay/jobs/{id}` - Get replay status
    - `GET /replay/jobs/{id}/results` - Get replay results
    - `DELETE /replay/jobs/{id}` - Cancel replay
    """
    )

st.caption(
    "Replay jobs write to Kafka topic `txn.replay.results` and Iceberg table `replay_results`"
)
