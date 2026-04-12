"""TDD tests for new device rule family.

v1 notes:
- device_known_fraud is always None in enriched-txn-v1.avsc — ND-003 always False.
- Decimal comparisons use Decimal(str(threshold)).
- Boundary: strictly greater than (>) for amounts; >= for counts.
"""

from __future__ import annotations

from decimal import Decimal

from pipelines.scoring.rules.families.new_device import evaluate_new_device

ND_001 = {"amount": 500.00}
ND_002 = {"device_count": 3, "vel_count": 5}
ND_003: dict = {}
ND_004 = {"network_class": "HOSTING"}


class TestND001NewDeviceHighAmount:
    def test_triggered_new_device_high_amount(self):
        txn = {"device_is_new": True, "txn_amount": Decimal("500.01")}
        assert evaluate_new_device(txn, ND_001) is True

    def test_not_triggered_amount_at_boundary(self):
        """Equal to threshold is NOT exceeded."""
        txn = {"device_is_new": True, "txn_amount": Decimal("500.00")}
        assert evaluate_new_device(txn, ND_001) is False

    def test_not_triggered_not_new_device(self):
        txn = {"device_is_new": False, "txn_amount": Decimal("999.99")}
        assert evaluate_new_device(txn, ND_001) is False

    def test_not_triggered_amount_below(self):
        txn = {"device_is_new": True, "txn_amount": Decimal("499.99")}
        assert evaluate_new_device(txn, ND_001) is False

    def test_missing_device_is_new_returns_false(self):
        txn = {"txn_amount": Decimal("999.99")}
        assert evaluate_new_device(txn, ND_001) is False

    def test_missing_txn_amount_returns_false(self):
        txn = {"device_is_new": True}
        assert evaluate_new_device(txn, ND_001) is False


class TestND002NewDeviceRapidTxn:
    def test_triggered_all_conditions_met(self):
        txn = {"device_is_new": True, "device_count_24h": 3, "vel_count_1m": 5}
        assert evaluate_new_device(txn, ND_002) is True

    def test_triggered_above_all_thresholds(self):
        txn = {"device_is_new": True, "device_count_24h": 5, "vel_count_1m": 10}
        assert evaluate_new_device(txn, ND_002) is True

    def test_not_triggered_device_count_below(self):
        txn = {"device_is_new": True, "device_count_24h": 2, "vel_count_1m": 5}
        assert evaluate_new_device(txn, ND_002) is False

    def test_not_triggered_vel_count_below(self):
        txn = {"device_is_new": True, "device_count_24h": 3, "vel_count_1m": 4}
        assert evaluate_new_device(txn, ND_002) is False

    def test_not_triggered_not_new_device(self):
        txn = {"device_is_new": False, "device_count_24h": 5, "vel_count_1m": 10}
        assert evaluate_new_device(txn, ND_002) is False

    def test_missing_device_count_returns_false(self):
        txn = {"device_is_new": True, "vel_count_1m": 5}
        assert evaluate_new_device(txn, ND_002) is False

    def test_missing_vel_count_returns_false(self):
        txn = {"device_is_new": True, "device_count_24h": 3}
        assert evaluate_new_device(txn, ND_002) is False


class TestND003KnownFraudDevice:
    def test_triggered_known_fraud_true(self):
        txn = {"device_known_fraud": True}
        assert evaluate_new_device(txn, ND_003) is True

    def test_not_triggered_known_fraud_none(self):
        """v1 behaviour: device_known_fraud is always None."""
        txn = {"device_known_fraud": None}
        assert evaluate_new_device(txn, ND_003) is False

    def test_not_triggered_known_fraud_false(self):
        txn = {"device_known_fraud": False}
        assert evaluate_new_device(txn, ND_003) is False

    def test_not_triggered_field_absent(self):
        assert evaluate_new_device({}, ND_003) is False


class TestND004NewDeviceForeign:
    def test_triggered_hosting_network(self):
        txn = {"device_is_new": True, "geo_network_class": "HOSTING"}
        assert evaluate_new_device(txn, ND_004) is True

    def test_not_triggered_residential_network(self):
        txn = {"device_is_new": True, "geo_network_class": "RESIDENTIAL"}
        assert evaluate_new_device(txn, ND_004) is False

    def test_not_triggered_not_new_device(self):
        txn = {"device_is_new": False, "geo_network_class": "HOSTING"}
        assert evaluate_new_device(txn, ND_004) is False

    def test_not_triggered_missing_network_class(self):
        txn = {"device_is_new": True}
        assert evaluate_new_device(txn, ND_004) is False

    def test_case_sensitive_network_class(self):
        txn = {"device_is_new": True, "geo_network_class": "hosting"}
        assert evaluate_new_device(txn, ND_004) is False


class TestNullSafety:
    def test_empty_txn_returns_false(self):
        assert evaluate_new_device({}, ND_001) is False
        assert evaluate_new_device({}, ND_002) is False
        assert evaluate_new_device({}, ND_004) is False

    def test_device_is_new_none_returns_false(self):
        txn = {"device_is_new": None, "txn_amount": Decimal("999")}
        assert evaluate_new_device(txn, ND_001) is False
