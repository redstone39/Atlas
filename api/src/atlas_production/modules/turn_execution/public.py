from __future__ import annotations

from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator
from atlas_production.modules.prompt_skills.public import (
    PromptSkillInstructionsV1,
    PromptSkillSelectorCandidateV1,
)
from atlas_production.modules.answer_behavior import public as _answer_behavior

from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    ExpandKnowledgeV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    KnowledgeToolObservationV1,
    ListKnowledgeDocumentsV1,
    NavigateDocumentV1,
    SearchKnowledgeV1,
    OpaqueKnowledgeHandle,
    VisualImagePayloadV1,
)
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionSnapshotV1,
    ReasoningEvaluationV1,
    ReasoningPlanV2,
    RoutePolicyV1,
    SchemaRetryOriginCode,
    TurnRouteSnapshotV2,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
KnowledgeActionName: TypeAlias = Literal[
    "list_knowledge_documents",
    "find_knowledge_documents",
    "discover_relevant_documents",
    "search_knowledge",
    "inspect_knowledge",
    "inspect_visual",
    "expand_knowledge",
    "navigate_document",
    "finalize_answer",
]
ExpandDirection: TypeAlias = Literal[
    "previous_page", "next_page", "figure_context", "related_evidence"
]




class AnswerSegmentProposalV1(_StrictModel):
    segment_id: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=12000)


class FinalizeAnswerV1(_StrictModel):
    action: Literal["finalize_answer"]
    segments: list[AnswerSegmentProposalV1] = Field(min_length=1, max_length=100)
    claimed_evidence_handles: list[Identity] = Field(max_length=100)

    @model_validator(mode="after")
    def require_unique_segment_ids(self) -> "FinalizeAnswerV1":
        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("final answer segment ids must be unique")
        return self


TurnActionV1: TypeAlias = Annotated[
    ListKnowledgeDocumentsV1
    | FindKnowledgeDocumentsV1
    | DiscoverRelevantDocumentsV1
    | SearchKnowledgeV1
    | InspectKnowledgeV1
    | InspectVisualV1
    | ExpandKnowledgeV1
    | NavigateDocumentV1
    | FinalizeAnswerV1,
    Field(discriminator="action"),
]

ProviderTurnActionV1: TypeAlias = (
    ListKnowledgeDocumentsV1
    | FindKnowledgeDocumentsV1
    | DiscoverRelevantDocumentsV1
    | SearchKnowledgeV1
    | InspectKnowledgeV1
    | InspectVisualV1
    | ExpandKnowledgeV1
    | NavigateDocumentV1
    | FinalizeAnswerV1
)


class TurnActionEnvelopeV1(_StrictModel):
    next_action: ProviderTurnActionV1


class FinalizeActionEnvelopeV1(_StrictModel):
    next_action: FinalizeAnswerV1


class ModelActionResultV1(_StrictModel):
    """One provider-native outcome plus safe usage metadata.

    Tool-call transport details remain inside the non-transferable provider
    session.  The orchestrator receives only the strict application action.
    """

    action: ProviderTurnActionV1
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ModelContractViolationV1(_StrictModel):
    result_type: Literal["model_contract_violation"] = "model_contract_violation"
    safe_code: Literal[
        "unknown_turn_tool",
        "invalid_turn_tool_arguments",
        "selection_outside_capabilities",
        "invalid_finalize_answer",
    ]
    action_name: str | None = Field(default=None, max_length=100)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


ModelStepResultV1: TypeAlias = ModelActionResultV1 | ModelContractViolationV1


class TurnModelRecentExchangeV3(_StrictModel):
    logical_turn_id: Identity
    representative_turn_id: Identity
    user_text: str = Field(max_length=50000)
    assistant_text: str | None = Field(default=None, max_length=50000)
    user_authority: Literal["user_provided_history"] = "user_provided_history"
    assistant_authority: Literal["pending_verification"] | None = None
    assistant_usage_scope: Literal["dialogue_context_only"] | None = None

    @model_validator(mode="after")
    def require_assistant_authority_with_text(self) -> "TurnModelRecentExchangeV3":
        has_text = self.assistant_text is not None
        has_authority = (
            self.assistant_authority is not None
            and self.assistant_usage_scope is not None
        )
        if has_text != has_authority:
            raise ValueError("assistant history text requires pending authority metadata")
        return self


