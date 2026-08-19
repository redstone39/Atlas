from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogRefV1,
    PromptSkillRefV1,
)
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    AssessmentStateV2,
    DeclaredEvidenceConsistencyV1,
    EvidenceReviewReasonCodeV2,
    EvidenceReviewStatusV2,
    RetrievalStatusV1,
)
from atlas_production.modules.turn_runtime.public import (
    PromptSkillSelectionFallbackCode,
    ReasoningMode,
    ResponseLanguage,
)


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TurnExperienceCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    execution_id: Identity


class TurnExperienceRouteRefV1(_StrictModel):
    route_id: Identity
    route_revision: int = Field(ge=1)
    runtime_policy_revision: int = Field(ge=1)
    vision_route_id: Identity | None = None
    vision_route_revision: int | None = Field(default=None, ge=1)
    vision_runtime_policy_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_complete_vision_ref(self) -> "TurnExperienceRouteRefV1":
        vision_values = (
            self.vision_route_id,
            self.vision_route_revision,
            self.vision_runtime_policy_revision,
        )
        if any(value is not None for value in vision_values) and any(
            value is None for value in vision_values
        ):
            raise ValueError("vision route ref must be wholly present or absent")
        return self


class TurnExperienceUsageV1(_StrictModel):
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


class TurnExperienceSkillSelectionV1(_StrictModel):
    node: Literal["deep_initial_planner", "deep_replanner"]
    plan_generation: int = Field(ge=1, le=4)
    status: Literal["not_applicable", "selected", "baseline_fallback"]
    selected_skills: list[PromptSkillRefV1] = Field(default_factory=list, max_length=8)
    fallback_code: PromptSkillSelectionFallbackCode | None = None
class TurnExperienceExecutionSkillSelectionV1(_StrictModel):
    category: Literal["understanding", "answer"]
    node: Literal["resolver", "answer_candidate"]
    candidate_ordinal: int | None = Field(default=None, ge=1, le=5)
    candidate_kind: Literal["normal", "limit_final"] | None = None
    status: Literal["not_applicable", "selected", "baseline_fallback"]
    selected_skills: list[PromptSkillRefV1] = Field(default_factory=list, max_length=8)
    fallback_code: PromptSkillSelectionFallbackCode | None = None




class TurnExperiencePlanGenerationV1(_StrictModel):
    generation: int = Field(ge=1, le=4)
    parent_generation: int | None = Field(default=None, ge=1, le=3)
    pending_count: int = Field(ge=0, le=8)
    completed_count: int = Field(ge=0, le=8)
    skipped_count: int = Field(ge=0, le=8)

    @model_validator(mode="after")
    def require_nonempty_plan(self) -> "TurnExperiencePlanGenerationV1":
        if self.pending_count + self.completed_count + self.skipped_count < 1:
            raise ValueError("plan generation must contain at least one item")
        return self


class TurnExperienceEvaluationV1(_StrictModel):
    cycle: int = Field(ge=1, le=4)
    verdict: Literal["accept", "revise_only", "research_then_revise", "unavailable"]
    finding_codes: list[Identity] = Field(default_factory=list, max_length=8)
    rubric_version: Literal["atlas-process-rubric-v1"] | None = None
    plan_coverage: int | None = Field(default=None, ge=0, le=2)
    evidence_handling: int | None = Field(default=None, ge=0, le=2)
    conflict_handling: int | None = Field(default=None, ge=0, le=2)
    gap_resolution: int | None = Field(default=None, ge=0, le=2)
    revision_completion: int | None = Field(default=None, ge=0, le=2)
    total: int | None = Field(default=None, ge=0, le=10)
    unavailable_reason: Literal[
        "provider_unavailable", "budget_exhausted", "deadline_exceeded"
    ] | None = None

    @model_validator(mode="after")
    def require_score_shape(self) -> "TurnExperienceEvaluationV1":
        score_values = (
            self.rubric_version,
            self.plan_coverage,
            self.evidence_handling,
            self.conflict_handling,
            self.gap_resolution,
            self.revision_completion,
            self.total,
        )
        unavailable = self.verdict == "unavailable"
        if unavailable:
            if self.unavailable_reason is None or any(
                value is not None for value in score_values
            ):
                raise ValueError("unavailable evaluation requires only a safe reason")
        elif self.unavailable_reason is not None or any(
            value is None for value in score_values
        ):
            raise ValueError("available evaluation requires the complete process score")
        return self


class TurnExperienceCorrectionV1(_StrictModel):
    cycle: int = Field(ge=1, le=3)
    kind: Literal["revise_only", "research_then_revise"]
    triggering_evaluation: int = Field(ge=1, le=3)
    plan_generation: int | None = Field(default=None, ge=2, le=4)
    tool_invocation_start: int | None = Field(default=None, ge=1)
    tool_invocation_end: int | None = Field(default=None, ge=1)
    result_evaluation: int = Field(ge=2, le=4)
    addressed_finding_codes: list[Identity] = Field(default_factory=list, max_length=8)


class TurnExperienceEvidenceCheckV1(_StrictModel):
    ordinal: int = Field(ge=1, le=5)
    candidate_kind: Literal["normal", "limit_final"]
    linked_evaluation_cycle: int | None = Field(default=None, ge=1, le=4)
    consistency: Literal[
        "aligned", "conflict", "insufficient", "not_applicable", "unavailable"
    ]
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    candidate_disposition: Literal[
        "pending", "accepted", "revised", "degraded", "limit_finalized"
    ]
    answer_digest: Digest
    declared_subset_digest: Digest
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None
    visual_image_digests: list[Digest] = Field(default_factory=list)


