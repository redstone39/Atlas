"""Turn-runtime-owner execution, lease, ledger, terminal, and saga schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import OrmBase


OWNER = "turn_runtime"

EXECUTION_STATES = "'allocated','accepted','context_ready','awaiting_model_action','tool_pending','tool_completed','governing_result','materializing_terminal','terminal_completed','terminal_failed'"


class AtlasTurnExecutionRow(OrmBase):
    __tablename__ = "atlas_turn_executions"

    execution_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_language: Mapped[str] = mapped_column(String(10), nullable=False)
    applied_guidance_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    applied_guidance_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    route_id: Mapped[str] = mapped_column(String(200), nullable=False)
    route_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    tokenizer_profile: Mapped[str] = mapped_column(String(200), nullable=False)
    context_window_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens_per_invocation: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens_per_invocation: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_result_tokens_per_execution: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_tokens_per_conversation: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_invocations: Mapped[int] = mapped_column(Integer, nullable=False)
    max_catalog_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_search_rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_unique_evidence: Mapped[int] = mapped_column(Integer, nullable=False)
    max_provider_invocations: Mapped[int] = mapped_column(Integer, nullable=False)
    context_token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_sweep_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    grant_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    catalog_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    context_pack_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    terminal_commit_intent_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    terminal_failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"state IN ({EXECUTION_STATES})", name="ck_atlas_turn_execution_state"),
        CheckConstraint("version >= 1", name="ck_atlas_turn_execution_version"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_execution_input_digest"),
        CheckConstraint(
            "response_language IN ('zh-TW','en')",
            name="ck_atlas_turn_execution_response_language",
        ),
        CheckConstraint(
            "(applied_guidance_revision = 0 AND applied_guidance_digest IS NULL) OR "
            "(applied_guidance_revision >= 1 AND "
            "applied_guidance_digest ~ '^[0-9a-f]{64}$')",
            name="ck_atlas_turn_execution_guidance_snapshot",
        ),
        CheckConstraint(
            "route_revision >= 1 AND runtime_policy_revision >= 1",
            name="ck_atlas_turn_execution_route_revisions",
        ),
        CheckConstraint(
            "context_window_tokens >= 1 AND max_input_tokens_per_invocation >= 1 AND "
            "max_output_tokens_per_invocation >= 1 AND max_tool_result_tokens_per_execution >= 1 AND "
            "max_total_tokens_per_conversation >= 1 AND "
            "max_input_tokens_per_invocation + max_output_tokens_per_invocation <= context_window_tokens",
            name="ck_atlas_turn_execution_model_token_policy",
        ),
        CheckConstraint(
            "max_tool_invocations >= 0 AND max_catalog_pages >= 0 AND "
            "max_search_rounds >= 0 AND max_unique_evidence >= 0",
            name="ck_atlas_turn_execution_nonnegative_policy",
        ),
        CheckConstraint(
            "max_provider_invocations >= max_tool_invocations + 2",
            name="ck_atlas_turn_execution_provider_budget",
        ),
        CheckConstraint(
            "context_token_budget >= 1 AND tool_token_budget >= 1 AND "
            "deadline_seconds >= 1",
            name="ck_atlas_turn_execution_positive_policy",
        ),
        CheckConstraint(
            "heartbeat_interval_seconds >= 1 AND ttl_seconds >= 2 AND "
            "failure_sweep_interval_seconds >= 1 AND "
            "heartbeat_interval_seconds < ttl_seconds",
            name="ck_atlas_turn_execution_lease_policy",
        ),
        CheckConstraint("deadline_at >= created_at", name="ck_atlas_turn_execution_deadline"),
        CheckConstraint("(state = 'terminal_failed') = (terminal_failure_code IS NOT NULL)", name="ck_atlas_turn_execution_failure_state"),
    )


class AtlasTurnExecutionLeaseRow(OrmBase):
    __tablename__ = "atlas_turn_execution_leases"

    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), primary_key=True
    )
    holder_id: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("lease_version >= 1", name="ck_atlas_turn_execution_lease_version"),
        CheckConstraint("fencing_token >= 1", name="ck_atlas_turn_execution_lease_fence"),
        CheckConstraint("acquired_at <= heartbeat_at AND heartbeat_at < expires_at", name="ck_atlas_turn_execution_lease_times"),
    )


class AtlasTurnBudgetCounterRow(OrmBase):
    __tablename__ = "atlas_turn_budget_counters"

    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), primary_key=True
    )
    tool_invocations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    catalog_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_evidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_invocations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("tool_invocations >= 0 AND catalog_pages >= 0 AND document_candidates >= 0 AND search_rounds >= 0 AND unique_evidence >= 0 AND provider_invocations >= 0 AND context_tokens >= 0 AND tool_tokens >= 0", name="ck_atlas_turn_budget_nonnegative"),
    )


class AtlasTurnDocumentCandidateLedgerRow(OrmBase):
    __tablename__ = "atlas_turn_document_candidate_ledger"

    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), primary_key=True
    )
    document_identity: Mapped[str] = mapped_column(String(300), primary_key=True)
    first_invocation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("first_invocation_ordinal >= 1", name="ck_atlas_turn_candidate_first_ordinal"),)


class AtlasTurnUniqueEvidenceLedgerRow(OrmBase):
    __tablename__ = "atlas_turn_unique_evidence_ledger"

    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), primary_key=True
    )
    evidence_identity: Mapped[str] = mapped_column(String(300), primary_key=True)
    first_invocation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("first_invocation_ordinal >= 1", name="ck_atlas_turn_evidence_first_ordinal"),)


class AtlasTurnStepLedgerRow(OrmBase):
    __tablename__ = "atlas_turn_step_ledger"

    step_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_atlas_turn_step_ordinal"),
        CheckConstraint("status IN ('started','completed','failed')", name="ck_atlas_turn_step_status"),
        CheckConstraint("input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_step_digest"),
        UniqueConstraint("execution_id", "ordinal", name="uq_atlas_turn_step_ordinal"),
    )


class AtlasTurnToolLedgerRow(OrmBase):
    __tablename__ = "atlas_turn_tool_ledger"

    tool_invocation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invocation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(200), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reserve_catalog_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    reserve_document_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    reserve_search_rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    reserve_unique_evidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reserve_tool_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    result_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("invocation_ordinal >= 1", name="ck_atlas_turn_tool_ordinal"),
        CheckConstraint("arguments_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_tool_arguments_digest"),
        CheckConstraint(
            "reserve_catalog_pages >= 0 AND reserve_document_candidates >= 0 AND "
            "reserve_search_rounds >= 0 AND reserve_unique_evidence >= 0 AND "
            "reserve_tool_tokens >= 0",
            name="ck_atlas_turn_tool_reservations_nonnegative",
        ),
        CheckConstraint("result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_tool_result_digest"),
        CheckConstraint("status IN ('started','completed','failed')", name="ck_atlas_turn_tool_status"),
        UniqueConstraint("execution_id", "invocation_ordinal", name="uq_atlas_turn_tool_ordinal"),
    )


class AtlasTurnRuntimeEventRow(OrmBase):
    __tablename__ = "atlas_turn_runtime_events"

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    invocation_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_atlas_turn_runtime_event_sequence"),
        CheckConstraint(f"state IN ({EXECUTION_STATES})", name="ck_atlas_turn_runtime_event_state"),
        CheckConstraint("event_type IN ('execution_allocated','execution_accepted','context_ready','model_action_requested','tool_started','tool_completed','governance_started','terminal_completed','terminal_failed')", name="ck_atlas_turn_runtime_event_type"),
        CheckConstraint("invocation_ordinal IS NULL OR invocation_ordinal >= 1", name="ck_atlas_turn_runtime_event_invocation_ordinal"),
        UniqueConstraint("execution_id", "sequence", name="uq_atlas_turn_runtime_event_sequence"),
    )


class AtlasTurnTerminalIntentRow(OrmBase):
    __tablename__ = "atlas_turn_terminal_intents"

    terminal_intent_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    evidence_pack_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    governed_answer_draft_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    citation_binding_draft_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    audit_draft_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("intent_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_terminal_intent_digest"),)


class AtlasTurnTerminalOutcomeRow(OrmBase):
    __tablename__ = "atlas_turn_terminal_outcomes"

    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="RESTRICT"), primary_key=True
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    terminal_intent_ref: Mapped[str | None] = mapped_column(
        String(300), ForeignKey("atlas_turn_terminal_intents.terminal_intent_ref", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("outcome IN ('completed','failed')", name="ck_atlas_turn_terminal_outcome"),
        CheckConstraint("(outcome = 'completed' AND terminal_intent_ref IS NOT NULL AND failure_code IS NULL) OR (outcome = 'failed' AND terminal_intent_ref IS NULL AND failure_code IS NOT NULL)", name="ck_atlas_turn_terminal_outcome_shape"),
    )


class AtlasTurnAcceptanceResourceRow(OrmBase):
    """Runtime-owned saga intent recorded before calling a resource owner."""

    __tablename__ = "atlas_turn_acceptance_resources"

    execution_id: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("atlas_turn_executions.execution_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    resource_owner: Mapped[str] = mapped_column(String(40), primary_key=True)
    release_kind: Mapped[str] = mapped_column(String(200), nullable=False)
    staged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "resource_owner IN ('authorization','processing_pipeline','retrieval','context_engineering')",
            name="ck_atlas_turn_acceptance_resource_owner",
        ),
        UniqueConstraint(
            "execution_id", "release_kind", name="uq_atlas_turn_acceptance_release_kind"
        ),
    )


class AtlasTurnReleaseIntentRow(OrmBase):
    __tablename__ = "atlas_turn_release_intents"

    release_intent_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("atlas_turn_executions.execution_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    resource_owner: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    release_kind: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("resource_owner IN ('authorization','processing_pipeline','retrieval','context_engineering','result_governance','citation','audit')", name="ck_atlas_turn_release_intent_owner"),
        CheckConstraint("status IN ('pending','releasing','released','failed')", name="ck_atlas_turn_release_intent_status"),
        CheckConstraint("attempt_count >= 0", name="ck_atlas_turn_release_intent_attempts"),
        UniqueConstraint("execution_id", "resource_owner", "resource_ref", "release_kind", name="uq_atlas_turn_release_intent_resource"),
        UniqueConstraint("execution_id", "idempotency_key", name="uq_atlas_turn_release_intent_idempotency"),
        Index("ix_atlas_turn_release_intent_pending", "status", "next_attempt_at", postgresql_where=text("status IN ('pending','failed')")),
    )


class AtlasTurnRuntimeIdempotencyRow(OrmBase):
    __tablename__ = "atlas_turn_runtime_idempotency"

    scope_ref: Mapped[str] = mapped_column(String(500), primary_key=True)
    operation: Mapped[str] = mapped_column(String(50), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_execution_id: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("atlas_turn_executions.execution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_atlas_turn_runtime_idempotency_digest"),
        CheckConstraint("result_version >= 1", name="ck_atlas_turn_runtime_idempotency_version"),
        CheckConstraint("char_length(scope_ref) >= 1", name="ck_atlas_turn_runtime_idempotency_scope"),
    )


OWNER_TABLES = frozenset(
    {
        AtlasTurnExecutionRow.__tablename__, AtlasTurnExecutionLeaseRow.__tablename__, AtlasTurnBudgetCounterRow.__tablename__,
        AtlasTurnDocumentCandidateLedgerRow.__tablename__, AtlasTurnUniqueEvidenceLedgerRow.__tablename__,
        AtlasTurnStepLedgerRow.__tablename__, AtlasTurnToolLedgerRow.__tablename__, AtlasTurnRuntimeEventRow.__tablename__,
        AtlasTurnTerminalIntentRow.__tablename__, AtlasTurnTerminalOutcomeRow.__tablename__,
        AtlasTurnAcceptanceResourceRow.__tablename__, AtlasTurnReleaseIntentRow.__tablename__,
        AtlasTurnRuntimeIdempotencyRow.__tablename__,
    }
)
