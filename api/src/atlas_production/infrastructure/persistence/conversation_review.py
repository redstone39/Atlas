from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Sequence,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase

CONVERSATION_REVIEW_SCAN_SEQUENCE = Sequence(
    "atlas_conversation_review_scan_sequence"
)


class AtlasConversationReviewRow(OrmBase):
    """Conversation Review-owned immutable snapshot and fenced lifecycle."""

    __tablename__ = "atlas_conversation_reviews"

    review_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    review_prompt_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expected_next_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_semantic_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
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
    review_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scan_sequence: Mapped[int | None] = mapped_column(
        BigInteger,
        CONVERSATION_REVIEW_SCAN_SEQUENCE,
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
            "schema_version = 'conversation-review-v1'",
            name="ck_atlas_conversation_review_schema",
        ),
        CheckConstraint(
            "review_prompt_revision = 'conversation-review-triage-v1'",
            name="ck_atlas_conversation_review_prompt_revision",
        ),
        CheckConstraint(
            "status IN ('pending','reviewing','retryable_failed','completed',"
            "'completed_no_cases','superseded','failed')",
            name="ck_atlas_conversation_review_status",
        ),
        CheckConstraint(
            "attempt >= 0 AND fence >= 0 AND expected_next_ordinal >= 1",
            name="ck_atlas_conversation_review_counters",
        ),
        CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$' AND "
            "(review_digest IS NULL OR review_digest ~ '^[0-9a-f]{64}$')",
            name="ck_atlas_conversation_review_digests",
        ),
        CheckConstraint(
            "eligible_at = latest_semantic_activity_at + interval '2 hours'",
            name="ck_atlas_conversation_review_eligibility",
        ),
        CheckConstraint(
            "((pinned_route_id IS NULL) AND (pinned_route_revision IS NULL) AND "
            "(pinned_runtime_policy_revision IS NULL)) OR "
            "((pinned_route_id IS NOT NULL) AND (pinned_route_revision IS NOT NULL) AND "
            "(pinned_runtime_policy_revision IS NOT NULL))",
            name="ck_atlas_conversation_review_route_pin",
        ),
        CheckConstraint(
            "((status = 'reviewing') AND worker_id IS NOT NULL AND claim_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "((status <> 'reviewing') AND worker_id IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_atlas_conversation_review_live_claim",
        ),
        CheckConstraint(
            "((status IN ('retryable_failed','failed')) AND failure_code IS NOT NULL) OR "
            "((status NOT IN ('retryable_failed','failed')) AND failure_code IS NULL)",
            name="ck_atlas_conversation_review_failure",
        ),
        CheckConstraint(
            "((status IN ('completed','completed_no_cases')) AND review_digest IS NOT NULL "
            "AND completed_at IS NOT NULL AND scan_sequence IS NOT NULL "
            "AND cardinality(model_invocation_refs) >= 1) OR "
            "((status NOT IN ('completed','completed_no_cases')) AND review_digest IS NULL "
            "AND completed_at IS NULL AND scan_sequence IS NULL "
            "AND cardinality(model_invocation_refs) = 0)",
            name="ck_atlas_conversation_review_result",
        ),
        UniqueConstraint(
            "conversation_id",
            "schema_version",
            "snapshot_digest",
            "review_prompt_revision",
            name="uq_atlas_conversation_review_snapshot",
        ),
        Index(
            "ix_atlas_conversation_review_due",
            "status",
            "next_attempt_at",
            "eligible_at",
            "review_ref",
        ),
        Index(
            "ix_atlas_conversation_review_conversation_completed",
            "conversation_id",
            "completed_at",
            "review_ref",
        ),
        Index(
            "ix_atlas_conversation_review_scan_ref",
            "scan_sequence",
            "review_ref",
        ),
    )


