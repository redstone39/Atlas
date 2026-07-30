from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import re
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_document_artifact_provider import (
    PostgresDocumentUploadJourneyProvider,
    _RestoreItem,
    _verify_restore_items,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    DocumentLifecycleMutationCommand,
)
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.artifact_storage.records import StorageFence
from atlas_production.modules.document_intake.api_models import DocumentTagRef
from atlas_production.modules.document_intake.records import DocumentRecord


NOW = "2026-07-18T04:00:00+00:00"


class _FenceSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _model, identity):
        if identity == "global":
            return SimpleNamespace(
                mode="active",
                active_target_id="target-1",
                active_target_revision=1,
                root_identity_digest="f" * 64,
                storage_epoch=3,
            )
        if _model.__name__ == "AtlasDocumentVersionRow":
            return SimpleNamespace(
                payload={
                    "document_version_id": identity,
                    "document_id": "document-1",
                    "title": "Manual",
                    "source_kind": "file_upload",
                    "document_format": "pdf",
                    "source_digest": hashlib.sha256(b"abc").hexdigest(),
                    "content_digest": hashlib.sha256(b"").hexdigest(),
                    "created_at": NOW,
                    "status": "active",
                    "supersedes_version_id": None,
                    "original_artifact_id": "artifact-canonical",
                    "content_type": "application/pdf",
                }
            )
        if _model.__name__ == "AtlasAuditEventRow":
            return SimpleNamespace(
                event_id=identity,
                event_type="document_library_uploaded",
                actor_id="user-1",
                target_ref="document:document-1",
                project_id="project-1",
                scope_type="project",
                scope_id="project-1",
                document_id="document-1",
                message_code="document.upload_is_accepted_for_asynchronous_processing",
                message_params={},
                event_metadata={"document_id": "document-1"},
                created_at=NOW,
            )
        if _model.__name__ == "AtlasArtifactRow":
            return SimpleNamespace(
                artifact_id=identity,
                artifact_class="original_document",
                blob_id="blob-canonical",
                checksum_algorithm="sha256",
                checksum_value=hashlib.sha256(b"abc").hexdigest(),
                byte_size=3,
                content_type="application/pdf",
                owner_scope_type="project",
                owner_scope_id="project-1",
                lifecycle_status="active",
                created_at=NOW,
                updated_at=NOW,
                logical_identity="document:document-1:original:key-1",
                source_artifact_id=None,
                document_version_id="dver-document-1-0001",
                parent_resource_id="document-1",
                parent_lifecycle_epoch=0,
                processing_generation=None,
                pipeline_id="document-intake",
                pipeline_version="celery-v1",
                generation=None,
                page_number=None,
                block_id=None,
                acl_policy_version="current",
                acl_action="document_upload",
                metadata_json={},
            )
        assert _model.__name__ == "AtlasStorageBlobRow"
        return SimpleNamespace(
            blob_id=identity,
            status="committed",
            checksum_value=hashlib.sha256(b"abc").hexdigest(),
            byte_size=3,
            content_type="application/pdf",
        )


class _JourneyCommand:
    def __init__(self, events: list[str], *, replay: bool = False):
        self.events = events
        self.replay = replay
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        self.events.append("boundary")
        assert request.request.presented_browser_session_token == "session-1"
        assert request.request.document.raw_sha256 is None
        if not self.replay:
            payload = b"".join(request.artifact.plan.chunks)
            size = len(payload)
            digest = hashlib.sha256(payload).hexdigest()
            assert request.request.document.raw_sha256 == digest
            assert request.request.document.source_byte_size == size
        self.events.append("terminal")
        return SimpleNamespace(
            artifact_id=request.request.document.original_artifact_id,
            document_version_id=request.request.version.document_version_id,
            job=SimpleNamespace(job_id="job-1"),
            audit_event_id="audit-canonical",
        )


