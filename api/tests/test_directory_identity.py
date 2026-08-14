from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher
from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.ldap_directory_gateway import validate_directory_filter
from atlas_production.modules.identity_access.api_models import (
    DirectoryConnectionCreateRequest,
    DirectoryUserImportRequest,
    LoginRequest,
    ScopedDirectoryUserSearchRequest,
    TeamDirectoryMemberImportRequest,
)
from atlas_production.modules.identity_access.contracts import IdentityAccessError
from atlas_production.modules.identity_access.directory_records import (
    DirectoryGatewayError,
    DirectoryPrincipal,
)
from atlas_production.modules.identity_access.directory_service import (
    DirectoryIdentityService,
)
from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.identity_access.security import password_digest
from atlas_production.modules.identity_access.service import IdentityAccessService
from atlas_production.modules.identity_access.team_contracts import TeamAccessError
from atlas_production.modules.identity_access.team_service import TeamAccessService
from atlas_production.modules.project_governance.api_models import (
    ProjectDirectoryMemberImportRequest,
)
from atlas_production.modules.project_governance.contracts import (
    ProjectGovernanceError,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.modules.project_governance.service import (
    ProjectGovernanceService,
)
from atlas_production.shared.public import (
    AuditEventRecord,
    utc_now_iso,
)
from atlas_production.modules.identity_access.api_models import SessionState


class FakeRepository:
    def __init__(self) -> None:
        self.users = {
            "admin": UserRecord(
                "admin",
                "Admin",
                "admin@example.test",
                "admin",
                password_digest("local-password"),
                created_at=utc_now_iso(),
            ),
            "local": UserRecord(
                "local",
                "Local User",
                "local@example.test",
                "user",
                password_digest("local-password"),
                created_at=utc_now_iso(),
            ),
        }
        self.connections = {}
        self.secrets = {}
        self.identities = {}
        self.sessions = {}
        self.audits: list[AuditEventRecord] = []
        self.team_memberships = {"local": ("team-a",)}
        self.project_grants = {"local": ("project-a",)}
        self.acl_decisions = {"local": True}

    @contextmanager
    def identity_mutation(self, owner_key, **kwargs):
        with self.directory_mutation(owner_key, **kwargs):
            yield

    @contextmanager
    def directory_mutation(
        self,
        _owner_key,
        *,
        authorization_actor_ids=(),
        **_kwargs,
    ):
        snapshot = deepcopy(
            (
                self.users,
                self.connections,
                self.secrets,
                self.identities,
                self.sessions,
                self.audits,
            )
        )
        if authorization_actor_ids:
            actor = self.users.get(authorization_actor_ids[0])
            if not self.is_system_admin(actor):
                raise IdentityAccessError(
                    "access_denied", "permission.admin_permission_is_required", 403
                )
        try:
            yield
        except Exception:
            (
                self.users,
                self.connections,
                self.secrets,
                self.identities,
                self.sessions,
                self.audits,
            ) = snapshot
            raise

    def actor_for_token(self, token):
        actor_id = self.sessions.get(token)
        return self.users.get(actor_id) if actor_id else None

    def session_state(self, user):
        return SessionState(
            authenticated=True,
            actor={
                "actor_id": user.actor_id,
                "actor_type": user.actor_type,
                "issuer": "atlas-local",
                "display_name": user.display_name,
                "groups": [],
                "correlation_id": "test",
            },
            available_projects=[],
            system_role=user.system_role,
        )

    def user_by_email(self, email):
        matches = [u for u in self.users.values() if u.email == email]
        return deepcopy(matches[0]) if len(matches) == 1 else None

    def get_user(self, actor_id):
        value = self.users.get(actor_id)
        return deepcopy(value) if value else None

    def list_users(self, *, limit=500, after_actor_id=None):
        values = sorted(self.users.values(), key=lambda item: item.actor_id)
        if after_actor_id is not None:
            values = [item for item in values if item.actor_id > after_actor_id]
        return deepcopy(values[:limit])

    def put_user(self, user):
        self.users[user.actor_id] = deepcopy(user)

    def issue_session(self, actor_id):
        return self.stage_session(actor_id)

    def stage_session(self, actor_id):
        token = f"session-{len(self.sessions) + 1}"
        self.sessions[token] = actor_id
        return token

    def revoke_session(self, token):
        return self.sessions.pop(token, None) is not None

    def invite_for_token(self, _token):
        return None

    def pending_invite_for_email(self, _email):
        return None

    def get_invite(self, _invite_id):
        return None

    def list_invites(self):
        return []

    def put_invite(self, _invite):
        raise AssertionError("not used")

    def is_system_admin(self, actor):
        return bool(actor and actor.active and actor.system_role == "admin")

    def active_admin_count(self):
        return 1

    def append_audit(self, command):
        event = AuditEventRecord(
            event_id=f"audit-{len(self.audits) + 1}",
            event_type=command.event_type,
            actor_id=command.actor_id,
            target_ref=command.target_ref,
            project_id=None,
            message_code=command.message_code,
            metadata=command.metadata,
            created_at=utc_now_iso(),
            message_params=command.message_params,
            scope_type=command.scope_type,
            scope_id=command.scope_id,
        )
        self.audits.append(event)
        return event

    def persist(self):
        return None

    def list_directory_connections(self):
        return deepcopy(
            sorted(
                self.connections.values(),
                key=lambda item: (item.priority, item.connection_id),
            )
        )

    def get_directory_connection(self, connection_id):
        return deepcopy(self.connections.get(connection_id))

    def put_directory_connection(self, connection):
        self.connections[connection.connection_id] = deepcopy(connection)

    def expect_directory_connection(self, connection):
        if self.connections.get(connection.connection_id) != connection:
            raise IdentityAccessError(
                "directory_conflict", "directory.concurrent_change", 409
            )

    def get_directory_secret(self, connection_id, secret_kind):
        return deepcopy(self.secrets.get((connection_id, secret_kind)))

    def put_directory_secret(self, secret):
        self.secrets[(secret.connection_id, secret.secret_kind)] = deepcopy(secret)

    def expect_directory_secret(self, secret):
        if self.secrets.get((secret.connection_id, secret.secret_kind)) != secret:
            raise IdentityAccessError(
                "directory_conflict", "directory.concurrent_change", 409
            )

    def delete_directory_secret(self, connection_id, secret_kind):
        self.secrets.pop((connection_id, secret_kind), None)

    def get_external_identity(self, actor_id):
        return deepcopy(self.identities.get(actor_id))

    def get_external_identity_by_subject(self, connection_id, external_subject):
        matches = [
            identity
            for identity in self.identities.values()
            if identity.connection_id == connection_id
            and identity.external_subject == external_subject
        ]
        return deepcopy(matches[0]) if len(matches) == 1 else None

    def list_external_identities(self):
        return deepcopy(list(self.identities.values()))

    def put_external_identity(self, identity):
        self.identities[identity.actor_id] = deepcopy(identity)

    def expect_external_identity(self, identity):
        if self.identities.get(identity.actor_id) != identity:
            raise IdentityAccessError(
                "directory_conflict", "directory.concurrent_change", 409
            )


class FakeGateway:
    def __init__(self) -> None:
        self.principals = {
            "subject-ada": DirectoryPrincipal(
                "subject-ada",
                "ada",
                "Ada Lovelace",
                "ada@example.test",
                ("Research",),
                "Engineering",
                "Programmer",
                "E-100",
                True,
            ),
            "subject-grace": DirectoryPrincipal(
                "subject-grace",
                "grace",
                "Grace Hopper",
                "grace@example.test",
                ("Compiler",),
                "Engineering",
                "Admiral",
                "E-101",
                True,
            ),
        }
        self.outages: set[str] = set()
        self.authentication_calls: list[tuple[str, str]] = []
        self.on_authenticate = lambda: None

    def test_connection(self, connection, bind_password):
        assert bind_password == "bind-secret"
        if connection.connection_id in self.outages:
            raise DirectoryGatewayError("directory_unavailable")

    def search_users(
        self,
        connection,
        bind_password,
        *,
        query,
        department,
        limit,
    ):
        self.test_connection(connection, bind_password)
        if department is not None:
            normalized = department.casefold()
            return tuple(
                item
                for item in self.principals.values()
                if (item.department or "").casefold() == normalized
            )[:limit]
        normalized = (query or "").casefold()
        return tuple(
            item
            for item in self.principals.values()
            if normalized in item.username.casefold()
            or normalized in item.display_name.casefold()
            or normalized in (item.email or "").casefold()
        )[:limit]

    def fetch_user(self, connection, bind_password, external_subject):
        self.test_connection(connection, bind_password)
        return self.principals.get(external_subject)

    def authenticate(
        self, connection, bind_password, external_subject, password
    ):
        self.test_connection(connection, bind_password)
        self.authentication_calls.append((connection.connection_id, password))
        self.on_authenticate()
        if password != "directory-password":
            raise DirectoryGatewayError("invalid_credentials")
        principal = self.principals.get(external_subject)
        if principal is None:
            raise DirectoryGatewayError("invalid_credentials")
        return principal


def create_payload(connection_id="main", priority=10):
    return DirectoryConnectionCreateRequest(
        connection_id=connection_id,
        display_name=connection_id.title(),
        priority=priority,
        provider_type="ldap",
        host="ldap.example.test",
        port=636,
        tls_mode="ldaps",
        connect_timeout_seconds=3,
        operation_timeout_seconds=4,
        bind_dn="cn=bind,dc=example,dc=test",
        user_base_dn="ou=people,dc=example,dc=test",
        user_object_filter="(objectClass=person)",
        login_attribute="uid",
        stable_id_attribute="entryUUID",
        display_name_attribute="cn",
        email_attribute="mail",
        groups_attribute="memberOf",
        department_attribute="department",
        title_attribute="title",
        employee_id_attribute="employeeNumber",
        enabled=True,
        bind_password="bind-secret",
    )


def service_fixture():
    repository = FakeRepository()
    gateway = FakeGateway()
    cipher = AesGcmEnvelopeCipher(key=b"k" * 32, key_id="test-key")
    return (
        DirectoryIdentityService(
            repository,
            gateway,
            cipher,
            validate_directory_filter,
        ),
        repository,
        gateway,
    )


def test_admin_connection_import_is_all_or_nothing_and_secrets_are_redacted() -> None:
    service, repository, _gateway = service_fixture()
    admin = repository.users["admin"]
    status = service.create_connection(admin, create_payload())
    assert status.bind_password_configured is True
    assert "bind-secret" not in repr(status.model_dump())
    imported = service.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(
            external_subjects=["subject-ada", "subject-grace"]
        ),
    )
    assert imported.imported_count == 2
    assert len(repository.identities) == 2
    assert all(repository.users[actor_id].system_role == "user" for actor_id in imported.imported_actor_ids)

    before = deepcopy((repository.users, repository.identities, repository.audits))
    with pytest.raises(IdentityAccessError) as conflict:
        service.import_users(
            admin,
            "main",
            DirectoryUserImportRequest(external_subjects=["subject-ada"]),
        )
    assert conflict.value.status_code == 409
    assert (repository.users, repository.identities, repository.audits) == before


