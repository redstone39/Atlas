from __future__ import annotations

import secrets
import inspect
from dataclasses import replace

import pytest

from atlas_production.infrastructure.postgres_agent_adapter import (
    PostgresAgentQueryAuthority,
    PostgresAgentAccessRepository,
)
from atlas_production.infrastructure.postgres_audit_adapter import (
    PostgresAuditConsumerAdapter,
    PostgresReadAuditWriter,
)
from atlas_production.infrastructure.postgres_identity_adapter import (
    PostgresCurrentPrincipal,
    PostgresIdentityAccessRepository,
    PostgresInviteScopeGrantAdapter,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityAuthorizationConflict,
    IdentityCurrentnessConflict,
    IdentityRepository,
    IdentitySessionChangeSet,
    RevokeBrowserSessionCommand,
    SeedLocalPilotAdminCommand,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
    ProjectAclChangeSet,
    ProjectAclRepository,
    ProjectAuthorizationConflict,
    PostgresNotesMembershipAuthority,
)
from atlas_production.infrastructure.postgres_owner.team import (
    TeamAuthorizationConflict,
    TeamCurrentnessConflict,
    TeamGovernanceChangeSet,
    TeamInvariantViolation,
    TeamRepository,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasAgentTokenRow,
    AtlasSessionRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.audit_events import (
    AtlasAuditEventRow,
)
from atlas_production.infrastructure.persistence.project_governance import (
    AtlasProjectRow,
)
from atlas_production.infrastructure.persistence.retrieval_currentness import (
    read_effective_document_scope_with_team_ids,
)
from atlas_production.infrastructure.postgres_project_adapter import (
    PostgresProjectGovernanceRepository,
)
from atlas_production.infrastructure.postgres_team_adapter import (
    PostgresTeamAccessRepository,
)
from atlas_production.modules.identity_access.agent_contracts import (
    AgentAuditCommand,
)
from atlas_production.modules.identity_access.agent_ports import AgentAccessRepository
from atlas_production.modules.identity_access.contracts import IdentityAuditCommand
from atlas_production.modules.identity_access.directory_ports import (
    DirectoryRepository,
    ScopedDirectoryImportCommitPort,
)
from atlas_production.modules.identity_access.ports import (
    IdentityAccessRepository,
    InviteScopeGrantPort,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    AgentTokenRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.security import (
    agent_token_digest,
    verify_password,
)
from atlas_production.modules.identity_access.local_pilot import (
    AdminBootstrapConfigurationError,
)
from atlas_production.modules.identity_access.team_contracts import TeamAuditCommand
from atlas_production.modules.identity_access.team_ports import TeamAccessRepository
from atlas_production.modules.project_governance.contracts import ProjectAuditCommand
from atlas_production.modules.project_governance.ports import (
    ProjectGovernanceRepository,
)
from atlas_production.modules.project_governance.records import ProjectRecord


NOW = "2026-07-18T00:00:00+00:00"
VALID_TEST_PASSWORD = secrets.token_urlsafe(18)


class _SeedSession:
    def __init__(self, existing_actor_id: str | None = None) -> None:
        self.existing_actor_id = existing_actor_id
        self.added: list[object] = []
        self.executed: list[tuple[object, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters=None):
        self.executed.append((statement, parameters))

    def scalar(self, _statement):
        return self.existing_actor_id

    def add(self, row) -> None:
        self.added.append(row)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_local_pilot_seed_creates_admin_and_audit_for_empty_identity_owner() -> None:
    session = _SeedSession()
    password = secrets.token_urlsafe(18)

    receipt = SeedLocalPilotAdminCommand(lambda: session).execute(
        actor_id="user-admin-001",
        display_name="Atlas Admin",
        email=" ADMIN@EXAMPLE.TEST ",
        password=password,
    )

    assert receipt.actor_id == "user-admin-001"
    assert receipt.created is True
    assert session.commits == 1
    assert session.rollbacks == 0
    assert len(session.executed) == 2
    user = next(row for row in session.added if isinstance(row, AtlasUserRow))
    audit = next(row for row in session.added if isinstance(row, AtlasAuditEventRow))
    assert user.email == "admin@example.test"
    assert user.system_role == "admin"
    assert user.active is True
    assert user.actor_type == "user"
    assert verify_password(password, user.password_digest)
    assert audit.event_type == "local_pilot_admin_seeded"
    assert audit.target_ref == "user:user-admin-001"
    assert audit.event_metadata == {
        "email": "admin@example.test",
        "system_role": "admin",
    }


def test_local_pilot_seed_replay_never_mutates_nonempty_identity_owner() -> None:
    session = _SeedSession(existing_actor_id="some-existing-user")

    receipt = SeedLocalPilotAdminCommand(lambda: session).execute(
        actor_id="user-admin-001",
        display_name="Atlas Admin",
        email=None,
        password=None,
    )

    assert receipt.created is False
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.parametrize(
    ("email", "password", "error_code"),
    (
        (None, None, "identity_admin_bootstrap_configuration_required"),
        ("", VALID_TEST_PASSWORD, "identity_admin_bootstrap_configuration_required"),
        ("admin@example.test", "too-short", "identity_admin_bootstrap_configuration_invalid"),
    ),
)
def test_local_pilot_seed_requires_valid_configuration_only_for_empty_identity(
    email: str | None,
    password: str | None,
    error_code: str,
) -> None:
    session = _SeedSession()

    with pytest.raises(AdminBootstrapConfigurationError) as exc_info:
        SeedLocalPilotAdminCommand(lambda: session).execute(
            actor_id="user-admin-001",
            display_name="Atlas Admin",
            email=email,
            password=password,
        )

    assert exc_info.value.error_code == error_code
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 1


def _parameter_shape(owner: type[object], name: str) -> tuple[tuple[object, ...], ...]:
    signature = inspect.signature(getattr(owner, name))
    return tuple(
        (parameter.name, parameter.kind, parameter.default)
        for parameter in signature.parameters.values()
    )


@pytest.mark.parametrize(
    ("contract", "implementation"),
    [
        (DirectoryRepository, PostgresIdentityAccessRepository),
        (InviteScopeGrantPort, PostgresInviteScopeGrantAdapter),
        (AgentAccessRepository, PostgresAgentAccessRepository),
        (TeamAccessRepository, PostgresTeamAccessRepository),
        (ProjectGovernanceRepository, PostgresProjectGovernanceRepository),
    ],
)
def test_route_facing_adapters_cover_every_public_port_signature(
    contract: type[object],
    implementation: type[object],
) -> None:
    contract_owners = (
        (
            DirectoryRepository,
            IdentityAccessRepository,
            ScopedDirectoryImportCommitPort,
        )
        if contract is DirectoryRepository
        else (contract,)
    )
    contract_methods = {
        name
        for owner in contract_owners
        for name, value in owner.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert contract_methods
    if contract is ProjectGovernanceRepository:
        assert len(contract_methods) == 14
    implementation_methods = {
        name
        for name, value in implementation.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert implementation_methods == contract_methods
    for name in sorted(contract_methods):
        assert callable(getattr(implementation, name, None)), name
        contract_owner = next(
            owner for owner in contract_owners if name in owner.__dict__
        )
        assert _parameter_shape(implementation, name) == _parameter_shape(
            contract_owner,
            name,
        )


def _page(items, *, after: str | None, key, limit: int):
    selected = [item for item in items if after is None or key(item) > after]
    return selected[:limit]


class _PagedIdentityOwner:
    def __init__(self) -> None:
        self.users = [
            UserRecord(
                f"user-{index:04d}", f"User {index}",
                f"user-{index}@example.test", "admin", None, True, "user", NOW,
            )
            for index in range(501)
        ]
        self.invites = [
            UserInviteRecord(
                f"invite-{index:04d}", f"user-{index:04d}",
                f"invite-{index}@example.test", f"Invite {index}", "user",
                f"digest-{index}", f"finger-{index}", "pending", NOW, NOW,
            )
            for index in range(501)
        ]
        self.tokens = [
            AgentTokenRecord(
                f"token-{index:04d}", "agent-unit", f"digest-{index}",
                f"finger-{index}", "active", NOW,
            )
            for index in range(501)
        ]

    def list_users(self, *, limit=500, after_actor_id=None):
        return _page(
            self.users, after=after_actor_id, key=lambda item: item.actor_id,
            limit=limit,
        )

    def list_invites(self, *, limit=500, after_invite_id=None):
        return _page(
            self.invites, after=after_invite_id, key=lambda item: item.invite_id,
            limit=limit,
        )

    def active_admin_count(self):
        return len(self.users)

    def list_agent_tokens(
        self, actor_id, *, limit=500, after_token_id=None,
    ):
        items = [item for item in self.tokens if item.actor_id == actor_id]
        return _page(
            items, after=after_token_id, key=lambda item: item.token_id,
            limit=limit,
        )

    def get_user(self, actor_id):
        return next((item for item in self.users if item.actor_id == actor_id), None)


def test_identity_public_lists_and_admin_count_are_complete_past_500() -> None:
    repository = PostgresIdentityAccessRepository(lambda: None)
    repository.owner = _PagedIdentityOwner()
    assert len(repository.list_users()) == 501
    assert len(repository.list_invites()) == 501
    assert repository.active_admin_count() == 501
    with repository.identity_mutation("identity:user-0500"):
        user = repository.get_user("user-0500")
        assert user is not None
        repository.put_user(replace(user, active=False))
        assert repository.active_admin_count() == 500


def test_agent_token_list_is_complete_past_500() -> None:
    repository = PostgresAgentAccessRepository(lambda: None)
    repository.identity_owner = _PagedIdentityOwner()
    assert len(repository.list_tokens_for_agent("agent-unit")) == 501


class _PagedTeamOwner:
    def __init__(self) -> None:
        self.teams = [
            TeamRecord(f"team-{index:04d}", f"Team {index}", None, "active", NOW)
            for index in range(501)
        ]
        self.memberships = [
            TeamMembershipRecord(
                f"tm-{index:04d}", f"team-{index:04d}", "user",
                "user-unit", "active", NOW,
            )
            for index in range(501)
        ]

    def list_teams(self, *, limit=500, after_team_id=None):
        return _page(
            self.teams, after=after_team_id, key=lambda item: item.team_id,
            limit=limit,
        )

    def list_memberships(
        self, *, team_id=None, actor_id=None, limit=500,
        after_membership_id=None,
    ):
        items = self.memberships
        if team_id is not None:
            items = [item for item in items if item.team_id == team_id]
        if actor_id is not None:
            items = [item for item in items if item.member_actor_id == actor_id]
        return _page(
            items, after=after_membership_id,
            key=lambda item: item.membership_id, limit=limit,
        )


def test_team_public_lists_are_complete_past_500() -> None:
    repository = PostgresTeamAccessRepository(lambda: None)
    repository.owner = _PagedTeamOwner()
    assert len(repository.list_teams()) == 501
    assert len(repository.list_memberships()) == 501


class _PagedProjectOwner:
    def __init__(self) -> None:
        self.projects = [
            ProjectRecord(f"project-{index:04d}", f"Project {index}", "policy")
            for index in range(501)
        ]
        self.grants = [
            PermissionGrantRecord(
                f"grant-{index:04d}", f"project-{index:04d}", "user",
                "user-unit", "viewer", "allow", "active", NOW,
            )
            for index in range(501)
        ]

    def list_projects(self, *, limit=500, after_project_id=None):
        return _page(
            self.projects, after=after_project_id,
            key=lambda item: item.project_id, limit=limit,
        )

    def list_grants(self, *, project_id=None, limit=500, after_grant_id=None):
        items = self.grants
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        return _page(
            items, after=after_grant_id, key=lambda item: item.grant_id,
            limit=limit,
        )

    def list_subject_grants(
        self, *, subject_type, subject_id=None, active_only=False, limit=500,
        after_grant_id=None,
    ):
        items = [
            replace(item, subject_type=subject_type)
            for item in self.grants
            if subject_id is None or item.subject_id == subject_id
        ]
        if active_only:
            items = [item for item in items if item.status == "active"]
        return _page(
            items, after=after_grant_id, key=lambda item: item.grant_id,
            limit=limit,
        )


def test_project_public_lists_are_complete_past_500() -> None:
    repository = PostgresProjectGovernanceRepository(lambda: None)
    repository.owner = _PagedProjectOwner()
    assert len(repository.list_projects()) == 501
    assert len(repository.list_grants()) == 501


def test_agent_project_grants_are_complete_past_500() -> None:
    repository = PostgresAgentAccessRepository(lambda: None)
    repository.project_owner = _PagedProjectOwner()
    assert len(repository.list_project_grants()) == 501


class _AllowAllAcl:
    def resolve(self, *, actor_type, actor_id, project_id, action, persist):
        return AccessDecisionRecord(
            f"decision-{project_id}", actor_type, actor_id, project_id, action,
            "viewer", True, "allow_grant", "viewer", "user", actor_id,
            "allowed", NOW,
        )


def test_session_projects_are_complete_past_500() -> None:
    repository = PostgresIdentityAccessRepository(lambda: None, _AllowAllAcl())
    owner = _PagedIdentityOwner()
    current = owner.users[0]
    repository.owner = owner
    repository.project_owner = _PagedProjectOwner()
    repository.team_owner = _PagedTeamOwner()
    session = repository.session_state(current)
    assert len(session.available_projects) == 501


class _IdentityOwnerCapture:
    def __init__(self) -> None:
        self.change_sets = []

    def get_user(self, _actor_id: str):
        return None

    def active_direct_admin_team_ids(self, _actor_ids):
        return ()

    def identity_session(self, change_set) -> None:
        self.change_sets.append(change_set)


def test_identity_adapter_publishes_typed_user_and_audit_change_set() -> None:
    repository = PostgresIdentityAccessRepository(lambda: None)
    owner = _IdentityOwnerCapture()
    repository.owner = owner
    user = UserRecord(
        "user-new", "New User", "new@example.test", "user", None,
        False, "user", NOW,
    )
    with repository.identity_mutation(
        "identity-email:new",
        authorization_actor_ids=("user-admin",),
        user_email="new@example.test",
    ):
        repository.put_user(user)
        event = repository.append_audit(IdentityAuditCommand(
            "user_invite_created", "user-admin", "user:user-new", None, None,
            'invite.user_invite_has_been_created', {"email": user.email},
        ))
        repository.persist()
    assert event.event_type == "user_invite_created"
    assert len(owner.change_sets) == 1
    change_set = owner.change_sets[0]
    assert change_set.users == (user,)
    assert change_set.expected_users == (("user-new", None),)
    assert change_set.audit_events == (event,)
    assert change_set.identity_lock_keys == ("identity-email:new",)
    assert change_set.authorization_actor_id == "user-admin"
    assert change_set.authorization_requires_system_admin is True


class _InviteLoserIdentityOwner(_IdentityOwnerCapture):
    def identity_session(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise IdentityCurrentnessConflict("pending invite already exists")


def test_same_email_adapter_loser_returns_durable_rejection_ref(
    monkeypatch,
) -> None:
    persisted = []

    def persist(_factory, *, candidate, message_code, reason):
        persisted.append((candidate, message_code, reason))
        return replace(candidate, event_id="audit-invite-loser-durable")

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_identity_adapter."
        "persist_rejection_audit",
        persist,
    )
    repository = PostgresIdentityAccessRepository(lambda: None)
    owner = _InviteLoserIdentityOwner()
    repository.owner = owner
    user = UserRecord(
        "user-loser", "Loser", "same@example.test", "user", None,
        False, "user", NOW,
    )
    caught = None
    with repository.identity_mutation(
        "identity-email:same@example.test",
        user_email="same@example.test",
    ):
        try:
            repository.put_user(user)
            repository.append_audit(IdentityAuditCommand(
                "user_invite_created", "user-admin", "user:user-loser",
                None, None, 'invite.user_invite_has_been_created',
                {"email": user.email},
            ))
        except Exception as exc:
            caught = exc
    assert caught is not None
    assert caught.audit_event_ref == "audit-invite-loser-durable"
    assert persisted
    candidate, message_code, reason = persisted[0]
    assert candidate.event_id != caught.audit_event_ref
    assert message_code == 'invite.already_pending_for_email'
    assert reason == "commit_time_pending_invite_conflict"


class _AgentIdentityCapture:
    def __init__(self, users=None) -> None:
        self.change_sets = []
        self.users = users or {}

    def get_user(self, actor_id: str):
        return self.users.get(actor_id)

    def identity_session(self, change_set) -> None:
        self.change_sets.append(change_set)


def test_agent_adapter_commits_service_account_and_audit_together() -> None:
    repository = PostgresAgentAccessRepository(lambda: None)
    owner = _AgentIdentityCapture()
    repository.identity_owner = owner
    agent = UserRecord(
        "agent-unit", "Unit Agent", None, "agent", None,
        True, "service_account", NOW,
    )
    repository.put_user(agent)
    event = repository.append_audit(AgentAuditCommand(
        "agent_user_created", "user-admin", "agent:agent-unit",
        'agent.user_is_ready_for_token_issue', {},
    ))
    assert owner.change_sets[0].users == (agent,)
    assert owner.change_sets[0].audit_events == (event,)


def test_agent_issue_carries_exact_active_target_preimage() -> None:
    agent = UserRecord(
        "agent-target", "Target", None, "agent", None,
        True, "service_account", NOW,
    )
    repository = PostgresAgentAccessRepository(lambda: None)
    owner = _AgentIdentityCapture({agent.actor_id: agent})
    repository.identity_owner = owner
    _raw, issued = repository.issue_token(agent.actor_id)
    event = repository.append_audit(AgentAuditCommand(
        "agent_token_issued", "user-admin", f"agent:{agent.actor_id}",
        'agent.token_has_been_issued_copy_it_now', {},
    ))
    change_set = owner.change_sets[0]
    assert change_set.agent_tokens == (issued,)
    assert change_set.expected_agent_users == ((agent.actor_id, agent),)
    assert change_set.audit_events == (event,)


class _TeamOwnerCapture:
    def __init__(self) -> None:
        self.change_sets = []

    def team_governance(self, change_set) -> None:
        self.change_sets.append(change_set)

    def get_team(self, _team_id: str):
        return None

    def get_membership(self, _membership_id: str):
        return None


def test_team_adapter_commits_hierarchy_mutation_and_audit_together() -> None:
    repository = PostgresTeamAccessRepository(lambda: None)
    owner = _TeamOwnerCapture()
    repository.owner = owner
    team = TeamRecord("team-unit", "Unit", None, "active", NOW)
    with repository.team_mutation(
        "team-unit",
        actor_ids=("user-admin",),
        include_hierarchy=True,
    ):
        repository.put_team(team)
        event = repository.append_audit(TeamAuditCommand(
            "team_created", "user-admin", "team:team-unit",
            'team.is_ready', {},
        ))
    assert owner.change_sets[0].teams == (team,)
    assert owner.change_sets[0].expected_teams == ((team.team_id, None),)
    assert owner.change_sets[0].audit_events == (event,)
    assert owner.change_sets[0].protect_hierarchy is True
    assert owner.change_sets[0].authorization_actor_ids == ("user-admin",)
    assert owner.change_sets[0].current_actor_ids == ("user-admin",)
    assert owner.change_sets[0].authorization_requires_system_admin is True


def test_team_adapter_separates_authorizing_actor_from_current_target() -> None:
    repository = PostgresTeamAccessRepository(lambda: None)
    owner = _TeamOwnerCapture()
    repository.owner = owner
    membership = TeamMembershipRecord(
        "tm-target",
        "team-unit",
        "user",
        "user-target",
        "active",
        NOW,
    )
    with repository.team_mutation(
        "team-unit",
        actor_ids=("user-admin", "user-target"),
    ):
        repository.put_membership(membership)
        repository.append_audit(
            TeamAuditCommand(
                "team_member_added",
                "user-admin",
                "team-membership:tm-target",
                "team.is_ready",
                {},
            )
        )

    assert owner.change_sets[0].authorization_actor_ids == ("user-admin",)
    assert owner.change_sets[0].current_actor_ids == (
        "user-admin",
        "user-target",
    )


class _ProjectOwnerCapture:
    def __init__(self) -> None:
        self.change_sets = []

    def project_acl(self, change_set) -> None:
        self.change_sets.append(change_set)

    def get_project(self, _project_id: str):
        return None

    def get_grant(self, _grant_id: str):
        return None


def test_project_adapter_commits_project_grant_and_audit_together() -> None:
    repository = PostgresProjectGovernanceRepository(lambda: None)
    owner = _ProjectOwnerCapture()
    repository.owner = owner
    project = ProjectRecord("project-unit", "Unit", "policy-default")
    grant = PermissionGrantRecord(
        "grant-unit", project.project_id, "user", "user-admin", "admin",
        "allow", "active", NOW,
    )
    repository.put_project(project)
    repository.put_grant(grant)
    event = repository.append_audit(ProjectAuditCommand(
        "project_created", "user-admin", "project:project-unit",
        project.project_id, 'project.is_ready_for_membership_setup', {},
    ))
    assert owner.change_sets[0].projects == (project,)
    assert owner.change_sets[0].grants == (grant,)
    assert owner.change_sets[0].expected_projects == ((project.project_id, None),)
    assert owner.change_sets[0].expected_grants == ((grant.grant_id, None),)
    assert owner.change_sets[0].audit_events == (event,)
    assert owner.change_sets[0].authorization_actor_id == "user-admin"
    assert owner.change_sets[0].authorization_requires_system_admin is True


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)



class _ExecutionResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return list(self.values)

class _AclSession:
    def __init__(
        self,
        *,
        actor: AtlasUserRow,
        role: str = "viewer",
        grants: list[AtlasPermissionGrantRow] | None = None,
        memberships: list[AtlasTeamMembershipRow] | None = None,
        teams: list[AtlasTeamRow] | None = None,
        projects: list[AtlasProjectRow] | None = None,
    ) -> None:
        self.actor = actor
        self.project = AtlasProjectRow(
            project_id="project-unit", name="Unit", policy_profile_id="policy-default"
        )
        self.team = AtlasTeamRow(
            team_id="team-unit", name="Unit", parent_team_id=None,
            status="active", created_at=NOW, inherit_parent_documents=True,
        )
        self.membership = AtlasTeamMembershipRow(
            membership_id="membership-unit", team_id="team-unit",
            member_actor_type=actor.actor_type, member_actor_id=actor.actor_id,
            role="member", status="active", created_at=NOW, removed_at=None,
        )
        self.grant = AtlasPermissionGrantRow(
            grant_id="grant-unit", project_id="project-unit",
            subject_type="team", subject_id="team-unit", role=role,
            effect="allow", status="active", created_at=NOW, revoked_at=None,
        )
        self.grants = grants if grants is not None else [self.grant]
        self.memberships = memberships if memberships is not None else [self.membership]
        self.teams = teams if teams is not None else [self.team]
        self.projects = projects if projects is not None else [self.project]
        self.connection_options: list[dict[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def connection(self, *, execution_options):
        self.connection_options.append(execution_options)

    def get(self, row_type, key):
        if row_type is AtlasUserRow and key == self.actor.actor_id:
            return self.actor
        if row_type is AtlasProjectRow:
            return next((item for item in self.projects if item.project_id == key), None)
        if row_type is AtlasTeamRow:
            return next((item for item in self.teams if item.team_id == key), None)
        return None

    def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        selected_name = statement.column_descriptions[0].get("name")
        if entity is AtlasPermissionGrantRow:
            return _Rows(self.grants)
        if entity is AtlasTeamMembershipRow:
            return _Rows(self.memberships)
        if entity is AtlasProjectRow:
            return _Rows(self.projects)
        if entity is AtlasTeamRow:
            if selected_name == "team_id":
                return _Rows(team.team_id for team in self.teams)
            return _Rows(self.teams)
        raise AssertionError(f"unexpected scalar entity: {entity}")

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is AtlasPermissionGrantRow:
            return _ExecutionResult(self.grants)
        if entity is AtlasTeamMembershipRow:
            return _ExecutionResult(self.memberships)
        if entity is AtlasProjectRow:
            return _ExecutionResult(self.projects)
        if entity is AtlasTeamRow:
            return _ExecutionResult(self.teams)
        raise AssertionError(f"unexpected execute entity: {entity}")

    def rollback(self):
        return None


def _actor(
    *,
    active: bool = True,
    system_role: str = "user",
) -> AtlasUserRow:
    return AtlasUserRow(
        actor_id="user-unit", display_name="Unit User",
        email="unit@example.test", system_role=system_role,
        password_digest=None, active=active, actor_type="user", created_at=NOW,
    )


def test_action_aware_acl_uses_requested_role_and_fails_closed() -> None:
    viewer_session = _AclSession(actor=_actor(), role="viewer")
    authority = ActionAwareAclAuthority(lambda: viewer_session)
    assert authority.resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="workspace_query", persist=False,
    ).allowed is True
    register = authority.resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="document_register", persist=False,
    )
    assert register.allowed is False
    assert register.required_role == "contributor"
    assert register.reason == "missing_required_role"
    with pytest.raises(ValueError, match="unknown ACL action"):
        authority.resolve(
            actor_type="user", actor_id="user-unit", project_id="project-unit",
            action="unknown_action", persist=False,
        )


def test_action_aware_acl_rejects_inactive_current_actor() -> None:
    authority = ActionAwareAclAuthority(
        lambda: _AclSession(actor=_actor(active=False), role="admin")
    )
    decision = authority.resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="permission_manage", persist=False,
    )
    assert decision.allowed is False
    assert decision.reason == "actor_inactive_or_missing"


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        ("viewer", "read_original", True),
        ("viewer", "read_citation", True),
        ("viewer", "copy_citation", True),
        ("viewer", "read_derived", True),
        ("viewer", "preview", True),
        ("viewer", "agent_query", True),
        ("viewer", "document_register", False),
        ("contributor", "document_register", True),
        ("contributor", "ingestion_run", True),
        ("contributor", "membership_manage", False),
        ("admin", "membership_manage", True),
        ("admin", "permission_manage", True),
    ],
)
def test_action_aware_acl_covers_every_route_action_role(
    role: str,
    action: str,
    allowed: bool,
) -> None:
    authority = ActionAwareAclAuthority(
        lambda: _AclSession(actor=_actor(), role=role)
    )
    assert authority.resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action=action, persist=False,
    ).allowed is allowed


