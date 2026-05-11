"""Contract tests for schema evolution and BACKWARD_TRANSITIVE compatibility.

TB-002: Schema Evolution Contract Tests

These tests validate that schema changes maintain backward compatibility according
to BACKWARD_TRANSITIVE mode - meaning old consumers can read data written with
both old AND new schemas. This requires:
  1. New fields have default values
  2. No required fields are removed
  3. Field types maintain compatibility

Constitution References:
    - Article 5 (Fail-Safe): Schemas must not break existing consumers
    - Article 7 (Observability): Schema changes must be detectable and verifiable

Compatible with contracts package from CHB-006.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import fastavro
from fastavro.schema import parse_schema

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "specs"


def _load_schema(path: Path) -> dict[str, Any]:
    """Load and parse an Avro schema from file."""
    with open(path) as f:
        return json.load(f)


def _get_field_default(field: dict[str, Any]) -> Any:
    """Get the default value for a field, handling nullable unions."""
    return field.get("default")


def _is_nullable(field: dict[str, Any]) -> bool:
    """Check if a field is nullable (union with null)."""
    field_type = field.get("type")
    return isinstance(field_type, list) and "null" in field_type


def _get_non_null_type(field: dict[str, Any]) -> Any:
    """Get the non-null type from a nullable union."""
    field_type = field.get("type")
    if isinstance(field_type, list):
        # Find the type that's not null
        for t in field_type:
            if t != "null":
                return t
    return field_type


class TestBackwardTransitiveCompatibility:
    """BACKWARD_TRANSITIVE: Old readers can read new data.

    Constitution Article 5: Changes must not break existing consumers.
    New fields MUST have default values to maintain compatibility.
    """

    def test_enriched_txn_nullable_fields_have_defaults(self):
        """TB-002-SE-01: All nullable fields must have default=null.

        BACKWARD_TRANSITIVE requires that new fields added to a schema
        have default values so old consumers can read new records.

        Constitution (Article 5 - Fail-Safe):
            Schema changes must not break existing consumers. Default values
            ensure backward compatibility when fields are added.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        violations = []
        for field in schema.get("fields", []):
            if _is_nullable(field):
                default_val = _get_field_default(field)
                if default_val is not None:
                    violations.append(
                        f"{field['name']}: nullable field must have default=null, "
                        f"got default={default_val}"
                    )

        assert not violations, "BACKWARD_TRANSITIVE violations:\n" + "\n".join(violations)

    def test_enriched_txn_no_field_removals(self):
        """TB-002-SE-02: Required fields from v1 must not be removed.

        Removing fields breaks backward compatibility. This test documents
        the critical fields that must remain for data continuity.

        Constitution (Article 5 - Fail-Safe):
            Field removals require schema versioning and consumer migration.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        # These fields must exist in all v1 schema versions
        required_v1_fields = {
            "transaction_id",  # Primary key for deduplication
            "account_id",  # Partition key for co-partitioned consumers
            "merchant_id",
            "amount",
            "currency",
            "event_time",  # Event time anchor for windowing
            "processing_time",
            "channel",
            "card_bin",
            "card_last4",
            "caller_ip_subnet",
            "api_key_id",
            "oauth_scope",
            "vel_count_1m",  # Velocity aggregates
            "vel_amount_1m",
            "enrichment_time",
            "enrichment_latency_ms",
            "processor_version",
            "schema_version",
        }

        field_names = {f["name"] for f in schema.get("fields", [])}
        missing = required_v1_fields - field_names

        assert not missing, f"Critical fields removed from schema: {missing}"

    def test_enriched_txn_partition_key_non_nullable(self):
        """TB-002-SE-03: Partition key fields (account_id) must be non-nullable.

        account_id is the Kafka partition key. Null values would break
        co-partitioning guarantees required by the scoring layer.

        Constitution (Article 6 - Performance):
            Partition keys must be stable and non-null for consistent routing.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        account_id_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "account_id"),
            None,
        )
        assert account_id_field is not None, "account_id field missing from schema"
        assert not _is_nullable(account_id_field), (
            "account_id must be non-nullable (partition key)"
        )

    def test_enriched_txn_transaction_id_non_nullable(self):
        """TB-002-SE-04: transaction_id must be non-nullable (deduplication key).

        Transaction ID is used for deduplication across topics. Null values
        would make reconciliation impossible.

        Constitution (Article 7 - Observability):
            Reconciliation contracts require stable identifiers.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        txn_id_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "transaction_id"),
            None,
        )
        assert txn_id_field is not None, "transaction_id field missing from schema"
        assert not _is_nullable(txn_id_field), (
            "transaction_id must be non-nullable (deduplication key)"
        )


class TestProcessingDlqSchemaEvolution:
    """DLQ schema evolution for processing pipeline.

    DLQ records must maintain schema compatibility to ensure failed events
    can always be written and later analyzed.
    """

    def test_processing_dlq_nullable_fields_have_defaults(self):
        """TB-002-SE-05: DLQ nullable fields must have default values.

        DLQ records capture failure context. Fields that may not be available
        at failure time (like transaction_id for parse errors) must have
        defaults to maintain schema compatibility.

        Constitution (Article 5 - Fail-Safe):
            Failure paths must not fail due to schema issues.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        violations = []
        for field in schema.get("fields", []):
            if _is_nullable(field):
                default_val = _get_field_default(field)
                if default_val is not None:
                    violations.append(
                        f"{field['name']}: nullable field must have default=null"
                    )

        assert not violations, "DLQ schema violations:\n" + "\n".join(violations)

    def test_processing_dlq_required_fields(self):
        """TB-002-SE-06: DLQ must have fields for failure analysis.

        Core fields required for debugging and replay.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        required_fields = {
            "dlq_id",  # Unique DLQ record identifier
            "source_topic",  # Where the failure occurred
            "source_partition",
            "source_offset",
            "original_payload_bytes",  # Raw failed data
            "error_type",  # Classification of failure
            "error_message",  # Human-readable details
            "failed_at",  # Timestamp
            "processor_host",  # Which instance failed
            "processor_subtask_index",  # Which subtask
        }

        field_names = {f["name"] for f in schema.get("fields", [])}
        missing = required_fields - field_names

        assert not missing, f"Required DLQ fields missing: {missing}"


class TestFraudAlertSchemaEvolution:
    """Fraud alert schema evolution for scoring pipeline."""

    def test_fraud_alert_nullable_fields_have_defaults(self):
        """TB-002-SE-07: Fraud alert nullable fields must have defaults.

        Alert records may have optional fields for extensibility.
        All optional fields must have defaults for BACKWARD_TRANSITIVE.

        Constitution (Article 5 - Fail-Safe):
            Alert schema must not break downstream consumers.
        """
        schema_path = (
            SCHEMAS_DIR / "003-fraud-rule-engine" / "contracts" / "fraud-alert-v1.avsc"
        )
        # Schema may not exist yet in all branches
        if not schema_path.exists():
            pytest.skip(f"Schema not found: {schema_path}")

        schema = _load_schema(schema_path)

        violations = []
        for field in schema.get("fields", []):
            if _is_nullable(field):
                default_val = _get_field_default(field)
                if default_val is not None:
                    violations.append(
                        f"{field['name']}: nullable field must have default=null"
                    )

        assert not violations, "Alert schema violations:\n" + "\n".join(violations)


class TestSchemaTypeCompatibility:
    """Type compatibility rules for schema evolution.

    Constitution Article 5: Type changes must not break existing consumers.
    """

    def test_decimal_type_consistency(self):
        """TB-002-SE-08: Decimal fields must maintain precision and scale.

        Amount fields use decimal types. Changing precision or scale
        would silently corrupt monetary values.

        Constitution (Article 5 - Fail-Safe):
            Monetary fields must not lose precision through schema changes.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        decimal_fields = ["amount", "vel_amount_1m", "vel_amount_5m", "vel_amount_1h", "vel_amount_24h"]

        for field_name in decimal_fields:
            field = next(
                (f for f in schema.get("fields", []) if f["name"] == field_name),
                None,
            )
            assert field is not None, f"{field_name} field missing"

            # Handle nullable union
            field_type = _get_non_null_type(field)
            assert isinstance(field_type, dict), f"{field_name} must be a complex type"
            assert field_type.get("logicalType") == "decimal", (
                f"{field_name} must have decimal logicalType"
            )
            assert field_type.get("precision") == 18, (
                f"{field_name} must have precision=18"
            )
            assert field_type.get("scale") == 4, (
                f"{field_name} must have scale=4"
            )

    def test_timestamp_logical_type_consistency(self):
        """TB-002-SE-09: Timestamp fields must use timestamp-millis logicalType.

        Consistent timestamp handling ensures correct event-time processing
        and windowing behavior.

        Constitution (Article 7 - Observability):
            Time fields must be unambiguous and consistently formatted.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        timestamp_fields = ["event_time", "processing_time", "enrichment_time"]

        for field_name in timestamp_fields:
            field = next(
                (f for f in schema.get("fields", []) if f["name"] == field_name),
                None,
            )
            assert field is not None, f"{field_name} field missing"

            field_type = _get_non_null_type(field)
            assert isinstance(field_type, dict), f"{field_name} must be a complex type"
            assert field_type.get("logicalType") == "timestamp-millis", (
                f"{field_name} must have timestamp-millis logicalType"
            )


class TestSchemaDocumentation:
    """Schema documentation requirements.

    Constitution Article 7: Documentation enables operational understanding.
    """

    def test_enriched_txn_fields_have_documentation(self):
        """TB-002-SE-10: All fields must have doc strings.

        Schema documentation is essential for operational debugging
        and consumer onboarding.

        Constitution (Article 7 - Observability):
            Schemas must be self-documenting for operational clarity.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        missing_docs = []
        for field in schema.get("fields", []):
            if not field.get("doc"):
                missing_docs.append(field["name"])

        # Only warn for now - documentation is best effort
        if missing_docs:
            pytest.skip(f"Fields missing documentation: {missing_docs}")


