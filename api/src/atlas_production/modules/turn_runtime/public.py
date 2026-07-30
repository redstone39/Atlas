from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.conversation.public import ResponseLanguage


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionState(StrEnum):
    ALLOCATED = "allocated"
    ACCEPTED = "accepted"
    CONTEXT_READY = "context_ready"
    AWAITING_MODEL_ACTION = "awaiting_model_action"
    TOOL_PENDING = "tool_pending"
    TOOL_COMPLETED = "tool_completed"
    GOVERNING_RESULT = "governing_result"
    MATERIALIZING_TERMINAL = "materializing_terminal"
    TERMINAL_COMPLETED = "terminal_completed"
    TERMINAL_FAILED = "terminal_failed"


TERMINAL_STATES = frozenset({ExecutionState.TERMINAL_COMPLETED, ExecutionState.TERMINAL_FAILED})


class TurnRuntimeError(RuntimeError):
    """Base class for typed runtime owner failures."""


class TurnRuntimeReplayConflict(TurnRuntimeError):
    pass


class TurnRuntimeCurrentnessConflict(TurnRuntimeError):
    pass


class TurnRuntimeLeaseConflict(TurnRuntimeCurrentnessConflict):
    pass


class TurnRuntimeBudgetExceeded(TurnRuntimeError):
    pass


class TurnRuntimeTerminalConflict(TurnRuntimeCurrentnessConflict):
    pass


class LeasePolicyV1(_StrictModel):
    heartbeat_interval_seconds: int = Field(default=5, ge=1)
    ttl_seconds: int = Field(default=15, ge=2)
    failure_sweep_interval_seconds: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def require_heartbeat_before_expiry(self) -> "LeasePolicyV1":
        if self.heartbeat_interval_seconds >= self.ttl_seconds:
            raise ValueError("heartbeat interval must be shorter than lease ttl")
        return self


class RoutePolicyV1(_StrictModel):
    max_tool_invocations: int = Field(default=12, ge=0)
    max_catalog_pages: int = Field(default=5, ge=0)
    max_search_rounds: int = Field(default=6, ge=0)
    max_unique_evidence: int = Field(default=40, ge=0)
    max_provider_invocations: int = Field(default=14, ge=2)
    context_token_budget: int = Field(default=272000, ge=1)
    tool_token_budget: int = Field(default=64000, ge=1)
    deadline_seconds: int = Field(default=240, ge=1)

    @model_validator(mode="after")
    def reserve_provider_rounds_for_initial_and_terminal_actions(self) -> "RoutePolicyV1":
        if self.max_provider_invocations < self.max_tool_invocations + 2:
            raise ValueError("provider invocation budget must cover tool rounds plus initial and terminal actions")
        return self


class TurnRouteSnapshotV2(_StrictModel):
    route_id: Identity
    route_revision: int = Field(ge=1)
    runtime_policy_revision: int = Field(ge=1)
    tokenizer_profile: Identity
    context_window_tokens: int = Field(ge=1)
    max_input_tokens_per_invocation: int = Field(ge=1)
    max_output_tokens_per_invocation: int = Field(ge=1)
    max_tool_result_tokens_per_execution: int = Field(ge=1)
    max_total_tokens_per_conversation: int = Field(ge=1)

    @model_validator(mode="after")
    def require_legal_window(self) -> "TurnRouteSnapshotV2":
        if (
            self.max_input_tokens_per_invocation
            + self.max_output_tokens_per_invocation
            > self.context_window_tokens
        ):
            raise ValueError("route input and output limits exceed context window")
        return self


class BudgetSnapshotV1(_StrictModel):
    tool_invocations: int = Field(ge=0)
    catalog_pages: int = Field(ge=0)
    document_candidates: int = Field(ge=0)
    search_rounds: int = Field(ge=0)
    unique_evidence: int = Field(ge=0)
    provider_invocations: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    tool_tokens: int = Field(ge=0)


class ExecutionLeaseV1(_StrictModel):
    execution_id: Identity
    holder_id: Identity
    lease_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    acquired_at: AwareDatetime
    heartbeat_at: AwareDatetime
    expires_at: AwareDatetime


