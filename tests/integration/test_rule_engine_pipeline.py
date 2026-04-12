"""Integration tests for the fraud rule engine pipeline (feature 003).

These tests verify the end-to-end evaluation path:
  enriched transaction dict → RuleEvaluator.dispatch() → EvaluationResult

They do NOT require Docker, Kafka, or Flink — they exercise the scoring
layer directly using in-process rule evaluation.

Markers:
  (none) — run with the standard test suite
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

RULES_YAML = Path(__file__).parent.parent.parent / "rules" / "rules.yaml"


@pytest.fixture(scope="module")
def evaluator():
    """RuleEvaluator loaded from the real rules.yaml."""
    from pipelines.scoring.rules.evaluator import RuleEvaluator
    from pipelines.scoring.rules.loader import RuleLoader

    rules = RuleLoader.load(str(RULES_YAML))
    return RuleEvaluator(rules)


def _clean_txn(**overrides) -> dict:
    """Return a transaction that triggers no rules by default.

    Field names match the enriched-txn schema as read by the family evaluators.
    """
    base = {
        "transaction_id": "txn-int-001",
        "account_id": "acc-int-001",
        "txn_amount": Decimal("50.00"),
        "vel_count_1m": 1,
        "vel_count_5m": 2,
        "vel_amount_5m": Decimal("100.00"),
        "vel_amount_24h": Decimal("500.00"),
        "device_is_new": False,
        "device_fingerprint": "fp-known",
        "device_known_fraud": None,
        "device_count_24h": 1,
        "geo_country": "US",
        "geo_network_class": "residential",
        "prev_geo_country": None,
        "prev_txn_time_ms": None,
        "txn_time_ms": 1_700_000_000_000,
    }
    base.update(overrides)
    return base


class TestCleanTransaction:
    def test_clean_txn_is_not_suspicious(self, evaluator):
        result = evaluator.dispatch(_clean_txn())
        assert result.determination == "clean"
        assert result.matched_rules == []
        assert result.highest_severity is None

    def test_clean_txn_has_evaluation_timestamp(self, evaluator):
        result = evaluator.dispatch(_clean_txn())
        assert result.evaluation_timestamp > 0


class TestVelocityRules:
    def test_vel_001_triggers_on_high_frequency(self, evaluator):
        txn = _clean_txn(vel_count_1m=6)
        result = evaluator.dispatch(txn)
        assert "VEL-001" in result.matched_rules
        assert result.determination == "suspicious"

    def test_vel_001_does_not_trigger_at_threshold(self, evaluator):
        txn = _clean_txn(vel_count_1m=5)
        result = evaluator.dispatch(txn)
        assert "VEL-001" not in result.matched_rules

    def test_vel_002_triggers_on_high_amount_5m(self, evaluator):
        # VEL-002: vel_amount_5m > 2000
        txn = _clean_txn(vel_amount_5m=Decimal("2001.00"))
        result = evaluator.dispatch(txn)
        assert "VEL-002" in result.matched_rules

    def test_vel_003_triggers_on_high_count_5m(self, evaluator):
        # VEL-003: vel_count_5m > 10
        txn = _clean_txn(vel_count_5m=11)
        result = evaluator.dispatch(txn)
        assert "VEL-003" in result.matched_rules


class TestNewDeviceRules:
    def test_nd_001_triggers_on_new_device_high_amount(self, evaluator):
        # ND-001: device_is_new=True, txn_amount > 500
        txn = _clean_txn(device_is_new=True, txn_amount=Decimal("501.00"))
        result = evaluator.dispatch(txn)
        assert "ND-001" in result.matched_rules

    def test_nd_002_triggers_on_rapid_new_device(self, evaluator):
        # ND-002: device_is_new=True, device_count_24h >= 3, vel_count_1m >= 5
        txn = _clean_txn(device_is_new=True, device_count_24h=4, vel_count_1m=6)
        result = evaluator.dispatch(txn)
        assert "ND-002" in result.matched_rules

    def test_nd_003_does_not_trigger_in_v1(self, evaluator):
        """device_known_fraud is always None in v1 — ND-003 must not fire."""
        txn = _clean_txn(device_is_new=True, device_known_fraud=None)
        result = evaluator.dispatch(txn)
        assert "ND-003" not in result.matched_rules

    def test_nd_004_triggers_on_hosting_network(self, evaluator):
        # ND-004: device_is_new=True, geo_network_class == "HOSTING"
        txn = _clean_txn(device_is_new=True, geo_network_class="HOSTING")
        result = evaluator.dispatch(txn)
        assert "ND-004" in result.matched_rules


class TestImpossibleTravelV1:
    def test_it_001_never_triggers_in_v1(self, evaluator):
        """prev_geo_country absent in v1 — IT-001 must return clean."""
        txn = _clean_txn(prev_geo_country=None, prev_txn_time_ms=None)
        result = evaluator.dispatch(txn)
        assert "IT-001" not in result.matched_rules

    def test_it_002_never_triggers_in_v1(self, evaluator):
        txn = _clean_txn(prev_geo_country=None)
        result = evaluator.dispatch(txn)
        assert "IT-002" not in result.matched_rules


class TestSeverityOrdering:
    def test_multiple_rules_highest_severity_reported(self, evaluator):
        """VEL-001 (high) + ND-001 (high) → highest_severity == 'high'."""
        txn = _clean_txn(
            vel_count_1m=6,
            device_is_new=True,
            txn_amount=Decimal("501.00"),
        )
        result = evaluator.dispatch(txn)
        assert result.highest_severity == "high"
        assert len(result.matched_rules) >= 2

    def test_high_severity_dominates_medium(self, evaluator):
        """VEL-001 (high) + VEL-003 (medium) → highest_severity == 'high'."""
        txn = _clean_txn(vel_count_1m=6, vel_count_5m=11)
        result = evaluator.dispatch(txn)
        assert result.highest_severity == "high"
        assert "VEL-001" in result.matched_rules
        assert "VEL-003" in result.matched_rules
