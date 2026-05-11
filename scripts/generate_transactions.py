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
import math
import random
import threading
import time
import uuid

# Genuine consumer/residential ISP IPs across major geographies.
# Avoids CDN, hosting, or datacenter ranges that geo-enrichment classifies as HOSTING.
_PUBLIC_IPS = [
    # US consumer ISPs
    "24.239.192.1",  # Comcast
    "71.236.192.1",  # Cox Communications
    "98.7.32.1",  # AT&T
    "173.66.172.1",  # Xfinity
    "108.210.16.1",  # Charter / Spectrum
    # EU residential ISPs
    "80.58.61.250",  # Telefónica / Orange ES
    "62.214.200.1",  # Deutsche Telekom DE
    "80.82.0.1",  # Proximus BE
    "212.54.40.1",  # BT Broadband UK
    "82.132.0.1",  # Virgin Media UK
    # LATAM
    "200.172.32.1",  # NET Serviços BR
    "148.240.0.1",  # TELMEX MX
    # APAC
    "203.0.96.1",  # KDDI JP
    "110.50.0.1",  # NTT Docomo JP
]

# (merchant_id, min_amount, max_amount, weight)
# Weights control relative transaction frequency; random.choices normalises automatically.
_MERCHANT_PROFILES: list[tuple[str, float, float, float]] = [
    ("merch-amazon", 5.00, 280.00, 0.20),  # online retail — most common
    ("merch-starbucks", 3.50, 14.00, 0.12),  # coffee — high frequency, low value
    ("merch-uber", 5.00, 48.00, 0.12),  # ride share
    ("merch-netflix", 8.99, 19.99, 0.10),  # streaming subscription
    ("merch-spotify", 4.99, 14.99, 0.09),  # music subscription
    ("merch-walmart", 8.00, 200.00, 0.09),  # retail
    ("merch-apple", 0.99, 199.00, 0.08),  # app / media store
    ("merch-shell", 24.00, 75.00, 0.07),  # petrol / gas
    ("merch-steam", 4.99, 59.99, 0.06),  # PC gaming
    ("merch-airbnb", 80.00, 2000.00, 0.04),  # accommodation — infrequent, high value
    ("merch-google", 1.00, 99.00, 0.03),  # Play Store / cloud
]

_CHANNELS = ["WEB", "MOBILE", "POS", "API"]
_CHANNEL_WEIGHTS = [0.35, 0.40, 0.20, 0.05]

_CURRENCIES = ["USD", "EUR", "GBP", "BRL", "MXN"]
_CURRENCY_WEIGHTS = [0.60, 0.20, 0.08, 0.07, 0.05]

_SCOPES = ["read", "write", "read write"]

# 100 accounts: at 500 ms / txn the normal traffic rate is ~1.2 txns/min per account,
# well under the VEL-001 threshold of 5 txns/min.
_ACCOUNTS = [f"acc-{i:04d}" for i in range(1, 101)]
_API_KEYS = [f"key-{i}" for i in range(1, 6)]

# ── Suspicious transaction profiles ─────────────────────────────────────
# Five dedicated accounts so velocity state accumulates quickly on a small pool.
# VEL-001 fires at vel_count_1m > 5; with 25 % of traffic concentrated here,
# the threshold is reached within ~1 minute.
_SUSPICIOUS_ACCOUNTS = ["acc-0001", "acc-0002", "acc-0003", "acc-0004", "acc-0005"]

# Hosting/datacenter IPs — enrichment classifies these as
# network_class=HOSTING, the condition for ND-004 (NEW_DEVICE_FOREIGN).
_HOSTING_IPS = [
    "52.0.0.1",  # AWS us-east-1
    "13.64.0.1",  # Azure westus
    "34.64.0.1",  # GCP us-central1
    "45.33.0.1",  # Linode (Akamai)
    "167.99.0.1",  # DigitalOcean
]

# Patterns: one is chosen per suspicious transaction.
# velocity_burst — same small account pool → accumulates VEL-001
# high_amount    — large amount on a suspicious account → VEL-002 when 2+ in 5 min
# hosting_ip     — datacenter IP → ND-004 once device_is_new is available
_SUSPICIOUS_PATTERNS = ["velocity_burst", "high_amount", "hosting_ip"]


def _luhn_complete(partial: str) -> str:
    """Append a Luhn check digit to make a valid card number."""
    digits = [int(d) for d in partial]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return partial + str(check)


def _sample_amount(min_amt: float, max_amt: float) -> float:
    """Lognormal sample anchored at the geometric midpoint of [min_amt, max_amt].

    Produces a right-skewed distribution: most transactions fall in the lower
    portion of the range (realistic spending behaviour), with a natural long tail
    toward max_amt.  Result is clamped and rounded to two decimal places.
    """
    if min_amt >= max_amt:
        return round(min_amt, 2)
    geo_mid = math.sqrt(min_amt * max_amt)
    mu = math.log(geo_mid)
    # sigma chosen so ±2σ in log-space spans [min, max]
    sigma = math.log(max_amt / min_amt) / 4.0
    sample = math.exp(random.gauss(mu, sigma))
    return round(max(min_amt, min(max_amt, sample)), 2)


def _make_payload() -> dict:
    now_ms = int(time.time() * 1000)

    # Merchant-category-aware amount: pick merchant first, then sample realistic amount
    merch_id, min_amt, max_amt, _ = random.choices(
        _MERCHANT_PROFILES,
        weights=[p[3] for p in _MERCHANT_PROFILES],
    )[0]

    # Visa (4xxx) and Mastercard (51xx–55xx) BIN prefixes with Luhn check digit
    prefix = random.choices(
        ["4", "51", "52", "53", "54", "55"],
        weights=[50, 10, 10, 10, 10, 10],
    )[0]
    partial = prefix + "".join(str(random.randint(0, 9)) for _ in range(15 - len(prefix)))
    card_number = _luhn_complete(partial)

    return {
        "transaction_id": str(uuid.uuid4()),
        "account_id": random.choice(_ACCOUNTS),
        "merchant_id": merch_id,
        "amount": _sample_amount(min_amt, max_amt),
        "currency": random.choices(_CURRENCIES, weights=_CURRENCY_WEIGHTS)[0],
        "event_time": now_ms,
        "channel": random.choices(_CHANNELS, weights=_CHANNEL_WEIGHTS)[0],
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
    high_amount    — large single amount (700-1600) from suspicious accounts;
                     two in 5 min = $1400-$3200 cumulative → triggers VEL-002
    hosting_ip     — datacenter caller IP to trigger ND-004
    """
    base = _make_payload()
    if pattern == "velocity_burst":
        base["account_id"] = random.choice(_SUSPICIOUS_ACCOUNTS)
    elif pattern == "high_amount":
        base["account_id"] = random.choice(_SUSPICIOUS_ACCOUNTS)
        base["amount"] = round(random.uniform(700.0, 1600.0), 2)
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
            except json.JSONDecodeError:
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

    if not 0.0 <= args.suspicious_rate <= 1.0:
        parser.error(f"--suspicious-rate must be in [0.0, 1.0], got {args.suspicious_rate}")

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
