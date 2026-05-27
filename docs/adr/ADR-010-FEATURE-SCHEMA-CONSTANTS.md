# Rule predicates read field names from `feature_schema.py`, not bare string literals

`pipelines/shared/feature_schema.py` declares itself the "single source of truth for field names" and defines `VELOCITY_COLUMNS`, `GEO_COLUMNS`, `DEVICE_COLUMNS` as typed `ColumnSpec` tuples. Each `ColumnSpec.field` is the canonical field name string. Despite this, rule predicate families access the same fields via bare string literals: `txn.get("vel_count_1m")`, `txn.get("prev_geo_country")`, `txn.get("device_known_fraud")`. A field rename requires edits in `feature_schema.py` and then hunting across three rule family files — the registry does not deliver its stated locality.

`feature_schema.py` is extended with named string constants derived from the existing `ColumnSpec` definitions. Rule families import and use these constants instead of bare strings.

## What changes in `feature_schema.py`

A constants block is added below the existing column spec definitions, derived from the specs so the field name is defined exactly once:

```python
# Named constants for use in rule predicates and txn dict access.
# Derived from ColumnSpec definitions above — field name is defined once.
VEL_COUNT_1M   = VELOCITY_COLUMNS[0].field   # "vel_count_1m"
VEL_AMOUNT_1M  = VELOCITY_COLUMNS[1].field
VEL_COUNT_5M   = VELOCITY_COLUMNS[2].field
VEL_AMOUNT_5M  = VELOCITY_COLUMNS[3].field
VEL_COUNT_1H   = VELOCITY_COLUMNS[4].field
VEL_AMOUNT_1H  = VELOCITY_COLUMNS[5].field
VEL_COUNT_24H  = VELOCITY_COLUMNS[6].field
VEL_AMOUNT_24H = VELOCITY_COLUMNS[7].field

GEO_COUNTRY      = GEO_COLUMNS[0].field     # "geo_country"
GEO_CITY         = GEO_COLUMNS[1].field
GEO_NETWORK_CLASS= GEO_COLUMNS[2].field
GEO_CONFIDENCE   = GEO_COLUMNS[3].field

DEVICE_FIRST_SEEN  = DEVICE_COLUMNS[0].field  # "device_first_seen"
DEVICE_TXN_COUNT   = DEVICE_COLUMNS[1].field
DEVICE_KNOWN_FRAUD = DEVICE_COLUMNS[2].field
PREV_GEO_COUNTRY   = DEVICE_COLUMNS[3].field
PREV_TXN_TIME_MS   = DEVICE_COLUMNS[4].field
```

## What changes in rule families

`pipelines/scoring/rules/families/velocity.py`, `impossible_travel.py`, and `new_device.py` import the relevant constants and replace bare string `txn.get("field_name")` lookups with `txn.get(FIELD_CONSTANT)`. The YAML-driven velocity evaluator that receives a field name from `conditions.get("field")` is unchanged — those field names come from `rules.yaml`, not from predicate code.

## Scope boundary

`FeatureVector` (in `pipelines/scoring/types.py`) is not moved. Its field names are already consistent with `feature_schema.py` and it is a scoring-internal type; moving it is a separate concern. `feature_serving.py`, operators, and the materializer are not changed — they already use `feature_schema.py` directly (Feast refs, ColumnSpec extraction lambdas).

## Test improvement

A single test in `tests/unit/shared/test_feature_schema.py` asserts that each named constant matches its corresponding `ColumnSpec.field`. A rename in `ColumnSpec` that is not reflected in the constant will be caught immediately — no grep required.

## Considered alternatives

- *Add a `fields` sub-module with standalone string constants* — decoupled from `ColumnSpec` definitions, so a rename in `ColumnSpec` would not automatically update the constant. Rejected: the derivation relationship is the point.
- *Use `ColumnSpec` directly in rule predicates (`VELOCITY_COLUMNS[0].field`)* — index-based access is fragile and unreadable. Rejected in favour of named constants.
- *Leave as-is* — the registry's stated contract ("adding a feature means editing this file only") is not honoured. Rejected.
