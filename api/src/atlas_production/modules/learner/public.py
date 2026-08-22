from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.conversation_review.public import (
    ConversationLearningCaseProposalV1,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCategory,
    PromptSkillRefV1,
)

Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=12_000)]

SCHEMA_VERSION = "learner-experience-v1"
LEARNER_PROMPT_REVISION = "layered-learner-v1"
MAX_CANONICAL_EXPERIENCE_BYTES = 65_536

LearnerRunStatus = Literal[
    "pending", "learning", "retryable_failed", "completed", "failed"
]
LearnerNode = Literal["understanding", "planner", "answer"]
LearnerLayerApplicability = Literal["applicable", "unavailable", "not_applicable"]
LearnerLayerVerdict = Literal["pass", "fail", "indeterminate", "not_applicable"]
LearnerLayerRelation = Literal[
    "origin",
    "propagated",
    "amplified",
    "corrected",
    "added_independent_failure",
    "none",
    "indeterminate",
    "not_applicable",
]
LearnerSkillIssueType = Literal[
    "wrong_skill_selected",
    "selected_skill_underperformed",
    "missing_suitable_skill",
    "not_skill_related",
    "indeterminate",
]
LearnerUnderperformanceCause = Literal[
    "selection_mismatch",
    "instruction_gap",
    "instruction_not_followed",
    "capability_gap",
    "not_applicable",
    "indeterminate",
]
LearnerOutcome = Literal["supported", "indeterminate", "no_learning"]
LearnerEvidenceSufficiency = Literal["complete", "partial"]
LearnerOriginStatus = Literal["confirmed", "indeterminate", "no_failure"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def learner_case_digest(
    *,
    review_ref: str,
    review_digest: str,
    case_ordinal: int,
    case: ConversationLearningCaseProposalV1,
) -> str:
    projection = {
        "case": case.model_dump(mode="json"),
        "case_ordinal": case_ordinal,
        "review_digest": review_digest,
        "review_ref": review_ref,
    }
    return hashlib.sha256(_canonical(projection)).hexdigest()


def learner_run_ref(
    *,
    review_ref: str,
    review_digest: str,
    case_ordinal: int,
    case_digest: str,
    schema_version: str = SCHEMA_VERSION,
    learner_prompt_revision: str = LEARNER_PROMPT_REVISION,
) -> str:
    projection = {
        "case_digest": case_digest,
        "case_ordinal": case_ordinal,
        "learner_prompt_revision": learner_prompt_revision,
        "review_digest": review_digest,
        "review_ref": review_ref,
        "schema_version": schema_version,
    }
    return f"learner-run:{hashlib.sha256(_canonical(projection)).hexdigest()}:v1"


def learner_experience_ref(*, run_ref: str) -> str:
    projection = {
        "learner_prompt_revision": LEARNER_PROMPT_REVISION,
        "run_ref": run_ref,
        "schema_version": SCHEMA_VERSION,
    }
    return f"learner-experience:{hashlib.sha256(_canonical(projection)).hexdigest()}:v1"


class RegisterLearnerCaseV1(_StrictModel):
    review_ref: OpaqueRef
    review_digest: Digest
    snapshot_digest: Digest
    case: ConversationLearningCaseProposalV1

    @model_validator(mode="after")
    def require_matching_ordinal(self) -> "RegisterLearnerCaseV1":
        if self.case.case_ordinal < 1:
            raise ValueError("learner case ordinal must be positive")
        return self


class LearnerSourceIdentityV1(_StrictModel):
    run_ref: OpaqueRef
    experience_ref: OpaqueRef
    schema_version: Literal["learner-experience-v1"] = SCHEMA_VERSION
    learner_prompt_revision: Literal["layered-learner-v1"] = LEARNER_PROMPT_REVISION
    review_ref: OpaqueRef
    review_digest: Digest
    snapshot_digest: Digest
    case_ordinal: int = Field(ge=1)
    case_digest: Digest
    case_title: Annotated[str, Field(min_length=1, max_length=500)]
    involved_turn_ids: list[Identity] = Field(min_length=1)
    primary_assistant_turn_id: Identity

    @model_validator(mode="after")
    def require_exact_identity(self) -> "LearnerSourceIdentityV1":
        if len(set(self.involved_turn_ids)) != len(self.involved_turn_ids):
            raise ValueError("learner involved turn ids must be ordered and unique")
        if self.primary_assistant_turn_id not in self.involved_turn_ids:
            raise ValueError("learner primary turn must be involved")
        expected_run_ref = learner_run_ref(
            review_ref=self.review_ref,
            review_digest=self.review_digest,
            case_ordinal=self.case_ordinal,
            case_digest=self.case_digest,
            schema_version=self.schema_version,
            learner_prompt_revision=self.learner_prompt_revision,
        )
        if self.run_ref != expected_run_ref:
            raise ValueError("learner run ref does not bind source identity")
        if self.experience_ref != learner_experience_ref(run_ref=self.run_ref):
            raise ValueError("learner experience ref does not bind run identity")
        return self


class LearnerSkillDiagnosisV1(_StrictModel):
    node: LearnerNode
    category: PromptSkillCategory
    issue_type: LearnerSkillIssueType
    underperformance_cause: LearnerUnderperformanceCause
    selected_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list, max_length=8)
    alternative_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list, max_length=8)
    required_capability: BoundedText
    selected_skill_assessment: BoundedText
    explanation: BoundedText
    evidence_ids: list[OpaqueRef] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_unique_refs(self) -> "LearnerSkillDiagnosisV1":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("skill evidence ids must be ordered and unique")
        selected = [tuple(ref.model_dump().values()) for ref in self.selected_skill_refs]
        alternatives = [tuple(ref.model_dump().values()) for ref in self.alternative_skill_refs]
        if len(set(selected)) != len(selected) or len(set(alternatives)) != len(alternatives):
            raise ValueError("skill refs must be ordered and unique")
        return self