class AtlasConversationReviewSnapshotTurnRow(OrmBase):
    """Normalized immutable source lineage; never stores transcript text."""

    __tablename__ = "atlas_conversation_review_snapshot_turns"

    review_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_conversation_reviews.review_ref", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    retry_of_turn_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_projection_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    user_text_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    terminal_status: Mapped[str] = mapped_column(String(20), nullable=False)
    terminal_scan_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    terminal_commit_intent_ref: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    terminal_committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    governed_answer_draft_ref: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    governed_answer_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_atlas_conversation_review_turn_position"),
        CheckConstraint(
            "terminal_status IN ('completed','failed')",
            name="ck_atlas_conversation_review_turn_status",
        ),
        CheckConstraint(
            "user_text_digest ~ '^[0-9a-f]{64}$' AND "
            "(governed_answer_digest IS NULL OR governed_answer_digest ~ '^[0-9a-f]{64}$')",
            name="ck_atlas_conversation_review_turn_digests",
        ),
        CheckConstraint(
            "(terminal_status = 'completed' AND terminal_commit_intent_ref IS NOT NULL "
            "AND governed_answer_draft_ref IS NOT NULL AND governed_answer_digest IS NOT NULL) OR "
            "(terminal_status = 'failed' AND terminal_commit_intent_ref IS NULL "
            "AND governed_answer_draft_ref IS NULL AND governed_answer_digest IS NULL)",
            name="ck_atlas_conversation_review_turn_terminal_shape",
        ),
        UniqueConstraint(
            "review_ref", "turn_id", name="uq_atlas_conversation_review_turn_id"
        ),
        UniqueConstraint(
            "review_ref", "execution_id", name="uq_atlas_conversation_review_execution_id"
        ),
    )


class AtlasConversationLearningCaseRow(OrmBase):
    __tablename__ = "atlas_conversation_learning_cases"

    review_ref: Mapped[str] = mapped_column(
        String(300),
        ForeignKey("atlas_conversation_reviews.review_ref", ondelete="CASCADE"),
        primary_key=True,
    )
    case_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    learning_evidence: Mapped[str] = mapped_column(String(12_000), nullable=False)
    generalization_hypothesis: Mapped[str] = mapped_column(String(12_000), nullable=False)
    investigation_question: Mapped[str] = mapped_column(String(12_000), nullable=False)
    selection_rationale: Mapped[str] = mapped_column(String(12_000), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "case_ordinal BETWEEN 1 AND 3",
            name="ck_atlas_conversation_learning_case_ordinal",
        ),
    )


class AtlasConversationLearningCaseTurnRow(OrmBase):
    __tablename__ = "atlas_conversation_learning_case_turns"

    review_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    case_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    turn_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_primary_assistant: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["review_ref", "case_ordinal"],
            [
                "atlas_conversation_learning_cases.review_ref",
                "atlas_conversation_learning_cases.case_ordinal",
            ],
            ondelete="CASCADE",
            name="fk_atlas_conversation_learning_case_turn_case",
        ),
        ForeignKeyConstraint(
            ["review_ref", "turn_position"],
            [
                "atlas_conversation_review_snapshot_turns.review_ref",
                "atlas_conversation_review_snapshot_turns.position",
            ],
            ondelete="CASCADE",
            name="fk_atlas_conversation_learning_case_turn_snapshot",
        ),
        CheckConstraint(
            "case_ordinal BETWEEN 1 AND 3 AND turn_position >= 1",
            name="ck_atlas_conversation_learning_case_turn_position",
        ),
        Index(
            "uq_atlas_conversation_learning_case_primary",
            "review_ref",
            "case_ordinal",
            unique=True,
            postgresql_where=and_(is_primary_assistant.is_(True)),
        ),
    )


CONVERSATION_REVIEW_OWNER_TABLES = frozenset(
    {
        AtlasConversationReviewRow.__tablename__,
        AtlasConversationReviewSnapshotTurnRow.__tablename__,
        AtlasConversationLearningCaseRow.__tablename__,
        AtlasConversationLearningCaseTurnRow.__tablename__,
    }
)


__all__ = [
    "AtlasConversationLearningCaseRow",
    "AtlasConversationLearningCaseTurnRow",
    "AtlasConversationReviewRow",
    "AtlasConversationReviewSnapshotTurnRow",
    "CONVERSATION_REVIEW_OWNER_TABLES",
    "CONVERSATION_REVIEW_SCAN_SEQUENCE",
]
