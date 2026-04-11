"""Synthetic transaction generator + enriched output consumer.

Produces realistic Avro-serialized transactions to txn.api and streams
the enriched output from txn.enriched in real time.

Usage:
    python3.11 scripts/generate_transactions.py [--count N] [--delay MS]
    python3.11 scripts/generate_transactions.py --consume-only   # just watch txn.enriched

Examples:
    python3.11 scripts/generate_transactions.py --count 20 --delay 500
    python3.11 scripts/generate_transactions.py --consume-only
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid

# Concrete public IPs for realistic geo enrichment results (not subnets)
_PUBLIC_IPS = [
    "8.8.8.1",  # Google DNS (US)
    "1.1.1.1",  # Cloudflare (AU)
    "185.60.216.35",  # Facebook (IE)
    "151.101.1.1",  # Fastly CDN (US)
    "104.16.0.1",  # Cloudflare (US)
    "13.32.0.1",  # AWS CloudFront (US)
    "31.13.64.1",  # Facebook (IE)
    "142.250.80.1",  # Google (US)
    "77.234.44.1",  # (NL)
    "5.9.0.1",  # Hetzner (DE)
]

_MERCHANTS = [
    "merch-amazon",
    "merch-netflix",
    "merch-uber",
    "merch-airbnb",
    "merch-spotify",
    "merch-apple",
    "merch-google",
    "merch-steam",
]

_CHANNELS = ["WEB", "MOBILE", "POS", "API"]
_CURRENCIES = ["USD", "EUR", "GBP", "BRL", "MXN"]
_SCOPES = ["read", "write", "read write"]

# A small pool of accounts so velocity state accumulates across messages
_ACCOUNTS = [f"acc-{i:04d}" for i in range(1, 11)]
_API_KEYS = [f"key-{i}" for i in range(1, 6)]

# ── Suspicious transaction profiles ──────────────────────────────────────────
# Two dedicated accounts targeted repeatedly to accumulate velocity state fast.
# VEL-001 fires at 5 txns/1min; with 25% of traffic from 2 accounts the
# threshold is reached quickly even at moderate generation rates.
_SUSPICIOUS_ACCOUNTS = ["acc-0001", "acc-0002"]

# Hosting/datacenter IPs — enrichment classifies these as
# network_class=HOSTING, the condition for ND-004 (NEW_DEVICE_FOREIGN).
_HOSTING_IPS = [
    "52.0.0.1",   # AWS us-east-1
    "13.64.0.1",  # Azure westus
    "34.64.0.1",  # GCP us-central1
    "45.33.0.1",  # Linode (Akamai)
    "167.99.0.1", # DigitalOcean
]

# Patterns: one is chosen per suspicious transaction.
# velocity_burst — same small account pool → accumulates VEL-001
# high_amount    — amount 800-2000         → VEL-002 / ND-001
# hosting_ip     — datacenter IP           → ND-004
_SUSPICIOUS_PATTERNS = ["velocity_burst", "high_amount", "hosting_ip"]


def _luhn_complete(partial: str) -> str:
    """Append a Luhn check digit to make a valid card number."""
    digits = [int(d) for d in partial]
    # Double every second digit from the right (position counting from check digit slot)
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return partial + str(check)


def _make_payload() -> dict:
    now_ms = int(time.time() * 1000)
    prefix = random.choice(["4", "5"])
    partial = prefix + "".join([str(random.randint(0, 9)) for _ in range(14)])
    card_number = _luhn_complete(partial)

    return {
        "transaction_id": str(uuid.uuid4()),
        "account_id": random.choice(_ACCOUNTS),
        "merchant_id": random.choice(_MERCHANTS),
        "amount": round(random.uniform(5.0, 300.0), 2),
        "currency": random.choice(_CURRENCIES),
        "event_time": now_ms,
        "channel": random.choice(_CHANNELS),
        "card_number": card_number,
        "caller_ip": random.choice(_PUBLIC_IPS),
        "api_key_id": random.choice(_API_KEYS),
        "oauth_scope": random.choice(_SCOPES),
        "geo_lat": None,
        "geo_lon": None,
    }


_RED = "\033[31m"
_RESET_COLOR = "\033[0m"


def _make_suspicious_payload(pattern: str) -> dict:
    """Return a transaction crafted to trigger one or more fraud rules.

    velocity_burst — targets _SUSPICIOUS_ACCOUNTS to accumulate VEL-001
    high_amount    — large single amount (800-2000) to trigger VEL-002 / ND-001
    hosting_ip     — datacenter caller IP to trigger ND-004
    """
    base = _make_payload()
    if pattern == "velocity_burst":
        base["account_id"] = random.choice(_SUSPICIOUS_ACCOUNTS)
    elif pattern == "high_amount":
        base["amount"] = round(random.uniform(800.0, 2000.0), 2)
    elif pattern == "hosting_ip":
        base["caller_ip"] = random.choice(_HOSTING_IPS)
    return base


def _consume_enriched(bootstrap_servers: str, stop_event: threading.Event) -> None:
    """Background thread: tail txn.enriched and pretty-print each record."""
    from confluent_kafka import Consumer, KafkaError

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"generator-monitor-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    consumer.subscribe(["txn.enriched"])

    _GEO_COLOR = "\033[32m"  # green
    _RESET = "\033[0m"

    try:
        while not stop_event.is_set():
            msg = consumer.poll(timeout=0.5)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    print(f"[consumer] error: {msg.error()}")
                continue

            try:
                record = json.loads(msg.value())
            except Exception:
                print(f"[consumer] non-JSON message: {msg.value()!r}")
                continue

            geo = (
                f"{_GEO_COLOR}{record.get('geo_country')} / {record.get('geo_city')}{_RESET}"
                if record.get("geo_country")
                else "geo=null"
            )
            print(
                f"  ← enriched  txn={record.get('transaction_id')}  "
                f"acct={record.get('account_id')}  "
                f"vel_1m={record.get('vel_count_1m')}  "
                f"{geo}  "
                f"device_count={record.get('device_txn_count')}  "
                f"latency={record.get('enrichment_latency_ms')}ms"
            )
    finally:
        consumer.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthetic transaction generator + enriched consumer"
    )
    parser.add_argument(
        "--count", type=int, default=10, help="Number of transactions to produce (0 = unlimited)"
    )
    parser.add_argument("--delay", type=int, default=500, help="Delay between messages (ms)")
    parser.add_argument("--kafka-brokers", default="localhost:9092")
    parser.add_argument("--schema-registry", default="http://localhost:8081")
    parser.add_argument(
        "--consume-only",
        action="store_true",
        help="Only consume txn.enriched, do not produce",
    )
    parser.add_argument(
        "--suspicious-rate",
        type=float,
        default=0.25,
        help=(
            "Fraction of transactions injected as suspicious (0.0–1.0). "
            "Default 0.25 → ~2-3 per 10 messages."
        ),
    )
    args = parser.parse_args()

    stop_event = threading.Event()
    consumer_thread = threading.Thread(
        target=_consume_enriched,
        args=(args.kafka_brokers, stop_event),
        daemon=True,
    )
    consumer_thread.start()
    print("Consuming from txn.enriched (Ctrl+C to stop) ...")

    if args.consume_only:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
        return

    from pipelines.ingestion.api.config import ProducerConfig
    from pipelines.ingestion.api.producer import ProducerService

    config = ProducerConfig(
        bootstrap_servers=args.kafka_brokers,
        schema_registry_url=args.schema_registry,
    )
    service = ProducerService(config)
    service.start()

    unlimited = args.count == 0
    count_label = "unlimited" if unlimited else args.count
    print(f"Producing {count_label} transactions to txn.api ...")
    if args.suspicious_rate > 0:
        print(
            f"  Suspicious rate: {args.suspicious_rate:.0%} "
            f"(patterns: {', '.join(_SUSPICIOUS_PATTERNS)})"
        )
    ok = 0
    i = 0
    try:
        while unlimited or i < args.count:
            if random.random() < args.suspicious_rate:
                pattern = random.choice(_SUSPICIOUS_PATTERNS)
                payload = _make_suspicious_payload(pattern)
                tag = f" {_RED}[{pattern}]{_RESET_COLOR}"
            else:
                payload = _make_payload()
                tag = ""
            try:
                result = service.publish(payload)
                print(
                    f"  → produced  txn={result.transaction_id}  "
                    f"acct={payload['account_id']}  "
                    f"amount={payload['amount']} {payload['currency']}  "
                    f"ip={payload['caller_ip']}{tag}"
                )
                ok += 1
            except Exception as exc:
                print(f"  → ERROR: {exc}")
            i += 1
            if args.delay:
                time.sleep(args.delay / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        service.flush()
        print(
            f"\nProduced {ok}/{i} messages. Waiting for enriched output (Ctrl+C again to exit)..."
        )
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            pass
        stop_event.set()


if __name__ == "__main__":
    main()