def test_scoped_directory_search_query_trims_before_length_validation() -> None:
    assert ScopedDirectoryUserSearchRequest(
        search_mode="member",
        query=f" {'x' * 200} ",
    ).query == "x" * 200
    assert ScopedDirectoryUserSearchRequest(
        search_mode="member",
        query=" x ",
    ).query == "x"
    with pytest.raises(ValidationError):
        ScopedDirectoryUserSearchRequest(search_mode="member", query="   ")
    with pytest.raises(ValidationError):
        ScopedDirectoryUserSearchRequest(
            search_mode="member",
            query=f" {'x' * 201} ",
        )
def test_scoped_directory_search_and_preparation_minimize_and_reuse_identity() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload())
    disabled_payload = create_payload("disabled", priority=20).model_copy(
        update={"enabled": False}
    )
    service.create_connection(admin, disabled_payload)

    sources = service.list_scoped_connections()
    assert sources.model_dump() == {
        "connections": [{"connection_id": "main", "display_name": "Main"}]
    }
    with pytest.raises(IdentityAccessError) as disabled_search:
        service.search_scoped_users(
            "disabled",
            ScopedDirectoryUserSearchRequest(search_mode="member", query="Ada"),
        )
    assert disabled_search.value.status_code == 409
    assert disabled_search.value.message_code == "directory.import_entry_unavailable"
    with pytest.raises(IdentityAccessError) as disabled_import:
        service.prepare_scoped_import("disabled", ["subject-ada"])
    assert disabled_import.value.status_code == 409
    assert disabled_import.value.message_code == "directory.import_entry_unavailable"

    gateway.principals["subject-disabled"] = DirectoryPrincipal(
        "subject-disabled",
        "disabled",
        "Disabled User",
        "disabled@example.test",
        (),
        "Engineering",
        None,
        None,
        False,
    )
    department = service.search_scoped_users(
        "main",
        ScopedDirectoryUserSearchRequest(
            search_mode="department",
            query="  Engineering  ",
        ),
    )
    assert [candidate.external_subject for candidate in department.users] == [
        "subject-ada",
        "subject-grace",
    ]
    assert set(department.users[0].model_dump()) == {
        "external_subject",
        "username",
        "display_name",
        "email",
    }

    for index in range(101):
        gateway.principals[f"subject-user-{index:03d}"] = DirectoryPrincipal(
            f"subject-user-{index:03d}",
            f"user-{index:03d}",
            f"Directory User {index:03d}",
            f"user-{index:03d}@example.test",
            (),
            "Other",
            None,
            None,
            True,
        )
    capped = service.search_scoped_users(
        "main",
        ScopedDirectoryUserSearchRequest(search_mode="member", query="user-"),
    )
    assert len(capped.users) == 100
    assert capped.limit_reached is True
    assert all(candidate.username != "disabled" for candidate in capped.users)

    prepared = service.prepare_scoped_import(
        "main",
        ["subject-ada", "subject-grace"],
    )
    assert len(prepared.actor_ids) == 2
    assert len(prepared.new_users) == 2
    assert len(prepared.new_external_identities) == 2
    for user in prepared.new_users:
        repository.put_user(user)
    for identity in prepared.new_external_identities:
        repository.put_external_identity(identity)

    replay = service.prepare_scoped_import(
        "main",
        ["subject-ada", "subject-grace"],
    )
    assert replay.actor_ids == prepared.actor_ids
    assert replay.new_users == ()
    assert replay.new_external_identities == ()

    repository.users[prepared.actor_ids[0]] = replace(
        repository.users[prepared.actor_ids[0]],
        active=False,
    )
    with pytest.raises(IdentityAccessError) as inactive:
        service.prepare_scoped_import("main", ["subject-ada"])
    assert inactive.value.status_code == 409

    secondary = create_payload("secondary", priority=30)
    service.create_connection(admin, secondary)
    cross_source = service.prepare_scoped_import("secondary", ["subject-grace"])
    assert cross_source.actor_ids != (prepared.actor_ids[1],)


