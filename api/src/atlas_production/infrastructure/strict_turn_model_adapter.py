from __future__ import annotations

import logging
from copy import copy, deepcopy

from pydantic import ValidationError

from atlas_production.infrastructure.strict_turn_model_capabilities import (
    _ACTION_MODELS,
    _final_schema,
    _tool,
    _within_capabilities,
)
from atlas_production.infrastructure.strict_turn_model_messages import (
    _answer_skill_system_message,
    _canonical,
    _digest,
    _initial_provider_messages,
    _packet_answer_provider_messages,
)
from atlas_production.infrastructure.strict_turn_model_reasoning import (
    _ProviderInitialPlanDecisionV1,
    _ProviderProcessEvaluationV1,
    _ProviderProcessRubricDecisionV1,
    _ProviderReplanDecisionV1,
    _bounded_plan_summaries,
    _bounded_plan_text,
    _build_reasoning_wire,
    _evaluation_payload,
    _evaluation_schema,
    _initial_planning_context,
    _next_runtime_plan_item_id,
    _plan_payload,
    _replan_payload,
    _replan_schema,
    _replanning_context,
    _selection_payload,
    _selection_schema,
    _usage_value,
)
from atlas_production.modules.model_routing.public import (
    ModelRoutingRuntime,
    ProviderAssistantToolCallMessage,
    ProviderCompleted,
    ProviderConversationRequest,
    ProviderIncomplete,
    ProviderImageContentPart,
    ProviderProtocolError,
    ProviderRefused,
    ProviderToolCall,
    ProviderToolResultMessage,
    ProviderTextContentPart,
    ProviderUserMessage,
    estimate_provider_wire,
    require_provider_wire_within_limits,
)
from atlas_production.modules.prompt_skills.public import PromptSkillInstructionsV1
from atlas_production.modules.retrieval.public import (
    KnowledgeToolObservationEnvelopeV1,
    KnowledgeToolObservationV1,
    VisualImagePayloadV1,
    VisualInspectionResultV1,
)
from atlas_production.modules.turn_execution.public import (
    DeepReasoningEvaluationResultV1,
    DeepReasoningContractError,
    DeepReasoningModel,
    DeepReasoningPlanResultV1,
    FinalizeAnswerV1,
    FinalizeResearchV1,
    GateCorrectionFeedbackV1,
    InitialPlanningNodeContextV1,
    ModelActionResultV1,
    ModelContractViolationV1,
    ModelStepResultV1,
    PacketAnswerComposer,
    PacketAnswerModelInputV1,
    ResearchModelInputV1,
    ReplanningNodeContextV1,
    SkillSelectionDecisionV1,
    SkillSelectionRequestV2,
    SkillSelectionResultV1,
    SkillSelectorModel,
    StrictModelInputV1,
    StrictTurnModel,
    StrictTurnModelSession,
    TurnModelInputV3,
    finalize_answer_schema,
)
from atlas_production.providers import ProviderError, build_native_json_schema
from atlas_production.modules.turn_runtime.public import (
    ExecutionSnapshotV1,
    ProcessScoreV1,
    ReasoningEvaluationV1,
    ReasoningPlanV2,
    SchemaRetryOriginCode,
)


logger = logging.getLogger(__name__)


_PROCESS_FINDING_CODE_BY_DIMENSION = {
    "plan_coverage": "coverage_gap",
    "evidence_handling": "evidence_gap",
    "conflict_handling": "conflict_handling_gap",
    "gap_resolution": "gap_resolution_gap",
    "revision_completion": "revision_incomplete",
}

def _request_contains_image(request: ProviderConversationRequest) -> bool:
    return any(
        isinstance(part, ProviderImageContentPart)
        for message in request.messages
        for part in (
            message.content
            if isinstance(getattr(message, "content", None), (tuple, list))
            else ()
        )
    )


def _attempt_matches_snapshot(attempt, snapshot) -> bool:
    policy = attempt.route.runtime_policy
    return (
        attempt.route.route_id == snapshot.route_id
        and attempt.route.revision == snapshot.route_revision
        and policy.revision == snapshot.runtime_policy_revision
        and policy.tokenizer_profile == snapshot.tokenizer_profile
        and policy.context_window_tokens == snapshot.context_window_tokens
        and policy.max_input_tokens_per_invocation
        == snapshot.max_input_tokens_per_invocation
        and policy.max_output_tokens_per_invocation
        == snapshot.max_output_tokens_per_invocation
        and policy.max_tool_result_tokens_per_execution
        == snapshot.max_tool_result_tokens_per_execution
        and policy.max_total_tokens_per_conversation
        == snapshot.max_total_tokens_per_conversation
    )


def _bound_model_contract_digest(model_input: StrictModelInputV1) -> str:
    if isinstance(model_input, TurnModelInputV3):
        payload: object = {
            "result_kind": "conversation_answer",
            "answer_behavior": model_input.answer_behavior.model_dump(mode="json"),
        }
    else:
        payload = {
            "result_kind": "agent_research",
            "research_id": model_input.research_id,
            "question_ref": model_input.question_ref,
            "scope_ref": model_input.scope_ref,
            "scope_digest": model_input.scope_digest,
            "catalog_ref": model_input.knowledge_catalog_ref,
            "output_mode": model_input.output_mode,
        }
    return _digest(payload)


