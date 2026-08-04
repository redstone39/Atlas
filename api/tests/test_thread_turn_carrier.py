from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.thread_turn_carrier import (
    ThreadTurnCarrier,
    TurnLeaseFailureSweeper,
)
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    ExecutionState,
    RoutePolicyV1,
    TurnRuntimeCurrentnessConflict,
)
from tests.turn_runtime_fixtures import route_snapshot


NOW = datetime.now(timezone.utc)


def _snapshot() -> ExecutionSnapshotV1:
    return ExecutionSnapshotV1(
        execution_id="execution-carrier-1",
        turn_id="turn-carrier-1",
        conversation_id="conversation-carrier-1",
        actor_id="actor-1",
        state=ExecutionState.CONTEXT_READY,
        version=3,
        policy=RoutePolicyV1(),
        route=route_snapshot(),
        input_digest="0" * 64,
        response_language="zh-TW",
        applied_guidance_revision=0,
        applied_guidance_digest=None,
        lease=ExecutionLeaseV1(
            execution_id="execution-carrier-1",
            holder_id="process-1",
            lease_version=2,
            fencing_token=7,
            acquired_at=NOW,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(seconds=15),
        ),
        budget=BudgetSnapshotV1(
            tool_invocations=0,
            catalog_pages=0,
            document_candidates=0,
            search_rounds=0,
            unique_evidence=0,
            provider_invocations=0,
            context_tokens=0,
            tool_tokens=0,
            schema_retries=0,
        ),
        grant_ref="grant-1",
        catalog_ref="catalog-1",
        context_pack_ref="context-1",
        deadline_at=NOW + timedelta(seconds=120),
        created_at=NOW,
        updated_at=NOW,
    )


class Runtime:
    def __init__(self) -> None:
        self.current = _snapshot()
        self.failures = []
        self.sweeps: list[int] = []
        self.renewals = []
        self.renew_attempted = Event()
        self.renew_error: Exception | None = None
        self.fail_conflicts_remaining = 0

    def snapshot(self, execution_id):
        assert execution_id == self.current.execution_id
        return self.current

    def fail_carrier(self, command):
        if self.fail_conflicts_remaining:
            self.fail_conflicts_remaining -= 1
            self.current = self.current.model_copy(
                update={
                    "state": ExecutionState.AWAITING_MODEL_ACTION,
                    "version": self.current.version + 1,
                }
            )
            raise TurnRuntimeCurrentnessConflict("stale execution version")
        self.failures.append(command)
        self.current = self.current.model_copy(
            update={
                "state": ExecutionState.TERMINAL_FAILED,
                "version": self.current.version + 1,
                "terminal_failure_code": command.failure_code,
            }
        )
        return self.current

    def renew_lease(self, command):
        self.renewals.append(command)
        self.renew_attempted.set()
        if self.renew_error is not None:
            raise self.renew_error
        renewed = self.current.lease.model_copy(
            update={
                "lease_version": self.current.lease.lease_version + 1,
                "heartbeat_at": NOW + timedelta(seconds=1),
                "expires_at": NOW + timedelta(seconds=16),
            }
        )
        self.current = self.current.model_copy(update={"lease": renewed})
        return renewed

    def fail_expired_leases(self, *, limit: int):
        self.sweeps.append(limit)
        return []


class BlockingOrchestrator:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def run(self, _execution_id: str) -> None:
        self.started.set()
        assert self.release.wait(timeout=2)


def test_graceful_carrier_shutdown_fails_active_execution_and_forbids_new_launch() -> None:
    runtime = Runtime()
    orchestrator = BlockingOrchestrator()
    carrier = ThreadTurnCarrier(orchestrator, runtime, heartbeat_interval_seconds=60)
    carrier.launch(runtime.current.execution_id)
    assert orchestrator.started.wait(timeout=1)
    assert runtime.renew_attempted.is_set()
    assert len(runtime.renewals) == 1

    carrier.shutdown()
    carrier.shutdown()

    assert runtime.current.state is ExecutionState.TERMINAL_FAILED
    assert len(runtime.failures) == 1
    assert runtime.failures[0].failure_code == "carrier_shutdown"
    assert runtime.failures[0].detected_by == "carrier"
    with pytest.raises(RuntimeError, match="shutting down"):
        carrier.launch("execution-carrier-2")
    orchestrator.release.set()


