from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections import deque
from dataclasses import replace
from threading import Event, get_ident
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select


from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher
from atlas_production.infrastructure.ldap_directory_gateway import validate_directory_filter
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasDirectoryConnectionRow,
    AtlasDirectoryConnectionSecretRow,
    AtlasExternalIdentityRow,
    AtlasIdentityCreateReceiptRow,
    AtlasPermissionGrantRow,
    AtlasSessionRow,
    AtlasTeamMembershipRow,
    AtlasUserInviteRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_identity_adapter import (
    PostgresIdentityAccessRepository,
)
from atlas_production.infrastructure.postgres_project_adapter import (
    build_postgres_project_governance,
)
from atlas_production.infrastructure.postgres_team_adapter import build_postgres_team_access
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.postgres_owner import identity as identity_owner
from atlas_production.infrastructure.postgres_owner.identity import IdentityRepository
from atlas_production.modules.identity_access.api_models import (
    DirectoryConnectionCreateRequest,
    DirectoryUserImportRequest,
    UserInviteCreateRequest,
    TeamDirectoryMemberImportRequest,
)
from atlas_production.modules.identity_access.contracts import IdentityAccessError
from atlas_production.modules.identity_access.team_contracts import TeamAccessError
from atlas_production.modules.project_governance.api_models import (
    ProjectDirectoryMemberImportRequest,
)
from atlas_production.modules.project_governance.contracts import ProjectGovernanceError
from atlas_production.modules.identity_access.directory_records import (
    DirectoryGatewayError,
    DirectoryPrincipal,
)
from atlas_production.modules.identity_access.directory_service import (
    DirectoryIdentityService,
)
from atlas_production.modules.identity_access.service import IdentityAccessService
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
            "subject-katherine": DirectoryPrincipal(
                "subject-katherine",
                "katherine",
                "Katherine Johnson",
                "katherine@example.test",
                ("Flight Dynamics",),
                "Engineering",
                "Mathematician",
                "E-102",
                True,
            ),
            "subject-linus": DirectoryPrincipal(
                "subject-linus",
                "linus",
                "Linus Torvalds",
                "linus@example.test",
                ("Systems",),
                "Engineering",
                "Engineer",
                "E-103",
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
                principal
                for principal in self.principals.values()
                if (principal.department or "").casefold() == normalized
            )[:limit]
        normalized = (query or "").casefold()
        return tuple(
            principal
            for principal in self.principals.values()
            if normalized in principal.username.casefold()
            or normalized in principal.display_name.casefold()
            or normalized in (principal.email or "").casefold()
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
        display_name=connection_id.title(),
        idempotency_key=f"directory-create-{connection_id}",
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
            AtlasIdentityCreateReceiptRow,
            AtlasAuditEventRow,
            AtlasUserInviteRow,
            AtlasSessionRow,
            AtlasPermissionGrantRow,
            AtlasProjectRow,
            AtlasTeamMembershipRow,
            AtlasTeamRow,
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


def build_service(
    runtime: PostgresRuntime,
    directory_ids: tuple[str, ...] = ("directory-main",),
):
    allocated_directory_ids = deque(directory_ids)
    repository = PostgresIdentityAccessRepository(
        runtime.session_factory,
        id_allocator=lambda: (
            allocated_directory_ids.popleft().removeprefix("directory-")
            if allocated_directory_ids
            else uuid4().hex
        ),
    )
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


def test_fresh_baseline_accepts_ldap_and_active_directory_plain(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, _repository, _gateway, _cipher = build_service(
        postgres_runtime,
        ("plain",),
    )
    status = service.create_connection(
        admin,
        connection_payload("plain", 10).model_copy(
            update={"port": 389, "tls_mode": "plain"}
        ),
    )
    assert status.provider_type == "ldap"
    assert status.tls_mode == "plain"
    assert status.port == 389

    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasDirectoryConnectionRow(
                connection_id="ad-plain",
                display_name="AD Plain",
                priority=20,
                provider_type="active_directory",
                host="directory.example.test",
                port=389,
                tls_mode="plain",
                connect_timeout_seconds=3,
                operation_timeout_seconds=4,
                bind_dn="cn=bind,dc=example,dc=test",
                user_base_dn="ou=people,dc=example,dc=test",
                user_object_filter="(&(objectCategory=person)(objectClass=user))",
                login_attribute="userPrincipalName",
                stable_id_attribute="objectGUID",
                display_name_attribute="displayName",
                email_attribute="mail",
                groups_attribute="memberOf",
                department_attribute="department",
                title_attribute="title",
                employee_id_attribute="employeeID",
                enabled=True,
                created_at="2026-08-12T00:00:00+00:00",
                updated_at="2026-08-12T00:00:00+00:00",
            )
        )
        session.commit()

    with postgres_runtime.session_factory() as session:
        stored = session.get(AtlasDirectoryConnectionRow, "ad-plain")
        assert stored is not None
        assert stored.provider_type == "active_directory"
        assert stored.tls_mode == "plain"


def test_directory_owner_persists_encrypted_secrets_and_atomic_import(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, repository, _gateway, cipher = build_service(postgres_runtime)

    status = service.create_connection(admin, connection_payload("directory-main", 10))
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

    bind_secret = repository.get_directory_secret("directory-main", "bind_password")
    ca_secret = repository.get_directory_secret("directory-main", "custom_ca")
    assert bind_secret is not None
    assert ca_secret is not None
    assert cipher.decrypt(
        bind_secret,
        domain="identity_directory_bind_password",
        owner_id="directory-main",
        owner_kind="directory_connection",
    ) == "bind-secret"
    assert cipher.decrypt(
        ca_secret,
        domain="identity_directory_custom_ca",
        owner_id="directory-main",
        owner_kind="directory_connection",
    ) == "-----BEGIN CERTIFICATE-----\ntest-ca"

    imported = service.import_users(
        admin,
        "directory-main",
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
            "directory-main",
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
            "directory-main",
            DirectoryUserImportRequest(external_subjects=["subject-grace"]),
        )
    assert alias_conflict.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == before_users
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == before_identities



def test_distinct_directory_sources_may_share_email(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, _repository, gateway, _cipher = build_service(
        postgres_runtime,
        ("directory-secondary", "directory-primary"),
    )
    service.create_connection(admin, connection_payload("directory-secondary", 20))
    service.create_connection(admin, connection_payload("directory-primary", 5))
    first = service.import_users(
        admin,
        "directory-secondary",
        DirectoryUserImportRequest(external_subjects=["subject-ada"]),
    )
    gateway.principals["subject-grace"] = replace(
        gateway.principals["subject-grace"],
        username="ada",
        email="ada@example.test",
    )
    second = service.import_users(
        admin,
        "directory-primary",
        DirectoryUserImportRequest(external_subjects=["subject-grace"]),
    )
    assert first.imported_actor_ids != second.imported_actor_ids
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 3
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 2


def test_directory_login_and_refresh_commit_current_state_without_authority_drift(
    postgres_runtime: PostgresRuntime,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    service, repository, gateway, _cipher = build_service(
        postgres_runtime,
        ("directory-secondary", "directory-primary"),
    )
    service.create_connection(admin, connection_payload("directory-secondary", 20))
    service.create_connection(admin, connection_payload("directory-primary", 5))
    imported = service.import_users(
        admin,
        "directory-primary",
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
    assert gateway.authentication_calls == [("directory-primary", "directory-password")]

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

    gateway.outages.add("directory-primary")
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
    service.create_connection(admin, connection_payload("directory-main", 1))
    imported = service.import_users(
        admin,
        "directory-main",
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


def test_scoped_team_import_commits_atomically_and_retry_converges(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, _gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasTeamRow(
                team_id="team-directory",
                name="Directory Team",
                parent_team_id=None,
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                inherit_parent_documents=True,
            )
        )
        session.commit()
    team = build_postgres_team_access(
        postgres_runtime.session_factory,
        directory,
        repository,
    )
    payload = TeamDirectoryMemberImportRequest(
        external_subjects=["subject-ada", "subject-grace"],
        role="uploader",
        idempotency_key="team-directory-response-loss",
    )

    first = team.import_directory_members(admin, "team-directory", "directory-main", payload)
    replay = team.import_directory_members(admin, "team-directory", "directory-main", payload)
    assert replay.actor_ids == first.actor_ids
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 3
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasTeamMembershipRow)
        ) == 2
        memberships = session.scalars(select(AtlasTeamMembershipRow)).all()
        assert {membership.role for membership in memberships} == {"uploader"}
        membership = session.scalar(select(AtlasTeamMembershipRow))
        assert membership is not None
        assert membership.role == "uploader"
        successful_audits = session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        )

    prepare_scoped_import = directory.prepare_scoped_import

    def prepare_then_disable_source(connection_id, external_subjects):
        preparation = prepare_scoped_import(connection_id, external_subjects)
        with postgres_runtime.session_factory() as session:
            connection = session.get(AtlasDirectoryConnectionRow, connection_id)
            assert connection is not None
            connection.enabled = False
            session.commit()
        return preparation

    monkeypatch.setattr(
        directory,
        "prepare_scoped_import",
        prepare_then_disable_source,
    )
    with pytest.raises(TeamAccessError) as rejected:
        team.import_directory_members(
            admin,
            "team-directory",
            "directory-main",
            TeamDirectoryMemberImportRequest(
                external_subjects=["subject-katherine", "subject-linus"],
                role="member",
                idempotency_key="team-directory-source-change",
            ),
        )
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 3
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasTeamMembershipRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        ) == successful_audits




def test_scoped_import_serializes_with_global_alias_producer(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    gateway.principals["subject-grace"] = replace(
        gateway.principals["subject-ada"],
        external_subject="subject-grace",
    )
    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasTeamRow(
                team_id="team-directory",
                name="Directory Team",
                parent_team_id=None,
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                inherit_parent_documents=True,
            )
        )
        session.commit()
    team = build_postgres_team_access(
        postgres_runtime.session_factory,
        directory,
        repository,
    )
    writer_held = Event()
    release_writer = Event()
    write_identity_rows = IdentityRepository._write_identity_rows

    def hold_global_writer(session, change_set):
        if change_set.external_identities:
            writer_held.set()
            assert release_writer.wait(5)
        return write_identity_rows(session, change_set)

    monkeypatch.setattr(
        IdentityRepository,
        "_write_identity_rows",
        staticmethod(hold_global_writer),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        global_import = pool.submit(
            directory.import_users,
            admin,
            "directory-main",
            DirectoryUserImportRequest(external_subjects=["subject-grace"]),
        )
        assert writer_held.wait(5)
        scoped_import = pool.submit(
            team.import_directory_members,
            admin,
            "team-directory",
            "directory-main",
            TeamDirectoryMemberImportRequest(
                external_subjects=["subject-ada"],
                role="member",
                idempotency_key="scoped-global-alias-race",
            ),
        )
        with pytest.raises(FutureTimeoutError):
            scoped_import.result(timeout=0.2)
        release_writer.set()
        global_import.result(timeout=5)
        with pytest.raises(TeamAccessError) as rejected:
            scoped_import.result(timeout=5)
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AtlasTeamMembershipRow)
        ) == 0


def test_global_import_rechecks_stable_subject_after_owner_lock(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    winner, _repository, _gateway, _cipher = build_service(postgres_runtime)
    winner.create_connection(admin, connection_payload("directory-main", 1))
    loser, _repository, loser_gateway, _cipher = build_service(postgres_runtime)
    loser_gateway.principals["subject-ada"] = replace(
        loser_gateway.principals["subject-ada"],
        username="ada-renamed",
        email="ada-renamed@example.test",
    )
    writer_held = Event()
    release_writer = Event()
    write_identity_rows = IdentityRepository._write_identity_rows

    def hold_winner(session, change_set):
        if change_set.external_identities:
            writer_held.set()
            assert release_writer.wait(5)
        return write_identity_rows(session, change_set)

    monkeypatch.setattr(
        IdentityRepository,
        "_write_identity_rows",
        staticmethod(hold_winner),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        winning_import = pool.submit(
            winner.import_users,
            admin,
            "directory-main",
            DirectoryUserImportRequest(external_subjects=["subject-ada"]),
        )
        assert writer_held.wait(5)
        losing_import = pool.submit(
            loser.import_users,
            admin,
            "directory-main",
            DirectoryUserImportRequest(external_subjects=["subject-ada"]),
        )
        with pytest.raises(FutureTimeoutError):
            losing_import.result(timeout=0.2)
        release_writer.set()
        winning_import.result(timeout=5)
        with pytest.raises(IdentityAccessError) as rejected:
            losing_import.result(timeout=5)
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 1


@pytest.mark.parametrize("winner", ["local", "directory"])
@pytest.mark.parametrize("alias_field", ["email", "username"])
def test_canonical_unicode_email_conflicts_sequentially(
    postgres_runtime: PostgresRuntime,
    winner: str,
    alias_field: str,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    if alias_field == "username":
        gateway.principals["subject-ada"] = replace(
            gateway.principals["subject-ada"],
            username="ada@example.test",
            email="other@example.test",
        )

    class ScopeGrants:
        @staticmethod
        def validate_scope_values(*_args):
            return None

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    invite_payload = UserInviteCreateRequest(
        display_name="Local Ada",
        email="ａｄａ@example.test",
        system_role="user",
        idempotency_key=f"canonical-{alias_field}-{winner}",
    )
    import_payload = DirectoryUserImportRequest(
        external_subjects=["subject-ada"],
    )
    if winner == "local":
        identity.create_invite(admin, invite_payload)
        with pytest.raises(IdentityAccessError) as rejected:
            directory.import_users(admin, "directory-main", import_payload)
    else:
        directory.import_users(admin, "directory-main", import_payload)
        with pytest.raises(IdentityAccessError) as rejected:
            identity.create_invite(admin, invite_payload)
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == (1 if winner == "directory" else 0)


@pytest.mark.parametrize(
    ("local_email", "alias_field"),
    [
        ("ada@example.test", "email"),
        ("ａｄａ@example.test", "email"),
        ("ａｄａ@example.test", "username"),
    ],
)
def test_scoped_import_serializes_with_local_email_producer(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
    local_email: str,
    alias_field: str,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    if alias_field == "username":
        gateway.principals["subject-ada"] = replace(
            gateway.principals["subject-ada"],
            username="ada@example.test",
            email="other@example.test",
        )
    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasTeamRow(
                team_id="team-directory",
                name="Directory Team",
                parent_team_id=None,
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                inherit_parent_documents=True,
            )
        )
        session.commit()
    team = build_postgres_team_access(
        postgres_runtime.session_factory,
        directory,
        repository,
    )

    class ScopeGrants:
        @staticmethod
        def validate_scope_values(*_args):
            return None

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    writer_held = Event()
    release_writer = Event()
    write_identity_rows = IdentityRepository._write_identity_rows

    def hold_invite_writer(session, change_set):
        if change_set.invite_transitions:
            writer_held.set()
            assert release_writer.wait(5)
        return write_identity_rows(session, change_set)

    monkeypatch.setattr(
        IdentityRepository,
        "_write_identity_rows",
        staticmethod(hold_invite_writer),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        invite = pool.submit(
            identity.create_invite,
            admin,
            UserInviteCreateRequest(
                display_name="Local Ada",
                email=local_email,
                system_role="user",
                idempotency_key="local-directory-email-race",
            ),
        )
        assert writer_held.wait(5)
        scoped_import = pool.submit(
            team.import_directory_members,
            admin,
            "team-directory",
            "directory-main",
            TeamDirectoryMemberImportRequest(
                external_subjects=["subject-ada"],
                role="member",
                idempotency_key="scoped-local-email-race",
            ),
        )
        with pytest.raises(FutureTimeoutError):
            scoped_import.result(timeout=0.2)
        release_writer.set()
        invite.result(timeout=5)
        with pytest.raises(TeamAccessError) as rejected:
            scoped_import.result(timeout=5)
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(AtlasTeamMembershipRow)
        ) == 0


@pytest.mark.parametrize(
    ("local_email", "alias_field"),
    [
        ("ada@example.test", "email"),
        ("ａｄａ@example.test", "email"),
        ("ａｄａ@example.test", "username"),
    ],
)
def test_local_email_producer_revalidates_after_scoped_import_wins(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
    local_email: str,
    alias_field: str,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    if alias_field == "username":
        gateway.principals["subject-ada"] = replace(
            gateway.principals["subject-ada"],
            username="ada@example.test",
            email="other@example.test",
        )
    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasTeamRow(
                team_id="team-directory",
                name="Directory Team",
                parent_team_id=None,
                status="active",
                created_at="2026-08-13T00:00:00+00:00",
                inherit_parent_documents=True,
            )
        )
        session.commit()
    team = build_postgres_team_access(
        postgres_runtime.session_factory,
        directory,
        repository,
    )

    class ScopeGrants:
        @staticmethod
        def validate_scope_values(*_args):
            return None

    identity = IdentityAccessService(repository, ScopeGrants(), directory)
    import_domain_held = Event()
    release_import = Event()
    import_thread_id: list[int] = []
    acquire_owner_locks = identity_owner.acquire_owner_locks

    def hold_import_between_domain_and_identity(
        session,
        *,
        domain_keys=(),
        identity_keys=(),
    ):
        if import_thread_id and get_ident() == import_thread_id[0] and domain_keys:
            acquire_owner_locks(session, domain_keys=domain_keys)
            import_domain_held.set()
            assert release_import.wait(5)
            return acquire_owner_locks(session, identity_keys=identity_keys)
        return acquire_owner_locks(
            session,
            domain_keys=domain_keys,
            identity_keys=identity_keys,
        )

    monkeypatch.setattr(
        identity_owner,
        "acquire_owner_locks",
        hold_import_between_domain_and_identity,
    )

    def import_members():
        import_thread_id.append(get_ident())
        return team.import_directory_members(
            admin,
            "team-directory",
            "directory-main",
            TeamDirectoryMemberImportRequest(
                external_subjects=["subject-ada"],
                role="member",
                idempotency_key="directory-wins-local-email-race",
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        scoped_import = pool.submit(import_members)
        assert import_domain_held.wait(5), scoped_import.exception(timeout=1)
        invite = pool.submit(
            identity.create_invite,
            admin,
            UserInviteCreateRequest(
                display_name="Local Ada",
                email=local_email,
                system_role="user",
                idempotency_key="directory-local-email-race",
            ),
        )
        with pytest.raises(FutureTimeoutError):
            invite.result(timeout=0.2)
        release_import.set()
        scoped_import.result(timeout=5)
        with pytest.raises(IdentityAccessError) as rejected:
            invite.result(timeout=5)
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AtlasTeamMembershipRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AtlasUserInviteRow)
        ) == 0
def test_scoped_project_import_commits_atomically_and_retry_converges(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = reset_identity_rows(postgres_runtime)
    directory, repository, _gateway, _cipher = build_service(postgres_runtime)
    directory.create_connection(admin, connection_payload("directory-main", 1))
    with postgres_runtime.session_factory() as session:
        session.add(
            AtlasProjectRow(
                project_id="project-directory",
                name="Directory Project",
                policy_profile_id="policy-default",
                status="active",
            )
        )
        session.commit()
    project = build_postgres_project_governance(
        postgres_runtime.session_factory,
        directory,
        repository,
    )
    payload = ProjectDirectoryMemberImportRequest(
        external_subjects=["subject-ada", "subject-grace"],
        role="contributor",
        idempotency_key="project-directory-response-loss",
    )

    first = project.import_directory_members(
        admin,
        "project-directory",
        "directory-main",
        payload,
    )
    replay = project.import_directory_members(
        admin,
        "project-directory",
        "directory-main",
        payload,
    )
    assert replay.actor_ids == first.actor_ids
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 3
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasPermissionGrantRow)
        ) == 2
        grants = session.scalars(select(AtlasPermissionGrantRow)).all()
        assert {grant.role for grant in grants} == {"contributor"}
        assert {grant.effect for grant in grants} == {"allow"}
        successful_audits = session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        )

    prepare_scoped_import = directory.prepare_scoped_import

    def prepare_then_disable_source(connection_id, external_subjects):
        preparation = prepare_scoped_import(connection_id, external_subjects)
        with postgres_runtime.session_factory() as session:
            connection = session.get(AtlasDirectoryConnectionRow, connection_id)
            assert connection is not None
            connection.enabled = False
            session.commit()
        return preparation

    monkeypatch.setattr(
        directory,
        "prepare_scoped_import",
        prepare_then_disable_source,
    )
    with pytest.raises(ProjectGovernanceError) as rejected:
        project.import_directory_members(
            admin,
            "project-directory",
            "directory-main",
            ProjectDirectoryMemberImportRequest(
                external_subjects=["subject-katherine", "subject-linus"],
                role="viewer",
                idempotency_key="project-directory-source-change",
            ),
        )
    assert rejected.value.status_code == 409
    with postgres_runtime.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtlasUserRow)) == 3
        assert session.scalar(
            select(func.count()).select_from(AtlasExternalIdentityRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasPermissionGrantRow)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AtlasAuditEventRow)
        ) == successful_audits