class ProviderTurnModelSession(StrictTurnModelSession):
    """Carrier-local transcript; it cannot be serialized or reconstructed."""

    def __init__(
        self,
        *,
        routing: ModelRoutingRuntime,
        model_input: StrictModelInputV1,
        record_invocations: bool,
    ) -> None:
        self._routing = routing
        self._attempt = routing.open_tested_attempt(model_input.route.route_id)
        if not _attempt_matches_snapshot(self._attempt, model_input.route):
            raise ProviderProtocolError(safe_code="model_route_revision_conflict")
        self._vision_route = model_input.route.vision_route
        self._vision_attempt = None
        self._execution_id = model_input.execution_id
        self._model_contract_digest = _bound_model_contract_digest(model_input)
        # ``open_session`` binds the non-transferable carrier to one execution,
        # but the first provider-visible input is appended only after the
        # runtime has accepted the provider-invocation budget by CAS.
        self._messages = []
        self._last_input_digest: str | None = None
        self._pending_tool_call_id: str | None = None
        self._discarded = False
        self._candidate_ordinal = 0
        self._candidate_provider_ordinal = 0
        self._candidate_complete = False
        self._record_invocations = record_invocations

    def _attempt_for_request(self, request: ProviderConversationRequest):
        if not _request_contains_image(request):
            return self._attempt
        if self._vision_route is None:
            raise ProviderProtocolError(
                safe_code="vision_model_route_unavailable"
            )
        if self._vision_attempt is None:
            try:
                candidate = self._routing.open_tested_attempt(
                    self._vision_route.route_id
                )
            except ProviderError as error:
                raise ProviderProtocolError(
                    safe_code="vision_model_route_unavailable"
                ) from error
            if (
                not getattr(candidate.route, "supports_vision", False)
                or not _attempt_matches_snapshot(candidate, self._vision_route)
            ):
                raise ProviderProtocolError(
                    safe_code="vision_model_route_unavailable"
                )
            self._vision_attempt = candidate
        return self._vision_attempt

    def _next_request(
        self,
        model_input: StrictModelInputV1,
        *,
        finalize_only: bool,
        enforce_limits: bool = True,
    ):
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if model_input.execution_id != self._execution_id:
            raise ProviderProtocolError(safe_code="turn_model_execution_changed")
        if self._candidate_ordinal == 0:
            raise ProviderProtocolError(
                safe_code="turn_model_answer_candidate_not_started"
            )
        if _bound_model_contract_digest(model_input) != self._model_contract_digest:
            raise ProviderProtocolError(
                safe_code="turn_model_input_contract_changed"
            )
        input_payload = model_input.model_dump(mode="json")
        input_digest = _digest(input_payload)
        messages = list(self._messages)
        capabilities = model_input.capabilities
        if (
            capabilities.execution_id != self._execution_id
            or capabilities.catalog_ref != model_input.knowledge_catalog_ref
        ):
            raise ProviderProtocolError(safe_code="turn_model_capabilities_changed_owner")
        tools = [
            _tool(_ACTION_MODELS[action], capabilities)
            for action in capabilities.allowed_actions
            if action in _ACTION_MODELS
        ]
        final_schema = _final_schema(capabilities)
        request = ProviderConversationRequest(
            messages=messages,
            tools=[] if finalize_only else tools,
            tool_choice="none" if finalize_only else "auto",
            parallel_tool_calls=False,
            max_output_tokens=min(
                16000,
                self._attempt.route.runtime_policy.max_output_tokens_per_invocation,
            ),
        )
        attempt = self._attempt_for_request(request)
        selected_max_output = min(
            16000,
            attempt.route.runtime_policy.max_output_tokens_per_invocation,
        )
        if request.max_output_tokens != selected_max_output:
            request = request.model_copy(
                update={"max_output_tokens": selected_max_output}
            )
        sizing = (
            require_provider_wire_within_limits
            if enforce_limits
            else estimate_provider_wire
        )
        estimate = sizing(
            policy=attempt.route.runtime_policy,
            request=request,
            response_schema=final_schema,
            tool_reserve_tokens=(
                0
                if finalize_only
                else attempt.route.runtime_policy.max_tool_result_tokens_per_execution
            ),
        )
        return messages, request, final_schema, input_digest, estimate, attempt
    def estimate_begin_answer_candidate_tokens(
        self,
        model_input: StrictModelInputV1,
        *,
        candidate_ordinal: int,
        candidate_kind: str,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
    ) -> int:
        trial = copy(self)
        trial._messages = list(self._messages)
        trial.begin_answer_candidate(
            model_input,
            candidate_ordinal=candidate_ordinal,
            candidate_kind=candidate_kind,
            selected_skills=selected_skills,
        )
        return trial.estimate_next_request_tokens(
            model_input,
            finalize_only=candidate_kind == "limit_final",
        )

    def begin_answer_candidate(
        self,
        model_input: StrictModelInputV1,
        *,
        candidate_ordinal: int,
        candidate_kind: str,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
    ) -> None:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if self._pending_tool_call_id is not None:
            raise ProviderProtocolError(safe_code="turn_model_tool_result_missing")
        if candidate_kind not in {"normal", "limit_final"}:
            raise ProviderProtocolError(safe_code="invalid_answer_candidate_kind")
        if candidate_ordinal != self._candidate_ordinal + 1:
            raise ProviderProtocolError(
                safe_code="answer_candidate_ordinal_not_contiguous"
            )
        if self._candidate_ordinal > 0 and not self._candidate_complete:
            raise ProviderProtocolError(
                safe_code="previous_answer_candidate_is_incomplete"
            )
        if model_input.execution_id != self._execution_id:
            raise ProviderProtocolError(safe_code="turn_model_execution_changed")
        if candidate_ordinal == 1:
            self._messages = _initial_provider_messages(
                model_input,
                selected_skills=selected_skills,
            )
        elif isinstance(model_input, TurnModelInputV3):
            self._messages.append(
                _answer_skill_system_message(
                    selected_skills,
                    replacement=True,
                )
            )
        self._candidate_ordinal = candidate_ordinal
        self._candidate_provider_ordinal = 0
        self._candidate_complete = False


    def estimate_next_request_tokens(
        self, model_input: StrictModelInputV1, *, finalize_only: bool
    ) -> int:
        return self._next_request(
            model_input, finalize_only=finalize_only
        )[4].input_tokens

    def estimate_next_request_tokens_unchecked(
        self, model_input: StrictModelInputV1, *, finalize_only: bool
    ) -> int:
        return self._next_request(
            model_input,
            finalize_only=finalize_only,
            enforce_limits=False,
        )[4].input_tokens

    def next_action(
        self,
        model_input: StrictModelInputV1,
        *,
        finalize_only: bool,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> ModelStepResultV1:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if model_input.execution_id != self._execution_id:
            raise ProviderProtocolError(safe_code="turn_model_execution_changed")
        if self._pending_tool_call_id is not None:
            raise ProviderProtocolError(safe_code="turn_model_tool_result_missing")

        (
            messages,
            request,
            final_schema,
            input_digest,
            _estimate,
            attempt,
        ) = self._next_request(model_input, finalize_only=finalize_only)
        # Keep the carrier transcript distinct from the immutable request
        # snapshot retained by providers/tests for audit.
        self._messages = list(messages)
        self._candidate_provider_ordinal += 1
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                final_schema,
                invocation_purpose="turn_execution",
                subject_kind="turn_execution",
                subject_ref=self._execution_id,
                execution_key=(
                    f"{self._execution_id}:answer-candidate:{self._candidate_ordinal}:"
                    f"provider:{self._candidate_provider_ordinal}"
                ),
                prompt_digest=_digest([input_digest, finalize_only]),
                attempt_ordinal=self._candidate_provider_ordinal,
                repair_origin_error_codes=(
                    []
                    if repair_origin_error_code is None
                    else [repair_origin_error_code]
                ),
            )
            self._routing.record_invocation_started(handle)
        try:
            outcome = self._routing.invoke(attempt, request, final_schema)
        except Exception as error:
            if handle is not None:
                self._routing.record_invocation_failure(
                    handle, getattr(error, "safe_code", "provider_failed")
                )
            raise
        if handle is not None:
            self._routing.record_invocation_success(handle, dict(outcome.usage))

        input_tokens = _usage_value(outcome.usage, "input_tokens", "prompt_tokens")
        output_tokens = _usage_value(outcome.usage, "output_tokens", "completion_tokens")
        capabilities = model_input.capabilities
        if isinstance(outcome, ProviderToolCall):
            if finalize_only:
                raise ProviderProtocolError(safe_code="tool_call_after_finalize_only")
            model = _ACTION_MODELS.get(outcome.call.name)
            if model is None:
                self._messages.append(outcome.assistant_message)
                self._pending_tool_call_id = outcome.call.call_id
                return ModelContractViolationV1(
                    safe_code="unknown_turn_tool",
                    action_name=outcome.call.name[:100],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            arguments = deepcopy(outcome.call.arguments)
            try:
                action = model.model_validate(arguments)
            except ValidationError:
                self._messages.append(outcome.assistant_message)
                self._pending_tool_call_id = outcome.call.call_id
                return ModelContractViolationV1(
                    safe_code="invalid_turn_tool_arguments",
                    action_name=outcome.call.name[:100],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            if action.action != outcome.call.name:
                self._messages.append(outcome.assistant_message)
                self._pending_tool_call_id = outcome.call.call_id
                return ModelContractViolationV1(
                    safe_code="invalid_turn_tool_arguments",
                    action_name=outcome.call.name[:100],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            self._messages.append(outcome.assistant_message)
            self._pending_tool_call_id = outcome.call.call_id
            if not _within_capabilities(action, capabilities):
                return ModelContractViolationV1(
                    safe_code="selection_outside_capabilities",
                    action_name=outcome.call.name[:100],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            return ModelActionResultV1(
                action=action,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if isinstance(outcome, ProviderCompleted):
            research = "finalize_research" in capabilities.allowed_actions
            model = FinalizeResearchV1 if research else FinalizeAnswerV1
            action_name = "finalize_research" if research else "finalize_answer"
            try:
                action = model.model_validate(outcome.output)
            except ValidationError:
                self._messages.append(outcome.assistant_message)
                return ModelContractViolationV1(
                    safe_code=(
                        "invalid_finalize_research"
                        if research
                        else "invalid_finalize_answer"
                    ),
                    action_name=action_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            self._messages.append(outcome.assistant_message)
            if not _within_capabilities(action, capabilities):
                return ModelContractViolationV1(
                    safe_code="selection_outside_capabilities",
                    action_name=action_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            self._candidate_complete = True
            return ModelActionResultV1(
                action=action,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if isinstance(outcome, (ProviderRefused, ProviderIncomplete)):
            raise ProviderProtocolError(safe_code=f"provider_{outcome.kind}")
        raise ProviderProtocolError(safe_code="unknown_provider_outcome")

    def accept_tool_observation(
        self,
        observation: KnowledgeToolObservationV1,
        *,
        visual_image: VisualImagePayloadV1 | None = None,
    ) -> None:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        call_id = self._pending_tool_call_id
        if call_id is None:
            raise ProviderProtocolError(safe_code="unexpected_turn_tool_result")
        envelope = KnowledgeToolObservationEnvelopeV1(observation=observation)
        self._messages.append(
            ProviderToolResultMessage(
                tool_call_id=call_id,
                content=_canonical(envelope.model_dump(mode="json")),
            )
        )
        if isinstance(observation, VisualInspectionResultV1):
            if (
                visual_image is None
                or visual_image.visual_handle != observation.visual_handle
                or visual_image.image_ref != observation.image_ref
                or visual_image.image_digest != observation.image_digest
                or visual_image.width != observation.width
                or visual_image.height != observation.height
            ):
                raise ProviderProtocolError(safe_code="visual_tool_image_mismatch")
            self._messages.append(
                ProviderUserMessage(
                    content=(
                        ProviderTextContentPart(
                            text=_canonical(
                                {
                                    "visual_observation": observation.model_dump(
                                        mode="json"
                                    )
                                }
                            )
                        ),
                        ProviderImageContentPart(
                            content=visual_image.content,
                            digest=visual_image.image_digest,
                            width=visual_image.width,
                            height=visual_image.height,
                        ),
                    )
                )
            )
        elif visual_image is not None:
            raise ProviderProtocolError(safe_code="unexpected_visual_tool_image")
        self._pending_tool_call_id = None

    def accept_contract_repair(self, violation: ModelContractViolationV1) -> None:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        repair = {
            "result_type": "knowledge_tool_error",
            "error_code": "invalid_handle",
            "message_code": "model_selection_outside_current_capabilities",
            "retryable": True,
        }
        if self._pending_tool_call_id is not None:
            self.accept_tool_observation(
                KnowledgeToolObservationEnvelopeV1.model_validate(
                    {"observation": repair}
                ).observation
            )
            return
        self._messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "contract_repair": {
                            "safe_code": violation.safe_code,
                            "instruction": (
                                "Choose only values listed in the current "
                                "turn_model_input.capabilities snapshot."
                            ),
                        }
                    }
                )
            )
        )

    def accept_reasoning_feedback(
        self,
        evaluation: ReasoningEvaluationV1,
        *,
        correction_kind: Literal["revise_only", "research_then_revise"],
        gate_feedback: GateCorrectionFeedbackV1 | None = None,
        plan: ReasoningPlanV2 | None = None,
    ) -> None:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if self._pending_tool_call_id is not None:
            raise ProviderProtocolError(safe_code="turn_model_tool_result_missing")
        if (
            evaluation.verdict == "unavailable" and gate_feedback is None
        ) or (
            evaluation.verdict != "unavailable" and evaluation.score is None
        ):
            raise ProviderProtocolError(safe_code="invalid_reasoning_feedback")
        if (
            (correction_kind == "research_then_revise") != (plan is not None)
            or (evaluation.verdict == "accept" and gate_feedback is None)
            or (
                (evaluation.verdict == "research_then_revise")
                != (correction_kind == "research_then_revise")
            )
        ):
            raise ProviderProtocolError(safe_code="invalid_reasoning_feedback")
        instruction = (
            "Use the replacement plan to choose legal tools needed only for the identified "
            "material evidence gap, then make the smallest local revision to the complete "
            "candidate and return finalize_answer. Preserve every supported direct answer, "
            "current-user premise, and deterministic derivation."
            if plan is not None
            else "Make the smallest local revision to the complete candidate without tools, "
            "preserving every supported direct answer, current-user premise, and "
            "deterministic derivation, then return finalize_answer."
        )
        self._messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "atlas_process_evaluation": evaluation.model_dump(mode="json"),
                        "atlas_runtime_correction_kind": correction_kind,
                        "atlas_gate_correction": (
                            None
                            if gate_feedback is None
                            else gate_feedback.model_dump(mode="json")
                        ),
                        "replacement_plan": (
                            None if plan is None else plan.model_dump(mode="json")
                        ),
                        "instruction": instruction + " Do not reveal hidden reasoning.",
                    }
                )
            )
        )

    def accept_reasoning_limit(
        self,
        evaluation: ReasoningEvaluationV1,
        *,
        gate_feedback: GateCorrectionFeedbackV1 | None = None,
    ) -> None:
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if self._pending_tool_call_id is not None:
            raise ProviderProtocolError(safe_code="turn_model_tool_result_missing")
        if (
            evaluation.verdict in {"accept", "unavailable"}
            and gate_feedback is None
        ):
            raise ProviderProtocolError(safe_code="invalid_reasoning_limit")
        self._messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "atlas_process_evaluation": evaluation.model_dump(mode="json"),
                        "atlas_gate_correction": (
                            None
                            if gate_feedback is None
                            else gate_feedback.model_dump(mode="json")
                        ),
                        "instruction": (
                            "No correction or tool cycles remain. Return one complete limitation-aware "
                            "finalize_answer that makes only the smallest local corrections needed for "
                            "safe findings and explicitly states unresolved material evidence gaps. "
                            "Preserve every supported direct answer, current-user premise, and "
                            "deterministic derivation. Remove only unsupported secondary ranking, "
                            "preference, recommendation, or tradeoff. Do not turn missing retrieved "
                            "evidence into a claim that the underlying fact is unknowable. If a comparison "
                            "still lacks decisive evidence for a material candidate, state that the "
                            "comparison is incomplete and do not rank or select unsupported candidates. "
                            "Do not reveal hidden reasoning."
                        ),
                    }
                )
            )
        )

    def discard(self) -> None:
        self._messages.clear()
        self._pending_tool_call_id = None
        self._candidate_complete = False
        self._discarded = True


