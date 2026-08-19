from __future__ import annotations

from enum import StrEnum
import json
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.conversation.public import ReasoningMode, ResponseLanguage
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogRefV1,
    PromptSkillRefV1,
)


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MessageParamString = Annotated[str, Field(max_length=500)]
MessageParamValue = MessageParamString | int | bool | None


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
    max_model_visible_items_per_turn: int = Field(default=40, ge=0)
    max_retrieval_repairs: int = Field(default=3, ge=1, le=3)
    max_selected_anchor_pages_per_round: int = Field(default=20, ge=1, le=20)
    max_provider_invocations: int = Field(default=33, ge=6)
    max_reasoning_revision_cycles: int = Field(default=2, ge=0, le=3)
    max_schema_retries_per_turn: int = Field(default=1, ge=1, le=3)
    context_token_budget: int = Field(default=272000, ge=1)
    tool_token_budget: int = Field(default=64000, ge=1)
    tool_execution_timeout_seconds: int = Field(default=45, ge=1)
    deadline_seconds: int = Field(default=240, ge=1)

    @model_validator(mode="after")
    def reserve_provider_rounds_for_initial_and_terminal_actions(self) -> "RoutePolicyV1":
        required = self.max_tool_invocations + 6 * self.max_reasoning_revision_cycles + 9
        if self.max_provider_invocations < required:
            raise ValueError(
                "provider invocation budget must cover tools, selectors, planning, evaluation, revisions, and terminal actions"
            )
        if self.tool_execution_timeout_seconds > self.deadline_seconds:
            raise ValueError("turn deadline is shorter than tool timeout")
        return self


ReasoningPhase = Literal[
    "understanding",
    "planning",
    "researching",
    "drafting",
    "evaluating",
    "revising",
    "governing",
    "finalizing",
    "completed",
    "failed",
]
ReasoningProgressStatus = Literal["started", "completed", "degraded", "failed"]


class ReasoningPlanItemV2(_StrictModel):
    item_id: str = Field(min_length=1, max_length=32, pattern=r"^[\x21-\x7e]+$")
    summary: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "completed", "skipped"] = "pending"


class ReasoningPlanV2(_StrictModel):
    schema_version: Literal["atlas-reasoning-plan-v2"] = "atlas-reasoning-plan-v2"
    generation: int = Field(ge=1, le=4)
    parent_generation: int | None = Field(default=None, ge=1, le=3)
    next_objective: str = Field(min_length=1, max_length=160)
    completion_condition: str = Field(min_length=1, max_length=160)
    items: list[ReasoningPlanItemV2] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_generation_parent(self) -> "ReasoningPlanV2":
        if self.generation == 1 and self.parent_generation is not None:
            raise ValueError("initial plan cannot have a parent generation")
        if self.generation > 1 and self.parent_generation != self.generation - 1:
            raise ValueError("replanned generation must reference its immediate parent")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("plan item ids must be unique within a generation")
        return self


class ProcessScoreV1(_StrictModel):
    rubric_version: Literal["atlas-process-rubric-v1"] = "atlas-process-rubric-v1"
    plan_coverage: int = Field(ge=0, le=2)
    evidence_handling: int = Field(ge=0, le=2)
    conflict_handling: int = Field(ge=0, le=2)
    gap_resolution: int = Field(ge=0, le=2)
    revision_completion: int = Field(ge=0, le=2)
    total: int = Field(ge=0, le=10)

    @model_validator(mode="after")
    def require_derived_total(self) -> "ProcessScoreV1":
        expected = (
            self.plan_coverage
            + self.evidence_handling
            + self.conflict_handling
            + self.gap_resolution
            + self.revision_completion
        )
        if self.total != expected:
            raise ValueError("process score total must equal the rubric dimensions")
        return self


