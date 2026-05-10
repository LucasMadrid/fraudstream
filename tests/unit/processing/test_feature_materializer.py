"""Unit tests for FeatureMaterializer.

Store is injected as a mock so tests never touch the real Feast repo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipelines.processing.feature_materializer import FeatureMaterializer


@pytest.fixture()
def store():
    return MagicMock()


@pytest.fixture()
def materializer(store):
    return FeatureMaterializer(store)


@pytest.fixture()
def sample_records():
    return [
        {
            "account_id": "acc-1",
            "transaction_id": "txn-1",
            "event_time": 1_700_000_000_000,
            "vel_count_1m": 2,
            "vel_amount_1m": "10.50",
            "vel_count_5m": 3,
            "vel_amount_5m": "20.00",
            "vel_count_1h": 5,
            "vel_amount_1h": "50.00",
            "vel_count_24h": 10,
            "vel_amount_24h": "100.00",
            "geo_country": "US",
            "geo_city": "New York",
            "geo_network_class": "residential",
            "geo_confidence": 0.95,
            "geo_lat": 40.7128,
            "geo_lon": -74.0060,
            "device_first_seen": 1_699_000_000_000,
            "device_txn_count": 5,
            "device_known_fraud": False,
            "prev_geo_country": "US",
            "prev_txn_time_ms": 1_699_900_000_000,
        },
    ]


class TestMaterializeEmpty:
    def test_no_op_on_empty_records(self, materializer, store):
        materializer.materialize([])
        store.push.assert_not_called()


class TestMaterializeCallsAllGroups:
    def test_calls_velocity_geo_device(self, materializer, store, sample_records):
        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        push_sources = [c.args[0] for c in store.push.call_args_list]
        assert "velocity_push_source" in push_sources
        assert "geo_push_source" in push_sources
        assert "device_push_source" in push_sources

    def test_push_called_three_times(self, materializer, store, sample_records):
        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        assert store.push.call_count == 3


class TestVelocityPush:
    def test_velocity_dataframe_columns(self, materializer, store, sample_records):
        captured = {}

        def capture_push(source, df, **kwargs):
            captured[source] = df

        store.push.side_effect = capture_push

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        df = captured["velocity_push_source"]
        assert "account_id" in df.columns
        assert "transaction_id" in df.columns
        assert "vel_count_1m" in df.columns
        assert "vel_amount_24h" in df.columns
        assert len(df) == 1

    def test_velocity_values_correct(self, materializer, store, sample_records):
        captured = {}

        def capture_push(source, df, **kwargs):
            captured[source] = df

        store.push.side_effect = capture_push

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        row = captured["velocity_push_source"].iloc[0]
        assert row["vel_count_1m"] == 2
        assert row["vel_count_24h"] == 10


class TestGeoPush:
    def test_geo_dataframe_columns(self, materializer, store, sample_records):
        captured = {}

        def capture_push(source, df, **kwargs):
            captured[source] = df

        store.push.side_effect = capture_push

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        df = captured["geo_push_source"]
        for col in ("account_id", "geo_country", "geo_city", "geo_lat", "geo_lon"):
            assert col in df.columns

    def test_none_geo_falls_back_to_empty_string(self, materializer, store):
        records = [
            {
                "account_id": "acc-2",
                "transaction_id": "txn-2",
                "event_time": 0,
                "vel_count_1m": 0,
                "vel_amount_1m": 0,
                "vel_count_5m": 0,
                "vel_amount_5m": 0,
                "vel_count_1h": 0,
                "vel_amount_1h": 0,
                "vel_count_24h": 0,
                "vel_amount_24h": 0,
                "geo_country": None,
                "geo_city": None,
                "geo_network_class": None,
                "geo_confidence": None,
                "geo_lat": None,
                "geo_lon": None,
                "device_first_seen": None,
                "device_txn_count": 0,
                "device_known_fraud": False,
                "prev_geo_country": None,
                "prev_txn_time_ms": None,
            }
        ]
        captured = {}

        def capture_push(source, df, **kwargs):
            captured[source] = df

        store.push.side_effect = capture_push

        with patch("feast.data_source.PushMode"):
            materializer.materialize(records)

        assert captured["geo_push_source"].iloc[0]["geo_country"] == ""


class TestDevicePush:
    def test_device_dataframe_columns(self, materializer, store, sample_records):
        captured = {}

        def capture_push(source, df, **kwargs):
            captured[source] = df

        store.push.side_effect = capture_push

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        df = captured["device_push_source"]
        for col in ("account_id", "device_known_fraud", "device_txn_count"):
            assert col in df.columns


class TestPerGroupIsolation:
    def test_velocity_failure_does_not_block_geo_and_device(
        self, materializer, store, sample_records
    ):
        call_log: list[str] = []

        def side_effect(source, df, **kwargs):
            if source == "velocity_push_source":
                raise RuntimeError("network error")
            call_log.append(source)

        store.push.side_effect = side_effect

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        assert "geo_push_source" in call_log
        assert "device_push_source" in call_log

    def test_geo_failure_does_not_block_device(self, materializer, store, sample_records):
        call_log: list[str] = []

        def side_effect(source, df, **kwargs):
            if source == "geo_push_source":
                raise RuntimeError("geo down")
            call_log.append(source)

        store.push.side_effect = side_effect

        with patch("feast.data_source.PushMode"):
            materializer.materialize(sample_records)

        assert "device_push_source" in call_log


class TestStalenessGauge:
    def test_updates_last_push_ms(self, materializer, store, sample_records):
        before = materializer._last_push_ms
        with patch("feast.data_source.PushMode"):
            with patch(
                "pipelines.processing.feature_materializer.FeatureMaterializer._update_staleness_gauge"
            ) as mock_gauge:
                materializer.materialize(sample_records)
                mock_gauge.assert_called_once()
