"""Notes-owned durable metadata, immutable journal, savepoints, and settings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase

OWNER = "notes"


class AtlasNoteCategoryRow(OrmBase):
    __tablename__ = "atlas_note_categories"

    category_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trashed_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("scope_type IN ('project','team')", name="ck_atlas_note_category_scope_type"),
        CheckConstraint("lifecycle_status IN ('active','trashed')", name="ck_atlas_note_category_lifecycle"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="ck_atlas_note_category_name"),
        CheckConstraint("metadata_revision >= 1", name="ck_atlas_note_category_metadata_revision"),
        CheckConstraint("(lifecycle_status = 'active' AND trashed_actor_id IS NULL AND trashed_at IS NULL) OR (lifecycle_status = 'trashed' AND trashed_actor_id IS NOT NULL AND trashed_at IS NOT NULL)", name="ck_atlas_note_category_trash_metadata"),
        UniqueConstraint("category_id", "scope_type", "scope_id", name="uq_atlas_note_category_exact_scope"),
        UniqueConstraint("scope_type", "scope_id", "name", name="uq_atlas_note_category_scope_name"),
        Index("ix_atlas_note_category_scope_lifecycle", "scope_type", "scope_id", "lifecycle_status"),
    )


class AtlasNoteRow(OrmBase):
    __tablename__ = "atlas_notes"

    note_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_update_head: Mapped[int] = mapped_column(BigInteger, nullable=False)
    savepoint_head: Mapped[int] = mapped_column(BigInteger, nullable=False)
    collaboration_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trashed_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["category_id", "scope_type", "scope_id"], ["atlas_note_categories.category_id", "atlas_note_categories.scope_type", "atlas_note_categories.scope_id"], name="fk_atlas_note_exact_scope_category", deferrable=True, initially="DEFERRED"),
        CheckConstraint("scope_type IN ('project','team')", name="ck_atlas_note_scope_type"),
        CheckConstraint("lifecycle_status IN ('active','trashed')", name="ck_atlas_note_lifecycle"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 500", name="ck_atlas_note_title"),
        CheckConstraint("metadata_revision >= 1 AND accepted_update_head >= 1 AND savepoint_head >= 1 AND collaboration_epoch >= 1", name="ck_atlas_note_heads"),
        CheckConstraint("(lifecycle_status = 'active' AND trashed_actor_id IS NULL AND trashed_at IS NULL) OR (lifecycle_status = 'trashed' AND trashed_actor_id IS NOT NULL AND trashed_at IS NOT NULL)", name="ck_atlas_note_trash_metadata"),
        Index("ix_atlas_note_scope_lifecycle", "scope_type", "scope_id", "lifecycle_status"),
        Index("ix_atlas_note_category", "category_id"),
    )


class AtlasNoteRevisionRow(OrmBase):
    __tablename__ = "atlas_note_revisions"

    revision_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(200), ForeignKey("atlas_notes.note_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_yjs_update: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    before_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    after_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    change_set: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    restore_source_savepoint_id: Mapped[str | None] = mapped_column(String(200), ForeignKey("atlas_note_savepoints.savepoint_id", use_alter=True, name="fk_atlas_note_revision_restore_savepoint"), nullable=True)

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_atlas_note_revision_sequence"),
        CheckConstraint("event_kind IN ('create','content_update','body_restore')", name="ck_atlas_note_revision_kind"),
        CheckConstraint("before_digest ~ '^[0-9a-f]{64}$' AND after_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_note_revision_digests"),
        CheckConstraint("(event_kind = 'body_restore' AND restore_source_savepoint_id IS NOT NULL) OR (event_kind <> 'body_restore' AND restore_source_savepoint_id IS NULL)", name="ck_atlas_note_revision_restore_source"),
        UniqueConstraint("note_id", "sequence", name="uq_atlas_note_revision_sequence"),
        Index("ix_atlas_note_revision_note_time", "note_id", "server_timestamp"),
    )


class AtlasNoteSavepointRow(OrmBase):
    __tablename__ = "atlas_note_savepoints"

    savepoint_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(200), ForeignKey("atlas_notes.note_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    covered_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    encoded_yjs_state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    canonical_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    document_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    body_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_change_set: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    contributor_actor_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("sequence >= 1 AND covered_revision >= 1", name="ck_atlas_note_savepoint_heads"),
        CheckConstraint("body_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_note_savepoint_digest"),
        UniqueConstraint("note_id", "sequence", name="uq_atlas_note_savepoint_sequence"),
        UniqueConstraint("note_id", "covered_revision", name="uq_atlas_note_savepoint_covered_revision"),
        Index("ix_atlas_note_savepoint_note_time", "note_id", "created_at"),
    )


class AtlasNoteAttachmentRow(OrmBase):
    __tablename__ = "atlas_note_attachments"

    attachment_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    note_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_notes.note_id"), nullable=False
    )
    artifact_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(BigInteger, nullable=False)
    height: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "mime_type IN ('image/png','image/jpeg','image/webp')",
            name="ck_atlas_note_attachment_mime",
        ),
        CheckConstraint(
            "byte_size BETWEEN 1 AND 16777216",
            name="ck_atlas_note_attachment_size",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_note_attachment_digests",
        ),
        CheckConstraint(
            "width >= 1 AND height >= 1",
            name="ck_atlas_note_attachment_dimensions",
        ),
        UniqueConstraint(
            "note_id",
            "idempotency_key",
            name="uq_atlas_note_attachment_idempotency",
        ),
        Index("ix_atlas_note_attachment_note", "note_id", "created_at"),
    )


class AtlasNotesSettingsRow(OrmBase):
    __tablename__ = "atlas_notes_settings"

    settings_key: Mapped[str] = mapped_column(String(30), primary_key=True)
    checkpoint_interval_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("30"))
    settings_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("settings_key = 'global'", name="ck_atlas_notes_settings_singleton"),
        CheckConstraint("checkpoint_interval_seconds > 0", name="ck_atlas_notes_settings_positive_interval"),
        CheckConstraint("settings_revision >= 1", name="ck_atlas_notes_settings_revision"),
    )


OWNER_TABLES = frozenset({AtlasNoteRow.__tablename__, AtlasNoteCategoryRow.__tablename__, AtlasNoteRevisionRow.__tablename__, AtlasNoteSavepointRow.__tablename__, AtlasNoteAttachmentRow.__tablename__, AtlasNotesSettingsRow.__tablename__})

__all__ = ["AtlasNoteAttachmentRow", "AtlasNoteCategoryRow", "AtlasNoteRevisionRow", "AtlasNoteRow", "AtlasNoteSavepointRow", "AtlasNotesSettingsRow", "OWNER", "OWNER_TABLES"]
