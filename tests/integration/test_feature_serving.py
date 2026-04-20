"""Integration test for _FeatureEnrichmentFunction with mocked FeatureServingClient."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from pipelines.scoring.job_extension import _FeatureEnrichmentFunction
from pipelines.scoring.types import ZERO_FEATURE_VECTOR, FeatureVector

_SAMPLE_TXN = {
    "transaction_id": "txn-int-001",
    "account_id": "acct-int-001",
    "event_time": 1700000000000,
    "amount": 99.99,
    "currency": "USD",
}

_POPULATED_FV = FeatureVector(
    account_id="acct-int-001",
    vel_count_1m=3,
    vel_amount_1m=200.0,
    vel_count_5m=7,
    vel_amount_5m=500.0,
    vel_count_1h=15,
    vel_amount_1h=900.0,
    vel_count_24h=40,
    vel_amount_24h=2000.0,
    geo_country="US",
    geo_city="Chicago",
    geo_network_class="business",
    geo_confidence=0.88,
    device_first_seen=1699000000000,
    device_txn_count=55,
    device_known_fraud=False,
    prev_geo_country="US",
    prev_txn_time_ms=1699999990000,
)


class TestFeatureEnrichmentFunction:
    def _make_fn(self, feature_vector: FeatureVector) -> _FeatureEnrichmentFunction:
        fn = _FeatureEnrichmentFunction(feature_store_repo_path="storage/feature_store")
        mock_client = MagicMock()
        mock_client.get_features.return_value = feature_vector
        fn._client = mock_client
        return fn

    def test_feature_fields_merged_into_output_dict(self):
        fn = self._make_fn(_POPULATED_FV)
        result = fn.map(dict(_SAMPLE_TXN))

        assert result["vel_count_1m"] == 3
        assert result["geo_country"] == "US"
        assert result["device_known_fraud"] is False
        assert result["prev_geo_country"] == "US"

    def test_original_txn_fields_preserved(self):
        fn = self._make_fn(_POPULATED_FV)
        result = fn.map(dict(_SAMPLE_TXN))

        assert result["transaction_id"] == "txn-int-001"
        assert result["account_id"] == "acct-int-001"
        assert result["amount"] == pytest.approx(99.99)

    def test_zero_vector_on_fallback(self):
        zero_fv = replace(ZERO_FEATURE_VECTOR, account_id="acct-int-001")
        fn = self._make_fn(zero_fv)
        result = fn.map(dict(_SAMPLE_TXN))

        assert result["vel_count_1m"] == 0
        assert result["geo_country"] == ""
        assert result["transaction_id"] == "txn-int-001"

    def test_get_features_called_with_correct_args(self):
        fn = self._make_fn(_POPULATED_FV)
        fn.map(dict(_SAMPLE_TXN))

        fn._client.get_features.assert_called_once_with(
            "acct-int-001", "txn-int-001", 1700000000000
        )

    def test_close_calls_client_close(self):
        fn = self._make_fn(_POPULATED_FV)
        fn.close()
        fn._client.close.assert_called_once()
