from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from time import sleep
from typing import Callable, Iterable
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from atlas_production.infrastructure.artifact_storage_config_adapter import (
    RootOnlyStorageTargetConfig,
)
from atlas_production.infrastructure.artifact_storage_filesystem_adapter import (
    LocalArtifactFilesystemAdapter,
    opaque_blob_ref,
)
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasArtifactScopeBindingRow,
    AtlasArtifactStorageControlRow,
    AtlasArtifactStorageTargetRow,
    AtlasArtifactWriteAttemptRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentRow
from atlas_production.infrastructure.persistence.async_processing import (
    AtlasProcessingBatchClaimRow,
    AtlasProcessingCheckpointRow,
    AtlasProcessingGenerationRow,
    AtlasProcessingJobRow,
)
from atlas_production.modules.artifact_storage.records import ArtifactRecord
from atlas_production.modules.artifact_storage.public import MAX_ARTIFACT_BYTES
from atlas_production.shared.public import utc_now_iso


@dataclass(frozen=True)
class BoundedArtifactWriteResult:
    artifact: ArtifactRecord
    replayed: bool


@dataclass(frozen=True)
class ProcessingArtifactFence:
    """Current asynchronous batch authority for derived artifact I/O.

    AsyncProcessingJob is the v1 processing-operation authority.  Its attempt,
    generation and batch checkpoint form the durable visibility fence; a
    separate artifact-operation lifecycle would duplicate that state machine.
    """

    job_id: str
    attempt: int
    claim_fence: int
    claim_token: str
    document_id: str
    document_version_id: str
    processing_generation: int
    batch_id: str
    unit_start: int
    unit_end: int
    parent_lifecycle_epoch: int


@dataclass(frozen=True)
class DocumentPreparationArtifactFence:
    """Document-level authority for publishing one prepared page source."""

    job_id: str
    attempt: int
    claim_fence: int
    claim_token: str
    document_id: str
    document_version_id: str
    processing_generation: int
    batch_id: str
    page_number: int
    parent_lifecycle_epoch: int


ProcessingWriteFence = ProcessingArtifactFence | DocumentPreparationArtifactFence


def _artifact_record(row: AtlasArtifactRow) -> ArtifactRecord:
    values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    values["metadata"] = values.pop("metadata_json")
    return ArtifactRecord(**values)


