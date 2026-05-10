"""Shared Arrow table construction utilities for Iceberg sinks."""

from __future__ import annotations

from collections.abc import Callable

import pyarrow as pa


def build_arrow_table(
    schema: pa.Schema,
    records: list[dict],
    coerce: Callable[[str, object], object],
) -> pa.Table:
    """Build a PyArrow Table from a list of record dicts.

    Args:
        schema: Target PyArrow schema — defines field names and types.
        records: Dicts to convert; missing keys are treated as None.
        coerce: Per-field value mapper called as coerce(field_name, raw_value).
                Must return a value compatible with schema.field(field_name).type.
    """
    field_names = [f.name for f in schema]
    columns: dict[str, list] = {name: [] for name in field_names}
    for record in records:
        for field_name in field_names:
            columns[field_name].append(coerce(field_name, record.get(field_name)))
    arrays = [pa.array(columns[name], type=schema.field(name).type) for name in field_names]
    return pa.table({name: arr for name, arr in zip(field_names, arrays)}, schema=schema)
