from dataclasses import asdict
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.shared.public import content_digest, utc_now_iso

from .base import OrmBase
from .payload_policy import serialize_typed_dataclass


DOCUMENT_VERSION_FIELDS = (
    "document_version_id", "document_id", "title", "source_kind",
    "document_format", "source_digest", "content_digest", "created_at", "status",
    "supersedes_version_id", "original_artifact_id", "content_type",
)


def _document_version_payload(record: DocumentVersionRecord) -> dict[str, Any]:
    return serialize_typed_dataclass(
        record,
        family="document version metadata",
        allowed_fields=DOCUMENT_VERSION_FIELDS,
    )

class AtlasDocumentRow(OrmBase):
    __tablename__ = "atlas_documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    searchable_projection: Mapped[str] = mapped_column(String(4096), nullable=False)
    source_digest: Mapped[str] = mapped_column(String, nullable=False)
    intake_status: Mapped[str] = mapped_column(String, nullable=False)
    source_kind: Mapped[str] = mapped_column(String, nullable=False, default="inline_text")
    document_format: Mapped[str] = mapped_column(String, nullable=False, default="txt")
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    source_byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploader_actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    allow_member_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_download_restricted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lifecycle_status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    original_artifact_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("atlas_artifacts.artifact_id"), nullable=True
    )
    raw_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[str | None] = mapped_column(String, nullable=True)
    disabled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    restored_at: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_lifecycle_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_processing_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_index_generation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    processing_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_profile_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    warning_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    processing_identity_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("atlas_processing_identities.processing_identity_id"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(searchable_projection) <= 4096",
            name="ck_atlas_document_projection_bound",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active','disabled','restoring')",
            name="ck_atlas_document_lifecycle_status",
        ),
        CheckConstraint(
            "resource_lifecycle_epoch >= 0 AND active_processing_generation >= 0",
            name="ck_atlas_document_epochs",
        ),
        CheckConstraint(
            "document_format IN ('pdf','docx','pptx','xlsx','txt','csv','doc','ppt','xls')",
            name="ck_atlas_document_format",
        ),
        CheckConstraint(
            "(processing_profile_id IS NULL AND processing_profile_revision IS NULL) "
            "OR (processing_profile_id IS NOT NULL AND char_length(processing_profile_id) > 0 "
            "AND processing_profile_revision > 0)",
            name="ck_atlas_document_processing_profile_pair",
        ),
        CheckConstraint(
            "current_stage IS NULL OR current_stage IN "
            "('queued','parsing','indexing','publishing','completed','failed')",
            name="ck_atlas_document_current_stage",
        ),
        CheckConstraint(
            "jsonb_typeof(warning_codes) = 'array'",
            name="ck_atlas_document_warning_codes_array",
        ),
    )


class AtlasDocumentTagRow(OrmBase):
    __tablename__ = "atlas_document_tags"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    tag_type: Mapped[str] = mapped_column(String, primary_key=True)
    tag_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasDocumentVersionRow(OrmBase):
    __tablename__ = "atlas_document_versions"

    document_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


def document_tag_key(document_id: str, tag_type: str, tag_id: str) -> str:
    return f"{document_id}:{tag_type}:{tag_id}"


def _document_row(document: DocumentRecord) -> AtlasDocumentRow:
    return AtlasDocumentRow(
        document_id=document.document_id,
        title=document.title,
        description=document.description,
        searchable_projection=document.searchable_projection,
        source_digest=document.source_digest,
        intake_status=document.intake_status,
        source_kind=document.source_kind,
        document_format=document.document_format,
        content_type=document.content_type,
        source_filename=document.source_filename,
        source_byte_size=document.source_byte_size,
        uploader_actor_id=document.uploader_actor_id,
        scope_type=document.scope_type,
        scope_id=document.scope_id,
        allow_member_download=document.allow_member_download,
        source_download_restricted=document.source_download_restricted,
        lifecycle_status=document.lifecycle_status,
        original_artifact_id=document.original_artifact_id,
        raw_sha256=document.raw_sha256,
        uploaded_at=document.uploaded_at,
        disabled_at=document.disabled_at,
        restored_at=document.restored_at,
        resource_lifecycle_epoch=document.resource_lifecycle_epoch,
        active_processing_generation=document.active_processing_generation,
        active_index_generation_id=getattr(
            document, "active_index_generation_id", None
        ),
        processing_profile_id=document.processing_profile_id,
        processing_profile_revision=document.processing_profile_revision,
        current_stage=document.current_stage,
        warning_codes=list(document.warning_codes),
        failure_code=document.failure_code,
        processing_job_id=document.processing_job_id,
        processing_identity_id=document.processing_identity_id,
    )
