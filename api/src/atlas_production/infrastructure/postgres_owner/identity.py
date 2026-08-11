from __future__ import annotations

from dataclasses import dataclass, replace
from secrets import token_urlsafe
from typing import Callable
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import identity_access
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAgentTokenRow,
    AtlasDirectoryConnectionRow,
    AtlasDirectoryConnectionSecretRow,
    AtlasExternalIdentityRow,
    AtlasPermissionGrantRow,
    AtlasSessionRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserInviteRow,
    AtlasUserRow,
    directory_connection_record,
    directory_connection_row,
    directory_secret_record,
    directory_secret_row,
    external_identity_record,
    external_identity_row,
)
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.infrastructure.postgres_owner.project import ProjectGrantWriter
from atlas_production.infrastructure.postgres_owner.team import TeamMembershipWriter
from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectorySecretRecord,
    ExternalIdentityRecord,
)
from atlas_production.modules.identity_access.records import (
    AgentTokenRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.local_pilot import (
    AdminBootstrapConfigurationError,
)
from atlas_production.modules.identity_access.security import (
    agent_token_digest,
    password_digest,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]
_PROJECT_SCOPE_ROLES = {
    "member": "viewer",
    "uploader": "contributor",
    "admin": "admin",
}


def _user_row(record: UserRecord) -> AtlasUserRow:
    return AtlasUserRow(
        actor_id=record.actor_id,
        display_name=record.display_name,
        email=record.email,
        system_role=record.system_role,
        password_digest=record.password_digest,
        active=record.active,
        actor_type=record.actor_type,
        created_at=record.created_at,
    )


def _invite_row(record: UserInviteRecord) -> AtlasUserInviteRow:
    return AtlasUserInviteRow(
        invite_id=record.invite_id,
        actor_id=record.actor_id,
        email=record.email,
        display_name=record.display_name,
        system_role=record.system_role,
        token_digest=record.token_digest,
        token_fingerprint=record.token_fingerprint,
        status=record.status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        accepted_at=record.accepted_at,
        revoked_at=record.revoked_at,
        scope_type=record.scope_type,
        scope_id=record.scope_id,
        scope_role=record.scope_role,
    )


def _agent_token_row(record: AgentTokenRecord) -> AtlasAgentTokenRow:
    return AtlasAgentTokenRow(
        token_id=record.token_id,
        actor_id=record.actor_id,
        token_digest=record.token_digest,
        token_fingerprint=record.token_fingerprint,
        status=record.status,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
    )


def _user_record(row: AtlasUserRow) -> UserRecord:
    return UserRecord(
        actor_id=row.actor_id,
        display_name=row.display_name,
        email=row.email,
        system_role=row.system_role,
        password_digest=row.password_digest,
        active=row.active,
        actor_type=row.actor_type,
        created_at=row.created_at,
    )


def _invite_record(row: AtlasUserInviteRow) -> UserInviteRecord:
    return UserInviteRecord(
        invite_id=row.invite_id,
        actor_id=row.actor_id,
        email=row.email,
        display_name=row.display_name,
        system_role=row.system_role,
        token_digest=row.token_digest,
        token_fingerprint=row.token_fingerprint,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        scope_role=row.scope_role,
    )


def _agent_token_record(row: AtlasAgentTokenRow) -> AgentTokenRecord:
    return AgentTokenRecord(
        token_id=row.token_id,
        actor_id=row.actor_id,
        token_digest=row.token_digest,
        token_fingerprint=row.token_fingerprint,
        status=row.status,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _membership_record(row: AtlasTeamMembershipRow) -> TeamMembershipRecord:
    return TeamMembershipRecord(
        membership_id=row.membership_id,
        team_id=row.team_id,
        member_actor_type=row.member_actor_type,
        member_actor_id=row.member_actor_id,
        role=row.role,
        status=row.status,
        created_at=row.created_at,
        removed_at=row.removed_at,
    )


def _grant_record(row: AtlasPermissionGrantRow) -> PermissionGrantRecord:
    return PermissionGrantRecord(
        grant_id=row.grant_id,
        project_id=row.project_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        role=row.role,
        effect=row.effect,
        status=row.status,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


@dataclass(frozen=True, slots=True)
class InviteTransition:
    record: UserInviteRecord
    expected_status: str | None


@dataclass(frozen=True, slots=True)
class IdentitySessionChangeSet:
    users: tuple[UserRecord, ...] = ()
    expected_users: tuple[tuple[str, UserRecord | None], ...] = ()
    sessions: tuple[tuple[str, str], ...] = ()
    deleted_session_tokens: tuple[str, ...] = ()
    invite_transitions: tuple[InviteTransition, ...] = ()
    agent_tokens: tuple[AgentTokenRecord, ...] = ()
    expected_agent_users: tuple[tuple[str, UserRecord], ...] = ()
    directory_connections: tuple[DirectoryConnectionRecord, ...] = ()
    expected_directory_connections: tuple[
        tuple[str, DirectoryConnectionRecord | None], ...
    ] = ()
    directory_secrets: tuple[DirectorySecretRecord, ...] = ()
    expected_directory_secrets: tuple[
        tuple[str, str, DirectorySecretRecord | None], ...
    ] = ()
    deleted_directory_secrets: tuple[tuple[str, str], ...] = ()
    external_identities: tuple[ExternalIdentityRecord, ...] = ()
    expected_external_identities: tuple[
        tuple[str, ExternalIdentityRecord | None], ...
    ] = ()
    reject_directory_alias_conflicts: bool = False
    audit_events: tuple[AuditEventRecord, ...] = ()
    protect_admin_count: bool = False
    protected_admin_team_ids: tuple[str, ...] = ()
    identity_lock_keys: tuple[str, ...] = ()
    expected_pending_invite_absent_emails: tuple[str, ...] = ()
    authorization_actor_id: str | None = None
    authorization_scope_type: str | None = None
    authorization_scope_id: str | None = None
    authorization_requires_system_admin: bool = False

    def __post_init__(self) -> None:
        has_mutation = any(
            (
                self.users,
                self.sessions,
                self.deleted_session_tokens,
                self.invite_transitions,
                self.agent_tokens,
                self.directory_connections,
                self.directory_secrets,
                self.deleted_directory_secrets,
                self.external_identities,
            )
        )
        if has_mutation and not self.audit_events:
            raise ValueError("identity mutation requires audit events")


@dataclass(frozen=True, slots=True)
class IdentityScopeAcceptanceChangeSet:
    user: UserRecord
    expected_user: UserRecord
    invite: UserInviteRecord
    team_membership: TeamMembershipRecord | None = None
    expected_team_membership: TeamMembershipRecord | None = None
    project_grant: PermissionGrantRecord | None = None
    expected_project_grant: PermissionGrantRecord | None = None
    audit_events: tuple[AuditEventRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.user, UserRecord):
            raise ValueError("scope acceptance requires exactly one user")
        if not isinstance(self.invite, UserInviteRecord):
            raise ValueError("scope acceptance requires exactly one invite")
        if not isinstance(self.expected_user, UserRecord):
            raise ValueError("scope acceptance requires one expected user preimage")
        if (self.team_membership is None) == (self.project_grant is None):
            raise ValueError("scope acceptance requires exactly one owner writer")
        if self.invite.status != "accepted":
            raise ValueError("scope acceptance must publish an accepted invite")
        if self.invite.accepted_at is None or self.invite.revoked_at is not None:
            raise ValueError("scope acceptance requires a current accepted invite")
        if not self.user.active:
            raise ValueError("scope acceptance requires an active user")
        if self.invite.actor_id != self.user.actor_id:
            raise ValueError("scope acceptance invite and user owners differ")
        if (
            self.user.actor_type != "user"
            or self.expected_user.actor_type != "user"
            or self.user.actor_id != self.expected_user.actor_id
            or self.user.created_at != self.expected_user.created_at
            or self.user.email != self.invite.email
            or self.user.display_name != self.invite.display_name
            or self.user.system_role != self.invite.system_role
        ):
            raise ValueError("scope acceptance user does not match invite or preimage")
        if not self.audit_events:
            raise ValueError("scope acceptance requires audit events")
        if self.team_membership is not None and (
            self.invite.scope_type != "team"
            or self.invite.scope_id != self.team_membership.team_id
            or self.invite.scope_role != self.team_membership.role
            or self.team_membership.member_actor_type != "user"
            or self.team_membership.member_actor_id != self.user.actor_id
            or self.team_membership.status != "active"
            or self.team_membership.removed_at is not None
        ):
            raise ValueError("scope acceptance Team writer does not match the invite")
        if self.project_grant is not None and (
            self.invite.scope_type != "project"
            or self.invite.scope_id != self.project_grant.project_id
            or _PROJECT_SCOPE_ROLES.get(self.invite.scope_role)
            != self.project_grant.role
            or self.project_grant.subject_type != "user"
            or self.project_grant.subject_id != self.user.actor_id
            or self.project_grant.effect != "allow"
            or self.project_grant.status != "active"
            or self.project_grant.revoked_at is not None
        ):
            raise ValueError("scope acceptance Project writer does not match the invite")


class IdentityCurrentnessConflict(RuntimeError):
    pass


class IdentityInvariantViolation(RuntimeError):
    pass


class IdentityAuthorizationConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalPilotAdminSeedReceipt:
    actor_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class SeedLocalPilotAdminCommand:
    """Create the fixed local-pilot admin only for an empty Identity owner."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        actor_id: str,
        display_name: str,
        email: str | None,
        password: str | None,
    ) -> LocalPilotAdminSeedReceipt:
        if not actor_id or not display_name:
            raise ValueError("local-pilot admin identity must be non-empty")

        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=("identity:local-pilot-bootstrap",),
                    identity_keys=(identity_actor_owner_key(actor_id),),
                )
                existing_actor_id = session.scalar(
                    select(AtlasUserRow.actor_id)
                    .order_by(AtlasUserRow.actor_id)
                    .limit(1)
                    .with_for_update()
                )
                if existing_actor_id is not None:
                    session.rollback()
                    return LocalPilotAdminSeedReceipt(
                        actor_id=actor_id,
                        created=False,
                    )

                normalized_email = (email or "").strip().lower()
                if not normalized_email or not password:
                    raise AdminBootstrapConfigurationError(
                        "identity_admin_bootstrap_configuration_required"
                    )
                if len(password) < 12:
                    raise AdminBootstrapConfigurationError(
                        "identity_admin_bootstrap_configuration_invalid"
                    )

                created_at = utc_now_iso()
                session.add(
                    AtlasUserRow(
                        actor_id=actor_id,
                        display_name=display_name,
                        email=normalized_email,
                        system_role="admin",
                        password_digest=password_digest(password),
                        active=True,
                        actor_type="user",
                        created_at=created_at,
                    )
                )
                AuditEventWriter(session).append(
                    AuditEventRecord(
                        event_id=f"audit-{uuid4().hex}",
                        event_type="local_pilot_admin_seeded",
                        actor_id=None,
                        target_ref=f"user:{actor_id}",
                        project_id=None,
                        message_code="identity.local_pilot_admin_was_seeded",
                        metadata={
                            "email": normalized_email,
                            "system_role": "admin",
                        },
                        created_at=created_at,
                    )
                )
                session.commit()
                return LocalPilotAdminSeedReceipt(actor_id=actor_id, created=True)
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class IssueBrowserSessionCommand:
    """Issue one current human session without changing public login semantics."""

    session_factory: SessionFactory
    token_factory: Callable[[], str] = lambda: token_urlsafe(24)

    def execute(self, actor_id: str) -> str:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    identity_keys=(identity_actor_owner_key(actor_id),),
                )
                actor = session.scalar(
                    select(AtlasUserRow)
                    .where(AtlasUserRow.actor_id == actor_id)
                    .with_for_update()
                )
                if actor is None or not actor.active or actor.actor_type != "user":
                    raise IdentityCurrentnessConflict(
                        "browser session actor is inactive or missing"
                    )
                token = self.token_factory()
                if not token or session.get(AtlasSessionRow, token) is not None:
                    raise IdentityCurrentnessConflict(
                        "browser session token was not unique"
                    )
                session.add(AtlasSessionRow(session_token=token, actor_id=actor_id))
                session.commit()
                return token
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class RevokeBrowserSessionCommand:
    """Revoke exactly the presented browser session, if it still exists."""

    session_factory: SessionFactory

    def execute(self, token: str | None) -> bool:
        if not token:
            return False
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    identity_keys=(f"identity:session:{token}",),
                )
                row = session.scalar(
                    select(AtlasSessionRow)
                    .where(AtlasSessionRow.session_token == token)
                    .with_for_update()
                )
                if row is None:
                    session.rollback()
                    return False
                session.delete(row)
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class ExpireInviteCommand:
    """Persist the current public expired-invite marker without inventing audit."""

    session_factory: SessionFactory

    def execute(self, invite: UserInviteRecord) -> None:
        if invite.status != "expired" or invite.accepted_at is not None:
            raise ValueError("expired invite command requires an expired invite")
        expected = replace(invite, status="pending", revoked_at=None)
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    identity_keys=(f"identity:invite:{invite.invite_id}",),
                )
                current = session.scalar(
                    select(AtlasUserInviteRow)
                    .where(AtlasUserInviteRow.invite_id == invite.invite_id)
                    .with_for_update()
                )
                if current is None or _invite_record(current) != expected:
                    raise IdentityCurrentnessConflict(
                        "expired invite currentness changed"
                    )
                session.merge(_invite_row(invite))
                session.commit()
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class IdentityRepository:
    session_factory: SessionFactory

    def identity_session(self, change_set: IdentitySessionChangeSet) -> None:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        *(("identity:system-admin-control",) if change_set.protect_admin_count else ()),
                        *(("team:membership-control",) if change_set.users else ()),
                        *(("team:hierarchy-control",) if change_set.authorization_scope_type in {"team", "project"} else ()),
                    ),
                    identity_keys=(
                        *change_set.identity_lock_keys,
                        *(
                            identity_actor_owner_key(user.actor_id)
                            for user in change_set.users
                        ),
                        *(
                            identity_actor_owner_key(actor_id)
                            for actor_id, _expected in change_set.expected_agent_users
                        ),
                        *(
                            (identity_actor_owner_key(change_set.authorization_actor_id),)
                            if change_set.authorization_actor_id
                            else ()
                        ),
                        *(
                            (team_owner_key(change_set.authorization_scope_id),)
                            if change_set.authorization_scope_type == "team"
                            and change_set.authorization_scope_id
                            else ()
                        ),
                        *(
                            (project_owner_key(change_set.authorization_scope_id),)
                            if change_set.authorization_scope_type == "project"
                            and change_set.authorization_scope_id
                            else ()
                        ),
                        *(
                            (
                                team_subject_owner_key(
                                    "user",
                                    change_set.authorization_actor_id,
                                ),
                                project_acl_subject_owner_key(
                                    "user",
                                    change_set.authorization_actor_id,
                                ),
                            )
                            if change_set.authorization_actor_id
                            and change_set.authorization_scope_type == "project"
                            else ()
                        ),
                        *(
                            f"identity:session:{token}"
                            for token, _actor_id in change_set.sessions
                        ),
                        *(
                            f"identity:session:{token}"
                            for token in change_set.deleted_session_tokens
                        ),
                        *(
                            f"identity:invite:{transition.record.invite_id}"
                            for transition in change_set.invite_transitions
                        ),
                        *(
                            f"identity:agent-token:{token.token_id}"
                            for token in change_set.agent_tokens
                        ),
                        *(
                            f"team:admin-control:{team_id}"
                            for team_id in change_set.protected_admin_team_ids
                        ),
                    ),
                )
                self._lock_invite_transitions(
                    session,
                    change_set.invite_transitions,
                )
                self._validate_authorization(session, change_set)
                self._lock_user_currentness(session, change_set.expected_users)
                self._lock_agent_user_currentness(
                    session,
                    change_set.expected_agent_users,
                )
                self._lock_directory_currentness(session, change_set)
                self._lock_pending_invite_absence(
                    session,
                    change_set.expected_pending_invite_absent_emails,
                )
                if change_set.reject_directory_alias_conflicts:
                    self._validate_directory_alias_conflicts(
                        session,
                        change_set.external_identities,
                    )
                if change_set.protect_admin_count:
                    self._validate_active_admin_invariant(session, change_set.users)
                (
                    protected_team_ids,
                    protected_team_memberships,
                ) = self._lock_complete_protected_team_memberships(
                    session,
                    changed_users=change_set.users,
                    requested_team_ids=change_set.protected_admin_team_ids,
                )
                self._validate_active_team_admin_invariant(
                    session,
                    changed_users=change_set.users,
                    team_ids=protected_team_ids,
                    membership_rows=protected_team_memberships,
                )
                self._write_identity_rows(session, change_set)
                AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def identity_scope_acceptance(
        self,
        change_set: IdentityScopeAcceptanceChangeSet,
    ) -> None:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        *(("team:membership-control",) if change_set.team_membership else ()),
                        *(
                            (f"project:acl-control:{change_set.project_grant.project_id}",)
                            if change_set.project_grant
                            else ()
                        ),
                    ),
                    identity_keys=(
                        identity_actor_owner_key(change_set.user.actor_id),
                        f"identity:invite:{change_set.invite.invite_id}",
                        *(
                            (
                                "team:membership:"
                                f"{change_set.team_membership.membership_id}",
                                team_owner_key(change_set.team_membership.team_id),
                                team_subject_owner_key(
                                    change_set.team_membership.member_actor_type,
                                    change_set.team_membership.member_actor_id,
                                ),
                            )
                            if change_set.team_membership is not None
                            else ()
                        ),
                        *(
                            (
                                f"project:grant:{change_set.project_grant.grant_id}",
                                project_owner_key(change_set.project_grant.project_id),
                                project_acl_subject_owner_key(
                                    change_set.project_grant.subject_type,
                                    change_set.project_grant.subject_id,
                                ),
                            )
                            if change_set.project_grant is not None
                            else ()
                        ),
                    ),
                )
                self._lock_scope_acceptance_currentness(session, change_set)
                session.merge(_user_row(change_set.user))
                session.merge(_invite_row(change_set.invite))
                if change_set.team_membership is not None:
                    TeamMembershipWriter(session).merge(change_set.team_membership)
                if change_set.project_grant is not None:
                    ProjectGrantWriter(session).merge(change_set.project_grant)
                AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _lock_invite_transitions(
        session: Session,
        transitions: tuple[InviteTransition, ...],
    ) -> None:
        for transition in transitions:
            current = session.scalar(
                select(AtlasUserInviteRow)
                .where(AtlasUserInviteRow.invite_id == transition.record.invite_id)
                .with_for_update()
            )
            if transition.expected_status is None:
                if current is not None:
                    raise IdentityCurrentnessConflict(
                        "identity invite already exists"
                    )
                continue
            if (
                current is None
                or current.status != transition.expected_status
                or current.actor_id != transition.record.actor_id
                or current.token_digest != transition.record.token_digest
            ):
                raise IdentityCurrentnessConflict(
                    "identity invite currentness changed"
                )

    @staticmethod
    def _validate_authorization(
        session: Session,
        change_set: IdentitySessionChangeSet,
    ) -> None:
        actor_id = change_set.authorization_actor_id
        if actor_id is None:
            return
        actor = session.get(AtlasUserRow, actor_id)
        if actor is None or not actor.active or actor.actor_type != "user":
            raise IdentityAuthorizationConflict("identity actor is no longer active")
        if actor.system_role == "admin":
            return
        if change_set.authorization_requires_system_admin:
            raise IdentityAuthorizationConflict("System Admin authority changed")
        scope_type = change_set.authorization_scope_type
        scope_id = change_set.authorization_scope_id
        if scope_type == "team" and scope_id:
            team = session.get(AtlasTeamRow, scope_id)
            membership = session.scalar(
                select(AtlasTeamMembershipRow).where(
                    AtlasTeamMembershipRow.team_id == scope_id,
                    AtlasTeamMembershipRow.member_actor_type == "user",
                    AtlasTeamMembershipRow.member_actor_id == actor_id,
                    AtlasTeamMembershipRow.status == "active",
                    AtlasTeamMembershipRow.role == "admin",
                ).limit(1)
            )
            if team is not None and team.status == "active" and membership is not None:
                return
        elif scope_type == "project" and scope_id:
            from atlas_production.infrastructure.postgres_owner.project import (
                ActionAwareAclAuthority,
            )

            if ActionAwareAclAuthority.resolve_in_session(
                session,
                actor_type="user",
                actor_id=actor_id,
                project_id=scope_id,
                action="permission_manage",
            ).allowed:
                return
        raise IdentityAuthorizationConflict("identity scope authority changed")

    @staticmethod
    def _lock_user_currentness(
        session: Session,
        expected_users: tuple[tuple[str, UserRecord | None], ...],
    ) -> None:
        for actor_id, expected in expected_users:
            current = session.scalar(
                select(AtlasUserRow)
                .where(AtlasUserRow.actor_id == actor_id)
                .with_for_update()
            )
            current_record = _user_record(current) if current is not None else None
            if current_record != expected:
                raise IdentityCurrentnessConflict(
                    "identity user currentness changed"
                )

    @staticmethod
    def _lock_agent_user_currentness(
        session: Session,
        expected_agents: tuple[tuple[str, UserRecord], ...],
    ) -> None:
        for actor_id, expected in expected_agents:
            current = session.scalar(
                select(AtlasUserRow)
                .where(AtlasUserRow.actor_id == actor_id)
                .with_for_update()
            )
            if (
                current is None
                or _user_record(current) != expected
                or current.actor_type != "service_account"
                or not current.active
            ):
                raise IdentityCurrentnessConflict(
                    "agent token target currentness changed"
                )

    @staticmethod
    def _lock_directory_currentness(
        session: Session,
        change_set: IdentitySessionChangeSet,
    ) -> None:
        for connection_id, expected in change_set.expected_directory_connections:
            row = session.scalar(
                select(AtlasDirectoryConnectionRow)
                .where(AtlasDirectoryConnectionRow.connection_id == connection_id)
                .with_for_update()
            )
            current = directory_connection_record(row) if row is not None else None
            if current != expected:
                raise IdentityCurrentnessConflict("directory connection currentness changed")
        for connection_id, secret_kind, expected in change_set.expected_directory_secrets:
            row = session.scalar(
                select(AtlasDirectoryConnectionSecretRow)
                .where(
                    AtlasDirectoryConnectionSecretRow.connection_id == connection_id,
                    AtlasDirectoryConnectionSecretRow.secret_kind == secret_kind,
                )
                .with_for_update()
            )
            current = directory_secret_record(row) if row is not None else None
            if current != expected:
                raise IdentityCurrentnessConflict("directory secret currentness changed")
        for actor_id, expected in change_set.expected_external_identities:
            row = session.scalar(
                select(AtlasExternalIdentityRow)
                .where(AtlasExternalIdentityRow.actor_id == actor_id)
                .with_for_update()
            )
            current = external_identity_record(row) if row is not None else None
            if current != expected:
                raise IdentityCurrentnessConflict("external identity currentness changed")

    @staticmethod
    def _validate_directory_alias_conflicts(
        session: Session,
        identities: tuple[ExternalIdentityRecord, ...],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        changed_actor_ids = {identity.actor_id for identity in identities}
        for identity in identities:
            aliases = [identity.normalized_username]
            if identity.normalized_email is not None:
                aliases.append(identity.normalized_email)
            for alias in aliases:
                key = (identity.connection_id, alias)
                if key in seen:
                    raise IdentityCurrentnessConflict("directory alias is ambiguous")
                seen.add(key)
                conflict = session.scalar(
                    select(AtlasExternalIdentityRow.actor_id)
                    .where(
                        AtlasExternalIdentityRow.connection_id == identity.connection_id,
                        or_(
                            AtlasExternalIdentityRow.normalized_username == alias,
                            AtlasExternalIdentityRow.normalized_email == alias,
                        ),
                        AtlasExternalIdentityRow.actor_id.not_in(changed_actor_ids),
                    )
                    .with_for_update()
                    .limit(1)
                )
                if conflict is not None:
                    raise IdentityCurrentnessConflict("directory alias is ambiguous")
            if identity.normalized_email is None:
                continue
            local_rows = session.scalars(
                select(AtlasUserRow)
                .where(
                    func.lower(AtlasUserRow.email) == identity.normalized_email,
                    AtlasUserRow.actor_id.not_in(changed_actor_ids),
                )
                .with_for_update()
            ).all()
            for local_row in local_rows:
                binding = session.scalar(
                    select(AtlasExternalIdentityRow.actor_id).where(
                        AtlasExternalIdentityRow.actor_id == local_row.actor_id
                    )
                )
                if binding is None:
                    raise IdentityCurrentnessConflict(
                        "directory alias conflicts with a local email"
                    )

    @staticmethod
    def _lock_complete_protected_team_memberships(
        session: Session,
        *,
        changed_users: tuple[UserRecord, ...],
        requested_team_ids: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[AtlasTeamMembershipRow, ...]]:
        changed_actor_ids = {
            user.actor_id for user in changed_users if user.actor_type == "user"
        }
        requested = set(requested_team_ids)
        conditions = []
        if requested:
            conditions.append(AtlasTeamMembershipRow.team_id.in_(requested))
        if changed_actor_ids:
            conditions.append(
                and_(
                        AtlasTeamMembershipRow.member_actor_type == "user",
                        AtlasTeamMembershipRow.member_actor_id.in_(changed_actor_ids),
                        AtlasTeamMembershipRow.status == "active",
                        AtlasTeamMembershipRow.role == "admin",
                )
            )
        if not conditions:
            return (), ()
        rows = session.scalars(
            select(AtlasTeamMembershipRow)
            .where(or_(*conditions))
            .order_by(
                AtlasTeamMembershipRow.team_id,
                AtlasTeamMembershipRow.membership_id,
            )
            .with_for_update()
        ).all()
        discovered = requested | {
            row.team_id
            for row in rows
            if row.member_actor_type == "user"
            and row.member_actor_id in changed_actor_ids
            and row.status == "active"
            and row.role == "admin"
        }
        newly_discovered = discovered - requested
        if newly_discovered:
            complete_rows = session.scalars(
                select(AtlasTeamMembershipRow)
                .where(AtlasTeamMembershipRow.team_id.in_(newly_discovered))
                .order_by(
                    AtlasTeamMembershipRow.team_id,
                    AtlasTeamMembershipRow.membership_id,
                )
                .with_for_update()
            ).all()
            by_id = {row.membership_id: row for row in rows}
            by_id.update({row.membership_id: row for row in complete_rows})
            rows = list(by_id.values())
        return tuple(sorted(discovered)), tuple(rows)

    @staticmethod
    def _lock_pending_invite_absence(
        session: Session,
        emails: tuple[str, ...],
    ) -> None:
        for email in dict.fromkeys(item.strip().lower() for item in emails):
            current = session.scalar(
                select(AtlasUserInviteRow.invite_id)
                .where(
                    AtlasUserInviteRow.email == email,
                    AtlasUserInviteRow.status == "pending",
                )
                .order_by(AtlasUserInviteRow.invite_id)
                .with_for_update()
                .limit(1)
            )
            if current is not None:
                raise IdentityCurrentnessConflict(
                    "pending invite for email already exists"
                )

    @staticmethod
    def _lock_scope_acceptance_currentness(
        session: Session,
        change_set: IdentityScopeAcceptanceChangeSet,
    ) -> None:
        current_invite = session.scalar(
            select(AtlasUserInviteRow)
            .where(AtlasUserInviteRow.invite_id == change_set.invite.invite_id)
            .with_for_update()
        )
        current_user = session.scalar(
            select(AtlasUserRow)
            .where(AtlasUserRow.actor_id == change_set.user.actor_id)
            .with_for_update()
        )
        expected_pending_invite = replace(
            change_set.invite,
            status="pending",
            accepted_at=None,
            revoked_at=None,
        )
        if (
            current_invite is None
            or _invite_record(current_invite) != expected_pending_invite
            or current_user is None
            or _user_record(current_user) != change_set.expected_user
        ):
            raise IdentityCurrentnessConflict(
                "identity scope acceptance currentness changed"
            )
        if change_set.team_membership is not None:
            membership = session.scalar(
                select(AtlasTeamMembershipRow)
                .where(
                    AtlasTeamMembershipRow.membership_id
                    == change_set.team_membership.membership_id
                )
                .with_for_update()
            )
            current_membership = (
                _membership_record(membership) if membership is not None else None
            )
            if current_membership != change_set.expected_team_membership:
                raise IdentityCurrentnessConflict(
                    "invite Team membership currentness changed"
                )
        if change_set.project_grant is not None:
            grant = session.scalar(
                select(AtlasPermissionGrantRow)
                .where(
                    AtlasPermissionGrantRow.grant_id
                    == change_set.project_grant.grant_id
                )
                .with_for_update()
            )
            current_grant = _grant_record(grant) if grant is not None else None
            if current_grant != change_set.expected_project_grant:
                raise IdentityCurrentnessConflict(
                    "invite Project grant currentness changed"
                )

    @staticmethod
    def _validate_active_admin_invariant(
        session: Session,
        changed_users: tuple[UserRecord, ...],
    ) -> None:
        current_admin_rows = session.scalars(
            select(AtlasUserRow)
            .where(
                AtlasUserRow.actor_type == "user",
                AtlasUserRow.active.is_(True),
                AtlasUserRow.system_role == "admin",
            )
            .order_by(AtlasUserRow.actor_id)
            .with_for_update()
        ).all()
        active_admin_ids = {row.actor_id for row in current_admin_rows}
        for user in changed_users:
            if user.actor_type == "user" and user.active and user.system_role == "admin":
                active_admin_ids.add(user.actor_id)
            else:
                active_admin_ids.discard(user.actor_id)
        if not active_admin_ids:
            raise IdentityInvariantViolation("at least one active System Admin is required")

    @staticmethod
    def _validate_active_team_admin_invariant(
        session: Session,
        *,
        changed_users: tuple[UserRecord, ...],
        team_ids: tuple[str, ...],
        membership_rows: tuple[AtlasTeamMembershipRow, ...],
    ) -> None:
        if not team_ids:
            return
        referenced_user_ids = {
            row.member_actor_id
            for row in membership_rows
            if row.member_actor_type == "user"
        } | {user.actor_id for user in changed_users}
        current_user_rows = session.scalars(
            select(AtlasUserRow)
            .where(AtlasUserRow.actor_id.in_(referenced_user_ids or {""}))
            .order_by(AtlasUserRow.actor_id)
            .with_for_update()
        ).all()
        projected_users = {
            row.actor_id: _user_record(row) for row in current_user_rows
        }
        projected_users.update({user.actor_id: user for user in changed_users})
        for team_id in sorted(set(team_ids)):
            if not any(
                row.team_id == team_id
                and row.member_actor_type == "user"
                and row.status == "active"
                and row.role == "admin"
                and (user := projected_users.get(row.member_actor_id)) is not None
                and user.active
                for row in membership_rows
            ):
                raise IdentityInvariantViolation(
                    "at least one active direct human Team Admin is required"
                )

    @staticmethod
    def _write_identity_rows(
        session: Session,
        change_set: IdentitySessionChangeSet,
    ) -> None:
        for token in dict.fromkeys(change_set.deleted_session_tokens):
            session.execute(
                delete(AtlasSessionRow).where(AtlasSessionRow.session_token == token)
            )
        for connection_id, secret_kind in change_set.deleted_directory_secrets:
            session.execute(
                delete(AtlasDirectoryConnectionSecretRow).where(
                    AtlasDirectoryConnectionSecretRow.connection_id == connection_id,
                    AtlasDirectoryConnectionSecretRow.secret_kind == secret_kind,
                )
            )
        for connection in change_set.directory_connections:
            session.merge(directory_connection_row(connection))
        for user in change_set.users:
            session.merge(_user_row(user))
        if change_set.directory_connections or change_set.users:
            session.flush()
        for secret in change_set.directory_secrets:
            session.merge(directory_secret_row(secret))
        for identity in change_set.external_identities:
            session.merge(external_identity_row(identity))
        for token, actor_id in change_set.sessions:
            session.merge(AtlasSessionRow(session_token=token, actor_id=actor_id))
        for transition in change_set.invite_transitions:
            session.merge(_invite_row(transition.record))
        for agent_token in change_set.agent_tokens:
            session.merge(_agent_token_row(agent_token))

    def actor_for_token(self, token: str | None) -> UserRecord | None:
        with self.session_factory() as session:
            return identity_access.read_session_actor(session, token)

    def get_user(self, actor_id: str) -> UserRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasUserRow).where(AtlasUserRow.actor_id == actor_id)
            )
            return _user_record(row) if row is not None else None

    def user_by_email(self, email: str) -> UserRecord | None:
        normalized = email.strip().lower()
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasUserRow)
                .where(AtlasUserRow.email == normalized)
                .order_by(AtlasUserRow.actor_id)
                .limit(2)
            ).all()
            return _user_record(rows[0]) if len(rows) == 1 else None

    def list_users(
        self,
        *,
        limit: int = 500,
        after_actor_id: str | None = None,
    ) -> list[UserRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("User list limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasUserRow)
            if after_actor_id is not None:
                statement = statement.where(AtlasUserRow.actor_id > after_actor_id)
            rows = session.scalars(
                statement.order_by(AtlasUserRow.actor_id).limit(limit)
            ).all()
            return [_user_record(row) for row in rows]
    def list_directory_connections(self) -> list[DirectoryConnectionRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasDirectoryConnectionRow).order_by(
                    AtlasDirectoryConnectionRow.priority,
                    AtlasDirectoryConnectionRow.connection_id,
                )
            ).all()
            return [directory_connection_record(row) for row in rows]

    def get_directory_connection(
        self, connection_id: str
    ) -> DirectoryConnectionRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasDirectoryConnectionRow, connection_id)
            return directory_connection_record(row) if row is not None else None

    def get_directory_secret(
        self, connection_id: str, secret_kind: str
    ) -> DirectorySecretRecord | None:
        with self.session_factory() as session:
            row = session.get(
                AtlasDirectoryConnectionSecretRow,
                (connection_id, secret_kind),
            )
            return directory_secret_record(row) if row is not None else None

    def get_external_identity(
        self, actor_id: str
    ) -> ExternalIdentityRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasExternalIdentityRow, actor_id)
            return external_identity_record(row) if row is not None else None

    def get_external_identity_by_subject(
        self, connection_id: str, external_subject: str
    ) -> ExternalIdentityRecord | None:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasExternalIdentityRow)
                .where(
                    AtlasExternalIdentityRow.connection_id == connection_id,
                    AtlasExternalIdentityRow.external_subject == external_subject,
                )
                .order_by(AtlasExternalIdentityRow.actor_id)
                .limit(2)
            ).all()
            return external_identity_record(rows[0]) if len(rows) == 1 else None

    def list_external_identities(self) -> list[ExternalIdentityRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasExternalIdentityRow).order_by(
                    AtlasExternalIdentityRow.connection_id,
                    AtlasExternalIdentityRow.actor_id,
                )
            ).all()
            return [external_identity_record(row) for row in rows]

    def active_admin_count(self) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(AtlasUserRow).where(
                        AtlasUserRow.actor_type == "user",
                        AtlasUserRow.active.is_(True),
                        AtlasUserRow.system_role == "admin",
                    )
                ) or 0
            )

    def get_invite(self, invite_id: str) -> UserInviteRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasUserInviteRow).where(
                    AtlasUserInviteRow.invite_id == invite_id
                )
            )
            return _invite_record(row) if row is not None else None

    def pending_invite_for_email(self, email: str) -> UserInviteRecord | None:
        normalized = email.strip().lower()
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasUserInviteRow)
                .where(
                    AtlasUserInviteRow.email == normalized,
                    AtlasUserInviteRow.status == "pending",
                )
                .order_by(
                    AtlasUserInviteRow.created_at,
                    AtlasUserInviteRow.invite_id,
                )
                .limit(1)
            )
            return _invite_record(row) if row is not None else None

    def list_invites(
        self,
        *,
        limit: int = 500,
        after_invite_id: str | None = None,
    ) -> list[UserInviteRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Invite list limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasUserInviteRow)
            if after_invite_id is not None:
                statement = statement.where(
                    AtlasUserInviteRow.invite_id > after_invite_id
                )
            rows = session.scalars(
                statement.order_by(AtlasUserInviteRow.invite_id).limit(limit)
            ).all()
            return [_invite_record(row) for row in rows]

    def invite_by_digest(self, digest: str) -> UserInviteRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasUserInviteRow).where(
                    AtlasUserInviteRow.token_digest == digest
                )
            )
            return _invite_record(row) if row is not None else None

    def list_agent_tokens(
        self,
        actor_id: str,
        *,
        limit: int = 500,
        after_token_id: str | None = None,
    ) -> list[AgentTokenRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Agent token limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasAgentTokenRow).where(
                AtlasAgentTokenRow.actor_id == actor_id
            )
            if after_token_id is not None:
                statement = statement.where(
                    AtlasAgentTokenRow.token_id > after_token_id
                )
            rows = session.scalars(
                statement.order_by(AtlasAgentTokenRow.token_id).limit(limit)
            ).all()
            return [_agent_token_record(row) for row in rows]

    def get_agent_token(self, token_id: str) -> AgentTokenRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasAgentTokenRow, token_id)
            return _agent_token_record(row) if row is not None else None

    def agent_token_for_raw(self, raw_token: str | None) -> AgentTokenRecord | None:
        if not raw_token:
            return None
        digest = agent_token_digest(raw_token)
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasAgentTokenRow)
                .where(AtlasAgentTokenRow.token_digest == digest)
                .order_by(AtlasAgentTokenRow.token_id)
                .limit(2)
            ).all()
            return _agent_token_record(rows[0]) if len(rows) == 1 else None

    def active_direct_admin_team_ids(
        self,
        actor_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not actor_ids:
            return ()
        with self.session_factory() as session:
            return tuple(
                session.scalars(
                    select(AtlasTeamMembershipRow.team_id)
                    .where(
                        AtlasTeamMembershipRow.member_actor_type == "user",
                        AtlasTeamMembershipRow.member_actor_id.in_(set(actor_ids)),
                        AtlasTeamMembershipRow.status == "active",
                        AtlasTeamMembershipRow.role == "admin",
                    )
                    .distinct()
                    .order_by(AtlasTeamMembershipRow.team_id)
                ).all()
            )


__all__ = [
    "ExpireInviteCommand",
    "IdentityCurrentnessConflict",
    "IdentityAuthorizationConflict",
    "IdentityInvariantViolation",
    "IdentityRepository",
    "IdentityScopeAcceptanceChangeSet",
    "IdentitySessionChangeSet",
    "IssueBrowserSessionCommand",
    "InviteTransition",
    "LocalPilotAdminSeedReceipt",
    "RevokeBrowserSessionCommand",
    "SeedLocalPilotAdminCommand",
]
