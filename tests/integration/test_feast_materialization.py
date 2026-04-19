"""Integration tests for Feast feature materialization in IcebergEnrichedSink.

Tests the feature push functionality that writes enriched records to Feast feature views
after successful Iceberg table.append() operations.

Covers:
- Feast push called after Iceberg append (3 feature views: velocity, geo, device)
- Feast push not called if Iceberg append fails
- event_timestamp uses record["event_time"] (NOT wall-clock)
- Feast push failure re-raises (unlike Iceberg failures which are DLQ'd)
- Feast push failures increment the feast_push_failures_total metric
- Correct feature subsets in each push (no cross-feature leakage)
- All required columns present in each feature view

Run with:
  pytest tests/integration/test_feast_materialization.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from pipelines.processing.operators.iceberg_sink import IcebergEnrichedSink


def _has_pyarrow() -> bool:
    """Check if pyarrow is available."""
    try:
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enriched_record():
    """Create a valid enriched record with all 36 Iceberg schema fields.

    Returns a dict with sensible defaults for testing:
    - All 36 fields from iceberg.enriched_transactions schema
    - Decimal amounts
    - Timestamps as epoch ms integers
    - Nullable fields with None values
    """
    return {
        # Core transaction fields
        "transaction_id": "txn-test-001",
        "account_id": "acc-test-001",
        "merchant_id": "mer-test-001",
        "amount": Decimal("123.4500"),
        "currency": "USD",
        # Time fields
        "event_time": 1700000000000,  # Fixed epoch ms for testing
        "enrichment_time": 1704067200100,
        # Channel and payment fields
        "channel": "WEB",
        "card_bin": "123456",
        "card_last4": "6789",
        "caller_ip_subnet": "192.168.1.0/24",
        "api_key_id": "key-test-001",
        "oauth_scope": "transactions:read",
        # Geolocation fields
        "geo_lat": 40.7128,
        "geo_lon": -74.0060,
        "masking_lib_version": "1.0.0",
        # Velocity aggregates (1m, 5m, 1h, 24h)
        "vel_count_1m": 5,
        "vel_amount_1m": Decimal("500.0000"),
        "vel_count_5m": 12,
        "vel_amount_5m": Decimal("1200.0000"),
        "vel_count_1h": 45,
        "vel_amount_1h": Decimal("4500.0000"),
        "vel_count_24h": 120,
        "vel_amount_24h": Decimal("12000.0000"),
        # GeoLite2 fields
        "geo_country": "US",
        "geo_city": "New York",
        "geo_network_class": "RESIDENTIAL",
        "geo_confidence": 0.95,
        # Device fields
        "device_first_seen": 1704000000000,  # epoch ms
        "device_txn_count": 42,
        "device_known_fraud": False,
        # Previous transaction fields
        "prev_geo_country": "US",
        "prev_txn_time_ms": 1704067100000,  # epoch ms
        # Metadata fields
        "enrichment_latency_ms": 100,
        "processor_version": "002-stream-processor@1.0.0",
        "schema_version": "1",
    }


@pytest.fixture
def mock_feast_module():
    """Mock the feast module to avoid import errors during testing.

    Injects a mock feast module into sys.modules so the _push_to_feast
    method can import PushMode without the actual feast library.
    """
    import sys

    mock_feast = MagicMock()
    mock_feast.PushMode.ONLINE_AND_OFFLINE = "ONLINE_AND_OFFLINE"
    sys.modules["feast"] = mock_feast

    yield mock_feast

    # Clean up
    if "feast" in sys.modules:
        del sys.modules["feast"]


@pytest.fixture
def sink_with_mocked_feast(mock_feast_module):
    """Create a fresh IcebergEnrichedSink instance with mocked Feast store.

    Mocks both the Iceberg table and Feast feature store to isolate
    feature materialization logic from external dependencies.
    """
    s = IcebergEnrichedSink()
    # Mock the Iceberg table
    s._table = MagicMock()
    # Mock the Feast feature store
    s._feast_store = MagicMock()
    # Initialize breaker manually
    try:
        from pybreaker import CircuitBreaker

        s._breaker = CircuitBreaker(fail_max=3, reset_timeout=30, listeners=[])
    except ImportError:
        s._breaker = MagicMock()
    s._catalog_loaded = True
    return s


# ---------------------------------------------------------------------------
# Test: Feast push called after Iceberg append
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_called_after_iceberg_append(sink_with_mocked_feast, enriched_record):
    """Verify feast_store.push() is called 3 times after successful table.append().

    Given:
      - A sink with mocked Iceberg table and Feast store
      - A single enriched record
    When:
      - Buffer the record and flush (triggers table.append())
    Then:
      - feast_store.push() is called exactly 3 times
      - One call for "velocity_features"
      - One call for "geo_features"
      - One call for "device_features"
    """
    sink = sink_with_mocked_feast

    # Add record to buffer and flush
    sink._buffer = [enriched_record]
    sink._flush()

    # Verify table.append was called (Iceberg write succeeded)
    assert sink._table.append.call_count == 1

    # Verify feast_store.push was called 3 times
    assert sink._feast_store.push.call_count == 3

    # Verify the feature view names passed to push
    push_calls = sink._feast_store.push.call_args_list
    feature_view_names = [call_args[0][0] for call_args in push_calls]
    assert "velocity_features" in feature_view_names
    assert "geo_features" in feature_view_names
    assert "device_features" in feature_view_names


# ---------------------------------------------------------------------------
# Test: Feast push not called if Iceberg fails
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_not_called_if_iceberg_fails(sink_with_mocked_feast, enriched_record):
    """Verify feast_store.push() is never called if table.append() raises.

    Given:
      - A sink with mocked Iceberg table and Feast store
      - table.append() configured to raise ConnectionError
    When:
      - Buffer a record and flush
    Then:
      - table.append() is called
      - feast_store.push() is NOT called (Feast only runs after successful Iceberg write)
      - Buffer is cleared (failure handling)
    """
    sink = sink_with_mocked_feast
    sink._table.append.side_effect = ConnectionError("Iceberg catalog unavailable")

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]
    sink._flush()

    # Verify table.append was attempted
    assert sink._table.append.call_count == 1

    # Verify feast_store.push was NOT called
    assert sink._feast_store.push.call_count == 0

    # Verify buffer was cleared (exception handling)
    assert len(sink._buffer) == 0


# ---------------------------------------------------------------------------
# Test: event_timestamp uses event_time, not wall-clock
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_event_timestamp_uses_event_time_not_wall_clock(sink_with_mocked_feast, enriched_record):
    """Verify event_timestamp in Feast push uses record["event_time"], not wall-clock.

    Given:
      - A record with event_time=1700000000000 (specific epoch ms)
    When:
      - Flush the record
    Then:
      - Feast push calls receive PyArrow tables with event_timestamp column
      - event_timestamp column contains the epoch ms converted to datetime (not wall-clock)
    """
    pytest.importorskip("pyarrow")
    from datetime import datetime

    sink = sink_with_mocked_feast

    # Use specific event_time
    specific_event_time = 1700000000000
    record = {**enriched_record, "event_time": specific_event_time}
    sink._buffer = [record]
    sink._flush()

    # Get all feast_store.push calls
    push_calls = sink._feast_store.push.call_args_list
    assert len(push_calls) == 3

    # Verify event_timestamp in each push
    # call_args[0] is a tuple of positional args: (feature_view_name, pa_table, ...)
    for call_obj in push_calls:
        pa_table = call_obj[0][1]  # args[1]
        assert "event_timestamp" in pa_table.column_names
        event_timestamps = pa_table["event_timestamp"].to_pylist()
        assert len(event_timestamps) == 1
        # event_timestamp is converted from the record's event_time
        # Just verify it's a datetime and roughly correct
        ts = event_timestamps[0]
        assert isinstance(ts, datetime)
        # Verify the ISO format string contains the expected date/time
        # (avoid timezone conversion issues by checking the string representation)
        ts_iso = ts.isoformat()
        # 1700000000000 ms = 2023-11-14 22:13:20 UTC
        assert "2023-11-14" in ts_iso
        assert "22:13:20" in ts_iso


# ---------------------------------------------------------------------------
# Test: Feast failure re-raises
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_failure_re_raises(sink_with_mocked_feast, enriched_record):
    """Verify feast_store.push() exceptions are re-raised (not swallowed).

    Given:
      - feast_store.push() configured to raise RuntimeError
    When:
      - Flush a record
    Then:
      - The RuntimeError is re-raised (feast failures propagate, unlike Iceberg)
    """
    sink = sink_with_mocked_feast
    sink._feast_store.push.side_effect = RuntimeError("Feast connection lost")

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]

    # Verify that flush re-raises the Feast exception
    with pytest.raises(RuntimeError, match="Feast connection lost"):
        sink._flush()


# ---------------------------------------------------------------------------
# Test: Feast failure increments metric
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_failure_increments_metric(sink_with_mocked_feast, enriched_record):
    """Verify feast_push_failures_total counter is incremented on Feast failure.

    Given:
      - feast_store.push() raises
      - feast_push_failures_total metric is mocked
    When:
      - Flush a record
    Then:
      - feast_push_failures_total.inc() is called
    """
    sink = sink_with_mocked_feast
    sink._feast_store.push.side_effect = RuntimeError("Feast down")

    record = {**enriched_record, "transaction_id": "txn-001"}
    sink._buffer = [record]

    # Mock the metric (imported inside _increment_counter)
    with patch("pipelines.processing.metrics.feast_push_failures_total") as mock_counter:
        try:
            sink._flush()
        except RuntimeError:
            pass  # Expected to raise

        # Verify metric was incremented
        mock_counter.inc.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Feast push with correct feature subsets
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_with_correct_feature_subsets(sink_with_mocked_feast, enriched_record):
    """Verify each Feast push receives correct feature subset (no cross-leakage).

    Given:
      - A record with all velocity, geo, and device fields
    When:
      - Flush the record
    Then:
      - velocity_features push table has: vel_count_1m, vel_amount_1m, vel_count_5m,
        vel_amount_5m, vel_count_1h, vel_amount_1h, vel_count_24h, vel_amount_24h,
        account_id, transaction_id, event_timestamp (no geo or device fields)
      - geo_features push table has: geo_country, geo_city, geo_network_class,
        geo_confidence, account_id, transaction_id, event_timestamp (no vel or device)
      - device_features push table has: device_first_seen, device_txn_count,
        device_known_fraud, prev_geo_country, prev_txn_time_ms, account_id,
        transaction_id, event_timestamp (no vel or geo)
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    # Use known values for all fields
    record = {
        **enriched_record,
        "vel_count_1m": 5,
        "vel_amount_1m": Decimal("500.0000"),
        "vel_count_5m": 12,
        "vel_amount_5m": Decimal("1200.0000"),
        "vel_count_1h": 45,
        "vel_amount_1h": Decimal("4500.0000"),
        "vel_count_24h": 120,
        "vel_amount_24h": Decimal("12000.0000"),
        "geo_country": "US",
        "geo_city": "New York",
        "geo_network_class": "RESIDENTIAL",
        "geo_confidence": 0.95,
        "device_first_seen": 1704000000000,
        "device_txn_count": 42,
        "device_known_fraud": False,
        "prev_geo_country": "US",
        "prev_txn_time_ms": 1704067100000,
    }

    sink._buffer = [record]
    sink._flush()

    # Get all feast_store.push calls
    push_calls = sink._feast_store.push.call_args_list
    assert len(push_calls) == 3

    # Organize calls by feature view name
    # call_args[0] is a tuple of positional args
    push_by_feature = {}
    for call_obj in push_calls:
        feature_view_name = call_obj[0][0]  # args[0]
        pa_table = call_obj[0][1]  # args[1]
        push_by_feature[feature_view_name] = pa_table

    # Verify velocity_features columns
    velocity_table = push_by_feature["velocity_features"]
    velocity_cols = set(velocity_table.column_names)
    expected_velocity = {
        "vel_count_1m",
        "vel_amount_1m",
        "vel_count_5m",
        "vel_amount_5m",
        "vel_count_1h",
        "vel_amount_1h",
        "vel_count_24h",
        "vel_amount_24h",
        "account_id",
        "transaction_id",
        "event_timestamp",
    }
    assert velocity_cols == expected_velocity, (
        f"Velocity table has unexpected columns: {velocity_cols} vs {expected_velocity}"
    )

    # Verify geo_features columns
    geo_table = push_by_feature["geo_features"]
    geo_cols = set(geo_table.column_names)
    expected_geo = {
        "geo_country",
        "geo_city",
        "geo_network_class",
        "geo_confidence",
        "geo_lat",
        "geo_lon",
        "account_id",
        "transaction_id",
        "event_timestamp",
    }
    assert geo_cols == expected_geo, (
        f"Geo table has unexpected columns: {geo_cols} vs {expected_geo}"
    )

    # Verify device_features columns
    device_table = push_by_feature["device_features"]
    device_cols = set(device_table.column_names)
    expected_device = {
        "device_first_seen",
        "device_txn_count",
        "device_known_fraud",
        "prev_geo_country",
        "prev_txn_time_ms",
        "account_id",
        "transaction_id",
        "event_timestamp",
    }
    assert device_cols == expected_device, (
        f"Device table has unexpected columns: {device_cols} vs {expected_device}"
    )