class ExecutionSnapshotV1(_StrictModel):
    execution_id: Identity
    turn_id: Identity
    conversation_id: Identity
    actor_id: Identity
    state: ExecutionState
    version: int = Field(ge=1)
    policy: RoutePolicyV1
    route: TurnRouteSnapshotV2
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_language: ResponseLanguage
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    lease: ExecutionLeaseV1
    budget: BudgetSnapshotV1
    grant_ref: OpaqueRef | None = None
    catalog_ref: OpaqueRef | None = None
    context_pack_ref: OpaqueRef | None = None
    terminal_commit_intent_ref: OpaqueRef | None = None
    terminal_failure_code: str | None = None
    deadline_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def require_guidance_snapshot_shape(self) -> "ExecutionSnapshotV1":
        if (self.applied_guidance_revision == 0) != (
            self.applied_guidance_digest is None
        ):
            raise ValueError(
                "guidance revision zero requires null digest and positive revision requires digest"
            )
        return self


class AllocateExecutionV1(_StrictModel):
    execution_id: Identity
    turn_id: Identity
    conversation_id: Identity
    actor_id: Identity
    holder_id: Identity
    route_policy: RoutePolicyV1
    route: TurnRouteSnapshotV2
    lease_policy: LeasePolicyV1
    idempotency_key: Identity
    operation: Literal["create_turn", "retry_turn"]
    retry_of_turn_id: Identity | None
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_language: ResponseLanguage
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_retry_source_only_for_retry(self) -> "AllocateExecutionV1":
        if (self.operation == "retry_turn") != (self.retry_of_turn_id is not None):
            raise ValueError("retry operation requires exactly one retry source turn")
        if (self.applied_guidance_revision == 0) != (
            self.applied_guidance_digest is None
        ):
            raise ValueError(
                "guidance revision zero requires null digest and positive revision requires digest"
            )
        return self


class AcceptExecutionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    grant_ref: OpaqueRef
    catalog_ref: OpaqueRef


class StageAcceptanceResourceV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    resource_owner: Literal[
        "authorization", "processing_pipeline", "retrieval", "context_engineering"
    ]
    release_kind: Literal[
        "release_turn_grant",
        "release_generation_retention",
        "release_knowledge_catalog",
        "release_context_pack",
    ]


class BindContextV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    context_pack_ref: OpaqueRef


class RequestModelActionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    context_tokens: int = Field(ge=0)
    contract_repair: bool = False


class BeginToolInvocationV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    tool_invocation_id: Identity
    invocation_ordinal: int = Field(ge=1)
    tool_name: Identity
    schema_version: Identity
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reserve_catalog_pages: int = Field(ge=0)
    reserve_document_candidates: int = Field(ge=0)
    reserve_search_rounds: int = Field(ge=0)
    reserve_unique_evidence: int = Field(ge=0)
    reserve_tool_tokens: int = Field(ge=0)


class CompleteToolInvocationV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    tool_invocation_id: Identity
    invocation_ordinal: int = Field(ge=1)
    result_ref: OpaqueRef
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_candidate_handles: list[Identity] = Field(max_length=20)
    unique_evidence_identities: list[Identity] = Field(max_length=20)
    catalog_pages: int = Field(ge=0)
    search_rounds: int = Field(ge=0)
    tool_tokens: int = Field(ge=0)


class BeginResultGovernanceV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    finalize_action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrepareTerminalV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    evidence_pack_ref: OpaqueRef
    governed_answer_draft_ref: OpaqueRef
    citation_binding_draft_ref: OpaqueRef
    audit_draft_ref: OpaqueRef


class CommitTerminalV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    terminal_commit_intent_ref: OpaqueRef


class FailCarrierExecutionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    holder_id: Identity
    expected_lease_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    failure_code: Literal[
        "carrier_shutdown",
        "contract_violation",
        "budget_exhausted",
        "deadline_exceeded",
        "provider_failed",
        "context_limit_exceeded",
        "summary_generation_failed",
        "resolver_failed",
        "rewrite_failed",
        "tool_failed",
        "terminal_materialization_failed",
    ]
    detected_by: Literal["carrier", "runtime_validator"]


class FinalizeExpiredExecutionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    expected_lease_version: int = Field(ge=1)
    failure_code: Literal["execution_carrier_lost", "lease_expired"]
    detected_by: Literal["lease_sweep", "startup_sweep"]


class RenewExecutionLeaseV1(_StrictModel):
    execution_id: Identity
    expected_lease_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    holder_id: Identity


class ReleaseIntentV1(_StrictModel):
    release_intent_id: Identity
    execution_id: Identity
    resource_owner: Literal["authorization", "processing_pipeline", "retrieval", "context_engineering", "result_governance", "citation", "audit"]
    resource_ref: OpaqueRef
    release_kind: Identity
    status: Literal["pending", "releasing", "released", "failed"]
    attempt_count: int = Field(ge=0)
    next_attempt_at: AwareDatetime | None = None


