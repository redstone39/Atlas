from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
from queue import Queue
from time import monotonic, sleep

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasArtifactScopeBindingRow,
    AtlasArtifactStorageControlRow,
    AtlasArtifactStorageTargetRow,
    AtlasArtifactWriteAttemptRow,
    AtlasStorageBlobRow,
    AtlasStorageRequestLeaseRow,
)
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentRow
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasProcessingIdentityRow as _AtlasProcessingIdentityRow,  # noqa: F401
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_locks import (
    acquire_mixed_owner_locks,
    acquire_owner_locks,
    acquire_shared_owner_locks,
)
from atlas_production.infrastructure.postgres_owner.artifact import (
    ArtifactCommandConflict,
    DocumentParentCurrentness,
    FinalizeArtifactWriteCommand,
    FinalizeArtifactWriteInput,
    HeartbeatArtifactWriteCommand,
    HeartbeatArtifactWriteInput,
    NewDocumentOriginalArtifactPublication,
    NewDocumentOriginalArtifactPublicationWriter,
    TargetControlCommand,
    TargetControlInput,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.artifact_storage.records import (
    ArtifactRecord,
    ArtifactOperationRecord,
    ArtifactScopeBindingRecord,
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageFence,
    StorageControlRecord,
    StorageRequestLeaseRecord,
    StorageTargetRecord,
)
from atlas_production.modules.document_intake.records import DocumentRecord
from atlas_production.shared.public import AuditEventRecord


NOW = "2026-07-17T00:00:00+00:00"
TARGET_ID = "target-c3-authority-concurrency"
PARENT_ID = "document-c3-authority-concurrency"
VERSION_ID = "version-c3-authority-concurrency"
ATTEMPT_ID = "attempt-c3-authority-concurrency"
LEASE_ID = "lease-c3-authority-concurrency"
READ_LEASE_ID = "read-lease-c3-durable-interval"
BLOB_ID = "blob-c3-authority-concurrency"
ARTIFACT_ID = "artifact-c3-authority-concurrency"
OWNER_BINDING_ID = "binding-c3-authority-owner"
AUTHORIZATION_A_ID = "binding-c3-authority-a"
AUTHORIZATION_B_ID = "binding-c3-authority-b"
ROOT_DIGEST = "a" * 64
CONTENT_DIGEST = "b" * 64
REQUEST_DIGEST = "c" * 64
FENCE = StorageFence(TARGET_ID, 1, ROOT_DIGEST, 2)


def _publication_records(
    authorization_binding_id: str,
) -> tuple[
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    ArtifactScopeBindingRecord,
]:
    logical_identity = f"document:{PARENT_ID}:{VERSION_ID}:original"
    attempt = ArtifactWriteAttemptRecord(
        write_attempt_id=ATTEMPT_ID,
        idempotency_scope=f"document:{PARENT_ID}",
        idempotency_key="authority-concurrency",
        request_fingerprint=REQUEST_DIGEST,
        fence=FENCE,
        parent_resource_id=PARENT_ID,
        parent_lifecycle_epoch=0,
        status="succeeded",
        lease_owner="worker-c3-authority",
        lease_expires_at=NOW,
        attempt_generation=1,
        last_heartbeat_at=NOW,
        opaque_temp_name="opaque-c3-authority",
        created_at=NOW,
        updated_at=NOW,
        intent={
            "artifact_class": "original_document",
            "logical_identity": logical_identity,
            "content_type": "application/pdf",
            "owner_scope_type": "project",
            "owner_scope_id": "project-c3-authority",
            "document_version_id": VERSION_ID,
            "source_artifact_id": None,
            "processing_generation": 1,
            "pipeline_id": None,
            "pipeline_version": None,
            "generation": None,
            "page_number": None,
            "block_id": None,
            "acl_policy_version": None,
            "acl_action": None,
            "authorization_bindings": [["project", "project-c3-authority"]],
            "allowed_parent_statuses": ["active"],
        },
        blob_id=BLOB_ID,
        byte_size=3,
        checksum_sha256=CONTENT_DIGEST,
    )
    blob = StorageBlobRecord(
        blob_id=BLOB_ID,
        opaque_ref="opaque/c3-authority-concurrency",
        status="committed",
        dedup_mode="none",
        checksum_algorithm="sha256",
        checksum_value=CONTENT_DIGEST,
        byte_size=3,
        content_type="application/pdf",
        fence=FENCE,
        created_at=NOW,
        updated_at=NOW,
        write_attempt_id=ATTEMPT_ID,
        committed_at=NOW,
    )
    artifact = ArtifactRecord(
        artifact_id=ARTIFACT_ID,
        artifact_class="original_document",
        blob_id=BLOB_ID,
        checksum_algorithm="sha256",
        checksum_value=CONTENT_DIGEST,
        byte_size=3,
        content_type="application/pdf",
        owner_scope_type="project",
        owner_scope_id="project-c3-authority",
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
        logical_identity=logical_identity,
        document_version_id=VERSION_ID,
        parent_resource_id=PARENT_ID,
        parent_lifecycle_epoch=0,
        processing_generation=1,
    )
    owner = ArtifactScopeBindingRecord(
        binding_id=OWNER_BINDING_ID,
        artifact_id=ARTIFACT_ID,
        binding_kind="owner",
        scope_type="project",
        scope_id="project-c3-authority",
        created_at=NOW,
    )
    authorization = ArtifactScopeBindingRecord(
        binding_id=authorization_binding_id,
        artifact_id=ARTIFACT_ID,
        binding_kind="authorization",
        scope_type="project",
        scope_id="project-c3-authority",
        created_at=NOW,
    )
    return attempt, blob, artifact, owner, authorization


def _expected_attempt() -> ArtifactWriteAttemptRecord:
    final = _publication_records(AUTHORIZATION_A_ID)[0]
    return replace(
        final,
        status="receiving",
        blob_id=None,
        byte_size=None,
        checksum_sha256=None,
    )


def _write_lease() -> StorageRequestLeaseRecord:
    return StorageRequestLeaseRecord(
        lease_id=LEASE_ID,
        request_kind="artifact_write",
        owner="worker-c3-authority",
        fence=FENCE,
        acquired_at=NOW,
        expires_at=NOW,
        last_heartbeat_at=NOW,
        attempt_generation=1,
        parent_resource_id=PARENT_ID,
        parent_lifecycle_epoch=0,
    )


def _document() -> DocumentRecord:
    return DocumentRecord(
        document_id=PARENT_ID,
        title="Concurrency parent",
        source_digest=CONTENT_DIGEST,
        source_kind="file_upload",
        document_format="pdf",
        content_type="application/pdf",
        scope_type="project",
        scope_id="project-c3-authority",
        resource_lifecycle_epoch=0,
        active_processing_generation=1,
    )


def _payload(record: object) -> dict[str, object]:
    payload = asdict(record)
    fence = payload.pop("fence", None)
    if isinstance(fence, dict):
        payload.update(fence)
    if "intent" in payload:
        payload["intent_json"] = payload.pop("intent")
    if "metadata" in payload:
        payload["metadata_json"] = payload.pop("metadata")
    return payload


def _audit(event_id: str) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event_id,
        event_type="artifact_test",
        actor_id="test-c3-authority",
        target_ref=f"artifact:{ARTIFACT_ID}",
        project_id="project-c3-authority",
        message_code="project.is_ready_for_membership_setup",
        metadata={},
        created_at=NOW,
    )


