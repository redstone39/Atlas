from __future__ import annotations

from typing import Literal, Sequence

from atlas_production.infrastructure.turn_execution_foundation import _digest, _ref
from atlas_production.modules.agent_runtime.public import ResearchPacketV1
from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV2,
    TurnAuditStepV1,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftV2,
    MaterializeCitationBindingDraftV2,
)
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    ExecutionEvidenceLineageV1,
    FinalizedAnswerV1,
    GovernedAnswerDraftV2,
    MaterializeGovernedAnswerDraftV2,
    PostHocAnswerAssessmentV2,
    RetrievalStatusV1,
)
from atlas_production.modules.retrieval.public import (
    DeclaredEvidenceSubsetV1,
    GovernanceEvidencePackV1,
)
from atlas_production.modules.turn_execution.public import (
    FinalizeAnswerV1,
    FinalizeResearchV1,
)
from atlas_production.modules.turn_runtime.public import (
    CommitTerminalV1,
    ExecutionSnapshotV1,
    PrepareTerminalV1,
)


def _terminal_retrieval_status(
    *,
    evidence_pack: GovernanceEvidencePackV1,
    retrieval_error_status: RetrievalStatusV1 | None,
    finalize_only: bool,
    used_retrieval: bool,
) -> RetrievalStatusV1:
    if evidence_pack.items:
        return "evidence_found"
    if retrieval_error_status is not None:
        return retrieval_error_status
    if finalize_only and used_retrieval:
        return "budget_exhausted"
    if used_retrieval:
        return "no_evidence"
    return "not_used"


def _finalized_answer(proposal: FinalizeAnswerV1) -> FinalizedAnswerV1:
    return FinalizedAnswerV1(
        segments=[segment.model_dump(mode="json") for segment in proposal.segments]
    )


def _declared_lineage(
    subset: DeclaredEvidenceSubsetV1,
) -> list[ExecutionEvidenceLineageV1]:
    return [
        ExecutionEvidenceLineageV1(
            evidence_handle=item.evidence_handle,
            evidence_ref=item.evidence_ref,
            evidence_digest=item.evidence_digest,
            result_ref=item.source_result_ref,
            invocation_ordinal=item.source_invocation_ordinal,
        )
        for item in subset.items
    ]


def _assessment_audit_step(
    *,
    ordinal: int,
    assessment_state: Literal["completed", "unavailable", "not_attempted"],
    declared_subset: DeclaredEvidenceSubsetV1,
    finalized_answer: FinalizedAnswerV1,
) -> TurnAuditStepV1:
    return TurnAuditStepV1(
        ordinal=ordinal,
        step_kind="governance",
        operation="assess_declared_evidence",
        status=(
            "skipped"
            if assessment_state == "not_attempted"
            else "completed"
            if assessment_state == "completed"
            else "failed"
        ),
        safe_input_digest=_digest(
            [declared_subset.digest, finalized_answer.model_dump(mode="json")]
        ),
        evidence_count=len(declared_subset.items),
    )


def _governance_command(
    *,
    execution_id: str,
    finalized_answer: FinalizedAnswerV1,
    retrieval_status: RetrievalStatusV1,
    declared_subset: DeclaredEvidenceSubsetV1,
    declared_lineage: list[ExecutionEvidenceLineageV1],
    assessment_state: Literal["completed", "unavailable", "not_attempted"],
    assessment_reason_code: AssessmentReasonCodeV2,
    assessment_consistency: Literal[
        "aligned", "conflict", "insufficient", "not_applicable", "unavailable"
    ],
    answer_digest: str,
    visual_image_digests: list[str],
    assessment_input_digest: str | None,
    assessment_output_digest: str | None,
    assessment_results: list[PostHocAnswerAssessmentV2],
    delivery_constraint: Literal["none", "correction_limit_reached"],
    research_packet_ref: str | None = None,
    research_packet_digest: str | None = None,
) -> MaterializeGovernedAnswerDraftV2:
    return MaterializeGovernedAnswerDraftV2(
        draft_ref=_ref("governed-answer-draft", execution_id),
        execution_id=execution_id,
        finalized_answer=finalized_answer,
        retrieval_status=retrieval_status,
        declared_evidence_mappings=declared_subset.mappings,
        evidence_lineage=declared_lineage,
        assessment_state=assessment_state,
        assessment_reason_code=assessment_reason_code,
        assessment_version="provisional-declared-evidence-v1",
        assessment_consistency=assessment_consistency,
        assessment_answer_digest=answer_digest,
        assessment_declared_subset_digest=declared_subset.digest,
        assessment_visual_image_digests=visual_image_digests,
        assessment_input_digest=assessment_input_digest,
        assessment_output_digest=assessment_output_digest,
        assessment_results=assessment_results,
        delivery_constraint=delivery_constraint,
        research_packet_ref=research_packet_ref,
        research_packet_digest=research_packet_digest,
        idempotency_key=f"{execution_id}:governed-answer",
    )


