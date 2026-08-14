"""Private deterministic row mappings for the Identity owner."""

from __future__ import annotations

from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAgentTokenRow,
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasUserInviteRow,
    AtlasUserRow,
)
from atlas_production.modules.identity_access.records import (
    AgentTokenRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    UserInviteRecord,
    UserRecord,
)


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
