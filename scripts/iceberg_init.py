#!/usr/bin/env python3
"""Iceberg table initialization script.

Creates the Iceberg catalog connection and initializes the required tables:
- default.enriched_transactions
- default.fraud_decisions

Usage:
    python scripts/iceberg_init.py

Environment variables:
    PYICEBERG_CATALOG__ICEBERG__URI - Iceberg REST catalog URI (default: http://localhost:8181)
    PYICEBERG_CATALOG__ICEBERG__WAREHOUSE - S3 warehouse path (default: s3://fraudstream-lake/)
    PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT - MinIO S3 endpoint (default: http://localhost:9000)
    PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS - Use path-style S3 access (default: true)
    AWS_ACCESS_KEY_ID - S3 access key (default: minioadmin)
    AWS_SECRET_ACCESS_KEY - S3 secret key (default: minioadmin)
"""

from __future__ import annotations

import logging
import os
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_enriched_transactions_schema():
    """Return the PyArrow schema for enriched_transactions table.

    Matches the schema in pipelines/processing/operators/iceberg_sink.py
    """
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("transaction_id", pa.string(), nullable=False),
            pa.field("account_id", pa.string(), nullable=False),
            pa.field("merchant_id", pa.string(), nullable=False),
            pa.field("amount", pa.decimal128(18, 4), nullable=False),
            pa.field("currency", pa.string(), nullable=False),
            pa.field("event_time", pa.timestamp("us"), nullable=False),
            pa.field("enrichment_time", pa.timestamp("us"), nullable=False),
            pa.field("channel", pa.string(), nullable=False),
            pa.field("card_bin", pa.string(), nullable=False),
            pa.field("card_last4", pa.string(), nullable=False),
            pa.field("caller_ip_subnet", pa.string(), nullable=False),
            pa.field("api_key_id", pa.string(), nullable=False),
            pa.field("oauth_scope", pa.string(), nullable=False),
            pa.field("geo_lat", pa.float32(), nullable=True),
            pa.field("geo_lon", pa.float32(), nullable=True),
            pa.field("masking_lib_version", pa.string(), nullable=False),
            pa.field("vel_count_1m", pa.int32(), nullable=False),
            pa.field("vel_amount_1m", pa.decimal128(18, 4), nullable=False),
            pa.field("vel_count_5m", pa.int32(), nullable=False),
            pa.field("vel_amount_5m", pa.decimal128(18, 4), nullable=False),
            pa.field("vel_count_1h", pa.int32(), nullable=False),
            pa.field("vel_amount_1h", pa.decimal128(18, 4), nullable=False),
            pa.field("vel_count_24h", pa.int32(), nullable=False),
            pa.field("vel_amount_24h", pa.decimal128(18, 4), nullable=False),
            pa.field("geo_country", pa.string(), nullable=True),
            pa.field("geo_city", pa.string(), nullable=True),
            pa.field("geo_network_class", pa.string(), nullable=True),
            pa.field("geo_confidence", pa.float32(), nullable=True),
            pa.field("device_first_seen", pa.timestamp("us"), nullable=True),
            pa.field("device_txn_count", pa.int32(), nullable=True),
            pa.field("device_known_fraud", pa.bool_(), nullable=True),
            pa.field("prev_geo_country", pa.string(), nullable=True),
            pa.field("prev_txn_time_ms", pa.timestamp("us"), nullable=True),
            pa.field("enrichment_latency_ms", pa.int32(), nullable=False),
            pa.field("processor_version", pa.string(), nullable=False),
            pa.field("schema_version", pa.string(), nullable=False),
        ]
    )


def get_fraud_decisions_schema():
    """Return the PyArrow schema for fraud_decisions table.

    Matches the schema in pipelines/scoring/sinks/iceberg_decisions.py
    """
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("transaction_id", pa.string(), nullable=False),
            pa.field("decision", pa.string(), nullable=False),
            pa.field("fraud_score", pa.float64(), nullable=False),
            pa.field("rule_triggers", pa.list_(pa.string()), nullable=False),
            pa.field("model_version", pa.string(), nullable=False),
            pa.field("decision_time_ms", pa.timestamp("us"), nullable=False),
            pa.field("latency_ms", pa.float64(), nullable=False),
            pa.field("schema_version", pa.string(), nullable=False),
        ]
    )


