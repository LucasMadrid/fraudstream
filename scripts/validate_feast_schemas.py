"""Validate Avro schema fields exist in corresponding Feast feature views."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


FEATURE_FAMILIES = {
    "velocity": [
        "vel_count_1m",
        "vel_amount_1m",
        "vel_count_5m",
        "vel_amount_5m",
        "vel_count_1h",
        "vel_amount_1h",
        "vel_count_24h",
        "vel_amount_24h",
    ],
    "geo": [
        "geo_country",
        "geo_city",
        "geo_network_class",
        "geo_confidence",
    ],
    "device": [
        "device_first_seen",
        "device_txn_count",
        "device_known_fraud",
        "prev_geo_country",
        "prev_txn_time_ms",
    ],
}


def load_avro_schema(avsc_path: Path) -> dict:
    """Load and parse Avro schema.

    Args:
        avsc_path: Path to .avsc file.

    Returns:
        Parsed Avro schema dict.

    Raises:
        FileNotFoundError: If schema file does not exist.
    """
    if not avsc_path.exists():
        raise FileNotFoundError(f"Schema file not found: {avsc_path}")

    with open(avsc_path) as f:
        return json.load(f)


def extract_field_names(schema: dict) -> set[str]:
    """Extract all field names from Avro schema.

    Args:
        schema: Parsed Avro schema dict.

    Returns:
        Set of field names.
    """
    return {field["name"] for field in schema.get("fields", [])}


def check_feast_view(feast_file: Path, expected_fields: list[str]) -> list[str]:
    """Check if expected fields exist in Feast view file.

    Args:
        feast_file: Path to Feast feature view .py file.
        expected_fields: List of field names to check.

    Returns:
        List of missing field names.
    """
    if not feast_file.exists():
        return expected_fields

    with open(feast_file) as f:
        content = f.read()

    missing = []
    for field in expected_fields:
        if not re.search(r"\b" + re.escape(field) + r"\b", content):
            missing.append(field)

    return missing


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate Feast schema alignment with Avro"
    )
    parser.add_argument(
        "--avsc", required=True, help="Path to .avsc schema file"
    )
    parser.add_argument(
        "--feast-repo",
        required=True,
        help="Path to Feast feature store repository",
    )
    args = parser.parse_args()

    try:
        avsc_path = Path(args.avsc)
        feast_repo = Path(args.feast_repo)

        load_avro_schema(avsc_path)

        all_missing = {}
        for family, expected_fields in FEATURE_FAMILIES.items():
            feast_file = feast_repo / "features" / f"{family}.py"
            missing = check_feast_view(feast_file, expected_fields)

            if missing:
                all_missing[family] = missing

        if all_missing:
            print("Schema validation FAILED:", file=sys.stderr)
            for family, missing_fields in all_missing.items():
                feast_file = feast_repo / "features" / f"{family}.py"
                print(
                    f"  {family}.py missing fields: {', '.join(missing_fields)}",
                    file=sys.stderr,
                )
                if not feast_file.exists():
                    print(f"    (file does not exist: {feast_file})")
            return 1

        print("Schema validation passed: all Avro fields found in Feast views")
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
