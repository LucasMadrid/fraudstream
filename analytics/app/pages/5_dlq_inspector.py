"""DLQ Inspector — browse dead-letter queue messages with PII masking.

Uses an ephemeral consumer group per session (dlq-inspector-{uuid}) with no
persistent offset, as mandated by the analytics consumer spec.
"""

import json
import os
import uuid
from datetime import UTC, datetime

import streamlit as st

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DLQ_TOPIC = os.environ.get("DLQ_TOPIC", "txn.api.dlq")
FETCH_MAX = 100


@st.cache_resource
def _session_group() -> str:
    return f"dlq-inspector-{uuid.uuid4().hex[:8]}"


GROUP_ID = _session_group()

st.set_page_config(page_title="DLQ Inspector", page_icon="🗂️", layout="wide")
st.title("🗂️ Dead-Letter Queue Inspector")
st.caption(f"Ephemeral consumer group: `{GROUP_ID}` — offsets not committed")

# ── controls ──────────────────────────────────────────────────────────────────
with st.sidebar:
    fetch_n = st.slider("Messages to fetch", 1, FETCH_MAX, 20)
    auto_refresh = st.toggle("Auto-refresh (30s)", value=False)
    mask_pii = st.toggle("Mask PII fields", value=True)

fetch_btn = st.button("Fetch DLQ Messages", type="primary")

if not fetch_btn and not auto_refresh:
    st.info("Click **Fetch DLQ Messages** to load messages from the dead-letter queue.")
    st.stop()

# ── fetch ─────────────────────────────────────────────────────────────────────
try:
    from confluent_kafka import Consumer, KafkaException
except ImportError:
    st.error("confluent-kafka not installed.")
    st.stop()

messages: list[dict] = []
errors: list[str] = []

try:
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([DLQ_TOPIC])

    deadline = 5.0
    fetched = 0
    start = __import__("time").monotonic()
    while fetched < fetch_n and (__import__("time").monotonic() - start) < deadline:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            errors.append(str(msg.error()))
            continue
        raw = msg.value()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw.decode("utf-8", errors="replace")}

        if mask_pii:
            # Mask known PII-carrying fields
            for field in ("card_number", "pan", "ip_address", "ip", "email"):
                if field in payload:
                    val = str(payload[field])
                    if len(val) >= 10:
                        payload[field] = val[:6] + "****" + val[-4:]
                    else:
                        payload[field] = "****"

        messages.append(
            {
                "offset": msg.offset(),
                "partition": msg.partition(),
                "timestamp": datetime.fromtimestamp(msg.timestamp()[1] / 1000, tz=UTC).isoformat()
                if msg.timestamp()[1] > 0
                else "—",
                "payload": payload,
            }
        )
        fetched += 1
except KafkaException as ke:
    st.error(f"Kafka error: {ke}")
    st.stop()
finally:
    try:
        consumer.close()
    except Exception:
        pass

# ── display ───────────────────────────────────────────────────────────────────
if errors:
    for e in errors:
        st.warning(f"Consumer error: {e}")

if not messages:
    st.info(f"No messages found on `{DLQ_TOPIC}`. The DLQ may be empty.")
else:
    st.success(f"Fetched {len(messages)} message(s)")
    for i, m in enumerate(messages):
        with st.expander(
            f"[{i + 1}] offset={m['offset']} part={m['partition']} ts={m['timestamp']}"
        ):
            st.json(m["payload"])

if auto_refresh:
    import time

    time.sleep(30)
    st.rerun()
