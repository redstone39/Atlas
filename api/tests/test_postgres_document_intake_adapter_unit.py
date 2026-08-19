from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_owner import (
    document_processing as processing_owner,
)
from atlas_production.infrastructure import (
    postgres_document_intake_adapter as intake_adapter,
)
from atlas_production.infrastructure import (
    postgres_document_upload as document_upload,
)
from atlas_production.infrastructure.postgres_document_intake_adapter import (
    DocumentIntakeJourneyFacade,
    PostgresDocumentIntakeAdapter,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
)
from atlas_production.infrastructure.processing_jobs_authorization import (
    RbacProcessingJobsAuthorization,
)
from atlas_production.infrastructure.postgres_document_upload import (
    NewDocumentUploadCommand,
    NewDocumentUploadInput,
    NewDocumentUploadJourneyCommand,
    NewDocumentUploadRequestBoundaryCommand,
    _terminal_audit_rows,
    _validate_input,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    AcceptProcessingExecutionCommand,
    CaptureProcessingExecutionCommand,
    DocumentLifecycleMutationCommand,
    ProcessingExecutionAcceptanceWriter,
    attach_document_job_request_projections,
    document_processing_acceptance_identity,
    document_processing_acceptance_lock_identities,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.document_intake.public import (
    DocumentLibraryItemProjection,
    DocumentLibraryRequestProjection,
    DocumentLifecycleRequestInput,
    RequestedDocumentScopeProjection,
)
from atlas_production.modules.processing_pipeline.public import (
    ProcessingControlResult,
    ProcessingJobAuthorizationState,
    ProcessingJobListBatch,
    ProcessingJobView,
    ProcessingProfilePin,
    VerifiedDocumentRestoreSet,
)
from atlas_production.modules.identity_access.records import TeamRecord, UserRecord
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.shared.public import AuditEventRecord


NOW = "2026-07-18T00:00:00+00:00"


def _command() -> NewDocumentUploadInput:
    document = DocumentRecord(
        document_id="document-1",
        title="Manual",
        source_digest="digest",
        source_kind="file_upload",
        document_format="pdf",
        content_type="application/pdf",
        source_byte_size=3,
        raw_sha256="digest",
        scope_type="project",
        scope_id="project-1",
        original_artifact_id="artifact-1",
    )
    version = DocumentVersionRecord(
        document_version_id="version-1",
        document_id=document.document_id,
        title=document.title,
        source_kind=document.source_kind,
        document_format=document.document_format,
        source_digest=document.source_digest,
        content_digest="content",
        created_at=NOW,
        original_artifact_id=document.original_artifact_id,
        content_type=document.content_type,
    )
    tag = DocumentTagRecord(document.document_id, "project", "project-1", NOW)
    publication = SimpleNamespace(
        attempt=SimpleNamespace(parent_resource_id=document.document_id),
        artifact=SimpleNamespace(
            artifact_id=document.original_artifact_id,
            document_version_id=version.document_version_id,
            parent_resource_id=document.document_id,
            parent_lifecycle_epoch=0,
            owner_scope_type="project",
            owner_scope_id="project-1",
            content_type="application/pdf",
        ),
        blob=SimpleNamespace(
            checksum_value="digest", byte_size=3, content_type="application/pdf"
        ),
        verified_tag_scopes=frozenset({("project", "project-1")}),
    )
    audit = AuditEventRecord(
        "audit-1",
        "document_uploaded",
        "user-1",
        "document:document-1",
        "project-1",
        "processing.retry_is_completed",
        {
            "document_id": "document-1",
            "access_decision_ids": ["decision-1"],
            "operation": "document-upload",
            "request_id": "request-1",
        },
        NOW,
        scope_type="project",
        scope_id="project-1",
        document_id="document-1",
    )
    return NewDocumentUploadInput(
        media_type="application/pdf",
        document=document,
        version=version,
        tags=(tag,),
        artifact_publication=publication,  # type: ignore[arg-type]
        job_kind="ingest",
        idempotency_scope="document-upload",
        idempotency_key="request-1",
        created_by="user-1",
        audit_events=(audit,),
        execution_snapshot=SimpleNamespace(acceptance_request_digest="f" * 64),  # type: ignore[arg-type]
        authorization_decisions=(
            SimpleNamespace(
                decision_id="decision-1",
                allowed=True,
                actor_type="user",
                actor_id="user-1",
                action="document_register",
                scope_type="project",
                scope_id="project-1",
            ),
        ),
    )


def test_document_intake_adapter_has_complete_public_port_parity() -> None:
    expected = {
        "get_document",
        "list_documents",
        "put_document",
        "document_exists",
        "replace_tags",
        "tags_for_document",
        "scope_label",
        "active_document_version_id",
        "processing_document_version_id",
        "create_document_version",
        "count_ready_evidence",
        "append_audit",
        "list_document_audit_events",
    }
    assert expected <= set(dir(PostgresDocumentIntakeAdapter))


def test_document_library_projection_is_one_request_bounded_fact_graph() -> None:
    signature = inspect.signature(PostgresDocumentIntakeAdapter.document_library_projection)
    assert {
        "actor_type",
        "actor_id",
        "presented_browser_session_token",
        "document_id",
        "include_events",
    } <= set(
        signature.parameters
    )
    assert DocumentLibraryRequestProjection.__dataclass_fields__.keys() == {
        "authenticated_actor",
        "items",
        "authorization_state",
    }
    source = inspect.getsource(PostgresDocumentIntakeAdapter.document_library_projection)
    assert source.count("with self.session_factory() as session:") == 1
    assert "AtlasEvidenceRow" in source
    assert "AtlasArtifactRow" in source
    assert "authorization_state" in source


def test_document_library_projection_owns_route_action_decisions() -> None:
    assert {
        "can_view",
        "can_administer",
        "can_edit",
        "can_view_logs",
        "download_available",
    } <= set(DocumentLibraryItemProjection.__dataclass_fields__)
    assert {
        "scope_type",
        "scope_id",
        "exists",
        "active",
        "label",
        "can_upload",
        "denial_audit_event",
    } == set(RequestedDocumentScopeProjection.__dataclass_fields__)
    signature = inspect.signature(PostgresDocumentIntakeAdapter.requested_scope_projection)
    assert {
        "actor_type",
        "actor_id",
        "presented_browser_session_token",
        "scope_type",
        "scope_id",
        "record_upload_denial",
    } <= set(signature.parameters)


class _ProjectionScalarRows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _ProjectionExecuteRows:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def scalars(self):
        return list(self.values)


class _RequestedScopeSession:
    def __init__(self, project: AtlasProjectRow) -> None:
        self.project = project

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, row_type, key):
        if row_type is AtlasProjectRow and key == self.project.project_id:
            return self.project
        return None


