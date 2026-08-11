from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from atlas_production.infrastructure.bounded_artifact_writer import (
    ProcessingArtifactFence,
)
from atlas_production.modules.processing_pipeline.job_records import (
    ProcessingExecutionSnapshot,
)
from atlas_production.modules.model_routing.contracts import ModelInvocationHandle
from atlas_production.modules.model_routing.ports import ProviderAttemptSession
from atlas_production.modules.model_routing.provider_contracts import (
    ProviderConversationOutcome,
    ProviderConversationRequest,
)
from atlas_production.modules.model_routing.records import (
    ModelInvocationRecord,
    ModelRouteRecord,
)
from atlas_production.providers import NativeJsonSchema


class OutboxTaskClaim(TypedDict):
    outbox_id: str
    task_name: str
    queue_name: str
    payload: dict[str, object]
    celery_task_id: str


class JobTaskPort(Protocol):
    def is_current_task_attempt(
        self,
        *,
        task_name: str,
        identity: Mapping[str, str],
        attempt: int | None,
    ) -> bool: ...

    def fail_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        code: str,
        detail: str,
    ) -> None: ...

    def schedule_retry(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        task_name: str,
        queue_name: str,
        payload: Mapping[str, str],
        code: str,
        detail: str,
    ) -> None: ...

    def schedule_page_batch_retry(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
        task_name: str,
        code: str,
    ) -> bool: ...

    def claim_pending_outbox(
        self,
        worker_id: str,
        *,
        limit: int = 50,
    ) -> list[OutboxTaskClaim]: ...

    def release_outbox(
        self,
        outbox_id: str,
        worker_id: str,
        error_code: str,
    ) -> None: ...

    def complete_outbox(self, outbox_id: str, worker_id: str) -> None: ...

    def reconcile_expired_claims(self, *, limit: int = 100) -> None: ...

    def reconcile_incomplete_page_batches(self, *, limit: int = 100) -> int: ...

    def cleanup_staging(self, *, limit: int = 100) -> None: ...


class ArtifactTaskPort(Protocol):
    def read_processing(
        self,
        artifact_id: str,
        *,
        expected_content_type: str,
        expected_artifact_class: str,
        expected_logical_identity: str,
        fence: ProcessingArtifactFence,
    ) -> bytes: ...

    def reconcile_incomplete(self, *, worker_id: str, limit: int = 100) -> int: ...


class ProcessingTaskPort(Protocol):
    def prepare(self, job_id: str, *, attempt: int) -> None: ...

    def process_batch(self, job_id: str, batch_id: str, *, attempt: int) -> bool: ...

    def finalize_generation(
        self,
        job_id: str,
        *,
        attempt: int,
    ) -> Literal[
        "published",
        "generation_manifest_not_ready",
        "qdrant_generation_manifest_mismatch",
        "generation_manifest_changed",
    ]: ...


class ProcessingExecutionTaskPort(Protocol):
    def load_processing_execution(
        self,
        *,
        job_id: str,
        expected_attempt: int,
        expected_fence: int,
    ) -> ProcessingExecutionSnapshot: ...