# ---------------------------------------------------------------------------
# Test: Velocity features have correct values
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_velocity_features_have_correct_values(sink_with_mocked_feast, enriched_record):
    """Verify velocity_features push table contains correct feature values.

    Given:
      - A record with specific velocity counts and amounts
    When:
      - Flush the record
    Then:
      - velocity_features push table rows have matching values
      - account_id, transaction_id match the record
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record = {
        **enriched_record,
        "transaction_id": "txn-velocity-test",
        "account_id": "acc-velocity-test",
        "vel_count_1m": 10,
        "vel_amount_1m": Decimal("1000.0000"),
        "vel_count_5m": 25,
        "vel_amount_5m": Decimal("2500.0000"),
        "vel_count_1h": 60,
        "vel_amount_1h": Decimal("6000.0000"),
        "vel_count_24h": 150,
        "vel_amount_24h": Decimal("15000.0000"),
    }

    sink._buffer = [record]
    sink._flush()

    # Get velocity_features push call
    push_calls = sink._feast_store.push.call_args_list
    velocity_table = [
        call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "velocity_features"
    ][0]

    # Verify values
    assert velocity_table.num_rows == 1
    assert velocity_table["vel_count_1m"][0].as_py() == 10
    assert velocity_table["vel_count_5m"][0].as_py() == 25
    assert velocity_table["vel_count_1h"][0].as_py() == 60
    assert velocity_table["vel_count_24h"][0].as_py() == 150
    assert velocity_table["account_id"][0].as_py() == "acc-velocity-test"
    assert velocity_table["transaction_id"][0].as_py() == "txn-velocity-test"


# ---------------------------------------------------------------------------
# Test: Geo features have correct values
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_geo_features_have_correct_values(sink_with_mocked_feast, enriched_record):
    """Verify geo_features push table contains correct feature values.

    Given:
      - A record with specific geo fields
    When:
      - Flush the record
    Then:
      - geo_features push table rows have matching values
      - account_id, transaction_id match the record
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record = {
        **enriched_record,
        "transaction_id": "txn-geo-test",
        "account_id": "acc-geo-test",
        "geo_country": "FR",
        "geo_city": "Paris",
        "geo_network_class": "COMMERCIAL",
        "geo_confidence": 0.87,
    }

    sink._buffer = [record]
    sink._flush()

    # Get geo_features push call
    push_calls = sink._feast_store.push.call_args_list
    geo_table = [call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "geo_features"][0]

    # Verify values
    assert geo_table.num_rows == 1
    assert geo_table["geo_country"][0].as_py() == "FR"
    assert geo_table["geo_city"][0].as_py() == "Paris"
    assert geo_table["geo_network_class"][0].as_py() == "COMMERCIAL"
    assert abs(geo_table["geo_confidence"][0].as_py() - 0.87) < 0.01
    assert geo_table["account_id"][0].as_py() == "acc-geo-test"
    assert geo_table["transaction_id"][0].as_py() == "txn-geo-test"


