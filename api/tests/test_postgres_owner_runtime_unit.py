from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from atlas_production.infrastructure.postgres_locks import (
    acquire_owner_locks,
    advisory_lock_key,
)
from atlas_production.infrastructure.postgres_owner.audit import (
    AccessDecisionWriter,
    AuditEventWriter,
    AuditRepository,
)
from atlas_production.infrastructure.postgres_owner.identity import (
    IdentityAuthorizationConflict,
    IdentityCurrentnessConflict,
    IdentityInvariantViolation,
    IdentityRepository,
    IdentityScopeAcceptanceChangeSet,
    IdentitySessionChangeSet,
)
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ProjectAclChangeSet,
    ProjectAclRepository,
    ProjectGrantWriter,
)
from atlas_production.infrastructure.postgres_owner.team import (
    TeamGovernanceChangeSet,
    TeamInvariantViolation,
    TeamMembershipWriter,
    TeamRepository,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.persistence import schema
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAccessDecisionRow,
    AtlasDirectoryConnectionRow,
    AtlasDirectoryConnectionSecretRow,
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserInviteRow,
    AtlasUserRow,
    directory_connection_row,
    directory_secret_row,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.modules.identity_access.directory_ports import (
    ScopedDirectoryImportChangeSet,
    ScopedDirectoryImportPreparation,
)
from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectorySecretRecord,
    ExternalIdentityRecord,
    directory_record_revision,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserInviteRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.shared.public import AuditEventRecord


SCHEMA_TABLES = frozenset(schema.OrmBase.metadata.tables)


class RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self._in_transaction = False

    def execute(self, statement, parameters=None):
        self.executed.append((str(statement), parameters))
        self._in_transaction = True
        return self

    def commit(self) -> None:
        self.commits += 1
        self._in_transaction = False

    def rollback(self) -> None:
        self.rollbacks += 1
        self._in_transaction = False

    def in_transaction(self) -> bool:
        return self._in_transaction

    @contextmanager
    def begin(self):
        self._in_transaction = True
        try:
            yield self
        except Exception:
            self.rollback()
            raise
        else:
            self.commit()


class RecordingEngine:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    @contextmanager
    def connect(self):
        yield self.connection


class RecordingSession:
    def __init__(
        self,
        *,
        fail_on_audit: bool = False,
        scalar_results: tuple[Any, ...] = (),
        scalars_results: tuple[tuple[Any, ...], ...] = (),
        get_results: tuple[Any, ...] = (),
    ) -> None:
        self.fail_on_audit = fail_on_audit
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.merged: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.scalar_results = list(scalar_results)
        self.scalars_results = list(scalars_results)
        self.get_results = list(get_results)
        self.scalar_statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters=None):
        self.executed.append((str(statement), parameters))
        return self

    def merge(self, row):
        self.merged.append(row)
        return row

    def scalar(self, statement):
        self.scalar_statements.append(str(statement))
        return self.scalar_results.pop(0) if self.scalar_results else None

    def scalars(self, statement):
        self.scalar_statements.append(str(statement))
        rows = self.scalars_results.pop(0) if self.scalars_results else ()
        return ScalarRows(rows)

    def get(self, _owner, _key):
        return self.get_results.pop(0) if self.get_results else None

    def add(self, row) -> None:
        if self.fail_on_audit and isinstance(row, AtlasAuditEventRow):
            raise RuntimeError("audit unavailable")
        self.added.append(row)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class SessionFactory:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> RecordingSession:
        self.calls += 1
        return self.session


class ScalarRows:
    def __init__(self, rows: tuple[Any, ...]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return list(self.rows)


def _event(event_id: str = "audit-owner-1") -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event_id,
        event_type="project_created",
        actor_id="user-admin",
        target_ref="project:project-1",
        project_id="project-1",
        message_code="project.is_ready_for_membership_setup",
        metadata={"policy_profile_id": "default"},
        created_at="2026-07-17T00:00:00+00:00",
    )


