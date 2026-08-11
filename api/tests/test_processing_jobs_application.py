from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.modules.identity_access.records import UserRecord
import pytest


from atlas_production.modules.processing_pipeline.public import (
    ProcessingControlDenied,
    ProcessingJobsApplication,
)


NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)
ACTOR = UserRecord("admin-1", "Admin", None, "admin", None)
MEMBER = UserRecord("member-1", "Member", None, "member", None)


class _Principal:
    def __init__(self, actor: UserRecord = ACTOR):
        self.actor = actor

    def current_user(self, _token):
        return self.actor


def _http_client(
    application: ProcessingJobsApplication, actor: UserRecord = ACTOR
) -> TestClient:
    values = {name: object() for name in ApiComposition.__dataclass_fields__}
    values.update(current_principal=_Principal(actor), processing_jobs=application)
    return TestClient(create_app(ApiComposition(**values)))




def _document(**changes):
    values = {
        "document_id": "doc-1",
        "document_format": "pdf",
        "processing_job_id": "job-1",
        "warning_codes": [],
        "lifecycle_status": "active",
        "active_processing_generation": 1,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _job(**changes):
    values = {
        "job_id": "job-1",
        "document_id": "doc-1",
        "status": "queued",
        "stage": "queued",
        "failure_code": None,
        "progress_current": 0,
        "progress_total": 4,
        "progress_unit": "pages",
        "attempt_started_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@dataclass
class _Backend:
    projection: object
    created: list[dict[str, object]] = field(default_factory=list)
    list_calls: list[dict[str, object]] = field(default_factory=list)
    order: list[str] | None = None

    def get_document_job_request_projection(self, **_kwargs):
        if self.order is not None:
            self.order.append("reload")
        return self.projection

    def list_document_job_request_projections(self, **kwargs):
        self.list_calls.append(kwargs)
        return (self.projection,) if self.projection is not None else ()

    def create_processing_job(self, **kwargs):
        self.created.append(kwargs)
        if self.order is not None:
            self.order.append("accepted")
        return _job()

    def retry_processing_job_request(self, **_kwargs):
        if self.order is not None:
            self.order.append("accepted")
        return SimpleNamespace(
            job=_job(status="queued"),
            audit_event=SimpleNamespace(event_id="audit-retry"),
        )

    def stop_processing_job_request(self, **_kwargs):
        if self.order is not None:
            self.order.append("accepted")
        return SimpleNamespace(
            job=_job(status="cancelled"),
            audit_event=SimpleNamespace(event_id="audit-cancel"),
        )


@dataclass
class _DocumentLibrary:
    projection: object
    version_calls: list[object] = field(default_factory=list)
    version_id: str | None = "version-1"

    def document_library_projection(self, **_kwargs):
        return self.projection

    def processing_document_version_id(self, document_id: str):
        self.version_calls.append(document_id)
        return self.version_id


@dataclass
class _Authorization:
    readable: bool = True
    controllable: bool = True

    def can_read(self, _projection, _actor):
        return self.readable

    def can_control(self, _projection, _actor):
        return self.controllable


def _application(
    *,
    document=None,
    job=None,
    authorization=None,
    version_id="version-1",
    new_id=None,
    order=None,
):
    document = document or _document()
    item = SimpleNamespace(
        document=document,
        job=job or _job(),
        profile_pin=SimpleNamespace(profile_id="profile-1", profile_revision=2),
    )
    backend = _Backend(item, order=order)
    library = _DocumentLibrary(
        SimpleNamespace(items=(SimpleNamespace(document=document),)),
        version_id=version_id,
    )
    dispatches: list[bool] = []

    def dispatch():
        dispatches.append(True)
        if order is not None:
            order.append("dispatch")

    application = ProcessingJobsApplication(
        backend,
        library,
        authorization or _Authorization(),
        dispatch,
        now=lambda: NOW,
        new_id=new_id or (lambda: "generated"),
    )
    return application, backend, library, dispatches


def test_reindex_passes_document_identifier_to_version_lookup() -> None:
    application, backend, library, dispatches = _application()
    actor = ACTOR

    outcome = application.reindex(
        actor=actor,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key=None,
    )

    assert outcome.failure is None
    assert outcome.status_code == 202
    assert library.version_calls == ["doc-1"]
    assert backend.created == [
        {
            "document_id": "doc-1",
            "document_version_id": "version-1",
            "job_kind": "reindex",
            "idempotency_scope": "document_reindex",
            "idempotency_key": "reindex-doc-1-generated",
            "created_by": "admin-1",
        }
    ]
    assert dispatches == [True]


def test_reindex_denies_non_admin_before_lookup_creation_or_dispatch() -> None:
    application, backend, library, dispatches = _application()

    outcome = application.reindex(
        actor=MEMBER,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key="reindex-denied",
    )

    assert outcome.failure is not None
    assert (
        outcome.failure.error_code,
        outcome.failure.message_code,
        outcome.failure.status_code,
    ) == (
        "access_denied",
        "permission.admin_permission_is_required",
        403,
    )
    assert library.version_calls == []
    assert backend.created == []
    assert dispatches == []

    denied = _http_client(application, MEMBER).post(
        "/api/v1/admin/document-library/doc-1/reindex",
        headers={"Idempotency-Key": "reindex-denied"},
    )
    assert denied.status_code == 403
    assert denied.json()["message_code"] == "permission.admin_permission_is_required"
    assert library.version_calls == []
    assert backend.created == []
    assert dispatches == []


def test_processing_jobs_http_routes_preserve_owner_status_and_failure_mapping() -> None:
    application, backend, _library, dispatches = _application()
    client = _http_client(application)

    status = client.get("/api/v1/processing/jobs/job-1")
    listed = client.get(
        "/api/v1/processing/jobs",
        params={"profile_id": "profile-1", "profile_revision": 2, "status": "queued"},
    )
    cancelled = client.post("/api/v1/processing/jobs/job-1/cancel")
    retried = client.post("/api/v1/processing/jobs/job-1/retry")
    reindexed = client.post(
        "/api/v1/admin/document-library/doc-1/reindex",
        headers={"Idempotency-Key": "reindex-http"},
    )
    backend.projection = None
    missing = client.get("/api/v1/processing/jobs/missing")

    assert status.status_code == 200
    assert set(status.json()) == {
        "document_id",
        "document_format",
        "profile_id",
        "profile_revision",
        "current_stage",
        "warning_codes",
        "failure_code",
        "job_id",
        "status",
        "status_url",
        "retry_available",
        "cancel_available",
        "review_available",
        "progress_current",
        "progress_total",
        "progress_unit",
        "elapsed_seconds",
        "attempt_started_at",
        "is_current",
        "created_at",
        "updated_at",
    }
    assert status.json()["status"] == "queued"
    assert len(listed.json()["jobs"]) == 1
    assert cancelled.status_code == 200
    assert cancelled.json()["audit_event_ref"] == "audit-cancel"
    assert retried.status_code == 202
    assert retried.json()["audit_event_ref"] == "audit-retry"
    assert reindexed.status_code == 202
    assert reindexed.json()["document_id"] == "doc-1"
    assert dispatches == [True, True]
    assert backend.created[-1]["idempotency_key"] == "reindex-http"
    assert missing.status_code == 404
    assert missing.json()["message_code"] == "processing.job_was_not_found"


def test_processing_status_projection_preserves_ready_with_warnings() -> None:
    application, _backend, _library, _dispatches = _application(
        document=_document(warning_codes=["quality_warning"]),
        job=_job(status="succeeded", stage="complete"),
    )
    actor = SimpleNamespace(actor_type="user", actor_id="member-1")

    outcome = application.get(
        actor=actor,
        session_token="session-1",
        job_id="job-1",
    )

    assert outcome.failure is None
    assert outcome.value["status"] == "ready_with_warnings"
    assert outcome.value["warning_codes"] == ["quality_warning"]
    assert outcome.value["retry_available"] is False


def test_retry_preserves_accepted_status_audit_and_dispatch() -> None:
    application, _backend, _library, dispatches = _application()
    actor = SimpleNamespace(actor_type="user", actor_id="member-1")

    outcome = application.control(
        actor=actor,
        session_token="session-1",
        job_id="job-1",
        retry=True,
    )

    assert outcome.failure is None
    assert outcome.status_code == 202
    assert outcome.value["audit_event_ref"] == "audit-retry"
    assert dispatches == [True]



@pytest.mark.parametrize(
    ("job_changes", "document_changes", "expected_status"),
    [
        ({"status": "queued"}, {}, "queued"),
        ({"status": "retry_wait"}, {}, "waiting_retry"),
        ({"status": "running", "stage": "extracting"}, {}, "processing"),
        ({"status": "running", "stage": "publishing"}, {}, "publishing"),
        ({"status": "succeeded"}, {}, "ready"),
        ({"status": "cancelled"}, {}, "cancelled"),
        ({"status": "failed", "failure_code": "extract_failed"}, {}, "failed"),
        (
            {"status": "succeeded"},
            {"warning_codes": ["quality_warning"]},
            "ready_with_warnings",
        ),
    ],
)
def test_processing_status_matrix_is_exact(
    job_changes, document_changes, expected_status
) -> None:
    application, _backend, _library, _dispatches = _application(
        document=_document(**document_changes),
        job=_job(**job_changes),
    )
    outcome = application.get(
        actor=ACTOR,
        session_token="session-1",
        job_id="job-1",
    )
    assert outcome.value["status"] == expected_status


def test_processing_historical_and_acl_projections_fail_closed() -> None:
    historical, _backend, _library, _dispatches = _application(
        document=_document(
            processing_job_id="new-job",
            warning_codes=["current-generation-warning"],
        ),
        job=_job(status="failed", failure_code="old-failure"),
    )
    projected = historical.get(
        actor=ACTOR,
        session_token="session-1",
        job_id="job-1",
    ).value
    assert projected["warning_codes"] == []
    assert projected["retry_available"] is False
    assert projected["cancel_available"] is False

    hidden, _backend, _library, _dispatches = _application(
        authorization=_Authorization(readable=False)
    )
    failure = hidden.get(
        actor=ACTOR,
        session_token="session-1",
        job_id="job-1",
    ).failure
    assert (failure.error_code, failure.status_code) == ("not_found", 404)


@pytest.mark.parametrize(
    ("retry", "raised", "status_code", "message_code"),
    [
        (
            True,
            ProcessingControlDenied(
                "denied", SimpleNamespace(event_id="audit-denied")
            ),
            403,
            "processing.only_the_uploader_or_scope_admin_can_control_this_job",
        ),
        (
            True,
            ValueError("processing_job_not_retryable"),
            409,
            "processing.only_a_failed_or_stopped_job_can_start_a_new_attempt",
        ),
        (
            False,
            ValueError("processing_job_not_active"),
            409,
            "processing.only_an_active_processing_job_can_be_stopped",
        ),
    ],
)
def test_processing_control_failure_matrix(
    retry, raised, status_code, message_code
) -> None:
    application, backend, _library, dispatches = _application()

    def fail(**_kwargs):
        raise raised

    if retry:
        backend.retry_processing_job_request = fail
    else:
        backend.stop_processing_job_request = fail
    failure = application.control(
        actor=ACTOR,
        session_token="session-1",
        job_id="job-1",
        retry=retry,
    ).failure
    assert (failure.status_code, failure.message_code) == (status_code, message_code)
    assert dispatches == []


def test_processing_list_filters_exclude_nonmatching_and_hidden_jobs() -> None:
    application, backend, _library, _dispatches = _application()
    excluded = application.list(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        profile_id="other-profile",
        profile_revision=None,
        status="failed",
    )
    assert excluded.value == {"jobs": []}
    assert backend.list_calls[-1]["document_id"] == "doc-1"

    hidden, hidden_backend, _library, _dispatches = _application(
        authorization=_Authorization(readable=False)
    )
    hidden_result = hidden.list(
        actor=ACTOR,
        session_token="session-1",
        document_id=None,
        profile_id=None,
        profile_revision=None,
        status=None,
    )
    assert hidden_result.value == {"jobs": []}
    assert len(hidden_backend.list_calls) == 1


@pytest.mark.parametrize(
    ("document", "version_id", "status_code", "message_code"),
    [
        (
            _document(lifecycle_status="disabled"),
            "version-1",
            404,
            "document.was_not_found",
        ),
        (
            _document(active_processing_generation=0),
            "version-1",
            409,
            "document.has_no_published_processing_generation",
        ),
        (
            _document(),
            None,
            422,
            "artifact.source_document_is_unavailable_this_file_cannot_be_processed",
        ),
    ],
)
def test_processing_reindex_supported_failure_matrix(
    document, version_id, status_code, message_code
) -> None:
    application, backend, _library, dispatches = _application(
        document=document,
        version_id=version_id,
    )
    failure = application.reindex(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key="reindex-failure",
    ).failure
    assert (failure.status_code, failure.message_code) == (status_code, message_code)
    assert backend.created == []
    assert dispatches == []


def test_processing_reindex_fresh_keys_are_unique_and_supplied_key_is_preserved() -> None:
    generated = iter(("first", "second"))
    application, backend, _library, dispatches = _application(
        new_id=lambda: next(generated)
    )
    for key in (None, None, "caller-key"):
        outcome = application.reindex(
            actor=ACTOR,
            session_token="session-1",
            document_id="doc-1",
            idempotency_key=key,
        )
        assert outcome.failure is None
    assert [call["idempotency_key"] for call in backend.created] == [
        "reindex-doc-1-first",
        "reindex-doc-1-second",
        "caller-key",
    ]
    assert dispatches == [True, True, True]


def test_processing_retry_and_reindex_dispatch_only_after_acceptance_reload() -> None:
    retry_order: list[str] = []
    retry, _backend, _library, _dispatches = _application(order=retry_order)
    outcome = retry.control(
        actor=ACTOR,
        session_token="session-1",
        job_id="job-1",
        retry=True,
    )
    assert outcome.failure is None
    assert retry_order == ["accepted", "reload", "dispatch"]

    reindex_order: list[str] = []
    reindex, _backend, _library, _dispatches = _application(order=reindex_order)
    outcome = reindex.reindex(
        actor=ACTOR,
        session_token="session-1",
        document_id="doc-1",
        idempotency_key="ordered-reindex",
    )
    assert outcome.failure is None
    assert reindex_order == ["accepted", "dispatch"]


def test_processing_control_http_denial_preserves_failure_mapping_and_no_dispatch() -> None:
    application, backend, _library, dispatches = _application()

    def deny(**_kwargs):
        raise ProcessingControlDenied(
            "denied",
            SimpleNamespace(event_id="audit-control-denied"),
        )

    backend.retry_processing_job_request = deny
    response = _http_client(application).post(
        "/api/v1/processing/jobs/job-1/retry"
    )
    assert response.status_code == 403
    assert response.json()["audit_event_ref"] == "audit-control-denied"
    assert dispatches == []