"""Create Iceberg tables for fraudstream.

Creates (or verifies) the two required Iceberg tables:
  - default.enriched_transactions
  - default.fraud_decisions

Safe to re-run — uses create_table_if_not_exists.

Usage:
    make iceberg-init
    # or directly:
    AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
    PYICEBERG_CATALOG__ICEBERG__URI=http://localhost:8181 \
    PYICEBERG_CATALOG__ICEBERG__WAREHOUSE=s3://fraudstream-lake/ \
    PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT=http://localhost:9000 \
    PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS=true \
    python scripts/iceberg_init.py
"""

from __future__ import annotations

import sys

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    ListType,
    NestedField,
    StringType,
    TimestampType,
)

CATALOG_NAME = "iceberg"
NAMESPACE = "default"


def _enriched_schema() -> Schema:
    return Schema(
        NestedField(1, "transaction_id", StringType(), required=True),
        NestedField(2, "account_id", StringType(), required=True),
        NestedField(3, "merchant_id", StringType(), required=True),
        NestedField(4, "amount", DecimalType(18, 4), required=True),
        NestedField(5, "currency", StringType(), required=True),
        NestedField(6, "event_time", TimestampType(), required=True),
        NestedField(7, "enrichment_time", TimestampType(), required=True),
        NestedField(8, "channel", StringType(), required=True),
        NestedField(9, "card_bin", StringType(), required=True),
        NestedField(10, "card_last4", StringType(), required=True),
        NestedField(11, "caller_ip_subnet", StringType(), required=True),
        NestedField(12, "api_key_id", StringType(), required=True),
        NestedField(13, "oauth_scope", StringType(), required=True),
        NestedField(14, "geo_lat", FloatType(), required=False),
        NestedField(15, "geo_lon", FloatType(), required=False),
        NestedField(16, "masking_lib_version", StringType(), required=True),
        NestedField(17, "vel_count_1m", IntegerType(), required=True),
        NestedField(18, "vel_amount_1m", DecimalType(18, 4), required=True),
        NestedField(19, "vel_count_5m", IntegerType(), required=True),
        NestedField(20, "vel_amount_5m", DecimalType(18, 4), required=True),
        NestedField(21, "vel_count_1h", IntegerType(), required=True),
        NestedField(22, "vel_amount_1h", DecimalType(18, 4), required=True),
        NestedField(23, "vel_count_24h", IntegerType(), required=True),
        NestedField(24, "vel_amount_24h", DecimalType(18, 4), required=True),
        NestedField(25, "geo_country", StringType(), required=False),
        NestedField(26, "geo_city", StringType(), required=False),
        NestedField(27, "geo_network_class", StringType(), required=False),
        NestedField(28, "geo_confidence", FloatType(), required=False),
        NestedField(29, "device_first_seen", TimestampType(), required=False),
        NestedField(30, "device_txn_count", IntegerType(), required=False),
        NestedField(31, "device_known_fraud", BooleanType(), required=False),
        NestedField(32, "prev_geo_country", StringType(), required=False),
        NestedField(33, "prev_txn_time_ms", TimestampType(), required=False),
        NestedField(34, "enrichment_latency_ms", IntegerType(), required=True),
        NestedField(35, "processor_version", StringType(), required=True),
        NestedField(36, "schema_version", StringType(), required=True),
    )


def _decisions_schema() -> Schema:
    return Schema(
        NestedField(1, "transaction_id", StringType(), required=True),
        NestedField(2, "decision", StringType(), required=True),
        NestedField(3, "fraud_score", DoubleType(), required=True),
        NestedField(
            4,
            "rule_triggers",
            ListType(101, StringType(), element_required=True),
            required=True,
        ),
        NestedField(5, "model_version", StringType(), required=True),
        NestedField(6, "decision_time_ms", TimestampType(), required=True),
        NestedField(7, "latency_ms", DoubleType(), required=True),
        NestedField(8, "schema_version", StringType(), required=True),
    )


def main() -> int:
    print(f"Connecting to Iceberg catalog '{CATALOG_NAME}'...")
    catalog = load_catalog(CATALOG_NAME)

    # Ensure namespace exists
    try:
        catalog.create_namespace(NAMESPACE)
        print(f"Created namespace '{NAMESPACE}'.")
    except NamespaceAlreadyExistsError:
        print(f"Namespace '{NAMESPACE}' already exists.")

    # enriched_transactions
    enriched_id = (NAMESPACE, "enriched_transactions")
    try:
        catalog.load_table(enriched_id)
        print("Table 'default.enriched_transactions' already exists — skipping.")
    except Exception:
        catalog.create_table(
            identifier=enriched_id,
            schema=_enriched_schema(),
            partition_spec=UNPARTITIONED_PARTITION_SPEC,
            properties={"format-version": "2"},
        )
        print("Created table 'default.enriched_transactions'.")

    # fraud_decisions
    decisions_id = (NAMESPACE, "fraud_decisions")
    try:
        catalog.load_table(decisions_id)
        print("Table 'default.fraud_decisions' already exists — skipping.")
    except Exception:
        catalog.create_table(
            identifier=decisions_id,
            schema=_decisions_schema(),
            partition_spec=UNPARTITIONED_PARTITION_SPEC,
            properties={"format-version": "2"},
        )
        print("Created table 'default.fraud_decisions'.")

    print("\nDone. Both Iceberg tables are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
