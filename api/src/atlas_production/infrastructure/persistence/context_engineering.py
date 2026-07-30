"""Context-engineering-owner immutable Context/Summary V3 schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "context_engineering"


class AtlasTurnInputProjectionRow(OrmBase):
    __tablename__ = "atlas_turn_input_projections"

    projection_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    original_user_input: Mapped[str] = mapped_column(Text, nullable=False)
    resolver_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewritten_user_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolver_invocation_ref: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    rewrite_invocation_ref: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    resolver_failure_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    rewrite_failure_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(original_user_input) BETWEEN 1 AND 50000",
            name="ck_atlas_turn_input_projection_original",
        ),
        CheckConstraint(
            "resolver_output IS NULL OR char_length(resolver_output) BETWEEN 1 AND 50000",
            name="ck_atlas_turn_input_projection_resolver",
        ),
        CheckConstraint(
            "rewritten_user_input IS NULL OR char_length(rewritten_user_input) BETWEEN 1 AND 50000",
            name="ck_atlas_turn_input_projection_rewrite",
        ),
        CheckConstraint(
            "(resolver_output IS NULL) <> (resolver_failure_code IS NULL) OR "
            "(resolver_output IS NULL AND resolver_failure_code IS NULL)",
            name="ck_atlas_turn_input_projection_resolver_outcome",
        ),
        CheckConstraint(
            "(rewritten_user_input IS NULL) <> (rewrite_failure_code IS NULL) OR "
            "(rewritten_user_input IS NULL AND rewrite_failure_code IS NULL)",
            name="ck_atlas_turn_input_projection_rewrite_outcome",
        ),
        CheckConstraint(
            "(rewritten_user_input IS NULL AND rewrite_failure_code IS NULL) OR "
            "(resolver_output IS NOT NULL AND resolver_failure_code IS NULL)",
            name="ck_atlas_turn_input_projection_rewrite_after_resolver",
        ),
    )


class AtlasTurnContextPackRow(OrmBase):
    __tablename__ = "atlas_turn_context_packs"

    context_pack_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    input_projection_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_input_projections.projection_ref", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_user_input: Mapped[str] = mapped_column(Text, nullable=False)
    summary_ref: Mapped[str | None] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_context_summaries.summary_ref", ondelete="RESTRICT"),
        nullable=True,
    )
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'context-pack-v3'", name="ck_atlas_turn_context_pack_schema"),
        CheckConstraint(
            "char_length(model_user_input) BETWEEN 1 AND 50000",
            name="ck_atlas_turn_context_pack_model_user_input",
        ),
        CheckConstraint("token_budget >= 1", name="ck_atlas_turn_context_pack_budget"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_context_pack_digest"),
        UniqueConstraint("execution_id", "idempotency_key", name="uq_atlas_turn_context_pack_idempotency"),
    )


class AtlasTurnContextPackRecentExchangeRow(OrmBase):
    __tablename__ = "atlas_turn_context_pack_recent_exchanges"

    context_pack_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_context_packs.context_pack_ref", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    logical_turn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    representative_turn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    representative_content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    assistant_verification_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_atlas_turn_context_recent_position"),
        CheckConstraint("representative_content_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_context_recent_digest"),
        CheckConstraint("char_length(user_text) <= 50000", name="ck_atlas_turn_context_recent_user_text"),
        CheckConstraint("assistant_text IS NULL OR char_length(assistant_text) <= 50000", name="ck_atlas_turn_context_recent_assistant_text"),
        CheckConstraint(
            "assistant_verification_status IS NULL OR assistant_verification_status IN ('verified','partially_verified','unverified','not_applicable')",
            name="ck_atlas_turn_context_recent_verification",
        ),
        UniqueConstraint("context_pack_ref", "representative_turn_id", name="uq_atlas_turn_context_recent_representative"),
    )


class AtlasTurnContextPackRecentResourceRow(OrmBase):
    __tablename__ = "atlas_turn_context_pack_recent_resources"

    context_pack_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_context_packs.context_pack_ref", ondelete="RESTRICT"),
        primary_key=True,
    )
    representative_turn_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(200), primary_key=True)


class AtlasTurnContextSummaryRow(OrmBase):
    __tablename__ = "atlas_turn_context_summaries"

    summary_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_summary_ref: Mapped[str | None] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_context_summaries.summary_ref", ondelete="RESTRICT"),
        nullable=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'context-summary-v3'", name="ck_atlas_turn_context_summary_schema"),
        CheckConstraint("token_count BETWEEN 1 AND 6000", name="ck_atlas_turn_context_summary_tokens"),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_context_summary_digest"),
    )


class AtlasTurnContextSummarySourceRow(OrmBase):
    __tablename__ = "atlas_turn_context_summary_sources"

    summary_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_context_summaries.summary_ref", ondelete="RESTRICT"), primary_key=True
    )
    representative_turn_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_turn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    representative_content_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("source_ordinal >= 1", name="ck_atlas_turn_context_summary_source_ordinal"),
        CheckConstraint("representative_content_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_context_summary_source_digest"),
        UniqueConstraint("summary_ref", "source_ordinal", name="uq_atlas_turn_context_summary_source_ordinal"),
        UniqueConstraint("summary_ref", "logical_turn_id", name="uq_atlas_turn_context_summary_logical_turn"),
    )


class AtlasTurnContextSummarySourceResourceRow(OrmBase):
    __tablename__ = "atlas_turn_context_summary_source_resources"

    summary_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_context_summaries.summary_ref", ondelete="RESTRICT"), primary_key=True
    )
    representative_turn_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(200), primary_key=True)


class AtlasTurnContextLineageEdgeRow(OrmBase):
    __tablename__ = "atlas_turn_context_lineage_edges"

    edge_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    dependent_turn_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    dependent_context_pack_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_context_packs.context_pack_ref", ondelete="RESTRICT"), nullable=False
    )
    source_turn_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    source_resource_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_resource_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    dependency_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    lifecycle_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    generation_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint("source_resource_kind IN ('turn','summary','document','evidence','citation')", name="ck_atlas_turn_context_lineage_resource_kind"),
        CheckConstraint("dependency_kind IN ('recent_turn','summary_source','knowledge_hint')", name="ck_atlas_turn_context_lineage_dependency_kind"),
        CheckConstraint("lifecycle_epoch IS NULL OR lifecycle_epoch >= 1", name="ck_atlas_turn_context_lineage_epoch"),
    )


class AtlasTurnContextPackReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_context_pack_releases"

    release_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    context_pack_ref: Mapped[str] = mapped_column(
        String(300), ForeignKey("atlas_turn_context_packs.context_pack_ref", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("execution_id", "idempotency_key", name="uq_atlas_turn_context_release_idempotency"),
        UniqueConstraint("execution_id", "context_pack_ref", name="uq_atlas_turn_context_release_pack"),
    )


OWNER_TABLES = frozenset(
    {
        AtlasTurnInputProjectionRow.__tablename__,
        AtlasTurnContextPackRow.__tablename__,
        AtlasTurnContextPackRecentExchangeRow.__tablename__,
        AtlasTurnContextPackRecentResourceRow.__tablename__,
        AtlasTurnContextSummaryRow.__tablename__,
        AtlasTurnContextSummarySourceRow.__tablename__,
        AtlasTurnContextSummarySourceResourceRow.__tablename__,
        AtlasTurnContextLineageEdgeRow.__tablename__,
        AtlasTurnContextPackReleaseRow.__tablename__,
    }
)
