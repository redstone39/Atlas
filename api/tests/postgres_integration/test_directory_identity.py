from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import delete, func, select

from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher
from atlas_production.infrastructure.ldap_directory_gateway import validate_directory_filter
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasDirectoryConnectionRow,
    AtlasDirectoryConnectionSecretRow,
    AtlasExternalIdentityRow,
    AtlasSessionRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.postgres_identity_adapter import (
    PostgresIdentityAccessRepository,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.identity_access.api_models import (
    DirectoryConnectionCreateRequest,
    DirectoryUserImportRequest,
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


class FakeDirectoryGateway:
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
        normalized = query.casefold()
        return tuple(
            principal
            for principal in self.principals.values()
            if normalized in principal.username.casefold()
            or normalized in principal.display_name.casefold()
        )[:limit]

    def fetch_user(self, connection, bind_password, external_subject):
        self.test_connection(connection, bind_password)
        return self.principals.get(external_subject)

    def authenticate(
        self,
        connection,
        bind_password,
        external_subject,
        password,
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


def connection_payload(connection_id: str, priority: int):
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
        custom_ca_pem="-----BEGIN CERTIFICATE-----\ntest-ca",
    )


def reset_identity_rows(runtime: PostgresRuntime) -> UserRecord:
    with runtime.session_factory() as session:
        for row_type in (
            AtlasAuditEventRow,
            AtlasSessionRow,
            AtlasExternalIdentityRow,
            AtlasDirectoryConnectionSecretRow,
            AtlasDirectoryConnectionRow,
            AtlasUserRow,
        ):
            session.execute(delete(row_type))
        session.add(
            AtlasUserRow(
                actor_id="admin",
                display_name="Atlas Admin",
                email="admin@example.test",
                system_role="admin",
                password_digest="unused",
                active=True,
                actor_type="user",
                created_at="2026-08-10T00:00:00+00:00",
            )
        )
        session.commit()
    return UserRecord(
        actor_id="admin",
        display_name="Atlas Admin",
        email="admin@example.test",
        system_role="admin",
        password_digest="unused",
        active=True,
        actor_type="user",
        created_at="2026-08-10T00:00:00+00:00",
    )


def build_service(runtime: PostgresRuntime):
    repository = PostgresIdentityAccessRepository(runtime.session_factory)
    gateway = FakeDirectoryGateway()
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
        cipher,
    )


def test_directory_owner_persists_encrypted_secrets_and_atomic_import(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, repository, _gateway, cipher = build_service(postgres_runtime)

    status = service.create_connection(admin, connection_payload("main", 10))
    assert status.bind_password_configured is True
    assert status.custom_ca_configured is True

    with postgres_runtime.session_factory() as session:
        secrets = session.scalars(
            select(AtlasDirectoryConnectionSecretRow).order_by(
                AtlasDirectoryConnectionSecretRow.secret_kind
            )
        ).all()
        assert len(secrets) == 2
        assert all("bind-secret" not in row.ciphertext for row in secrets)
        assert all("test-ca" not in row.ciphertext for row in secrets)

    bind_secret = repository.get_directory_secret("main", "bind_password")
    ca_secret = repository.get_directory_secret("main", "custom_ca")
    assert bind_secret is not None
    assert ca_secret is not None
    assert cipher.decrypt(
        bind_secret,
        domain="identity_directory_bind_password",
        owner_id="main",
        owner_kind="directory_connection",
    ) == "bind-secret"
    assert cipher.decrypt(
        ca_secret,
        domain="identity_directory_custom_ca",
        owner_id="main",
        owner_kind="directory_connection",
    ) == "-----BEGIN CERTIFICATE-----\ntest-ca"

    imported = service.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(
            external_subjects=["subject-ada", "subject-grace"]
        ),
    )
    assert imported.imported_count == 2
    with postgres_runtime.session_factory() as session:
        before_users = session.scalar(select(func.count()).select_from(AtlasUserRow))
        before_identities = session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        )
        before_audits = session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        )

    with pytest.raises(IdentityAccessError) as conflict:
        service.import_users(
            admin,
            "main",
            DirectoryUserImportRequest(
                external_subjects=["subject-ada", "subject-grace"]
            ),
        )
    assert conflict.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == before_users
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == before_identities
        assert session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        ) == before_audits

    gateway = _gateway
    gateway.principals["subject-grace"] = replace(
        gateway.principals["subject-grace"],
        email="ada",
    )
    with pytest.raises(IdentityAccessError) as alias_conflict:
        service.import_users(
            admin,
            "main",
            DirectoryUserImportRequest(external_subjects=["subject-grace"]),
        )
    assert alias_conflict.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == before_users
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == before_identities



