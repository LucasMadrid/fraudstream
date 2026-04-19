"""Persistence layer simulation: write synthetic records to Iceberg, validate via Trino.

Usage:
    ICEBERG_REST_URI=http://localhost:8181 \
    ICEBERG_WAREHOUSE=s3://fraudstream-lake \
    AWS_S3_ENDPOINT=http://localhost:9000 \
    python scripts/simulate_persistence.py
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import sys
import time
import uuid
from decimal import Decimal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("simulate_persistence")

os.environ.setdefault("ICEBERG_REST_URI", "http://localhost:8181")
os.environ.setdefault("ICEBERG_WAREHOUSE", "s3://fraudstream-lake/")
os.environ.setdefault("AWS_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")

# PyIceberg REST catalog config (must be set before pyiceberg imports)
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__URI", "http://localhost:8181")
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__WAREHOUSE", "s3://fraudstream-lake/")
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT", "http://localhost:9000")
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__S3__PATH__STYLE__ACCESS", "true")
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__S3__ACCESS__KEY__ID", "minioadmin")
os.environ.setdefault("PYICEBERG_CATALOG__ICEBERG__S3__SECRET__ACCESS__KEY", "minioadmin")

CHANNELS = ["web", "mobile", "pos", "api"]
CURRENCIES = ["USD", "EUR", "GBP", "BRL"]
GEO_COUNTRIES = ["US", "BR", "DE", "GB", "FR"]
GEO_CITIES = ["New York", "Sao Paulo", "Berlin", "London", "Paris"]
DECISIONS = ["ALLOW", "BLOCK", "REVIEW"]
RULES = ["high_velocity_1m", "geo_anomaly", "device_known_fraud", "amount_spike", "new_device"]
NOW_MS = int(time.time() * 1000)
DAY_MS = 86_400_000


def _make_enriched_record(i: int) -> dict:
    """Generate a synthetic enriched transaction dict."""
    txn_id = f"txn-sim-{i:04d}-{uuid.uuid4().hex[:8]}"
    account_id = f"acct-{(i % 20):04d}"
    event_time = NOW_MS - random.randint(0, DAY_MS)
    enrichment_time = event_time + random.randint(10, 200)
    return {
        "transaction_id": txn_id,
        "account_id": account_id,
        "merchant_id": f"merch-{(i % 10):04d}",
        "amount": Decimal(str(round(random.uniform(1.0, 5000.0), 4))),
        "currency": random.choice(CURRENCIES),
        "event_time": event_time,
        "processing_time": event_time + 5,
        "channel": random.choice(CHANNELS),
        "card_bin": f"{random.randint(400000, 499999)}",
        "card_last4": f"{random.randint(1000, 9999)}",
        "caller_ip_subnet": f"192.168.{random.randint(0, 255)}.0/24",
        "api_key_id": f"key-{(i % 5):04d}",
        "oauth_scope": "txn:write",
        "geo_lat": round(random.uniform(-90, 90), 6),
        "geo_lon": round(random.uniform(-180, 180), 6),
        "masking_lib_version": "1.2.3",
        "vel_count_1m": random.randint(0, 5),
        "vel_amount_1m": Decimal(str(round(random.uniform(0, 500), 4))),
        "vel_count_5m": random.randint(0, 15),
        "vel_amount_5m": Decimal(str(round(random.uniform(0, 1500), 4))),
        "vel_count_1h": random.randint(0, 60),
        "vel_amount_1h": Decimal(str(round(random.uniform(0, 10000), 4))),
        "vel_count_24h": random.randint(0, 200),
        "vel_amount_24h": Decimal(str(round(random.uniform(0, 50000), 4))),
        "geo_country": random.choice(GEO_COUNTRIES),
        "geo_city": random.choice(GEO_CITIES),
        "geo_network_class": random.choice(["residential", "business", "vpn"]),
        "geo_confidence": round(random.uniform(0.5, 1.0), 4),
        "device_first_seen": event_time - random.randint(0, 30 * DAY_MS),
        "device_txn_count": random.randint(1, 100),
        "device_known_fraud": random.random() < 0.05,
        "prev_geo_country": random.choice(GEO_COUNTRIES),
        "prev_txn_time_ms": event_time - random.randint(60_000, DAY_MS),
        "enrichment_time": enrichment_time,
        "enrichment_latency_ms": enrichment_time - event_time,
        "processor_version": "002-stream-processor@1.0.0",
        "schema_version": "1",
    }


def _make_decision_record(txn_id: str, account_id: str, event_time: int) -> dict:
    """Generate a synthetic fraud decision dict."""
    score = round(random.uniform(0.0, 1.0), 4)
    triggered = random.sample(RULES, k=random.randint(0, 3))
    decision = "BLOCK" if score >= 0.8 else ("REVIEW" if score >= 0.5 else "ALLOW")
    return {
        "transaction_id": txn_id,
        "decision": decision,
        "fraud_score": score,
        "rule_triggers": triggered,
        "model_version": "xgb-fraud-v2.1",
        "decision_time_ms": event_time + random.randint(5, 80),
        "latency_ms": float(random.randint(5, 120)),
        "schema_version": "1",
    }


def _trino_count(table: str) -> int:
    """Get row count from Iceberg table via Trino CLI in container."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            "fraudstream-trino",
            "trino",
            "--server",
            "localhost:8080",
            "--execute",
            f"SELECT COUNT(*) FROM iceberg.default.{table}",
            "--output-format",
            "TSV",
        ],
        capture_output=True,
        text=True,
    )
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip().isdigit()]
    return int(lines[0]) if lines else -1


