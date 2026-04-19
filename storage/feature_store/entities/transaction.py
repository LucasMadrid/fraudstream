from __future__ import annotations

from feast import Entity, ValueType

account = Entity(
    name="account_id",
    join_keys=["account_id"],
    value_type=ValueType.STRING,
    description="Account identifier — primary entity key for all feature views",
)
