from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    GovernedResult,
    GovernedStatus,
    ResultSurface,
)
from .ports import ResultGovernanceRuntime
from .service import ResultGovernanceService
from atlas_production.modules.retrieval.public import (
    DeclaredEvidenceMappingV1,
    DeclaredEvidenceSubsetV1,
    GovernanceEvidencePackV1,
)
from atlas_production.modules.turn_runtime.public import TurnRouteSnapshotV2


class _StrictTurnDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RetrievalStatusV1 = Literal[
    "not_used", "evidence_found", "no_evidence", "access_denied",
    "tool_failed", "budget_exhausted",
]
VerificationStatusV1 = Literal["verified", "partially_verified", "unverified"]
EvidenceReviewStatusV2 = Literal["evidence_aligned", "questionable"]
AssessmentStateV2 = Literal["completed", "unavailable", "not_attempted"]
DeclaredEvidenceConsistencyV1 = Literal[
    "aligned", "conflict", "insufficient", "not_applicable", "unavailable"
]
ProvisionalEvidenceReasonCodeV1 = Literal[
    "aligned",
    "declared_evidence_conflict",
    "declared_evidence_insufficient",
    "empty_declaration",
    "no_resolved_declared_evidence",
    "partially_unresolved_declared_evidence",
    "deadline_elapsed",
    "route_unavailable",
    "provider_contract_unavailable",
    "physical_limit_rejected",
    "tokenizer_unavailable",
    "provider_timeout",
    "provider_failed",
    "provider_refused",
    "provider_incomplete",
    "invalid_output",
]
AssessmentReasonCodeV2 = Literal[
    "completed",
    "empty_declaration",
    "no_resolved_declared_evidence",
    "deadline_elapsed",
    "route_unavailable",
    "provider_contract_unavailable",
    "physical_limit_rejected",
    "tokenizer_unavailable",
    "provider_timeout",
    "provider_failed",
    "provider_refused",
    "provider_incomplete",
    "invalid_output",
]
EvidenceReviewReasonCodeV2 = Literal[
    "evidence_aligned",
    "empty_declaration",
    "assessment_not_completed",
    "declared_evidence_not_aligned",
    "answer_item_failed",
]


class ExecutionEvidenceLineageV1(_StrictTurnDraftModel):
    evidence_handle: Annotated[str, Field(min_length=8, max_length=200)]
    evidence_ref: OpaqueRef
    evidence_digest: Digest
    result_ref: OpaqueRef
    invocation_ordinal: int = Field(ge=1)


class FinalizedAnswerSegmentV1(_StrictTurnDraftModel):
    segment_id: Identity
    text: str = Field(max_length=12000)


