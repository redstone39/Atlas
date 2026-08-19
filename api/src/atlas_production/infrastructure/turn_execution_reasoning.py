from __future__ import annotations

import json
from typing import Literal

from atlas_production.infrastructure.turn_execution_foundation import _digest
from atlas_production.modules.audit.public import TurnAuditStepV1
from atlas_production.modules.result_governance.public import ProvisionalEvidenceAssessmentV1
from atlas_production.modules.turn_execution.public import GateCorrectionFeedbackV1
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.turn_runtime.public import (
    ProvisionalEvidenceCheckV1,
    PromptSkillSelectionTraceV1,
    ReasoningCorrectionV2,
    ReasoningEvaluationV1,
    ReasoningLimitFinalizationV2,
    ReasoningPlanV2,
    ReasoningTraceV4,
)

def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


MAX_MINIMAL_SKILL_FALLBACK_BYTES = (
    max(
        len(
            _canonical_json_bytes(
                PromptSkillSelectionTraceV1(
                    node=node,
                    plan_generation=generation,
                    status="baseline_fallback",
                    fallback_code=fallback_code,
                ).model_dump(mode="json")
            )
        )
        for node in ("deep_initial_planner", "deep_replanner")
        for generation in (1, 4)
        for fallback_code in (
            "selector_unavailable",
            "selector_contract_invalid",
            "selection_outside_catalog",
            "selected_skill_integrity_error",
            "selected_skill_context_exceeded",
            "selected_skill_trace_exceeded",
        )
    )
    + 1
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
    previous: ReasoningTraceV4 | None,
    *,
    status: Literal["planning", "running", "completed", "degraded", "failed"],
    plans: list[ReasoningPlanV2],
    evaluations: list[ReasoningEvaluationV1],
    corrections: list[ReasoningCorrectionV2],
    prompt_skill_catalog: PromptSkillCatalogRefV1 | None = None,
    appended_skill_selection: PromptSkillSelectionTraceV1 | None = None,
    remaining_possible_skill_selection_nodes: int = 0,
    provisional_evidence_checks: list[ProvisionalEvidenceCheckV1] | None = None,
    limit_finalization: ReasoningLimitFinalizationV2 | None = None,
    termination_reason: _ReasoningTerminationReason | None = None,
) -> ReasoningTraceV4:
    if previous is None:
        if prompt_skill_catalog is None:
            raise ValueError("initial reasoning trace requires a prompt skill catalog")
        skill_selections: list[PromptSkillSelectionTraceV1] = []
    else:
        if (
            prompt_skill_catalog is not None
            and prompt_skill_catalog != previous.prompt_skill_catalog
        ):
            raise ValueError("reasoning trace prompt skill catalog is immutable")
        prompt_skill_catalog = previous.prompt_skill_catalog
        skill_selections = list(previous.skill_selections)
    if appended_skill_selection is not None:
        skill_selections.append(appended_skill_selection)
    payload = {
        "prompt_skill_catalog": prompt_skill_catalog,
        "skill_selections": skill_selections,
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
    provisional = ReasoningTraceV4.model_validate(payload)
    digest_payload = provisional.model_dump(mode="json")
    digest_payload.pop("trace_digest")
    result = provisional.model_copy(update={"trace_digest": _digest(digest_payload)})
    encoded = _canonical_json_bytes(result.model_dump(mode="json"))
    required_reserve = (
        remaining_possible_skill_selection_nodes
        * MAX_MINIMAL_SKILL_FALLBACK_BYTES
    )
    if len(encoded) + required_reserve > 32768:
        raise ValueError("reasoning trace cannot preserve future skill selection reserve")
    return result


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
