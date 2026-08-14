from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.identity_access import (
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    team_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.identity_access.records import (
    TeamMembershipRecord,
    TeamRecord,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]
_MAX_TEAM_DEPTH = 5


def _team_row(record: TeamRecord) -> AtlasTeamRow:
    return AtlasTeamRow(
        team_id=record.team_id,
        name=record.name,
        parent_team_id=record.parent_team_id,
        status=record.status,
        created_at=record.created_at,
        inherit_parent_documents=record.inherit_parent_documents,
    )


def _membership_row(record: TeamMembershipRecord) -> AtlasTeamMembershipRow:
    return AtlasTeamMembershipRow(
        membership_id=record.membership_id,
        team_id=record.team_id,
        member_actor_type=record.member_actor_type,
        member_actor_id=record.member_actor_id,
        role=record.role,
        status=record.status,
        created_at=record.created_at,
        removed_at=record.removed_at,
    )


def _team_record(row: AtlasTeamRow) -> TeamRecord:
    return TeamRecord(
        team_id=row.team_id,
        name=row.name,
        parent_team_id=row.parent_team_id,
        status=row.status,
        created_at=row.created_at,
        inherit_parent_documents=row.inherit_parent_documents,
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


@dataclass(frozen=True, slots=True)
class TeamMembershipWriter:
    _session: Session

    def merge(self, membership: TeamMembershipRecord) -> None:
        self._session.merge(_membership_row(membership))


@dataclass(frozen=True, slots=True)
class TeamGovernanceChangeSet:
    teams: tuple[TeamRecord, ...] = ()
    expected_teams: tuple[tuple[str, TeamRecord | None], ...] = ()
    memberships: tuple[TeamMembershipRecord, ...] = ()
    expected_memberships: tuple[
        tuple[str, TeamMembershipRecord | None], ...
    ] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()
    protect_hierarchy: bool = False
    protected_admin_team_ids: tuple[str, ...] = ()
    authorization_actor_ids: tuple[str, ...] = ()
    current_actor_ids: tuple[str, ...] = ()
    authorization_team_id: str | None = None
    authorization_requires_system_admin: bool = False

    def __post_init__(self) -> None:
        if (self.teams or self.memberships) and not self.audit_events:
            raise ValueError("Team mutation requires audit events")


class TeamInvariantViolation(RuntimeError):
    pass


class TeamAuthorizationConflict(RuntimeError):
    pass


class TeamCurrentnessConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TeamRepository:
    session_factory: SessionFactory

    def team_governance(self, change_set: TeamGovernanceChangeSet) -> None:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        *(("team:hierarchy-control",) if change_set.protect_hierarchy else ()),
                        *(("team:membership-control",) if change_set.memberships else ()),
                    ),
                    identity_keys=(
                        *(
                            identity_actor_owner_key(actor_id)
                            for actor_id in (
                                *change_set.authorization_actor_ids,
                                *change_set.current_actor_ids,
                            )
                        ),
                        *(
                            f"team:admin-control:{team_id}"
                            for team_id in change_set.protected_admin_team_ids
                        ),
                        *(team_owner_key(team.team_id) for team in change_set.teams),
                        *(
                            f"team:membership:{membership.membership_id}"
                            for membership in change_set.memberships
                        ),
                        *(
                            team_owner_key(membership.team_id)
                            for membership in change_set.memberships
                        ),
                        *(
                            team_subject_owner_key(
                                membership.member_actor_type,
                                membership.member_actor_id,
                            )
                            for membership in change_set.memberships
                        ),
                        *(
                            identity_actor_owner_key(membership.member_actor_id)
                            for membership in change_set.memberships
                        ),
                    ),
                )
                self._validate_currentness(session, change_set)
                self._validate_actor_currentness(session, change_set)
                if change_set.protect_hierarchy:
                    self._validate_hierarchy(session, change_set.teams)
                self._validate_authorization(session, change_set)
                self._validate_direct_admins(
                    session,
                    change_set.memberships,
                    change_set.protected_admin_team_ids,
                )
                for team in change_set.teams:
                    session.merge(_team_row(team))
                membership_writer = TeamMembershipWriter(session)
                for membership in change_set.memberships:
                    membership_writer.merge(membership)
                AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _validate_currentness(
        session: Session,
        change_set: TeamGovernanceChangeSet,
    ) -> None:
        for team_id, expected in change_set.expected_teams:
            row = session.scalar(
                select(AtlasTeamRow)
                .where(AtlasTeamRow.team_id == team_id)
                .with_for_update()
            )
            current = _team_record(row) if row is not None else None
            if current != expected:
                raise TeamCurrentnessConflict("Team currentness changed")
        for membership_id, expected in change_set.expected_memberships:
            row = session.scalar(
                select(AtlasTeamMembershipRow)
                .where(AtlasTeamMembershipRow.membership_id == membership_id)
                .with_for_update()
            )
            current = _membership_record(row) if row is not None else None
            if current != expected:
                raise TeamCurrentnessConflict("Team membership currentness changed")

    @staticmethod
    def _validate_hierarchy(
        session: Session,
        changed_teams: tuple[TeamRecord, ...],
    ) -> None:
        current_rows = session.scalars(
            select(AtlasTeamRow)
            .order_by(AtlasTeamRow.team_id)
            .with_for_update()
        ).all()
        parents = {row.team_id: row.parent_team_id for row in current_rows}
        parents.update({team.team_id: team.parent_team_id for team in changed_teams})
        for team_id, parent_id in parents.items():
            if parent_id is not None and parent_id not in parents:
                raise TeamInvariantViolation("Team parent must exist")
            seen = {team_id}
            depth = 1
            cursor = parent_id
            while cursor is not None:
                if cursor in seen:
                    raise TeamInvariantViolation("Team hierarchy must remain acyclic")
                seen.add(cursor)
                depth += 1
                if depth > _MAX_TEAM_DEPTH:
                    raise TeamInvariantViolation("Team hierarchy exceeds maximum depth")
                cursor = parents.get(cursor)

    @staticmethod
    def _validate_actor_currentness(
        session: Session,
        change_set: TeamGovernanceChangeSet,
    ) -> None:
        for actor_id in change_set.current_actor_ids:
            actor = session.get(AtlasUserRow, actor_id)
            if actor is None or not actor.active:
                raise TeamCurrentnessConflict("Team actor currentness changed")

    @staticmethod
    def _validate_authorization(
        session: Session,
        change_set: TeamGovernanceChangeSet,
    ) -> None:
        for actor_id in change_set.authorization_actor_ids:
            actor = session.get(AtlasUserRow, actor_id)
            if actor is None or not actor.active or actor.actor_type != "user":
                raise TeamAuthorizationConflict("Team actor is no longer active")
            if actor.system_role == "admin":
                continue
            if change_set.authorization_requires_system_admin:
                raise TeamAuthorizationConflict("System Admin authority changed")
            team_id = change_set.authorization_team_id
            team = session.get(AtlasTeamRow, team_id) if team_id else None
            membership = session.scalar(
                select(AtlasTeamMembershipRow).where(
                    AtlasTeamMembershipRow.team_id == team_id,
                    AtlasTeamMembershipRow.member_actor_type == "user",
                    AtlasTeamMembershipRow.member_actor_id == actor_id,
                    AtlasTeamMembershipRow.status == "active",
                    AtlasTeamMembershipRow.role == "admin",
                ).limit(1)
            ) if team_id else None
            if team is None or team.status != "active" or membership is None:
                raise TeamAuthorizationConflict("Team Admin authority changed")

    @staticmethod
    def _validate_direct_admins(
        session: Session,
        changed_memberships: tuple[TeamMembershipRecord, ...],
        protected_team_ids: tuple[str, ...],
    ) -> None:
        changed_by_team: dict[str, dict[str, TeamMembershipRecord]] = {}
        for membership in changed_memberships:
            changed_by_team.setdefault(membership.team_id, {})[
                membership.membership_id
            ] = membership
        for team_id in sorted(set(protected_team_ids)):
            rows = session.scalars(
                select(AtlasTeamMembershipRow)
                .where(AtlasTeamMembershipRow.team_id == team_id)
                .order_by(AtlasTeamMembershipRow.membership_id)
                .with_for_update()
            ).all()
            projected = {row.membership_id: _membership_record(row) for row in rows}
            projected.update(changed_by_team.get(team_id, {}))
            referenced_user_ids = {
                membership.member_actor_id
                for membership in projected.values()
                if membership.member_actor_type == "user"
            }
            user_rows = session.scalars(
                select(AtlasUserRow)
                .where(AtlasUserRow.actor_id.in_(referenced_user_ids or {""}))
                .order_by(AtlasUserRow.actor_id)
                .with_for_update()
            ).all()
            active_user_ids = {row.actor_id for row in user_rows if row.active}
            if not any(
                membership.member_actor_type == "user"
                and membership.member_actor_id in active_user_ids
                and membership.status == "active"
                and membership.role == "admin"
                for membership in projected.values()
            ):
                raise TeamInvariantViolation(
                    "at least one active direct human Team Admin is required"
                )

    def get_team(self, team_id: str) -> TeamRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasTeamRow).where(AtlasTeamRow.team_id == team_id)
            )
            return _team_record(row) if row is not None else None

    def list_teams(
        self,
        *,
        limit: int = 500,
        after_team_id: str | None = None,
    ) -> list[TeamRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Team list limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasTeamRow)
            if after_team_id is not None:
                statement = statement.where(AtlasTeamRow.team_id > after_team_id)
            rows = session.scalars(
                statement.order_by(AtlasTeamRow.team_id).limit(limit)
            ).all()
            return [_team_record(row) for row in rows]

    def list_memberships(
        self,
        *,
        team_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 500,
        after_membership_id: str | None = None,
    ) -> list[TeamMembershipRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Team membership limit must be between 1 and 500")
        statement = select(AtlasTeamMembershipRow)
        if team_id is not None:
            statement = statement.where(AtlasTeamMembershipRow.team_id == team_id)
        if actor_id is not None:
            statement = statement.where(
                AtlasTeamMembershipRow.member_actor_id == actor_id
            )
        if after_membership_id is not None:
            statement = statement.where(
                AtlasTeamMembershipRow.membership_id > after_membership_id
            )
        statement = statement.order_by(AtlasTeamMembershipRow.membership_id).limit(limit)
        with self.session_factory() as session:
            return [
                _membership_record(row)
                for row in session.scalars(statement).all()
            ]

    def get_membership(
        self,
        membership_id: str,
    ) -> TeamMembershipRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasTeamMembershipRow, membership_id)
            return _membership_record(row) if row is not None else None


__all__ = [
    "TeamAuthorizationConflict",
    "TeamCurrentnessConflict",
    "TeamGovernanceChangeSet",
    "TeamInvariantViolation",
    "TeamMembershipWriter",
    "TeamRepository",
]
