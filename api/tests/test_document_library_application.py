from __future__ import annotations

from dataclasses import dataclass, field, replace
from io import BytesIO
from types import SimpleNamespace
from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.modules.identity_access.records import UserRecord

from atlas_production.modules.document_intake.public import (
    DocumentLibraryApplication,
    DocumentLibraryExceptionTypes,
    DocumentRecord,
    DocumentTagRef,
    DocumentLibraryUpdateRequest,
)
from atlas_production.modules.processing_pipeline.public import (
    DocumentLifecycleProcessingAcceptance,
)


ACTOR = UserRecord("user-1", "User", None, "admin", None)


class _Principal:
    def current_user(self, _token):
        return ACTOR


def _http_client(application: DocumentLibraryApplication) -> TestClient:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=_Principal(), document_library=application)
    return TestClient(create_app(ApiComposition(**values)))


class _NeverRaised(Exception):
    pass


class _LifecycleDenied(Exception):
    def __init__(self):
        self.audit_event = SimpleNamespace(event_id="audit-lifecycle-denied")


class _CurrentnessConflict(Exception):
    pass

class _UploadReplayConflict(Exception):
    pass






@dataclass
class _Facade:
    refresh_requests: list[object] = field(default_factory=list)
    patch_requests: list[object] = field(default_factory=list)
    disable_requests: list[object] = field(default_factory=list)
    begin_restore_requests: list[object] = field(default_factory=list)
    finish_restore_requests: list[object] = field(default_factory=list)
    deny: bool = False
    conflict: bool = False
    order: list[str] | None = None



    def _authorize(self):
        if self.deny:
            raise _LifecycleDenied()
        if self.conflict:
            raise _CurrentnessConflict()

    def patch_document(self, request):
        self.patch_requests.append(request)
        self._authorize()
        if self.order is not None:
            self.order.append("accepted")
        return None

    def disable_document(self, request):
        self.disable_requests.append(request)
        self._authorize()
        if self.order is not None:
            self.order.append("accepted")
        return None

    def begin_restore(self, request):
        self.begin_restore_requests.append(request)
        self._authorize()
        if self.order is not None:
            self.order.append("accepted")
        return None

    def finish_restore(self, request):
        self.finish_restore_requests.append(request)
        self._authorize()
        if self.order is not None:
            self.order.append("accepted")
        if request.processing_acceptance is not None:
            return SimpleNamespace(job_id="job-restore")
        return None

    def refresh_or_reindex(self, request):
        self.refresh_requests.append(request)
        self._authorize()
        if self.order is not None:
            self.order.append("accepted")
        return SimpleNamespace(job_id="job-new")


@dataclass
class _Intake:
    items: tuple[object, ...]
    facade: _Facade = field(default_factory=_Facade)
    version_calls: list[str] = field(default_factory=list)
    can_upload: bool = True

    def document_library_projection(self, **_kwargs):
        return SimpleNamespace(items=self.items)

    def requested_scope_projection(self, **_kwargs):
        return SimpleNamespace(
            exists=True,
            active=True,
            can_upload=self.can_upload,
            denial_audit_event=SimpleNamespace(event_id="audit-upload-denied"),
        )

    def processing_document_version_id(self, document_id: str):
        self.version_calls.append(document_id)
        return "version-1"

    def journey_facade(self):
        return self.facade


@dataclass
class _Processing:
    current_job: object | None = None
    captures: list[dict[str, object]] = field(default_factory=list)

    def get_job(self, _job_id):
        return self.current_job

    def capture_processing_execution(self, **kwargs):
        self.captures.append(kwargs)
        return SimpleNamespace(snapshot="processing")


class _Unused:
    pass


@dataclass
class _RestoreProofs:
    reusable: bool = True
    fail: bool = False

    def verify(self, document):
        if self.fail:
            raise ValueError("restore verification failed")
        return SimpleNamespace(
            document_id=document.document_id,
            reusable_processing_generation=self.reusable,
        )


