from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol
from atlas_production.shared.public import AuditEventRecord

if TYPE_CHECKING:
    from atlas_production.modules.processing_pipeline.public import (
        DocumentLifecycleProcessingAcceptance,
        ProcessingExecutionSnapshot,
        ProcessingJobRecord,
        VerifiedDocumentRestoreSet,
    )

from .api_models import DocumentTagRef
from .library_records import (
    DocumentLibraryRequestProjection,
    DocumentLifecycleRequestInput,
    DocumentUploadResult,
    RequestedDocumentScopeProjection,
)
from .records import DocumentRecord, DocumentTagRecord, DocumentVersionRecord


class DocumentLifecycleFacade(Protocol):
    def patch_document(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None: ...

    def disable_document(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None: ...

    def begin_restore(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None: ...

    def finish_restore(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None: ...

    def refresh_or_reindex(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord: ...


class DocumentLibraryIntakeBackend(Protocol):
    def document_library_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        include_events: bool = False,
    ) -> DocumentLibraryRequestProjection: ...

    def requested_scope_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        scope_type: str,
        scope_id: str,
        record_upload_denial: bool = False,
    ) -> RequestedDocumentScopeProjection: ...

    def processing_document_version_id(self, document_id: str) -> str | None: ...

    def journey_facade(self) -> DocumentLifecycleFacade: ...


class DocumentLibraryProcessingBackend(Protocol):
    def get_job(self, job_id: str) -> ProcessingJobRecord | None: ...

    def capture_processing_execution(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        created_by: str | None,
        progress_total: int | None = None,
    ) -> ProcessingExecutionSnapshot: ...


class DocumentLibraryUploadBackend(Protocol):
    def upload(
        self,
        *,
        chunks: Iterable[bytes],
        request_fingerprint: str,
        artifact_class: str,
        logical_identity: str,
        content_type: str,
        document: DocumentRecord,
        tag_refs: list[DocumentTagRef],
        authorization_bindings: tuple[tuple[str, str], ...],
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        audit_event_type: str,
        audit_message_code: str,
        audit_metadata: dict[str, object],
        presented_browser_session_token: str,
        actor_type: str = "user",
        progress_total: int | None = None,
    ) -> DocumentUploadResult: ...


class DocumentRestoreProofProvider(Protocol):
    def verify(self, expected_document: DocumentRecord) -> VerifiedDocumentRestoreSet: ...


class LifecycleRequestFactory(Protocol):
    def __call__(
        self,
        *,
        presented_browser_session_token: str,
        actor_type: str,
        actor_id: str,
        expected_document: DocumentRecord,
        document: DocumentRecord,
        tags: tuple[DocumentTagRecord, ...],
        audit_events: tuple[AuditEventRecord, ...],
        denial_audit_event: AuditEventRecord,
        versions: tuple[DocumentVersionRecord, ...] = (),
        processing_acceptance: DocumentLifecycleProcessingAcceptance | None = None,
        restore_verification: VerifiedDocumentRestoreSet | None = None,
    ) -> DocumentLifecycleRequestInput: ...


class ProcessingAcceptanceFactory(Protocol):
    def __call__(
        self,
        media_type: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        execution_snapshot: ProcessingExecutionSnapshot,
        progress_total: int | None = None,
    ) -> DocumentLifecycleProcessingAcceptance: ...