class FinalizedAnswerV1(_StrictTurnDraftModel):
    segments: list[FinalizedAnswerSegmentV1] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_segment_ids(self) -> "FinalizedAnswerV1":
        ids = [segment.segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("finalized answer segment ids must be unique")
        return self


class PostHocClaimAssessmentV1(_StrictTurnDraftModel):
    segment_id: Identity
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    decision: Literal["supported", "unsupported"]
    supporting_evidence_handles: list[
        Annotated[str, Field(min_length=8, max_length=200)]
    ] = Field(max_length=20)

    @model_validator(mode="after")
    def require_valid_support_shape(self) -> "PostHocClaimAssessmentV1":
        if self.end <= self.start:
            raise ValueError("claim assessment end must be greater than start")
        if len(self.supporting_evidence_handles) != len(
            set(self.supporting_evidence_handles)
        ):
            raise ValueError("supporting evidence handles must be unique")
        if self.decision == "supported" and not self.supporting_evidence_handles:
            raise ValueError("supported claim requires supporting evidence")
        if self.decision == "unsupported" and self.supporting_evidence_handles:
            raise ValueError("unsupported claim cannot bind supporting evidence")
        return self


class PostHocClaimAssessmentEnvelopeV1(_StrictTurnDraftModel):
    assessments: list[PostHocClaimAssessmentV1] = Field(max_length=2000)


class PostHocAnswerAssessmentV2(_StrictTurnDraftModel):
    id: Identity
    status: Literal["success", "failure"]
    internal_consistency: Literal["aligned", "conflict", "insufficient"] | None = Field(
        default=None, exclude=True, repr=False
    )


class PostHocAnswerAssessmentEnvelopeV2(_StrictTurnDraftModel):
    consistency: Literal["aligned", "conflict", "insufficient"]
    results: list[PostHocAnswerAssessmentV2] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_consistency_result_shape(self) -> "PostHocAnswerAssessmentEnvelopeV2":
        if self.consistency == "aligned" and any(
            result.status != "success" for result in self.results
        ):
            raise ValueError("aligned provider output requires all answer items to succeed")
        if self.consistency == "conflict" and all(
            result.status == "success" for result in self.results
        ):
            raise ValueError("conflict provider output requires a failed answer item")
        return self


class ProvisionalEvidenceAssessmentV1(_StrictTurnDraftModel):
    assessment_version: Literal["provisional-declared-evidence-v1"] = (
        "provisional-declared-evidence-v1"
    )
    state: AssessmentStateV2
    consistency: DeclaredEvidenceConsistencyV1
    reason_code: ProvisionalEvidenceReasonCodeV1
    answer_digest: Digest
    declared_subset_digest: Digest
    visual_image_digests: list[Digest] = Field(max_length=40)
    results: list[PostHocAnswerAssessmentV2] = Field(default_factory=list, max_length=100)
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None

    @model_validator(mode="after")
    def require_consistent_assessment(self) -> "ProvisionalEvidenceAssessmentV1":
        if self.state == "completed":
            if self.consistency not in {"aligned", "conflict", "insufficient"}:
                raise ValueError("completed assessment requires a provider consistency")
            if self.assessment_input_digest is None or self.assessment_output_digest is None:
                raise ValueError("completed assessment requires input/output digests")
            if self.consistency == "aligned" and any(
                result.status != "success" for result in self.results
            ):
                raise ValueError("aligned assessment requires all answer items to succeed")
        elif self.state == "not_attempted":
            if self.consistency not in {"not_applicable", "insufficient"}:
                raise ValueError("not-attempted assessment has an invalid consistency")
            if self.results or self.assessment_input_digest or self.assessment_output_digest:
                raise ValueError("not-attempted assessment cannot carry provider output")
        else:
            if self.consistency != "unavailable":
                raise ValueError("unavailable assessment requires unavailable consistency")
            if self.results or self.assessment_output_digest is not None:
                raise ValueError("unavailable assessment cannot carry provider results")
        return self


PostHocAnswerAssessmentResultV2 = ProvisionalEvidenceAssessmentV1


class PostHocClaimEvaluator(Protocol):
    def assess(
        self,
        *,
        execution_id: Identity,
        finalized_answer: FinalizedAnswerV1,
        evidence_pack: GovernanceEvidencePackV1,
        deadline_at: datetime,
        route: TurnRouteSnapshotV2,
    ) -> PostHocClaimAssessmentEnvelopeV1: ...


class PostHocAnswerEvaluatorV2(Protocol):
    def assess(
        self,
        *,
        execution_id: Identity,
        finalized_answer: FinalizedAnswerV1,
        declared_evidence_subset: DeclaredEvidenceSubsetV1,
        deadline_at: datetime,
        route: TurnRouteSnapshotV2,
        assessment_ordinal: int = 1,
        evidence_handles_by_segment: dict[Identity, list[Identity]] | None = None,
    ) -> PostHocAnswerAssessmentResultV2: ...


class MaterializeGovernedAnswerDraftV1(_StrictTurnDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    finalized_answer: FinalizedAnswerV1
    retrieval_status: RetrievalStatusV1
    evidence_lineage: list[ExecutionEvidenceLineageV1] = Field(max_length=40)
    assessment_succeeded: bool
    assessments: list[PostHocClaimAssessmentV1] = Field(max_length=2000)
    idempotency_key: Identity

    @model_validator(mode="after")
    def require_runtime_consistent_retrieval_status(self) -> "MaterializeGovernedAnswerDraftV1":
        handles = [item.evidence_handle for item in self.evidence_lineage]
        if len(handles) != len(set(handles)):
            raise ValueError("execution evidence handles must be unique")
        if self.retrieval_status == "evidence_found" and not handles:
            raise ValueError("evidence_found requires execution evidence lineage")
        if self.retrieval_status != "evidence_found" and handles:
            raise ValueError("only evidence_found may carry execution evidence lineage")
        if not self.assessment_succeeded and self.assessments:
            raise ValueError("unsuccessful assessment cannot carry claims")
        segment_text = {
            segment.segment_id: segment.text for segment in self.finalized_answer.segments
        }
        spans: dict[str, list[tuple[int, int]]] = {}
        for item in self.assessments:
            text = segment_text.get(item.segment_id)
            if text is None:
                raise ValueError("claim assessment references an unknown segment")
            if item.end > len(text):
                raise ValueError("claim assessment span is out of bounds")
            if any(handle not in handles for handle in item.supporting_evidence_handles):
                raise ValueError("claim assessment references unknown evidence")
            spans.setdefault(item.segment_id, []).append((item.start, item.end))
        for segment_spans in spans.values():
            if len(segment_spans) > 100:
                raise ValueError("a segment cannot contain more than 100 claim assessments")
            ordered = sorted(segment_spans)
            if any(current[0] < previous[1] for previous, current in zip(ordered, ordered[1:])):
                raise ValueError("claim assessment spans overlap")
        return self


class GovernedClaimV1(_StrictTurnDraftModel):
    claim_id: Identity
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    verification_status: Literal["verified", "unverified"]
    evidence_refs: list[OpaqueRef] = Field(max_length=20)


class GovernedAnswerSegmentV1(_StrictTurnDraftModel):
    segment_id: Identity
    text: str = Field(max_length=12000)
    verification_status: Literal["verified", "unverified"]
    claims: list[GovernedClaimV1] = Field(max_length=100)


class GovernedAnswerDraftV1(_StrictTurnDraftModel):
    draft_ref: OpaqueRef
    schema_version: Literal["governed-answer-draft-v1"] = "governed-answer-draft-v1"
    execution_id: Identity
    retrieval_status: RetrievalStatusV1
    verification_status: VerificationStatusV1
    segments: list[GovernedAnswerSegmentV1] = Field(min_length=1, max_length=100)
    digest: Digest
    created_at: AwareDatetime


class MaterializeGovernedAnswerDraftV2(_StrictTurnDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    finalized_answer: FinalizedAnswerV1
    retrieval_status: RetrievalStatusV1
    declared_evidence_mappings: list[DeclaredEvidenceMappingV1] = Field(
        max_length=100
    )
    evidence_lineage: list[ExecutionEvidenceLineageV1] = Field(max_length=40)
    assessment_state: AssessmentStateV2
    assessment_reason_code: AssessmentReasonCodeV2
    assessment_version: Literal["provisional-declared-evidence-v1"]
    assessment_consistency: DeclaredEvidenceConsistencyV1
    assessment_answer_digest: Digest
    assessment_declared_subset_digest: Digest
    assessment_visual_image_digests: list[Digest] = Field(max_length=40)
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None
    assessment_results: list[PostHocAnswerAssessmentV2] = Field(max_length=100)
    research_packet_ref: OpaqueRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    research_packet_digest: Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    delivery_constraint: Literal["none", "correction_limit_reached"] = "none"
    idempotency_key: Identity

    @model_validator(mode="after")
    def require_soft_review_consistency(self) -> "MaterializeGovernedAnswerDraftV2":
        expected_answer_digest = hashlib.sha256(
            json.dumps(
                self.finalized_answer.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.assessment_answer_digest != expected_answer_digest:
            raise ValueError("assessment answer digest does not bind finalized answer")
        handles = [item.evidence_handle for item in self.evidence_lineage]
        if len(handles) != len(set(handles)):
            raise ValueError("declared evidence lineage handles must be unique")
        resolved_handles: list[str] = []
        seen: set[str] = set()
        for mapping in self.declared_evidence_mappings:
            if (
                mapping.resolution_status == "resolved"
                and mapping.handle not in seen
            ):
                resolved_handles.append(mapping.handle)
                seen.add(mapping.handle)
        if handles != resolved_handles:
            raise ValueError(
                "declared evidence lineage must follow first resolved declaration order"
            )
        if self.assessment_state == "completed":
            if (
                self.assessment_reason_code != "completed"
                or self.assessment_input_digest is None
                or self.assessment_output_digest is None
            ):
                raise ValueError("completed assessment requires input/output digests")
            expected_ids = [
                segment.segment_id for segment in self.finalized_answer.segments
            ]
            result_ids = [result.id for result in self.assessment_results]
            if result_ids != expected_ids:
                raise ValueError(
                    "completed assessment results must match answer ids in order"
                )
            if not self.evidence_lineage:
                raise ValueError("completed assessment requires resolved evidence")
        elif self.assessment_results or self.assessment_output_digest is not None:
            raise ValueError(
                "incomplete assessment cannot carry results or output digest"
            )
        elif self.assessment_reason_code == "completed":
            raise ValueError("incomplete assessment requires an unavailable reason")
        if (
            self.assessment_state == "not_attempted"
            and self.assessment_reason_code
            not in {"empty_declaration", "no_resolved_declared_evidence"}
        ):
            raise ValueError("not-attempted assessment reason is invalid")
        expected_consistency = {
            "completed": {"aligned", "conflict", "insufficient"},
            "not_attempted": {"not_applicable", "insufficient"},
            "unavailable": {"unavailable"},
        }[self.assessment_state]
        if self.assessment_consistency not in expected_consistency:
            raise ValueError("assessment state and consistency disagree")
        if self.assessment_consistency == "aligned" and any(
            result.status != "success" for result in self.assessment_results
        ):
            raise ValueError("aligned assessment requires all answer items to succeed")
        if (self.research_packet_ref is None) != (
            self.research_packet_digest is None
        ):
            raise ValueError("research packet ref and digest must be paired")
        return self


class GovernedAnswerSegmentV2(_StrictTurnDraftModel):
    segment_id: Identity
    text: str = Field(max_length=12000)


class GovernedAnswerDraftV2(_StrictTurnDraftModel):
    draft_ref: OpaqueRef
    schema_version: Literal["governed-answer-draft-v2"] = "governed-answer-draft-v2"
    execution_id: Identity
    retrieval_status: RetrievalStatusV1
    evidence_review_status: EvidenceReviewStatusV2
    evidence_review_reason_codes: list[EvidenceReviewReasonCodeV2] = Field(
        min_length=1, max_length=7
    )
    declared_evidence_mappings: list[DeclaredEvidenceMappingV1] = Field(
        max_length=100
    )
    assessment_state: AssessmentStateV2
    assessment_reason_code: AssessmentReasonCodeV2
    assessment_version: Literal["provisional-declared-evidence-v1"]
    assessment_consistency: DeclaredEvidenceConsistencyV1
    assessment_answer_digest: Digest
    assessment_declared_subset_digest: Digest
    assessment_visual_image_digests: list[Digest] = Field(max_length=40)
    assessment_input_digest: Digest | None = None
    assessment_output_digest: Digest | None = None
    assessment_results: list[PostHocAnswerAssessmentV2] = Field(max_length=100)
    research_packet_ref: OpaqueRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    research_packet_digest: Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    segments: list[GovernedAnswerSegmentV2] = Field(min_length=1, max_length=100)
    digest: Digest
    created_at: AwareDatetime
    @model_validator(mode="after")
    def require_research_packet_pair(self) -> "GovernedAnswerDraftV2":
        if (self.research_packet_ref is None) != (
            self.research_packet_digest is None
        ):
            raise ValueError("research packet ref and digest must be paired")
        return self


class ReleaseGovernedAnswerDraftV1(_StrictTurnDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    idempotency_key: Identity


class GovernedAnswerDraftReleaseV1(_StrictTurnDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    schema_version: Literal["governed-answer-draft-release-v1"] = "governed-answer-draft-release-v1"
    released_at: AwareDatetime


class ResultGovernanceDraftOwner(Protocol):
    def materialize(self, command: MaterializeGovernedAnswerDraftV1) -> GovernedAnswerDraftV1: ...

    def read(self, draft_ref: OpaqueRef) -> GovernedAnswerDraftV1 | None: ...

    def release(self, command: ReleaseGovernedAnswerDraftV1) -> GovernedAnswerDraftReleaseV1: ...


class ResultGovernanceDraftOwnerV2(Protocol):
    def materialize_v2(
        self, command: MaterializeGovernedAnswerDraftV2
    ) -> GovernedAnswerDraftV2: ...

    def read_v2(self, draft_ref: OpaqueRef) -> GovernedAnswerDraftV2 | None: ...

__all__ = [
    "GovernedResult",
    "GovernedStatus",
    "ResultGovernanceRuntime",
    "ResultGovernanceService",
    "ResultSurface",
    "ExecutionEvidenceLineageV1",
    "AssessmentReasonCodeV2",
    "AssessmentStateV2",
    "DeclaredEvidenceConsistencyV1",
    "EvidenceReviewReasonCodeV2",
    "EvidenceReviewStatusV2",
    "FinalizedAnswerSegmentV1",
    "FinalizedAnswerV1",
    "GovernedAnswerDraftReleaseV1",
    "GovernedAnswerDraftV1",
    "GovernedAnswerDraftV2",
    "GovernedAnswerSegmentV1",
    "GovernedAnswerSegmentV2",
    "GovernedClaimV1",
    "MaterializeGovernedAnswerDraftV1",
    "MaterializeGovernedAnswerDraftV2",
    "ReleaseGovernedAnswerDraftV1",
    "ResultGovernanceDraftOwner",
    "ResultGovernanceDraftOwnerV2",
    "PostHocClaimAssessmentEnvelopeV1",
    "PostHocAnswerAssessmentEnvelopeV2",
    "PostHocAnswerAssessmentResultV2",
    "ProvisionalEvidenceAssessmentV1",
    "ProvisionalEvidenceReasonCodeV1",
    "PostHocAnswerAssessmentV2",
    "PostHocClaimAssessmentV1",
    "PostHocClaimEvaluator",
    "PostHocAnswerEvaluatorV2",
    "RetrievalStatusV1",
    "VerificationStatusV1",
]