def test_scoped_directory_preparation_fails_before_any_write() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload())
    before = deepcopy((repository.users, repository.identities, repository.audits))

    with pytest.raises(IdentityAccessError) as missing:
        service.prepare_scoped_import("main", ["missing-subject"])
    assert missing.value.status_code == 409
    assert (repository.users, repository.identities, repository.audits) == before

    gateway.outages.add("main")
    with pytest.raises(IdentityAccessError) as outage:
        service.prepare_scoped_import("main", ["subject-ada"])
    assert outage.value.status_code == 503
    assert (repository.users, repository.identities, repository.audits) == before

def test_team_and_project_directory_import_build_owner_specific_atomic_intent() -> None:
    directory, identity_repository, _gateway = service_fixture()
    actor = identity_repository.users["admin"]
    directory.create_connection(actor, create_payload())
    prepared = directory.prepare_scoped_import(
        "main",
        ["subject-ada", "subject-grace"],
    )
    for user in prepared.new_users:
        identity_repository.put_user(user)
    for identity in prepared.new_external_identities:
        identity_repository.put_external_identity(identity)

    class CaptureCommit:
        def __init__(self) -> None:
            self.change_sets = []

        def commit_scoped_directory_import(self, change_set) -> None:
            self.change_sets.append(change_set)

    team_memberships = {
        f"tm-team-a-{prepared.actor_ids[0]}": TeamMembershipRecord(
            membership_id=f"tm-team-a-{prepared.actor_ids[0]}",
            team_id="team-a",
            member_actor_type="user",
            member_actor_id=prepared.actor_ids[0],
            role="admin",
            status="active",
            created_at=utc_now_iso(),
        ),
        f"tm-team-a-{prepared.actor_ids[1]}": TeamMembershipRecord(
            membership_id=f"tm-team-a-{prepared.actor_ids[1]}",
            team_id="team-a",
            member_actor_type="user",
            member_actor_id=prepared.actor_ids[1],
            role="member",
            status="removed",
            created_at=utc_now_iso(),
            removed_at=utc_now_iso(),
        ),
    }
    team_repository = SimpleNamespace(
        get_team=lambda team_id: (
            TeamRecord(
                team_id="team-a",
                name="Team A",
                parent_team_id=None,
                status="active",
                created_at=utc_now_iso(),
            )
            if team_id == "team-a"
            else None
        ),
        can_manage_team=lambda current_actor, team_id: (
            current_actor.actor_id == actor.actor_id and team_id == "team-a"
        ),
        get_membership=lambda membership_id: team_memberships.get(membership_id),
    )
    team_commit = CaptureCommit()
    team_service = TeamAccessService(team_repository, directory, team_commit)
    team_result = team_service.import_directory_members(
        actor,
        "team-a",
        "main",
        TeamDirectoryMemberImportRequest(
            external_subjects=["subject-ada", "subject-grace"],
            role="uploader",
            idempotency_key="team-import-1",
        ),
    )
    assert team_result.applied_count == 2
    team_change_set = team_commit.change_sets[0]
    assert [membership.role for membership in team_change_set.team_memberships] == [
        "admin",
        "uploader",
    ]
    assert all(
        membership.member_actor_type == "user"
        for membership in team_change_set.team_memberships
    )
    assert team_change_set.audit_events[-1].event_type == (
        "team_directory_members_imported"
    )
    assert "external_subject" not in repr(
        team_change_set.audit_events[-1].metadata
    )

    project_grants = {
        ProjectGovernanceService.project_access_grant_id(
            "project-a", "user", prepared.actor_ids[0]
        ): PermissionGrantRecord(
            grant_id=ProjectGovernanceService.project_access_grant_id(
                "project-a", "user", prepared.actor_ids[0]
            ),
            project_id="project-a",
            subject_type="user",
            subject_id=prepared.actor_ids[0],
            role="viewer",
            effect="allow",
            status="active",
            created_at=utc_now_iso(),
        ),
        ProjectGovernanceService.project_access_grant_id(
            "project-a", "user", prepared.actor_ids[1]
        ): PermissionGrantRecord(
            grant_id=ProjectGovernanceService.project_access_grant_id(
                "project-a", "user", prepared.actor_ids[1]
            ),
            project_id="project-a",
            subject_type="user",
            subject_id=prepared.actor_ids[1],
            role="viewer",
            effect="allow",
            status="revoked",
            created_at=utc_now_iso(),
            revoked_at=utc_now_iso(),
        ),
    }
    project_repository = SimpleNamespace(
        get_project=lambda project_id: (
            ProjectRecord(
                project_id="project-a",
                name="Project A",
                policy_profile_id="default",
            )
            if project_id == "project-a"
            else None
        ),
        resolve_access=lambda **_kwargs: SimpleNamespace(allowed=True),
        get_grant=lambda grant_id: project_grants.get(grant_id),
    )
    project_commit = CaptureCommit()
    project_service = ProjectGovernanceService(
        project_repository,
        directory,
        project_commit,
    )
    project_result = project_service.import_directory_members(
        actor,
        "project-a",
        "main",
        ProjectDirectoryMemberImportRequest(
            external_subjects=["subject-ada", "subject-grace"],
            role="contributor",
            idempotency_key="project-import-1",
        ),
    )
    assert project_result.applied_count == 2
    project_change_set = project_commit.change_sets[0]
    assert [grant.role for grant in project_change_set.project_grants] == [
        "viewer",
        "contributor",
    ]
    assert all(grant.effect == "allow" for grant in project_change_set.project_grants)

    first_grant_id = ProjectGovernanceService.project_access_grant_id(
        "project-a",
        "user",
        prepared.actor_ids[0],
    )
    project_grants[first_grant_id] = replace(
        project_grants[first_grant_id],
        effect="deny",
    )
    with pytest.raises(ProjectGovernanceError) as deny:
        project_service.import_directory_members(
            actor,
            "project-a",
            "main",
            ProjectDirectoryMemberImportRequest(
                external_subjects=["subject-ada", "subject-grace"],
                role="admin",
                idempotency_key="project-import-2",
            ),
        )
    assert deny.value.status_code == 409
    assert len(project_commit.change_sets) == 1


