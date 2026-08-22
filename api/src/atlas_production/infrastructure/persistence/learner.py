from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase

LEARNER_EXPERIENCE_SCAN_SEQUENCE = Sequence("atlas_learner_experience_scan_sequence")


class AtlasLearnerRunRow(OrmBase):
    """Learner-owned fenced run and completion-only immutable Experience."""

    __tablename__ = "atlas_learner_runs"

    run_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    experience_ref: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    learner_prompt_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    review_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    case_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    case_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    case_title: Mapped[str] = mapped_column(String(500), nullable=False)
    involved_turn_ids: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False)
    primary_assistant_turn_id: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pinned_route_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pinned_route_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinned_runtime_policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_invocation_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    experience_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    experience_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scan_sequence: Mapped[int | None] = mapped_column(
        BigInteger,
        LEARNER_EXPERIENCE_SCAN_SEQUENCE,
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "schema_version = 'learner-experience-v1'",
            name="ck_atlas_learner_schema",
        ),
        CheckConstraint(
            "learner_prompt_revision = 'layered-learner-v1'",
            name="ck_atlas_learner_prompt_revision",
        ),
        CheckConstraint(
            "status IN ('pending','learning','retryable_failed','completed','failed')",
            name="ck_atlas_learner_status",
        ),
        CheckConstraint(
            "attempt >= 0 AND fence >= 0 AND case_ordinal >= 1",
            name="ck_atlas_learner_counters",
        ),
        CheckConstraint(
            "review_digest ~ '^[0-9a-f]{64}$' AND snapshot_digest ~ '^[0-9a-f]{64}$' "
            "AND case_digest ~ '^[0-9a-f]{64}$' AND "
            "(experience_digest IS NULL OR experience_digest ~ '^[0-9a-f]{64}$')",
            name="ck_atlas_learner_digests",
        ),
        CheckConstraint(
            "cardinality(involved_turn_ids) >= 1 AND "
            "primary_assistant_turn_id = ANY(involved_turn_ids)",
            name="ck_atlas_learner_turn_identity",
        ),
        CheckConstraint(
            "((pinned_route_id IS NULL) AND (pinned_route_revision IS NULL) AND "
            "(pinned_runtime_policy_revision IS NULL)) OR "
            "((pinned_route_id IS NOT NULL) AND (pinned_route_revision IS NOT NULL) AND "
            "(pinned_runtime_policy_revision IS NOT NULL))",
            name="ck_atlas_learner_route_pin",
        ),
        CheckConstraint(
            "((status = 'learning') AND worker_id IS NOT NULL AND claim_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "((status <> 'learning') AND worker_id IS NULL AND claim_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_atlas_learner_live_claim",
        ),
        CheckConstraint(
            "((status IN ('retryable_failed','failed')) AND failure_code IS NOT NULL) OR "
            "((status NOT IN ('retryable_failed','failed')) AND failure_code IS NULL)",
            name="ck_atlas_learner_failure",
        ),
        CheckConstraint(
            "((status = 'retryable_failed') AND next_attempt_at IS NOT NULL) OR "
            "((status <> 'retryable_failed') AND next_attempt_at IS NULL)",
            name="ck_atlas_learner_retry",
        ),
        CheckConstraint(
            "((status = 'completed') AND experience_payload IS NOT NULL "
            "AND experience_digest IS NOT NULL AND scan_sequence IS NOT NULL "
            "AND completed_at IS NOT NULL AND cardinality(model_invocation_refs) >= 1) OR "
            "((status <> 'completed') AND experience_payload IS NULL "
            "AND experience_digest IS NULL AND scan_sequence IS NULL "
            "AND completed_at IS NULL AND cardinality(model_invocation_refs) = 0)",
            name="ck_atlas_learner_result",
        ),
        UniqueConstraint(
            "review_ref",
            "review_digest",
            "case_ordinal",
            "case_digest",
            "schema_version",
            "learner_prompt_revision",
            name="uq_atlas_learner_source_case",
        ),
        Index(
            "ix_atlas_learner_due",
            "status",
            "next_attempt_at",
            "created_at",
            "run_ref",
        ),
        Index(
            "ix_atlas_learner_scan_ref",
            "scan_sequence",
            "experience_ref",
        ),
    )


LEARNER_OWNER_TABLES = frozenset({AtlasLearnerRunRow.__tablename__})


__all__ = [
    "AtlasLearnerRunRow",
    "LEARNER_EXPERIENCE_SCAN_SEQUENCE",
    "LEARNER_OWNER_TABLES",
]
