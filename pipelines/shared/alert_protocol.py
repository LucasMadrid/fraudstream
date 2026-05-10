"""AlertSink — shared protocol for alert emission.

Lets _AlertSinkFunction treat Kafka and PostgreSQL sinks uniformly
without knowing either concrete type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pipelines.scoring.types import FraudAlert


@runtime_checkable
class AlertSink(Protocol):
    """Any object with a single emit() method satisfies this protocol."""

    def emit(self, alert: FraudAlert) -> None: ...