def _user() -> UserRecord:
    return UserRecord(
        actor_id="user-admin",
        display_name="Admin",
        email="admin@example.test",
        system_role="admin",
        password_digest="digest",
        created_at="2026-07-17T00:00:00+00:00",
    )


def _expected_user() -> UserRecord:
    return replace(_user(), active=False)


def _membership() -> TeamMembershipRecord:
    return TeamMembershipRecord(
        membership_id="tm-team-1-user-admin",
        team_id="team-1",
        member_actor_type="user",
        member_actor_id="user-admin",
        role="admin",
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
    )


def _invite(*, status: str = "accepted", scope_type: str = "team") -> UserInviteRecord:
    return UserInviteRecord(
        invite_id="invite-1",
        actor_id="user-admin",
        email="admin@example.test",
        display_name="Admin",
        system_role="admin",
        token_digest="invite-digest",
        token_fingerprint="invite-diges",
        status=status,
        created_at="2026-07-17T00:00:00+00:00",
        expires_at="2026-07-24T00:00:00+00:00",
        accepted_at=(
            "2026-07-17T00:01:00+00:00" if status == "accepted" else None
        ),
        scope_type=scope_type,
        scope_id="team-1" if scope_type == "team" else "project-1",
        scope_role="admin",
    )


def _current_invite_row(
    *, status: str = "pending", scope_type: str = "team"
) -> AtlasUserInviteRow:
    invite = _invite(status=status, scope_type=scope_type)
    return AtlasUserInviteRow(
        invite_id=invite.invite_id,
        actor_id=invite.actor_id,
        email=invite.email,
        display_name=invite.display_name,
        system_role=invite.system_role,
        token_digest=invite.token_digest,
        token_fingerprint=invite.token_fingerprint,
        status=status,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=None,
        revoked_at=None,
        scope_type=invite.scope_type,
        scope_id=invite.scope_id,
        scope_role=invite.scope_role,
    )


def _current_user_row() -> AtlasUserRow:
    user = _expected_user()
    return AtlasUserRow(
        actor_id=user.actor_id,
        display_name=user.display_name,
        email=user.email,
        system_role=user.system_role,
        password_digest=user.password_digest,
        active=False,
        actor_type=user.actor_type,
        created_at=user.created_at,
    )


def _grant() -> PermissionGrantRecord:
    return PermissionGrantRecord(
        grant_id="grant-project-1-user-admin",
        project_id="project-1",
        subject_type="user",
        subject_id="user-admin",
        role="admin",
        effect="allow",
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
    )
def _scoped_directory_source(
) -> tuple[DirectoryConnectionRecord, DirectorySecretRecord]:
    connection = DirectoryConnectionRecord(
        connection_id="directory-1",
        display_name="Directory",
        priority=1,
        provider_type="ldap",
        host="ldap.example.test",
        port=636,
        tls_mode="ldaps",
        connect_timeout_seconds=3,
        operation_timeout_seconds=5,
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
        created_at="2026-07-17T00:00:00+00:00",
        updated_at="2026-07-17T00:00:00+00:00",
    )
    secret = DirectorySecretRecord(
        connection_id=connection.connection_id,
        secret_kind="bind_password",
        ciphertext="ciphertext",
        nonce="nonce",
        key_id="key",
        version=1,
        algorithm="AES-256-GCM",
        storage_backend="database",
        updated_at=connection.updated_at,
    )
    return connection, secret