def _delete_authority_fixture(
    runtime: PostgresRuntime,
    *,
    restore_control: dict[str, object] | None = None,
) -> None:
    with runtime.session_factory() as session:
        session.execute(
            delete(AtlasArtifactScopeBindingRow).where(
                AtlasArtifactScopeBindingRow.artifact_id == ARTIFACT_ID
            )
        )
        session.execute(
            delete(AtlasArtifactRow).where(
                AtlasArtifactRow.artifact_id == ARTIFACT_ID
            )
        )
        session.execute(
            delete(AtlasStorageBlobRow).where(
                AtlasStorageBlobRow.blob_id == BLOB_ID
            )
        )
        session.execute(
            delete(AtlasArtifactWriteAttemptRow).where(
                AtlasArtifactWriteAttemptRow.write_attempt_id == ATTEMPT_ID
            )
        )
        session.execute(
            delete(AtlasDocumentRow).where(AtlasDocumentRow.document_id == PARENT_ID)
        )
        session.execute(
            delete(AtlasProjectRow).where(
                AtlasProjectRow.project_id == "project-c3-authority"
            )
        )
        session.execute(
            delete(AtlasStorageRequestLeaseRow).where(
                AtlasStorageRequestLeaseRow.lease_id.in_((LEASE_ID, READ_LEASE_ID))
            )
        )
        control = session.get(AtlasArtifactStorageControlRow, "global")
        if control is not None and control.active_target_id == TARGET_ID:
            session.delete(control)
            session.flush()
        session.execute(
            delete(AtlasArtifactStorageTargetRow).where(
                AtlasArtifactStorageTargetRow.target_id == TARGET_ID
            )
        )
        if restore_control is not None:
            session.add(AtlasArtifactStorageControlRow(**restore_control))
        session.commit()


