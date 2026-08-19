from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
from typing import NoReturn

from atlas_production.shared.public import (
    AdminActionResult,
)
from .api_models import (
    ScopedDirectoryConnectionListResult,
    ScopedDirectoryMemberImportResult,
    ScopedDirectoryUserSearchRequest,
    ScopedDirectoryUserSearchResult,
    TeamCreateRequest,
    TeamDirectoryMemberImportRequest,
    TeamListResult,
    TeamMemberCandidate,
    TeamMemberCandidatesResult,
    TeamMemberListResult,
    TeamMemberSummary,
    TeamMembershipCreateRequest,
    TeamMembershipRecord as TeamMembershipModel,
    TeamRecord as TeamModel,
    TeamUpdateRequest,
)
from .records import (
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.shared.public import (
    utc_now_iso,
)
from .contracts import IdentityAccessError
from .team_contracts import TeamAccessError, TeamActionOutcome, TeamAuditCommand
from .team_ports import TeamAccessRepository
from .directory_ports import (
    ScopedDirectoryIdentityCapability,
    ScopedDirectoryImportAuthorizationConflict,
    ScopedDirectoryImportChangeSet,
    ScopedDirectoryImportCommitPort,
    ScopedDirectoryImportCurrentnessConflict,
)
from atlas_production.shared.public import AuditEventRecord


class TeamAccessService:
    def __init__(
        self,
        repository: TeamAccessRepository,
        directory_identity: ScopedDirectoryIdentityCapability,
        directory_import_commit: ScopedDirectoryImportCommitPort,
    ) -> None:
        self.repository = repository
        self.directory_identity = directory_identity
        self.directory_import_commit = directory_import_commit

    def _team_mutation_context(
        self,
        team_id: str,
        *,
        actor_ids: tuple[str, ...],
        include_hierarchy: bool = False,
    ):
        try:
            return self.repository.team_mutation(
                team_id,
                actor_ids=actor_ids,
                include_hierarchy=include_hierarchy,
            )
        except TypeError:
            # Module-contract fakes may expose the original one-argument seam.
            return self.repository.team_mutation(team_id)

    @staticmethod
    def _run_mutation(mutation, action) -> TeamActionOutcome:
        outcome: TeamActionOutcome | None = None
        rejection: TeamAccessError | None = None
        with mutation:
            try:
                outcome = action()
            except TeamAccessError as exc:
                rejection = exc
        if rejection is not None:
            raise rejection
        assert outcome is not None
        return outcome

    def list_teams(self, actor: UserRecord | None) -> TeamListResult:
        actor = self._require_actor(actor)
        all_teams = self.repository.list_teams()
        if self.repository.is_system_admin(actor):
            teams = all_teams
        else:
            direct_roles = self.repository.direct_team_roles(actor)
            team_by_id = {team.team_id: team for team in all_teams}
            team_ids = {
                team_id
                for team_id, role in direct_roles.items()
                if role == "admin"
                and (team := team_by_id.get(team_id)) is not None
                and team.status == "active"
            }
            if not team_ids:
                raise TeamAccessError(
                    "access_denied",
                    'team.admin_access_is_required',
                    403,
                )
            teams = [team_by_id[team_id] for team_id in team_ids]
        visible_team_ids = {team.team_id for team in teams}
        return TeamListResult(
            teams=[
                self._team_model(team)
                for team in sorted(
                    teams,
                    key=lambda item: (item.name.casefold(), item.team_id),
                )
            ],
            memberships=[
                self._membership_model(membership)
                for membership in sorted(
                    self.repository.list_memberships(),
                    key=lambda item: item.membership_id,
                )
                if membership.status == "active"
                and membership.team_id in visible_team_ids
            ],
        )

    def list_members(
        self,
        actor: UserRecord | None,
        team_id: str,
    ) -> TeamMemberListResult:
        actor = self._require_actor(actor)
        self._require_active_team(team_id)
        if not self.repository.can_manage_team(actor, team_id):
            raise TeamAccessError(
                "access_denied",
                'team.member_management_requires_team_admin_access',
                403,
            )
        members = [
            self._member_summary(membership)
            for membership in self.repository.list_memberships()
            if membership.team_id == team_id and membership.status == "active"
        ]
        return TeamMemberListResult(
            members=sorted(
                members,
                key=lambda item: (
                    item.display_name.casefold(),
                    item.subject_type,
                    item.subject_id,
                ),
            )
        )

    def list_member_candidates(
        self,
        actor: UserRecord | None,
        team_id: str,
    ) -> TeamMemberCandidatesResult:
        actor = self._require_actor(actor)
        self._require_active_team(team_id)
        if not self.repository.can_manage_team(actor, team_id):
            raise TeamAccessError(
                "access_denied",
                'team.member_management_requires_team_admin_access',
                403,
            )
        active_member_ids = {
            membership.member_actor_id
            for membership in self.repository.list_memberships()
            if membership.team_id == team_id and membership.status == "active"
        }
        users = [
            TeamMemberCandidate(
                subject_id=user.actor_id,
                display_name=user.display_name,
                display_detail=user.email,
            )
            for user in self.repository.list_users()
            if user.actor_type == "user"
            and user.active
            and user.actor_id not in active_member_ids
        ]
        return TeamMemberCandidatesResult(
            users=sorted(
                users,
                key=lambda item: (item.display_name.casefold(), item.subject_id),
            )
        )
    def list_directory_connections(
        self,
        actor: UserRecord | None,
        team_id: str,
    ) -> ScopedDirectoryConnectionListResult:
        self._require_team_manage(actor, team_id)
        try:
            return self.directory_identity.list_scoped_connections()
        except IdentityAccessError as exc:
            self._raise_directory_error(exc)

    def search_directory_users(
        self,
        actor: UserRecord | None,
        team_id: str,
        connection_id: str,
        payload: ScopedDirectoryUserSearchRequest,
    ) -> ScopedDirectoryUserSearchResult:
        self._require_team_manage(actor, team_id)
        try:
            return self.directory_identity.search_scoped_users(
                connection_id,
                payload,
            )
        except IdentityAccessError as exc:
            self._raise_directory_error(exc)

    def import_directory_members(
        self,
        actor: UserRecord | None,
        team_id: str,
        connection_id: str,
        payload: TeamDirectoryMemberImportRequest,
    ) -> ScopedDirectoryMemberImportResult:
        actor = self._require_team_manage(actor, team_id)
        try:
            preparation = self.directory_identity.prepare_scoped_import(
                connection_id,
                payload.external_subjects,
            )
        except IdentityAccessError as exc:
            self._raise_directory_error(exc)
        now = utc_now_iso()
        memberships: list[TeamMembershipRecord] = []
        expected_memberships: list[
            tuple[str, TeamMembershipRecord | None]
        ] = []
        for user in preparation.users:
            membership_id = f"tm-{team_id}-{user.actor_id}"
            existing = self.repository.get_membership(membership_id)
            expected_memberships.append(
                (membership_id, replace(existing) if existing else None)
            )
            if existing and existing.status == "active":
                membership = replace(existing)
            elif existing:
                membership = replace(
                    existing,
                    role=payload.role,
                    status="active",
                    removed_at=None,
                )
            else:
                membership = TeamMembershipRecord(
                    membership_id=membership_id,
                    team_id=team_id,
                    member_actor_type="user",
                    member_actor_id=user.actor_id,
                    role=payload.role,
                    status="active",
                    created_at=now,
                )
            memberships.append(membership)

        actor_ids = list(preparation.actor_ids)
        count = len(actor_ids)
        audits = (
            self._directory_import_audit(
                event_type="directory_users_scoped_imported",
                actor_id=actor.actor_id,
                target_ref=f"directory-connection:{connection_id}",
                scope_type="team",
                scope_id=team_id,
                message_code="directory.users_scoped_imported",
                metadata={
                    "connection_id": connection_id,
                    "actor_ids": actor_ids,
                    "count": count,
                    "status": "imported",
                },
                count=count,
            ),
            self._directory_import_audit(
                event_type="team_directory_members_imported",
                actor_id=actor.actor_id,
                target_ref=f"team:{team_id}",
                scope_type="team",
                scope_id=team_id,
                message_code="team.directory_members_imported",
                metadata={
                    "connection_id": connection_id,
                    "team_id": team_id,
                    "role": payload.role,
                    "actor_ids": actor_ids,
                    "count": count,
                    "status": "imported",
                },
                count=count,
            ),
        )
        try:
            self.directory_import_commit.commit_scoped_directory_import(
                ScopedDirectoryImportChangeSet(
                    authorization_actor_id=actor.actor_id,
                    authorization_scope_type="team",
                    authorization_scope_id=team_id,
                    preparation=preparation,
                    team_memberships=tuple(memberships),
                    expected_team_memberships=tuple(expected_memberships),
                    audit_events=audits,
                )
            )
        except ScopedDirectoryImportAuthorizationConflict as exc:
            raise TeamAccessError(
                "access_denied",
                "team.member_management_requires_team_admin_access",
                403,
                exc.audit_event_ref,
            ) from exc
        except ScopedDirectoryImportCurrentnessConflict as exc:
            raise TeamAccessError(
                "team_directory_import_conflict",
                "directory.concurrent_change",
                409,
            ) from exc
        return ScopedDirectoryMemberImportResult(
            actor_ids=actor_ids,
            applied_count=count,
            message_code="team.directory_members_imported",
            message_params={"count": count},
        )


    def create_team(
        self,
        actor: UserRecord | None,
        payload: TeamCreateRequest,
    ) -> TeamActionOutcome:
        return self._run_mutation(
            self._team_mutation_context(
                payload.team_id,
                actor_ids=(actor.actor_id,) if actor else (),
                include_hierarchy=True,
            ),
            lambda: self._create_team_locked(actor, payload),
        )

    def _create_team_locked(
        self,
        actor: UserRecord | None,
        payload: TeamCreateRequest,
    ) -> TeamActionOutcome:
        actor = self._require_system_admin(actor)
        if self.repository.get_team(payload.team_id):
            self._reject(
                "team.already_exists",
                "audit-team-create-rejected",
                409,
            )
        if payload.parent_team_id and not self.repository.get_team(payload.parent_team_id):
            self._reject(
                "team.parent_was_not_found",
                "audit-team-create-rejected",
                404,
            )
        if self.repository.would_exceed_depth(payload.team_id, payload.parent_team_id):
            self._reject(
                "team.depth_limit_exceeded",
                "audit-team-create-rejected",
                422,
            )
        team = TeamRecord(
            team_id=payload.team_id,
            name=payload.name,
            parent_team_id=payload.parent_team_id,
            status="active",
            created_at=utc_now_iso(),
            inherit_parent_documents=payload.inherit_parent_documents,
        )
        self.repository.put_team(team)
        audit = self.repository.append_audit(
            TeamAuditCommand(
                event_type="team_created",
                actor_id=actor.actor_id,
                target_ref=f"team:{payload.team_id}",
                message_code='team.is_ready',
                metadata={
                    "parent_team_id": payload.parent_team_id,
                    "inherit_parent_documents": payload.inherit_parent_documents,
                },
            )
        )
        return TeamActionOutcome(
            result=AdminActionResult(
                request_id=payload.idempotency_key,
                status="applied",
                target_ref=f"team:{payload.team_id}",
                message_code='team.is_ready',
                audit_event_ref=audit.event_id,
            ),
            success_status_code=201,
        )

    def update_team(
        self,
        actor: UserRecord | None,
        team_id: str,
        payload: TeamUpdateRequest,
    ) -> TeamActionOutcome:
        submitted_fields = payload.model_fields_set - {"idempotency_key"}
        is_system_admin = bool(
            actor is not None and self.repository.is_system_admin(actor)
        )
        return self._run_mutation(
            self._team_mutation_context(
                team_id,
                actor_ids=(actor.actor_id,) if actor else (),
                include_hierarchy=is_system_admin
                or bool(
                    submitted_fields
                    & {"parent_team_id", "status", "inherit_parent_documents"}
                ),
            ),
            lambda: self._update_team_locked(actor, team_id, payload),
        )

    def _update_team_locked(
        self,
        actor: UserRecord | None,
        team_id: str,
        payload: TeamUpdateRequest,
    ) -> TeamActionOutcome:
        actor = self._require_actor(actor)
        submitted_fields = payload.model_fields_set - {"idempotency_key"}
        is_system_admin = self.repository.is_system_admin(actor)
        if not is_system_admin:
            if submitted_fields != {"name"}:
                raise TeamAccessError(
                    "access_denied",
                    'permission.admin_permission_is_required',
                    403,
                )
            team = self.repository.get_team(team_id)
            if (
                team is None
                or team.status != "active"
                or not self.repository.can_manage_team(actor, team_id)
            ):
                raise TeamAccessError(
                    "access_denied",
                    'team.admin_access_is_required',
                    403,
                )
        else:
            team = self.repository.get_team(team_id)
            if team is None:
                self._reject(
                    'team.was_not_found',
                    "audit-team-update-rejected",
                    404,
                )
        assert team is not None
        if is_system_admin and (
            team.status == "retired"
            and submitted_fields
            & {"parent_team_id", "inherit_parent_documents"}
        ):
            self._reject(
                "team.was_not_found_or_is_retired",
                "audit-team-update-rejected",
                409,
            )
        parent_provided = "parent_team_id" in payload.model_fields_set
        next_parent = payload.parent_team_id if parent_provided else team.parent_team_id
        if next_parent and not self.repository.get_team(next_parent):
            self._reject(
                "team.parent_was_not_found",
                "audit-team-update-rejected",
                404,
            )
        if self.repository.would_create_cycle(team_id, next_parent):
            self._reject(
                "team.parent_would_create_cycle",
                "audit-team-update-rejected",
                422,
            )
        if self.repository.would_exceed_depth(team_id, next_parent):
            self._reject(
                "team.depth_limit_exceeded",
                "audit-team-update-rejected",
                422,
            )
        if payload.name is not None:
            team.name = payload.name
        if parent_provided:
            team.parent_team_id = payload.parent_team_id
        if payload.status is not None:
            team.status = payload.status
        if payload.inherit_parent_documents is not None:
            team.inherit_parent_documents = payload.inherit_parent_documents
        self.repository.put_team(team)
        audit = self.repository.append_audit(
            TeamAuditCommand(
                event_type="team_updated",
                actor_id=actor.actor_id,
                target_ref=f"team:{team_id}",
                message_code='team.is_updated',
                metadata={
                    "parent_team_id": team.parent_team_id,
                    "status": team.status,
                    "inherit_parent_documents": team.inherit_parent_documents,
                },
            )
        )
        return TeamActionOutcome(
            result=AdminActionResult(
                request_id=payload.idempotency_key,
                status="applied",
                target_ref=f"team:{team_id}",
                message_code='team.is_updated',
                audit_event_ref=audit.event_id,
            ),
            success_status_code=200,
        )

    def add_member(
        self,
        actor: UserRecord | None,
        team_id: str,
        payload: TeamMembershipCreateRequest,
    ) -> TeamActionOutcome:
        actor = self._require_actor(actor)
        return self._run_mutation(
            self._team_mutation_context(
                team_id,
                actor_ids=tuple(
                    actor_id
                    for actor_id in (
                        actor.actor_id if actor else None,
                        payload.member_actor_id,
                    )
                    if actor_id
                ),
            ),
            lambda: self._add_member_locked(actor, team_id, payload),
        )

    def remove_member(
        self,
        actor: UserRecord | None,
        team_id: str,
        membership_id: str,
    ) -> TeamActionOutcome:
        actor = self._require_actor(actor)
        return self._run_mutation(
            self._team_mutation_context(
                team_id,
                actor_ids=(actor.actor_id,) if actor else (),
            ),
            lambda: self._remove_member_locked(actor, team_id, membership_id),
        )

    def _add_member_locked(
        self,
        actor: UserRecord,
        team_id: str,
        payload: TeamMembershipCreateRequest,
    ) -> TeamActionOutcome:
        team = self.repository.get_team(team_id)
        target = self.repository.get_user(payload.member_actor_id)
        if not team or team.status != "active":
            self._reject(
                "team.was_not_found_or_is_retired",
                "audit-team-member-rejected",
                404,
            )
        if not target or target.actor_type != payload.member_actor_type:
            self._reject(
                'team.member_actor_was_not_found',
                "audit-team-member-rejected",
                404,
            )
        assert target is not None
        if not self.repository.can_manage_team(actor, team_id):
            audit = self._append_team_denial(
                "team_member_denied",
                actor,
                f"team:{team_id}",
                team_id,
                'team.member_management_requires_team_admin_access',
                "missing_team_admin",
            )
            raise TeamAccessError(
                "access_denied",
                'team.member_management_requires_team_admin_access',
                403,
                audit.event_id,
            )
        scoped_team_admin = not self.repository.is_system_admin(actor)
        if scoped_team_admin and payload.member_actor_type == "service_account":
            audit = self._append_team_denial(
                "team_member_denied",
                actor,
                f"team:{team_id}",
                team_id,
                'team.service_accounts_are_read_only_for_team_admins',
                "service_account_immutable",
            )
            raise TeamAccessError(
                "access_denied",
                'team.service_accounts_are_read_only_for_team_admins',
                403,
                audit.event_id,
            )
        if scoped_team_admin and (
            payload.member_actor_type != "user" or not target.active
        ):
            raise TeamAccessError(
                "not_found",
                'team.member_actor_was_not_found',
                404,
            )
        membership_id = f"tm-{team_id}-{payload.member_actor_id}"
        existing = self.repository.get_membership(membership_id)
        if (
            scoped_team_admin
            and existing
            and existing.status == "active"
            and existing.member_actor_type == "user"
            and existing.member_actor_id == actor.actor_id
            and existing.role == "admin"
            and payload.role != "admin"
            and self.repository.active_direct_human_admin_count(team_id) <= 1
        ):
            audit = self._append_team_denial(
                "team_admin_lockout_prevented",
                actor,
                f"team-membership:{membership_id}",
                team_id,
                'team.keep_at_least_one_direct_team_admin',
                "last_direct_team_admin",
            )
            raise TeamAccessError(
                "team_admin_required",
                'team.keep_at_least_one_direct_team_admin',
                422,
                audit.event_id,
            )
        if existing and existing.status == "active":
            existing.role = payload.role
            self.repository.put_membership(existing)
            audit = self.repository.append_audit(
                TeamAuditCommand(
                    event_type="team_member_role_updated",
                    actor_id=actor.actor_id,
                    target_ref=f"team-membership:{membership_id}",
                    scope_type="team",
                    scope_id=team_id,
                    message_code='team.member_role_is_updated',
                    metadata=self._membership_metadata(team_id, payload),
                )
            )
            return TeamActionOutcome(
                result=AdminActionResult(
                    request_id=payload.idempotency_key,
                    status="applied",
                    target_ref=f"team-membership:{membership_id}",
                    message_code='team.member_role_is_updated',
                    audit_event_ref=audit.event_id,
                ),
                success_status_code=200,
            )
        if existing:
            existing.status = "active"
            existing.role = payload.role
            existing.removed_at = None
            membership = existing
        else:
            membership = TeamMembershipRecord(
                membership_id=membership_id,
                team_id=team_id,
                member_actor_type=payload.member_actor_type,
                member_actor_id=payload.member_actor_id,
                status="active",
                created_at=utc_now_iso(),
                role=payload.role,
            )
        self.repository.put_membership(membership)
        audit = self.repository.append_audit(
            TeamAuditCommand(
                event_type="team_member_added",
                actor_id=actor.actor_id,
                target_ref=f"team-membership:{membership_id}",
                message_code='team.member_is_active',
                metadata=self._membership_metadata(team_id, payload),
            )
        )
        return TeamActionOutcome(
            result=AdminActionResult(
                request_id=payload.idempotency_key,
                status="applied",
                target_ref=f"team-membership:{membership_id}",
                message_code='team.member_is_active',
                audit_event_ref=audit.event_id,
            ),
            success_status_code=201,
        )

    def _remove_member_locked(
        self,
        actor: UserRecord,
        team_id: str,
        membership_id: str,
    ) -> TeamActionOutcome:
        self._require_active_team(team_id)
        membership = self.repository.get_membership(membership_id)
        if not membership or membership.team_id != team_id:
            self._reject(
                "team.membership_was_not_found",
                "audit-team-member-remove-rejected",
                404,
            )
        assert membership is not None
        if not self.repository.can_manage_team(actor, team_id):
            audit = self._append_team_denial(
                "team_member_remove_denied",
                actor,
                f"team-membership:{membership_id}",
                team_id,
                'team.member_management_requires_team_admin_access',
                "missing_team_admin",
            )
            raise TeamAccessError(
                "access_denied",
                'team.member_management_requires_team_admin_access',
                403,
                audit.event_id,
            )
        scoped_team_admin = not self.repository.is_system_admin(actor)
        if scoped_team_admin and membership.member_actor_type == "service_account":
            audit = self._append_team_denial(
                "team_member_remove_denied",
                actor,
                f"team-membership:{membership_id}",
                team_id,
                'team.service_accounts_are_read_only_for_team_admins',
                "service_account_immutable",
            )
            raise TeamAccessError(
                "access_denied",
                'team.service_accounts_are_read_only_for_team_admins',
                403,
                audit.event_id,
            )
        if (
            scoped_team_admin
            and membership.status == "active"
            and membership.member_actor_type == "user"
            and membership.member_actor_id == actor.actor_id
            and membership.role == "admin"
            and self.repository.active_direct_human_admin_count(team_id) <= 1
        ):
            audit = self._append_team_denial(
                "team_admin_lockout_prevented",
                actor,
                f"team-membership:{membership_id}",
                team_id,
                'team.keep_at_least_one_direct_team_admin',
                "last_direct_team_admin",
            )
            raise TeamAccessError(
                "team_admin_required",
                'team.keep_at_least_one_direct_team_admin',
                422,
                audit.event_id,
            )
        membership.status = "removed"
        membership.removed_at = utc_now_iso()
        self.repository.put_membership(membership)
        audit = self.repository.append_audit(
            TeamAuditCommand(
                event_type="team_member_removed",
                actor_id=actor.actor_id,
                target_ref=f"team-membership:{membership_id}",
                message_code='team.member_has_been_removed',
                metadata={
                    "team_id": team_id,
                    "member_actor_id": membership.member_actor_id,
                },
            )
        )
        return TeamActionOutcome(
            result=AdminActionResult(
                request_id=f"remove-{membership_id}",
                status="applied",
                target_ref=f"team-membership:{membership_id}",
                message_code='team.member_has_been_removed',
                audit_event_ref=audit.event_id,
            ),
            success_status_code=200,
        )

    def _append_team_denial(
        self,
        event_type: str,
        actor: UserRecord,
        target_ref: str,
        team_id: str,
        message_code: str,
        reason: str,
    ):
        return self.repository.append_audit(
            TeamAuditCommand(
                event_type=event_type,
                actor_id=actor.actor_id,
                target_ref=target_ref,
                scope_type="team",
                scope_id=team_id,
                message_code=message_code,
                metadata={"reason": reason},
            )
        )

    def _require_active_team(self, team_id: str) -> TeamRecord:
        team = self.repository.get_team(team_id)
        if not team or team.status != "active":
            raise TeamAccessError("not_found", 'team.was_not_found', 404)
        return team
    def _require_team_manage(
        self,
        actor: UserRecord | None,
        team_id: str,
    ) -> UserRecord:
        actor = self._require_actor(actor)
        self._require_active_team(team_id)
        if not self.repository.can_manage_team(actor, team_id):
            raise TeamAccessError(
                "access_denied",
                "team.member_management_requires_team_admin_access",
                403,
            )
        return actor

    @staticmethod
    def _directory_import_audit(
        *,
        event_type: str,
        actor_id: str,
        target_ref: str,
        scope_type: str,
        scope_id: str,
        message_code: str,
        metadata: dict[str, object],
        count: int,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            event_id=f"audit-{uuid4().hex}",
            event_type=event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=None,
            message_code=message_code,
            message_params={"count": count},
            metadata=metadata,
            created_at=utc_now_iso(),
            scope_type=scope_type,
            scope_id=scope_id,
        )


    @staticmethod
    def _raise_directory_error(error: IdentityAccessError) -> NoReturn:
        raise TeamAccessError(
            error.error_code,
            error.message_code,
            error.status_code,
            error.audit_event_ref,
        ) from error

    @staticmethod
    def _team_model(team: TeamRecord) -> TeamModel:
        return TeamModel(
            team_id=team.team_id,
            name=team.name,
            parent_team_id=team.parent_team_id,
            status=team.status,
            created_at=team.created_at,
            inherit_parent_documents=team.inherit_parent_documents,
        )

    @staticmethod
    def _membership_model(membership: TeamMembershipRecord) -> TeamMembershipModel:
        return TeamMembershipModel(
            membership_id=membership.membership_id,
            team_id=membership.team_id,
            member_actor_type=membership.member_actor_type,
            member_actor_id=membership.member_actor_id,
            role=membership.role,
            status=membership.status,
            created_at=membership.created_at,
            removed_at=membership.removed_at,
        )

    def _member_summary(self, membership: TeamMembershipRecord) -> TeamMemberSummary:
        subject = self.repository.get_user(membership.member_actor_id)
        display_name = subject.display_name if subject else (
            "Service account"
            if membership.member_actor_type == "service_account"
            else "Team member"
        )
        return TeamMemberSummary(
            membership_id=membership.membership_id,
            team_id=membership.team_id,
            subject_type=membership.member_actor_type,
            subject_id=membership.member_actor_id,
            display_name=display_name,
            display_detail=(
                subject.email
                if subject and membership.member_actor_type == "user"
                else None
            ),
            role=membership.role,
            status="active",
            created_at=membership.created_at,
        )

    @staticmethod
    def _membership_metadata(
        team_id: str,
        payload: TeamMembershipCreateRequest,
    ) -> dict[str, object]:
        return {
            "team_id": team_id,
            "member_actor_type": payload.member_actor_type,
            "member_actor_id": payload.member_actor_id,
            "role": payload.role,
        }

    @staticmethod
    def _require_actor(actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise TeamAccessError(
                "unauthenticated",
                'auth.please_sign_in_before_using_admin_tools',
                401,
            )
        return actor

    @classmethod
    def _require_system_admin(cls, actor: UserRecord | None) -> UserRecord:
        actor = cls._require_actor(actor)
        if actor.system_role != "admin":
            raise TeamAccessError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            )
        return actor

    @staticmethod
    def _reject(message: str, audit_event_ref: str, status_code: int) -> None:
        raise TeamAccessError(
            "admin_action_rejected",
            message,
            status_code,
            audit_event_ref,
        )
