from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas_production.infrastructure.history_authority import (
    HISTORY_AUTHORITY_POLICY,
    history_exchange_payload,
    history_summary_payload,
)
from atlas_production.infrastructure.strict_turn_model_messages import _canonical
from atlas_production.modules.model_routing.public import (
    ProviderConversationRequest,
    ProviderSystemMessage,
    ProviderUserMessage,
)
from atlas_production.modules.turn_execution.public import (
    FinalizeAnswerV1,
    TurnModelInputV3,
)
from atlas_production.modules.turn_runtime.public import (
    ReasoningEvaluationV1,
    ReasoningPlanV2,
)
from atlas_production.providers import build_native_json_schema


def _build_reasoning_wire(
    *,
    purpose: str,
    payload: dict[str, object],
    schema_name: str,
    schema: dict[str, object],
    max_output_tokens: int,
):
    response_schema = build_native_json_schema(schema_name, schema)
    request = ProviderConversationRequest(
        messages=[
            ProviderSystemMessage(
                content=_canonical(
                    {
                        "atlas_deep_reasoning_contract": {
                            "purpose": purpose,
                            "structured_process_only": True,
                            "provider_reasoning_forbidden": True,
                            "accuracy_or_confidence_claim_forbidden": True,
                        }
                    }
                )
            ),
            ProviderUserMessage(content=_canonical(payload)),
        ],
        tools=[],
        tool_choice="none",
        parallel_tool_calls=False,
        max_output_tokens=max_output_tokens,
    )
    return request, response_schema

class _ProviderInitialPlanDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_objective: str
    completion_condition: str
    item_summaries: list[str]


class _ProviderReplanDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_objective: str
    completion_condition: str
    completed_item_ids: list[str]
    skipped_item_ids: list[str]
    new_item_summaries: list[str]


class _ProviderProcessRubricDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_coverage: Literal[0, 1, 2]
    evidence_handling: Literal[0, 1, 2]
    conflict_handling: Literal[0, 1, 2]
    gap_resolution: Literal[0, 1, 2]
    revision_completion: Literal[0, 1, 2]


class _ProviderProcessEvaluationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["accept", "revise_only", "research_then_revise"]
    summary: str = Field(min_length=1, max_length=240)
    rubric_dimensions: _ProviderProcessRubricDecisionV1

def _usage_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _bounded_plan_text(value: str, *, max_length: int) -> str:
    text = value.strip()
    if not text:
        raise ValueError("plan text must be non-empty")
    if len(text) > max_length:
        text = text[:max_length].rstrip()
    if not text:
        raise ValueError("bounded plan text must be non-empty")
    return text


def _bounded_plan_summaries(values: list[str], *, limit: int) -> list[str]:
    summaries: list[str] = []
    for value in values:
        if len(summaries) >= limit:
            break
        if not value.strip():
            continue
        summaries.append(_bounded_plan_text(value, max_length=120))
    return summaries


def _with_schema_repair_instruction(instruction: str, *, repair: bool) -> str:
    if not repair:
        return instruction
    return (
        instruction
        + " The previous response violated the required JSON or schema contract. "
        "Return exactly one JSON object that matches the supplied response schema; "
        "do not add Markdown fences, commentary, prefixes, or suffixes."
    )


def _next_runtime_plan_item_id(
    *, generation: int, used_item_ids: set[str], ordinal: int
) -> str:
    candidate_ordinal = ordinal
    while True:
        candidate = f"g{generation}-item-{candidate_ordinal:02d}"
        if candidate not in used_item_ids:
            return candidate
        candidate_ordinal += 1

def _plan_payload(
    model_input: TurnModelInputV3, *, repair: bool
) -> dict[str, object]:
    return {
        "instruction": _with_schema_repair_instruction(
            "Return only a bounded next_objective of at most 160 characters, a "
            "completion_condition of at most 160 characters, and item_summaries "
            "containing 1 to 8 concise observable work steps of at most 120 characters "
            "each. Runtime owns generation, parent linkage, item IDs and status. Do not "
            "include hidden reasoning, draft answer text, evidence snippets, confidence, "
            "or accuracy claims.",
            repair=repair,
        ),
        "schema_repair": repair,
        "current_user_request": model_input.model_user_input,
        "history_authority_policy": HISTORY_AUTHORITY_POLICY,
        "history_summary": (
            None
            if model_input.summary is None
            else history_summary_payload(
                historical_user_context=(
                    model_input.summary.historical_user_context
                ),
                assistant_pending_verification_context=(
                    model_input.summary.assistant_pending_verification_context
                ),
            )
        ),
        "recent_history": [
            history_exchange_payload(
                user_text=item.user_text,
                assistant_text=item.assistant_text,
            )
            for item in model_input.recent_tail
        ],
        "catalog_document_count": model_input.catalog_document_count,
        "allowed_actions": model_input.capabilities.allowed_actions,
    }

def _replan_payload(
    *,
    plan: ReasoningPlanV2,
    evaluation: ReasoningEvaluationV1,
    repair: bool,
    allowed_action_kinds: list[str],
    safe_counts: dict[str, object],
    remaining_execution_limits: dict[str, int],
) -> dict[str, object]:
    return {
        "instruction": _with_schema_repair_instruction(
            "Return only a bounded next_objective and completion_condition, each at "
            "most 160 characters; completed_item_ids and skipped_item_ids selected "
            "only from currently pending item IDs; and concise new_item_summaries of "
            "at most 120 characters each. Omit an unchanged pending item from both ID "
            "lists and Runtime will retain it. Runtime owns generation, parent linkage, "
            "new item IDs and pending status. Do not include draft text, evidence "
            "excerpts, opaque handles, provider reasoning, confidence, or accuracy claims.",
            repair=repair,
        ),
        "schema_repair": repair,
        "current_plan": plan.model_dump(mode="json"),
        "evaluator_finding": {
            "cycle": evaluation.cycle,
            "verdict": evaluation.verdict,
            "finding_codes": evaluation.finding_codes,
            "summary": evaluation.summary,
        },
        "allowed_action_kinds": allowed_action_kinds,
        "safe_counts": safe_counts,
        "remaining_execution_limits": remaining_execution_limits,
    }

