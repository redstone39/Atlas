from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_production.infrastructure.turn_experience_reconciler import (
    TurnExperienceReconciler,
)
from atlas_production.modules.turn_runtime.public import TerminalOutcomeV1


NOW = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def _outcome(execution_id: str, offset: int = 0) -> TerminalOutcomeV1:
    return TerminalOutcomeV1(
        execution_id=execution_id,
        scan_sequence=offset + 1,
        outcome="completed",
        terminal_commit_intent_ref=f"terminal-{execution_id}",
        evidence_pack_ref=f"evidence-{execution_id}",
        governed_answer_draft_ref=f"governed-{execution_id}",
        citation_binding_draft_ref=f"citation-{execution_id}",
        audit_draft_ref=f"audit-{execution_id}",
        committed_at=NOW + timedelta(seconds=offset),
    )


class Runtime:
    def __init__(self, outcomes: list[TerminalOutcomeV1]) -> None:
        self.outcomes = outcomes
        self.scans = []

    def completed_terminal_outcomes(self, *, after, limit):
        self.scans.append((after, limit))
        rows = self.outcomes
        if after is not None:
            rows = [
                row
                for row in rows
                if (row.scan_sequence, row.execution_id)
                > (after.scan_sequence, after.execution_id)
            ]
        return rows[:limit]


class Recorder:
    def __init__(self, *, fail_on: str | None = None, lose_after_commit: bool = False):
        self.fail_on = fail_on
        self.lose_after_commit = lose_after_commit
        self.calls = []
        self.materialized = set()
        self._lost = False

    def record_execution(self, execution_id: str):
        self.calls.append(execution_id)
        if execution_id == self.fail_on:
            raise RuntimeError("secret recorder failure detail")
        self.materialized.add(execution_id)
        if self.lose_after_commit and not self._lost:
            self._lost = True
            raise RuntimeError("caller disappeared after commit")
        return execution_id


def test_start_runs_bounded_backfill_before_daemon_and_stop_joins() -> None:
    runtime = Runtime([_outcome("execution-a"), _outcome("execution-b", 1)])
    recorder = Recorder()
    reconciler = TurnExperienceReconciler(
        runtime=runtime,
        recorder=recorder,
        interval_seconds=60,
        batch_size=1,
    )

    reconciler.start()
    reconciler.stop()

    assert recorder.calls == ["execution-a"]
    assert runtime.scans[0][1] == 1
    assert reconciler.cursor is not None
    assert reconciler.cursor.execution_id == "execution-a"


def test_failure_keeps_cursor_at_last_success_and_does_not_skip(caplog) -> None:
    runtime = Runtime(
        [_outcome("execution-a"), _outcome("execution-b", 1), _outcome("execution-c", 2)]
    )
    recorder = Recorder(fail_on="execution-b")
    reconciler = TurnExperienceReconciler(runtime=runtime, recorder=recorder)

    assert reconciler.run_once() == 1

    assert recorder.calls == ["execution-a", "execution-b"]
    assert reconciler.cursor is not None
    assert reconciler.cursor.execution_id == "execution-a"
    assert "turn_experience_reconciliation_failed execution_id=execution-b" in caplog.text
    assert "secret recorder failure detail" not in caplog.text

    recorder.fail_on = None
    assert reconciler.run_once() == 2
    assert recorder.calls[-2:] == ["execution-b", "execution-c"]


def test_store_commit_then_caller_loss_retries_same_source_without_duplicate() -> None:
    runtime = Runtime([_outcome("execution-a")])
    recorder = Recorder(lose_after_commit=True)
    reconciler = TurnExperienceReconciler(runtime=runtime, recorder=recorder)

    assert reconciler.run_once() == 0
    assert reconciler.cursor is None
    assert recorder.materialized == {"execution-a"}

    assert reconciler.run_once() == 1
    assert recorder.calls == ["execution-a", "execution-a"]
    assert recorder.materialized == {"execution-a"}
    assert reconciler.cursor is not None


def test_process_restart_from_null_cursor_exact_replays_all_sources() -> None:
    runtime = Runtime([_outcome("execution-a"), _outcome("execution-b", 1)])
    recorder = Recorder()
    first = TurnExperienceReconciler(runtime=runtime, recorder=recorder)
    restarted = TurnExperienceReconciler(runtime=runtime, recorder=recorder)

    assert first.run_once() == 2
    assert restarted.run_once() == 2

    assert recorder.calls == [
        "execution-a",
        "execution-b",
        "execution-a",
        "execution-b",
    ]
    assert recorder.materialized == {"execution-a", "execution-b"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_seconds": 0},
        {"batch_size": 0},
        {"batch_size": 101},
    ],
)
def test_reconciler_rejects_unbounded_configuration(kwargs) -> None:
    with pytest.raises(ValueError):
        TurnExperienceReconciler(runtime=Runtime([]), recorder=Recorder(), **kwargs)