def test_requested_project_scope_marks_retired_project_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = UserRecord(
        "user-admin", "Admin", None, "admin", None, True, "user", NOW
    )
    project = AtlasProjectRow(
        project_id="project-retired",
        name="Retired",
        policy_profile_id="policy-default",
        status="retired",
    )
    session = _RequestedScopeSession(project)
    monkeypatch.setattr(intake_adapter, "read_session_actor", lambda *_args: actor)
    monkeypatch.setattr(
        intake_adapter,
        "_authorization_state",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        intake_adapter,
        "resolve_access",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )

    projection = PostgresDocumentIntakeAdapter(
        lambda: session
    ).requested_scope_projection(
        actor_type="user",
        actor_id=actor.actor_id,
        presented_browser_session_token="browser-token",
        scope_type="project",
        scope_id=project.project_id,
    )

    assert projection.exists is True
    assert projection.active is False
    assert projection.can_upload is False


class _ProjectionSession:
    def __init__(self, documents, tags, artifacts):
        self.documents = documents
        self.tags = tags
        self.artifacts = artifacts

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalars(self, statement):
        description = statement.column_descriptions[0]
        entity = description.get("entity")
        entity_name = getattr(entity, "__name__", "")
        if entity_name == "AtlasDocumentRow":
            values = (
                [document.document_id for document in self.documents]
                if description.get("name") == "document_id"
                else self.documents
            )
        elif entity_name == "AtlasDocumentTagRow":
            values = self.tags
        elif entity_name == "AtlasTeamRow":
            values = [SimpleNamespace(team_id="team-1", name="Team One")]
        elif entity_name == "AtlasProjectRow":
            values = [
                SimpleNamespace(project_id="project-1", name="Project One")
            ]
        else:
            values = []
        return _ProjectionScalarRows(values)

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        entity_name = getattr(entity, "__name__", "")
        return _ProjectionExecuteRows(
            self.artifacts if entity_name == "AtlasArtifactRow" else []
        )