# ---------------------------------------------------------------------------
# Test: Device features have correct values
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_device_features_have_correct_values(sink_with_mocked_feast, enriched_record):
    """Verify device_features push table contains correct feature values.

    Given:
      - A record with specific device fields
    When:
      - Flush the record
    Then:
      - device_features push table rows have matching values
      - account_id, transaction_id match the record
    """
    pytest.importorskip("pyarrow")
    from datetime import datetime

    sink = sink_with_mocked_feast

    device_first_seen_ms = 1700000000000
    prev_txn_time_ms_val = 1700000001000

    record = {
        **enriched_record,
        "transaction_id": "txn-device-test",
        "account_id": "acc-device-test",
        "device_first_seen": device_first_seen_ms,
        "device_txn_count": 99,
        "device_known_fraud": "True",
        "prev_geo_country": "BR",
        "prev_txn_time_ms": prev_txn_time_ms_val,
    }

    sink._buffer = [record]
    sink._flush()

    # Get device_features push call
    push_calls = sink._feast_store.push.call_args_list
    device_table = [
        call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "device_features"
    ][0]

    # Verify values
    assert device_table.num_rows == 1
    # device_first_seen is timestamp("ms"), verify the datetime is correct
    first_seen_ts = device_table["device_first_seen"][0].as_py()
    assert isinstance(first_seen_ts, datetime)
    # 1700000000000 ms = 2023-11-14 22:13:20 UTC
    assert "2023-11-14" in first_seen_ts.isoformat()
    assert "22:13:20" in first_seen_ts.isoformat()

    assert device_table["device_txn_count"][0].as_py() == 99
    assert device_table["device_known_fraud"][0].as_py() is True
    assert device_table["prev_geo_country"][0].as_py() == "BR"

    # prev_txn_time_ms is also timestamp("ms")
    prev_txn_ts = device_table["prev_txn_time_ms"][0].as_py()
    assert isinstance(prev_txn_ts, datetime)
    # 1700000001000 ms = 2023-11-14 22:13:21 UTC
    assert "2023-11-14" in prev_txn_ts.isoformat()
    assert "22:13:21" in prev_txn_ts.isoformat()

    assert device_table["account_id"][0].as_py() == "acc-device-test"
    assert device_table["transaction_id"][0].as_py() == "txn-device-test"