def _document() -> DocumentRecord:
    return DocumentRecord(
        document_id="document-1",
        title="Manual",
        source_digest="sha256:pending",
        intake_status="queued",
        source_kind="file_upload",
        document_format="pdf",
        content_type="application/pdf",
        scope_type="project",
        scope_id="project-1",
        lifecycle_status="active",
        uploaded_at=NOW,
    )


def _upload(provider, chunks):
    return provider.upload(
        chunks=chunks,
        request_fingerprint="a" * 64,
        artifact_class="original_document",
        logical_identity="document:document-1:original:key-1",
        content_type="application/pdf",
        document=_document(),
        tag_refs=[DocumentTagRef(tag_type="project", tag_id="project-1")],
        authorization_bindings=(),
        job_kind="ingest",
        idempotency_scope="document_library_upload",
        idempotency_key="key-1",
        created_by="user-1",
        audit_event_type="document_library_uploaded",
        audit_message_code="document.upload_is_accepted_for_asynchronous_processing",
        audit_metadata={"document_format": "pdf"},
        presented_browser_session_token="session-1",
    )


def test_upload_provider_builds_boundary_facts_before_consuming_bytes() -> None:
    events: list[str] = []

    def chunks():
        events.append("bytes")
        yield b"abc"

    command = _JourneyCommand(events)
    provider = PostgresDocumentUploadJourneyProvider(
        lambda: _FenceSession(), command  # type: ignore[arg-type]
    )
    result = _upload(provider, chunks())

    assert events == ["boundary", "bytes", "terminal"]
    request = command.requests[0]
    assert request.request.actor_type == "user"
    assert request.artifact.attempt.fence.storage_epoch == 3
    assert request.artifact.plan.begin.audit_events[0].metadata[
        "request_fingerprint"
    ] == "a" * 64
    assert request.artifact.attempt.intent["authorization_bindings"] == [
        ["project", "project-1"]
    ]
    finalized = request.artifact.plan.finalize(
        3,
        hashlib.sha256(b"abc").hexdigest(),
        request.artifact.attempt,
        request.artifact.lease,
        NOW,
    )
    assert finalized.audit_events[0].metadata["request_fingerprint"] == "a" * 64
    assert result.artifact.checksum_value == hashlib.sha256(b"abc").hexdigest()
    assert re.fullmatch(
        r"blobs/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}\.blob",
        result.artifact.blob_id and finalized.blob.opaque_ref,
    )
    assert result.publication.job.job_id == "job-1"


def test_upload_provider_canonical_replay_does_not_consume_bytes_and_keeps_identity() -> None:
    first_command = _JourneyCommand([])
    first = PostgresDocumentUploadJourneyProvider(
        lambda: _FenceSession(), first_command  # type: ignore[arg-type]
    )
    first_result = _upload(first, (b"abc",))
    events: list[str] = []

    def forbidden_chunks():
        events.append("bytes")
        yield b"abc"

    replay_command = _JourneyCommand(events, replay=True)
    replay = PostgresDocumentUploadJourneyProvider(
        lambda: _FenceSession(), replay_command  # type: ignore[arg-type]
    )
    replay_result = _upload(replay, forbidden_chunks())

    assert events == ["boundary", "terminal"]
    assert replay_result.artifact.artifact_id == first_result.artifact.artifact_id
    assert replay_result.artifact.checksum_value == hashlib.sha256(b"abc").hexdigest()
    assert replay_result.artifact.byte_size == 3
    assert replay_result.artifact.content_type == "application/pdf"
    assert replay_result.publication.version.source_digest == hashlib.sha256(
        b"abc"
    ).hexdigest()
    assert replay_result.publication.audit.event_id == "audit-canonical"