class CompleteReleaseIntentV1(_StrictModel):
    release_intent_id: Identity
    expected_status: Literal["pending", "releasing", "failed"]
    outcome: Literal["released", "failed"]
    failure_code: str | None = None


class RuntimeEventV1(_StrictModel):
    event_id: Identity
    execution_id: Identity
    sequence: int = Field(ge=1)
    event_type: Literal[
        "execution_allocated",
        "execution_accepted",
        "context_ready",
        "model_action_requested",
        "tool_started",
        "tool_completed",
        "governance_started",
        "terminal_completed",
        "terminal_failed",
    ]
    state: ExecutionState
    invocation_ordinal: int | None = Field(default=None, ge=1)
    result_ref: OpaqueRef | None = None
    failure_code: str | None = None
    created_at: AwareDatetime


class TerminalOutcomeV1(_StrictModel):
    execution_id: Identity
    outcome: Literal["completed", "failed"]
    terminal_commit_intent_ref: OpaqueRef | None = None
    evidence_pack_ref: OpaqueRef | None = None
    governed_answer_draft_ref: OpaqueRef | None = None
    citation_binding_draft_ref: OpaqueRef | None = None
    audit_draft_ref: OpaqueRef | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)
    committed_at: AwareDatetime

    @model_validator(mode="after")
    def require_exact_outcome_shape(self) -> "TerminalOutcomeV1":
        completed_refs = (
            self.terminal_commit_intent_ref,
            self.evidence_pack_ref,
            self.governed_answer_draft_ref,
            self.citation_binding_draft_ref,
            self.audit_draft_ref,
        )
        if self.outcome == "completed":
            if any(value is None for value in completed_refs) or self.failure_code is not None:
                raise ValueError("completed terminal outcome requires immutable refs only")
        elif any(value is not None for value in completed_refs) or self.failure_code is None:
            raise ValueError("failed terminal outcome requires only failure_code")
        return self


class TurnRuntimeOwner(Protocol):
    def find_execution(
        self, execution_id: Identity
    ) -> ExecutionSnapshotV1 | None: ...

    def snapshot(self, execution_id: Identity) -> ExecutionSnapshotV1: ...

    def terminal_outcome(self, execution_id: Identity) -> TerminalOutcomeV1 | None: ...

    def allocate(self, command: AllocateExecutionV1) -> ExecutionSnapshotV1: ...

    def stage_acceptance_resource(self, command: StageAcceptanceResourceV1) -> None: ...

    def accept(self, command: AcceptExecutionV1) -> ExecutionSnapshotV1: ...

    def bind_context(self, command: BindContextV1) -> ExecutionSnapshotV1: ...

    def request_model_action(self, command: RequestModelActionV1) -> ExecutionSnapshotV1: ...

    def begin_tool(self, command: BeginToolInvocationV1) -> ExecutionSnapshotV1: ...

    def complete_tool(self, command: CompleteToolInvocationV1) -> ExecutionSnapshotV1: ...

    def begin_governance(self, command: BeginResultGovernanceV1) -> ExecutionSnapshotV1: ...

    def prepare_terminal(self, command: PrepareTerminalV1) -> ExecutionSnapshotV1: ...

    def commit_terminal(self, command: CommitTerminalV1) -> ExecutionSnapshotV1: ...

    def fail_carrier(self, command: FailCarrierExecutionV1) -> ExecutionSnapshotV1: ...

    def finalize_expired(self, command: FinalizeExpiredExecutionV1) -> ExecutionSnapshotV1: ...

    def renew_lease(self, command: RenewExecutionLeaseV1) -> ExecutionLeaseV1: ...

    def fail_expired_leases(self, *, limit: int) -> list[ExecutionSnapshotV1]: ...

    def pending_release_intents(self, *, limit: int) -> list[ReleaseIntentV1]: ...

    def complete_release_intent(self, command: CompleteReleaseIntentV1) -> ReleaseIntentV1: ...

    def events(self, execution_id: str, *, after_sequence: int = 0) -> list[RuntimeEventV1]: ...


__all__ = [
    name
    for name in globals()
    if name.endswith(("V1", "V2"))
    or name.startswith("TurnRuntime")
    or name in {"ExecutionState", "TERMINAL_STATES"}
]
