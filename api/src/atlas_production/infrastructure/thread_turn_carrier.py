"""Non-transferable process-local carrier for fresh turn executions."""

from __future__ import annotations

from threading import Event, RLock, Thread

from atlas_production.modules.turn_execution.public import TurnExecutionOrchestrator
from atlas_production.modules.turn_runtime.public import (
    ExecutionLeaseV1,
    FailCarrierExecutionV1,
    RenewExecutionLeaseV1,
    TERMINAL_STATES,
    TurnRuntimeCurrentnessConflict,
    TurnRuntimeOwner,
)


class ThreadTurnCarrier:
    """Starts exactly one daemon thread; there is no resume or takeover API."""

    def __init__(
        self,
        orchestrator: TurnExecutionOrchestrator,
        runtime: TurnRuntimeOwner,
        *,
        heartbeat_interval_seconds: float = 5.0,
    ) -> None:
        self._orchestrator = orchestrator
        self._runtime = runtime
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._lock = RLock()
        self._active_execution_ids: set[str] = set()
        self._closing = False
        self._shutdown_requested = Event()

    def launch(self, execution_id: str) -> None:
        with self._lock:
            if self._closing:
                raise RuntimeError("turn execution carrier is shutting down")
            if execution_id in self._active_execution_ids:
                raise RuntimeError("turn execution already has a local carrier")
            self._active_execution_ids.add(execution_id)
        thread = Thread(
            target=self._run,
            args=(execution_id,),
            name=f"atlas-turn-{execution_id[-12:]}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._active_execution_ids.discard(execution_id)
            raise

    def _run(self, execution_id: str) -> None:
        with self._lock:
            if self._shutdown_requested.is_set():
                return
            try:
                lease = self._runtime.snapshot(execution_id).lease
                lease = self._renew(execution_id, lease)
            except Exception:
                # The carrier never works with stale or expired authority and
                # never reacquires it. The fail-only sweeper owns convergence.
                self._active_execution_ids.discard(execution_id)
                return
            if self._shutdown_requested.is_set():
                return
        stop = Event()
        heartbeat = Thread(
            target=self._heartbeat,
            args=(execution_id, lease, stop),
            name=f"atlas-turn-heartbeat-{execution_id[-12:]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self._orchestrator.run(execution_id)
        finally:
            stop.set()
            with self._lock:
                self._active_execution_ids.discard(execution_id)

    def shutdown(self) -> None:
        """Fail every execution still carried by this process; never transfer it."""

        self._shutdown_requested.set()
        with self._lock:
            self._closing = True
            active_execution_ids = tuple(sorted(self._active_execution_ids))
            first_error: Exception | None = None
            for execution_id in active_execution_ids:
                try:
                    while True:
                        snapshot = self._runtime.snapshot(execution_id)
                        if snapshot.state in TERMINAL_STATES:
                            self._active_execution_ids.discard(execution_id)
                            break
                        try:
                            self._runtime.fail_carrier(
                                FailCarrierExecutionV1(
                                    execution_id=execution_id,
                                    expected_version=snapshot.version,
                                    holder_id=snapshot.lease.holder_id,
                                    expected_lease_version=snapshot.lease.lease_version,
                                    fencing_token=snapshot.lease.fencing_token,
                                    failure_code="carrier_shutdown",
                                    detected_by="carrier",
                                )
                            )
                        except TurnRuntimeCurrentnessConflict:
                            current = self._runtime.snapshot(execution_id)
                            if current.state in TERMINAL_STATES:
                                self._active_execution_ids.discard(execution_id)
                                break
                            if (
                                current.version != snapshot.version
                                or current.lease.lease_version
                                != snapshot.lease.lease_version
                            ):
                                continue
                            raise
                        else:
                            self._active_execution_ids.discard(execution_id)
                            break
                except Exception as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def _renew(
        self,
        execution_id: str,
        lease: ExecutionLeaseV1,
    ) -> ExecutionLeaseV1:
        with self._lock:
            return self._runtime.renew_lease(
                RenewExecutionLeaseV1(
                    execution_id=execution_id,
                    expected_lease_version=lease.lease_version,
                    fencing_token=lease.fencing_token,
                    holder_id=lease.holder_id,
                )
            )

    def _heartbeat(
        self,
        execution_id: str,
        lease: ExecutionLeaseV1,
        stop: Event,
    ) -> None:
        while not stop.wait(self._heartbeat_interval_seconds):
            try:
                lease = self._renew(execution_id, lease)
            except Exception:
                # Lost heartbeat authority is terminalized by the fail-only
                # expiry sweeper. This carrier must never reacquire or resume.
                return


class TurnLeaseFailureSweeper:
    """Terminalizes lost carriers after expiry; never claims or resumes them."""

    def __init__(
        self,
        runtime: TurnRuntimeOwner,
        *,
        interval_seconds: float = 5.0,
        batch_size: int = 500,
    ) -> None:
        self._runtime = runtime
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        # Startup reconciliation has the same fail-only semantics as periodic
        # detection. There is deliberately no claim, takeover, or resume path.
        self._runtime.fail_expired_leases(limit=self._batch_size)
        self._thread = Thread(
            target=self._run,
            name="atlas-turn-lease-failure-sweeper",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self._interval_seconds, 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._runtime.fail_expired_leases(limit=self._batch_size)


__all__ = ["ThreadTurnCarrier", "TurnLeaseFailureSweeper"]
