from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import (
    AccessDecisionWriter,
    AuditEventWriter,
)
from atlas_production.modules.identity_access.notes_membership import (
    CurrentTeamNotesMembershipSnapshot,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
)
from atlas_production.modules.project_governance.notes_membership import (
    CurrentProjectNotesMembershipSnapshot,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.shared.public import AuditEventRecord
from atlas_production.shared.public import utc_now_iso
from atlas_production.rbac import (
    ACTION_REQUIRED_ROLE,
    MAX_TEAM_DEPTH,
    ROLE_ORDER,
    highest_role,
    role_covers,
)


SessionFactory = Callable[[], Session]


def _project_row(record: ProjectRecord) -> AtlasProjectRow:
    return AtlasProjectRow(
        project_id=record.project_id,
        name=record.name,
        policy_profile_id=record.policy_profile_id,
    )


def _grant_row(record: PermissionGrantRecord) -> AtlasPermissionGrantRow:
    return AtlasPermissionGrantRow(
        grant_id=record.grant_id,
        project_id=record.project_id,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        role=record.role,
        effect=record.effect,
        status=record.status,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
    )


def _project_record(row: AtlasProjectRow) -> ProjectRecord:
    return ProjectRecord(
        project_id=row.project_id,
        name=row.name,
        policy_profile_id=row.policy_profile_id,
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


def _access_decision(
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


@dataclass(frozen=True, slots=True)
class ProjectGrantWriter:
    _session: Session

    def merge(self, grant: PermissionGrantRecord) -> None:
        self._session.merge(_grant_row(grant))


@dataclass(frozen=True, slots=True)
class ProjectAclChangeSet:
    projects: tuple[ProjectRecord, ...] = ()
    expected_projects: tuple[tuple[str, ProjectRecord | None], ...] = ()
    grants: tuple[PermissionGrantRecord, ...] = ()
    expected_grants: tuple[
        tuple[str, PermissionGrantRecord | None], ...
    ] = ()
    access_decisions: tuple[AccessDecisionRecord, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()
    authorization_actor_id: str | None = None
    authorization_project_id: str | None = None
    authorization_action: str | None = None
    authorization_requires_system_admin: bool = False

    def __post_init__(self) -> None:
        if (self.projects or self.grants or self.access_decisions) and not self.audit_events:
            raise ValueError("Project or ACL mutation requires audit events")
        decision_ids = [decision.decision_id for decision in self.access_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("access decisions must be append-only unique facts")


@dataclass(frozen=True, slots=True)
class ProjectAclRepository:
    session_factory: SessionFactory

    def project_acl(self, change_set: ProjectAclChangeSet) -> None:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        *(("team:hierarchy-control", "team:membership-control")
                          if change_set.authorization_action is not None else ()),
                        *(
                            f"project:acl-control:{project_id}"
                            for project_id in {
                                *(grant.project_id for grant in change_set.grants),
                                *(
                                    (change_set.authorization_project_id,)
                                    if change_set.authorization_project_id
                                    else ()
                                ),
                            }
                        ),
                    ),
                    identity_keys=(
                        *(
                            (identity_actor_owner_key(change_set.authorization_actor_id),)
                            if change_set.authorization_actor_id
                            else ()
                        ),
                        *(
                            (
                                team_subject_owner_key(
                                    "user",
                                    change_set.authorization_actor_id,
                                ),
                                team_subject_owner_key(
                                    "service_account",
                                    change_set.authorization_actor_id,
                                ),
                                project_acl_subject_owner_key(
                                    "user",
                                    change_set.authorization_actor_id,
                                ),
                                project_acl_subject_owner_key(
                                    "service_account",
                                    change_set.authorization_actor_id,
                                ),
                            )
                            if change_set.authorization_actor_id
                            and change_set.authorization_action is not None
                            else ()
                        ),
                        *(
                            project_owner_key(project.project_id)
                            for project in change_set.projects
                        ),
                        *(
                            f"project:grant:{grant.grant_id}"
                            for grant in change_set.grants
                        ),
                        *(
                            project_owner_key(grant.project_id)
                            for grant in change_set.grants
                        ),
                        *(
                            project_acl_subject_owner_key(
                                grant.subject_type,
                                grant.subject_id,
                            )
                            for grant in change_set.grants
                        ),
                        *(
                            f"audit:decision:{decision.decision_id}"
                            for decision in change_set.access_decisions
                        ),
                    ),
                )
                self._validate_currentness(session, change_set)
                self._validate_authorization(session, change_set)
                for project in change_set.projects:
                    session.merge(_project_row(project))
                grant_writer = ProjectGrantWriter(session)
                for grant in change_set.grants:
                    grant_writer.merge(grant)
                decision_writer = AccessDecisionWriter(session)
                for decision in change_set.access_decisions:
                    decision_writer.append(decision)
                AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _validate_currentness(
        session: Session,
        change_set: ProjectAclChangeSet,
    ) -> None:
        for project_id, expected in change_set.expected_projects:
            row = session.scalar(
                select(AtlasProjectRow)
                .where(AtlasProjectRow.project_id == project_id)
                .with_for_update()
            )
            current = _project_record(row) if row is not None else None
            if current != expected:
                raise ProjectCurrentnessConflict("Project currentness changed")
        for grant_id, expected in change_set.expected_grants:
            row = session.scalar(
                select(AtlasPermissionGrantRow)
                .where(AtlasPermissionGrantRow.grant_id == grant_id)
                .with_for_update()
            )
            current = _grant_record(row) if row is not None else None
            if current != expected:
                raise ProjectCurrentnessConflict("Project grant currentness changed")

    @staticmethod
    def _validate_authorization(
        session: Session,
        change_set: ProjectAclChangeSet,
    ) -> None:
        actor_id = change_set.authorization_actor_id
        if actor_id is None:
            return
        actor = session.get(AtlasUserRow, actor_id)
        if actor is None or not actor.active:
            raise ProjectAuthorizationConflict("Project actor is no longer active")
        if change_set.authorization_requires_system_admin:
            if actor.actor_type != "user" or actor.system_role != "admin":
                raise ProjectAuthorizationConflict("System Admin authority changed")
            return
        project_id = change_set.authorization_project_id
        action = change_set.authorization_action
        if not project_id or not action or not ActionAwareAclAuthority.resolve_in_session(
            session,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            project_id=project_id,
            action=action,
            lock_rows=True,
        ).allowed:
            raise ProjectAuthorizationConflict("Project action authority changed")

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProjectRow).where(AtlasProjectRow.project_id == project_id)
            )
            return _project_record(row) if row is not None else None

    def list_projects(
        self,
        *,
        limit: int = 500,
        after_project_id: str | None = None,
    ) -> list[ProjectRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Project list limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasProjectRow)
            if after_project_id is not None:
                statement = statement.where(
                    AtlasProjectRow.project_id > after_project_id
                )
            rows = session.scalars(
                statement.order_by(AtlasProjectRow.project_id).limit(limit)
            ).all()
            return [_project_record(row) for row in rows]

    def list_grants(
        self,
        *,
        project_id: str | None = None,
        limit: int = 500,
        after_grant_id: str | None = None,
    ) -> list[PermissionGrantRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Project grant limit must be between 1 and 500")
        with self.session_factory() as session:
            statement = select(AtlasPermissionGrantRow)
            if project_id is not None:
                statement = statement.where(
                    AtlasPermissionGrantRow.project_id == project_id
                )
            if after_grant_id is not None:
                statement = statement.where(
                    AtlasPermissionGrantRow.grant_id > after_grant_id
                )
            rows = session.scalars(
                statement.order_by(AtlasPermissionGrantRow.grant_id).limit(limit)
            ).all()
            return [_grant_record(row) for row in rows]

    def get_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasPermissionGrantRow, grant_id)
            return _grant_record(row) if row is not None else None

    def list_subject_grants(
        self,
        *,
        subject_type: str,
        subject_id: str | None = None,
        active_only: bool = False,
        limit: int = 500,
        after_grant_id: str | None = None,
    ) -> list[PermissionGrantRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("Subject grant limit must be between 1 and 500")
        statement = select(AtlasPermissionGrantRow).where(
            AtlasPermissionGrantRow.subject_type == subject_type
        )
        if subject_id is not None:
            statement = statement.where(
                AtlasPermissionGrantRow.subject_id == subject_id
            )
        if active_only:
            statement = statement.where(AtlasPermissionGrantRow.status == "active")
        if after_grant_id is not None:
            statement = statement.where(
                AtlasPermissionGrantRow.grant_id > after_grant_id
            )
        with self.session_factory() as session:
            rows = session.scalars(
                statement.order_by(AtlasPermissionGrantRow.grant_id).limit(limit)
            ).all()
            return [_grant_record(row) for row in rows]


@dataclass(frozen=True, slots=True)
class ActionAwareAclAuthority:
    """Resolve one named action from current PostgreSQL identity and ACL rows."""

    session_factory: SessionFactory

    def resolve(
        self,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
        action: str,
        persist: bool = True,
    ) -> AccessDecisionRecord:
        if action not in ACTION_REQUIRED_ROLE:
            raise ValueError(f"unknown ACL action: {action}")
        session = self.session_factory()
        with session:
            try:
                if persist:
                    acquire_owner_locks(
                        session,
                        domain_keys=(
                            "team:hierarchy-control",
                            "team:membership-control",
                            f"project:acl-control:{project_id}",
                        ),
                        identity_keys=(
                            identity_actor_owner_key(actor_id),
                            project_owner_key(project_id),
                            project_acl_subject_owner_key(actor_type, actor_id),
                            team_subject_owner_key(actor_type, actor_id),
                        ),
                    )
                decision = self.resolve_in_session(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    project_id=project_id,
                    action=action,
                    lock_rows=persist,
                )
                if persist:
                    AccessDecisionWriter(session).append(decision)
                    session.commit()
                return decision
            except Exception:
                session.rollback()
                raise

    def effective_document_scope(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
    ) -> set[tuple[str, str]]:
        if action not in ACTION_REQUIRED_ROLE:
            raise ValueError(f"unknown ACL action: {action}")
        with self.session_factory() as session:
            actor = session.get(AtlasUserRow, actor_id)
            if (
                actor is None
                or actor.actor_type != actor_type
                or not actor.active
            ):
                return set()
            project_rows = session.scalars(
                select(AtlasProjectRow).order_by(AtlasProjectRow.project_id)
            ).all()
            project_scope = {
                ("project", project.project_id)
                for project in project_rows
                if self.resolve_in_session(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    project_id=project.project_id,
                    action=action,
                ).allowed
            }
            return project_scope | {
                ("team", team_id)
                for team_id in self._effective_team_tag_ids(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    system_admin=actor.system_role == "admin",
                )
            }

    def effective_document_scope_labels(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
    ) -> list[tuple[str, str, str]]:
        if action not in ACTION_REQUIRED_ROLE:
            raise ValueError(f"unknown ACL action: {action}")
        with self.session_factory() as session:
            session.connection(
                execution_options={"isolation_level": "REPEATABLE READ"}
            )
            actor = session.get(AtlasUserRow, actor_id)
            if (
                actor is None
                or actor.actor_type != actor_type
                or not actor.active
            ):
                return []
            project_rows = session.scalars(
                select(AtlasProjectRow).order_by(AtlasProjectRow.project_id)
            ).all()
            project_labels = [
                ("project", project.project_id, project.name)
                for project in project_rows
                if self.resolve_in_session(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    project_id=project.project_id,
                    action=action,
                ).allowed
            ]
            team_ids = self._effective_team_tag_ids(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                system_admin=actor.system_role == "admin",
            )
            team_rows = session.scalars(
                select(AtlasTeamRow).where(
                    AtlasTeamRow.team_id.in_(team_ids or {""})
                )
            ).all()
            return sorted(
                [
                    *project_labels,
                    *[
                        ("team", team.team_id, team.name)
                        for team in team_rows
                        if team.team_id in team_ids
                    ],
                ]
            )

    @classmethod
    def resolve_in_session(
        cls,
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
        action: str,
        lock_rows: bool = False,
    ) -> AccessDecisionRecord:
        required_role = ACTION_REQUIRED_ROLE[action]
        if lock_rows:
            actor = session.scalar(
                select(AtlasUserRow)
                .where(AtlasUserRow.actor_id == actor_id)
                .with_for_update()
            )
        else:
            actor = session.get(AtlasUserRow, actor_id)
        if actor is None or actor.actor_type != actor_type or not actor.active:
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=False,
                reason="actor_inactive_or_missing", effective_role=None,
                source_type=None, source_id=None,
                explanation="The actor is inactive or missing.",
            )
        project = (
            session.scalar(
                select(AtlasProjectRow)
                .where(AtlasProjectRow.project_id == project_id)
                .with_for_update()
            )
            if lock_rows
            else session.get(AtlasProjectRow, project_id)
        )
        if project is None:
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=False,
                reason="project_missing", effective_role=None,
                source_type=None, source_id=None,
                explanation="The project was not found.",
            )
        if actor.system_role == "admin":
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=True,
                reason="system_admin", effective_role="admin",
                source_type=actor_type, source_id=actor_id,
                explanation="System admin grants access to this project action.",
            )

        tiers = cls._actor_team_tiers(
            session,
            actor_type=actor_type,
            actor_id=actor_id,
            lock_rows=lock_rows,
        )
        grant_statement = select(AtlasPermissionGrantRow).where(
                AtlasPermissionGrantRow.project_id == project_id,
                AtlasPermissionGrantRow.status == "active",
            ).order_by(AtlasPermissionGrantRow.grant_id)
        if lock_rows:
            grant_statement = grant_statement.with_for_update()
        rows = session.scalars(grant_statement).all()
        candidates_by_tier: dict[int, list[PermissionGrantRecord]] = defaultdict(list)
        for row in rows:
            grant = _grant_record(row)
            if grant.subject_type == actor_type and grant.subject_id == actor_id:
                candidates_by_tier[0].append(grant)
            elif grant.subject_type == "team" and grant.subject_id in tiers:
                candidates_by_tier[tiers[grant.subject_id]].append(grant)
        if not candidates_by_tier:
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=False,
                reason="missing_permission", effective_role=None,
                source_type=None, source_id=None,
                explanation="No active permission grant applies to this actor and project.",
            )
        tier = min(candidates_by_tier)
        candidates = sorted(candidates_by_tier[tier], key=lambda item: item.grant_id)
        covering_denies = [
            grant for grant in candidates
            if grant.effect == "deny" and role_covers(grant.role, required_role)
        ]
        if covering_denies:
            winner = max(
                covering_denies,
                key=lambda item: (ROLE_ORDER.get(item.role, 0), item.grant_id),
            )
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=False,
                reason="deny_grant", effective_role=None,
                source_type=winner.subject_type, source_id=winner.grant_id,
                explanation=f"{cls._subject_label(winner)} denies {winner.role} access.",
            )
        covering_allows = [
            grant for grant in candidates
            if grant.effect == "allow" and role_covers(grant.role, required_role)
        ]
        if covering_allows:
            winner = max(
                covering_allows,
                key=lambda item: (ROLE_ORDER.get(item.role, 0), item.grant_id),
            )
            return _access_decision(
                actor_type=actor_type, actor_id=actor_id, project_id=project_id,
                action=action, required_role=required_role, allowed=True,
                reason="allow_grant",
                effective_role=highest_role([item.role for item in covering_allows]),
                source_type=winner.subject_type, source_id=winner.grant_id,
                explanation=f"{cls._subject_label(winner)} grants {winner.role} access.",
            )
        strongest = max(
            candidates,
            key=lambda item: (ROLE_ORDER.get(item.role, 0), item.grant_id),
        )
        return _access_decision(
            actor_type=actor_type, actor_id=actor_id, project_id=project_id,
            action=action, required_role=required_role, allowed=False,
            reason="missing_required_role",
            effective_role=highest_role([
                item.role for item in candidates if item.effect == "allow"
            ]),
            source_type=strongest.subject_type, source_id=strongest.grant_id,
            explanation=(
                "The most specific grant does not include the required "
                f"{required_role} role."
            ),
        )

    @staticmethod
    def _actor_team_tiers(
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        lock_rows: bool = False,
    ) -> dict[str, int]:
        membership_statement = select(AtlasTeamMembershipRow).where(
            AtlasTeamMembershipRow.member_actor_type == actor_type,
            AtlasTeamMembershipRow.member_actor_id == actor_id,
            AtlasTeamMembershipRow.status == "active",
        ).order_by(AtlasTeamMembershipRow.membership_id)
        if lock_rows:
            membership_statement = membership_statement.with_for_update()
        memberships = session.scalars(membership_statement).all()
        tiers: dict[str, int] = {}
        for membership in memberships:
            distance = 0
            seen: set[str] = set()
            current_id: str | None = membership.team_id
            while current_id and current_id not in seen:
                seen.add(current_id)
                team = (
                    session.scalar(
                        select(AtlasTeamRow)
                        .where(AtlasTeamRow.team_id == current_id)
                        .with_for_update()
                    )
                    if lock_rows
                    else session.get(AtlasTeamRow, current_id)
                )
                if team is None or team.status != "active":
                    break
                tier = 1 + distance
                tiers[current_id] = min(tiers.get(current_id, tier), tier)
                current_id = team.parent_team_id
                distance += 1
        return tiers

    @staticmethod
    def _actor_team_tiers_with_validity(
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        lock_rows: bool = False,
    ) -> tuple[dict[str, int], bool]:
        membership_statement = select(AtlasTeamMembershipRow).where(
            AtlasTeamMembershipRow.member_actor_type == actor_type,
            AtlasTeamMembershipRow.member_actor_id == actor_id,
            AtlasTeamMembershipRow.status == "active",
        ).order_by(AtlasTeamMembershipRow.membership_id)
        if lock_rows:
            membership_statement = membership_statement.with_for_update()
        memberships = session.scalars(membership_statement).all()
        tiers: dict[str, int] = {}
        invalid_hierarchy = False
        for membership in memberships:
            if membership.role not in {"member", "uploader", "admin"}:
                invalid_hierarchy = True
                continue
            distance = 0
            seen: set[str] = set()
            path_tiers: dict[str, int] = {}
            current_id: str | None = membership.team_id
            valid_path = True
            while current_id:
                if current_id in seen or distance >= MAX_TEAM_DEPTH:
                    valid_path = False
                    break
                seen.add(current_id)
                team = (
                    session.scalar(
                        select(AtlasTeamRow)
                        .where(AtlasTeamRow.team_id == current_id)
                        .with_for_update()
                    )
                    if lock_rows
                    else session.get(AtlasTeamRow, current_id)
                )
                if team is None or team.status != "active":
                    valid_path = False
                    break
                path_tiers[current_id] = 1 + distance
                current_id = team.parent_team_id
                distance += 1
            if not valid_path:
                invalid_hierarchy = True
                continue
            for team_id, tier in path_tiers.items():
                tiers[team_id] = min(tiers.get(team_id, tier), tier)
        return tiers, invalid_hierarchy

    @classmethod
    def _effective_team_tag_ids(
        cls,
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        system_admin: bool,
    ) -> set[str]:
        if system_admin:
            return set(session.scalars(
                select(AtlasTeamRow.team_id).where(AtlasTeamRow.status == "active")
            ).all())
        memberships = session.scalars(
            select(AtlasTeamMembershipRow).where(
                AtlasTeamMembershipRow.member_actor_type == actor_type,
                AtlasTeamMembershipRow.member_actor_id == actor_id,
                AtlasTeamMembershipRow.status == "active",
            )
        ).all()
        team_ids: set[str] = set()
        for membership in memberships:
            seen: set[str] = set()
            current_id: str | None = membership.team_id
            while current_id and current_id not in seen:
                seen.add(current_id)
                team = session.get(AtlasTeamRow, current_id)
                if team is None or team.status != "active":
                    break
                team_ids.add(current_id)
                if not team.inherit_parent_documents:
                    break
                current_id = team.parent_team_id
        return team_ids

    @staticmethod
    def _subject_label(grant: PermissionGrantRecord) -> str:
        if grant.subject_type == "team":
            return f"Team {grant.subject_id}"
        return f"Actor {grant.subject_id}"

