from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher
from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.infrastructure.ldap_directory_gateway import validate_directory_filter
from atlas_production.modules.identity_access.api_models import (
    DirectoryConnectionCreateRequest,
    DirectoryUserImportRequest,
    LoginRequest,
)
from atlas_production.modules.identity_access.contracts import IdentityAccessError
from atlas_production.modules.identity_access.directory_records import (
    DirectoryGatewayError,
    DirectoryPrincipal,
)
from atlas_production.modules.identity_access.directory_service import (
    DirectoryIdentityService,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.identity_access.service import IdentityAccessService
from atlas_production.modules.identity_access.security import password_digest
from atlas_production.shared.public import AuditEventRecord, utc_now_iso
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

    def list_users(self):
        return deepcopy(list(self.users.values()))

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

    def search_users(self, connection, bind_password, query, limit):
        self.test_connection(connection, bind_password)
        query = query.casefold()
        return tuple(
            item
            for item in self.principals.values()
            if query in item.username.casefold() or query in item.display_name.casefold()
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
    payload["bind_password"] = "bind-secret"
    created = client.post(
        "/api/v1/admin/directory-connections",
        json=payload,
    )
    assert created.status_code == 201
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
    directory.create_connection(admin, create_payload())
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
