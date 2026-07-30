from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas_production.shared.user_messages import MessageReferenceModel


class DocumentTagRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_type: Literal["project", "team"]
    tag_id: str


class DocumentTagSummary(BaseModel):
    tag_type: Literal["project", "team"]
    tag_id: str
    label: str


class DocumentLibrarySummary(BaseModel):
    document_id: str
    title: str
    description: str | None = None
    intake_status: str
    document_format: str
    profile_id: str | None = None
    profile_revision: int | None = None
    current_stage: str | None = None
    warning_codes: list[str] = []
    failure_code: str | None = None
    job_id: str | None = None
    lifecycle_status: Literal["active", "disabled", "restoring"]
    uploader_actor_id: str | None = None
    scope_type: Literal["team", "project"]
    scope_id: str
    direct_tags: list[DocumentTagSummary]
    allow_member_download: bool
    download_available: bool
    source_filename: str | None = None
    source_byte_size: int | None = None
    content_type: str | None = None
    raw_sha256: str | None = None
    uploaded_at: str | None = None
    disabled_at: str | None = None
    restored_at: str | None = None
    evidence_count: int


class DocumentLibraryListResult(BaseModel):
    documents: list[DocumentLibrarySummary]


class DocumentLibraryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    allow_member_download: bool | None = None
    idempotency_key: str


class DocumentLibraryMutationResult(MessageReferenceModel):
    request_id: str
    status: Literal["accepted", "applied", "rejected", "access_denied"]
    target_ref: str | None
    audit_event_ref: str
    document: DocumentLibrarySummary | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    status_url: str | None = None


class KnowledgeScopeSummary(BaseModel):
    scope_type: Literal["team", "project"]
    scope_id: str
    scope_label: str


class KnowledgeDocumentSummary(BaseModel):
    document_id: str
    title: str
    description: str | None = None
    document_format: str
    authorized_scopes: list[KnowledgeScopeSummary]
    source_filename: str | None = None
    source_byte_size: int | None = None
    uploaded_at: str | None = None
    download_available: bool


class KnowledgeDocumentListResult(BaseModel):
    documents: list[KnowledgeDocumentSummary]


class WorkspaceTagScopeResult(BaseModel):
    tags: list[DocumentTagSummary]