def test_upload_provider_rejects_scope_currentness_before_bytes_or_sql() -> None:
    events: list[str] = []

    def chunks():
        events.append("bytes")
        yield b"abc"

    provider = PostgresDocumentUploadJourneyProvider(
        lambda: pytest.fail("opened SQL"), _JourneyCommand(events)  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="scope graph"):
        provider.upload(
            chunks=chunks(),
            request_fingerprint="a" * 64,
            artifact_class="original_document",
            logical_identity="logical",
            content_type="application/pdf",
            document=_document(),
            tag_refs=[DocumentTagRef(tag_type="team", tag_id="team-2")],
            authorization_bindings=(),
            job_kind="ingest",
            idempotency_scope="document_library_upload",
            idempotency_key="key-1",
            created_by="user-1",
            audit_event_type="uploaded",
            audit_message_code="uploaded",
            audit_metadata={},
            presented_browser_session_token="session-1",
        )
    assert events == []


class _Filesystem:
    def __init__(self, failing: set[str] = set()):
        self.failing = failing
        self.verified: list[str] = []

    def verify_full(self, opaque_ref, *, expected_size, expected_sha256):
        self.verified.append(opaque_ref)
        if opaque_ref in self.failing:
            raise OSError("corrupt")


def _restore_document() -> DocumentRecord:
    return replace(
        _document(),
        original_artifact_id="artifact-original",
        lifecycle_status="restoring",
        resource_lifecycle_epoch=1,
        active_processing_generation=2,
    )


def _restore_items() -> tuple[_RestoreItem, ...]:
    return (
        _RestoreItem(
            "artifact-original", "blob-original", "objects/original", "a" * 64,
            3, "original_document", None, "version-1", None, True,
        ),
        _RestoreItem(
            "artifact-page", "blob-page", "objects/page", "b" * 64,
            5, "document_page_pdf", "artifact-original", "version-1", 2, True,
        ),
    )


def test_restore_proof_full_hashes_outside_loader_and_reuses_only_complete_generation() -> None:
    filesystem = _Filesystem()
    proof = _verify_restore_items(
        expected_document=_restore_document(),
        items=_restore_items(),
        ready_evidence=True,
        active_fence=StorageFence("target-1", 1, "f" * 64, 3),
        filesystem=filesystem,  # type: ignore[arg-type]
    )
    assert filesystem.verified == ["objects/original", "objects/page"]
    assert proof.reusable_processing_generation is True
    assert {item[0] for item in proof.artifacts} == {
        "artifact-original", "artifact-page"
    }
    assert proof.active_fence == StorageFence("target-1", 1, "f" * 64, 3)


def test_restore_terminal_rechecks_control_and_each_blob_fence() -> None:
    source = inspect.getsource(DocumentLifecycleMutationCommand.execute)
    control_at = source.index("AtlasArtifactStorageControlRow")
    artifact_at = source.index("artifact_rows_by_id")
    assert control_at < artifact_at
    for field in (
        "proof_fence.target_id",
        "proof_fence.target_revision",
        "proof_fence.root_identity_digest",
        "proof_fence.storage_epoch",
        "restore storage fence changed",
    ):
        assert field in source


def test_restore_proof_corrupt_derived_forces_rebuild_and_cannot_reuse_it() -> None:
    proof = _verify_restore_items(
        expected_document=_restore_document(),
        items=_restore_items(),
        ready_evidence=True,
        active_fence=StorageFence("target-1", 1, "f" * 64, 3),
        filesystem=_Filesystem({"objects/page"}),  # type: ignore[arg-type]
    )
    assert proof.reusable_processing_generation is False
    assert proof.artifacts == (("artifact-original", "blob-original", "a" * 64, 3),)


def test_restore_proof_corrupt_original_never_authorizes_restore() -> None:
    with pytest.raises(ArtifactStorageError) as raised:
        _verify_restore_items(
            expected_document=_restore_document(),
            items=_restore_items(),
            ready_evidence=True,
            active_fence=StorageFence("target-1", 1, "f" * 64, 3),
            filesystem=_Filesystem({"objects/original"}),  # type: ignore[arg-type]
        )
    assert raised.value.error_code == "document_restore_original_integrity_failed"
