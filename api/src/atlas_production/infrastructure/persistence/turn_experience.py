from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


class AtlasTurnExperienceRow(OrmBase):
    """Turn Experience-owned immutable refs-only derived projection."""

    __tablename__ = "atlas_turn_experiences"

    experience_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    scan_sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(), nullable=False, unique=True
    )
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reasoning_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "schema_version = 'turn-experience-v1'",
            name="ck_atlas_turn_experience_schema",
        ),
        CheckConstraint(
            "reasoning_mode IN ('standard','deep')",
            name="ck_atlas_turn_experience_reasoning_mode",
        ),
        CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_turn_experience_digest",
        ),
        CheckConstraint(
            "octet_length(payload::text) <= 65536",
            name="ck_atlas_turn_experience_payload_bytes",
        ),
        UniqueConstraint(
            "execution_id",
            "schema_version",
            name="uq_atlas_turn_experience_execution_schema",
        ),
        Index(
            "ix_atlas_turn_experience_scan_execution",
            "scan_sequence",
            "execution_id",
        ),
    )


TURN_EXPERIENCE_OWNER_TABLES = frozenset({AtlasTurnExperienceRow.__tablename__})


__all__ = ["AtlasTurnExperienceRow", "TURN_EXPERIENCE_OWNER_TABLES"]
