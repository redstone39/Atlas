from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.consolidator.public import (
    ConsolidatedExperienceV1,
    ConsolidationCursorV1,
    ConsolidationReader,
    ConsolidationV1,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogRefV1,
    PromptSkillCategory,
    PromptSkillRefV1,
)

Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=12_000)]
SkillName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=200)]

SCHEMA_VERSION = "skill-design-v1"
SKILL_DESIGNER_PROMPT_REVISION = "skill-designer-propose-v1"
MAX_SKILL_CANDIDATE_SOURCE_BYTES = 32_768

SkillDesignRunStatus = Literal[
    "pending", "designing", "retryable_failed", "completed", "failed"
]
SkillCandidateStatus = Literal["draft", "applying", "stale", "approved", "rejected"]
SkillCandidateDisposition = Literal["add", "revise"]
SkillCandidateMutationKind = Literal[
    "approved", "rejected", "stale", "replayed", "conflict"
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _normalize_topic_goal(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def add_draft_key(*, category: PromptSkillCategory, topic: str, goal: str) -> str:
    normalized_topic = _normalize_topic_goal(topic)
    normalized_goal = _normalize_topic_goal(goal)
    if not normalized_topic or not normalized_goal:
        raise ValueError("add draft topic and goal must be non-empty")
    projection = {
        "category": category,
        "goal": normalized_goal,
        "topic": normalized_topic,
    }
    return f"add:{category}:{hashlib.sha256(_canonical(projection)).hexdigest()}"


def revise_draft_key(*, category: PromptSkillCategory, name: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("revise draft target name must be canonical")
    return f"revise:{category}:{name}"

def _candidate_ref_for_draft_key(draft_key: str) -> str:
    return f"skill-candidate:{hashlib.sha256(draft_key.encode('utf-8')).hexdigest()}:v1"


class SkillCandidateEvidenceRefV1(_StrictModel):
    consolidation_ref: OpaqueRef
    consolidation_digest: Digest
    generalized_experience_ordinal: int = Field(ge=1)


class SkillCandidateDraftV1(_StrictModel):
    candidate_ref: OpaqueRef | None = None
    disposition: SkillCandidateDisposition
    category: PromptSkillCategory
    target_name: SkillName
    topic: BoundedText
    goal: BoundedText
    draft_key: Annotated[str, Field(min_length=1, max_length=400)]
    source_evidence: list[SkillCandidateEvidenceRefV1] = Field(min_length=1, max_length=64)
    observed_catalog_refs: list[PromptSkillCatalogRefV1] = Field(
        min_length=3, max_length=3
    )
    matched_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list, max_length=64)
    skill_source: Annotated[str, Field(min_length=1, max_length=MAX_SKILL_CANDIDATE_SOURCE_BYTES)]
    skill_source_digest: Digest
    rationale: BoundedText
    risk: BoundedText

    @model_validator(mode="after")
    def require_exact_draft_identity(self) -> "SkillCandidateDraftV1":
        categories = [ref.category for ref in self.observed_catalog_refs]
        if categories != ["understanding", "planner", "answer"]:
            raise ValueError("candidate must pin understanding, planner, answer catalogs in order")
        expected_key = (
            add_draft_key(category=self.category, topic=self.topic, goal=self.goal)
            if self.disposition == "add"
            else revise_draft_key(category=self.category, name=self.target_name)
        )
        if self.candidate_ref is None and self.draft_key != expected_key:
            raise ValueError("new candidate draft key does not bind its semantic target")
        if (
            self.candidate_ref is not None
            and self.candidate_ref != _candidate_ref_for_draft_key(self.draft_key)
        ):
            raise ValueError("existing candidate ref does not bind its draft key")
        if self.disposition == "add" and any(
            ref.category == self.category and ref.name == self.target_name
            for ref in self.matched_skill_refs
        ):
            raise ValueError("add candidate cannot target an existing matched Skill")
        if self.disposition == "revise" and not any(
            ref.category == self.category and ref.name == self.target_name
            for ref in self.matched_skill_refs
        ):
            raise ValueError("revise candidate must exact-match its target Skill")
        if len({tuple(ref.model_dump().values()) for ref in self.source_evidence}) != len(
            self.source_evidence
        ):
            raise ValueError("candidate source evidence must be ordered and unique")
        if len({tuple(ref.model_dump().values()) for ref in self.matched_skill_refs}) != len(
            self.matched_skill_refs
        ):
            raise ValueError("candidate matched Skill refs must be ordered and unique")
        expected_digest = hashlib.sha256(self.skill_source.encode("utf-8")).hexdigest()
        if self.skill_source_digest != expected_digest:
            raise ValueError("candidate source digest does not bind SKILL.md")
        return self


class SkillCandidateSummaryV1(_StrictModel):
    candidate_ref: OpaqueRef
    draft_key: Annotated[str, Field(min_length=1, max_length=400)]
    disposition: SkillCandidateDisposition
    category: PromptSkillCategory
    target_name: SkillName
    topic: BoundedText
    goal: BoundedText
    draft_revision: int = Field(ge=1)
    status: SkillCandidateStatus
    skill_source_digest: Digest
    updated_at: AwareDatetime


class SkillCandidateDetailV1(SkillCandidateSummaryV1):
    source_evidence: list[SkillCandidateEvidenceRefV1] = Field(min_length=1, max_length=64)
    observed_catalog_refs: list[PromptSkillCatalogRefV1] = Field(
        min_length=3, max_length=3
    )
    matched_skill_refs: list[PromptSkillRefV1] = Field(default_factory=list, max_length=64)
    skill_source: Annotated[str, Field(min_length=1, max_length=MAX_SKILL_CANDIDATE_SOURCE_BYTES)]
    rationale: BoundedText
    risk: BoundedText
    approved_skill_ref: PromptSkillRefV1 | None = None

    @model_validator(mode="after")
    def require_detail_lifecycle(self) -> "SkillCandidateDetailV1":
        categories = [ref.category for ref in self.observed_catalog_refs]
        if categories != ["understanding", "planner", "answer"]:
            raise ValueError("candidate detail must preserve three ordered catalog pins")
        if (self.status == "approved") != (self.approved_skill_ref is not None):
            raise ValueError("only approved candidate detail carries published Skill ref")
        expected_digest = hashlib.sha256(self.skill_source.encode("utf-8")).hexdigest()
        if self.skill_source_digest != expected_digest:
            raise ValueError("candidate detail source digest does not bind SKILL.md")
        return self


class SkillCandidateListV1(_StrictModel):
    items: list[SkillCandidateSummaryV1]


class ApproveSkillCandidateV1(_StrictModel):
    expected_draft_revision: int = Field(ge=1)
    idempotency_key: IdempotencyKey


class RejectSkillCandidateV1(_StrictModel):
    expected_draft_revision: int = Field(ge=1)
    idempotency_key: IdempotencyKey


class SkillCandidateMutationOutcomeV1(_StrictModel):
    candidate_ref: OpaqueRef
    draft_revision: int = Field(ge=1)
    status: SkillCandidateStatus
    outcome: SkillCandidateMutationKind
    approved_skill_ref: PromptSkillRefV1 | None = None

    @model_validator(mode="after")
    def require_mutation_outcome_shape(self) -> "SkillCandidateMutationOutcomeV1":
        if (self.status == "approved") != (self.approved_skill_ref is not None):
            raise ValueError("only approved outcome carries published Skill ref")
        return self


class SkillDesignSourceV1(_StrictModel):
    consolidation_ref: OpaqueRef
    consolidation_digest: Digest
    consolidation_scan_sequence: int = Field(ge=1)


class SkillDesignCursorV1(_StrictModel):
    scan_sequence: int = Field(ge=1)
    consolidation_ref: OpaqueRef


class SkillDesignRunClaimV1(_StrictModel):
    run_ref: OpaqueRef
    source: SkillDesignSourceV1
    attempt: int = Field(ge=1)
    fence: int = Field(ge=1)
    claim_token: Identity
    lease_expires_at: AwareDatetime
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_complete_route_pin(self) -> "SkillDesignRunClaimV1":
        route = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route) != all(value is None for value in route):
            raise ValueError("Skill Designer route pin must be wholly present or absent")
        return self


class SkillDesignRunV1(_StrictModel):
    run_ref: OpaqueRef
    source: SkillDesignSourceV1
    status: SkillDesignRunStatus
    attempt: int = Field(ge=0)
    fence: int = Field(ge=0)
    pinned_route_id: Identity | None = None
    pinned_route_revision: int | None = Field(default=None, ge=1)
    pinned_runtime_policy_revision: int | None = Field(default=None, ge=1)
    model_invocation_refs: list[OpaqueRef] = Field(default_factory=list, max_length=64)
    result_digest: Digest | None = None
    failure_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    next_attempt_at: AwareDatetime | None = None
    candidate_refs: list[OpaqueRef] = Field(default_factory=list, max_length=64)
    candidate_material_digests: list[Digest] = Field(default_factory=list, max_length=64)
    completed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def require_lifecycle_shape(self) -> "SkillDesignRunV1":
        route = (
            self.pinned_route_id,
            self.pinned_route_revision,
            self.pinned_runtime_policy_revision,
        )
        if any(value is None for value in route) != all(value is None for value in route):
            raise ValueError("Skill Designer route pin must be wholly present or absent")
        if len(self.candidate_material_digests) != len(self.candidate_refs):
            raise ValueError(
                "Skill Designer candidate refs and material digests must align"
            )
        if self.status == "completed":
            if (
                any(value is None for value in route)
                or self.completed_at is None
                or not self.model_invocation_refs
                or self.result_digest is None
            ):
                raise ValueError(
                    "completed Skill Designer run requires route, invocation, and result provenance"
                )
            expected_digest = skill_design_result_digest(
                source=self.source,
                candidate_refs=self.candidate_refs,
                candidate_material_digests=self.candidate_material_digests,
                model_invocation_refs=self.model_invocation_refs,
            )
            if self.result_digest != expected_digest:
                raise ValueError("Skill Designer result digest does not bind provenance")
            if self.failure_code is not None or self.next_attempt_at is not None:
                raise ValueError("completed Skill Designer run cannot carry failure state")
        elif (
            self.completed_at is not None
            or self.candidate_refs
            or self.candidate_material_digests
            or self.model_invocation_refs
            or self.result_digest is not None
        ):
            raise ValueError("non-completed Skill Designer run cannot expose a result")
        if self.status in {"retryable_failed", "failed"}:
            if self.failure_code is None:
                raise ValueError("failed Skill Designer run requires a safe failure code")
            if self.status == "retryable_failed" and self.next_attempt_at is None:
                raise ValueError("retryable Skill Designer run requires next attempt time")
        elif self.failure_code is not None or self.next_attempt_at is not None:
            raise ValueError("non-failed Skill Designer run cannot carry failure state")
        return self


def skill_design_run_ref(*, consolidation_ref: str, consolidation_digest: str) -> str:
    projection = {
        "consolidation_digest": consolidation_digest,
        "consolidation_ref": consolidation_ref,
        "prompt_revision": SKILL_DESIGNER_PROMPT_REVISION,
        "schema_version": SCHEMA_VERSION,
    }
    return f"skill-design:{hashlib.sha256(_canonical(projection)).hexdigest()}:v1"



def skill_design_result_digest(
    *,
    source: SkillDesignSourceV1,
    candidate_refs: list[str],
    candidate_material_digests: list[str],
    model_invocation_refs: list[str],
) -> str:
    projection = {
        "source": source.model_dump(mode="json"),
        "candidate_refs": candidate_refs,
        "candidate_material_digests": candidate_material_digests,
        "model_invocation_refs": model_invocation_refs,
    }
    return hashlib.sha256(_canonical(projection)).hexdigest()

class SkillDesignerOwner(Protocol):
    def register_consolidation(self, consolidation: ConsolidationV1) -> SkillDesignRunV1: ...

    def register_completed_after(
        self,
        reader: ConsolidationReader,
        cursor: ConsolidationCursorV1 | None,
        limit: int,
    ) -> list[SkillDesignRunV1]: ...

    def claim_next(
        self, worker_id: Identity, observed_at: AwareDatetime, lease_seconds: int = 300
    ) -> SkillDesignRunClaimV1 | None: ...

    def pin_route(
        self,
        claim: SkillDesignRunClaimV1,
        route_id: Identity,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: AwareDatetime,
    ) -> SkillDesignRunClaimV1: ...

    def renew_claim(
        self,
        claim: SkillDesignRunClaimV1,
        observed_at: AwareDatetime,
        lease_seconds: int = 300,
    ) -> SkillDesignRunClaimV1: ...

    def complete(
        self,
        claim: SkillDesignRunClaimV1,
        drafts: list[SkillCandidateDraftV1],
        model_invocation_refs: list[OpaqueRef],
        observed_at: AwareDatetime,
    ) -> SkillDesignRunV1: ...

    def fail(
        self,
        claim: SkillDesignRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: AwareDatetime,
    ) -> SkillDesignRunV1: ...

    def list_candidate_summaries(
        self, category: PromptSkillCategory | None = None
    ) -> SkillCandidateListV1: ...

    def read_candidate(self, candidate_ref: OpaqueRef) -> SkillCandidateDetailV1 | None: ...


class SkillCandidateStoreError(RuntimeError):
    pass


class SkillCandidateError(RuntimeError):
    def __init__(self, error_code: str, message_code: str, status_code: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code


class SkillCandidateAdmin(Protocol):
    def list_candidates(
        self, actor_id: Identity, category: PromptSkillCategory | None = None
    ) -> SkillCandidateListV1: ...

    def get_candidate(
        self, actor_id: Identity, candidate_ref: OpaqueRef
    ) -> SkillCandidateDetailV1: ...

    def approve_candidate(
        self,
        actor_id: Identity,
        candidate_ref: OpaqueRef,
        command: ApproveSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1: ...

    def reject_candidate(
        self,
        actor_id: Identity,
        candidate_ref: OpaqueRef,
        command: RejectSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1: ...


__all__ = [
    "MAX_SKILL_CANDIDATE_SOURCE_BYTES",
    "SCHEMA_VERSION",
    "SKILL_DESIGNER_PROMPT_REVISION",
    "ApproveSkillCandidateV1",
    "RejectSkillCandidateV1",
    "SkillCandidateError",
    "SkillCandidateStoreError",
    "SkillCandidateAdmin",
    "SkillCandidateDetailV1",
    "SkillCandidateDisposition",
    "SkillCandidateDraftV1",
    "SkillCandidateEvidenceRefV1",
    "SkillCandidateListV1",
    "SkillCandidateMutationKind",
    "SkillCandidateMutationOutcomeV1",
    "SkillCandidateStatus",
    "SkillCandidateSummaryV1",
    "SkillDesignCursorV1",
    "SkillDesignRunClaimV1",
    "SkillDesignRunStatus",
    "SkillDesignRunV1",
    "SkillDesignSourceV1",
    "SkillDesignerOwner",
    "add_draft_key",
    "revise_draft_key",
    "skill_design_run_ref",
]
