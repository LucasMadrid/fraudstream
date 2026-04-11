"""Contract tests for fraud alert Avro schemas.

Validates fraud-alert-v1.avsc and fraud-alert-dlq-v1.avsc are parseable
by fastavro and that a FraudAlert can be round-tripped through Avro serialisation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "pipelines" / "scoring" / "schemas"
ALERT_SCHEMA_PATH = SCHEMAS_DIR / "fraud-alert-v1.avsc"
DLQ_SCHEMA_PATH = SCHEMAS_DIR / "fraud-alert-dlq-v1.avsc"


class TestSchemaFiles:
    def test_alert_schema_file_exists(self):
        assert ALERT_SCHEMA_PATH.exists(), f"Missing: {ALERT_SCHEMA_PATH}"

    def test_dlq_schema_file_exists(self):
        assert DLQ_SCHEMA_PATH.exists(), f"Missing: {DLQ_SCHEMA_PATH}"

    def test_alert_schema_is_valid_json(self):
        schema = json.loads(ALERT_SCHEMA_PATH.read_text())
        assert schema["type"] == "record"
        assert schema["name"] == "FraudAlert"

    def test_dlq_schema_is_valid_json(self):
        schema = json.loads(DLQ_SCHEMA_PATH.read_text())
        assert schema["type"] == "record"

    def test_alert_schema_has_required_fields(self):
        schema = json.loads(ALERT_SCHEMA_PATH.read_text())
        field_names = {f["name"] for f in schema["fields"]}
        assert "transaction_id" in field_names
        assert "account_id" in field_names
        assert "matched_rule_names" in field_names
        assert "severity" in field_names
        assert "evaluation_timestamp" in field_names

    def test_dlq_schema_has_error_fields(self):
        schema = json.loads(DLQ_SCHEMA_PATH.read_text())
        field_names = {f["name"] for f in schema["fields"]}
        assert "error_type" in field_names
        assert "error_message" in field_names
        assert "failed_at" in field_names


class TestAvroRoundTrip:
    def test_alert_round_trips_through_avro(self):
        pytest.importorskip("fastavro")
        from fastavro import parse_schema, reader, writer

        schema = parse_schema(json.loads(ALERT_SCHEMA_PATH.read_text()))
        record = {
            "transaction_id": "txn-rt-001",
            "account_id": "acc-rt-001",
            "matched_rule_names": ["VEL-001", "ND-003"],
            "severity": "high",
            "evaluation_timestamp": 1_700_000_000_000,
        }

        buf = io.BytesIO()
        writer(buf, schema, [record])
        buf.seek(0)
        decoded = list(reader(buf))
        assert len(decoded) == 1
        assert decoded[0]["transaction_id"] == "txn-rt-001"
        assert decoded[0]["severity"] == "high"
        assert decoded[0]["matched_rule_names"] == ["VEL-001", "ND-003"]

    def test_dlq_round_trips_through_avro(self):
        pytest.importorskip("fastavro")
        from fastavro import parse_schema, reader, writer

        schema = parse_schema(json.loads(DLQ_SCHEMA_PATH.read_text()))
        record = {
            "transaction_id": "txn-dlq-001",
            "account_id": "acc-dlq-001",
            "matched_rule_names": [],
            "severity": "low",
            "evaluation_timestamp": 1_700_000_000_000,
            "error_type": "SERIALISATION_ERROR",
            "error_message": "schema mismatch",
            "failed_at": 1_700_000_001_000,
        }

        buf = io.BytesIO()
        writer(buf, schema, [record])
        buf.seek(0)
        decoded = list(reader(buf))
        assert decoded[0]["error_type"] == "SERIALISATION_ERROR"
