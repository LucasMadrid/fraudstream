"""Idempotent Iceberg DDL generator for Avro schema evolution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_avro_type(avro_type: dict | str | list) -> str:
    """Map Avro type to Iceberg SQL type.

    Args:
        avro_type: Avro type spec (can be string, dict, or union list).

    Returns:
        Iceberg SQL type string.
    """
    if isinstance(avro_type, str):
        if avro_type == "string":
            return "STRING"
        elif avro_type == "int":
            return "INT"
        elif avro_type == "long":
            return "BIGINT"
        elif avro_type == "float":
            return "FLOAT"
        elif avro_type == "boolean":
            return "BOOLEAN"
        elif avro_type == "bytes":
            return "BINARY"
        else:
            return "STRING"

    if isinstance(avro_type, list):
        non_null = [t for t in avro_type if t != "null"]
        if len(non_null) == 1:
            return parse_avro_type(non_null[0])
        return "STRING"

    if isinstance(avro_type, dict):
        avro_base = avro_type.get("type")
        logical_type = avro_type.get("logicalType")

        if logical_type == "timestamp-millis":
            return "TIMESTAMP(6)"
        elif logical_type == "decimal":
            precision = avro_type.get("precision", 18)
            scale = avro_type.get("scale", 4)
            return f"DECIMAL({precision},{scale})"
        elif avro_base == "enum":
            return "STRING"
        else:
            return parse_avro_type(avro_base)

    return "STRING"


def is_nullable(avro_type: dict | str | list) -> bool:
    """Check if Avro field is nullable.

    Args:
        avro_type: Avro type spec.

    Returns:
        True if field can be null.
    """
    if isinstance(avro_type, list):
        return "null" in avro_type
    return False


def load_avro_schema(avsc_path: Path) -> dict:
    """Load and parse Avro schema from JSON file.

    Args:
        avsc_path: Path to .avsc file.

    Returns:
        Parsed Avro schema dict.

    Raises:
        FileNotFoundError: If schema file does not exist.
        json.JSONDecodeError: If schema is invalid JSON.
    """
    if not avsc_path.exists():
        raise FileNotFoundError(f"Schema file not found: {avsc_path}")

    with open(avsc_path, encoding="utf-8") as f:
        return json.load(f)


def extract_fields(schema: dict) -> list[tuple[str, str, bool]]:
    """Extract field names, types, and nullability from schema.

    Args:
        schema: Parsed Avro schema dict.

    Returns:
        List of (field_name, sql_type, nullable) tuples.
    """
    fields = []
    for field in schema.get("fields", []):
        name = field["name"]
        avro_type = field["type"]
        sql_type = parse_avro_type(avro_type)
        nullable = is_nullable(avro_type)
        fields.append((name, sql_type, nullable))
    return fields


def load_existing_ddl(output_path: Path) -> tuple[set[str], bool]:
    """Load existing DDL file and extract defined field names.

    Args:
        output_path: Path to existing .sql file.

    Returns:
        Tuple of (set of existing field names, has_create_table).
    """
    if not output_path.exists() or output_path.stat().st_size == 0:
        return set(), False

    with open(output_path, encoding="utf-8") as f:
        content = f.read()

    has_create = "CREATE TABLE" in content.upper()
    fields = set()

    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("--") and not line.startswith("("):
            parts = line.split()
            if len(parts) >= 2:
                maybe_field = parts[0].lower()
                if not any(
                    maybe_field.startswith(kw)
                    for kw in [
                        "create",
                        "table",
                        "alter",
                        "add",
                        "column",
                        "using",
                        "partitioned",
                        "by",
                        "tblproperties",
                        ")",
                        "'",
                    ]
                ):
                    fields.add(maybe_field)

    return fields, has_create


def generate_create_table(fields: list[tuple[str, str, bool]]) -> str:
    """Generate CREATE TABLE DDL.

    Args:
        fields: List of (name, sql_type, nullable) tuples.

    Returns:
        CREATE TABLE statement.
    """
    column_lines = []
    for name, sql_type, nullable in fields:
        nullable_str = "" if nullable else " NOT NULL"
        column_lines.append(f"  {name} {sql_type}{nullable_str}")

    columns = ",\n".join(column_lines)
    return (
        "CREATE TABLE IF NOT EXISTS enriched_transactions (\n"
        f"{columns}\n"
        ")\n"
        "USING iceberg\n"
        "PARTITIONED BY (days(event_time));\n"
    )


def generate_alter_statements(
    new_fields: list[tuple[str, str, bool]], existing: set[str]
) -> str:
    """Generate ALTER TABLE ADD COLUMN statements.

    Args:
        new_fields: List of (name, sql_type, nullable) tuples from Avro.
        existing: Set of field names in existing DDL.

    Returns:
        ALTER TABLE statements (empty string if no new fields).
    """
    statements = []
    for name, sql_type, _ in new_fields:
        if name not in existing:
            # ALTER ADD COLUMN must always be nullable — Iceberg cannot backfill NOT NULL
            statements.append(
                f"ALTER TABLE enriched_transactions ADD COLUMN "
                f"{name} {sql_type};\n"
            )
    return "".join(statements)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate idempotent Iceberg DDL from Avro schema"
    )
    parser.add_argument(
        "--avsc", required=True, help="Path to .avsc schema file"
    )
    parser.add_argument(
        "--output", required=True, help="Path to output .sql file"
    )
    args = parser.parse_args()

    try:
        avsc_path = Path(args.avsc)
        output_path = Path(args.output)

        schema = load_avro_schema(avsc_path)
        fields = extract_fields(schema)

        existing_fields, has_create = load_existing_ddl(output_path)

        if not has_create:
            ddl = generate_create_table(fields)
        else:
            alter = generate_alter_statements(fields, existing_fields)
            if not alter:
                return 0
            ddl = alter

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not has_create:
            output_path.write_text(ddl)
        else:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(ddl)

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error parsing schema JSON: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
