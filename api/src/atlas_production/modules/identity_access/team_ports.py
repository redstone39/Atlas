from __future__ import annotations

from typing import ContextManager, Protocol

from atlas_production.shared.public import (
    AuditEventRecord,
)
from .api_models import TeamCreateRequest
from .records import (
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from .team_contracts import TeamActionOutcome, TeamAuditCommand


class TeamAccessRepository(Protocol):
    def create_team_once(
        self,
        actor: UserRecord,
        payload: TeamCreateRequest,
    ) -> TeamActionOutcome: ...

    def team_mutation(
        self,
        team_id: str,
        *,
        actor_ids: tuple[str, ...] = (),
        include_hierarchy: bool = False,
    ) -> ContextManager[None]: ...

    def get_team(self, team_id: str) -> TeamRecord | None: ...

    def list_teams(self) -> list[TeamRecord]: ...

    def put_team(self, team: TeamRecord) -> None: ...

    def get_user(self, actor_id: str) -> UserRecord | None: ...

    def list_users(self) -> list[UserRecord]: ...

    def get_membership(self, membership_id: str) -> TeamMembershipRecord | None: ...

    def list_memberships(self) -> list[TeamMembershipRecord]: ...

    def put_membership(self, membership: TeamMembershipRecord) -> None: ...

    def is_system_admin(self, actor: UserRecord) -> bool: ...

    def direct_team_roles(self, actor: UserRecord) -> dict[str, str]: ...

    def can_manage_team(self, actor: UserRecord, team_id: str) -> bool: ...

    def would_create_cycle(self, team_id: str, parent_team_id: str | None) -> bool: ...

    def would_exceed_depth(self, team_id: str, parent_team_id: str | None) -> bool: ...

    def active_direct_human_admin_count(self, team_id: str) -> int: ...

    def append_audit(self, command: TeamAuditCommand) -> AuditEventRecord: ...
