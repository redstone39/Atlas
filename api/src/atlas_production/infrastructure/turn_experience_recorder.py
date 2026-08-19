from __future__ import annotations

from atlas_production.modules.result_governance.public import (
    GovernedAnswerDraftV2,
    ResultGovernanceDraftOwnerV2,
)
from atlas_production.modules.turn_experience.public import (
    MaterializeTurnExperienceV1,
    TurnExperienceCorrectionV1,
    TurnExperienceDeepTraceV1,
    TurnExperienceExecutionSkillSelectionV1,
    TurnExperienceEvaluationV1,
    TurnExperienceEvidenceCheckV1,
    TurnExperienceGovernanceV1,
    TurnExperiencePlanGenerationV1,
    TurnExperienceRouteRefV1,
    TurnExperienceSkillSelectionV1,
    TurnExperienceStore,
    TurnExperienceTerminalV1,
    TurnExperienceUsageV1,
    TurnExperienceV1,
)
from atlas_production.modules.turn_runtime.public import (
    ExecutionSnapshotV1,
    ExecutionState,
    ReasoningEvaluationV1,
    TerminalOutcomeV1,
    TurnRuntimeOwner,
)


class TurnExperienceRecordingError(RuntimeError):
    """Pinned Turn sources cannot produce a valid immutable experience."""


def _required_completed_ref(value: str | None, field: str) -> str:
    if value is None:
        raise TurnExperienceRecordingError(f"completed outcome is missing {field}")
    return value


def _project_evaluation(
    evaluation: ReasoningEvaluationV1,
) -> TurnExperienceEvaluationV1:
    score = evaluation.score
    score_fields: dict[str, object | None]
    if score is None:
        score_fields = {
            "rubric_version": None,
            "plan_coverage": None,
            "evidence_handling": None,
            "conflict_handling": None,
            "gap_resolution": None,
            "revision_completion": None,
            "total": None,
        }
    else:
        score_fields = score.model_dump(mode="python")
    return TurnExperienceEvaluationV1(
        cycle=evaluation.cycle,
        verdict=evaluation.verdict,
        finding_codes=evaluation.finding_codes,
        unavailable_reason=evaluation.unavailable_reason,
        **score_fields,
    )


def _project_deep_trace(snapshot: ExecutionSnapshotV1) -> TurnExperienceDeepTraceV1 | None:
    trace = snapshot.reasoning_trace
    if snapshot.reasoning_mode == "standard":
        if trace is not None:
            raise TurnExperienceRecordingError(
                "standard execution cannot carry a reasoning trace"
            )
        return None
    if trace is None:
        return None
    planner_catalog = next(
        catalog
        for catalog in snapshot.prompt_skill_catalogs
        if catalog.category == "planner"
    )
    if planner_catalog != trace.prompt_skill_catalog:
        raise TurnExperienceRecordingError(
            "deep trace catalog does not match pinned execution catalog"
        )

    return TurnExperienceDeepTraceV1(
        prompt_skill_catalog=trace.prompt_skill_catalog,
        trace_revision=trace.trace_revision,
        trace_digest=trace.trace_digest,
        parent_trace_digest=trace.parent_trace_digest,
        status=trace.status,
        termination_reason=trace.termination_reason,
        skill_selections=[
            TurnExperienceSkillSelectionV1(
                node=selection.node,
                plan_generation=selection.plan_generation,
                status=selection.status,
                selected_skills=selection.selected_skills,
                fallback_code=selection.fallback_code,
            )
            for selection in trace.skill_selections
        ],
        plans=[
            TurnExperiencePlanGenerationV1(
                generation=plan.generation,
                parent_generation=plan.parent_generation,
                pending_count=sum(item.status == "pending" for item in plan.items),
                completed_count=sum(item.status == "completed" for item in plan.items),
                skipped_count=sum(item.status == "skipped" for item in plan.items),
            )
            for plan in trace.plans
        ],
        evaluations=[_project_evaluation(evaluation) for evaluation in trace.evaluations],
        corrections=[
            TurnExperienceCorrectionV1(
                cycle=correction.cycle,
                kind=correction.kind,
                triggering_evaluation=correction.triggering_evaluation,
                plan_generation=correction.plan_generation,
                tool_invocation_start=correction.tool_invocation_start,
                tool_invocation_end=correction.tool_invocation_end,
                result_evaluation=correction.result_evaluation,
                addressed_finding_codes=correction.addressed_finding_codes,
            )
            for correction in trace.corrections
        ],
        evidence_checks=[
            TurnExperienceEvidenceCheckV1(
                ordinal=check.ordinal,
                candidate_kind=check.candidate_kind,
                linked_evaluation_cycle=check.linked_evaluation_cycle,
                consistency=check.consistency,
                reason_code=check.reason_code,
                candidate_disposition=check.candidate_disposition,
                answer_digest=check.answer_digest,
                declared_subset_digest=check.declared_subset_digest,
                assessment_input_digest=check.assessment_input_digest,
                assessment_output_digest=check.assessment_output_digest,
                visual_image_digests=check.visual_image_digests,
            )
            for check in trace.provisional_evidence_checks
        ],
        limit_finalization_triggering_evaluation=(
            trace.limit_finalization.triggering_evaluation
            if trace.limit_finalization is not None
            else None
        ),
    )


