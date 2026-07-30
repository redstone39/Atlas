from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Callable

from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_audit_adapter import (
    build_audit_event,
    persist_rejection_audit,
)
from atlas_production.infrastructure.postgres_owner.identity import IdentityRepository
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    ProjectAclChangeSet,
    ProjectAclRepository,
    ProjectAuthorizationConflict,
    ProjectCurrentnessConflict,
)
from atlas_production.infrastructure.postgres_owner.team import TeamRepository
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.contracts import (
    ProjectAuditCommand,
    ProjectGovernanceError,
)
from atlas_production.modules.project_governance.ports import (
    ProjectGovernanceRepository,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.modules.project_governance.service import (
    ProjectGovernanceService,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]


@dataclass(slots=True)
class _ProjectMutationBuffer:
    projects: dict[str, ProjectRecord] = field(default_factory=dict)
    original_projects: dict[str, ProjectRecord | None] = field(default_factory=dict)
    grants: dict[str, PermissionGrantRecord] = field(default_factory=dict)
    original_grants: dict[
        str, PermissionGrantRecord | None
    ] = field(default_factory=dict)
    audit_events: list[AuditEventRecord] = field(default_factory=list)
    committed: bool = False


class PostgresProjectGovernanceRepository(ProjectGovernanceRepository):
    def __init__(
        self,
        session_factory: SessionFactory,
        acl_authority: ActionAwareAclAuthority | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.owner = ProjectAclRepository(session_factory)
        self.identity_owner = IdentityRepository(session_factory)
        self.team_owner = TeamRepository(session_factory)
        self.acl_authority = acl_authority or ActionAwareAclAuthority(session_factory)
        self._buffer: ContextVar[_ProjectMutationBuffer | None] = ContextVar(
            f"atlas_postgres_project_buffer_{id(self)}",
            default=None,
        )

    def get_project(self, project_id: str) -> ProjectRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and project_id in buffer.projects:
            return replace(buffer.projects[project_id])
        project = self.owner.get_project(project_id)
        if buffer is not None and project_id not in buffer.original_projects:
            buffer.original_projects[project_id] = replace(project) if project else None
        return replace(project) if project else None

    def list_projects(self) -> list[ProjectRecord]:
        projects = {item.project_id: item for item in self._all_projects()}
        buffer = self._buffer.get()
        if buffer is not None:
            projects.update(buffer.projects)
        return [replace(projects[key]) for key in sorted(projects)]

    def put_project(self, project: ProjectRecord) -> None:
        buffer = self._pending()
        if project.project_id not in buffer.original_projects:
            buffer.original_projects[project.project_id] = self.owner.get_project(
                project.project_id
            )
        buffer.projects[project.project_id] = replace(project)

    def get_user(self, actor_id: str) -> UserRecord | None:
        user = self.identity_owner.get_user(actor_id)
        return replace(user) if user else None

    def list_users(self) -> list[UserRecord]:
        return [replace(item) for item in self._all_users()]

    def get_team(self, team_id: str) -> TeamRecord | None:
        team = self.team_owner.get_team(team_id)
        return replace(team) if team else None

    def list_teams(self) -> list[TeamRecord]:
        return [replace(item) for item in self._all_teams()]

    def get_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and grant_id in buffer.grants:
            return replace(buffer.grants[grant_id])
        grant = self.owner.get_grant(grant_id)
        if buffer is not None and grant_id not in buffer.original_grants:
            buffer.original_grants[grant_id] = replace(grant) if grant else None
        return replace(grant) if grant else None

    def list_grants(self) -> list[PermissionGrantRecord]:
        grants = {item.grant_id: item for item in self._all_grants()}
        buffer = self._buffer.get()
        if buffer is not None:
            grants.update(buffer.grants)
        return [replace(grants[key]) for key in sorted(grants)]

    def put_grant(self, grant: PermissionGrantRecord) -> None:
        buffer = self._pending()
        if grant.grant_id not in buffer.original_grants:
            buffer.original_grants[grant.grant_id] = self.owner.get_grant(
                grant.grant_id
            )
        buffer.grants[grant.grant_id] = replace(grant)

    def is_system_admin(self, actor: UserRecord) -> bool:
        current = self.identity_owner.get_user(actor.actor_id)
        return bool(
            current
            and current.actor_type == actor.actor_type
            and current.active
            and current.system_role == "admin"
        )

    def resolve_access(
        self,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
        action: str,
        persist: bool = True,
    ) -> AccessDecisionRecord:
        return self.acl_authority.resolve(
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            persist=persist,
        )

    def append_audit(self, command: ProjectAuditCommand) -> AuditEventRecord:
        buffer = self._pending()
        event = build_audit_event(
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=command.project_id,
            message_code=command.message_code,
            metadata=command.metadata,
            message_params=command.message_params,
            scope_type="project",
            scope_id=command.project_id,
        )
        buffer.audit_events.append(event)
        try:
            requires_system_admin = bool(buffer.projects)
            self.owner.project_acl(
                ProjectAclChangeSet(
                    projects=tuple(buffer.projects.values()),
                    expected_projects=tuple(
                        (project_id, buffer.original_projects.get(project_id))
                        for project_id in buffer.projects
                    ),
                    grants=tuple(buffer.grants.values()),
                    expected_grants=tuple(
                        (grant_id, buffer.original_grants.get(grant_id))
                        for grant_id in buffer.grants
                    ),
                    audit_events=tuple(buffer.audit_events),
                    authorization_actor_id=(
                        command.actor_id
                        if buffer.projects or buffer.grants
                        else None
                    ),
                    authorization_project_id=(
                        command.project_id if buffer.grants and not buffer.projects else None
                    ),
                    authorization_action=(
                        "permission_manage"
                        if buffer.grants and not buffer.projects
                        else None
                    ),
                    authorization_requires_system_admin=requires_system_admin,
                )
            )
            buffer.committed = True
        except ProjectAuthorizationConflict as exc:
            raise ProjectGovernanceError(
                "access_denied",
                (
                    'permission.admin_permission_is_required'
                    if buffer.projects
                    else 'project.members_require_project_admin_access'
                ),
                403,
            ) from exc
        except ProjectCurrentnessConflict as exc:
            rejection = persist_rejection_audit(
                self.session_factory,
                candidate=event,
                message_code='project.was_not_found',
                reason="commit_time_project_currentness",
            )
            raise ProjectGovernanceError(
                "admin_action_rejected",
                'project.was_not_found',
                409,
                rejection.event_id,
            ) from exc
        finally:
            self._buffer.set(None)
        return event

    def persist(self) -> None:
        buffer = self._buffer.get()
        if buffer is None or buffer.committed:
            return
        if buffer.projects or buffer.grants:
            raise ValueError("project mutation requires audit evidence")

    def _pending(self) -> _ProjectMutationBuffer:
        buffer = self._buffer.get()
        if buffer is None:
            buffer = _ProjectMutationBuffer()
            self._buffer.set(buffer)
        return buffer

    def _all_users(self) -> list[UserRecord]:
        result: list[UserRecord] = []
        after_actor_id: str | None = None
        while True:
            page = self.identity_owner.list_users(
                limit=500,
                after_actor_id=after_actor_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_actor_id = page[-1].actor_id

    def _all_teams(self) -> list[TeamRecord]:
        result: list[TeamRecord] = []
        after_team_id: str | None = None
        while True:
            page = self.team_owner.list_teams(
                limit=500,
                after_team_id=after_team_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_team_id = page[-1].team_id

    def _all_projects(self) -> list[ProjectRecord]:
        result: list[ProjectRecord] = []
        after_project_id: str | None = None
        while True:
            page = self.owner.list_projects(
                limit=500,
                after_project_id=after_project_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_project_id = page[-1].project_id

    def _all_grants(self) -> list[PermissionGrantRecord]:
        result: list[PermissionGrantRecord] = []
        after_grant_id: str | None = None
        while True:
            page = self.owner.list_grants(
                limit=500,
                after_grant_id=after_grant_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_grant_id = page[-1].grant_id


def build_postgres_project_governance(
    session_factory: SessionFactory,
    acl_authority: ActionAwareAclAuthority | None = None,
) -> ProjectGovernanceService:
    return ProjectGovernanceService(
        PostgresProjectGovernanceRepository(session_factory, acl_authority)
    )


__all__ = [
    "PostgresProjectGovernanceRepository",
    "build_postgres_project_governance",
]
