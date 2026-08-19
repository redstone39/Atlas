from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Callable, Literal, Protocol, Sequence

from pydantic import ValidationError

from atlas_production.infrastructure.prompt_skill_selection import (
    PromptSkillSelectionResolutionError,
    admit_execution_prompt_skill_selection,
    resolve_selected_skill_refs,
    validate_exact_skill_instructions,
)
from atlas_production.infrastructure.strict_posthoc_claim_evaluator import (
    ClaimAssessmentUnavailable,
)
from atlas_production.infrastructure.turn_execution_foundation import (
    _contract_repair_remaining,
    _digest,
    _has_legal_tool,
    _schema_retry_origin,
    _validate_model_input,
)
from atlas_production.infrastructure.turn_execution_reasoning import (
    _ReasoningTerminationReason as ReasoningTerminationReason,
    _completed_correction,
    _gate_correction_feedback,
    _limit_finalization_pending,
    _merged_correction_kind,
    _next_reasoning_trace,
    _pending_correction,
    _provisional_evidence_check,
    _provisional_gate_audit_step,
)
from atlas_production.infrastructure.turn_execution_tool_payloads import (
    _complete_tool_command,
    _retrieval_error_status,
    _tool_audit_step,
    _tool_reservation_projection,
)
from atlas_production.infrastructure.turn_execution_terminal_payloads import (
    _assessment_audit_step,
    _audit_command,
    _citation_command,
    _commit_terminal_command,
    _declared_lineage,
    _finalized_answer,
    _governance_command,
    _invalid_governance_command,
    _prepare_terminal_command,
    _terminal_materialization_steps,
    _terminal_retrieval_status,
)

from atlas_production.modules.audit.public import (
    TurnAuditDraftOwnerV2,
    TurnAuditStepV1,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftOwnerV2,
)
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    FinalizedAnswerV1,
    PostHocAnswerEvaluatorV2,
    ProvisionalEvidenceAssessmentV1,
    ResultGovernanceDraftOwnerV2,
    RetrievalStatusV1,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalog,
    PromptSkillCatalogRefV1,
    PromptSkillCatalogV1,
    PromptSkillExactReader,
    PromptSkillInstructionsV1,
)
from atlas_production.modules.retrieval.public import (
    KnowledgeToolObservationV1,
    RetrievalEvidenceLineageV1,
    RetrievalOwner,
    VisualImagePayloadV1,
)
from atlas_production.modules.turn_execution.public import (
    AnswerCandidateNodeContextV1,
    DeepReasoningContractError,
    DeepReasoningModel,
    FinalizeAnswerV1,
    InitialPlanningNodeContextV1,
    ModelContractViolationV1,
    ReplanningNodeContextV1,
    SkillSelectionRequestV2,
    SkillSelectorModel,
    StrictTurnModel,
    StrictTurnModelSession,
    TurnExecutionOrchestrator,
    TurnModelInputV3,
)
from atlas_production.modules.turn_runtime.public import (
    BeginResultGovernanceV1,
    ClaimSchemaRetryV1,
    ExecutionPromptSkillSelectionTraceV1,
    ExecutionSnapshotV1,
    ExecutionState,
    FailCarrierExecutionV1,
    PromptSkillSelectionFallbackCode,
    PromptSkillSelectionTraceV1,
    ReasoningEvaluationV1,
    ReasoningPhase,
    ReasoningCorrectionV2,
    ReasoningLimitFinalizationV2,
    ReasoningPlanV2,
    ProvisionalEvidenceCheckV1,
    ReasoningProgressStatus,
    ReasoningTraceV4,
    RecordExecutionPromptSkillSelectionV1,
    RecordReasoningProgressV1,
    RequestModelActionV1,
    SchemaRetryOriginCode,
    TERMINAL_STATES,
    TurnRuntimeOwner,
    TurnRuntimeBudgetExceeded,
)
logger = logging.getLogger(__name__)

def _capability_rejection_audit_step(
    *,
    ordinal: int,
    safe_input_digest: str,
    violation: ModelContractViolationV1,
) -> TurnAuditStepV1:
    return TurnAuditStepV1(
        ordinal=ordinal,
        step_kind="model",
        operation="provider_capability_rejected",
        status="failed",
        safe_input_digest=safe_input_digest,
        input_tokens=violation.input_tokens,
        output_tokens=violation.output_tokens,
    )



class TurnModelInputSource(Protocol):
    def build(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
    ) -> TurnModelInputV3: ...


def _context_token_reservation(
    source: TurnModelInputSource,
    snapshot: ExecutionSnapshotV1,
    observations: Sequence[KnowledgeToolObservationV1],
    contract_repair_remaining: int,
    count_tokens: Callable[[TurnModelInputV3, bool], int],
    *,
    finalize_only: bool,
    reasoning_plan: ReasoningPlanV2 | None = None,
) -> int:
    """Converge on the route-tokenized size of the post-CAS model input."""

    initial = source.build(
            snapshot,
            observations=observations,
            contract_repair_remaining=contract_repair_remaining,
        )
    if reasoning_plan is not None:
        initial = initial.model_copy(update={"reasoning_plan": reasoning_plan})
    estimate = count_tokens(
        initial,
        finalize_only=finalize_only,
    )
    for _ in range(8):
        predicted_budget = snapshot.budget.model_copy(
            update={
                "provider_invocations": snapshot.budget.provider_invocations + 1,
                "context_tokens": snapshot.budget.context_tokens + estimate,
            }
        )
        predicted = snapshot.model_copy(
            update={
                "state": ExecutionState.AWAITING_MODEL_ACTION,
                "version": snapshot.version + 1,
                "budget": predicted_budget,
            }
        )
        predicted_input = source.build(
                predicted,
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            )
        if reasoning_plan is not None:
            predicted_input = predicted_input.model_copy(
                update={"reasoning_plan": reasoning_plan}
            )
        required = count_tokens(
            predicted_input,
            finalize_only=finalize_only,
        )
        if required <= estimate:
            return estimate
        estimate = required
    return estimate + 32








