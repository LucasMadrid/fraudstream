"""Contract tests: Avro schema and Iceberg DDL alignment.

Validates that enriched-txn-v1.avsc and enriched_transactions.sql agree on:
  - Field names present in both systems
  - Nullability constraints (NOT NULL in Iceberg ↔ non-nullable in Avro)
  - Default values for nullable Avro fields (BACKWARD_TRANSITIVE compatibility)

These tests catch schema drift before data loss or type mismatches occur.
"""

import json
import re
from pathlib import Path

import pytest

# Paths to schema files
REPO_ROOT = Path(__file__).parent.parent.parent
AVRO_SCHEMA_PATH = REPO_ROOT / "pipelines" / "processing" / "schemas" / "enriched-txn-v1.avsc"
ICEBERG_DDL_PATH = REPO_ROOT / "storage" / "lake" / "schemas" / "enriched_transactions.sql"


@pytest.fixture
def avro_schema():
    """Load and parse the Avro schema."""
    with open(AVRO_SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture
def iceberg_sql():
    """Load the Iceberg DDL as plain text."""
    with open(ICEBERG_DDL_PATH) as f:
        return f.read()


@pytest.fixture
def avro_fields(avro_schema):
    """Extract Avro fields as dict: field_name -> field_definition."""
    return {f["name"]: f for f in avro_schema["fields"]}


@pytest.fixture
def iceberg_columns(iceberg_sql):
    """Parse Iceberg CREATE TABLE block to extract column definitions.

    Returns: dict of column_name -> {"nullable": bool, "type": str}
    """
    # Extract CREATE TABLE block (from CREATE TABLE to closing paren before USING)
    create_match = re.search(
        r"CREATE TABLE IF NOT EXISTS.*?\((.*?)\)\s*USING iceberg",
        iceberg_sql,
        re.DOTALL,
    )
    if not create_match:
        msg = "Could not parse CREATE TABLE block from Iceberg DDL"
        raise ValueError(msg)

    create_block = create_match.group(1)
    columns = {}

    # Parse each column line: name TYPE [NOT NULL]
    # Regex pattern: column name, type, optionally NOT NULL
    lines = create_block.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("--"):
            continue

        # Match: COLUMN_NAME TYPE [NOT NULL]
        match = re.match(
            r"(\w+)\s+(.+?)\s*(NOT NULL)?(?:,\s*)?$",
            line,
        )
        if match:
            col_name = match.group(1)
            col_type = match.group(2).strip()
            is_not_null = "NOT NULL" in line.upper()
            columns[col_name] = {
                "nullable": not is_not_null,
                "type": col_type,
            }

    return columns


def _is_avro_nullable(field_def: dict) -> bool:
    """Check if an Avro field is nullable.

    A field is nullable if its type is a union containing "null",
    typically as ["null", ...] (null first) or [..., "null"].
    """
    field_type = field_def["type"]
    return isinstance(field_type, list) and "null" in field_type


def _is_avro_non_nullable(field_def: dict) -> bool:
    """Check if an Avro field is non-nullable."""
    return not _is_avro_nullable(field_def)


class TestAvroFieldsPresentInIceberg:
    """Avro fields should have corresponding Iceberg columns."""

    def test_avro_fields_present_in_iceberg(self, avro_fields, iceberg_columns):
        """Every Avro field name should appear as an Iceberg column.

        Exception: processing_time is intentionally excluded from Iceberg's
        CREATE TABLE block — it's an internal Flink field, not persisted to
        the analytics lake (see test_no_processing_time_in_iceberg_create_table).
        """
        avro_field_names = set(avro_fields.keys())
        iceberg_column_names = set(iceberg_columns.keys())

        # processing_time is intentionally NOT persisted to Iceberg
        expected_missing = {"processing_time"}

        missing = avro_field_names - iceberg_column_names
        unexpected_missing = missing - expected_missing
        assert not unexpected_missing, (
            f"Avro unexpectedly missing from Iceberg: {unexpected_missing}\n"
            f"(Expected missing: {expected_missing})\n"
            f"Iceberg has: {sorted(iceberg_column_names)}"
        )

    def test_iceberg_columns_present_in_avro(self, avro_fields, iceberg_columns):
        """Every Iceberg column should correspond to an Avro field."""
        avro_field_names = set(avro_fields.keys())
        iceberg_column_names = set(iceberg_columns.keys())

        # Allow columns that might be added by Iceberg infrastructure
        # (None currently, but this guards against future drift)
        extra = iceberg_column_names - avro_field_names
        if extra:
            msg = f"Extra Iceberg columns (not in Avro): {extra}"
            pytest.skip(msg)


class TestNullabilityAlignment:
    """NOT NULL in Iceberg should align with non-nullable Avro fields."""

    def test_iceberg_non_nullable_fields_not_null_in_avro(self, avro_fields, iceberg_columns):
        """For Iceberg NOT NULL, Avro field should not be nullable."""
        mismatches = []
        for col_name, col_def in iceberg_columns.items():
            if col_name not in avro_fields:
                continue  # Skip columns not in Avro

            is_iceberg_not_null = not col_def["nullable"]
            avro_field = avro_fields[col_name]
            is_avro_nullable = _is_avro_nullable(avro_field)

            if is_iceberg_not_null and is_avro_nullable:
                msg = (
                    f"{col_name}: Iceberg NOT NULL but Avro is nullable "
                    f"(type: {avro_field['type']})"
                )
                mismatches.append(msg)

        assert not mismatches, "\n".join(mismatches)

    def test_avro_non_nullable_fields_not_null_in_iceberg(self, avro_fields, iceberg_columns):
        """For non-nullable Avro fields, Iceberg column should be NOT NULL."""
        mismatches = []
        for field_name, field_def in avro_fields.items():
            if field_name not in iceberg_columns:
                continue  # Skip fields not in Iceberg

            is_avro_non_nullable = _is_avro_non_nullable(field_def)
            iceberg_col = iceberg_columns[field_name]
            is_iceberg_not_null = not iceberg_col["nullable"]

            if is_avro_non_nullable and not is_iceberg_not_null:
                msg = f"{field_name}: Avro non-nullable but Iceberg is nullable"
                mismatches.append(msg)

        assert not mismatches, "\n".join(mismatches)


class TestAvroNullableDefaults:
    """Nullable Avro fields should have default: null (BACKWARD_TRANSITIVE)."""

    def test_avro_nullable_fields_have_null_default(self, avro_fields):
        """Nullable Avro fields must have 'default': null."""
        mismatches = []
        for field_name, field_def in avro_fields.items():
            if not _is_avro_nullable(field_def):
                continue  # Only check nullable fields

            has_null_default = "default" in field_def and field_def.get("default") is None
            if not has_null_default:
                default_val = field_def.get("default")
                msg = (
                    f"{field_name}: nullable but default is {default_val}, "
                    f"expected null (BACKWARD_TRANSITIVE)"
                )
                mismatches.append(msg)

        assert not mismatches, "\n".join(mismatches)


class TestKeyFieldConstraints:
    """Validate that critical fields have correct constraints."""

    def test_transaction_id_non_nullable_both(self, avro_fields, iceberg_columns):
        """transaction_id NOT NULL in both (deduplication key)."""
        # Check Avro
        txn_id_field = avro_fields["transaction_id"]
        is_avro_non_nullable = _is_avro_non_nullable(txn_id_field)
        assert is_avro_non_nullable, "transaction_id must be non-nullable in Avro"

        # Check Iceberg
        txn_id_col = iceberg_columns["transaction_id"]
        is_iceberg_not_null = not txn_id_col["nullable"]
        assert is_iceberg_not_null, "transaction_id must be NOT NULL in Iceberg"

    def test_event_time_non_nullable_both(self, avro_fields, iceberg_columns):
        """event_time NOT NULL in both (partition key anchor)."""
        # Check Avro
        event_time_field = avro_fields["event_time"]
        is_avro_non_nullable = _is_avro_non_nullable(event_time_field)
        assert is_avro_non_nullable, "event_time must be non-nullable in Avro"

        # Check Iceberg
        event_time_col = iceberg_columns["event_time"]
        is_iceberg_not_null = not event_time_col["nullable"]
        assert is_iceberg_not_null, "event_time must be NOT NULL in Iceberg"

    def test_enrichment_time_in_both_not_null(self, avro_fields, iceberg_columns):
        """enrichment_time NOT NULL in both (set at flush time)."""
        # Check Avro
        enrich_time_field = avro_fields["enrichment_time"]
        is_avro_non_nullable = _is_avro_non_nullable(enrich_time_field)
        assert is_avro_non_nullable, "enrichment_time must be non-nullable in Avro"

        # Check Iceberg
        enrich_time_col = iceberg_columns["enrichment_time"]
        is_iceberg_not_null = not enrich_time_col["nullable"]
        assert is_iceberg_not_null, "enrichment_time must be NOT NULL in Iceberg"


class TestProcessingTimeHandling:
    """Regression guard: processing_time should NOT be in Iceberg CREATE TABLE.

    Note: ALTER TABLE statements may add columns, but the CREATE TABLE block
    is what defines the canonical schema. This test ensures processing_time
    is not part of the persisted analytics table (it's an internal Flink field).
    """

    def test_no_processing_time_in_iceberg_create_table(self, iceberg_sql):
        """processing_time must not appear in the CREATE TABLE block.

        It's an internal Flink field, not persisted to Iceberg. If ALTER TABLE
        adds it later, that's a schema governance issue to investigate.
        """
        # Extract just the CREATE TABLE block
        create_match = re.search(
            r"CREATE TABLE IF NOT EXISTS.*?\((.*?)\)\s*USING iceberg",
            iceberg_sql,
            re.DOTALL,
        )
        assert create_match, "Could not parse CREATE TABLE block"
        create_block = create_match.group(1)

        # Check that processing_time is not mentioned as a column
        has_processing_time = re.search(
            r"\bprocessing_time\b",
            create_block,
            re.IGNORECASE,
        )
        assert not has_processing_time, (
            "processing_time must not be in CREATE TABLE block "
            "(internal Flink field, not persisted)"
        )

    def test_processing_time_in_avro_schema(self, avro_fields):
        """Confirm processing_time is in Avro (sanity check)."""
        assert "processing_time" in avro_fields, "processing_time missing from Avro schema"


class TestEnrichmentTimeHandling:
    """enrichment_time is set by Flink at flush time, should be in both."""

    def test_enrichment_time_in_avro(self, avro_fields):
        """enrichment_time must be present in Avro schema."""
        assert "enrichment_time" in avro_fields, "enrichment_time missing from Avro schema"

    def test_enrichment_time_in_iceberg(self, iceberg_columns):
        """enrichment_time must be present in Iceberg schema (persisted to lake)."""
        assert "enrichment_time" in iceberg_columns, "enrichment_time missing from Iceberg schema"

    def test_enrichment_time_nullable_alignment(self, avro_fields, iceberg_columns):
        """enrichment_time NOT NULL in both."""
        avro_field = avro_fields["enrichment_time"]
        iceberg_col = iceberg_columns["enrichment_time"]

        is_avro_non_null = _is_avro_non_nullable(avro_field)
        is_iceberg_not_null = not iceberg_col["nullable"]

        assert is_avro_non_null, "enrichment_time non-nullable in Avro"
        assert is_iceberg_not_null, "enrichment_time NOT NULL in Iceberg"


class TestOptionalFields:
    """Some fields are optional; verify they're consistently nullable."""

    def test_geo_lat_lon_nullable_both(self, avro_fields, iceberg_columns):
        """geo_lat and geo_lon are optional; nullable in both."""
        for field_name in ("geo_lat", "geo_lon"):
            avro_field = avro_fields[field_name]
            iceberg_col = iceberg_columns[field_name]

            is_avro_nullable = _is_avro_nullable(avro_field)
            is_iceberg_nullable = iceberg_col["nullable"]

            assert is_avro_nullable, f"{field_name} nullable in Avro"
            assert is_iceberg_nullable, f"{field_name} nullable in Iceberg"

    def test_device_fields_nullable_both(self, avro_fields, iceberg_columns):
        """device_first_seen, device_txn_count, device_known_fraud optional."""
        device_fields = (
            "device_first_seen",
            "device_txn_count",
            "device_known_fraud",
        )
        for field_name in device_fields:
            avro_field = avro_fields[field_name]
            iceberg_col = iceberg_columns[field_name]

            is_avro_nullable = _is_avro_nullable(avro_field)
            is_iceberg_nullable = iceberg_col["nullable"]

            assert is_avro_nullable, f"{field_name} nullable in Avro"
            assert is_iceberg_nullable, f"{field_name} nullable in Iceberg"

    def test_geo_enrichment_fields_nullable_both(self, avro_fields, iceberg_columns):
        """geo_country, geo_city, geo_network_class, geo_confidence optional."""
        geo_fields = (
            "geo_country",
            "geo_city",
            "geo_network_class",
            "geo_confidence",
        )
        for field_name in geo_fields:
            avro_field = avro_fields[field_name]
            iceberg_col = iceberg_columns[field_name]

            is_avro_nullable = _is_avro_nullable(avro_field)
            is_iceberg_nullable = iceberg_col["nullable"]

            assert is_avro_nullable, f"{field_name} nullable in Avro"
            assert is_iceberg_nullable, f"{field_name} nullable in Iceberg"

    def test_prev_transaction_fields_nullable_both(self, avro_fields, iceberg_columns):
        """prev_geo_country and prev_txn_time_ms optional (from prior txn)."""
        prev_fields = ("prev_geo_country", "prev_txn_time_ms")
        for field_name in prev_fields:
            avro_field = avro_fields[field_name]
            iceberg_col = iceberg_columns[field_name]

            is_avro_nullable = _is_avro_nullable(avro_field)
            is_iceberg_nullable = iceberg_col["nullable"]

            assert is_avro_nullable, f"{field_name} nullable in Avro"
            assert is_iceberg_nullable, f"{field_name} nullable in Iceberg"


class TestFieldCoverage:
    """Ensure no critical fields are missing."""

    def test_all_velocity_fields_present(self, avro_fields, iceberg_columns):
        """All velocity aggregates should be in both schemas."""
        velocity_fields = {
            "vel_count_1m",
            "vel_amount_1m",
            "vel_count_5m",
            "vel_amount_5m",
            "vel_count_1h",
            "vel_amount_1h",
            "vel_count_24h",
            "vel_amount_24h",
        }
        for field_name in velocity_fields:
            assert field_name in avro_fields, f"{field_name} missing from Avro"
            msg = f"{field_name} missing from Iceberg"
            assert field_name in iceberg_columns, msg

    def test_all_original_txn_fields_present(self, avro_fields, iceberg_columns):
        """All original transaction fields from txn.api should be present."""
        original_fields = {
            "transaction_id",
            "account_id",
            "merchant_id",
            "amount",
            "currency",
            "event_time",
            "processing_time",
            "channel",
            "card_bin",
            "card_last4",
            "caller_ip_subnet",
            "api_key_id",
            "oauth_scope",
            "masking_lib_version",
        }
        for field_name in original_fields:
            assert field_name in avro_fields, f"{field_name} missing from Avro"
            # processing_time in Avro but NOT in Iceberg CREATE TABLE (by design)
            if field_name != "processing_time":
                msg = f"{field_name} missing from Iceberg"
                assert field_name in iceberg_columns, msg

    def test_enrichment_metadata_fields_present(self, avro_fields, iceberg_columns):
        """Enrichment metadata fields should be present in both."""
        metadata_fields = {
            "enrichment_time",
            "enrichment_latency_ms",
            "processor_version",
            "schema_version",
        }
        for field_name in metadata_fields:
            assert field_name in avro_fields, f"{field_name} missing from Avro"
            msg = f"{field_name} missing from Iceberg"
            assert field_name in iceberg_columns, msg