def project_turn_experience(
    snapshot: ExecutionSnapshotV1,
    outcome: TerminalOutcomeV1,
    governed: GovernedAnswerDraftV2,
) -> MaterializeTurnExperienceV1:
    if snapshot.state != ExecutionState.TERMINAL_COMPLETED:
        raise TurnExperienceRecordingError("execution is not terminal_completed")
    if outcome.outcome != "completed":
        raise TurnExperienceRecordingError("failed terminal outcome is not recordable")
    if outcome.execution_id != snapshot.execution_id or governed.execution_id != snapshot.execution_id:
        raise TurnExperienceRecordingError("experience sources cross execution identities")

    terminal_commit_intent_ref = _required_completed_ref(
        outcome.terminal_commit_intent_ref, "terminal_commit_intent_ref"
    )
    evidence_pack_ref = _required_completed_ref(outcome.evidence_pack_ref, "evidence_pack_ref")
    governed_answer_draft_ref = _required_completed_ref(
        outcome.governed_answer_draft_ref, "governed_answer_draft_ref"
    )
    citation_binding_draft_ref = _required_completed_ref(
        outcome.citation_binding_draft_ref, "citation_binding_draft_ref"
    )
    audit_draft_ref = _required_completed_ref(outcome.audit_draft_ref, "audit_draft_ref")
    if snapshot.terminal_commit_intent_ref != terminal_commit_intent_ref:
        raise TurnExperienceRecordingError("terminal intent ref does not match execution snapshot")
    if governed.draft_ref != governed_answer_draft_ref:
        raise TurnExperienceRecordingError("governed draft ref does not match terminal outcome")

    vision = snapshot.route.vision_route
    resolved_count = sum(
        mapping.resolution_status == "resolved"
        for mapping in governed.declared_evidence_mappings
    )
    success_count = sum(result.status == "success" for result in governed.assessment_results)

    return MaterializeTurnExperienceV1(
        experience_ref=f"turn-experience:{snapshot.execution_id}:v1",
        execution_id=snapshot.execution_id,
        turn_id=snapshot.turn_id,
        input_digest=snapshot.input_digest,
        applied_guidance_revision=snapshot.applied_guidance_revision,
        applied_guidance_digest=snapshot.applied_guidance_digest,
        response_language=snapshot.response_language,
        reasoning_mode=snapshot.reasoning_mode,
        route=TurnExperienceRouteRefV1(
            route_id=snapshot.route.route_id,
            route_revision=snapshot.route.route_revision,
            runtime_policy_revision=snapshot.route.runtime_policy_revision,
            vision_route_id=vision.route_id if vision is not None else None,
            vision_route_revision=vision.route_revision if vision is not None else None,
            vision_runtime_policy_revision=(
                vision.runtime_policy_revision if vision is not None else None
            ),
        ),
        usage=TurnExperienceUsageV1.model_validate(snapshot.budget.model_dump()),
        prompt_skill_selections=[
            TurnExperienceExecutionSkillSelectionV1(
                category=selection.category,
                node=selection.node,
                candidate_ordinal=selection.candidate_ordinal,
                candidate_kind=selection.candidate_kind,
                status=selection.status,
                selected_skills=selection.selected_skills,
                fallback_code=selection.fallback_code,
            )
            for selection in snapshot.prompt_skill_selections
        ],
        terminal=TurnExperienceTerminalV1(
            terminal_commit_intent_ref=terminal_commit_intent_ref,
            evidence_pack_ref=evidence_pack_ref,
            governed_answer_draft_ref=governed_answer_draft_ref,
            citation_binding_draft_ref=citation_binding_draft_ref,
            audit_draft_ref=audit_draft_ref,
            committed_at=outcome.committed_at,
        ),
        governance=TurnExperienceGovernanceV1(
            governed_answer_draft_ref=governed.draft_ref,
            governed_answer_digest=governed.digest,
            retrieval_status=governed.retrieval_status,
            evidence_review_status=governed.evidence_review_status,
            evidence_review_reason_codes=governed.evidence_review_reason_codes,
            declared_evidence_count=len(governed.declared_evidence_mappings),
            resolved_evidence_count=resolved_count,
            unresolved_evidence_count=(
                len(governed.declared_evidence_mappings) - resolved_count
            ),
            declared_evidence_reason_codes=[
                mapping.reason_code for mapping in governed.declared_evidence_mappings
            ],
            assessment_state=governed.assessment_state,
            assessment_reason_code=governed.assessment_reason_code,
            assessment_version=governed.assessment_version,
            assessment_consistency=governed.assessment_consistency,
            assessment_answer_digest=governed.assessment_answer_digest,
            assessment_declared_subset_digest=governed.assessment_declared_subset_digest,
            assessment_visual_image_digests=governed.assessment_visual_image_digests,
            assessment_input_digest=governed.assessment_input_digest,
            assessment_output_digest=governed.assessment_output_digest,
            assessment_success_count=success_count,
            assessment_failure_count=len(governed.assessment_results) - success_count,
        ),
        deep_trace=_project_deep_trace(snapshot),
        idempotency_key=f"{snapshot.execution_id}:turn-experience-v1",
    )


class TurnExperienceRecorder:
    def __init__(
        self,
        runtime: TurnRuntimeOwner,
        governance: ResultGovernanceDraftOwnerV2,
        store: TurnExperienceStore,
    ) -> None:
        self._runtime = runtime
        self._governance = governance
        self._store = store

    def record_execution(self, execution_id: str) -> TurnExperienceV1:
        snapshot = self._runtime.snapshot(execution_id)
        outcome = self._runtime.terminal_outcome(execution_id)
        if outcome is None:
            raise TurnExperienceRecordingError("execution has no terminal outcome")
        if outcome.outcome != "completed":
            raise TurnExperienceRecordingError("failed terminal outcome is not recordable")
        draft_ref = _required_completed_ref(
            outcome.governed_answer_draft_ref, "governed_answer_draft_ref"
        )
        governed = self._governance.read_v2(draft_ref)
        if governed is None:
            raise TurnExperienceRecordingError("completed governed draft is unavailable")
        return self._store.materialize(project_turn_experience(snapshot, outcome, governed))


__all__ = [
    "TurnExperienceRecorder",
    "TurnExperienceRecordingError",
    "project_turn_experience",
]