def _grant(
    grant_id: str,
    *,
    subject_type: str,
    subject_id: str,
    role: str,
    effect: str,
) -> AtlasPermissionGrantRow:
    return AtlasPermissionGrantRow(
        grant_id=grant_id, project_id="project-unit",
        subject_type=subject_type, subject_id=subject_id, role=role,
        effect=effect, status="active", created_at=NOW, revoked_at=None,
    )


def _membership(
    team_id: str,
    *,
    role: str = "member",
) -> AtlasTeamMembershipRow:
    return AtlasTeamMembershipRow(
        membership_id=f"membership-{team_id}", team_id=team_id,
        member_actor_type="user", member_actor_id="user-unit",
        role=role, status="active", created_at=NOW, removed_at=None,
    )


def _team(
    team_id: str,
    parent: str | None,
    *,
    inherit: bool = True,
    status: str = "active",
) -> AtlasTeamRow:
    return AtlasTeamRow(
        team_id=team_id, name=team_id, parent_team_id=parent,
        status=status, created_at=NOW, inherit_parent_documents=inherit,
    )


def test_direct_actor_tier_beats_team_deny_and_same_tier_deny_wins() -> None:
    direct_allow = _grant(
        "grant-direct", subject_type="user", subject_id="user-unit",
        role="viewer", effect="allow",
    )
    team_deny = _grant(
        "grant-team-deny", subject_type="team", subject_id="team-unit",
        role="admin", effect="deny",
    )
    direct = ActionAwareAclAuthority(lambda: _AclSession(
        actor=_actor(), grants=[direct_allow, team_deny],
    )).resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="workspace_query", persist=False,
    )
    assert direct.allowed is True
    same_tier = ActionAwareAclAuthority(lambda: _AclSession(
        actor=_actor(), grants=[
            _grant(
                "grant-team-allow", subject_type="team", subject_id="team-unit",
                role="admin", effect="allow",
            ),
            team_deny,
        ],
    )).resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="workspace_query", persist=False,
    )
    assert same_tier.allowed is False
    assert same_tier.reason == "deny_grant"