def _scoped_directory_preparation() -> ScopedDirectoryImportPreparation:
    connection, secret = _scoped_directory_source()
    user = UserRecord(
        actor_id="user-directory-target",
        display_name="Directory Target",
        email="target@example.test",
        system_role="user",
        password_digest=None,
        created_at=connection.created_at,
    )
    identity = ExternalIdentityRecord(
        actor_id=user.actor_id,
        connection_id=connection.connection_id,
        external_subject="subject-target",
        normalized_username="target",
        normalized_email="target@example.test",
        username="target",
        display_name=user.display_name,
        email=user.email,
        groups=(),
        department=None,
        title=None,
        employee_id=None,
        directory_enabled=True,
        status="current",
        last_refreshed_at=connection.updated_at,
    )
    return ScopedDirectoryImportPreparation(
        connection_id=connection.connection_id,
        source_revision=directory_record_revision(connection),
        credential_revision=directory_record_revision(secret),
        users=(user,),
        new_users=(user,),
        expected_users=((user.actor_id, None),),
        new_external_identities=(identity,),
        expected_external_identities=((user.actor_id, None),),
        expected_subject_bindings=((identity.external_subject, None),),
    )


def _scoped_team_import_change_set() -> ScopedDirectoryImportChangeSet:
    preparation = _scoped_directory_preparation()
    membership = TeamMembershipRecord(
        membership_id=f"tm-team-1-{preparation.actor_ids[0]}",
        team_id="team-1",
        member_actor_type="user",
        member_actor_id=preparation.actor_ids[0],
        role="member",
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
    )
    return ScopedDirectoryImportChangeSet(
        authorization_actor_id="user-admin",
        authorization_scope_type="team",
        authorization_scope_id="team-1",
        preparation=preparation,
        team_memberships=(membership,),
        expected_team_memberships=((membership.membership_id, None),),
        audit_events=(_event("audit-directory-import"),),
    )


def _scoped_project_import_change_set() -> ScopedDirectoryImportChangeSet:
    preparation = _scoped_directory_preparation()
    actor_id = preparation.actor_ids[0]
    grant = replace(
        _grant(),
        grant_id=f"grant-project-1-{actor_id}",
        subject_id=actor_id,
        role="viewer",
    )
    return ScopedDirectoryImportChangeSet(
        authorization_actor_id="user-admin",
        authorization_scope_type="project",
        authorization_scope_id="project-1",
        preparation=preparation,
        project_grants=(grant,),
        expected_project_grants=((grant.grant_id, None),),
        audit_events=(_event("audit-project-directory-import"),),
    )




def _decision() -> AccessDecisionRecord:
    return AccessDecisionRecord(
        decision_id="decision-1",
        actor_type="user",
        actor_id="user-admin",
        project_id="project-1",
        action="workspace_query",
        required_role="viewer",
        allowed=True,
        reason="system_admin",
        effective_role="admin",
        source_type="user",
        source_id="user-admin",
        explanation="System admin grants access.",
        created_at="2026-07-17T00:00:00+00:00",
    )


def _active_admin_row() -> AtlasUserRow:
    user = _user()
    return AtlasUserRow(
        actor_id=user.actor_id,
        display_name=user.display_name,
        email=user.email,
        system_role=user.system_role,
        password_digest=user.password_digest,
        active=True,
        actor_type=user.actor_type,
        created_at=user.created_at,
    )


