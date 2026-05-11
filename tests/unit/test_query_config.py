from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pytest

from analytics.queries.config import MAX_HOURS, start_ms


def test_max_hours_value():
    assert MAX_HOURS == 720


def test_start_ms_returns_int_in_past():
    import time

    now_ms = int(time.time() * 1000)
    result = start_ms(1)
    assert isinstance(result, int)
    assert result < now_ms
    assert result > now_ms - 2 * 3600 * 1000


def _make_empty_arrow():
    return pa.table(
        {
            "transaction_id": pa.array([], type=pa.string()),
            "decision": pa.array([], type=pa.string()),
            "fraud_score": pa.array([], type=pa.float32()),
            "rule_triggers": pa.array([], type=pa.list_(pa.string())),
            "model_version": pa.array([], type=pa.string()),
            "decision_time_ms": pa.array([], type=pa.timestamp("us")),
            "latency_ms": pa.array([], type=pa.float32()),
            "schema_version": pa.array([], type=pa.string()),
        }
    )


def _reader_stub(empty_arrow: pa.Table) -> MagicMock:
    reader = MagicMock()
    reader.scan_decisions.return_value = empty_arrow
    reader.scan_enriched.return_value = empty_arrow
    return reader


@pytest.mark.parametrize(
    "module,func,kwargs",
    [
        ("analytics.queries.fraud_rate", "fraud_rate_daily", {"days": 9999}),
        ("analytics.queries.fraud_rate", "fraud_rate_by_channel", {"days": 9999}),
        ("analytics.queries.rule_triggers", "rule_leaderboard", {"days": 9999}),
        (
            "analytics.queries.rule_triggers",
            "rule_trigger_daily",
            {"rule_name": "test_rule", "days": 9999},
        ),
        ("analytics.queries.model_versions", "model_version_summary", {"days": 9999}),
        (
            "analytics.queries.model_versions",
            "model_version_daily",
            {"model_version": "v1", "days": 9999},
        ),
        ("analytics.queries.analytics_insights", "kpi_summary", {"days": 9999}),
        ("analytics.queries.analytics_insights", "score_distribution", {"days": 9999}),
        ("analytics.queries.analytics_insights", "hourly_volume", {"days": 9999}),
        ("analytics.queries.analytics_insights", "amount_by_decision", {"days": 9999}),
        ("analytics.queries.analytics_insights", "geo_breakdown", {"days": 9999, "top_n": 1}),
        ("analytics.queries.analytics_insights", "velocity_by_decision", {"days": 9999}),
        ("analytics.queries.analytics_insights", "top_risk_accounts", {"days": 9999, "top_n": 1}),
    ],
)
def test_hours_clamped_returns_dataframe(module, func, kwargs):
    mod = importlib.import_module(module)
    fn = getattr(mod, func)
    stub = _reader_stub(_make_empty_arrow())
    with (
        patch("analytics.queries.fraud_rate.get_reader", return_value=stub),
        patch("analytics.queries.rule_triggers.get_reader", return_value=stub),
        patch("analytics.queries.model_versions.get_reader", return_value=stub),
        patch("analytics.queries.analytics_insights.get_reader", return_value=stub),
    ):
        result = fn(**kwargs)
    assert isinstance(result, pd.DataFrame)