@dataclass(frozen=True, slots=True)
class PostgresNotesMembershipAuthority:
    """Project/Team Notes projection over the existing current authorities."""

    session_factory: SessionFactory

    @staticmethod
    def _actor_team_notes_memberships_with_validity(
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        lock_rows: bool = False,
    ) -> tuple[set[str], bool]:
        membership_statement = select(AtlasTeamMembershipRow).where(
            AtlasTeamMembershipRow.member_actor_type == actor_type,
            AtlasTeamMembershipRow.member_actor_id == actor_id,
            AtlasTeamMembershipRow.status == "active",
        ).order_by(AtlasTeamMembershipRow.membership_id)
        if lock_rows:
            membership_statement = membership_statement.with_for_update()
        memberships = session.scalars(membership_statement).all()
        team_ids: set[str] = set()
        invalid_hierarchy = False
        for membership in memberships:
            if membership.role not in {"member", "uploader", "admin"}:
                invalid_hierarchy = True
                continue
            seen: set[str] = set()
            path_team_ids: set[str] = set()
            current_id: str | None = membership.team_id
            distance = 0
            valid_path = True
            while current_id:
                if current_id in seen or distance >= MAX_TEAM_DEPTH:
                    valid_path = False
                    break
                seen.add(current_id)
                team = (
                    session.scalar(
                        select(AtlasTeamRow)
                        .where(AtlasTeamRow.team_id == current_id)
                        .with_for_update()
                    )
                    if lock_rows
                    else session.get(AtlasTeamRow, current_id)
                )
                if team is None or team.status != "active":
                    valid_path = False
                    break
                path_team_ids.add(current_id)
                if not team.inherit_parent_documents:
                    break
                current_id = team.parent_team_id
                distance += 1
            if not valid_path:
                invalid_hierarchy = True
                continue
            team_ids.update(path_team_ids)
        return team_ids, invalid_hierarchy


    def current_project_notes_membership(
        self,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
    ) -> CurrentProjectNotesMembershipSnapshot:
        if actor_type != "user":
            return CurrentProjectNotesMembershipSnapshot(
                actor_id=actor_id,
                project_id=project_id,
                member=False,
                system_admin=False,
                reason="actor_not_human",
            )
        with self.session_factory() as session:
            decision = ActionAwareAclAuthority.resolve_in_session(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                project_id=project_id,
                action="notes_membership",
            )
            _tiers, invalid_hierarchy = (
                ActionAwareAclAuthority._actor_team_tiers_with_validity(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
            )
        reason = decision.reason
        if decision.allowed:
            reason = "system_admin" if decision.reason == "system_admin" else "member"
        elif reason == "missing_permission" and invalid_hierarchy:
            reason = "invalid_hierarchy"
        elif reason not in {
            "actor_inactive_or_missing",
            "project_missing",
            "missing_permission",
            "deny_grant",
        }:
            reason = "missing_permission"
        return CurrentProjectNotesMembershipSnapshot(
            actor_id=actor_id,
            project_id=project_id,
            member=decision.allowed,
            system_admin=decision.reason == "system_admin",
            reason=reason,
        )

    def current_team_notes_membership(
        self,
        *,
        actor_type: str,
        actor_id: str,
        team_id: str,
    ) -> CurrentTeamNotesMembershipSnapshot:
        if actor_type != "user":
            return CurrentTeamNotesMembershipSnapshot(
                actor_id=actor_id,
                team_id=team_id,
                member=False,
                system_admin=False,
                reason="actor_not_human",
            )
        with self.session_factory() as session:
            actor = session.get(AtlasUserRow, actor_id)
            if actor is None or actor.actor_type != "user" or not actor.active:
                return CurrentTeamNotesMembershipSnapshot(
                    actor_id=actor_id,
                    team_id=team_id,
                    member=False,
                    system_admin=False,
                    reason="actor_inactive_or_missing",
                )
            team = session.get(AtlasTeamRow, team_id)
            if team is None or team.status != "active":
                return CurrentTeamNotesMembershipSnapshot(
                    actor_id=actor_id,
                    team_id=team_id,
                    member=False,
                    system_admin=False,
                    reason="team_missing_or_retired",
                )
            if actor.system_role == "admin":
                return CurrentTeamNotesMembershipSnapshot(
                    actor_id=actor_id,
                    team_id=team_id,
                    member=True,
                    system_admin=True,
                    reason="system_admin",
                )
            team_ids, invalid_hierarchy = (
                self._actor_team_notes_memberships_with_validity(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
            )
            if team_id in team_ids:
                return CurrentTeamNotesMembershipSnapshot(
                    actor_id=actor_id,
                    team_id=team_id,
                    member=True,
                    system_admin=False,
                    reason="member",
                )
            return CurrentTeamNotesMembershipSnapshot(
                actor_id=actor_id,
                team_id=team_id,
                member=False,
                system_admin=False,
                reason=(
                    "invalid_hierarchy"
                    if invalid_hierarchy
                    else "missing_membership"
                ),
            )


class ProjectAuthorizationConflict(RuntimeError):
    pass


class ProjectCurrentnessConflict(RuntimeError):
    pass


__all__ = [
    "ActionAwareAclAuthority",
    "PostgresNotesMembershipAuthority",
    "ProjectAclChangeSet",
    "ProjectAclRepository",
    "ProjectAuthorizationConflict",
    "ProjectCurrentnessConflict",
    "ProjectGrantWriter",
]
