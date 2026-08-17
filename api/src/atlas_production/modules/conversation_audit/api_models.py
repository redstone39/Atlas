from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from atlas_production.modules.audit.public import TurnAuditStepV1

from atlas_production.modules.conversation.public import ConversationV1, ReasoningMode
from atlas_production.modules.turn_runtime.public import (
    ExecutionState,
    ReasoningTraceV3,
    RuntimeEventV1,
)
from atlas_production.modules.workspace_turn.public import WorkspaceDiscoveryTraceV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminConversationListResult(_StrictModel):
    conversations: list[ConversationV1]
    next_cursor: str | None = None


class BudgetSnapshotV1(_StrictModel):
    tool_invocations: int = Field(ge=0)
    catalog_pages: int = Field(ge=0)
    document_candidates: int = Field(ge=0)
    search_rounds: int = Field(ge=0)
    model_visible_items: int = Field(ge=0)
    provider_invocations: int = Field(ge=0)
    context_tokens: int = Field(ge=0)
    tool_tokens: int = Field(ge=0)


class RuntimeTraceDetail(_StrictModel):
    execution_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    state: ExecutionState
    version: int = Field(ge=1)
    reasoning_mode: ReasoningMode
    reasoning_trace: ReasoningTraceV3 | None
    failure_code: str | None
    applied_guidance_revision: int = Field(ge=0)
    applied_guidance_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    budget: BudgetSnapshotV1
    model_visible_item_count: int = Field(ge=0)
    model_visible_item_limit: int = Field(ge=0)
    model_visible_item_exceeded: bool
    document_discovery: list[WorkspaceDiscoveryTraceV1]
    events: list[RuntimeEventV1]
    audit_steps: list[TurnAuditStepV1] = Field(max_length=40)
    created_at: AwareDatetime
    updated_at: AwareDatetime