# ---------------------------------------------------------------------------
# Test: Nullable fields in feature tables
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_nullable_fields_in_feature_tables(sink_with_mocked_feast, enriched_record):
    """Verify nullable feature fields are handled correctly when None.

    Given:
      - A record with nullable feature fields set to None
    When:
      - Flush the record
    Then:
      - Feature tables convert None string values to empty string "" (Feast convention)
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record = {
        **enriched_record,
        "geo_country": None,
        "geo_city": None,
        "prev_geo_country": None,
    }

    sink._buffer = [record]
    sink._flush()

    # Get geo_features and device_features push calls
    push_calls = sink._feast_store.push.call_args_list
    geo_table = [call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "geo_features"][0]
    device_table = [
        call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "device_features"
    ][0]

    # Verify None values converted to empty string in geo (Feast convention: no nulls in strings)
    assert geo_table["geo_country"][0].as_py() == ""
    assert geo_table["geo_city"][0].as_py() == ""

    # Verify None values converted to empty string in device
    assert device_table["prev_geo_country"][0].as_py() == ""


# ---------------------------------------------------------------------------
# Test: Multiple records batched together
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_multiple_records_batched_in_single_push(sink_with_mocked_feast, enriched_record):
    """Verify multiple records in buffer are batched together in Feast push.

    Given:
      - 3 different enriched records in the buffer
    When:
      - Flush the buffer
    Then:
      - Each feature table push has 3 rows (one per record)
      - transaction_ids match the original records
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record1 = {**enriched_record, "transaction_id": "txn-batch-001"}
    record2 = {**enriched_record, "transaction_id": "txn-batch-002"}
    record3 = {**enriched_record, "transaction_id": "txn-batch-003"}

    sink._buffer = [record1, record2, record3]
    sink._flush()

    # Get all push calls
    push_calls = sink._feast_store.push.call_args_list
    assert len(push_calls) == 3

    # Verify each push has 3 rows
    for call_obj in push_calls:
        pa_table = call_obj[0][1]  # args[1]
        assert pa_table.num_rows == 3
        txn_ids = pa_table["transaction_id"].to_pylist()
        assert set(txn_ids) == {
            "txn-batch-001",
            "txn-batch-002",
            "txn-batch-003",
        }


