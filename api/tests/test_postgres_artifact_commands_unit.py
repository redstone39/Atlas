from __future__ import annotations

from dataclasses import MISSING, asdict, fields, replace
from io import BytesIO
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atlas_production.infrastructure import postgres_artifact_storage_adapter as adapter
from atlas_production.infrastructure.persistence import artifact_storage as rows
from atlas_production.infrastructure.postgres_owner import artifact as owner
from atlas_production.modules.artifact_storage.records import (
    ArtifactOperationRecord,
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageControlRecord,
    StorageFence,
    StorageReconciliationFindingRecord,
    StorageRequestLeaseRecord,
    StorageTargetRecord,
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)
from atlas_production.modules.identity_access.records import AccessDecisionRecord
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentVersionRecord,
)
from atlas_production.shared.public import AuditEventRecord


NOW = "2026-07-18T00:00:00+00:00"
DIGEST = "a" * 64
CONTENT = "b" * 64
REQUEST = "c" * 64
FENCE = StorageFence("target-1", 1, DIGEST, 2)
OLD_FENCE = StorageFence("target-old", 1, "d" * 64, 1)
BROWSER_SESSION_TOKEN = "browser-session-secret-1"


class ScalarRows:
    def __init__(self, values: tuple[Any, ...]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return list(self.values)

    def __iter__(self):
        return iter(self.values)


class Result:
    def __init__(self, scalar: Any = None, scalars: tuple[Any, ...] = ()) -> None:
        self.scalar = scalar
        self.scalar_values = scalars

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def scalars(self) -> ScalarRows:
        return ScalarRows(self.scalar_values)


class RecordingSession:
    def __init__(self) -> None:
        self.rows: dict[tuple[type[Any], Any], Any] = {}
        self.results: list[Result] = []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.executed: list[str] = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.fail_audit = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, row_type, identity, **_kwargs):
        return self.rows.get((row_type, identity))

    def execute(self, statement, _parameters=None) -> Result:
        rendered = str(statement)
        self.executed.append(rendered)
        if self.results and "pg_advisory" not in rendered:
            return self.results.pop(0)
        return Result()

    def scalar(self, statement):
        rendered = str(statement)
        self.executed.append(rendered)
        if "atlas_sessions" in rendered:
            return self.rows.get(
                (owner.AtlasSessionRow, BROWSER_SESSION_TOKEN)
            )
        return self.execute(statement).scalar_one_or_none()

    def add(self, row) -> None:
        if self.fail_audit and type(row).__name__ == "AtlasAuditEventRow":
            raise RuntimeError("audit unavailable")
        self.added.append(row)

    def delete(self, row) -> None:
        self.deleted.append(row)

    def commit(self) -> None:
        self.commits += 1

    def flush(self) -> None:
        self.flushes += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Factory:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session

    def __call__(self) -> RecordingSession:
        return self.session


def _event(event_id: str = "audit-1", *, decision_id: str | None = None):
    metadata = {} if decision_id is None else {"access_decision_id": decision_id}
    return AuditEventRecord(
        event_id=event_id,
        event_type="artifact_test",
        actor_id="user-1",
        target_ref="artifact:artifact-1",
        project_id="project-1",
        message_code="project.is_ready_for_membership_setup",
        metadata=metadata,
        created_at=NOW,
    )


def _decision(
    *, allowed: bool = True, reason: str | None = None
) -> AccessDecisionRecord:
    return AccessDecisionRecord(
        decision_id="decision-1",
        actor_type="user",
        actor_id="user-1",
        project_id="project-1",
        action="read_original",
        required_role="viewer",
        allowed=allowed,
        reason=reason or ("project_grant" if allowed else "scope_denied"),
        effective_role="viewer" if allowed else None,
        source_type="user",
        source_id="user-1",
        explanation="Current PostgreSQL policy result.",
        created_at=NOW,
        scope_type="project",
        scope_id="project-1",
    )


def _document() -> DocumentRecord:
    return DocumentRecord(
        document_id="document-1",
        title="Manual",
        source_digest=CONTENT,
        source_kind="file_upload",
        document_format="pdf",
        content_type="application/pdf",
        source_filename="manual.pdf",
        source_byte_size=3,
        scope_type="project",
        scope_id="project-1",
        allow_member_download=True,
        original_artifact_id="artifact-1",
        raw_sha256=CONTENT,
        resource_lifecycle_epoch=1,
    )


def _version() -> DocumentVersionRecord:
    return DocumentVersionRecord(
        document_version_id="version-1",
        document_id="document-1",
        title="Manual",
        source_kind="file_upload",
        document_format="pdf",
        source_digest=CONTENT,
        content_digest=CONTENT,
        created_at=NOW,
        original_artifact_id="artifact-1",
        content_type="application/pdf",
    )


def _control() -> StorageControlRecord:
    return StorageControlRecord(
        mode="active",
        active_target_id=FENCE.target_id,
        active_target_revision=FENCE.target_revision,
        root_identity_digest=FENCE.root_identity_digest,
        storage_epoch=FENCE.storage_epoch,
        updated_at=NOW,
    )


def _old_control() -> StorageControlRecord:
    return StorageControlRecord(
        mode="active",
        active_target_id=OLD_FENCE.target_id,
        active_target_revision=OLD_FENCE.target_revision,
        root_identity_digest=OLD_FENCE.root_identity_digest,
        storage_epoch=OLD_FENCE.storage_epoch,
        updated_at=NOW,
    )


def _target() -> StorageTargetRecord:
    return StorageTargetRecord(
        target_id=FENCE.target_id,
        target_revision=FENCE.target_revision,
        target_kind="local",
        masked_label="local",
        config_key="artifact-root",
        root_identity_digest=FENCE.root_identity_digest,
        capabilities={"create_file": True, "modify_file": True, "remove_file": True},
        status="active",
        created_at=NOW,
        updated_at=NOW,
        created_by="user-1",
        verification_mode="full_hash",
        evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
    )


def _operation(
    blobs: tuple[StorageBlobRecord, ...] | None = None,
) -> ArtifactOperationRecord:
    committed = (_blob(fence=OLD_FENCE),) if blobs is None else blobs
    return ArtifactOperationRecord(
        operation_id="operation-1",
        operation_type="target_configuration",
        idempotency_scope="target",
        idempotency_key="generation-1",
        request_fingerprint=REQUEST,
        status="succeeded",
        fence=FENCE,
        created_at=NOW,
        updated_at=NOW,
        verification_mode="full_hash",
        evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
        committed_blob_count=len(committed),
        total_bytes=sum(blob.byte_size for blob in committed),
        blob_set_digest=owner._blob_set_digest(committed),
    )


def _attempt(*, status: str = "receiving", blob_id: str | None = None):
    return ArtifactWriteAttemptRecord(
        write_attempt_id="attempt-1",
        idempotency_scope="document:document-1",
        idempotency_key="upload-1",
        request_fingerprint=REQUEST,
        fence=FENCE,
        parent_resource_id="document-1",
        parent_lifecycle_epoch=1,
        status=status,
        lease_owner="worker-1",
        lease_expires_at=NOW,
        attempt_generation=1,
        last_heartbeat_at=NOW,
        opaque_temp_name="temp-1",
        created_at=NOW,
        updated_at=NOW,
        intent={
            "artifact_class": "original_document",
            "logical_identity": "document:document-1:version-1:original",
            "content_type": "application/pdf",
            "owner_scope_type": "project",
            "owner_scope_id": "project-1",
            "document_version_id": "version-1",
        },
        blob_id=blob_id,
        byte_size=3 if blob_id else None,
        checksum_sha256=CONTENT if blob_id else None,
    )


def _lease(*, lease_id: str = "lease-1", request_kind: str = "artifact_write"):
    return StorageRequestLeaseRecord(
        lease_id=lease_id,
        request_kind=request_kind,
        owner="worker-1",
        fence=FENCE,
        acquired_at=NOW,
        expires_at=NOW,
        last_heartbeat_at=NOW,
        attempt_generation=1,
        parent_resource_id="document-1",
        parent_lifecycle_epoch=1,
    )


def _read_lease() -> StorageRequestLeaseRecord:
    return _lease(lease_id="read-lease-1", request_kind="artifact_read")


def _parent() -> owner.DocumentParentCurrentness:
    return owner.DocumentParentCurrentness(
        document_id="document-1",
        lifecycle_status="active",
        resource_lifecycle_epoch=1,
        active_processing_generation=0,
    )


def _blob(*, fence: StorageFence = FENCE) -> StorageBlobRecord:
    return StorageBlobRecord(
        blob_id="blob-1",
        opaque_ref="objects/blob-1",
        status="committed",
        dedup_mode="none",
        checksum_algorithm="sha256",
        checksum_value=CONTENT,
        byte_size=3,
        content_type="application/pdf",
        fence=fence,
        created_at=NOW,
        updated_at=NOW,
        write_attempt_id="attempt-1",
        committed_at=NOW,
    )


def _artifact() -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id="artifact-1",
        artifact_class="original_document",
        blob_id="blob-1",
        checksum_algorithm="sha256",
        checksum_value=CONTENT,
        byte_size=3,
        content_type="application/pdf",
        owner_scope_type="project",
        owner_scope_id="project-1",
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
        logical_identity="document:document-1:version-1:original",
        document_version_id="version-1",
        parent_resource_id="document-1",
        parent_lifecycle_epoch=1,
    )


def _binding() -> ArtifactScopeBindingRecord:
    return ArtifactScopeBindingRecord(
        binding_id="binding-1",
        artifact_id="artifact-1",
        binding_kind="owner",
        scope_type="project",
        scope_id="project-1",
        created_at=NOW,
    )


