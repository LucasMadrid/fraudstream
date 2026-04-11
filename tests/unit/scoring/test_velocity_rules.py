"""TDD tests for velocity rule family.

Boundary rule: strictly greater than (>) — equal to threshold is NOT exceeded.
Decimal comparisons use Decimal(str(threshold)) to avoid float precision bugs.
"""

from __future__ import annotations

from decimal import Decimal

from pipelines.scoring.rules.families.velocity import evaluate_velocity

VEL_001 = {"field": "vel_count_1m", "count": 5}
VEL_002 = {"field": "vel_amount_5m", "amount": 2000.00}
VEL_003 = {"field": "vel_count_5m", "count": 10}
VEL_004 = {"field": "vel_amount_24h", "amount": 10000.00}


class TestVelocityCountRules:
    def test_vel_001_count_exceeded(self):
        txn = {"vel_count_1m": 6}
        assert evaluate_velocity(txn, VEL_001) is True

    def test_vel_001_count_not_exceeded_boundary(self):
        """Equal to threshold is NOT exceeded."""
        txn = {"vel_count_1m": 5}
        assert evaluate_velocity(txn, VEL_001) is False

    def test_vel_001_count_zero(self):
        txn = {"vel_count_1m": 0}
        assert evaluate_velocity(txn, VEL_001) is False

    def test_vel_003_burst_count_exceeded(self):
        txn = {"vel_count_5m": 11}
        assert evaluate_velocity(txn, VEL_003) is True

    def test_vel_003_burst_count_boundary(self):
        txn = {"vel_count_5m": 10}
        assert evaluate_velocity(txn, VEL_003) is False

    def test_vel_003_burst_count_below(self):
        txn = {"vel_count_5m": 9}
        assert evaluate_velocity(txn, VEL_003) is False


class TestVelocityAmountRules:
    def test_vel_002_amount_exceeded(self):
        txn = {"vel_amount_5m": Decimal("2000.01")}
        assert evaluate_velocity(txn, VEL_002) is True

    def test_vel_002_amount_not_exceeded_boundary(self):
        """Equal to threshold is NOT exceeded."""
        txn = {"vel_amount_5m": Decimal("2000.00")}
        assert evaluate_velocity(txn, VEL_002) is False

    def test_vel_002_amount_below(self):
        txn = {"vel_amount_5m": Decimal("1999.99")}
        assert evaluate_velocity(txn, VEL_002) is False

    def test_vel_004_daily_amount_exceeded(self):
        txn = {"vel_amount_24h": Decimal("10000.01")}
        assert evaluate_velocity(txn, VEL_004) is True

    def test_vel_004_daily_amount_boundary(self):
        txn = {"vel_amount_24h": Decimal("10000.00")}
        assert evaluate_velocity(txn, VEL_004) is False

    def test_vel_004_daily_amount_below(self):
        txn = {"vel_amount_24h": Decimal("9999.99")}
        assert evaluate_velocity(txn, VEL_004) is False


class TestVelocityDecimalPrecision:
    def test_cent_level_precision_triggers(self):
        """One cent above threshold must trigger."""
        txn = {"vel_amount_5m": Decimal("2000.01")}
        assert evaluate_velocity(txn, VEL_002) is True

    def test_cent_level_precision_does_not_trigger(self):
        txn = {"vel_amount_5m": Decimal("1999.99")}
        assert evaluate_velocity(txn, VEL_002) is False

    def test_large_amount_precision(self):
        txn = {"vel_amount_24h": Decimal("10000.001")}
        assert evaluate_velocity(txn, VEL_004) is True


class TestVelocityNullSafety:
    def test_missing_field_returns_false(self):
        assert evaluate_velocity({}, VEL_001) is False

    def test_field_value_none_returns_false(self):
        txn = {"vel_count_1m": None}
        assert evaluate_velocity(txn, VEL_001) is False

    def test_missing_amount_field_returns_false(self):
        assert evaluate_velocity({}, VEL_002) is False

    def test_conditions_missing_field_key_returns_false(self):
        txn = {"vel_count_1m": 99}
        assert evaluate_velocity(txn, {"count": 5}) is False  # no "field" key

    def test_empty_conditions_returns_false(self):
        txn = {"vel_count_1m": 99}
        assert evaluate_velocity(txn, {}) is False


class TestVelocityUnknownFields:
    def test_unknown_field_name_returns_false(self):
        txn = {"vel_count_1m": 99}
        assert evaluate_velocity(txn, {"field": "vel_unknown_field", "count": 5}) is False

    def test_unknown_condition_key_returns_false(self):
        txn = {"vel_count_1m": 99}
        assert evaluate_velocity(txn, {"field": "vel_count_1m", "threshold": 5}) is False