def test_scoped_owner_acl_precedes_directory_source_lookup() -> None:
    class ExplodingDirectory:
        called = False

        def list_scoped_connections(self):
            self.called = True
            raise AssertionError("directory lookup must follow scope ACL")

    directory = ExplodingDirectory()
    repository = SimpleNamespace(
        get_team=lambda _team_id: TeamRecord(
            team_id="team-a",
            name="Team A",
            parent_team_id=None,
            status="active",
            created_at=utc_now_iso(),
        ),
        can_manage_team=lambda _actor, _team_id: False,
    )
    service = TeamAccessService(repository, directory, SimpleNamespace())
    with pytest.raises(TeamAccessError) as denied:
        service.list_directory_connections(
            UserRecord(
                actor_id="other-admin",
                display_name="Other Admin",
                email="other@example.test",
                system_role="user",
                password_digest=None,
                created_at=utc_now_iso(),
            ),
            "team-a",
        )
    assert denied.value.status_code == 403
    assert directory.called is False





def test_directory_profile_never_changes_atlas_authority() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload())
    imported = service.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    actor_id = imported.imported_actor_ids[0]
    repository.users[actor_id].system_role = "operator"
    repository.users[actor_id].active = False
    repository.team_memberships[actor_id] = ("team-a",)
    repository.project_grants[actor_id] = ("project-a",)
    repository.acl_decisions[actor_id] = False
    authority_before = (
        repository.users[actor_id].system_role,
        repository.users[actor_id].active,
        repository.team_memberships[actor_id],
        repository.project_grants[actor_id],
        repository.acl_decisions[actor_id],
    )
    gateway.principals["subject-ada"] = replace(
        gateway.principals["subject-ada"],
        groups=("Administrators",),
        department="Privileged",
        title="Owner",
        employee_id="ROOT",
    )
    service.refresh_profile(admin, actor_id)
    authority_after = (
        repository.users[actor_id].system_role,
        repository.users[actor_id].active,
        repository.team_memberships[actor_id],
        repository.project_grants[actor_id],
        repository.acl_decisions[actor_id],
    )
    assert authority_after == authority_before

    gateway.outages.add("main")
    with pytest.raises(IdentityAccessError) as unavailable:
        service.refresh_profile(admin, actor_id)
    assert unavailable.value.status_code == 503
    assert repository.identities[actor_id].status == "stale"
    assert repository.users[actor_id].active is False


