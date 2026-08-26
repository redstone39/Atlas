from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .api_models import StartAgentResearchV1
from .contracts import (
    AgentResearchAcceptanceV1,
    AgentResearchAuditSummaryV1,
    AgentResearchRecordV1,
)


class AgentResearchAuthority(Protocol):
    def identify_replay_actor(self, *, raw_token: str | None) -> str | None: ...

    def accept_research(
        self,
        *,
        raw_token: str | None,
        payload: StartAgentResearchV1,
        research_id: str,
        execution_id: str,
        request_digest: str,
    ) -> AgentResearchAcceptanceV1: ...


class AgentResearchStore(Protocol):
    def find(self, research_id: str) -> AgentResearchRecordV1 | None: ...

    def find_replay(
        self, *, actor_id: str, idempotency_key: str
    ) -> AgentResearchRecordV1 | None: ...

    def list_audit_summaries(
        self,
        *,
        after: tuple[datetime, str] | None,
        upper: tuple[datetime, str] | None,
        limit: int,
    ) -> list[AgentResearchAuditSummaryV1]: ...



class AgentResearchAuditEvent(Protocol):
    event_id: str


class AgentResearchAuditWriter(Protocol):
    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        project_id: str | None,
        message_code: str,
        metadata: dict[str, object],
    ) -> AgentResearchAuditEvent: ...
