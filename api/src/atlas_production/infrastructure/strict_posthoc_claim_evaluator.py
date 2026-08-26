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
    ProviderOutputDecodeError,
    ProviderOutputSchemaError,
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
from atlas_production.modules.turn_runtime.public import (
    ClaimSchemaRetryV1,
    RequestModelActionV1,
    SchemaRetryOriginCode,
    TurnRouteSnapshotV2,
    TurnRuntimeBudgetExceeded,
    TurnRuntimeOwner,
)
from atlas_production.providers import ProviderError, build_native_json_schema


logger = logging.getLogger(__name__)


class _ProviderAnswerAssessmentDecisionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_outcomes: list[Literal["aligned", "conflict", "insufficient"]]


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
    """Use the execution-fixed tested route without tools or transport retry."""

    def __init__(
        self,
        routing: ModelRoutingRuntime,
        runtime: TurnRuntimeOwner | None = None,
        *,
        record_invocations: bool = True,
    ) -> None:
        self._routing = routing
        self._runtime = runtime
        self._record_invocations = record_invocations

    def _claim_schema_retry(
        self,
        *,
        execution_id: str,
        assessment_ordinal: int,
        attempt_ordinal: int,
        origin_error_code: SchemaRetryOriginCode,
    ) -> bool:
        if self._runtime is None:
            return False
        snapshot = self._runtime.snapshot(execution_id)
        try:
            claimed = self._runtime.claim_schema_retry(
                ClaimSchemaRetryV1(
                    execution_id=execution_id,
                    fencing_token=snapshot.lease.fencing_token,
                    claim_key=(
                        "provisional_evidence_assessment:"
                        f"{assessment_ordinal}:schema-retry:{attempt_ordinal}"
                    ),
                    origin_error_code=origin_error_code,
                )
            )
            self._runtime.request_model_action(
                RequestModelActionV1(
                    execution_id=execution_id,
                    expected_version=claimed.version,
                    fencing_token=claimed.lease.fencing_token,
                    context_tokens=0,
                )
            )
        except TurnRuntimeBudgetExceeded:
            return False
        return True

    def _invoke_decision(
        self,
        *,
        execution_id: str,
        assessment_ordinal: int,
        assessment_input_digest: str,
        answer_count: int,
        attempt,
        request: ProviderConversationRequest,
        schema,
    ):
        schema_retry_attempt = 0
        repair_origin: SchemaRetryOriginCode | None = None
        while True:
            provider_attempt_ordinal = assessment_ordinal * 10 + schema_retry_attempt
            handle = None
            if self._record_invocations:
                handle = self._routing.prepare_invocation(
                    attempt.route,
                    schema,
                    invocation_purpose="provisional_evidence_assessment",
                    subject_kind="turn_execution",
                    subject_ref=execution_id,
                    execution_key=(
                        f"{execution_id}:provisional-evidence:{assessment_ordinal}:"
                        f"{schema_retry_attempt}"
                    ),
                    prompt_digest=assessment_input_digest,
                    attempt_ordinal=provider_attempt_ordinal,
                    repair_origin_error_codes=(
                        [] if repair_origin is None else [repair_origin]
                    ),
                )
                self._routing.record_invocation_started(handle)
            try:
                outcome = self._routing.invoke(attempt, request, schema)
            except ProviderInvocationError as error:
                if handle is not None:
                    self._routing.record_invocation_failure(handle, error.safe_code)
                if isinstance(
                    error, (ProviderOutputDecodeError, ProviderOutputSchemaError)
                ):
                    origin: SchemaRetryOriginCode = error.safe_code
                    if self._claim_schema_retry(
                        execution_id=execution_id,
                        assessment_ordinal=assessment_ordinal,
                        attempt_ordinal=schema_retry_attempt + 1,
                        origin_error_code=origin,
                    ):
                        repair_origin = origin
                        schema_retry_attempt += 1
                        continue
                reason_code: AssessmentReasonCodeV2 = (
                    "provider_timeout"
                    if "timeout" in error.safe_code
                    else "provider_failed"
                )
                raise ClaimAssessmentUnavailable(
                    "provisional evidence assessment provider failed",
                    reason_code=reason_code,
                ) from error
            if not isinstance(outcome, ProviderCompleted):
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
            if handle is not None:
                self._routing.record_invocation_success(handle, dict(outcome.usage))
            try:
                decision = _ProviderAnswerAssessmentDecisionV2.model_validate(
                    outcome.output
                )
                if len(decision.item_outcomes) != answer_count:
                    raise ValueError("provisional evidence item count is invalid")
            except (ValidationError, ValueError) as error:
                origin = (
                    "provisional_evidence_semantic_shape_invalid"
                    if isinstance(error, ValidationError)
                    else "provisional_evidence_item_count_invalid"
                )
                logger.warning(
                    "provisional evidence output rejected execution_id=%s safe_code=%s",
                    execution_id,
                    origin,
                )
                if self._claim_schema_retry(
                    execution_id=execution_id,
                    assessment_ordinal=assessment_ordinal,
                    attempt_ordinal=schema_retry_attempt + 1,
                    origin_error_code=origin,
                ):
                    repair_origin = origin
                    schema_retry_attempt += 1
                    continue
                raise ClaimAssessmentUnavailable(
                    "provisional evidence assessment output is invalid",
                    reason_code="invalid_output",
                ) from error
            return decision, outcome, handle

    def assess(
        self,
        *,
        execution_id: str,
        finalized_answer: FinalizedAnswerV1,
        declared_evidence_subset: DeclaredEvidenceSubsetV1,
        deadline_at: datetime,
        route: TurnRouteSnapshotV2,
        assessment_ordinal: int = 1,
        evidence_handles_by_segment: dict[str, list[str]] | None = None,
    ) -> PostHocAnswerAssessmentResultV2:
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise ClaimAssessmentUnavailable(
                "claim assessment deadline elapsed", reason_code="deadline_elapsed"
            )
        selected_route = route
        if declared_evidence_subset.visual_images:
            if route.vision_route is None:
                raise ClaimAssessmentUnavailable(
                    "vision claim assessment route is unavailable",
                    reason_code="route_unavailable",
                )
            selected_route = route.vision_route
        try:
            attempt = self._routing.open_tested_attempt(selected_route.route_id)
        except ProviderError as error:
            raise ClaimAssessmentUnavailable(
                "claim assessment route is unavailable",
                reason_code="route_unavailable",
            ) from error
        if (
            selected_route is not route
            and not getattr(attempt.route, "supports_vision", False)
        ):
            raise ClaimAssessmentUnavailable(
                "vision claim assessment route is unavailable",
                reason_code="route_unavailable",
            )
        policy = attempt.route.runtime_policy
        if (
            attempt.route.route_id != selected_route.route_id
            or attempt.route.revision != selected_route.route_revision
            or policy.revision != selected_route.runtime_policy_revision
            or policy.tokenizer_profile != selected_route.tokenizer_profile
            or policy.context_window_tokens != selected_route.context_window_tokens
            or policy.max_input_tokens_per_invocation
            != selected_route.max_input_tokens_per_invocation
            or policy.max_output_tokens_per_invocation
            != selected_route.max_output_tokens_per_invocation
            or policy.max_tool_result_tokens_per_execution
            != selected_route.max_tool_result_tokens_per_execution
            or policy.max_total_tokens_per_conversation
            != selected_route.max_total_tokens_per_conversation
        ):
            raise ProviderProtocolError(
                safe_code="model_route_revision_conflict"
            )
        answer_ids = [
            segment.segment_id for segment in finalized_answer.segments
        ]
        known_handles = {
            item.evidence_handle for item in declared_evidence_subset.items
        }
        if evidence_handles_by_segment is not None:
            if set(evidence_handles_by_segment) != set(answer_ids):
                raise ClaimAssessmentUnavailable(
                    "research finding evidence mapping is incomplete",
                    reason_code="invalid_output",
                )
            if any(
                len(handles) != len(set(handles))
                or not set(handles).issubset(known_handles)
                for handles in evidence_handles_by_segment.values()
            ):
                raise ClaimAssessmentUnavailable(
                    "research finding evidence mapping is invalid",
                    reason_code="invalid_output",
                )
        answer_items = [
            {"id": segment.segment_id, "text": segment.text}
            for segment in finalized_answer.segments
        ]
        if evidence_handles_by_segment is not None:
            for item in answer_items:
                item["evidence_handles"] = evidence_handles_by_segment[str(item["id"])]
        payload = {
            "answer_items": answer_items,
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
            "For every answer item, evaluate both evidence alignment and evidence "
            "coverage. Determine whether every material, externally verifiable domain "
            "claim is consistent with and reasonably supported by the provided evidence, "
            "without materially exceeding that evidence. Do not exempt a claim merely "
            "because it sounds familiar, plausible, or like common knowledge.\n\n"
            "Mark an answer item as aligned only when:\n"
            "1. Its material factual content is explicit in the evidence or is a "
            "faithful paraphrase, summary, comparison, or direct grounded conclusion "
            "from that evidence.\n"
            "2. Any inference has every material premise supported, follows plainly "
            "from those premises, is distinguished from directly documented fact when "
            "that distinction matters, preserves the evidence's conditions and degree "
            "of certainty, and does not replace an authoritative decision that remains "
            "unresolved. Such a grounded inference may be aligned even when the evidence "
            "does not state the exact conclusion verbatim.\n"
            "3. Non-material conversational framing, restating the question, or naming "
            "the adopted referent does not by itself make the item fail.\n\n"
            "Evaluate every material claim independently. Evidence supports a claim "
            "only when it matches all material dimensions of that claim, including "
            "the applicable entity, component or attribute, operating mode or "
            "interface, condition, scope or quantifier, polarity, value, and degree "
            "of certainty.\n"
            "Do not treat related but different entities, components, attributes, "
            "modes, interfaces, conditions, documents, or measurement contexts as "
            "interchangeable. The same value or terminology appearing in different "
            "contexts does not establish equivalence.\n"
            "A comparative, combined, or universal claim requires evidence for every "
            "material member and every stated dimension. Partial support for some "
            "members or conditions is insufficient for the broader claim.\n"
            "Material product-specific facts, values, limits, compatibility claims, "
            "risk or causal claims, pass/fail judgments, comparisons, selections, and "
            "operational recommendations require evidence coverage. A related citation "
            "does not support a conclusion unless it supports the material premises and "
            "the inferential step.\n"
            "Evaluate each claim as written. A qualification, caveat, or narrower "
            "statement elsewhere in the answer does not repair an unsupported or "
            "overbroad claim. A request to confirm later does not make a disputed value, "
            "configuration, recommendation, or decision usable now.\n\n"
            "Mark an answer item as conflict when its material factual content "
            "contradicts the evidence, applies the evidence to the wrong subject or "
            "referent, fails to disclose a visible material conflict, or operationalizes "
            "one side of a visible unresolved conflict as an instruction, recommendation, "
            "selected configuration, or conditionally acceptable option.\n"
            "Mark an answer item as insufficient when there is no material conflict, "
            "but it adds a material fact, number, entity, relationship, causal claim, "
            "judgment, recommendation, or other evidence-required claim with no supporting "
            "evidence; a material premise or inferential step is unsupported; the conclusion "
            "asserts materially stronger scope or certainty; or an inference substitutes "
            "for an unresolved authoritative decision.\n\n"
            "An answer item containing no factual statement may be marked aligned.\n\n"
            "Use only the supplied evidence_items.\n"
            "Do not search for additional evidence.\n"
            "Do not use outside knowledge.\n"
            "Do not require verbatim wording or fail merely because the evidence does "
            "not contain the same sentence. Distinguish reasonable evidence-backed "
            "inference from unsupported speculation.\n"
            "Do not correct, rewrite, explain, or expand the answer.\n"
            "Do not assess whether the answer fully addresses the user's request.\n\n"
            "Return exactly one item_outcomes entry for every answer item.\n"
            "Preserve the input order.\n"
            "Do not return or reproduce answer item ids. Runtime owns identity mapping.\n"
            "Do not return explanations or additional text.\n\n"
            "The provider-native JSON Schema is the sole output-format authority."
        )
        if evidence_handles_by_segment is not None:
            system += (
                "\nEach answer item includes evidence_handles. Evaluate that item "
                "using only its listed evidence handles, even when other evidence_items "
                "are present for another item. An empty list cannot be evidence-aligned "
                "for a material factual claim.\n"
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
            application_schema = _ProviderAnswerAssessmentDecisionV2.model_json_schema()
            schema = build_native_json_schema(
                "provisional_declared_evidence_decision_v3",
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
        decision, outcome, handle = self._invoke_decision(
            execution_id=execution_id,
            assessment_ordinal=assessment_ordinal,
            assessment_input_digest=assessment_input_digest,
            answer_count=len(answer_ids),
            attempt=attempt,
            request=request,
            schema=schema,
        )
        if "conflict" in decision.item_outcomes:
            consistency: Literal["aligned", "conflict", "insufficient"] = "conflict"
        elif "insufficient" in decision.item_outcomes:
            consistency = "insufficient"
        else:
            consistency = "aligned"
        result = PostHocAnswerAssessmentEnvelopeV2(
            consistency=consistency,
            results=[
                PostHocAnswerAssessmentV2(
                    id=answer_id,
                    status="success" if item_outcome == "aligned" else "failure",
                    internal_consistency=(
                        item_outcome
                        if evidence_handles_by_segment is not None
                        else None
                    ),
                )
                for answer_id, item_outcome in zip(
                    answer_ids, decision.item_outcomes, strict=True
                )
            ],
        )
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


__all__ = ["ClaimAssessmentUnavailable", "StrictPostHocClaimEvaluator"]