def _project_download_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    *,
    team_role: str,
    project_admin: bool,
    original_scope: set[tuple[str, str]] | None = None,
    team_source_restricted: bool = False,
    artifacts: tuple[object, ...] | None = None,
    project_member_download: bool = False,
    project_status: str = "active",
    team_status: str = "active",
    actor_id: str = "user-1",
    system_admin: bool = False,
    capability: str = "download_available",
) -> dict[str, bool]:
    team_document = replace(
        _command().document,
        document_id="document-team",
        scope_type="team",
        scope_id="team-1",
        allow_member_download=False,
        source_download_restricted=team_source_restricted,
        original_artifact_id="artifact-team",
        uploader_actor_id="user-uploader",
    )
    project_document = replace(
        _command().document,
        document_id="document-project",
        scope_type="project",
        scope_id="project-1",
        allow_member_download=project_member_download,
        original_artifact_id="artifact-project",
        uploader_actor_id="user-uploader",
    )
    tags = (
        SimpleNamespace(
            document_id=team_document.document_id,
            tag_type="team",
            tag_id="team-1",
            created_at=NOW,
        ),
        SimpleNamespace(
            document_id=team_document.document_id,
            tag_type="project",
            tag_id="project-1",
            created_at=NOW,
        ),
        SimpleNamespace(
            document_id=project_document.document_id,
            tag_type="project",
            tag_id="project-1",
            created_at=NOW,
        ),
        SimpleNamespace(
            document_id=project_document.document_id,
            tag_type="team",
            tag_id="team-1",
            created_at=NOW,
        ),
    )
    if artifacts is None:
        artifacts = (
            SimpleNamespace(
                artifact_id="artifact-team",
                parent_resource_id=team_document.document_id,
                owner_scope_type="team",
                owner_scope_id="team-1",
            ),
            SimpleNamespace(
                artifact_id="artifact-project",
                parent_resource_id=project_document.document_id,
                owner_scope_type="project",
                owner_scope_id="project-1",
            ),
        )
    session = _ProjectionSession(
        (team_document, project_document),
        tags,
        artifacts,
    )
    actor = UserRecord(
        actor_id,
        "User",
        None,
        "admin" if system_admin else "member",
        None,
        True,
        "user",
        NOW,
    )
    authorization_state = ProcessingJobAuthorizationState(
        users={actor.actor_id: actor},
        projects={
            "project-1": ProjectRecord(
                "project-1", "Project", "policy-default", project_status
            )
        },
        teams={
            "team-1": TeamRecord(
                "team-1", "Team", None, team_status, NOW, True
            )
        },
        team_memberships={},
        permission_grants={},
    )
    monkeypatch.setattr(intake_adapter, "acquire_mixed_owner_locks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(intake_adapter, "read_session_actor", lambda *_args: actor)
    monkeypatch.setattr(intake_adapter, "_document_record", lambda row: row)
    monkeypatch.setattr(
        intake_adapter,
        "_authorization_state",
        lambda *_args, **_kwargs: authorization_state,
    )
    monkeypatch.setattr(intake_adapter, "is_system_admin", lambda *_args: system_admin)
    monkeypatch.setattr(
        intake_adapter,
        "direct_team_role",
        lambda _state, _actor_type, _actor_id, team_id: (
            team_role if team_id == "team-1" else None
        ),
    )
    monkeypatch.setattr(
        intake_adapter,
        "resolve_access",
        lambda _state, **kwargs: SimpleNamespace(
            allowed=(
                project_admin
                and kwargs["project_id"] == "project-1"
                and kwargs["action"] == "permission_manage"
            )
        ),
    )
    visible_scope = (
        {("team", "team-1"), ("project", "project-1")}
        if original_scope is None
        else original_scope
    )
    monkeypatch.setattr(
        intake_adapter,
        "effective_document_scope",
        lambda *_args, **_kwargs: set(visible_scope),
    )

    projection = PostgresDocumentIntakeAdapter(
        lambda: session
    ).document_library_projection(
        actor_type="user",
        actor_id=actor_id,
        presented_browser_session_token="browser-token",
    )

    return {
        item.document.document_id: getattr(item, capability)
        for item in projection.items
    }


@pytest.mark.parametrize(
    "actor",
    (
        UserRecord("system-admin", "Admin", None, "admin", None, True, "user", NOW),
        UserRecord("user-uploader", "Uploader", None, "member", None, True, "user", NOW),
    ),
)
def test_retired_project_denies_document_control_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    actor: UserRecord,
) -> None:
    document = replace(
        _command().document,
        uploader_actor_id="user-uploader",
        scope_type="project",
        scope_id="project-1",
    )
    session = SimpleNamespace(scalar=lambda _statement: object())
    monkeypatch.setattr(
        processing_owner,
        "acquire_mixed_owner_locks",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        processing_owner,
        "_document_record",
        lambda _row: document,
    )
    monkeypatch.setattr(
        processing_owner.identity_rows,
        "read_session_actor",
        lambda *_args: actor,
    )

    def state(status: str) -> ProcessingJobAuthorizationState:
        return ProcessingJobAuthorizationState(
            users={actor.actor_id: actor},
            projects={
                "project-1": ProjectRecord(
                    "project-1", "Project", "policy-default", status
                )
            },
            teams={},
            team_memberships={},
            permission_grants={},
        )

    monkeypatch.setattr(
        processing_owner._JobTransitionReadSql,
        "_authorization_state",
        staticmethod(lambda *_args, **_kwargs: state("retired")),
    )
    with pytest.raises(processing_owner._ProcessingControlAuthorizationDenied):
        processing_owner._authorize_document_control(
            session,
            document=document,
            presented_browser_session_token="browser-token",
            expected_actor_type=actor.actor_type,
            expected_actor_id=actor.actor_id,
        )

    monkeypatch.setattr(
        processing_owner._JobTransitionReadSql,
        "_authorization_state",
        staticmethod(lambda *_args, **_kwargs: state("active")),
    )
    assert processing_owner._authorize_document_control(
        session,
        document=document,
        presented_browser_session_token="browser-token",
        expected_actor_type=actor.actor_type,
        expected_actor_id=actor.actor_id,
    ) == actor


