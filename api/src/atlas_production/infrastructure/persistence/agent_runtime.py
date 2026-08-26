"""Agent-runtime-owner accepted research and immutable terminal packet schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


class AtlasAgentResearchRow(OrmBase):
    __tablename__ = "atlas_agent_research"

    research_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    question_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    output_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    accepted_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB(none_as_null=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    packet_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    packet_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    packet_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_atlas_agent_research_actor_idempotency",
        ),
        CheckConstraint(
            "char_length(question) BETWEEN 1 AND 12000",
            name="ck_atlas_agent_research_question_length",
        ),
        CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_agent_research_request_digest",
        ),
        CheckConstraint(
            "output_mode IN ('evidence_packet','evidence_packet_and_answer')",
            name="ck_atlas_agent_research_output_mode",
        ),
        CheckConstraint(
            "status IN ('accepted','completed')",
            name="ck_atlas_agent_research_status",
        ),
        CheckConstraint(
            "(status = 'accepted' AND packet_payload IS NULL AND packet_ref IS NULL AND packet_digest IS NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND packet_payload IS NOT NULL AND packet_ref IS NOT NULL AND packet_digest ~ '^[0-9a-f]{64}$' AND completed_at IS NOT NULL)",
            name="ck_atlas_agent_research_terminal_shape",
        ),
        Index(
            "ix_atlas_agent_research_accepted_at_id",
            "accepted_at",
            "research_id",
        ),
    )


OWNER_TABLES = frozenset({AtlasAgentResearchRow.__tablename__})