def test_nearest_team_tier_beats_parent_deny() -> None:
    child = _team("team-child", "team-parent")
    parent = _team("team-parent", None)
    decision = ActionAwareAclAuthority(lambda: _AclSession(
        actor=_actor(),
        teams=[child, parent],
        memberships=[_membership("team-child")],
        grants=[
            _grant(
                "grant-child", subject_type="team", subject_id="team-child",
                role="viewer", effect="allow",
            ),
            _grant(
                "grant-parent", subject_type="team", subject_id="team-parent",
                role="admin", effect="deny",
            ),
        ],
    )).resolve(
        actor_type="user", actor_id="user-unit", project_id="project-unit",
        action="workspace_query", persist=False,
    )
    assert decision.allowed is True
    assert decision.source_id == "grant-child"


def test_project_acl_keeps_active_child_tier_when_ancestor_is_retired() -> None:
    decision = ActionAwareAclAuthority(lambda: _AclSession(
        actor=_actor(),
        teams=[
            _team("team-child", "team-parent"),
            _team("team-parent", None, status="retired"),
        ],
        memberships=[_membership("team-child")],
        grants=[
            _grant(
                "grant-child",
                subject_type="team",
                subject_id="team-child",
                role="viewer",
                effect="allow",
            ),
        ],
    )).resolve(
        actor_type="user",
        actor_id="user-unit",
        project_id="project-unit",
        action="workspace_query",
        persist=False,
    )

    assert decision.allowed is True
    assert decision.source_id == "grant-child"