def test_initial_lease_renewal_failure_never_runs_orchestrator() -> None:
    runtime = Runtime()
    runtime.renew_error = RuntimeError("lease expired")
    orchestrator = BlockingOrchestrator()
    carrier = ThreadTurnCarrier(orchestrator, runtime)

    carrier.launch(runtime.current.execution_id)

    assert runtime.renew_attempted.wait(timeout=1)
    assert not orchestrator.started.wait(timeout=0.1)
    assert len(runtime.renewals) == 1
    assert runtime.failures == []


def test_shutdown_waits_for_initial_renew_and_prevents_orchestrator_start() -> None:
    class PausedRenewRuntime(Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.release_renew = Event()

        def renew_lease(self, command):
            self.renew_attempted.set()
            assert self.release_renew.wait(timeout=2)
            return super().renew_lease(command)

    runtime = PausedRenewRuntime()
    orchestrator = BlockingOrchestrator()
    carrier = ThreadTurnCarrier(orchestrator, runtime)

    carrier.launch(runtime.current.execution_id)
    assert runtime.renew_attempted.wait(timeout=1)
    shutdown = Thread(target=carrier.shutdown)
    shutdown.start()
    runtime.release_renew.set()
    shutdown.join(timeout=1)

    assert not shutdown.is_alive()
    assert not orchestrator.started.is_set()
    assert runtime.current.state is ExecutionState.TERMINAL_FAILED
    assert len(runtime.failures) == 1
    assert runtime.failures[0].expected_lease_version == 3


def test_shutdown_retries_after_execution_version_advances() -> None:
    runtime = Runtime()
    runtime.fail_conflicts_remaining = 1
    orchestrator = BlockingOrchestrator()
    carrier = ThreadTurnCarrier(orchestrator, runtime)

    carrier.launch(runtime.current.execution_id)
    assert orchestrator.started.wait(timeout=1)
    carrier.shutdown()

    assert runtime.current.state is ExecutionState.TERMINAL_FAILED
    assert len(runtime.failures) == 1
    assert runtime.failures[0].expected_version == 4
    orchestrator.release.set()


def test_shutdown_continues_after_one_active_snapshot_fails() -> None:
    class MultiRuntime:
        def __init__(self) -> None:
            self.second = _snapshot().model_copy(
                update={
                    "execution_id": "execution-b",
                    "lease": _snapshot().lease.model_copy(
                        update={"execution_id": "execution-b"}
                    ),
                }
            )
            self.failures = []

        def snapshot(self, execution_id):
            if execution_id == "execution-a":
                raise RuntimeError("snapshot unavailable")
            assert execution_id == "execution-b"
            return self.second

        def fail_carrier(self, command):
            self.failures.append(command)
            self.second = self.second.model_copy(
                update={
                    "state": ExecutionState.TERMINAL_FAILED,
                    "version": self.second.version + 1,
                }
            )
            return self.second

    runtime = MultiRuntime()
    carrier = ThreadTurnCarrier(BlockingOrchestrator(), runtime)
    carrier._active_execution_ids.update({"execution-a", "execution-b"})

    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        carrier.shutdown()

    assert runtime.second.state is ExecutionState.TERMINAL_FAILED
    assert len(runtime.failures) == 1
    assert carrier._active_execution_ids == {"execution-a"}


def test_startup_sweeper_is_fail_only_and_can_stop_without_takeover() -> None:
    runtime = Runtime()
    sweeper = TurnLeaseFailureSweeper(runtime, interval_seconds=60, batch_size=17)

    sweeper.start()
    sweeper.stop()

    assert runtime.sweeps == [17]
    assert not hasattr(sweeper, "claim")
    assert not hasattr(sweeper, "resume")


def test_startup_sweeper_default_respects_runtime_read_limit() -> None:
    runtime = Runtime()
    sweeper = TurnLeaseFailureSweeper(runtime, interval_seconds=60)

    sweeper.start()
    sweeper.stop()

    assert runtime.sweeps == [500]


def test_api_lifespan_fails_carriers_before_stopping_expiry_sweep() -> None:
    order: list[str] = []

    class CarrierLifecycle:
        def shutdown(self) -> None:
            order.append("carrier_shutdown")

    class SweeperLifecycle:
        def stop(self) -> None:
            order.append("sweeper_stop")

    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(
        turn_execution_carrier=CarrierLifecycle(),
        turn_lease_failure_sweeper=SweeperLifecycle(),
    )

    with TestClient(create_app(ApiComposition(**values))):
        pass

    assert order == ["carrier_shutdown", "sweeper_stop"]
