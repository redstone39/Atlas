from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


class AtlasProcessingJobRow(OrmBase):
    __tablename__ = "atlas_processing_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_kind: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_documents.document_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    processing_identity_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
    )
    processing_revision_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
    )
    processing_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    index_generation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued", index=True)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_unit: Mapped[str] = mapped_column(String, nullable=False, default="page")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    idempotency_scope: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_scope", "idempotency_key", name="ux_atlas_processing_job_idempotency"),
        CheckConstraint("job_kind IN ('ingest','reprocess','reindex','finalize')", name="ck_atlas_processing_job_kind"),
        CheckConstraint("stage IN ('queued','parsing','indexing','publishing','completed')", name="ck_atlas_processing_job_stage"),
        CheckConstraint("status IN ('queued','running','retry_wait','succeeded','failed','cancelled')", name="ck_atlas_processing_job_status"),
        CheckConstraint("progress_unit IN ('page','batch')", name="ck_atlas_processing_job_progress_unit"),
        CheckConstraint("progress_current >= 0 AND (progress_total IS NULL OR progress_total >= progress_current)", name="ck_atlas_processing_job_progress"),
        CheckConstraint("attempt > 0 AND fence >= 0", name="ck_atlas_processing_job_attempt_fence"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_atlas_processing_job_fingerprint"),
        ForeignKeyConstraint(
            ["processing_revision_id", "processing_identity_id"],
            [
                "atlas_processing_revisions.processing_revision_id",
                "atlas_processing_revisions.processing_identity_id",
            ],
            name="fk_atlas_processing_job_revision_identity",
        ),
        Index(
            "ux_atlas_processing_job_active_identity",
            "processing_identity_id",
            unique=True,
            postgresql_where=text(
                "processing_identity_id IS NOT NULL "
                "AND status IN ('queued','running','retry_wait')"
            ),
        ),
    )


class AtlasProcessingRequestSnapshotRow(OrmBase):
    """Executable configuration owned by one durable processing request/job."""

    __tablename__ = "atlas_processing_request_snapshots"

    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("atlas_processing_jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    processing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "processing_generation > 0 AND accepted_attempt > 0",
            name="ck_atlas_processing_request_snapshot_identity",
        ),
    )


class AtlasProcessingBatchClaimRow(OrmBase):
    __tablename__ = "atlas_processing_batch_claims"

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("atlas_processing_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    unit_kind: Mapped[str] = mapped_column(String, nullable=False)
    unit_start: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_end: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "attempt > 0 AND unit_kind IN ('page','batch') "
            "AND unit_start > 0 AND unit_end >= unit_start",
            name="ck_atlas_processing_batch_claim",
        ),
    )


class AtlasProcessingCheckpointRow(OrmBase):
    __tablename__ = "atlas_processing_checkpoints"

    job_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_processing_jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    unit_kind: Mapped[str] = mapped_column(String, primary_key=True)
    unit_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_end: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    claim_token: Mapped[str] = mapped_column(String, nullable=False)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    output_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("unit_kind IN ('page','batch') AND unit_start > 0 AND unit_end >= unit_start", name="ck_atlas_processing_checkpoint_range"),
        CheckConstraint("evidence_count >= 0 AND chunk_count >= 0 AND preview_count >= 0", name="ck_atlas_processing_checkpoint_counts"),
    )


class AtlasProcessingGenerationRow(OrmBase):
    __tablename__ = "atlas_processing_generations"

    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_generation: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expected_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("processing_generation > 0", name="ck_atlas_processing_generation_positive"),
        CheckConstraint("status IN ('building','active','retired','failed')", name="ck_atlas_processing_generation_status"),
        Index("ux_atlas_active_processing_generation", "document_id", unique=True, postgresql_where=text("status = 'active'")),
    )


class AtlasIndexGenerationRow(OrmBase):
    __tablename__ = "atlas_index_generations"

    index_generation_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_documents.document_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    processing_revision_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("atlas_processing_revisions.processing_revision_id"),
        nullable=True,
        index=True,
    )
    source_processing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    embedding_profile_id: Mapped[str] = mapped_column(String, nullable=False)
    embedding_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expected_point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_fts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_fts_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_index_generation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "processing_revision_id",
            name="uq_atlas_index_generation_processing_revision",
        ),
        CheckConstraint("status IN ('building','active','retired','failed')", name="ck_atlas_index_generation_status"),
        CheckConstraint("jsonb_typeof(embedding_profile) = 'object' AND octet_length(embedding_profile::text) <= 8192", name="ck_atlas_embedding_profile_bound"),
        Index("ux_atlas_active_index_generation", "document_id", unique=True, postgresql_where=text("status = 'active'")),
    )


class AtlasProcessingGenerationRetentionRow(OrmBase):
    """Processing-owned lifetime claim for exact generations used by one turn."""

    __tablename__ = "atlas_processing_generation_retentions"

    retention_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    release_idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("resource_count >= 0", name="ck_atlas_processing_retention_count"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_processing_retention_digest"),
        CheckConstraint("status IN ('active','released')", name="ck_atlas_processing_retention_status"),
    )


class AtlasProcessingGenerationRetentionEntryRow(OrmBase):
    """One immutable generation identity protected by its owner-local claim."""

    __tablename__ = "atlas_processing_generation_retention_entries"

    retention_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_processing_generation_retentions.retention_ref", ondelete="CASCADE"),
        primary_key=True,
    )
    index_generation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("atlas_index_generations.index_generation_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    document_version_id: Mapped[str] = mapped_column(String, nullable=False)
    processing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("processing_generation > 0", name="ck_atlas_processing_retention_generation"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_processing_retention_manifest"),
    )


class AtlasSearchChunkRow(OrmBase):
    __tablename__ = "atlas_search_chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    document_version_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    processing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    processing_revision_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("atlas_processing_revisions.processing_revision_id"),
        nullable=True,
        index=True,
    )
    index_generation_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_index_generations.index_generation_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    evidence_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    segment_id: Mapped[str] = mapped_column(String, nullable=False)
    window_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("index_generation_id", "segment_id", "window_ordinal", name="ux_atlas_search_chunk_window"),
        CheckConstraint("window_ordinal >= 0", name="ck_atlas_search_chunk_window"),
        CheckConstraint("status IN ('staged','active','retired')", name="ck_atlas_search_chunk_status"),
        CheckConstraint("octet_length(locator::text) <= 8192", name="ck_atlas_search_chunk_locator_bound"),
        Index("ix_atlas_search_chunks_fts", "search_vector", postgresql_using="gin"),
    )


class AtlasVectorPointMappingRow(OrmBase):
    __tablename__ = "atlas_vector_point_mappings"

    index_generation_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_index_generations.index_generation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    point_id: Mapped[str] = mapped_column(String, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_search_chunks.chunk_id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    vector_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasTaskOutboxRow(OrmBase):
    __tablename__ = "atlas_task_outbox"

    outbox_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_name: Mapped[str] = mapped_column(String, nullable=False)
    queue_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    celery_task_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    claim_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("queue_name IN ('atlas.dispatch','atlas.processing','atlas.indexing','atlas.maintenance')", name="ck_atlas_task_outbox_queue"),
        CheckConstraint("status IN ('pending','dispatching','dispatched','cancelled')", name="ck_atlas_task_outbox_status"),
        CheckConstraint("jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 4096", name="ck_atlas_task_outbox_payload_bound"),
        Index("ix_atlas_task_outbox_claim", "status", "available_at", "claim_expires_at"),
    )