@pytest.mark.parametrize("role", ["viewer", "contributor", "admin"])
def test_project_notes_membership_projects_every_project_role_equally(role: str) -> None:
    authority = PostgresNotesMembershipAuthority(
        lambda: _AclSession(actor=_actor(), role=role)
    )

    snapshot = authority.current_project_notes_membership(
        actor_type="user",
        actor_id="user-unit",
        project_id="project-unit",
    )

    assert snapshot.member is True
    assert snapshot.system_admin is False
    assert snapshot.reason == "member"


def test_project_notes_membership_preserves_same_tier_deny_and_human_boundary() -> None:
    deny_session = _AclSession(
        actor=_actor(),
        grants=[
            _grant("grant-allow", subject_type="team", subject_id="team-unit", role="viewer", effect="allow"),
            _grant("grant-deny", subject_type="team", subject_id="team-unit", role="admin", effect="deny"),
        ],
    )
    authority = PostgresNotesMembershipAuthority(lambda: deny_session)

    assert authority.current_project_notes_membership(
        actor_type="user", actor_id="user-unit", project_id="project-unit"
    ).reason == "deny_grant"
    assert authority.current_project_notes_membership(
        actor_type="service_account", actor_id="user-unit", project_id="project-unit"
    ).reason == "actor_not_human"


