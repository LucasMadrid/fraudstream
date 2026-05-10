"""Main replay job orchestrator.

Manages the lifecycle of a replay job including event ingestion,
processing through the scoring engine, and result collection.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pipelines.replay.models import (
    ReplayConfig,
    ReplayJobStatus,
    ReplayResult,
    ReplayStatus,
)
from pipelines.replay.result_sink import ReplayResultSink
from pipelines.replay.source_adapters import create_source_adapter

logger = logging.getLogger(__name__)


class ReplayJob:
    """Orchestrates a replay job from source to sink.

    Manages the entire lifecycle:
    1. Initialize source adapter based on config
    2. Count total events
    3. Process events through scoring engine
    4. Collect and emit results
    5. Handle cancellation and errors

    Example:
        config = ReplayConfig(
            source_type=ReplaySource.iceberg,
            iceberg_config=IcebergSourceConfig(...),
        )
        job = ReplayJob(config)
        await job.start(scoring_fn)
        status = job.get_status()
    """

    def __init__(
        self,
        config: ReplayConfig,
        job_id: str | None = None,
        brokers: str = "localhost:9092",
    ) -> None:
        """Initialize replay job.

        Args:
            config: Replay configuration
            job_id: Optional job ID (generated if not provided)
            brokers: Kafka bootstrap servers
        """
        self.config = config
        self.job_id = job_id or str(uuid.uuid4())
        self.brokers = brokers

        self._status = ReplayJobStatus(
            job_id=self.job_id,
            config=config,
            status=ReplayStatus.pending,
        )
        self._cancelled = False
        self._sink: ReplayResultSink | None = None
        self._results: list[ReplayResult] = []
        self._lock = asyncio.Lock()

    @property
    def status(self) -> ReplayJobStatus:
        """Get current job status."""
        return self._status

    def get_status(self) -> ReplayJobStatus:
        """Get current job status (thread-safe copy)."""
        return self._status

    async def start(
        self,
        scoring_fn: Callable[[dict[str, Any]], dict[str, Any]],
        result_callback: Callable[[ReplayResult], None] | None = None,
    ) -> ReplayJobStatus:
        """Start the replay job.

        Args:
            scoring_fn: Function to score an event, returns decision dict
            result_callback: Optional callback for each result

        Returns:
            Final job status
        """
        async with self._lock:
            if self._status.status != ReplayStatus.pending:
                raise RuntimeError(f"Job already started (status: {self._status.status})")

            self._status.status = ReplayStatus.running
            self._status.started_at = datetime.now().astimezone()

        logger.info(
            "Starting replay job %s with source type %s",
            self.job_id,
            self.config.source_type.value,
        )

        # Initialize sink
        self._sink = ReplayResultSink(
            kafka_brokers=self.brokers,
            topic=self.config.output_topic,
        )

        try:
            await self._run_replay(scoring_fn, result_callback)
        except asyncio.CancelledError:
            logger.info("Replay job %s cancelled", self.job_id)
            self._status.status = ReplayStatus.cancelled
            raise
        except Exception as e:
            logger.exception("Replay job %s failed", self.job_id)
            self._status.status = ReplayStatus.failed
            self._status.error_message = str(e)
        finally:
            self._status.completed_at = datetime.now().astimezone()
            if self._sink:
                self._sink.close()

        return self._status

    async def _run_replay(
        self,
        scoring_fn: Callable[[dict[str, Any]], dict[str, Any]],
        result_callback: Callable[[ReplayResult], None] | None = None,
    ) -> None:
        """Main replay loop.

        Args:
            scoring_fn: Function to score events
            result_callback: Optional callback for results
        """
        # Create source adapter
        source_config = self._get_source_config()
        adapter = create_source_adapter(
            self.config.source_type.value,
            source_config,
            brokers=self.brokers,
        )

        with adapter:
            # Count events
            self._status.total_events = adapter.get_event_count()
            logger.info(
                "Replay job %s: %d events to process", self.job_id, self._status.total_events
            )

            # Process events
            processed = 0
            failed = 0

            for event in adapter.iter_events():
                if self._cancelled:
                    logger.info("Replay job %s cancelled after %d events", self.job_id, processed)
                    self._status.status = ReplayStatus.cancelled
                    return

                try:
                    result = await self._process_event(event, scoring_fn)
                    self._results.append(result)

                    # Emit to sink
                    if self._sink:
                        self._sink.send_result(result)

                    # Call optional callback
                    if result_callback:
                        result_callback(result)

                    processed += 1

                    # Update status periodically
                    if processed % 100 == 0:
                        self._status.processed_events = processed
                        logger.debug(
                            "Replay job %s: processed %d/%d events",
                            self.job_id,
                            processed,
                            self._status.total_events,
                        )

                except Exception as e:
                    logger.warning("Failed to process event %s: %s", event.get("event_id"), e)
                    failed += 1

            self._status.processed_events = processed
            self._status.failed_events = failed

        # Compute summary
        self._compute_summary()
        self._status.status = ReplayStatus.completed

        logger.info(
            "Replay job %s completed: %d processed, %d failed",
            self.job_id,
            processed,
            failed,
        )

    def _get_source_config(self) -> Any:
        """Get the appropriate source config based on source type."""
        if self.config.source_type.value == "iceberg":
            return self.config.iceberg_config
        elif self.config.source_type.value == "kafka":
            return self.config.kafka_config
        elif self.config.source_type.value == "dlq":
            return self.config.dlq_config
        else:
            raise ValueError(f"Unknown source type: {self.config.source_type}")

    async def _process_event(
        self,
        event: dict[str, Any],
        scoring_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> ReplayResult:
        """Process a single event through the scoring engine.

        Args:
            event: Event data from source
            scoring_fn: Scoring function

        Returns:
            ReplayResult with original and replay decisions
        """
        import time

        start_time = time.time()

        # Get original decision from event metadata if available
        original_decision = event.get("payload", {}).get("fraud_decision")
        original_score = event.get("payload", {}).get("fraud_score")
        triggered_rules_original = event.get("payload", {}).get("triggered_rules", [])

        # Run through scoring engine
        payload = event.get("payload", {})
        replay_result = scoring_fn(payload)

        processing_time_ms = (time.time() - start_time) * 1000

        # Parse timestamp
        original_timestamp = event.get("timestamp", datetime.now().astimezone().isoformat())
        if isinstance(original_timestamp, str):
            try:
                original_timestamp = datetime.fromisoformat(
                    original_timestamp.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                original_timestamp = datetime.now().astimezone()

        return ReplayResult(
            replay_job_id=self.job_id,
            original_event_id=event.get("event_id", str(uuid.uuid4())),
            original_timestamp=original_timestamp,
            original_decision=original_decision,
            replay_decision=replay_result.get("decision"),
            original_score=original_score if isinstance(original_score, (int, float)) else None,
            replay_score=replay_result.get("score"),
            triggered_rules_original=triggered_rules_original
            if isinstance(triggered_rules_original, list)
            else [],
            triggered_rules_replay=replay_result.get("triggered_rules", []),
            processing_time_ms=processing_time_ms,
            metadata={
                "source": event.get("source"),
                "table": event.get("table"),
                "topic": event.get("topic"),
                "offset": event.get("offset"),
            },
        )

    def _compute_summary(self) -> None:
        """Compute summary statistics from results."""
        if not self._results:
            return

        decisions_changed = sum(1 for r in self._results if r.decision_changed)
        score_deltas = [r.score_delta for r in self._results if r.replay_score is not None]

        avg_score_delta = sum(score_deltas) / len(score_deltas) if score_deltas else 0.0
        max_score_delta = max((abs(d) for d in score_deltas), default=0.0)

        # Count rule trigger changes
        rule_changes: dict[str, int] = {}
        for r in self._results:
            orig_set = set(r.triggered_rules_original)
            replay_set = set(r.triggered_rules_replay)
            for rule in orig_set.symmetric_difference(replay_set):
                rule_changes[rule] = rule_changes.get(rule, 0) + 1

        rules_changed_most = sorted(
            rule_changes.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        self._status.results_summary = {
            "total_compared": len(self._results),
            "decisions_changed": decisions_changed,
            "decision_change_rate": round((decisions_changed / len(self._results)) * 100, 2),
            "avg_score_delta": round(avg_score_delta, 4),
            "max_score_delta": round(max_score_delta, 4),
            "rules_changed_most": rules_changed_most,
            "avg_processing_time_ms": round(
                sum(r.processing_time_ms for r in self._results) / len(self._results), 2
            ),
        }

    def cancel(self) -> None:
        """Request cancellation of the replay job."""
        logger.info("Cancelling replay job %s", self.job_id)
        self._cancelled = True

    def get_results(self) -> list[ReplayResult]:
        """Get all results collected so far.

        Returns:
            List of ReplayResult objects
        """
        return self._results.copy()

    def get_comparison_summary(self) -> dict[str, Any]:
        """Get summary of original vs replay comparison.

        Returns:
            Dictionary with comparison statistics
        """
        return self._status.results_summary
