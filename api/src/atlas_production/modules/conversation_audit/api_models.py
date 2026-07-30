from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from atlas_production.modules.conversation.public import ConversationV1
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionState,
    RuntimeEventV1,
)
from atlas_production.modules.workspace_turn.public import WorkspaceDiscoveryTraceV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminConversationListResult(_StrictModel):
    conversations: list[ConversationV1]
    next_cursor: str | None = None


class RuntimeTraceDetail(_StrictModel):
    execution_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    state: ExecutionState
    version: int = Field(ge=1)
    failure_code: str | None
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    budget: BudgetSnapshotV1
    document_discovery: list[WorkspaceDiscoveryTraceV1]
    events: list[RuntimeEventV1]
    created_at: AwareDatetime
    updated_at: AwareDatetime