def _replan_schema(plan: ReasoningPlanV2) -> dict[str, object]:
    schema = _ProviderReplanDecisionV1.model_json_schema()
    pending_item_ids = [
        item.item_id for item in plan.items if item.status == "pending"
    ]
    if pending_item_ids:
        for field_name in ("completed_item_ids", "skipped_item_ids"):
            schema["properties"][field_name]["items"]["enum"] = pending_item_ids
    return schema

def _evaluation_payload(
    model_input: TurnModelInputV3,
    *,
    plan: ReasoningPlanV2,
    proposal: FinalizeAnswerV1,
    observation_payloads: list[dict[str, object]],
    cycle: int,
) -> dict[str, object]:
    return {
        "history_authority_policy": HISTORY_AUTHORITY_POLICY,
        "historical_context": {
            "summary": (
                None
                if model_input.summary is None
                else history_summary_payload(
                    historical_user_context=(
                        model_input.summary.historical_user_context
                    ),
                    assistant_pending_verification_context=(
                        model_input.summary.assistant_pending_verification_context
                    ),
                )
            ),
            "recent_exchanges": [
                history_exchange_payload(
                    user_text=item.user_text,
                    assistant_text=item.assistant_text,
                )
                for item in model_input.recent_tail
            ],
        },
        "instruction": (
            "Evaluate only process quality using the supplied rubric. Return one "
            "0, 1, or 2 judgment for each rubric dimension and a concise remediation "
            "summary of 1 to 240 Unicode characters. Do not restate the candidate "
            "in the summary. On cycle 1, return 2 for revision_completion because "
            "there is no prior requested revision. Runtime derives finding codes "
            "and the total and owns cycle and rubric metadata. This is not accuracy "
            "or confidence. Treat facts and values explicitly supplied in the current "
            "user request as task premises unless the request asks to verify them. "
            "Deterministic arithmetic or logical derivations from those premises do not "
            "require separate retrieved evidence. Historical assistant content is "
            "pending verification and is not factual evidence: identify a history-source "
            "gap only when the candidate materially reuses a factual claim sourced only "
            "from pending assistant history and no current authorized evidence supports "
            "it. Do not turn dialogue continuity, referent resolution, or a supported "
            "direct calculation into an evidence gap. Other current evidence rules remain "
            "unchanged. A caveat or disclaimer does not resolve a decisive evidence gap. "
            "For a comparison or selection, evidence_handling and gap_resolution "
            "are fully satisfied only when every material candidate is supported on the "
            "decisive criterion. Treat an unsupported secondary ranking, preference, "
            "recommendation, or tradeoff as a defect. Return research_then_revise when "
            "legal retrieval could close a decisive gap; return revise_only when the safe "
            "correction is to remove an unsupported extension; return accept only when "
            "neither defect remains. Conflict disclosure alone is not conflict "
            "resolution. When observations contain mutually exclusive, physically "
            "impossible, or authority-uncertain claims, the candidate must not "
            "operationalize any conflicting claim as an instruction, recommendation, "
            "selected configuration, or conditionally acceptable option. Score "
            "conflict_handling as 2 only when every material conflict is resolved by "
            "adequate authority or preserved as an explicit non-operational blocking "
            "open question; score 1 when the conflict is mentioned but its affected "
            "decision, risk, required authority, or blocking consequence is incomplete; "
            "score 0 when a conflicting claim is ignored, selected, normalized, or "
            "operationalized. A request to confirm later does not resolve a gap when the "
            "candidate still supplies the disputed value or instruction. Return "
            "research_then_revise when legal retrieval could obtain the authoritative "
            "evidence needed to resolve a material conflict; return revise_only when the "
            "safe correction is to remove the operational instruction and preserve a "
            "blocking open question; return accept only when no unresolved material "
            "conflict has been operationalized. Treat visual inspection as a required "
            "process step whenever the user request, plan, or candidate conclusion "
            "materially depends on figures, diagrams, images, shapes, visual labels, "
            "relative positions, page layout, or visually encoded tables. Text "
            "extraction, snippets, captions, and page handles do not prove that visual "
            "inspection occurred. Require a visual_inspection_result for every material "
            "visual target, including every side of a comparison. If a required target "
            "was not visually inspected and legal retrieval can still inspect it, return "
            "research_then_revise; return revise_only only when the candidate can safely "
            "remove the visually dependent conclusion and still answer the request; do "
            "not return accept while a required visual inspection is missing."
        ),
        "cycle": cycle,
        "user_request": model_input.model_user_input,
        "plan": plan.model_dump(mode="json"),
        "candidate": proposal.model_dump(mode="json"),
        "tool_observations": observation_payloads,
        "rubric": {
            "plan_coverage": "Did the candidate address the planned work?",
            "evidence_handling": "Were retrieved materials used and declared coherently?",
            "conflict_handling": (
                "Were visible conflicts resolved by adequate authority or preserved "
                "as explicit non-operational blocking open questions?"
            ),
            "gap_resolution": (
                "Were material gaps resolved without leaving disputed values or "
                "instructions operational?"
            ),
            "revision_completion": "Were prior requested changes completed?",
        },
    }

def _evaluation_schema() -> dict[str, object]:
    return _ProviderProcessEvaluationV1.model_json_schema()
