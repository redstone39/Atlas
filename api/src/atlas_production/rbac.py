from collections import defaultdict
from uuid import uuid4

from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
)
from atlas_production.shared.public import (
    utc_now_iso,
)


ROLE_ORDER = {
    "viewer": 1,
    "contributor": 2,
    "admin": 3,
}

TEAM_ROLE_ORDER = {
    "member": 1,
    "uploader": 2,
    "admin": 3,
}

ACTION_REQUIRED_ROLE = {
    "workspace_query": "viewer",
    "notes_membership": "viewer",
    "citation_copy": "viewer",
    "copy_citation": "viewer",
    "read_derived": "viewer",
    "read_citation": "viewer",
    "read_original": "viewer",
    "preview": "viewer",
    "agent_query": "viewer",
    "document_register": "contributor",
    "ingestion_run": "contributor",
    "membership_manage": "admin",
    "permission_manage": "admin",
}

MAX_TEAM_DEPTH = 5


def role_covers(role: str, required_role: str) -> bool:
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(required_role, 0)


def team_role_covers(role: str | None, required_role: str) -> bool:
    return TEAM_ROLE_ORDER.get(role or "", 0) >= TEAM_ROLE_ORDER.get(required_role, 0)


def highest_role(roles: list[str]) -> str | None:
    if not roles:
        return None
    return max(roles, key=lambda item: ROLE_ORDER.get(item, 0))


def team_depth(store, team_id: str) -> int | None:
    team = store.teams.get(team_id)
    if not team:
        return None
    depth = 1
    seen = {team_id}
    parent_id = team.parent_team_id
    while parent_id:
        if parent_id in seen:
            return None
        seen.add(parent_id)
        parent = store.teams.get(parent_id)
        if not parent:
            return None
        depth += 1
        parent_id = parent.parent_team_id
    return depth


def would_create_cycle(store, team_id: str, parent_team_id: str | None) -> bool:
    seen = {team_id}
    current_id = parent_team_id
    while current_id:
        if current_id in seen:
            return True
        seen.add(current_id)
        current = store.teams.get(current_id)
        if not current:
            return False
        current_id = current.parent_team_id
    return False


def would_exceed_depth(store, team_id: str, parent_team_id: str | None) -> bool:
    original_parent = None
    if team_id in store.teams:
        original_parent = store.teams[team_id].parent_team_id
        store.teams[team_id].parent_team_id = parent_team_id
    try:
        if team_id not in store.teams:
            parent_depth = team_depth(store, parent_team_id) if parent_team_id else 0
            return parent_depth is None or parent_depth + 1 > MAX_TEAM_DEPTH
        return any(
            depth is None or depth > MAX_TEAM_DEPTH
            for depth in (team_depth(store, candidate.team_id) for candidate in store.teams.values())
        )
    finally:
        if team_id in store.teams:
            store.teams[team_id].parent_team_id = original_parent


def actor_team_tiers(store, actor_type: str, actor_id: str) -> dict[str, int]:
    tiers: dict[str, int] = {}
    for membership in store.team_memberships.values():
        if (
            membership.member_actor_type != actor_type
            or membership.member_actor_id != actor_id
            or membership.status != "active"
        ):
            continue
        distance = 0
        seen: set[str] = set()
        current_id = membership.team_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            team = store.teams.get(current_id)
            if not team or team.status != "active":
                break
            tier = 1 + distance
            tiers[current_id] = min(tiers.get(current_id, tier), tier)
            current_id = team.parent_team_id
            distance += 1
    return tiers