def main() -> int:
    n = 50
    logger.info(f"Generating {n} synthetic transactions...")

    records = [_make_enriched_record(i) for i in range(n)]
    decisions = [
        _make_decision_record(r["transaction_id"], r["account_id"], r["event_time"])
        for r in records
    ]

    # --- Write enriched transactions ---
    logger.info("Opening IcebergEnrichedSink...")
    from pipelines.processing.operators.iceberg_sink import IcebergEnrichedSink

    enriched_sink = IcebergEnrichedSink()
    enriched_sink.open(None)

    t0 = time.monotonic()
    for rec in records:
        enriched_sink.invoke(rec, None)
    enriched_sink._flush()
    enriched_elapsed = time.monotonic() - t0
    logger.info(f"Enriched sink: {n} records pushed in {enriched_elapsed:.2f}s")

    # --- Write fraud decisions ---
    logger.info("Opening IcebergDecisionsSink...")
    from pipelines.scoring.sinks.iceberg_decisions import IcebergDecisionsSink

    decisions_sink = IcebergDecisionsSink()
    decisions_sink.open(None)

    t0 = time.monotonic()
    for dec in decisions:
        decisions_sink.invoke(dec, None)
    decisions_sink._flush()
    decisions_elapsed = time.monotonic() - t0
    logger.info(f"Decisions sink: {n} records pushed in {decisions_elapsed:.2f}s")

    # --- Validate via Trino ---
    logger.info("Waiting 2s for Iceberg metadata to propagate...")
    time.sleep(2)

    enriched_count = _trino_count("enriched_transactions")
    decisions_count = _trino_count("fraud_decisions")

    print("\n" + "=" * 60)
    print("PERSISTENCE LAYER SIMULATION RESULTS")
    print("=" * 60)
    print(f"  Records generated:               {n}")
    print(f"  enriched_transactions (Trino):   {enriched_count}")
    print(f"  fraud_decisions (Trino):         {decisions_count}")
    print(f"  Enriched write time:             {enriched_elapsed:.2f}s")
    print(f"  Decisions write time:            {decisions_elapsed:.2f}s")

    enriched_ok = enriched_count >= n
    decisions_ok = decisions_count >= n

    print(f"\n  enriched_transactions: {'PASS' if enriched_ok else 'FAIL'}")
    print(f"  fraud_decisions:       {'PASS' if decisions_ok else 'FAIL'}")
    print("=" * 60)

    if not enriched_ok or not decisions_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