def _conversation_publication() -> owner.ConversationArtifactPublication:
    conversation_id = "conversation-1"
    logical_identity = f"{conversation_id}:turn-1:answer"
    attempt = replace(
        _attempt(status="succeeded", blob_id="blob-1"),
        idempotency_scope="conversation_payload",
        idempotency_key=logical_identity,
        request_fingerprint=CONTENT,
        parent_resource_id=conversation_id,
        parent_lifecycle_epoch=0,
        byte_size=3,
        checksum_sha256=CONTENT,
        intent={
            "artifact_class": "conversation_turn_answer",
            "logical_identity": logical_identity,
            "content_type": "text/plain",
            "owner_scope_type": "conversation",
            "owner_scope_id": conversation_id,
            "document_version_id": None,
            "source_artifact_id": None,
            "processing_generation": None,
            "pipeline_id": None,
            "pipeline_version": None,
            "generation": None,
            "page_number": None,
            "block_id": None,
            "acl_policy_version": None,
            "acl_action": None,
            "authorization_bindings": [],
        },
    )
    blob = replace(_blob(), content_type="text/plain")
    artifact = replace(
        _artifact(),
        artifact_class="conversation_turn_answer",
        content_type="text/plain",
        owner_scope_type="conversation",
        owner_scope_id=conversation_id,
        logical_identity=logical_identity,
        document_version_id=None,
        parent_resource_id=conversation_id,
        parent_lifecycle_epoch=0,
    )
    binding = replace(
        _binding(),
        scope_type="conversation",
        scope_id=conversation_id,
    )
    lease = replace(
        _lease(),
        parent_resource_id=conversation_id,
        parent_lifecycle_epoch=0,
    )
    return owner.ConversationArtifactPublication(
        conversation_id=conversation_id,
        fence=FENCE,
        expected_attempts=(None,),
        attempts=(attempt,),
        expected_blobs=(None,),
        blobs=(blob,),
        expected_artifacts=(None,),
        artifacts=(artifact,),
        expected_bindings=(None,),
        bindings=(binding,),
        expected_leases=(None,),
        leases=(lease,),
    )


def _finding(*, status: str = "open") -> StorageReconciliationFindingRecord:
    return StorageReconciliationFindingRecord(
        finding_id="finding-1",
        finding_kind="published_attempt_incomplete",
        status=status,
        detected_at=NOW,
        safe_summary="bounded finding",
        blob_id="blob-1",
        write_attempt_id="attempt-1",
        reconciled_at=NOW if status != "open" else None,
        reconciled_by="worker-1" if status != "open" else None,
    )


def _active_control_row():
    return owner._row(_control(), rows.AtlasArtifactStorageControlRow)


def _target_request(*, generation: bool = False) -> owner.TargetControlInput:
    blobs = (_blob(fence=OLD_FENCE),)
    return owner.TargetControlInput(
        expected_control=_old_control(),
        expected_committed_blobs=blobs,
        target=_target(),
        control=_control(),
        operation=_operation(blobs),
        audit_events=(_event(),),
        observed_at=NOW,
        generation_prefix="target-" if generation else None,
        monotonic_generation=1 if generation else None,
    )


def _prime_target_control(
    session: RecordingSession,
    *,
    leases: tuple[StorageRequestLeaseRecord, ...] = (),
) -> None:
    old_target = replace(
        _target(),
        target_id=OLD_FENCE.target_id,
        root_identity_digest=OLD_FENCE.root_identity_digest,
    )
    old_blob = _blob(fence=OLD_FENCE)
    old_blob_row = owner._row(old_blob, rows.AtlasStorageBlobRow)
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = owner._row(
        _old_control(), rows.AtlasArtifactStorageControlRow
    )
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = old_blob_row
    session.results.extend(
        [
            Result(scalars=(owner._row(old_target, rows.AtlasArtifactStorageTargetRow),)),
            Result(),
            Result(),
            Result(),
            Result(scalars=(old_blob_row,)),
            Result(),
            Result(scalars=tuple(
                owner._row(lease, rows.AtlasStorageRequestLeaseRow)
                for lease in leases
            )),
        ]
    )


def _prime_protected_rows(session: RecordingSession) -> None:
    session.rows[(owner.AtlasSessionRow, BROWSER_SESSION_TOKEN)] = (
        owner.AtlasSessionRow(
            session_token=BROWSER_SESSION_TOKEN,
            actor_id="user-1",
        )
    )
    session.rows[(owner.AtlasUserRow, "user-1")] = owner.AtlasUserRow(
        actor_id="user-1",
        display_name="Protected User",
        email="protected@example.test",
        system_role="user",
        password_digest=None,
        active=True,
        actor_type="user",
        created_at=NOW,
    )
    session.rows[(owner.AtlasDocumentRow, "document-1")] = owner._row(
        _document(), owner.AtlasDocumentRow
    )
    session.rows[(owner.AtlasDocumentVersionRow, "version-1")] = (
        owner.AtlasDocumentVersionRow(
            document_version_id="version-1",
            document_id="document-1",
            payload=asdict(_version()),
        )
    )
    session.results.append(
        Result(
            scalars=(
                owner.AtlasDocumentTagRow(
                    document_id="document-1",
                    tag_type="project",
                    tag_id="project-1",
                    created_at=NOW,
                ),
            )
        )
    )


def _protected_open_request() -> owner.ProtectedArtifactOpenInput:
    return owner.ProtectedArtifactOpenInput(
        expected_document=_document(),
        expected_version=_version(),
        expected_tag_refs=frozenset({("project", "project-1")}),
        expected_artifact=_artifact(),
        expected_blob=_blob(),
        actor_type="user",
        actor_id="user-1",
        presented_browser_session_token=BROWSER_SESSION_TOKEN,
        action="read_original",
        record_success_evidence=False,
        candidate_scope=frozenset({("project", "project-1")}),
        candidate_team_ids=frozenset(),
        access_decision=None,
        audit_events=(),
        observed_at="2026-07-17T00:00:00+00:00",
        read_lease=_read_lease(),
    )


def _prime_protected_graph(session: RecordingSession) -> None:
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    session.results.append(
        Result(
            scalars=(
                owner._row(_binding(), rows.AtlasArtifactScopeBindingRow),
            )
        )
    )


def test_public_surface_has_exact_named_commands_and_no_generic_escape_hatch() -> None:
    command_types = (
        owner.TargetControlCommand,
        owner.BeginArtifactWriteCommand,
        owner.FinalizeArtifactWriteCommand,
        owner.ProtectedArtifactOpenCommand,
        owner.ClaimArtifactReconciliationCommand,
        owner.FinalizeArtifactReconciliationCommand,
    )
    for command_type in command_types:
        assert [field.name for field in fields(command_type)] == ["session_factory"]
        methods = {
            name
            for name, value in command_type.__dict__.items()
            if not name.startswith("_") and callable(value)
        }
        expected_methods = (
            {"execute", "heartbeat", "complete"}
            if command_type is owner.ProtectedArtifactOpenCommand
            else {"execute"}
        )
        assert methods == expected_methods
    source = inspect.getsource(owner)
    for forbidden in (
        "publish_graph",
        "ChangeSet",
        "Repository",
        "UnitOfWork",
        "reader_factory",
        "filesystem",
        "open_read",
        "write_temp",
    ):
        assert forbidden not in source


def test_target_control_commits_target_control_operation_and_audit() -> None:
    session = RecordingSession()
    _prime_target_control(session)
    request = _target_request()
    result = owner.TargetControlCommand(Factory(session)).execute(request)
    assert result.replayed is False
    assert session.commits == 1
    assert session.rollbacks == 0
    assert {type(row) for row in session.added} >= {
        rows.AtlasArtifactStorageTargetRow,
        rows.AtlasArtifactOperationRow,
    }
    assert owner._matches(
        session.rows[(rows.AtlasArtifactStorageControlRow, "global")],
        _control(),
    )
    rebound = session.rows[(rows.AtlasStorageBlobRow, "blob-1")]
    assert rebound.target_id == FENCE.target_id and rebound.storage_epoch == 2


def test_target_control_rejects_live_work_and_replays_complete_graph() -> None:
    with pytest.raises(ValueError, match="evidence does not match"):
        owner.TargetControlCommand(lambda: pytest.fail("session opened")).execute(
            replace(
                _target_request(),
                operation=replace(_operation(), committed_blob_count=2),
            )
        )

    live = RecordingSession()
    _prime_target_control(
        live,
        leases=(replace(_lease(), expires_at="2026-07-19T00:00:00+00:00"),),
    )
    with pytest.raises(owner.ArtifactCommandConflict, match="active leases"):
        owner.TargetControlCommand(Factory(live)).execute(_target_request())
    assert live.commits == 0

    request = _target_request()
    replay = RecordingSession()
    replay.rows[(rows.AtlasArtifactStorageControlRow, "global")] = owner._row(
        _control(), rows.AtlasArtifactStorageControlRow
    )
    replay.rows[(rows.AtlasArtifactOperationRow, "operation-1")] = owner._row(
        request.operation, rows.AtlasArtifactOperationRow
    )
    rebound_blob = replace(
        request.expected_committed_blobs[0],
        fence=FENCE,
        updated_at=request.operation.updated_at,
    )
    replay.results.extend(
        [
            Result(scalars=(owner._row(_target(), rows.AtlasArtifactStorageTargetRow),)),
            Result(scalar=owner._row(_target(), rows.AtlasArtifactStorageTargetRow)),
            Result(scalars=(owner._row(rebound_blob, rows.AtlasStorageBlobRow),)),
        ]
    )
    assert owner.TargetControlCommand(Factory(replay)).execute(request).replayed
    assert replay.commits == 0

    alternate = replace(
        request,
        operation=replace(request.operation, operation_id="operation-new"),
    )
    alternate_replay = RecordingSession()
    alternate_replay.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        owner._row(_control(), rows.AtlasArtifactStorageControlRow)
    )
    alternate_replay.results.extend(
        [
            Result(scalars=(owner._row(_target(), rows.AtlasArtifactStorageTargetRow),)),
            Result(scalar=owner._row(request.operation, rows.AtlasArtifactOperationRow)),
            Result(scalar=owner._row(_target(), rows.AtlasArtifactStorageTargetRow)),
            Result(scalars=(owner._row(rebound_blob, rows.AtlasStorageBlobRow),)),
        ]
    )
    alternate_result = owner.TargetControlCommand(
        Factory(alternate_replay)
    ).execute(alternate)
    assert alternate_result.replayed
    assert alternate_result.canonical_id == "operation-1"