def _invalid_governance_command(
    *,
    execution_id: str,
    finalized_answer: FinalizedAnswerV1,
    retrieval_status: RetrievalStatusV1,
    declared_subset: DeclaredEvidenceSubsetV1,
    declared_lineage: list[ExecutionEvidenceLineageV1],
    answer_digest: str,
    visual_image_digests: list[str],
    assessment_input_digest: str | None,
    delivery_constraint: Literal["none", "correction_limit_reached"],
    research_packet_ref: str | None = None,
    research_packet_digest: str | None = None,
) -> MaterializeGovernedAnswerDraftV2:
    return _governance_command(
        execution_id=execution_id,
        finalized_answer=finalized_answer,
        retrieval_status=retrieval_status,
        declared_subset=declared_subset,
        declared_lineage=declared_lineage,
        assessment_state="unavailable",
        assessment_reason_code="invalid_output",
        assessment_consistency="unavailable",
        answer_digest=answer_digest,
        visual_image_digests=visual_image_digests,
        assessment_input_digest=assessment_input_digest,
        assessment_output_digest=None,
        assessment_results=[],
        delivery_constraint=delivery_constraint,
        research_packet_ref=research_packet_ref,
        research_packet_digest=research_packet_digest,
    )


def _citation_command(
    execution_id: str, governed: GovernedAnswerDraftV2
) -> MaterializeCitationBindingDraftV2:
    return MaterializeCitationBindingDraftV2(
        draft_ref=_ref("citation-binding-draft", execution_id),
        execution_id=execution_id,
        governed_answer=governed,
        idempotency_key=f"{execution_id}:citation-binding",
    )


def _terminal_materialization_steps(
    *,
    start_ordinal: int,
    assessment_step: TurnAuditStepV1,
    evidence_pack: GovernanceEvidencePackV1,
    proposal: FinalizeAnswerV1,
    governed: GovernedAnswerDraftV2,
    citation: CitationBindingDraftV2,
    evidence_count: int,
) -> list[TurnAuditStepV1]:
    return [
        assessment_step,
        TurnAuditStepV1(
            ordinal=start_ordinal + 1,
            step_kind="governance",
            operation="materialize_governed_answer",
            status="completed",
            safe_input_digest=_digest(
                [evidence_pack.digest, proposal.model_dump(mode="json")]
            ),
            result_ref=governed.draft_ref,
            result_digest=governed.digest,
            evidence_count=evidence_count,
        ),
        TurnAuditStepV1(
            ordinal=start_ordinal + 2,
            step_kind="citation",
            operation="materialize_citation_binding",
            status="completed",
            safe_input_digest=_digest(governed.digest),
            result_ref=citation.draft_ref,
            result_digest=citation.digest,
            evidence_count=len(citation.bindings),
        ),
    ]


def _audit_command(
    *,
    execution_id: str,
    proposal: FinalizeAnswerV1,
    evidence_pack: GovernanceEvidencePackV1,
    governed: GovernedAnswerDraftV2,
    citation: CitationBindingDraftV2,
    steps: Sequence[TurnAuditStepV1],
) -> MaterializeTurnAuditDraftV2:
    return MaterializeTurnAuditDraftV2(
        draft_ref=_ref("turn-audit-draft", execution_id),
        execution_id=execution_id,
        claimed_evidence_handles=proposal.claimed_evidence_handles,
        evidence_pack_ref=evidence_pack.evidence_pack_ref,
        evidence_pack_digest=evidence_pack.digest,
        governed_answer_draft_ref=governed.draft_ref,
        governed_answer_digest=governed.digest,
        citation_binding_draft_ref=citation.draft_ref,
        citation_binding_digest=citation.digest,
        retrieval_status=governed.retrieval_status,
        evidence_review_status=governed.evidence_review_status,
        terminal_status="terminal_completed",
        steps=list(steps),
        idempotency_key=f"{execution_id}:turn-audit",
    )



def _research_packet_ref(execution_id: str) -> str:
    return _ref("research-packet", execution_id)


