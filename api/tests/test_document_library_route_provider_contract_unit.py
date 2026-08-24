from __future__ import annotations

from dataclasses import fields
import inspect
from typing import get_type_hints, NotRequired
from pathlib import Path

from atlas_production.modules.document_intake.public import (
    DocumentLibraryApplication,
    DocumentLibraryIntakeBackend,
    DocumentLibraryOutcomeV1,
    DocumentLibraryRequestProjection,
    DocumentLibraryUploadBackend,
    DocumentLibraryUploadCommand,
    DocumentLifecycleRequestInput,
)
from atlas_production.modules.answer_behavior.public import AnswerBehaviorAdmin
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.processing_pipeline.public import (
    ProcessingJobsBackend,
    ProcessingJobPayloadV1,
    ProcessingJobsOutcomeV1,
)
from atlas_production.routes.document_library import router


ROUTE_FILE = Path(__file__).parents[1] / "src/atlas_production/routes/document_library.py"
OWNER_FILE = (
    Path(__file__).parents[1]
    / "src/atlas_production/modules/document_intake/library_application.py"
)


def test_document_library_route_is_transport_only_and_owner_uses_typed_ports() -> None:
    route_source = ROUTE_FILE.read_text()
    owner_source = OWNER_FILE.read_text()

    assert len(router.routes) == 7
    assert "api_composition(request).document_library." in route_source
    for provider_call in (
        ".document_library_projection(",
        ".requested_scope_projection(",
        ".journey_facade(",
        ".uploads.upload(",
        ".restore_proofs.verify(",
        ".capture_processing_execution(",
        ".patch_document(",
        ".disable_document(",
        ".begin_restore(",
        ".finish_restore(",
        ".refresh_or_reindex(",
    ):
        assert provider_call not in route_source
        assert provider_call in owner_source


def test_public_owner_ports_and_dtos_have_explicit_contract_shapes() -> None:
    owner_source = OWNER_FILE.read_text()
    signatures = (
        inspect.signature(DocumentLibraryIntakeBackend.document_library_projection),
        inspect.signature(DocumentLibraryIntakeBackend.requested_scope_projection),
        inspect.signature(DocumentLibraryUploadBackend.upload),
        inspect.signature(ProcessingJobsBackend.get_document_job_request_projection),
        inspect.signature(ProcessingJobsBackend.create_processing_job),
    )

    assert all("Any" not in str(signature) for signature in signatures)
    assert "DocumentLibraryRequestProjection" in str(signatures[0].return_annotation)
    assert "DocumentUploadResult" in str(signatures[2].return_annotation)
    assert "DocumentJobRequestAuthorityProjection" in str(signatures[3].return_annotation)
    assert [field.name for field in fields(DocumentLibraryRequestProjection)] == [
        "authenticated_actor",
        "items",
        "authorization_state",
    ]
    assert [field.name for field in fields(DocumentLifecycleRequestInput)] == [
        "presented_browser_session_token",
        "actor_type",
        "actor_id",
        "expected_document",
        "document",
        "tags",
        "audit_events",
        "denial_audit_event",
        "versions",
        "processing_acceptance",
        "restore_verification",
    ]
    upload_signature = inspect.signature(DocumentLibraryApplication.upload)
    assert "form" not in upload_signature.parameters
    assert (
        upload_signature.parameters["command"].annotation
        == "DocumentLibraryUploadCommand | None"
    )
    assert [
        field.name for field in fields(DocumentLibraryUploadCommand)
    ] == [
        "idempotency_key",
        "scope_type",
        "scope_id",
        "tag_refs",
        "allow_member_download",
        "description",
        "filename",
        "content_type",
        "file",
    ]
    assert "object" not in str(get_type_hints(DocumentLibraryOutcomeV1)["value"])
    assert "object" not in str(get_type_hints(ProcessingJobsOutcomeV1)["value"])
    assert get_type_hints(AnswerBehaviorAdmin.get)["actor"] == UserRecord | None
    processing_payload_hints = get_type_hints(
        ProcessingJobPayloadV1, include_extras=True
    )
    assert "job_id" in processing_payload_hints
    assert processing_payload_hints["audit_event_ref"] == NotRequired[str]

    for forbidden in (
        "get_" + "store",
        "bounded_document_uploads",
        "append_document_audit",
        "update_document(",
        "register_document(",
        "put_document(",
        "effective_document_scope",
        "direct_team_role",
        "resolve_access",
        ".transaction()",
        ".create_processing_job(",
        "artifact_storage.write_artifact",
        "verify_document_restore_set",
    ):
        assert forbidden not in owner_source


def test_restore_owner_orders_durable_begin_proof_and_atomic_finish() -> None:
    source = OWNER_FILE.read_text()

    begin = source.index(".begin_restore(")
    proof = source.index(".restore_proofs.verify(")
    finish = source.index(".finish_restore(")
    assert begin < proof < finish
    assert '"document_restore_failed"' in source
    assert '"document.restore_verification_failed"' in source
    assert "audit_event_ref=failure.event_id" in source