def test_named_command_rolls_back_when_required_audit_write_fails() -> None:
    session = RecordingSession()
    _prime_target_control(session)
    session.fail_audit = True
    request = _target_request()
    with pytest.raises(RuntimeError, match="audit unavailable"):
        owner.TargetControlCommand(Factory(session)).execute(request)
    assert session.commits == 0
    assert session.rollbacks == 1


def test_begin_and_finalize_write_use_short_named_transactions() -> None:
    begin_session = RecordingSession()
    begin_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    begin = owner.BeginArtifactWriteInput(_attempt(), _lease(), (_event("audit-begin"),))
    owner.BeginArtifactWriteCommand(Factory(begin_session)).execute(begin)
    assert begin_session.commits == 1
    assert {type(row) for row in begin_session.added} >= {
        rows.AtlasArtifactWriteAttemptRow,
        rows.AtlasStorageRequestLeaseRow,
    }

    missing_lease = RecordingSession()
    missing_lease.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    missing_lease.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = (
        owner._row(_attempt(), rows.AtlasArtifactWriteAttemptRow)
    )
    with pytest.raises(owner.ArtifactCommandConflict, match="replay lease changed"):
        owner.BeginArtifactWriteCommand(Factory(missing_lease)).execute(begin)

    alternate_session = RecordingSession()
    alternate_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    canonical_attempt = replace(_attempt(), write_attempt_id="attempt-canonical")
    canonical_lease = replace(_lease(), lease_id="lease-canonical")
    alternate_session.results.extend(
        [
            Result(
                scalar=owner._row(
                    canonical_attempt,
                    rows.AtlasArtifactWriteAttemptRow,
                )
            ),
            Result(
                scalars=(
                    owner._row(canonical_lease, rows.AtlasStorageRequestLeaseRow),
                )
            ),
        ]
    )
    alternate_result = owner.BeginArtifactWriteCommand(
        Factory(alternate_session)
    ).execute(begin)
    assert alternate_result.replayed
    assert alternate_result.canonical_id == "attempt-canonical"
    assert not alternate_result.continue_external_work

    terminal_session = RecordingSession()
    terminal_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    terminal_session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = (
        owner._row(
            _attempt(status="succeeded", blob_id="blob-1"),
            rows.AtlasArtifactWriteAttemptRow,
        )
    )
    terminal_result = owner.BeginArtifactWriteCommand(
        Factory(terminal_session)
    ).execute(begin)
    assert terminal_result.replayed and not terminal_result.continue_external_work

    expected = _attempt()
    finalized = _attempt(status="succeeded", blob_id="blob-1")
    finalize_session = RecordingSession()
    finalize_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    finalize_session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        expected, rows.AtlasArtifactWriteAttemptRow
    )
    finalize_session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")] = owner._row(
        _lease(), rows.AtlasStorageRequestLeaseRow
    )
    finalize_session.rows[(owner.AtlasDocumentRow, "document-1")] = owner._row(
        _document(), owner.AtlasDocumentRow
    )
    owner.FinalizeArtifactWriteCommand(Factory(finalize_session)).execute(
        owner.FinalizeArtifactWriteInput(
            expected, _lease(), _parent(), finalized, _blob(), _artifact(), (_binding(),),
            (_event("audit-final"),)
        )
    )
    assert finalize_session.commits == 1
    assert finalize_session.flushes == 1
    assert {type(row) for row in finalize_session.added} >= {
        rows.AtlasStorageBlobRow,
        rows.AtlasArtifactRow,
        rows.AtlasArtifactScopeBindingRow,
    }
    assert len(finalize_session.deleted) == 1


def test_finalize_rejects_cross_wired_graph_before_opening_session() -> None:
    request = owner.FinalizeArtifactWriteInput(
        _attempt(),
        _lease(),
        _parent(),
        _attempt(status="succeeded", blob_id="blob-1"),
        _blob(),
        replace(_artifact(), blob_id="other"),
        (_binding(),),
        (_event(),),
    )
    with pytest.raises(ValueError, match="cross-wired"):
        owner.FinalizeArtifactWriteCommand(lambda: pytest.fail("session opened")).execute(request)

    cross_scope = replace(_binding(), scope_id="project-other")
    with pytest.raises(ValueError, match="owner binding is cross-wired"):
        owner.FinalizeArtifactWriteCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                request,
                artifact=_artifact(),
                bindings=(cross_scope,),
            )
        )

    with pytest.raises(ValueError, match="immutable authority cannot move"):
        owner.FinalizeArtifactWriteCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                request,
                artifact=_artifact(),
                attempt=replace(request.attempt, idempotency_key="changed"),
            )
        )


def test_finalize_replay_rejects_changed_authority_binding() -> None:
    expected = _attempt()
    finalized = _attempt(status="succeeded", blob_id="blob-1")
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        finalized, rows.AtlasArtifactWriteAttemptRow
    )
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    changed = replace(_binding(), binding_id="binding-other")
    session.results.append(
        Result(scalars=(owner._row(changed, rows.AtlasArtifactScopeBindingRow),))
    )
    with pytest.raises(owner.ArtifactCommandConflict, match="replay graph changed"):
        owner.FinalizeArtifactWriteCommand(Factory(session)).execute(
            owner.FinalizeArtifactWriteInput(
                expected,
                _lease(),
                _parent(),
                finalized,
                _blob(),
                _artifact(),
                (_binding(),),
                (_event(),),
            )
        )


def test_protected_open_requires_exact_nonempty_browser_credential_before_session() -> None:
    token_field = next(
        field
        for field in fields(owner.ProtectedArtifactOpenInput)
        if field.name == "presented_browser_session_token"
    )
    assert token_field.default is MISSING
    assert token_field.default_factory is MISSING

    request = replace(
        _protected_open_request(),
        presented_browser_session_token=" do-not-log-this-token ",
        actor_type="agent",
    )
    with pytest.raises(ValueError) as raised:
        owner.ProtectedArtifactOpenCommand(
            lambda: pytest.fail("session opened")
        ).execute(request)
    assert "do-not-log-this-token" not in str(raised.value)

    with pytest.raises(ValueError, match="browser session credential"):
        owner.ProtectedArtifactOpenCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                _protected_open_request(),
                presented_browser_session_token="   ",
            )
        )


@pytest.mark.parametrize("current_actor_id", [None, "user-other"])
def test_protected_open_fails_before_evidence_or_lease_when_session_is_not_current(
    current_actor_id: str | None,
) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    if current_actor_id is None:
        del session.rows[(owner.AtlasSessionRow, BROWSER_SESSION_TOKEN)]
    else:
        session.rows[(owner.AtlasSessionRow, BROWSER_SESSION_TOKEN)] = (
            owner.AtlasSessionRow(
                session_token=BROWSER_SESSION_TOKEN,
                actor_id=current_actor_id,
            )
        )

    with pytest.raises(owner.ArtifactProtectedOpenUnauthenticated) as raised:
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
            _protected_open_request()
        )

    assert BROWSER_SESSION_TOKEN not in str(raised.value)
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 1
    assert any("atlas_sessions" in statement for statement in session.executed)
    assert all(
        "FOR UPDATE" not in statement
        for statement in session.executed
        if "atlas_sessions" in statement
    )


def test_protected_open_fails_before_evidence_when_token_actor_is_inactive(
    monkeypatch,
) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    session.rows[(owner.AtlasUserRow, "user-1")].active = False
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )

    with pytest.raises(owner.ArtifactProtectedOpenUnauthenticated) as raised:
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
            _protected_open_request()
        )

    assert BROWSER_SESSION_TOKEN not in str(raised.value)
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 1


def test_protected_open_validates_token_without_extending_revoke_lock_or_leaking_token(
    monkeypatch,
) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    captured: dict[str, tuple[str, ...]] = {}

    def capture_locks(_session, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(owner, "acquire_mixed_owner_locks", capture_locks)
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )

    opener = owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
        _protected_open_request()
    )

    assert f"identity:session:{BROWSER_SESSION_TOKEN}" not in captured[
        "shared_identity_keys"
    ]
    assert BROWSER_SESSION_TOKEN not in repr(opener)
    assert BROWSER_SESSION_TOKEN not in repr(session.added)


def test_protected_open_commits_evidence_and_returns_post_commit_descriptor(monkeypatch) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    artifact_row = owner._row(_artifact(), rows.AtlasArtifactRow)
    blob_row = owner._row(_blob(), rows.AtlasStorageBlobRow)
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = artifact_row
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = blob_row
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.results.append(Result(scalars=(owner._row(_binding(), rows.AtlasArtifactScopeBindingRow),)))
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )
    decision = _decision()
    opener = owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
        owner.ProtectedArtifactOpenInput(
            expected_document=_document(),
            expected_version=_version(),
            expected_tag_refs=frozenset({("project", "project-1")}),
            expected_artifact=_artifact(),
            expected_blob=_blob(),
            actor_type="user",
            actor_id="user-1",
            presented_browser_session_token=BROWSER_SESSION_TOKEN,
            action="read_original",
            record_success_evidence=True,
            candidate_scope=frozenset({("project", "project-1")}),
            candidate_team_ids=frozenset(),
            access_decision=decision,
            audit_events=(_event(decision_id=decision.decision_id),),
            observed_at="2026-07-17T00:00:00+00:00",
            read_lease=_read_lease(),
        )
    )
    assert opener.opaque_ref == "objects/blob-1"
    assert session.commits == 1
    assert {type(row).__name__ for row in session.added} >= {
        "AtlasAccessDecisionRow",
        "AtlasAuditEventRow",
    }