class TurnModelHistorySummaryV4(_StrictModel):
    summary_ref: OpaqueRef
    historical_user_context: str = Field(max_length=50000)
    assistant_pending_verification_context: str = Field(max_length=50000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bounded_combined_text(self) -> "TurnModelHistorySummaryV4":
        combined = self.historical_user_context + self.assistant_pending_verification_context
        if not combined:
            raise ValueError("summary content must not be empty")
        if len(combined) > 50000:
            raise ValueError("combined summary content exceeds 50000 characters")
        return self


class TurnModelDocumentOptionV1(_StrictModel):
    document_handle: OpaqueKnowledgeHandle
    display_name: str = Field(min_length=1, max_length=500)
    media_type: str | None = Field(default=None, max_length=200)
    modalities: list[Literal["text", "table", "figure"]] = Field(
        min_length=1, max_length=3
    )
    tags: list[str] = Field(default_factory=list, max_length=50)
    version_label: str | None = Field(default=None, max_length=200)


class TurnModelEvidenceOptionV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    document_display_name: str = Field(min_length=1, max_length=500)
    locator_label: str = Field(min_length=1, max_length=500)
    snippet: str = Field(max_length=4096)
    modalities: list[Literal["text", "table", "figure"]] = Field(
        min_length=1, max_length=3
    )
    page_handle: OpaqueKnowledgeHandle | None = None
    page_number: int | None = Field(default=None, ge=1)


class TurnModelVisualOptionV1(_StrictModel):
    handle: OpaqueKnowledgeHandle
    handle_kind: Literal["page", "visual"]
    document_handle: OpaqueKnowledgeHandle
    page_number: int = Field(ge=1)


class TurnModelNavigationOptionV1(_StrictModel):
    navigation_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    kind: Literal["page", "slide", "heading", "figure", "table"]
    label: str = Field(min_length=1, max_length=500)
    page_number: int = Field(ge=1)


class TurnModelCapabilityLimitsV1(_StrictModel):
    max_page_size: int = Field(ge=0, le=20)
    max_discovery_limit: int = Field(ge=0, le=20)
    max_search_limit: int = Field(ge=0, le=20)
    max_expand_limit: int = Field(ge=0, le=20)
    max_expand_anchor_handles: int = Field(ge=0, le=20)
    max_navigation_limit: int = Field(default=0, ge=0, le=20)
    max_output_tokens: int = Field(ge=0, le=64_000)


class TurnModelCapabilitySnapshotV1(_StrictModel):
    schema_version: Literal["turn-model-capabilities-v1"] = "turn-model-capabilities-v1"
    execution_id: Identity
    catalog_ref: OpaqueRef
    allowed_actions: list[KnowledgeActionName] = Field(min_length=1, max_length=9)
    documents: list[TurnModelDocumentOptionV1]
    evidence: list[TurnModelEvidenceOptionV1] = Field(max_length=40)
    visuals: list[TurnModelVisualOptionV1] = Field(max_length=40)
    navigation: list[TurnModelNavigationOptionV1] = Field(default_factory=list, max_length=40)
    allowed_modalities: list[Literal["text", "table", "figure"]] = Field(
        min_length=3, max_length=3
    )
    allowed_expand_directions: list[ExpandDirection] = Field(
        min_length=4, max_length=4
    )
    allowed_navigation_relations: list[
        Literal["previous", "next", "parent", "children", "same_page"]
    ] = Field(
        default_factory=lambda: [
            "previous",
            "next",
            "parent",
            "children",
            "same_page",
        ],
        min_length=5,
        max_length=5,
    )
    catalog_wide_search_allowed: bool
    limits: TurnModelCapabilityLimitsV1
    contract_repair_remaining: int = Field(ge=0, le=3)
    digest: Digest


class TurnModelBehaviorContractV1(_StrictModel):
    selection_rule: Literal[
        "Choose only exact actions, handles, modalities, directions, and limits listed in the current capabilities; never invent or reuse stale opaque values."
    ] = "Choose only exact actions, handles, modalities, directions, and limits listed in the current capabilities; never invent or reuse stale opaque values."
    retrieval_rule: Literal[
        "Use document discovery before evidence search. Use discover_relevant_documents for natural-language content discovery; its previews guide selection only and are not evidence. For find_knowledge_documents, provide one concise document-identity keyword based on a name, model, version, or tag; do not use the user's content question as the identity keyword or treat find as content search. Review the disclosed candidates, then choose one or more current document handles for search_knowledge; never search without selected document handles or treat the whole authorized catalog as the target. You control each query, keyword, cursor, and selected handle and may discover, reselect, and search repeatedly, and may navigate repeatedly, within the current capabilities and budget. Use navigate_document overview, search, and around to explore a selected document's fixed structure and nearby locations when document layout, a table of contents, a named section, a figure, a table, or exhaustive page coverage matters. Any page_handle in current capabilities, whether returned by search_knowledge or navigate_document, can be passed directly to inspect_visual. Navigation targets and page handles are location choices only, never evidence; inspect text or visuals before relying on their contents. Treat an incomplete initial retrieval result as an evidence gap, not proof that disclosed content is unavailable. Use inspect_visual proactively whenever visual inspection would help understand, verify, compare, or resolve ambiguity in the requested task; the user does not need to ask explicitly. This includes figures, diagrams, images, shapes, visual labels, relative positions, page layouts, waveforms, schematics, and visually encoded tables. Text extraction, snippets, captions, and navigation metadata may help locate a target but do not replace visual inspection when the conclusion depends on visual content. For comparisons, inspect every material visual target. When the user requests exhaustive, all, complete, or equivalent coverage, make the best reasonable effort to continue with relevant legal discovery, navigation, search, text inspection, or visual inspection while useful disclosed handles and execution budget remain. Do not finalize by claiming that a disclosed page or visual is inaccessible before attempting inspect_visual when it is legal and relevant. You may choose any useful tool order and need not follow a fixed or repeatable path. If no candidate is found, retry content or identity discovery, browse with list_knowledge_documents, explain the limitation, or ask for clarification; never treat discovery preview, navigation metadata, or catalog metadata as cited evidence. Use evidence retrieval before presenting document-backed factual conclusions. Stop honestly with the precise unresolved scope when relevant actions or handles are unavailable, further exploration is no longer useful, or authorization, budget, or deadline prevents continuation."
    ] = "Use document discovery before evidence search. Use discover_relevant_documents for natural-language content discovery; its previews guide selection only and are not evidence. For find_knowledge_documents, provide one concise document-identity keyword based on a name, model, version, or tag; do not use the user's content question as the identity keyword or treat find as content search. Review the disclosed candidates, then choose one or more current document handles for search_knowledge; never search without selected document handles or treat the whole authorized catalog as the target. You control each query, keyword, cursor, and selected handle and may discover, reselect, and search repeatedly, and may navigate repeatedly, within the current capabilities and budget. Use navigate_document overview, search, and around to explore a selected document's fixed structure and nearby locations when document layout, a table of contents, a named section, a figure, a table, or exhaustive page coverage matters. Any page_handle in current capabilities, whether returned by search_knowledge or navigate_document, can be passed directly to inspect_visual. Navigation targets and page handles are location choices only, never evidence; inspect text or visuals before relying on their contents. Treat an incomplete initial retrieval result as an evidence gap, not proof that disclosed content is unavailable. Use inspect_visual proactively whenever visual inspection would help understand, verify, compare, or resolve ambiguity in the requested task; the user does not need to ask explicitly. This includes figures, diagrams, images, shapes, visual labels, relative positions, page layouts, waveforms, schematics, and visually encoded tables. Text extraction, snippets, captions, and navigation metadata may help locate a target but do not replace visual inspection when the conclusion depends on visual content. For comparisons, inspect every material visual target. When the user requests exhaustive, all, complete, or equivalent coverage, make the best reasonable effort to continue with relevant legal discovery, navigation, search, text inspection, or visual inspection while useful disclosed handles and execution budget remain. Do not finalize by claiming that a disclosed page or visual is inaccessible before attempting inspect_visual when it is legal and relevant. You may choose any useful tool order and need not follow a fixed or repeatable path. If no candidate is found, retry content or identity discovery, browse with list_knowledge_documents, explain the limitation, or ask for clarification; never treat discovery preview, navigation metadata, or catalog metadata as cited evidence. Use evidence retrieval before presenting document-backed factual conclusions. Stop honestly with the precise unresolved scope when relevant actions or handles are unavailable, further exploration is no longer useful, or authorization, budget, or deadline prevents continuation."
    answer_rule: Literal[
        "Success criteria: Answer only the user's current target request, covering the requested depth, format, scope, and comparison. State the adopted referent, scope, units, conditions, and material limitations needed to prevent misunderstanding, and make the direct answer the most prominent content. When the current message is an acknowledgment, confirmation, greeting, pause, farewell, or other non-request, respond only to that dialogue act without resuming prior work. Add context or ask a follow-up question only when necessary to resolve material ambiguity, disclose a material limitation, prevent a misleading answer, or complete the user's requested decision. Brevity must not remove qualifications needed for correctness. Prohibited behaviors: Do not answer a different, broader, adjacent, prior, or assistant-suggested task that the current user message did not request. Do not resume, repeat, or expand prior work merely because it is recent, detailed, unfinished, or related. Do not turn a question about one model, document, page, object, or item into an answer about every item or an unrequested comparison. Do not add tangential background, unsolicited alternatives, extra recommendations, extra checklists, or routine offers such as 'if you want, I can also...'. Do not let supplementary context precede, obscure, or outweigh the direct answer. Do not rely only on pronouns for referent-sensitive conclusions. Finalize only the complete ordered answer segments; a separate post-answer reviewer may assess declared evidence alignment, but its judgement never repairs, retries, truncates, or blocks this complete answer."
    ] = "Success criteria: Answer only the user's current target request, covering the requested depth, format, scope, and comparison. State the adopted referent, scope, units, conditions, and material limitations needed to prevent misunderstanding, and make the direct answer the most prominent content. When the current message is an acknowledgment, confirmation, greeting, pause, farewell, or other non-request, respond only to that dialogue act without resuming prior work. Add context or ask a follow-up question only when necessary to resolve material ambiguity, disclose a material limitation, prevent a misleading answer, or complete the user's requested decision. Brevity must not remove qualifications needed for correctness. Prohibited behaviors: Do not answer a different, broader, adjacent, prior, or assistant-suggested task that the current user message did not request. Do not resume, repeat, or expand prior work merely because it is recent, detailed, unfinished, or related. Do not turn a question about one model, document, page, object, or item into an answer about every item or an unrequested comparison. Do not add tangential background, unsolicited alternatives, extra recommendations, extra checklists, or routine offers such as 'if you want, I can also...'. Do not let supplementary context precede, obscure, or outweigh the direct answer. Do not rely only on pronouns for referent-sensitive conclusions. Finalize only the complete ordered answer segments; a separate post-answer reviewer may assess declared evidence alignment, but its judgement never repairs, retries, truncates, or blocks this complete answer."
    citation_rule: Literal[
        "Do not write a separate source list in answer text; Runtime separately projects every current-authorized resolved evidence item you declare below the response for manual review, without granting formal verified-citation authority. When finalizing, submit claimed_evidence_handles containing, in the original claimed order including duplicates, every evidence_handle or visual_handle from this execution that you claim to have used for the answer; submit an empty list when you claim none. Do not put document handles, page handles, result refs, evidence refs, or natural-language source descriptions in that list."
    ] = "Do not write a separate source list in answer text; Runtime separately projects every current-authorized resolved evidence item you declare below the response for manual review, without granting formal verified-citation authority. When finalizing, submit claimed_evidence_handles containing, in the original claimed order including duplicates, every evidence_handle or visual_handle from this execution that you claim to have used for the answer; submit an empty list when you claim none. Do not put document handles, page handles, result refs, evidence refs, or natural-language source descriptions in that list."
    language_rule: Literal[
        "Answer in exactly the immutable conversation reply language stated in the Answer behavior snapshot, including clarification and soft refusal text, and preserve the conversation's established referents."
    ] = "Answer in exactly the immutable conversation reply language stated in the Answer behavior snapshot, including clarification and soft refusal text, and preserve the conversation's established referents."
    referent_clarity_rule: Literal[
        "Before or with the first conclusion whose truth depends on the referent, explicitly name the adopted model, document, page, object, or comparison subject. When a referent can be reasonably determined, naturally state in the user's language what you understand it to be. When multiple reasonable referents would materially change the answer and none can be safely selected, explain the ambiguity and ask the user to confirm. Never rely only on pronouns such as it, this, or the other one for referent-sensitive facts."
    ] = "Before or with the first conclusion whose truth depends on the referent, explicitly name the adopted model, document, page, object, or comparison subject. When a referent can be reasonably determined, naturally state in the user's language what you understand it to be. When multiple reasonable referents would materially change the answer and none can be safely selected, explain the ambiguity and ask the user to confirm. Never rely only on pronouns such as it, this, or the other one for referent-sensitive facts."


class TurnModelInputV3(_StrictModel):
    schema_version: Literal["turn-model-input-v3"] = "turn-model-input-v3"
    execution_id: Identity
    model_user_input: str = Field(min_length=1, max_length=50000)
    recent_tail: list[TurnModelRecentExchangeV3]
    summary: TurnModelHistorySummaryV4 | None
    context_pack_ref: OpaqueRef
    knowledge_catalog_ref: OpaqueRef
    catalog_document_count: int = Field(ge=0)
    budget: BudgetSnapshotV1
    policy: RoutePolicyV1
    route: TurnRouteSnapshotV2
    answer_behavior: _answer_behavior.AnswerBehaviorInputV1
    capabilities: TurnModelCapabilitySnapshotV1
    behavior_contract: TurnModelBehaviorContractV1 = Field(
        default_factory=TurnModelBehaviorContractV1
    )
    previous_observation: KnowledgeToolObservationV1 | None = None
    reasoning_plan: ReasoningPlanV2 | None = None

    @model_validator(mode="after")
    def require_execution_fixed_model_visible_item_total(self) -> "TurnModelInputV3":
        identities = (
            {item.evidence_handle for item in self.capabilities.evidence}
            | {item.handle for item in self.capabilities.visuals}
            | {
                item.navigation_handle
                for item in self.capabilities.navigation
            }
        )
        if len(identities) != self.budget.model_visible_items:
            raise ValueError(
                "model-visible capabilities do not match the runtime budget total"
            )
        if len(identities) > self.policy.max_model_visible_items_per_turn:
            raise ValueError(
                "model-visible capabilities exceed the execution-fixed limit"
            )
        return self


class DeepReasoningPlanResultV1(_StrictModel):
    plan: ReasoningPlanV2
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class DeepReasoningContractError(RuntimeError):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


class DeepReasoningEvaluationResultV1(_StrictModel):
    evaluation: ReasoningEvaluationV1
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class GateCorrectionFeedbackV1(_StrictModel):
    consistency: Literal["conflict", "insufficient"]
    failing_segment_ids: list[Identity] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_unique_segment_ids(self) -> "GateCorrectionFeedbackV1":
        if len(self.failing_segment_ids) != len(set(self.failing_segment_ids)):
            raise ValueError("gate correction segment ids must be unique")
        return self

class _ImmutableStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


SkillSelectionNode: TypeAlias = Literal[
    "resolver", "deep_initial_planner", "deep_replanner", "answer_candidate"
]


class UnderstandingNodeContextV1(_ImmutableStrictModel):
    node: Literal["resolver"] = "resolver"
    original_user_input: str = Field(min_length=1, max_length=50000)
    authorized_rewritten_context: dict[str, object]

    @model_validator(mode="after")
    def require_exact_authorized_context(self) -> "UnderstandingNodeContextV1":
        if set(self.authorized_rewritten_context) != {
            "summary",
            "recent_exchanges",
        }:
            raise ValueError("understanding context must contain exact authorized history")
        if not isinstance(
            self.authorized_rewritten_context["recent_exchanges"], list
        ):
            raise ValueError("understanding recent exchanges must be an ordered list")
        return self


class InitialPlanningNodeContextV1(_ImmutableStrictModel):
    node: Literal["deep_initial_planner"] = "deep_initial_planner"
    current_user_request: str = Field(min_length=1, max_length=50000)
    history_authority_policy: dict[str, object]
    history_summary: dict[str, object] | None
    recent_history: tuple[dict[str, object], ...]
    catalog_document_count: int = Field(ge=0)
    allowed_actions: tuple[KnowledgeActionName, ...]


class ReplanningNodeContextV1(_ImmutableStrictModel):
    node: Literal["deep_replanner"] = "deep_replanner"
    current_plan: ReasoningPlanV2
    evaluator_finding: dict[str, object]
    allowed_action_kinds: tuple[KnowledgeActionName, ...]
    safe_counts: BudgetSnapshotV1
    remaining_execution_limits: dict[str, int]


class AnswerCandidateNodeContextV1(_ImmutableStrictModel):
    node: Literal["answer_candidate"] = "answer_candidate"
    candidate_ordinal: int = Field(ge=1, le=5)
    candidate_kind: Literal["normal", "limit_final"] = "normal"
    current_user_request: str = Field(min_length=1, max_length=50000)
    current_plan: ReasoningPlanV2 | None = None
    correction_kind: Literal[
        "revise_only", "research_then_revise", "limit_final"
    ] | None = None
    triggering_evaluation: ReasoningEvaluationV1 | None = None
    gate_correction_feedback: GateCorrectionFeedbackV1 | None = None

    @model_validator(mode="after")
    def require_candidate_boundary_context(self) -> "AnswerCandidateNodeContextV1":
        correction_fields = (
            self.correction_kind,
            self.triggering_evaluation,
            self.gate_correction_feedback,
        )
        if self.candidate_ordinal == 1:
            if self.candidate_kind != "normal" or any(
                value is not None for value in correction_fields
            ):
                raise ValueError("initial answer candidate cannot carry correction input")
            return self
        if self.triggering_evaluation is None or self.correction_kind is None:
            raise ValueError("later answer candidate requires triggering correction input")
        if self.candidate_kind == "limit_final":
            if self.correction_kind != "limit_final":
                raise ValueError("limit-final candidate requires limit-final correction")
        elif self.correction_kind == "limit_final":
            raise ValueError("normal candidate cannot carry limit-final correction")
        return self


SkillSelectionNodeContextV1: TypeAlias = Annotated[
    UnderstandingNodeContextV1
    | InitialPlanningNodeContextV1
    | ReplanningNodeContextV1
    | AnswerCandidateNodeContextV1,
    Field(discriminator="node"),
]


class SkillSelectionRequestV2(_ImmutableStrictModel):
    node: SkillSelectionNode
    node_context: SkillSelectionNodeContextV1
    candidates: tuple[PromptSkillSelectorCandidateV1, ...]

    @model_validator(mode="after")
    def require_matching_node_context_and_category(self) -> "SkillSelectionRequestV2":
        if self.node != self.node_context.node:
            raise ValueError("selector node must match its node context")
        required_category = {
            "resolver": "understanding",
            "deep_initial_planner": "planner",
            "deep_replanner": "planner",
            "answer_candidate": "answer",
        }[self.node]
        if any(
            candidate.ref.category != required_category
            for candidate in self.candidates
        ):
            raise ValueError("selector candidates must match the node category")
        selection_ids = [candidate.selection_id for candidate in self.candidates]
        if (
            len(selection_ids) != len(set(selection_ids))
            or selection_ids != sorted(selection_ids)
        ):
            raise ValueError("selector candidate IDs must be ordered and unique")
        return self


class SkillSelectionDecisionV1(_ImmutableStrictModel):
    selected_skill_ids: list[str]

    @model_validator(mode="after")
    def require_ordered_unique_selection(self) -> "SkillSelectionDecisionV1":
        if len(self.selected_skill_ids) != len(set(self.selected_skill_ids)):
            raise ValueError("selected skill IDs must be unique")
        return self


class SkillSelectionResultV1(_ImmutableStrictModel):
    decision: SkillSelectionDecisionV1
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class SkillSelectorModel(Protocol):
    def estimate_selection_request_tokens(
        self,
        snapshot: ExecutionSnapshotV1,
        request: SkillSelectionRequestV2,
    ) -> int: ...

    def select(
        self,
        snapshot: ExecutionSnapshotV1,
        request: SkillSelectionRequestV2,
    ) -> SkillSelectionResultV1: ...

class DeepReasoningModel(Protocol):
    def build_initial_planning_node_context(
        self, model_input: TurnModelInputV3
    ) -> InitialPlanningNodeContextV1: ...

    def build_replanning_node_context(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        remaining_execution_limits: dict[str, int],
    ) -> ReplanningNodeContextV1: ...

    def estimate_plan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: InitialPlanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        repair: bool,
    ) -> int: ...

    def plan(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: InitialPlanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1: ...

    def estimate_replan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        node_context: ReplanningNodeContextV1,
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
    ) -> int: ...

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
    ) -> DeepReasoningPlanResultV1: ...

    def estimate_evaluation_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        proposal: FinalizeAnswerV1,
        observations: list[KnowledgeToolObservationV1],
        cycle: int,
    ) -> int: ...

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
    ) -> DeepReasoningEvaluationResultV1: ...