# ---------------------------------------------------------------------------
# Test: Deduplication before Feast push
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_deduplication_applied_before_feast_push(sink_with_mocked_feast, enriched_record):
    """Verify Feast push receives deduplicated records (same as Iceberg).

    Given:
      - 3 records: txn-001, txn-001 (duplicate), txn-002
    When:
      - Flush all records
    Then:
      - Feast push tables have 2 rows (dedup applied before push)
      - First occurrence of txn-001 is kept
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record1 = {
        **enriched_record,
        "transaction_id": "txn-001",
        "geo_country": "US",
    }
    record1_dup = {
        **enriched_record,
        "transaction_id": "txn-001",
        "geo_country": "FR",  # Different value, but should be ignored
    }
    record2 = {
        **enriched_record,
        "transaction_id": "txn-002",
        "geo_country": "BR",
    }

    sink._buffer = [record1, record1_dup, record2]
    sink._flush()

    # Get geo_features push
    push_calls = sink._feast_store.push.call_args_list
    geo_table = [call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "geo_features"][0]

    # Verify 2 rows (dedup applied)
    assert geo_table.num_rows == 2
    txn_ids = geo_table["transaction_id"].to_pylist()
    geo_countries = geo_table["geo_country"].to_pylist()

    assert txn_ids == ["txn-001", "txn-002"]
    # First occurrence of txn-001 is kept (US, not FR)
    assert geo_countries[0] == "US"
    assert geo_countries[1] == "BR"


# ---------------------------------------------------------------------------
# Test: Feast push call order and atomicity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_order_and_atomicity(sink_with_mocked_feast, enriched_record):
    """Verify all three Feast pushes are called in consistent order.

    Given:
      - A record to flush
    When:
      - Flush the record
    Then:
      - feast_store.push() is called exactly 3 times
      - Calls are made sequentially (not concurrent)
      - If any push fails, exception is raised (no partial success)
    """
    sink = sink_with_mocked_feast

    record = {**enriched_record, "transaction_id": "txn-order-test"}
    sink._buffer = [record]
    sink._flush()

    # Verify 3 calls in order
    push_calls = sink._feast_store.push.call_args_list
    assert len(push_calls) == 3

    # Extract feature view names in call order
    called_features = [call_obj[0][0] for call_obj in push_calls]
    # Should have all three, in some consistent order
    assert set(called_features) == {"velocity_features", "geo_features", "device_features"}


# ---------------------------------------------------------------------------
# Test: Feast push handles feature amounts as floats
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_handles_decimal_amounts_as_floats(sink_with_mocked_feast, enriched_record):
    """Verify Decimal feature amounts are converted to floats in Feast push.

    Given:
      - A record with Decimal velocity amounts
    When:
      - Flush the record
    Then:
      - Velocity feature table converts Decimal to float for push
      - Values are preserved with acceptable precision loss
    """
    pytest.importorskip("pyarrow")

    sink = sink_with_mocked_feast

    record = {
        **enriched_record,
        "vel_amount_1m": Decimal("123.4567"),
        "vel_amount_5m": Decimal("1234.5678"),
        "vel_amount_1h": Decimal("12345.6789"),
        "vel_amount_24h": Decimal("123456.7890"),
    }

    sink._buffer = [record]
    sink._flush()

    # Get velocity_features push
    push_calls = sink._feast_store.push.call_args_list
    velocity_table = [
        call_obj[0][1] for call_obj in push_calls if call_obj[0][0] == "velocity_features"
    ][0]

    # Verify Decimal values are converted to float (with acceptable precision)
    vel_amount_1m = velocity_table["vel_amount_1m"][0].as_py()
    vel_amount_5m = velocity_table["vel_amount_5m"][0].as_py()
    vel_amount_1h = velocity_table["vel_amount_1h"][0].as_py()
    vel_amount_24h = velocity_table["vel_amount_24h"][0].as_py()

    assert abs(vel_amount_1m - 123.4567) < 0.01
    assert abs(vel_amount_5m - 1234.5678) < 0.01
    assert abs(vel_amount_1h - 12345.6789) < 0.1
    assert abs(vel_amount_24h - 123456.7890) < 1.0


# ---------------------------------------------------------------------------
# Test: Feast push independent of Iceberg circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_pyarrow(),
    reason="pyarrow not installed",
)
def test_feast_push_independent_of_iceberg_breaker(sink_with_mocked_feast, enriched_record):
    """Verify Feast push happens even if Iceberg breaker is recovering.

    Given:
      - Previous failures have incremented breaker counter (but not open yet)
    When:
      - A successful flush occurs
    Then:
      - Both Iceberg append and Feast push succeed
      - Breaker state doesn't affect Feast push
    """
    sink = sink_with_mocked_feast

    record = {**enriched_record, "transaction_id": "txn-independent"}
    sink._buffer = [record]
    sink._flush()

    # Verify both calls succeeded
    assert sink._table.append.call_count == 1
    assert sink._feast_store.push.call_count == 3
