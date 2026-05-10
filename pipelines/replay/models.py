"""Replay configuration and result models.

Defines dataclasses for replay job configuration, results, and status tracking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ReplaySource(str, Enum):  # noqa: UP042
    """Source types for replay events."""

    iceberg = "iceberg"
    kafka = "kafka"
    dlq = "dlq"


class ReplayStatus(str, Enum):  # noqa: UP042
    """Status of a replay job."""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class IcebergSourceConfig:
    """Configuration for Iceberg point-in-time query source.

    Attributes:
        table_name: Full table name (e.g., "default.enriched_transactions")
        snapshot_id: Optional specific snapshot to query
        start_timestamp: Start of time range (ISO format)
        end_timestamp: End of time range (ISO format)
        filter_expression: Optional DuckDB/PyIceberg filter expression
    """

    table_name: str
    start_timestamp: str
    end_timestamp: str
    snapshot_id: int | None = None
    filter_expression: str | None = None


@dataclass
class KafkaSourceConfig:
    """Configuration for Kafka offset range source.

    Attributes:
        topic: Kafka topic name
        partition: Partition number (default: 0)
        start_offset: Starting offset (inclusive)
        end_offset: Ending offset (inclusive, -1 for latest)
        consumer_group: Consumer group ID for replay
    """

    topic: str
    start_offset: int
    end_offset: int
    partition: int = 0
    consumer_group: str | None = None


@dataclass
class DLQSourceConfig:
    """Configuration for DLQ recovery testing source.

    Attributes:
        source_topic: Original topic that produced DLQ messages
        dlq_topic: DLQ topic name (default: txn.api.dlq)
        start_time: Optional start time filter
        end_time: Optional end time filter
        max_messages: Maximum messages to replay (default: 1000)
    """

    source_topic: str
    dlq_topic: str = "txn.api.dlq"
    start_time: str | None = None
    end_time: str | None = None
    max_messages: int = 1000


@dataclass
class ReplayConfig:
    """Configuration for a replay job.

    Attributes:
        source_type: Type of source (iceberg, kafka, dlq)
        iceberg_config: Iceberg-specific config (if source_type=iceberg)
        kafka_config: Kafka-specific config (if source_type=kafka)
        dlq_config: DLQ-specific config (if source_type=dlq)
        rule_set_version: Optional specific rule set version to use
        use_shadow_rules: Whether to run rules in shadow mode
        output_topic: Kafka topic for replay results
        description: Optional human-readable description
    """

    source_type: ReplaySource
    iceberg_config: IcebergSourceConfig | None = None
    kafka_config: KafkaSourceConfig | None = None
    dlq_config: DLQSourceConfig | None = None
    rule_set_version: str | None = None
    use_shadow_rules: bool = True
    output_topic: str = "txn.replay.results"
    description: str = ""

    def __post_init__(self) -> None:
        """Validate configuration matches source type."""
        if self.source_type == ReplaySource.iceberg and self.iceberg_config is None:
            raise ValueError("iceberg_config required when source_type='iceberg'")
        if self.source_type == ReplaySource.kafka and self.kafka_config is None:
            raise ValueError("kafka_config required when source_type='kafka'")
        if self.source_type == ReplaySource.dlq and self.dlq_config is None:
            raise ValueError("dlq_config required when source_type='dlq'")


@dataclass
class ReplayResult:
    """Result of a single replayed event.

    Attributes:
        replay_job_id: ID of the parent replay job
        original_event_id: ID of the original event
        original_timestamp: Timestamp when event was originally processed
        replay_timestamp: Timestamp when event was replayed
        original_decision: Original fraud decision (if available)
        replay_decision: New fraud decision from replay
        original_score: Original fraud score (if available)
        replay_score: New fraud score from replay
        triggered_rules_original: Rules that triggered originally
        triggered_rules_replay: Rules that triggered in replay
        score_delta: Difference in scores (replay - original)
        decision_changed: Whether decision changed between original and replay
        processing_time_ms: Time taken to process this event
        metadata: Additional metadata about the replay
    """

    replay_job_id: str
    original_event_id: str
    original_timestamp: datetime
    replay_timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())
    original_decision: str | None = None
    replay_decision: str | None = None
    original_score: float | None = None
    replay_score: float | None = None
    triggered_rules_original: list[str] = field(default_factory=list)
    triggered_rules_replay: list[str] = field(default_factory=list)
    score_delta: float = 0.0
    decision_changed: bool = False
    processing_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Compute derived fields."""
        if self.original_score is not None and self.replay_score is not None:
            self.score_delta = self.replay_score - self.original_score
        if self.original_decision is not None and self.replay_decision is not None:
            self.decision_changed = self.original_decision != self.replay_decision


