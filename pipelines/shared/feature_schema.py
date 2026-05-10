"""Single source of truth for Feast feature view references, field names, and Arrow specs.

Adding a new feature means editing this file — not hunting across feature_serving,
feature_materializer, job_extension, and FeatureVector independently.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import pyarrow as pa

# ---------------------------------------------------------------------------
# Arrow column specification (used by feature_materializer)
# ---------------------------------------------------------------------------


class ColumnSpec(NamedTuple):
    field: str
    arrow_type: pa.DataType | None
    extract: Callable[[dict], object]


VELOCITY_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("vel_count_1m", pa.int32(), lambda r: int(r.get("vel_count_1m") or 0)),
    ColumnSpec("vel_amount_1m", pa.float64(), lambda r: float(r.get("vel_amount_1m") or 0)),
    ColumnSpec("vel_count_5m", pa.int32(), lambda r: int(r.get("vel_count_5m") or 0)),
    ColumnSpec("vel_amount_5m", pa.float64(), lambda r: float(r.get("vel_amount_5m") or 0)),
    ColumnSpec("vel_count_1h", pa.int32(), lambda r: int(r.get("vel_count_1h") or 0)),
    ColumnSpec("vel_amount_1h", pa.float64(), lambda r: float(r.get("vel_amount_1h") or 0)),
    ColumnSpec("vel_count_24h", pa.int32(), lambda r: int(r.get("vel_count_24h") or 0)),
    ColumnSpec("vel_amount_24h", pa.float64(), lambda r: float(r.get("vel_amount_24h") or 0)),
]

GEO_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("geo_country", None, lambda r: r.get("geo_country") or ""),
    ColumnSpec("geo_city", None, lambda r: r.get("geo_city") or ""),
    ColumnSpec("geo_network_class", None, lambda r: r.get("geo_network_class") or ""),
    ColumnSpec("geo_confidence", pa.float64(), lambda r: float(r.get("geo_confidence") or 0)),
    # geo_lat / geo_lon: materialised to Feast but not served back to the scoring pipeline
    ColumnSpec("geo_lat", pa.float32(), lambda r: float(r.get("geo_lat") or 0)),
    ColumnSpec("geo_lon", pa.float32(), lambda r: float(r.get("geo_lon") or 0)),
]

DEVICE_COLUMNS: list[ColumnSpec] = [
    ColumnSpec(
        "device_first_seen",
        pa.int64(),
        lambda r: int(r["device_first_seen"]) if r.get("device_first_seen") else None,
    ),
    ColumnSpec("device_txn_count", pa.int64(), lambda r: int(r.get("device_txn_count") or 0)),
    ColumnSpec(
        "device_known_fraud",
        pa.bool_(),
        lambda r: bool(r.get("device_known_fraud", False)),
    ),
    ColumnSpec("prev_geo_country", None, lambda r: r.get("prev_geo_country") or ""),
    ColumnSpec(
        "prev_txn_time_ms",
        pa.int64(),
        lambda r: int(r["prev_txn_time_ms"]) if r.get("prev_txn_time_ms") else None,
    ),
]

# Maps Feast push-source name → column specs.  Iteration order determines push order.
FEATURE_GROUPS: list[tuple[str, list[ColumnSpec]]] = [
    ("velocity_push_source", VELOCITY_COLUMNS),
    ("geo_push_source", GEO_COLUMNS),
    ("device_push_source", DEVICE_COLUMNS),
]

# ---------------------------------------------------------------------------
# Feast online-store feature references (used by feature_serving)
# ---------------------------------------------------------------------------

VELOCITY_REFS: list[str] = [
    "velocity_features:vel_count_1m",
    "velocity_features:vel_amount_1m",
    "velocity_features:vel_count_5m",
    "velocity_features:vel_amount_5m",
    "velocity_features:vel_count_1h",
    "velocity_features:vel_amount_1h",
    "velocity_features:vel_count_24h",
    "velocity_features:vel_amount_24h",
]

GEO_REFS: list[str] = [
    "geo_features:geo_country",
    "geo_features:geo_city",
    "geo_features:geo_network_class",
    "geo_features:geo_confidence",
]

DEVICE_REFS: list[str] = [
    "device_features:device_first_seen",
    "device_features:device_txn_count",
    "device_features:device_known_fraud",
    "device_features:prev_geo_country",
    "device_features:prev_txn_time_ms",
]

FEATURE_REFS: list[str] = VELOCITY_REFS + GEO_REFS + DEVICE_REFS
