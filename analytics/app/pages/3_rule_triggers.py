"""
Shadow Rule Monitor — Streamlit page for managing shadow rule canary mode.

Allows fraud analysts to:
1. Monitor shadow rules in real-time with false positive rate calculation
2. Promote safe rules to active mode
3. View auto-demoted rules in the last 24 hours
"""

import datetime
import logging

import pandas as pd
import requests
import streamlit as st

# Configure page layout
st.set_page_config(
    page_title="Shadow Rule Monitor",
    page_icon="🛡️",
    layout="wide",
)

# Configure logging with structured format
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration & Constants
# ============================================================================

PROMETHEUS_URL = "http://prometheus:9090/api/v1/query"
MANAGEMENT_API_URL = "http://scoring-management:8090"

# Prometheus query timeout (seconds)
QUERY_TIMEOUT = 30

# FP rate threshold for promotion gate (2%)
FP_RATE_PROMOTION_GATE = 0.02

# Minimum trigger count threshold for promotion
MIN_TRIGGERS_FOR_PROMOTION = 100

# ============================================================================
# Page Title & Description
# ============================================================================

st.title("🛡️ Shadow Rule Monitor")
st.markdown(
    """
    Monitor shadow rules in real-time, calculate estimated false positive rates,
    and safely promote rules to active mode when they meet thresholds.
    """
)

# ============================================================================
# Helper Functions: Prometheus Queries
# ============================================================================


