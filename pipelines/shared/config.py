"""Shared configuration for pipelines/shared infrastructure."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _parse_int(env_var: str, default: str) -> int:
    raw = os.environ.get(env_var, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {env_var}={raw!r} must be an integer") from None


def _parse_float(env_var: str, default: str) -> float:
    raw = os.environ.get(env_var, default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Environment variable {env_var}={raw!r} must be a float") from None


@dataclass
class IcebergSinkConfig:
    cb_fail_max: int = field(default_factory=lambda: _parse_int("ICEBERG_CB_FAIL_MAX", "3"))
    cb_reset_timeout_sec: float = field(
        default_factory=lambda: _parse_float("ICEBERG_CB_RESET_TIMEOUT_SEC", "30.0")
    )

    def __post_init__(self) -> None:
        if self.cb_fail_max < 1:
            raise ValueError("cb_fail_max must be >= 1")
        if self.cb_reset_timeout_sec <= 0:
            raise ValueError("cb_reset_timeout_sec must be > 0")