def test_protected_denial_commits_before_withholding_output(monkeypatch) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.results.append(Result(scalars=(owner._row(_binding(), rows.AtlasArtifactScopeBindingRow),)))
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: (set(), set()),
    )
    decision = _decision(allowed=False)
    with pytest.raises(owner.ArtifactProtectedOpenDenied):
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
            owner.ProtectedArtifactOpenInput(
                expected_document=_document(), expected_version=_version(),
                expected_tag_refs=frozenset({("project", "project-1")}),
                expected_artifact=_artifact(), expected_blob=_blob(),
                actor_type="user", actor_id="user-1", action="read_original",
                presented_browser_session_token=BROWSER_SESSION_TOKEN,
                record_success_evidence=False,
                candidate_scope=frozenset({("project", "project-1")}),
                candidate_team_ids=frozenset(), access_decision=decision,
                audit_events=(_event(decision_id=decision.decision_id),),
                observed_at="2026-07-17T00:00:00+00:00",
                read_lease=_read_lease(),
            )
        )
    assert session.commits == 1


@pytest.mark.parametrize(
    ("document", "system_role", "reason"),
    (
        (
            replace(_document(), source_download_restricted=True),
            "admin",
            "source_download_restricted",
        ),
        (
            replace(_document(), allow_member_download=False),
            "user",
            "member_download_policy",
        ),
    ),
)
def test_protected_policy_denial_commits_exact_evidence_before_output(
    monkeypatch,
    document: DocumentRecord,
    system_role: str,
    reason: str,
) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    session.rows[(owner.AtlasDocumentRow, document.document_id)] = owner._row(
        document, owner.AtlasDocumentRow
    )
    session.rows[(owner.AtlasUserRow, "user-1")].system_role = system_role
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )
    decision = _decision(allowed=False, reason=reason)
    request = replace(
        _protected_open_request(),
        expected_document=document,
        access_decision=decision,
        audit_events=(_event(decision_id=decision.decision_id),),
    )

    with pytest.raises(owner.ArtifactProtectedOpenDenied):
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(request)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert {type(row).__name__ for row in session.added} == {
        "AtlasAccessDecisionRow",
        "AtlasAuditEventRow",
    }


def test_protected_policy_denial_rejects_inexact_reason(monkeypatch) -> None:
    session = RecordingSession()
    document = replace(_document(), source_download_restricted=True)
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    session.rows[(owner.AtlasDocumentRow, document.document_id)] = owner._row(
        document, owner.AtlasDocumentRow
    )
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )
    wrong = _decision(allowed=False, reason="scope_denied")

    with pytest.raises(owner.ArtifactCommandConflict, match="policy reason"):
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
            replace(
                _protected_open_request(),
                expected_document=document,
                access_decision=wrong,
                audit_events=(_event(decision_id=wrong.decision_id),),
            )
        )

    assert session.commits == 0
    assert session.added == []


@pytest.mark.parametrize(
    ("decision", "event"),
    (
        (
            replace(_decision(allowed=False), scope_id="project-other"),
            _event(decision_id="decision-1"),
        ),
        (
            _decision(allowed=False),
            replace(_event(decision_id="decision-1"), actor_id="user-other"),
        ),
    ),
)
def test_protected_denial_rejects_cross_owned_evidence(
    decision: AccessDecisionRecord,
    event: AuditEventRecord,
) -> None:
    with pytest.raises(ValueError, match="exact decision|exact actor"):
        owner.ProtectedArtifactOpenCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                _protected_open_request(),
                access_decision=decision,
                audit_events=(event,),
            )
        )


def test_team_scoped_denial_rejects_cross_project_evidence() -> None:
    document = replace(_document(), scope_type="team", scope_id="team-1")
    artifact = replace(
        _artifact(), owner_scope_type="team", owner_scope_id="team-1"
    )
    decision = replace(
        _decision(allowed=False),
        scope_type="team",
        scope_id="team-1",
        project_id="project-other",
    )

    with pytest.raises(ValueError, match="exact decision"):
        owner.ProtectedArtifactOpenCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                _protected_open_request(),
                expected_document=document,
                expected_artifact=artifact,
                expected_tag_refs=frozenset({("team", "team-1")}),
                candidate_scope=frozenset({("team", "team-1")}),
                access_decision=decision,
                audit_events=(
                    replace(
                        _event(decision_id=decision.decision_id),
                        project_id="project-other",
                    ),
                ),
            )
        )


def test_system_admin_bypasses_member_download_policy_when_acl_allows(monkeypatch) -> None:
    session = RecordingSession()
    document = replace(_document(), allow_member_download=False)
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    session.rows[(owner.AtlasDocumentRow, document.document_id)] = owner._row(
        document, owner.AtlasDocumentRow
    )
    session.rows[(owner.AtlasUserRow, "user-1")].system_role = "admin"
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )
    decision = _decision()
    request = replace(
        _protected_open_request(),
        expected_document=document,
        record_success_evidence=True,
        access_decision=decision,
        audit_events=(_event(decision_id=decision.decision_id),),
    )

    opener = owner.ProtectedArtifactOpenCommand(Factory(session)).execute(request)

    assert opener.artifact_id == "artifact-1"
    assert session.commits == 1
    assert {type(row).__name__ for row in session.added} >= {
        "AtlasAccessDecisionRow",
        "AtlasAuditEventRow",
        "AtlasStorageRequestLeaseRow",
    }


def test_system_admin_does_not_bypass_acl_denial(monkeypatch) -> None:
    session = RecordingSession()
    document = replace(_document(), allow_member_download=False)
    _prime_protected_rows(session)
    _prime_protected_graph(session)
    session.rows[(owner.AtlasDocumentRow, document.document_id)] = owner._row(
        document, owner.AtlasDocumentRow
    )
    session.rows[(owner.AtlasUserRow, "user-1")].system_role = "admin"
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: (set(), set()),
    )
    decision = _decision(allowed=False, reason="scope_denied")

    with pytest.raises(owner.ArtifactProtectedOpenDenied):
        owner.ProtectedArtifactOpenCommand(Factory(session)).execute(
            replace(
                _protected_open_request(),
                expected_document=document,
                access_decision=decision,
                audit_events=(_event(decision_id=decision.decision_id),),
            )
        )

    assert session.commits == 1
    assert {type(row).__name__ for row in session.added} == {
        "AtlasAccessDecisionRow",
        "AtlasAuditEventRow",
    }


def test_protected_head_skips_success_evidence_and_rejects_stale_blob(monkeypatch) -> None:
    session = RecordingSession()
    _prime_protected_rows(session)
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.results.append(Result(scalars=(owner._row(_binding(), rows.AtlasArtifactScopeBindingRow),)))
    monkeypatch.setattr(
        owner,
        "read_effective_document_scope_with_team_ids",
        lambda *_args, **_kwargs: ({("project", "project-1")}, set()),
    )
    request = owner.ProtectedArtifactOpenInput(
        expected_document=_document(), expected_version=_version(),
        expected_tag_refs=frozenset({("project", "project-1")}),
        expected_artifact=_artifact(), expected_blob=_blob(),
        actor_type="user", actor_id="user-1", action="read_original",
        presented_browser_session_token=BROWSER_SESSION_TOKEN,
        record_success_evidence=False,
        candidate_scope=frozenset({("project", "project-1")}),
        candidate_team_ids=frozenset(), access_decision=None, audit_events=(),
        observed_at="2026-07-17T00:00:00+00:00",
        read_lease=_read_lease(),
    )
    with pytest.raises(ValueError, match="document lineage"):
        owner.ProtectedArtifactOpenCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            replace(
                request,
                candidate_scope=frozenset({("project", "project-other")}),
            )
        )
    opener = owner.ProtectedArtifactOpenCommand(Factory(session)).execute(request)
    assert opener.artifact_id == "artifact-1"
    assert session.commits == 1
    assert [type(row) for row in session.added] == [rows.AtlasStorageRequestLeaseRow]

    stale = RecordingSession()
    _prime_protected_rows(stale)
    stale.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    stale.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        replace(_blob(), checksum_value="d" * 64), rows.AtlasStorageBlobRow
    )
    stale.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    with pytest.raises(owner.ArtifactCommandConflict, match="not committed"):
        owner.ProtectedArtifactOpenCommand(Factory(stale)).execute(request)
    assert stale.commits == 0

    restored = RecordingSession()
    restored_document = replace(_document(), resource_lifecycle_epoch=2)
    _prime_protected_rows(restored)
    restored.rows[(owner.AtlasDocumentRow, "document-1")] = owner._row(
        restored_document, owner.AtlasDocumentRow
    )
    restored.rows[(rows.AtlasArtifactRow, "artifact-1")] = owner._row(
        _artifact(), rows.AtlasArtifactRow
    )
    restored.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    restored.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    restored.results.append(
        Result(scalars=(owner._row(_binding(), rows.AtlasArtifactScopeBindingRow),))
    )
    restored_opener = owner.ProtectedArtifactOpenCommand(Factory(restored)).execute(
        replace(request, expected_document=restored_document)
    )
    assert restored_opener.artifact_id == "artifact-1"


def test_protected_read_heartbeat_and_completion_keep_durable_fence() -> None:
    expected = _read_lease()
    extended = replace(
        expected,
        last_heartbeat_at="2026-07-18T00:00:01+00:00",
        expires_at="2026-07-18T00:02:00+00:00",
    )
    heartbeat_session = RecordingSession()
    heartbeat_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    heartbeat_session.rows[(rows.AtlasStorageRequestLeaseRow, expected.lease_id)] = (
        owner._row(expected, rows.AtlasStorageRequestLeaseRow)
    )
    command = owner.ProtectedArtifactOpenCommand(Factory(heartbeat_session))
    assert command.heartbeat(
        owner.HeartbeatArtifactReadInput(expected, extended)
    ) == extended
    assert heartbeat_session.commits == 1

    completion_session = RecordingSession()
    completion_session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = (
        _active_control_row()
    )
    completion_session.rows[(rows.AtlasStorageRequestLeaseRow, expected.lease_id)] = (
        owner._row(extended, rows.AtlasStorageRequestLeaseRow)
    )
    owner.ProtectedArtifactOpenCommand(Factory(completion_session)).complete(
        owner.CompleteArtifactReadInput(extended)
    )
    assert completion_session.commits == 1
    assert len(completion_session.deleted) == 1


