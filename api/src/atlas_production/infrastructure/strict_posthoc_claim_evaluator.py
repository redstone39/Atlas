"""One-shot answer-item evidence-alignment assessment."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from atlas_production.modules.model_routing.public import (
    ModelRoutingRuntime,
    ProviderCompleted,
    ProviderConversationRequest,
    ProviderIncomplete,
    ProviderImageContentPart,
    ProviderInvocationError,
    ProviderRefused,
    ProviderSystemMessage,
    ProviderTextContentPart,
    ProviderUserMessage,
    ProviderProtocolError,
    require_provider_wire_within_limits,
)
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    FinalizedAnswerV1,
    PostHocAnswerAssessmentEnvelopeV2,
    PostHocAnswerAssessmentV2,
    PostHocAnswerAssessmentResultV2,
    PostHocAnswerEvaluatorV2,
)
from atlas_production.modules.retrieval.public import (
    DeclaredEvidenceSubsetV1,
)
from atlas_production.modules.turn_runtime.public import TurnRouteSnapshotV2
from atlas_production.providers import ProviderError, build_native_json_schema


logger = logging.getLogger(__name__)


class _ProviderAnswerAssessmentDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consistency: Literal["aligned", "conflict", "insufficient"]
    item_statuses: list[Literal["success", "failure"]]


class ClaimAssessmentUnavailable(RuntimeError):
    """The isolated assessment could not produce safe, strict output."""

    def __init__(self, message: str, *, reason_code: AssessmentReasonCodeV2) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class StrictPostHocClaimEvaluator(PostHocAnswerEvaluatorV2):
    """Use the execution-fixed tested route exactly once, without tools or retry."""

    def __init__(
        self, routing: ModelRoutingRuntime, *, record_invocations: bool = True
    ) -> None:
        self._routing = routing
        self._record_invocations = record_invocations

    def assess(
        self,
        *,
        execution_id: str,
        finalized_answer: FinalizedAnswerV1,
        declared_evidence_subset: DeclaredEvidenceSubsetV1,
        deadline_at: datetime,
        route: TurnRouteSnapshotV2,
        assessment_ordinal: int = 1,
    ) -> PostHocAnswerAssessmentResultV2:
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise ClaimAssessmentUnavailable(
                "claim assessment deadline elapsed", reason_code="deadline_elapsed"
            )
        try:
            attempt = self._routing.open_tested_attempt(route.route_id)
        except ProviderError as error:
            raise ClaimAssessmentUnavailable(
                "claim assessment route is unavailable",
                reason_code="route_unavailable",
            ) from error
        policy = attempt.route.runtime_policy
        if (
            attempt.route.route_id != route.route_id
            or attempt.route.revision != route.route_revision
            or policy.revision != route.runtime_policy_revision
            or policy.tokenizer_profile != route.tokenizer_profile
            or policy.context_window_tokens != route.context_window_tokens
            or policy.max_input_tokens_per_invocation
            != route.max_input_tokens_per_invocation
            or policy.max_output_tokens_per_invocation
            != route.max_output_tokens_per_invocation
            or policy.max_tool_result_tokens_per_execution
            != route.max_tool_result_tokens_per_execution
            or policy.max_total_tokens_per_conversation
            != route.max_total_tokens_per_conversation
        ):
            raise ProviderProtocolError(
                safe_code="model_route_revision_conflict"
            )
        answer_ids = [
            segment.segment_id for segment in finalized_answer.segments
        ]
        payload = {
            "answer_items": [
                {"id": segment.segment_id, "text": segment.text}
                for segment in finalized_answer.segments
            ],
            "evidence_items": [
                {
                    "id": item.evidence_handle,
                    "content": "\n\n".join(
                        observation.model_visible_content
                        for observation in item.observations
                    ),
                }
                for item in declared_evidence_subset.items
            ],
        }
        answer_digest = _digest(finalized_answer.model_dump(mode="json"))
        visual_image_digests = [
            image.image_digest for image in declared_evidence_subset.visual_images
        ]
        if visual_image_digests and not getattr(attempt.route, "supports_vision", False):
            raise ClaimAssessmentUnavailable(
                "claim assessment route does not support visual evidence",
                reason_code="physical_limit_rejected",
            )
        assessment_input_digest = _digest(
            {
                "payload": payload,
                "visual_image_digests": visual_image_digests,
            }
        )
        system = (
            "You are a soft evidence-alignment assessor.\n\n"
            "The input contains:\n"
            "- answer_items: answer units identified by id and text.\n"
            "- evidence_items: the only evidence you may use.\n\n"
            "For every answer item, determine whether its material factual content is "
            "consistent with and reasonably supported by the provided evidence, "
            "without materially exceeding that evidence.\n\n"
            "Mark an answer item as success only when:\n"
            "1. Its material factual content is explicit in the evidence or is a "
            "faithful paraphrase, summary, comparison, or direct grounded conclusion "
            "from that evidence.\n"
            "2. Any conclusion follows plainly from the evidence without adding a "
            "material new fact or relying on outside knowledge or speculative "
            "inference.\n"
            "3. Non-material conversational framing, restating the question, or naming "
            "the adopted referent does not by itself make the item fail.\n\n"
            "Mark it as failure when:\n"
            "1. Its material factual content conflicts with the evidence.\n"
            "2. It adds a material fact, number, entity, relationship, causal claim, "
            "or materially stronger certainty that the evidence cannot reasonably "
            "support.\n"
            "3. It applies the evidence to the wrong subject or referent.\n"
            "4. The evidence is insufficient to reasonably support a material "
            "conclusion without outside knowledge or speculative inference.\n\n"
            "Set the answer-level consistency to conflict when any declared evidence "
            "materially contradicts the answer, is applied to the wrong subject, or a "
            "visible material conflict is not disclosed.\n"
            "Set it to insufficient when there is no material conflict but the answer "
            "exceeds the supplied evidence or depends on outside knowledge or "
            "speculative inference.\n"
            "Set it to aligned only when all material factual content is reasonably "
            "supported and visible material conflicts are disclosed.\n\n"
            "An answer item containing no factual statement may be marked success.\n\n"
            "Use only the supplied evidence_items.\n"
            "Do not search for additional evidence.\n"
            "Do not use outside knowledge.\n"
            "Do not require verbatim wording or fail merely because the evidence does "
            "not contain the same sentence. Distinguish reasonable direct synthesis "
            "from unsupported speculation.\n"
            "Do not correct, rewrite, explain, or expand the answer.\n"
            "Do not assess whether the answer fully addresses the user's request.\n\n"
            "Return exactly one item_statuses entry for every answer item.\n"
            "Preserve the input order.\n"
            "Do not return or reproduce answer item ids. Runtime owns identity mapping.\n"
            "Do not return explanations or additional text.\n\n"
            "The provider-native JSON Schema is the sole output-format authority."
        )
        messages = [
            ProviderSystemMessage(content=system),
            ProviderUserMessage(content=_canonical(payload)),
        ]
        messages.extend(
            ProviderUserMessage(
                content=(
                    ProviderTextContentPart(
                        text=(
                            "Exact visual evidence for handle "
                            f"{image.visual_handle}. Assess only what is visible "
                            "in this image."
                        )
                    ),
                    ProviderImageContentPart(
                        content=image.content,
                        digest=image.image_digest,
                        width=image.width,
                        height=image.height,
                    ),
                )
            )
            for image in declared_evidence_subset.visual_images
        )
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise ClaimAssessmentUnavailable(
                "claim assessment deadline elapsed", reason_code="deadline_elapsed"
            )
        try:
            application_schema = _ProviderAnswerAssessmentDecisionV1.model_json_schema()
            schema = build_native_json_schema(
                "provisional_declared_evidence_decision_v2",
                application_schema,
            )
            request = ProviderConversationRequest(
                messages=messages,
                tools=[],
                tool_choice="none",
                parallel_tool_calls=False,
                max_output_tokens=policy.max_output_tokens_per_invocation,
                timeout_seconds=min(
                    float(policy.provider_invocation_timeout_seconds), remaining
                ),
            )
        except (ProviderError, TypeError, ValueError) as error:
            raise ClaimAssessmentUnavailable(
                "provisional evidence assessment provider contract is unavailable",
                reason_code="provider_contract_unavailable",
            ) from error
        try:
            require_provider_wire_within_limits(
                policy=policy,
                request=request,
                response_schema=schema,
            )
        except ProviderProtocolError as error:
            raise ClaimAssessmentUnavailable(
                "provisional evidence assessment exceeds execution route physical limits",
                reason_code="physical_limit_rejected",
            ) from error
        except ValueError as error:
            raise ClaimAssessmentUnavailable(
                "provisional evidence assessment tokenizer is unavailable",
                reason_code="tokenizer_unavailable",
            ) from error
        handle = None
        if self._record_invocations:
            handle = self._routing.prepare_invocation(
                attempt.route,
                schema,
                invocation_purpose="provisional_evidence_assessment",
                subject_kind="turn_execution",
                subject_ref=execution_id,
                execution_key=f"{execution_id}:provisional-evidence:{assessment_ordinal}",
                prompt_digest=assessment_input_digest,
                attempt_ordinal=assessment_ordinal,
            )
            self._routing.record_invocation_started(handle)
        try:
            outcome = self._routing.invoke(attempt, request, schema)
        except ProviderInvocationError as error:
            if handle is not None:
                self._routing.record_invocation_failure(
                    handle,
                    getattr(error, "safe_code", "provisional_evidence_assessment_failed"),
                )
            reason_code: AssessmentReasonCodeV2 = (
                "provider_timeout"
                if "timeout" in getattr(error, "safe_code", "")
                else "provider_failed"
            )
            raise ClaimAssessmentUnavailable(
                "provisional evidence assessment provider failed", reason_code=reason_code
            ) from error
        if isinstance(outcome, ProviderCompleted):
            try:
                decision = _ProviderAnswerAssessmentDecisionV1.model_validate(
                    outcome.output
                )
            except ValidationError as error:
                safe_code = "provisional_evidence_semantic_shape_invalid"
                if handle is not None:
                    self._routing.record_invocation_failure(
                        handle, safe_code
                    )
                logger.warning(
                    "provisional evidence output rejected execution_id=%s safe_code=%s",
                    execution_id,
                    safe_code,
                )
                raise ClaimAssessmentUnavailable(
                    "provisional evidence assessment output is invalid",
                    reason_code="invalid_output",
                ) from error
            if len(decision.item_statuses) != len(answer_ids):
                safe_code = "provisional_evidence_item_count_invalid"
                if handle is not None:
                    self._routing.record_invocation_failure(
                        handle, safe_code
                    )
                logger.warning(
                    "provisional evidence output rejected execution_id=%s safe_code=%s",
                    execution_id,
                    safe_code,
                )
                raise ClaimAssessmentUnavailable(
                    "provisional evidence assessment item count is invalid",
                    reason_code="invalid_output",
                )
            try:
                result = PostHocAnswerAssessmentEnvelopeV2(
                    consistency=decision.consistency,
                    results=[
                        PostHocAnswerAssessmentV2(id=answer_id, status=status)
                        for answer_id, status in zip(
                            answer_ids, decision.item_statuses, strict=True
                        )
                    ],
                )
            except (ValidationError, ValueError) as error:
                safe_code = "provisional_evidence_consistency_invalid"
                if handle is not None:
                    self._routing.record_invocation_failure(handle, safe_code)
                logger.warning(
                    "provisional evidence output rejected execution_id=%s safe_code=%s",
                    execution_id,
                    safe_code,
                )
                raise ClaimAssessmentUnavailable(
                    "provisional evidence assessment consistency is invalid",
                    reason_code="invalid_output",
                ) from error
            if handle is not None:
                self._routing.record_invocation_success(handle, dict(outcome.usage))
            return PostHocAnswerAssessmentResultV2(
                state="completed",
                consistency=result.consistency,
                reason_code=(
                    "aligned"
                    if result.consistency == "aligned"
                    else f"declared_evidence_{result.consistency}"
                ),
                answer_digest=answer_digest,
                declared_subset_digest=declared_evidence_subset.digest,
                visual_image_digests=visual_image_digests,
                results=result.results,
                assessment_input_digest=assessment_input_digest,
                assessment_output_digest=_digest(result.model_dump(mode="json")),
            )
        if handle is not None:
            self._routing.record_invocation_failure(
                handle,
                "provisional_evidence_assessment_refused"
                if isinstance(outcome, ProviderRefused)
                else "provisional_evidence_assessment_incomplete",
            )
        if isinstance(outcome, (ProviderRefused, ProviderIncomplete)):
            raise ClaimAssessmentUnavailable(
                "provisional evidence assessment did not complete",
                reason_code=(
                    "provider_refused"
                    if isinstance(outcome, ProviderRefused)
                    else "provider_incomplete"
                ),
            )
        raise ClaimAssessmentUnavailable(
            "provisional evidence assessment provider outcome is invalid",
            reason_code="provider_failed",
        )


__all__ = ["ClaimAssessmentUnavailable", "StrictPostHocClaimEvaluator"]
