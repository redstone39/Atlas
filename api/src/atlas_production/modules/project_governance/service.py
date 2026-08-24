from __future__ import annotations
from dataclasses import replace
from typing import Literal, NoReturn
from uuid import uuid4

from atlas_production.shared.public import (
    AdminActionResult,
)
from .api_models import (
    ProjectAccessGrant,
    ProjectAccessGrantCreateRequest,
    ProjectAccessGrantListResult,
    ProjectAccessGrantUpdateRequest,
    ProjectAdminListResult,
    ProjectAdminSummary,
    ProjectCreateRequest,
    ProjectDirectoryMemberImportRequest,
    ProjectMemberCandidate,
    ProjectMemberCandidatesResult,
    ProjectUpdateRequest,
)
from .records import ProjectRecord
from atlas_production.modules.identity_access.public import (
    IdentityAccessError,
    ScopedDirectoryConnectionListResult,
    ScopedDirectoryIdentityCapability,
    ScopedDirectoryImportAuthorizationConflict,
    ScopedDirectoryImportChangeSet,
    ScopedDirectoryImportCommitPort,
    ScopedDirectoryImportCurrentnessConflict,
    ScopedDirectoryMemberImportResult,
    ScopedDirectoryUserSearchRequest,
    ScopedDirectoryUserSearchResult,
)
from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    UserRecord,
)
from atlas_production.shared.public import (
    AuditEventRecord,
    utc_now_iso,
)
from .contracts import (
    ProjectAccessGrantOutcome,
    ProjectActionOutcome,
    ProjectAuditCommand,
    ProjectGovernanceError,
)
from .ports import ProjectGovernanceRepository


PROJECT_ACCESS_SUBJECT_TYPES = frozenset({"user", "team", "service_account"})
PROJECT_ACCESS_ROLES = frozenset({"viewer", "contributor", "admin"})
PROJECT_ACCESS_EFFECTS = frozenset({"allow", "deny"})
PROJECT_ACCESS_STATUSES = frozenset({"active", "revoked"})