def test_reconciliation_claim_and_finalize_are_separate_transactions() -> None:
    claim_session = RecordingSession()
    claim_session.rows[(rows.AtlasStorageReconciliationFindingRow, "finding-1")] = owner._row(
        _finding(), rows.AtlasStorageReconciliationFindingRow
    )
    lease = _lease(lease_id="reconcile-lease", request_kind="artifact_reconciliation")
    claim = owner.ClaimArtifactReconciliationCommand(Factory(claim_session)).execute(
        owner.ClaimArtifactReconciliationInput(_finding(), lease, (_event("audit-claim"),))
    )
    assert claim.lease_id == "reconcile-lease"
    assert claim_session.commits == 1

    final_session = RecordingSession()
    final_session.rows[(rows.AtlasStorageReconciliationFindingRow, "finding-1")] = owner._row(
        _finding(), rows.AtlasStorageReconciliationFindingRow
    )
    final_session.rows[(rows.AtlasStorageRequestLeaseRow, "reconcile-lease")] = owner._row(
        lease, rows.AtlasStorageRequestLeaseRow
    )
    final_session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        _attempt(), rows.AtlasArtifactWriteAttemptRow
    )
    final_session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        _blob(), rows.AtlasStorageBlobRow
    )
    owner.FinalizeArtifactReconciliationCommand(Factory(final_session)).execute(
        owner.FinalizeArtifactReconciliationInput(
            _finding(), _finding(status="resolved"), lease,
            _attempt(), None, _blob(), None,
            (_event("audit-resolved"),),
        )
    )
    assert final_session.commits == 1
    assert len(final_session.deleted) == 1

    with pytest.raises(ValueError, match="attempt is cross-wired"):
        owner.FinalizeArtifactReconciliationCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            owner.FinalizeArtifactReconciliationInput(
                _finding(), _finding(status="resolved"), lease,
                replace(_attempt(), write_attempt_id="attempt-other"), None,
                _blob(), None, (_event("audit-crosswire"),),
            )
        )
    with pytest.raises(ValueError, match="blob immutable authority cannot move"):
        owner.FinalizeArtifactReconciliationCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            owner.FinalizeArtifactReconciliationInput(
                _finding(), _finding(status="resolved"), lease,
                _attempt(), None, _blob(),
                replace(_blob(), opaque_ref="objects/other"),
                (_event("audit-immutable"),),
            )
        )
    with pytest.raises(ValueError, match="attempt status transition"):
        owner.FinalizeArtifactReconciliationCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            owner.FinalizeArtifactReconciliationInput(
                _finding(), _finding(status="resolved"), lease,
                _attempt(status="succeeded"), _attempt(status="receiving"),
                _blob(), None, (_event("audit-attempt-regression"),),
            )
        )
    with pytest.raises(ValueError, match="blob status transition"):
        owner.FinalizeArtifactReconciliationCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            owner.FinalizeArtifactReconciliationInput(
                _finding(), _finding(status="resolved"), lease,
                _attempt(), None, _blob(), replace(_blob(), status="pending"),
                (_event("audit-blob-regression"),),
            )
        )
    published_attempt = _attempt(status="published", blob_id="blob-1")
    for invalid_result in (
        replace(published_attempt, status="succeeded", blob_id="foreign-blob"),
        replace(published_attempt, status="succeeded", byte_size=999),
        replace(published_attempt, status="succeeded", checksum_sha256="f" * 64),
    ):
        with pytest.raises(ValueError, match="authoritative blob"):
            owner.FinalizeArtifactReconciliationCommand(
                lambda: pytest.fail("session opened")
            ).execute(
                owner.FinalizeArtifactReconciliationInput(
                    _finding(), _finding(status="resolved"), lease,
                    published_attempt, invalid_result, _blob(), None,
                    (_event("audit-attempt-result-mismatch"),),
                )
            )
    attempt_only_finding = replace(_finding(), blob_id=None)
    with pytest.raises(ValueError, match="requires an authoritative blob"):
        owner.FinalizeArtifactReconciliationCommand(
            lambda: pytest.fail("session opened")
        ).execute(
            owner.FinalizeArtifactReconciliationInput(
                attempt_only_finding,
                replace(_finding(status="resolved"), blob_id=None),
                lease,
                published_attempt,
                replace(published_attempt, status="succeeded"),
                None,
                None,
                (_event("audit-attempt-without-blob"),),
            )
        )

    recovery_session = RecordingSession()
    failed_blob = replace(
        _blob(),
        status="failed",
        failure_code="checksum_mismatch",
        committed_at=None,
    )
    pending_blob = replace(
        failed_blob,
        status="pending",
        failure_code=None,
        reconciliation_required_at=None,
    )
    recovery_session.rows[(rows.AtlasStorageReconciliationFindingRow, "finding-1")] = owner._row(
        _finding(), rows.AtlasStorageReconciliationFindingRow
    )
    recovery_session.rows[(rows.AtlasStorageRequestLeaseRow, "reconcile-lease")] = owner._row(
        lease, rows.AtlasStorageRequestLeaseRow
    )
    recovery_session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        _attempt(), rows.AtlasArtifactWriteAttemptRow
    )
    recovery_session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = owner._row(
        failed_blob, rows.AtlasStorageBlobRow
    )
    owner.FinalizeArtifactReconciliationCommand(Factory(recovery_session)).execute(
        owner.FinalizeArtifactReconciliationInput(
            _finding(), _finding(status="resolved"), lease,
            _attempt(), None, failed_blob, pending_blob,
            (_event("audit-blob-recovered"),),
        )
    )
    assert recovery_session.commits == 1


class StubCommand:
    def __init__(self, value: Any, events: list[str], name: str) -> None:
        self.value = value
        self.events = events
        self.name = name

    def execute(self, _request):
        self.events.append(self.name)
        return self.value

    def heartbeat(self, request):
        self.events.append("commit-heartbeat")
        return request.lease

    def complete(self, _request):
        self.events.append("commit-close")
        return owner.CommandResult()


class RecordingFilesystem:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.payload = b"abc"

    def open_read(self, _ref: str, *, expected_size: int):
        assert expected_size == 3
        self.events.append("open-bytes")
        return BytesIO(self.payload)

    def write_temp(self, _name, chunks, *, max_bytes):
        data = b"".join(chunks)
        assert len(data) <= max_bytes
        self.events.append("write-bytes")
        return len(data), CONTENT

    def publish_no_overwrite(self, _temp, _ref):
        self.events.append("publish-bytes")

    def verify_full(self, _ref, *, expected_size, expected_sha256):
        assert expected_size == 3 and expected_sha256 == CONTENT
        self.events.append("verify-bytes")

    def remove_temp(self, _name):
        self.events.append("remove-temp")

    def remove_committed(self, _ref):
        self.events.append("remove-committed")

    def list_blob_refs(self, *, max_refs=None):
        assert max_refs is None
        return {"objects/blob-1"}


def _adapter(events: list[str]) -> adapter.PostgresArtifactStorageAdapter:
    opener = owner.PostCommitArtifactOpener(
        "artifact-1", "blob-1", "objects/blob-1", 3, CONTENT,
        "application/pdf", _read_lease(),
    )
    result = owner.CommandResult()
    return adapter.PostgresArtifactStorageAdapter(
        StubCommand(opener, events, "commit-open"),
        StubCommand(result, events, "commit-begin"),
        StubCommand(result, events, "commit-finalize"),
        StubCommand(result, events, "commit-target"),
        StubCommand(owner.ReconciliationClaim("finding-1", "lease-1", 1), events, "commit-claim"),
        StubCommand(result, events, "commit-reconcile"),
        RecordingFilesystem(events),
    )


def test_http_adapter_preserves_conditionals_range_head_and_post_commit_open() -> None:
    events: list[str] = []
    service = _adapter(events)
    get_request = SimpleNamespace(record_success_evidence=True)
    head_request = SimpleNamespace(record_success_evidence=False)
    response = service.open_original(
        get_request, method="GET", filename="manual.pdf", range_header="bytes=1-2"
    )
    assert response.status_code == 206
    assert response.headers["ETag"] == f'"{CONTENT}"'
    assert response.headers["Content-Range"] == "bytes 1-2/3"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="atlas-document.pdf"; '
        "filename*=UTF-8''manual.pdf"
    )
    assert events == ["commit-open"]
    assert b"".join(response.body) == b"bc"
    assert events == ["commit-open", "open-bytes", "commit-close"]

    head = service.open_original(head_request, method="HEAD", filename="manual.pdf")
    assert head.status_code == 200 and b"".join(head.body) == b""
    assert events[-2:] == ["commit-open", "commit-close"]
    not_modified = service.open_original(
        get_request, method="GET", filename="manual.pdf", if_none_match=f'"{CONTENT}"',
        range_header="bytes=bad",
    )
    assert not_modified.status_code == 304
    assert "open-bytes" not in events[-2:]
    assert service.open_original(
        get_request, method="GET", filename="manual.pdf", if_match='"other"'
    ).status_code == 412
    unsatisfied = service.open_original(
        get_request, method="GET", filename="manual.pdf", range_header="bytes=9-10"
    )
    assert unsatisfied.status_code == 416
    assert unsatisfied.headers["Content-Range"] == "bytes */3"
    if_range_miss = service.open_original(
        get_request,
        method="GET",
        filename="manual.pdf",
        range_header="bytes=1-2",
        if_range='"stale"',
    )
    assert if_range_miss.status_code == 200
    assert "Content-Range" not in if_range_miss.headers
    sanitized = service.open_original(
        get_request,
        method="GET",
        filename='../folder/unsafe"\n.pdf ',
    )
    assert sanitized.headers["Content-Disposition"] == (
        'attachment; filename="atlas-document.pdf"; '
        "filename*=UTF-8''unsafe.pdf"
    )

    zero_events: list[str] = []
    zero = _adapter(zero_events)
    object.__setattr__(
        zero,
        "protected_open_command",
        StubCommand(
            owner.PostCommitArtifactOpener(
                "artifact-1", "blob-1", "objects/blob-1", 0, CONTENT,
                "application/pdf", _read_lease(),
            ),
            zero_events,
            "commit-open",
        ),
    )
    with pytest.raises(ValueError, match="positive committed byte size"):
        zero.open_original(get_request, method="GET", filename="empty.pdf")


