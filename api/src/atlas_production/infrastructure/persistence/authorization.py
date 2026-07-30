"""Authorization-owner immutable grant and release schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "authorization"


class AtlasTurnAccessGrantRow(OrmBase):
    __tablename__ = "atlas_turn_access_grants"

    grant_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'turn-access-grant-v1'", name="ck_atlas_turn_access_grant_schema"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_access_grant_digest"),
        CheckConstraint("authorization_revision >= 1", name="ck_atlas_turn_access_grant_revision"),
        CheckConstraint("deadline_at >= issued_at", name="ck_atlas_turn_access_grant_deadline"),
        UniqueConstraint("actor_id", "idempotency_key", name="uq_atlas_turn_access_grant_idempotency"),
    )


class AtlasTurnAccessGrantReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_access_grant_releases"

    release_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    grant_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_access_grants.grant_ref", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("execution_id", "grant_ref", name="uq_atlas_turn_access_grant_release_binding"),
        UniqueConstraint("execution_id", "idempotency_key", name="uq_atlas_turn_access_grant_release_idempotency"),
    )


class AtlasAuthorizationRevisionRow(OrmBase):
    __tablename__ = "atlas_authorization_revisions"

    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    authority_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_atlas_authorization_revision_positive"),
        CheckConstraint("authority_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_authorization_revision_digest"),
    )


class AtlasTurnGrantResourceSnapshotRow(OrmBase):
    __tablename__ = "atlas_turn_grant_resource_snapshots"

    grant_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_access_grants.grant_ref", ondelete="RESTRICT"), primary_key=True
    )
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    authorization_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("authorization_revision >= 1", name="ck_atlas_turn_grant_resource_revision"),
        CheckConstraint("resource_count >= 0", name="ck_atlas_turn_grant_resource_count"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_grant_resource_digest"),
        UniqueConstraint("grant_ref", "idempotency_key", name="uq_atlas_turn_grant_resource_idempotency"),
    )


class AtlasTurnGrantDocumentResourceRow(OrmBase):
    __tablename__ = "atlas_turn_grant_document_resources"

    grant_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_grant_resource_snapshots.grant_ref", ondelete="RESTRICT"), primary_key=True
    )
    resource_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_version_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    processing_generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    index_generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_atlas_turn_grant_document_ordinal"),
        CheckConstraint("lifecycle_epoch >= 1", name="ck_atlas_turn_grant_document_lifecycle_epoch"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_grant_document_manifest_digest"),
        CheckConstraint("octet_length(descriptor::text) <= 16384", name="ck_atlas_turn_grant_document_descriptor_bytes"),
        UniqueConstraint("grant_ref", "ordinal", name="uq_atlas_turn_grant_document_ordinal"),
    )


OWNER_TABLES = frozenset(
    {
        AtlasTurnAccessGrantRow.__tablename__, AtlasTurnAccessGrantReleaseRow.__tablename__,
        AtlasAuthorizationRevisionRow.__tablename__, AtlasTurnGrantResourceSnapshotRow.__tablename__,
        AtlasTurnGrantDocumentResourceRow.__tablename__,
    }
)
