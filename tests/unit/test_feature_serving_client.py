"""Unit tests for FeatureServingClient — all paths exercised with mocked Feast."""

from __future__ import annotations

import concurrent.futures
import threading
from unittest.mock import MagicMock, patch

import pytest

from pipelines.scoring.clients.feature_serving import FeatureServingClient
from pipelines.scoring.types import FeatureVector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POPULATED_RAW = {
    "account_id": ["acct-001"],
    "vel_count_1m": [5],
    "vel_amount_1m": [120.50],
    "vel_count_5m": [8],
    "vel_amount_5m": [340.00],
    "vel_count_1h": [20],
    "vel_amount_1h": [800.00],
    "vel_count_24h": [42],
    "vel_amount_24h": [1500.00],
    "geo_country": ["US"],
    "geo_city": ["New York"],
    "geo_network_class": ["residential"],
    "geo_confidence": [0.95],
    "device_first_seen": [1700000000000],
    "device_txn_count": [99],
    "device_known_fraud": [False],
    "prev_geo_country": ["US"],
    "prev_txn_time_ms": [1700000060000],
}

_ALL_NONE_RAW = {k: [None] for k in _POPULATED_RAW}


@pytest.fixture
def client():
    c = FeatureServingClient(
        feature_store_repo_path="storage/feature_store",
        timeout_seconds=0.003,
    )
    mock_store = MagicMock()
    mock_store.get_online_features.return_value.to_dict.return_value = _POPULATED_RAW
    c._store = mock_store
    c._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    yield c
    c._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# US1 — Happy path (T010)
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_populated_feature_vector(self, client):
        fv = client.get_features("acct-001", "txn-001", 1700000000000)

        assert isinstance(fv, FeatureVector)
        assert fv.account_id == "acct-001"
        assert fv.vel_count_1m == 5
        assert fv.vel_amount_1m == pytest.approx(120.50)
        assert fv.geo_country == "US"
        assert fv.geo_confidence == pytest.approx(0.95)
        assert fv.device_known_fraud is False
        assert fv.prev_geo_country == "US"

    def test_records_retrieval_histogram(self, client):
        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_retrieval_seconds"
        ) as mock_hist:
            client.get_features("acct-001", "txn-001", 1700000000000)
            mock_hist.observe.assert_called_once()

    def test_no_fallback_counter_on_success(self, client):
        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_fallback_total"
        ) as mock_ctr:
            client.get_features("acct-001", "txn-001", 1700000000000)
            mock_ctr.labels.assert_not_called()


# ---------------------------------------------------------------------------
# US2 — Timeout path (T014)
# ---------------------------------------------------------------------------


class TestTimeoutFallback:
    def test_returns_zero_vector_on_timeout(self, client):
        stop = threading.Event()
        client._store.get_online_features.side_effect = lambda **_: stop.wait(timeout=5)
        client._timeout_seconds = 0.01

        try:
            fv = client.get_features("acct-timeout", "txn-002", 1700000000001)
            assert fv.vel_count_1m == 0
            assert fv.geo_country == ""
            assert fv.account_id == "acct-timeout"
        finally:
            stop.set()

    def test_fallback_counter_incremented_with_timeout_reason(self, client):
        stop = threading.Event()
        client._store.get_online_features.side_effect = lambda **_: stop.wait(timeout=5)
        client._timeout_seconds = 0.01

        try:
            with patch(
                "pipelines.scoring.clients.feature_serving.feature_store_fallback_total"
            ) as mock_ctr:
                client.get_features("acct-timeout", "txn-002", 1700000000001)
                mock_ctr.labels.assert_called_once_with(reason="timeout")
                mock_ctr.labels.return_value.inc.assert_called_once()
        finally:
            stop.set()

    def test_no_exception_escapes_on_timeout(self, client):
        stop = threading.Event()
        client._store.get_online_features.side_effect = lambda **_: stop.wait(timeout=5)
        client._timeout_seconds = 0.01

        try:
            result = client.get_features("acct-timeout", "txn-002", 1700000000001)
            assert result is not None
        finally:
            stop.set()


# ---------------------------------------------------------------------------
# US2 — Unavailability path (T015)
# ---------------------------------------------------------------------------


class TestUnavailableFallback:
    def test_returns_zero_vector_on_connection_error(self, client):
        client._store.get_online_features.side_effect = ConnectionError("store down")

        fv = client.get_features("acct-down", "txn-003", 1700000000002)

        assert fv.vel_count_1m == 0
        assert fv.account_id == "acct-down"

    def test_fallback_counter_incremented_with_unavailable_reason(self, client):
        client._store.get_online_features.side_effect = ConnectionError("store down")

        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_fallback_total"
        ) as mock_ctr:
            client.get_features("acct-down", "txn-003", 1700000000002)
            mock_ctr.labels.assert_called_once_with(reason="unavailable")
            mock_ctr.labels.return_value.inc.assert_called_once()

    def test_no_exception_escapes_on_unavailability(self, client):
        client._store.get_online_features.side_effect = RuntimeError("unexpected")

        result = client.get_features("acct-err", "txn-004", 1700000000003)
        assert result is not None


# ---------------------------------------------------------------------------
# US3 — Cache miss path (T018)
# ---------------------------------------------------------------------------


class TestCacheMiss:
    def test_returns_zero_vector_on_all_none(self, client):
        client._store.get_online_features.return_value.to_dict.return_value = _ALL_NONE_RAW

        fv = client.get_features("acct-new", "txn-005", 1700000000004)

        assert fv.vel_count_1m == 0
        assert fv.account_id == "acct-new"

    def test_miss_counter_increments_on_all_none(self, client):
        client._store.get_online_features.return_value.to_dict.return_value = _ALL_NONE_RAW

        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_miss_total"
        ) as mock_ctr:
            client.get_features("acct-new", "txn-005", 1700000000004)
            mock_ctr.inc.assert_called_once()

    def test_fallback_counter_not_incremented_on_miss(self, client):
        client._store.get_online_features.return_value.to_dict.return_value = _ALL_NONE_RAW

        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_fallback_total"
        ) as mock_ctr:
            client.get_features("acct-new", "txn-005", 1700000000004)
            mock_ctr.labels.assert_not_called()

    def test_partial_none_treated_as_miss(self, client):
        partial = dict(_POPULATED_RAW)
        partial["vel_count_1m"] = [None]
        client._store.get_online_features.return_value.to_dict.return_value = partial

        with patch(
            "pipelines.scoring.clients.feature_serving.feature_store_miss_total"
        ) as mock_ctr:
            fv = client.get_features("acct-partial", "txn-006", 1700000000005)
            mock_ctr.inc.assert_called_once()
        assert fv.vel_count_1m == 0
