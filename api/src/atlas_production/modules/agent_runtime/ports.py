from __future__ import annotations

from typing import Protocol

from .contracts import AgentQueryAuthorizationV1


class AgentQueryAuthority(Protocol):
    def authorize(
        self, *, raw_token: str | None, project_id: str
    ) -> AgentQueryAuthorizationV1: ...


class AgentQueryAuditEvent(Protocol):
    event_id: str


class AgentQueryAuditWriter(Protocol):
    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        project_id: str | None,
        message_code: str,
        metadata: dict[str, object],
    ) -> AgentQueryAuditEvent: ...
