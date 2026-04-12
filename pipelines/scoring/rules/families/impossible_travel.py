from __future__ import annotations


def evaluate_impossible_travel(txn: dict, conditions: dict) -> bool:
    """Return True if impossible travel condition is met.

    CROSS-FEATURE DEPENDENCY: requires prev_geo_country and prev_txn_time_ms
    from feature 002 DeviceProfileState. These fields are absent from
    enriched-txn-v1.avsc. Until feature 002 ships them, returns False.

    Args:
        txn: Avro-decoded enriched transaction dict.
        conditions: Rule conditions, e.g. {"window_ms": 3600000} for IT-001
                    or {"count": 3} for IT-002.

    Returns:
        False if prev_geo_country or prev_txn_time_ms is absent/None (v1 behaviour).
        True if different countries detected within window_ms timeframe.
    """
    prev_geo_country = txn.get("prev_geo_country")
    if prev_geo_country is None:
        return False

    prev_txn_time_ms = txn.get("prev_txn_time_ms")
    if prev_txn_time_ms is None:
        return False

    current_country = txn.get("geo_country")
    if current_country is None:
        return False

    current_time_ms = txn.get("txn_time_ms")
    if current_time_ms is None:
        return False

    if prev_geo_country == current_country:
        return False

    if "window_ms" in conditions:
        time_delta = abs(current_time_ms - prev_txn_time_ms)
        return time_delta <= conditions["window_ms"]

    return False
