from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentRecord:
    document_id: str
    title: str
    source_digest: str
    searchable_projection: str = ""
    intake_status: str = "registered"
    source_kind: str = "inline_text"
    document_format: str = "txt"
    content_type: str | None = None
    source_filename: str | None = None
    source_byte_size: int | None = None
    description: str | None = None
    uploader_actor_id: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    allow_member_download: bool = False
    source_download_restricted: bool = False
    lifecycle_status: str = "active"
    original_artifact_id: str | None = None
    raw_sha256: str | None = None
    uploaded_at: str | None = None
    disabled_at: str | None = None
    restored_at: str | None = None
    resource_lifecycle_epoch: int = 0
    active_processing_generation: int = 0
    active_index_generation_id: str | None = None
    processing_profile_id: str | None = None
    processing_profile_revision: int | None = None
    current_stage: str | None = None
    warning_codes: list[str] = field(default_factory=list)
    failure_code: str | None = None
    processing_job_id: str | None = None
    processing_identity_id: str | None = None


@dataclass
class DocumentVersionRecord:
    document_version_id: str
    document_id: str
    title: str
    source_kind: str
    document_format: str
    source_digest: str
    content_digest: str
    created_at: str
    status: str = "active"
    supersedes_version_id: str | None = None
    original_artifact_id: str | None = None
    content_type: str | None = None


@dataclass
class DocumentTagRecord:
    document_id: str
    tag_type: str
    tag_id: str
    created_at: str