@pytest.mark.parametrize(
    "actor",
    (
        UserRecord("system-admin", "Admin", None, "admin", None, True, "user", NOW),
        UserRecord("user-uploader", "Uploader", None, "member", None, True, "user", NOW),
    ),
)
def test_retired_team_denies_document_control_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    actor: UserRecord,
) -> None:
    document = replace(
        _command().document,
        uploader_actor_id="user-uploader",
        scope_type="team",
        scope_id="team-1",
    )
    session = SimpleNamespace(scalar=lambda _statement: object())
    monkeypatch.setattr(
        processing_owner,
        "acquire_mixed_owner_locks",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        processing_owner,
        "_document_record",
        lambda _row: document,
    )
    monkeypatch.setattr(
        processing_owner.identity_rows,
        "read_session_actor",
        lambda *_args: actor,
    )

    def state(status: str) -> ProcessingJobAuthorizationState:
        return ProcessingJobAuthorizationState(
            users={actor.actor_id: actor},
            projects={},
            teams={
                "team-1": TeamRecord(
                    "team-1", "Team", None, status, NOW, True
                )
            },
            team_memberships={},
            permission_grants={},
        )

    monkeypatch.setattr(
        processing_owner._JobTransitionReadSql,
        "_authorization_state",
        staticmethod(lambda *_args, **_kwargs: state("retired")),
    )
    with pytest.raises(processing_owner._ProcessingControlAuthorizationDenied):
        processing_owner._authorize_document_control(
            session,
            document=document,
            presented_browser_session_token="browser-token",
            expected_actor_type=actor.actor_type,
            expected_actor_id=actor.actor_id,
        )

    monkeypatch.setattr(
        processing_owner._JobTransitionReadSql,
        "_authorization_state",
        staticmethod(lambda *_args, **_kwargs: state("active")),
    )
    assert processing_owner._authorize_document_control(
        session,
        document=document,
        presented_browser_session_token="browser-token",
        expected_actor_type=actor.actor_type,
        expected_actor_id=actor.actor_id,
    ) == actor


@pytest.mark.parametrize(
    "actor",
    (
        UserRecord("system-admin", "Admin", None, "admin", None, True, "user", NOW),
        UserRecord("user-uploader", "Uploader", None, "member", None, True, "user", NOW),
    ),
)
def test_retired_project_denies_job_control_until_reactivation(
    actor: UserRecord,
) -> None:
    document = replace(
        _command().document,
        uploader_actor_id="user-uploader",
        scope_type="project",
        scope_id="project-1",
    )

    def projection(status: str):
        return SimpleNamespace(
            document=document,
            tag_refs=(("project", "project-1"), ("team", "team-1")),
            authorization_state=ProcessingJobAuthorizationState(
                users={actor.actor_id: actor},
                projects={
                    "project-1": ProjectRecord(
                        "project-1", "Project", "policy-default", status
                    )
                },
                teams={},
                team_memberships={},
                permission_grants={},
            ),
        )

    authority = RbacProcessingJobsAuthorization()
    assert authority.can_read(projection("retired"), actor) is False
    assert authority.can_control(projection("retired"), actor) is False
    assert authority.can_read(projection("active"), actor) is (
        actor.system_role == "admin"
    )
    assert authority.can_control(projection("active"), actor) is True


