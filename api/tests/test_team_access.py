from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atlas_production.modules.identity_access.api_models import (
    TeamCreateRequest,
    TeamUpdateRequest,
)
from atlas_production.modules.identity_access.records import TeamRecord, UserRecord
from atlas_production.modules.identity_access.team_contracts import TeamAccessError
from atlas_production.modules.identity_access.team_service import TeamAccessService
from atlas_production.shared.public import AuditEventRecord


def test_team_create_request_leaves_identifier_allocation_to_owner() -> None:
    request = TeamCreateRequest(
        name="Owner allocated",
        parent_team_id=None,
        idempotency_key="team-create-owner",
    )
    assert "team_id" not in request.model_dump()
    with pytest.raises(ValidationError):
        TeamCreateRequest(
            team_id="caller-team",
            name="Caller allocated",
            parent_team_id=None,
            idempotency_key="team-create-caller",
        )


NOW = "2026-08-18T00:00:00+00:00"


class TeamRepository:
    def __init__(self) -> None:
        self.teams = {
            "team-a": TeamRecord("team-a", "Team A", None, "active", NOW),
            "team-parent": TeamRecord(
                "team-parent", "Parent", None, "active", NOW
            ),
        }
        self.system_admin_ids = {"system-admin"}
        self.direct_admin_ids = {"team-admin"}
        self.audits: list[AuditEventRecord] = []
        self.mutation_modes: list[bool] = []
        self.fail_audit = False
        self.fail_current_authority = False
        self.retire_before_commit = False
        self._actor_ids: tuple[str, ...] = ()
        self._include_hierarchy = False
        self._original: TeamRecord | None = None
        self._staged: TeamRecord | None = None

    @contextmanager
    def team_mutation(
        self,
        team_id: str,
        *,
        actor_ids: tuple[str, ...] = (),
        include_hierarchy: bool = False,
    ):
        self._actor_ids = actor_ids
        self._include_hierarchy = include_hierarchy
        self.mutation_modes.append(include_hierarchy)
        try:
            yield
        finally:
            self._actor_ids = ()
            self._include_hierarchy = False
            self._original = None
            self._staged = None

    def get_team(self, team_id: str) -> TeamRecord | None:
        if self._staged is not None and self._staged.team_id == team_id:
            return replace(self._staged)
        team = self.teams.get(team_id)
        if self._actor_ids and self._original is None and team is not None:
            self._original = replace(team)
        return replace(team) if team else None

    def put_team(self, team: TeamRecord) -> None:
        self._staged = replace(team)

    def is_system_admin(self, actor: UserRecord) -> bool:
        return (
            actor.active
            and actor.actor_type == "user"
            and actor.actor_id in self.system_admin_ids
        )

    def can_manage_team(self, actor: UserRecord, team_id: str) -> bool:
        return (
            actor.active
            and actor.actor_type == "user"
            and actor.actor_id in self.direct_admin_ids
            and self.teams.get(team_id) is not None
            and self.teams[team_id].status == "active"
        )

    def would_create_cycle(self, _team_id: str, _parent_team_id: str | None) -> bool:
        return False

    def would_exceed_depth(self, _team_id: str, _parent_team_id: str | None) -> bool:
        return False

    def append_audit(self, command) -> AuditEventRecord:
        if self.fail_audit:
            raise RuntimeError("audit write failed")
        if self.retire_before_commit:
            self.teams["team-a"].status = "retired"
        actor_id = self._actor_ids[0] if self._actor_ids else ""
        if self.fail_current_authority or (
            not self._include_hierarchy
            and actor_id not in self.system_admin_ids
            and (
                actor_id not in self.direct_admin_ids
                or self.teams["team-a"].status != "active"
            )
        ):
            raise TeamAccessError(
                "access_denied",
                "team.admin_access_is_required",
                403,
            )
        if self._original != self.teams.get("team-a"):
            raise TeamAccessError(
                "team_update_conflict",
                "team.was_not_found",
                409,
            )
        event = AuditEventRecord(
            event_id=f"audit-{len(self.audits) + 1}",
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=None,
            message_code=command.message_code,
            metadata=command.metadata,
            created_at=NOW,
            scope_type="team",
            scope_id="team-a",
        )
        assert self._staged is not None
        self.teams[self._staged.team_id] = replace(self._staged)
        self.audits.append(event)
        return event


def actor(
    actor_id: str,
    *,
    active: bool = True,
    actor_type: str = "user",
) -> UserRecord:
    return UserRecord(
        actor_id=actor_id,
        display_name=actor_id,
        email=f"{actor_id}@example.test",
        system_role="admin" if actor_id == "system-admin" else "user",
        password_digest=None,
        active=active,
        actor_type=actor_type,
        created_at=NOW,
    )


def service(repository: TeamRepository) -> TeamAccessService:
    return TeamAccessService(repository, SimpleNamespace(), SimpleNamespace())


def update(**fields: object) -> TeamUpdateRequest:
    return TeamUpdateRequest(idempotency_key="request-1", **fields)