class LearnerLayerDiagnosisV1(_StrictModel):
    node: LearnerNode
    applicability: LearnerLayerApplicability
    verdict: LearnerLayerVerdict
    relation: LearnerLayerRelation
    expected_behavior: BoundedText | None = None
    observed_behavior: BoundedText | None = None
    divergence: BoundedText | None = None
    propagation_effect: BoundedText | None = None
    skill_diagnosis: LearnerSkillDiagnosisV1 | None = None
    supporting_observations: list[BoundedText] = Field(default_factory=list, max_length=8)
    counterevidence: list[BoundedText] = Field(default_factory=list, max_length=8)
    unresolved_questions: list[BoundedText] = Field(default_factory=list, max_length=8)
    evidence_ids: list[OpaqueRef] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_layer_shape(self) -> "LearnerLayerDiagnosisV1":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("layer evidence ids must be ordered and unique")
        if self.applicability == "not_applicable":
            if self.verdict != "not_applicable" or self.relation != "not_applicable":
                raise ValueError("not-applicable layer requires not-applicable verdict/relation")
            if any(
                value is not None
                for value in (
                    self.expected_behavior,
                    self.observed_behavior,
                    self.divergence,
                    self.propagation_effect,
                    self.skill_diagnosis,
                )
            ) or any(
                (
                    self.supporting_observations,
                    self.counterevidence,
                    self.unresolved_questions,
                    self.evidence_ids,
                )
            ):
                raise ValueError("not-applicable layer cannot carry model diagnosis")
        else:
            if self.verdict == "not_applicable" or self.relation == "not_applicable":
                raise ValueError("applicable layer cannot use not-applicable verdict/relation")
            if self.applicability == "unavailable" and self.verdict != "indeterminate":
                raise ValueError("unavailable layer must be indeterminate")
            if self.skill_diagnosis is not None and self.skill_diagnosis.node != self.node:
                raise ValueError("skill diagnosis node must match layer node")
            if self.expected_behavior is None or self.observed_behavior is None:
                raise ValueError("inspected layer requires expected and observed behavior")
        return self


class LearnerExperienceSynthesisV1(_StrictModel):
    outcome: LearnerOutcome
    scenario_context: BoundedText
    user_goal: BoundedText
    explicit_requirements: list[BoundedText] = Field(default_factory=list, max_length=8)
    explicit_constraints: list[BoundedText] = Field(default_factory=list, max_length=8)
    expected_behavior: BoundedText
    observed_behavior: BoundedText
    user_impact: BoundedText
    correction_signal: BoundedText
    failure_statement: BoundedText
    problem_pattern: BoundedText
    trigger_conditions: list[BoundedText] = Field(default_factory=list, max_length=8)
    desired_behavior: BoundedText
    prohibited_behavior: BoundedText
    rationale: BoundedText
    applicability_boundaries: list[BoundedText] = Field(default_factory=list, max_length=8)
    counterexamples: list[BoundedText] = Field(default_factory=list, max_length=8)
    success_observations: list[BoundedText] = Field(default_factory=list, max_length=8)
    target_nodes: list[LearnerNode] = Field(default_factory=list, max_length=3)
    behavior_kinds: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=8
    )
    evidence_sufficiency: LearnerEvidenceSufficiency
    supporting_observations: list[BoundedText] = Field(default_factory=list, max_length=8)
    counterevidence: list[BoundedText] = Field(default_factory=list, max_length=8)
    unresolved_questions: list[BoundedText] = Field(default_factory=list, max_length=8)
    alternative_explanations: list[BoundedText] = Field(default_factory=list, max_length=8)
    generalization_risks: list[BoundedText] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_ordered_unique_facets(self) -> "LearnerExperienceSynthesisV1":
        if len(set(self.target_nodes)) != len(self.target_nodes):
            raise ValueError("target nodes must be ordered and unique")
        if len(set(self.behavior_kinds)) != len(self.behavior_kinds):
            raise ValueError("behavior kinds must be ordered and unique")
        return self


