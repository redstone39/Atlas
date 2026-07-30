from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_document_intake_adapter import (
    DocumentIntakeJourneyFacade,
    DocumentLibraryItemProjection,
    DocumentLibraryRequestProjection,
    DocumentLifecycleRequestInput,
    PostgresDocumentIntakeAdapter,
    RequestedDocumentScopeProjection,
)
from atlas_production.infrastructure.postgres_document_processing_adapter import (
    PostgresDocumentProcessingAdapter,
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
    ProcessingJobAuthorizationState,
    ProcessingJobListBatch,
    ProcessingJobView,
    ProcessingProfilePin,
    ProcessingControlResult,
    VerifiedDocumentRestoreSet,
    attach_document_job_request_projections,
    document_processing_acceptance_identity,
    document_processing_acceptance_lock_identities,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.identity_access.records import UserRecord
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
            SimpleNamespace(  # type: ignore[arg-type]
                decision_id="decision-1",
                allowed=True,
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