class BoundedArtifactWriter:
    """Publish one derived byte object without an aggregate snapshot/global lock."""

    def __init__(
        self,
        engine,
        *,
        target_config_directory: str | None = None,
        allowlisted_parents: tuple[str, ...] | None = None,
    ) -> None:
        self.engine = engine
        self.target_config_directory = target_config_directory
        self.allowlisted_parents = allowlisted_parents

    def _target_config(self) -> RootOnlyStorageTargetConfig:
        directory = self.target_config_directory or os.environ["ATLAS_ARTIFACT_TARGET_CONFIG"]
        return RootOnlyStorageTargetConfig(directory)

    def _allowed_parents(self) -> tuple[str, ...]:
        if self.allowlisted_parents is not None:
            return self.allowlisted_parents
        return tuple(
            value for value in os.getenv("ATLAS_ARTIFACT_ALLOWED_PARENTS", "").split(":")
            if value
        )

    @staticmethod
    def _require_processing_fence(
        session: Session,
        fence: ProcessingWriteFence,
    ) -> None:
        if fence.attempt <= 0 or fence.claim_fence < 0 or fence.processing_generation <= 0:
            raise ValueError("processing_artifact_fence_rejected")
        if isinstance(fence, ProcessingArtifactFence):
            if (
                fence.unit_start <= 0
                or fence.unit_end != fence.unit_start
                or fence.batch_id != f"{fence.job_id}:page:{fence.unit_start}"
            ):
                raise ValueError("processing_artifact_fence_rejected")
        elif (
            fence.page_number <= 0
            or fence.batch_id != f"{fence.job_id}:prepare"
        ):
            raise ValueError("processing_artifact_fence_rejected")
        row = session.execute(
            select(
                AtlasProcessingJobRow,
                AtlasDocumentRow,
                AtlasProcessingGenerationRow,
            )
            .join(
                AtlasDocumentRow,
                AtlasDocumentRow.document_id == AtlasProcessingJobRow.document_id,
            )
            .join(
                AtlasProcessingGenerationRow,
                (AtlasProcessingGenerationRow.document_id == AtlasProcessingJobRow.document_id)
                & (
                    AtlasProcessingGenerationRow.processing_generation
                    == AtlasProcessingJobRow.processing_generation
                ),
            )
            .where(AtlasProcessingJobRow.job_id == fence.job_id)
        ).one_or_none()
        if row is None:
            raise ValueError("processing_artifact_fence_rejected")
        job, document, generation = row
        claim = session.execute(
            select(AtlasProcessingBatchClaimRow).where(
                AtlasProcessingBatchClaimRow.batch_id == fence.batch_id,
            )
        ).scalar_one_or_none()
        if (
            job.status != "running"
            or int(job.attempt) != fence.attempt
            or int(job.fence) != fence.claim_fence
            or job.document_id != fence.document_id
            or job.document_version_id != fence.document_version_id
            or int(job.processing_generation or 0) != fence.processing_generation
            or document.document_id != fence.document_id
            or document.lifecycle_status not in {"active", "restoring"}
            or int(document.resource_lifecycle_epoch) != fence.parent_lifecycle_epoch
            or generation.document_version_id != fence.document_version_id
            or generation.status != "building"
            or claim is None
            or claim.job_id != fence.job_id
            or int(claim.attempt) != fence.attempt
            or claim.claim_token != fence.claim_token
            or claim.lease_expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError("processing_artifact_fence_rejected")
        if isinstance(fence, ProcessingArtifactFence):
            if (
                claim.unit_kind != "page"
                or int(claim.unit_start) != fence.unit_start
                or int(claim.unit_end) != fence.unit_end
            ):
                raise ValueError("processing_artifact_fence_rejected")
        else:
            if (
                claim.unit_kind != "batch"
                or int(claim.unit_start) != 1
                or int(claim.unit_end) != 1
            ):
                raise ValueError("processing_artifact_fence_rejected")
            return
        checkpoint = session.execute(
            select(AtlasProcessingCheckpointRow.batch_id).where(
                AtlasProcessingCheckpointRow.job_id == fence.job_id,
                AtlasProcessingCheckpointRow.batch_id == fence.batch_id,
            )
        ).scalar_one_or_none()
        if checkpoint is not None:
            raise ValueError("processing_artifact_fence_rejected")

    def read_processing(
        self,
        artifact_id: str,
        *,
        expected_content_type: str,
        expected_artifact_class: str,
        expected_logical_identity: str,
        fence: ProcessingArtifactFence,
    ) -> bytes:
        """Read one generation-scoped result with job and storage revalidation."""

        def current_row(session: Session):
            self._require_processing_fence(session, fence)
            row = session.execute(
                select(
                    AtlasArtifactRow,
                    AtlasStorageBlobRow,
                    AtlasArtifactStorageControlRow,
                    AtlasArtifactStorageTargetRow,
                )
                .join(
                    AtlasStorageBlobRow,
                    AtlasStorageBlobRow.blob_id == AtlasArtifactRow.blob_id,
                )
                .join(
                    AtlasArtifactStorageControlRow,
                    AtlasArtifactStorageControlRow.control_id == "global",
                )
                .join(
                    AtlasArtifactStorageTargetRow,
                    (AtlasArtifactStorageTargetRow.target_id == AtlasStorageBlobRow.target_id)
                    & (
                        AtlasArtifactStorageTargetRow.target_revision
                        == AtlasStorageBlobRow.target_revision
                    ),
                )
                .where(AtlasArtifactRow.artifact_id == artifact_id)
            ).one_or_none()
            if row is None:
                raise ValueError("processing_artifact_unavailable")
            artifact, blob, control, target = row
            if (
                artifact.lifecycle_status != "active"
                or blob.status != "committed"
                or artifact.artifact_class != expected_artifact_class
                or artifact.logical_identity != expected_logical_identity
                or artifact.content_type != expected_content_type
                or blob.content_type != expected_content_type
                or artifact.parent_resource_id != fence.document_id
                or artifact.document_version_id != fence.document_version_id
                or int(artifact.processing_generation or 0)
                != fence.processing_generation
                or artifact.parent_lifecycle_epoch is None
                or int(artifact.parent_lifecycle_epoch)
                != fence.parent_lifecycle_epoch
                or control.mode != "active"
                or blob.target_id != control.active_target_id
                or blob.target_revision != control.active_target_revision
                or blob.root_identity_digest != control.root_identity_digest
                or blob.storage_epoch != control.storage_epoch
                or target.status != "active"
                or target.root_identity_digest != blob.root_identity_digest
            ):
                raise ValueError("processing_artifact_fence_rejected")
            return artifact, blob, target

        with Session(self.engine) as session:
            artifact, blob, target = current_row(session)
        configured = self._target_config().load().get(blob.target_id)
        if (
            configured is None
            or configured["revision"] != target.target_revision
            or configured["config_key"] != target.config_key
        ):
            raise RuntimeError("artifact_storage_target_unavailable")
        adapter = LocalArtifactFilesystemAdapter(
            configured["raw_path"],
            allowlisted_parents=self._allowed_parents(),
            create_layout=False,
        )
        adapter.verify_full(
            blob.opaque_ref,
            expected_size=blob.byte_size,
            expected_sha256=blob.checksum_value,
        )
        with adapter.open_read(blob.opaque_ref, expected_size=blob.byte_size) as stream:
            content = stream.read()
        if (
            len(content) != artifact.byte_size
            or hashlib.sha256(content).hexdigest() != artifact.checksum_value
        ):
            raise ValueError("processing_artifact_integrity_failed")
        with Session(self.engine) as session:
            final_artifact, final_blob, _target = current_row(session)
        if (
            final_artifact.checksum_value != artifact.checksum_value
            or final_blob.checksum_value != blob.checksum_value
        ):
            raise ValueError("processing_artifact_fence_rejected")
        return content

    def read(
        self,
        artifact_id: str,
        *,
        expected_content_type: str,
        allow_member_download_bypass: bool = False,
    ) -> bytes:
        # Keep the document row share-locked until verification/read completes,
        # so disable/epoch or member-download policy changes cannot race bytes.
        with Session(self.engine) as session, session.begin():
            row = session.execute(select(
                    AtlasArtifactRow,
                    AtlasStorageBlobRow,
                    AtlasArtifactStorageControlRow,
                    AtlasArtifactStorageTargetRow,
                    AtlasDocumentRow,
                ).join(
                    AtlasStorageBlobRow,
                    AtlasStorageBlobRow.blob_id == AtlasArtifactRow.blob_id,
                ).join(
                    AtlasArtifactStorageControlRow,
                    AtlasArtifactStorageControlRow.control_id == "global",
                ).join(
                    AtlasArtifactStorageTargetRow,
                    (AtlasArtifactStorageTargetRow.target_id == AtlasStorageBlobRow.target_id)
                    & (AtlasArtifactStorageTargetRow.target_revision == AtlasStorageBlobRow.target_revision),
                ).join(
                    AtlasDocumentRow,
                    AtlasDocumentRow.document_id == AtlasArtifactRow.parent_resource_id,
                ).where(
                    AtlasArtifactRow.artifact_id == artifact_id,
                    AtlasArtifactRow.lifecycle_status == "active",
                    AtlasStorageBlobRow.status == "committed",
                    AtlasDocumentRow.lifecycle_status == "active",
                    AtlasDocumentRow.resource_lifecycle_epoch
                    == AtlasArtifactRow.parent_lifecycle_epoch,
                ).with_for_update(of=AtlasDocumentRow, read=True)
            ).one_or_none()
            if row is None:
                raise ValueError("artifact_unavailable")
            artifact, blob, control, target, document = row
            if not allow_member_download_bypass and not document.allow_member_download:
                raise ValueError("member_download_policy")
            if (
                artifact.content_type != expected_content_type
                or blob.content_type != expected_content_type
                or blob.target_id != control.active_target_id
                or blob.target_revision != control.active_target_revision
                or blob.root_identity_digest != control.root_identity_digest
                or blob.storage_epoch != control.storage_epoch
            ):
                raise ValueError("artifact_read_contract_mismatch")
            configured = self._target_config().load().get(blob.target_id)
            if (
                configured is None
                or configured["revision"] != target.target_revision
                or configured["config_key"] != target.config_key
                or target.status != "active"
                or target.root_identity_digest != blob.root_identity_digest
            ):
                raise RuntimeError("artifact_storage_target_unavailable")
            adapter = LocalArtifactFilesystemAdapter(
                configured["raw_path"], allowlisted_parents=self._allowed_parents(), create_layout=False
            )
            adapter.verify_full(
                blob.opaque_ref,
                expected_size=blob.byte_size,
                expected_sha256=blob.checksum_value,
            )
            with adapter.open_read(blob.opaque_ref, expected_size=blob.byte_size) as stream:
                return stream.read()

    def purge(self, artifact_id: str, *, expected_class: str) -> bool:
        """Delete one ephemeral artifact with row-scoped fencing and no snapshot load."""
        with Session(self.engine) as session, session.begin():
            row = session.execute(select(
                AtlasArtifactRow,
                AtlasStorageBlobRow,
                AtlasArtifactStorageTargetRow,
            ).join(
                AtlasStorageBlobRow,
                AtlasStorageBlobRow.blob_id == AtlasArtifactRow.blob_id,
            ).join(
                AtlasArtifactStorageTargetRow,
                (AtlasArtifactStorageTargetRow.target_id == AtlasStorageBlobRow.target_id)
                & (AtlasArtifactStorageTargetRow.target_revision == AtlasStorageBlobRow.target_revision),
            ).where(
                AtlasArtifactRow.artifact_id == artifact_id,
            ).with_for_update(of=AtlasArtifactRow)).one_or_none()
            if row is None:
                return False
            artifact, blob, target = row
            if artifact.artifact_class != expected_class:
                raise ValueError("artifact_purge_class_mismatch")
            configured = self._target_config().load().get(blob.target_id)
            if (
                configured is None
                or configured["revision"] != target.target_revision
                or configured["config_key"] != target.config_key
                or target.root_identity_digest != blob.root_identity_digest
            ):
                raise RuntimeError("artifact_storage_target_unavailable")
            adapter = LocalArtifactFilesystemAdapter(
                configured["raw_path"], allowlisted_parents=self._allowed_parents(), create_layout=False
            )
            adapter.remove_committed(blob.opaque_ref)
            session.execute(delete(AtlasArtifactScopeBindingRow).where(
                AtlasArtifactScopeBindingRow.artifact_id == artifact_id
            ))
            session.execute(delete(AtlasArtifactRow).where(
                AtlasArtifactRow.artifact_id == artifact_id
            ))
            remaining = session.execute(select(func.count()).select_from(
                AtlasArtifactRow
            ).where(AtlasArtifactRow.blob_id == blob.blob_id)).scalar_one()
            if int(remaining) == 0:
                session.execute(delete(AtlasStorageBlobRow).where(
                    AtlasStorageBlobRow.blob_id == blob.blob_id
                ))
            return True

    def reconcile_incomplete(self, *, worker_id: str, limit: int = 100) -> int:
        """Bound cleanup of expired write attempts; each row is independently retryable."""
        now = utc_now_iso()
        reconciled = 0
        with (
            Session(self.engine) as session,
            session.begin(),
        ):
            attempts = session.execute(select(AtlasArtifactWriteAttemptRow).where(
                AtlasArtifactWriteAttemptRow.reconciliation_required_at.is_not(None),
                or_(
                    AtlasArtifactWriteAttemptRow.lease_expires_at <= now,
                    AtlasArtifactWriteAttemptRow.status.in_(("failed", "quarantined")),
                ),
                AtlasArtifactWriteAttemptRow.status.not_in(("succeeded", "quarantined")),
            ).order_by(
                AtlasArtifactWriteAttemptRow.lease_expires_at,
                AtlasArtifactWriteAttemptRow.write_attempt_id,
            ).limit(limit).with_for_update(skip_locked=True)).scalars().all()
            for attempt in attempts:
                # The writer holds this identity-scoped session lock across the
                # potentially slow durable filesystem write.  Never reconcile a
                # live writer solely because its wall-clock lease elapsed.
                if not session.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:identity, 0))"),
                    {"identity": attempt.write_attempt_id},
                ).scalar_one():
                    continue
                # Claim a new generation before touching bytes.  Any stale writer
                # that resumes after this transaction must fail its generation CAS.
                attempt.attempt_generation = int(attempt.attempt_generation) + 1
                blob = session.get(AtlasStorageBlobRow, attempt.blob_id) if attempt.blob_id else None
                artifact = (
                    session.execute(select(AtlasArtifactRow).where(
                        AtlasArtifactRow.blob_id == blob.blob_id
                    )).scalar_one_or_none()
                    if blob is not None else None
                )
                target = session.get(
                    AtlasArtifactStorageTargetRow,
                    (attempt.target_id, attempt.target_revision),
                )
                configured = self._target_config().load().get(attempt.target_id)
                if (
                    target is None
                    or configured is None
                    or configured["revision"] != target.target_revision
                    or configured["config_key"] != target.config_key
                    or target.root_identity_digest != attempt.root_identity_digest
                ):
                    continue
                adapter = LocalArtifactFilesystemAdapter(
                    configured["raw_path"], allowlisted_parents=self._allowed_parents(), create_layout=False
                )
                adapter.remove_temp(attempt.opaque_temp_name)
                if artifact is not None:
                    attempt.status = "succeeded"
                    attempt.failure_code = None
                else:
                    if blob is not None:
                        adapter.remove_committed(blob.opaque_ref)
                        session.delete(blob)
                    attempt.status = "failed"
                    attempt.failure_code = "write_interrupted"
                    attempt.failure_detail_summary = "Expired incomplete write was removed."
                attempt.lease_expires_at = now
                attempt.reconciliation_required_at = None
                attempt.reconciled_at = now
                attempt.reconciled_by = worker_id
                attempt.updated_at = now
                reconciled += 1
        return reconciled

    def write(
        self,
        *,
        content: bytes,
        artifact_class: str,
        logical_identity: str,
        content_type: str,
        owner_scope_type: str,
        owner_scope_id: str | None,
        parent_resource_id: str,
        parent_lifecycle_epoch: int,
        document_version_id: str | None,
        source_artifact_id: str | None,
        processing_generation: int | None,
        pipeline_id: str,
        pipeline_version: str,
        generation: int | None,
        page_number: int | None = None,
        authorization_bindings: tuple[tuple[str, str], ...] = (),
        allowed_parent_statuses: tuple[str, ...] = ("active", "restoring"),
        require_member_download: bool = False,
        allow_member_download_bypass: bool = False,
        allow_missing_parent: bool = False,
        require_missing_parent: bool = False,
        processing_fence: ProcessingWriteFence | None = None,
        finalize: Callable[[Connection, ArtifactRecord], None] | None = None,
    ) -> BoundedArtifactWriteResult:
        if isinstance(processing_fence, DocumentPreparationArtifactFence) and (
            page_number != processing_fence.page_number
            or artifact_class != "document_page_pdf"
        ):
            raise ValueError("processing_artifact_fence_rejected")
        if not content:
            raise ValueError("artifact_empty")
        digest = hashlib.sha256(content).hexdigest()
        with Session(self.engine) as session:
            if processing_fence is not None:
                self._require_processing_fence(session, processing_fence)
            existing = session.execute(select(AtlasArtifactRow).where(
                AtlasArtifactRow.artifact_class == artifact_class,
                AtlasArtifactRow.logical_identity == logical_identity,
            )).scalar_one_or_none()
        if existing is not None:
            if existing.checksum_value != digest or existing.byte_size != len(content):
                raise ValueError("artifact_identity_digest_conflict")
            return BoundedArtifactWriteResult(_artifact_record(existing), True)
        identity = hashlib.sha256(
            f"{artifact_class}\0{logical_identity}".encode("utf-8")
        ).hexdigest()
        attempt_id = f"attempt-{identity[:20]}"
        while True:
            try:
                return self.write_chunks(
                    chunks=(content,),
                    max_bytes=len(content),
                    request_fingerprint=digest,
                    artifact_class=artifact_class,
                    logical_identity=logical_identity,
                    content_type=content_type,
                    owner_scope_type=owner_scope_type,
                    owner_scope_id=owner_scope_id,
                    parent_resource_id=parent_resource_id,
                    parent_lifecycle_epoch=parent_lifecycle_epoch,
                    document_version_id=document_version_id,
                    source_artifact_id=source_artifact_id,
                    processing_generation=processing_generation,
                    pipeline_id=pipeline_id,
                    pipeline_version=pipeline_version,
                    generation=generation,
                    page_number=page_number,
                    authorization_bindings=authorization_bindings,
                    allowed_parent_statuses=allowed_parent_statuses,
                    require_member_download=require_member_download,
                    allow_member_download_bypass=allow_member_download_bypass,
                    allow_missing_parent=allow_missing_parent,
                    require_missing_parent=require_missing_parent,
                    processing_fence=processing_fence,
                    finalize=finalize,
                )
            except RuntimeError as exc:
                if str(exc) != "artifact_write_in_progress":
                    raise

            # Same-generation sibling batches may converge on one immutable
            # native image.  The current write-attempt lease and heartbeat are
            # the wait bound; there is no independent wall-clock threshold.
            # First wait behind the identity-scoped I/O guard, then observe the
            # terminal row while continuously revalidating this batch claim.
            with self.engine.connect() as waiter:
                waiter.execute(
                    text(
                        "SELECT pg_advisory_lock(hashtextextended(:identity, 0))"
                    ),
                    {"identity": attempt_id},
                )
                waiter.execute(
                    text(
                        "SELECT pg_advisory_unlock(hashtextextended(:identity, 0))"
                    ),
                    {"identity": attempt_id},
                )
            while True:
                with Session(self.engine) as session:
                    if processing_fence is not None:
                        self._require_processing_fence(session, processing_fence)
                    existing = session.execute(select(AtlasArtifactRow).where(
                        AtlasArtifactRow.artifact_class == artifact_class,
                        AtlasArtifactRow.logical_identity == logical_identity,
                    )).scalar_one_or_none()
                    attempt = session.get(AtlasArtifactWriteAttemptRow, attempt_id)
                if existing is not None:
                    if (
                        existing.checksum_value != digest
                        or existing.byte_size != len(content)
                        or existing.content_type != content_type
                    ):
                        raise ValueError("artifact_identity_digest_conflict")
                    return BoundedArtifactWriteResult(
                        _artifact_record(existing), True
                    )
                if attempt is None:
                    break
                if attempt.status in {"succeeded", "quarantined"}:
                    raise RuntimeError("artifact_write_reconciliation_required")
                lease_until = datetime.fromisoformat(attempt.lease_expires_at)
                if lease_until <= datetime.now(timezone.utc):
                    break
                sleep(0.05)

    def write_chunks(
        self,
        *,
        chunks: Iterable[bytes],
        request_fingerprint: str,
        artifact_class: str,
        logical_identity: str,
        content_type: str,
        owner_scope_type: str,
        owner_scope_id: str | None,
        parent_resource_id: str,
        parent_lifecycle_epoch: int,
        document_version_id: str | None,
        source_artifact_id: str | None,
        processing_generation: int | None,
        pipeline_id: str,
        pipeline_version: str,
        generation: int | None,
        page_number: int | None = None,
        authorization_bindings: tuple[tuple[str, str], ...] = (),
        allowed_parent_statuses: tuple[str, ...] = ("active", "restoring"),
        require_member_download: bool = False,
        allow_member_download_bypass: bool = False,
        allow_missing_parent: bool = False,
        require_missing_parent: bool = False,
        processing_fence: ProcessingWriteFence | None = None,
        max_bytes: int = MAX_ARTIFACT_BYTES,
        finalize: Callable[[Connection, ArtifactRecord], None] | None = None,
    ) -> BoundedArtifactWriteResult:
        if isinstance(processing_fence, DocumentPreparationArtifactFence) and (
            page_number != processing_fence.page_number
            or artifact_class != "document_page_pdf"
        ):
            raise ValueError("processing_artifact_fence_rejected")
        if len(request_fingerprint) != 64:
            raise ValueError("artifact_request_fingerprint_invalid")
        if max_bytes < 1:
            raise ValueError("artifact_max_bytes_invalid")
        identity = hashlib.sha256(
            f"{artifact_class}\0{logical_identity}".encode("utf-8")
        ).hexdigest()
        artifact_id = f"artifact-{identity[:20]}"
        blob_id = f"blob-{identity[20:40]}"

        with Session(self.engine) as session:
            if processing_fence is not None:
                self._require_processing_fence(session, processing_fence)
            existing = session.execute(select(AtlasArtifactRow).where(
                AtlasArtifactRow.artifact_class == artifact_class,
                AtlasArtifactRow.logical_identity == logical_identity,
            )).scalar_one_or_none()
            if existing is not None:
                return BoundedArtifactWriteResult(_artifact_record(existing), True)
            control = session.get(AtlasArtifactStorageControlRow, "global")
            target = (
                session.get(
                    AtlasArtifactStorageTargetRow,
                    (control.active_target_id, control.active_target_revision),
                )
                if control is not None
                and control.active_target_id is not None
                and control.active_target_revision is not None
                else None
            )
            document = session.get(AtlasDocumentRow, parent_resource_id)
        if (
            control is None
            or control.mode != "active"
            or control.active_target_id is None
            or control.active_target_revision is None
            or control.root_identity_digest is None
            or target is None
            or target.status != "active"
            or target.root_identity_digest != control.root_identity_digest
        ):
            raise RuntimeError("artifact_storage_unavailable")
        if not allow_missing_parent and (
            document is None
            or document.lifecycle_status not in allowed_parent_statuses
            or int(document.resource_lifecycle_epoch) != parent_lifecycle_epoch
        ):
            raise ValueError("artifact_parent_fence_rejected")
        if require_missing_parent and document is not None:
            raise ValueError("artifact_parent_already_exists")

        configured = self._target_config().load().get(control.active_target_id)
        if (
            configured is None
            or configured["revision"] != target.target_revision
            or configured["config_key"] != target.config_key
        ):
            raise RuntimeError("artifact_storage_target_unavailable")
        adapter = LocalArtifactFilesystemAdapter(
            configured["raw_path"], allowlisted_parents=self._allowed_parents(), create_layout=False
        )
        if adapter.root_identity_digest != control.root_identity_digest:
            raise RuntimeError("artifact_storage_root_mismatch")
        temp_name = f"{uuid4().hex}.tmp"
        attempt_id = f"attempt-{identity[:20]}"
        lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        intent = {
            "artifact_class": artifact_class,
            "logical_identity": logical_identity,
            "content_type": content_type,
            "owner_scope_type": owner_scope_type,
            "owner_scope_id": owner_scope_id,
            "document_version_id": document_version_id,
            "source_artifact_id": source_artifact_id,
            "processing_generation": processing_generation,
            "pipeline_id": pipeline_id,
            "pipeline_version": pipeline_version,
            "generation": generation,
            "page_number": page_number,
            "block_id": None,
            "acl_policy_version": None,
            "acl_action": None,
            "authorization_bindings": [list(item) for item in authorization_bindings],
            "allowed_parent_statuses": list(allowed_parent_statuses),
        }
        now = utc_now_iso()
        stale_temp_name: str | None = None
        with (
            Session(self.engine) as session,
            session.begin(),
        ):
            # Serialize only this logical artifact identity.  This is deliberately
            # not the global storage advisory lock used by the legacy aggregate
            # writer, so unrelated uploads and every read path remain independent.
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": attempt_id},
            )
            existing_attempt = session.execute(
                select(AtlasArtifactWriteAttemptRow)
                .where(AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id)
                .with_for_update()
            ).scalar_one_or_none()
            if existing_attempt is not None:
                if existing_attempt.request_fingerprint != request_fingerprint:
                    raise ValueError("artifact_idempotency_conflict")
                if existing_attempt.status == "succeeded":
                    # A succeeded attempt without its artifact row is corruption,
                    # not a new write opportunity.
                    raise RuntimeError("artifact_write_reconciliation_required")
                if existing_attempt.status == "quarantined":
                    raise RuntimeError("artifact_write_reconciliation_required")
                if (
                    existing_attempt.status == "failed"
                    and (
                        existing_attempt.reconciliation_required_at is not None
                        or existing_attempt.reconciled_at is None
                    )
                ):
                    raise RuntimeError("artifact_write_reconciliation_required")
                lease_until = datetime.fromisoformat(existing_attempt.lease_expires_at)
                if (
                    existing_attempt.status not in {"failed", "quarantined"}
                    and lease_until > datetime.now(timezone.utc)
                ):
                    raise RuntimeError("artifact_write_in_progress")
                stale_temp_name = existing_attempt.opaque_temp_name
            attempt_generation = (
                int(existing_attempt.attempt_generation) + 1
                if existing_attempt is not None else 1
            )
            values = dict(
                write_attempt_id=attempt_id,
                idempotency_scope="bounded_artifact",
                idempotency_key=logical_identity,
                request_fingerprint=request_fingerprint,
                parent_resource_id=parent_resource_id,
                parent_lifecycle_epoch=parent_lifecycle_epoch,
                status="receiving",
                lease_owner=f"bounded:{os.getpid()}",
                lease_expires_at=lease_expires_at.isoformat(),
                attempt_generation=attempt_generation,
                last_heartbeat_at=now,
                opaque_temp_name=temp_name,
                intent_json=intent,
                blob_id=None,
                byte_size=None,
                checksum_sha256=None,
                failure_code=None,
                failure_detail_summary=None,
                reconciliation_required_at=now,
                reconciled_at=None,
                reconciled_by=None,
                created_at=(existing_attempt.created_at if existing_attempt else now),
                updated_at=now,
                target_id=control.active_target_id,
                target_revision=control.active_target_revision,
                root_identity_digest=control.root_identity_digest,
                storage_epoch=control.storage_epoch,
            )
            if existing_attempt is None:
                session.add(AtlasArtifactWriteAttemptRow(**values))
            else:
                for key, value in values.items():
                    if key != "write_attempt_id":
                        setattr(existing_attempt, key, value)
        if stale_temp_name and stale_temp_name != temp_name:
            adapter.remove_temp(stale_temp_name)

        def heartbeat(_written: int) -> None:
            heartbeat_at = utc_now_iso()
            heartbeat_lease = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat()
            with Session(self.engine) as heartbeat_session, heartbeat_session.begin():
                changed = heartbeat_session.execute(
                    update(AtlasArtifactWriteAttemptRow).where(
                        AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                        AtlasArtifactWriteAttemptRow.attempt_generation == attempt_generation,
                        AtlasArtifactWriteAttemptRow.status == "receiving",
                    ).values(
                        lease_expires_at=heartbeat_lease,
                        last_heartbeat_at=heartbeat_at,
                        updated_at=heartbeat_at,
                    )
                ).rowcount
                if changed != 1:
                    raise RuntimeError("artifact_write_attempt_stale")

        # The advisory lock is scoped to this logical artifact only.  It is held
        # across write+fsync, while all unrelated uploads and every read remain
        # lock-free with respect to this operation.
        with self.engine.connect() as io_guard:
            io_guard.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:identity, 0))"),
                {"identity": attempt_id},
            )
            try:
                byte_size, digest = adapter.write_temp(
                    temp_name,
                    chunks,
                    max_bytes=max_bytes,
                    progress_callback=heartbeat,
                )
            finally:
                io_guard.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:identity, 0))"),
                    {"identity": attempt_id},
                )
        if byte_size < 1:
            adapter.remove_temp(temp_name)
            with Session(self.engine) as session, session.begin():
                changed = session.execute(
                    update(AtlasArtifactWriteAttemptRow).where(
                        AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                        AtlasArtifactWriteAttemptRow.attempt_generation == attempt_generation,
                    ).values(
                        status="failed",
                        failure_code="artifact_empty",
                        lease_expires_at=utc_now_iso(),
                        updated_at=utc_now_iso(),
                    )
                ).rowcount
                if changed != 1:
                    raise RuntimeError("artifact_write_attempt_stale")
            raise ValueError("artifact_empty")
        opaque_ref = opaque_blob_ref(hashlib.sha256(
            f"{identity}:{digest}".encode("ascii")
        ).hexdigest()[:32])
        blob_conflict = False
        with Session(self.engine) as session, session.begin():
            existing_blob = session.execute(
                select(AtlasStorageBlobRow)
                .where(AtlasStorageBlobRow.blob_id == blob_id)
                .with_for_update()
            ).scalar_one_or_none()
            expected_blob_identity = {
                "opaque_ref": opaque_ref,
                "checksum_value": digest,
                "byte_size": byte_size,
                "content_type": content_type,
                "write_attempt_id": attempt_id,
                "target_id": control.active_target_id,
                "target_revision": control.active_target_revision,
                "root_identity_digest": control.root_identity_digest,
                "storage_epoch": control.storage_epoch,
            }
            if existing_blob is not None and (
                existing_blob.status != "pending"
                or any(
                    getattr(existing_blob, key) != value
                    for key, value in expected_blob_identity.items()
                )
            ):
                blob_conflict = True
                changed = session.execute(
                    update(AtlasArtifactWriteAttemptRow).where(
                        AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                        AtlasArtifactWriteAttemptRow.attempt_generation == attempt_generation,
                    ).values(
                        status="failed",
                        failure_code="artifact_blob_identity_conflict",
                        blob_id=blob_id,
                        byte_size=existing_blob.byte_size,
                        checksum_sha256=existing_blob.checksum_value,
                        lease_expires_at=utc_now_iso(),
                        updated_at=utc_now_iso(),
                    )
                ).rowcount
                if changed != 1:
                    raise RuntimeError("artifact_write_attempt_stale")
            elif existing_blob is None:
                session.add(AtlasStorageBlobRow(
                blob_id=blob_id,
                opaque_ref=opaque_ref,
                status="pending",
                dedup_mode="none",
                dedup_scope_type=None,
                dedup_scope_id=None,
                checksum_algorithm="sha256",
                checksum_value=digest,
                byte_size=byte_size,
                content_type=content_type,
                write_attempt_id=attempt_id,
                committed_at=None,
                failure_code=None,
                failure_detail_summary=None,
                reconciliation_required_at=now,
                reconciled_at=None,
                reconciled_by=None,
                created_at=now,
                updated_at=now,
                target_id=control.active_target_id,
                target_revision=control.active_target_revision,
                root_identity_digest=control.root_identity_digest,
                storage_epoch=control.storage_epoch,
                ))
            if not blob_conflict:
                changed = session.execute(
                    update(AtlasArtifactWriteAttemptRow).where(
                        AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                        AtlasArtifactWriteAttemptRow.attempt_generation == attempt_generation,
                    ).values(
                        status="bytes_verified", blob_id=blob_id,
                        byte_size=byte_size, checksum_sha256=digest,
                        updated_at=utc_now_iso(),
                    )
                ).rowcount
                if changed != 1:
                    raise RuntimeError("artifact_write_attempt_stale")
        if blob_conflict:
            adapter.remove_temp(temp_name)
            raise ValueError("artifact_blob_identity_conflict")
        # Copy publication can be much slower than temp creation on SMB. Keep
        # the same identity guard held until the verified final is recorded as
        # published, then refresh the lease before reconciliation can acquire it.
        with self.engine.connect() as publish_guard:
            publish_guard.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:identity, 0))"),
                {"identity": attempt_id},
            )
            try:
                try:
                    adapter.publish_no_overwrite(temp_name, opaque_ref)
                except FileExistsError:
                    adapter.remove_temp(temp_name)
                adapter.verify_full(
                    opaque_ref, expected_size=byte_size, expected_sha256=digest
                )
                now = utc_now_iso()
                published_lease = (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat()
                with Session(self.engine) as session, session.begin():
                    changed = session.execute(
                        update(AtlasArtifactWriteAttemptRow).where(
                            AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                            AtlasArtifactWriteAttemptRow.attempt_generation
                            == attempt_generation,
                        ).values(
                            status="published",
                            lease_expires_at=published_lease,
                            last_heartbeat_at=now,
                            updated_at=now,
                        )
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError("artifact_write_attempt_stale")
            finally:
                publish_guard.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:identity, 0))"),
                    {"identity": attempt_id},
                )
        with Session(self.engine) as session, session.begin():
            if processing_fence is not None:
                self._require_processing_fence(session, processing_fence)
            current_control = session.execute(select(
                AtlasArtifactStorageControlRow
            ).where(
                AtlasArtifactStorageControlRow.control_id == "global"
            ).with_for_update()).scalar_one()
            current_document = session.execute(select(AtlasDocumentRow).where(
                AtlasDocumentRow.document_id == parent_resource_id
            ).with_for_update()).scalar_one_or_none()
            if (
                current_control.active_target_id != control.active_target_id
                or current_control.active_target_revision != control.active_target_revision
                or current_control.root_identity_digest != control.root_identity_digest
                or current_control.storage_epoch != control.storage_epoch
                or (
                    require_missing_parent
                    and current_document is not None
                )
                or (
                    not allow_missing_parent
                    and (
                        current_document is None
                        or current_document.lifecycle_status
                        not in allowed_parent_statuses
                        or int(current_document.resource_lifecycle_epoch)
                        != parent_lifecycle_epoch
                    )
                )
                or (
                    require_member_download
                    and (
                        current_document is None
                        or current_document.source_download_restricted
                        or (
                            not allow_member_download_bypass
                            and not current_document.allow_member_download
                        )
                    )
                )
            ):
                raise ValueError("artifact_publication_fence_rejected")
            committed = session.execute(
                update(AtlasStorageBlobRow).where(
                    AtlasStorageBlobRow.blob_id == blob_id,
                    AtlasStorageBlobRow.write_attempt_id == attempt_id,
                    AtlasStorageBlobRow.status == "pending",
                    AtlasStorageBlobRow.checksum_value == digest,
                    AtlasStorageBlobRow.byte_size == byte_size,
                    AtlasStorageBlobRow.content_type == content_type,
                ).values(
                    status="committed", committed_at=now,
                    reconciliation_required_at=None, updated_at=now,
                )
            ).rowcount
            if committed != 1:
                raise RuntimeError("artifact_blob_publication_stale")
            session.execute(pg_insert(AtlasArtifactRow).values(
                artifact_id=artifact_id,
                artifact_class=artifact_class,
                blob_id=blob_id,
                checksum_algorithm="sha256",
                checksum_value=digest,
                byte_size=byte_size,
                content_type=content_type,
                owner_scope_type=owner_scope_type,
                owner_scope_id=owner_scope_id,
                lifecycle_status="active",
                logical_identity=logical_identity,
                source_artifact_id=source_artifact_id,
                document_version_id=document_version_id,
                parent_resource_id=parent_resource_id,
                parent_lifecycle_epoch=parent_lifecycle_epoch,
                processing_generation=processing_generation,
                pipeline_id=pipeline_id,
                pipeline_version=pipeline_version,
                generation=generation,
                page_number=page_number,
                block_id=None,
                acl_policy_version=None,
                acl_action=None,
                metadata_json={},
                created_at=now,
                updated_at=now,
            ).on_conflict_do_nothing())
            bindings = ((owner_scope_type, owner_scope_id, "owner"),) + tuple(
                (scope_type, scope_id, "authorization")
                for scope_type, scope_id in authorization_bindings
            )
            for scope_type, scope_id, kind in bindings:
                binding_identity = hashlib.sha256(
                    f"{artifact_id}:{kind}:{scope_type}:{scope_id}".encode()
                ).hexdigest()
                session.execute(pg_insert(AtlasArtifactScopeBindingRow).values(
                    binding_id=f"binding-{binding_identity[:20]}",
                    artifact_id=artifact_id,
                    binding_kind=kind,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    created_at=now,
                ).on_conflict_do_nothing())
            row = session.get(AtlasArtifactRow, artifact_id)
            if row is None:
                raise RuntimeError("artifact_publication_failed")
            if row.checksum_value != digest or row.byte_size != byte_size:
                raise ValueError("artifact_identity_digest_conflict")
            artifact = _artifact_record(row)
            if finalize is not None:
                finalize(session.connection(), artifact)
            changed = session.execute(
                update(AtlasArtifactWriteAttemptRow).where(
                    AtlasArtifactWriteAttemptRow.write_attempt_id == attempt_id,
                    AtlasArtifactWriteAttemptRow.attempt_generation == attempt_generation,
                    AtlasArtifactWriteAttemptRow.status == "published",
                ).values(
                    status="succeeded", lease_expires_at=now,
                    reconciliation_required_at=None, updated_at=now,
                )
            ).rowcount
            if changed != 1:
                raise RuntimeError("artifact_write_attempt_stale")
        return BoundedArtifactWriteResult(artifact, False)