def resolve_learner_origin(
    layer_results: list[LearnerLayerDiagnosisV1],
) -> tuple[LearnerOriginStatus, LearnerNode | None]:
    nodes = [layer.node for layer in layer_results]
    if nodes != ["understanding", "planner", "answer"]:
        raise ValueError("learner layers must be understanding, planner, answer")
    prior_applicable_pass = True
    saw_indeterminate = False
    for layer in layer_results:
        if layer.verdict == "not_applicable":
            continue
        if layer.verdict == "indeterminate":
            saw_indeterminate = True
            prior_applicable_pass = False
            continue
        if layer.verdict == "fail":
            if prior_applicable_pass:
                return ("confirmed", layer.node)
            return ("indeterminate", None)
        if layer.verdict != "pass":
            raise ValueError("unknown learner layer verdict")
    if saw_indeterminate:
        return ("indeterminate", None)
    return ("no_failure", None)


class LearnerExperiencePayloadV1(_StrictModel):
    source: LearnerSourceIdentityV1
    layers: list[LearnerLayerDiagnosisV1] = Field(min_length=3, max_length=3)
    origin_status: LearnerOriginStatus
    origin_node: LearnerNode | None = None
    synthesis: LearnerExperienceSynthesisV1
    route_id: Identity
    route_revision: int = Field(ge=1)
    runtime_policy_revision: int = Field(ge=1)
    model_invocation_refs: list[OpaqueRef] = Field(min_length=1)
    audit_lineage: list[OpaqueRef] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_complete_payload(self) -> "LearnerExperiencePayloadV1":
        expected_status, expected_node = resolve_learner_origin(self.layers)
        if (self.origin_status, self.origin_node) != (expected_status, expected_node):
            raise ValueError("learner origin must be system-derived from layer verdicts")
        if len(set(self.model_invocation_refs)) != len(self.model_invocation_refs):
            raise ValueError("model invocation refs must be ordered and unique")
        if len(set(self.audit_lineage)) != len(self.audit_lineage):
            raise ValueError("audit lineage must be ordered and unique")
        if self.synthesis.outcome == "supported" and self.origin_status == "no_failure":
            raise ValueError("supported learning requires a failure or indeterminate origin")
        if len(_canonical(self.model_dump(mode="json"))) > MAX_CANONICAL_EXPERIENCE_BYTES:
            raise ValueError("learner experience exceeds canonical byte limit")
        forbidden = {"raw_transcript", "instructions", "provider_payload", "chain_of_thought", "secret"}
        if forbidden.intersection(self.model_dump(mode="json")):
            raise ValueError("learner experience contains forbidden payload fields")
        return self


class LearnerExperienceV1(_StrictModel):
    payload: LearnerExperiencePayloadV1
    experience_digest: Digest
    scan_sequence: int = Field(ge=1)
    created_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def require_digest(self) -> "LearnerExperienceV1":
        expected = hashlib.sha256(_canonical(self.payload.model_dump(mode="json"))).hexdigest()
        if self.experience_digest != expected:
            raise ValueError("learner experience digest does not bind payload")
        if self.completed_at < self.created_at:
            raise ValueError("learner completion cannot precede creation")
        return self


class LearnerExperienceCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    experience_ref: OpaqueRef


class LearnerRunClaimV1(_StrictModel):
    run_ref: OpaqueRef
    experience_ref: OpaqueRef
    attempt: int = Field(ge=1)
    fence: int = Field(ge=1)
    claim_token: Identity
    lease_expires_at: AwareDatetime
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_complete_route_pin(self) -> "LearnerRunClaimV1":
        values = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("learner route pin must be wholly present or absent")
        return self


