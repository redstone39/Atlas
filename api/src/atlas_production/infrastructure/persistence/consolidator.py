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
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase

CONSOLIDATION_SCAN_SEQUENCE = Sequence("atlas_consolidation_scan_sequence")


class AtlasConsolidatorCheckpointRow(OrmBase):
    """Singleton durable discovery cursor owned by Consolidator."""

    __tablename__ = "atlas_consolidator_checkpoint"

    checkpoint_key: Mapped[str] = mapped_column(String(30), primary_key=True)
    last_scan_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_experience_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "checkpoint_key = 'global'",
            name="ck_atlas_consolidator_checkpoint_singleton",
        ),
        CheckConstraint(
            "(last_scan_sequence IS NULL AND last_experience_ref IS NULL) OR "
            "(last_scan_sequence >= 1 AND last_experience_ref IS NOT NULL)",
            name="ck_atlas_consolidator_checkpoint_cursor",
        ),
    )


class AtlasConsolidationRunRow(OrmBase):
    """Fixed ten-Experience claim lifecycle and completion-only result."""

    __tablename__ = "atlas_consolidation_runs"

    consolidation_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    source_experience_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False
    )
    source_experience_digests: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False
    )
    source_scan_sequences: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )

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
    model_invocation_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scan_sequence: Mapped[int | None] = mapped_column(
        BigInteger,
        CONSOLIDATION_SCAN_SEQUENCE,
        nullable=True,
        unique=True,
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
            "schema_version = 'consolidation-v1'",
            name="ck_atlas_consolidation_schema",
        ),
        CheckConstraint(
            "prompt_revision = 'consolidator-generalize-v1'",
            name="ck_atlas_consolidation_prompt_revision",
        ),
        CheckConstraint(
            "status IN ('pending','consolidating','retryable_failed','completed','failed')",
            name="ck_atlas_consolidation_status",
        ),
        CheckConstraint(
            "attempt >= 0 AND fence >= 0",
            name="ck_atlas_consolidation_counters",
        ),
        CheckConstraint(
            "cardinality(source_experience_refs) = 10 AND "
            "cardinality(source_experience_digests) = 10 AND "
            "cardinality(source_scan_sequences) = 10",
            name="ck_atlas_consolidation_exact_ten",
        ),
        CheckConstraint(
            "source_experience_digests::text ~ '^\\{[0-9a-f,]+\\}$' AND "
            "(result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$')",
            name="ck_atlas_consolidation_digests",
        ),
        CheckConstraint(
            "((pinned_route_id IS NULL) AND (pinned_route_revision IS NULL) AND "
            "(pinned_runtime_policy_revision IS NULL)) OR "
            "((pinned_route_id IS NOT NULL) AND (pinned_route_revision IS NOT NULL) AND "
            "(pinned_runtime_policy_revision IS NOT NULL))",
            name="ck_atlas_consolidation_route_pin",
        ),
        CheckConstraint(
            "((status = 'consolidating') AND worker_id IS NOT NULL AND "
            "claim_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "((status <> 'consolidating') AND worker_id IS NULL AND "
            "claim_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_atlas_consolidation_live_claim",
        ),
        CheckConstraint(
            "((status IN ('retryable_failed','failed')) AND failure_code IS NOT NULL) OR "
            "((status NOT IN ('retryable_failed','failed')) AND failure_code IS NULL)",
            name="ck_atlas_consolidation_failure",
        ),
        CheckConstraint(
            "((status = 'retryable_failed') AND next_attempt_at IS NOT NULL) OR "
            "((status <> 'retryable_failed') AND next_attempt_at IS NULL)",
            name="ck_atlas_consolidation_retry",
        ),
        CheckConstraint(
            "((status = 'completed') AND result_payload IS NOT NULL AND "
            "result_digest IS NOT NULL AND scan_sequence IS NOT NULL AND "
            "completed_at IS NOT NULL AND cardinality(model_invocation_refs) >= 1) OR "
            "((status <> 'completed') AND result_payload IS NULL AND "
            "result_digest IS NULL AND scan_sequence IS NULL AND completed_at IS NULL "
            "AND cardinality(model_invocation_refs) = 0)",
            name="ck_atlas_consolidation_result",
        ),
        Index(
            "ix_atlas_consolidation_due",
            "status",
            "next_attempt_at",
            "created_at",
            "consolidation_ref",
        ),
        Index(
            "ix_atlas_consolidation_scan_ref",
            "scan_sequence",
            "consolidation_ref",
        ),
    )


CONSOLIDATOR_OWNER_TABLES = frozenset(
    {
        AtlasConsolidatorCheckpointRow.__tablename__,
        AtlasConsolidationRunRow.__tablename__,
    }
)


__all__ = [
    "AtlasConsolidationRunRow",
    "AtlasConsolidatorCheckpointRow",
    "CONSOLIDATION_SCAN_SEQUENCE",
    "CONSOLIDATOR_OWNER_TABLES",
]