@dataclass
class _Uploads:
    intake: _Intake
    calls: list[dict[str, object]] = field(default_factory=list)

    replay_conflict: bool = False
    order: list[str] | None = None


    def upload(self, **kwargs):
        self.calls.append(kwargs)
        if self.replay_conflict:
            raise _UploadReplayConflict()
        list(kwargs["chunks"])
        document = replace(
            kwargs["document"],
            document_id=f"doc-public-synthetic-{len(self.calls)}",
        )
        self.intake.items = (*self.intake.items, _item(document))
        if self.order is not None:
            self.order.append("accepted")
        return SimpleNamespace(
            artifact=SimpleNamespace(artifact_id="artifact-upload"),
            replayed=False,
            publication=SimpleNamespace(
                version=SimpleNamespace(document_id=document.document_id),
                job=SimpleNamespace(job_id="job-upload"),
                audit=SimpleNamespace(event_id="audit-upload"),
            ),
        )


def _item(
    document: DocumentRecord,
    *,
    can_view: bool = True,
    can_view_logs: bool = True,
    download_available: bool = False,
):
    return SimpleNamespace(
        document=document,
        tags=(DocumentTagRef(tag_type=document.scope_type, tag_id=document.scope_id),),
        scope_labels=((document.scope_type, document.scope_id, document.scope_id),),
        can_view=can_view,
        can_administer=True,
        original_artifact_available=True,
        download_available=download_available,
        ready_evidence_count=0,
        can_view_logs=can_view_logs,
        events=(),
    )


def _application(
    *items,
    processing=None,
    uploads=None,
    restore_proofs=None,
    upload_replay_conflict=False,
    order=None,
):
    intake = _Intake(tuple(items), facade=_Facade(order=order))
    processing = processing or _Processing()
    dispatches: list[bool] = []
    def dispatch():
        dispatches.append(True)
        if order is not None:
            order.append("dispatch")

    application = DocumentLibraryApplication(
        intake,
        processing,
        uploads
        or _Uploads(
            intake,
            replay_conflict=upload_replay_conflict,
            order=order,
        ),
        restore_proofs or _RestoreProofs(),
        lambda **values: SimpleNamespace(**values),
        lambda *values: DocumentLifecycleProcessingAcceptance(*values),
        DocumentLibraryExceptionTypes(
            _NeverRaised,
            _NeverRaised,
            _UploadReplayConflict,
            _LifecycleDenied,
            _CurrentnessConflict,
        ),
        dispatch,
        new_id=lambda: "generated-id",
    )
    return application, intake, processing, dispatches


def _document(document_id: str, title: str, **changes):
    values = {
        "document_id": document_id,
        "title": title,
        "source_digest": "sha256:source",
        "scope_type": "project",
        "scope_id": "project-1",
        "uploader_actor_id": "user-1",
        "original_artifact_id": "artifact-1",
    }
    values.update(changes)
    return DocumentRecord(**values)


def test_list_projects_visible_documents_in_stable_title_order() -> None:
    hidden = _item(_document("doc-hidden", "A hidden"), can_view=False)
    second = _item(
        _document("doc-2", "Second"),
        download_available=True,
    )
    first = _item(_document("doc-1", "A title"))
    application, _intake, _processing, _dispatches = _application(
        hidden, second, first
    )
    actor = SimpleNamespace(actor_type="user", actor_id="user-1")

    outcome = application.list(
        actor=actor,
        session_token="session-1",
        scope_type=None,
        scope_id=None,
    )
    assert outcome.failure is None
    assert [item.document_id for item in outcome.value.documents] == ["doc-1", "doc-2"]
    assert [item.download_available for item in outcome.value.documents] == [
        False,
        True,
    ]


def _pdf_bytes() -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    return buffer.getvalue()


def test_document_library_http_route_preserves_owner_success_and_failure_mapping() -> None:
    application, _intake, _processing, _dispatches = _application(
        _item(_document("doc-1", "Document"))
    )
    client = _http_client(application)

    listed = client.get("/api/v1/admin/document-library")
    invalid_scope = client.get(
        "/api/v1/admin/document-library",
        params={"scope_type": "invalid", "scope_id": "scope-1"},
    )

    assert listed.status_code == 200
    assert [item["document_id"] for item in listed.json()["documents"]] == ["doc-1"]
    assert invalid_scope.status_code == 422

    assert invalid_scope.json()["message_code"] == "project.choose_a_valid_team_or_project"