@pytest.mark.parametrize("role", ["member", "uploader", "admin"])
@pytest.mark.parametrize(
    ("inherit_parent_documents", "parent_member"),
    [(True, True), (False, False)],
)
def test_team_notes_membership_obeys_inheritance_flag_for_every_role(
    role: str,
    inherit_parent_documents: bool,
    parent_member: bool,
) -> None:
    child = _team(
        "team-child",
        "team-parent",
        inherit=inherit_parent_documents,
    )
    parent = _team("team-parent", None)
    session = _AclSession(
        actor=_actor(),
        teams=[child, parent],
        memberships=[_membership("team-child", role=role)],
        grants=[],
    )
    authority = PostgresNotesMembershipAuthority(lambda: session)

    parent_snapshot = authority.current_team_notes_membership(
        actor_type="user",
        actor_id="user-unit",
        team_id="team-parent",
    )
    child_snapshot = authority.current_team_notes_membership(
        actor_type="user",
        actor_id="user-unit",
        team_id="team-child",
    )

    assert parent_snapshot.member is parent_member
    assert parent_snapshot.reason == (
        "member" if parent_member else "missing_membership"
    )
    assert child_snapshot.member is True
    assert child_snapshot.reason == "member"


def test_notes_membership_admin_bypass_and_invalid_team_hierarchy_fail_closed() -> None:
    admin_session = _AclSession(
        actor=_actor(system_role="admin"),
        teams=[_team("team-parent", None)],
        memberships=[],
        grants=[],
    )
    assert PostgresNotesMembershipAuthority(
        lambda: admin_session
    ).current_team_notes_membership(
        actor_type="user", actor_id="user-unit", team_id="team-parent"
    ).system_admin is True

    cycle_session = _AclSession(
        actor=_actor(),
        teams=[_team("team-child", "team-parent"), _team("team-parent", "team-child")],
        memberships=[_membership("team-child")],
        grants=[],
    )
    cycle = PostgresNotesMembershipAuthority(
        lambda: cycle_session
    ).current_team_notes_membership(
        actor_type="user", actor_id="user-unit", team_id="team-parent"
    )
    assert cycle.member is False
    assert cycle.reason == "invalid_hierarchy"


