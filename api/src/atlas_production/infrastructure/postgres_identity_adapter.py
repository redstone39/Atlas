from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from secrets import token_urlsafe
from typing import Callable

from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_audit_adapter import (
    build_audit_event,
    persist_rejection_audit,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    ExpireInviteCommand,
    IdentityAuthorizationConflict,
    IdentityCurrentnessConflict,
    IdentityInvariantViolation,
    IdentityRepository,
    IdentityScopeAcceptanceChangeSet,
    IdentitySessionChangeSet,
    InviteTransition,
    IssueBrowserSessionCommand,
    RevokeBrowserSessionCommand,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    ProjectAclRepository,
)
from atlas_production.infrastructure.postgres_owner.team import TeamRepository
from atlas_production.modules.identity_access.api_models import (
    ProjectSummary,
    SessionState,
)
from atlas_production.modules.identity_access.contracts import (
    IdentityAccessError,
    IdentityAuditCommand,
)
from atlas_production.infrastructure.identity_projection import actor_context
from atlas_production.modules.identity_access.ports import (
    IdentityAccessRepository,
    InviteScopeGrantPort,
)
from atlas_production.modules.identity_access.directory_ports import DirectoryRepository
from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectorySecretRecord,
    ExternalIdentityRecord,
)
from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    TeamMembershipRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.security import invite_token_digest
from atlas_production.modules.identity_access.service import IdentityAccessService
from atlas_production.rbac import TEAM_ROLE_ORDER
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]
_PROJECT_SCOPE_ROLES = {
    "member": "viewer",
    "uploader": "contributor",
    "admin": "admin",
}


@dataclass(slots=True)
class _IdentityMutationBuffer:
    owner_key: str
    user_email: str | None
    protect_admin_count: bool
    authorization_actor_ids: tuple[str, ...]
    scope_type: str | None
    scope_id: str | None
    users: dict[str, UserRecord] = field(default_factory=dict)
    original_users: dict[str, UserRecord | None] = field(default_factory=dict)
    invites: dict[str, UserInviteRecord] = field(default_factory=dict)
    original_invites: dict[str, UserInviteRecord | None] = field(default_factory=dict)
    directory_connections: dict[str, DirectoryConnectionRecord] = field(
        default_factory=dict
    )
    original_directory_connections: dict[
        str, DirectoryConnectionRecord | None
    ] = field(default_factory=dict)
    directory_secrets: dict[tuple[str, str], DirectorySecretRecord] = field(
        default_factory=dict
    )
    original_directory_secrets: dict[
        tuple[str, str], DirectorySecretRecord | None
    ] = field(default_factory=dict)
    deleted_directory_secrets: set[tuple[str, str]] = field(default_factory=set)
    external_identities: dict[str, ExternalIdentityRecord] = field(
        default_factory=dict
    )
    original_external_identities: dict[
        str, ExternalIdentityRecord | None
    ] = field(default_factory=dict)
    sessions: list[tuple[str, str]] = field(default_factory=list)
    team_membership: TeamMembershipRecord | None = None
    expected_team_membership: TeamMembershipRecord | None = None
    project_grant: PermissionGrantRecord | None = None
    expected_project_grant: PermissionGrantRecord | None = None
    audit_events: list[AuditEventRecord] = field(default_factory=list)
    committed: bool = False