def test_directory_login_and_refresh_commit_current_state_without_authority_drift(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, repository, gateway, _cipher = build_service(postgres_runtime)
    service.create_connection(admin, connection_payload("secondary", 20))
    service.create_connection(admin, connection_payload("primary", 5))
    imported = service.import_users(
        admin,
        "primary",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    actor_id = imported.imported_actor_ids[0]
    original = repository.get_user(actor_id)
    assert original is not None

    outcome = service.authenticate_imported("ADA@EXAMPLE.TEST", "directory-password")
    assert outcome is not None
    assert outcome.session.authenticated is True
    assert outcome.session.actor is not None
    assert outcome.session.actor.actor_id == actor_id
    assert gateway.authentication_calls == [("primary", "directory-password")]

    with postgres_runtime.session_factory() as session:
        identity = session.get(AtlasExternalIdentityRow, actor_id)
        assert identity is not None
        assert identity.status == "current"
        assert session.scalar(
            select(func.count()).select_from(AtlasSessionRow)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(AtlasAuditEventRow)
            .where(AtlasAuditEventRow.event_type == "directory_login_succeeded")
        ) == 1

    gateway.principals["subject-ada"] = replace(
        gateway.principals["subject-ada"],
        groups=("Changed group",),
        department="Changed department",
        title="Changed title",
        employee_id="Changed employee",
    )
    refreshed = service.refresh_profile(admin, actor_id)
    assert refreshed.department == "Changed department"
    current = repository.get_user(actor_id)
    assert current is not None
    assert current.system_role == original.system_role == "user"
    assert current.active is original.active is True

    gateway.outages.add("primary")
    with pytest.raises(IdentityAccessError) as unavailable:
        service.refresh_profile(admin, actor_id)
    assert unavailable.value.status_code == 503
    stale = repository.get_external_identity(actor_id)
    assert stale is not None
    assert stale.status == "stale"
    current_after_failure = repository.get_user(actor_id)
    assert current_after_failure is not None
    assert current_after_failure.system_role == "user"
    assert current_after_failure.active is True



def test_directory_login_cannot_overwrite_concurrent_atlas_deactivation(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, repository, gateway, _cipher = build_service(postgres_runtime)
    service.create_connection(admin, connection_payload("main", 1))
    imported = service.import_users(
        admin,
        "main",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    actor_id = imported.imported_actor_ids[0]
    before_identity = repository.get_external_identity(actor_id)
    assert before_identity is not None

    def deactivate_during_authentication() -> None:
        with postgres_runtime.session_factory() as session:
            row = session.get(AtlasUserRow, actor_id)
            assert row is not None
            row.active = False
            row.system_role = "operator"
            session.commit()

    gateway.on_authenticate = deactivate_during_authentication
    with pytest.raises(IdentityAccessError) as rejected:
        service.authenticate_imported("ada", "directory-password")
    assert rejected.value.status_code == 401

    with postgres_runtime.session_factory() as session:
        user = session.get(AtlasUserRow, actor_id)
        identity = session.get(AtlasExternalIdentityRow, actor_id)
        assert user is not None
        assert user.active is False
        assert user.system_role == "operator"
        assert identity is not None
        assert identity.last_refreshed_at == before_identity.last_refreshed_at
        assert session.scalar(
            select(func.count()).select_from(AtlasSessionRow)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(AtlasAuditEventRow)
            .where(AtlasAuditEventRow.event_type == "directory_login_succeeded")
        ) == 0