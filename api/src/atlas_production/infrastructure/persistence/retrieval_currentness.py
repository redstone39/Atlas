from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, or_, select, tuple_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.rbac import (
    actor_team_tiers,
    direct_team_role,
    effective_document_scope,
    is_system_admin,
    resolve_access,
    team_role_covers,
)

from .document_intake import AtlasDocumentRow, AtlasDocumentTagRow
from .identity_access import (
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from .project_governance import AtlasProjectRow


@dataclass(slots=True)
class RequestAuthorizationState:
    """Bounded durable rows used by the existing RBAC evaluator once."""

    users: dict[str, UserRecord] = field(default_factory=dict)
    projects: dict[str, ProjectRecord] = field(default_factory=dict)
    teams: dict[str, TeamRecord] = field(default_factory=dict)
    team_memberships: dict[str, TeamMembershipRecord] = field(
        default_factory=dict
    )
    permission_grants: dict[str, PermissionGrantRecord] = field(
        default_factory=dict
    )


def _record(row: Any, record_type: type[Any]) -> Any:
    return record_type(
        **{
            field_name: getattr(row, field_name)
            for field_name in record_type.__dataclass_fields__
        }
    )


def _team_hierarchy_rows(
    session: Session,
    direct_team_ids: set[str],
) -> list[AtlasTeamRow]:
    rows: list[AtlasTeamRow] = []
    loaded: set[str] = set()
    unresolved = set(direct_team_ids)
    while unresolved:
        current = list(
            session.execute(
                select(AtlasTeamRow).where(
                    AtlasTeamRow.team_id.in_(sorted(unresolved))
                )
            ).scalars()
        )
        if not current:
            break
        rows.extend(current)
        loaded.update(row.team_id for row in current)
        unresolved = {
            row.parent_team_id
            for row in current
            if row.parent_team_id is not None
            and row.parent_team_id not in loaded
        }
    return rows


def read_effective_document_scope(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    requested_scope: set[tuple[str, str]] | None = None,
) -> set[tuple[str, str]]:
    """Resolve current Project/Team scope without publishing shared state."""

    scope, _team_ids, _can_administer_owner_scope = (
        read_effective_document_scope_with_team_ids(
            session,
            actor_type=actor_type,
            actor_id=actor_id,
            requested_scope=requested_scope,
        )
    )
    return scope


def read_effective_document_scope_with_team_ids(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    requested_scope: set[tuple[str, str]] | None = None,
    owner_scope_type: str | None = None,
    owner_scope_id: str | None = None,
) -> tuple[set[tuple[str, str]], set[str], bool]:
    """Resolve current scope, exact Team ancestors, and owner administration."""
    if (owner_scope_type is None) != (owner_scope_id is None):
        raise ValueError("owner scope type and id must be provided together")

    actor_row = session.get(AtlasUserRow, actor_id)
    if (
        actor_row is None
        or actor_row.actor_type != actor_type
        or not actor_row.active
    ):
        return set(), set(), False
    actor = _record(actor_row, UserRecord)
    state = RequestAuthorizationState(users={actor.actor_id: actor})
    requested_project_ids = (
        None
        if requested_scope is None
        else {
            scope_id
            for scope_type, scope_id in requested_scope
            if scope_type == "project"
        }
    )
    requested_team_ids = (
        None
        if requested_scope is None
        else {
            scope_id
            for scope_type, scope_id in requested_scope
            if scope_type == "team"
        }
    )

    if actor.system_role == "admin":
        team_rows = list(
            session.execute(
                select(AtlasTeamRow).where(
                    AtlasTeamRow.team_id.in_(
                        sorted(requested_team_ids or set())
                    )
                )
                if requested_team_ids is not None
                else select(AtlasTeamRow)
            ).scalars()
        )
        project_rows = list(
            session.execute(
                select(AtlasProjectRow).where(
                    AtlasProjectRow.project_id.in_(
                        sorted(requested_project_ids or set())
                    )
                )
                if requested_project_ids is not None
                else select(AtlasProjectRow)
            ).scalars()
        )
    else:
        membership_rows = list(
            session.execute(
                select(AtlasTeamMembershipRow).where(
                    AtlasTeamMembershipRow.member_actor_type == actor_type,
                    AtlasTeamMembershipRow.member_actor_id == actor_id,
                    AtlasTeamMembershipRow.status == "active",
                )
            ).scalars()
        )
        team_rows = _team_hierarchy_rows(
            session,
            {row.team_id for row in membership_rows},
        )
        team_ids = {row.team_id for row in team_rows}
        subject_conditions = [
            and_(
                AtlasPermissionGrantRow.subject_type == actor_type,
                AtlasPermissionGrantRow.subject_id == actor_id,
            )
        ]
        if team_ids:
            subject_conditions.append(
                and_(
                    AtlasPermissionGrantRow.subject_type == "team",
                    AtlasPermissionGrantRow.subject_id.in_(sorted(team_ids)),
                )
            )
        grant_statement = select(AtlasPermissionGrantRow).where(
            AtlasPermissionGrantRow.status == "active",
            or_(*subject_conditions),
        )
        if requested_project_ids is not None:
            grant_statement = grant_statement.where(
                AtlasPermissionGrantRow.project_id.in_(
                    sorted(requested_project_ids)
                )
            )
        grant_rows = (
            list(session.execute(grant_statement).scalars())
            if requested_project_ids is None or requested_project_ids
            else []
        )
        project_ids = {row.project_id for row in grant_rows}
        project_rows = (
            list(
                session.execute(
                    select(AtlasProjectRow).where(
                        AtlasProjectRow.project_id.in_(sorted(project_ids))
                    )
                ).scalars()
            )
            if project_ids
            else []
        )
        state.team_memberships = {
            record.membership_id: record
            for record in (
                _record(row, TeamMembershipRecord)
                for row in membership_rows
            )
        }
        state.permission_grants = {
            record.grant_id: record
            for record in (
                _record(row, PermissionGrantRecord) for row in grant_rows
            )
        }

    state.teams = {
        record.team_id: record
        for record in (_record(row, TeamRecord) for row in team_rows)
    }
    state.projects = {
        record.project_id: record
        for record in (_record(row, ProjectRecord) for row in project_rows)
    }
    resolved_scope = effective_document_scope(
        state,
        actor_type=actor_type,
        actor_id=actor_id,
        action="workspace_query",
    )
    if requested_scope is not None:
        resolved_scope.intersection_update(requested_scope)
    effective_team_ids = (
        {
            team.team_id
            for team in state.teams.values()
            if team.status == "active"
        }
        if actor.system_role == "admin"
        else set(actor_team_tiers(state, actor_type, actor_id))
    )
    can_administer_owner_scope = False
    if owner_scope_type is not None and owner_scope_id is not None:
        owner_scope = (owner_scope_type, owner_scope_id)
        can_administer_owner_scope = owner_scope in resolved_scope and (
            is_system_admin(state, actor_type, actor_id)
            or (
                owner_scope_type == "team"
                and team_role_covers(
                    direct_team_role(
                        state, actor_type, actor_id, owner_scope_id
                    ),
                    "admin",
                )
            )
            or (
                owner_scope_type == "project"
                and resolve_access(
                    state,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    project_id=owner_scope_id,
                    action="permission_manage",
                    persist=False,
                ).allowed
            )
        )
    return resolved_scope, effective_team_ids, can_administer_owner_scope


def document_owner_row_is_active(
    session: Session,
    scope_type: str,
    scope_id: str | None,
) -> bool:
    if not scope_id:
        return False
    if scope_type == "project":
        owner = session.get(AtlasProjectRow, scope_id, populate_existing=True)
    elif scope_type == "team":
        owner = session.get(AtlasTeamRow, scope_id, populate_existing=True)
    else:
        return False
    return bool(owner and owner.status == "active")


def _active_document_owner_clause(
    scope_type_column: Any,
    scope_id_column: Any,
) -> ColumnElement[bool]:
    return or_(
        and_(
            scope_type_column == "project",
            exists(
                select(1).where(
                    AtlasProjectRow.project_id == scope_id_column,
                    AtlasProjectRow.status == "active",
                )
            ),
        ),
        and_(
            scope_type_column == "team",
            exists(
                select(1).where(
                    AtlasTeamRow.team_id == scope_id_column,
                    AtlasTeamRow.status == "active",
                )
            ),
        ),
    )




def read_current_document_ids_for_scope(
    session: Session,
    scope: set[tuple[str, str]],
) -> set[str]:
    """Read active documents whose durable direct tags match current scope."""

    if not scope:
        return set()
    return set(
        session.execute(
            select(AtlasDocumentTagRow.document_id)
            .join(
                AtlasDocumentRow,
                AtlasDocumentRow.document_id
                == AtlasDocumentTagRow.document_id,
            )
            .where(
                AtlasDocumentRow.lifecycle_status == "active",
                tuple_(
                    AtlasDocumentTagRow.tag_type,
                    AtlasDocumentTagRow.tag_id,
                ).in_(sorted(scope)),
                _active_document_owner_clause(
                    AtlasDocumentRow.scope_type,
                    AtlasDocumentRow.scope_id,
                ),
            )
            .distinct()
        ).scalars()
    )