def create_catalog():
    """Create and return the Iceberg catalog connection."""
    from pyiceberg.catalog import load_catalog

    # Set default environment variables if not present
    defaults = {
        "PYICEBERG_CATALOG__ICEBERG__URI": "http://localhost:8181",
        "PYICEBERG_CATALOG__ICEBERG__WAREHOUSE": "s3://fraudstream-lake/",
        "PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT": "http://localhost:9000",
        "PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS": "true",
        "AWS_ACCESS_KEY_ID": "minioadmin",
        "AWS_SECRET_ACCESS_KEY": "minioadmin",
    }

    for key, value in defaults.items():
        if not os.environ.get(key):
            os.environ[key] = value
            logger.debug(f"Set default {key}={value}")

    logger.info("Loading Iceberg catalog...")

    # Build catalog configuration from environment
    catalog_config = {
        "uri": os.environ.get("PYICEBERG_CATALOG__ICEBERG__URI"),
        "warehouse": os.environ.get("PYICEBERG_CATALOG__ICEBERG__WAREHOUSE"),
        "s3.endpoint": os.environ.get("PYICEBERG_CATALOG__ICEBERG__S3__ENDPOINT"),
        "s3.path-style-access": os.environ.get(
            "PYICEBERG_CATALOG__ICEBERG__S3__PATH_STYLE_ACCESS", "true"
        ),
    }

    catalog = load_catalog("iceberg", **catalog_config)
    logger.info("Catalog loaded successfully")
    return catalog


def create_table(catalog, table_name: str, schema, partition_spec=None):
    """Create an Iceberg table if it doesn't exist.

    Args:
        catalog: The Iceberg catalog instance
        table_name: Fully qualified table name (e.g., "default.enriched_transactions")
        schema: PyArrow schema for the table
        partition_spec: Optional partition specification

    Returns:
        The table instance (existing or newly created)
    """
    try:
        # Try to load existing table
        table = catalog.load_table(table_name)
        logger.info(f"Table '{table_name}' already exists")
        return table
    except Exception as e:
        error_str = str(e).lower()
        if any(msg in error_str for msg in ["does not exist", "not found", "nosuchtable"]):
            logger.info(f"Creating table '{table_name}'...")
            try:
                # Only pass partition_spec if provided
                kwargs = {"identifier": table_name, "schema": schema}
                if partition_spec is not None:
                    kwargs["partition_spec"] = partition_spec

                table = catalog.create_table(**kwargs)
                logger.info(f"Table '{table_name}' created successfully")
                return table
            except Exception as create_error:
                logger.error(f"Failed to create table '{table_name}': {create_error}")
                raise
        else:
            logger.error(f"Error loading table '{table_name}': {e}")
            raise


def main():
    """Initialize Iceberg tables."""
    try:
        # Create catalog connection
        catalog = create_catalog()

        # Create enriched_transactions table
        enriched_schema = get_enriched_transactions_schema()
        create_table(catalog, "default.enriched_transactions", enriched_schema)

        # Create fraud_decisions table
        decisions_schema = get_fraud_decisions_schema()
        create_table(catalog, "default.fraud_decisions", decisions_schema)

        logger.info("=" * 60)
        logger.info("Iceberg table initialization complete!")
        logger.info("=" * 60)
        logger.info("Tables created:")
        logger.info("  - default.enriched_transactions")
        logger.info("  - default.fraud_decisions")
        logger.info("=" * 60)
        return 0

    except ImportError as e:
        logger.error(f"Missing required dependency: {e}")
        logger.error("Please install pyiceberg: pip install 'pyiceberg[pyarrow,s3fs]>=0.11'")
        return 1
    except Exception as e:
        logger.error(f"Initialization failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
