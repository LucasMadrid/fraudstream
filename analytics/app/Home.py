"""FraudStream Analytics home page."""

import streamlit as st

from analytics.app._consumer import get_consumer

st.set_page_config(
    page_title="FraudStream Analytics",
    page_icon="🛡️",
    layout="wide",
)

consumer = get_consumer()

st.title("FraudStream Analytics")

col1, col2, col3 = st.columns(3)

with col1:
    status = "🟢 Connected" if consumer.is_alive() else "🔴 Disconnected"
    st.metric("Consumer", status)

with col2:
    st.metric("Consumer Lag", f"{consumer.consumer_lag:,} msgs")

with col3:
    buffered = consumer.queue.qsize()
    st.metric("Buffered Alerts", f"{buffered:,}")

st.markdown("---")
st.markdown(
    """
Use the sidebar to navigate:

| Page | Description |
|------|-------------|
| **Live Feed** | Real-time fraud alerts from `txn.fraud.alerts` |
| **Fraud Rate** | Historical fraud rate trends via Iceberg |
| **Rule Triggers** | Rule leaderboard and trigger history |
| **Model Compare** | Side-by-side model version comparison |
| **DLQ Inspector** | Dead-letter queue browser |
"""
)

st.caption("Metrics exposed at `:8004/metrics` · Consumer group `analytics.dashboard`")
