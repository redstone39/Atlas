"""Private collaborator-free projections for Identity currentness invariants."""

from __future__ import annotations

from atlas_production.modules.identity_access.records import (
    TeamMembershipRecord,
    UserRecord,
)


def projected_active_admin_ids(
    current_admin_ids: set[str],
    changed_users: tuple[UserRecord, ...],
) -> frozenset[str]:
    active_admin_ids = set(current_admin_ids)
    for user in changed_users:
        if (
            user.actor_type == "user"
            and user.active
            and user.system_role == "admin"
        ):
            active_admin_ids.add(user.actor_id)
        else:
            active_admin_ids.discard(user.actor_id)
    return frozenset(active_admin_ids)


def projected_users_by_actor(
    current_users: tuple[UserRecord, ...],
    changed_users: tuple[UserRecord, ...],
) -> dict[str, UserRecord]:
    projected = {user.actor_id: user for user in current_users}
    projected.update({user.actor_id: user for user in changed_users})
    return projected


def has_active_direct_human_team_admin(
    *,
    team_id: str,
    memberships: tuple[TeamMembershipRecord, ...],
    users_by_actor: dict[str, UserRecord],
) -> bool:
    return any(
        membership.team_id == team_id
        and membership.member_actor_type == "user"
        and membership.status == "active"
        and membership.role == "admin"
        and (user := users_by_actor.get(membership.member_actor_id)) is not None
        and user.active
        for membership in memberships
    )
