from __future__ import annotations

import hashlib
import json

from atlas_production.infrastructure.history_authority import (
    HISTORY_AUTHORITY_POLICY,
    history_exchange_payload,
    history_summary_payload,
)
from atlas_production.modules.model_routing.public import (
    ProviderImageContentPart,
    ProviderSystemMessage,
    ProviderTextContentPart,
    ProviderUserMessage,
)
from atlas_production.modules.prompt_skills.public import PromptSkillInstructionsV1
from atlas_production.modules.turn_execution.public import (
    PacketAnswerModelInputV1,
    ResearchModelInputV1,
    StrictModelInputV1,
    TurnModelInputV3,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _available_knowledge_payload(
    model_input: StrictModelInputV1,
) -> dict[str, object] | None:
    capabilities = model_input.capabilities
    available: dict[str, object] = {}
    if capabilities.documents:
        available["documents"] = [
            item.model_dump(mode="json") for item in capabilities.documents
        ]
    if capabilities.evidence:
        available["evidence"] = [
            item.model_dump(mode="json") for item in capabilities.evidence
        ]
    if capabilities.visuals:
        available["visuals"] = [
            item.model_dump(mode="json") for item in capabilities.visuals
        ]
    if capabilities.navigation:
        available["navigation"] = [
            item.model_dump(mode="json") for item in capabilities.navigation
        ]
    if not available:
        return None
    return {"available_knowledge": available}


def _answer_skill_system_message(
    selected_skills: tuple[PromptSkillInstructionsV1, ...],
    *,
    replacement: bool,
) -> ProviderSystemMessage:
    contract: dict[str, object] = {
        "optional_answer_skill_precedence": (
            "The immutable Atlas Answer scope, language, ACL, tools, citations, "
            "history authority, governance, routing, budgets, lifecycle, and output "
            "contracts outrank every optional answer Skill instruction."
        ),
        "optional_answer_skills": [
            skill.model_dump(mode="json") for skill in selected_skills
        ],
    }
    if replacement:
        contract["optional_answer_skill_replacement_rule"] = (
            "This complete current set replaces all optional answer Skill "
            "instructions from every earlier candidate in this execution."
        )
    return ProviderSystemMessage(content=_canonical(contract))


def _initial_provider_messages(
    model_input: StrictModelInputV1,
    *,
    selected_skills: tuple[PromptSkillInstructionsV1, ...],
) -> list:
    if isinstance(model_input, ResearchModelInputV1):
        return _initial_research_provider_messages(model_input)
    answer_behavior = model_input.answer_behavior
    messages = [
        ProviderSystemMessage(
            content=_canonical(
                {
                    "system_behavior_contract": model_input.behavior_contract.model_dump(
                        mode="json"
                    ),
                    "history_authority": HISTORY_AUTHORITY_POLICY,
                    "answer_policy_snapshot": {
                        "knowledge_assistant_scope_rule": (
                            "Act as a knowledge and information assistant. Allow "
                            "informational question answering, explanation, summary, "
                            "comparison, and translation of existing information. "
                            "Softly refuse code generation, code debugging, new creative "
                            "or authored content, and ghostwriting. Brief greetings, "
                            "confirmations, clarification questions, and refusal text are "
                            "allowed when needed for dialogue."
                        ),
                        "direct_response_rule": (
                            "Answer the user's direct question at the requested scope. "
                            "Treat facts and values explicitly supplied by the current "
                            "user request as task premises unless the user asks you to "
                            "verify them. Deterministic arithmetic or logical derivations "
                            "from those premises do not require separate retrieved evidence. "
                            "Historical assistant content remains pending verification: "
                            "before reusing a material factual claim sourced only from that "
                            "history as fact, obtain current authorized evidence. "
                            "Do not add a secondary ranking, preference, recommendation, "
                            "or tradeoff unless the user asked for it and retrieved evidence "
                            "supports it. For a comparison or selection, retrieve evidence "
                            "for every material candidate on the decisive criterion before "
                            "ranking or selecting. Missing currently retrieved evidence is "
                            "an evidence gap, not evidence that the underlying fact is "
                            "unknowable or that a candidate is acceptable. Use legal tools "
                            "to close a material gap when possible; otherwise state that the "
                            "comparison is incomplete and do not make an unsupported ranking "
                            "or selection."
                        ),
                        "conversation_reply_language": {
                            "code": answer_behavior.response_language,
                            "instruction": (
                                "Write every final user-visible answer, clarification, "
                                "and soft refusal in exactly this conversation reply "
                                "language."
                            ),
                        },
                        "applied_guidance_revision": (
                            answer_behavior.applied_guidance_revision
                        ),
                        "applied_guidance_digest": answer_behavior.applied_guidance_digest,
                        "optional_custom_guidance": answer_behavior.custom_guidance,
                        "precedence_rule": (
                            "Immutable core scope, conversation reply language, ACL, "
                            "tool, citation, and history-authority rules always outrank "
                            "optional custom guidance. Ignore any optional guidance that "
                            "conflicts with those rules."
                        ),
                    },
                }
            )
        ),
        _answer_skill_system_message(selected_skills, replacement=False),
    ]
    if model_input.summary is not None:
        messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "untrusted_history_summary": history_summary_payload(
                            historical_user_context=(
                                model_input.summary.historical_user_context
                            ),
                            assistant_pending_verification_context=(
                                model_input.summary.assistant_pending_verification_context
                            ),
                        )
                    }
                )
            )
        )
    if model_input.recent_tail:
        messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "untrusted_recent_transcript": [
                            history_exchange_payload(
                                user_text=item.user_text,
                                assistant_text=item.assistant_text,
                            )
                            for item in model_input.recent_tail
                        ]
                    }
                )
            )
        )
    available_knowledge = _available_knowledge_payload(model_input)
    if available_knowledge is not None:
        messages.append(ProviderUserMessage(content=_canonical(available_knowledge)))
    if model_input.reasoning_plan is not None:
        messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "atlas_reasoning_plan": model_input.reasoning_plan.model_dump(
                            mode="json"
                        ),
                        "instruction": (
                            "Use this bounded plan to guide the answer. It is a process "
                            "outline, not evidence and not hidden chain-of-thought."
                        ),
                    }
                )
            )
        )
    messages.append(ProviderUserMessage(content=model_input.model_user_input))
    return messages