def test_team_document_inheritance_off_excludes_parent_scope() -> None:
    child = _team("team-child", "team-parent", inherit=False)
    parent = _team("team-parent", None)
    authority = ActionAwareAclAuthority(lambda: _AclSession(
        actor=_actor(), teams=[child, parent],
        memberships=[_membership("team-child")], grants=[], projects=[],
    ))
    assert authority.effective_document_scope(
        actor_type="user", actor_id="user-unit", action="read_original",
    ) == {("team", "team-child")}


def test_workspace_scope_labels_include_empty_admin_projects_and_teams() -> None:
    project = AtlasProjectRow(
        project_id="project-empty",
        name="New Project",
        policy_profile_id="policy-default",
    )
    team = _team("team-empty", None)
    session = _AclSession(
        actor=_actor(system_role="admin"),
        teams=[team],
        memberships=[],
        grants=[],
        projects=[project],
    )
    authority = ActionAwareAclAuthority(lambda: session)

    assert authority.effective_document_scope_labels(
        actor_type="user",
        actor_id="user-unit",
        action="workspace_query",
    ) == [
        ("project", "project-empty", "New Project"),
        ("team", "team-empty", team.name),
    ]
    assert session.connection_options == [
        {"isolation_level": "REPEATABLE READ"}
    ]


def _resolved_owner_scope(
    session: _AclSession,
    owner_scope: tuple[str, str] | None,
) -> tuple[set[tuple[str, str]], set[str], bool]:
    requested_scope = (
        {owner_scope}
        if owner_scope is not None
        else {("project", "project-unit"), ("team", "team-unit")}
    )
    return read_effective_document_scope_with_team_ids(
        session,
        actor_type="user",
        actor_id="user-unit",
        requested_scope=requested_scope,
        owner_scope_type=owner_scope[0] if owner_scope else None,
        owner_scope_id=owner_scope[1] if owner_scope else None,
    )


def test_scope_resolver_generic_caller_never_derives_owner_authority() -> None:
    session = _AclSession(actor=_actor(), role="admin")
    session.memberships = [_membership("team-unit", role="admin")]

    scope, team_ids, can_administer = _resolved_owner_scope(session, None)

    assert scope == {("project", "project-unit"), ("team", "team-unit")}
    assert team_ids == {"team-unit"}
    assert can_administer is False


@pytest.mark.parametrize(
    ("owner_scope", "membership_role", "project_role", "expected"),
    (
        (("team", "team-unit"), "admin", "viewer", True),
        (("team", "team-unit"), "member", "admin", False),
        (("team", "team-other"), "admin", "admin", False),
        (("project", "project-unit"), "member", "admin", True),
        (("project", "project-unit"), "member", "contributor", False),
        (("project", "project-other"), "admin", "admin", False),
    ),
)
def test_scope_resolver_derives_only_exact_owner_scope_administration(
    owner_scope: tuple[str, str],
    membership_role: str,
    project_role: str,
    expected: bool,
) -> None:
    session = _AclSession(actor=_actor(), role=project_role)
    session.memberships = [
        _membership("team-unit", role=membership_role)
    ]

    assert _resolved_owner_scope(session, owner_scope)[2] is expected


def test_scope_resolver_rejects_inherited_parent_team_as_admin_authority() -> None:
    child = _team("team-child", "team-parent")
    parent = _team("team-parent", None)
    session = _AclSession(
        actor=_actor(),
        teams=[child, parent],
        memberships=[
            _membership("team-child", role="admin")
        ],
        grants=[],
        projects=[],
    )

    scope, _, can_administer = _resolved_owner_scope(
        session, ("team", "team-parent")
    )

    assert scope == {("team", "team-parent")}
    assert can_administer is False


def test_scope_resolver_preserves_project_same_tier_deny_precedence() -> None:
    session = _AclSession(
        actor=_actor(),
        grants=[
            _grant(
                "grant-allow",
                subject_type="team",
                subject_id="team-unit",
                role="admin",
                effect="allow",
            ),
            _grant(
                "grant-deny",
                subject_type="team",
                subject_id="team-unit",
                role="admin",
                effect="deny",
            ),
        ],
    )

    assert _resolved_owner_scope(
        session, ("project", "project-unit")
    )[2] is False


