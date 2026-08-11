from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4
from atlas_production.modules.document_intake.records import DocumentRecord
from atlas_production.modules.identity_access.records import UserRecord

from .job_contracts import (
    ProcessingControlDenied,
    ProcessingJobPayloadV1,
    ProcessingPublicStatusV1,
    ProcessingJobsFailureV1,
    ProcessingJobsOutcomeV1,
)
from .job_ports import (
    ProcessingJobsAuthorization,
    ProcessingJobsBackend,
    ProcessingJobsDocumentLibrary,
)
from .job_records import ProcessingJobRecord, ProcessingProfilePin


PUBLIC_PROCESSING_STATUSES = frozenset(
    {
        "queued",
        "processing",
        "waiting_retry",
        "publishing",
        "ready",
        "ready_with_warnings",
        "failed",
        "cancelled",
    }
)


@dataclass(frozen=True, slots=True)
class ProcessingJobsApplication:
    """Own processing status, control, and reindex use cases."""

    backend: ProcessingJobsBackend
    document_library: ProcessingJobsDocumentLibrary
    authorization: ProcessingJobsAuthorization
    dispatch: Callable[[], None]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    new_id: Callable[[], str] = lambda: uuid4().hex

    def get(
        self,
        *,
        actor: UserRecord,
        session_token: str,
        job_id: str,
    ) -> ProcessingJobsOutcomeV1:
        item = self.backend.get_document_job_request_projection(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token,
            job_id=job_id,
        )
        if item is None or not self.authorization.can_read(item, actor):
            return self._failure("not_found", "processing.job_was_not_found", 404)
        return ProcessingJobsOutcomeV1(
            self._serialize(
                item.job,
                item.document,
                profile_pin=item.profile_pin,
                can_control=self.authorization.can_control(item, actor),
            )
        )

    def list(
        self,
        *,
        actor: UserRecord,
        session_token: str,
        document_id: str | None,
        profile_id: str | None,
        profile_revision: int | None,
        status: str | None,
    ) -> ProcessingJobsOutcomeV1:
        projections = self.backend.list_document_job_request_projections(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token,
            document_id=document_id,
        )
        jobs: list[ProcessingJobPayloadV1] = []
        for item in projections:
            if not self.authorization.can_read(item, actor):
                continue
            projected = self._serialize(
                item.job,
                item.document,
                profile_pin=item.profile_pin,
                can_control=self.authorization.can_control(item, actor),
            )
            if status is not None and projected["status"] != status:
                continue
            if profile_id is not None and projected["profile_id"] != profile_id:
                continue
            if (
                profile_revision is not None
                and projected["profile_revision"] != profile_revision
            ):
                continue
            jobs.append(projected)
        return ProcessingJobsOutcomeV1({"jobs": jobs})

    def control(
        self,
        *,
        actor: UserRecord,
        session_token: str,
        job_id: str,
        retry: bool,
    ) -> ProcessingJobsOutcomeV1:
        command = (
            self.backend.retry_processing_job_request
            if retry
            else self.backend.stop_processing_job_request
        )
        try:
            result = command(
                job_id=job_id,
                presented_browser_session_token=session_token,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
            )
        except ProcessingControlDenied as exc:
            return self._failure(
                "access_denied",
                "processing.only_the_uploader_or_scope_admin_can_control_this_job",
                403,
                audit_event_ref=exc.audit_event.event_id,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "processing_job_not_found":
                return self._failure("not_found", "processing.job_was_not_found", 404)
            message = (
                "processing.only_a_failed_or_stopped_job_can_start_a_new_attempt"
                if retry
                else "processing.only_an_active_processing_job_can_be_stopped"
            )
            return self._failure(code, message, 409)
        item = self.backend.get_document_job_request_projection(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token,
            job_id=job_id,
        )
        if item is None:
            return self._failure("not_found", "processing.job_was_not_found", 404)
        if retry:
            self.dispatch()
        payload = self._serialize(
            result.job,
            item.document,
            profile_pin=item.profile_pin,
            can_control=True,
        )
        payload["audit_event_ref"] = result.audit_event.event_id
        return ProcessingJobsOutcomeV1(payload, status_code=202 if retry else 200)

    def reindex(
        self,
        *,
        actor: UserRecord,
        session_token: str,
        document_id: str,
        idempotency_key: str | None,
    ) -> ProcessingJobsOutcomeV1:
        if actor.system_role != "admin":
            return self._failure(
                "access_denied",
                "permission.admin_permission_is_required",
                403,
            )
        projection = self.document_library.document_library_projection(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            presented_browser_session_token=session_token,
            document_id=document_id,
        )
        if (
            len(projection.items) != 1
            or projection.items[0].document.lifecycle_status != "active"
        ):
            return self._failure("not_found", "document.was_not_found", 404)
        document = projection.items[0].document
        if document.active_processing_generation <= 0:
            return self._failure(
                "document_not_published",
                "document.has_no_published_processing_generation",
                409,
            )
        version_id = self.document_library.processing_document_version_id(document_id)
        if version_id is None:
            return self._failure(
                "source_artifact_unavailable",
                "artifact.source_document_is_unavailable_this_file_cannot_be_processed",
                422,
            )
        job = self.backend.create_processing_job(
            document_id=document_id,
            document_version_id=version_id,
            job_kind="reindex",
            idempotency_scope="document_reindex",
            idempotency_key=(
                idempotency_key or f"reindex-{document_id}-{self.new_id()}"
            ),
            created_by=actor.actor_id,
        )
        self.dispatch()
        return ProcessingJobsOutcomeV1(
            self._serialize(job, document, profile_pin=None, can_control=True),
            status_code=202,
        )

    def _serialize(
        self,
        job: ProcessingJobRecord,
        document: DocumentRecord,
        *,
        profile_pin: ProcessingProfilePin | None,
        can_control: bool,
    ) -> ProcessingJobPayloadV1:
        status = self._public_status(job, document)
        is_current = document.processing_job_id == job.job_id
        end = (
            job.updated_at
            if job.status in {"succeeded", "failed", "cancelled"}
            else self.now()
        )
        payload: ProcessingJobPayloadV1 = {
            "document_id": job.document_id,
            "document_format": document.document_format,
            "profile_id": profile_pin.profile_id if profile_pin else None,
            "profile_revision": profile_pin.profile_revision if profile_pin else None,
            "current_stage": job.stage,
            "warning_codes": list(document.warning_codes) if is_current else [],
            "failure_code": job.failure_code if status == "failed" else None,
            "job_id": job.job_id,
            "status": status,
            "status_url": f"/api/v1/processing/jobs/{job.job_id}",
            "retry_available": (
                is_current and can_control and status in {"failed", "cancelled"}
            ),
            "cancel_available": (
                is_current
                and can_control
                and status in {"queued", "processing", "waiting_retry", "publishing"}
            ),
            "review_available": True,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "progress_unit": job.progress_unit,
            "elapsed_seconds": max(
                0, int((end - job.attempt_started_at).total_seconds())
            ),
            "attempt_started_at": job.attempt_started_at.isoformat(),
            "is_current": is_current,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }
        assert payload["status"] in PUBLIC_PROCESSING_STATUSES
        return payload

    @staticmethod
    def _public_status(
        job: ProcessingJobRecord, document: DocumentRecord
    ) -> ProcessingPublicStatusV1:
        if job.status == "queued":
            return "queued"
        if job.status == "retry_wait":
            return "waiting_retry"
        if job.status == "running" and job.stage == "publishing":
            return "publishing"
        if job.status == "running":
            return "processing"
        if job.status == "succeeded":
            if document.processing_job_id == job.job_id and document.warning_codes:
                return "ready_with_warnings"
            return "ready"
        if job.status == "cancelled":
            return "cancelled"
        return "failed"

    @staticmethod
    def _failure(
        error_code: str,
        message_code: str,
        status_code: int,
        *,
        audit_event_ref: str | None = None,
    ) -> ProcessingJobsOutcomeV1:
        return ProcessingJobsOutcomeV1(
            failure=ProcessingJobsFailureV1(
                error_code,
                message_code,
                status_code,
                audit_event_ref,
            )
        )