def test_system_admin_retires_reactivates_and_can_only_edit_safe_retired_fields() -> None:
    repository = TeamRepository()
    team_service = service(repository)

    retired = team_service.update_team(
        actor("system-admin"), "team-a", update(status="retired")
    )

    assert retired.success_status_code == 200
    assert repository.teams["team-a"].status == "retired"
    assert repository.mutation_modes == [True]
    assert repository.audits[-1].metadata == {
        "parent_team_id": None,
        "status": "retired",
        "inherit_parent_documents": True,
    }
    assert "name" not in repository.audits[-1].metadata

    team_service.update_team(
        actor("system-admin"), "team-a", update(name="Retired Team")
    )
    assert repository.teams["team-a"].name == "Retired Team"
    assert repository.mutation_modes[-1] is True

    with pytest.raises(TeamAccessError) as raised:
        team_service.update_team(
            actor("system-admin"),
            "team-a",
            update(status="active", parent_team_id="team-parent"),
        )
    assert raised.value.status_code == 409
    assert repository.teams["team-a"].status == "retired"
    assert repository.teams["team-a"].parent_team_id is None

    team_service.update_team(
        actor("system-admin"), "team-a", update(status="active")
    )
    assert repository.teams["team-a"].status == "active"


def test_current_direct_human_team_admin_can_submit_exact_name_only() -> None:
    repository = TeamRepository()

    outcome = service(repository).update_team(
        actor("team-admin"), "team-a", update(name="Renamed Team")
    )

    assert outcome.success_status_code == 200
    assert repository.teams["team-a"].name == "Renamed Team"
    assert repository.teams["team-a"].status == "active"
    assert repository.mutation_modes == [False]
    assert len(repository.audits) == 1
    assert "name" not in repository.audits[0].metadata


@pytest.mark.parametrize(
    "payload",
    [
        update(status="retired"),
        update(parent_team_id="team-parent"),
        update(inherit_parent_documents=False),
        update(name="Must Not Apply", status=None),
        update(name="Must Not Apply", parent_team_id=None),
        update(name="Must Not Apply", inherit_parent_documents=None),
    ],
)
def test_scoped_team_admin_privileged_or_mixed_payload_fails_as_one_unit(
    payload: TeamUpdateRequest,
) -> None:
    repository = TeamRepository()

    with pytest.raises(TeamAccessError) as raised:
        service(repository).update_team(actor("team-admin"), "team-a", payload)

    assert raised.value.status_code == 403
    assert repository.teams["team-a"] == TeamRecord(
        "team-a", "Team A", None, "active", NOW
    )
    assert repository.audits == []


@pytest.mark.parametrize(
    "payload",
    [update(name="Must Not Apply"), update(status="retired")],
)
def test_scoped_update_does_not_disclose_unknown_team(
    payload: TeamUpdateRequest,
) -> None:
    repository = TeamRepository()
    repository.teams.pop("team-a")

    with pytest.raises(TeamAccessError) as raised:
        service(repository).update_team(actor("team-admin"), "team-a", payload)

    assert raised.value.status_code == 403
    assert repository.audits == []


@pytest.mark.parametrize(
    ("user", "target_status"),
    [
        (actor("ordinary-member"), "active"),
        (actor("team-admin", active=False), "active"),
        (actor("team-admin", actor_type="service_account"), "active"),
        (actor("team-admin"), "retired"),
    ],
)
def test_non_current_non_human_or_retired_scoped_actor_is_denied(
    user: UserRecord,
    target_status: str,
) -> None:
    repository = TeamRepository()
    repository.teams["team-a"].status = target_status

    with pytest.raises(TeamAccessError) as raised:
        service(repository).update_team(
            user, "team-a", update(name="Must Not Apply")
        )

    assert raised.value.status_code == 403
    assert repository.teams["team-a"].name == "Team A"
    assert repository.audits == []


def test_retired_team_rejects_system_admin_membership_removal_before_lookup() -> None:
    repository = TeamRepository()
    repository.teams["team-a"].status = "retired"
    repository.get_membership = lambda _membership_id: pytest.fail(
        "retired Team membership must not be read"
    )

    with pytest.raises(TeamAccessError) as raised:
        service(repository).remove_member(
            actor("system-admin"), "team-a", "tm-team-a-user-1"
        )

    assert raised.value.status_code == 404
    assert repository.audits == []


def test_audit_failure_rolls_back_team_update() -> None:
    repository = TeamRepository()
    repository.fail_audit = True

    with pytest.raises(RuntimeError, match="audit write failed"):
        service(repository).update_team(
            actor("system-admin"), "team-a", update(status="retired")
        )

    assert repository.teams["team-a"].status == "active"
    assert repository.audits == []


@pytest.mark.parametrize("failure", ["authority", "retirement"])
def test_commit_time_authority_or_status_loss_rolls_back_scoped_rename(
    failure: str,
) -> None:
    repository = TeamRepository()
    repository.fail_current_authority = failure == "authority"
    repository.retire_before_commit = failure == "retirement"

    with pytest.raises(TeamAccessError):
        service(repository).update_team(
            actor("team-admin"), "team-a", update(name="Must Not Apply")
        )

    assert repository.teams["team-a"].name == "Team A"
    assert repository.audits == []