def turn_action_schema(*, finalize_only: bool = False) -> dict:
    envelope_type = FinalizeActionEnvelopeV1 if finalize_only else TurnActionEnvelopeV1
    return envelope_type.model_json_schema()


def finalize_answer_schema() -> dict:
    """Return the strict final response contract used on the provider wire."""

    return FinalizeAnswerV1.model_json_schema()


class StrictTurnModelSession(Protocol):
    """One live carrier's ephemeral provider transcript.

    There is intentionally no serialization, reconstruction, transfer, resume,
    or takeover API.  Losing this object ends the execution.
    """

    def next_action(
        self,
        model_input: TurnModelInputV3,
        *,
        finalize_only: bool,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> ModelStepResultV1: ...

    def estimate_next_request_tokens(
        self, model_input: TurnModelInputV3, *, finalize_only: bool
    ) -> int: ...
    def estimate_begin_answer_candidate_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        candidate_ordinal: int,
        candidate_kind: Literal["normal", "limit_final"],
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
    ) -> int: ...

    def begin_answer_candidate(
        self,
        model_input: TurnModelInputV3,
        *,
        candidate_ordinal: int,
        candidate_kind: Literal["normal", "limit_final"],
        selected_skills: tuple[PromptSkillInstructionsV1, ...],
    ) -> None: ...


    def accept_tool_observation(
        self,
        observation: KnowledgeToolObservationV1,
        *,
        visual_image: VisualImagePayloadV1 | None = None,
    ) -> None: ...

    def accept_contract_repair(self, violation: ModelContractViolationV1) -> None: ...

    def accept_reasoning_feedback(
        self,
        evaluation: ReasoningEvaluationV1,
        *,
        correction_kind: Literal["revise_only", "research_then_revise"],
        gate_feedback: GateCorrectionFeedbackV1 | None = None,
        plan: ReasoningPlanV2 | None = None,
    ) -> None: ...

    def accept_reasoning_limit(
        self,
        evaluation: ReasoningEvaluationV1,
        *,
        gate_feedback: GateCorrectionFeedbackV1 | None = None,
    ) -> None: ...

    def discard(self) -> None: ...