class StrictProviderTurnModel(
    StrictTurnModel, DeepReasoningModel, SkillSelectorModel, PacketAnswerComposer
):
    def __init__(self, routing: ModelRoutingRuntime, *, record_invocations: bool = True) -> None:
        self._routing = routing
        self._record_invocations = record_invocations

    def open_session(self, model_input: StrictModelInputV1) -> ProviderTurnModelSession:
        return ProviderTurnModelSession(
            routing=self._routing,
            model_input=model_input,
            record_invocations=self._record_invocations,
        )

    def estimate_packet_answer_tokens(
        self, model_input: PacketAnswerModelInputV1
    ) -> int:
        return self._packet_answer_wire(model_input)[3].input_tokens

    def compose_packet_answer(
        self,
        model_input: PacketAnswerModelInputV1,
        *,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> ModelStepResultV1:
        attempt, request, response_schema, _estimate = self._packet_answer_wire(
            model_input
        )
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                response_schema,
                invocation_purpose="agent_research_packet_answer",
                subject_kind="turn_execution",
                subject_ref=model_input.execution_id,
                execution_key=(
                    f"{model_input.execution_id}:packet-answer:"
                    f"{schema_retry_ordinal + 1}"
                ),
                prompt_digest=_digest(model_input.model_dump(mode="json")),
                attempt_ordinal=schema_retry_ordinal + 1,
                repair_origin_error_codes=(
                    []
                    if repair_origin_error_code is None
                    else [repair_origin_error_code]
                ),
            )
            self._routing.record_invocation_started(handle)
        try:
            outcome = self._routing.invoke(attempt, request, response_schema)
        except Exception as error:
            if handle is not None:
                self._routing.record_invocation_failure(
                    handle, getattr(error, "safe_code", "provider_failed")
                )
            raise
        if handle is not None:
            self._routing.record_invocation_success(handle, dict(outcome.usage))
        input_tokens = _usage_value(outcome.usage, "input_tokens", "prompt_tokens")
        output_tokens = _usage_value(
            outcome.usage, "output_tokens", "completion_tokens"
        )
        if not isinstance(outcome, ProviderCompleted):
            if isinstance(outcome, (ProviderRefused, ProviderIncomplete)):
                raise ProviderProtocolError(safe_code=f"provider_{outcome.kind}")
            raise ProviderProtocolError(safe_code="unknown_provider_outcome")
        try:
            action = FinalizeAnswerV1.model_validate(outcome.output)
        except ValidationError:
            return ModelContractViolationV1(
                safe_code="invalid_finalize_answer",
                action_name="finalize_answer",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        allowed = {item.evidence_handle for item in model_input.evidence}
        if not set(action.claimed_evidence_handles).issubset(allowed):
            return ModelContractViolationV1(
                safe_code="selection_outside_capabilities",
                action_name="finalize_answer",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return ModelActionResultV1(
            action=action,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _packet_answer_wire(self, model_input: PacketAnswerModelInputV1):
        attempt = self._routing.open_tested_attempt(model_input.route.route_id)
        if not _attempt_matches_snapshot(attempt, model_input.route):
            raise ProviderProtocolError(safe_code="model_route_revision_conflict")
        response_schema = build_native_json_schema(
            f"packet_answer_v1_{model_input.packet_digest[:12]}",
            finalize_answer_schema(),
        )
        request = ProviderConversationRequest(
            messages=_packet_answer_provider_messages(model_input),
            tools=[],
            tool_choice="none",
            parallel_tool_calls=False,
            max_output_tokens=min(
                16_000,
                attempt.route.runtime_policy.max_output_tokens_per_invocation,
            ),
        )
        estimate = require_provider_wire_within_limits(
            policy=attempt.route.runtime_policy,
            request=request,
            response_schema=response_schema,
            tool_reserve_tokens=0,
        )
        return attempt, request, response_schema, estimate

    def _reasoning_wire(
        self,
        model_input: StrictModelInputV1 | PacketAnswerModelInputV1 | ExecutionSnapshotV1,
        *,
        purpose: str,
        payload: dict[str, object],
        schema_name: str,
        schema: dict[str, object],
        max_output_tokens: int,
        selected_skills: tuple[PromptSkillInstructionsV1, ...] = (),
    ):
        attempt = self._routing.open_tested_attempt(model_input.route.route_id)
        policy = attempt.route.runtime_policy
        if (
            attempt.route.revision != model_input.route.route_revision
            or policy.revision != model_input.route.runtime_policy_revision
            or policy.tokenizer_profile != model_input.route.tokenizer_profile
            or policy.context_window_tokens != model_input.route.context_window_tokens
            or policy.max_input_tokens_per_invocation
            != model_input.route.max_input_tokens_per_invocation
            or policy.max_output_tokens_per_invocation
            != model_input.route.max_output_tokens_per_invocation
        ):
            raise ProviderProtocolError(safe_code="model_route_revision_conflict")
        request, response_schema = _build_reasoning_wire(
            purpose=purpose,
            payload=payload,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=min(
                max_output_tokens, policy.max_output_tokens_per_invocation
            ),
            selected_skills=selected_skills,
        )
        estimate = require_provider_wire_within_limits(
            policy=policy,
            request=request,
            response_schema=response_schema,
            tool_reserve_tokens=0,
        )
        return attempt, request, response_schema, estimate

    def _invoke_reasoning(
        self,
        model_input: StrictModelInputV1 | PacketAnswerModelInputV1 | ExecutionSnapshotV1,
        *,
        purpose: str,
        ordinal: int,
        payload: dict[str, object],
        schema_name: str,
        schema: dict[str, object],
        max_output_tokens: int,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
        selected_skills: tuple[PromptSkillInstructionsV1, ...] = (),
        execution_key: str | None = None,
    ) -> ProviderCompleted:
        attempt, request, response_schema, _estimate = self._reasoning_wire(
            model_input,
            purpose=purpose,
            payload=payload,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=max_output_tokens,
            selected_skills=selected_skills,
        )
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                response_schema,
                invocation_purpose=purpose,
                subject_kind="turn_execution",
                subject_ref=model_input.execution_id,
                execution_key=execution_key or f"{model_input.execution_id}:{purpose}:{ordinal}",
                prompt_digest=_digest(payload),
                attempt_ordinal=ordinal,
                repair_origin_error_codes=(
                    []
                    if repair_origin_error_code is None
                    else [repair_origin_error_code]
                ),
            )
            self._routing.record_invocation_started(handle)
        try:
            outcome = self._routing.invoke(attempt, request, response_schema)
        except Exception as error:
            if handle is not None:
                self._routing.record_invocation_failure(
                    handle, getattr(error, "safe_code", "provider_failed")
                )
            raise
        if handle is not None:
            self._routing.record_invocation_success(handle, dict(outcome.usage))
        if not isinstance(outcome, ProviderCompleted):
            safe_code = (
                f"provider_{outcome.kind}"
                if isinstance(outcome, (ProviderRefused, ProviderIncomplete))
                else "unknown_provider_outcome"
            )
            raise ProviderProtocolError(safe_code=safe_code)
        return outcome

    @staticmethod
    def build_initial_planning_node_context(
        model_input: TurnModelInputV3,
    ) -> InitialPlanningNodeContextV1:
        return _initial_planning_context(model_input)

    @staticmethod
    def build_replanning_node_context(
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        remaining_execution_limits: dict[str, int],
    ) -> ReplanningNodeContextV1:
        return _replanning_context(
            model_input,
            plan=plan,
            evaluation=evaluation,
            remaining_execution_limits=remaining_execution_limits,
        )
    @staticmethod
    def _selection_purpose(request: SkillSelectionRequestV2) -> str:
        return {
            "resolver": "context_understanding_skill_selection",
            "deep_initial_planner": "deep_initial_planner_skill_selection",
            "deep_replanner": "deep_replanner_skill_selection",
            "answer_candidate": "answer_candidate_skill_selection",
        }[request.node]

    @staticmethod
    def _selection_ordinal(request: SkillSelectionRequestV2) -> int:
        if request.node == "resolver":
            return 1
        if request.node == "deep_initial_planner":
            return 1
        if request.node == "deep_replanner":
            return request.node_context.current_plan.generation + 1
        return request.node_context.candidate_ordinal

    def estimate_selection_request_tokens(
        self,
        snapshot: ExecutionSnapshotV1,
        request: SkillSelectionRequestV2,
    ) -> int:
        return self._reasoning_wire(
            snapshot,
            purpose=self._selection_purpose(request),
            payload=_selection_payload(request),
            schema_name="atlas_skill_selection_v2",
            schema=_selection_schema(request),
            max_output_tokens=1000,
        )[3].input_tokens

    def select(
        self,
        snapshot: ExecutionSnapshotV1,
        request: SkillSelectionRequestV2,
    ) -> SkillSelectionResultV1:
        ordinal = self._selection_ordinal(request)
        execution_key = (
            f"{snapshot.execution_id}:answer-candidate:{ordinal}:skill-selection"
            if request.node == "answer_candidate"
            else f"{snapshot.execution_id}:{request.node}:skill-selection:{ordinal}"
        )
        outcome = self._invoke_reasoning(
            snapshot,
            purpose=self._selection_purpose(request),
            ordinal=ordinal,
            payload=_selection_payload(request),
            schema_name="atlas_skill_selection_v2",
            schema=_selection_schema(request),
            max_output_tokens=1000,
            execution_key=execution_key,
        )
        try:
            decision = SkillSelectionDecisionV1.model_validate(outcome.output)
        except ValidationError as error:
            raise DeepReasoningContractError("selector_contract_invalid") from error
        offered_ids = {candidate.selection_id for candidate in request.candidates}
        if (
            len(decision.selected_skill_ids) > len(request.candidates)
            or any(
                selection_id not in offered_ids
                for selection_id in decision.selected_skill_ids
            )
        ):
            raise DeepReasoningContractError("selection_outside_catalog")
        return SkillSelectionResultV1(
            decision=decision,
            input_tokens=_usage_value(outcome.usage, "input_tokens", "prompt_tokens"),
            output_tokens=_usage_value(
                outcome.usage, "output_tokens", "completion_tokens"
            ),
        )



    @staticmethod
    def _plan_payload(
        node_context: InitialPlanningNodeContextV1, *, repair: bool
    ) -> dict[str, object]:
        return _plan_payload(node_context, repair=repair)

    def estimate_plan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: InitialPlanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        repair: bool,
    ) -> int:
        return self._reasoning_wire(
            model_input,
            purpose="deep_reasoning_plan",
            payload=self._plan_payload(node_context, repair=repair),
            schema_name="atlas_initial_plan_decision_v1",
            schema=_ProviderInitialPlanDecisionV1.model_json_schema(),
            max_output_tokens=4000,
            selected_skills=selected_skills,
        )[3].input_tokens

    def plan(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: InitialPlanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1:
        outcome = self._invoke_reasoning(
            model_input,
            purpose="deep_reasoning_plan",
            ordinal=schema_retry_ordinal + 1,
            payload=self._plan_payload(node_context, repair=repair),
            schema_name="atlas_initial_plan_decision_v1",
            schema=_ProviderInitialPlanDecisionV1.model_json_schema(),
            max_output_tokens=4000,
            repair_origin_error_code=repair_origin_error_code,
            selected_skills=selected_skills,
        )
        try:
            decision = _ProviderInitialPlanDecisionV1.model_validate(outcome.output)
            summaries = _bounded_plan_summaries(decision.item_summaries, limit=8)
            if not summaries:
                raise ValueError("initial plan requires at least one work item")
            plan = ReasoningPlanV2(
                generation=1,
                parent_generation=None,
                next_objective=_bounded_plan_text(
                    decision.next_objective, max_length=160
                ),
                completion_condition=_bounded_plan_text(
                    decision.completion_condition, max_length=160
                ),
                items=[
                    {
                        "item_id": _next_runtime_plan_item_id(
                            generation=1,
                            used_item_ids=set(),
                            ordinal=ordinal,
                        ),
                        "summary": summary,
                        "status": "pending",
                    }
                    for ordinal, summary in enumerate(summaries, start=1)
                ]
            )
        except (ValidationError, ValueError) as error:
            raise DeepReasoningContractError("deep_reasoning_plan_invalid") from error
        return DeepReasoningPlanResultV1(
            plan=plan,
            input_tokens=_usage_value(outcome.usage, "input_tokens", "prompt_tokens"),
            output_tokens=_usage_value(
                outcome.usage, "output_tokens", "completion_tokens"
            ),
        )

    @staticmethod
    def _replan_payload(
        node_context: ReplanningNodeContextV1, *, repair: bool
    ) -> dict[str, object]:
        return _replan_payload(node_context, repair=repair)

    @staticmethod
    def _replan_schema(plan: ReasoningPlanV2) -> dict[str, object]:
        return _replan_schema(plan)

    @staticmethod
    def _require_matching_replan_context(
        *,
        node_context: ReplanningNodeContextV1,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
    ) -> None:
        expected_finding = {
            "cycle": evaluation.cycle,
            "verdict": evaluation.verdict,
            "finding_codes": evaluation.finding_codes,
            "summary": evaluation.summary,
        }
        if (
            node_context.current_plan != plan
            or node_context.evaluator_finding != expected_finding
        ):
            raise DeepReasoningContractError("replanning_node_context_mismatch")

    def estimate_replan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: ReplanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
    ) -> int:
        self._require_matching_replan_context(
            node_context=node_context,
            plan=plan,
            evaluation=evaluation,
        )
        return self._reasoning_wire(
            model_input,
            purpose="deep_reasoning_replan",
            payload=self._replan_payload(node_context, repair=repair),
            schema_name="atlas_replan_decision_v1",
            schema=self._replan_schema(plan),
            max_output_tokens=4000,
            selected_skills=selected_skills,
        )[3].input_tokens

    def replan(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: ReplanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1:
        self._require_matching_replan_context(
            node_context=node_context,
            plan=plan,
            evaluation=evaluation,
        )
        outcome = self._invoke_reasoning(
            model_input,
            purpose="deep_reasoning_replan",
            ordinal=plan.generation * 10 + schema_retry_ordinal,
            payload=self._replan_payload(node_context, repair=repair),
            schema_name="atlas_replan_decision_v1",
            schema=self._replan_schema(plan),
            max_output_tokens=4000,
            repair_origin_error_code=repair_origin_error_code,
            selected_skills=selected_skills,
        )
        try:
            decision = _ProviderReplanDecisionV1.model_validate(outcome.output)
            pending_item_ids = {
                item.item_id for item in plan.items if item.status == "pending"
            }
            completed_item_ids = set(decision.completed_item_ids)
            skipped_item_ids = set(decision.skipped_item_ids)
            if completed_item_ids & skipped_item_ids:
                raise ValueError("one pending item received conflicting dispositions")
            if not completed_item_ids.issubset(pending_item_ids):
                raise ValueError("completed disposition references a non-pending item")
            if not skipped_item_ids.issubset(pending_item_ids):
                raise ValueError("skipped disposition references a non-pending item")

            next_generation = plan.generation + 1
            replacement_items = []
            used_item_ids = {item.item_id for item in plan.items}
            for item in plan.items:
                status = item.status
                if item.item_id in completed_item_ids:
                    status = "completed"
                elif item.item_id in skipped_item_ids:
                    status = "skipped"
                replacement_items.append(
                    {
                        "item_id": item.item_id,
                        "summary": item.summary,
                        "status": status,
                    }
                )

            remaining_item_capacity = max(0, 8 - len(replacement_items))
            new_summaries = _bounded_plan_summaries(
                decision.new_item_summaries,
                limit=remaining_item_capacity,
            )
            for ordinal, summary in enumerate(new_summaries, start=1):
                item_id = _next_runtime_plan_item_id(
                    generation=next_generation,
                    used_item_ids=used_item_ids,
                    ordinal=ordinal,
                )
                used_item_ids.add(item_id)
                replacement_items.append(
                    {"item_id": item_id, "summary": summary, "status": "pending"}
                )

            replacement = ReasoningPlanV2(
                generation=next_generation,
                parent_generation=plan.generation,
                next_objective=_bounded_plan_text(
                    decision.next_objective, max_length=160
                ),
                completion_condition=_bounded_plan_text(
                    decision.completion_condition, max_length=160
                ),
                items=replacement_items,
            )
        except (ValidationError, ValueError) as error:
            raise DeepReasoningContractError("deep_reasoning_replan_invalid") from error
        return DeepReasoningPlanResultV1(
            plan=replacement,
            input_tokens=_usage_value(outcome.usage, "input_tokens", "prompt_tokens"),
            output_tokens=_usage_value(outcome.usage, "output_tokens", "completion_tokens"),
        )

    @staticmethod
    def _evaluation_payload(
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        proposal: FinalizeAnswerV1,
        observations: list[KnowledgeToolObservationV1],
        cycle: int,
    ) -> dict[str, object]:
        return _evaluation_payload(
            model_input,
            plan=plan,
            proposal=proposal,
            observation_payloads=[
                item.model_dump(mode="json") for item in observations
            ],
            cycle=cycle,
        )

    @staticmethod
    def _evaluation_schema() -> dict[str, object]:
        return _evaluation_schema()

    def estimate_evaluation_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        proposal: FinalizeAnswerV1,
        observations: list[KnowledgeToolObservationV1],
        cycle: int,
    ) -> int:
        payload = self._evaluation_payload(
            model_input,
            plan=plan,
            proposal=proposal,
            observations=observations,
            cycle=cycle,
        )
        return self._reasoning_wire(
            model_input,
            purpose="deep_reasoning_evaluation",
            payload=payload,
            schema_name="atlas_process_evaluation_decision_v2",
            schema=self._evaluation_schema(),
            max_output_tokens=4000,
        )[3].input_tokens

    def evaluate(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        proposal: FinalizeAnswerV1,
        observations: list[KnowledgeToolObservationV1],
        cycle: int,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningEvaluationResultV1:
        payload = self._evaluation_payload(
            model_input,
            plan=plan,
            proposal=proposal,
            observations=observations,
            cycle=cycle,
        )
        outcome = self._invoke_reasoning(
            model_input,
            purpose="deep_reasoning_evaluation",
            ordinal=cycle * 10 + schema_retry_ordinal,
            payload=payload,
            schema_name="atlas_process_evaluation_decision_v2",
            schema=self._evaluation_schema(),
            max_output_tokens=4000,
            repair_origin_error_code=repair_origin_error_code,
        )
        try:
            provider_evaluation = _ProviderProcessEvaluationV1.model_validate(
                outcome.output
            )
        except ValidationError as error:
            safe_code = "deep_reasoning_evaluation_semantic_shape_invalid"
            logger.warning(
                "deep evaluator output rejected execution_id=%s safe_code=%s",
                model_input.execution_id,
                safe_code,
            )
            raise DeepReasoningContractError(safe_code) from error
        dimensions = provider_evaluation.rubric_dimensions.model_dump()
        if cycle == 1:
            dimensions["revision_completion"] = 2
        finding_codes = [
            _PROCESS_FINDING_CODE_BY_DIMENSION[name]
            for name, score in dimensions.items()
            if score < 2
        ]
        try:
            evaluation = ReasoningEvaluationV1(
                cycle=cycle,
                verdict=provider_evaluation.verdict,
                finding_codes=finding_codes,
                summary=provider_evaluation.summary,
                score=ProcessScoreV1(
                    rubric_version="atlas-process-rubric-v1",
                    **dimensions,
                    total=sum(dimensions.values()),
                ),
            )
        except ValidationError as error:
            safe_code = "deep_reasoning_evaluation_rubric_invalid"
            logger.warning(
                "deep evaluator output rejected execution_id=%s safe_code=%s",
                model_input.execution_id,
                safe_code,
            )
            raise DeepReasoningContractError(safe_code) from error
        return DeepReasoningEvaluationResultV1(
            evaluation=evaluation,
            input_tokens=_usage_value(outcome.usage, "input_tokens", "prompt_tokens"),
            output_tokens=_usage_value(
                outcome.usage, "output_tokens", "completion_tokens"
            ),
        )

    def estimate_initial_request_tokens(self, model_input: TurnModelInputV3) -> int:
        session = ProviderTurnModelSession(
            routing=self._routing,
            model_input=model_input,
            record_invocations=False,
        )
        session.begin_answer_candidate(
            model_input,
            candidate_ordinal=1,
            candidate_kind="normal",
            selected_skills=(),
        )
        return session.estimate_next_request_tokens(
            model_input, finalize_only=False
        )

    def estimate_initial_request_tokens_unchecked(
        self, model_input: TurnModelInputV3
    ) -> int:
        session = ProviderTurnModelSession(
            routing=self._routing,
            model_input=model_input,
            record_invocations=False,
        )
        session.begin_answer_candidate(
            model_input,
            candidate_ordinal=1,
            candidate_kind="normal",
            selected_skills=(),
        )
        return session.estimate_next_request_tokens_unchecked(
            model_input, finalize_only=False
        )


__all__ = ["ProviderTurnModelSession", "StrictProviderTurnModel"]