class StatelessTurnExecutionOrchestrator(TurnExecutionOrchestrator):
    """Sequential owner-call coordinator with no repository or durable state."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeOwner,
        model: StrictTurnModel,
        model_inputs: TurnModelInputSource,
        retrieval: RetrievalOwner,
        result_governance: ResultGovernanceDraftOwnerV2,
        citation: CitationBindingDraftOwnerV2,
        audit: TurnAuditDraftOwnerV2,
        evaluator: PostHocAnswerEvaluatorV2,
        reasoning_model: DeepReasoningModel | None = None,
        skill_selector_model: SkillSelectorModel | None = None,
        prompt_skill_catalog: PromptSkillCatalog | None = None,
        prompt_skill_exact_reader: PromptSkillExactReader | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._model = model
        self._model_inputs = model_inputs
        self._retrieval = retrieval
        self._result_governance = result_governance
        self._citation = citation
        self._audit = audit
        self._evaluator = evaluator
        self._reasoning_model = reasoning_model
        self._skill_selector_model = skill_selector_model
        self._prompt_skill_catalog = prompt_skill_catalog
        self._prompt_skill_exact_reader = prompt_skill_exact_reader
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _claim_schema_retry(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        purpose: str,
        semantic_ordinal: int,
        error: Exception,
    ) -> tuple[ExecutionSnapshotV1, SchemaRetryOriginCode] | None:
        origin = _schema_retry_origin(error)
        if origin is None:
            return None
        next_ordinal = snapshot.budget.schema_retries + 1
        try:
            claimed = self._runtime.claim_schema_retry(
                ClaimSchemaRetryV1(
                    execution_id=snapshot.execution_id,
                    fencing_token=snapshot.lease.fencing_token,
                    claim_key=(
                        f"{purpose}:{semantic_ordinal}:schema-retry:{next_ordinal}"
                    ),
                    origin_error_code=origin,
                )
            )
        except TurnRuntimeBudgetExceeded:
            return None
        return claimed, origin

    def _record_reasoning_progress(
        self,
        snapshot: ExecutionSnapshotV1,
        trace: ReasoningTraceV4,
        *,
        phase: ReasoningPhase,
        progress_status: ReasoningProgressStatus,
        cycle: int | None = None,
        message_code: str,
        message_params: dict[str, str | int | bool | None] | None = None,
    ) -> ExecutionSnapshotV1:
        return self._runtime.record_reasoning_progress(
            RecordReasoningProgressV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                trace=trace,
                phase=phase,
                progress_status=progress_status,
                cycle=cycle,
                message_code=message_code,
                message_params=message_params or {},
            )
        )

    def _assess_provisional_evidence(
        self,
        *,
        snapshot: ExecutionSnapshotV1,
        proposal: FinalizeAnswerV1,
        visual_images_by_handle: dict[str, VisualImagePayloadV1],
        assessment_ordinal: int,
        assessment_cache: dict[
            tuple[str, str, tuple[str, ...], str],
            ProvisionalEvidenceAssessmentV1,
        ],
    ) -> tuple[ExecutionSnapshotV1, ProvisionalEvidenceAssessmentV1]:
        if snapshot.catalog_ref is None:
            raise ValueError("candidate assessment lost its catalog ref")
        finalized_answer = FinalizedAnswerV1(
            segments=[segment.model_dump(mode="json") for segment in proposal.segments]
        )
        declared_subset = self._retrieval.read_declared_evidence_subset(
            execution_id=snapshot.execution_id,
            catalog_ref=snapshot.catalog_ref,
            handles=proposal.claimed_evidence_handles,
            visual_images=list(visual_images_by_handle.values()),
        )
        answer_digest = _digest(finalized_answer.model_dump(mode="json"))
        visual_image_digests = [
            image.image_digest for image in declared_subset.visual_images
        ]
        common = {
            "answer_digest": answer_digest,
            "declared_subset_digest": declared_subset.digest,
            "visual_image_digests": visual_image_digests,
        }
        cache_key = (
            answer_digest,
            declared_subset.digest,
            tuple(visual_image_digests),
            "provisional-declared-evidence-v1",
        )
        cached = assessment_cache.get(cache_key)
        if cached is not None:
            return snapshot, cached

        def remember(
            assessment: ProvisionalEvidenceAssessmentV1,
        ) -> tuple[ExecutionSnapshotV1, ProvisionalEvidenceAssessmentV1]:
            assessment_cache[cache_key] = assessment
            return snapshot, assessment

        if not proposal.claimed_evidence_handles:
            return remember(
                ProvisionalEvidenceAssessmentV1(
                    state="not_attempted",
                    consistency="not_applicable",
                    reason_code="empty_declaration",
                    **common,
                )
            )
        if not declared_subset.items:
            return remember(
                ProvisionalEvidenceAssessmentV1(
                    state="not_attempted",
                    consistency="insufficient",
                    reason_code="no_resolved_declared_evidence",
                    **common,
                )
            )
        try:
            snapshot = self._runtime.request_model_action(
                RequestModelActionV1(
                    execution_id=snapshot.execution_id,
                    expected_version=snapshot.version,
                    fencing_token=snapshot.lease.fencing_token,
                    context_tokens=0,
                )
            )
            assessment = self._evaluator.assess(
                execution_id=snapshot.execution_id,
                finalized_answer=finalized_answer,
                declared_evidence_subset=declared_subset,
                deadline_at=snapshot.deadline_at,
                route=snapshot.route,
                assessment_ordinal=assessment_ordinal,
            )
        except ClaimAssessmentUnavailable as error:
            return remember(
                ProvisionalEvidenceAssessmentV1(
                    state="unavailable",
                    consistency="unavailable",
                    reason_code=error.reason_code,
                    **common,
                )
            )
        except TurnRuntimeBudgetExceeded:
            return remember(
                ProvisionalEvidenceAssessmentV1(
                    state="unavailable",
                    consistency="unavailable",
                    reason_code=(
                        "deadline_elapsed"
                        if self._clock() >= snapshot.deadline_at
                        else "physical_limit_rejected"
                    ),
                    **common,
                )
            )
        if (
            assessment.answer_digest != answer_digest
            or assessment.declared_subset_digest != declared_subset.digest
            or assessment.visual_image_digests != visual_image_digests
        ):
            raise ValueError("provisional evidence assessment binding changed")
        partially_unresolved = any(
            mapping.resolution_status == "unresolved"
            for mapping in declared_subset.mappings
        )
        if partially_unresolved and assessment.consistency != "conflict":
            assessment = assessment.model_copy(
                update={
                    "consistency": "insufficient",
                    "reason_code": "partially_unresolved_declared_evidence",
                }
            )
        return remember(assessment)

    def _load_prompt_skill_catalog(
        self,
        snapshot: ExecutionSnapshotV1,
        category: Literal["planner", "answer"],
    ) -> tuple[PromptSkillCatalogRefV1, PromptSkillCatalogV1 | None]:
        catalog_ref = next(
            catalog
            for catalog in snapshot.prompt_skill_catalogs
            if catalog.category == category
        )
        if self._prompt_skill_catalog is None:
            raise ValueError("deep execution has no prompt skill catalog reader")
        try:
            catalog = self._prompt_skill_catalog.read_catalog(catalog_ref)
        except Exception:
            return catalog_ref, None
        if catalog.ref != catalog_ref:
            return catalog_ref, None
        return catalog_ref, catalog

    def _post_selector_remaining_limits(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        completed_reasoning_corrections: int,
    ) -> dict[str, int]:
        return {
            "provider_invocations": max(
                0,
                snapshot.policy.max_provider_invocations
                - (snapshot.budget.provider_invocations + 1),
            ),
            "context_tokens": max(
                0,
                snapshot.policy.context_token_budget - snapshot.budget.context_tokens,
            ),
            "schema_retries": max(
                0,
                snapshot.policy.max_schema_retries_per_turn
                - snapshot.budget.schema_retries,
            ),
            "reasoning_revision_cycles": max(
                0,
                snapshot.policy.max_reasoning_revision_cycles
                - completed_reasoning_corrections,
            ),
            "deadline_seconds": max(
                0, int((snapshot.deadline_at - self._clock()).total_seconds())
            ),
        }

    @staticmethod
    def _selector_failure_code(
        error: Exception,
    ) -> PromptSkillSelectionFallbackCode:
        if isinstance(error, PromptSkillSelectionResolutionError):
            return error.fallback_code
        if isinstance(error, DeepReasoningContractError):
            if error.safe_code == "selector_contract_invalid":
                return "selector_contract_invalid"
            if error.safe_code == "selection_outside_catalog":
                return "selection_outside_catalog"
        return "selector_unavailable"

    def _select_prompt_skills(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
        node_context: InitialPlanningNodeContextV1 | ReplanningNodeContextV1,
        catalog: PromptSkillCatalogV1 | None,
        plan_generation: int,
    ) -> tuple[
        ExecutionSnapshotV1,
        PromptSkillSelectionTraceV1,
        tuple[PromptSkillInstructionsV1, ...],
        int,
        int,
    ]:
        node = node_context.node
        if catalog is None:
            return (
                snapshot,
                PromptSkillSelectionTraceV1(
                    node=node,
                    plan_generation=plan_generation,
                    status="baseline_fallback",
                    fallback_code="selected_skill_integrity_error",
                ),
                (),
                0,
                0,
            )
        candidates = tuple(catalog.skills)
        if not candidates:
            return (
                snapshot,
                PromptSkillSelectionTraceV1(
                    node=node,
                    plan_generation=plan_generation,
                    status="not_applicable",
                ),
                (),
                0,
                0,
            )
        if self._skill_selector_model is None:
            raise ValueError("deep execution has no skill selector model")
        request = SkillSelectionRequestV2(
            node=node,
            node_context=node_context,
            candidates=candidates,
        )
        try:
            context_tokens = (
                self._skill_selector_model.estimate_selection_request_tokens(
                    snapshot, request
                )
            )
            snapshot = self._runtime.request_model_action(
                RequestModelActionV1(
                    execution_id=snapshot.execution_id,
                    expected_version=snapshot.version,
                    fencing_token=snapshot.lease.fencing_token,
                    context_tokens=context_tokens,
                )
            )
            result = self._skill_selector_model.select(snapshot, request)
        except Exception as error:
            return (
                snapshot,
                PromptSkillSelectionTraceV1(
                    node=node,
                    plan_generation=plan_generation,
                    status="baseline_fallback",
                    fallback_code=self._selector_failure_code(error),
                ),
                (),
                0,
                0,
            )
        try:
            refs = resolve_selected_skill_refs(
                candidates,
                result.decision.selected_skill_ids,
            )
            if self._prompt_skill_exact_reader is None:
                raise PromptSkillSelectionResolutionError(
                    "selected_skill_integrity_error"
                )
            try:
                resolved = tuple(
                    self._prompt_skill_exact_reader.read_instructions(ref)
                    for ref in refs
                )
            except Exception as error:
                raise PromptSkillSelectionResolutionError(
                    "selected_skill_integrity_error"
                ) from error
            selected_skills = validate_exact_skill_instructions(refs, resolved)
        except PromptSkillSelectionResolutionError as error:
            return (
                snapshot,
                PromptSkillSelectionTraceV1(
                    node=node,
                    plan_generation=plan_generation,
                    status="baseline_fallback",
                    fallback_code=error.fallback_code,
                ),
                (),
                result.input_tokens,
                result.output_tokens,
            )
        return (
            snapshot,
            PromptSkillSelectionTraceV1(
                node=node,
                plan_generation=plan_generation,
                status="selected",
                selected_skills=list(refs),
            ),
            selected_skills,
            result.input_tokens,
            result.output_tokens,
        )

    def _select_and_begin_answer_candidate(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        session: StrictTurnModelSession,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
        node_context: AnswerCandidateNodeContextV1,
        catalog: PromptSkillCatalogV1 | None,
        reasoning_plan: ReasoningPlanV2 | None,
    ) -> tuple[ExecutionSnapshotV1, int, int]:
        candidates = () if catalog is None else tuple(catalog.skills)
        selected_skills: tuple[PromptSkillInstructionsV1, ...] = ()
        selector_input_tokens = 0
        selector_output_tokens = 0
        if catalog is None:
            selection = ExecutionPromptSkillSelectionTraceV1(
                category="answer",
                node="answer_candidate",
                candidate_ordinal=node_context.candidate_ordinal,
                candidate_kind=node_context.candidate_kind,
                status="baseline_fallback",
                fallback_code="selected_skill_integrity_error",
            )
        elif not candidates:
            selection = ExecutionPromptSkillSelectionTraceV1(
                category="answer",
                node="answer_candidate",
                candidate_ordinal=node_context.candidate_ordinal,
                candidate_kind=node_context.candidate_kind,
                status="not_applicable",
            )
        elif self._skill_selector_model is None:
            selection = ExecutionPromptSkillSelectionTraceV1(
                category="answer",
                node="answer_candidate",
                candidate_ordinal=node_context.candidate_ordinal,
                candidate_kind=node_context.candidate_kind,
                status="baseline_fallback",
                fallback_code="selector_unavailable",
            )
        else:
            request = SkillSelectionRequestV2(
                node="answer_candidate",
                node_context=node_context,
                candidates=candidates,
            )
            try:
                context_tokens = (
                    self._skill_selector_model.estimate_selection_request_tokens(
                        snapshot,
                        request,
                    )
                )
                snapshot = self._runtime.request_model_action(
                    RequestModelActionV1(
                        execution_id=snapshot.execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        context_tokens=context_tokens,
                    )
                )
                result = self._skill_selector_model.select(snapshot, request)
                selector_input_tokens = result.input_tokens
                selector_output_tokens = result.output_tokens
                refs = resolve_selected_skill_refs(
                    candidates,
                    result.decision.selected_skill_ids,
                )
                if self._prompt_skill_exact_reader is None:
                    raise PromptSkillSelectionResolutionError(
                        "selected_skill_integrity_error"
                    )
                try:
                    resolved = tuple(
                        self._prompt_skill_exact_reader.read_instructions(ref)
                        for ref in refs
                    )
                except Exception as error:
                    raise PromptSkillSelectionResolutionError(
                        "selected_skill_integrity_error"
                    ) from error
                selected_skills = validate_exact_skill_instructions(refs, resolved)
                selection = ExecutionPromptSkillSelectionTraceV1(
                    category="answer",
                    node="answer_candidate",
                    candidate_ordinal=node_context.candidate_ordinal,
                    candidate_kind=node_context.candidate_kind,
                    status="selected",
                    selected_skills=list(refs),
                )
            except Exception as error:
                selected_skills = ()
                selection = ExecutionPromptSkillSelectionTraceV1(
                    category="answer",
                    node="answer_candidate",
                    candidate_ordinal=node_context.candidate_ordinal,
                    candidate_kind=node_context.candidate_kind,
                    status="baseline_fallback",
                    fallback_code=self._selector_failure_code(error),
                )
        candidate_input = self._model_inputs.build(
            snapshot,
            observations=observations,
            contract_repair_remaining=contract_repair_remaining,
        )
        if reasoning_plan is not None:
            candidate_input = candidate_input.model_copy(
                update={"reasoning_plan": reasoning_plan}
            )
        _validate_model_input(snapshot, candidate_input)
        if selected_skills:
            try:
                selected_context_tokens = (
                    session.estimate_begin_answer_candidate_tokens(
                        candidate_input,
                        candidate_ordinal=node_context.candidate_ordinal,
                        candidate_kind=node_context.candidate_kind,
                        selected_skills=selected_skills,
                    )
                )
                if selected_context_tokens > (
                    snapshot.policy.context_token_budget
                    - snapshot.budget.context_tokens
                ):
                    raise ValueError("selected answer Skill context exceeds budget")
            except Exception:
                selected_skills = ()
                selection = ExecutionPromptSkillSelectionTraceV1(
                    category="answer",
                    node="answer_candidate",
                    candidate_ordinal=node_context.candidate_ordinal,
                    candidate_kind=node_context.candidate_kind,
                    status="baseline_fallback",
                    fallback_code="selected_skill_context_exceeded",
                )
        total_possible_nodes = (
            2
            if snapshot.reasoning_mode == "standard"
            else min(6, snapshot.policy.max_reasoning_revision_cycles + 3)
        )
        remaining_possible_nodes = max(
            0,
            total_possible_nodes - len(snapshot.prompt_skill_selections) - 1,
        )
        admitted = admit_execution_prompt_skill_selection(
            snapshot.prompt_skill_selections,
            selection,
            remaining_possible_nodes=remaining_possible_nodes,
        )
        if admitted.status != "selected":
            selected_skills = ()
        snapshot = self._runtime.record_prompt_skill_selection(
            RecordExecutionPromptSkillSelectionV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                selection=admitted,
            )
        )
        candidate_input = self._model_inputs.build(
            snapshot,
            observations=observations,
            contract_repair_remaining=contract_repair_remaining,
        )
        if reasoning_plan is not None:
            candidate_input = candidate_input.model_copy(
                update={"reasoning_plan": reasoning_plan}
            )
        _validate_model_input(snapshot, candidate_input)
        session.begin_answer_candidate(
            candidate_input,
            candidate_ordinal=node_context.candidate_ordinal,
            candidate_kind=node_context.candidate_kind,
            selected_skills=selected_skills,
        )
        return snapshot, selector_input_tokens, selector_output_tokens

    def _append_skill_selection(
        self,
        snapshot: ExecutionSnapshotV1,
        trace: ReasoningTraceV4,
        selection: PromptSkillSelectionTraceV1,
        *,
        plans: list[ReasoningPlanV2],
        evaluations: list[ReasoningEvaluationV1],
        corrections: list[ReasoningCorrectionV2],
    ) -> tuple[ReasoningTraceV4, PromptSkillSelectionTraceV1]:
        remaining_nodes = max(
            0,
            snapshot.policy.max_reasoning_revision_cycles
            + 1
            - (len(trace.skill_selections) + 1),
        )
        try:
            return (
                _next_reasoning_trace(
                    trace,
                    status=trace.status,
                    plans=plans,
                    evaluations=evaluations,
                    corrections=corrections,
                    appended_skill_selection=selection,
                    remaining_possible_skill_selection_nodes=remaining_nodes,
                ),
                selection,
            )
        except ValueError as error:
            if not any(
                marker in str(error)
                for marker in (
                    "future skill selection reserve",
                    "reasoning trace exceeds 32 KiB",
                )
            ):
                raise
        fallback = PromptSkillSelectionTraceV1(
            node=selection.node,
            plan_generation=selection.plan_generation,
            status="baseline_fallback",
            fallback_code="selected_skill_trace_exceeded",
        )
        return (
            _next_reasoning_trace(
                trace,
                status=trace.status,
                plans=plans,
                evaluations=evaluations,
                corrections=corrections,
                appended_skill_selection=fallback,
                remaining_possible_skill_selection_nodes=remaining_nodes,
            ),
            fallback,
        )

    @staticmethod
    def _remaining_selector_nodes(
        snapshot: ExecutionSnapshotV1,
        trace: ReasoningTraceV4,
    ) -> int:
        return max(
            0,
            snapshot.policy.max_reasoning_revision_cycles
            + 1
            - len(trace.skill_selections),
        )

    def run(self, execution_id: str) -> None:
        snapshot = self._runtime.snapshot(execution_id)
        if snapshot.state is not ExecutionState.CONTEXT_READY:
            raise ValueError("orchestrator only starts a fresh context_ready execution")
        if snapshot.grant_ref is None or snapshot.catalog_ref is None or snapshot.context_pack_ref is None:
            raise ValueError("accepted execution refs are incomplete")

        observations: list[KnowledgeToolObservationV1] = []
        contract_repair_remaining = _contract_repair_remaining(snapshot)
        document_candidate_handles: set[str] = set()
        evidence_by_handle: dict[str, RetrievalEvidenceLineageV1] = {}
        visual_images_by_handle: dict[str, VisualImagePayloadV1] = {}
        completed_actions: set[str] = set()
        audit_steps: list[TurnAuditStepV1] = []
        used_retrieval = False
        retrieval_error_status: RetrievalStatusV1 | None = None
        session = None
        contract_repair_request = False
        failure_code = "contract_violation"
        reasoning_replanner_failed = False
        step_ordinal = 0
        reasoning_trace: ReasoningTraceV4 | None = None
        reasoning_plan: ReasoningPlanV2 | None = None
        reasoning_plans: list[ReasoningPlanV2] = []
        reasoning_evaluations: list[ReasoningEvaluationV1] = []
        reasoning_corrections: list[ReasoningCorrectionV2] = []
        provisional_evidence_checks: list[ProvisionalEvidenceCheckV1] = []
        provisional_assessment_cache: dict[
            tuple[str, str, tuple[str, ...], str],
            ProvisionalEvidenceAssessmentV1,
        ] = {}
        pending_correction: tuple[
            Literal["revise_only", "research_then_revise"], int, list[str], str, int | None, int
        ] | None = None
        pending_limit_finalization: tuple[int, str] | None = None
        schema_repair_origin: SchemaRetryOriginCode | None = None
        force_finalize_only = False
        terminal_provisional_assessment: ProvisionalEvidenceAssessmentV1 | None = None
        try:
            initial_input = self._model_inputs.build(
                snapshot,
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            )
            _validate_model_input(snapshot, initial_input)
            if snapshot.reasoning_mode == "deep":
                if self._reasoning_model is None:
                    raise ValueError("deep execution has no reasoning model")
                prompt_skill_catalog_ref, prompt_skill_catalog = (
                    self._load_prompt_skill_catalog(snapshot, "planner")
                )
                reasoning_trace = _next_reasoning_trace(
                    None,
                    status="planning",
                    plans=[],
                    evaluations=[],
                    corrections=[],
                    prompt_skill_catalog=prompt_skill_catalog_ref,
                    remaining_possible_skill_selection_nodes=(
                        snapshot.policy.max_reasoning_revision_cycles + 1
                    ),
                )
                snapshot = self._record_reasoning_progress(
                    snapshot,
                    reasoning_trace,
                    phase="understanding",
                    progress_status="completed",
                    message_code="reasoning.understanding_completed",
                )
                planner_context_input = self._model_inputs.build(
                    snapshot,
                    observations=observations,
                    contract_repair_remaining=contract_repair_remaining,
                )
                _validate_model_input(snapshot, planner_context_input)
                planner_node_context = (
                    self._reasoning_model.build_initial_planning_node_context(
                        planner_context_input
                    )
                )
                (
                    snapshot,
                    initial_selection,
                    initial_selected_skills,
                    selector_input_tokens,
                    selector_output_tokens,
                ) = self._select_prompt_skills(
                    snapshot,
                    observations=observations,
                    contract_repair_remaining=contract_repair_remaining,
                    node_context=planner_node_context,
                    catalog=prompt_skill_catalog,
                    plan_generation=1,
                )
                if (
                    prompt_skill_catalog is not None
                    and prompt_skill_catalog.skills
                    and initial_selection.fallback_code
                    != "selector_unavailable"
                ):
                    step_ordinal += 1
                    audit_steps.append(
                        TurnAuditStepV1(
                            ordinal=step_ordinal,
                            step_kind="model",
                            operation="deep_initial_planner_skill_selection",
                            status="completed",
                            safe_input_digest=_digest(
                                {"node": "deep_initial_planner", "plan_generation": 1}
                            ),
                            input_tokens=selector_input_tokens,
                            output_tokens=selector_output_tokens,
                        )
                    )
                if initial_selected_skills:
                    selected_preflight_input = self._model_inputs.build(
                        snapshot,
                        observations=observations,
                        contract_repair_remaining=contract_repair_remaining,
                    )
                    _validate_model_input(snapshot, selected_preflight_input)
                    selected_context_exceeded = False
                    try:
                        selected_context_tokens = max(
                            self._reasoning_model.estimate_plan_request_tokens(
                                selected_preflight_input,
                                node_context=planner_node_context,
                                selected_skills=initial_selected_skills,
                                repair=repair_variant,
                            )
                            for repair_variant in (False, True)
                        )
                    except Exception as error:
                        if getattr(error, "safe_code", None) != "context_limit_exceeded":
                            raise
                        selected_context_exceeded = True
                    else:
                        selected_context_exceeded = selected_context_tokens > (
                            snapshot.policy.context_token_budget
                            - snapshot.budget.context_tokens
                        )
                    if selected_context_exceeded:
                        initial_selection = PromptSkillSelectionTraceV1(
                            node="deep_initial_planner",
                            plan_generation=1,
                            status="baseline_fallback",
                            fallback_code="selected_skill_context_exceeded",
                        )
                        initial_selected_skills = ()
                reasoning_trace, persisted_initial_selection = (
                    self._append_skill_selection(
                        snapshot,
                        reasoning_trace,
                        initial_selection,
                        plans=reasoning_plans,
                        evaluations=reasoning_evaluations,
                        corrections=reasoning_corrections,
                    )
                )
                if persisted_initial_selection.status != "selected":
                    initial_selected_skills = ()
                snapshot = self._record_reasoning_progress(
                    snapshot,
                    reasoning_trace,
                    phase="planning",
                    progress_status="started",
                    message_code="reasoning.planning_started",
                )
                schema_retry_ordinal = 0
                while True:
                    repair = schema_retry_ordinal > 0
                    planner_input = self._model_inputs.build(
                        snapshot,
                        observations=observations,
                        contract_repair_remaining=contract_repair_remaining,
                    )
                    _validate_model_input(snapshot, planner_input)
                    context_tokens = self._reasoning_model.estimate_plan_request_tokens(
                        planner_input,
                        node_context=planner_node_context,
                        selected_skills=initial_selected_skills,
                        repair=repair,
                    )
                    snapshot = self._runtime.request_model_action(
                        RequestModelActionV1(
                            execution_id=execution_id,
                            expected_version=snapshot.version,
                            fencing_token=snapshot.lease.fencing_token,
                            contract_repair=False,
                            context_tokens=context_tokens,
                        )
                    )
                    contract_repair_remaining = _contract_repair_remaining(snapshot)
                    planner_input = self._model_inputs.build(
                        snapshot,
                        observations=observations,
                        contract_repair_remaining=contract_repair_remaining,
                    )
                    _validate_model_input(snapshot, planner_input)
                    try:
                        plan_kwargs = {
                            "node_context": planner_node_context,
                            "selected_skills": initial_selected_skills,
                            "repair": repair,
                        }
                        if schema_repair_origin is not None:
                            plan_kwargs.update(
                                {
                                    "schema_retry_ordinal": schema_retry_ordinal,
                                    "repair_origin_error_code": schema_repair_origin,
                                }
                            )
                        plan_result = self._reasoning_model.plan(
                            planner_input, **plan_kwargs
                        )
                    except Exception as error:
                        step_ordinal += 1
                        audit_steps.append(
                            TurnAuditStepV1(
                                ordinal=step_ordinal,
                                step_kind="model",
                                operation="deep_reasoning_plan_repair",
                                status="failed",
                                safe_input_digest=_digest(
                                    planner_input.model_dump(mode="json")
                                ),
                            )
                        )
                        claimed = self._claim_schema_retry(
                            snapshot,
                            purpose="deep_reasoning_plan",
                            semantic_ordinal=1,
                            error=error,
                        )
                        if claimed is None:
                            raise
                        snapshot, schema_repair_origin = claimed
                        schema_retry_ordinal = snapshot.budget.schema_retries
                        continue
                    reasoning_plan = plan_result.plan
                    step_ordinal += 1
                    audit_steps.append(
                        TurnAuditStepV1(
                            ordinal=step_ordinal,
                            step_kind="model",
                            operation="deep_reasoning_plan",
                            status="completed",
                            safe_input_digest=_digest(
                                planner_input.model_dump(mode="json")
                            ),
                            input_tokens=plan_result.input_tokens,
                            output_tokens=plan_result.output_tokens,
                        )
                    )
                    schema_repair_origin = None
                    break
                if reasoning_plan is None:
                    raise ValueError("deep reasoning planner did not produce a plan")
                reasoning_plans.append(reasoning_plan)
                reasoning_trace = _next_reasoning_trace(
                    reasoning_trace,
                    status="running",
                    plans=reasoning_plans,
                    evaluations=reasoning_evaluations,
                    corrections=reasoning_corrections,
                    remaining_possible_skill_selection_nodes=(
                        self._remaining_selector_nodes(snapshot, reasoning_trace)
                    ),
                )
                snapshot = self._record_reasoning_progress(
                    snapshot,
                    reasoning_trace,
                    phase="planning",
                    progress_status="completed",
                    message_code="reasoning.planning_completed",
                    message_params={"plan_items": len(reasoning_plan.items)},
                )
                initial_input = self._model_inputs.build(
                    snapshot,
                    observations=observations,
                    contract_repair_remaining=contract_repair_remaining,
                ).model_copy(update={"reasoning_plan": reasoning_plan})
                _validate_model_input(snapshot, initial_input)
            _answer_catalog_ref, answer_skill_catalog = (
                self._load_prompt_skill_catalog(snapshot, "answer")
            )
            session = self._model.open_session(initial_input)
            candidate_ordinal = 0
            candidate_needs_start = True
            next_candidate_kind: Literal["normal", "limit_final"] = "normal"
            next_candidate_correction_kind: Literal[
                "revise_only", "research_then_revise", "limit_final"
            ] | None = None
            next_candidate_evaluation: ReasoningEvaluationV1 | None = None
            next_candidate_gate_feedback = None

            while True:
                if self._clock() >= snapshot.deadline_at:
                    failure_code = "deadline_exceeded"
                    raise TimeoutError("turn deadline elapsed")
                if candidate_needs_start:
                    candidate_ordinal += 1
                    candidate_input = self._model_inputs.build(
                        snapshot,
                        observations=observations,
                        contract_repair_remaining=contract_repair_remaining,
                    )
                    if reasoning_plan is not None:
                        candidate_input = candidate_input.model_copy(
                            update={"reasoning_plan": reasoning_plan}
                        )
                    _validate_model_input(snapshot, candidate_input)
                    candidate_context = AnswerCandidateNodeContextV1(
                        candidate_ordinal=candidate_ordinal,
                        candidate_kind=next_candidate_kind,
                        current_user_request=candidate_input.model_user_input,
                        current_plan=reasoning_plan,
                        correction_kind=next_candidate_correction_kind,
                        triggering_evaluation=next_candidate_evaluation,
                        gate_correction_feedback=next_candidate_gate_feedback,
                    )
                    (
                        snapshot,
                        selector_input_tokens,
                        selector_output_tokens,
                    ) = self._select_and_begin_answer_candidate(
                        snapshot,
                        session=session,
                        observations=observations,
                        contract_repair_remaining=contract_repair_remaining,
                        node_context=candidate_context,
                        catalog=answer_skill_catalog,
                        reasoning_plan=reasoning_plan,
                    )
                    contract_repair_remaining = _contract_repair_remaining(snapshot)
                    persisted_answer_selection = snapshot.prompt_skill_selections[-1]
                    if (
                        answer_skill_catalog is not None
                        and answer_skill_catalog.skills
                        and persisted_answer_selection.fallback_code
                        != "selector_unavailable"
                    ):
                        step_ordinal += 1
                        audit_steps.append(
                            TurnAuditStepV1(
                                ordinal=step_ordinal,
                                step_kind="model",
                                operation="answer_candidate_skill_selection",
                                status="completed",
                                safe_input_digest=_digest(
                                    {
                                        "node": "answer_candidate",
                                        "candidate_ordinal": candidate_ordinal,
                                        "candidate_kind": next_candidate_kind,
                                    }
                                ),
                                input_tokens=selector_input_tokens,
                                output_tokens=selector_output_tokens,
                            )
                        )
                    candidate_needs_start = False
                finalize_only = force_finalize_only or not _has_legal_tool(
                    snapshot,
                    has_documents=bool(document_candidate_handles),
                    has_evidence=bool(evidence_by_handle),
                )
                context_tokens = _context_token_reservation(
                    self._model_inputs,
                    snapshot,
                    observations,
                    contract_repair_remaining,
                    lambda value, *, finalize_only: session.estimate_next_request_tokens(
                        value, finalize_only=finalize_only
                    ),
                    finalize_only=finalize_only,
                    reasoning_plan=reasoning_plan,
                )
                failure_code = "budget_exhausted"
                snapshot = self._runtime.request_model_action(
                    RequestModelActionV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        context_tokens=context_tokens,
                        contract_repair=contract_repair_request,
                    )
                )
                contract_repair_remaining = _contract_repair_remaining(snapshot)
                contract_repair_request = False
                model_input = self._model_inputs.build(
                    snapshot,
                    observations=observations,
                    contract_repair_remaining=contract_repair_remaining,
                )
                if reasoning_plan is not None:
                    model_input = model_input.model_copy(
                        update={"reasoning_plan": reasoning_plan}
                    )
                _validate_model_input(snapshot, model_input)
                if (
                    session.estimate_next_request_tokens(
                        model_input, finalize_only=finalize_only
                    )
                    > context_tokens
                ):
                    raise ValueError("post-CAS model input exceeded its context reservation")
                failure_code = "provider_failed"
                try:
                    if schema_repair_origin is None:
                        model_result = session.next_action(
                            model_input, finalize_only=finalize_only
                        )
                    else:
                        model_result = session.next_action(
                            model_input,
                            finalize_only=finalize_only,
                            repair_origin_error_code=schema_repair_origin,
                        )
                except Exception as error:
                    claimed = self._claim_schema_retry(
                        snapshot,
                        purpose="turn_execution",
                        semantic_ordinal=step_ordinal + 1,
                        error=error,
                    )
                    if claimed is None:
                        raise
                    snapshot, schema_repair_origin = claimed
                    step_ordinal += 1
                    audit_steps.append(
                        TurnAuditStepV1(
                            ordinal=step_ordinal,
                            step_kind="model",
                            operation="turn_execution_schema_repair",
                            status="failed",
                            safe_input_digest=_digest(
                                model_input.model_dump(mode="json")
                            ),
                        )
                    )
                    continue
                schema_repair_origin = None
                step_ordinal += 1
                if isinstance(model_result, ModelContractViolationV1):
                    logger.warning(
                        "turn model capability rejected execution_id=%s safe_code=%s repair_remaining=%s",
                        execution_id,
                        model_result.safe_code,
                        contract_repair_remaining,
                    )
                    audit_steps.append(
                        _capability_rejection_audit_step(
                            ordinal=step_ordinal,
                            safe_input_digest=_digest(
                                model_input.model_dump(mode="json")
                            ),
                            violation=model_result,
                        )
                    )
                    failure_code = "contract_violation"
                    if contract_repair_remaining == 0:
                        raise ValueError("provider repeated an invalid capability selection")
                    session.accept_contract_repair(model_result)
                    contract_repair_request = True
                    continue

                audit_steps.append(
                    TurnAuditStepV1(
                        ordinal=step_ordinal,
                        step_kind="model",
                        operation=model_result.action.action,
                        status="completed",
                        safe_input_digest=_digest(model_input.model_dump(mode="json")),
                        input_tokens=model_result.input_tokens,
                        output_tokens=model_result.output_tokens,
                    )
                )

                action = model_result.action
                if isinstance(action, FinalizeAnswerV1):
                    if snapshot.reasoning_mode == "deep":
                        assert reasoning_trace is not None
                        assert reasoning_plan is not None
                        assert self._reasoning_model is not None
                        if (
                            pending_correction is not None
                            and pending_correction[0] == "research_then_revise"
                            and snapshot.budget.tool_invocations < pending_correction[5]
                        ):
                            failure_code = "contract_violation"
                            raise DeepReasoningContractError(
                                "deep_reasoning_research_tool_required"
                            )
                        assessment_ordinal = len(provisional_evidence_checks) + 1
                        snapshot, terminal_provisional_assessment = (
                            self._assess_provisional_evidence(
                                snapshot=snapshot,
                                proposal=action,
                                visual_images_by_handle=visual_images_by_handle,
                                assessment_ordinal=assessment_ordinal,
                                assessment_cache=provisional_assessment_cache,
                            )
                        )
                        is_limit_final = pending_limit_finalization is not None
                        provisional_evidence_checks.append(
                            _provisional_evidence_check(
                                ordinal=assessment_ordinal,
                                assessment=terminal_provisional_assessment,
                                is_limit_final=is_limit_final,
                                evaluation_count=len(reasoning_evaluations),
                            )
                        )
                        step_ordinal += 1
                        audit_steps.append(
                            _provisional_gate_audit_step(
                                ordinal=step_ordinal,
                                assessment=terminal_provisional_assessment,
                                evidence_count=len(action.claimed_evidence_handles),
                            )
                        )
                        terminal_trace_status: Literal["completed", "degraded"]
                        termination_reason: ReasoningTerminationReason
                        if pending_limit_finalization is not None:
                            trigger_cycle, limit_summary = pending_limit_finalization
                            limit_finalization = ReasoningLimitFinalizationV2(
                                triggering_evaluation=trigger_cycle,
                                summary=limit_summary,
                            )
                            pending_limit_finalization = None
                            terminal_trace_status = "degraded"
                            termination_reason = "correction_limit_reached"
                            reasoning_trace = _next_reasoning_trace(
                                reasoning_trace,
                                status="degraded",
                                plans=reasoning_plans,
                                evaluations=reasoning_evaluations,
                                corrections=reasoning_corrections,
                                provisional_evidence_checks=provisional_evidence_checks,
                                limit_finalization=limit_finalization,
                                termination_reason=termination_reason,
                                remaining_possible_skill_selection_nodes=0,
                            )
                            snapshot = self._record_reasoning_progress(
                                snapshot,
                                reasoning_trace,
                                phase="revising",
                                progress_status="degraded",
                                cycle=trigger_cycle,
                                message_code="reasoning.correction_limit_reached",
                                message_params={"cycle": trigger_cycle},
                            )
                        else:
                            reasoning_trace = _next_reasoning_trace(
                                reasoning_trace,
                                status="running",
                                plans=reasoning_plans,
                                evaluations=reasoning_evaluations,
                                corrections=reasoning_corrections,
                                provisional_evidence_checks=provisional_evidence_checks,
                                remaining_possible_skill_selection_nodes=(
                                    self._remaining_selector_nodes(snapshot, reasoning_trace)
                                ),
                            )
                            snapshot = self._record_reasoning_progress(
                                snapshot,
                                reasoning_trace,
                                phase="drafting",
                                progress_status="completed",
                                message_code="reasoning.drafting_completed",
                                message_params={
                                    "candidate_segments": len(action.segments)
                                },
                            )
                            evaluation_cycle = len(reasoning_evaluations) + 1
                            try:
                                evaluation_schema_origin: SchemaRetryOriginCode | None = None
                                evaluation_schema_ordinal = 0
                                while True:
                                    evaluation_input = self._model_inputs.build(
                                        snapshot,
                                        observations=observations,
                                        contract_repair_remaining=contract_repair_remaining,
                                    ).model_copy(update={"reasoning_plan": reasoning_plan})
                                    _validate_model_input(snapshot, evaluation_input)
                                    evaluation_tokens = (
                                        self._reasoning_model.estimate_evaluation_request_tokens(
                                            evaluation_input,
                                            plan=reasoning_plan,
                                            proposal=action,
                                            observations=observations,
                                            cycle=evaluation_cycle,
                                        )
                                    )
                                    snapshot = self._runtime.request_model_action(
                                        RequestModelActionV1(
                                            execution_id=execution_id,
                                            expected_version=snapshot.version,
                                            fencing_token=snapshot.lease.fencing_token,
                                            context_tokens=evaluation_tokens,
                                        )
                                    )
                                    evaluation_input = self._model_inputs.build(
                                        snapshot,
                                        observations=observations,
                                        contract_repair_remaining=contract_repair_remaining,
                                    ).model_copy(update={"reasoning_plan": reasoning_plan})
                                    _validate_model_input(snapshot, evaluation_input)
                                    try:
                                        if evaluation_schema_origin is None:
                                            evaluation_result = self._reasoning_model.evaluate(
                                                evaluation_input,
                                                plan=reasoning_plan,
                                                proposal=action,
                                                observations=observations,
                                                cycle=evaluation_cycle,
                                            )
                                        else:
                                            evaluation_result = self._reasoning_model.evaluate(
                                                evaluation_input,
                                                plan=reasoning_plan,
                                                proposal=action,
                                                observations=observations,
                                                cycle=evaluation_cycle,
                                                schema_retry_ordinal=evaluation_schema_ordinal,
                                                repair_origin_error_code=evaluation_schema_origin,
                                            )
                                    except Exception as error:
                                        claimed = self._claim_schema_retry(
                                            snapshot,
                                            purpose="deep_reasoning_evaluation",
                                            semantic_ordinal=evaluation_cycle,
                                            error=error,
                                        )
                                        if claimed is None:
                                            raise
                                        snapshot, evaluation_schema_origin = claimed
                                        evaluation_schema_ordinal = snapshot.budget.schema_retries
                                        continue
                                    break
                                evaluation = evaluation_result.evaluation
                                step_ordinal += 1
                                audit_steps.append(
                                    TurnAuditStepV1(
                                        ordinal=step_ordinal,
                                        step_kind="model",
                                        operation="deep_reasoning_evaluation",
                                        status="completed",
                                        safe_input_digest=_digest(
                                            evaluation_input.model_dump(mode="json")
                                        ),
                                        input_tokens=evaluation_result.input_tokens,
                                        output_tokens=evaluation_result.output_tokens,
                                    )
                                )
                            except Exception as error:
                                safe_code = getattr(error, "safe_code", "")
                                unavailable_reason = (
                                    "deadline_exceeded"
                                    if self._clock() >= snapshot.deadline_at
                                    else "budget_exhausted"
                                    if "budget" in safe_code
                                    or "budget" in type(error).__name__.lower()
                                    else "provider_unavailable"
                                )
                                evaluation = ReasoningEvaluationV1(
                                    cycle=evaluation_cycle,
                                    verdict="unavailable",
                                    unavailable_reason=unavailable_reason,
                                )
                                step_ordinal += 1
                                audit_steps.append(
                                    TurnAuditStepV1(
                                        ordinal=step_ordinal,
                                        step_kind="model",
                                        operation="deep_reasoning_evaluation",
                                        status="failed",
                                        safe_input_digest=_digest(
                                            {
                                                "execution_id": execution_id,
                                                "cycle": evaluation_cycle,
                                            }
                                        ),
                                    )
                                )
                            reasoning_evaluations.append(evaluation)
                            gate_feedback = _gate_correction_feedback(
                                terminal_provisional_assessment
                            )
                            correction_kind = _merged_correction_kind(
                                evaluation=evaluation,
                                gate_feedback=gate_feedback,
                            )
                            disposition = (
                                "revised"
                                if correction_kind is not None
                                else "degraded"
                                if evaluation.verdict == "unavailable"
                                or terminal_provisional_assessment.consistency
                                == "unavailable"
                                else "accepted"
                            )
                            provisional_evidence_checks[-1] = (
                                provisional_evidence_checks[-1].model_copy(
                                    update={"candidate_disposition": disposition}
                                )
                            )
                            if pending_correction is not None:
                                reasoning_corrections.append(
                                    _completed_correction(
                                        cycle=len(reasoning_corrections) + 1,
                                        pending=pending_correction,
                                        tool_invocation_end=(
                                            snapshot.budget.tool_invocations
                                        ),
                                        result_evaluation=evaluation_cycle,
                                    )
                                )
                                pending_correction = None
                            reasoning_trace = _next_reasoning_trace(
                                reasoning_trace,
                                status=(
                                    "degraded"
                                    if evaluation.verdict == "unavailable"
                                    and correction_kind is None
                                    else "running"
                                ),
                                plans=reasoning_plans,
                                evaluations=reasoning_evaluations,
                                corrections=reasoning_corrections,
                                provisional_evidence_checks=provisional_evidence_checks,
                                termination_reason=(
                                    "evaluator_unavailable"
                                    if evaluation.verdict == "unavailable"
                                    and correction_kind is None
                                    else None
                                ),
                                remaining_possible_skill_selection_nodes=(
                                    self._remaining_selector_nodes(snapshot, reasoning_trace)
                                ),
                            )
                            snapshot = self._record_reasoning_progress(
                                snapshot,
                                reasoning_trace,
                                phase="evaluating",
                                progress_status=(
                                    "degraded"
                                    if evaluation.verdict == "unavailable"
                                    else "completed"
                                ),
                                cycle=evaluation_cycle,
                                message_code=(
                                    "reasoning.evaluator_unavailable"
                                    if evaluation.verdict == "unavailable"
                                    else "reasoning.evaluation_completed"
                                ),
                                message_params={"cycle": evaluation_cycle},
                            )
                            if correction_kind is None:
                                if evaluation.verdict == "unavailable":
                                    terminal_trace_status = "degraded"
                                    termination_reason = "evaluator_unavailable"
                                elif (
                                    terminal_provisional_assessment.consistency
                                    == "unavailable"
                                ):
                                    terminal_trace_status = "degraded"
                                    termination_reason = (
                                        "provisional_evidence_unavailable"
                                    )
                                else:
                                    terminal_trace_status = "completed"
                                    termination_reason = "completed"
                            elif (
                                len(reasoning_corrections)
                                < snapshot.policy.max_reasoning_revision_cycles
                            ):
                                replacement_plan: ReasoningPlanV2 | None = None
                                if correction_kind == "research_then_revise":
                                    failure_code = "contract_violation"
                                    reasoning_replanner_failed = True
                                    replan_context_input = self._model_inputs.build(
                                        snapshot,
                                        observations=observations,
                                        contract_repair_remaining=contract_repair_remaining,
                                    ).model_copy(update={"reasoning_plan": reasoning_plan})
                                    _validate_model_input(snapshot, replan_context_input)
                                    replan_node_context = (
                                        self._reasoning_model.build_replanning_node_context(
                                            replan_context_input,
                                            plan=reasoning_plan,
                                            evaluation=evaluation,
                                            remaining_execution_limits=(
                                                self._post_selector_remaining_limits(
                                                    snapshot,
                                                    completed_reasoning_corrections=len(
                                                        reasoning_corrections
                                                    ),
                                                )
                                            ),
                                        )
                                    )
                                    target_generation = reasoning_plan.generation + 1
                                    (
                                        snapshot,
                                        replan_selection,
                                        replan_selected_skills,
                                        selector_input_tokens,
                                        selector_output_tokens,
                                    ) = self._select_prompt_skills(
                                        snapshot,
                                        observations=observations,
                                        contract_repair_remaining=contract_repair_remaining,
                                        node_context=replan_node_context,
                                        catalog=prompt_skill_catalog,
                                        plan_generation=target_generation,
                                    )
                                    if (
                                        prompt_skill_catalog is not None
                                        and prompt_skill_catalog.skills
                                        and replan_selection.fallback_code
                                        != "selector_unavailable"
                                    ):
                                        step_ordinal += 1
                                        audit_steps.append(
                                            TurnAuditStepV1(
                                                ordinal=step_ordinal,
                                                step_kind="model",
                                                operation="deep_replanner_skill_selection",
                                                status="completed",
                                                safe_input_digest=_digest(
                                                    {
                                                        "node": "deep_replanner",
                                                        "plan_generation": target_generation,
                                                    }
                                                ),
                                                input_tokens=selector_input_tokens,
                                                output_tokens=selector_output_tokens,
                                            )
                                        )
                                    if replan_selected_skills:
                                        selected_preflight_input = self._model_inputs.build(
                                            snapshot,
                                            observations=observations,
                                            contract_repair_remaining=contract_repair_remaining,
                                        ).model_copy(update={"reasoning_plan": reasoning_plan})
                                        _validate_model_input(
                                            snapshot, selected_preflight_input
                                        )
                                        selected_context_exceeded = False
                                        try:
                                            selected_context_tokens = max(
                                                self._reasoning_model.estimate_replan_request_tokens(
                                                    selected_preflight_input,
                                                    node_context=replan_node_context,
                                                    selected_skills=replan_selected_skills,
                                                    plan=reasoning_plan,
                                                    evaluation=evaluation,
                                                    repair=repair_variant,
                                                )
                                                for repair_variant in (False, True)
                                            )
                                        except Exception as error:
                                            if (
                                                getattr(error, "safe_code", None)
                                                != "context_limit_exceeded"
                                            ):
                                                raise
                                            selected_context_exceeded = True
                                        else:
                                            selected_context_exceeded = (
                                                selected_context_tokens
                                                > snapshot.policy.context_token_budget
                                                - snapshot.budget.context_tokens
                                            )
                                        if selected_context_exceeded:
                                            replan_selection = PromptSkillSelectionTraceV1(
                                                node="deep_replanner",
                                                plan_generation=target_generation,
                                                status="baseline_fallback",
                                                fallback_code="selected_skill_context_exceeded",
                                            )
                                            replan_selected_skills = ()
                                    (
                                        reasoning_trace,
                                        persisted_replan_selection,
                                    ) = self._append_skill_selection(
                                        snapshot,
                                        reasoning_trace,
                                        replan_selection,
                                        plans=reasoning_plans,
                                        evaluations=reasoning_evaluations,
                                        corrections=reasoning_corrections,
                                    )
                                    if persisted_replan_selection.status != "selected":
                                        replan_selected_skills = ()
                                    snapshot = self._record_reasoning_progress(
                                        snapshot,
                                        reasoning_trace,
                                        phase="revising",
                                        progress_status="started",
                                        cycle=evaluation_cycle,
                                        message_code="reasoning.replanning_started",
                                    )
                                    replan_schema_origin: SchemaRetryOriginCode | None = None
                                    replan_schema_ordinal = 0
                                    while True:
                                        repair = replan_schema_ordinal > 0
                                        replan_input = self._model_inputs.build(
                                            snapshot,
                                            observations=observations,
                                            contract_repair_remaining=contract_repair_remaining,
                                        ).model_copy(update={"reasoning_plan": reasoning_plan})
                                        _validate_model_input(snapshot, replan_input)
                                        replan_tokens = (
                                            self._reasoning_model.estimate_replan_request_tokens(
                                                replan_input,
                                                node_context=replan_node_context,
                                                selected_skills=replan_selected_skills,
                                                plan=reasoning_plan,
                                                evaluation=evaluation,
                                                repair=repair,
                                            )
                                        )
                                        snapshot = self._runtime.request_model_action(
                                            RequestModelActionV1(
                                                execution_id=execution_id,
                                                expected_version=snapshot.version,
                                                fencing_token=snapshot.lease.fencing_token,
                                                context_tokens=replan_tokens,
                                                contract_repair=False,
                                            )
                                        )
                                        contract_repair_remaining = (
                                            _contract_repair_remaining(snapshot)
                                        )
                                        replan_input = self._model_inputs.build(
                                            snapshot,
                                            observations=observations,
                                            contract_repair_remaining=contract_repair_remaining,
                                        ).model_copy(update={"reasoning_plan": reasoning_plan})
                                        _validate_model_input(snapshot, replan_input)
                                        try:
                                            replan_kwargs = {
                                                "node_context": replan_node_context,
                                                "selected_skills": replan_selected_skills,
                                                "plan": reasoning_plan,
                                                "evaluation": evaluation,
                                                "repair": repair,
                                            }
                                            if replan_schema_origin is not None:
                                                replan_kwargs.update(
                                                    {
                                                        "schema_retry_ordinal": replan_schema_ordinal,
                                                        "repair_origin_error_code": replan_schema_origin,
                                                    }
                                                )
                                            replan_result = self._reasoning_model.replan(
                                                replan_input, **replan_kwargs
                                            )
                                        except Exception as error:
                                            step_ordinal += 1
                                            audit_steps.append(
                                                TurnAuditStepV1(
                                                    ordinal=step_ordinal,
                                                    step_kind="model",
                                                    operation="deep_reasoning_replan_repair",
                                                    status="failed",
                                                    safe_input_digest=_digest(
                                                        replan_input.model_dump(mode="json")
                                                    ),
                                                )
                                            )
                                            claimed = self._claim_schema_retry(
                                                snapshot,
                                                purpose="deep_reasoning_replan",
                                                semantic_ordinal=reasoning_plan.generation,
                                                error=error,
                                            )
                                            if claimed is None:
                                                raise
                                            snapshot, replan_schema_origin = claimed
                                            replan_schema_ordinal = snapshot.budget.schema_retries
                                            continue
                                        replacement_plan = replan_result.plan
                                        step_ordinal += 1
                                        audit_steps.append(
                                            TurnAuditStepV1(
                                                ordinal=step_ordinal,
                                                step_kind="model",
                                                operation="deep_reasoning_replan",
                                                status="completed",
                                                safe_input_digest=_digest(
                                                    replan_input.model_dump(mode="json")
                                                ),
                                                input_tokens=replan_result.input_tokens,
                                                output_tokens=replan_result.output_tokens,
                                            )
                                        )
                                        break
                                    if replacement_plan is None:
                                        raise DeepReasoningContractError(
                                            "deep_reasoning_replan_invalid"
                                        )
                                    reasoning_replanner_failed = False
                                    reasoning_plan = replacement_plan
                                    reasoning_plans.append(replacement_plan)
                                    reasoning_trace = _next_reasoning_trace(
                                        reasoning_trace,
                                        status="running",
                                        plans=reasoning_plans,
                                        evaluations=reasoning_evaluations,
                                        corrections=reasoning_corrections,
                                        remaining_possible_skill_selection_nodes=(
                                            self._remaining_selector_nodes(
                                                snapshot, reasoning_trace
                                            )
                                        ),
                                    )
                                    snapshot = self._record_reasoning_progress(
                                        snapshot,
                                        reasoning_trace,
                                        phase="planning",
                                        progress_status="completed",
                                        cycle=evaluation_cycle,
                                        message_code="reasoning.replanning_completed",
                                        message_params={
                                            "generation": replacement_plan.generation,
                                            "cycle": evaluation_cycle,
                                        },
                                    )
                                session.accept_reasoning_feedback(
                                    evaluation,
                                    correction_kind=correction_kind,
                                    gate_feedback=gate_feedback,
                                    plan=replacement_plan,
                                )
                                pending_correction = _pending_correction(
                                    correction_kind=correction_kind,
                                    evaluation=evaluation,
                                    plan_generation=(
                                        None
                                        if replacement_plan is None
                                        else replacement_plan.generation
                                    ),
                                    next_tool_invocation=(
                                        snapshot.budget.tool_invocations + 1
                                    ),
                                )
                                reasoning_trace = _next_reasoning_trace(
                                    reasoning_trace,
                                    status="running",
                                    plans=reasoning_plans,
                                    evaluations=reasoning_evaluations,
                                    corrections=reasoning_corrections,
                                    remaining_possible_skill_selection_nodes=(
                                        self._remaining_selector_nodes(
                                            snapshot, reasoning_trace
                                        )
                                    ),
                                )
                                snapshot = self._record_reasoning_progress(
                                    snapshot,
                                    reasoning_trace,
                                    phase="revising",
                                    progress_status="started",
                                    cycle=evaluation_cycle,
                                    message_code="reasoning.correction_requested",
                                    message_params={
                                        "cycle": evaluation_cycle,
                                        "research": correction_kind
                                        == "research_then_revise",
                                    },
                                )
                                next_candidate_kind = "normal"
                                next_candidate_correction_kind = correction_kind
                                next_candidate_evaluation = evaluation
                                next_candidate_gate_feedback = gate_feedback
                                candidate_needs_start = True
                                force_finalize_only = correction_kind == "revise_only"
                                continue
                            else:
                                session.accept_reasoning_limit(
                                    evaluation,
                                    gate_feedback=gate_feedback,
                                )
                                pending_limit_finalization = (
                                    _limit_finalization_pending(evaluation)
                                )
                                reasoning_trace = _next_reasoning_trace(
                                    reasoning_trace,
                                    status="running",
                                    plans=reasoning_plans,
                                    evaluations=reasoning_evaluations,
                                    corrections=reasoning_corrections,
                                    remaining_possible_skill_selection_nodes=(
                                        self._remaining_selector_nodes(
                                            snapshot, reasoning_trace
                                        )
                                    ),
                                )
                                snapshot = self._record_reasoning_progress(
                                    snapshot,
                                    reasoning_trace,
                                    phase="revising",
                                    progress_status="started",
                                    cycle=evaluation_cycle,
                                    message_code="reasoning.limit_finalization_started",
                                    message_params={"cycle": evaluation_cycle},
                                )
                                next_candidate_kind = "limit_final"
                                next_candidate_correction_kind = "limit_final"
                                next_candidate_evaluation = evaluation
                                next_candidate_gate_feedback = gate_feedback
                                candidate_needs_start = True
                                force_finalize_only = True
                                continue
                        reasoning_trace = _next_reasoning_trace(
                            reasoning_trace,
                            status=terminal_trace_status,
                            plans=reasoning_plans,
                            evaluations=reasoning_evaluations,
                            corrections=reasoning_corrections,
                            limit_finalization=(
                                limit_finalization
                                if termination_reason == "correction_limit_reached"
                                else None
                            ),
                            termination_reason=termination_reason,
                            remaining_possible_skill_selection_nodes=0,
                        )
                        snapshot = self._record_reasoning_progress(
                            snapshot,
                            reasoning_trace,
                            phase="governing",
                            progress_status=(
                                "degraded"
                                if terminal_trace_status == "degraded"
                                else "started"
                            ),
                            message_code="reasoning.governance_started",
                            message_params={
                                "evaluations": len(reasoning_evaluations),
                                "corrections": len(reasoning_corrections),
                            },
                        )
                    failure_code = "terminal_materialization_failed"
                    self._materialize_terminal(
                        snapshot=snapshot,
                        proposal=action,
                        evidence_by_handle=evidence_by_handle,
                        visual_images_by_handle=visual_images_by_handle,
                        audit_steps=audit_steps,
                        used_retrieval=used_retrieval,
                        retrieval_error_status=retrieval_error_status,
                        finalize_only=finalize_only,
                        provisional_assessment=terminal_provisional_assessment,
                        delivery_constraint=(
                            "correction_limit_reached"
                            if snapshot.reasoning_mode == "deep"
                            and reasoning_trace is not None
                            and reasoning_trace.termination_reason
                            == "correction_limit_reached"
                            else "none"
                        ),
                    )
                    return

                used_retrieval = True
                projection = _tool_reservation_projection(
                    execution_id=execution_id,
                    snapshot=snapshot,
                    action=action,
                    completed_action_digests=completed_actions,
                )
                arguments = projection.arguments
                action_digest = projection.action_digest
                invocation_ordinal = projection.invocation_ordinal
                invocation_id = projection.invocation_id
                tokens = projection.max_output_tokens
                failure_code = "budget_exhausted"
                snapshot = self._runtime.begin_tool(projection.command)
                failure_code = "tool_failed"
                tool_started_at = self._clock()
                if tool_started_at >= snapshot.deadline_at:
                    failure_code = "deadline_exceeded"
                    raise TimeoutError("turn deadline elapsed before retrieval")
                tool_deadline_at = min(
                    tool_started_at
                    + timedelta(seconds=snapshot.policy.tool_execution_timeout_seconds),
                    snapshot.deadline_at,
                )
                envelope = self._retrieval.invoke(
                    execution_id=execution_id,
                    grant_ref=snapshot.grant_ref,
                    catalog_ref=snapshot.catalog_ref,
                    invocation_ordinal=invocation_ordinal,
                    action=action,
                    max_output_tokens=tokens,
                    tokenizer_profile=snapshot.route.tokenizer_profile,
                    deadline_at=tool_deadline_at,
                )
                if self._clock() >= snapshot.deadline_at:
                    failure_code = "deadline_exceeded"
                    raise TimeoutError("turn deadline elapsed during retrieval")
                if self._clock() >= tool_deadline_at and (
                    envelope.observation.result_type != "knowledge_tool_error"
                    or envelope.observation.message_code != "retrieval_tool_timeout"
                ):
                    raise ValueError("retrieval returned a late normal result")
                document_candidate_handles.update(
                    envelope.document_candidate_handles
                )
                for item in envelope.evidence_lineage:
                    current = evidence_by_handle.get(item.evidence_handle)
                    if current is not None and current != item:
                        raise ValueError("retrieval evidence lineage changed within execution")
                    evidence_by_handle[item.evidence_handle] = item
                failure_code = "budget_exhausted"
                snapshot = self._runtime.complete_tool(
                    _complete_tool_command(
                        execution_id=execution_id,
                        snapshot=snapshot,
                        action=action,
                        invocation_id=invocation_id,
                        invocation_ordinal=invocation_ordinal,
                        envelope=envelope,
                    )
                )
                completed_actions.add(action_digest)
                if envelope.observation.result_type == "knowledge_tool_error":
                    retrieval_error_status = _retrieval_error_status(envelope)
                step_ordinal += 1
                audit_steps.append(
                    _tool_audit_step(
                        ordinal=step_ordinal,
                        action=action,
                        arguments=arguments,
                        envelope=envelope,
                    )
                )
                observations.append(envelope.observation)
                if envelope.visual_image is None:
                    session.accept_tool_observation(envelope.observation)
                else:
                    current_image = visual_images_by_handle.get(
                        envelope.visual_image.visual_handle
                    )
                    if (
                        current_image is not None
                        and current_image != envelope.visual_image
                    ):
                        raise ValueError(
                            "model-visible visual carrier changed within execution"
                        )
                    visual_images_by_handle[
                        envelope.visual_image.visual_handle
                    ] = envelope.visual_image
                    session.accept_tool_observation(
                        envelope.observation,
                        visual_image=envelope.visual_image,
                    )
        except Exception as error:
            if snapshot.reasoning_mode == "deep" and reasoning_trace is not None:
                try:
                    current = self._runtime.snapshot(snapshot.execution_id)
                    if current.state not in TERMINAL_STATES:
                        reasoning_trace = _next_reasoning_trace(
                            reasoning_trace,
                            status="failed",
                            plans=reasoning_plans,
                            evaluations=reasoning_evaluations,
                            corrections=reasoning_corrections,
                            termination_reason=(
                                "planner_failed"
                                if reasoning_plan is None
                                else "replanner_failed"
                                if reasoning_replanner_failed
                                else "execution_failed"
                            ),
                            remaining_possible_skill_selection_nodes=0,
                        )
                        snapshot = self._record_reasoning_progress(
                            current,
                            reasoning_trace,
                            phase="failed",
                            progress_status="failed",
                            message_code="reasoning.execution_failed",
                        )
                except Exception:
                    logger.exception(
                        "failed to persist deep reasoning failure trace execution_id=%s",
                        snapshot.execution_id,
                    )
            if getattr(error, "safe_code", None) == "context_limit_exceeded":
                failure_code = "context_limit_exceeded"
            logger.error(
                "turn execution failed safely execution_id=%s failure_code=%s "
                "exception_type=%s exception_digest=%s",
                snapshot.execution_id,
                failure_code,
                f"{type(error).__module__}.{type(error).__qualname__}",
                _digest(
                    {
                        "exception_type": (
                            f"{type(error).__module__}.{type(error).__qualname__}"
                        ),
                        "message": str(error),
                    }
                ),
            )
            self._fail_active(snapshot, failure_code)
        finally:
            if session is not None:
                session.discard()

    def _materialize_terminal(
        self,
        *,
        snapshot: ExecutionSnapshotV1,
        proposal: FinalizeAnswerV1,
        evidence_by_handle: dict[str, RetrievalEvidenceLineageV1],
        visual_images_by_handle: dict[str, VisualImagePayloadV1],
        audit_steps: list[TurnAuditStepV1],
        used_retrieval: bool,
        retrieval_error_status: RetrievalStatusV1 | None,
        finalize_only: bool,
        provisional_assessment: ProvisionalEvidenceAssessmentV1 | None,
        delivery_constraint: Literal["none", "correction_limit_reached"],
    ) -> None:
        execution_id = snapshot.execution_id
        snapshot = self._runtime.begin_governance(
            BeginResultGovernanceV1(
                execution_id=execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                finalize_action_digest=_digest(proposal.model_dump(mode="json")),
            )
        )
        if snapshot.catalog_ref is None:
            raise ValueError("governing execution lost its catalog ref")
        evidence_pack = self._retrieval.materialize_evidence_pack(
            execution_id=execution_id,
            catalog_ref=snapshot.catalog_ref,
            evidence_handles=list(evidence_by_handle),
            idempotency_key=f"{execution_id}:terminal-evidence",
        )
        retrieval_status = _terminal_retrieval_status(
            evidence_pack=evidence_pack,
            retrieval_error_status=retrieval_error_status,
            finalize_only=finalize_only,
            used_retrieval=used_retrieval,
        )
        finalized_answer = _finalized_answer(proposal)
        declared_subset = self._retrieval.read_declared_evidence_subset(
            execution_id=execution_id,
            catalog_ref=snapshot.catalog_ref,
            handles=proposal.claimed_evidence_handles,
            visual_images=list(visual_images_by_handle.values()),
        )
        declared_lineage = _declared_lineage(declared_subset)
        assessment_state: Literal["completed", "unavailable", "not_attempted"]
        assessment_reason_code: AssessmentReasonCodeV2
        assessment_input_digest: str | None = None
        assessment_output_digest: str | None = None
        assessment_results = []
        answer_digest = _digest(finalized_answer.model_dump(mode="json"))
        visual_image_digests = [
            image.image_digest for image in declared_subset.visual_images
        ]
        assessment_consistency: Literal[
            "aligned", "conflict", "insufficient", "not_applicable", "unavailable"
        ]
        if provisional_assessment is not None:
            if (
                provisional_assessment.answer_digest != answer_digest
                or provisional_assessment.declared_subset_digest
                != declared_subset.digest
                or provisional_assessment.visual_image_digests
                != visual_image_digests
            ):
                raise ValueError("terminal provisional assessment binding changed")
            assessment_state = provisional_assessment.state
            assessment_reason_code = (
                "completed"
                if provisional_assessment.state == "completed"
                else "empty_declaration"
                if provisional_assessment.reason_code == "empty_declaration"
                else "no_resolved_declared_evidence"
                if provisional_assessment.reason_code
                == "no_resolved_declared_evidence"
                else provisional_assessment.reason_code
            )
            assessment_consistency = provisional_assessment.consistency
            assessment_input_digest = provisional_assessment.assessment_input_digest
            assessment_output_digest = provisional_assessment.assessment_output_digest
            assessment_results = provisional_assessment.results
        else:
            if not proposal.claimed_evidence_handles:
                assessment_state = "not_attempted"
                assessment_reason_code = "empty_declaration"
                assessment_consistency = "not_applicable"
            elif not declared_subset.items:
                assessment_state = "not_attempted"
                assessment_reason_code = "no_resolved_declared_evidence"
                assessment_consistency = "insufficient"
            else:
                assessment_state = "unavailable"
                assessment_reason_code = "provider_failed"
                assessment_consistency = "unavailable"
            try:
                if declared_subset.items:
                    assessment_result = self._evaluator.assess(
                        execution_id=execution_id,
                        finalized_answer=finalized_answer,
                        declared_evidence_subset=declared_subset,
                        deadline_at=snapshot.deadline_at,
                        route=snapshot.route,
                        assessment_ordinal=1,
                    )
                    assessment_state = "completed"
                    assessment_reason_code = "completed"
                    assessment_consistency = assessment_result.consistency
                    assessment_input_digest = assessment_result.assessment_input_digest
                    assessment_output_digest = assessment_result.assessment_output_digest
                    assessment_results = assessment_result.results
            except ClaimAssessmentUnavailable as error:
                assessment_state = "unavailable"
                assessment_reason_code = error.reason_code
                assessment_consistency = "unavailable"
        assessment_step = _assessment_audit_step(
            ordinal=len(audit_steps) + 1,
            assessment_state=assessment_state,
            declared_subset=declared_subset,
            finalized_answer=finalized_answer,
        )
        try:
            governance_command = _governance_command(
                execution_id=execution_id,
                finalized_answer=finalized_answer,
                retrieval_status=retrieval_status,
                declared_subset=declared_subset,
                declared_lineage=declared_lineage,
                assessment_state=assessment_state,
                assessment_reason_code=assessment_reason_code,
                assessment_consistency=assessment_consistency,
                answer_digest=answer_digest,
                visual_image_digests=visual_image_digests,
                assessment_input_digest=assessment_input_digest,
                assessment_output_digest=assessment_output_digest,
                assessment_results=assessment_results,
                delivery_constraint=delivery_constraint,
            )
        except ValidationError:
            # Provider/schema-semantic mistakes never make answer text unavailable.
            assessment_step = assessment_step.model_copy(
                update={"status": "failed"}
            )
            governance_command = _invalid_governance_command(
                execution_id=execution_id,
                finalized_answer=finalized_answer,
                retrieval_status=retrieval_status,
                declared_subset=declared_subset,
                declared_lineage=declared_lineage,
                answer_digest=answer_digest,
                visual_image_digests=visual_image_digests,
                assessment_input_digest=assessment_input_digest,
                delivery_constraint=delivery_constraint,
            )
        governed = self._result_governance.materialize_v2(governance_command)
        citation = self._citation.materialize_v2(
            _citation_command(execution_id, governed)
        )
        audit_steps.extend(
            _terminal_materialization_steps(
                start_ordinal=len(audit_steps) + 1,
                assessment_step=assessment_step,
                evidence_pack=evidence_pack,
                proposal=proposal,
                governed=governed,
                citation=citation,
                evidence_count=len(declared_lineage),
            )
        )
        audit = self._audit.materialize_v2(
            _audit_command(
                execution_id=execution_id,
                proposal=proposal,
                evidence_pack=evidence_pack,
                governed=governed,
                citation=citation,
                steps=audit_steps,
            )
        )
        snapshot = self._runtime.prepare_terminal(
            _prepare_terminal_command(
                snapshot=snapshot,
                evidence_pack_ref=evidence_pack.evidence_pack_ref,
                governed_answer_draft_ref=governed.draft_ref,
                citation_binding_draft_ref=citation.draft_ref,
                audit_draft_ref=audit.draft_ref,
            )
        )
        self._runtime.commit_terminal(_commit_terminal_command(snapshot))

    def _fail_active(self, snapshot: ExecutionSnapshotV1, failure_code: str) -> None:
        try:
            current = self._runtime.snapshot(snapshot.execution_id)
        except Exception:
            current = snapshot
        if current.state in TERMINAL_STATES:
            return
        self._runtime.fail_carrier(
            FailCarrierExecutionV1(
                execution_id=current.execution_id,
                expected_version=current.version,
                holder_id=current.lease.holder_id,
                expected_lease_version=current.lease.lease_version,
                fencing_token=current.lease.fencing_token,
                failure_code=failure_code,
                detected_by="carrier",
            )
        )


__all__ = [
    "StatelessTurnExecutionOrchestrator",
    "TurnModelInputSource",
]