@pytest.mark.parametrize(
    "actor",
    (
        UserRecord("system-admin", "Admin", None, "admin", None, True, "user", NOW),
        UserRecord("user-uploader", "Uploader", None, "member", None, True, "user", NOW),
    ),
)
def test_retired_team_denies_job_control_until_reactivation(
    actor: UserRecord,
) -> None:
    document = replace(
        _command().document,
        uploader_actor_id="user-uploader",
        scope_type="team",
        scope_id="team-1",
    )

    def projection(status: str):
        return SimpleNamespace(
            document=document,
            tag_refs=(("team", "team-1"), ("project", "project-1")),
            authorization_state=ProcessingJobAuthorizationState(
                users={actor.actor_id: actor},
                projects={},
                teams={
                    "team-1": TeamRecord(
                        "team-1", "Team", None, status, NOW, True
                    )
                },
                team_memberships={},
                permission_grants={},
            ),
        )

    authority = RbacProcessingJobsAuthorization()
    assert authority.can_read(projection("retired"), actor) is False
    assert authority.can_control(projection("retired"), actor) is False
    assert authority.can_read(projection("active"), actor) is (
        actor.system_role == "admin"
    )
    assert authority.can_control(projection("active"), actor) is True


@pytest.mark.parametrize(
    ("actor_id", "system_admin"),
    (("user-uploader", False), ("system-admin", True)),
)
def test_retired_project_hides_document_actions_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    actor_id: str,
    system_admin: bool,
) -> None:
    retired = _project_download_capabilities(
        monkeypatch,
        team_role="member",
        project_admin=False,
        project_status="retired",
        actor_id=actor_id,
        system_admin=system_admin,
        capability="can_edit",
    )
    assert retired["document-project"] is False

    active = _project_download_capabilities(
        monkeypatch,
        team_role="member",
        project_admin=False,
        project_status="active",
        actor_id=actor_id,
        system_admin=system_admin,
        capability="can_edit",
    )
    assert active["document-project"] is True


@pytest.mark.parametrize("capability", ("can_view", "download_available"))
def test_retired_project_hides_cross_tagged_document_reads_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
) -> None:
    retired = _project_download_capabilities(
        monkeypatch,
        team_role="member",
        project_admin=False,
        project_status="retired",
        project_member_download=True,
        capability=capability,
    )
    assert retired["document-project"] is False

    active = _project_download_capabilities(
        monkeypatch,
        team_role="member",
        project_admin=False,
        project_status="active",
        project_member_download=True,
        capability=capability,
    )
    assert active["document-project"] is True


@pytest.mark.parametrize(
    ("actor_id", "system_admin"),
    (("user-uploader", False), ("system-admin", True)),
)
def test_retired_team_hides_document_actions_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    actor_id: str,
    system_admin: bool,
) -> None:
    retired = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        team_status="retired",
        actor_id=actor_id,
        system_admin=system_admin,
        capability="can_edit",
    )
    assert retired["document-team"] is False

    active = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        team_status="active",
        actor_id=actor_id,
        system_admin=system_admin,
        capability="can_edit",
    )
    assert active["document-team"] is True


@pytest.mark.parametrize("capability", ("can_view", "download_available"))
def test_retired_team_hides_cross_tagged_document_reads_until_reactivation(
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
) -> None:
    retired = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        team_status="retired",
        capability=capability,
    )
    assert retired["document-team"] is False

    active = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        team_status="active",
        capability=capability,
    )
    assert active["document-team"] is True


@pytest.mark.parametrize(
    ("team_role", "project_admin", "expected"),
    (
        ("admin", False, {"document-team": True, "document-project": False}),
        ("member", True, {"document-team": False, "document-project": True}),
        ("uploader", False, {"document-team": False, "document-project": False}),
        ("member", False, {"document-team": False, "document-project": False}),
    ),
)
def test_document_projection_uses_exact_owner_admin_for_false_member_policy(
    monkeypatch: pytest.MonkeyPatch,
    team_role: str,
    project_admin: bool,
    expected: dict[str, bool],
) -> None:
    assert _project_download_capabilities(
        monkeypatch,
        team_role=team_role,
        project_admin=project_admin,
    ) == expected


def test_document_projection_keeps_source_acl_and_artifact_gates_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_denied = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        team_source_restricted=True,
    )
    acl_denied = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        original_scope=set(),
    )
    artifact_denied = _project_download_capabilities(
        monkeypatch,
        team_role="admin",
        project_admin=False,
        artifacts=(),
    )

    assert source_denied["document-team"] is False
    assert acl_denied["document-team"] is False
    assert artifact_denied["document-team"] is False