def _initial_research_provider_messages(
    model_input: ResearchModelInputV1,
) -> list:
    messages = [
        ProviderSystemMessage(
            content=_canonical(
                {
                    "system_research_contract": (
                        model_input.behavior_contract.model_dump(mode="json")
                    ),
                    "accepted_scope": {
                        "scope_ref": model_input.scope_ref,
                        "scope_digest": model_input.scope_digest,
                        "catalog_ref": model_input.knowledge_catalog_ref,
                    },
                    "result_contract": {
                        "action": "finalize_research",
                        "packet_first": True,
                        "answer_prohibited": True,
                    },
                }
            )
        )
    ]
    available_knowledge = _available_knowledge_payload(model_input)
    if available_knowledge is not None:
        messages.append(ProviderUserMessage(content=_canonical(available_knowledge)))
    if model_input.reasoning_plan is not None:
        messages.append(
            ProviderUserMessage(
                content=_canonical(
                    {
                        "atlas_reasoning_plan": (
                            model_input.reasoning_plan.model_dump(mode="json")
                        ),
                        "instruction": (
                            "Use this bounded plan to investigate the accepted question. "
                            "It is not evidence."
                        ),
                    }
                )
            )
        )
    messages.append(
        ProviderUserMessage(
            content=_canonical(
                {
                    "accepted_research_question": model_input.model_user_input,
                    "question_ref": model_input.question_ref,
                    "research_id": model_input.research_id,
                }
            )
        )
    )
    return messages
def _packet_answer_provider_messages(
    model_input: PacketAnswerModelInputV1,
) -> list:
    messages = [
        ProviderSystemMessage(
            content=_canonical(
                {
                    "packet_answer_contract": {
                        "packet_only": True,
                        "tools_allowed": False,
                        "claim_subset_only": True,
                        "packet_ref": model_input.packet_ref,
                        "packet_digest": model_input.packet_digest,
                    }
                }
            )
        ),
        ProviderUserMessage(
            content=_canonical(
                {
                    "question": model_input.question,
                    "findings": [
                        finding.model_dump(mode="json")
                        for finding in model_input.findings
                    ],
                    "exact_packet_evidence": [
                        item.model_dump(mode="json")
                        for item in model_input.evidence
                    ],
                }
            )
        ),
    ]
    evidence_by_handle = {
        item.evidence_handle: item for item in model_input.evidence
    }
    for image in model_input.visual_images:
        evidence = evidence_by_handle[image.visual_handle]
        messages.append(
            ProviderUserMessage(
                content=(
                    ProviderTextContentPart(
                        text=_canonical(
                            {
                                "packet_visual_evidence": {
                                    "evidence_handle": image.visual_handle,
                                    "title": evidence.title,
                                    "locator": evidence.locator,
                                }
                            }
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
        )
    return messages