class ModelRoutingTaskPort(Protocol):
    def tested_vision_route(self, route_id: str) -> ModelRouteRecord | None: ...

    def visual_invocation(self, execution_key: str) -> ModelInvocationRecord | None: ...

    def open_attempt(self, route: ModelRouteRecord) -> ProviderAttemptSession: ...

    def invoke(
        self,
        session: ProviderAttemptSession,
        request: ProviderConversationRequest,
        response_schema: NativeJsonSchema,
    ) -> ProviderConversationOutcome: ...

    def prepare_invocation(
        self,
        route: ModelRouteRecord,
        response_schema: NativeJsonSchema,
        *,
        invocation_purpose: str = "conversation",
        subject_kind: str = "conversation",
        subject_ref: str | None = None,
        request_artifact_ref: str | None = None,
        execution_key: str | None = None,
        prompt_digest: str | None = None,
        input_digest: str | None = None,
        input_content_type: str | None = None,
        input_width: int | None = None,
        input_height: int | None = None,
        attempt_ordinal: int | None = None,
        repair_origin_error_codes: Sequence[str] = (),
    ) -> ModelInvocationHandle: ...

    def record_invocation_started(
        self,
        handle: ModelInvocationHandle,
    ) -> ModelInvocationRecord: ...

    def record_invocation_success(
        self,
        handle: ModelInvocationHandle,
        token_usage: dict[str, int],
        *,
        response_artifact_ref: str | None = None,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord: ...

    def record_invocation_failure(
        self,
        handle: ModelInvocationHandle,
        error_code: str,
        *,
        duration_ms: int | None = None,
    ) -> ModelInvocationRecord: ...


class IndexingTaskPort(Protocol):
    def index_batch(self, job_id: str, batch_id: str, *, attempt: int) -> bool: ...

    def reindex_generation(
        self,
        job_id: str,
        batch_id: str,
        *,
        attempt: int,
    ) -> bool: ...

    def cleanup_old_index(self, *, limit: int = 100) -> None: ...

    def verify_generation(
        self,
        *,
        collection_name: str,
        index_generation_id: str,
        processing_revision_id: str,
        expected_points: Mapping[str, str],
    ) -> bool: ...


WORKER_TASK_PORT_INVENTORY: Mapping[str, tuple[str, ...]] = {
    "atlas.dispatch.pending_outbox": (
        "job.claim_pending_outbox",
        "job.release_outbox",
        "job.complete_outbox",
    ),
    "atlas.processing.prepare_job": (
        "job.is_current_task_attempt",
        "job.fail_job",
        "job.schedule_retry",
        "processing.prepare",
        "execution.load_processing_execution",
    ),
    "atlas.processing.process_batch": (
        "job.is_current_task_attempt",
        "job.fail_job",
        "job.schedule_retry",
        "job.schedule_page_batch_retry",
        "processing.process_batch",
        "execution.load_processing_execution",
    ),
    "atlas.indexing.index_batch": (
        "job.is_current_task_attempt",
        "job.fail_job",
        "job.schedule_retry",
        "job.schedule_page_batch_retry",
        "indexing.index_batch",
    ),
    "atlas.indexing.reindex_generation": (
        "job.is_current_task_attempt",
        "job.fail_job",
        "job.schedule_retry",
        "indexing.reindex_generation",
    ),
    "atlas.processing.finalize_generation": (
        "job.is_current_task_attempt",
        "job.fail_job",
        "job.schedule_retry",
        "processing.finalize_generation",
        "indexing.verify_generation",
    ),
    "atlas.maintenance.reconcile_jobs": (
        "job.reconcile_expired_claims",
        "job.reconcile_incomplete_page_batches",
    ),
    "atlas.maintenance.reconcile_storage": ("artifact.reconcile_incomplete",),
    "atlas.maintenance.cleanup_staging": ("job.cleanup_staging",),
    "atlas.maintenance.cleanup_old_index": ("indexing.cleanup_old_index",),
}


@dataclass(frozen=True, slots=True)
class DispatchWorkerComposition:
    job: JobTaskPort


@dataclass(frozen=True, slots=True)
class ProcessingWorkerComposition:
    job: JobTaskPort
    artifact: ArtifactTaskPort
    processing: ProcessingTaskPort
    execution: ProcessingExecutionTaskPort
    model_routing: ModelRoutingTaskPort
    indexing: IndexingTaskPort


@dataclass(frozen=True, slots=True)
class IndexingWorkerComposition:
    job: JobTaskPort
    indexing: IndexingTaskPort


@dataclass(frozen=True, slots=True)
class MaintenanceWorkerComposition:
    job: JobTaskPort
    artifact: ArtifactTaskPort
    indexing: IndexingTaskPort


@dataclass(frozen=True, slots=True)
class BeatWorkerComposition:
    """Scheduler-only process; Celery beat owns no task runtime ports."""


@dataclass(frozen=True, slots=True)
class WorkerPortFactories:
    job: Callable[[], JobTaskPort]
    artifact: Callable[[], ArtifactTaskPort]
    processing: Callable[[], ProcessingTaskPort]
    execution: Callable[[], ProcessingExecutionTaskPort]
    model_routing: Callable[[], ModelRoutingTaskPort]
    indexing: Callable[[], IndexingTaskPort]


WorkerRoleComposition = (
    DispatchWorkerComposition
    | ProcessingWorkerComposition
    | IndexingWorkerComposition
    | MaintenanceWorkerComposition
    | BeatWorkerComposition
)
_worker_port_factories: WorkerPortFactories | None = None


def configure_worker_port_factories(factories: WorkerPortFactories) -> None:
    """T-060 composition root hook; registration performs no construction."""
    global _worker_port_factories
    _worker_port_factories = factories


def build_worker_composition(role: str) -> WorkerRoleComposition:
    """Construct only ports used by one worker role, at first role demand."""
    if role == "beat":
        return BeatWorkerComposition()
    factories = _worker_port_factories
    if factories is None:
        raise RuntimeError("worker port factories are not configured")
    if role == "dispatch":
        return DispatchWorkerComposition(job=factories.job())
    if role == "processing":
        return ProcessingWorkerComposition(
            job=factories.job(),
            artifact=factories.artifact(),
            processing=factories.processing(),
            execution=factories.execution(),
            model_routing=factories.model_routing(),
            indexing=factories.indexing(),
        )
    if role == "indexing":
        return IndexingWorkerComposition(
            job=factories.job(),
            indexing=factories.indexing(),
        )
    if role == "maintenance":
        return MaintenanceWorkerComposition(
            job=factories.job(),
            artifact=factories.artifact(),
            indexing=factories.indexing(),
        )
    raise ValueError(f"unsupported worker role: {role}")


__all__ = [
    "ArtifactTaskPort",
    "BeatWorkerComposition",
    "DispatchWorkerComposition",
    "IndexingWorkerComposition",
    "IndexingTaskPort",
    "JobTaskPort",
    "MaintenanceWorkerComposition",
    "ModelRoutingTaskPort",
    "OutboxTaskClaim",
    "ProcessingTaskPort",
    "ProcessingExecutionTaskPort",
    "ProcessingWorkerComposition",
    "WORKER_TASK_PORT_INVENTORY",
    "WorkerPortFactories",
    "WorkerRoleComposition",
    "build_worker_composition",
    "configure_worker_port_factories",
]
