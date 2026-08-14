from __future__ import annotations

import logging
from copy import deepcopy

from pydantic import ValidationError

from atlas_production.infrastructure.strict_turn_model_capabilities import (
    _ACTION_MODELS,
    _final_schema,
    _tool,
    _within_capabilities,
)
from atlas_production.infrastructure.strict_turn_model_messages import (
    _canonical,
    _digest,
    _initial_provider_messages,
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
    _next_runtime_plan_item_id,
    _plan_payload,
    _replan_payload,
    _replan_schema,
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
    GateCorrectionFeedbackV1,
    ModelActionResultV1,
    ModelContractViolationV1,
    ModelStepResultV1,
    StrictTurnModel,
    StrictTurnModelSession,
    TurnModelInputV3,
)
from atlas_production.providers import ProviderError
from atlas_production.modules.turn_runtime.public import (
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


class ProviderTurnModelSession(StrictTurnModelSession):
    """Carrier-local transcript; it cannot be serialized or reconstructed."""

    def __init__(
        self,
        *,
        routing: ModelRoutingRuntime,
        model_input: TurnModelInputV3,
        record_invocations: bool,
    ) -> None:
        self._routing = routing
        self._attempt = routing.open_tested_attempt(model_input.route.route_id)
        if not _attempt_matches_snapshot(self._attempt, model_input.route):
            raise ProviderProtocolError(safe_code="model_route_revision_conflict")
        self._vision_route = model_input.route.vision_route
        self._vision_attempt = None
        self._execution_id = model_input.execution_id
        self._answer_behavior_digest = _digest(
            model_input.answer_behavior.model_dump(mode="json")
        )
        # ``open_session`` binds the non-transferable carrier to one execution,
        # but the first provider-visible input is appended only after the
        # runtime has accepted the provider-invocation budget by CAS.
        self._messages = []
        self._last_input_digest: str | None = None
        self._pending_tool_call_id: str | None = None
        self._discarded = False
        self._provider_ordinal = 0
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
        model_input: TurnModelInputV3,
        *,
        finalize_only: bool,
        enforce_limits: bool = True,
    ):
        if self._discarded:
            raise ProviderProtocolError(safe_code="turn_model_session_discarded")
        if model_input.execution_id != self._execution_id:
            raise ProviderProtocolError(safe_code="turn_model_execution_changed")
        if (
            _digest(model_input.answer_behavior.model_dump(mode="json"))
            != self._answer_behavior_digest
        ):
            raise ProviderProtocolError(
                safe_code="turn_model_answer_behavior_changed"
            )
        input_payload = model_input.model_dump(mode="json")
        input_digest = _digest(input_payload)
        messages = list(self._messages)
        if self._last_input_digest is None:
            messages.extend(_initial_provider_messages(model_input))
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

    def estimate_next_request_tokens(
        self, model_input: TurnModelInputV3, *, finalize_only: bool
    ) -> int:
        return self._next_request(
            model_input, finalize_only=finalize_only
        )[4].input_tokens

    def estimate_next_request_tokens_unchecked(
        self, model_input: TurnModelInputV3, *, finalize_only: bool
    ) -> int:
        return self._next_request(
            model_input,
            finalize_only=finalize_only,
            enforce_limits=False,
        )[4].input_tokens

    def next_action(
        self,
        model_input: TurnModelInputV3,
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
        self._last_input_digest = input_digest
        self._provider_ordinal += 1
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                final_schema,
                invocation_purpose="turn_execution",
                subject_kind="turn_execution",
                subject_ref=self._execution_id,
                execution_key=f"{self._execution_id}:provider:{self._provider_ordinal}",
                prompt_digest=_digest([input_digest, finalize_only]),
                attempt_ordinal=self._provider_ordinal,
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
            try:
                action = FinalizeAnswerV1.model_validate(outcome.output)
            except ValidationError:
                self._messages.append(outcome.assistant_message)
                return ModelContractViolationV1(
                    safe_code="invalid_finalize_answer",
                    action_name="finalize_answer",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            self._messages.append(outcome.assistant_message)
            if not _within_capabilities(action, capabilities):
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
        self._discarded = True


class StrictProviderTurnModel(StrictTurnModel, DeepReasoningModel):
    def __init__(self, routing: ModelRoutingRuntime, *, record_invocations: bool = True) -> None:
        self._routing = routing
        self._record_invocations = record_invocations

    def open_session(self, model_input: TurnModelInputV3) -> ProviderTurnModelSession:
        return ProviderTurnModelSession(
            routing=self._routing,
            model_input=model_input,
            record_invocations=self._record_invocations,
        )

    def _reasoning_wire(
        self,
        model_input: TurnModelInputV3,
        *,
        purpose: str,
        payload: dict[str, object],
        schema_name: str,
        schema: dict[str, object],
        max_output_tokens: int,
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
        model_input: TurnModelInputV3,
        *,
        purpose: str,
        ordinal: int,
        payload: dict[str, object],
        schema_name: str,
        schema: dict[str, object],
        max_output_tokens: int,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> ProviderCompleted:
        attempt, request, response_schema, _estimate = self._reasoning_wire(
            model_input,
            purpose=purpose,
            payload=payload,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=max_output_tokens,
        )
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                response_schema,
                invocation_purpose=purpose,
                subject_kind="turn_execution",
                subject_ref=model_input.execution_id,
                execution_key=f"{model_input.execution_id}:{purpose}:{ordinal}",
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
    def _plan_payload(
        model_input: TurnModelInputV3, *, repair: bool
    ) -> dict[str, object]:
        return _plan_payload(model_input, repair=repair)

    def estimate_plan_request_tokens(
        self, model_input: TurnModelInputV3, *, repair: bool
    ) -> int:
        return self._reasoning_wire(
            model_input,
            purpose="deep_reasoning_plan",
            payload=self._plan_payload(model_input, repair=repair),
            schema_name="atlas_initial_plan_decision_v1",
            schema=_ProviderInitialPlanDecisionV1.model_json_schema(),
            max_output_tokens=4000,
        )[3].input_tokens

    def plan(
        self,
        model_input: TurnModelInputV3,
        *,
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1:
        outcome = self._invoke_reasoning(
            model_input,
            purpose="deep_reasoning_plan",
            ordinal=schema_retry_ordinal + 1,
            payload=self._plan_payload(model_input, repair=repair),
            schema_name="atlas_initial_plan_decision_v1",
            schema=_ProviderInitialPlanDecisionV1.model_json_schema(),
            max_output_tokens=4000,
            repair_origin_error_code=repair_origin_error_code,
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
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
    ) -> dict[str, object]:
        remaining = {
            "tool_invocations": max(
                0,
                model_input.policy.max_tool_invocations
                - model_input.budget.tool_invocations,
            ),
            "provider_invocations": max(
                0,
                model_input.policy.max_provider_invocations
                - model_input.budget.provider_invocations,
            ),
            "search_rounds": max(
                0,
                model_input.policy.max_search_rounds
                - model_input.budget.search_rounds,
            ),
            "model_visible_items": max(
                0,
                model_input.policy.max_model_visible_items_per_turn
                - model_input.budget.model_visible_items,
            ),
        }
        return _replan_payload(
            plan=plan,
            evaluation=evaluation,
            repair=repair,
            allowed_action_kinds=model_input.capabilities.allowed_actions,
            safe_counts=model_input.budget.model_dump(mode="json"),
            remaining_execution_limits=remaining,
        )

    @staticmethod
    def _replan_schema(plan: ReasoningPlanV2) -> dict[str, object]:
        return _replan_schema(plan)

    def estimate_replan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
    ) -> int:
        return self._reasoning_wire(
            model_input,
            purpose="deep_reasoning_replan",
            payload=self._replan_payload(
                model_input, plan=plan, evaluation=evaluation, repair=repair
            ),
            schema_name="atlas_replan_decision_v1",
            schema=self._replan_schema(plan),
            max_output_tokens=4000,
        )[3].input_tokens

    def replan(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1:
        outcome = self._invoke_reasoning(
            model_input,
            purpose="deep_reasoning_replan",
            ordinal=plan.generation * 10 + schema_retry_ordinal,
            payload=self._replan_payload(
                model_input, plan=plan, evaluation=evaluation, repair=repair
            ),
            schema_name="atlas_replan_decision_v1",
            schema=self._replan_schema(plan),
            max_output_tokens=4000,
            repair_origin_error_code=repair_origin_error_code,
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
        return session.estimate_next_request_tokens_unchecked(
            model_input, finalize_only=False
        )


__all__ = ["ProviderTurnModelSession", "StrictProviderTurnModel"]