@pytest.mark.parametrize(
    ("actor", "expected_scope", "expected"),
    (
        (_actor(system_role="admin"), {("project", "project-unit")}, True),
        (_actor(active=False), set(), False),
    ),
)
def test_scope_resolver_handles_system_admin_and_inactive_actor(
    actor: AtlasUserRow,
    expected_scope: set[tuple[str, str]],
    expected: bool,
) -> None:
    session = _AclSession(actor=actor)

    scope, _, can_administer = _resolved_owner_scope(
        session, ("project", "project-unit")
    )

    assert scope == expected_scope
    assert can_administer is expected


def test_scope_resolver_rejects_cross_wired_owner_arguments() -> None:
    session = _AclSession(actor=_actor())

    with pytest.raises(ValueError, match="provided together"):
        read_effective_document_scope_with_team_ids(
            session,
            actor_type="user",
            actor_id="user-unit",
            owner_scope_type="team",
        )


def test_owner_commit_revalidates_revoked_system_admin_authority() -> None:
    session = _AclSession(actor=_actor(), grants=[])
    with pytest.raises(IdentityAuthorizationConflict):
        IdentityRepository._validate_authorization(
            session,
            IdentitySessionChangeSet(
                authorization_actor_id="user-unit",
                authorization_requires_system_admin=True,
            ),
        )
    with pytest.raises(TeamAuthorizationConflict):
        TeamRepository._validate_authorization(
            session,
            TeamGovernanceChangeSet(
                authorization_actor_ids=("user-unit",),
                authorization_requires_system_admin=True,
            ),
        )
    with pytest.raises(ProjectAuthorizationConflict):
        ProjectAclRepository._validate_authorization(
            session,
            ProjectAclChangeSet(
                authorization_actor_id="user-unit",
                authorization_requires_system_admin=True,
            ),
        )


def test_team_owner_rejects_inactive_current_target() -> None:
    session = _AclSession(actor=_actor(active=False))

    with pytest.raises(TeamCurrentnessConflict, match="actor currentness"):
        TeamRepository._validate_actor_currentness(
            session,
            TeamGovernanceChangeSet(
                current_actor_ids=("user-unit",),
            ),
        )


class _PrincipalIdentity:
    def __init__(self, actors: dict[str, UserRecord | None]) -> None:
        self.actors = actors

    def actor_for_token(self, token: str | None):
        return self.actors.get(token or "")

    def is_system_admin(self, actor: UserRecord) -> bool:
        return actor.active and actor.actor_type == "user" and actor.system_role == "admin"


def test_current_browser_principal_matrix_is_fail_closed() -> None:
    active = UserRecord(
        "user-active", "Active", "active@example.test", "admin", None,
        True, "user", NOW,
    )
    principal = PostgresCurrentPrincipal(_PrincipalIdentity({
        "active": active,
        "inactive": None,
        "wrong-type": None,
        "revoked": None,
    }))
    assert principal.current_user(None) is None
    assert principal.current_user("unknown") is None
    assert principal.current_user("inactive") is None
    assert principal.current_user("wrong-type") is None
    assert principal.current_user("revoked") is None
    assert principal.current_user("active") == active
    assert principal.is_admin("active") is True


class _AgentAuthoritySession(_AclSession):
    def __init__(
        self,
        *,
        token_status: str = "active",
        actor_active: bool = True,
        actor_type: str = "service_account",
        token_known: bool = True,
    ) -> None:
        actor = AtlasUserRow(
            actor_id="agent-unit", display_name="Unit Agent", email=None,
            system_role="agent", password_digest=None, active=actor_active,
            actor_type=actor_type, created_at=NOW,
        )
        grant = _grant(
            "grant-agent", subject_type="service_account",
            subject_id="agent-unit", role="viewer", effect="allow",
        )
        super().__init__(actor=actor, grants=[grant], memberships=[], teams=[])
        raw = "atlas_agent_unit_raw"
        digest = agent_token_digest(raw)
        self.raw = raw
        self.token_known = token_known
        self.token_row = AtlasAgentTokenRow(
            token_id="token-agent-unit", actor_id="agent-unit",
            token_digest=digest, token_fingerprint=digest[:12],
            status=token_status, created_at=NOW,
            revoked_at=NOW if token_status != "active" else None,
        )
        self.scalar_calls = 0
        self.added = []
        self.commits = 0

    def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return self.token_row
        if self.scalar_calls in {2, 3}:
            return self.actor
        if self.scalar_calls == 4:
            return self.project
        raise AssertionError("unexpected scalar call")

    def execute(self, _statement, _parameters=None):
        return None

    def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is AtlasAgentTokenRow:
            return _Rows([self.token_row] if self.token_known else [])
        return super().scalars(statement)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"token_known": False}, "invalid_token"),
        ({"actor_active": False}, "invalid_agent"),
        ({"actor_type": "user"}, "invalid_agent"),
        ({"token_status": "revoked"}, "revoked"),
        ({}, "allowed"),
    ],
)
def test_raw_agent_principal_matrix(kwargs: dict[str, object], expected: str) -> None:
    session = _AgentAuthoritySession(**kwargs)
    result = PostgresAgentQueryAuthority(lambda: session).authorize(
        raw_token=session.raw,
        project_id="project-unit",
    )
    assert result.status == expected
    if expected == "allowed":
        assert result.actor_id == session.actor.actor_id
        assert result.token_fingerprint
        assert result.access_decision_id
        assert session.commits == 1
        assert session.scalar_calls == 4
        assert session.added
    else:
        assert session.commits == 0


def test_raw_agent_missing_token_does_not_open_database() -> None:
    opened = False

    def factory():
        nonlocal opened
        opened = True
        raise AssertionError("missing token must fail before SQL")

    result = PostgresAgentQueryAuthority(factory).authorize(
        raw_token=None,
        project_id="project-unit",
    )
    assert result.status == "invalid_token"
    assert opened is False