def test_processing_detail_projection_derives_document_from_job_identity() -> None:
    signature = inspect.signature(
        PostgresDocumentProcessingAdapter.get_document_job_request_projection
    )
    assert "job_id" in signature.parameters
    assert "document_id" not in signature.parameters


def test_route_facing_document_and_processing_commands_are_typed() -> None:
    assert {
        "patch_document",
        "disable_document",
        "begin_restore",
        "finish_restore",
        "refresh_or_reindex",
    } <= set(dir(DocumentIntakeJourneyFacade))
    assert {
        "presented_browser_session_token",
        "actor_type",
        "actor_id",
        "expected_document",
        "document",
        "tags",
        "audit_events",
    } <= set(DocumentLifecycleRequestInput.__dataclass_fields__)
    assert {
        "stop_processing_job_request",
        "retry_processing_job_request",
        "capture_processing_execution",
    } <= set(dir(PostgresDocumentProcessingAdapter))
    assert "audit_event" in ProcessingControlResult.__dataclass_fields__
    assert {
        "document_id",
        "resource_lifecycle_epoch",
        "active_fence",
        "artifacts",
        "reusable_processing_generation",
    } == set(VerifiedDocumentRestoreSet.__dataclass_fields__)
    facade_source = inspect.getsource(DocumentIntakeJourneyFacade)
    assert "changes_download_policy" in facade_source
    assert 'control_action="admin" if changes_download_policy else "edit"' in facade_source
    projection_source = inspect.getsource(
        PostgresDocumentIntakeAdapter.document_library_projection
    )
    assert "original_artifact.parent_resource_id" in projection_source
    assert "original_artifact.owner_scope_type" in projection_source
    lifecycle_source = inspect.getsource(DocumentLifecycleMutationCommand.execute)
    assert "restore storage verification became stale" in lifecycle_source
    assert "document lifecycle attribution is cross-wired" in lifecycle_source
    capture_source = inspect.getsource(
        PostgresDocumentProcessingAdapter.capture_processing_execution
    )
    assert "CaptureProcessingExecutionCommand" in capture_source
    assert "accept_processing_job" not in capture_source


def test_raw_document_repository_writes_fail_closed() -> None:
    adapter = PostgresDocumentIntakeAdapter(lambda: pytest.fail("opened SQL session"))
    command = _command()
    with pytest.raises(RuntimeError, match="raw document writes are disabled"):
        adapter.put_document(command.document)
    with pytest.raises(RuntimeError, match="raw tag writes are disabled"):
        adapter.replace_tags(command.document.document_id, [])
    with pytest.raises(RuntimeError, match="raw version writes are disabled"):
        adapter.create_document_version(command.document)


def test_named_upload_requires_one_complete_document_graph() -> None:
    command = _command()
    _validate_input(command)

    with pytest.raises(ValueError, match="graph is incomplete"):
        _validate_input(replace(command, tags=()))
    with pytest.raises(ValueError, match="graph is incomplete"):
        _validate_input(replace(command, audit_events=()))


@pytest.mark.parametrize("scope_type", ("project", "team"))
def test_upload_terminal_rejects_retired_owner_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    scope_type: str,
) -> None:
    events: list[str] = []
    scope_id = f"{scope_type}-1"
    command = _command()
    command.document.scope_type = scope_type
    command.document.scope_id = scope_id
    command.tags[0].tag_type = scope_type
    command.tags[0].tag_id = scope_id
    command.artifact_publication.artifact.owner_scope_type = scope_type
    command.artifact_publication.artifact.owner_scope_id = scope_id
    command.artifact_publication.verified_tag_scopes = frozenset(
        {(scope_type, scope_id)}
    )
    command.authorization_decisions[0].scope_type = scope_type
    command.authorization_decisions[0].scope_id = scope_id
    command = replace(
        command,
        audit_events=(
            replace(
                command.audit_events[0],
                project_id=scope_id if scope_type == "project" else None,
                scope_type=scope_type,
                scope_id=scope_id,
            ),
        ),
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, row_type, _key, **_kwargs):
            assert row_type.__name__ == f"Atlas{scope_type.title()}Row"
            return SimpleNamespace(status="retired")

        def rollback(self):
            events.append("rollback")

    monkeypatch.setattr(
        document_upload,
        "new_document_original_artifact_lock_identities",
        lambda _publication: (),
    )
    monkeypatch.setattr(
        document_upload,
        "acquire_mixed_owner_locks",
        lambda *_args, **kwargs: events.append(
            f"locks:{f'{scope_type}:{scope_type}:{scope_id}' in kwargs['exclusive_identity_keys']}"
        ),
    )
    monkeypatch.setattr(
        document_upload,
        "NewDocumentOriginalArtifactPublicationWriter",
        lambda _session: pytest.fail("retired owner must not publish"),
    )

    with pytest.raises(
        document_upload.DocumentUploadReplayConflict,
        match=f"{scope_type.title()} is no longer active",
    ):
        NewDocumentUploadCommand(Session).execute(command)

    assert events == ["locks:True", "rollback"]