class TurnExperienceDeepTraceV1(_StrictModel):
    prompt_skill_catalog: PromptSkillCatalogRefV1
    trace_revision: int = Field(ge=1)
    trace_digest: Digest
    parent_trace_digest: Digest | None = None
    status: Literal["planning", "running", "completed", "degraded", "failed"]
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
    skill_selections: list[TurnExperienceSkillSelectionV1] = Field(
        default_factory=list, max_length=4
    )
    plans: list[TurnExperiencePlanGenerationV1] = Field(default_factory=list, max_length=4)
    evaluations: list[TurnExperienceEvaluationV1] = Field(default_factory=list, max_length=4)
    corrections: list[TurnExperienceCorrectionV1] = Field(default_factory=list, max_length=3)
    evidence_checks: list[TurnExperienceEvidenceCheckV1] = Field(
        default_factory=list, max_length=5
    )
    limit_finalization_triggering_evaluation: int | None = Field(
        default=None, ge=1, le=4
    )


class TurnExperienceGovernanceV1(_StrictModel):
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    retrieval_status: RetrievalStatusV1
    evidence_review_status: EvidenceReviewStatusV2
    evidence_review_reason_codes: list[EvidenceReviewReasonCodeV2] = Field(
        min_length=1, max_length=7
    )
    declared_evidence_count: int = Field(ge=0, le=100)
    resolved_evidence_count: int = Field(ge=0, le=100)
    unresolved_evidence_count: int = Field(ge=0, le=100)
    declared_evidence_reason_codes: list[str] = Field(default_factory=list, max_length=100)
    assessment_state: AssessmentStateV2
    assessment_reason_code: AssessmentReasonCodeV2
    assessment_version: Literal["provisional-declared-evidence-v1"]
    assessment_consistency: DeclaredEvidenceConsistencyV1
    assessment_answer_digest: Digest
    assessment_declared_subset_digest: Digest
    assessment_visual_image_digests: list[Digest] = Field(default_factory=list)
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None
    assessment_success_count: int = Field(ge=0, le=100)
    assessment_failure_count: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def require_derived_counts(self) -> "TurnExperienceGovernanceV1":
        if self.resolved_evidence_count + self.unresolved_evidence_count != self.declared_evidence_count:
            raise ValueError("declared evidence counts must reconcile")
        return self


class TurnExperienceTerminalV1(_StrictModel):
    terminal_commit_intent_ref: OpaqueRef
    evidence_pack_ref: OpaqueRef
    governed_answer_draft_ref: OpaqueRef
    citation_binding_draft_ref: OpaqueRef
    audit_draft_ref: OpaqueRef
    committed_at: AwareDatetime


class MaterializeTurnExperienceV1(_StrictModel):
    experience_ref: OpaqueRef
    schema_version: Literal["turn-experience-v1"] = "turn-experience-v1"
    execution_id: Identity
    turn_id: Identity
    input_digest: Digest
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: Digest | None = None
    response_language: ResponseLanguage
    reasoning_mode: ReasoningMode
    route: TurnExperienceRouteRefV1
    usage: TurnExperienceUsageV1
    prompt_skill_selections: list[
        TurnExperienceExecutionSkillSelectionV1
    ] = Field(max_length=6)
    terminal: TurnExperienceTerminalV1
    governance: TurnExperienceGovernanceV1
    deep_trace: TurnExperienceDeepTraceV1 | None = None
    idempotency_key: Identity

    @model_validator(mode="after")
    def require_identity_and_mode(self) -> "MaterializeTurnExperienceV1":
        if self.experience_ref != f"turn-experience:{self.execution_id}:v1":
            raise ValueError("experience_ref must be derived from execution identity")
        if self.idempotency_key != f"{self.execution_id}:turn-experience-v1":
            raise ValueError("idempotency_key must be derived from execution identity")
        if (self.applied_guidance_revision == 0) != (
            self.applied_guidance_digest is None
        ):
            raise ValueError("guidance revision and digest disagree")
        if self.reasoning_mode == "standard" and self.deep_trace is not None:
            raise ValueError("standard reasoning cannot carry a deep trace")
        if self.terminal.governed_answer_draft_ref != self.governance.governed_answer_draft_ref:
            raise ValueError("terminal and governance draft refs disagree")
        return self


class TurnExperienceV1(MaterializeTurnExperienceV1):
    scan_sequence: int = Field(ge=1)
    digest: Digest
    created_at: AwareDatetime


class TurnExperienceStore(Protocol):
    def materialize(self, command: MaterializeTurnExperienceV1) -> TurnExperienceV1: ...

    def read_for_execution(
        self, execution_id: Identity, schema_version: str
    ) -> TurnExperienceV1 | None: ...

    def list_after(
        self, cursor: TurnExperienceCursorV1 | None, limit: int
    ) -> list[TurnExperienceV1]: ...


__all__ = [
    "MaterializeTurnExperienceV1",
    "TurnExperienceCorrectionV1",
    "TurnExperienceCursorV1",
    "TurnExperienceDeepTraceV1",
    "TurnExperienceExecutionSkillSelectionV1",
    "TurnExperienceEvaluationV1",
    "TurnExperienceEvidenceCheckV1",
    "TurnExperienceGovernanceV1",
    "TurnExperiencePlanGenerationV1",
    "TurnExperienceRouteRefV1",
    "TurnExperienceSkillSelectionV1",
    "TurnExperienceStore",
    "TurnExperienceTerminalV1",
    "TurnExperienceUsageV1",
    "TurnExperienceV1",
]
