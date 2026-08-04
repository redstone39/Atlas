from __future__ import annotations

from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_production.modules.conversation.public import ResponseLanguage
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


class AnswerBehaviorError(RuntimeError):
    def __init__(self, error_code: str, message_code: str, status_code: int) -> None:
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code


class AnswerBehaviorRevisionV1(_StrictModel):
    revision: int = Field(ge=0)
    custom_guidance: str | None = Field(default=None, max_length=2000)
    guidance_digest: Digest | None = None
    created_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_revision_shape(self) -> "AnswerBehaviorRevisionV1":
        empty = self.revision == 0
        if empty != (
            self.custom_guidance is None
            and self.guidance_digest is None
            and self.created_at is None
        ):
            raise ValueError("revision zero is the only empty Answer behavior revision")
        if not empty and (
            self.guidance_digest is None or self.created_at is None
        ):
            raise ValueError("positive Answer behavior revision requires immutable content")
        return self


class AnswerBehaviorUpdateRequest(_StrictModel):
    custom_guidance: str | None = None
    expected_revision: int = Field(ge=0)
    idempotency_key: Identity

    @field_validator("custom_guidance")
    @classmethod
    def normalize_custom_guidance(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 2000:
            raise ValueError("custom guidance exceeds 2000 characters")
        return normalized


class AnswerBehaviorStatus(_StrictModel):
    revision: int = Field(ge=0)
    custom_guidance: str | None = Field(default=None, max_length=2000)
    guidance_digest: Digest | None = None
    updated_by: Identity | None = None
    updated_at: AwareDatetime | None = None
    audit_event_ref: Identity | None = None

    @model_validator(mode="after")
    def require_status_shape(self) -> "AnswerBehaviorStatus":
        empty = self.revision == 0
        if empty and any(
            value is not None
            for value in (
                self.custom_guidance,
                self.guidance_digest,
                self.updated_by,
                self.updated_at,
                self.audit_event_ref,
            )
        ):
            raise ValueError("revision zero is the only empty Answer behavior status")
        if not empty and (
            self.guidance_digest is None
            or self.updated_by is None
            or self.updated_at is None
            or self.audit_event_ref is None
        ):
            raise ValueError("positive Answer behavior status requires trace metadata")
        return self


class AnswerBehaviorInputV1(_StrictModel):
    response_language: ResponseLanguage
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: Digest | None = None
    custom_guidance: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_input_snapshot_shape(self) -> "AnswerBehaviorInputV1":
        empty = self.applied_guidance_revision == 0
        if empty != (
            self.applied_guidance_digest is None and self.custom_guidance is None
        ):
            raise ValueError("Answer input guidance snapshot is inconsistent")
        if not empty and self.applied_guidance_digest is None:
            raise ValueError("positive Answer input revision requires immutable digest")
        return self


class AnswerBehaviorOwner(Protocol):
    def current(self) -> AnswerBehaviorRevisionV1: ...

    def read_exact(
        self, *, revision: int, guidance_digest: str | None
    ) -> AnswerBehaviorRevisionV1: ...


class AnswerBehaviorAdmin(Protocol):
    def get(self, actor: object | None) -> AnswerBehaviorStatus: ...

    def update(
        self, actor: object | None, payload: AnswerBehaviorUpdateRequest
    ) -> AnswerBehaviorStatus: ...


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
    verification_status: Literal[
        "verified", "partially_verified", "unverified", "not_applicable"
    ]


class TurnModelHistorySummaryV3(_StrictModel):
    summary_ref: OpaqueRef
    text: str = Field(max_length=50000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    navigation: list[TurnModelNavigationOptionV1] = Field(
        default_factory=list, max_length=40
    )
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
    contract_repair_remaining: Literal[0, 1]
    digest: Digest


class TurnModelBehaviorContractV1(_StrictModel):
    selection_rule: Literal[
        "Choose only exact actions, handles, modalities, directions, and limits listed in the current capabilities; never invent or reuse stale opaque values."
    ] = "Choose only exact actions, handles, modalities, directions, and limits listed in the current capabilities; never invent or reuse stale opaque values."
    retrieval_rule: Literal[
        "Use document discovery before evidence search. Use discover_relevant_documents for natural-language content discovery; its previews guide selection only and are not evidence. For find_knowledge_documents, provide one concise document-identity keyword based on a name, model, version, or tag; do not use the user's content question as the identity keyword or treat find as content search. Review the disclosed candidates, then choose one or more current document handles for search_knowledge; never search without selected document handles or treat the whole authorized catalog as the target. You control each query, keyword, cursor, and selected handle and may discover, reselect, and search repeatedly, and may navigate repeatedly, within the current capabilities and budget. Use navigate_document overview, search, and around to explore a selected document's fixed structure and nearby locations when document layout, a table of contents, a named section, a figure, a table, or exhaustive page coverage matters. A page_handle returned by navigate_document can be passed directly to inspect_visual while that handle remains in current capabilities. Navigation targets and page handles are location choices only, never evidence; inspect text or visuals before relying on their contents. Treat an incomplete initial retrieval result as an evidence gap, not proof that disclosed content is unavailable. When the user explicitly asks to look at or inspect a page, figure, diagram, image, shape, or visual arrangement, or requests exhaustive, all, complete, or equivalent coverage, make the best reasonable effort to continue with relevant legal discovery, navigation, search, text inspection, or visual inspection while useful disclosed handles and execution budget remain. Do not finalize by claiming that a disclosed page or visual is inaccessible before attempting inspect_visual when it is legal and relevant. You may choose any useful tool order and need not follow a fixed or repeatable path. If no candidate is found, retry content or identity discovery, browse with list_knowledge_documents, explain the limitation, or ask for clarification; never treat discovery preview, navigation metadata, or catalog metadata as cited evidence. Use evidence retrieval before presenting document-backed factual conclusions. Stop honestly with the precise unresolved scope when relevant actions or handles are unavailable, further exploration is no longer useful, or authorization, budget, or deadline prevents continuation."
    ] = "Use document discovery before evidence search. Use discover_relevant_documents for natural-language content discovery; its previews guide selection only and are not evidence. For find_knowledge_documents, provide one concise document-identity keyword based on a name, model, version, or tag; do not use the user's content question as the identity keyword or treat find as content search. Review the disclosed candidates, then choose one or more current document handles for search_knowledge; never search without selected document handles or treat the whole authorized catalog as the target. You control each query, keyword, cursor, and selected handle and may discover, reselect, and search repeatedly, and may navigate repeatedly, within the current capabilities and budget. Use navigate_document overview, search, and around to explore a selected document's fixed structure and nearby locations when document layout, a table of contents, a named section, a figure, a table, or exhaustive page coverage matters. A page_handle returned by navigate_document can be passed directly to inspect_visual while that handle remains in current capabilities. Navigation targets and page handles are location choices only, never evidence; inspect text or visuals before relying on their contents. Treat an incomplete initial retrieval result as an evidence gap, not proof that disclosed content is unavailable. When the user explicitly asks to look at or inspect a page, figure, diagram, image, shape, or visual arrangement, or requests exhaustive, all, complete, or equivalent coverage, make the best reasonable effort to continue with relevant legal discovery, navigation, search, text inspection, or visual inspection while useful disclosed handles and execution budget remain. Do not finalize by claiming that a disclosed page or visual is inaccessible before attempting inspect_visual when it is legal and relevant. You may choose any useful tool order and need not follow a fixed or repeatable path. If no candidate is found, retry content or identity discovery, browse with list_knowledge_documents, explain the limitation, or ask for clarification; never treat discovery preview, navigation metadata, or catalog metadata as cited evidence. Use evidence retrieval before presenting document-backed factual conclusions. Stop honestly with the precise unresolved scope when relevant actions or handles are unavailable, further exploration is no longer useful, or authorization, budget, or deadline prevents continuation."
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
    summary: TurnModelHistorySummaryV3 | None
    context_pack_ref: OpaqueRef
    knowledge_catalog_ref: OpaqueRef
    catalog_document_count: int = Field(ge=0)
    budget: BudgetSnapshotV1
    policy: RoutePolicyV1
    route: TurnRouteSnapshotV2
    answer_behavior: AnswerBehaviorInputV1
    capabilities: TurnModelCapabilitySnapshotV1
    behavior_contract: TurnModelBehaviorContractV1 = Field(
        default_factory=TurnModelBehaviorContractV1
    )
    previous_observation: KnowledgeToolObservationV1 | None = None
    reasoning_plan: ReasoningPlanV2 | None = None


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


class DeepReasoningModel(Protocol):
    def estimate_plan_request_tokens(
        self, model_input: TurnModelInputV3, *, repair: bool
    ) -> int: ...

    def plan(
        self,
        model_input: TurnModelInputV3,
        *,
        repair: bool,
        schema_retry_ordinal: int = 0,
        repair_origin_error_code: SchemaRetryOriginCode | None = None,
    ) -> DeepReasoningPlanResultV1: ...

    def estimate_replan_request_tokens(
        self,
        model_input: TurnModelInputV3,
        *,
        plan: ReasoningPlanV2,
        evaluation: ReasoningEvaluationV1,
        repair: bool,
    ) -> int: ...

    def replan(
        self,
        model_input: TurnModelInputV3,
        *,
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
    "AnswerBehaviorAdmin",
    "AnswerBehaviorError",
    "AnswerBehaviorInputV1",
    "AnswerBehaviorOwner",
    "AnswerBehaviorRevisionV1",
    "AnswerBehaviorStatus",
    "AnswerBehaviorUpdateRequest",
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
    "StrictTurnModel",
    "StrictTurnModelSession",
    "TurnExecutionOrchestrator",
    "TurnActionV1",
    "TurnActionEnvelopeV1",
    "TurnModelHistorySummaryV3",
    "TurnModelCapabilityLimitsV1",
    "TurnModelCapabilitySnapshotV1",
    "TurnModelBehaviorContractV1",
    "TurnModelDocumentOptionV1",
    "TurnModelEvidenceOptionV1",
    "TurnModelNavigationOptionV1",
    "TurnModelInputV3",
    "TurnModelRecentExchangeV3",
    "finalize_answer_schema",
    "turn_action_schema",
]
