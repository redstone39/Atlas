"""Typed application contract for accepting and projecting Workspace turns.

This module owns no tables.  It coordinates public owner contracts only after
each collaborator has closed its own transaction.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.audit.public import (
    TurnAuditDraftOwner,
    TurnAuditDraftOwnerV2,
    TurnAuditStepV1,
)
from atlas_production.modules.authorization.public import (
    AuthorizationOwner,
    CreateTurnAccessGrantV1,
    GrantDocumentResourceV1,
    LineageResourceV1,
    MaterializeGrantDocumentResourcesV1,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftOwner,
    CitationBindingDraftOwnerV2,
    ProtectedCitationEvidenceV1,
    ProtectedCitationReadOwner,
    ProtectedDeclaredEvidencePageIntegrityError,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceReadOwner,
    ProtectedDeclaredEvidenceV1,
    ReadProtectedDeclaredEvidenceV1,
    ReadProtectedCitationV1,
    declared_evidence_protected_open_ref,
)
from atlas_production.modules.context_engineering.public import (
    ContextEngineeringOwner,
    ContextExchangeV3,
    ContextLineageEdgeV3,
    ContextMessageV3,
    ContextSummaryInputV4,
    CreateTurnInputProjectionV1,
    MaterializeContextPackV3,
    TurnInputProjectionOwner,
)
from atlas_production.modules.model_routing.public import ModelRoutingRuntime
from atlas_production.modules.conversation.public import (
    AppendTurnMemberV1,
    ConversationArchiveError,
    ConversationArchiveResultV1,
    ConversationArchiveV1,
    ConversationCreateV1,
    ConversationMembershipConflict,
    ConversationScopeTagV1,
    ConversationOwner,
    ConversationRetryLineageOwner,
    ConversationTurnMemberV1,
    ConversationV1,
    ReasoningMode,
    ResponseLanguage,
    TurnAcceptedV1,
    TurnFeedbackError,
    TurnFeedbackRevisionV1,
    TurnFeedbackUpdateV1,
)
from atlas_production.modules.prompt_skills.public import PromptSkillCatalog
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    AssessmentStateV2,
    EvidenceReviewReasonCodeV2,
    EvidenceReviewStatusV2,
    RetrievalStatusV1,
    ResultGovernanceDraftOwnerV2,
)
from atlas_production.modules.processing_pipeline.public import (
    CreateGenerationRetentionV1,
    GenerationRetentionOwner,
    GenerationRetentionResourceV1,
)
from atlas_production.modules.retrieval.public import (
    ClaimedEvidenceLineageV1,
    DeclaredEvidenceMappingV1,
    DiscoveryCandidateComponentV1,
    DiscoveryChannelTraceV1,
    EvidencePackLineageItemV1,
    EvidencePackRefV1,
    RelevantDocumentDiscoveryTraceV1,
    RetrievalOwner,
)
from atlas_production.modules.answer_behavior.public import AnswerBehaviorOwner
from atlas_production.modules.turn_execution.public import (
    TurnExecutionOrchestrator,
)
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    AllocateExecutionV1,
    BindContextV1,
    ExecutionSnapshotV1,
    ExecutionState,
    FailCarrierExecutionV1,
    LeasePolicyV1,
    MessageParamValue,
    ReasoningPhase,
    ReasoningProgressStatus,
    RuntimeEventV1,
    StageAcceptanceResourceV1,
    TerminalOutcomeV1,
    TurnRuntimeReplayConflict,
    TurnRuntimeOwner,
)
from ._internals import (
    acceptance_failure_code,
    acceptance_identity,
    build_context_command,
    conversation_turn_status,
    historical_exchange_content_digest as _historical_exchange_content_digest,
    input_projection_ref as _input_projection_ref,
    logical_member_chains,
    logical_members,
    reasoning_progress_payload,
    route_snapshots,
    stable_id as _stable_id,
    terminal_projection_is_complete,
)


Identity = Annotated[str, Field(min_length=1, max_length=200)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceTurnError(RuntimeError):
    def __init__(self, error_code: str, message_code: str, status_code: int) -> None:
        super().__init__(message_code)
        self.error_code = error_code
        self.message_code = message_code
        self.status_code = status_code


class WorkspaceTurnCreateV1(_StrictModel):
    input_text: str = Field(min_length=1, max_length=50000)
    idempotency_key: Identity
    reasoning_mode: ReasoningMode | None = None


class WorkspaceTurnRetryV1(_StrictModel):
    idempotency_key: Identity


class WorkspaceConversationCreateV1(ConversationCreateV1):
    pass


class WorkspaceConversationArchiveV1(_StrictModel):
    idempotency_key: Identity


class WorkspaceCitationV1(_StrictModel):
    citation_ref: Identity
    segment_id: Identity
    claim_id: Identity


class WorkspaceAnswerSegmentV2(_StrictModel):
    segment_id: Identity
    text: str = Field(max_length=12000)


class WorkspaceClaimedEvidenceV1(_StrictModel):
    position: int = Field(ge=1)
    handle: Identity
    resolution_status: Literal["resolved", "unresolved", "access_required"]
    duplicate_of_position: int | None = Field(default=None, ge=1)
    handle_kind: Literal["evidence", "visual"] | None = None
    evidence_ref: str | None = None
    result_ref: str | None = None
    invocation_ordinal: int | None = Field(default=None, ge=1)
    document_ref: str | None = None
    document_handle: str | None = None
    lifecycle_epoch: int | None = Field(default=None, ge=1)
    document_version_ref: str | None = None
    processing_revision_ref: str | None = None
    processing_generation_ref: str | None = None
    index_generation_ref: str | None = None
    document_display_name: str | None = Field(default=None, max_length=500)
    document_version_label: str | None = Field(default=None, max_length=200)
    page_number: int | None = Field(default=None, ge=1)
    locator_label: str | None = Field(default=None, max_length=500)
    review_resolution_reason: str | None = Field(default=None, max_length=100)
    protected_open_ref: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def require_metadata_only_when_resolved(self) -> "WorkspaceClaimedEvidenceV1":
        required_metadata = (
            self.handle_kind,
            self.evidence_ref,
            self.result_ref,
            self.invocation_ordinal,
            self.document_ref,
            self.document_handle,
            self.lifecycle_epoch,
            self.document_version_ref,
            self.processing_generation_ref,
            self.index_generation_ref,
            self.document_display_name,
            self.locator_label,
            self.review_resolution_reason,
            self.protected_open_ref,
        )
        if self.resolution_status == "resolved" and any(
            value is None for value in required_metadata
        ):
            raise ValueError("resolved claimed evidence requires complete visible lineage")
        hidden_lineage = (
            self.handle_kind,
            self.evidence_ref,
            self.result_ref,
            self.invocation_ordinal,
            self.document_ref,
            self.document_handle,
            self.lifecycle_epoch,
            self.document_version_ref,
            self.processing_generation_ref,
            self.index_generation_ref,
            self.document_display_name,
            self.locator_label,
            self.processing_revision_ref,
            self.document_version_label,
            self.page_number,
            self.protected_open_ref,
        )
        if self.resolution_status == "unresolved" and (
            any(value is not None for value in hidden_lineage)
            or self.review_resolution_reason in {None, "resolved"}
        ):
            raise ValueError(
                "unresolved claimed evidence requires only a non-resolved reason"
            )
        if self.resolution_status == "access_required" and any(
            value is not None
            for value in (
                *hidden_lineage,
                self.review_resolution_reason,
            )
        ):
            raise ValueError("hidden claimed evidence cannot expose lineage metadata")
        return self


class WorkspaceDiscoveryCandidateV1(_StrictModel):
    position: int = Field(ge=1)
    document_handle: Identity
    resolution_status: Literal["resolved", "access_required"]
    fused_score: str | None = Field(default=None, min_length=3, max_length=100)
    best_component_rank: int | None = Field(default=None, ge=1)
    components: list[DiscoveryCandidateComponentV1] = Field(
        default_factory=list, max_length=2
    )
    document_ref: str | None = None
    lifecycle_epoch: int | None = Field(default=None, ge=1)
    document_version_ref: str | None = None
    processing_revision_ref: str | None = None
    processing_generation_ref: str | None = None
    index_generation_ref: str | None = None
    document_display_name: str | None = Field(default=None, max_length=500)
    document_version_label: str | None = Field(default=None, max_length=200)
    preview: str | None = Field(default=None, max_length=1000)
    locator_label: str | None = Field(default=None, max_length=500)
    page_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_authorized_metadata(self) -> "WorkspaceDiscoveryCandidateV1":
        required = (
            self.fused_score,
            self.best_component_rank,
            self.document_ref,
            self.lifecycle_epoch,
            self.document_version_ref,
            self.processing_generation_ref,
            self.index_generation_ref,
            self.document_display_name,
        )
        if self.resolution_status == "resolved" and (
            any(value is None for value in required) or not self.components
        ):
            raise ValueError("resolved discovery candidate requires complete lineage")
        if self.resolution_status == "access_required" and (
            any(value is not None for value in required)
            or self.components
            or self.processing_revision_ref is not None
            or self.document_version_label is not None
            or self.preview is not None
            or self.locator_label is not None
            or self.page_number is not None
        ):
            raise ValueError("hidden discovery candidate cannot expose metadata")
        return self


class WorkspaceDiscoveryTraceV1(_StrictModel):
    invocation_id: Identity
    result_ref: Identity
    invocation_ordinal: int = Field(ge=1)
    query_text: str = Field(min_length=1, max_length=4000)
    requested_limit: int = Field(ge=1, le=20)
    ranking_contract: Literal["equal-reciprocal-rank-v1"]
    channels: list[DiscoveryChannelTraceV1] = Field(max_length=2)
    degraded: bool
    failure_code: str | None = None
    candidates: list[WorkspaceDiscoveryCandidateV1]


class WorkspaceReasoningProgressV1(_StrictModel):
    event_id: Identity
    sequence: int = Field(ge=1)
    phase: ReasoningPhase
    status: ReasoningProgressStatus
    cycle: int | None = Field(default=None, ge=1, le=3)
    message_code: Identity
    message_params: dict[Identity, MessageParamValue] = Field(
        default_factory=dict, max_length=12
    )
    created_at: AwareDatetime


class WorkspaceTurnProjectionV1(_StrictModel):
    turn_id: Identity
    execution_id: Identity
    ordinal: int = Field(ge=1)
    user_input: str = Field(min_length=1, max_length=50000)
    execution_status: ExecutionState
    reasoning_mode: ReasoningMode = "standard"
    reasoning_timeline: list[WorkspaceReasoningProgressV1] = Field(
        default_factory=list
    )
    retrieval_status: RetrievalStatusV1 | None = None
    evidence_review_status: EvidenceReviewStatusV2 | None = None
    evidence_review_reason_codes: list[EvidenceReviewReasonCodeV2] = Field(
        default_factory=list, max_length=7
    )
    assessment_state: AssessmentStateV2 | None = None
    assessment_reason_code: AssessmentReasonCodeV2 | None = None
    assessment_input_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    assessment_output_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    segments: list[WorkspaceAnswerSegmentV2] = Field(default_factory=list)
    citations: list[WorkspaceCitationV1] = Field(default_factory=list)
    model_claimed_evidence: list[WorkspaceClaimedEvidenceV1] = Field(
        default_factory=list, max_length=100
    )
    failure_code: str | None = None
    feedback: TurnFeedbackRevisionV1 | None = None
    created_at: AwareDatetime


class WorkspaceConversationDetailV1(_StrictModel):
    conversation: ConversationV1
    turns: list[WorkspaceTurnProjectionV1]


class WorkspaceConversationSummaryV1(ConversationV1):
    last_turn_status: Literal["processing", "completed", "failed_closed"] | None


class WorkspaceConversationListV1(_StrictModel):
    conversations: list[WorkspaceConversationSummaryV1]


class WorkspaceExecutionStatusV1(_StrictModel):
    execution_id: Identity
    turn_id: Identity
    conversation_id: Identity
    state: ExecutionState
    version: int = Field(ge=1)
    reasoning_mode: ReasoningMode = "standard"
    reasoning_timeline: list[WorkspaceReasoningProgressV1] = Field(
        default_factory=list
    )
    failure_code: str | None = None
    updated_at: AwareDatetime


class AuthorizedKnowledgeSource(Protocol):
    def current_scope(
        self, *, actor_id: Identity
    ) -> frozenset[tuple[str, str]]: ...

    def resources_for_grant(
        self,
        *,
        actor_id: Identity,
        conversation_id: Identity,
        authorization_revision: int,
    ) -> list[GrantDocumentResourceV1]: ...


class TurnCarrierLauncher(Protocol):
    def launch(self, execution_id: Identity) -> None: ...


class ContextCommandPreparer(Protocol):
    def prepare(
        self,
        command: MaterializeContextPackV3,
        snapshot: ExecutionSnapshotV1,
        *,
        catalog_document_count: int,
    ) -> tuple[ExecutionSnapshotV1, MaterializeContextPackV3]: ...


class ConversationTokenUsageReader(Protocol):
    def observed_tokens(self, conversation_id: Identity) -> int: ...


class WorkspaceCitationDraftSource(
    CitationBindingDraftOwner, CitationBindingDraftOwnerV2, Protocol
):
    """Read legacy formal bindings and current soft-review empty bindings."""


class WorkspaceAuditDraftSource(
    TurnAuditDraftOwner, TurnAuditDraftOwnerV2, Protocol
):
    """Read legacy and current immutable terminal audit drafts."""


class WorkspaceTurnApplication:
    """Stateless coordinator over owner-local commands and request-time reads."""

    def __init__(
        self,
        *,
        conversations: ConversationOwner,
        retry_lineage: ConversationRetryLineageOwner,
        authorization: AuthorizationOwner,
        knowledge_source: AuthorizedKnowledgeSource,
        contexts: ContextEngineeringOwner,
        input_projections: TurnInputProjectionOwner,
        retrieval: RetrievalOwner,
        generation_retention: GenerationRetentionOwner,
        runtime: TurnRuntimeOwner,
        prompt_skill_catalog: PromptSkillCatalog,
        results: ResultGovernanceDraftOwnerV2,
        citations: WorkspaceCitationDraftSource,
        audits: WorkspaceAuditDraftSource,
        citation_reader: ProtectedCitationReadOwner,
        declared_evidence_reader: ProtectedDeclaredEvidenceReadOwner,
        carrier: TurnCarrierLauncher,
        model_routes: ModelRoutingRuntime,
        answer_behavior: AnswerBehaviorOwner,
        context_preparer: ContextCommandPreparer,
        conversation_usage: ConversationTokenUsageReader,
        lease_policy: LeasePolicyV1 | None = None,
    ) -> None:
        self._conversations = conversations
        self._retry_lineage = retry_lineage
        self._authorization = authorization
        self._knowledge_source = knowledge_source
        self._contexts = contexts
        self._input_projections = input_projections
        self._retrieval = retrieval
        self._generation_retention = generation_retention
        self._runtime = runtime
        self._prompt_skill_catalog = prompt_skill_catalog
        self._results = results
        self._citations = citations
        self._audits = audits
        self._citation_reader = citation_reader
        self._declared_evidence_reader = declared_evidence_reader
        self._carrier = carrier
        self._model_routes = model_routes
        self._answer_behavior = answer_behavior
        self._context_preparer = context_preparer
        self._conversation_usage = conversation_usage
        self._lease_policy = lease_policy or LeasePolicyV1()

    @staticmethod
    def _actor_id(actor: object | None) -> str:
        actor_id = getattr(actor, "actor_id", None)
        if not actor_id or not getattr(actor, "active", False):
            raise WorkspaceTurnError(
                "unauthenticated", "auth.please_sign_in_before_asking_a_question", 401
            )
        return str(actor_id)

    def _owned_conversation(self, actor_id: str, conversation_id: str) -> ConversationV1:
        item = self._conversations.get(conversation_id)
        if item is None or item.owner_actor_id != actor_id or item.status != "active":
            raise WorkspaceTurnError("not_found", "conversation.was_not_found", 404)
        return item

    def create_conversation(
        self, actor: object | None, command: WorkspaceConversationCreateV1
    ) -> WorkspaceConversationDetailV1:
        actor_id = self._actor_id(actor)
        requested_scope = {
            (ref.tag_type, ref.tag_id) for ref in command.tag_refs
        }
        if requested_scope and not requested_scope.issubset(
            self._knowledge_source.current_scope(actor_id=actor_id)
        ):
            raise WorkspaceTurnError(
                "knowledge_scope_access_denied",
                "result.knowledge_scope_access_required",
                403,
            )
        item = self._conversations.create(
            actor_id=actor_id,
            command=ConversationCreateV1(
                title=command.title,
                idempotency_key=command.idempotency_key,
                response_language=command.response_language,
                tag_refs=command.tag_refs,
            ),
        )
        return WorkspaceConversationDetailV1(conversation=item, turns=[])

    def list_conversations(self, actor: object | None) -> WorkspaceConversationListV1:
        actor_id = self._actor_id(actor)
        return WorkspaceConversationListV1(
            conversations=[
                self._conversation_summary(item)
                for item in self._conversations.list_for_actor(actor_id)
                if item.status == "active"
            ]
        )

    def archive_conversation(
        self,
        actor: object | None,
        conversation_id: str,
        command: WorkspaceConversationArchiveV1,
    ) -> ConversationArchiveResultV1:
        actor_id = self._actor_id(actor)
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.owner_actor_id != actor_id:
            raise WorkspaceTurnError(
                "not_found", "conversation.was_not_found", 404
            )
        members = self._conversations.candidate_turns(conversation_id)
        expected_next_ordinal = max(
            (member.ordinal for member in members), default=0
        ) + 1
        if conversation.status == "active":
            for member in members:
                state = self._runtime.snapshot(member.execution_id).state
                if state not in {
                    ExecutionState.TERMINAL_COMPLETED,
                    ExecutionState.TERMINAL_FAILED,
                }:
                    raise WorkspaceTurnError(
                        "conversation_processing",
                        "conversation.cannot_be_archived_while_processing",
                        409,
                    )
        try:
            return self._conversations.archive(
                actor_id=actor_id,
                conversation_id=conversation_id,
                command=ConversationArchiveV1(
                    idempotency_key=command.idempotency_key,
                    expected_next_ordinal=expected_next_ordinal,
                ),
            )
        except ConversationArchiveError as error:
            if error.reason == "not_found":
                raise WorkspaceTurnError(
                    "not_found", "conversation.was_not_found", 404
                ) from error
            raise WorkspaceTurnError(
                "conversation_changed",
                "conversation.changed_before_archive",
                409,
            ) from error

    def _conversation_summary(
        self, conversation: ConversationV1
    ) -> WorkspaceConversationSummaryV1:
        members = self._conversations.candidate_turns(conversation.conversation_id)
        last_turn_status = None
        if members:
            latest = max(members, key=lambda item: item.ordinal)
            state = self._runtime.snapshot(latest.execution_id).state
            last_turn_status = conversation_turn_status(state)
        return WorkspaceConversationSummaryV1(
            **conversation.model_dump(mode="python"),
            last_turn_status=last_turn_status,
        )

    def audit_list_conversations(self, *, actor_id: str) -> list[ConversationV1]:
        if not actor_id:
            raise ValueError("audit actor_id must be non-empty")
        return self._conversations.list_all()

    def accept_turn(
        self,
        actor: object | None,
        conversation_id: str,
        command: WorkspaceTurnCreateV1,
        *,
        retry_of: ConversationTurnMemberV1 | None = None,
    ) -> TurnAcceptedV1:
        actor_id = self._actor_id(actor)
        conversation = self._owned_conversation(actor_id, conversation_id)
        operation: Literal["create_turn", "retry_turn"] = (
            "retry_turn" if retry_of is not None else "create_turn"
        )
        retry_of_turn_id = retry_of.turn_id if retry_of is not None else None
        reasoning_mode: ReasoningMode = (
            self._runtime.snapshot(retry_of.execution_id).reasoning_mode
            if retry_of is not None
            else command.reasoning_mode or conversation.reasoning_mode
        )
        turn_id, execution_id, holder_id, input_digest = acceptance_identity(
            actor_id=actor_id,
            conversation_id=conversation_id,
            operation=operation,
            retry_of_turn_id=retry_of_turn_id,
            idempotency_key=command.idempotency_key,
            input_text=command.input_text,
        )
        replay = self._runtime.find_execution(execution_id)
        if replay is not None:
            if (
                replay.actor_id != actor_id
                or replay.conversation_id != conversation_id
                or replay.turn_id != turn_id
                or replay.input_digest != input_digest
                or replay.reasoning_mode != reasoning_mode
            ):
                raise WorkspaceTurnError(
                    "idempotency_conflict", "common.rejected", 409
                )
            return TurnAcceptedV1(
                turn_id=replay.turn_id,
                execution_id=replay.execution_id,
                status="accepted",
                status_url=(
                    f"/api/v1/workspace/turn-executions/{replay.execution_id}"
                ),
                events_url=(
                    f"/api/v1/workspace/turn-executions/{replay.execution_id}/events"
                ),
            )
        prompt_skill_categories = (
            ("understanding", "answer")
            if reasoning_mode == "standard"
            else ("understanding", "planner", "answer")
        )
        prompt_skill_catalogs = [
            self._prompt_skill_catalog.current_catalog(category)
            for category in prompt_skill_categories
        ]
        route = self._model_routes.tested_route()
        vision_route = self._model_routes.tested_vision_default_route()
        if route is None:
            raise WorkspaceTurnError(
                "model_route_unavailable", "model.route_is_unavailable", 503
            )
        runtime_policy = route.runtime_policy
        if (
            self._conversation_usage.observed_tokens(conversation_id)
            >= runtime_policy.max_total_tokens_per_conversation
        ):
            raise WorkspaceTurnError(
                "conversation_token_quota_exceeded",
                "common.rejected",
                429,
            )
        route_snapshot, route_policy = route_snapshots(route, vision_route)
        answer_behavior = self._answer_behavior.current()
        try:
            snapshot = self._runtime.allocate(
                AllocateExecutionV1(
                    execution_id=execution_id,
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    actor_id=actor_id,
                    holder_id=holder_id,
                    route_policy=route_policy,
                    route=route_snapshot,
                    lease_policy=self._lease_policy,
                    idempotency_key=command.idempotency_key,
                    operation=operation,
                    retry_of_turn_id=retry_of_turn_id,
                    input_digest=input_digest,
                    response_language=conversation.response_language,
                    reasoning_mode=reasoning_mode,
                    prompt_skill_catalogs=prompt_skill_catalogs,
                    applied_guidance_revision=answer_behavior.revision,
                    applied_guidance_digest=answer_behavior.guidance_digest,
                )
            )
        except TurnRuntimeReplayConflict as error:
            raise WorkspaceTurnError(
                "idempotency_conflict", "common.rejected", 409
            ) from error
        launch_fresh_carrier = False
        if snapshot.state is ExecutionState.ALLOCATED:
            membership_published = False
            try:
                # Conversation membership is the request-time visibility seam
                # for status/SSE. Publish it before fallible grant/catalog
                # owner calls so every allocated execution can expose its
                # eventual terminal failure without storing mask messages.
                membership_command = AppendTurnMemberV1(
                    conversation_id=snapshot.conversation_id,
                    turn_id=snapshot.turn_id,
                    execution_id=snapshot.execution_id,
                    role="user",
                    idempotency_key=_stable_id(
                        "membership", snapshot.actor_id, snapshot.execution_id
                    ),
                    operation=operation,
                    reasoning_mode=(
                        reasoning_mode if operation == "create_turn" else None
                    ),
                )
                if retry_of_turn_id is None:
                    self._conversations.append_turn_member(
                        actor_id=actor_id, command=membership_command
                    )
                else:
                    self._retry_lineage.append_retry_turn_member(
                        actor_id=actor_id,
                        command=membership_command,
                        retry_of_turn_id=retry_of_turn_id,
                    )
                membership_published = True
                snapshot = self._complete_acceptance(
                    snapshot=snapshot,
                    actor_id=actor_id,
                    input_text=command.input_text,
                )
                launch_fresh_carrier = True
            except Exception as error:
                # A concurrent exact replay may have completed the CAS. It must
                # never launch a second carrier. Any genuine partial acceptance
                # is failed terminally and its bound refs enter release saga.
                current = self._runtime.snapshot(execution_id)
                if current.state is ExecutionState.ALLOCATED:
                    self._fail_acceptance(
                        current, self._acceptance_failure_code(error)
                    )
                    current = self._runtime.snapshot(execution_id)
                if not membership_published:
                    if isinstance(error, ConversationMembershipConflict):
                        raise WorkspaceTurnError(
                            "conversation_changed",
                            "conversation.changed_before_turn_was_accepted",
                            409,
                        ) from error
                    raise
                # Allocation plus membership is the public acceptance point.
                # Any later owner failure is represented by the durable
                # terminal status/events rather than an opaque first-call 500.
                snapshot = current
        if launch_fresh_carrier and snapshot.state is ExecutionState.CONTEXT_READY:
            try:
                self._carrier.launch(execution_id)
            except Exception:
                self._fail_acceptance(snapshot, "contract_violation")
                snapshot = self._runtime.snapshot(execution_id)
        elif snapshot.state not in {
            ExecutionState.ACCEPTED,
            ExecutionState.CONTEXT_READY,
            ExecutionState.AWAITING_MODEL_ACTION,
            ExecutionState.TOOL_PENDING,
            ExecutionState.TOOL_COMPLETED,
            ExecutionState.GOVERNING_RESULT,
            ExecutionState.MATERIALIZING_TERMINAL,
            ExecutionState.TERMINAL_COMPLETED,
            ExecutionState.TERMINAL_FAILED,
        }:
            raise WorkspaceTurnError("execution_conflict", "common.rejected", 409)
        return TurnAcceptedV1(
            turn_id=turn_id,
            execution_id=execution_id,
            # This response acknowledges the immutable execution identity. The
            # status and event resources carry the subsequently advancing state.
            status="accepted",
            status_url=f"/api/v1/workspace/turn-executions/{execution_id}",
            events_url=f"/api/v1/workspace/turn-executions/{execution_id}/events",
        )

    @staticmethod
    def _acceptance_failure_code(error: Exception) -> str:
        return acceptance_failure_code(error)

    def _fail_acceptance(
        self, snapshot: ExecutionSnapshotV1, failure_code: str
    ) -> None:
        self._runtime.fail_carrier(
            FailCarrierExecutionV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                holder_id=snapshot.lease.holder_id,
                expected_lease_version=snapshot.lease.lease_version,
                fencing_token=snapshot.lease.fencing_token,
                failure_code=failure_code,
                detected_by="runtime_validator",
            )
        )

    def _complete_acceptance(
        self,
        *,
        snapshot: ExecutionSnapshotV1,
        actor_id: str,
        input_text: str,
    ) -> ExecutionSnapshotV1:
        accepted_by_this_call = False
        try:
            self._input_projections.create_input_projection(
                CreateTurnInputProjectionV1(
                    projection_ref=_input_projection_ref(snapshot.execution_id),
                    execution_id=snapshot.execution_id,
                    original_user_input=input_text,
                )
            )
            self._stage_acceptance_resource(
                snapshot, "authorization", "release_turn_grant"
            )
            grant = self._authorization.create_grant(
                CreateTurnAccessGrantV1(
                    execution_id=snapshot.execution_id,
                    actor_id=actor_id,
                    conversation_id=snapshot.conversation_id,
                    deadline_at=snapshot.deadline_at,
                    idempotency_key=_stable_id(
                        "grant", snapshot.actor_id, snapshot.execution_id
                    ),
                )
            )
            resources = self._knowledge_source.resources_for_grant(
                actor_id=actor_id,
                conversation_id=snapshot.conversation_id,
                authorization_revision=grant.authorization_revision,
            )
            self._authorization.materialize_grant_document_resources(
                MaterializeGrantDocumentResourcesV1(
                    execution_id=snapshot.execution_id,
                    grant_ref=grant.grant_ref,
                    authorization_revision=grant.authorization_revision,
                    resources=resources,
                    idempotency_key=_stable_id(
                        "grant-resources", snapshot.actor_id, snapshot.execution_id
                    ),
                )
            )
            self._stage_acceptance_resource(
                snapshot,
                "processing_pipeline",
                "release_generation_retention",
            )
            retention = self._generation_retention.create_generation_retention(
                CreateGenerationRetentionV1(
                    execution_id=snapshot.execution_id,
                    resources=[
                        GenerationRetentionResourceV1(
                            document_version_ref=resource.document_version_ref,
                            processing_generation_ref=resource.processing_generation_ref,
                            index_generation_ref=resource.index_generation_ref,
                            manifest_digest=resource.manifest_digest,
                        )
                        for resource in resources
                    ],
                    idempotency_key=_stable_id(
                        "generation-retention",
                        snapshot.actor_id,
                        snapshot.execution_id,
                    ),
                )
            )
            self._stage_acceptance_resource(
                snapshot, "retrieval", "release_knowledge_catalog"
            )
            catalog = self._retrieval.create_catalog(
                execution_id=snapshot.execution_id,
                grant_ref=grant.grant_ref,
                generation_retention_ref=retention.retention_ref,
                idempotency_key=_stable_id(
                    "catalog", snapshot.actor_id, snapshot.execution_id
                ),
            )
            snapshot = self._runtime.accept(
                AcceptExecutionV1(
                    execution_id=snapshot.execution_id,
                    expected_version=snapshot.version,
                    fencing_token=snapshot.lease.fencing_token,
                    grant_ref=grant.grant_ref,
                    catalog_ref=catalog.catalog_ref,
                )
            )
            accepted_by_this_call = True
            context_command = self._context_command(
                snapshot=snapshot,
                actor_id=actor_id,
                input_text=input_text,
            )
            snapshot, context_command = self._context_preparer.prepare(
                context_command,
                snapshot,
                catalog_document_count=catalog.document_count,
            )
            self._stage_acceptance_resource(
                snapshot, "context_engineering", "release_context_pack"
            )
            pack = self._contexts.materialize(context_command)
            return self._runtime.bind_context(
                BindContextV1(
                    execution_id=snapshot.execution_id,
                    expected_version=snapshot.version,
                    fencing_token=snapshot.lease.fencing_token,
                    context_pack_ref=pack.context_pack_ref,
                )
            )
        except Exception as error:
            if accepted_by_this_call:
                self._fail_acceptance(
                    snapshot, self._acceptance_failure_code(error)
                )
            raise

    def _stage_acceptance_resource(
        self,
        snapshot: ExecutionSnapshotV1,
        owner: Literal[
            "authorization",
            "processing_pipeline",
            "retrieval",
            "context_engineering",
        ],
        release_kind: Literal[
            "release_turn_grant",
            "release_generation_retention",
            "release_knowledge_catalog",
            "release_context_pack",
        ],
    ) -> None:
        self._runtime.stage_acceptance_resource(
            StageAcceptanceResourceV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                resource_owner=owner,
                release_kind=release_kind,
            )
        )

    def _context_command(
        self,
        *,
        snapshot: ExecutionSnapshotV1,
        actor_id: str,
        input_text: str,
    ) -> MaterializeContextPackV3:
        retry_sources = self._retry_lineage.retry_sources(snapshot.conversation_id)
        candidates = self._conversations.candidate_turns(snapshot.conversation_id)
        chains = logical_member_chains(
            candidates,
            retry_sources,
            current_turn_id=snapshot.turn_id,
        )
        states = {
            member.execution_id: self._runtime.snapshot(member.execution_id).state
            for _logical_turn_id, chain in chains
            for member in chain
        }
        logical_turn_members = logical_members(
            chains,
            states,
        )
        def exchange(
            logical_turn_id: str, member: ConversationTurnMemberV1
        ) -> ContextExchangeV3 | None:
            turn_snapshot = self._runtime.snapshot(member.execution_id)
            source_context = (
                None
                if turn_snapshot.context_pack_ref is None
                else self._contexts.get(turn_snapshot.context_pack_ref)
            )
            if source_context is None:
                return None
            outcome = self._runtime.terminal_outcome(member.execution_id)
            projection = self._project_turn(actor_id, member, turn_snapshot, outcome)
            answer = "\n".join(segment.text for segment in projection.segments)
            direct_document_ids: list[str] = []
            assistant_visible = bool(answer)
            if (
                outcome is not None
                and outcome.outcome == "completed"
                and outcome.evidence_pack_ref
            ):
                evidence_pack = self._retrieval.read_evidence_pack(
                    outcome.evidence_pack_ref
                )
                if evidence_pack is None:
                    raise WorkspaceTurnError(
                        "projection_incomplete", "common.rejected", 503
                    )
                direct_document_ids = sorted(
                    {item.resource_ref for item in evidence_pack.items}
                )
                resources = [
                    LineageResourceV1(
                        resource_ref=item.resource_ref,
                        resource_kind="document",
                        lifecycle_epoch=item.lifecycle_epoch,
                        version_ref=item.document_version_ref,
                        generation_ref=item.index_generation_ref,
                        processing_generation_ref=item.processing_generation_ref,
                        index_generation_ref=item.index_generation_ref,
                    )
                    for item in evidence_pack.items
                ]
                if resources:
                    decisions = self._authorization.current_visibility(
                        actor_id=actor_id,
                        resources=resources,
                    )
                    assistant_visible = (
                        len(decisions) == len(resources)
                        and all(
                            decision.decision == "visible"
                            for decision in decisions
                        )
                    )
            if projection.evidence_review_status == "questionable":
                assistant_visible = False
            if not assistant_visible:
                answer = ""
                direct_document_ids = []
            content_digest = _historical_exchange_content_digest(
                user_text=source_context.model_user_input,
                assistant_text=answer,
                direct_document_ids=direct_document_ids,
            )
            return ContextExchangeV3(
                logical_turn_id=logical_turn_id,
                representative_turn_id=member.turn_id,
                representative_content_digest=content_digest,
                user_message=ContextMessageV3(
                    role="user", text=source_context.model_user_input
                ),
                assistant_message=(
                    None
                    if not answer
                    else ContextMessageV3(
                        role="assistant",
                        text=answer,
                        # Every selected assistant answer is dialogue context whose
                        # factual content remains pending current-turn verification.
                        verification_status="unverified",
                    )
                ),
                direct_document_ids=direct_document_ids,
            )

        raw_exchanges = []
        for logical_turn_id, member in logical_turn_members:
            item = exchange(logical_turn_id, member)
            if item is not None:
                raw_exchanges.append(item)
        raw_by_logical = {
            exchange.logical_turn_id: exchange for exchange in raw_exchanges
        }
        reusable_summary = None
        for _logical_turn_id, member in reversed(logical_turn_members):
            prior_snapshot = self._runtime.snapshot(member.execution_id)
            if prior_snapshot.context_pack_ref is None:
                continue
            prior_pack = self._contexts.get(prior_snapshot.context_pack_ref)
            if prior_pack is None or prior_pack.summary is None:
                continue
            candidate = prior_pack.summary
            valid = True
            for source in candidate.sources:
                current = raw_by_logical.get(source.logical_turn_id)
                if (
                    current is None
                    or current.representative_turn_id
                    != source.representative_turn_id
                    or current.representative_content_digest
                    != source.representative_content_digest
                    or current.direct_document_ids
                    != source.direct_document_ids
                ):
                    valid = False
                    break
            if valid:
                reusable_summary = ContextSummaryInputV4(
                    summary_ref=candidate.summary_ref,
                    parent_summary_ref=candidate.parent_summary_ref,
                    historical_user_context=candidate.historical_user_context,
                    assistant_pending_verification_context=(
                        candidate.assistant_pending_verification_context
                    ),
                    token_count=candidate.token_count,
                    sources=candidate.sources,
                )
            break

        covered_logical_ids = (
            set()
            if reusable_summary is None
            else {
                source.logical_turn_id for source in reusable_summary.sources
            }
        )
        recent = [
            item
            for item in raw_exchanges
            if item.logical_turn_id not in covered_logical_ids
        ]
        return build_context_command(
            execution_id=snapshot.execution_id,
            actor_id=snapshot.actor_id,
            conversation_id=snapshot.conversation_id,
            turn_id=snapshot.turn_id,
            input_text=input_text,
            recent=recent,
            summary=reusable_summary,
            context_token_budget=snapshot.policy.context_token_budget,
        )

    def retry_turn(
        self, actor: object | None, turn_id: str, command: WorkspaceTurnRetryV1
    ) -> TurnAcceptedV1:
        actor_id = self._actor_id(actor)
        source = self._conversations.get_turn(turn_id)
        if source is None:
            raise WorkspaceTurnError("not_found", "conversation.was_not_found", 404)
        self._owned_conversation(actor_id, source.conversation_id)
        source_snapshot = self._runtime.snapshot(source.execution_id)
        source_projection = self._input_projections.get_input_projection(
            source_snapshot.execution_id
        )
        if source_projection is None:
            raise WorkspaceTurnError("retry_unavailable", "common.rejected", 409)
        return self.accept_turn(
            actor,
            source.conversation_id,
            WorkspaceTurnCreateV1(
                input_text=source_projection.original_user_input,
                idempotency_key=command.idempotency_key,
                reasoning_mode=source_snapshot.reasoning_mode,
            ),
            retry_of=source,
        )

    def get_conversation(
        self, actor: object | None, conversation_id: str
    ) -> WorkspaceConversationDetailV1:
        actor_id = self._actor_id(actor)
        conversation = self._owned_conversation(actor_id, conversation_id)
        return WorkspaceConversationDetailV1(
            conversation=conversation,
            turns=self._detail_turns(actor_id, conversation_id),
        )
    def update_turn_feedback(
        self,
        actor: object | None,
        conversation_id: str,
        turn_id: str,
        command: TurnFeedbackUpdateV1,
    ) -> TurnFeedbackRevisionV1:
        actor_id = self._actor_id(actor)
        self._owned_conversation(actor_id, conversation_id)
        member = self._conversations.get_turn(turn_id)
        if member is None or member.conversation_id != conversation_id:
            raise WorkspaceTurnError(
                "not_found", "conversation.was_not_found", 404
            )
        snapshot = self._runtime.snapshot(member.execution_id)
        outcome = self._runtime.terminal_outcome(member.execution_id)
        if (
            snapshot.state != ExecutionState.TERMINAL_COMPLETED
            or outcome is None
            or outcome.outcome != "completed"
            or not outcome.governed_answer_draft_ref
        ):
            raise WorkspaceTurnError(
                "feedback_is_not_available",
                "conversation.feedback_is_not_available",
                409,
            )
        answer = self._results.read_v2(outcome.governed_answer_draft_ref)
        if (
            answer is None
            or answer.execution_id != member.execution_id
            or not any(segment.text.strip() for segment in answer.segments)
        ):
            raise WorkspaceTurnError(
                "feedback_is_not_available",
                "conversation.feedback_is_not_available",
                409,
            )
        try:
            return self._conversations.revise_turn_feedback(
                actor_id=actor_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                command=command,
            )
        except TurnFeedbackError as error:
            mapping = {
                "not_found": (
                    "not_found",
                    "conversation.was_not_found",
                    404,
                ),
                "revision_conflict": (
                    "feedback_revision_conflict",
                    "conversation.feedback_revision_changed_before_update",
                    409,
                ),
                "idempotency_conflict": (
                    "feedback_idempotency_conflict",
                    "conversation.feedback_idempotency_key_was_reused",
                    409,
                ),
                "history_invalid": (
                    "feedback_history_invalid",
                    "conversation.feedback_history_is_invalid",
                    503,
                ),
            }
            error_code, message_code, status_code = mapping[error.reason]
            raise WorkspaceTurnError(
                error_code, message_code, status_code
            ) from error


    def read_citation(
        self,
        actor: object | None,
        conversation_id: str,
        turn_id: str,
        citation_ref: str,
    ) -> ProtectedCitationEvidenceV1:
        actor_id = self._actor_id(actor)
        self._owned_conversation(actor_id, conversation_id)
        visible = {
            item.turn_id: item for item in self._visible_turns(actor_id, conversation_id)
        }
        if turn_id not in visible:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        member = self._conversations.get_turn(turn_id)
        if member is None or member.conversation_id != conversation_id:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        outcome = self._runtime.terminal_outcome(member.execution_id)
        if outcome is None or outcome.outcome != "completed" or not outcome.evidence_pack_ref:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        binding = self._citations.read(outcome.citation_binding_draft_ref)
        pack = self._retrieval.read_evidence_pack(outcome.evidence_pack_ref)
        if binding is None or pack is None:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        citation = next(
            (item for item in binding.bindings if item.citation_ref == citation_ref),
            None,
        )
        lineage = next(
            (
                item
                for item in pack.items
                if citation is not None and item.evidence_ref == citation.evidence_ref
            ),
            None,
        )
        if citation is None or lineage is None:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        result = self._citation_reader.read_protected(
            ReadProtectedCitationV1(
                draft_ref=binding.draft_ref,
                citation_ref=citation.citation_ref,
                evidence_ref=lineage.evidence_ref,
                document_version_ref=lineage.document_version_ref,
                processing_revision_ref=lineage.processing_revision_ref,
                processing_generation_ref=lineage.processing_generation_ref,
                index_generation_ref=lineage.index_generation_ref,
                page_artifact_ref=lineage.page_artifact_ref,
            )
        )
        if result is None:
            raise WorkspaceTurnError("not_found", "citation.was_not_found", 404)
        return result

    @staticmethod
    def _declared_read_command(
        *,
        execution_id: str,
        declaration_position: int,
        evidence_handle: str,
        evidence_pack: EvidencePackRefV1,
        lineage: EvidencePackLineageItemV1,
        protected_open_ref: str | None = None,
    ) -> ReadProtectedDeclaredEvidenceV1:
        return ReadProtectedDeclaredEvidenceV1(
            execution_id=execution_id,
            declaration_position=declaration_position,
            evidence_handle=evidence_handle,
            evidence_pack_ref=evidence_pack.evidence_pack_ref,
            evidence_pack_digest=evidence_pack.digest,
            evidence_ref=lineage.evidence_ref,
            evidence_digest=lineage.evidence_digest,
            resource_ref=lineage.resource_ref,
            lifecycle_epoch=lineage.lifecycle_epoch,
            document_version_ref=lineage.document_version_ref,
            processing_revision_ref=lineage.processing_revision_ref,
            processing_generation_ref=lineage.processing_generation_ref,
            index_generation_ref=lineage.index_generation_ref,
            page_artifact_ref=lineage.page_artifact_ref,
            result_ref=lineage.result_ref,
            invocation_ordinal=lineage.invocation_ordinal,
            protected_open_ref=protected_open_ref,
        )

    def _read_declared_evidence(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        turn_id: str,
        protected_open_ref: str,
        require_owner: bool,
        accepted_page_media_types: frozenset[str] = frozenset(),
    ) -> ProtectedDeclaredEvidenceV1 | ProtectedDeclaredEvidencePageV1:
        if require_owner:
            self._owned_conversation(actor_id, conversation_id)
        elif self._conversations.get(conversation_id) is None:
            raise WorkspaceTurnError(
                "not_found", "conversation.was_not_found", 404
            )
        member = self._conversations.get_turn(turn_id)
        if member is None or member.conversation_id != conversation_id:
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        outcome = self._runtime.terminal_outcome(member.execution_id)
        if (
            outcome is None
            or outcome.outcome != "completed"
            or not outcome.evidence_pack_ref
        ):
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        audit = self._audits.read_v2(outcome.audit_draft_ref)
        evidence_pack = self._retrieval.read_evidence_pack(
            outcome.evidence_pack_ref
        )
        if (
            audit is None
            or evidence_pack is None
            or audit.execution_id != member.execution_id
            or evidence_pack.execution_id != member.execution_id
            or audit.evidence_pack_ref != evidence_pack.evidence_pack_ref
            or audit.evidence_pack_digest != evidence_pack.digest
        ):
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        pack_by_handle = {
            item.evidence_handle: item for item in evidence_pack.items
        }
        command = None
        for position, handle in enumerate(
            audit.claimed_evidence_handles, start=1
        ):
            lineage = pack_by_handle.get(handle)
            if lineage is None:
                continue
            candidate = self._declared_read_command(
                execution_id=member.execution_id,
                declaration_position=position,
                evidence_handle=handle,
                evidence_pack=evidence_pack,
                lineage=lineage,
            )
            if (
                declared_evidence_protected_open_ref(candidate)
                == protected_open_ref
            ):
                command = candidate.model_copy(
                    update={"protected_open_ref": protected_open_ref}
                )
                break
        if command is None:
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        decisions = self._authorization.current_visibility(
            actor_id=actor_id,
            resources=[
                LineageResourceV1(
                    resource_ref=command.resource_ref,
                    resource_kind="document",
                    lifecycle_epoch=command.lifecycle_epoch,
                    version_ref=command.document_version_ref,
                    generation_ref=command.index_generation_ref,
                    processing_generation_ref=(
                        command.processing_generation_ref
                    ),
                    index_generation_ref=command.index_generation_ref,
                )
            ],
        )
        if len(decisions) != 1 or decisions[0].decision != "visible":
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        try:
            result = (
                self._declared_evidence_reader.read_protected_declared(
                    command,
                    accepted_page_media_types=accepted_page_media_types,
                )
                if accepted_page_media_types
                else self._declared_evidence_reader.read_protected_declared(
                    command
                )
            )
        except ProtectedDeclaredEvidencePageIntegrityError:
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            ) from None
        if result is None:
            raise WorkspaceTurnError(
                "not_found", "citation.was_not_found", 404
            )
        return result

    def read_declared_evidence(
        self,
        actor: object | None,
        conversation_id: str,
        turn_id: str,
        protected_open_ref: str,
        *,
        accepted_page_media_types: frozenset[str] = frozenset(),
    ) -> ProtectedDeclaredEvidenceV1 | ProtectedDeclaredEvidencePageV1:
        return self._read_declared_evidence(
            actor_id=self._actor_id(actor),
            conversation_id=conversation_id,
            turn_id=turn_id,
            protected_open_ref=protected_open_ref,
            require_owner=True,
            accepted_page_media_types=accepted_page_media_types,
        )

    def audit_read_declared_evidence(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        turn_id: str,
        protected_open_ref: str,
    ) -> ProtectedDeclaredEvidenceV1:
        return self._read_declared_evidence(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            protected_open_ref=protected_open_ref,
            require_owner=False,
        )

    def audit_get_conversation(
        self, *, actor_id: str, conversation_id: str
    ) -> WorkspaceConversationDetailV1:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise WorkspaceTurnError("not_found", "conversation.was_not_found", 404)
        return WorkspaceConversationDetailV1(
            conversation=conversation,
            turns=self._detail_turns(actor_id, conversation_id),
        )

    def audit_execution(
        self, *, actor_id: str, conversation_id: str, turn_id: str
    ) -> tuple[
        ExecutionSnapshotV1,
        list[RuntimeEventV1],
        list[WorkspaceDiscoveryTraceV1],
        int,
        list[TurnAuditStepV1],
    ]:
        member = self._conversations.get_turn(turn_id)
        if member is None or member.conversation_id != conversation_id:
            raise WorkspaceTurnError("not_found", "audit.runtime_trace_was_not_found", 404)
        snapshot = self._runtime.snapshot(member.execution_id)
        outcome = self._runtime.terminal_outcome(member.execution_id)
        visible_turn_ids = {
            turn.turn_id
            for turn in self._visible_turns(
                actor_id,
                conversation_id,
                execution_views={
                    member.execution_id: (snapshot, outcome),
                },
            )
        }
        if turn_id not in visible_turn_ids:
            raise WorkspaceTurnError("not_found", "audit.runtime_trace_was_not_found", 404)
        audit_steps: list[TurnAuditStepV1] = []
        if snapshot.state == ExecutionState.TERMINAL_COMPLETED:
            if (
                outcome is None
                or outcome.outcome != "completed"
                or outcome.audit_draft_ref is None
            ):
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                )
            audit = self._audits.read_v2(outcome.audit_draft_ref)
            if audit is None or audit.execution_id != snapshot.execution_id:
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                )
            audit_steps = audit.steps
        traces = (
            self._retrieval.read_discovery_traces(
                execution_id=snapshot.execution_id,
                catalog_ref=snapshot.catalog_ref,
            )
            if snapshot.catalog_ref is not None
            else []
        )
        return (
            snapshot,
            self._runtime.events(member.execution_id),
            self._project_discovery_traces(actor_id, traces),
            (
                self._retrieval.count_page_and_visual_handles(
                    execution_id=snapshot.execution_id,
                    catalog_ref=snapshot.catalog_ref,
                )
                if snapshot.catalog_ref is not None
                else 0
            ),
            audit_steps,
        )

    def _project_discovery_traces(
        self,
        actor_id: str,
        traces: list[RelevantDocumentDiscoveryTraceV1],
    ) -> list[WorkspaceDiscoveryTraceV1]:
        resource_items = [
            (
                trace.result_ref,
                candidate,
                LineageResourceV1(
                    resource_ref=candidate.document_ref,
                    resource_kind="document",
                    lifecycle_epoch=candidate.lifecycle_epoch,
                    version_ref=candidate.document_version_ref,
                    generation_ref=candidate.index_generation_ref,
                    processing_generation_ref=candidate.processing_generation_ref,
                    index_generation_ref=candidate.index_generation_ref,
                ),
            )
            for trace in traces
            for candidate in trace.candidates
        ]
        decisions = (
            self._authorization.current_visibility(
                actor_id=actor_id,
                resources=[resource for _, _, resource in resource_items],
            )
            if resource_items
            else []
        )
        visible = {
            (result_ref, candidate.position): decision.decision == "visible"
            for (result_ref, candidate, _), decision in zip(
                resource_items, decisions
            )
        }
        projected = []
        for trace in traces:
            candidates = []
            for candidate in trace.candidates:
                if not visible.get((trace.result_ref, candidate.position), False):
                    candidates.append(
                        WorkspaceDiscoveryCandidateV1(
                            position=candidate.position,
                            document_handle=candidate.document_handle,
                            resolution_status="access_required",
                        )
                    )
                else:
                    candidates.append(
                        WorkspaceDiscoveryCandidateV1(
                            **candidate.model_dump(mode="python"),
                            resolution_status="resolved",
                        )
                    )
            projected.append(
                WorkspaceDiscoveryTraceV1(
                    **trace.model_dump(
                        mode="python", exclude={"candidates"}
                    ),
                    candidates=candidates,
                )
            )
        return projected

    def _lineage_ordered_members(
        self, conversation_id: str
    ) -> list[tuple[ConversationTurnMemberV1, list[ContextLineageEdgeV3]]]:
        members = self._conversations.candidate_turns(conversation_id)
        graph = self._contexts.lineage_graph([item.turn_id for item in members])
        edges_by_turn: dict[str, list[ContextLineageEdgeV3]] = {}
        for edge in graph.edges:
            edges_by_turn.setdefault(edge.dependent_turn_id, []).append(edge)
        known_ids = {item.turn_id for item in members}
        ordered_ids: set[str] = set()
        ordered: list[
            tuple[ConversationTurnMemberV1, list[ContextLineageEdgeV3]]
        ] = []
        for member in sorted(members, key=lambda item: item.ordinal):
            edges = edges_by_turn.get(member.turn_id, [])
            source_turns = {edge.source_turn_id for edge in edges}
            if any(
                source not in known_ids or source not in ordered_ids
                for source in source_turns
            ):
                continue
            ordered_ids.add(member.turn_id)
            ordered.append((member, edges))
        return ordered

    def _detail_turns(
        self, actor_id: str, conversation_id: str
    ) -> list[WorkspaceTurnProjectionV1]:
        return [
            self._project_turn(
                actor_id,
                member,
                self._runtime.snapshot(member.execution_id),
                self._runtime.terminal_outcome(member.execution_id),
            )
            for member, _edges in self._lineage_ordered_members(conversation_id)
        ]

    def _visible_turns(
        self,
        actor_id: str,
        conversation_id: str,
        *,
        execution_views: dict[
            str, tuple[ExecutionSnapshotV1, TerminalOutcomeV1 | None]
        ]
        | None = None,
    ) -> list[WorkspaceTurnProjectionV1]:
        visible_ids: set[str] = set()
        projected: list[WorkspaceTurnProjectionV1] = []
        pinned_views = execution_views or {}
        for member, edges in self._lineage_ordered_members(conversation_id):
            view = pinned_views.get(member.execution_id)
            if view is None:
                snapshot = self._runtime.snapshot(member.execution_id)
                outcome = self._runtime.terminal_outcome(member.execution_id)
            else:
                snapshot, outcome = view
            dependency_hidden = any(
                edge.source_turn_id not in visible_ids for edge in edges
            )
            resources = [
                LineageResourceV1(
                    resource_ref=edge.source_resource_ref,
                    resource_kind=edge.source_resource_kind,
                    lifecycle_epoch=edge.lifecycle_epoch,
                    version_ref=edge.version_ref,
                    generation_ref=edge.generation_ref,
                )
                for edge in edges
                if edge.source_resource_ref is not None
                and edge.source_resource_kind in {"document", "evidence", "citation"}
                and edge.lifecycle_epoch is not None
            ]
            if outcome is not None and outcome.outcome == "completed" and outcome.evidence_pack_ref:
                pack = self._retrieval.read_evidence_pack(outcome.evidence_pack_ref)
                if pack is None:
                    dependency_hidden = True
                else:
                    resources.extend(
                        LineageResourceV1(
                            resource_ref=item.resource_ref,
                            resource_kind="document",
                            lifecycle_epoch=item.lifecycle_epoch,
                            version_ref=item.document_version_ref,
                            generation_ref=item.index_generation_ref,
                            processing_generation_ref=item.processing_generation_ref,
                            index_generation_ref=item.index_generation_ref,
                        )
                        for item in pack.items
                    )
            if resources:
                decisions = self._authorization.current_visibility(
                    actor_id=actor_id, resources=resources
                )
                dependency_hidden = dependency_hidden or len(decisions) != len(resources) or any(
                    decision.decision != "visible" for decision in decisions
                )
            if dependency_hidden:
                continue
            visible_ids.add(member.turn_id)
            projected.append(self._project_turn(actor_id, member, snapshot, outcome))
        return projected

    def _project_turn(
        self, actor_id: str, member, snapshot, outcome
    ) -> WorkspaceTurnProjectionV1:
        input_projection = self._input_projections.get_input_projection(
            snapshot.execution_id
        )
        user_input = (
            input_projection.original_user_input
            if input_projection is not None
            else "Input unavailable"
        )
        segments = []
        citations = []
        model_claimed_evidence = []
        retrieval_status = None
        evidence_review_status = None
        evidence_review_reason_codes = []
        assessment_state = None
        assessment_reason_code = None
        assessment_input_digest = None
        assessment_output_digest = None
        failure_code = snapshot.terminal_failure_code
        feedback = self._current_turn_feedback(member.turn_id)
        if outcome is not None and outcome.outcome == "completed":
            answer = self._results.read_v2(outcome.governed_answer_draft_ref)
            binding = self._citations.read_v2(outcome.citation_binding_draft_ref)
            audit = self._audits.read_v2(outcome.audit_draft_ref)
            evidence_pack = self._retrieval.read_evidence_pack(
                outcome.evidence_pack_ref
            )
            if not terminal_projection_is_complete(
                execution_id=snapshot.execution_id,
                answer=answer,
                binding=binding,
                audit=audit,
                evidence_pack=evidence_pack,
            ):
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                )
            segments = [
                WorkspaceAnswerSegmentV2(
                    segment_id=segment.segment_id,
                    text=segment.text,
                )
                for segment in answer.segments
            ]
            retrieval_status = answer.retrieval_status
            evidence_review_status = answer.evidence_review_status
            evidence_review_reason_codes = answer.evidence_review_reason_codes
            assessment_state = answer.assessment_state
            assessment_reason_code = answer.assessment_reason_code
            assessment_input_digest = answer.assessment_input_digest
            assessment_output_digest = answer.assessment_output_digest
            citations = [
                WorkspaceCitationV1(
                    citation_ref=item.citation_ref,
                    segment_id=item.segment_id,
                    claim_id=item.claim_id,
                )
                for item in binding.bindings
            ]
            claimed_lineage = self._retrieval.read_claimed_evidence_lineage(
                execution_id=snapshot.execution_id,
                catalog_ref=evidence_pack.catalog_ref,
                handles=audit.claimed_evidence_handles,
            )
            model_claimed_evidence = self._project_claimed_evidence(
                actor_id,
                snapshot.execution_id,
                claimed_lineage,
                evidence_pack,
                answer.declared_evidence_mappings,
            )
        return WorkspaceTurnProjectionV1(
            turn_id=member.turn_id,
            execution_id=member.execution_id,
            ordinal=member.ordinal,
            user_input=user_input,
            execution_status=snapshot.state,
            reasoning_mode=snapshot.reasoning_mode,
            reasoning_timeline=self._reasoning_timeline(snapshot.execution_id),
            retrieval_status=retrieval_status,
            evidence_review_status=evidence_review_status,
            evidence_review_reason_codes=evidence_review_reason_codes,
            assessment_state=assessment_state,
            assessment_reason_code=assessment_reason_code,
            assessment_input_digest=assessment_input_digest,
            assessment_output_digest=assessment_output_digest,
            segments=segments,
            citations=citations,
            model_claimed_evidence=model_claimed_evidence,
            feedback=feedback,
            failure_code=failure_code,
            created_at=member.created_at,
        )

    def _current_turn_feedback(
        self, turn_id: str
    ) -> TurnFeedbackRevisionV1 | None:
        try:
            return self._conversations.current_turn_feedback(turn_id)
        except TurnFeedbackError as error:
            raise WorkspaceTurnError(
                "feedback_history_invalid",
                "conversation.feedback_history_is_invalid",
                503,
            ) from error

    def _reasoning_timeline(
        self, execution_id: str
    ) -> list[WorkspaceReasoningProgressV1]:
        projected: list[WorkspaceReasoningProgressV1] = []
        for event in self._runtime.events(execution_id):
            if not isinstance(event, RuntimeEventV1):
                continue
            try:
                payload = reasoning_progress_payload(event)
            except ValueError as error:
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                ) from error
            if payload is not None:
                projected.append(WorkspaceReasoningProgressV1(**payload))
        return projected

    def _project_claimed_evidence(
        self,
        actor_id: str,
        execution_id: str,
        lineage: list[ClaimedEvidenceLineageV1],
        evidence_pack: EvidencePackRefV1,
        mappings: list[DeclaredEvidenceMappingV1],
    ) -> list[WorkspaceClaimedEvidenceV1]:
        resolved = [item for item in lineage if item.resolution_status == "resolved"]
        resource_items = [
            (
                item,
                LineageResourceV1(
                    resource_ref=item.document_ref,
                    resource_kind="document",
                    lifecycle_epoch=item.lifecycle_epoch,
                    version_ref=item.document_version_ref,
                    generation_ref=item.index_generation_ref,
                    processing_generation_ref=item.processing_generation_ref,
                    index_generation_ref=item.index_generation_ref,
                ),
            )
            for item in resolved
            if item.document_ref is not None
            and item.lifecycle_epoch is not None
            and item.document_version_ref is not None
            and item.processing_generation_ref is not None
            and item.index_generation_ref is not None
        ]
        resources = [resource for _, resource in resource_items]
        decisions = (
            self._authorization.current_visibility(
                actor_id=actor_id, resources=resources
            )
            if resources
            else []
        )
        visible_by_position = {
            item.position: decision.decision == "visible"
            for (item, _), decision in zip(resource_items, decisions)
        }
        mapping_by_position = {item.position: item for item in mappings}
        pack_by_handle = {
            item.evidence_handle: item for item in evidence_pack.items
        }
        projected = []
        for item in lineage:
            mapping = mapping_by_position.get(item.position)
            if mapping is None or mapping.handle != item.handle:
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                )
            if item.resolution_status != "resolved":
                projected.append(
                    WorkspaceClaimedEvidenceV1(
                        position=item.position,
                        handle=item.handle,
                        resolution_status="unresolved",
                        duplicate_of_position=item.duplicate_of_position,
                        review_resolution_reason=mapping.reason_code,
                    )
                )
                continue
            if not visible_by_position.get(item.position, False):
                projected.append(
                    WorkspaceClaimedEvidenceV1(
                        position=item.position,
                        handle=item.handle,
                        resolution_status="access_required",
                        duplicate_of_position=item.duplicate_of_position,
                    )
                )
                continue
            pack_item = pack_by_handle.get(item.handle)
            if pack_item is None:
                raise WorkspaceTurnError(
                    "projection_incomplete", "common.rejected", 503
                )
            read_command = self._declared_read_command(
                execution_id=execution_id,
                declaration_position=item.position,
                evidence_handle=item.handle,
                evidence_pack=evidence_pack,
                lineage=pack_item,
            )
            projected.append(
                WorkspaceClaimedEvidenceV1(
                    **item.model_dump(mode="python"),
                    review_resolution_reason=mapping.reason_code,
                    protected_open_ref=declared_evidence_protected_open_ref(
                        read_command
                    ),
                )
            )
        return projected

    def execution_status(
        self, actor: object | None, execution_id: str
    ) -> WorkspaceExecutionStatusV1:
        actor_id = self._actor_id(actor)
        snapshot = self._runtime.snapshot(execution_id)
        self._owned_conversation(actor_id, snapshot.conversation_id)
        if snapshot.turn_id not in {item.turn_id for item in self._visible_turns(actor_id, snapshot.conversation_id)}:
            raise WorkspaceTurnError("not_found", "conversation.was_not_found", 404)
        return WorkspaceExecutionStatusV1(
            execution_id=snapshot.execution_id,
            turn_id=snapshot.turn_id,
            conversation_id=snapshot.conversation_id,
            state=snapshot.state,
            version=snapshot.version,
            reasoning_mode=snapshot.reasoning_mode,
            reasoning_timeline=self._reasoning_timeline(snapshot.execution_id),
            failure_code=snapshot.terminal_failure_code,
            updated_at=snapshot.updated_at,
        )

    def execution_events(
        self, actor: object | None, execution_id: str, *, after_event_id: str | None
    ) -> list[RuntimeEventV1]:
        self.execution_status(actor, execution_id)
        events = self._runtime.events(execution_id)
        if after_event_id is None:
            return events
        matched = next((item.sequence for item in events if item.event_id == after_event_id), None)
        if matched is None:
            raise WorkspaceTurnError("event_cursor_invalid", "common.rejected", 409)
        return [item for item in events if item.sequence > matched]


class InlineTurnCarrier:
    """Test-only carrier that runs synchronously without changing ownership."""

    def __init__(self, orchestrator: TurnExecutionOrchestrator) -> None:
        self._orchestrator = orchestrator

    def launch(self, execution_id: str) -> None:
        self._orchestrator.run(execution_id)


__all__ = [
    "AuthorizedKnowledgeSource",
    "ContextCommandPreparer",
    "ConversationTokenUsageReader",
    "InlineTurnCarrier",
    "TurnCarrierLauncher",
    "WorkspaceAnswerSegmentV2",
    "WorkspaceAuditDraftSource",
    "WorkspaceCitationDraftSource",
    "WorkspaceCitationV1",
    "WorkspaceClaimedEvidenceV1",
    "WorkspaceConversationArchiveV1",
    "WorkspaceConversationCreateV1",
    "WorkspaceConversationDetailV1",
    "WorkspaceConversationListV1",
    "WorkspaceConversationSummaryV1",
    "WorkspaceDiscoveryCandidateV1",
    "WorkspaceDiscoveryTraceV1",
    "WorkspaceExecutionStatusV1",
    "WorkspaceReasoningProgressV1",
    "WorkspaceTurnApplication",
    "WorkspaceTurnCreateV1",
    "WorkspaceTurnError",
    "WorkspaceTurnProjectionV1",
    "WorkspaceTurnRetryV1",
]