def test_upload_and_adapter_do_not_recreate_an_aggregate_or_generic_uow() -> None:
    source = inspect.getsource(NewDocumentUploadCommand)
    adapter_source = inspect.getsource(PostgresDocumentIntakeAdapter)
    assert "NewDocumentOriginalArtifactPublicationWriter" in source
    assert "ProcessingExecutionAcceptanceWriter" in source
    assert "session.commit()" in source
    for forbidden in (
        "Atlas" + "Store",
        "FileBacked" + "Atlas" + "Store",
        "publish_graph",
        "UnitOfWork",
        "BoundedReadFactory",
    ):
        assert forbidden not in source
        assert forbidden not in adapter_source


def test_processing_acceptance_exposes_caller_transaction_seam() -> None:
    signature = inspect.signature(AcceptProcessingExecutionCommand.accept_job)
    assert "connection" in signature.parameters
    source = inspect.getsource(ProcessingExecutionAcceptanceWriter.accept_job)
    assert 'join_transaction_mode="rollback_only"' in source


def test_upload_uses_boundary_captured_configuration_without_terminal_recapture() -> None:
    source = inspect.getsource(NewDocumentUploadCommand.execute)
    lock_at = source.index("acquire_mixed_owner_locks")
    artifact_at = source.index("NewDocumentOriginalArtifactPublicationWriter")
    processing_at = source.index("ProcessingExecutionAcceptanceWriter")
    assert lock_at < artifact_at < processing_at
    assert '"artifact:control"' in source
    assert '"model-routing:configuration-control"' not in source
    assert '"processing-registry:configuration-control"' not in source

    capture_source = inspect.getsource(CaptureProcessingExecutionCommand.execute)
    capture_writer_source = inspect.getsource(
        __import__(
            "atlas_production.infrastructure.postgres_owner.document_processing",
            fromlist=["ProcessingExecutionCaptureWriter"],
        ).ProcessingExecutionCaptureWriter.execute
    )
    snapshot_source = inspect.getsource(
        __import__(
            "atlas_production.infrastructure.postgres_owner.document_processing",
            fromlist=["_capture_processing_execution_snapshot"],
        )._capture_processing_execution_snapshot
    )
    assert "ProcessingExecutionCaptureWriter" in capture_source
    assert "_capture_processing_execution_snapshot" in capture_writer_source
    assert '"model-routing:configuration-control"' in snapshot_source
    assert '"processing-registry:configuration-control"' in snapshot_source

    accept_source = inspect.getsource(AcceptProcessingExecutionCommand.accept_job)
    assert "if execution_snapshot is None" in accept_source
    assert "processing execution snapshot request is mismatched" in accept_source

    boundary_signature = inspect.signature(
        NewDocumentUploadRequestBoundaryCommand.execute
    )
    assert "presented_browser_session_token" in boundary_signature.parameters
    boundary_source = inspect.getsource(NewDocumentUploadRequestBoundaryCommand.execute)
    assert "read_session_actor" in boundary_source
    assert "identity:session:" in boundary_source


def test_new_upload_preallocates_the_complete_processing_lock_inventory() -> None:
    identity = document_processing_acceptance_identity(
        document_id="document-1",
        idempotency_scope="document-upload",
        idempotency_key="request-1",
    )
    assert identity == document_processing_acceptance_identity(
        document_id="document-1",
        idempotency_scope="document-upload",
        idempotency_key="request-1",
    )
    keys = document_processing_acceptance_lock_identities(
        document_id="document-1",
        document_version_id="version-1",
        idempotency_scope="document-upload",
        idempotency_key="request-1",
        identity=identity,
    )
    assert keys == tuple(sorted(set(keys)))
    assert {
        "document:allocation:document-1",
        "document:document:document-1",
        "document:version:version-1",
        "document:job-idempotency:document-upload:request-1",
        f"document:job:{identity.job_id}",
        f"document:outbox:{identity.outbox_id}",
        f"document:generation:document-1:{identity.processing_generation}",
        f"document:index:{identity.index_generation_id}",
        identity.outbox_work_identity_key,
    } == set(keys)


