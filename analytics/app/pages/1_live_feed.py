"""Live fraud alert feed — streams from the analytics Kafka consumer queue."""

import time
from collections import deque

import streamlit as st

from analytics.consumers.kafka_consumer import AnalyticsKafkaConsumer, FraudAlertDisplay

st.set_page_config(page_title="Live Feed", page_icon="⚡", layout="wide")
st.title("⚡ Live Fraud Alert Feed")

DEQUE_MAX = 500
POLL_INTERVAL_S = 1.0

_SEVERITY_COLOR = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}
_DECISION_COLOR = {
    "BLOCK": "🛑",
    "FLAG": "⚠️",
    "ALLOW": "✅",
    "UNKNOWN": "❓",
}


def _get_consumer() -> AnalyticsKafkaConsumer | None:
    return st.session_state.get("consumer")


consumer = _get_consumer()

# ── connection banner ────────────────────────────────────────────────────────
if consumer is None or not consumer.is_alive():
    st.error("⚠️ Consumer not running — navigate to Home to start it, then return here.")
    st.stop()

# ── local alert buffer (per session) ─────────────────────────────────────────
if "live_feed_buffer" not in st.session_state:
    st.session_state["live_feed_buffer"] = deque(maxlen=DEQUE_MAX)
if "live_feed_seen" not in st.session_state:
    st.session_state["live_feed_seen"] = set()

buf: deque[FraudAlertDisplay] = st.session_state["live_feed_buffer"]
seen: set[str] = st.session_state["live_feed_seen"]

# ── drain queue into buffer (deduplicate by transaction_id) ──────────────────
new_count = 0
while True:
    try:
        alert = consumer.queue.get_nowait()
        if alert.transaction_id not in seen:
            buf.appendleft(alert)
            seen.add(alert.transaction_id)
            new_count += 1
    except Exception:
        break

# ── controls ─────────────────────────────────────────────────────────────────
col_lag, col_buf, col_new = st.columns(3)
col_lag.metric("Consumer Lag", f"{consumer.consumer_lag:,} msgs")
col_buf.metric("Buffered", f"{len(buf):,}")
col_new.metric("New (this poll)", f"{new_count:,}")

st.markdown("---")

if not buf:
    placeholder = st.empty()
    placeholder.info(
        "Waiting for fraud alerts… (alerts appear as they arrive on `txn.fraud.alerts`)"
    )
else:
    st.caption(
        f"Showing latest {len(buf)} unique alerts. Auto-refreshing every {POLL_INTERVAL_S}s."
    )
    for alert in list(buf)[:100]:
        sev_icon = _SEVERITY_COLOR.get(alert.severity, "⚪")
        dec_icon = _DECISION_COLOR.get(alert.decision, "❓")
        ts = alert.evaluation_timestamp.strftime("%H:%M:%S")
        rules = ", ".join(alert.rule_triggers) or "—"
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 4])
            c1.write(f"`{ts}`")
            c2.write(f"{sev_icon} {alert.severity}")
            c3.write(f"{dec_icon} {alert.decision}")
            c4.write(f"`{alert.account_id}`")
            c5.write(f"Rules: {rules}")

# ── auto-refresh ─────────────────────────────────────────────────────────────
time.sleep(POLL_INTERVAL_S)
st.rerun()
