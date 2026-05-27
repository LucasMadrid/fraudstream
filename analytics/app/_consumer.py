"""Process-global Kafka consumer singleton for the Streamlit app."""

import os

import streamlit as st

from analytics.consumers.kafka_consumer import AnalyticsKafkaConsumer
from analytics.consumers.metrics import start_metrics_server


@st.cache_resource
def get_consumer() -> AnalyticsKafkaConsumer:
    """Return the single shared consumer, starting it on first call."""
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    try:
        metrics_port = int(os.environ.get("METRICS_PORT", "8004"))
    except ValueError:
        metrics_port = 8004

    start_metrics_server(metrics_port)
    consumer = AnalyticsKafkaConsumer(bootstrap_servers=bootstrap)
    consumer.start()
    return consumer
