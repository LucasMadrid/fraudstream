from __future__ import annotations

from decimal import Decimal


def evaluate_new_device(txn: dict, conditions: dict) -> bool:
    """Return True if the new-device rule condition is met.

    Dispatches to one of four rule types based on conditions keys:
    - {"amount": ...}           → ND-001: new device + high amount
    - {"device_count": ...}     → ND-002: new device + rapid txn
    - {"network_class": ...}    → ND-004: new device + foreign network
    - {}                        → ND-003: known fraud device flag

    Args:
        txn: Avro-decoded enriched transaction dict.
        conditions: Rule conditions from YAML.

    Returns:
        True if condition is met, False if not or if required fields are absent.
    """
    if "amount" in conditions:
        return _evaluate_nd001(txn, conditions)

    if "device_count" in conditions:
        return _evaluate_nd002(txn, conditions)

    if "network_class" in conditions:
        return _evaluate_nd004(txn, conditions)

    # ND-003: empty conditions — known fraud device boolean flag
    return _evaluate_nd003(txn)


def _evaluate_nd001(txn: dict, conditions: dict) -> bool:
    """ND-001: new device + transaction amount above threshold."""
    device_is_new = txn.get("device_is_new")
    if not device_is_new:
        return False
    txn_amount = txn.get("txn_amount")
    if txn_amount is None:
        return False
    return txn_amount > Decimal(str(conditions["amount"]))


def _evaluate_nd002(txn: dict, conditions: dict) -> bool:
    """ND-002: new device + rapid transaction velocity."""
    device_is_new = txn.get("device_is_new")
    if not device_is_new:
        return False
    device_count_24h = txn.get("device_count_24h")
    vel_count_1m = txn.get("vel_count_1m")
    if device_count_24h is None or vel_count_1m is None:
        return False
    return (
        device_count_24h >= conditions["device_count"] and vel_count_1m >= conditions["vel_count"]
    )


def _evaluate_nd003(txn: dict) -> bool:
    """ND-003: known fraud device — explicit True check; None → False (v1 behaviour)."""
    return txn.get("device_known_fraud") is True


def _evaluate_nd004(txn: dict, conditions: dict) -> bool:
    """ND-004: new device from a specific network class (e.g. HOSTING)."""
    device_is_new = txn.get("device_is_new")
    if not device_is_new:
        return False
    geo_network_class = txn.get("geo_network_class")
    if geo_network_class is None:
        return False
    return geo_network_class == conditions["network_class"]
