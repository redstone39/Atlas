from __future__ import annotations

import json
from collections.abc import Sequence

from atlas_production.modules.prompt_skills.public import (
    PromptSkillInstructionsV1,
    PromptSkillRefV1,
    PromptSkillSelectorCandidateV1,
)
from atlas_production.modules.turn_runtime.public import (
    ExecutionPromptSkillSelectionTraceV1,
    PromptSkillSelectionFallbackCode,
)


class PromptSkillSelectionResolutionError(ValueError):
    def __init__(self, fallback_code: PromptSkillSelectionFallbackCode) -> None:
        super().__init__(fallback_code)
        self.fallback_code = fallback_code


def resolve_selected_skill_refs(
    candidates: Sequence[PromptSkillSelectorCandidateV1],
    selected_ids: Sequence[str],
) -> tuple[PromptSkillRefV1, ...]:
    if len(selected_ids) != len(set(selected_ids)):
        raise PromptSkillSelectionResolutionError("selector_contract_invalid")
    offered = {candidate.selection_id: candidate.ref for candidate in candidates}
    try:
        return tuple(offered[selection_id] for selection_id in selected_ids)
    except KeyError as error:
        raise PromptSkillSelectionResolutionError(
            "selection_outside_catalog"
        ) from error


def validate_exact_skill_instructions(
    refs: Sequence[PromptSkillRefV1],
    instructions: Sequence[PromptSkillInstructionsV1],
) -> tuple[PromptSkillInstructionsV1, ...]:
    if len(refs) != len(instructions):
        raise PromptSkillSelectionResolutionError("selected_skill_integrity_error")
    for ref, resolved in zip(refs, instructions, strict=True):
        if (
            resolved.name != ref.name
            or resolved.revision != ref.revision
            or resolved.content_digest != ref.content_digest
        ):
            raise PromptSkillSelectionResolutionError(
                "selected_skill_integrity_error"
            )
    return tuple(instructions)


def admit_execution_prompt_skill_selection(
    existing: Sequence[ExecutionPromptSkillSelectionTraceV1],
    selection: ExecutionPromptSkillSelectionTraceV1,
    *,
    remaining_possible_nodes: int,
) -> ExecutionPromptSkillSelectionTraceV1:
    if remaining_possible_nodes < 0:
        raise ValueError("remaining selection node reserve cannot be negative")
    if selection.status != "selected":
        return selection
    candidate_ordinal = selection.candidate_ordinal or 0
    future = [
        ExecutionPromptSkillSelectionTraceV1(
            category="answer",
            node="answer_candidate",
            candidate_ordinal=candidate_ordinal + offset,
            candidate_kind="normal",
            status="not_applicable",
        )
        for offset in range(1, remaining_possible_nodes + 1)
    ]
    payload = [
        item.model_dump(mode="json") for item in [*existing, selection, *future]
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) <= 6 and len(encoded) <= 32768:
        return selection
    return selection.model_copy(
        update={
            "status": "baseline_fallback",
            "selected_skills": [],
            "fallback_code": "selected_skill_trace_exceeded",
        }
    )


__all__ = [
    "PromptSkillSelectionResolutionError",
    "admit_execution_prompt_skill_selection",
    "resolve_selected_skill_refs",
    "validate_exact_skill_instructions",
]
