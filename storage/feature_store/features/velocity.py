from __future__ import annotations

from datetime import timedelta

from feast import FeatureView, Field, FileSource, PushSource
from feast.types import Float64, Int64

from storage.feature_store.entities.transaction import account

velocity_batch_source = FileSource(
    name="velocity_batch_source",
    path="data/velocity_features.parquet",
    timestamp_field="event_timestamp",
)

velocity_push_source = PushSource(
    name="velocity_push_source",
    batch_source=velocity_batch_source,
)

velocity_features = FeatureView(
    name="velocity_features",
    entities=[account],
    ttl=timedelta(hours=48),
    schema=[
        Field(name="vel_count_1m", dtype=Int64),
        Field(name="vel_amount_1m", dtype=Float64),
        Field(name="vel_count_5m", dtype=Int64),
        Field(name="vel_amount_5m", dtype=Float64),
        Field(name="vel_count_1h", dtype=Int64),
        Field(name="vel_amount_1h", dtype=Float64),
        Field(name="vel_count_24h", dtype=Int64),
        Field(name="vel_amount_24h", dtype=Float64),
    ],
    source=velocity_push_source,
    online=True,
)