def test_write_and_offline_target_keep_filesystem_between_named_transactions() -> None:
    events: list[str] = []
    service = _adapter(events)
    expected = _attempt()
    finalized = _attempt(status="succeeded", blob_id="blob-1")
    service.write_artifact(
        adapter.ArtifactWriteJourneyInput(
            owner.BeginArtifactWriteInput(expected, _lease(), (_event("audit-begin"),)),
            owner.FinalizeArtifactWriteInput(
                expected, _lease(), _parent(), finalized, _blob(), _artifact(),
                (_binding(),),
                (_event("audit-final"),),
            ),
            (b"abc",),
            10,
        )
    )
    assert events[:5] == [
        "commit-begin", "write-bytes", "publish-bytes", "verify-bytes", "commit-finalize"
    ]
    output = service.configure_offline_target(
        adapter.OfflineTargetInput(
            _target_request(),
            (_blob(fence=OLD_FENCE),),
        )
    )
    assert events[-2:] == ["verify-bytes", "commit-target"]
    assert output["status"] == "succeeded"
    assert output["verification_mode"] == "full_hash"

    heartbeat_service = replace(
        service,
        heartbeat_write_command=StubCommand(
            owner.CommandResult(canonical_id="attempt-1"),
            events,
            "commit-write-heartbeat",
        ),
    )
    assert heartbeat_service.heartbeat_write(SimpleNamespace()) == owner.CommandResult(
        canonical_id="attempt-1"
    )
    assert events[-1] == "commit-write-heartbeat"


class CommitThenResponseLossCommand:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, _request):
        self.events.append("commit-finalize-then-response-loss")
        raise RuntimeError("finalize-response-lost")


class CleanupFailureFilesystem(RecordingFilesystem):
    def remove_temp(self, _name):
        self.events.append("remove-temp")
        raise OSError("temp-cleanup-failed")


def test_write_adapter_retains_published_bytes_when_finalize_response_is_lost() -> None:
    events: list[str] = []
    service = replace(
        _adapter(events),
        finalize_write_command=CommitThenResponseLossCommand(  # type: ignore[arg-type]
            events
        ),
        filesystem=CleanupFailureFilesystem(events),
    )
    expected = _attempt()
    finalized = _attempt(status="succeeded", blob_id="blob-1")
    request = adapter.ArtifactWriteJourneyInput(
        owner.BeginArtifactWriteInput(
            expected,
            _lease(),
            (_event("audit-begin-cleanup"),),
        ),
        owner.FinalizeArtifactWriteInput(
            expected,
            _lease(),
            _parent(),
            finalized,
            _blob(),
            _artifact(),
            (_binding(),),
            (_event("audit-final-cleanup"),),
        ),
        (b"abc",),
        10,
    )

    with pytest.raises(RuntimeError, match="finalize-response-lost"):
        service.write_artifact(request)

    assert events == [
        "commit-begin",
        "write-bytes",
        "publish-bytes",
        "verify-bytes",
        "commit-finalize-then-response-loss",
        "remove-temp",
    ]
    assert "remove-committed" not in events


class PrimaryFailureFilesystem(CleanupFailureFilesystem):
    def __init__(self, events: list[str], fail_at: str) -> None:
        super().__init__(events)
        self.fail_at = fail_at

    def write_temp(self, name, chunks, *, max_bytes):
        result = super().write_temp(name, chunks, max_bytes=max_bytes)
        if self.fail_at == "write":
            raise RuntimeError("write-primary")
        return result

    def publish_no_overwrite(self, temp, ref):
        super().publish_no_overwrite(temp, ref)
        if self.fail_at == "publish":
            raise RuntimeError("publish-primary")

    def verify_full(self, ref, *, expected_size, expected_sha256):
        super().verify_full(
            ref,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        if self.fail_at == "verify":
            raise RuntimeError("verify-primary")


@pytest.mark.parametrize("fail_at", ("write", "publish", "verify"))
def test_write_adapter_temp_cleanup_failure_never_masks_primary_filesystem_error(
    fail_at: str,
) -> None:
    events: list[str] = []
    service = replace(
        _adapter(events),
        filesystem=PrimaryFailureFilesystem(events, fail_at),
    )
    expected = _attempt()
    request = adapter.ArtifactWriteJourneyInput(
        owner.BeginArtifactWriteInput(
            expected,
            _lease(),
            (_event(f"audit-begin-{fail_at}"),),
        ),
        owner.FinalizeArtifactWriteInput(
            expected,
            _lease(),
            _parent(),
            _attempt(status="succeeded", blob_id="blob-1"),
            _blob(),
            _artifact(),
            (_binding(),),
            (_event(f"audit-final-{fail_at}"),),
        ),
        (b"abc",),
        10,
    )

    with pytest.raises(RuntimeError, match=f"{fail_at}-primary"):
        service.write_artifact(request)

    assert events[-1] == "remove-temp"
    assert "commit-finalize" not in events


def test_portainer_target_preserves_generation_and_unverified_evidence_semantics() -> None:
    events: list[str] = []
    service = _adapter(events)
    unverified_operation = replace(
        _operation(),
        verification_mode="operator_accepted_unverified",
        evidence_claim="OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
    )
    unverified_target = replace(
        _target(),
        verification_mode="operator_accepted_unverified",
        evidence_claim="OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
    )
    command = _target_request(generation=True)
    request = adapter.PortainerTargetInput(
        replace(
            command,
            target=unverified_target,
            operation=unverified_operation,
        ),
        (_blob(fence=OLD_FENCE),),
        generation=1,
        generation_prefix="target-",
        switch_mode="explicit",
        risk_acknowledgement=UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
    )
    output = service.configure_portainer_target(request)
    assert output == {
        "status": "succeeded",
        "generation": 1,
        "verification_mode": "operator_accepted_unverified",
        "evidence_claim": "OPERATOR_ACCEPTED_UNVERIFIED_TARGET",
        "committed_blob_count": 1,
        "storage_epoch": 2,
        "replayed": False,
    }
    assert events == ["commit-target"]
    with pytest.raises(ValueError, match="risk acknowledgement"):
        service.configure_portainer_target(
            replace(request, risk_acknowledgement="unsafe")
        )


def test_shared_and_mixed_lock_helpers_are_pure_and_sorted() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/atlas_production/infrastructure/postgres_locks.py"
    ).read_text()
    assert "pg_advisory_xact_lock_shared" in source
    assert "ordered_domain" in source and "ordered_identity" in source
    assert "session.commit" not in source and "session.rollback" not in source


def _prime_conversation_publication(
    session: RecordingSession,
    publication: owner.ConversationArtifactPublication,
) -> None:
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    for row_type, identity, record in (
        (rows.AtlasArtifactWriteAttemptRow, publication.attempts[0].write_attempt_id, publication.attempts[0]),
        (rows.AtlasStorageBlobRow, publication.blobs[0].blob_id, publication.blobs[0]),
        (rows.AtlasArtifactRow, publication.artifacts[0].artifact_id, publication.artifacts[0]),
        (rows.AtlasArtifactScopeBindingRow, publication.bindings[0].binding_id, publication.bindings[0]),
        (rows.AtlasStorageRequestLeaseRow, publication.leases[0].lease_id, publication.leases[0]),
    ):
        session.rows[(row_type, identity)] = owner._row(record, row_type)


def test_conversation_artifact_writer_uses_caller_session_without_audit_or_commit() -> None:
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()

    result = owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_conversation_metadata(_conversation_publication())

    assert result == owner.CommandResult()
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.flushes == 5
    assert {type(item) for item in session.added} == {
        rows.AtlasArtifactWriteAttemptRow,
        rows.AtlasStorageBlobRow,
        rows.AtlasArtifactRow,
        rows.AtlasArtifactScopeBindingRow,
        rows.AtlasStorageRequestLeaseRow,
    }
    assert not any(type(item).__name__ == "AtlasAuditEventRow" for item in session.added)


def test_conversation_artifact_writer_accepts_only_exact_terminal_replay() -> None:
    publication = _conversation_publication()
    session = RecordingSession()
    _prime_conversation_publication(session, publication)

    result = owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_conversation_metadata(publication)

    assert result == owner.CommandResult(replayed=True, continue_external_work=False)
    assert session.added == []
    assert session.commits == 0

    session.rows[(rows.AtlasStorageBlobRow, "blob-1")].byte_size = 4
    with pytest.raises(owner.ArtifactCommandConflict, match="partially replayed"):
        owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_conversation_metadata(publication)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda request: replace(
                request,
                artifacts=(replace(request.artifacts[0], owner_scope_id="conversation-2"),),
            ),
            "lineage",
        ),
        (
            lambda request: replace(
                request,
                artifacts=(replace(request.artifacts[0], blob_id="blob-2"),),
            ),
            "cross-wired",
        ),
        (
            lambda request: replace(request, attempts=(object(),)),
            "record type",
        ),
    ],
)
def test_conversation_artifact_writer_rejects_foreign_crosswired_and_arbitrary_records(
    mutation, message: str
) -> None:
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    with pytest.raises((TypeError, ValueError), match=message):
        owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_conversation_metadata(mutation(_conversation_publication()))


@pytest.mark.parametrize(
    "artifact_class",
    ("preview", "original_document", "document_page_pdf", "index_snapshot"),
)
def test_conversation_artifact_writer_rejects_foreign_artifact_classes(
    artifact_class: str,
) -> None:
    request = _conversation_publication()
    artifact = replace(request.artifacts[0], artifact_class=artifact_class)
    attempt = replace(
        request.attempts[0],
        intent={**request.attempts[0].intent, "artifact_class": artifact_class},
    )

    with pytest.raises(ValueError, match="lineage"):
        owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
            RecordingSession()
        ).publish_conversation_metadata(
            replace(request, attempts=(attempt,), artifacts=(artifact,))
        )


