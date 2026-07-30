from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


Identity = Annotated[str, Field(min_length=1, max_length=200)]
ResponseLanguage = Literal["zh-TW", "en"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreateV1(_StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: Identity | None = None
    response_language: ResponseLanguage = "zh-TW"


class ConversationV1(_StrictModel):
    conversation_id: Identity
    owner_actor_id: Identity
    title: str = Field(min_length=1, max_length=200)
    status: Literal["active", "archived"]
    response_language: ResponseLanguage
    created_at: AwareDatetime
    updated_at: AwareDatetime


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
    "ConversationCreateV1",
    "ConversationOwner",
    "ConversationRetryLineageOwner",
    "ConversationTurnMemberV1",
    "ConversationV1",
    "CreateTurnV1",
    "RetryTurnV1",
    "ResponseLanguage",
    "TurnAcceptedV1",
]