def actor_direct_team_roles(store, actor_type: str, actor_id: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for membership in store.team_memberships.values():
        if (
            membership.member_actor_type != actor_type
            or membership.member_actor_id != actor_id
            or membership.status != "active"
        ):
            continue
        existing = roles.get(membership.team_id)
        if not existing or TEAM_ROLE_ORDER.get(membership.role, 0) > TEAM_ROLE_ORDER.get(existing, 0):
            roles[membership.team_id] = membership.role
    return roles


def direct_team_role(store, actor_type: str, actor_id: str, team_id: str) -> str | None:
    return actor_direct_team_roles(store, actor_type, actor_id).get(team_id)


def is_system_admin(store, actor_type: str, actor_id: str) -> bool:
    actor = store.users.get(actor_id)
    return bool(actor and actor.actor_type == actor_type and actor.active and actor.system_role == "admin")


def effective_team_tag_ids(store, actor_type: str, actor_id: str) -> set[str]:
    if is_system_admin(store, actor_type, actor_id):
        return {
            team.team_id
            for team in store.teams.values()
            if team.status == "active"
        }
    team_ids: set[str] = set()
    for membership in store.team_memberships.values():
        if (
            membership.member_actor_type != actor_type
            or membership.member_actor_id != actor_id
            or membership.status != "active"
        ):
            continue
        seen: set[str] = set()
        current_id = membership.team_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            team = store.teams.get(current_id)
            if not team or team.status != "active":
                break
            team_ids.add(current_id)
            if not team.inherit_parent_documents:
                break
            current_id = team.parent_team_id
    return team_ids


def authorized_project_tag_ids(
    store,
    actor_type: str,
    actor_id: str,
    *,
    action: str = "workspace_query",
) -> set[str]:
    if is_system_admin(store, actor_type, actor_id):
        return set(store.projects)
    project_ids: set[str] = set()
    for project_id in store.projects:
        decision = resolve_access(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            persist=False,
        )
        if decision.allowed:
            project_ids.add(project_id)
    return project_ids


def effective_document_scope(
    store,
    *,
    actor_type: str,
    actor_id: str,
    action: str = "workspace_query",
) -> set[tuple[str, str]]:
    return {
        *{("project", project_id) for project_id in authorized_project_tag_ids(store, actor_type, actor_id, action=action)},
        *{("team", team_id) for team_id in effective_team_tag_ids(store, actor_type, actor_id)},
    }


def document_ids_for_scope(store, scope: set[tuple[str, str]]) -> set[str]:
    return {
        tag.document_id
        for tag in store.document_tags.values()
        if (tag.tag_type, tag.tag_id) in scope
        and (document := store.documents.get(tag.document_id)) is not None
        and document.lifecycle_status == "active"
    }


def resolve_access(
    store,
    *,
    actor_type: str,
    actor_id: str,
    project_id: str,
    action: str,
    persist: bool = True,
) -> AccessDecisionRecord:
    required_role = ACTION_REQUIRED_ROLE[action]
    actor = store.users.get(actor_id)
    if not actor or actor.actor_type != actor_type or not actor.active:
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=False,
            reason="actor_inactive_or_missing",
            effective_role=None,
            source_type=None,
            source_id=None,
            explanation="The actor is inactive or missing.",
        )
        return _persist_decision(store, decision, persist)
    if project_id not in store.projects:
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=False,
            reason="project_missing",
            effective_role=None,
            source_type=None,
            source_id=None,
            explanation="The project was not found.",
        )
        return _persist_decision(store, decision, persist)
    if actor.system_role == "admin":
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=True,
            reason="system_admin",
            effective_role="admin",
            source_type=actor_type,
            source_id=actor_id,
            explanation="System admin grants access to this project action.",
        )
        return _persist_decision(store, decision, persist)

    candidates_by_tier: dict[int, list[PermissionGrantRecord]] = defaultdict(list)
    for grant in store.permission_grants.values():
        if grant.project_id != project_id or grant.status != "active":
            continue
        if grant.subject_type == actor_type and grant.subject_id == actor_id:
            candidates_by_tier[0].append(grant)

    team_tiers = actor_team_tiers(store, actor_type, actor_id)
    for grant in store.permission_grants.values():
        if grant.project_id != project_id or grant.status != "active":
            continue
        if grant.subject_type == "team" and grant.subject_id in team_tiers:
            candidates_by_tier[team_tiers[grant.subject_id]].append(grant)

    if not candidates_by_tier:
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=False,
            reason="missing_permission",
            effective_role=None,
            source_type=None,
            source_id=None,
            explanation="No active permission grant applies to this actor and project.",
        )
        return _persist_decision(store, decision, persist)

    tier = min(candidates_by_tier)
    candidates = sorted(candidates_by_tier[tier], key=lambda item: item.grant_id)
    covering_denies = [
        grant for grant in candidates if grant.effect == "deny" and role_covers(grant.role, required_role)
    ]
    if covering_denies:
        winner = max(covering_denies, key=lambda item: (ROLE_ORDER[item.role], item.grant_id))
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=False,
            reason="deny_grant",
            effective_role=None,
            source_type=winner.subject_type,
            source_id=winner.grant_id,
            explanation=f"{_subject_label(winner)} denies {winner.role} access.",
        )
        return _persist_decision(store, decision, persist)

    covering_allows = [
        grant for grant in candidates if grant.effect == "allow" and role_covers(grant.role, required_role)
    ]
    if covering_allows:
        winner = max(covering_allows, key=lambda item: (ROLE_ORDER[item.role], item.grant_id))
        decision = _decision(
            store,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role=required_role,
            allowed=True,
            reason="allow_grant",
            effective_role=highest_role([grant.role for grant in covering_allows]),
            source_type=winner.subject_type,
            source_id=winner.grant_id,
            explanation=f"{_subject_label(winner)} grants {winner.role} access.",
        )
        return _persist_decision(store, decision, persist)

    strongest = max(candidates, key=lambda item: (ROLE_ORDER.get(item.role, 0), item.grant_id))
    decision = _decision(
        store,
        actor_type=actor_type,
        actor_id=actor_id,
        project_id=project_id,
        action=action,
        required_role=required_role,
        allowed=False,
        reason="missing_required_role",
        effective_role=highest_role([grant.role for grant in candidates if grant.effect == "allow"]),
        source_type=strongest.subject_type,
        source_id=strongest.grant_id,
        explanation=f"The most specific grant does not include the required {required_role} role.",
    )
    return _persist_decision(store, decision, persist)


def _subject_label(grant: PermissionGrantRecord) -> str:
    if grant.subject_type == "team":
        return f"Team {grant.subject_id}"
    return f"Actor {grant.subject_id}"


def _decision(
    store,
    *,
    actor_type: str,
    actor_id: str,
    project_id: str,
    action: str,
    required_role: str,
    allowed: bool,
    reason: str,
    effective_role: str | None,
    source_type: str | None,
    source_id: str | None,
    explanation: str,
) -> AccessDecisionRecord:
    return AccessDecisionRecord(
        decision_id=f"access-{uuid4().hex}",
        actor_type=actor_type,
        actor_id=actor_id,
        project_id=project_id,
        action=action,
        required_role=required_role,
        allowed=allowed,
        reason=reason,
        effective_role=effective_role,
        source_type=source_type,
        source_id=source_id,
        explanation=explanation,
        created_at=utc_now_iso(),
    )


def _persist_decision(store, decision: AccessDecisionRecord, persist: bool) -> AccessDecisionRecord:
    if persist:
        store.access_decisions[decision.decision_id] = decision
        store.persist_read_observability(access_decisions=(decision,))
    return decision
