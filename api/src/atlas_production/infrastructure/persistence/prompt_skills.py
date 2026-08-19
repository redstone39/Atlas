"""Prompt-skills-owned immutable revisions, controls, catalogs and idempotency."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "prompt_skills"


class AtlasPromptSkillRevisionRow(OrmBase):
    __tablename__ = "atlas_prompt_skill_revisions"

    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    compatibility: Mapped[str | None] = mapped_column(String(500), nullable=True)
    skill_metadata: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('understanding', 'planner', 'answer')", name="ck_atlas_prompt_skill_revision_category"),
        CheckConstraint("revision >= 1", name="ck_atlas_prompt_skill_revision_number"),
        CheckConstraint("name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="ck_atlas_prompt_skill_revision_name"),
        CheckConstraint("content_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_prompt_skill_revision_digest"),
    )


class AtlasPromptSkillControlRow(OrmBase):
    __tablename__ = "atlas_prompt_skill_controls"

    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    head_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    enabled_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    control_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('understanding', 'planner', 'answer')", name="ck_atlas_prompt_skill_control_category"),
        CheckConstraint("head_revision >= 1", name="ck_atlas_prompt_skill_control_head"),
        CheckConstraint("control_revision >= 1", name="ck_atlas_prompt_skill_control_revision"),
        CheckConstraint("enabled_revision IS NULL OR enabled_revision <= head_revision", name="ck_atlas_prompt_skill_control_enabled"),
        ForeignKeyConstraint(
            ["category", "name", "head_revision"],
            ["atlas_prompt_skill_revisions.category", "atlas_prompt_skill_revisions.name", "atlas_prompt_skill_revisions.revision"],
            name="fk_atlas_prompt_skill_control_head",
        ),
        ForeignKeyConstraint(
            ["category", "name", "enabled_revision"],
            ["atlas_prompt_skill_revisions.category", "atlas_prompt_skill_revisions.name", "atlas_prompt_skill_revisions.revision"],
            name="fk_atlas_prompt_skill_control_enabled",
        ),
    )


class AtlasPromptSkillCatalogRevisionRow(OrmBase):
    __tablename__ = "atlas_prompt_skill_catalog_revisions"

    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    catalog_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    catalog_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('understanding', 'planner', 'answer')", name="ck_atlas_prompt_skill_catalog_category"),
        CheckConstraint("catalog_revision >= 1", name="ck_atlas_prompt_skill_catalog_revision"),
        CheckConstraint("catalog_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_prompt_skill_catalog_digest"),
    )


class AtlasPromptSkillIdempotencyRow(OrmBase):
    __tablename__ = "atlas_prompt_skill_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_prompt_skill_idempotency_digest"),
        CheckConstraint("status_code BETWEEN 200 AND 299", name="ck_atlas_prompt_skill_idempotency_status"),
    )


OWNER_TABLES = frozenset(
    {
        AtlasPromptSkillRevisionRow.__tablename__,
        AtlasPromptSkillControlRow.__tablename__,
        AtlasPromptSkillCatalogRevisionRow.__tablename__,
        AtlasPromptSkillIdempotencyRow.__tablename__,
    }
)


__all__ = [
    "AtlasPromptSkillCatalogRevisionRow",
    "AtlasPromptSkillControlRow",
    "AtlasPromptSkillIdempotencyRow",
    "AtlasPromptSkillRevisionRow",
    "OWNER",
    "OWNER_TABLES",
]
