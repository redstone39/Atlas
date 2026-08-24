from __future__ import annotations

from typing import Protocol

from .api_models import AgentUserCreateRequest
from .records import (
    AgentTokenRecord,
    UserRecord,
)
from atlas_production.shared.public import (
    AuditEventRecord,
)
from .agent_contracts import (
    AgentAuditCommand,
    AgentCreateOutcome,
    AgentProjectGrantView,
)


class AgentAccessRepository(Protocol):
    def create_agent_once(
        self,
        actor: UserRecord,
        payload: AgentUserCreateRequest,
    ) -> AgentCreateOutcome: ...

    def get_user(self, actor_id: str) -> UserRecord | None: ...

    def list_users(self) -> list[UserRecord]: ...

    def put_user(self, user: UserRecord) -> None: ...

    def get_token(self, token_id: str) -> AgentTokenRecord | None: ...

    def list_tokens_for_agent(self, actor_id: str) -> list[AgentTokenRecord]: ...

    def put_token(self, token: AgentTokenRecord) -> None: ...

    def list_project_grants(self) -> list[AgentProjectGrantView]: ...

    def is_system_admin(self, actor: UserRecord) -> bool: ...

    def issue_token(self, actor_id: str) -> tuple[str, AgentTokenRecord]: ...

    def append_audit(self, command: AgentAuditCommand) -> AuditEventRecord: ...
