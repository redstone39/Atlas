from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.api_models import (
    ProjectUpdateRequest,
)
from atlas_production.modules.project_governance.contracts import (
    ProjectGovernanceError,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.modules.project_governance.service import (
    ProjectGovernanceService,
)
from atlas_production.rbac import authorized_project_tag_ids, resolve_access
from atlas_production.shared.public import AuditEventRecord


class ProjectRepository:
    def __init__(self) -> None:
        self.projects = {
            "project-a": ProjectRecord(
                project_id="project-a",
                name="Project A",
                policy_profile_id="default",
                status="active",
            )
        }
        self.system_admin_ids = {"system-admin"}
        self.project_admin_ids = {"project-admin", "team-derived-admin"}
        self.current_actor_active: dict[str, bool] = {}
        self.staged_project: ProjectRecord | None = None
        self.staged_authorization: str | None = None
        self.staged_expected_project: ProjectRecord | None = None
        self.committed_authorizations: list[str] = []
        self.audits: list[AuditEventRecord] = []
        self.fail_audit = False
        self.fail_current_authority = False
        self.mutate_before_put = False

    def get_project(self, project_id: str) -> ProjectRecord | None:
        project = self.projects.get(project_id)
        return replace(project) if project else None

    def list_projects(self) -> list[ProjectRecord]:
        return [replace(project) for project in self.projects.values()]

    def put_project(
        self,
        project: ProjectRecord,
        *,
        expected_project: ProjectRecord | None,
        authorization: str,
    ) -> None:
        if self.mutate_before_put:
            self.projects[project.project_id].status = "retired"
        current = self.projects.get(project.project_id)
        if current != expected_project:
            self._discard_staging()
            raise ProjectGovernanceError(
                "admin_action_rejected",
                "project.was_not_found",
                409,
            )
        self.staged_project = replace(project)
        self.staged_expected_project = (
            replace(expected_project) if expected_project else None
        )
        self.staged_authorization = authorization

    def is_system_admin(self, actor: UserRecord) -> bool:
        self.current_actor_active[actor.actor_id] = actor.active
        return actor.active and actor.actor_id in self.system_admin_ids

    def resolve_access(
        self,
        *,
        actor_type: str,
        actor_id: str,
        project_id: str,
        action: str,
        persist: bool = True,
    ) -> AccessDecisionRecord:
        allowed = (
            self.current_actor_active.get(actor_id, True)
            and actor_id in self.project_admin_ids
        )
        source_type = "team" if actor_id == "team-derived-admin" else "user"
        return AccessDecisionRecord(
            decision_id=f"decision-{actor_id}",
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=project_id,
            action=action,
            required_role="admin",
            allowed=allowed,
            reason="active_grant" if allowed else "no_matching_grant",
            effective_role="admin" if allowed else None,
            source_type=source_type if allowed else None,
            source_id="team-admins" if source_type == "team" else actor_id,
            explanation="test decision",
            created_at="2026-08-18T00:00:00Z",
        )

    def append_audit(self, command) -> AuditEventRecord:
        if self.fail_audit:
            self._discard_staging()
            raise RuntimeError("audit write failed")
        if self.fail_current_authority:
            self._discard_staging()
            raise ProjectGovernanceError(
                "access_denied",
                "project.members_require_project_admin_access",
                403,
            )
        event = AuditEventRecord(
            event_id=f"audit-{len(self.audits) + 1}",
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=command.project_id,
            message_code=command.message_code,
            metadata=command.metadata,
            created_at="2026-08-18T00:00:00Z",
            scope_type="project",
            scope_id=command.project_id,
        )
        if self.staged_project is None:
            self.audits.append(event)
            return event
        assert self.staged_authorization is not None
        self.committed_authorizations.append(self.staged_authorization)
        self.projects[self.staged_project.project_id] = replace(self.staged_project)
        self.audits.append(event)
        self._discard_staging()
        return event

    def persist(self) -> None:
        assert self.staged_project is None

    def _discard_staging(self) -> None:
        self.staged_project = None
        self.staged_expected_project = None
        self.staged_authorization = None


def actor(
    actor_id: str,
    *,
    active: bool = True,
) -> UserRecord:
    return UserRecord(
        actor_id=actor_id,
        display_name=actor_id,
        email=f"{actor_id}@example.test",
        system_role="admin" if actor_id == "system-admin" else "user",
        password_digest=None,
        active=active,
    )


def service(repository: ProjectRepository) -> ProjectGovernanceService:
    return ProjectGovernanceService(
        repository,
        SimpleNamespace(),
        SimpleNamespace(),
    )


def update(**fields: object) -> ProjectUpdateRequest:
    return ProjectUpdateRequest(idempotency_key="request-1", **fields)


def test_system_admin_retires_and_reactivates_project_with_canonical_status() -> None:
    repository = ProjectRepository()
    project_service = service(repository)

    retired = project_service.update_project(
        actor("system-admin"),
        "project-a",
        update(status="retired"),
    )

    assert retired.success_status_code == 200
    assert repository.projects["project-a"].status == "retired"
    assert repository.committed_authorizations == ["system_admin"]
    assert repository.audits[-1].metadata == {
        "policy_profile_id": "default",
        "status": "retired",
    }
    assert "name" not in repository.audits[-1].metadata
    assert project_service.list_projects(actor("system-admin")).projects[0].status == "retired"

    project_service.update_project(
        actor("system-admin"),
        "project-a",
        update(status="active"),
    )

    assert repository.projects["project-a"].status == "active"
    assert project_service.list_projects(actor("system-admin")).projects[0].status == "active"
    assert repository.committed_authorizations == ["system_admin", "system_admin"]


@pytest.mark.parametrize("actor_id", ["project-admin", "team-derived-admin"])
def test_current_project_admin_can_submit_exact_name_only(actor_id: str) -> None:
    repository = ProjectRepository()

    outcome = service(repository).update_project(
        actor(actor_id),
        "project-a",
        update(name="Renamed Project"),
    )

    assert outcome.success_status_code == 200
    assert repository.projects["project-a"].name == "Renamed Project"
    assert repository.projects["project-a"].policy_profile_id == "default"
    assert repository.projects["project-a"].status == "active"
    assert len(repository.audits) == 1
    assert repository.committed_authorizations == ["permission_manage"]


@pytest.mark.parametrize(
    "payload",
    [
        update(status="retired"),
        update(policy_profile_id="strict"),
        update(name="Must Not Apply", status="retired"),
        update(name="Must Not Apply", policy_profile_id=None),
    ],
)
def test_scoped_project_admin_privileged_or_mixed_payload_fails_as_one_unit(
    payload: ProjectUpdateRequest,
) -> None:
    repository = ProjectRepository()

    with pytest.raises(ProjectGovernanceError) as raised:
        service(repository).update_project(
            actor("project-admin"),
            "project-a",
            payload,
        )

    assert raised.value.status_code == 403
    assert repository.projects["project-a"] == ProjectRecord(
        "project-a",
        "Project A",
        "default",
        "active",
    )
    assert repository.audits == []
    assert repository.staged_project is None


@pytest.mark.parametrize(
    ("user", "target_status", "expected_audit_type"),
    [
        (actor("ordinary-member"), "active", "project_member_access_denied"),
        (actor("project-admin", active=False), "active", "project_member_access_denied"),
        (actor("project-admin"), "retired", None),
    ],
)
def test_non_current_or_retired_target_scoped_actor_is_denied(
    user: UserRecord,
    target_status: str,
    expected_audit_type: str | None,
) -> None:
    repository = ProjectRepository()
    repository.projects["project-a"].status = target_status

    with pytest.raises(ProjectGovernanceError) as raised:
        service(repository).update_project(
            user,
            "project-a",
            update(name="Must Not Apply"),
        )

    assert raised.value.status_code == 403
    assert repository.projects["project-a"].name == "Project A"
    assert [audit.event_type for audit in repository.audits] == (
        [expected_audit_type] if expected_audit_type else []
    )


def test_retired_project_rejects_system_admin_policy_change_before_staging() -> None:
    repository = ProjectRepository()
    repository.projects["project-a"].status = "retired"

    with pytest.raises(ProjectGovernanceError) as raised:
        service(repository).update_project(
            actor("system-admin"),
            "project-a",
            update(name="Must Not Apply", policy_profile_id="strict"),
        )

    assert raised.value.status_code == 409
    assert raised.value.message_code == "project.was_not_found_or_is_retired"
    assert repository.projects["project-a"] == ProjectRecord(
        "project-a",
        "Project A",
        "default",
        "retired",
    )
    assert repository.audits == []
    assert repository.staged_project is None


def test_audit_failure_rolls_back_project_update() -> None:
    repository = ProjectRepository()
    repository.fail_audit = True

    with pytest.raises(RuntimeError, match="audit write failed"):
        service(repository).update_project(
            actor("system-admin"),
            "project-a",
            update(status="retired"),
        )

    assert repository.projects["project-a"].status == "active"
    assert repository.audits == []
    assert repository.staged_project is None


def test_commit_time_authority_loss_rolls_back_name_and_success_audit() -> None:
    repository = ProjectRepository()
    repository.fail_current_authority = True

    with pytest.raises(ProjectGovernanceError) as raised:
        service(repository).update_project(
            actor("project-admin"),
            "project-a",
            update(name="Must Not Apply"),
        )

    assert raised.value.status_code == 403
    assert repository.projects["project-a"].name == "Project A"
    assert repository.audits == []
    assert repository.staged_project is None


def test_concurrent_retire_between_service_read_and_staging_rejects_stale_rename() -> None:
    repository = ProjectRepository()
    repository.mutate_before_put = True

    with pytest.raises(ProjectGovernanceError) as raised:
        service(repository).update_project(
            actor("project-admin"),
            "project-a",
            update(name="Must Not Reactivate"),
        )

    assert raised.value.status_code == 409
    assert repository.projects["project-a"] == ProjectRecord(
        "project-a",
        "Project A",
        "default",
        "retired",
    )
    assert repository.audits == []
    assert repository.staged_project is None


def test_retired_project_denies_in_memory_system_admin_operational_access() -> None:
    actor = UserRecord(
        "system-admin",
        "System Admin",
        "admin@example.test",
        "admin",
        None,
        True,
        "user",
        "2026-08-18T00:00:00+00:00",
    )
    project = ProjectRecord("project-retired", "Retired", "default", "retired")
    store = SimpleNamespace(
        users={actor.actor_id: actor},
        projects={project.project_id: project},
        teams={},
        team_memberships={},
        permission_grants={},
        access_decisions={},
    )

    decision = resolve_access(
        store,
        actor_type="user",
        actor_id=actor.actor_id,
        project_id=project.project_id,
        action="workspace_query",
        persist=False,
    )

    assert decision.allowed is False
    assert decision.reason == "project_retired"
    assert authorized_project_tag_ids(
        store,
        "user",
        actor.actor_id,
        action="workspace_query",
    ) == set()