def test_login_selects_one_ordered_imported_source_without_fallback() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload("secondary", priority=20))
    service.create_connection(admin, create_payload("primary", priority=1))
    first = service.import_users(
        admin,
        "secondary",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    gateway.principals["subject-grace"] = replace(
        gateway.principals["subject-grace"],
        username="ada",
        email="ada@example.test",
    )
    second = service.import_users(
        admin,
        "primary",
        DirectoryUserImportRequest(external_subjects=["subject-grace"]),
    )
    assert first.imported_actor_ids != second.imported_actor_ids
    repository.users[first.imported_actor_ids[0]].active = False

    outcome = service.authenticate_imported(
        " ADA@EXAMPLE.TEST ", "directory-password"
    )
    assert outcome is not None
    assert gateway.authentication_calls == [("primary", "directory-password")]

    gateway.authentication_calls.clear()
    gateway.outages.add("primary")
    with pytest.raises(IdentityAccessError) as unavailable:
        service.authenticate_imported("ada", "directory-password")
    assert unavailable.value.status_code == 503
    assert gateway.authentication_calls == []



def test_import_rejects_cross_field_alias_collision_atomically() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload())
    gateway.principals["subject-ada"] = replace(
        gateway.principals["subject-ada"],
        username="shared",
    )
    gateway.principals["subject-grace"] = replace(
        gateway.principals["subject-grace"],
        email="shared",
    )
    before = deepcopy((repository.users, repository.identities, repository.audits))

    with pytest.raises(IdentityAccessError) as conflict:
        service.import_users(
            admin,
            "main",
            DirectoryUserImportRequest(
                external_subjects=["subject-ada", "subject-grace"]
            ),
        )

    assert conflict.value.status_code == 409
    assert (repository.users, repository.identities, repository.audits) == before


