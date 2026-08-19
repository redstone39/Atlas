"""Private deterministic advisory lock plans for the Identity owner."""

from __future__ import annotations

from dataclasses import dataclass

from atlas_production.infrastructure.postgres_lock_keys import (directory_alias_owner_key,
directory_subject_owner_key,
identity_actor_owner_key,
identity_email_owner_key,
project_acl_subject_owner_key,
project_owner_key,
team_owner_key,
team_subject_owner_key,)
from atlas_production.modules.identity_access.directory_ports import (
    ScopedDirectoryImportChangeSet,
)
from atlas_production.modules.identity_access.directory_records import (
    ExternalIdentityRecord,
)
from atlas_production.modules.identity_access.records import (
    AgentTokenRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    UserRecord,
)


@dataclass(frozen=True, slots=True)
class IdentityLockPlan:
    domain_keys: tuple[str, ...]
    identity_keys: tuple[str, ...]


def directory_identity_owner_keys(
    identity: ExternalIdentityRecord,
) -> tuple[str, ...]:
    keys = [
        directory_subject_owner_key(
            identity.connection_id,
            identity.external_subject,
        ),
        directory_alias_owner_key(
            identity.connection_id,
            identity.normalized_username,
        ),
        identity_email_owner_key(identity.normalized_username),
    ]
    if identity.normalized_email is not None:
        keys.extend(
            (
                directory_alias_owner_key(
                    identity.connection_id,
                    identity.normalized_email,
                ),
                identity_email_owner_key(identity.normalized_email),
            )
        )
    return tuple(keys)


def identity_session_lock_plan(
    *,
    protect_admin_count: bool,
    users: tuple[UserRecord, ...],
    authorization_scope_type: str | None,
    authorization_scope_id: str | None,
    authorization_actor_id: str | None,
    identity_lock_keys: tuple[str, ...],
    external_identities: tuple[ExternalIdentityRecord, ...],
    expected_agent_actor_ids: tuple[str, ...],
    session_tokens: tuple[str, ...],
    deleted_session_tokens: tuple[str, ...],
    invite_ids: tuple[str, ...],
    agent_tokens: tuple[AgentTokenRecord, ...],
    protected_admin_team_ids: tuple[str, ...],
) -> IdentityLockPlan:
    return IdentityLockPlan(
        domain_keys=(
            *(('identity:system-admin-control',) if protect_admin_count else ()),
            *(('team:membership-control',) if users else ()),
            *(
                ('team:hierarchy-control',)
                if authorization_scope_type in {'team', 'project'}
                else ()
            ),
        ),
        identity_keys=(
            *identity_lock_keys,
            *(
                key
                for identity in external_identities
                for key in directory_identity_owner_keys(identity)
            ),
            *(
                identity_email_owner_key(user.email)
                for user in users
                if user.email is not None
            ),
            *(identity_actor_owner_key(user.actor_id) for user in users),
            *(identity_actor_owner_key(actor_id) for actor_id in expected_agent_actor_ids),
            *(
                (identity_actor_owner_key(authorization_actor_id),)
                if authorization_actor_id
                else ()
            ),
            *(
                (team_owner_key(authorization_scope_id),)
                if authorization_scope_type == 'team' and authorization_scope_id
                else ()
            ),
            *(
                (project_owner_key(authorization_scope_id),)
                if authorization_scope_type == 'project' and authorization_scope_id
                else ()
            ),
            *(
                (
                    team_subject_owner_key('user', authorization_actor_id),
                    project_acl_subject_owner_key('user', authorization_actor_id),
                )
                if authorization_actor_id and authorization_scope_type == 'project'
                else ()
            ),
            *(f'identity:session:{token}' for token in session_tokens),
            *(f'identity:session:{token}' for token in deleted_session_tokens),
            *(f'identity:invite:{invite_id}' for invite_id in invite_ids),
            *(f'identity:agent-token:{token.token_id}' for token in agent_tokens),
            *(f'team:admin-control:{team_id}' for team_id in protected_admin_team_ids),
        ),
    )


def identity_scope_acceptance_lock_plan(
    *,
    user: UserRecord,
    invite_id: str,
    team_membership: TeamMembershipRecord | None,
    project_grant: PermissionGrantRecord | None,
) -> IdentityLockPlan:
    return IdentityLockPlan(
        domain_keys=(
            *(('team:membership-control',) if team_membership else ()),
            *(
                (f'project:acl-control:{project_grant.project_id}',)
                if project_grant
                else ()
            ),
        ),
        identity_keys=(
            identity_actor_owner_key(user.actor_id),
            f'identity:invite:{invite_id}',
            *(
                (
                    f'team:membership:{team_membership.membership_id}',
                    team_owner_key(team_membership.team_id),
                    team_subject_owner_key(
                        team_membership.member_actor_type,
                        team_membership.member_actor_id,
                    ),
                )
                if team_membership is not None
                else ()
            ),
            *(
                (
                    f'project:grant:{project_grant.grant_id}',
                    project_owner_key(project_grant.project_id),
                    project_acl_subject_owner_key(
                        project_grant.subject_type,
                        project_grant.subject_id,
                    ),
                )
                if project_grant is not None
                else ()
            ),
        ),
    )


def scoped_directory_import_lock_plan(
    change_set: ScopedDirectoryImportChangeSet,
) -> IdentityLockPlan:
    preparation = change_set.preparation
    return IdentityLockPlan(
        domain_keys=(
            *(('team:membership-control',) if change_set.team_memberships else ()),
            *(
                ('team:hierarchy-control', 'team:membership-control')
                if change_set.project_grants
                else ()
            ),
            *(
                (f'project:acl-control:{change_set.authorization_scope_id}',)
                if change_set.project_grants
                else ()
            ),
        ),
        identity_keys=(
            identity_actor_owner_key(change_set.authorization_actor_id),
            f'identity:directory-connection:{preparation.connection_id}',
            *(identity_actor_owner_key(user.actor_id) for user in preparation.users),
            *(
                directory_subject_owner_key(
                    preparation.connection_id,
                    external_subject,
                )
                for external_subject, _expected in preparation.expected_subject_bindings
            ),
            *(
                key
                for identity in preparation.new_external_identities
                for key in directory_identity_owner_keys(identity)
            ),
            *(
                key
                for membership in change_set.team_memberships
                for key in (
                    f'team:membership:{membership.membership_id}',
                    team_owner_key(membership.team_id),
                    team_subject_owner_key(
                        membership.member_actor_type,
                        membership.member_actor_id,
                    ),
                )
            ),
            *(
                key
                for grant in change_set.project_grants
                for key in (
                    f'project:grant:{grant.grant_id}',
                    project_owner_key(grant.project_id),
                    project_acl_subject_owner_key(
                        grant.subject_type,
                        grant.subject_id,
                    ),
                )
            ),
        ),
    )
