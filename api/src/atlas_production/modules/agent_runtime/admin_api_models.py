from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from atlas_production.modules.audit.public import AuditEvent, TurnAuditStepV1
from atlas_production.modules.citation_preview.public import CitationBindingDraftV2
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.result_governance.public import GovernedAnswerDraftV2
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionPromptSkillSelectionTraceV1,
    ExecutionState,
    ReasoningTraceV4,
    RuntimeEventV1,
)

from .api_models import (
    AcceptedScopeSnapshotV1,
    ResearchPacketV1,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptedResearchAuditListItemV1(_StrictModel):
    kind: Literal["accepted"] = "accepted"
    research_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    status: Literal["accepted", "completed"]
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"]
    occurred_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class DeniedResearchAuditListItemV1(_StrictModel):
    kind: Literal["denied"] = "denied"
    event_id: str = Field(min_length=1, max_length=200)
    actor_id: str | None = Field(default=None, max_length=200)
    message_code: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime


AgentResearchAuditListItemV1 = Annotated[
    AcceptedResearchAuditListItemV1 | DeniedResearchAuditListItemV1,
    Field(discriminator="kind"),
]


class AgentResearchAuditListV1(_StrictModel):
    items: list[AgentResearchAuditListItemV1]
    next_cursor: str | None = None


class AgentResearchAdminAnswerV1(_StrictModel):
    status: Literal["not_requested", "available", "unavailable"]
    packet_ref: str = Field(min_length=1, max_length=300)
    packet_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    governed_answer: GovernedAnswerDraftV2 | None = None
    citations: CitationBindingDraftV2 | None = None

    @model_validator(mode="after")
    def require_answer_shape(self) -> "AgentResearchAdminAnswerV1":
        has_payload = self.governed_answer is not None and self.citations is not None
        if self.status == "available":
            if not has_payload:
                raise ValueError("available answer requires answer and citations")
        elif self.governed_answer is not None or self.citations is not None:
            raise ValueError("unavailable answer cannot expose partial result payloads")
        return self


class AgentResearchAuditDetailV1(_StrictModel):
    research_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=12_000)
    accepted_scope: AcceptedScopeSnapshotV1
    output_mode: Literal["evidence_packet", "evidence_packet_and_answer"]
    status: Literal["accepted", "completed"]
    packet: ResearchPacketV1 | None = None
    answer: AgentResearchAdminAnswerV1 | None = None
    business_events: list[AuditEvent]
    accepted_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class AgentResearchRuntimeDetailV1(_StrictModel):
    research_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=200)
    state: ExecutionState
    version: int = Field(ge=1)
    reasoning_mode: Literal["standard", "deep"]
    reasoning_trace: ReasoningTraceV4 | None
    prompt_skill_catalogs: list[PromptSkillCatalogRefV1] = Field(max_length=3)
    prompt_skill_selections: list[
        ExecutionPromptSkillSelectionTraceV1
    ] = Field(max_length=6)
    failure_code: str | None = Field(default=None, max_length=200)
    budget: BudgetSnapshotV1
    events: list[RuntimeEventV1] = Field(max_length=200)
    events_truncated: bool
    audit_steps: list[TurnAuditStepV1] = Field(max_length=40)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AgentResearchEvidenceContentV1(_StrictModel):
    research_id: str = Field(min_length=1, max_length=200)
    evidence_id: str = Field(min_length=1, max_length=200)
    representation: Literal["text", "visual", "native"]
    media_type: Literal["text/plain", "image/png", "application/pdf"]
    text: str | None = None
    content_base64: str | None = None

    @model_validator(mode="after")
    def require_representation_shape(self) -> "AgentResearchEvidenceContentV1":
        if self.representation == "text":
            if (
                self.media_type != "text/plain"
                or self.text is None
                or self.content_base64 is not None
            ):
                raise ValueError("text evidence requires only bounded text content")
        elif (
            self.text is not None
            or self.content_base64 is None
            or (
                self.representation == "visual"
                and self.media_type != "image/png"
            )
            or (
                self.representation == "native"
                and self.media_type != "application/pdf"
            )
        ):
            raise ValueError("binary evidence requires exact encoded media content")
        return self