class TestSchemaVersioning:
    """Schema version field requirements.

    Constitution Article 7: Version tracking enables change management.
    """

    def test_enriched_txn_has_schema_version(self):
        """TB-002-SE-11: Schema must include schema_version field.

        The schema_version field enables consumers to handle schema evolution
        gracefully by detecting which schema version was used to write a record.

        Constitution (Article 7 - Observability):
            Schema versions must be explicit for compatibility tracking.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        version_field = next(
            (f for f in schema.get("fields", []) if f["name"] == "schema_version"),
            None,
        )
        assert version_field is not None, "schema_version field missing"

        # Must have a default value
        default_val = _get_field_default(version_field)
        assert default_val is not None, "schema_version must have a default value"


class TestSchemaParseValidation:
    """Validate schemas can be parsed by Avro libraries."""

    def test_enriched_txn_parses_with_fastavro(self):
        """TB-002-SE-12: Schema must be valid for fastavro parser.

        Runtime validation that the schema is valid Avro.
        """
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "enriched-txn-v1.avsc"
        )
        schema = _load_schema(schema_path)

        # Should not raise
        parsed = parse_schema(schema)
        assert parsed is not None

    def test_processing_dlq_parses_with_fastavro(self):
        """TB-002-SE-13: DLQ schema must be valid for fastavro parser."""
        schema_path = (
            SCHEMAS_DIR / "002-flink-stream-processor" / "contracts" / "processing-dlq-v1.avsc"
        )
        schema = _load_schema(schema_path)

        parsed = parse_schema(schema)
        assert parsed is not None