class PostgresIdentityAccessRepository(DirectoryRepository):
    """Exact route-facing Identity port backed by named PostgreSQL owners."""

    def __init__(
        self,
        session_factory: SessionFactory,
        acl_authority: ActionAwareAclAuthority | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.owner = IdentityRepository(session_factory)
        self.team_owner = TeamRepository(session_factory)
        self.project_owner = ProjectAclRepository(session_factory)
        self.acl_authority = acl_authority or ActionAwareAclAuthority(session_factory)
        self.issue_session_command = IssueBrowserSessionCommand(session_factory)
        self.revoke_session_command = RevokeBrowserSessionCommand(session_factory)
        self.expire_invite_command = ExpireInviteCommand(session_factory)
        self._buffer: ContextVar[_IdentityMutationBuffer | None] = ContextVar(
            f"atlas_postgres_identity_buffer_{id(self)}",
            default=None,
        )

    @contextmanager
    def identity_mutation(
        self,
        owner_key: str,
        *,
        actor_ids: tuple[str, ...] = (),
        authorization_actor_ids: tuple[str, ...] = (),
        user_email: str | None = None,
        invite_id: str | None = None,
        invite_digest: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        protect_admin_count: bool = False,
    ) -> AbstractContextManager[None]:
        del actor_ids, invite_id, invite_digest
        existing = self._buffer.get()
        if existing is not None:
            yield
            return
        token = self._buffer.set(
            _IdentityMutationBuffer(
                owner_key=owner_key,
                user_email=user_email,
                protect_admin_count=protect_admin_count,
                authorization_actor_ids=authorization_actor_ids,
                scope_type=scope_type,
                scope_id=scope_id,
            )
        )
        try:
            yield
        finally:
            self._buffer.reset(token)
    @contextmanager
    def directory_mutation(
        self,
        owner_key: str,
        *,
        actor_ids: tuple[str, ...] = (),
        authorization_actor_ids: tuple[str, ...] = (),
        connection_ids: tuple[str, ...] = (),
    ) -> AbstractContextManager[None]:
        del connection_ids
        with self.identity_mutation(
            owner_key,
            actor_ids=actor_ids,
            authorization_actor_ids=authorization_actor_ids,
        ):
            yield

    def actor_for_token(self, token: str | None) -> UserRecord | None:
        return self.owner.actor_for_token(token)

    def session_state(self, user: UserRecord) -> SessionState:
        current = self.owner.get_user(user.actor_id)
        if (
            current is None
            or current.actor_type != "user"
            or not current.active
        ):
            return SessionState(
                authenticated=False,
                actor=None,
                available_projects=[],
                system_role=None,
            )
        projects: list[ProjectSummary] = []
        for project in self._all_projects():
            decision = self.acl_authority.resolve(
                actor_type=current.actor_type,
                actor_id=current.actor_id,
                project_id=project.project_id,
                action="workspace_query",
                persist=False,
            )
            if decision.allowed:
                projects.append(ProjectSummary(
                    project_id=project.project_id,
                    name=project.name,
                    membership_status="active",
                    role=decision.effective_role,
                ))
        return SessionState(
            authenticated=True,
            actor=actor_context(current),
            available_projects=projects,
            system_role=current.system_role,
            team_roles=self._direct_team_roles(current),
        )

    def user_by_email(self, email: str) -> UserRecord | None:
        normalized = email.strip().lower()
        buffer = self._buffer.get()
        if buffer is not None:
            for user in buffer.users.values():
                if user.email and user.email.lower() == normalized:
                    return replace(user)
        user = self.owner.user_by_email(normalized)
        self._remember_user(user.actor_id, user) if user is not None else None
        return replace(user) if user is not None else None

    def get_user(self, actor_id: str) -> UserRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and actor_id in buffer.users:
            return replace(buffer.users[actor_id])
        user = self.owner.get_user(actor_id)
        self._remember_user(actor_id, user)
        return replace(user) if user is not None else None

    def list_users(self) -> list[UserRecord]:
        users = {item.actor_id: item for item in self._all_users()}
        buffer = self._buffer.get()
        if buffer is not None:
            users.update(buffer.users)
        return [replace(users[key]) for key in sorted(users)]

    def put_user(self, user: UserRecord) -> None:
        buffer = self._require_buffer()
        if user.actor_id not in buffer.original_users:
            buffer.original_users[user.actor_id] = self.owner.get_user(user.actor_id)
        buffer.users[user.actor_id] = replace(user)
    def list_directory_connections(self) -> list[DirectoryConnectionRecord]:
        connections = {
            item.connection_id: item
            for item in self.owner.list_directory_connections()
        }
        buffer = self._buffer.get()
        if buffer is not None:
            connections.update(buffer.directory_connections)
        return [
            replace(connections[key])
            for key in sorted(
                connections,
                key=lambda key: (connections[key].priority, key),
            )
        ]

    def get_directory_connection(
        self, connection_id: str
    ) -> DirectoryConnectionRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and connection_id in buffer.directory_connections:
            return replace(buffer.directory_connections[connection_id])
        record = self.owner.get_directory_connection(connection_id)
        if buffer is not None:
            buffer.original_directory_connections.setdefault(connection_id, record)
        return replace(record) if record is not None else None

    def put_directory_connection(self, connection: DirectoryConnectionRecord) -> None:
        buffer = self._require_buffer()
        if connection.connection_id not in buffer.original_directory_connections:
            buffer.original_directory_connections[
                connection.connection_id
            ] = self.owner.get_directory_connection(connection.connection_id)
        buffer.directory_connections[connection.connection_id] = replace(connection)

    def expect_directory_connection(
        self, connection: DirectoryConnectionRecord
    ) -> None:
        buffer = self._require_buffer()
        buffer.original_directory_connections.setdefault(
            connection.connection_id,
            replace(connection),
        )

    def get_directory_secret(
        self, connection_id: str, secret_kind: str
    ) -> DirectorySecretRecord | None:
        key = (connection_id, secret_kind)
        buffer = self._buffer.get()
        if buffer is not None:
            if key in buffer.deleted_directory_secrets:
                return None
            if key in buffer.directory_secrets:
                return replace(buffer.directory_secrets[key])
        record = self.owner.get_directory_secret(connection_id, secret_kind)
        if buffer is not None:
            buffer.original_directory_secrets.setdefault(key, record)
        return replace(record) if record is not None else None

    def put_directory_secret(self, secret: DirectorySecretRecord) -> None:
        buffer = self._require_buffer()
        key = (secret.connection_id, secret.secret_kind)
        if key not in buffer.original_directory_secrets:
            buffer.original_directory_secrets[key] = self.owner.get_directory_secret(*key)
        buffer.deleted_directory_secrets.discard(key)
        buffer.directory_secrets[key] = replace(secret)

    def expect_directory_secret(self, secret: DirectorySecretRecord) -> None:
        buffer = self._require_buffer()
        buffer.original_directory_secrets.setdefault(
            (secret.connection_id, secret.secret_kind),
            replace(secret),
        )

    def delete_directory_secret(self, connection_id: str, secret_kind: str) -> None:
        buffer = self._require_buffer()
        key = (connection_id, secret_kind)
        if key not in buffer.original_directory_secrets:
            buffer.original_directory_secrets[key] = self.owner.get_directory_secret(*key)
        buffer.directory_secrets.pop(key, None)
        buffer.deleted_directory_secrets.add(key)

    def get_external_identity(
        self, actor_id: str
    ) -> ExternalIdentityRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and actor_id in buffer.external_identities:
            return replace(buffer.external_identities[actor_id])
        record = self.owner.get_external_identity(actor_id)
        if buffer is not None:
            buffer.original_external_identities.setdefault(actor_id, record)
        return replace(record) if record is not None else None

    def get_external_identity_by_subject(
        self, connection_id: str, external_subject: str
    ) -> ExternalIdentityRecord | None:
        buffer = self._buffer.get()
        if buffer is not None:
            matches = [
                item
                for item in buffer.external_identities.values()
                if item.connection_id == connection_id
                and item.external_subject == external_subject
            ]
            if len(matches) == 1:
                return replace(matches[0])
        record = self.owner.get_external_identity_by_subject(
            connection_id, external_subject
        )
        if record is not None and buffer is not None:
            buffer.original_external_identities.setdefault(record.actor_id, record)
        return replace(record) if record is not None else None

    def list_external_identities(self) -> list[ExternalIdentityRecord]:
        identities = {
            item.actor_id: item
            for item in self.owner.list_external_identities()
        }
        buffer = self._buffer.get()
        if buffer is not None:
            identities.update(buffer.external_identities)
        return [replace(identities[key]) for key in sorted(identities)]

    def put_external_identity(self, identity: ExternalIdentityRecord) -> None:
        buffer = self._require_buffer()
        if identity.actor_id not in buffer.original_external_identities:
            buffer.original_external_identities[
                identity.actor_id
            ] = self.owner.get_external_identity(identity.actor_id)
        buffer.external_identities[identity.actor_id] = replace(identity)

    def expect_external_identity(self, identity: ExternalIdentityRecord) -> None:
        buffer = self._require_buffer()
        buffer.original_external_identities.setdefault(
            identity.actor_id,
            replace(identity),
        )

    def stage_session(self, actor_id: str) -> str:
        buffer = self._require_buffer()
        token = token_urlsafe(24)
        buffer.sessions.append((token, actor_id))
        return token

    def issue_session(self, actor_id: str) -> str:
        return self.issue_session_command.execute(actor_id)

    def revoke_session(self, token: str | None) -> bool:
        return self.revoke_session_command.execute(token)

    def invite_for_token(self, raw_token: str | None) -> UserInviteRecord | None:
        if not raw_token:
            return None
        invite = self.owner.invite_by_digest(invite_token_digest(raw_token))
        if invite is not None:
            self._remember_invite(invite.invite_id, invite)
        return replace(invite) if invite is not None else None

    def pending_invite_for_email(self, email: str) -> UserInviteRecord | None:
        normalized = email.strip().lower()
        buffer = self._buffer.get()
        if buffer is not None:
            for invite in buffer.invites.values():
                if invite.email.lower() == normalized and invite.status == "pending":
                    return replace(invite)
        invite = self.owner.pending_invite_for_email(normalized)
        if invite is not None:
            self._remember_invite(invite.invite_id, invite)
        return replace(invite) if invite is not None else None

    def get_invite(self, invite_id: str) -> UserInviteRecord | None:
        buffer = self._buffer.get()
        if buffer is not None and invite_id in buffer.invites:
            return replace(buffer.invites[invite_id])
        invite = self.owner.get_invite(invite_id)
        self._remember_invite(invite_id, invite)
        return replace(invite) if invite is not None else None

    def list_invites(self) -> list[UserInviteRecord]:
        invites = {item.invite_id: item for item in self._all_invites()}
        buffer = self._buffer.get()
        if buffer is not None:
            invites.update(buffer.invites)
        return [replace(invites[key]) for key in sorted(invites)]

    def put_invite(self, invite: UserInviteRecord) -> None:
        buffer = self._require_buffer()
        if invite.invite_id not in buffer.original_invites:
            buffer.original_invites[invite.invite_id] = self.owner.get_invite(
                invite.invite_id
            )
        buffer.invites[invite.invite_id] = replace(invite)

    def is_system_admin(self, actor: UserRecord) -> bool:
        current = self.owner.get_user(actor.actor_id)
        return bool(
            current
            and current.actor_type == actor.actor_type
            and current.active
            and current.system_role == "admin"
        )

    def active_admin_count(self) -> int:
        count = self.owner.active_admin_count()
        buffer = self._buffer.get()
        if buffer is not None:
            for actor_id, user in buffer.users.items():
                before = buffer.original_users.get(actor_id)
                before_admin = bool(
                    before
                    and before.actor_type == "user"
                    and before.active
                    and before.system_role == "admin"
                )
                after_admin = bool(
                    user.actor_type == "user"
                    and user.active
                    and user.system_role == "admin"
                )
                count += int(after_admin) - int(before_admin)
        return count

    def append_audit(self, command: IdentityAuditCommand) -> AuditEventRecord:
        buffer = self._require_buffer()
        event = build_audit_event(
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=command.scope_id if command.scope_type == "project" else None,
            message_code=command.message_code,
            metadata=command.metadata,
            message_params=command.message_params,
            scope_type=command.scope_type,
            scope_id=command.scope_id,
        )
        buffer.audit_events.append(event)
        self._commit_buffer(buffer)
        return event

    def persist(self) -> None:
        buffer = self._buffer.get()
        if buffer is None or buffer.committed:
            return
        if (
            not buffer.users
            and len(buffer.invites) == 1
            and not buffer.audit_events
            and not buffer.team_membership
            and not buffer.project_grant
        ):
            invite = next(iter(buffer.invites.values()))
            self.expire_invite_command.execute(invite)
            buffer.committed = True
            return
        self._commit_buffer(buffer)

    def _stage_invite_scope(
        self,
        *,
        membership: TeamMembershipRecord | None = None,
        grant: PermissionGrantRecord | None = None,
    ) -> None:
        buffer = self._require_buffer()
        if (membership is None) == (grant is None):
            raise ValueError("invite scope requires exactly one owner record")
        buffer.team_membership = replace(membership) if membership else None
        buffer.project_grant = replace(grant) if grant else None
        buffer.expected_team_membership = (
            self.team_owner.get_membership(membership.membership_id)
            if membership
            else None
        )
        buffer.expected_project_grant = (
            self.project_owner.get_grant(grant.grant_id) if grant else None
        )

    def _commit_buffer(self, buffer: _IdentityMutationBuffer) -> None:
        if buffer.committed:
            return
        if not buffer.audit_events:
            raise ValueError("audited identity mutation is missing audit evidence")
        accepted_invites = [
            invite for invite in buffer.invites.values()
            if invite.status == "accepted"
        ]
        try:
            if buffer.team_membership is not None or buffer.project_grant is not None:
                if len(accepted_invites) != 1:
                    raise ValueError("scope acceptance requires one accepted invite")
                invite = accepted_invites[0]
                user = buffer.users.get(invite.actor_id)
                expected_user = buffer.original_users.get(invite.actor_id)
                if user is None or expected_user is None:
                    raise ValueError("scope acceptance requires an existing user preimage")
                self.owner.identity_scope_acceptance(
                    IdentityScopeAcceptanceChangeSet(
                        user=user,
                        expected_user=expected_user,
                        invite=invite,
                        team_membership=buffer.team_membership,
                        expected_team_membership=buffer.expected_team_membership,
                        project_grant=buffer.project_grant,
                        expected_project_grant=buffer.expected_project_grant,
                        audit_events=tuple(buffer.audit_events),
                    )
                )
            else:
                self.owner.identity_session(
                    IdentitySessionChangeSet(
                        users=tuple(buffer.users.values()),
                        expected_users=tuple(
                            (actor_id, buffer.original_users.get(actor_id))
                            for actor_id in buffer.users
                        ),
                        sessions=tuple(buffer.sessions),
                        directory_connections=tuple(
                            buffer.directory_connections.values()
                        ),
                        expected_directory_connections=tuple(
                            (connection_id, expected)
                            for connection_id, expected in (
                                buffer.original_directory_connections.items()
                            )
                        ),
                        directory_secrets=tuple(buffer.directory_secrets.values()),
                        expected_directory_secrets=tuple(
                            (connection_id, secret_kind, expected)
                            for (
                                connection_id,
                                secret_kind,
                            ), expected in buffer.original_directory_secrets.items()
                        ),
                        deleted_directory_secrets=tuple(
                            sorted(buffer.deleted_directory_secrets)
                        ),
                        external_identities=tuple(
                            buffer.external_identities.values()
                        ),
                        expected_external_identities=tuple(
                            (actor_id, expected)
                            for actor_id, expected in (
                                buffer.original_external_identities.items()
                            )
                        ),
                        reject_directory_alias_conflicts=bool(
                            buffer.external_identities
                        ),
                        invite_transitions=tuple(
                            InviteTransition(
                                record=invite,
                                expected_status=(
                                    buffer.original_invites[invite_id].status
                                    if buffer.original_invites.get(invite_id) is not None
                                    else None
                                ),
                            )
                            for invite_id, invite in buffer.invites.items()
                        ),
                        audit_events=tuple(buffer.audit_events),
                        protect_admin_count=buffer.protect_admin_count,
                        identity_lock_keys=(buffer.owner_key,),
                        expected_pending_invite_absent_emails=(
                            (buffer.user_email,)
                            if buffer.user_email
                            and all(
                                original is None
                                for original in buffer.original_invites.values()
                            )
                            else ()
                        ),
                        authorization_actor_id=self._authorization_actor(buffer),
                        authorization_scope_type=self._authorization_scope(buffer)[0],
                        authorization_scope_id=self._authorization_scope(buffer)[1],
                        authorization_requires_system_admin=(
                            self._authorization_actor(buffer) is not None
                            and self._authorization_scope(buffer) == (None, None)
                        ),
                    )
                )
            buffer.committed = True
        except IdentityAuthorizationConflict as exc:
            raise IdentityAccessError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            ) from exc
        except IdentityInvariantViolation as exc:
            rejection = persist_rejection_audit(
                self.session_factory,
                candidate=buffer.audit_events[-1],
                message_code='identity.active_admin_required',
                reason="commit_time_identity_invariant",
            )
            raise IdentityAccessError(
                "admin_action_rejected",
                'identity.active_admin_required',
                422,
                rejection.event_id,
            ) from exc
        except IdentityCurrentnessConflict as exc:
            if buffer.owner_key.startswith("identity-email:"):
                rejection = persist_rejection_audit(
                    self.session_factory,
                    candidate=buffer.audit_events[-1],
                    message_code='invite.already_pending_for_email',
                    reason="commit_time_pending_invite_conflict",
                )
                raise IdentityAccessError(
                    "admin_action_rejected",
                    'invite.already_pending_for_email',
                    409,
                    rejection.event_id,
                ) from exc
            if buffer.owner_key.startswith("identity:directory-"):
                raise IdentityAccessError(
                    "directory_conflict",
                    "directory.concurrent_change",
                    409,
                ) from exc
            raise IdentityAccessError(
                "admin_action_rejected",
                'invite.was_not_found_or_is_no_longer_valid',
                409,
            ) from exc

    @staticmethod
    def _authorization_actor(buffer: _IdentityMutationBuffer) -> str | None:
        if buffer.authorization_actor_ids:
            return buffer.authorization_actor_ids[0]
        for event in buffer.audit_events:
            if event.event_type == "user_lifecycle_updated":
                return event.actor_id
        return None

    @staticmethod
    def _authorization_scope(
        buffer: _IdentityMutationBuffer,
    ) -> tuple[str | None, str | None]:
        if buffer.scope_type and buffer.scope_id:
            return buffer.scope_type, buffer.scope_id
        for invite in buffer.invites.values():
            if invite.scope_type and invite.scope_id:
                return invite.scope_type, invite.scope_id
        return None, None

    def _direct_team_roles(self, actor: UserRecord) -> dict[str, str]:
        roles: dict[str, str] = {}
        after_membership_id: str | None = None
        while True:
            page = self.team_owner.list_memberships(
                actor_id=actor.actor_id,
                limit=500,
                after_membership_id=after_membership_id,
            )
            for membership in page:
                if (
                    membership.member_actor_type != actor.actor_type
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
            if len(page) < 500:
                break
            after_membership_id = page[-1].membership_id
        return roles

    def _all_users(self) -> list[UserRecord]:
        result: list[UserRecord] = []
        after_actor_id: str | None = None
        while True:
            page = self.owner.list_users(
                limit=500,
                after_actor_id=after_actor_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_actor_id = page[-1].actor_id

    def _all_invites(self) -> list[UserInviteRecord]:
        result: list[UserInviteRecord] = []
        after_invite_id: str | None = None
        while True:
            page = self.owner.list_invites(
                limit=500,
                after_invite_id=after_invite_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_invite_id = page[-1].invite_id

    def _all_projects(self):
        result = []
        after_project_id: str | None = None
        while True:
            page = self.project_owner.list_projects(
                limit=500,
                after_project_id=after_project_id,
            )
            result.extend(page)
            if len(page) < 500:
                return result
            after_project_id = page[-1].project_id

    def _remember_user(self, actor_id: str, user: UserRecord | None) -> None:
        buffer = self._buffer.get()
        if buffer is not None and actor_id not in buffer.original_users:
            buffer.original_users[actor_id] = replace(user) if user else None

    def _remember_invite(
        self,
        invite_id: str,
        invite: UserInviteRecord | None,
    ) -> None:
        buffer = self._buffer.get()
        if buffer is not None and invite_id not in buffer.original_invites:
            buffer.original_invites[invite_id] = replace(invite) if invite else None

    def _require_buffer(self) -> _IdentityMutationBuffer:
        buffer = self._buffer.get()
        if buffer is None:
            raise RuntimeError("identity mutation requires identity_mutation context")
        return buffer


class PostgresInviteScopeGrantAdapter(InviteScopeGrantPort):
    def __init__(
        self,
        identity: PostgresIdentityAccessRepository,
    ) -> None:
        self.identity = identity

    def validate_scope_values(
        self,
        scope_type: str | None,
        scope_id: str | None,
        scope_role: str | None,
        request_id: str,
    ) -> IdentityAccessError | None:
        del request_id
        if scope_type is None and scope_id is None and scope_role is None:
            return None
        if (
            scope_type not in {"team", "project"}
            or not scope_id
            or scope_role not in _PROJECT_SCOPE_ROLES
        ):
            return IdentityAccessError(
                "admin_action_rejected",
                'invite.scope_was_not_valid',
                422,
                "audit-user-invite-rejected",
            )
        if scope_type == "team":
            team = self.identity.team_owner.get_team(scope_id)
            if team is None or team.status != "active":
                return IdentityAccessError(
                    "admin_action_rejected",
                    'team.was_not_found',
                    404,
                    "audit-user-invite-rejected",
                )
        elif self.identity.project_owner.get_project(scope_id) is None:
            return IdentityAccessError(
                "admin_action_rejected",
                'project.was_not_found',
                404,
                "audit-user-invite-rejected",
            )
        return None

    def can_manage_scope(
        self,
        actor: UserRecord,
        scope_type: str,
        scope_id: str,
    ) -> bool:
        if self.identity.is_system_admin(actor):
            return True
        current = self.identity.owner.get_user(actor.actor_id)
        if (
            current is None
            or not current.active
            or current.actor_type != actor.actor_type
        ):
            return False
        if scope_type == "team":
            return self.identity._direct_team_roles(current).get(scope_id) == "admin"
        if scope_type != "project":
            return False
        return self.identity.acl_authority.resolve(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            project_id=scope_id,
            action="permission_manage",
            persist=True,
        ).allowed

    def apply_invite_scope(self, invite: UserInviteRecord) -> None:
        if not invite.scope_type or not invite.scope_id or not invite.scope_role:
            return
        if invite.scope_type == "team":
            membership_id = f"tm-{invite.scope_id}-{invite.actor_id}"
            current = self.identity.team_owner.get_membership(membership_id)
            membership = replace(
                current,
                role=invite.scope_role,
                status="active",
                removed_at=None,
            ) if current else TeamMembershipRecord(
                membership_id=membership_id,
                team_id=invite.scope_id,
                member_actor_type="user",
                member_actor_id=invite.actor_id,
                role=invite.scope_role,
                status="active",
                created_at=utc_now_iso(),
            )
            self.identity._stage_invite_scope(membership=membership)
            return
        grant = PermissionGrantRecord(
            grant_id=f"grant-invite-{invite.invite_id}",
            project_id=invite.scope_id,
            subject_type="user",
            subject_id=invite.actor_id,
            role=_PROJECT_SCOPE_ROLES[invite.scope_role],
            effect="allow",
            status="active",
            created_at=utc_now_iso(),
        )
        self.identity._stage_invite_scope(grant=grant)


@dataclass(frozen=True, slots=True)
class PostgresCurrentPrincipal:
    identity: PostgresIdentityAccessRepository

    def current_user(self, session_token: str | None) -> UserRecord | None:
        return self.identity.actor_for_token(session_token)

    def is_admin(self, session_token: str | None) -> bool:
        actor = self.current_user(session_token)
        return bool(actor and self.identity.is_system_admin(actor))


def build_postgres_identity_access(
    session_factory: SessionFactory,
    acl_authority: ActionAwareAclAuthority | None = None,
) -> tuple[IdentityAccessService, PostgresCurrentPrincipal]:
    repository = PostgresIdentityAccessRepository(session_factory, acl_authority)
    service = IdentityAccessService(
        repository,
        PostgresInviteScopeGrantAdapter(repository),
    )
    return service, PostgresCurrentPrincipal(repository)


__all__ = [
    "PostgresCurrentPrincipal",
    "PostgresIdentityAccessRepository",
    "PostgresInviteScopeGrantAdapter",
    "build_postgres_identity_access",
]
