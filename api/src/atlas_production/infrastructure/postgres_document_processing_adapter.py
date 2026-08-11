"""Isolated pre-cutover adapter for current document-processing consumers.

T-060 owns executable wiring.  This adapter deliberately contains no mutation
logic; it preserves the current method signatures while routing each family to
the named PostgreSQL command owner introduced by T-030.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Connection

from atlas_production.infrastructure.postgres_owner.document_processing import (
    AcceptProcessingExecutionCommand,
    BatchCheckpointCommand,
    CaptureProcessingExecutionCommand,
    DocumentMutationCommand,
    FinalGenerationPublicationCommand,
    IndexPublicationManifest,
    JobTransitionCommand,
    LoadProcessingExecutionCommand,
    OutboxDeliveryCommand,
    ProcessingBatchClaimRecord,
    ProcessingCheckpointRecord,
    SessionFactory,
    TaskOutboxRecord,
)
from atlas_production.modules.processing_pipeline.job_records import (
    DocumentJobRequestAuthorityProjection,
    ProcessingControlResult,
    ProcessingExecutionSnapshot,
    ProcessingJobListBatch,
    ProcessingJobRecord,
    ProcessingJobView,
    ProcessingProfilePin,
)


@dataclass(frozen=True, slots=True)
class PostgresProcessingExecutionAdapter:
    """Request-owned deterministic processing snapshot provider."""

    session_factory: SessionFactory

    def accept_processing_job(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        progress_total: int | None = None,
    ) -> ProcessingJobRecord:
        return AcceptProcessingExecutionCommand(self.session_factory).accept_job(
            media_type=media_type,
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            created_by=created_by,
            progress_total=progress_total,
        )

    def load_processing_execution(
        self,
        *,
        job_id: str,
        expected_attempt: int,
        expected_fence: int,
    ) -> ProcessingExecutionSnapshot:
        return LoadProcessingExecutionCommand(self.session_factory).execute(
            job_id=job_id,
            expected_attempt=expected_attempt,
            expected_fence=expected_fence,
        )

@dataclass(frozen=True, slots=True)
class PostgresDocumentProcessingAdapter:
    session_factory: SessionFactory
    _documents: DocumentMutationCommand = field(init=False, repr=False)
    _jobs: JobTransitionCommand = field(init=False, repr=False)
    _outbox: OutboxDeliveryCommand = field(init=False, repr=False)
    _batches: BatchCheckpointCommand = field(init=False, repr=False)
    _publication: FinalGenerationPublicationCommand = field(init=False, repr=False)
    _accept_execution: AcceptProcessingExecutionCommand = field(
        init=False, repr=False
    )
    _load_execution: LoadProcessingExecutionCommand = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_documents", DocumentMutationCommand(self.session_factory))
        object.__setattr__(self, "_jobs", JobTransitionCommand(self.session_factory))
        object.__setattr__(
            self,
            "_accept_execution",
            AcceptProcessingExecutionCommand(self.session_factory),
        )
        object.__setattr__(
            self,
            "_load_execution",
            LoadProcessingExecutionCommand(self.session_factory),
        )
        object.__setattr__(self, "_outbox", OutboxDeliveryCommand(self.session_factory))
        object.__setattr__(
            self,
            "_batches",
            BatchCheckpointCommand(self.session_factory),
        )
        object.__setattr__(
            self,
            "_publication",
            FinalGenerationPublicationCommand(self.session_factory),
        )

    def list_jobs(self, *, document_id: str | None = None, limit: int = 100) -> list[ProcessingJobRecord]:
        return self._jobs.list_jobs(document_id=document_id, limit=limit)

    def get_job(self, job_id: str) -> ProcessingJobRecord | None:
        return self._jobs.get_job(job_id)

    def transaction(self) -> AbstractContextManager[Connection]:
        return self._jobs.transaction()

    def list_job_projection_batch(self, *, actor_type: str, actor_id: str, document_id: str | None = None) -> ProcessingJobListBatch:
        """Legacy-shape seam retained fail-closed until T-060 deletes its caller."""
        return self._jobs.list_job_projection_batch(actor_type=actor_type, actor_id=actor_id, presented_browser_session_token="", document_id=document_id)

    def list_document_job_request_projections(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[DocumentJobRequestAuthorityProjection, ...]:
        return self._jobs.list_document_job_request_projections(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            document_id=document_id,
            limit=limit,
        )

    def get_document_job_request_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        job_id: str,
    ) -> DocumentJobRequestAuthorityProjection | None:
        return self._jobs.get_document_job_request_projection(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            job_id=job_id,
        )

    def create_processing_job(self, *, document_id: str, document_version_id: str, job_kind: str, idempotency_scope: str, idempotency_key: str, created_by: str | None, progress_total: int | None = None, connection: Connection | None = None) -> ProcessingJobRecord:
        return self._jobs.create_processing_job(document_id=document_id, document_version_id=document_version_id, job_kind=job_kind, idempotency_scope=idempotency_scope, idempotency_key=idempotency_key, created_by=created_by, progress_total=progress_total, connection=connection)

    def accept_processing_job(self, *, media_type: str, document_id: str, document_version_id: str, job_kind: str, idempotency_scope: str, idempotency_key: str, created_by: str | None, progress_total: int | None = None) -> ProcessingJobRecord:
        return self._accept_execution.accept_job(media_type=media_type, document_id=document_id, document_version_id=document_version_id, job_kind=job_kind, idempotency_scope=idempotency_scope, idempotency_key=idempotency_key, created_by=created_by, progress_total=progress_total)

    def capture_processing_execution(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        created_by: str | None,
        progress_total: int | None = None,
    ) -> ProcessingExecutionSnapshot:
        """Capture caller-bound config without accepting a job."""

        return CaptureProcessingExecutionCommand(self.session_factory).execute(
            media_type=media_type,
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            created_by=created_by,
            progress_total=progress_total,
        )

    def load_processing_execution(self, *, job_id: str, expected_attempt: int, expected_fence: int) -> ProcessingExecutionSnapshot:
        return self._load_execution.execute(job_id=job_id, expected_attempt=expected_attempt, expected_fence=expected_fence)

    def is_current_task_attempt(self, *, task_name: str, identity: Mapping[str, str], attempt: int | None) -> bool:
        return self._jobs.is_current_task_attempt(task_name=task_name, identity=identity, attempt=attempt)

    def claim_job(self, job_id: str, worker_id: str, *, lease_seconds: int = 90) -> tuple[ProcessingJobRecord, int] | None:
        return self._jobs.claim_job(job_id, worker_id, lease_seconds=lease_seconds)

    def cancel_processing_job(self, job_id: str) -> ProcessingJobRecord:
        return self._jobs.cancel_processing_job(job_id)

    def stop_processing_job_request(self, *, job_id: str, presented_browser_session_token: str, actor_type: str, actor_id: str) -> ProcessingControlResult:
        return self._jobs.stop_processing_job_request(job_id=job_id, presented_browser_session_token=presented_browser_session_token, actor_type=actor_type, actor_id=actor_id)

    def retry_terminal_job(self, job_id: str) -> ProcessingJobRecord:
        return self._jobs.retry_terminal_job(job_id)

    def retry_processing_job_request(self, *, job_id: str, presented_browser_session_token: str, actor_type: str, actor_id: str) -> ProcessingControlResult:
        return self._jobs.retry_processing_job_request(job_id=job_id, presented_browser_session_token=presented_browser_session_token, actor_type=actor_type, actor_id=actor_id)

    def fail_job(self, job_id: str, *, expected_attempt: int, code: str, detail: str) -> None:
        self._jobs.fail_job(job_id, expected_attempt=expected_attempt, code=code, detail=detail)

    def schedule_retry(self, job_id: str, *, expected_attempt: int, task_name: str, queue_name: str, payload: Mapping[str, str], code: str, detail: str, delay_seconds: int = 2) -> None:
        self._jobs.schedule_retry(job_id, expected_attempt=expected_attempt, task_name=task_name, queue_name=queue_name, payload=payload, code=code, detail=detail, delay_seconds=delay_seconds)

    def schedule_page_batch_retry(self, job_id: str, batch_id: str, *, expected_attempt: int, task_name: str, code: str, delay_seconds: int = 2) -> bool:
        return self._batches.schedule_page_batch_retry(job_id, batch_id, expected_attempt=expected_attempt, task_name=task_name, code=code, delay_seconds=delay_seconds)

    def prepare_job(self, job_id: str, *, total_units: int, profile_id: str, profile_revision: int, expected_attempt: int, enqueue_batches: bool = True) -> list[str]:
        return self._jobs.prepare_job(job_id, total_units=total_units, profile_id=profile_id, profile_revision=profile_revision, expected_attempt=expected_attempt, enqueue_batches=enqueue_batches)

    def prepare_reindex(self, job_id: str, *, expected_attempt: int, batch_size: int = 100) -> int:
        return self._jobs.prepare_reindex(job_id, expected_attempt=expected_attempt, batch_size=batch_size)

    def mark_failure(self, job_id: str, *, fence: int, code: str, detail: str, transient: bool) -> None:
        self._jobs.mark_failure(job_id, fence=fence, code=code, detail=detail, transient=transient)

    def get_outbox(self, outbox_id: str) -> TaskOutboxRecord | None:
        return self._outbox.get_outbox(outbox_id)

    def list_outbox(self, *, status: str | None = None, limit: int = 100) -> list[TaskOutboxRecord]:
        return self._outbox.list_outbox(status=status, limit=limit)

    def claim_pending_outbox(self, worker_id: str, *, limit: int = 50) -> list[dict[str, object]]:
        return self._outbox.claim_pending_outbox(worker_id, limit=limit)

    def release_outbox(self, outbox_id: str, worker_id: str, code: str) -> None:
        self._outbox.release_outbox(outbox_id, worker_id, code)

    def complete_outbox(self, outbox_id: str, worker_id: str) -> None:
        self._outbox.complete_outbox(outbox_id, worker_id)

    def reconcile_expired_claims(self, *, limit: int = 100) -> None:
        self._outbox.reconcile_expired_claims(limit=limit)

    def reconcile_incomplete_page_batches(self, *, limit: int = 100) -> int:
        return self._batches.reconcile_incomplete_page_batches(limit=limit)

    def cleanup_staging(self, *, limit: int = 100) -> None:
        self._batches.cleanup_staging(limit=limit)

    def batch_execution(self, job_id: str, batch_id: str) -> AbstractContextManager[ProcessingJobView | None]:
        return self._batches.batch_execution(job_id, batch_id)

    def preparation_execution(self, job_id: str, *, expected_attempt: int) -> AbstractContextManager[ProcessingJobView | None]:
        return self._batches.preparation_execution(job_id, expected_attempt=expected_attempt)

    def finalize_document_page_preparation(self, connection: Connection, *, job_id: str, expected_attempt: int, claim_fence: int, claim_token: str, page_record: dict[str, Any]) -> str:
        return self._batches.finalize_document_page_preparation(connection, job_id=job_id, expected_attempt=expected_attempt, claim_fence=claim_fence, claim_token=claim_token, page_record=page_record)

    def prepared_page_artifact(self, job_id: str, batch_id: str) -> dict[str, Any]:
        return self._batches.prepared_page_artifact(job_id, batch_id)

    def get_processing_profile_pin(self, *, document_id: str, processing_generation: int) -> ProcessingProfilePin:
        return self._batches.get_processing_profile_pin(document_id=document_id, processing_generation=processing_generation)

    def chunks_for_batch(self, job_id: str, batch_id: str) -> tuple[ProcessingJobView, list[dict[str, Any]]]:
        return self._batches.chunks_for_batch(job_id, batch_id)

    def index_batch_execution(self, job_id: str, batch_id: str, *, expected_attempt: int) -> AbstractContextManager[ProcessingJobView | None]:
        return self._batches.index_batch_execution(job_id, batch_id, expected_attempt=expected_attempt)

    def set_embedding_profile(self, job_id: str, index_generation_id: str, profile: dict[str, Any], *, expected_attempt: int) -> bool:
        return self._batches.set_embedding_profile(job_id, index_generation_id, profile, expected_attempt=expected_attempt)

    def stage_reindex_batch(self, job_id: str, batch_id: str, *, expected_attempt: int, batch_size: int = 100) -> bool:
        return self._batches.stage_reindex_batch(job_id, batch_id, expected_attempt=expected_attempt, batch_size=batch_size)

    def get_batch_claim(self, batch_id: str) -> ProcessingBatchClaimRecord | None:
        return self._batches.get_batch_claim(batch_id)

    def list_batch_claims(self, *, job_id: str, limit: int = 100) -> list[ProcessingBatchClaimRecord]:
        return self._batches.list_batch_claims(job_id=job_id, limit=limit)

    def get_checkpoint(self, *, job_id: str, unit_kind: str, unit_start: int, unit_end: int) -> ProcessingCheckpointRecord | None:
        return self._batches.get_checkpoint(job_id=job_id, unit_kind=unit_kind, unit_start=unit_start, unit_end=unit_end)

    def list_checkpoints(self, *, job_id: str, limit: int = 200) -> list[ProcessingCheckpointRecord]:
        return self._batches.list_checkpoints(job_id=job_id, limit=limit)

    def claim_processing_batch(self, *, job_id: str, batch_id: str, expected_attempt: int, expected_fence: int, unit_kind: str, unit_start: int, unit_end: int, lease_seconds: int = 300) -> ProcessingBatchClaimRecord | None:
        return self._batches.claim_processing_batch(job_id=job_id, batch_id=batch_id, expected_attempt=expected_attempt, expected_fence=expected_fence, unit_kind=unit_kind, unit_start=unit_start, unit_end=unit_end, lease_seconds=lease_seconds)

    def renew_batch_claim(self, *, job_id: str, batch_id: str, attempt: int, claim_fence: int, claim_token: str, unit_kind: str = "page", lease_seconds: int = 300) -> bool:
        return self._batches.renew_batch_claim(job_id=job_id, batch_id=batch_id, attempt=attempt, claim_fence=claim_fence, claim_token=claim_token, unit_kind=unit_kind, lease_seconds=lease_seconds)

    def checkpoint_for_batch(self, job_id: str, batch_id: str) -> dict[str, Any] | None:
        return self._batches.checkpoint_for_batch(job_id, batch_id)

    def commit_checkpoint(self, *, job_id: str, attempt: int, claim_fence: int, claim_token: str, batch_id: str, unit_start: int, unit_end: int, input_fingerprint: str, output_digest: str, evidence_rows: list[dict[str, Any]], chunk_rows: list[dict[str, Any]], page_artifact_rows: list[dict[str, Any]] | None = None, preview_count: int = 0, warning_codes: list[str] | None = None) -> bool:
        return self._batches.commit_checkpoint(job_id=job_id, attempt=attempt, claim_fence=claim_fence, claim_token=claim_token, batch_id=batch_id, unit_start=unit_start, unit_end=unit_end, input_fingerprint=input_fingerprint, output_digest=output_digest, evidence_rows=evidence_rows, chunk_rows=chunk_rows, page_artifact_rows=page_artifact_rows, preview_count=preview_count, warning_codes=warning_codes)

    def enqueue_index_batch(self, job_id: str, batch_id: str, *, expected_attempt: int) -> bool:
        return self._batches.enqueue_index_batch(job_id, batch_id, expected_attempt=expected_attempt)

    def mark_batch_indexed(self, *, job_id: str, batch_id: str, mappings: list[dict[str, Any]], expected_attempt: int) -> bool:
        return self._batches.mark_batch_indexed(job_id=job_id, batch_id=batch_id, mappings=mappings, expected_attempt=expected_attempt)

    def load_publication_manifest(self, job_id: str, *, expected_attempt: int) -> IndexPublicationManifest | None:
        return self._publication.load_publication_manifest(job_id, expected_attempt=expected_attempt)

    def publish_job(self, job_id: str, *, expected_attempt: int, verified_manifest_digest: str | None = None) -> bool:
        return self._publication.publish_job(job_id, expected_attempt=expected_attempt, verified_manifest_digest=verified_manifest_digest)

    def retired_vector_points(self, *, limit: int = 100) -> dict[str, list[str]]:
        return self._publication.retired_vector_points(limit=limit)

    def delete_retired_vector_points(self, points: Mapping[str, list[str]]) -> None:
        self._publication.delete_retired_vector_points(points)

    def cleanup_retired_generations(self, *, limit: int = 10) -> None:
        self._publication.cleanup_retired_generations(limit=limit)


__all__ = ["PostgresDocumentProcessingAdapter"]
