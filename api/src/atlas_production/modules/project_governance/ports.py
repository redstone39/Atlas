from __future__ import annotations

from typing import Literal, Protocol

from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.shared.public import (
    AuditEventRecord,
)
from .records import ProjectRecord
from .contracts import ProjectAuditCommand


class ProjectGovernanceRepository(Protocol):
    def get_project(self, project_id: str) -> ProjectRecord | None: ...

    def list_projects(self) -> list[ProjectRecord]: ...

    def put_project(
        self,
        project: ProjectRecord,
        *,
        expected_project: ProjectRecord | None,
        authorization: Literal["system_admin", "permission_manage"],
    ) -> None: ...

    def get_user(self, actor_id: str) -> UserRecord | None: ...

    def list_users(self) -> list[UserRecord]: ...

    def get_team(self, team_id: str) -> TeamRecord | None: ...

    def list_teams(self) -> list[TeamRecord]: ...

    def get_grant(self, grant_id: str) -> PermissionGrantRecord | None: ...

    def list_grants(self) -> list[PermissionGrantRecord]: ...

    def put_grant(self, grant: PermissionGrantRecord) -> None: ...

    def is_system_admin(self, actor: UserRecord) -> bool: ...

    def resolve_access(
        self,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
        action: str,
        persist: bool = True,
    ) -> AccessDecisionRecord: ...

    def append_audit(self, command: ProjectAuditCommand) -> AuditEventRecord: ...

    def persist(self) -> None: ...
