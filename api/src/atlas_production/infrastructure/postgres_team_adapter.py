from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Callable

from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_audit_adapter import (
    build_audit_event,
    persist_rejection_audit,
)
from atlas_production.infrastructure.postgres_owner.identity import IdentityRepository
from atlas_production.infrastructure.postgres_owner.team import (
    TeamAuthorizationConflict,
    TeamCurrentnessConflict,
    TeamGovernanceChangeSet,
    TeamInvariantViolation,
    TeamRepository,
)
from atlas_production.modules.identity_access.records import (
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.team_contracts import (
    TeamAccessError,
    TeamAuditCommand,
)
from atlas_production.modules.identity_access.team_ports import TeamAccessRepository
from atlas_production.modules.identity_access.directory_ports import (
    ScopedDirectoryIdentityCapability,
    ScopedDirectoryImportCommitPort,
)
from atlas_production.modules.identity_access.team_service import TeamAccessService
from atlas_production.rbac import TEAM_ROLE_ORDER
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]
_MAX_TEAM_DEPTH = 5


@dataclass(slots=True)
class _TeamMutationBuffer:
    team_id: str
    include_hierarchy: bool
    actor_ids: tuple[str, ...]
    teams: dict[str, TeamRecord] = field(default_factory=dict)
    original_teams: dict[str, TeamRecord | None] = field(default_factory=dict)
    memberships: dict[str, TeamMembershipRecord] = field(default_factory=dict)
    original_memberships: dict[
        str, TeamMembershipRecord | None
    ] = field(default_factory=dict)
    audit_events: list[AuditEventRecord] = field(default_factory=list)
    committed: bool = False