def test_postgres_runtime_has_only_engine_session_factory_and_bootstrap(monkeypatch) -> None:
    assert [field.name for field in fields(PostgresRuntime)] == [
        "engine",
        "session_factory",
    ]
    monkeypatch.delenv("ATLAS_PRODUCTION_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="ATLAS_PRODUCTION_DATABASE_URL"):
        PostgresRuntime.from_environment()
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresRuntime.from_url("sqlite+pysqlite:///:memory:")

    for repository_type in (
        AuditRepository,
        IdentityRepository,
        TeamRepository,
        ProjectAclRepository,
    ):
        assert [field.name for field in fields(repository_type)] == ["session_factory"]


def test_target_owner_lock_keys_are_isolated_from_legacy_retrieval_keys() -> None:
    assert identity_actor_owner_key("user-1") == "identity:actor:user-1"
    assert team_owner_key("team-1") == "team:team:team-1"
    assert team_subject_owner_key("user", "user-1") == "team:subject:user:user-1"
    assert project_owner_key("project-1") == "project:project:project-1"
    assert (
        project_acl_subject_owner_key("team", "team-1")
        == "project:acl-subject:team:team-1"
    )

    owner_root = (
        Path(__file__).parents[1]
        / "src"
        / "atlas_production"
        / "infrastructure"
    )
    for module_name in ("identity.py", "project.py", "team.py"):
        source = (owner_root / "postgres_owner" / module_name).read_text()
        assert "persistence.retrieval_currentness import" not in source

    retrieval_source = (
        owner_root / "persistence" / "retrieval_currentness.py"
    ).read_text()
    for legacy_identity in (
        'f"identity:{actor_id}"',
        'f"acl-subject:{actor_type}:{actor_id}"',
        'f"project:{scope_id}"',
        'f"team:{team_id}"',
        'f"acl-subject:team:{team_id}"',
        'f"document-processing:document:{document_id}"',
    ):
        assert legacy_identity in retrieval_source


def test_audit_owner_exposes_no_arbitrary_reader_factory() -> None:
    audit_source = (
        Path(__file__).parents[1]
        / "src"
        / "atlas_production"
        / "infrastructure"
        / "postgres_owner"
        / "audit.py"
    ).read_text()
    for forbidden_shape in (
        "BoundedReadFactory",
        "SessionBoundedRead",
        "ReadObservabilityChangeSet",
        "read_observability",
        "reader_factory",
        "_reader_members",
        "hasattr(reader",
    ):
        assert forbidden_shape not in audit_source
    assert {
        name
        for name, member in AuditRepository.__dict__.items()
        if not name.startswith("_") and callable(member)
    } == {"recent_events"}

    factory = SessionFactory(RecordingSession())
    with pytest.raises(ValueError, match="between 1 and 200"):
        AuditRepository(factory).recent_events(limit=0)
    assert factory.calls == 0


def test_bootstrap_uses_one_locked_connection_for_alembic(monkeypatch) -> None:
    engine = RecordingEngine()
    engine.connection.engine = SimpleNamespace(
        url=make_url("postgresql://atlas:p%25ss@db/atlas_production")
    )
    runtime = PostgresRuntime(engine=engine, session_factory=lambda: None)
    calls: list[tuple[Any, str, str]] = []

    def fake_upgrade(config, revision: str) -> None:
        calls.append(
            (
                config.attributes["connection"],
                config.get_main_option("sqlalchemy.url"),
                revision,
            )
        )

    monkeypatch.setattr("alembic.command.upgrade", fake_upgrade)
    runtime.bootstrap_schema()

    assert calls == [
        (
            engine.connection,
            "postgresql://bootstrap-via-existing-connection",
            "head",
        )
    ]
    sql = [statement for statement, _ in engine.connection.executed]
    assert sql == [
        "SELECT 1",
        "SELECT pg_advisory_xact_lock(:lock_key)",
    ]


def test_bootstrap_releases_startup_lock_after_migration_failure(monkeypatch) -> None:
    engine = RecordingEngine()
    runtime = PostgresRuntime(engine=engine, session_factory=lambda: None)

    def fail_upgrade(_config, _revision: str) -> None:
        engine.connection._in_transaction = True
        raise RuntimeError("migration failed")

    monkeypatch.setattr("alembic.command.upgrade", fail_upgrade)
    with pytest.raises(RuntimeError, match="migration failed"):
        runtime.bootstrap_schema()

    assert engine.connection.rollbacks == 1
    assert engine.connection.executed[-1][0] == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert not engine.connection.in_transaction()


def test_schema_registration_is_pure_and_matches_existing_metadata() -> None:
    from atlas_production.infrastructure.persistence import OrmBase

    assert schema.OrmBase is OrmBase
    assert SCHEMA_TABLES
    assert SCHEMA_TABLES == frozenset(OrmBase.metadata.tables)
    migration_env = (
        Path(__file__).parents[1]
        / "src/atlas_production/migrations/env.py"
    ).read_text(encoding="utf-8")
    assert "persistence.schema import OrmBase" in migration_env
    assert "persistence." + "registry" not in migration_env


def test_owner_lock_order_is_domain_then_sorted_identity() -> None:
    session = RecordingSession()
    acquire_owner_locks(
        session,
        domain_keys=("team:domain-control",),
        identity_keys=("team:z", "team:a", "team:a"),
    )
    assert [parameters["lock_key"] for _, parameters in session.executed] == [
        advisory_lock_key("team:domain-control"),
        advisory_lock_key("team:a"),
        advisory_lock_key("team:z"),
    ]


def test_identity_scope_acceptance_allows_only_one_injected_writer() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        IdentityScopeAcceptanceChangeSet(
            user=_user(),
            expected_user=_expected_user(),
            invite=_invite(),
            team_membership=_membership(),
            project_grant=_grant(),
            audit_events=(_event(),),
        )
    with pytest.raises(ValueError, match="exactly one"):
        IdentityScopeAcceptanceChangeSet(
            user=_user(),
            expected_user=_expected_user(),
            invite=_invite(),
            audit_events=(_event(),),
        )
    with pytest.raises(ValueError, match="exactly one user"):
        IdentityScopeAcceptanceChangeSet(
            user=(_user(),),  # type: ignore[arg-type]
            expected_user=_expected_user(),
            invite=_invite(),
            team_membership=_membership(),
            audit_events=(_event(),),
        )
    with pytest.raises(ValueError, match="audit"):
        IdentityScopeAcceptanceChangeSet(
            user=_user(),
            expected_user=_expected_user(),
            invite=_invite(),
            team_membership=_membership(),
        )
    with pytest.raises(ValueError, match="does not match invite"):
        IdentityScopeAcceptanceChangeSet(
            user=replace(_user(), system_role="member"),
            expected_user=_expected_user(),
            invite=_invite(),
            team_membership=_membership(),
            audit_events=(_event(),),
        )


def test_owner_mutations_require_caller_supplied_audit() -> None:
    with pytest.raises(ValueError, match="audit"):
        IdentitySessionChangeSet(users=(_user(),))
    with pytest.raises(ValueError, match="audit"):
        TeamGovernanceChangeSet(memberships=(_membership(),))
    with pytest.raises(ValueError, match="audit"):
        ProjectAclChangeSet(grants=(_grant(),))


def test_identity_scope_acceptance_uses_one_session_for_audit_and_team_writer() -> None:
    session = RecordingSession(
        scalar_results=(_current_invite_row(), _current_user_row())
    )
    factory = SessionFactory(session)
    repository = IdentityRepository(factory)
    repository.identity_scope_acceptance(
        IdentityScopeAcceptanceChangeSet(
            user=_user(),
            expected_user=_expected_user(),
            invite=_invite(),
            team_membership=_membership(),
            audit_events=(_event(),),
        )
    )

    assert factory.calls == 1
    assert session.commits == 1
    assert session.rollbacks == 0
    assert any(isinstance(row, AtlasUserRow) for row in session.merged)
    assert any(isinstance(row, AtlasTeamMembershipRow) for row in session.merged)
    assert any(isinstance(row, AtlasAuditEventRow) for row in session.added)

    project_session = RecordingSession(
        scalar_results=(
            _current_invite_row(scope_type="project"),
            _current_user_row(),
        )
    )
    IdentityRepository(SessionFactory(project_session)).identity_scope_acceptance(
        IdentityScopeAcceptanceChangeSet(
            user=_user(),
            expected_user=_expected_user(),
            invite=_invite(scope_type="project"),
            project_grant=_grant(),
            audit_events=(_event("audit-scope-project-1"),),
        )
    )
    assert any(isinstance(row, AtlasPermissionGrantRow) for row in project_session.merged)
    assert project_session.commits == 1
def _scoped_import_session(
    *,
    fail_on_audit: bool = False,
    connection_enabled: bool = True,
    team_status: str = "active",
    secret_ciphertext: str = "ciphertext",
) -> RecordingSession:
    source_connection, source_secret = _scoped_directory_source()
    source_secret = replace(source_secret, ciphertext=secret_ciphertext)
    connection = replace(source_connection, enabled=connection_enabled)
    return RecordingSession(
        fail_on_audit=fail_on_audit,
        get_results=(
            _active_admin_row(),
            AtlasTeamRow(
                team_id="team-1",
                name="Team",
                parent_team_id=None,
                status=team_status,
                created_at="2026-07-17T00:00:00+00:00",
                inherit_parent_documents=True,
            ),
        ),
        scalar_results=(
            directory_connection_row(connection),
            directory_secret_row(source_secret),
            None,
            None,
            None,
            None,
        ),
        scalars_results=((), ()),
    )


def test_scoped_directory_import_commits_identity_access_and_audit_once() -> None:
    session = _scoped_import_session()
    factory = SessionFactory(session)

    IdentityRepository(factory).scoped_directory_import(
        _scoped_team_import_change_set()
    )

    assert factory.calls == 1
    assert session.commits == 1
    assert session.rollbacks == 0
    assert any(isinstance(row, AtlasUserRow) for row in session.merged)
    assert any(isinstance(row, AtlasTeamMembershipRow) for row in session.merged)
    assert any(isinstance(row, AtlasAuditEventRow) for row in session.added)


def test_scoped_project_import_locks_team_hierarchy_before_authority_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_domain_keys: tuple[str, ...] = ()

    def stop_after_locks(
        _session: RecordingSession,
        *,
        domain_keys: tuple[str, ...],
        identity_keys: tuple[str, ...],
    ) -> None:
        del identity_keys
        nonlocal captured_domain_keys
        captured_domain_keys = domain_keys
        raise RuntimeError("stop after locks")

    monkeypatch.setattr(
        "atlas_production.infrastructure.postgres_owner.identity.acquire_owner_locks",
        stop_after_locks,
    )
    with pytest.raises(RuntimeError, match="stop after locks"):
        IdentityRepository(SessionFactory(RecordingSession())).scoped_directory_import(
            _scoped_project_import_change_set()
        )

    assert "team:hierarchy-control" in captured_domain_keys
    assert "team:membership-control" in captured_domain_keys
    assert "project:acl-control:project-1" in captured_domain_keys


def test_scoped_directory_import_rolls_back_source_or_audit_failure() -> None:
    disabled_session = _scoped_import_session(connection_enabled=False)
    with pytest.raises(IdentityCurrentnessConflict, match="connection currentness"):
        IdentityRepository(
            SessionFactory(disabled_session)
        ).scoped_directory_import(_scoped_team_import_change_set())
    assert disabled_session.commits == 0
    assert disabled_session.rollbacks == 1
    assert disabled_session.merged == []

    audit_session = _scoped_import_session(fail_on_audit=True)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        IdentityRepository(SessionFactory(audit_session)).scoped_directory_import(
            _scoped_team_import_change_set()
        )
    assert audit_session.commits == 0
    assert audit_session.rollbacks == 1

def test_scoped_directory_import_rechecks_target_and_opaque_credential_revision() -> None:
    retired_team_session = _scoped_import_session(team_status="retired")
    with pytest.raises(IdentityAuthorizationConflict, match="Team is no longer active"):
        IdentityRepository(SessionFactory(retired_team_session)).scoped_directory_import(
            _scoped_team_import_change_set()
        )
    assert retired_team_session.commits == 0
    assert retired_team_session.merged == []

    rotated_secret_session = _scoped_import_session(secret_ciphertext="rotated")
    with pytest.raises(
        IdentityCurrentnessConflict,
        match="directory credential currentness changed",
    ):
        IdentityRepository(SessionFactory(rotated_secret_session)).scoped_directory_import(
            _scoped_team_import_change_set()
        )
    assert rotated_secret_session.commits == 0
    assert rotated_secret_session.merged == []





def test_scope_acceptance_rechecks_pending_invite_inside_the_write_session() -> None:
    session = RecordingSession(
        scalar_results=(_current_invite_row(status="revoked"), _current_user_row())
    )
    repository = IdentityRepository(SessionFactory(session))

    with pytest.raises(IdentityCurrentnessConflict, match="currentness"):
        repository.identity_scope_acceptance(
            IdentityScopeAcceptanceChangeSet(
                user=_user(),
                expected_user=_expected_user(),
                invite=_invite(),
                team_membership=_membership(),
                audit_events=(_event(),),
            )
        )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.merged == []
    assert len(session.scalar_statements) == 2


def test_scope_acceptance_rejects_a_stale_user_preimage() -> None:
    stale_row = _current_user_row()
    stale_row.display_name = "Changed Elsewhere"
    session = RecordingSession(
        scalar_results=(_current_invite_row(), stale_row)
    )

    with pytest.raises(IdentityCurrentnessConflict, match="currentness"):
        IdentityRepository(SessionFactory(session)).identity_scope_acceptance(
            IdentityScopeAcceptanceChangeSet(
                user=_user(),
                expected_user=_expected_user(),
                invite=_invite(),
                team_membership=_membership(),
                audit_events=(_event(),),
            )
        )

    assert session.rollbacks == 1
    assert session.merged == []


def test_final_admin_and_team_invariants_are_reread_in_the_write_session() -> None:
    current_admin = AtlasUserRow(
        actor_id="user-admin",
        display_name="Admin",
        email="admin@example.test",
        system_role="admin",
        password_digest="digest",
        active=True,
        actor_type="user",
        created_at="2026-07-17T00:00:00+00:00",
    )
    demoted = replace(_user(), system_role="member")
    identity_session = RecordingSession(scalars_results=((current_admin,),))
    with pytest.raises(IdentityInvariantViolation, match="System Admin"):
        IdentityRepository(SessionFactory(identity_session)).identity_session(
            IdentitySessionChangeSet(
                users=(demoted,),
                audit_events=(_event("audit-admin-demotion"),),
                protect_admin_count=True,
            )
        )
    assert identity_session.rollbacks == 1
    assert identity_session.merged == []

    current_team_admin_membership = AtlasTeamMembershipRow(
        membership_id="tm-team-1-user-admin",
        team_id="team-1",
        member_actor_type="user",
        member_actor_id="user-admin",
        role="admin",
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
        removed_at=None,
    )
    identity_team_session = RecordingSession(
        scalars_results=((current_team_admin_membership,), (current_admin,))
    )
    with pytest.raises(IdentityInvariantViolation, match="Team Admin"):
        IdentityRepository(SessionFactory(identity_team_session)).identity_session(
            IdentitySessionChangeSet(
                users=(replace(_user(), active=False),),
                audit_events=(_event("audit-team-admin-deactivate"),),
                protected_admin_team_ids=("team-1",),
            )
        )
    assert identity_team_session.rollbacks == 1
    assert advisory_lock_key("team:admin-control:team-1") in {
        parameters["lock_key"]
        for _statement, parameters in identity_team_session.executed
        if parameters is not None and "lock_key" in parameters
    }

    team_a = AtlasTeamRow(
        team_id="team-a",
        name="A",
        parent_team_id=None,
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
        inherit_parent_documents=True,
    )
    team_b = AtlasTeamRow(
        team_id="team-b",
        name="B",
        parent_team_id=None,
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
        inherit_parent_documents=True,
    )
    hierarchy_session = RecordingSession(scalars_results=((team_a, team_b),))
    with pytest.raises(TeamInvariantViolation, match="acyclic"):
        TeamRepository(SessionFactory(hierarchy_session)).team_governance(
            TeamGovernanceChangeSet(
                teams=(
                    TeamRecord(
                        "team-a",
                        "A",
                        "team-b",
                        "active",
                        "2026-07-17T00:00:00+00:00",
                    ),
                    TeamRecord(
                        "team-b",
                        "B",
                        "team-a",
                        "active",
                        "2026-07-17T00:00:00+00:00",
                    ),
                ),
                audit_events=(_event("audit-team-cycle"),),
                protect_hierarchy=True,
            )
        )
    assert hierarchy_session.rollbacks == 1

    current_membership = AtlasTeamMembershipRow(
        membership_id="tm-team-1-user-admin",
        team_id="team-1",
        member_actor_type="user",
        member_actor_id="user-admin",
        role="admin",
        status="active",
        created_at="2026-07-17T00:00:00+00:00",
        removed_at=None,
    )
    admin_session = RecordingSession(
        scalars_results=((current_membership,), (current_admin,))
    )
    with pytest.raises(TeamInvariantViolation, match="Team Admin"):
        TeamRepository(SessionFactory(admin_session)).team_governance(
            TeamGovernanceChangeSet(
                memberships=(replace(_membership(), role="member"),),
                audit_events=(_event("audit-team-demotion"),),
                protected_admin_team_ids=("team-1",),
            )
        )
    assert admin_session.rollbacks == 1


def test_identity_session_rolls_back_business_rows_when_audit_write_fails() -> None:
    session = RecordingSession(fail_on_audit=True)
    repository = IdentityRepository(SessionFactory(session))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.identity_session(
            IdentitySessionChangeSet(users=(_user(),), audit_events=(_event(),))
        )
    assert session.commits == 0
    assert session.rollbacks == 1
    assert any(isinstance(row, AtlasUserRow) for row in session.merged)


def test_team_project_and_session_bound_audit_writers_have_closed_shapes() -> None:
    team_session = RecordingSession()
    TeamRepository(SessionFactory(team_session)).team_governance(
        TeamGovernanceChangeSet(
            teams=(
                TeamRecord(
                    team_id="team-1",
                    name="Team 1",
                    parent_team_id=None,
                    status="active",
                    created_at="2026-07-17T00:00:00+00:00",
                ),
            ),
            memberships=(_membership(),),
            audit_events=(_event("audit-team-1"),),
        )
    )
    project_session = RecordingSession()
    ProjectAclRepository(SessionFactory(project_session)).project_acl(
        ProjectAclChangeSet(
            projects=(
                ProjectRecord(
                    project_id="project-1",
                    name="Project 1",
                    policy_profile_id="default",
                ),
            ),
            grants=(_grant(),),
            access_decisions=(_decision(),),
            audit_events=(_event("audit-project-1"),),
        )
    )
    audit_session = RecordingSession()
    AccessDecisionWriter(audit_session).append(_decision())
    AuditEventWriter(audit_session).append(_event("audit-read-1"))

    assert any(row.__class__.__name__ == "AtlasTeamRow" for row in team_session.merged)
    assert any(isinstance(row, AtlasProjectRow) for row in project_session.merged)
    assert any(isinstance(row, AtlasPermissionGrantRow) for row in project_session.merged)
    assert not any(
        isinstance(row, AtlasAccessDecisionRow) for row in project_session.merged
    )
    assert any(
        isinstance(row, AtlasAccessDecisionRow) for row in project_session.added
    )
    assert advisory_lock_key("audit:decision:decision-1") in {
        parameters["lock_key"]
        for _statement, parameters in project_session.executed
        if parameters is not None and "lock_key" in parameters
    }
    assert {type(row) for row in audit_session.added} == {
        AtlasAccessDecisionRow,
        AtlasAuditEventRow,
    }


def test_injected_writers_expose_no_commit_or_rollback() -> None:
    session = RecordingSession()
    for writer in (
        AuditEventWriter(session),
        AccessDecisionWriter(session),
        TeamMembershipWriter(session),
        ProjectGrantWriter(session),
    ):
        assert not hasattr(writer, "commit")
        assert not hasattr(writer, "rollback")
    assert not hasattr(AccessDecisionWriter(session), "merge")
