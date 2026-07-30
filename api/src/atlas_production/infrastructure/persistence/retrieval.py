"""Retrieval-owner immutable catalog, result, handle, and release schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "retrieval"


class AtlasTurnKnowledgeCatalogRow(OrmBase):
    __tablename__ = "atlas_turn_knowledge_catalogs"

    catalog_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    grant_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    generation_retention_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    authorization_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    retrieval_generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'knowledge-catalog-snapshot-v1'", name="ck_atlas_turn_catalog_schema"),
        CheckConstraint("document_count >= 0", name="ck_atlas_turn_catalog_document_count"),
        CheckConstraint("authorization_revision >= 1", name="ck_atlas_turn_catalog_authorization_revision"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_catalog_digest"),
        UniqueConstraint("grant_ref", "idempotency_key", name="uq_atlas_turn_catalog_idempotency"),
    )


class AtlasTurnCatalogDocumentRow(OrmBase):
    __tablename__ = "atlas_turn_catalog_documents"

    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), primary_key=True
    )
    document_handle: Mapped[str] = mapped_column(String(200), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_version_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    processing_generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    index_generation_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(document_handle) >= 8", name="ck_atlas_turn_catalog_document_handle"),
        CheckConstraint("ordinal >= 1", name="ck_atlas_turn_catalog_document_ordinal"),
        CheckConstraint("lifecycle_epoch >= 1", name="ck_atlas_turn_catalog_document_lifecycle_epoch"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_catalog_document_manifest_digest"),
        CheckConstraint("octet_length(descriptor::text) <= 65536", name="ck_atlas_turn_catalog_document_descriptor_bytes"),
        UniqueConstraint("catalog_ref", "ordinal", name="uq_atlas_turn_catalog_document_ordinal"),
    )


class AtlasTurnRetrievalInvocationRow(OrmBase):
    __tablename__ = "atlas_turn_retrieval_invocations"

    invocation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), nullable=False
    )
    invocation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(200), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("invocation_ordinal >= 1", name="ck_atlas_turn_retrieval_invocation_ordinal"),
        CheckConstraint("action IN ('list_knowledge_documents','find_knowledge_documents','discover_relevant_documents','search_knowledge','inspect_knowledge','inspect_visual','expand_knowledge','navigate_document')", name="ck_atlas_turn_retrieval_invocation_action"),
        CheckConstraint("arguments_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_retrieval_invocation_digest"),
        CheckConstraint("octet_length(canonical_arguments::text) <= 16384", name="ck_atlas_turn_retrieval_arguments_bytes"),
        CheckConstraint("status IN ('started','completed','failed')", name="ck_atlas_turn_retrieval_invocation_status"),
        UniqueConstraint("execution_id", "invocation_ordinal", name="uq_atlas_turn_retrieval_invocation_ordinal"),
        UniqueConstraint(
            "execution_id",
            "catalog_ref",
            "action",
            "schema_version",
            "arguments_digest",
            name="uq_atlas_turn_retrieval_replay",
        ),
    )


class AtlasTurnRetrievalResultRow(OrmBase):
    __tablename__ = "atlas_turn_retrieval_results"

    result_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_retrieval_invocations.invocation_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    result_type: Mapped[str] = mapped_column(String(50), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    observation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("result_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_retrieval_result_digest"),
        CheckConstraint("octet_length(observation::text) <= 262144", name="ck_atlas_turn_retrieval_observation_bytes"),
        CheckConstraint("error_code IS NULL OR error_code IN ('access_denied','invalid_handle','catalog_stale','budget_exhausted','navigation_unavailable','tool_failed')", name="ck_atlas_turn_retrieval_result_error"),
    )


class AtlasTurnRetrievalHandleRow(OrmBase):
    __tablename__ = "atlas_turn_retrieval_handles"

    handle: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), nullable=False
    )
    handle_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence_identity: Mapped[str | None] = mapped_column(String(300), nullable=True)
    document_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_invocation_id: Mapped[str | None] = mapped_column(
        String(200), ForeignKey("atlas_turn_retrieval_invocations.invocation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(handle) >= 8", name="ck_atlas_turn_retrieval_handle_length"),
        CheckConstraint("handle_kind IN ('document','evidence','page','visual','navigation')", name="ck_atlas_turn_retrieval_handle_kind"),
        UniqueConstraint("execution_id", "catalog_ref", "resource_ref", "handle_kind", name="uq_atlas_turn_retrieval_handle_resource"),
    )


class AtlasTurnEvidenceIdentityRow(OrmBase):
    __tablename__ = "atlas_turn_evidence_identities"

    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), primary_key=True
    )
    evidence_identity: Mapped[str] = mapped_column(String(300), primary_key=True)
    document_handle: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasTurnRetrievalEvidencePackRow(OrmBase):
    __tablename__ = "atlas_turn_retrieval_evidence_packs"

    evidence_pack_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), nullable=False
    )
    lineage_items: Mapped[list] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_retrieval_evidence_pack_digest"),
        CheckConstraint("jsonb_array_length(lineage_items) <= 40", name="ck_atlas_turn_retrieval_evidence_pack_count"),
        CheckConstraint("octet_length(lineage_items::text) <= 32768", name="ck_atlas_turn_retrieval_evidence_pack_bytes"),
    )


class AtlasTurnRetrievalReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_retrieval_releases"

    release_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    catalog_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_knowledge_catalogs.catalog_ref", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("execution_id", "catalog_ref", name="uq_atlas_turn_retrieval_release_binding"),
        UniqueConstraint("execution_id", "idempotency_key", name="uq_atlas_turn_retrieval_release_idempotency"),
    )


OWNER_TABLES = frozenset(
    {
        AtlasTurnKnowledgeCatalogRow.__tablename__, AtlasTurnCatalogDocumentRow.__tablename__,
        AtlasTurnRetrievalInvocationRow.__tablename__, AtlasTurnRetrievalResultRow.__tablename__,
        AtlasTurnRetrievalHandleRow.__tablename__, AtlasTurnEvidenceIdentityRow.__tablename__,
        AtlasTurnRetrievalEvidencePackRow.__tablename__, AtlasTurnRetrievalReleaseRow.__tablename__,
    }
)