def test_document_library_http_upload_preserves_acceptance_and_dispatch() -> None:
    application, _intake, _processing, dispatches = _application()
    client = _http_client(application)

    uploaded = client.post(
        "/api/v1/admin/document-library",
        data={
            "scope_type": "project",
            "scope_id": "project-1",
            "idempotency_key": "upload-http",
        },
        files={
            "file": (
                "manual.pdf",
                _pdf_bytes(),
                "application/pdf",
            )
        },
    )

    assert uploaded.status_code == 202
    assert uploaded.json()["request_id"] == "upload-http"
    assert uploaded.json()["artifact_id"] == "artifact-upload"
    assert uploaded.json()["job_id"] == "job-upload"
    assert uploaded.json()["audit_event_ref"] == "audit-upload"
    assert dispatches == [True]



@pytest.mark.parametrize(
    ("data", "files", "status_code", "message_code"),
    [
        (
            {"scope_type": "invalid", "scope_id": "project-1", "idempotency_key": "invalid-scope"},
            None,
            422,
            "project.choose_a_team_or_project_before_uploading",
        ),
        (
            {
                "scope_type": "project",
                "scope_id": "project-1",
                "tag_refs": "not-json",
                "idempotency_key": "invalid-tags",
            },
            None,
            422,
            "document.tags_were_not_valid",
        ),
        (
            {"scope_type": "project", "scope_id": "project-1", "idempotency_key": "missing-file"},
            None,
            422,
            "document.file_upload_requires_a_file",
        ),
        (
            {"scope_type": "project", "scope_id": "project-1", "idempotency_key": "broken-file"},
            {"file": ("broken.pdf", b"%PDF-broken", "application/pdf")},
            422,
            "document.file_is_corrupt_or_incomplete",
        ),
    ],
)
def test_document_library_http_upload_validation_matrix(
    data, files, status_code, message_code
) -> None:
    response = _http_client(_application()[0]).post(
        "/api/v1/admin/document-library",
        data=data,
        files=files,
    )
    assert response.status_code == status_code
    assert response.json()["message_code"] == message_code


