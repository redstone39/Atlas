from __future__ import annotations

import logging
from threading import Event, Thread

from atlas_production.infrastructure.turn_experience_recorder import TurnExperienceRecorder
from atlas_production.modules.turn_runtime.public import (
    TerminalCompletionCursorV1,
    TurnRuntimeOwner,
)


logger = logging.getLogger(__name__)


class TurnExperienceReconciler:
    """Process-local bounded replay of durable completed terminal sources."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeOwner,
        recorder: TurnExperienceRecorder,
        interval_seconds: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("experience reconciliation interval must be positive")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("experience reconciliation batch size must be between 1 and 100")
        self._runtime = runtime
        self._recorder = recorder
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._cursor: TerminalCompletionCursorV1 | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def cursor(self) -> TerminalCompletionCursorV1 | None:
        return self._cursor

    def start(self) -> None:
        self.run_once()
        self._thread = Thread(
            target=self._run,
            name="atlas-turn-experience-reconciler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self._interval_seconds, 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.run_once()

    def run_once(self) -> int:
        outcomes = self._runtime.completed_terminal_outcomes(
            after=self._cursor,
            limit=self._batch_size,
        )
        recorded = 0
        for outcome in outcomes:
            try:
                self._recorder.record_execution(outcome.execution_id)
            except Exception:
                logger.warning(
                    "turn_experience_reconciliation_failed execution_id=%s",
                    outcome.execution_id,
                )
                break
            self._cursor = TerminalCompletionCursorV1(
                scan_sequence=outcome.scan_sequence,
                execution_id=outcome.execution_id,
            )
            recorded += 1
        return recorded


__all__ = ["TurnExperienceReconciler"]
