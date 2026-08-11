from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from atlas_production.modules.identity_access.records import UserRecord

if TYPE_CHECKING:
    from atlas_production.modules.document_intake.public import (
        DocumentLibraryRequestProjection,
    )

from .job_records import (
    DocumentJobRequestAuthorityProjection,
    ProcessingControlResult,
    ProcessingJobRecord,
)


class ProcessingJobsBackend(Protocol):
    def get_document_job_request_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        job_id: str,
    ) -> DocumentJobRequestAuthorityProjection | None: ...

    def list_document_job_request_projections(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[DocumentJobRequestAuthorityProjection, ...]: ...

    def retry_processing_job_request(
        self,
        *,
        job_id: str,
        presented_browser_session_token: str,
        actor_type: str,
        actor_id: str,
    ) -> ProcessingControlResult: ...

    def stop_processing_job_request(
        self,
        *,
        job_id: str,
        presented_browser_session_token: str,
        actor_type: str,
        actor_id: str,
    ) -> ProcessingControlResult: ...

    def create_processing_job(
        self,
        *,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        progress_total: int | None = None,
    ) -> ProcessingJobRecord: ...


class ProcessingJobsDocumentLibrary(Protocol):
    def document_library_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        include_events: bool = False,
    ) -> DocumentLibraryRequestProjection: ...

    def processing_document_version_id(self, document_id: str) -> str | None: ...


class ProcessingJobsAuthorization(Protocol):
    def can_read(
        self,
        projection: DocumentJobRequestAuthorityProjection,
        actor: UserRecord,
    ) -> bool: ...

    def can_control(
        self,
        projection: DocumentJobRequestAuthorityProjection,
        actor: UserRecord,
    ) -> bool: ...
