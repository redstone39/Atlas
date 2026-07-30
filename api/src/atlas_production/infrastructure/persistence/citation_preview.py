from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import OrmBase
class AtlasTurnCitationBindingDraftRow(OrmBase):
    """Citation-owner immutable verified-claim binding projection."""

    __tablename__ = "atlas_turn_citation_binding_drafts"

    draft_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    governed_answer_draft_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    governed_answer_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "schema_version IN ('citation-binding-draft-v1', 'citation-binding-draft-v2')",
            name="ck_atlas_turn_citation_binding_schema",
        ),
        CheckConstraint(
            "governed_answer_digest ~ '^[0-9a-f]{64}$' AND digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_citation_binding_digests",
        ),
        CheckConstraint(
            "octet_length(payload::text) <= 1048576",
            name="ck_atlas_turn_citation_binding_payload_bytes",
        ),
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_citation_binding_idempotency",
        ),
    )


class AtlasTurnCitationBindingDraftReleaseRow(OrmBase):
    __tablename__ = "atlas_turn_citation_binding_draft_releases"

    release_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    draft_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_turn_citation_binding_drafts.draft_ref", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "execution_id", "idempotency_key",
            name="uq_atlas_turn_citation_binding_release_idempotency",
        ),
        UniqueConstraint(
            "execution_id", "draft_ref",
            name="uq_atlas_turn_citation_binding_release_binding",
        ),
    )


TURN_CITATION_OWNER_TABLES = frozenset(
    {
        AtlasTurnCitationBindingDraftRow.__tablename__,
        AtlasTurnCitationBindingDraftReleaseRow.__tablename__,
    }
)
