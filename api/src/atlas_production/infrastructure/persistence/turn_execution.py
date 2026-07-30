"""Turn-execution-owned append-only global Answer behavior revisions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "turn_execution"


class AtlasTurnAnswerBehaviorRevisionRow(OrmBase):
    __tablename__ = "atlas_turn_answer_behavior_revisions"

    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    custom_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    guidance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_event_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_atlas_turn_answer_behavior_revision",
        ),
        CheckConstraint(
            "custom_guidance IS NULL OR "
            "(char_length(custom_guidance) BETWEEN 1 AND 2000)",
            name="ck_atlas_turn_answer_behavior_guidance_length",
        ),
        CheckConstraint(
            "guidance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_answer_behavior_guidance_digest",
        ),
        CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_answer_behavior_request_digest",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_atlas_turn_answer_behavior_idempotency",
        ),
    )


OWNER_TABLES = frozenset({AtlasTurnAnswerBehaviorRevisionRow.__tablename__})


__all__ = [
    "AtlasTurnAnswerBehaviorRevisionRow",
    "OWNER",
    "OWNER_TABLES",
]
