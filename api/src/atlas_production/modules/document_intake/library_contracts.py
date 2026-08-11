from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Literal, TypeAlias

from atlas_production.modules.audit.public import AuditEventList
from atlas_production.shared.public import AdminActionResult

from .api_models import DocumentLibraryListResult, DocumentLibraryMutationResult

@dataclass(frozen=True, slots=True)
class DocumentLibraryUploadCommand:
    idempotency_key: str | None
    scope_type: str | None
    scope_id: str | None
    tag_refs: str | None
    document_id: str | None
    allow_member_download: str | None
    description: str | None
    filename: str | None
    content_type: str | None
    file: BinaryIO | None


DocumentLibraryResultV1: TypeAlias = (
    DocumentLibraryListResult
    | DocumentLibraryMutationResult
    | AdminActionResult
    | AuditEventList
)



@dataclass(frozen=True, slots=True)
class DocumentLibraryFailureV1:
    kind: Literal["error", "admin_rejected"]
    status_code: int
    message_code: str
    error_code: str | None = None
    request_id: str | None = None
    audit_event_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentLibraryOutcomeV1:
    value: DocumentLibraryResultV1 | None = None
    status_code: int = 200
    failure: DocumentLibraryFailureV1 | None = None


@dataclass(frozen=True, slots=True)
class DocumentLibraryExceptionTypes:
    upload_access_denied: type[Exception]
    upload_unauthenticated: type[Exception]
    upload_replay_conflict: type[Exception]
    lifecycle_denied: type[Exception]
    currentness_conflict: type[Exception]