@dataclass
class ReplayJobStatus:
    """Status and metadata for a replay job.

    Attributes:
        job_id: Unique job identifier
        config: Replay configuration
        status: Current job status
        created_at: Job creation timestamp
        started_at: When job started running
        completed_at: When job finished
        total_events: Total events to process
        processed_events: Events processed so far
        failed_events: Events that failed processing
        results_summary: Summary statistics of results
        error_message: Error details if failed
    """

    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: ReplayConfig | None = None
    status: ReplayStatus = ReplayStatus.pending
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_events: int = 0
    processed_events: int = 0
    failed_events: int = 0
    results_summary: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_events == 0:
            return 0.0
        return (self.processed_events / self.total_events) * 100

    @property
    def is_active(self) -> bool:
        """Check if job is currently running."""
        return self.status == ReplayStatus.running

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "job_id": self.job_id,
            "source_type": self.config.source_type.value if self.config else None,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress_percent": round(self.progress_percent, 2),
            "total_events": self.total_events,
            "processed_events": self.processed_events,
            "failed_events": self.failed_events,
            "results_summary": self.results_summary,
            "error_message": self.error_message,
            "description": self.config.description if self.config else "",
        }


@dataclass
class ReplayComparisonSummary:
    """Summary of original vs replay comparison.

    Attributes:
        total_compared: Total events compared
        decisions_changed: Number of events with different decisions
        decision_change_rate: Percentage of events with changed decisions
        avg_score_delta: Average score difference
        max_score_delta: Maximum absolute score difference
        rules_changed_most: Rules with most changes in trigger behavior
        direction_breakdown: Breakdown of decision changes by direction
    """

    total_compared: int = 0
    decisions_changed: int = 0
    decision_change_rate: float = 0.0
    avg_score_delta: float = 0.0
    max_score_delta: float = 0.0
    rules_changed_most: list[tuple[str, int]] = field(default_factory=list)
    direction_breakdown: dict[str, int] = field(default_factory=dict)

    def compute(self, results: list[ReplayResult]) -> None:
        """Compute summary statistics from results."""
        if not results:
            return

        self.total_compared = len(results)
        self.decisions_changed = sum(1 for r in results if r.decision_changed)
        self.decision_change_rate = (
            (self.decisions_changed / self.total_compared) * 100 if self.total_compared > 0 else 0.0
        )

        score_deltas = [r.score_delta for r in results if r.replay_score is not None]
        if score_deltas:
            self.avg_score_delta = sum(score_deltas) / len(score_deltas)
            self.max_score_delta = max(abs(d) for d in score_deltas)

        # Count rule trigger changes
        rule_changes: dict[str, int] = {}
        for r in results:
            orig_set = set(r.triggered_rules_original)
            replay_set = set(r.triggered_rules_replay)
            for rule in orig_set.symmetric_difference(replay_set):
                rule_changes[rule] = rule_changes.get(rule, 0) + 1

        self.rules_changed_most = sorted(rule_changes.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]

        # Decision change direction
        for r in results:
            if r.decision_changed and r.original_decision and r.replay_decision:
                direction = f"{r.original_decision}_to_{r.replay_decision}"
                self.direction_breakdown[direction] = self.direction_breakdown.get(direction, 0) + 1