@pytest.mark.parametrize("collision", ("attempt", "artifact", "binding"))
def test_conversation_artifact_writer_rejects_alternate_unique_identity(
    collision: str,
) -> None:
    request = _conversation_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    attempt_row = owner._row(
        replace(request.attempts[0], write_attempt_id="attempt-foreign"),
        rows.AtlasArtifactWriteAttemptRow,
    )
    artifact_row = owner._row(
        replace(request.artifacts[0], artifact_id="artifact-foreign"),
        rows.AtlasArtifactRow,
    )
    binding_row = owner._row(
        replace(request.bindings[0], binding_id="binding-foreign"),
        rows.AtlasArtifactScopeBindingRow,
    )
    session.results = {
        "attempt": [Result(scalar=attempt_row)],
        "artifact": [Result(), Result(scalar=artifact_row)],
        "binding": [Result(), Result(), Result(scalar=binding_row)],
    }[collision]

    with pytest.raises(owner.ArtifactCommandConflict, match="owned elsewhere"):
        owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_conversation_metadata(request)


def test_conversation_artifact_writer_rejects_stale_preimage() -> None:
    publication = _conversation_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    stale = replace(publication.attempts[0], status="published")
    session.rows[(rows.AtlasArtifactWriteAttemptRow, stale.write_attempt_id)] = owner._row(
        stale, rows.AtlasArtifactWriteAttemptRow
    )

    with pytest.raises(owner.ArtifactCommandConflict, match="preimage changed"):
        owner.ConversationArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_conversation_metadata(publication)


def test_conversation_artifact_writer_exposes_no_transaction_or_generic_escape() -> None:
    writer_source = inspect.getsource(owner.ConversationArtifactPublicationWriter)
    begin_source = inspect.getsource(owner.BeginArtifactWriteCommand)
    assert "publish_conversation_metadata" in writer_source
    assert "artifact:idempotency:" in writer_source
    assert "artifact:idempotency:" in begin_source
    assert "artifact:attempt-idempotency:" not in writer_source
    for forbidden in (
        "def commit", "def rollback", "session_factory", "publish_graph",
        "ArtifactMetadataWriter", "UnitOfWork", "Repository",
    ):
        assert forbidden not in writer_source


def _new_document_original_publication(
    *,
    artifact_class: str = "original_document",
    owner_scope_type: str = "project",
    owner_scope_id: str = "project-1",
) -> owner.NewDocumentOriginalArtifactPublication:
    artifact = replace(
        _artifact(),
        artifact_class=artifact_class,
        owner_scope_type=owner_scope_type,
        owner_scope_id=owner_scope_id,
    )
    intent = {
        "artifact_class": artifact.artifact_class,
        "logical_identity": artifact.logical_identity,
        "content_type": artifact.content_type,
        "owner_scope_type": artifact.owner_scope_type,
        "owner_scope_id": artifact.owner_scope_id,
        "document_version_id": artifact.document_version_id,
        "source_artifact_id": artifact.source_artifact_id,
        "processing_generation": artifact.processing_generation,
        "pipeline_id": artifact.pipeline_id,
        "pipeline_version": artifact.pipeline_version,
        "generation": artifact.generation,
        "page_number": artifact.page_number,
        "block_id": artifact.block_id,
        "acl_policy_version": artifact.acl_policy_version,
        "acl_action": artifact.acl_action,
        "authorization_bindings": [[owner_scope_type, owner_scope_id]],
        "allowed_parent_statuses": ["active"],
    }
    expected = replace(_attempt(), intent=intent)
    final = replace(
        expected,
        status="succeeded",
        blob_id="blob-1",
        byte_size=3,
        checksum_sha256=CONTENT,
    )
    authorization = replace(
        _binding(),
        binding_id="binding-authorization-1",
        binding_kind="authorization",
        scope_type=owner_scope_type,
        scope_id=owner_scope_id,
    )
    owner_binding = replace(
        _binding(),
        scope_type=owner_scope_type,
        scope_id=owner_scope_id,
    )
    return owner.NewDocumentOriginalArtifactPublication(
        fence=FENCE,
        expected_attempt=expected,
        expected_lease=_lease(),
        attempt=final,
        blob=replace(
            _blob(),
            dedup_mode="original",
            dedup_scope_type=owner_scope_type,
            dedup_scope_id=owner_scope_id,
        ),
        artifact=artifact,
        bindings=(owner_binding, authorization),
        verified_tag_scopes=frozenset({(owner_scope_type, owner_scope_id)}),
    )


def _reuse_new_document_original_publication(
) -> owner.NewDocumentOriginalArtifactPublication:
    request = _new_document_original_publication()
    return replace(
        request,
        blob=replace(request.blob, write_attempt_id="attempt-existing"),
        reuse_committed_blob=True,
    )


def test_write_lease_heartbeat_updates_only_exact_active_attempt_and_lease() -> None:
    expected = replace(
        _attempt(),
        lease_expires_at="2026-07-18T00:01:00+00:00",
        last_heartbeat_at="2026-07-18T00:00:00+00:00",
    )
    expected_lease = replace(
        _lease(),
        expires_at=expected.lease_expires_at,
        last_heartbeat_at=expected.last_heartbeat_at,
    )
    updated = replace(
        expected,
        lease_expires_at="2026-07-18T00:02:00+00:00",
        last_heartbeat_at="2026-07-18T00:00:30+00:00",
        updated_at="2026-07-18T00:00:30+00:00",
    )
    updated_lease = replace(
        expected_lease,
        expires_at=updated.lease_expires_at,
        last_heartbeat_at=updated.last_heartbeat_at,
    )
    request = owner.HeartbeatArtifactWriteInput(
        expected,
        expected_lease,
        updated,
        updated_lease,
        "2026-07-18T00:00:30+00:00",
    )
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        expected,
        rows.AtlasArtifactWriteAttemptRow,
    )
    session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")] = owner._row(
        expected_lease,
        rows.AtlasStorageRequestLeaseRow,
    )

    result = owner.HeartbeatArtifactWriteCommand(Factory(session)).execute(request)

    assert result == owner.CommandResult(canonical_id="attempt-1")
    assert session.commits == 1
    assert owner._matches(
        session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")],
        updated,
    )
    assert owner._matches(
        session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")],
        updated_lease,
    )
    rendered = "\n".join(session.executed)
    assert "pg_advisory_xact_lock_shared" in rendered
    assert "pg_advisory_xact_lock" in rendered

    replay = owner.HeartbeatArtifactWriteCommand(Factory(session)).execute(request)
    assert replay.replayed is True
    assert session.commits == 1


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda request: replace(
                request,
                observed_at="2026-07-18T00:01:00+00:00",
            ),
            "invalid",
        ),
        (
            lambda request: replace(
                request,
                lease=replace(
                    request.lease,
                    expires_at="2026-07-18T00:02:01+00:00",
                ),
            ),
            "invalid",
        ),
        (
            lambda request: replace(
                request,
                attempt=replace(request.attempt, request_fingerprint="f" * 64),
            ),
            "immutable authority",
        ),
    ],
)
def test_write_lease_heartbeat_rejects_expired_unbounded_or_changed_authority(
    mutation,
    message: str,
) -> None:
    expected = replace(
        _attempt(),
        lease_expires_at="2026-07-18T00:01:00+00:00",
    )
    expected_lease = replace(_lease(), expires_at=expected.lease_expires_at)
    request = owner.HeartbeatArtifactWriteInput(
        expected,
        expected_lease,
        replace(
            expected,
            lease_expires_at="2026-07-18T00:02:00+00:00",
            last_heartbeat_at="2026-07-18T00:00:30+00:00",
            updated_at="2026-07-18T00:00:30+00:00",
        ),
        replace(
            expected_lease,
            expires_at="2026-07-18T00:02:00+00:00",
            last_heartbeat_at="2026-07-18T00:00:30+00:00",
        ),
        "2026-07-18T00:00:30+00:00",
    )
    with pytest.raises(ValueError, match=message):
        owner.HeartbeatArtifactWriteCommand(Factory(RecordingSession())).execute(
            mutation(request)
        )


def test_new_document_original_writer_stages_exact_graph_without_transaction_authority() -> None:
    request = _new_document_original_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        request.expected_attempt,
        rows.AtlasArtifactWriteAttemptRow,
    )
    session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")] = owner._row(
        request.expected_lease,
        rows.AtlasStorageRequestLeaseRow,
    )

    result = owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_new_document_original(request)

    assert result == owner.CommandResult()
    assert session.commits == 0 and session.rollbacks == 0
    assert session.flushes == 1
    assert session.deleted == [
        session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")]
    ]
    assert {type(item) for item in session.added} == {
        rows.AtlasStorageBlobRow,
        rows.AtlasArtifactRow,
        rows.AtlasArtifactScopeBindingRow,
    }
    assert not any(type(item).__name__ == "AtlasAuditEventRow" for item in session.added)
    identities = owner.new_document_original_artifact_lock_identities(request)
    assert identities == tuple(sorted(identities))
    assert "artifact:parent:document-1" in identities
    assert "project:project:project-1" in identities
    assert "artifact:blob-opaque:objects/blob-1" in identities
    assert (
        "artifact:logical:original_document:"
        "document:document-1:version-1:original"
    ) in identities
    assert (
        'artifact:original-dedup:["project","project-1","sha256","'
        + CONTENT
        + '",3]'
    ) in identities
    assert "artifact:canonical-original:version-1" in identities


def test_new_document_original_writer_reuses_exact_committed_same_scope_blob() -> None:
    request = _reuse_new_document_original_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        request.expected_attempt,
        rows.AtlasArtifactWriteAttemptRow,
    )
    session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")] = owner._row(
        request.expected_lease,
        rows.AtlasStorageRequestLeaseRow,
    )
    existing_blob = owner._row(request.blob, rows.AtlasStorageBlobRow)
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = existing_blob
    session.results = [
        Result(),
        Result(),
        Result(scalar=existing_blob),
        Result(scalar=existing_blob),
        Result(),
        Result(),
        Result(),
    ]
    before = {
        column.name: getattr(existing_blob, column.name)
        for column in rows.AtlasStorageBlobRow.__table__.columns
    }

    result = owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_new_document_original(request)

    assert result == owner.CommandResult()
    assert not any(type(item) is rows.AtlasStorageBlobRow for item in session.added)
    assert {type(item) for item in session.added} == {
        rows.AtlasArtifactRow,
        rows.AtlasArtifactScopeBindingRow,
    }
    assert {
        column.name: getattr(existing_blob, column.name)
        for column in rows.AtlasStorageBlobRow.__table__.columns
    } == before
    assert session.commits == 0 and session.rollbacks == 0


