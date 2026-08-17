from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


Identity = Annotated[str, Field(min_length=1, max_length=200)]
ResponseLanguage = Literal["zh-TW", "en"]
ReasoningMode = Literal["standard", "deep"]



class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

TurnFeedbackValue = Literal["helpful", "not_helpful"]


class TurnFeedbackUpdateV1(_StrictModel):
    feedback: TurnFeedbackValue
    expected_revision: int = Field(ge=0)
    idempotency_key: Identity


class TurnFeedbackRevisionV1(_StrictModel):
    feedback: TurnFeedbackValue
    revision: int = Field(ge=1)
    updated_at: AwareDatetime


class TurnFeedbackError(RuntimeError):
    def __init__(
        self,
        reason: Literal[
            "not_found",
            "revision_conflict",
            "idempotency_conflict",
            "history_invalid",
        ],
    ) -> None:
        super().__init__(reason)
        self.reason = reason


class ConversationScopeTagV1(_StrictModel):
    tag_type: Literal["project", "team"]
    tag_id: Identity



class ConversationCreateV1(_StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: Identity | None = None
    response_language: ResponseLanguage = "zh-TW"
    tag_refs: list[ConversationScopeTagV1] = Field(default_factory=list)

    @field_validator("tag_refs")
    @classmethod
    def canonicalize_tag_refs(
        cls, value: list[ConversationScopeTagV1]
    ) -> list[ConversationScopeTagV1]:
        ordered = sorted(value, key=lambda ref: (ref.tag_type, ref.tag_id))
        keys = [(ref.tag_type, ref.tag_id) for ref in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("tag_refs must be unique")
        return ordered


class ConversationV1(_StrictModel):
    conversation_id: Identity
    owner_actor_id: Identity
    title: str = Field(min_length=1, max_length=200)
    status: Literal["active", "archived"]
    response_language: ResponseLanguage
    reasoning_mode: ReasoningMode = "standard"
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationArchiveV1(_StrictModel):
    idempotency_key: Identity
    expected_next_ordinal: int = Field(ge=1)


class ConversationArchiveResultV1(_StrictModel):
    conversation: ConversationV1
    audit_event_ref: Identity


class ConversationArchiveError(RuntimeError):
    def __init__(self, reason: Literal["not_found", "conflict"]) -> None:
        super().__init__(reason)
        self.reason = reason


class ConversationMembershipConflict(RuntimeError):
    """A turn membership could not be published against the active conversation."""


class ConversationTurnMemberV1(_StrictModel):
    turn_id: Identity
    conversation_id: Identity
    execution_id: Identity
    role: Literal["user", "assistant"]
    ordinal: int = Field(ge=1)
    created_at: AwareDatetime


class AppendTurnMemberV1(_StrictModel):
    conversation_id: Identity
    turn_id: Identity
    execution_id: Identity
    role: Literal["user", "assistant"]
    idempotency_key: Identity
    operation: Literal["create_turn", "retry_turn"] = "create_turn"
    reasoning_mode: ReasoningMode | None = None

    @model_validator(mode="after")
    def require_mode_only_for_fresh_turn(self) -> "AppendTurnMemberV1":
        if (self.operation == "create_turn") != (self.reasoning_mode is not None):
            raise ValueError("fresh turn membership requires exactly one reasoning mode")
        return self


class CreateTurnV1(_StrictModel):
    input_text: str = Field(min_length=1, max_length=50000)
    idempotency_key: Identity


class TurnAcceptedV1(_StrictModel):
    turn_id: Identity
    execution_id: Identity
    status: Literal["allocated", "accepted", "context_ready", "awaiting_model_action"]
    status_url: str = Field(min_length=1, max_length=1000)
    events_url: str = Field(min_length=1, max_length=1000)


class RetryTurnV1(_StrictModel):
    idempotency_key: Identity


class ConversationOwner(Protocol):
    def create(self, *, actor_id: Identity, command: ConversationCreateV1) -> ConversationV1: ...

    def append_turn_member(
        self, *, actor_id: Identity, command: AppendTurnMemberV1
    ) -> ConversationTurnMemberV1: ...

    def archive(
        self,
        *,
        actor_id: Identity,
        conversation_id: Identity,
        command: ConversationArchiveV1,
    ) -> ConversationArchiveResultV1: ...
    def revise_turn_feedback(
        self,
        *,
        actor_id: Identity,
        conversation_id: Identity,
        turn_id: Identity,
        command: TurnFeedbackUpdateV1,
    ) -> TurnFeedbackRevisionV1: ...

    def current_turn_feedback(
        self, turn_id: Identity
    ) -> TurnFeedbackRevisionV1 | None: ...


    def list_for_actor(self, actor_id: Identity) -> list[ConversationV1]: ...

    def list_all(self) -> list[ConversationV1]: ...

    def get(self, conversation_id: Identity) -> ConversationV1 | None: ...

    def get_turn(self, turn_id: Identity) -> ConversationTurnMemberV1 | None: ...

    def candidate_turns(self, conversation_id: Identity) -> list[ConversationTurnMemberV1]: ...


class ConversationRetryLineageOwner(Protocol):
    """Owner-private projection; it does not extend Workspace or HTTP DTOs."""

    def append_retry_turn_member(
        self,
        *,
        actor_id: Identity,
        command: AppendTurnMemberV1,
        retry_of_turn_id: Identity,
    ) -> ConversationTurnMemberV1: ...

    def retry_sources(self, conversation_id: Identity) -> dict[str, str]: ...


__all__ = [
    "AppendTurnMemberV1",
    "ConversationArchiveError",
    "ConversationArchiveResultV1",
    "ConversationArchiveV1",
    "ConversationCreateV1",
    "ConversationMembershipConflict",
    "ConversationScopeTagV1",
    "ConversationOwner",
    "ConversationRetryLineageOwner",
    "ConversationTurnMemberV1",
    "ConversationV1",
    "CreateTurnV1",
    "RetryTurnV1",
    "ResponseLanguage",
    "ReasoningMode",
    "TurnAcceptedV1",
    "TurnFeedbackError",
    "TurnFeedbackRevisionV1",
    "TurnFeedbackUpdateV1",
    "TurnFeedbackValue",
]