class ProjectGovernanceService:
    def __init__(
        self,
        repository: ProjectGovernanceRepository,
        directory_identity: ScopedDirectoryIdentityCapability,
        directory_import_commit: ScopedDirectoryImportCommitPort,
    ) -> None:
        self.repository = repository
        self.directory_identity = directory_identity
        self.directory_import_commit = directory_import_commit

    def list_projects(
        self,
        actor: UserRecord | None,
    ) -> ProjectAdminListResult:
        actor = self._require_project_actor(actor)
        projects = self.repository.list_projects()
        if not self.repository.is_system_admin(actor):
            projects = [
                project
                for project in projects
                if self.repository.resolve_access(
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    project_id=project.project_id,
                    action="permission_manage",
                    persist=False,
                ).allowed
            ]
            if not projects:
                raise ProjectGovernanceError(
                    "access_denied",
                    'project.management_requires_project_admin_access',
                    403,
                )
        return ProjectAdminListResult(
            projects=[
                ProjectAdminSummary(
                    project_id=project.project_id,
                    name=project.name,
                    policy_profile_id=project.policy_profile_id,
                    status=project.status,
                )
                for project in sorted(projects, key=lambda item: item.project_id)
            ]
        )

    def list_access_grants(
        self,
        actor: UserRecord | None,
        project_id: str,
    ) -> ProjectAccessGrantListResult:
        self._require_project_manage(actor, project_id)
        grants = [
            grant
            for grant in sorted(
                self.repository.list_grants(),
                key=lambda item: item.grant_id,
            )
            if self._is_project_access_grant(grant, project_id)
        ]
        return ProjectAccessGrantListResult(
            grants=[self._access_grant(grant) for grant in grants],
            subjects=[
                subject
                for grant in grants
                if (subject := self._access_subject(grant)) is not None
            ],
        )

    def list_member_candidates(
        self,
        actor: UserRecord | None,
        project_id: str,
    ) -> ProjectMemberCandidatesResult:
        self._require_project_manage(actor, project_id)
        actors = sorted(
            self.repository.list_users(),
            key=lambda item: item.display_name.lower(),
        )
        active_subjects = {
            (grant.subject_type, grant.subject_id)
            for grant in self.repository.list_grants()
            if self._is_project_access_grant(grant, project_id)
            and grant.status == "active"
        }
        return ProjectMemberCandidatesResult(
            users=[
                ProjectMemberCandidate(
                    subject_type="user",
                    subject_id=user.actor_id,
                    display_name=user.display_name,
                    display_detail=user.email,
                )
                for user in actors
                if user.actor_type == "user"
                and user.active
                and ("user", user.actor_id) not in active_subjects
            ],
            teams=[
                ProjectMemberCandidate(
                    subject_type="team",
                    subject_id=team.team_id,
                    display_name=team.name,
                    display_detail=None,
                )
                for team in sorted(
                    self.repository.list_teams(),
                    key=lambda item: item.name.lower(),
                )
                if team.status == "active"
                and ("team", team.team_id) not in active_subjects
            ],
            service_accounts=[
                ProjectMemberCandidate(
                    subject_type="service_account",
                    subject_id=service_account.actor_id,
                    display_name=service_account.display_name,
                    display_detail=service_account.email,
                )
                for service_account in actors
                if service_account.actor_type == "service_account"
                and service_account.active
                and (
                    "service_account",
                    service_account.actor_id,
                )
                not in active_subjects
            ],
        )
    def list_directory_connections(
        self,
        actor: UserRecord | None,
        project_id: str,
    ) -> ScopedDirectoryConnectionListResult:
        self._require_project_manage(actor, project_id)
        try:
            return self.directory_identity.list_scoped_connections()
        except IdentityAccessError as exc:
            self._raise_directory_error(exc)

    def search_directory_users(
        self,
        actor: UserRecord | None,
        project_id: str,
        connection_id: str,
        payload: ScopedDirectoryUserSearchRequest,
    ) -> ScopedDirectoryUserSearchResult:
        self._require_project_manage(actor, project_id)
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
        project_id: str,
        connection_id: str,
        payload: ProjectDirectoryMemberImportRequest,
    ) -> ScopedDirectoryMemberImportResult:
        actor = self._require_project_manage(actor, project_id)
        try:
            preparation = self.directory_identity.prepare_scoped_import(
                connection_id,
                payload.external_subjects,
            )
        except IdentityAccessError as exc:
            self._raise_directory_error(exc)
        now = utc_now_iso()
        grants: list[PermissionGrantRecord] = []
        expected_grants: list[
            tuple[str, PermissionGrantRecord | None]
        ] = []
        for user in preparation.users:
            grant_id = self.project_access_grant_id(
                project_id,
                "user",
                user.actor_id,
            )
            existing = self.repository.get_grant(grant_id)
            expected_grants.append((grant_id, replace(existing) if existing else None))
            if existing and existing.status == "active" and existing.effect == "deny":
                raise ProjectGovernanceError(
                    "project_directory_import_conflict",
                    "directory.import_conflict",
                    409,
                )
            if existing and existing.status == "active":
                grant = replace(existing)
            elif existing:
                grant = replace(
                    existing,
                    role=payload.role,
                    effect="allow",
                    status="active",
                    revoked_at=None,
                )
            else:
                grant = PermissionGrantRecord(
                    grant_id=grant_id,
                    project_id=project_id,
                    subject_type="user",
                    subject_id=user.actor_id,
                    role=payload.role,
                    effect="allow",
                    status="active",
                    created_at=now,
                )
            grants.append(grant)

        actor_ids = list(preparation.actor_ids)
        count = len(actor_ids)
        audits = (
            self._directory_import_audit(
                event_type="directory_users_scoped_imported",
                actor_id=actor.actor_id,
                target_ref=f"directory-connection:{connection_id}",
                project_id=project_id,
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
                event_type="project_directory_members_imported",
                actor_id=actor.actor_id,
                target_ref=f"project:{project_id}",
                project_id=project_id,
                message_code="project.directory_members_imported",
                metadata={
                    "connection_id": connection_id,
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
                    authorization_scope_type="project",
                    authorization_scope_id=project_id,
                    preparation=preparation,
                    project_grants=tuple(grants),
                    expected_project_grants=tuple(expected_grants),
                    audit_events=audits,
                )
            )
        except ScopedDirectoryImportAuthorizationConflict as exc:
            raise ProjectGovernanceError(
                "access_denied",
                "project.management_requires_project_admin_access",
                403,
                exc.audit_event_ref,
            ) from exc
        except ScopedDirectoryImportCurrentnessConflict as exc:
            raise ProjectGovernanceError(
                "project_directory_import_conflict",
                "directory.concurrent_change",
                409,
            ) from exc
        return ScopedDirectoryMemberImportResult(
            actor_ids=actor_ids,
            applied_count=count,
            message_code="project.directory_members_imported",
            message_params={"count": count},
        )


    def create_access_grant(
        self,
        actor: UserRecord | None,
        project_id: str,
        payload: ProjectAccessGrantCreateRequest,
    ) -> ProjectAccessGrantOutcome:
        actor = self._require_project_manage(actor, project_id)
        missing = self._missing_access_subject(
            payload.subject_type,
            payload.subject_id,
        )
        if missing:
            self._reject(
                missing,
                "audit-project-member-rejected",
                404,
                request_id=payload.idempotency_key,
            )
        grant = self._find_access_grant(
            project_id,
            payload.subject_type,
            payload.subject_id,
        )
        if grant is None:
            grant = PermissionGrantRecord(
                grant_id=self.project_access_grant_id(
                    project_id,
                    payload.subject_type,
                    payload.subject_id,
                ),
                project_id=project_id,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                role=payload.role,
                effect=payload.effect,
                status="active",
                created_at=utc_now_iso(),
            )
        else:
            grant.role = payload.role
            grant.effect = payload.effect
            grant.status = "active"
            grant.revoked_at = None
        self.repository.put_grant(grant)
        self.repository.append_audit(
            ProjectAuditCommand(
                event_type="project_member_upserted",
                actor_id=actor.actor_id,
                target_ref=f"project_member:{grant.grant_id}",
                project_id=project_id,
                message_code='project.member_is_active',
                metadata={
                    "subject_type": payload.subject_type,
                    "subject_id": payload.subject_id,
                    "role": payload.role,
                    "effect": payload.effect,
                },
            )
        )
        self.repository.persist()
        return ProjectAccessGrantOutcome(self._access_grant(grant), 201)

    def update_access_grant(
        self,
        actor: UserRecord | None,
        project_id: str,
        grant_id: str,
        payload: ProjectAccessGrantUpdateRequest,
    ) -> ProjectAccessGrantOutcome:
        actor = self._require_project_manage(actor, project_id)
        grant = self.repository.get_grant(grant_id)
        if not self._is_project_access_grant(grant, project_id):
            self._reject(
                "project.member_was_not_found",
                "audit-project-member-rejected",
                404,
                request_id=payload.idempotency_key,
            )
        assert grant is not None
        grant.role = payload.role
        grant.effect = payload.effect
        grant.status = "active"
        grant.revoked_at = None
        self.repository.put_grant(grant)
        self.repository.append_audit(
            ProjectAuditCommand(
                event_type="project_member_role_updated",
                actor_id=actor.actor_id,
                target_ref=f"project_member:{grant_id}",
                project_id=project_id,
                message_code='project.member_role_is_updated',
                metadata={
                    "subject_type": grant.subject_type,
                    "subject_id": grant.subject_id,
                    "role": payload.role,
                    "effect": payload.effect,
                },
            )
        )
        self.repository.persist()
        return ProjectAccessGrantOutcome(self._access_grant(grant), 200)

    def revoke_access_grant(
        self,
        actor: UserRecord | None,
        project_id: str,
        grant_id: str,
    ) -> ProjectAccessGrantOutcome:
        actor = self._require_project_manage(actor, project_id)
        grant = self.repository.get_grant(grant_id)
        if not self._is_project_access_grant(grant, project_id):
            self._reject(
                "project.member_was_not_found",
                "audit-project-member-rejected",
                404,
                request_id=f"revoke-{grant_id}",
            )
        assert grant is not None
        grant.status = "revoked"
        grant.revoked_at = utc_now_iso()
        self.repository.put_grant(grant)
        self.repository.append_audit(
            ProjectAuditCommand(
                event_type="project_member_revoked",
                actor_id=actor.actor_id,
                target_ref=f"project_member:{grant_id}",
                project_id=project_id,
                message_code='project.member_has_been_removed',
                metadata={
                    "subject_type": grant.subject_type,
                    "subject_id": grant.subject_id,
                },
            )
        )
        self.repository.persist()
        return ProjectAccessGrantOutcome(self._access_grant(grant), 200)

    def create_project(
        self,
        actor: UserRecord | None,
        payload: ProjectCreateRequest,
    ) -> ProjectActionOutcome:
        actor = self._require_system_admin(actor)
        return self.repository.create_project_once(actor, payload)

    def update_project(
        self,
        actor: UserRecord | None,
        project_id: str,
        payload: ProjectUpdateRequest,
    ) -> ProjectActionOutcome:
        actor = self._require_project_actor(actor)
        submitted_fields = payload.model_fields_set - {"idempotency_key"}
        is_system_admin = self.repository.is_system_admin(actor)
        authorization: Literal["system_admin", "permission_manage"] = "system_admin"
        if not is_system_admin:
            if submitted_fields != {"name"}:
                raise ProjectGovernanceError(
                    "access_denied",
                    'permission.admin_permission_is_required',
                    403,
                )
            project = self.repository.get_project(project_id)
            if project is None or project.status != "active":
                raise ProjectGovernanceError(
                    "access_denied",
                    'project.management_requires_project_admin_access',
                    403,
                )
            actor = self._require_project_manage(actor, project_id)
            authorization = "permission_manage"
        else:
            project = self.repository.get_project(project_id)
            if project is None:
                self._reject(
                    'project.was_not_found',
                    "audit-project-update-rejected",
                    404,
                    request_id=payload.idempotency_key,
                )
            if (
                project.status == "retired"
                and "policy_profile_id" in submitted_fields
            ):
                self._reject(
                    'project.was_not_found_or_is_retired',
                    "audit-project-update-rejected",
                    409,
                    request_id=payload.idempotency_key,
                )
        expected_project = replace(project)
        if payload.name is not None:
            project.name = payload.name
        if payload.policy_profile_id is not None:
            project.policy_profile_id = payload.policy_profile_id
        if payload.status is not None:
            project.status = payload.status
        self.repository.put_project(
            project,
            expected_project=expected_project,
            authorization=authorization,
        )
        audit = self.repository.append_audit(
            ProjectAuditCommand(
                event_type="project_updated",
                actor_id=actor.actor_id,
                target_ref=f"project:{project_id}",
                project_id=project_id,
                message_code='project.is_updated',
                metadata={
                    "policy_profile_id": project.policy_profile_id,
                    "status": project.status,
                },
            )
        )
        self.repository.persist()
        return ProjectActionOutcome(
            AdminActionResult(
                request_id=payload.idempotency_key,
                status="applied",
                target_ref=f"project:{project_id}",
                message_code='project.is_updated',
                audit_event_ref=audit.event_id,
            ),
            200,
        )

    def _require_project_manage(
        self,
        actor: UserRecord | None,
        project_id: str,
    ) -> UserRecord:
        actor = self._require_project_actor(actor)
        if not self.repository.get_project(project_id):
            self._reject(
                'project.was_not_found',
                "audit-project-member-rejected",
                404,
                request_id=f"project-member-{project_id}",
            )
        decision = self.repository.resolve_access(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            project_id=project_id,
            action="permission_manage",
            persist=False,
        )
        if decision.allowed:
            return actor
        audit = self.repository.append_audit(
            ProjectAuditCommand(
                event_type="project_member_access_denied",
                actor_id=actor.actor_id,
                target_ref=f"project:{project_id}",
                project_id=project_id,
                message_code='project.members_require_project_admin_access',
                metadata={
                    "access_decision_id": decision.decision_id,
                    "reason": decision.reason,
                },
            )
        )
        raise ProjectGovernanceError(
            "access_denied",
            'project.members_require_project_admin_access',
            403,
            audit.event_id,
        )

    def _find_access_grant(
        self,
        project_id: str,
        subject_type: str,
        subject_id: str,
    ) -> PermissionGrantRecord | None:
        matches = [
            grant
            for grant in self.repository.list_grants()
            if grant.project_id == project_id
            and grant.subject_type == subject_type
            and grant.subject_id == subject_id
            and grant.role in PROJECT_ACCESS_ROLES
            and grant.effect in PROJECT_ACCESS_EFFECTS
            and grant.status in PROJECT_ACCESS_STATUSES
        ]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda item: (item.status != "active", item.grant_id),
        )[0]

    @staticmethod
    def _is_project_access_grant(
        grant: PermissionGrantRecord | None,
        project_id: str,
    ) -> bool:
        return bool(
            grant
            and grant.project_id == project_id
            and grant.subject_type in PROJECT_ACCESS_SUBJECT_TYPES
            and grant.role in PROJECT_ACCESS_ROLES
            and grant.effect in PROJECT_ACCESS_EFFECTS
            and grant.status in PROJECT_ACCESS_STATUSES
        )

    def _missing_access_subject(
        self,
        subject_type: str,
        subject_id: str,
    ) -> str | None:
        if subject_type == "user":
            user = self.repository.get_user(subject_id)
            if not user or user.actor_type != "user" or not user.active:
                return "user.was_not_found"
            return None
        if subject_type == "service_account":
            service_account = self.repository.get_user(subject_id)
            if (
                not service_account
                or service_account.actor_type != "service_account"
                or not service_account.active
            ):
                return "permission.subject_was_not_found"
            return None
        if subject_type == "team":
            team = self.repository.get_team(subject_id)
            if not team or team.status != "active":
                return 'team.was_not_found'
            return None
        return "permission.subject_was_not_found"

    @staticmethod
    def _access_grant(
        grant: PermissionGrantRecord,
    ) -> ProjectAccessGrant:
        return ProjectAccessGrant(
            grant_id=grant.grant_id,
            project_id=grant.project_id,
            subject_type=grant.subject_type,
            subject_id=grant.subject_id,
            role=grant.role,
            effect=grant.effect,
            status=grant.status,
            created_at=grant.created_at,
            revoked_at=grant.revoked_at,
        )

    def _access_subject(
        self,
        grant: PermissionGrantRecord,
    ) -> ProjectMemberCandidate | None:
        if grant.subject_type == "team":
            team = self.repository.get_team(grant.subject_id)
            if team is None:
                return None
            return ProjectMemberCandidate(
                subject_type="team",
                subject_id=team.team_id,
                display_name=team.name,
                display_detail=None,
            )
        actor = self.repository.get_user(grant.subject_id)
        if actor is None or actor.actor_type != grant.subject_type:
            return None
        return ProjectMemberCandidate(
            subject_type=grant.subject_type,
            subject_id=actor.actor_id,
            display_name=actor.display_name,
            display_detail=actor.email,
        )

    @staticmethod
    def _directory_import_audit(
        *,
        event_type: str,
        actor_id: str,
        target_ref: str,
        project_id: str,
        message_code: str,
        metadata: dict[str, object],
        count: int,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            event_id=f"audit-{uuid4().hex}",
            event_type=event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=project_id,
            message_code=message_code,
            message_params={"count": count},
            metadata=metadata,
            created_at=utc_now_iso(),
            scope_type="project",
            scope_id=project_id,
        )

    @staticmethod
    def _raise_directory_error(error: IdentityAccessError) -> NoReturn:
        raise ProjectGovernanceError(
            error.error_code,
            error.message_code,
            error.status_code,
            error.audit_event_ref,
        ) from error


    @staticmethod
    def _require_project_actor(actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise ProjectGovernanceError(
                "unauthenticated",
                'project.please_sign_in_before_using_project_management',
                401,
            )
        return actor

    def _require_system_admin(self, actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise ProjectGovernanceError(
                "unauthenticated",
                'auth.please_sign_in_before_using_admin_tools',
                401,
            )
        if not self.repository.is_system_admin(actor):
            raise ProjectGovernanceError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            )
        return actor

    @staticmethod
    def _reject(
        message: str,
        audit_event_ref: str,
        status_code: int,
        *,
        request_id: str,
        target_ref: str | None = None,
    ) -> NoReturn:
        raise ProjectGovernanceError(
            "admin_action_rejected",
            message,
            status_code,
            audit_event_ref,
            request_id,
            target_ref,
        )

    @staticmethod
    def project_access_grant_id(
        project_id: str,
        subject_type: str,
        subject_id: str,
    ) -> str:
        return f"grant-project-access-{project_id}-{subject_type}-{subject_id}"
