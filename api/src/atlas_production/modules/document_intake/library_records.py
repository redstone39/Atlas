from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from atlas_production.modules.artifact_storage.records import ArtifactRecord
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    UserRecord,
)
from atlas_production.shared.public import AuditEventRecord

from .api_models import DocumentTagRef
from .records import DocumentRecord, DocumentTagRecord, DocumentVersionRecord

if TYPE_CHECKING:
    from atlas_production.modules.processing_pipeline.public import (
        DocumentLifecycleProcessingAcceptance,
        ProcessingJobAuthorizationState,
        ProcessingJobRecord,
        VerifiedDocumentRestoreSet,
    )


@dataclass(frozen=True, slots=True)
class DocumentLibraryItemProjection:
    """All mutable owner facts used to render one library item in one request."""

    document: DocumentRecord
    tags: tuple[DocumentTagRecord, ...]
    scope_labels: tuple[tuple[str, str, str], ...]
    ready_evidence_count: int
    original_artifact_available: bool
    can_view: bool
    can_administer: bool
    can_edit: bool
    can_view_logs: bool
    download_available: bool
    events: tuple[AuditEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentLibraryRequestProjection:
    authenticated_actor: UserRecord
    items: tuple[DocumentLibraryItemProjection, ...]
    authorization_state: ProcessingJobAuthorizationState


@dataclass(frozen=True, slots=True)
class RequestedDocumentScopeProjection:
    scope_type: str
    scope_id: str
    exists: bool
    active: bool
    label: str | None
    can_upload: bool
    denial_audit_event: AuditEventRecord | None = None


@dataclass(frozen=True, slots=True)
class DocumentLifecycleRequestInput:
    presented_browser_session_token: str
    actor_type: str
    actor_id: str
    expected_document: DocumentRecord
    document: DocumentRecord
    tags: tuple[DocumentTagRecord, ...]
    audit_events: tuple[AuditEventRecord, ...]
    denial_audit_event: AuditEventRecord
    versions: tuple[DocumentVersionRecord, ...] = ()
    processing_acceptance: DocumentLifecycleProcessingAcceptance | None = None
    restore_verification: VerifiedDocumentRestoreSet | None = None


@dataclass(frozen=True, slots=True)
class PublishedDocumentUpload:
    version: DocumentVersionRecord
    job: ProcessingJobRecord | None
    audit: AuditEventRecord


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    artifact: ArtifactRecord
    publication: PublishedDocumentUpload
    replayed: bool = False


class DocumentUploadAccessDenied(PermissionError):
    def __init__(
        self,
        decision: AccessDecisionRecord,
        audit_event: AuditEventRecord,
    ) -> None:
        super().__init__("document upload is not authorized")
        self.decision = decision
        self.audit_event = audit_event


class DocumentUploadUnauthenticated(PermissionError):
    """The presented browser credential no longer names the expected actor."""


class DocumentUploadReplayConflict(ValueError):
    """A completed canonical result cannot be returned for this request."""
