from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from analytics.queries.iceberg_reader import IcebergReader

_SAMPLE = pa.table({"id": pa.array(["t1"], type=pa.string())})


def _catalog_returning(arrow_table: pa.Table) -> MagicMock:
    cat = MagicMock()
    tbl = MagicMock()
    scan = MagicMock()
    scan.to_arrow.return_value = arrow_table
    tbl.scan.return_value = scan
    cat.load_table.return_value = tbl
    return cat


def test_scan_decisions_returns_arrow_table():
    cat = _catalog_returning(_SAMPLE)
    with patch("analytics.queries.iceberg_reader._get_catalog", return_value=cat):
        result = IcebergReader().scan_decisions(0)
    assert result is _SAMPLE
    cat.load_table.assert_called_once_with("default.fraud_decisions")


def test_scan_enriched_returns_arrow_table():
    cat = _catalog_returning(_SAMPLE)
    with patch("analytics.queries.iceberg_reader._get_catalog", return_value=cat):
        result = IcebergReader().scan_enriched(0)
    assert result is _SAMPLE
    cat.load_table.assert_called_once_with("default.enriched_transactions")


def test_scan_decisions_raises_runtime_error_on_failure():
    cat = MagicMock()
    cat.load_table.side_effect = ValueError("catalog gone")
    with patch("analytics.queries.iceberg_reader._get_catalog", return_value=cat):
        with pytest.raises(RuntimeError, match="Unable to scan fraud_decisions"):
            IcebergReader().scan_decisions(0)


def test_scan_enriched_raises_runtime_error_on_failure():
    cat = MagicMock()
    cat.load_table.side_effect = ValueError("catalog gone")
    with patch("analytics.queries.iceberg_reader._get_catalog", return_value=cat):
        with pytest.raises(RuntimeError, match="Unable to scan enriched_transactions"):
            IcebergReader().scan_enriched(0)


def test_scan_decisions_passes_start_ms_to_row_filter():
    from pyiceberg.expressions import GreaterThanOrEqual

    cat = _catalog_returning(_SAMPLE)
    with patch("analytics.queries.iceberg_reader._get_catalog", return_value=cat):
        IcebergReader().scan_decisions(12345)
    cat.load_table.return_value.scan.assert_called_once()
    call_kwargs = cat.load_table.return_value.scan.call_args
    row_filter = call_kwargs.kwargs.get("row_filter") or call_kwargs.args[0]
    assert isinstance(row_filter, GreaterThanOrEqual)