def test_document_library_http_upload_acl_denial_preserves_audit_ref() -> None:
    application, intake, _processing, _dispatches = _application()
    intake.can_upload = False
    denied = _http_client(application).post(
        "/api/v1/admin/document-library",
        data={"scope_type": "project", "scope_id": "project-1", "idempotency_key": "denied-upload"},
        files={"file": ("manual.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert denied.status_code == 403
    assert denied.json()["audit_event_ref"] == "audit-upload-denied"

def test_document_library_http_matrix_uses_owner_for_every_lifecycle_route() -> None:
    active = _item(_document("doc-1", "Document"))
    application, intake, _processing, dispatches = _application(active)
    client = _http_client(application)

    updated = client.patch(
        "/api/v1/admin/document-library/doc-1",
        json={
            "description": " Updated description ",
            "allow_member_download": True,
            "idempotency_key": "update-http",
        },
    )
    refreshed = client.post(
        "/api/v1/admin/document-library/doc-1/refresh-searchable-content",
        headers={"Idempotency-Key": "refresh-http"},
    )
    disabled = client.post("/api/v1/admin/document-library/doc-1/disable")
    events = client.get("/api/v1/admin/document-library/doc-1/events")
    missing = client.get("/api/v1/admin/document-library/missing/events")

    assert updated.status_code == 200
    assert updated.json()["document"]["description"] == "Updated description"
    assert updated.json()["document"]["download_available"] is False
    assert refreshed.status_code == 202
    assert refreshed.json()["job_id"] == "job-new"
    assert disabled.status_code == 200
    assert disabled.json()["message_code"] == "document.is_disabled"
    assert events.status_code == 200
    assert events.json() == {"events": []}
    assert missing.status_code == 404
    assert missing.json()["message_code"] == "document.was_not_found"
    assert len(intake.facade.patch_requests) == 1
    assert len(intake.facade.refresh_requests) == 1
    assert len(intake.facade.disable_requests) == 1
    assert dispatches == [True]

    restoring = _item(
        _document("doc-disabled", "Disabled", lifecycle_status="disabled")
    )
    restore_application, restore_intake, _processing, restore_dispatches = _application(
        restoring
    )
    restored = _http_client(restore_application).post(
        "/api/v1/admin/document-library/doc-disabled/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["message_code"] == "document.is_restored"
    assert len(restore_intake.facade.begin_restore_requests) == 1
    assert len(restore_intake.facade.finish_restore_requests) == 1
    assert restore_dispatches == []


@pytest.mark.parametrize(
    ("document_changes", "processing_job", "method", "status_code", "message_code"),
    [
        (
            {"lifecycle_status": "disabled"},
            None,
            "refresh",
            409,
            "document.disabled_documents_cannot_update_searchable_content",
        ),
        (
            {"original_artifact_id": None},
            None,
            "refresh",
            422,
            "artifact.source_document_is_unavailable_this_file_cannot_be_processed",
        ),
        (
            {"processing_job_id": "job-failed"},
            SimpleNamespace(status="failed"),
            "refresh",
            409,
            "processing.use_the_job_retry_control_for_failed_or_stopped_work",
        ),
        (
            {"lifecycle_status": "active"},
            None,
            "restore",
            409,
            "document.only_a_disabled_document_can_be_restored",
        ),
    ],
)
def test_document_library_lifecycle_failure_matrix(
    document_changes, processing_job, method, status_code, message_code
) -> None:
    processing = _Processing(current_job=processing_job)
    application, _intake, _processing, _dispatches = _application(
        _item(_document("doc-1", "Document", **document_changes)),
        processing=processing,
    )
    outcome = getattr(application, method)(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        **({"idempotency_key": None} if method == "refresh" else {}),
    )
    assert outcome.failure.status_code == status_code
    assert outcome.failure.message_code == message_code


def test_document_library_lifecycle_acl_denial_preserves_audit_ref() -> None:
    item = _item(_document("doc-1", "Document"), can_view_logs=False)
    application, intake, _processing, _dispatches = _application(item)
    intake.facade.deny = True
    denied = _http_client(application).get(
        "/api/v1/admin/document-library/doc-1/events"
    )
    assert denied.status_code == 403
    assert denied.json()["audit_event_ref"] == "audit-lifecycle-denied"


def test_refresh_uses_string_document_id_and_clears_prior_generation_warnings() -> None:
    document = _document(
        "doc-1",
        "Document",
        warning_codes=["old_generation_warning"],
        processing_job_id=None,
    )
    item = _item(document)
    application, intake, processing, dispatches = _application(item)
    actor = SimpleNamespace(actor_type="user", actor_id="user-1")

    outcome = application.refresh(
        actor=actor,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key="refresh-key",
    )

    assert outcome.failure is None
    assert outcome.status_code == 202
    assert intake.version_calls == ["doc-1"]
    assert processing.captures[0]["document_id"] == "doc-1"
    assert intake.facade.refresh_requests[0].document.warning_codes == []
    assert outcome.value.job_id == "job-new"
    assert dispatches == [True]



@pytest.mark.parametrize("method", ["update", "refresh", "disable", "restore"])
def test_document_library_each_lifecycle_mutation_denies_before_dispatch(
    method: str,
) -> None:
    document = _document(
        "doc-1",
        "Document",
        lifecycle_status="disabled" if method == "restore" else "active",
    )
    application, intake, _processing, dispatches = _application(_item(document))
    intake.facade.deny = True
    kwargs = {
        "actor": ACTOR,
        "session_token": "session-1",
        "document_id": "doc-1",
    }
    if method == "update":
        kwargs["payload"] = DocumentLibraryUpdateRequest(
            description="Denied",
            idempotency_key="update-denied",
        )
    elif method == "refresh":
        kwargs["idempotency_key"] = "refresh-denied"

    outcome = getattr(application, method)(**kwargs)

    assert outcome.failure is not None
    assert outcome.failure.status_code == 403
    assert outcome.failure.audit_event_ref == "audit-lifecycle-denied"
    assert dispatches == []


def test_document_library_update_maps_currentness_conflict_without_dispatch() -> None:
    application, intake, _processing, dispatches = _application(
        _item(_document("doc-1", "Document"))
    )
    intake.facade.conflict = True

    outcome = application.update(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        payload=DocumentLibraryUpdateRequest(
            description="Stale",
            idempotency_key="update-stale",
        ),
    )

    assert outcome.failure is not None
    assert outcome.failure.status_code == 409
    assert outcome.failure.message_code == "document.was_not_found"
    assert dispatches == []


def test_document_library_upload_replay_conflict_never_dispatches() -> None:
    application, _intake, _processing, dispatches = _application(
        upload_replay_conflict=True
    )
    response = _http_client(application).post(
        "/api/v1/admin/document-library",
        data={
            "scope_type": "project",
            "scope_id": "project-1",
            "idempotency_key": "upload-replay",
        },
        files={"file": ("manual.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "artifact_upload_conflict"
    assert dispatches == []


def test_document_library_refresh_resumes_active_job_idempotently() -> None:
    processing = _Processing(
        current_job=SimpleNamespace(job_id="job-active", status="running")
    )
    application, intake, _processing, dispatches = _application(
        _item(
            _document(
                "doc-1",
                "Document",
                processing_job_id="job-active",
            )
        ),
        processing=processing,
    )

    outcome = application.refresh(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key=None,
    )

    assert outcome.failure is None
    assert outcome.value.request_id == "resume-job-active"
    assert outcome.value.job_id == "job-active"
    assert intake.version_calls == []
    assert len(intake.facade.patch_requests) == 1
    assert len(intake.facade.refresh_requests) == 0
    assert dispatches == [True]


@pytest.mark.parametrize(
    ("proofs", "status_code", "dispatch_count"),
    [
        (_RestoreProofs(fail=True), 409, 0),
        (_RestoreProofs(reusable=False), 202, 1),
    ],
)
def test_document_library_restore_failure_and_rebuild_branches(
    proofs: _RestoreProofs, status_code: int, dispatch_count: int
) -> None:
    application, intake, _processing, dispatches = _application(
        _item(_document("doc-1", "Document", lifecycle_status="disabled")),
        restore_proofs=proofs,
    )

    outcome = application.restore(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
    )

    assert (
        outcome.failure.status_code if outcome.failure is not None else outcome.status_code
    ) == status_code
    assert len(dispatches) == dispatch_count
    if proofs.fail:
        assert outcome.failure is not None
        assert outcome.failure.message_code == "document.restore_verification_failed"
    else:
        assert outcome.failure is None
        assert outcome.value.job_id == "job-restore"
        assert (
            intake.facade.finish_restore_requests[0].processing_acceptance
            is not None
        )


def test_document_library_dispatch_occurs_only_after_durable_acceptance() -> None:
    refresh_order: list[str] = []
    refresh, _intake, _processing, _dispatches = _application(
        _item(_document("doc-1", "Document")),
        order=refresh_order,
    )
    outcome = refresh.refresh(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key="ordered-refresh",
    )
    assert outcome.failure is None
    assert refresh_order == ["accepted", "dispatch"]

    restore_order: list[str] = []
    restore, _intake, _processing, _dispatches = _application(
        _item(_document("doc-1", "Document", lifecycle_status="disabled")),
        restore_proofs=_RestoreProofs(reusable=False),
        order=restore_order,
    )
    outcome = restore.restore(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
    )
    assert outcome.failure is None
    assert restore_order == ["accepted", "accepted", "dispatch"]

    upload_order: list[str] = []
    upload, _intake, _processing, _dispatches = _application(order=upload_order)
    response = _http_client(upload).post(
        "/api/v1/admin/document-library",
        data={
            "scope_type": "project",
            "scope_id": "project-1",
            "idempotency_key": "ordered-upload",
        },
        files={"file": ("manual.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 202
    assert upload_order == ["accepted", "dispatch"]