class ReasoningEvaluationV1(_StrictModel):
    cycle: int = Field(ge=1, le=4)
    verdict: Literal["accept", "revise_only", "research_then_revise", "unavailable"]
    finding_codes: list[Identity] = Field(default_factory=list, max_length=8)
    summary: str | None = Field(default=None, max_length=240)
    score: ProcessScoreV1 | None = None
    unavailable_reason: Literal[
        "provider_unavailable", "budget_exhausted", "deadline_exceeded"
    ] | None = None

    @model_validator(mode="after")
    def require_evaluation_shape(self) -> "ReasoningEvaluationV1":
        unavailable = self.verdict == "unavailable"
        if unavailable != (self.unavailable_reason is not None):
            raise ValueError("unavailable evaluation requires exactly one safe reason")
        if unavailable == (self.score is not None):
            raise ValueError("only an available evaluation can have a process score")
        return self


class ReasoningCorrectionV2(_StrictModel):
    cycle: int = Field(ge=1, le=3)
    kind: Literal["revise_only", "research_then_revise"]
    triggering_evaluation: int = Field(ge=1, le=3)
    plan_generation: int | None = Field(default=None, ge=2, le=4)
    tool_invocation_start: int | None = Field(default=None, ge=1)
    tool_invocation_end: int | None = Field(default=None, ge=1)
    result_evaluation: int = Field(ge=2, le=4)
    addressed_finding_codes: list[Identity] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def require_correction_shape(self) -> "ReasoningCorrectionV2":
        research = self.kind == "research_then_revise"
        if research != (self.plan_generation is not None):
            raise ValueError("research correction requires exactly one plan generation")
        if (self.tool_invocation_start is None) != (self.tool_invocation_end is None):
            raise ValueError("tool invocation span requires both ordinals")
        if research and self.tool_invocation_start is None:
            raise ValueError("research correction requires a tool invocation span")
        if not research and self.tool_invocation_start is not None:
            raise ValueError("revise-only correction cannot record tool invocations")
        if (
            self.tool_invocation_start is not None
            and self.tool_invocation_end is not None
            and self.tool_invocation_start > self.tool_invocation_end
        ):
            raise ValueError("tool invocation span is reversed")
        if self.result_evaluation != self.triggering_evaluation + 1:
            raise ValueError("correction must link adjacent evaluations")
        return self


class ReasoningLimitFinalizationV2(_StrictModel):
    triggering_evaluation: int = Field(ge=1, le=4)
    summary: str = Field(min_length=1, max_length=240)


class ProvisionalEvidenceCheckV1(_StrictModel):
    ordinal: int = Field(ge=1, le=5)
    candidate_kind: Literal["normal", "limit_final"]
    linked_evaluation_cycle: int | None = Field(default=None, ge=1, le=4)
    consistency: Literal[
        "aligned", "conflict", "insufficient", "not_applicable", "unavailable"
    ]
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    candidate_disposition: Literal[
        "pending", "accepted", "revised", "degraded", "limit_finalized"
    ] = "pending"
    answer_digest: Digest
    declared_subset_digest: Digest
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None
    visual_image_digests: list[Digest] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def require_candidate_link(self) -> "ProvisionalEvidenceCheckV1":
        if self.candidate_kind == "normal" and self.linked_evaluation_cycle is None:
            raise ValueError("normal provisional check requires an evaluation cycle")
        if self.candidate_kind == "limit_final" and self.linked_evaluation_cycle is not None:
            raise ValueError("limit-final provisional check cannot link an evaluation")
        return self


PromptSkillSelectionFallbackCode = Literal[
    "selector_unavailable",
    "selector_contract_invalid",
    "selection_outside_catalog",
    "selected_skill_integrity_error",
    "selected_skill_context_exceeded",
    "selected_skill_trace_exceeded",
]


