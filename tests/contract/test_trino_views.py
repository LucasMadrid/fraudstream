"""
Contract tests for Trino analyst views.

These tests verify that the deployed views in Trino (backed by Iceberg on MinIO)
have the expected schema and respond within acceptable time limits.

Marked as @pytest.mark.integration and skip gracefully if Trino is unreachable.
"""

import os
import time

import pytest
import trino.dbapi

# Configuration from environment
TRINO_HOST = os.getenv("TRINO_HOST", "localhost")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8080"))
TRINO_CONNECTION_TIMEOUT = 5  # seconds


@pytest.fixture(scope="module")
def trino_connection():
    """
    Fixture to establish a Trino connection.

    Skips all tests in the module if Trino is unreachable.
    """
    conn = None
    try:
        conn = trino.dbapi.connect(
            host=TRINO_HOST,
            port=TRINO_PORT,
            user="trino",
            catalog="iceberg",
            schema="default",
        )
        # Test the connection with a simple query
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        yield conn
    except Exception as exc:
        pytest.skip(f"Trino not reachable at {TRINO_HOST}:{TRINO_PORT}: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def assert_columns_exist(cursor, view_name: str, expected_columns: list[str]):
    """
    Assert that all expected columns exist in the result set.

    Args:
        cursor: Trino cursor with executed query
        view_name: Name of the view being tested (for error messages)
        expected_columns: List of expected column names
    """
    actual_columns = [desc[0] for desc in cursor.description]
    missing = set(expected_columns) - set(actual_columns)
    extra = set(actual_columns) - set(expected_columns)

    assert not missing, (
        f"View {view_name} missing columns: {missing}\nActual columns: {actual_columns}"
    )
    assert not extra, (
        f"View {view_name} has unexpected columns: {extra}\nExpected columns: {expected_columns}"
    )


def execute_with_timeout(
    cursor,
    query: str,
    timeout_seconds: float = 60.0,
) -> tuple | None:
    """
    Execute a query and assert it completes within the timeout.

    Args:
        cursor: Trino cursor
        query: SQL query to execute
        timeout_seconds: Maximum allowed execution time

    Returns:
        First row of results (or None if no results)

    Raises:
        AssertionError: If query takes longer than timeout_seconds
    """
    start = time.perf_counter()
    cursor.execute(query)
    elapsed = time.perf_counter() - start

    assert elapsed < timeout_seconds, (
        f"Query exceeded timeout of {timeout_seconds}s (took {elapsed:.2f}s)"
    )

    # Fetch first row to ensure query executed
    return cursor.fetchone()


# ============================================================================
# Contract Tests
# ============================================================================


@pytest.mark.integration
def test_v_transaction_audit_schema(trino_connection):
    """
    Verify v_transaction_audit view has expected schema and completes within 60s.
    """
    expected_columns = [
        "transaction_id",
        "account_id",
        "merchant_id",
        "amount",
        "currency",
        "channel",
        "event_time",
        "enrichment_time",
        "geo_country",
        "geo_city",
        "geo_network_class",
        "geo_confidence",
        "device_txn_count",
        "device_known_fraud",
        "prev_geo_country",
        "prev_txn_time_ms",
        "vel_count_1m",
        "vel_amount_1m",
        "vel_count_5m",
        "vel_amount_5m",
        "vel_count_1h",
        "vel_amount_1h",
        "vel_count_24h",
        "vel_amount_24h",
        "enrichment_latency_ms",
        "decision",
        "fraud_score",
        "rule_triggers",
        "model_version",
        "decision_time_ms",
        "decision_latency_ms",
        "decision_schema_version",
    ]

    cursor = trino_connection.cursor()

    # Query with date range filter to limit result set
    query = """
        SELECT *
        FROM v_transaction_audit
        WHERE event_time >= TIMESTAMP '2026-01-01 00:00:00'
        LIMIT 1
    """

    execute_with_timeout(cursor, query, timeout_seconds=60.0)
    assert_columns_exist(cursor, "v_transaction_audit", expected_columns)

    cursor.close()


@pytest.mark.integration
def test_v_fraud_rate_daily_schema(trino_connection):
    """
    Verify v_fraud_rate_daily view has expected schema and completes within 60s.
    """
    expected_columns = [
        "decision_date",
        "channel",
        "decision",
        "transaction_count",
        "total_amount",
        "avg_fraud_score",
        "p99_latency_ms",
    ]

    cursor = trino_connection.cursor()

    # Query with date range filter
    query = """
        SELECT *
        FROM v_fraud_rate_daily
        WHERE decision_date >= DATE '2026-01-01'
        LIMIT 1
    """

    execute_with_timeout(cursor, query, timeout_seconds=60.0)
    assert_columns_exist(cursor, "v_fraud_rate_daily", expected_columns)

    cursor.close()


@pytest.mark.integration
def test_v_rule_triggers_schema(trino_connection):
    """
    Verify v_rule_triggers view has expected schema and completes within 60s.
    """
    expected_columns = [
        "decision_date",
        "rule_name",
        "decision",
        "trigger_count",
        "avg_fraud_score",
        "median_fraud_score",
        "p95_fraud_score",
    ]

    cursor = trino_connection.cursor()

    # Query with date range filter
    query = """
        SELECT *
        FROM v_rule_triggers
        WHERE decision_date >= DATE '2026-01-01'
        LIMIT 1
    """

    execute_with_timeout(cursor, query, timeout_seconds=60.0)
    assert_columns_exist(cursor, "v_rule_triggers", expected_columns)

    cursor.close()


@pytest.mark.integration
def test_v_model_versions_schema(trino_connection):
    """
    Verify v_model_versions view has expected schema and completes within 60s.
    """
    expected_columns = [
        "model_version",
        "decision_date",
        "decision",
        "transaction_count",
        "avg_fraud_score",
        "median_fraud_score",
        "p95_fraud_score",
        "avg_latency_ms",
        "p99_latency_ms",
    ]

    cursor = trino_connection.cursor()

    # Query with date range filter
    query = """
        SELECT *
        FROM v_model_versions
        WHERE decision_date >= DATE '2026-01-01'
        LIMIT 1
    """

    execute_with_timeout(cursor, query, timeout_seconds=60.0)
    assert_columns_exist(cursor, "v_model_versions", expected_columns)

    cursor.close()