class StrictTurnModel(Protocol):
    def open_session(self, model_input: TurnModelInputV3) -> StrictTurnModelSession: ...


class TurnExecutionOrchestrator(Protocol):
    def run(self, execution_id: Identity) -> None:
        """Drive one already allocated execution without owning durable state."""
        ...


__all__ = [
    "AnswerSegmentProposalV1",
    "FinalizeAnswerV1",
    "DeepReasoningEvaluationResultV1",
    "DeepReasoningContractError",
    "DeepReasoningModel",
    "DeepReasoningPlanResultV1",
    "FinalizeActionEnvelopeV1",
    "ModelActionResultV1",
    "ModelContractViolationV1",
    "ModelStepResultV1",
    "GateCorrectionFeedbackV1",
    "UnderstandingNodeContextV1",
    "InitialPlanningNodeContextV1",
    "AnswerCandidateNodeContextV1",
    "StrictTurnModel",
    "StrictTurnModelSession",
    "TurnExecutionOrchestrator",
    "TurnActionV1",
    "TurnActionEnvelopeV1",
    "TurnModelHistorySummaryV4",
    "TurnModelCapabilityLimitsV1",
    "TurnModelCapabilitySnapshotV1",
    "TurnModelBehaviorContractV1",
    "TurnModelDocumentOptionV1",
    "TurnModelEvidenceOptionV1",
    "TurnModelNavigationOptionV1",
    "TurnModelInputV3",
    "TurnModelRecentExchangeV3",
    "ReplanningNodeContextV1",
    "SkillSelectionNodeContextV1",
    "SkillSelectionDecisionV1",
    "SkillSelectionNode",
    "SkillSelectionRequestV2",
    "SkillSelectionResultV1",
    "SkillSelectorModel",
    "finalize_answer_schema",
    "turn_action_schema",
]
