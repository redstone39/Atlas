"""Store-free PostgreSQL providers for document upload and restore byte journeys."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Callable, Iterable, Iterator
from uuid import uuid4

from sqlalchemy import select

from atlas_production.infrastructure.artifact_storage_filesystem_adapter import (
    opaque_blob_ref,
)
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasArtifactScopeBindingRow,
    AtlasArtifactStorageControlRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentRow
from atlas_production.infrastructure.persistence.document_intake import AtlasDocumentVersionRow
from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.processing_pipeline import AtlasEvidenceRow
from atlas_production.infrastructure.postgres_artifact_journeys import (
    ArtifactUploadFacts,
    ArtifactUploadJourneyBuilder,
)
from atlas_production.infrastructure.postgres_document_upload import (
    NewDocumentUploadIntentCommand,
    NewDocumentUploadJourneyCommand,
    NewDocumentUploadJourneyInput,
    NewDocumentUploadRequestFacts,
)
from atlas_production.infrastructure.postgres_owner.artifact import (
    DocumentParentCurrentness,
)
from atlas_production.infrastructure.postgres_owner.document_processing import (
    SessionFactory,
)
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.artifact_storage.ports import ArtifactFilesystemPort
from atlas_production.modules.artifact_storage.records import ArtifactRecord, StorageFence
from atlas_production.modules.artifact_storage.public import MAX_ARTIFACT_BYTES
from atlas_production.modules.document_intake.api_models import DocumentTagRef
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.document_intake.library_records import (
    DocumentUploadResult,
    PublishedDocumentUpload,
)
from atlas_production.modules.processing_pipeline.job_records import (
    VerifiedDocumentRestoreSet,
)
from atlas_production.shared.public import AuditEventRecord, content_digest, utc_now_iso


def _identity(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


@dataclass(slots=True)
class _MeasuredUploadChunks:
    source: Iterable[bytes]
    document: DocumentRecord
    version: DocumentVersionRecord
    byte_size: int | None = None
    checksum: str | None = None

    def __iter__(self) -> Iterator[bytes]:
        digest = hashlib.sha256()
        size = 0
        for chunk in self.source:
            payload = bytes(chunk)
            digest.update(payload)
            size += len(payload)
            yield payload
        checksum = digest.hexdigest()
        self.byte_size = size
        self.checksum = checksum
        self.document.source_byte_size = size
        self.document.source_digest = checksum
        self.document.raw_sha256 = checksum
        self.version.source_digest = checksum




@dataclass(frozen=True, slots=True)
class PostgresDocumentUploadJourneyProvider:
    """Build and execute the concrete request-owned new-document journey."""

    session_factory: SessionFactory
    journey_command: NewDocumentUploadJourneyCommand
    max_bytes: int = MAX_ARTIFACT_BYTES
    worker_id: str = "api-document-upload"
    now: Callable[[], str] = utc_now_iso
    new_document_id: Callable[[], str] = lambda: uuid4().hex
    intent_command: NewDocumentUploadIntentCommand | None = None

    def _active_fence(self) -> StorageFence:
        with self.session_factory() as session:
            row = session.get(AtlasArtifactStorageControlRow, "global")
            if (
                row is None
                or row.mode != "active"
                or row.active_target_id is None
                or row.active_target_revision is None
                or row.root_identity_digest is None
            ):
                raise ArtifactStorageError(
                    "artifact_storage_unavailable",
                    "artifact.storage_is_temporarily_unavailable",
                    503,
                )
            return StorageFence(
                row.active_target_id,
                row.active_target_revision,
                row.root_identity_digest,
                row.storage_epoch,
            )

    def _canonical_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self.session_factory() as session:
            row = session.get(AtlasArtifactRow, artifact_id)
            blob = (
                session.get(AtlasStorageBlobRow, row.blob_id)
                if row is not None
                else None
            )
            if (
                row is None
                or blob is None
                or row.lifecycle_status != "active"
                or blob.status != "committed"
                or row.blob_id != blob.blob_id
                or row.checksum_value != blob.checksum_value
                or row.byte_size != blob.byte_size
                or row.content_type != blob.content_type
            ):
                raise ValueError("canonical upload artifact graph is incomplete")
            return ArtifactRecord(
                **{
                    name: (
                        dict(row.metadata_json)
                        if name == "metadata"
                        else getattr(row, name)
                    )
                    for name in ArtifactRecord.__dataclass_fields__
                }
            )

    def _canonical_publication(
        self,
        *,
        document_version_id: str,
        audit_event_id: str | None,
        job: Any,
    ) -> PublishedDocumentUpload:
        with self.session_factory() as session:
            version_row = session.get(
                AtlasDocumentVersionRow, document_version_id
            )
            audit_row = (
                session.get(AtlasAuditEventRow, audit_event_id)
                if audit_event_id is not None
                else None
            )
            if version_row is None or audit_row is None:
                raise ValueError("canonical upload publication is incomplete")
            version = DocumentVersionRecord(**dict(version_row.payload))
            audit = AuditEventRecord(
                event_id=audit_row.event_id,
                event_type=audit_row.event_type,
                actor_id=audit_row.actor_id,
                target_ref=audit_row.target_ref,
                project_id=audit_row.project_id,
                message_code=audit_row.message_code,
                metadata=dict(audit_row.event_metadata),
                created_at=audit_row.created_at,
                message_params=dict(audit_row.message_params),
                scope_type=audit_row.scope_type,
                scope_id=audit_row.scope_id,
                document_id=audit_row.document_id,
            )
            return PublishedDocumentUpload(version=version, job=job, audit=audit)

    def upload(
        self,
        *,
        chunks: Iterable[bytes],
        request_fingerprint: str,
        artifact_class: str,
        content_type: str,
        document: DocumentRecord,
        tag_refs: list[DocumentTagRef],
        authorization_bindings: tuple[tuple[str, str], ...],
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        audit_event_type: str,
        audit_message_code: str,
        audit_metadata: dict[str, Any],
        presented_browser_session_token: str,
        actor_type: str = "user",
        progress_total: int | None = None,
    ) -> DocumentUploadResult:
        if (
            artifact_class != "original_document"
            or created_by is None
            or document.scope_type not in {"team", "project"}
            or not document.scope_id
            or document.source_kind != "file_upload"
            or document.lifecycle_status != "active"
            or document.resource_lifecycle_epoch != 0
            or document.content_type != content_type
            or job_kind != "ingest"
            or self.max_bytes <= 0
        ):
            raise ValueError("new-document upload provider input is incomplete")
        owner = (document.scope_type, document.scope_id)
        scopes = tuple(sorted({owner, *authorization_bindings}))
        tag_scopes = tuple(sorted((item.tag_type, item.tag_id) for item in tag_refs))
        if scopes != tag_scopes or len(tag_scopes) != len(tag_refs):
            raise ValueError("new-document upload scope graph is incomplete")
        intent = (
            self.intent_command
            or NewDocumentUploadIntentCommand(
                self.session_factory, self.new_document_id
            )
        ).execute(
            actor_id=created_by,
            scope_type=document.scope_type,
            scope_id=document.scope_id,
            operation=idempotency_scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if intent.replayed:
            canonical = self.journey_command.canonical_intent_result(intent)
            return DocumentUploadResult(
                artifact=self._canonical_artifact(canonical.artifact_id),
                publication=self._canonical_publication(
                    document_version_id=canonical.document_version_id,
                    audit_event_id=canonical.audit_event_id,
                    job=canonical.job,
                ),
                replayed=True,
            )
        document = replace(document, document_id=intent.document_id)

        observed_at = document.uploaded_at or self.now()
        artifact_id = _identity("artifact", document.document_id, idempotency_scope, idempotency_key)
        blob_id = _identity("blob", document.document_id, idempotency_scope, idempotency_key)
        version_id = f"dver-{document.document_id}-0001"
        request_document = replace(
            document,
            source_digest="sha256:pending",
            raw_sha256=None,
            source_byte_size=None,
            original_artifact_id=artifact_id,
            uploaded_at=observed_at,
        )
        version = DocumentVersionRecord(
            document_version_id=version_id,
            document_id=request_document.document_id,
            title=request_document.title,
            source_kind=request_document.source_kind,
            document_format=request_document.document_format,
            source_digest="sha256:pending",
            content_digest=content_digest(request_document.searchable_projection),
            created_at=observed_at,
            original_artifact_id=artifact_id,
            content_type=content_type,
        )
        tags = tuple(
            DocumentTagRecord(request_document.document_id, scope_type, scope_id, observed_at)
            for scope_type, scope_id in tag_scopes
        )
        audit = AuditEventRecord(
            event_id=f"audit-{uuid4().hex}",
            event_type=audit_event_type,
            actor_id=created_by,
            target_ref=f"document:{request_document.document_id}",
            project_id=document.scope_id if document.scope_type == "project" else None,
            message_code=audit_message_code,
            metadata={
                **audit_metadata,
                "document_id": document.document_id,
                "document_version_id": version_id,
                "artifact_id": artifact_id,
            },
            created_at=observed_at,
            scope_type=document.scope_type,
            scope_id=document.scope_id,
            document_id=document.document_id,
        )
        begin_audit = replace(
            audit,
            event_id=f"audit-artifact-begin-{uuid4().hex}",
            event_type="artifact_write_started",
            metadata={
                "document_id": document.document_id,
                "operation": idempotency_scope,
                "request_id": idempotency_key,
                "request_fingerprint": request_fingerprint,
            },
        )
        finalize_audit = replace(
            begin_audit,
            event_id=f"audit-artifact-finalize-{uuid4().hex}",
            event_type="artifact_write_succeeded",
        )
        measured = _MeasuredUploadChunks(chunks, request_document, version)
        now = datetime.now(timezone.utc)
        attempt_id = f"attempt-{uuid4().hex}"
        lease_id = f"lease-{uuid4().hex}"
        facts = ArtifactUploadFacts(
            write_attempt_id=attempt_id,
            lease_id=lease_id,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            fence=self._active_fence(),
            parent_resource_id=document.document_id,
            parent_lifecycle_epoch=document.resource_lifecycle_epoch,
            worker_id=self.worker_id,
            lease_expires_at=(now + timedelta(seconds=90)).isoformat(),
            observed_at=now.isoformat(),
            opaque_temp_name=f"{uuid4().hex}.tmp",
            artifact_id=artifact_id,
            blob_id=blob_id,
            opaque_ref=opaque_blob_ref(
                hashlib.sha256(blob_id.encode("utf-8")).hexdigest()[:32]
            ),
            logical_identity=(
                f"document:{document.document_id}:original_document:{idempotency_key}"
            ),
            document_version_id=version_id,
            content_type=content_type,
            owner_scope_type=document.scope_type,
            owner_scope_id=document.scope_id,
            authorization_bindings=tag_scopes,
            owner_binding_id=_identity("binding-owner", artifact_id),
            authorization_binding_ids=tuple(
                _identity("binding-auth", artifact_id, scope_type, scope_id)
                for scope_type, scope_id in tag_scopes
            ),
            chunks=measured,
            max_bytes=self.max_bytes,
            begin_audit_events=(begin_audit,),
            finalize_audit_events=(finalize_audit,),
            expected_parent=DocumentParentCurrentness(
                document.document_id,
                document.lifecycle_status,
                document.resource_lifecycle_epoch,
                document.active_processing_generation,
            ),
            pipeline_id="document-intake",
            pipeline_version="celery-v1",
            acl_policy_version="current",
            acl_action="document_upload",
        )
        journey = ArtifactUploadJourneyBuilder().build(facts)
        result = self.journey_command.execute(
            NewDocumentUploadJourneyInput(
                artifact=journey,
                request=NewDocumentUploadRequestFacts(
                    actor_type=actor_type,
                    presented_browser_session_token=presented_browser_session_token,
                    media_type=content_type,
                    document=request_document,
                    version=version,
                    tags=tags,
                    job_kind=job_kind,
                    idempotency_scope=idempotency_scope,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    created_by=created_by,
                    audit_events=(audit,),
                    progress_total=progress_total,
                ),
            )
        )
        # Reload the committed graph for both first publication and replay. This
        # also returns the authoritative blob selected by original-byte dedup.
        artifact = self._canonical_artifact(result.artifact_id)
        return DocumentUploadResult(
            artifact=artifact,
            publication=self._canonical_publication(
                document_version_id=result.document_version_id,
                audit_event_id=result.audit_event_id,
                job=result.job,
            ),
        )


@dataclass(frozen=True, slots=True)
class _RestoreItem:
    artifact_id: str
    blob_id: str
    opaque_ref: str
    checksum: str
    byte_size: int
    artifact_class: str
    source_artifact_id: str | None
    document_version_id: str | None
    processing_generation: int | None
    metadata_valid: bool

    def proof(self) -> tuple[str, str, str, int]:
        return (self.artifact_id, self.blob_id, self.checksum, self.byte_size)


def _verify_restore_items(
    *,
    expected_document: DocumentRecord,
    items: tuple[_RestoreItem, ...],
    ready_evidence: bool,
    active_fence: StorageFence,
    filesystem: ArtifactFilesystemPort,
) -> VerifiedDocumentRestoreSet:
    original_class = (
        "original_document"
        if expected_document.source_kind == "file_upload"
        else "original_inline_source"
    )
    original = next(
        (item for item in items if item.artifact_id == expected_document.original_artifact_id),
        None,
    )
    if original is None or original.artifact_class != original_class or not original.metadata_valid:
        raise ArtifactStorageError(
            "document_restore_original_metadata_invalid",
            "document.the_original_document_metadata_is_not_valid_for_restore",
            409,
        )
    try:
        filesystem.verify_full(
            original.opaque_ref,
            expected_size=original.byte_size,
            expected_sha256=original.checksum,
        )
    except Exception as exc:
        raise ArtifactStorageError(
            "document_restore_original_integrity_failed",
            "document.the_original_document_bytes_failed_restore_verification",
            409,
        ) from exc

    reusable = expected_document.active_processing_generation > 0 and ready_evidence
    derived = tuple(
        item
        for item in items
        if item.artifact_class in {"document_page_pdf", "page_image"}
        and item.processing_generation == expected_document.active_processing_generation
    )
    if expected_document.content_type == "application/pdf" and not any(
        item.artifact_class == "document_page_pdf" for item in derived
    ):
        reusable = False
    derived_proof: list[tuple[str, str, str, int]] = []
    for item in derived:
        if (
            not reusable
            or not item.metadata_valid
            or item.source_artifact_id != original.artifact_id
            or item.document_version_id != original.document_version_id
        ):
            reusable = False
            derived_proof.clear()
            break
        try:
            filesystem.verify_full(
                item.opaque_ref,
                expected_size=item.byte_size,
                expected_sha256=item.checksum,
            )
        except Exception:
            reusable = False
            derived_proof.clear()
            break
        derived_proof.append(item.proof())
    proof = [original.proof(), *(derived_proof if reusable else ())]
    return VerifiedDocumentRestoreSet(
        document_id=expected_document.document_id,
        resource_lifecycle_epoch=expected_document.resource_lifecycle_epoch,
        active_fence=active_fence,
        artifacts=tuple(sorted(proof)),
        reusable_processing_generation=reusable,
    )


@dataclass(frozen=True, slots=True)
class PostgresDocumentRestoreProofProvider:
    """Load exact restore metadata, then verify bytes after the SQL read closes."""

    session_factory: SessionFactory
    filesystem: ArtifactFilesystemPort

    def verify(self, expected_document: DocumentRecord) -> VerifiedDocumentRestoreSet:
        if expected_document.lifecycle_status != "restoring":
            raise ValueError("restore proof requires a restoring document preimage")
        with self.session_factory() as session:
            document_row = session.get(AtlasDocumentRow, expected_document.document_id)
            if document_row is None or any(
                getattr(document_row, name) != value
                for name, value in asdict(expected_document).items()
            ):
                raise ValueError("restore document currentness changed")
            control = session.get(AtlasArtifactStorageControlRow, "global")
            if (
                control is None
                or control.mode != "active"
                or control.active_target_id is None
                or control.active_target_revision is None
                or control.root_identity_digest is None
            ):
                raise ArtifactStorageError(
                    "artifact_storage_unavailable",
                    "artifact.storage_is_temporarily_unavailable",
                    503,
                )
            fence = (
                control.active_target_id,
                control.active_target_revision,
                control.root_identity_digest,
                control.storage_epoch,
            )
            active_fence = StorageFence(*fence)
            artifact_rows = tuple(
                session.scalars(
                    select(AtlasArtifactRow).where(
                        AtlasArtifactRow.parent_resource_id == expected_document.document_id,
                        AtlasArtifactRow.lifecycle_status == "active",
                    )
                ).all()
            )
            blob_ids = {row.blob_id for row in artifact_rows}
            blobs = {
                row.blob_id: row
                for row in session.scalars(
                    select(AtlasStorageBlobRow).where(AtlasStorageBlobRow.blob_id.in_(blob_ids or {""}))
                ).all()
            }
            owner_binding_rows = tuple(
                session.scalars(
                    select(AtlasArtifactScopeBindingRow).where(
                        AtlasArtifactScopeBindingRow.artifact_id.in_(
                            {row.artifact_id for row in artifact_rows} or {""}
                        ),
                        AtlasArtifactScopeBindingRow.binding_kind == "owner",
                    )
                ).all()
            )
            owner_bindings: dict[str, list[tuple[str, str | None]]] = {}
            for binding in owner_binding_rows:
                owner_bindings.setdefault(binding.artifact_id, []).append(
                    (binding.scope_type, binding.scope_id)
                )
            ready_evidence_rows = tuple(
                session.scalars(
                    select(AtlasEvidenceRow).where(
                        AtlasEvidenceRow.document_id == expected_document.document_id,
                        AtlasEvidenceRow.processing_generation
                        == expected_document.active_processing_generation,
                        AtlasEvidenceRow.status == "ready",
                    )
                ).all()
            )
            items: list[_RestoreItem] = []
            for artifact in artifact_rows:
                blob = blobs.get(artifact.blob_id)
                metadata_valid = bool(
                    blob is not None
                    and blob.status == "committed"
                    and (blob.target_id, blob.target_revision, blob.root_identity_digest, blob.storage_epoch) == fence
                    and artifact.owner_scope_type == expected_document.scope_type
                    and artifact.owner_scope_id == expected_document.scope_id
                    and owner_bindings.get(artifact.artifact_id)
                    == [(expected_document.scope_type, expected_document.scope_id)]
                    and artifact.checksum_value == blob.checksum_value
                    and artifact.byte_size == blob.byte_size
                    and artifact.parent_lifecycle_epoch
                    == expected_document.resource_lifecycle_epoch
                    and bool(artifact.acl_policy_version)
                    and bool(artifact.acl_action)
                )
                items.append(
                    _RestoreItem(
                        artifact.artifact_id,
                        artifact.blob_id,
                        blob.opaque_ref if blob is not None else "",
                        artifact.checksum_value,
                        artifact.byte_size,
                        artifact.artifact_class,
                        artifact.source_artifact_id,
                        artifact.document_version_id,
                        artifact.processing_generation,
                        metadata_valid,
                    )
                )

            original_version_id = next(
                (
                    item.document_version_id
                    for item in items
                    if item.artifact_id == expected_document.original_artifact_id
                ),
                None,
            )
            ready_evidence = bool(
                original_version_id
                and any(
                    row.document_version_id == original_version_id
                    for row in ready_evidence_rows
                )
            )

        return _verify_restore_items(
            expected_document=expected_document,
            items=tuple(items),
            ready_evidence=ready_evidence,
            active_fence=active_fence,
            filesystem=self.filesystem,
        )


__all__ = [
    "PostgresDocumentRestoreProofProvider",
    "PostgresDocumentUploadJourneyProvider",
]
