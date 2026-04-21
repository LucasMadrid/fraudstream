import os

import streamlit as st

from analytics.consumers.kafka_consumer import AnalyticsKafkaConsumer
from analytics.consumers.metrics import start_metrics_server

st.set_page_config(
    page_title="FraudStream Analytics",
    page_icon="🛡️",
    layout="wide",
)

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8004"))

# Start Prometheus metrics server once per process
start_metrics_server(METRICS_PORT)

# Start the Kafka consumer thread once per Streamlit session
if "consumer" not in st.session_state:
    consumer = AnalyticsKafkaConsumer(bootstrap_servers=BOOTSTRAP)
    consumer.start()
    st.session_state["consumer"] = consumer

consumer: AnalyticsKafkaConsumer = st.session_state["consumer"]

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
| **Fraud Rate** | Historical fraud rate trends via Trino/Iceberg |
| **Rule Triggers** | Rule leaderboard and trigger history |
| **Model Compare** | Side-by-side model version comparison |
| **DLQ Inspector** | Dead-letter queue browser |
"""
)

st.caption(f"Metrics exposed at `:{METRICS_PORT}/metrics` · Consumer group `analytics.dashboard`")