def _seed_authority_fixture(
    runtime: PostgresRuntime,
) -> dict[str, object] | None:
    previous_control: dict[str, object] | None = None
    with runtime.session_factory() as session:
        current = session.get(AtlasArtifactStorageControlRow, "global")
        if current is not None:
            if current.active_target_id != TARGET_ID:
                previous_control = {
                    column.name: getattr(current, column.name)
                    for column in AtlasArtifactStorageControlRow.__table__.columns
                }
            session.delete(current)
            session.flush()
        session.execute(
            delete(AtlasArtifactScopeBindingRow).where(
                AtlasArtifactScopeBindingRow.artifact_id == ARTIFACT_ID
            )
        )
        session.execute(
            delete(AtlasArtifactRow).where(
                AtlasArtifactRow.artifact_id == ARTIFACT_ID
            )
        )
        session.execute(
            delete(AtlasStorageBlobRow).where(
                AtlasStorageBlobRow.blob_id == BLOB_ID
            )
        )
        session.execute(
            delete(AtlasArtifactWriteAttemptRow).where(
                AtlasArtifactWriteAttemptRow.write_attempt_id == ATTEMPT_ID
            )
        )
        session.execute(
            delete(AtlasDocumentRow).where(AtlasDocumentRow.document_id == PARENT_ID)
        )
        session.execute(
            delete(AtlasProjectRow).where(
                AtlasProjectRow.project_id == "project-c3-authority"
            )
        )
        session.execute(
            delete(AtlasStorageRequestLeaseRow).where(
                AtlasStorageRequestLeaseRow.lease_id.in_((LEASE_ID, READ_LEASE_ID))
            )
        )
        session.execute(
            delete(AtlasArtifactStorageTargetRow).where(
                AtlasArtifactStorageTargetRow.target_id == TARGET_ID
            )
        )
        session.add(
            AtlasArtifactStorageTargetRow(
                target_id=TARGET_ID,
                target_revision=1,
                target_kind="local",
                masked_label="c3 authority concurrency",
                config_key="c3-authority-concurrency",
                root_identity_digest=ROOT_DIGEST,
                capabilities={
                    "create_file": True,
                    "modify_file": True,
                    "remove_file": True,
                },
                status="active",
                created_at=NOW,
                updated_at=NOW,
                created_by="test-c3-authority",
                verification_mode="full_hash",
                evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
                failure_code=None,
                registration_idempotency_key=None,
                registration_request_fingerprint=None,
            )
        )
        session.flush()
        session.add(
            AtlasArtifactStorageControlRow(
                control_id="global",
                mode="active",
                active_target_id=TARGET_ID,
                active_target_revision=1,
                root_identity_digest=ROOT_DIGEST,
                storage_epoch=2,
                updated_at=NOW,
            )
        )
        session.add(AtlasArtifactWriteAttemptRow(**_payload(_expected_attempt())))
        session.add(AtlasStorageRequestLeaseRow(**_payload(_write_lease())))
        session.add(
            AtlasProjectRow(
                project_id="project-c3-authority",
                name="C3 authority project",
                policy_profile_id="default",
                status="active",
            )
        )
        session.add(AtlasDocumentRow(**asdict(_document())))
        session.commit()
    return previous_control


