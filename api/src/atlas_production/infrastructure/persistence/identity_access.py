import json
from dataclasses import asdict
from secrets import token_urlsafe
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    or_,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectorySecretRecord,
    ExternalIdentityRecord,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    AgentTokenRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.security import (
    agent_token_digest,
    invite_token_digest,
)

from .base import OrmBase


class AtlasUserRow(OrmBase):
    __tablename__ = "atlas_users"

    actor_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    system_role: Mapped[str] = mapped_column(String, nullable=False)
    password_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasDirectoryConnectionRow(OrmBase):
    __tablename__ = "atlas_directory_connections"
    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('active_directory', 'ldap')",
            name="ck_atlas_directory_connection_provider_type",
        ),
        CheckConstraint(
            "tls_mode IN ('ldaps', 'start_tls')",
            name="ck_atlas_directory_connection_tls_mode",
        ),
        CheckConstraint("priority >= 0", name="ck_atlas_directory_connection_priority"),
        CheckConstraint(
            "port >= 1 AND port <= 65535",
            name="ck_atlas_directory_connection_port",
        ),
        CheckConstraint(
            "connect_timeout_seconds >= 1 AND connect_timeout_seconds <= 30",
            name="ck_atlas_directory_connection_connect_timeout",
        ),
        CheckConstraint(
            "operation_timeout_seconds >= 1 AND operation_timeout_seconds <= 30",
            name="ck_atlas_directory_connection_operation_timeout",
        ),
    )

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    tls_mode: Mapped[str] = mapped_column(String, nullable=False)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    bind_dn: Mapped[str] = mapped_column(Text, nullable=False)
    user_base_dn: Mapped[str] = mapped_column(Text, nullable=False)
    user_object_filter: Mapped[str] = mapped_column(Text, nullable=False)
    login_attribute: Mapped[str] = mapped_column(String, nullable=False)
    stable_id_attribute: Mapped[str] = mapped_column(String, nullable=False)
    display_name_attribute: Mapped[str] = mapped_column(String, nullable=False)
    email_attribute: Mapped[str] = mapped_column(String, nullable=False)
    groups_attribute: Mapped[str] = mapped_column(String, nullable=False)
    department_attribute: Mapped[str] = mapped_column(String, nullable=False)
    title_attribute: Mapped[str] = mapped_column(String, nullable=False)
    employee_id_attribute: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasDirectoryConnectionSecretRow(OrmBase):
    __tablename__ = "atlas_directory_connection_secrets"
    __table_args__ = (
        CheckConstraint(
            "secret_kind IN ('bind_password', 'custom_ca')",
            name="ck_atlas_directory_connection_secret_kind",
        ),
        CheckConstraint(
            "algorithm = 'AES-256-GCM'",
            name="ck_atlas_directory_connection_secret_algorithm",
        ),
        CheckConstraint(
            "storage_backend = 'encrypted_database'",
            name="ck_atlas_directory_connection_secret_storage",
        ),
        CheckConstraint("version >= 1", name="ck_atlas_directory_connection_secret_version"),
    )

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_directory_connections.connection_id", ondelete="CASCADE"),
        primary_key=True,
    )
    secret_kind: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String, nullable=False)
    storage_backend: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AtlasExternalIdentityRow(OrmBase):
    __tablename__ = "atlas_external_identities"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "external_subject",
            name="uq_atlas_external_identity_subject",
        ),
        CheckConstraint(
            "status IN ('current', 'stale', 'missing', 'disabled')",
            name="ck_atlas_external_identity_status",
        ),
        Index(
            "ix_atlas_external_identity_connection_username",
            "connection_id",
            "normalized_username",
        ),
        Index(
            "ix_atlas_external_identity_connection_email",
            "connection_id",
            "normalized_email",
        ),
    )

    actor_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_users.actor_id", ondelete="CASCADE"),
        primary_key=True,
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("atlas_directory_connections.connection_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_subject: Mapped[str] = mapped_column(String, nullable=False)
    normalized_username: Mapped[str] = mapped_column(String, nullable=False)
    normalized_email: Mapped[str | None] = mapped_column(String, nullable=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    groups_json: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    directory_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_refreshed_at: Mapped[str] = mapped_column(String, nullable=False)


def directory_connection_row(record: DirectoryConnectionRecord) -> AtlasDirectoryConnectionRow:
    return AtlasDirectoryConnectionRow(**asdict(record))


def directory_connection_record(row: AtlasDirectoryConnectionRow) -> DirectoryConnectionRecord:
    return DirectoryConnectionRecord(
        **{
            field_name: getattr(row, field_name)
            for field_name in DirectoryConnectionRecord.__dataclass_fields__
        }
    )


def directory_secret_row(record: DirectorySecretRecord) -> AtlasDirectoryConnectionSecretRow:
    return AtlasDirectoryConnectionSecretRow(**asdict(record))


def directory_secret_record(row: AtlasDirectoryConnectionSecretRow) -> DirectorySecretRecord:
    return DirectorySecretRecord(
        connection_id=row.connection_id,
        secret_kind=row.secret_kind,
        ciphertext=row.ciphertext,
        nonce=row.nonce,
        key_id=row.key_id,
        version=row.version,
        algorithm=row.algorithm,
        storage_backend=row.storage_backend,
        updated_at=row.updated_at,
    )


def external_identity_row(record: ExternalIdentityRecord) -> AtlasExternalIdentityRow:
    values = asdict(record)
    values["groups_json"] = json.dumps(
        list(record.groups), ensure_ascii=False, separators=(",", ":")
    )
    del values["groups"]
    return AtlasExternalIdentityRow(**values)


def external_identity_record(row: AtlasExternalIdentityRow) -> ExternalIdentityRecord:
    groups = json.loads(row.groups_json)
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise ValueError("directory groups payload must be a string array")
    return ExternalIdentityRecord(
        actor_id=row.actor_id,
        connection_id=row.connection_id,
        external_subject=row.external_subject,
        normalized_username=row.normalized_username,
        normalized_email=row.normalized_email,
        username=row.username,
        display_name=row.display_name,
        email=row.email,
        groups=tuple(groups),
        department=row.department,
        title=row.title,
        employee_id=row.employee_id,
        directory_enabled=row.directory_enabled,
        status=row.status,
        last_refreshed_at=row.last_refreshed_at,
    )


class AtlasSessionRow(OrmBase):
    __tablename__ = "atlas_sessions"

    session_token: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)


class AtlasAgentTokenRow(OrmBase):
    __tablename__ = "atlas_agent_tokens"

    token_id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(Text, nullable=False)
    token_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AtlasUserInviteRow(OrmBase):
    __tablename__ = "atlas_user_invites"

    invite_id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    system_role: Mapped[str] = mapped_column(String, nullable=False)
    token_digest: Mapped[str] = mapped_column(Text, nullable=False)
    token_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    accepted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_role: Mapped[str | None] = mapped_column(String, nullable=True)


class AtlasTeamRow(OrmBase):
    __tablename__ = "atlas_teams"

    team_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_team_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    inherit_parent_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AtlasTeamMembershipRow(OrmBase):
    __tablename__ = "atlas_team_memberships"

    membership_id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    member_actor_type: Mapped[str] = mapped_column(String, nullable=False)
    member_actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    removed_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AtlasPermissionGrantRow(OrmBase):
    __tablename__ = "atlas_permission_grants"

    grant_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    effect: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AtlasAccessDecisionRow(OrmBase):
    __tablename__ = "atlas_access_decisions"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    required_role: Mapped[str] = mapped_column(String, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    effective_role: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


def read_session_actor(session: Session, token: str | None) -> UserRecord | None:
    """Resolve one browser session against durable identity state.

    PostgreSQL request authentication must not trust the process-startup copy of
    sessions or users: another worker may have revoked the token, disabled the
    user, or changed the system role.  This join is the bounded request-time
    authority check for exactly one presented token.
    """

    if not token:
        return None
    row = (
        session.query(AtlasUserRow)
        .join(AtlasSessionRow, AtlasSessionRow.actor_id == AtlasUserRow.actor_id)
        .filter(AtlasSessionRow.session_token == token)
        .one_or_none()
    )
    if row is None or not row.active or row.actor_type != "user":
        return None
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


def read_core_payload(session: Session) -> dict[str, Any]:
    return {
        "users": [{"actor_id": row.actor_id, "display_name": row.display_name, "email": row.email,
            "system_role": row.system_role, "password_digest": row.password_digest,
            "active": row.active, "actor_type": row.actor_type, "created_at": row.created_at}
            for row in session.query(AtlasUserRow).all()],
        "sessions": {row.session_token: row.actor_id for row in session.query(AtlasSessionRow).all()},
        "agent_tokens": [{"token_id": row.token_id, "actor_id": row.actor_id,
            "token_digest": row.token_digest, "token_fingerprint": row.token_fingerprint,
            "status": row.status, "created_at": row.created_at, "revoked_at": row.revoked_at}
            for row in session.query(AtlasAgentTokenRow).all()],
        "user_invites": [{"invite_id": row.invite_id, "actor_id": row.actor_id,
            "email": row.email, "display_name": row.display_name, "system_role": row.system_role,
            "token_digest": row.token_digest, "token_fingerprint": row.token_fingerprint,
            "status": row.status, "created_at": row.created_at, "expires_at": row.expires_at,
            "accepted_at": row.accepted_at, "revoked_at": row.revoked_at,
            "scope_type": row.scope_type, "scope_id": row.scope_id, "scope_role": row.scope_role}
            for row in session.query(AtlasUserInviteRow).all()],
    }


def read_governance_payload(session: Session) -> dict[str, Any]:
    return {
        "teams": [{"team_id": row.team_id, "name": row.name, "parent_team_id": row.parent_team_id,
            "status": row.status, "created_at": row.created_at,
            "inherit_parent_documents": row.inherit_parent_documents}
            for row in session.query(AtlasTeamRow).all()],
        "team_memberships": [{"membership_id": row.membership_id, "team_id": row.team_id,
            "member_actor_type": row.member_actor_type, "member_actor_id": row.member_actor_id,
            "role": row.role, "status": row.status, "created_at": row.created_at,
            "removed_at": row.removed_at} for row in session.query(AtlasTeamMembershipRow).all()],
        "permission_grants": [{"grant_id": row.grant_id, "project_id": row.project_id,
            "subject_type": row.subject_type, "subject_id": row.subject_id, "role": row.role,
            "effect": row.effect, "status": row.status, "created_at": row.created_at,
            "revoked_at": row.revoked_at} for row in session.query(AtlasPermissionGrantRow).all()],
        "access_decisions": [{"decision_id": row.decision_id, "actor_type": row.actor_type,
            "actor_id": row.actor_id, "project_id": row.project_id, "action": row.action,
            "required_role": row.required_role, "allowed": row.allowed, "reason": row.reason,
            "effective_role": row.effective_role, "source_type": row.source_type,
            "source_id": row.source_id, "explanation": row.explanation, "created_at": row.created_at,
            "scope_type": row.scope_type, "scope_id": row.scope_id}
            for row in session.query(AtlasAccessDecisionRow).all()],
    }


def read_team_mutation_payload(
    session: Session,
    *,
    team_id: str,
    actor_ids: tuple[str, ...] = (),
    include_hierarchy: bool = False,
) -> dict[str, Any]:
    """Read only the fresh Team authority needed by one mutation."""

    team_query = session.query(AtlasTeamRow)
    if not include_hierarchy:
        team_query = team_query.filter(AtlasTeamRow.team_id == team_id)
    team_rows = team_query.all()
    membership_rows = (
        session.query(AtlasTeamMembershipRow)
        .filter(AtlasTeamMembershipRow.team_id == team_id)
        .all()
    )
    user_ids = set(actor_ids) | {
        row.member_actor_id
        for row in membership_rows
        if row.member_actor_type == "user"
    }
    user_rows = (
        session.query(AtlasUserRow)
        .filter(AtlasUserRow.actor_id.in_(user_ids or {""}))
        .all()
    )
    return {
        "users": [
            {
                "actor_id": row.actor_id,
                "display_name": row.display_name,
                "email": row.email,
                "system_role": row.system_role,
                "password_digest": row.password_digest,
                "active": row.active,
                "actor_type": row.actor_type,
                "created_at": row.created_at,
            }
            for row in user_rows
        ],
        "teams": [
            {
                "team_id": row.team_id,
                "name": row.name,
                "parent_team_id": row.parent_team_id,
                "status": row.status,
                "created_at": row.created_at,
                "inherit_parent_documents": row.inherit_parent_documents,
            }
            for row in team_rows
        ],
        "team_memberships": [
            {
                "membership_id": row.membership_id,
                "team_id": row.team_id,
                "member_actor_type": row.member_actor_type,
                "member_actor_id": row.member_actor_id,
                "role": row.role,
                "status": row.status,
                "created_at": row.created_at,
                "removed_at": row.removed_at,
            }
            for row in membership_rows
        ],
    }


def read_identity_mutation_payload(
    session: Session,
    *,
    actor_ids: tuple[str, ...] = (),
    user_email: str | None = None,
    invite_id: str | None = None,
    invite_digest: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    include_active_admins: bool = False,
) -> dict[str, Any]:
    """Read exact identity owners plus the active-admin guard set."""

    invite_filters = []
    if invite_id is not None:
        invite_filters.append(AtlasUserInviteRow.invite_id == invite_id)
    if invite_digest is not None:
        invite_filters.append(AtlasUserInviteRow.token_digest == invite_digest)
    if user_email is not None:
        invite_filters.append(AtlasUserInviteRow.email == user_email)
    invite_rows = (
        session.query(AtlasUserInviteRow).filter(or_(*invite_filters)).all()
        if invite_filters
        else []
    )
    selected_actor_ids = set(actor_ids) | {row.actor_id for row in invite_rows}
    user_filters = [AtlasUserRow.actor_id.in_(selected_actor_ids or {""})]
    if user_email is not None:
        user_filters.append(AtlasUserRow.email == user_email)
    if include_active_admins:
        user_filters.append(
            (AtlasUserRow.actor_type == "user")
            & AtlasUserRow.active.is_(True)
            & (AtlasUserRow.system_role == "admin")
        )
    user_rows = session.query(AtlasUserRow).filter(or_(*user_filters)).all()
    scope_team_ids = {
        row.scope_id
        for row in invite_rows
        if row.scope_type == "team" and row.scope_id
    }
    scope_project_ids = {
        row.scope_id
        for row in invite_rows
        if row.scope_type == "project" and row.scope_id
    }
    if scope_type == "team" and scope_id:
        scope_team_ids.add(scope_id)
    if scope_type == "project" and scope_id:
        scope_project_ids.add(scope_id)
    membership_rows = (
        session.query(AtlasTeamMembershipRow)
        .filter(
            or_(
                AtlasTeamMembershipRow.team_id.in_(scope_team_ids or {""}),
                (AtlasTeamMembershipRow.member_actor_type == "user")
                & AtlasTeamMembershipRow.member_actor_id.in_(
                    selected_actor_ids or {""}
                ),
            )
        )
        .all()
    )
    selected_team_ids = scope_team_ids | {row.team_id for row in membership_rows}
    team_rows = list(
        session.query(AtlasTeamRow)
        .filter(AtlasTeamRow.team_id.in_(selected_team_ids or {""}))
        .all()
    )
    loaded_team_ids = {row.team_id for row in team_rows}
    parent_ids = {
        row.parent_team_id
        for row in team_rows
        if row.parent_team_id and row.parent_team_id not in loaded_team_ids
    }
    while parent_ids:
        parent_rows = (
            session.query(AtlasTeamRow)
            .filter(AtlasTeamRow.team_id.in_(parent_ids))
            .all()
        )
        if not parent_rows:
            break
        team_rows.extend(parent_rows)
        loaded_team_ids.update(row.team_id for row in parent_rows)
        parent_ids = {
            row.parent_team_id
            for row in parent_rows
            if row.parent_team_id and row.parent_team_id not in loaded_team_ids
        }
    grant_rows = (
        session.query(AtlasPermissionGrantRow)
        .filter(AtlasPermissionGrantRow.project_id.in_(scope_project_ids or {""}))
        .all()
    )
    return {
        "users": [
            {
                "actor_id": row.actor_id, "display_name": row.display_name,
                "email": row.email, "system_role": row.system_role,
                "password_digest": row.password_digest, "active": row.active,
                "actor_type": row.actor_type, "created_at": row.created_at,
            }
            for row in user_rows
        ],
        "user_invites": [
            {
                "invite_id": row.invite_id, "actor_id": row.actor_id,
                "email": row.email, "display_name": row.display_name,
                "system_role": row.system_role, "token_digest": row.token_digest,
                "token_fingerprint": row.token_fingerprint, "status": row.status,
                "created_at": row.created_at, "expires_at": row.expires_at,
                "accepted_at": row.accepted_at, "revoked_at": row.revoked_at,
                "scope_type": row.scope_type, "scope_id": row.scope_id,
                "scope_role": row.scope_role,
            }
            for row in invite_rows
        ],
        "teams": [
            {
                "team_id": row.team_id, "name": row.name,
                "parent_team_id": row.parent_team_id, "status": row.status,
                "created_at": row.created_at,
                "inherit_parent_documents": row.inherit_parent_documents,
            }
            for row in team_rows
        ],
        "team_memberships": [
            {
                "membership_id": row.membership_id, "team_id": row.team_id,
                "member_actor_type": row.member_actor_type,
                "member_actor_id": row.member_actor_id, "role": row.role,
                "status": row.status, "created_at": row.created_at,
                "removed_at": row.removed_at,
            }
            for row in membership_rows
        ],
        "permission_grants": [
            {
                "grant_id": row.grant_id, "project_id": row.project_id,
                "subject_type": row.subject_type, "subject_id": row.subject_id,
                "role": row.role, "effect": row.effect, "status": row.status,
                "created_at": row.created_at, "revoked_at": row.revoked_at,
            }
            for row in grant_rows
        ],
        "scope_project_ids": tuple(sorted(scope_project_ids)),
    }


def invite_id_for_digest(session: Session, digest: str) -> str | None:
    row = (
        session.query(AtlasUserInviteRow.invite_id)
        .filter(AtlasUserInviteRow.token_digest == digest)
        .one_or_none()
    )
    return row[0] if row is not None else None


def invite_owner_details(
    session: Session,
    *,
    invite_id: str | None = None,
    digest: str | None = None,
) -> tuple[str, str | None, str | None] | None:
    query = session.query(
        AtlasUserInviteRow.invite_id,
        AtlasUserInviteRow.scope_type,
        AtlasUserInviteRow.scope_id,
    )
    if invite_id is not None:
        query = query.filter(AtlasUserInviteRow.invite_id == invite_id)
    elif digest is not None:
        query = query.filter(AtlasUserInviteRow.token_digest == digest)
    else:
        return None
    row = query.one_or_none()
    return (row.invite_id, row.scope_type, row.scope_id) if row else None
