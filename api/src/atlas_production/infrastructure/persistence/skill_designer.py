from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


class AtlasSkillDesignerCheckpointRow(OrmBase):
    __tablename__ = "atlas_skill_designer_checkpoint"

    checkpoint_key: Mapped[str] = mapped_column(String(30), primary_key=True)
    last_scan_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_consolidation_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "checkpoint_key = 'global'",
            name="ck_atlas_skill_designer_checkpoint_singleton",
        ),
        CheckConstraint(
            "(last_scan_sequence IS NULL AND last_consolidation_ref IS NULL) OR "
            "(last_scan_sequence >= 1 AND last_consolidation_ref IS NOT NULL)",
            name="ck_atlas_skill_designer_checkpoint_cursor",
        ),
    )


class AtlasSkillDesignRunRow(OrmBase):
    __tablename__ = "atlas_skill_design_runs"

    run_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    consolidation_ref: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    consolidation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    consolidation_scan_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned_route_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pinned_route_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinned_runtime_policy_revision: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    candidate_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "schema_version = 'skill-design-v1'",
            name="ck_atlas_skill_design_schema",
        ),
        CheckConstraint(
            "prompt_revision = 'skill-designer-propose-v1'",
            name="ck_atlas_skill_design_prompt_revision",
        ),
        CheckConstraint(
            "status IN ('pending','designing','retryable_failed','completed','failed')",
            name="ck_atlas_skill_design_status",
        ),
        CheckConstraint(
            "attempt >= 0 AND fence >= 0 AND consolidation_scan_sequence >= 1",
            name="ck_atlas_skill_design_counters",
        ),
        CheckConstraint(
            "consolidation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_skill_design_digest",
        ),
        CheckConstraint(
            "((pinned_route_id IS NULL) AND (pinned_route_revision IS NULL) AND "
            "(pinned_runtime_policy_revision IS NULL)) OR "
            "((pinned_route_id IS NOT NULL) AND (pinned_route_revision IS NOT NULL) AND "
            "(pinned_runtime_policy_revision IS NOT NULL))",
            name="ck_atlas_skill_design_route_pin",
        ),
        CheckConstraint(
            "((status = 'designing') AND worker_id IS NOT NULL AND claim_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR ((status <> 'designing') AND "
            "worker_id IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_atlas_skill_design_live_claim",
        ),
        CheckConstraint(
            "((status IN ('retryable_failed','failed')) AND failure_code IS NOT NULL) OR "
            "((status NOT IN ('retryable_failed','failed')) AND failure_code IS NULL)",
            name="ck_atlas_skill_design_failure",
        ),
        CheckConstraint(
            "((status = 'retryable_failed') AND next_attempt_at IS NOT NULL) OR "
            "((status <> 'retryable_failed') AND next_attempt_at IS NULL)",
            name="ck_atlas_skill_design_retry",
        ),
        CheckConstraint(
            "((status = 'completed') AND completed_at IS NOT NULL) OR "
            "((status <> 'completed') AND completed_at IS NULL AND cardinality(candidate_refs) = 0)",
            name="ck_atlas_skill_design_result",
        ),
        Index(
            "ix_atlas_skill_design_due",
            "status",
            "next_attempt_at",
            "consolidation_scan_sequence",
            "run_ref",
        ),
    )


class AtlasSkillCandidateRow(OrmBase):
    __tablename__ = "atlas_skill_candidates"

    candidate_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    draft_key: Mapped[str] = mapped_column(String(400), nullable=False)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    target_name: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(12_000), nullable=False)
    goal: Mapped[str] = mapped_column(String(12_000), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    draft_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    material_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_skill_ref: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("draft_key", name="uq_atlas_skill_candidate_draft_key"),
        CheckConstraint(
            "disposition IN ('add','revise')",
            name="ck_atlas_skill_candidate_disposition",
        ),
        CheckConstraint(
            "category IN ('understanding','planner','answer')",
            name="ck_atlas_skill_candidate_category",
        ),
        CheckConstraint(
            "status IN ('draft','applying','stale','approved','rejected')",
            name="ck_atlas_skill_candidate_status",
        ),
        CheckConstraint(
            "draft_revision >= 1 AND material_digest ~ '^[0-9a-f]{64}$' AND "
            "skill_source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_skill_candidate_integrity",
        ),
        CheckConstraint(
            "((status = 'approved') AND approved_skill_ref IS NOT NULL) OR "
            "((status <> 'approved') AND approved_skill_ref IS NULL)",
            name="ck_atlas_skill_candidate_approval",
        ),
        Index("ix_atlas_skill_candidate_status_updated", "status", "updated_at"),
    )


class AtlasSkillCandidateIdempotencyRow(OrmBase):
    __tablename__ = "atlas_skill_candidate_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('approve','reject') AND request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_atlas_skill_candidate_idempotency_integrity",
        ),
    )


SKILL_DESIGNER_OWNER_TABLES = frozenset(
    {
        AtlasSkillDesignerCheckpointRow.__tablename__,
        AtlasSkillDesignRunRow.__tablename__,
        AtlasSkillCandidateRow.__tablename__,
        AtlasSkillCandidateIdempotencyRow.__tablename__,
    }
)


__all__ = [
    "AtlasSkillCandidateIdempotencyRow",
    "AtlasSkillCandidateRow",
    "AtlasSkillDesignRunRow",
    "AtlasSkillDesignerCheckpointRow",
    "SKILL_DESIGNER_OWNER_TABLES",
]
