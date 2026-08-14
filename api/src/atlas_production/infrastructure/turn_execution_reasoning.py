from __future__ import annotations

from typing import Literal

from atlas_production.infrastructure.turn_execution_foundation import _digest
from atlas_production.modules.audit.public import TurnAuditStepV1
from atlas_production.modules.result_governance.public import ProvisionalEvidenceAssessmentV1
from atlas_production.modules.turn_execution.public import GateCorrectionFeedbackV1
from atlas_production.modules.turn_runtime.public import (
    ProvisionalEvidenceCheckV1,
    ReasoningCorrectionV2,
    ReasoningEvaluationV1,
    ReasoningLimitFinalizationV2,
    ReasoningPlanV2,
    ReasoningTraceV3,
)

_ReasoningTerminationReason = Literal[
    "completed",
    "planner_failed",
    "evaluator_unavailable",
    "provisional_evidence_unavailable",
    "replanner_failed",
    "correction_limit_reached",
    "budget_exhausted",
    "deadline_exceeded",
    "execution_failed",
]
_PendingCorrection = tuple[
    Literal["revise_only", "research_then_revise"],
    int,
    list[str],
    str,
    int | None,
    int,
]


def _gate_correction_feedback(
    assessment: ProvisionalEvidenceAssessmentV1,
) -> GateCorrectionFeedbackV1 | None:
    if assessment.consistency not in {"conflict", "insufficient"}:
        return None
    return GateCorrectionFeedbackV1(
        consistency=assessment.consistency,
        failing_segment_ids=[
            result.id for result in assessment.results if result.status == "failure"
        ],
    )


def _merged_correction_kind(
    *,
    evaluation: ReasoningEvaluationV1,
    gate_feedback: GateCorrectionFeedbackV1 | None,
) -> Literal["revise_only", "research_then_revise"] | None:
    if evaluation.verdict == "research_then_revise":
        return "research_then_revise"
    if evaluation.verdict == "revise_only" or gate_feedback is not None:
        return "revise_only"
    return None


def _next_reasoning_trace(
    previous: ReasoningTraceV3 | None,
    *,
    status: Literal["planning", "running", "completed", "degraded", "failed"],
    plans: list[ReasoningPlanV2],
    evaluations: list[ReasoningEvaluationV1],
    corrections: list[ReasoningCorrectionV2],
    provisional_evidence_checks: list[ProvisionalEvidenceCheckV1] | None = None,
    limit_finalization: ReasoningLimitFinalizationV2 | None = None,
    termination_reason: _ReasoningTerminationReason | None = None,
) -> ReasoningTraceV3:
    payload = {
        "trace_revision": 1 if previous is None else previous.trace_revision + 1,
        "trace_digest": "0" * 64,
        "parent_trace_digest": None if previous is None else previous.trace_digest,
        "status": status,
        "plans": plans,
        "evaluations": evaluations,
        "corrections": corrections,
        "provisional_evidence_checks": (
            provisional_evidence_checks
            if provisional_evidence_checks is not None
            else ([] if previous is None else previous.provisional_evidence_checks)
        ),
        "limit_finalization": limit_finalization,
        "termination_reason": termination_reason,
    }
    provisional = ReasoningTraceV3.model_validate(payload)
    digest_payload = provisional.model_dump(mode="json")
    digest_payload.pop("trace_digest")
    return provisional.model_copy(update={"trace_digest": _digest(digest_payload)})


def _provisional_evidence_check(
    *,
    ordinal: int,
    assessment: ProvisionalEvidenceAssessmentV1,
    is_limit_final: bool,
    evaluation_count: int,
) -> ProvisionalEvidenceCheckV1:
    return ProvisionalEvidenceCheckV1(
        ordinal=ordinal,
        candidate_kind="limit_final" if is_limit_final else "normal",
        linked_evaluation_cycle=None if is_limit_final else evaluation_count + 1,
        consistency=assessment.consistency,
        reason_code=assessment.reason_code,
        candidate_disposition="limit_finalized" if is_limit_final else "pending",
        answer_digest=assessment.answer_digest,
        declared_subset_digest=assessment.declared_subset_digest,
        assessment_input_digest=assessment.assessment_input_digest,
        assessment_output_digest=assessment.assessment_output_digest,
        visual_image_digests=assessment.visual_image_digests,
    )


def _provisional_gate_audit_step(
    *,
    ordinal: int,
    assessment: ProvisionalEvidenceAssessmentV1,
    evidence_count: int,
) -> TurnAuditStepV1:
    return TurnAuditStepV1(
        ordinal=ordinal,
        step_kind="governance",
        operation="provisional_declared_evidence_gate",
        status=(
            "skipped"
            if assessment.state == "not_attempted"
            else "completed"
            if assessment.state == "completed"
            else "failed"
        ),
        safe_input_digest=_digest(
            [
                assessment.answer_digest,
                assessment.declared_subset_digest,
                assessment.visual_image_digests,
            ]
        ),
        evidence_count=evidence_count,
    )


def _completed_correction(
    *,
    cycle: int,
    pending: _PendingCorrection,
    tool_invocation_end: int,
    result_evaluation: int,
) -> ReasoningCorrectionV2:
    kind, trigger, codes, summary, plan_generation, tool_start = pending
    return ReasoningCorrectionV2(
        cycle=cycle,
        kind=kind,
        triggering_evaluation=trigger,
        plan_generation=plan_generation,
        tool_invocation_start=(
            tool_start if kind == "research_then_revise" else None
        ),
        tool_invocation_end=(
            tool_invocation_end if kind == "research_then_revise" else None
        ),
        result_evaluation=result_evaluation,
        addressed_finding_codes=codes,
        summary=summary,
    )


def _pending_correction(
    *,
    correction_kind: Literal["revise_only", "research_then_revise"],
    evaluation: ReasoningEvaluationV1,
    plan_generation: int | None,
    next_tool_invocation: int,
) -> _PendingCorrection:
    summary = (
        evaluation.summary
        if evaluation.verdict in {"revise_only", "research_then_revise"}
        and evaluation.summary
        else "Correction requested by declared evidence Gate."
    )
    return (
        correction_kind,
        evaluation.cycle,
        evaluation.finding_codes,
        summary,
        plan_generation,
        next_tool_invocation,
    )


def _limit_finalization_pending(
    evaluation: ReasoningEvaluationV1,
) -> tuple[int, str]:
    summary = (
        evaluation.summary
        if evaluation.verdict in {"revise_only", "research_then_revise"}
        and evaluation.summary
        else (
            "Correction limit reached with an unresolved declared evidence Gate "
            "finding."
        )
    )
    return evaluation.cycle, summary
