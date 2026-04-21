from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analytics.queries.config import MAX_HOURS


def test_max_hours_value():
    assert MAX_HOURS == 720


def _make_empty_arrow():
    import pyarrow as pa
    return pa.table({
        "transaction_id": pa.array([], type=pa.string()),
        "decision": pa.array([], type=pa.string()),
        "fraud_score": pa.array([], type=pa.float32()),
        "rule_triggers": pa.array([], type=pa.list_(pa.string())),
        "model_version": pa.array([], type=pa.string()),
        "decision_time_ms": pa.array([], type=pa.int64()),
        "latency_ms": pa.array([], type=pa.float32()),
        "schema_version": pa.array([], type=pa.string()),
    })


def _mock_catalog(arrow_table):
    catalog = MagicMock()
    table = MagicMock()
    scan = MagicMock()
    scan.to_arrow.return_value = arrow_table
    table.scan.return_value = scan
    catalog.load_table.return_value = table
    return catalog


@pytest.mark.parametrize("module,func,kwargs", [
    ("analytics.queries.fraud_rate", "fraud_rate_daily", {"hours": 9999}),
    ("analytics.queries.fraud_rate", "fraud_rate_by_channel", {"hours": 9999}),
    ("analytics.queries.rule_triggers", "rule_leaderboard", {"hours": 9999}),
    ("analytics.queries.rule_triggers", "rule_trigger_daily", {"rule_name": "test_rule", "hours": 9999}),
    ("analytics.queries.model_versions", "model_version_summary", {"hours": 9999}),
    ("analytics.queries.model_versions", "model_version_daily", {"model_version": "v1", "hours": 9999}),
])
def test_hours_clamped_returns_dataframe(module, func, kwargs):
    import importlib
    mod = importlib.import_module(module)
    fn = getattr(mod, func)
    empty_arrow = _make_empty_arrow()
    mock_cat = _mock_catalog(empty_arrow)
    with patch("analytics.queries.fraud_rate.load_catalog", return_value=mock_cat), \
         patch("analytics.queries.rule_triggers.load_catalog", return_value=mock_cat), \
         patch("analytics.queries.model_versions.load_catalog", return_value=mock_cat):
        result = fn(**kwargs)
    assert isinstance(result, pd.DataFrame)