class PostgresTeamAccessRepository(TeamAccessRepository):
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory
        self.owner = TeamRepository(session_factory)
        self.identity_owner = IdentityRepository(session_factory)
        self._buffer: ContextVar[_TeamMutationBuffer | None] = ContextVar(
            f"atlas_postgres_team_buffer_{id(self)}",
            default=None,
        )

    @contextmanager
    def team_mutation(
        self,
        team_id: str,
        *,
        actor_ids: tuple[str, ...] = (),
        include_hierarchy: bool = False,
    ) -> AbstractContextManager[None]:
        existing = self._buffer.get()
        if existing is not None:
            yield
            return
        token = self._buffer.set(
            _TeamMutationBuffer(team_id, include_hierarchy, actor_ids)
        )
        try:
            yield
        finally:
            self._buffer.reset(token)

    def get_team(self, team_id: str) -> TeamRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and team_id in buffer.teams:
            return replace(buffer.teams[team_id])
        team = self.owner.get_team(team_id)
        if buffer is not None and team_id not in buffer.original_teams:
            buffer.original_teams[team_id] = replace(team) if team else None
        return replace(team) if team else None

    def list_teams(self) -> list[TeamRecord]:
        teams = {item.team_id: item for item in self._all_teams()}
        buffer = self._buffer.get()
        if buffer is not None:
            teams.update(buffer.teams)
        return [replace(teams[key]) for key in sorted(teams)]

    def put_team(self, team: TeamRecord) -> None:
        buffer = self._require_buffer()
        if team.team_id not in buffer.original_teams:
            buffer.original_teams[team.team_id] = self.owner.get_team(team.team_id)
        buffer.teams[team.team_id] = replace(team)

    def get_user(self, actor_id: str) -> UserRecord | None:
        user = self.identity_owner.get_user(actor_id)
        return replace(user) if user else None

    def list_users(self) -> list[UserRecord]:
        return [replace(item) for item in self._all_users()]

    def get_membership(self, membership_id: str) -> TeamMembershipRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and membership_id in buffer.memberships:
            return replace(buffer.memberships[membership_id])
        membership = self.owner.get_membership(membership_id)
        if buffer is not None and membership_id not in buffer.original_memberships:
            buffer.original_memberships[membership_id] = (
                replace(membership) if membership else None
            )
        return replace(membership) if membership else None

    def list_memberships(self) -> list[TeamMembershipRecord]:
        memberships = {
            item.membership_id: item for item in self._all_memberships()
        }
        buffer = self._buffer.get()
        if buffer is not None:
            memberships.update(buffer.memberships)
        return [replace(memberships[key]) for key in sorted(memberships)]

    def put_membership(self, membership: TeamMembershipRecord) -> None:
        buffer = self._require_buffer()
        if membership.membership_id not in buffer.original_memberships:
            buffer.original_memberships[membership.membership_id] = (
                self.owner.get_membership(membership.membership_id)
            )
        buffer.memberships[membership.membership_id] = replace(membership)

    def is_system_admin(self, actor: UserRecord) -> bool:
        current = self.identity_owner.get_user(actor.actor_id)
        return bool(
            current
            and current.actor_type == actor.actor_type
            and current.active
            and current.system_role == "admin"
        )

    def direct_team_roles(self, actor: UserRecord) -> dict[str, str]:
        current = self.identity_owner.get_user(actor.actor_id)
        if (
            current is None
            or not current.active
            or current.actor_type != actor.actor_type
        ):
            return {}
        roles: dict[str, str] = {}
        for membership in self.list_memberships():
            if (
                membership.member_actor_type != actor.actor_type
                or membership.member_actor_id != actor.actor_id
                or membership.status != "active"
            ):
                continue
            existing = roles.get(membership.team_id)
            if (
                existing is None
                or TEAM_ROLE_ORDER.get(membership.role, 0)
                > TEAM_ROLE_ORDER.get(existing, 0)
            ):
                roles[membership.team_id] = membership.role
        return roles

    def can_manage_team(self, actor: UserRecord, team_id: str) -> bool:
        return self.is_system_admin(actor) or self.direct_team_roles(actor).get(
            team_id
        ) == "admin"

    def would_create_cycle(
        self,
        team_id: str,
        parent_team_id: str | None,
    ) -> bool:
        parents = {item.team_id: item.parent_team_id for item in self.list_teams()}
        parents[team_id] = parent_team_id
        seen = {team_id}
        current_id = parent_team_id
        while current_id:
            if current_id in seen:
                return True
            seen.add(current_id)
            current_id = parents.get(current_id)
        return False

    def would_exceed_depth(
        self,
        team_id: str,
        parent_team_id: str | None,
    ) -> bool:
        parents = {item.team_id: item.parent_team_id for item in self.list_teams()}
        parents[team_id] = parent_team_id
        for candidate_id in parents:
            seen = {candidate_id}
            depth = 1
            current_id = parents[candidate_id]
            while current_id is not None:
                if current_id in seen or current_id not in parents:
                    return True
                seen.add(current_id)
                depth += 1
                if depth > _MAX_TEAM_DEPTH:
                    return True
                current_id = parents[current_id]
        return False

    def active_direct_human_admin_count(self, team_id: str) -> int:
        users = {item.actor_id: item for item in self._all_users()}
        return sum(
            1
            for membership in self.list_memberships()
            if membership.team_id == team_id
            and membership.member_actor_type == "user"
            and membership.status == "active"
            and membership.role == "admin"
            and (user := users.get(membership.member_actor_id)) is not None
            and user.active
        )

    def append_audit(self, command: TeamAuditCommand) -> AuditEventRecord:
        buffer = self._require_buffer()
        event = build_audit_event(
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=None,
            message_code=command.message_code,
            metadata=command.metadata,
            message_params=command.message_params,
            scope_type=command.scope_type,
            scope_id=command.scope_id,
        )
        buffer.audit_events.append(event)
        try:
            self.owner.team_governance(
                TeamGovernanceChangeSet(
                    teams=tuple(buffer.teams.values()),
                    expected_teams=tuple(
                        (team_id, buffer.original_teams.get(team_id))
                        for team_id in buffer.teams
                    ),
                    memberships=tuple(buffer.memberships.values()),
                    expected_memberships=tuple(
                        (
                            membership_id,
                            buffer.original_memberships.get(membership_id),
                        )
                        for membership_id in buffer.memberships
                    ),
                    audit_events=tuple(buffer.audit_events),
                    protect_hierarchy=buffer.include_hierarchy,
                    protected_admin_team_ids=(
                        (buffer.team_id,) if buffer.memberships else ()
                    ),
                    authorization_actor_ids=buffer.actor_ids,
                    authorization_team_id=(
                        None if buffer.include_hierarchy else buffer.team_id
                    ),
                    authorization_requires_system_admin=buffer.include_hierarchy,
                )
            )
            buffer.committed = True
        except TeamAuthorizationConflict as exc:
            raise TeamAccessError(
                "access_denied",
                (
                    'permission.admin_permission_is_required'
                    if buffer.include_hierarchy
                    else 'team.member_management_requires_team_admin_access'
                ),
                403,
            ) from exc
        except TeamInvariantViolation as exc:
            rejection = persist_rejection_audit(
                self.session_factory,
                candidate=event,
                message_code=(
                    'team.keep_at_least_one_direct_team_admin'
                    if "direct human Team Admin" in str(exc)
                    else 'team.parent_was_not_found'
                ),
                reason="commit_time_team_invariant",
            )
            message = str(exc)
            if "direct human Team Admin" in message:
                raise TeamAccessError(
                    "team_admin_required",
                    'team.keep_at_least_one_direct_team_admin',
                    422,
                    rejection.event_id,
                ) from exc
            if "acyclic" in message:
                code = 'team.parent_would_create_cycle'
            elif "depth" in message:
                code = 'team.depth_limit_exceeded'
            else:
                code = 'team.parent_was_not_found'
            raise TeamAccessError(
                "admin_action_rejected",
                code,
                422,
                rejection.event_id,
            ) from exc
        except TeamCurrentnessConflict as exc:
            rejection = persist_rejection_audit(
                self.session_factory,
                candidate=event,
                message_code='team.member_actor_was_not_found',
                reason="commit_time_team_currentness",
            )
            raise TeamAccessError(
                "admin_action_rejected",
                'team.member_actor_was_not_found',
                409,
                rejection.event_id,
            ) from exc
        return event

    def _require_buffer(self) -> _TeamMutationBuffer:
        buffer = self._buffer.get()
        if buffer is None:
            raise RuntimeError("team mutation requires team_mutation context")
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
            page = self.owner.list_teams(
                limit=500,
                after_team_id=after_team_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_team_id = page[-1].team_id

    def _all_memberships(self) -> list[TeamMembershipRecord]:
        result: list[TeamMembershipRecord] = []
        after_membership_id: str | None = None
        while True:
            page = self.owner.list_memberships(
                limit=500,
                after_membership_id=after_membership_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_membership_id = page[-1].membership_id


def build_postgres_team_access(
    session_factory: SessionFactory,
    directory_identity: ScopedDirectoryIdentityCapability,
    directory_import_commit: ScopedDirectoryImportCommitPort,
) -> TeamAccessService:
    return TeamAccessService(
        PostgresTeamAccessRepository(session_factory),
        directory_identity,
        directory_import_commit,
    )


__all__ = [
    "PostgresTeamAccessRepository",
    "build_postgres_team_access",
]
