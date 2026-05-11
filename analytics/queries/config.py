"""Configuration constants for analytics queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

MAX_HOURS: int = 720  # 30-day rolling window cap


def start_ms(hours: int) -> int:
    return int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