def test_duplicate_raw_agent_digest_fails_closed() -> None:
    session = _AgentAuthoritySession()
    original_scalars = session.scalars

    def duplicate_tokens(statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is AtlasAgentTokenRow:
            return _Rows([session.token_row, session.token_row])
        return original_scalars(statement)

    session.scalars = duplicate_tokens
    result = PostgresAgentQueryAuthority(lambda: session).authorize(
        raw_token=session.raw,
        project_id="project-unit",
    )
    assert result.status == "invalid_token"
    assert session.scalar_calls == 0


class _FailingIdentityOwner(_IdentityOwnerCapture):
    def identity_session(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise RuntimeError("audit write failed")


def test_identity_audit_failure_discards_typed_staging() -> None:
    repository = PostgresIdentityAccessRepository(lambda: None)
    repository.owner = _FailingIdentityOwner()
    user = UserRecord(
        "user-failed", "Failed", "failed@example.test", "user", None,
        False, "user", NOW,
    )
    with pytest.raises(RuntimeError, match="audit write failed"):
        with repository.identity_mutation("identity:user-failed"):
            repository.put_user(user)
            repository.append_audit(IdentityAuditCommand(
                "user_lifecycle_updated", "user-admin", "user:user-failed",
                None, None, 'processing.user_profile_is_updated', {},
            ))
    with pytest.raises(RuntimeError, match="identity_mutation context"):
        repository.put_user(user)


class _FailingAgentOwner(_AgentIdentityCapture):
    def identity_session(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise RuntimeError("audit write failed")


def test_agent_audit_failure_discards_typed_staging() -> None:
    repository = PostgresAgentAccessRepository(lambda: None)
    repository.identity_owner = _FailingAgentOwner()
    agent = UserRecord(
        "agent-failed", "Failed", None, "agent", None,
        True, "service_account", NOW,
    )
    repository.put_user(agent)
    with pytest.raises(RuntimeError, match="audit write failed"):
        repository.append_audit(AgentAuditCommand(
            "agent_user_created", "user-admin", "agent:agent-failed",
            'agent.user_is_ready_for_token_issue', {},
        ))
    assert repository._buffer.get() is None


class _FailingTeamOwner(_TeamOwnerCapture):
    def team_governance(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise RuntimeError("audit write failed")


def test_team_audit_failure_discards_typed_staging() -> None:
    repository = PostgresTeamAccessRepository(lambda: None)
    repository.owner = _FailingTeamOwner()
    with pytest.raises(RuntimeError, match="audit write failed"):
        with repository.team_mutation("team-failed"):
            repository.put_team(TeamRecord(
                "team-failed", "Failed", None, "active", NOW,
            ))
            repository.append_audit(TeamAuditCommand(
                "team_created", "user-admin", "team:team-failed",
                'team.is_ready', {},
            ))
    assert repository._buffer.get() is None


class _InvariantTeamOwner(_TeamOwnerCapture):
    def team_governance(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise TeamInvariantViolation(
            "at least one active direct human Team Admin is required"
        )


def test_commit_time_rejection_returns_only_durable_rejection_ref(
    monkeypatch,
) -> None:
    persisted = []

    def persist(_factory, *, candidate, message_code, reason):
        persisted.append((candidate, message_code, reason))
        return replace(candidate, event_id="audit-durable-rejection")

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_team_adapter."
        "persist_rejection_audit",
        persist,
    )
    repository = PostgresTeamAccessRepository(lambda: None)
    repository.owner = _InvariantTeamOwner()
    with pytest.raises(Exception) as caught:
        with repository.team_mutation("team-unit"):
            membership = TeamMembershipRecord(
                "tm-unit", "team-unit", "user", "user-unit", "removed", NOW,
                role="admin", removed_at=NOW,
            )
            repository.put_membership(membership)
            repository.append_audit(TeamAuditCommand(
                "team_member_removed", "user-admin", "team-membership:tm-unit",
                'team.member_has_been_removed', {}, "team", "team-unit",
            ))
    assert caught.value.audit_event_ref == "audit-durable-rejection"
    assert persisted and persisted[0][0].event_id != caught.value.audit_event_ref


class _FailingProjectOwner(_ProjectOwnerCapture):
    def project_acl(self, change_set) -> None:
        self.change_sets.append(change_set)
        raise RuntimeError("audit write failed")


def test_project_audit_failure_discards_typed_staging() -> None:
    repository = PostgresProjectGovernanceRepository(lambda: None)
    repository.owner = _FailingProjectOwner()
    repository.put_project(ProjectRecord(
        "project-failed", "Failed", "policy-default",
    ))
    with pytest.raises(RuntimeError, match="audit write failed"):
        repository.append_audit(ProjectAuditCommand(
            "project_created", "user-admin", "project:project-failed",
            "project-failed", 'project.is_ready_for_membership_setup', {},
        ))
    assert repository._buffer.get() is None


class _RevokeSession:
    def __init__(self) -> None:
        self.row = AtlasSessionRow(session_token="session-unit", actor_id="user-unit")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _statement, _parameters=None):
        return None

    def scalar(self, _statement):
        return self.row

    def delete(self, row):
        assert row is self.row
        self.row = None

    def commit(self):
        return None

    def rollback(self):
        return None


def test_browser_session_revoke_is_repeatable_and_fail_closed() -> None:
    session = _RevokeSession()
    command = RevokeBrowserSessionCommand(lambda: session)
    assert command.execute("session-unit") is True
    assert command.execute("session-unit") is False
    assert command.execute(None) is False


class _AuditOwner:
    def __init__(self) -> None:
        self.limit = None

    def recent_events(self, *, limit: int = 50):
        self.limit = limit
        return []


def test_audit_consumer_is_bounded_evidence_only() -> None:
    owner = _AuditOwner()
    adapter = PostgresAuditConsumerAdapter(owner)
    assert adapter.recent_events(limit=50) == []
    assert owner.limit == 50
    assert set(adapter.__class__.__dict__) >= {"recent_events"}
    assert not hasattr(adapter, "authorize")
    assert tuple(inspect.signature(
        PostgresReadAuditWriter.append_read_audit
    ).parameters) == (
        "self", "event_type", "actor_id", "target_ref", "message_code",
        "message_params", "project_id", "scope_type", "scope_id",
        "document_id", "metadata",
    )


def test_adapter_sources_have_no_store_or_reflection_escape_hatch() -> None:
    sources = "\n".join(
        inspect.getsource(adapter)
        for adapter in (
            PostgresIdentityAccessRepository,
            PostgresInviteScopeGrantAdapter,
            PostgresAgentAccessRepository,
            PostgresTeamAccessRepository,
            PostgresProjectGovernanceRepository,
            ActionAwareAclAuthority,
        )
    )
    for forbidden in (
        "Atlas" + "Store",
        "getattr(",
        "hasattr(",
        "__dataclass_fields__",
        "generic_repository",
    ):
        assert forbidden not in sources