def test_artifact_data_planes_share_control_lock_while_control_change_waits(
    postgres_runtime: PostgresRuntime,
) -> None:
    """Opt-in proof of the intended PostgreSQL lock compatibility matrix."""

    with (
        postgres_runtime.session_factory() as first_data_plane,
        postgres_runtime.session_factory() as second_data_plane,
    ):
        first_pid = int(
            first_data_plane.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        second_pid = int(
            second_data_plane.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        acquire_shared_owner_locks(
            first_data_plane,
            identity_keys=("artifact:control",),
        )
        # This must return before the first transaction commits: sibling data
        # planes use compatible shared transaction locks.
        acquire_shared_owner_locks(
            second_data_plane,
            identity_keys=("artifact:control",),
        )

        writer_pid: Queue[int] = Queue(maxsize=1)

        def take_control_lock() -> None:
            with postgres_runtime.session_factory() as control_session:
                writer_pid.put(
                    int(
                        control_session.execute(
                            text("SELECT pg_backend_pid()")
                        ).scalar_one()
                    )
                )
                acquire_owner_locks(
                    control_session,
                    domain_keys=("artifact:control",),
                )
                control_session.commit()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(take_control_lock)
            try:
                control_pid = writer_pid.get(timeout=5.0)
                deadline = monotonic() + 5.0
                while monotonic() < deadline:
                    blockers = set(
                        first_data_plane.execute(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": control_pid},
                        ).scalar_one()
                    )
                    if {first_pid, second_pid}.issubset(blockers):
                        break
                    if future.done():
                        raise AssertionError(
                            "artifact control lock bypassed active data-plane readers"
                        )
                    sleep(0.01)
                else:
                    raise AssertionError(
                        "artifact control lock did not wait for both data planes"
                    )

                first_data_plane.commit()
                assert not future.done()
                second_data_plane.commit()
                future.result(timeout=5.0)
            finally:
                # Always release both transaction-scoped shared locks before the
                # executor waits for the exclusive-lock worker.  Otherwise an
                # assertion above can strand the worker in PostgreSQL forever.
                first_data_plane.rollback()
                second_data_plane.rollback()
                future.result(timeout=5.0)


def test_target_control_rejects_durable_data_plane_intervals(
    postgres_runtime: PostgresRuntime,
) -> None:
    """The durable attempt/lease keeps a switch closed between SQL transactions."""

    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected_control = StorageControlRecord(
            mode="active",
            active_target_id=TARGET_ID,
            active_target_revision=1,
            root_identity_digest=ROOT_DIGEST,
            storage_epoch=2,
            updated_at=NOW,
        )
        next_fence = StorageFence("target-c3-next", 2, "e" * 64, 3)
        target = StorageTargetRecord(
            target_id=next_fence.target_id,
            target_revision=next_fence.target_revision,
            target_kind="local",
            masked_label="next",
            config_key="c3-next",
            root_identity_digest=next_fence.root_identity_digest,
            capabilities={
                "create_file": True,
                "modify_file": True,
                "remove_file": True,
            },
            status="active",
            created_at=NOW,
            updated_at=NOW,
            created_by="test-c3-authority",
            verification_mode="full_hash",
            evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
        )
        operation = ArtifactOperationRecord(
            operation_id="operation-c3-durable-interval",
            operation_type="target_configuration",
            idempotency_scope="offline_storage_change",
            idempotency_key="c3-durable-interval",
            request_fingerprint=REQUEST_DIGEST,
            status="succeeded",
            fence=next_fence,
            created_at=NOW,
            updated_at=NOW,
            verification_mode="full_hash",
            evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
            committed_blob_count=0,
            total_bytes=0,
            blob_set_digest=hashlib.sha256(b"[]").hexdigest(),
        )
        request = TargetControlInput(
            expected_control=expected_control,
            expected_committed_blobs=(),
            target=target,
            control=StorageControlRecord(
                mode="active",
                active_target_id=next_fence.target_id,
                active_target_revision=next_fence.target_revision,
                root_identity_digest=next_fence.root_identity_digest,
                storage_epoch=next_fence.storage_epoch,
                updated_at=NOW,
            ),
            operation=operation,
            audit_events=(_audit("audit-c3-durable-interval"),),
            observed_at="2026-07-16T00:00:00+00:00",
        )
        with pytest.raises(
            ArtifactCommandConflict,
            match="writes require reconciliation",
        ):
            TargetControlCommand(postgres_runtime.session_factory).execute(request)

        with postgres_runtime.session_factory() as session:
            session.execute(
                delete(AtlasArtifactWriteAttemptRow).where(
                    AtlasArtifactWriteAttemptRow.write_attempt_id == ATTEMPT_ID
                )
            )
            session.execute(
                delete(AtlasStorageRequestLeaseRow).where(
                    AtlasStorageRequestLeaseRow.lease_id == LEASE_ID
                )
            )
            session.add(
                AtlasStorageRequestLeaseRow(
                    **_payload(
                        replace(
                            _write_lease(),
                            lease_id=READ_LEASE_ID,
                            request_kind="artifact_read",
                            expires_at="2026-07-19T00:00:00+00:00",
                        )
                    )
                )
            )
            session.commit()
        with pytest.raises(
            ArtifactCommandConflict,
            match="active leases",
        ):
            TargetControlCommand(postgres_runtime.session_factory).execute(request)
    finally:
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )
def test_competing_authorization_ids_reread_durable_authority_after_wait(
    postgres_runtime: PostgresRuntime,
) -> None:
    """A waiter must reject the authority row committed by the lock holder."""

    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected = _expected_attempt()
        final_a = _publication_records(AUTHORIZATION_A_ID)
        final_b = _publication_records(AUTHORIZATION_B_ID)
        with postgres_runtime.session_factory() as first_session:
            first_pid = int(
                first_session.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
            )
            acquire_mixed_owner_locks(
                first_session,
                shared_domain_keys=("artifact:control",),
                exclusive_identity_keys=(
                    f"artifact:attempt:{ATTEMPT_ID}",
                    f"artifact:lease:{LEASE_ID}",
                ),
            )
            current_attempt = first_session.get(
                AtlasArtifactWriteAttemptRow,
                ATTEMPT_ID,
                with_for_update=True,
            )
            assert current_attempt is not None
            for name, value in _payload(final_a[0]).items():
                setattr(current_attempt, name, value)
            first_session.add(AtlasStorageBlobRow(**_payload(final_a[1])))
            first_session.flush()
            first_session.add(AtlasArtifactRow(**_payload(final_a[2])))
            first_session.add(AtlasArtifactScopeBindingRow(**_payload(final_a[3])))
            first_session.add(AtlasArtifactScopeBindingRow(**_payload(final_a[4])))
            lease = first_session.get(
                AtlasStorageRequestLeaseRow,
                LEASE_ID,
                with_for_update=True,
            )
            assert lease is not None
            first_session.delete(lease)
            first_session.flush()

            second_pid: Queue[int] = Queue(maxsize=1)

            def publish_competing_authority() -> None:
                def command_session_factory() -> Session:
                    command_session = postgres_runtime.session_factory()
                    second_pid.put(
                        int(
                            command_session.execute(
                                text("SELECT pg_backend_pid()")
                            ).scalar_one()
                        )
                    )
                    return command_session

                FinalizeArtifactWriteCommand(
                    command_session_factory
                ).execute(
                    FinalizeArtifactWriteInput(
                        expected_attempt=expected,
                        expected_lease=_write_lease(),
                        expected_parent=DocumentParentCurrentness(
                            document_id=PARENT_ID,
                            lifecycle_status="active",
                            resource_lifecycle_epoch=0,
                            active_processing_generation=1,
                        ),
                        attempt=final_b[0],
                        blob=final_b[1],
                        artifact=final_b[2],
                        bindings=(final_b[3], final_b[4]),
                        audit_events=(_audit("audit-c3-authority-b"),),
                    )
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(publish_competing_authority)
                try:
                    waiter_pid = second_pid.get(timeout=5.0)
                    with postgres_runtime.session_factory() as monitor:
                        deadline = monotonic() + 5.0
                        while monotonic() < deadline:
                            blockers = set(
                                monitor.execute(
                                    text("SELECT pg_blocking_pids(:pid)"),
                                    {"pid": waiter_pid},
                                ).scalar_one()
                            )
                            if first_pid in blockers:
                                break
                            if future.done():
                                raise AssertionError(
                                    "competing artifact authority bypassed "
                                    "the artifact identity lock"
                                )
                            sleep(0.01)
                        else:
                            raise AssertionError(
                                "competing artifact authority did not wait "
                                "for the artifact identity lock"
                            )

                    first_session.commit()
                    with pytest.raises(
                        ArtifactCommandConflict,
                        match="replay graph changed",
                    ):
                        future.result(timeout=5.0)
                finally:
                    # Release the transaction-scoped graph lock before the
                    # executor waits if an assertion fails before commit.
                    first_session.rollback()
                    if not future.done():
                        try:
                            future.result(timeout=5.0)
                        except ArtifactCommandConflict:
                            pass

        with postgres_runtime.session_factory() as verification:
            assert verification.get(AtlasArtifactWriteAttemptRow, ATTEMPT_ID)
            assert verification.get(AtlasStorageBlobRow, BLOB_ID)
            assert verification.get(AtlasArtifactRow, ARTIFACT_ID)
            bindings = tuple(
                verification.scalars(
                    select(AtlasArtifactScopeBindingRow)
                    .where(
                        AtlasArtifactScopeBindingRow.artifact_id == ARTIFACT_ID
                    )
                    .order_by(AtlasArtifactScopeBindingRow.binding_id)
                ).all()
            )
            assert tuple(binding.binding_id for binding in bindings) == (
                AUTHORIZATION_A_ID,
                OWNER_BINDING_ID,
            )
            assert tuple(
                (binding.binding_kind, binding.scope_type, binding.scope_id)
                for binding in bindings
            ) == (
                ("authorization", "project", "project-c3-authority"),
                ("owner", "project", "project-c3-authority"),
            )
            assert verification.get(
                AtlasArtifactScopeBindingRow,
                AUTHORIZATION_B_ID,
            ) is None
    finally:
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )


def test_write_heartbeat_waits_for_target_control_lock_then_rereads_fence(
    postgres_runtime: PostgresRuntime,
) -> None:
    """A heartbeat cannot bypass the target-switch control authority."""

    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected = replace(
            _expected_attempt(),
            lease_expires_at="2026-07-17T00:01:00+00:00",
            last_heartbeat_at="2026-07-17T00:00:00+00:00",
        )
        expected_lease = replace(
            _write_lease(),
            expires_at=expected.lease_expires_at,
            last_heartbeat_at=expected.last_heartbeat_at,
        )
        updated = replace(
            expected,
            lease_expires_at="2026-07-17T00:02:00+00:00",
            last_heartbeat_at="2026-07-17T00:00:30+00:00",
            updated_at="2026-07-17T00:00:30+00:00",
        )
        updated_lease = replace(
            expected_lease,
            expires_at=updated.lease_expires_at,
            last_heartbeat_at=updated.last_heartbeat_at,
        )
        with postgres_runtime.session_factory() as setup:
            attempt_row = setup.get(AtlasArtifactWriteAttemptRow, ATTEMPT_ID)
            lease_row = setup.get(AtlasStorageRequestLeaseRow, LEASE_ID)
            assert attempt_row is not None and lease_row is not None
            for name, value in _payload(expected).items():
                setattr(attempt_row, name, value)
            for name, value in _payload(expected_lease).items():
                setattr(lease_row, name, value)
            setup.commit()

        with postgres_runtime.session_factory() as control_session:
            control_pid = int(
                control_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            acquire_owner_locks(
                control_session,
                domain_keys=("artifact:control",),
            )
            waiter_pid: Queue[int] = Queue(maxsize=1)

            def heartbeat() -> None:
                def session_factory() -> Session:
                    session = postgres_runtime.session_factory()
                    waiter_pid.put(
                        int(
                            session.execute(
                                text("SELECT pg_backend_pid()")
                            ).scalar_one()
                        )
                    )
                    return session

                HeartbeatArtifactWriteCommand(session_factory).execute(
                    HeartbeatArtifactWriteInput(
                        expected_attempt=expected,
                        expected_lease=expected_lease,
                        attempt=updated,
                        lease=updated_lease,
                        observed_at="2026-07-17T00:00:30+00:00",
                    )
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(heartbeat)
                try:
                    heartbeat_pid = waiter_pid.get(timeout=5.0)
                    with postgres_runtime.session_factory() as monitor:
                        deadline = monotonic() + 5.0
                        while monotonic() < deadline:
                            blockers = set(
                                monitor.execute(
                                    text("SELECT pg_blocking_pids(:pid)"),
                                    {"pid": heartbeat_pid},
                                ).scalar_one()
                            )
                            if control_pid in blockers:
                                break
                            if future.done():
                                raise AssertionError(
                                    "write heartbeat bypassed target control lock"
                                )
                            sleep(0.01)
                        else:
                            raise AssertionError(
                                "write heartbeat did not wait for target control lock"
                            )
                    control_session.rollback()
                    future.result(timeout=5.0)
                finally:
                    control_session.rollback()
                    if not future.done():
                        future.result(timeout=5.0)

        with postgres_runtime.session_factory() as verification:
            attempt_row = verification.get(AtlasArtifactWriteAttemptRow, ATTEMPT_ID)
            lease_row = verification.get(AtlasStorageRequestLeaseRow, LEASE_ID)
            assert attempt_row is not None and lease_row is not None
            assert attempt_row.last_heartbeat_at == updated.last_heartbeat_at
            assert attempt_row.lease_expires_at == updated.lease_expires_at
            assert lease_row.last_heartbeat_at == updated_lease.last_heartbeat_at
            assert lease_row.expires_at == updated_lease.expires_at
    finally:
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )


def test_new_document_original_writer_rolls_back_with_caller_transaction(
    postgres_runtime: PostgresRuntime,
) -> None:
    """Caller rollback leaves the begun attempt/lease and no terminal graph."""

    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected = _expected_attempt()
        final, blob, artifact, owner_binding, authorization = _publication_records(
            AUTHORIZATION_A_ID
        )
        blob = replace(
            blob,
            dedup_mode="original",
            dedup_scope_type="project",
            dedup_scope_id="project-c3-authority",
        )
        request = NewDocumentOriginalArtifactPublication(
            fence=FENCE,
            expected_attempt=expected,
            expected_lease=_write_lease(),
            attempt=final,
            blob=blob,
            artifact=artifact,
            bindings=(owner_binding, authorization),
            verified_tag_scopes=frozenset(
                {("project", "project-c3-authority")}
            ),
        )
        with postgres_runtime.session_factory() as caller_session:
            parent = caller_session.get(AtlasDocumentRow, PARENT_ID)
            assert parent is not None
            caller_session.delete(parent)
            caller_session.flush()
            result = NewDocumentOriginalArtifactPublicationWriter(
                caller_session
            ).publish_new_document_original(request)
            assert result.replayed is False
            caller_session.rollback()

        with postgres_runtime.session_factory() as verification:
            assert verification.get(AtlasArtifactWriteAttemptRow, ATTEMPT_ID)
            assert verification.get(AtlasStorageRequestLeaseRow, LEASE_ID)
            assert verification.get(AtlasStorageBlobRow, BLOB_ID) is None
            assert verification.get(AtlasArtifactRow, ARTIFACT_ID) is None
            assert verification.get(
                AtlasArtifactScopeBindingRow,
                OWNER_BINDING_ID,
            ) is None
            assert verification.get(
                AtlasArtifactScopeBindingRow,
                AUTHORIZATION_A_ID,
            ) is None
    finally:
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )


@pytest.mark.parametrize("unique_identity", ("logical", "canonical"))
def test_new_document_publisher_and_stable_finalize_share_unique_owner(
    postgres_runtime: PostgresRuntime,
    unique_identity: str,
) -> None:
    """Stable finalize waits for and rejects each publisher-owned identity."""

    second_attempt_id = f"{ATTEMPT_ID}-second"
    second_lease_id = f"{LEASE_ID}-second"
    second_blob_id = f"{BLOB_ID}-second"
    second_artifact_id = f"{ARTIFACT_ID}-second"
    second_owner_id = f"{OWNER_BINDING_ID}-second"
    second_authorization_id = f"{AUTHORIZATION_B_ID}-second"
    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected = _expected_attempt()
        final, blob, artifact, owner_binding, authorization = _publication_records(
            AUTHORIZATION_A_ID
        )
        blob = replace(
            blob,
            dedup_mode="original",
            dedup_scope_type="project",
            dedup_scope_id="project-c3-authority",
        )
        first = NewDocumentOriginalArtifactPublication(
            fence=FENCE,
            expected_attempt=expected,
            expected_lease=_write_lease(),
            attempt=final,
            blob=blob,
            artifact=artifact,
            bindings=(owner_binding, authorization),
            verified_tag_scopes=frozenset(
                {("project", "project-c3-authority")}
            ),
        )
        second_logical = (
            artifact.logical_identity
            if unique_identity == "logical"
            else f"document:{PARENT_ID}:{VERSION_ID}:second-original"
        )
        second_intent = {
            **expected.intent,
            "logical_identity": second_logical,
        }
        second_expected = replace(
            expected,
            write_attempt_id=second_attempt_id,
            idempotency_key="authority-concurrency-second",
            request_fingerprint="d" * 64,
            lease_owner="worker-c3-authority-second",
            opaque_temp_name="opaque-c3-authority-second",
            intent=second_intent,
        )
        second_lease = replace(
            _write_lease(),
            lease_id=second_lease_id,
            owner=second_expected.lease_owner,
        )
        second_blob = replace(
            blob,
            blob_id=second_blob_id,
            opaque_ref="opaque/c3-authority-concurrency-second",
            checksum_value="e" * 64,
            byte_size=4,
            write_attempt_id=second_attempt_id,
        )
        second_final = replace(
            second_expected,
            status="succeeded",
            blob_id=second_blob_id,
            byte_size=second_blob.byte_size,
            checksum_sha256=second_blob.checksum_value,
        )
        second_artifact = replace(
            artifact,
            artifact_id=second_artifact_id,
            blob_id=second_blob_id,
            checksum_value=second_blob.checksum_value,
            byte_size=second_blob.byte_size,
            logical_identity=second_logical,
        )
        second_owner = replace(
            owner_binding,
            binding_id=second_owner_id,
            artifact_id=second_artifact_id,
        )
        second_authorization = replace(
            authorization,
            binding_id=second_authorization_id,
            artifact_id=second_artifact_id,
        )
        second = NewDocumentOriginalArtifactPublication(
            fence=FENCE,
            expected_attempt=second_expected,
            expected_lease=second_lease,
            attempt=second_final,
            blob=second_blob,
            artifact=second_artifact,
            bindings=(second_owner, second_authorization),
            verified_tag_scopes=first.verified_tag_scopes,
        )
        with postgres_runtime.session_factory() as setup:
            setup.add(AtlasArtifactWriteAttemptRow(**_payload(second_expected)))
            setup.add(AtlasStorageRequestLeaseRow(**_payload(second_lease)))
            setup.commit()

        with postgres_runtime.session_factory() as first_session:
            first_pid = int(
                first_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            NewDocumentOriginalArtifactPublicationWriter(
                first_session
            ).publish_new_document_original(first)
            first_session.flush()
            waiter_pid: Queue[int] = Queue(maxsize=1)

            def finalize_second() -> None:
                def command_session_factory() -> Session:
                    second_session = postgres_runtime.session_factory()
                    waiter_pid.put(
                        int(
                            second_session.execute(
                                text("SELECT pg_backend_pid()")
                            ).scalar_one()
                        )
                    )
                    return second_session

                FinalizeArtifactWriteCommand(command_session_factory).execute(
                    FinalizeArtifactWriteInput(
                        expected_attempt=second.expected_attempt,
                        expected_lease=second.expected_lease,
                        expected_parent=DocumentParentCurrentness(
                            document_id=PARENT_ID,
                            lifecycle_status="active",
                            resource_lifecycle_epoch=0,
                            active_processing_generation=1,
                        ),
                        attempt=second.attempt,
                        blob=second.blob,
                        artifact=second.artifact,
                        bindings=second.bindings,
                        audit_events=(_audit("audit-c3-cross-producer"),),
                    )
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(finalize_second)
                try:
                    second_pid = waiter_pid.get(timeout=5.0)
                    with postgres_runtime.session_factory() as monitor:
                        deadline = monotonic() + 5.0
                        while monotonic() < deadline:
                            blockers = set(
                                monitor.execute(
                                    text("SELECT pg_blocking_pids(:pid)"),
                                    {"pid": second_pid},
                                ).scalar_one()
                            )
                            if first_pid in blockers:
                                break
                            if future.done():
                                raise AssertionError(
                                    f"competing {unique_identity} identity bypassed owner lock"
                                )
                            sleep(0.01)
                        else:
                            raise AssertionError(
                                f"competing {unique_identity} identity did not wait"
                            )
                    first_session.commit()
                    with pytest.raises(
                        ArtifactCommandConflict,
                        match=(
                            "artifact logical identity"
                            if unique_identity == "logical"
                            else "canonical original identity"
                        ),
                    ):
                        future.result(timeout=5.0)
                finally:
                    first_session.rollback()
                    if not future.done():
                        try:
                            future.result(timeout=5.0)
                        except ArtifactCommandConflict:
                            pass
    finally:
        with postgres_runtime.session_factory() as cleanup:
            cleanup.execute(
                delete(AtlasArtifactScopeBindingRow).where(
                    AtlasArtifactScopeBindingRow.binding_id.in_(
                        (second_owner_id, second_authorization_id)
                    )
                )
            )
            cleanup.execute(
                delete(AtlasArtifactRow).where(
                    AtlasArtifactRow.artifact_id == second_artifact_id
                )
            )
            cleanup.execute(
                delete(AtlasStorageBlobRow).where(
                    AtlasStorageBlobRow.blob_id == second_blob_id
                )
            )
            cleanup.execute(
                delete(AtlasStorageRequestLeaseRow).where(
                    AtlasStorageRequestLeaseRow.lease_id == second_lease_id
                )
            )
            cleanup.execute(
                delete(AtlasArtifactWriteAttemptRow).where(
                    AtlasArtifactWriteAttemptRow.write_attempt_id
                    == second_attempt_id
                )
            )
            cleanup.commit()
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )


def test_same_scope_committed_blob_reuse_is_unchanged_after_caller_rollback(
    postgres_runtime: PostgresRuntime,
) -> None:
    """Reuse stages terminal metadata but never mutates the committed blob."""

    previous_control = _seed_authority_fixture(postgres_runtime)
    try:
        expected = _expected_attempt()
        final, blob, artifact, owner_binding, authorization = _publication_records(
            AUTHORIZATION_A_ID
        )
        existing_blob = replace(
            blob,
            dedup_mode="original",
            dedup_scope_type="project",
            dedup_scope_id="project-c3-authority",
            write_attempt_id=None,
        )
        request = NewDocumentOriginalArtifactPublication(
            fence=FENCE,
            expected_attempt=expected,
            expected_lease=_write_lease(),
            attempt=final,
            blob=existing_blob,
            artifact=artifact,
            bindings=(owner_binding, authorization),
            verified_tag_scopes=frozenset(
                {("project", "project-c3-authority")}
            ),
            reuse_committed_blob=True,
        )
        with postgres_runtime.session_factory() as setup:
            setup.add(AtlasStorageBlobRow(**_payload(existing_blob)))
            setup.commit()
        with postgres_runtime.session_factory() as caller_session:
            blob_row = caller_session.get(AtlasStorageBlobRow, BLOB_ID)
            assert blob_row is not None
            before = {
                column.name: getattr(blob_row, column.name)
                for column in AtlasStorageBlobRow.__table__.columns
            }
            result = NewDocumentOriginalArtifactPublicationWriter(
                caller_session
            ).publish_new_document_original(request)
            assert result.replayed is False
            caller_session.flush()
            assert not caller_session.is_modified(blob_row)
            assert {
                column.name: getattr(blob_row, column.name)
                for column in AtlasStorageBlobRow.__table__.columns
            } == before
            caller_session.rollback()

        with postgres_runtime.session_factory() as verification:
            blob_row = verification.get(AtlasStorageBlobRow, BLOB_ID)
            assert blob_row is not None
            assert all(
                getattr(blob_row, name) == value
                for name, value in _payload(existing_blob).items()
            )
            assert verification.get(AtlasArtifactWriteAttemptRow, ATTEMPT_ID)
            assert verification.get(AtlasStorageRequestLeaseRow, LEASE_ID)
            assert verification.get(AtlasArtifactRow, ARTIFACT_ID) is None
            assert verification.get(
                AtlasArtifactScopeBindingRow,
                OWNER_BINDING_ID,
            ) is None
    finally:
        _delete_authority_fixture(
            postgres_runtime,
            restore_control=previous_control,
        )