class LearnerRunV1(_StrictModel):
    source: LearnerSourceIdentityV1
    status: LearnerRunStatus
    attempt: int = Field(ge=0)
    fence: int = Field(ge=0)
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)
    model_invocation_refs: list[OpaqueRef] = Field(default_factory=list)
    failure_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    next_attempt_at: AwareDatetime | None = None
    experience_digest: Digest | None = None
    scan_sequence: int | None = Field(default=None, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_lifecycle_shape(self) -> "LearnerRunV1":
        route = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route) != all(value is None for value in route):
            raise ValueError("learner route pin must be wholly present or absent")
        if self.status == "completed":
            if any(value is None for value in route):
                raise ValueError("completed learner run requires route provenance")
            if not self.model_invocation_refs:
                raise ValueError("completed learner run requires invocation provenance")
            if self.experience_digest is None or self.scan_sequence is None or self.completed_at is None:
                raise ValueError("completed learner run requires immutable result metadata")
            if self.failure_code is not None or self.next_attempt_at is not None:
                raise ValueError("completed learner run cannot carry failure state")
        elif self.experience_digest is not None or self.scan_sequence is not None or self.completed_at is not None:
            raise ValueError("non-completed learner run cannot expose an Experience")
        if self.status in {"retryable_failed", "failed"}:
            if self.failure_code is None:
                raise ValueError("failed learner run requires safe failure code")
            if self.status == "retryable_failed" and self.next_attempt_at is None:
                raise ValueError("retryable learner run requires next attempt time")
        elif self.failure_code is not None or self.next_attempt_at is not None:
            raise ValueError("non-failed learner run cannot carry failure state")
        return self


class LearnerExperienceReader(Protocol):
    def read_experience(self, experience_ref: OpaqueRef) -> LearnerExperienceV1 | None: ...

    def list_experiences_after(
        self, cursor: LearnerExperienceCursorV1 | None, limit: int
    ) -> list[LearnerExperienceV1]: ...


class LearnerOwner(LearnerExperienceReader, Protocol):
    def register_case(self, command: RegisterLearnerCaseV1) -> LearnerRunV1: ...

    def claim_next(
        self, worker_id: Identity, observed_at: AwareDatetime, lease_seconds: int = 300
    ) -> LearnerRunClaimV1 | None: ...

    def pin_route(
        self,
        claim: LearnerRunClaimV1,
        route_id: Identity,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: AwareDatetime,
    ) -> LearnerRunClaimV1: ...

    def renew_claim(
        self,
        claim: LearnerRunClaimV1,
        observed_at: AwareDatetime,
        lease_seconds: int = 300,
    ) -> LearnerRunClaimV1: ...

    def complete(
        self,
        claim: LearnerRunClaimV1,
        payload: LearnerExperiencePayloadV1,
        observed_at: AwareDatetime,
    ) -> LearnerExperienceV1: ...

    def fail(
        self,
        claim: LearnerRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: AwareDatetime,
    ) -> LearnerRunV1: ...

    def read_run(self, run_ref: OpaqueRef) -> LearnerRunV1 | None: ...

    def read_experience(self, experience_ref: OpaqueRef) -> LearnerExperienceV1 | None: ...

    def list_experiences_after(
        self, cursor: LearnerExperienceCursorV1 | None, limit: int
    ) -> list[LearnerExperienceV1]: ...


__all__ = [
    "LEARNER_PROMPT_REVISION",
    "MAX_CANONICAL_EXPERIENCE_BYTES",
    "SCHEMA_VERSION",
    "LearnerEvidenceSufficiency",
    "LearnerExperienceCursorV1",
    "LearnerExperiencePayloadV1",
    "LearnerExperienceReader",
    "LearnerExperienceSynthesisV1",
    "LearnerExperienceV1",
    "LearnerLayerApplicability",
    "LearnerLayerDiagnosisV1",
    "LearnerLayerRelation",
    "LearnerLayerVerdict",
    "LearnerNode",
    "LearnerOriginStatus",
    "LearnerOutcome",
    "LearnerOwner",
    "LearnerRunClaimV1",
    "LearnerRunStatus",
    "LearnerRunV1",
    "LearnerSkillDiagnosisV1",
    "LearnerSkillIssueType",
    "LearnerSourceIdentityV1",
    "LearnerUnderperformanceCause",
    "RegisterLearnerCaseV1",
    "learner_case_digest",
    "learner_experience_ref",
    "learner_run_ref",
    "resolve_learner_origin",
]