class PromptSkillSelectionTraceV1(_StrictModel):
    node: Literal["deep_initial_planner", "deep_replanner"]
    plan_generation: int = Field(ge=1, le=4)
    status: Literal["not_applicable", "selected", "baseline_fallback"]
    selected_skills: list[PromptSkillRefV1] = Field(default_factory=list)
    fallback_code: PromptSkillSelectionFallbackCode | None = None

    @model_validator(mode="after")
    def require_status_shape(self) -> "PromptSkillSelectionTraceV1":
        if self.status == "baseline_fallback":
            if self.fallback_code is None:
                raise ValueError("baseline fallback requires a fallback code")
        elif self.fallback_code is not None:
            raise ValueError("non-fallback selection cannot have a fallback code")
        if self.status != "selected" and self.selected_skills:
            raise ValueError("only selected status may include selected skills")
        identities = [
            (skill.category, skill.name, skill.revision, skill.content_digest)
            for skill in self.selected_skills
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("selected skill refs must be unique and ordered")
        if any(skill.category != "planner" for skill in self.selected_skills):
            raise ValueError("planner selections require planner skill refs")
        return self
class ExecutionPromptSkillSelectionTraceV1(_StrictModel):
    category: Literal["understanding", "answer"]
    node: Literal["resolver", "answer_candidate"]
    candidate_ordinal: int | None = Field(default=None, ge=1, le=5)
    candidate_kind: Literal["normal", "limit_final"] | None = None
    status: Literal["not_applicable", "selected", "baseline_fallback"]
    selected_skills: list[PromptSkillRefV1] = Field(default_factory=list)
    fallback_code: PromptSkillSelectionFallbackCode | None = None

    @model_validator(mode="after")
    def require_execution_selection_shape(
        self,
    ) -> "ExecutionPromptSkillSelectionTraceV1":
        if self.node == "resolver":
            if (
                self.category != "understanding"
                or self.candidate_ordinal is not None
                or self.candidate_kind is not None
            ):
                raise ValueError("resolver selection requires understanding identity only")
        elif (
            self.category != "answer"
            or self.candidate_ordinal is None
            or self.candidate_kind is None
        ):
            raise ValueError("answer selection requires candidate identity")
        if self.status == "baseline_fallback":
            if self.fallback_code is None:
                raise ValueError("baseline fallback requires a fallback code")
        elif self.fallback_code is not None:
            raise ValueError("non-fallback selection cannot have a fallback code")
        if self.status != "selected" and self.selected_skills:
            raise ValueError("only selected status may include selected skills")
        identities = [
            (skill.category, skill.name, skill.revision, skill.content_digest)
            for skill in self.selected_skills
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("selected skill refs must be unique and ordered")
        if any(skill.category != self.category for skill in self.selected_skills):
            raise ValueError("selected skill refs must match the selection category")
        return self




class ReasoningTraceV4(_StrictModel):
    schema_version: Literal["atlas-reasoning-trace-v4"] = "atlas-reasoning-trace-v4"
    prompt_skill_catalog: PromptSkillCatalogRefV1
    skill_selections: list[PromptSkillSelectionTraceV1] = Field(
        default_factory=list, max_length=4
    )
    trace_revision: int = Field(ge=1)
    trace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_trace_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["deep"] = "deep"
    status: Literal["planning", "running", "completed", "degraded", "failed"]
    plans: list[ReasoningPlanV2] = Field(default_factory=list, max_length=4)
    evaluations: list[ReasoningEvaluationV1] = Field(default_factory=list, max_length=4)
    corrections: list[ReasoningCorrectionV2] = Field(default_factory=list, max_length=3)
    provisional_evidence_checks: list[ProvisionalEvidenceCheckV1] = Field(
        default_factory=list, max_length=5
    )
    limit_finalization: ReasoningLimitFinalizationV2 | None = None
    termination_reason: Literal[
        "completed",
        "planner_failed",
        "evaluator_unavailable",
        "provisional_evidence_unavailable",
        "replanner_failed",
        "correction_limit_reached",
        "budget_exhausted",
        "deadline_exceeded",
        "execution_failed",
    ] | None = None

    @model_validator(mode="after")
    def require_bounded_trace(self) -> "ReasoningTraceV4":
        if self.prompt_skill_catalog.category != "planner":
            raise ValueError("reasoning trace requires a planner skill catalog")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 32768:
            raise ValueError("reasoning trace exceeds 32 KiB")
        if self.trace_revision == 1 and self.parent_trace_digest is not None:
            raise ValueError("initial trace cannot have a parent digest")
        if self.trace_revision > 1 and self.parent_trace_digest is None:
            raise ValueError("updated trace requires a parent digest")
        if self.status in {"completed", "degraded", "failed"} and self.termination_reason is None:
            raise ValueError("terminal reasoning trace requires a termination reason")
        for index, plan in enumerate(self.plans, start=1):
            if plan.generation != index:
                raise ValueError("plan generations must be contiguous and ordered")
            if index == 1:
                continue
            previous = {item.item_id: item.status for item in self.plans[index - 2].items}
            current = {item.item_id: item.status for item in plan.items}
            for item_id, prior_status in previous.items():
                if prior_status == "pending" and item_id not in current:
                    raise ValueError("pending plan items must be retained or closed")
                if prior_status in {"completed", "skipped"} and current.get(item_id) == "pending":
                    raise ValueError("closed plan items cannot be reopened")
        if [evaluation.cycle for evaluation in self.evaluations] != list(
            range(1, len(self.evaluations) + 1)
        ):
            raise ValueError("evaluations must be contiguous and ordered")
        if [correction.cycle for correction in self.corrections] != list(
            range(1, len(self.corrections) + 1)
        ):
            raise ValueError("corrections must be contiguous and ordered")
        if [
            selection.plan_generation for selection in self.skill_selections
        ] != list(range(1, len(self.skill_selections) + 1)):
            raise ValueError("skill selection generations must be contiguous and ordered")
        if self.skill_selections:
            first = self.skill_selections[0]
            if first.node != "deep_initial_planner":
                raise ValueError("first skill selection must be the initial planner")
            if any(
                selection.node != "deep_replanner"
                for selection in self.skill_selections[1:]
            ):
                raise ValueError("subsequent skill selections must be replanner entries")
        if [check.ordinal for check in self.provisional_evidence_checks] != list(
            range(1, len(self.provisional_evidence_checks) + 1)
        ):
            raise ValueError("provisional evidence checks must be contiguous and ordered")
        if self.status in {"completed", "degraded", "failed"} and any(
            check.candidate_disposition == "pending"
            for check in self.provisional_evidence_checks
        ):
            raise ValueError("terminal trace cannot retain a pending evidence check")
        if self.limit_finalization is not None and self.termination_reason != "correction_limit_reached":
            raise ValueError("limit finalization requires correction-limit termination")
        return self


class VisionRouteSnapshotV1(_StrictModel):
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
    def require_legal_window(self) -> "VisionRouteSnapshotV1":
        if (
            self.max_input_tokens_per_invocation
            + self.max_output_tokens_per_invocation
            > self.context_window_tokens
        ):
            raise ValueError(
                "vision route input and output limits exceed context window"
            )
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
    vision_route: VisionRouteSnapshotV1 | None = None

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
    model_visible_items: int = Field(ge=0)
    provider_invocations: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    tool_tokens: int = Field(ge=0)
    retrieval_repairs: int = Field(ge=0)
    schema_retries: int = Field(ge=0)


class ExecutionLeaseV1(_StrictModel):
    execution_id: Identity
    holder_id: Identity
    lease_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    acquired_at: AwareDatetime
    heartbeat_at: AwareDatetime
    expires_at: AwareDatetime


def _require_prompt_skill_catalog_shape(
    reasoning_mode: ReasoningMode,
    catalogs: list[PromptSkillCatalogRefV1],
    *,
    subject: str,
) -> None:
    expected = (
        ["understanding", "answer"]
        if reasoning_mode == "standard"
        else ["understanding", "planner", "answer"]
    )
    categories = [catalog.category for catalog in catalogs]
    if categories != expected:
        raise ValueError(
            f"{subject} requires prompt skill catalogs in canonical mode order"
        )


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
    reasoning_mode: ReasoningMode = "standard"
    reasoning_trace: ReasoningTraceV4 | None = None
    prompt_skill_catalogs: list[PromptSkillCatalogRefV1] = Field(
        min_length=2, max_length=3
    )
    prompt_skill_selections: list[ExecutionPromptSkillSelectionTraceV1] = Field(
        default_factory=list, max_length=6
    )
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
    def require_snapshot_shape(self) -> "ExecutionSnapshotV1":
        if (self.applied_guidance_revision == 0) != (
            self.applied_guidance_digest is None
        ):
            raise ValueError(
                "guidance revision zero requires null digest and positive revision requires digest"
            )
        _require_prompt_skill_catalog_shape(
            self.reasoning_mode,
            self.prompt_skill_catalogs,
            subject="execution",
        )
        if self.reasoning_mode == "standard" and self.reasoning_trace is not None:
            raise ValueError("standard execution cannot carry a reasoning trace")
        if (
            self.reasoning_mode == "deep"
            and self.reasoning_trace is not None
            and self.reasoning_trace.prompt_skill_catalog
            != self.prompt_skill_catalogs[1]
        ):
            raise ValueError(
                "deep reasoning trace must match the execution planner catalog"
            )
        if self.prompt_skill_selections:
            if self.prompt_skill_selections[0].node != "resolver":
                raise ValueError("execution skill selections must begin with Resolver")
            if any(
                selection.node == "resolver"
                for selection in self.prompt_skill_selections[1:]
            ):
                raise ValueError("execution can record Resolver selection only once")
            answer_ordinals = [
                selection.candidate_ordinal
                for selection in self.prompt_skill_selections[1:]
            ]
            if answer_ordinals != list(range(1, len(answer_ordinals) + 1)):
                raise ValueError(
                    "answer candidate skill selections must be contiguous and ordered"
                )
        encoded = json.dumps(
            [
                selection.model_dump(mode="json")
                for selection in self.prompt_skill_selections
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 32768:
            raise ValueError("execution prompt skill selections exceed 32 KiB")
        missing_required_resolver_selection = (
            self.state not in {ExecutionState.ALLOCATED, ExecutionState.ACCEPTED}
            and not (
                self.state is ExecutionState.TERMINAL_FAILED
                and self.context_pack_ref is None
            )
            and not self.prompt_skill_selections
        )
        if missing_required_resolver_selection:
            raise ValueError("context-ready execution requires Resolver selection")
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
    reasoning_mode: ReasoningMode = "standard"
    prompt_skill_catalogs: list[PromptSkillCatalogRefV1] = Field(
        min_length=2, max_length=3
    )
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
        _require_prompt_skill_catalog_shape(
            self.reasoning_mode,
            self.prompt_skill_catalogs,
            subject="allocation",
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


class ReserveAcceptanceModelActionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    context_tokens: int = Field(ge=0)


class RequestModelActionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    context_tokens: int = Field(ge=0)
    contract_repair: bool = False


SchemaRetryOriginCode = Literal[
    "provider_output_decode_error",
    "provider_output_schema_error",
    "invalid_summary_output",
    "summary_output_too_large",
    "invalid_resolver_output",
    "invalid_rewrite_output",
    "deep_reasoning_plan_invalid",
    "deep_reasoning_replan_invalid",
    "deep_reasoning_evaluation_semantic_shape_invalid",
    "provisional_evidence_semantic_shape_invalid",
    "provisional_evidence_item_count_invalid",
]


class ClaimSchemaRetryV1(_StrictModel):
    execution_id: Identity
    fencing_token: int = Field(ge=1)
    claim_key: Identity
    origin_error_code: SchemaRetryOriginCode


class RecordReasoningProgressV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    trace: ReasoningTraceV4
    phase: ReasoningPhase
    progress_status: ReasoningProgressStatus
    cycle: int | None = Field(default=None, ge=1, le=4)
    message_code: Identity
    message_params: dict[Identity, MessageParamValue] = Field(
        default_factory=dict, max_length=12
    )


class RecordExecutionPromptSkillSelectionV1(_StrictModel):
    execution_id: Identity
    expected_version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    selection: ExecutionPromptSkillSelectionTraceV1


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
    reserve_model_visible_items: int = Field(ge=0)
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
    # One bounded tool result can expose at most 20 entries, each carrying its
    # own handle plus an optional page handle.
    model_visible_item_identities: list[Identity] = Field(max_length=40)
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
        "reasoning_progressed",
        "tool_started",
        "tool_completed",
        "governance_started",
        "terminal_completed",
        "terminal_failed",
        "prompt_skill_selection_recorded",
    ]
    state: ExecutionState
    invocation_ordinal: int | None = Field(default=None, ge=1)
    result_ref: OpaqueRef | None = None
    failure_code: str | None = None
    reasoning_phase: ReasoningPhase | None = None
    progress_status: ReasoningProgressStatus | None = None
    cycle: int | None = Field(default=None, ge=1, le=4)
    message_code: Identity | None = None
    message_params: dict[Identity, MessageParamValue] = Field(
        default_factory=dict, max_length=12
    )
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_reasoning_event_shape(self) -> "RuntimeEventV1":
        reasoning_fields = (
            self.reasoning_phase,
            self.progress_status,
            self.message_code,
        )
        if self.event_type == "reasoning_progressed":
            if any(value is None for value in reasoning_fields):
                raise ValueError("reasoning progress event requires safe progress fields")
        elif any(value is not None for value in (*reasoning_fields, self.cycle)) or self.message_params:
            raise ValueError("non-reasoning events cannot expose reasoning progress fields")
        return self


class TerminalCompletionCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    execution_id: Identity


class TerminalOutcomeV1(_StrictModel):
    execution_id: Identity
    scan_sequence: int = Field(ge=1)
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
    def completed_terminal_outcomes(
        self,
        *,
        after: TerminalCompletionCursorV1 | None,
        limit: int,
    ) -> list[TerminalOutcomeV1]: ...


    def allocate(self, command: AllocateExecutionV1) -> ExecutionSnapshotV1: ...

    def stage_acceptance_resource(self, command: StageAcceptanceResourceV1) -> None: ...

    def accept(self, command: AcceptExecutionV1) -> ExecutionSnapshotV1: ...

    def bind_context(self, command: BindContextV1) -> ExecutionSnapshotV1: ...

    def reserve_acceptance_model_action(
        self, command: ReserveAcceptanceModelActionV1
    ) -> ExecutionSnapshotV1: ...

    def request_model_action(self, command: RequestModelActionV1) -> ExecutionSnapshotV1: ...

    def claim_schema_retry(self, command: ClaimSchemaRetryV1) -> ExecutionSnapshotV1: ...

    def record_prompt_skill_selection(
        self, command: RecordExecutionPromptSkillSelectionV1
    ) -> ExecutionSnapshotV1: ...

    def record_reasoning_progress(
        self, command: RecordReasoningProgressV1
    ) -> ExecutionSnapshotV1: ...

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
    "ExecutionState",
    "TERMINAL_STATES",
    "TurnRuntimeError",
    "TurnRuntimeReplayConflict",
    "TurnRuntimeCurrentnessConflict",
    "TurnRuntimeLeaseConflict",
    "TurnRuntimeBudgetExceeded",
    "TurnRuntimeTerminalConflict",
    "LeasePolicyV1",
    "RoutePolicyV1",
    "ReasoningPlanItemV2",
    "ReasoningPlanV2",
    "ProcessScoreV1",
    "ReasoningEvaluationV1",
    "ReasoningCorrectionV2",
    "ReasoningLimitFinalizationV2",
    "ProvisionalEvidenceCheckV1",
    "PromptSkillSelectionFallbackCode",
    "PromptSkillSelectionTraceV1",
    "ExecutionPromptSkillSelectionTraceV1",
    "ReasoningTraceV4",
    "TurnRouteSnapshotV2",
    "VisionRouteSnapshotV1",
    "BudgetSnapshotV1",
    "ExecutionLeaseV1",
    "ExecutionSnapshotV1",
    "AllocateExecutionV1",
    "AcceptExecutionV1",
    "StageAcceptanceResourceV1",
    "BindContextV1",
    "ReserveAcceptanceModelActionV1",
    "RequestModelActionV1",
    "ClaimSchemaRetryV1",
    "RecordReasoningProgressV1",
    "RecordExecutionPromptSkillSelectionV1",
    "BeginToolInvocationV1",
    "CompleteToolInvocationV1",
    "BeginResultGovernanceV1",
    "PrepareTerminalV1",
    "CommitTerminalV1",
    "FailCarrierExecutionV1",
    "FinalizeExpiredExecutionV1",
    "RenewExecutionLeaseV1",
    "ReleaseIntentV1",
    "CompleteReleaseIntentV1",
    "RuntimeEventV1",
    "TerminalCompletionCursorV1",
    "TerminalOutcomeV1",
    "TurnRuntimeOwner",
]
