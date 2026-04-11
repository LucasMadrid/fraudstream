from __future__ import annotations

from decimal import Decimal


def evaluate_velocity(txn: dict, conditions: dict) -> bool:
    """Return True if the transaction meets the velocity rule conditions.

    Args:
        txn: Avro-decoded enriched transaction dict.
        conditions: Rule conditions from YAML, e.g. {"field": "vel_count_1m", "count": 5}

    Returns:
        True if condition is exceeded, False if not or if required fields are absent.
    """
    field = conditions.get("field")
    if field is None:
        return False

    value = txn.get(field)
    if value is None:
        return False

    if "count" in conditions:
        return value > conditions["count"]

    if "amount" in conditions:
        return value > Decimal(str(conditions["amount"]))

    return False
