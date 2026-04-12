"""TDD tests for impossible travel rule family.

v1 contract: prev_geo_country and prev_txn_time_ms are absent from
enriched-txn-v1.avsc. All IT rules must return False until feature 002
ships those fields.
"""

from __future__ import annotations

from pipelines.scoring.rules.families.impossible_travel import evaluate_impossible_travel

IT_001_CONDITIONS = {"window_ms": 3600000}
IT_002_CONDITIONS = {"count": 3}


class TestImpossibleTravelV1Contract:
    """v1 behaviour: always False when cross-feature fields are absent."""

    def test_always_false_when_prev_geo_missing(self):
        txn = {"geo_country": "US", "txn_time_ms": 1000000}
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_always_false_when_prev_geo_none(self):
        txn = {"prev_geo_country": None, "geo_country": "MX", "txn_time_ms": 1000000}
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_always_false_when_prev_time_missing(self):
        txn = {"prev_geo_country": "US", "geo_country": "MX", "txn_time_ms": 1000000}
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_always_false_when_prev_time_none(self):
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": None,
            "geo_country": "MX",
            "txn_time_ms": 1000000,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_it002_always_false_when_fields_missing(self):
        txn = {"geo_country": "MX", "txn_time_ms": 1000000}
        assert evaluate_impossible_travel(txn, IT_002_CONDITIONS) is False

    def test_returns_false_for_empty_txn(self):
        assert evaluate_impossible_travel({}, IT_001_CONDITIONS) is False

    def test_returns_false_for_empty_conditions(self):
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": 0,
            "geo_country": "MX",
            "txn_time_ms": 1000,
        }
        assert evaluate_impossible_travel(txn, {}) is False


class TestImpossibleTravelForwardCompat:
    """Forward-compatibility tests for when feature 002 ships the required fields."""

    def test_same_country_not_suspicious(self):
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": 0,
            "geo_country": "US",
            "txn_time_ms": 1000,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_different_countries_within_window_is_suspicious(self):
        window_ms = 3_600_000
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": 0,
            "geo_country": "MX",
            "txn_time_ms": window_ms - 1,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is True

    def test_different_countries_at_window_boundary_is_suspicious(self):
        window_ms = 3_600_000
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": 0,
            "geo_country": "MX",
            "txn_time_ms": window_ms,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is True

    def test_different_countries_outside_window_not_suspicious(self):
        window_ms = 3_600_000
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": 0,
            "geo_country": "MX",
            "txn_time_ms": window_ms + 1,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is False

    def test_time_delta_is_absolute(self):
        """Time ordering should not matter — uses abs()."""
        window_ms = 3_600_000
        txn = {
            "prev_geo_country": "US",
            "prev_txn_time_ms": window_ms - 1,
            "geo_country": "MX",
            "txn_time_ms": 0,
        }
        assert evaluate_impossible_travel(txn, IT_001_CONDITIONS) is True