def test_original_dedup_lock_identity_separates_primary_owner_scope() -> None:
    project = _new_document_original_publication()
    team = _new_document_original_publication(
        owner_scope_type="team",
        owner_scope_id="team-1",
    )
    project_keys = owner.new_document_original_artifact_lock_identities(project)
    team_keys = owner.new_document_original_artifact_lock_identities(team)
    project_dedup = next(
        item for item in project_keys if item.startswith("artifact:original-dedup:")
    )
    team_dedup = next(
        item for item in team_keys if item.startswith("artifact:original-dedup:")
    )
    assert project_dedup != team_dedup
    assert '"project","project-1"' in project_dedup
    assert '"team","team-1"' in team_dedup

    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = owner._row(
        team.expected_attempt,
        rows.AtlasArtifactWriteAttemptRow,
    )
    session.rows[(rows.AtlasStorageRequestLeaseRow, "lease-1")] = owner._row(
        team.expected_lease,
        rows.AtlasStorageRequestLeaseRow,
    )
    project_blob = replace(
        project.blob,
        blob_id="blob-project-existing",
        opaque_ref="objects/project-existing",
    )
    session.rows[(rows.AtlasStorageBlobRow, project_blob.blob_id)] = owner._row(
        project_blob,
        rows.AtlasStorageBlobRow,
    )

    owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_new_document_original(team)

    assert any(
        type(item) is rows.AtlasStorageBlobRow and item.blob_id == team.blob.blob_id
        for item in session.added
    )


@pytest.mark.parametrize(
    "blob_mutation",
    [
        lambda blob: replace(blob, status="pending", committed_at=None),
        lambda blob: replace(blob, status="failed", failure_code="failed"),
        lambda blob: replace(blob, status="quarantined", failure_code="quarantined"),
        lambda blob: replace(blob, fence=OLD_FENCE),
        lambda blob: replace(blob, content_type="text/plain"),
    ],
)
def test_new_document_original_blob_reuse_rejects_unsafe_currentness(
    blob_mutation,
) -> None:
    request = _reuse_new_document_original_publication()
    mutated_blob = blob_mutation(request.blob)
    artifact = replace(
        request.artifact,
        content_type=mutated_blob.content_type,
    )
    attempt = replace(
        request.attempt,
        fence=mutated_blob.fence,
    )
    with pytest.raises(ValueError):
        owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
            RecordingSession()
        ).publish_new_document_original(
            replace(
                request,
                blob=mutated_blob,
                artifact=artifact,
                attempt=attempt,
            )
        )


@pytest.mark.parametrize(
    "collision, message",
    [
        ("logical", "artifact logical identity"),
        ("opaque", "blob opaque identity"),
        ("dedup", "dedup identity"),
        ("canonical", "canonical original identity"),
    ],
)
def test_new_document_original_writer_rejects_each_alternate_unique_owner(
    collision: str,
    message: str,
) -> None:
    request = _new_document_original_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    foreign_blob = owner._row(
        replace(request.blob, blob_id="blob-foreign"),
        rows.AtlasStorageBlobRow,
    )
    foreign_artifact = owner._row(
        replace(request.artifact, artifact_id="artifact-foreign"),
        rows.AtlasArtifactRow,
    )
    session.results = {
        "logical": [Result(), Result(scalar=foreign_artifact)],
        "opaque": [Result(), Result(), Result(scalar=foreign_blob)],
        "dedup": [Result(), Result(), Result(), Result(scalar=foreign_blob)],
        "canonical": [
            Result(),
            Result(),
            Result(),
            Result(),
            Result(scalar=foreign_artifact),
        ],
    }[collision]

    with pytest.raises(owner.ArtifactCommandConflict, match=message):
        owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_new_document_original(request)
    assert session.added == [] and session.deleted == []
    assert session.commits == 0 and session.rollbacks == 0


@pytest.mark.parametrize(
    "collision, message",
    [
        ("logical", "artifact logical identity"),
        ("opaque", "blob opaque identity"),
        ("dedup", "original dedup identity"),
        ("canonical", "canonical original identity"),
    ],
)
def test_stable_finalize_rejects_same_alternate_unique_owners_as_publisher(
    collision: str,
    message: str,
) -> None:
    expected = _attempt()
    finalized = _attempt(status="succeeded", blob_id="blob-1")
    blob = replace(
        _blob(),
        dedup_mode="original",
        dedup_scope_type="project",
        dedup_scope_id="project-1",
    )
    foreign_blob = owner._row(
        replace(blob, blob_id="blob-foreign"),
        rows.AtlasStorageBlobRow,
    )
    foreign_artifact = owner._row(
        replace(_artifact(), artifact_id="artifact-foreign"),
        rows.AtlasArtifactRow,
    )
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.results = {
        "logical": [Result(scalar=foreign_artifact)],
        "opaque": [Result(), Result(scalar=foreign_blob)],
        "dedup": [Result(), Result(), Result(scalar=foreign_blob)],
        "canonical": [
            Result(),
            Result(),
            Result(),
            Result(scalar=foreign_artifact),
        ],
    }[collision]
    request = owner.FinalizeArtifactWriteInput(
        expected,
        _lease(),
        _parent(),
        finalized,
        blob,
        _artifact(),
        (_binding(),),
        (_event(f"audit-finalize-{collision}"),),
    )

    with pytest.raises(owner.ArtifactCommandConflict, match=message):
        owner.FinalizeArtifactWriteCommand(Factory(session)).execute(request)
    assert session.added == [] and session.commits == 0
    finalize_source = inspect.getsource(owner.FinalizeArtifactWriteCommand)
    publisher_source = inspect.getsource(
        owner.NewDocumentOriginalArtifactPublicationWriter
    )
    assert "_artifact_unique_lock_identities" in finalize_source
    assert "_require_artifact_unique_owners" in finalize_source
    assert "new_document_original_artifact_lock_identities" in publisher_source
    assert "_require_artifact_unique_owners" in publisher_source


@pytest.mark.parametrize("reuse_committed_blob", (False, True))
def test_new_document_original_writer_exact_terminal_graph_is_replay_noop(
    reuse_committed_blob: bool,
) -> None:
    request = (
        _reuse_new_document_original_publication()
        if reuse_committed_blob
        else _new_document_original_publication()
    )
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    attempt_row = owner._row(request.attempt, rows.AtlasArtifactWriteAttemptRow)
    blob_row = owner._row(request.blob, rows.AtlasStorageBlobRow)
    artifact_row = owner._row(request.artifact, rows.AtlasArtifactRow)
    binding_rows = tuple(
        owner._row(item, rows.AtlasArtifactScopeBindingRow)
        for item in request.bindings
    )
    session.rows[(rows.AtlasArtifactWriteAttemptRow, "attempt-1")] = attempt_row
    session.rows[(rows.AtlasStorageBlobRow, "blob-1")] = blob_row
    session.rows[(rows.AtlasArtifactRow, "artifact-1")] = artifact_row
    for item in binding_rows:
        session.rows[(rows.AtlasArtifactScopeBindingRow, item.binding_id)] = item
    session.results = [
        Result(scalar=attempt_row),
        Result(scalar=artifact_row),
        Result(scalar=blob_row),
        Result(scalar=blob_row),
        Result(scalar=artifact_row),
        Result(),
        Result(scalars=binding_rows),
    ]

    result = owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
        session
    ).publish_new_document_original(request)

    assert result == owner.CommandResult(
        replayed=True,
        continue_external_work=False,
    )
    assert session.added == [] and session.deleted == []
    assert session.commits == 0 and session.rollbacks == 0


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda request: replace(
                request,
                artifact=replace(request.artifact, artifact_class="original_inline_source"),
                attempt=replace(
                    request.attempt,
                    intent={
                        **request.attempt.intent,
                        "artifact_class": "original_inline_source",
                    },
                ),
                expected_attempt=replace(
                    request.expected_attempt,
                    intent={
                        **request.expected_attempt.intent,
                        "artifact_class": "original_inline_source",
                    },
                ),
            ),
            "graph",
        ),
        (
            lambda request: replace(
                request,
                verified_tag_scopes=frozenset({("team", "team-1")}),
            ),
            "authorization bindings",
        ),
        (
            lambda request: replace(
                request,
                bindings=request.bindings[:1],
            ),
            "authorization bindings",
        ),
    ],
)
def test_new_document_original_writer_rejects_foreign_or_incomplete_graph(
    mutation,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
            RecordingSession()
        ).publish_new_document_original(
            mutation(_new_document_original_publication())
        )


def test_new_document_original_writer_treats_findings_as_read_only_blockers() -> None:
    request = _new_document_original_publication()
    session = RecordingSession()
    session.rows[(rows.AtlasArtifactStorageControlRow, "global")] = _active_control_row()
    session.results = [
        Result(),
        Result(),
        Result(),
        Result(),
        Result(),
        Result(
            scalars=(
                owner._row(
                    _finding(),
                    rows.AtlasStorageReconciliationFindingRow,
                ),
            )
        ),
    ]

    with pytest.raises(owner.ArtifactCommandConflict, match="reconciliation findings"):
        owner.NewDocumentOriginalArtifactPublicationWriter(  # type: ignore[arg-type]
            session
        ).publish_new_document_original(request)
    assert session.added == [] and session.deleted == []
    assert session.commits == 0 and session.rollbacks == 0


def test_new_document_original_writer_exposes_no_commit_audit_filesystem_or_generic_escape() -> None:
    writer_source = inspect.getsource(owner.NewDocumentOriginalArtifactPublicationWriter)
    for forbidden in (
        "def commit",
        "def rollback",
        "session_factory",
        "AuditEventWriter",
        "filesystem",
        "publish_graph",
        "UnitOfWork",
        "Repository",
    ):
        assert forbidden not in writer_source
