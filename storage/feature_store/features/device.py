from __future__ import annotations

from datetime import timedelta

from feast import FeatureView, Field, FileSource, PushSource
from feast.types import Bool, Int64, String

from storage.feature_store.entities.transaction import account

device_batch_source = FileSource(
    name="device_batch_source",
    path="data/device_features.parquet",
    timestamp_field="event_timestamp",
)

device_push_source = PushSource(
    name="device_push_source",
    batch_source=device_batch_source,
)

device_features = FeatureView(
    name="device_features",
    entities=[account],
    ttl=timedelta(days=14),
    schema=[
        Field(name="device_first_seen", dtype=Int64),
        Field(name="device_txn_count", dtype=Int64),
        Field(name="device_known_fraud", dtype=Bool),
        Field(name="prev_geo_country", dtype=String),
        Field(name="prev_txn_time_ms", dtype=Int64),
    ],
    source=device_push_source,
    online=True,
)