def test_lifecycle_owner_models_restore_as_begin_verify_then_finalize_or_rebuild() -> None:
    signature = inspect.signature(DocumentLifecycleMutationCommand.execute)
    assert "processing_acceptance" in signature.parameters
    source = inspect.getsource(DocumentLifecycleMutationCommand.execute)
    assert "ProcessingExecutionAcceptanceWriter" in source
    assert "AuditEventWriter(session).append_many" in source
    assert source.index("AuditEventWriter") < source.index("session.commit()")
    command = _command()
    disabled = replace(
        command.document,
        lifecycle_status="disabled",
        resource_lifecycle_epoch=1,
    )
    restoring = replace(disabled, lifecycle_status="restoring")
    assert "restore verification must run outside" in source
    assert "requires an active refresh or restoring rebuild" in source
    assert "starts_restore !=" not in source
    assert "rebuilds_restore" in source


def test_upload_replay_uses_semantic_request_identity_and_exact_graph() -> None:
    source = inspect.getsource(NewDocumentUploadCommand.canonical_result)
    assert 'event_metadata["operation"]' in source
    assert 'event_metadata["request_id"]' in source
    assert "_terminal_audit_rows" in source
    assert 'accepted_snapshot.get("acceptance_request_digest")' in source
    assert "_audit_semantically_matches" in source
    assert "AtlasTaskOutboxRow" in source
    assert "AtlasArtifactWriteAttemptRow" in source
    assert "AtlasStorageRequestLeaseRow" in source
    assert "AtlasStorageReconciliationFindingRow" in source
    execute_source = inspect.getsource(NewDocumentUploadCommand.execute)
    assert 'event_metadata["operation"]' in execute_source
    assert 'event_metadata["request_id"]' in execute_source
    assert "_terminal_audit_rows" in execute_source
    journey_source = inspect.getsource(NewDocumentUploadJourneyCommand.execute)
    assert "if begin.replayed:" in journey_source
    assert "begin.continue_external_work" in journey_source
    assert "canonical_attempt_id=begin.canonical_id" in journey_source


def test_terminal_upload_audits_exclude_matching_byte_plane_lifecycle_rows() -> None:
    expected = _command().audit_events
    lifecycle = SimpleNamespace(
        event_id="audit-lifecycle",
        event_type="artifact_write_started",
    )
    terminal = SimpleNamespace(
        event_id=expected[0].event_id,
        event_type=expected[0].event_type,
    )

    assert _terminal_audit_rows([lifecycle, terminal], expected) == (terminal,)


def test_upload_journey_captures_boundary_before_artifact_intent() -> None:
    source = inspect.getsource(NewDocumentUploadJourneyCommand.execute)
    boundary_at = source.index("boundary_command.execute")
    begin_at = source.index("begin_write_command.execute")
    assert boundary_at < begin_at
    assert '"access_decision_ids": decision_ids' in source
    assert '"operation": facts.idempotency_scope' in source
    assert '"request_id": facts.idempotency_key' in source


def test_attached_document_job_projection_fails_closed_on_missing_owner_facts() -> None:
    job = ProcessingJobView(
        job_id="job-1",
        job_kind="ingest",
        document_id="document-1",
        document_version_id="version-1",
        processing_generation=1,
        index_generation_id="index-1",
        stage="queued",
        status="queued",
        progress_current=0,
        progress_total=None,
        progress_unit="page",
        attempt=1,
        fence=0,
        failure_code=None,
        failure_detail=None,
        created_by="user-1",
        attempt_started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    command = _command()
    authorization = ProcessingJobAuthorizationState(
        {"user-1": UserRecord("user-1", "User", None, "member", None)},
        {},
        {},
        {},
        {},
    )
    complete = ProcessingJobListBatch(
        jobs=(job,),
        documents={"document-1": command.document},
        tag_refs_by_document={"document-1": (("project", "project-1"),)},
        profile_pins={
            ("document-1", 1): ProcessingProfilePin("profile-1", 1)
        },
        authorization_state=authorization,
    )
    projection = attach_document_job_request_projections(complete)[0]
    assert projection.job is job
    assert projection.document is command.document
    assert projection.authorization_state is authorization

    with pytest.raises(ValueError, match="scope is incomplete"):
        attach_document_job_request_projections(
            replace(complete, tag_refs_by_document={"document-1": ()})
        )
    with pytest.raises(ValueError, match="profile pin is incomplete"):
        attach_document_job_request_projections(
            replace(complete, profile_pins={})
        )
