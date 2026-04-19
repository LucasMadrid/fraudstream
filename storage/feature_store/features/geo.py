from __future__ import annotations

from datetime import timedelta

from feast import FeatureView, Field, FileSource, PushSource
from feast.types import Float64, String

from storage.feature_store.entities.transaction import account

geo_batch_source = FileSource(
    name="geo_batch_source",
    path="data/geo_features.parquet",
    timestamp_field="event_timestamp",
)

geo_push_source = PushSource(
    name="geo_push_source",
    batch_source=geo_batch_source,
)

geo_features = FeatureView(
    name="geo_features",
    entities=[account],
    ttl=timedelta(hours=24),
    schema=[
        Field(name="geo_country", dtype=String),
        Field(name="geo_city", dtype=String),
        Field(name="geo_network_class", dtype=String),
        Field(name="geo_confidence", dtype=Float64),
    ],
    source=geo_push_source,
    online=True,
)