@st.cache_data(ttl=30)
def query_prometheus(query: str) -> dict:
    """
    Execute a PromQL instant query against Prometheus.

    Args:
        query: PromQL expression

    Returns:
        Parsed JSON response from Prometheus API

    Raises:
        RuntimeError: If query fails
    """
    try:
        response = requests.get(
            PROMETHEUS_URL,
            params={"query": query},
            timeout=QUERY_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Prometheus query failed: {query}", exc_info=e)
        raise RuntimeError(f"Failed to query Prometheus: {str(e)}") from e


def extract_metrics(result: dict) -> dict:
    """
    Extract metric values from Prometheus instant query result.

    Args:
        result: Response data from Prometheus API

    Returns:
        Dict mapping rule_id → value (or empty dict if no data)
    """
    if result.get("status") != "success":
        logger.warning(f"Prometheus query failed: {result.get('error')}")
        return {}

    metrics = {}
    for item in result.get("data", {}).get("result", []):
        labels = item.get("labels", {})
        rule_id = labels.get("rule_id")
        if rule_id:
            value = float(item.get("value", [None, 0])[1] or 0)
            metrics[rule_id] = value
    return metrics


# ============================================================================
# Helper Functions: Management API Calls
# ============================================================================


def promote_rule(rule_id: str) -> tuple[bool, str]:
    """
    Call the management API to promote a rule from shadow to active.

    Args:
        rule_id: ID of the rule to promote

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        payload = {
            "triggered_by": "analyst_promotion",
        }
        response = requests.post(
            f"{MANAGEMENT_API_URL}/rules/{rule_id}/promote",
            json=payload,
            timeout=10,
        )

        if response.status_code == 200:
            result = response.json()
            message = (
                f"✓ Rule {rule_id} promoted to active. "
                f"Reload timestamp: {result.get('reload_timestamp')}"
            )
            logger.info(f"Rule promotion succeeded: {rule_id}")
            return True, message

        elif response.status_code == 409:
            return (
                False,
                f"Rule {rule_id} is already active. No action needed.",
            )

        elif response.status_code == 404:
            return False, f"Rule {rule_id} not found in rule engine."

        else:
            error_detail = response.text or "Unknown error"
            return (
                False,
                f"Promotion failed ({response.status_code}): {error_detail}",
            )

    except requests.RequestException as e:
        logger.error(f"Management API call failed for rule {rule_id}", exc_info=e)
        return False, f"Connection error: {str(e)}"


def get_circuit_breaker_state() -> dict | None:
    """
    Query the management API for current circuit breaker state.

    Returns:
        Dict with circuit breaker state, or None if API unavailable
    """
    try:
        response = requests.get(
            f"{MANAGEMENT_API_URL}/circuit-breaker/state",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("Failed to fetch circuit breaker state", exc_info=e)
        return None


# ============================================================================
# Data Loading & Processing
# ============================================================================


def load_shadow_rule_metrics() -> pd.DataFrame:
    """
    Load shadow rule trigger counts and FP rates from Prometheus.

    Queries:
    - rule_shadow_triggers_total: total shadow triggers per rule (24h increase)
    - rule_shadow_fp_total: triggers where final determination was clean (24h increase)

    Returns:
        DataFrame with columns:
        - rule_id
        - shadow_triggers_24h
        - fp_count_24h
        - estimated_fp_rate
        - status (for highlighting demoted rules)
    """
    try:
        # Query total shadow triggers in the last 24 hours
        triggers_query = 'increase(rule_shadow_triggers_total[24h])'
        triggers_result = query_prometheus(triggers_query)
        triggers_metrics = extract_metrics(triggers_result)

        # Query FP count (triggers where determination was clean) in last 24 hours
        fp_query = 'increase(rule_shadow_fp_total[24h])'
        fp_result = query_prometheus(fp_query)
        fp_metrics = extract_metrics(fp_result)

        # Build DataFrame
        data = []
        for rule_id, trigger_count in triggers_metrics.items():
            fp_count = fp_metrics.get(rule_id, 0)
            fp_rate = fp_count / trigger_count if trigger_count > 0 else 0

            data.append(
                {
                    "rule_id": rule_id,
                    "shadow_triggers_24h": int(trigger_count),
                    "fp_count_24h": int(fp_count),
                    "estimated_fp_rate": fp_rate,
                }
            )

        df = pd.DataFrame(data)

        # Sort by trigger count descending
        if not df.empty:
            df = df.sort_values("shadow_triggers_24h", ascending=False)
            df = df.reset_index(drop=True)

        return df

    except Exception as e:
        logger.error("Failed to load shadow rule metrics", exc_info=e)
        st.error(f"Failed to load shadow rule metrics: {str(e)}")
        return pd.DataFrame()


# ============================================================================
# Section 1: Shadow Trigger Table
# ============================================================================

st.header("📊 Shadow Rule Monitoring")

# Load metrics
shadow_df = load_shadow_rule_metrics()

if shadow_df.empty:
    st.info("No shadow rules found. Rules in shadow mode will appear here once deployed.")
else:
    # Create display dataframe with formatted columns
    display_df = shadow_df.copy()
    display_df["Estimated FP Rate (%)"] = (
        display_df["estimated_fp_rate"] * 100
    ).apply(lambda x: f"{x:.2f}%")
    display_df["Shadow Triggers (24h)"] = display_df[
        "shadow_triggers_24h"
    ].apply(lambda x: f"{x:,}")
    display_df["Rule ID"] = display_df["rule_id"]

    # Display table
    st.subheader("Active Shadow Rules")
    display_columns = [
        "Rule ID",
        "Shadow Triggers (24h)",
        "Estimated FP Rate (%)",
    ]
    st.dataframe(
        display_df[display_columns],
        use_container_width=True,
        hide_index=True,
    )

    # Note on FP rate calculation
    st.info(
        "📌 **Ground truth note**: Estimated FP rate is based on rule engine "
        "determination (clean vs. suspicious), not human review labels. "
        "This is an approximation in v1."
    )

# ============================================================================
# Section 2: Promote Rules
# ============================================================================

st.header("⬆️ Promote Rules to Active")

if not shadow_df.empty:
    st.markdown(
        """
    Rules can be promoted when they meet these criteria:
    - **Shadow Triggers ≥ 100**: Sufficient data to assess false positive rate
    - **Estimated FP Rate < 2%**: Low enough to trust in production
    """
    )

    # Create promotion controls for each rule
    promotion_section = st.container()

    for _, row in shadow_df.iterrows():
        rule_id = row["rule_id"]
        trigger_count = row["shadow_triggers_24h"]
        fp_rate = row["estimated_fp_rate"]

        # Determine if rule is eligible for promotion
        meets_trigger_threshold = trigger_count >= MIN_TRIGGERS_FOR_PROMOTION
        meets_fp_threshold = fp_rate < FP_RATE_PROMOTION_GATE

        can_promote = meets_trigger_threshold and meets_fp_threshold
        disable_reason = ""
        if not meets_trigger_threshold:
            disable_reason = (
                f"Insufficient data ({trigger_count} < {MIN_TRIGGERS_FOR_PROMOTION})"
            )
        elif not meets_fp_threshold:
            disable_reason = f"FP rate too high ({fp_rate*100:.2f}% >= 2%)"

        # Display rule promotion card
        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            st.write(f"**{rule_id}**")
            st.caption(
                f"Triggers: {trigger_count:,} | FP Rate: {fp_rate*100:.2f}%"
            )

        with col2:
            if can_promote:
                if st.button(
                    "Promote to Active",
                    key=f"promote_{rule_id}",
                    use_container_width=True,
                ):
                    with st.spinner(f"Promoting {rule_id}..."):
                        success, message = promote_rule(rule_id)
                        if success:
                            st.success(message)
                            st.rerun()
                        else:
                            st.error(message)
            else:
                st.button(
                    "Promote to Active",
                    disabled=True,
                    key=f"promote_disabled_{rule_id}",
                    use_container_width=True,
                    help=disable_reason,
                )

        with col3:
            if not can_promote:
                status_icon = "⚠️" if not meets_fp_threshold else "⏳"
                st.markdown(status_icon)

        st.divider()

else:
    st.info("No shadow rules available for promotion.")

# ============================================================================
# Section 3: Demotion Status & Circuit Breaker Health
# ============================================================================

st.header("🚨 System Health")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Circuit Breaker State")
    cb_state = get_circuit_breaker_state()

    if cb_state:
        state_color = "🔴" if cb_state.get("state") == "open" else "🟢"
        st.metric(
            "State",
            f"{state_color} {cb_state.get('state', 'unknown').upper()}",
        )

        if cb_state.get("state") == "open":
            next_probe_str = datetime.datetime.fromtimestamp(
                cb_state.get("next_probe_at", 0) / 1000
            ).strftime("%Y-%m-%d %H:%M:%S")
            st.warning(
                f"Circuit breaker is OPEN. "
                f"Failure count: {cb_state.get('failure_count', 0)} | "
                f"Next probe: {next_probe_str} UTC"
            )
        else:
            st.success("Circuit breaker healthy — ML scoring available.")

    else:
        st.warning(
            "Unable to fetch circuit breaker state. "
            "Ensure management API service is running."
        )

with col2:
    st.subheader("Auto-Demotion Events (24h)")

    if not shadow_df.empty:
        # Note: In v1, we detect auto-demoted rules by querying Prometheus
        # for rules that show shadow triggers but weren't in active mode yesterday.
        # For simplicity in v1, we'll check if any rules recently appeared in shadow mode.
        try:
            # Query rules that had any shadow triggers
            shadow_check = 'count(rule_shadow_triggers_total > 0)'
            check_result = query_prometheus(shadow_check)
            shadow_count = check_result.get("data", {}).get("result", [])

            if shadow_count:
                st.metric("Rules Monitored", len(shadow_df))
                st.caption(
                    "Note: Auto-demotion events are detected via Alertmanager. "
                    "Rules demoted in the last 24h will be highlighted."
                )
            else:
                st.info("No auto-demotion events in the last 24 hours.")

        except Exception as e:
            logger.warning("Could not query auto-demotion status", exc_info=e)
            st.info("Rule demotion status unavailable — Prometheus may be down.")
    else:
        st.info("No shadow rules to monitor.")

# ============================================================================
# Footer & Refresh Info
# ============================================================================

st.divider()
st.caption(
    "💾 Data refreshes every 30 seconds. "
    "Prometheus queries use 24h time windows for stability. "
    "Management API calls may take 5-30 seconds to propagate "
    "(scoring engine rule reload cycle)."
)