def test_login_rechecks_atlas_active_after_directory_authentication() -> None:
    service, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    service.create_connection(admin, create_payload())
    imported = service.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    actor_id = imported.imported_actor_ids[0]

    def deactivate_during_authentication() -> None:
        repository.users[actor_id].active = False
        repository.users[actor_id].system_role = "operator"

    gateway.on_authenticate = deactivate_during_authentication
    with pytest.raises(IdentityAccessError) as rejected:
        service.authenticate_imported("ada", "directory-password")

    assert rejected.value.status_code == 401
    assert repository.users[actor_id].active is False
    assert repository.users[actor_id].system_role == "operator"
    assert repository.sessions == {}
    assert all(audit.event_type != "directory_login_succeeded" for audit in repository.audits)

def test_non_admin_cannot_manage_directory_connections() -> None:
    service, repository, _gateway = service_fixture()
    with pytest.raises(IdentityAccessError) as denied:
        service.create_connection(repository.users["local"], create_payload())
    assert denied.value.status_code == 403
    assert repository.connections == {}

def test_directory_admin_http_journey_and_safe_responses() -> None:
    service, repository, _gateway = service_fixture()

    class Principal:
        def __init__(self, actor_id):
            self.actor_id = actor_id

        def current_user(self, _token):
            return repository.users[self.actor_id]

    class ScopeGrants:
        pass

    values = {
        name: object()
        for name in ApiComposition.__dataclass_fields__
    }
    values.update(
        current_principal=Principal("admin"),
        directory_identity=service,
        identity_access=IdentityAccessService(repository, ScopeGrants(), service),
    )
    client = TestClient(create_app(ApiComposition(**values)))
    payload = create_payload().model_dump(mode="json")
    payload.update(port=389, tls_mode="plain", bind_password="bind-secret")
    ad_plain_payload = {
        **payload,
        "connection_id": "ad-plain",
        "display_name": "AD Plain",
        "provider_type": "active_directory",
        "user_object_filter": "(&(objectCategory=person)(objectClass=user))",
        "login_attribute": "userPrincipalName",
        "stable_id_attribute": "objectGUID",
        "display_name_attribute": "displayName",
        "employee_id_attribute": "employeeID",
    }
    ad_plain_created = client.post(
        "/api/v1/admin/directory-connections",
        json=ad_plain_payload,
    )
    assert ad_plain_created.status_code == 201
    assert ad_plain_created.json()["provider_type"] == "active_directory"
    assert ad_plain_created.json()["tls_mode"] == "plain"
    assert ad_plain_created.json()["stable_id_attribute"] == "objectGUID"
    assert "bind-secret" not in ad_plain_created.text

    created = client.post(
        "/api/v1/admin/directory-connections",
        json=payload,
    )
    assert created.status_code == 201
    assert created.json()["tls_mode"] == "plain"
    assert created.json()["port"] == 389
    assert created.json()["bind_password_configured"] is True
    assert "bind-secret" not in created.text
    rejected_update = client.patch(
        "/api/v1/admin/directory-connections/main",
        json={
            "bind_dn": "cn=validation-sentinel,dc=example,dc=test",
            "custom_ca_pem": "-----BEGIN CERTIFICATE-----\nvalidation-sentinel",
            "clear_custom_ca": True,
        },
    )
    assert rejected_update.status_code == 422
    assert "validation-sentinel" not in rejected_update.text

    ad_payload = create_payload("ad").model_dump(mode="json")
    ad_payload.update(provider_type="active_directory", bind_password="bind-secret")
    ad_created = client.post(
        "/api/v1/admin/directory-connections",
        json=ad_payload,
    )
    assert ad_created.status_code == 201
    updated_ad_plain = client.patch(
        "/api/v1/admin/directory-connections/ad",
        json={"port": 389, "tls_mode": "plain"},
    )
    assert updated_ad_plain.status_code == 200
    assert updated_ad_plain.json()["provider_type"] == "active_directory"
    assert updated_ad_plain.json()["port"] == 389
    assert updated_ad_plain.json()["tls_mode"] == "plain"

    invalid_filter = client.patch(
        "/api/v1/admin/directory-connections/main",
        json={"user_object_filter": "(objectClass=person))"},
    )
    assert invalid_filter.status_code == 422
    assert invalid_filter.json()["message_code"] == "directory.entry_is_invalid"

    empty_secret_payload = create_payload("empty-secret").model_dump(mode="json")
    empty_secret_payload["bind_password"] = ""
    empty_secret = client.post(
        "/api/v1/admin/directory-connections",
        json=empty_secret_payload,
    )
    assert empty_secret.status_code == 422

    tested = client.post("/api/v1/admin/directory-connections/main/test")
    assert tested.status_code == 200
    assert tested.json()["validation_status"] == "passed"

    searched = client.post(
        "/api/v1/admin/directory-connections/main/users/search",
        json={"query": "a", "limit": 50},
    )
    assert searched.status_code == 200
    assert {item["external_subject"] for item in searched.json()["users"]} == {
        "subject-ada",
        "subject-grace",
    }
    imported = client.post(
        "/api/v1/admin/directory-connections/main/users/import",
        json={"external_subjects": ["subject-ada", "subject-grace"]},
    )
    assert imported.status_code == 200
    actor_id = imported.json()["imported_actor_ids"][0]
    blocked_profile_edit = client.patch(
        f"/api/v1/admin/users/{actor_id}",
        json={
            "display_name": "Manual override",
            "idempotency_key": "directory-profile-edit",
        },
    )
    assert blocked_profile_edit.status_code == 422
    assert blocked_profile_edit.json()["message_code"] == "directory.profile_is_read_only"
    assert repository.users[actor_id].display_name != "Manual override"

    local_profile_edit = client.patch(
        "/api/v1/admin/users/local",
        json={
            "display_name": "Updated Local User",
            "idempotency_key": "local-profile-edit",
        },
    )
    assert local_profile_edit.status_code == 200
    assert repository.users["local"].display_name == "Updated Local User"
    filtered = client.get(
        "/api/v1/admin/users",
        params={
            "q": "compiler",
            "account_source": "directory",
            "directory_connection_id": "main",
            "active": "true",
            "directory_profile_status": "current",
            "directory_group": "Compiler",
            "department": "engineer",
            "title": "admir",
            "employee_id": "e-101",
        },
    )
    assert filtered.status_code == 200
    assert [user["display_name"] for user in filtered.json()["users"]] == [
        "Grace Hopper"
    ]
    refreshed = client.post(
        f"/api/v1/admin/users/{actor_id}/directory-profile/refresh"
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "current"
    combined = created.text + tested.text + searched.text + imported.text + refreshed.text
    assert "bind-secret" not in combined
    assert "BEGIN CERTIFICATE" not in combined
    assert "audit_event_ref" not in combined

    values["current_principal"] = Principal("local")
    member_client = TestClient(create_app(ApiComposition(**values)))
    denied = member_client.get("/api/v1/admin/directory-connections")
    assert denied.status_code == 403

def test_http_directory_login_sets_cookie_and_never_falls_back() -> None:
    directory, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    directory.create_connection(
        admin,
        create_payload().model_copy(update={"port": 389, "tls_mode": "plain"}),
    )
    directory.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )

    class ScopeGrants:
        pass

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    values = {
        name: object()
        for name in ApiComposition.__dataclass_fields__
    }
    values.update(
        identity_access=identity,
        directory_identity=directory,
    )
    client = TestClient(create_app(ApiComposition(**values)))
    accepted = client.post(
        "/api/v1/auth/sessions",
        json={
            "identifier": " ADA@EXAMPLE.TEST ",
            "password": "directory-password",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["authenticated"] is True
    assert accepted.cookies.get("atlas_session")

    rejected_client = TestClient(create_app(ApiComposition(**values)))
    rejected = rejected_client.post(
        "/api/v1/auth/sessions",
        json={"identifier": "ada", "password": "wrong"},
    )
    assert rejected.status_code == 401
    assert rejected.cookies.get("atlas_session") is None

    gateway.outages.add("main")
    unavailable_client = TestClient(create_app(ApiComposition(**values)))
    unavailable = unavailable_client.post(
        "/api/v1/auth/sessions",
        json={"identifier": "ada", "password": "directory-password"},
    )
    assert unavailable.status_code == 503
    assert unavailable.cookies.get("atlas_session") is None


def test_local_email_failure_does_not_fall_back_to_directory() -> None:
    directory, repository, gateway = service_fixture()
    admin = repository.users["admin"]
    directory.create_connection(admin, create_payload())
    imported = directory.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    directory_actor = imported.imported_actor_ids[0]
    repository.identities[directory_actor] = replace(
        repository.identities[directory_actor],
        normalized_username="local@example.test",
    )

    class ScopeGrants:
        pass

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    with pytest.raises(IdentityAccessError) as rejected:
        identity.login(
            LoginRequest(
                identifier="local@example.test",
                password="directory-password",
            )
        )
    assert rejected.value.status_code == 401
    assert gateway.authentication_calls == []

def test_user_list_projects_directory_profiles_and_applies_filters() -> None:
    directory, repository, _gateway = service_fixture()
    admin = repository.users["admin"]
    directory.create_connection(admin, create_payload())
    imported = directory.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(
            external_subjects=["subject-ada", "subject-grace"]
        ),
    )

    class ScopeGrants:
        pass

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    all_users = identity.list_users(admin)
    imported_users = [
        user for user in all_users.users if user.actor_id in imported.imported_actor_ids
    ]
    assert {user.account_source for user in imported_users} == {"directory"}
    assert {user.directory_profile.username for user in imported_users} == {
        "ada",
        "grace",
    }

    by_query = identity.list_users(admin, q="compiler")
    assert [user.display_name for user in by_query.users] == ["Grace Hopper"]
    by_group = identity.list_users(admin, directory_group="research")
    assert [user.display_name for user in by_group.users] == ["Ada Lovelace"]
    by_profile = identity.list_users(
        admin,
        account_source="directory",
        directory_connection_id="main",
        directory_profile_status="current",
        department="engineer",
        title="program",
        employee_id="e-100",
        active=True,
    )
    assert [user.display_name for user in by_profile.users] == ["Ada Lovelace"]
    local_only = identity.list_users(admin, account_source="local")
    assert {user.actor_id for user in local_only.users} == {"admin", "local"}