def _research_terminal_materialization_steps(
    *,
    start_ordinal: int,
    assessment_step: TurnAuditStepV1,
    evidence_pack: GovernanceEvidencePackV1,
    proposal: FinalizeResearchV1,
    packet_ref: str,
    packet: ResearchPacketV1,
    governed: GovernedAnswerDraftV2 | None,
    citation: CitationBindingDraftV2 | None,
) -> list[TurnAuditStepV1]:
    steps = [
        assessment_step,
        TurnAuditStepV1(
            ordinal=start_ordinal + 1,
            step_kind="governance",
            operation="materialize_research_packet",
            status="completed",
            safe_input_digest=_digest(
                [evidence_pack.digest, proposal.model_dump(mode="json")]
            ),
            result_ref=packet_ref,
            result_digest=packet.packet_digest,
            evidence_count=len(packet.evidence),
        ),
    ]
    if governed is not None and citation is not None:
        steps.extend(
            [
                TurnAuditStepV1(
                    ordinal=start_ordinal + 2,
                    step_kind="governance",
                    operation="materialize_packet_bound_answer",
                    status="completed",
                    safe_input_digest=_digest(
                        [packet.packet_digest, governed.digest]
                    ),
                    result_ref=governed.draft_ref,
                    result_digest=governed.digest,
                    evidence_count=len(packet.evidence),
                ),
                TurnAuditStepV1(
                    ordinal=start_ordinal + 3,
                    step_kind="citation",
                    operation="materialize_citation_binding",
                    status="completed",
                    safe_input_digest=_digest(governed.digest),
                    result_ref=citation.draft_ref,
                    result_digest=citation.digest,
                    evidence_count=len(citation.bindings),
                ),
            ]
        )
    return steps


def _research_audit_command(
    *,
    execution_id: str,
    proposal: FinalizeResearchV1,
    evidence_pack: GovernanceEvidencePackV1,
    packet_ref: str,
    packet: ResearchPacketV1,
    retrieval_status: RetrievalStatusV1,
    evidence_review_status: Literal["evidence_aligned", "questionable"],
    governed: GovernedAnswerDraftV2 | None,
    citation: CitationBindingDraftV2 | None,
    steps: Sequence[TurnAuditStepV1],
) -> MaterializeTurnAuditDraftV2:
    handles: list[str] = []
    seen: set[str] = set()
    for finding in proposal.findings:
        for handle in finding.claimed_evidence_handles:
            if handle not in seen:
                handles.append(handle)
                seen.add(handle)
    return MaterializeTurnAuditDraftV2(
        draft_ref=_ref("turn-audit-draft", execution_id),
        execution_id=execution_id,
        claimed_evidence_handles=handles,
        evidence_pack_ref=evidence_pack.evidence_pack_ref,
        evidence_pack_digest=evidence_pack.digest,
        governed_answer_draft_ref=(
            None if governed is None else governed.draft_ref
        ),
        governed_answer_digest=None if governed is None else governed.digest,
        citation_binding_draft_ref=(
            None if citation is None else citation.draft_ref
        ),
        citation_binding_digest=None if citation is None else citation.digest,
        research_packet_ref=packet_ref,
        research_packet_digest=packet.packet_digest,
        retrieval_status=retrieval_status,
        evidence_review_status=evidence_review_status,
        terminal_status="terminal_completed",
        steps=list(steps),
        idempotency_key=f"{execution_id}:turn-audit",
    )

def _prepare_terminal_command(
    *,
    snapshot: ExecutionSnapshotV1,
    evidence_pack_ref: str,
    governed_answer_draft_ref: str,
    citation_binding_draft_ref: str,
    audit_draft_ref: str,
) -> PrepareTerminalV1:
    return PrepareTerminalV1(
        execution_id=snapshot.execution_id,
        expected_version=snapshot.version,
        fencing_token=snapshot.lease.fencing_token,
        evidence_pack_ref=evidence_pack_ref,
        governed_answer_draft_ref=governed_answer_draft_ref,
        citation_binding_draft_ref=citation_binding_draft_ref,
        audit_draft_ref=audit_draft_ref,
    )

def _prepare_research_terminal_command(
    *,
    snapshot: ExecutionSnapshotV1,
    evidence_pack_ref: str,
    research_packet_ref: str,
    research_packet_digest: str,
    audit_draft_ref: str,
    governed_answer_draft_ref: str | None = None,
    citation_binding_draft_ref: str | None = None,
) -> PrepareTerminalV1:
    return PrepareTerminalV1(
        execution_id=snapshot.execution_id,
        expected_version=snapshot.version,
        fencing_token=snapshot.lease.fencing_token,
        result_kind="agent_research",
        evidence_pack_ref=evidence_pack_ref,
        governed_answer_draft_ref=governed_answer_draft_ref,
        citation_binding_draft_ref=citation_binding_draft_ref,
        research_packet_ref=research_packet_ref,
        research_packet_digest=research_packet_digest,
        audit_draft_ref=audit_draft_ref,
    )


def _commit_terminal_command(snapshot: ExecutionSnapshotV1) -> CommitTerminalV1:
    if snapshot.terminal_commit_intent_ref is None:
        raise ValueError("runtime did not bind a terminal commit intent")
    return CommitTerminalV1(
        execution_id=snapshot.execution_id,
        expected_version=snapshot.version,
        fencing_token=snapshot.lease.fencing_token,
        terminal_commit_intent_ref=snapshot.terminal_commit_intent_ref,
    )
