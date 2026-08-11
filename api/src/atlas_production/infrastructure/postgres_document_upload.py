"""Named atomic terminal transaction for one new-document upload."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Callable

from sqlalchemy import select

from atlas_production.infrastructure.persistence.audit_events import AtlasAuditEventRow
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
    _document_row,
    _document_version_payload,
)
from atlas_production.infrastructure.persistence.artifact_storage import AtlasArtifactRow
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactScopeBindingRow,
    AtlasArtifactWriteAttemptRow,
    AtlasStorageReconciliationFindingRow,
    AtlasStorageRequestLeaseRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingGenerationRow,
    AtlasProcessingJobRow,
    AtlasProcessingRequestSnapshotRow,
    AtlasTaskOutboxRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAccessDecisionRow,
    read_session_actor,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.postgres_artifact_journeys import (
    ArtifactUploadJourney,
    ArtifactUploadJourneyBuilder,
)
from atlas_production.infrastructure.postgres_artifact_storage_adapter import (
    PostgresArtifactStorageAdapter,
)
from atlas_production.infrastructure.postgres_locks import acquire_mixed_owner_locks
from atlas_production.infrastructure.postgres_owner.artifact import (
    NewDocumentOriginalArtifactPublication,
    NewDocumentOriginalArtifactPublicationWriter,
    new_document_original_artifact_lock_identities,
)
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.infrastructure.postgres_owner.document_processing import (
    ProcessingExecutionCaptureWriter,
    ProcessingExecutionAcceptanceWriter,
    JobTransitionCommand,
    SessionFactory,
    canonical_processing_spec_from_snapshot,
    document_processing_acceptance_identity,
    document_processing_acceptance_lock_identities,
    processing_fingerprint_from_snapshot,
)
from atlas_production.infrastructure.postgres_document_intake_adapter import (
    DocumentUploadAuthorityWriter,
    document_upload_authority_lock_plan,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.document_intake.library_records import (
    DocumentUploadAccessDenied,
    DocumentUploadReplayConflict,
    DocumentUploadUnauthenticated,
)
from atlas_production.modules.processing_pipeline.job_records import (
    ProcessingExecutionSnapshot,
    ProcessingJobRecord,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso
from atlas_production.modules.artifact_storage.records import (
    ArtifactWriteAttemptRecord,
    StorageRequestLeaseRecord,
)
from atlas_production.modules.identity_access.records import AccessDecisionRecord
from atlas_production.infrastructure.postgres_owner.artifact import (
    HeartbeatArtifactWriteInput,
)


@dataclass(frozen=True, slots=True)
class NewDocumentUploadInput:
    """SQL-terminal facts produced after byte-plane write and verification."""

    media_type: str
    document: DocumentRecord
    version: DocumentVersionRecord
    tags: tuple[DocumentTagRecord, ...]
    artifact_publication: NewDocumentOriginalArtifactPublication
    job_kind: str
    idempotency_scope: str
    idempotency_key: str
    created_by: str | None
    audit_events: tuple[AuditEventRecord, ...]
    execution_snapshot: ProcessingExecutionSnapshot
    authorization_decisions: tuple[AccessDecisionRecord, ...]
    progress_total: int | None = None


@dataclass(frozen=True, slots=True)
class NewDocumentUploadResult:
    document_id: str
    document_version_id: str
    artifact_id: str
    job: ProcessingJobRecord | None
    audit_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class NewDocumentUploadBoundaryFacts:
    """Detached facts fixed once for the lifetime of one live upload request."""

    authorization_decisions: tuple[AccessDecisionRecord, ...]
    execution_snapshot: ProcessingExecutionSnapshot




@dataclass(frozen=True, slots=True)
class NewDocumentUploadRequestBoundaryCommand:
    """Capture authority and processing configuration before any byte I/O."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        scope_type: str,
        scope_id: str,
        tag_scopes: tuple[tuple[str, str], ...],
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        progress_total: int | None = None,
    ) -> NewDocumentUploadBoundaryFacts:
        if (
            actor_type != "user"
            or not presented_browser_session_token
            or not presented_browser_session_token.strip()
        ):
            raise DocumentUploadUnauthenticated(
                "document upload requires a presented browser session credential"
            )
        if job_kind != "ingest":
            raise ValueError("new-document upload requires an ingest job")
        if (scope_type, scope_id) not in set(tag_scopes):
            raise ValueError("document owner scope is missing from upload tags")
        domain_keys, identity_keys, _ = document_upload_authority_lock_plan(
            actor_type=actor_type,
            actor_id=actor_id,
            scopes=tag_scopes,
        )
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=(
                        "model-routing:configuration-control",
                        "processing-registry:configuration-control",
                    ),
                    exclusive_domain_keys=domain_keys,
                    exclusive_identity_keys=(
                        *identity_keys,
                        f"identity:session:{presented_browser_session_token}",
                    ),
                )
                actor = read_session_actor(
                    session,
                    presented_browser_session_token,
                )
                if actor is None or actor.actor_id != actor_id:
                    raise DocumentUploadUnauthenticated(
                        "document upload browser session is no longer authenticated"
                    )
                decisions = DocumentUploadAuthorityWriter(session).execute_many(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    scopes=tag_scopes,
                    locks_held=True,
                )
                denied = next(
                    (decision for decision in decisions if not decision.allowed),
                    None,
                )
                denial_audit = (
                    AuditEventRecord(
                        event_id=f"audit-upload-denied-{denied.decision_id}",
                        event_type="document_upload_denied",
                        actor_id=actor_id,
                        target_ref=f"{scope_type}:{scope_id}",
                        project_id=scope_id if scope_type == "project" else None,
                        message_code="document.upload_requires_uploader_or_admin_access_to_this_scope",
                        metadata={
                            "document_id": document_id,
                            "reason": denied.reason,
                            "access_decision_ids": [
                                decision.decision_id for decision in decisions
                            ],
                        },
                        created_at=utc_now_iso(),
                        scope_type=scope_type,
                        scope_id=scope_id,
                        document_id=document_id,
                    )
                    if denied is not None
                    else None
                )
                if denial_audit is not None:
                    AuditEventWriter(session).append_many((denial_audit,))
                snapshot = (
                    ProcessingExecutionCaptureWriter(session).execute(
                        media_type=media_type,
                        document_id=document_id,
                        document_version_id=document_version_id,
                        job_kind=job_kind,
                        created_by=actor_id,
                        progress_total=progress_total,
                        configuration_locks_held=True,
                    )
                    if denied is None
                    else None
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
        if denied is not None:
            assert denial_audit is not None
            raise DocumentUploadAccessDenied(denied, denial_audit)
        assert snapshot is not None
        return NewDocumentUploadBoundaryFacts(
            authorization_decisions=decisions,
            execution_snapshot=snapshot,
        )


@dataclass(frozen=True, slots=True)
class NewDocumentUploadTerminalFacts:
    media_type: str
    document: DocumentRecord
    version: DocumentVersionRecord
    tags: tuple[DocumentTagRecord, ...]
    job_kind: str
    idempotency_scope: str
    idempotency_key: str
    created_by: str
    audit_events: tuple[AuditEventRecord, ...]
    execution_snapshot: ProcessingExecutionSnapshot
    authorization_decisions: tuple[AccessDecisionRecord, ...]
    progress_total: int | None = None


@dataclass(frozen=True, slots=True)
class NewDocumentUploadRequestFacts:
    actor_type: str
    presented_browser_session_token: str
    media_type: str
    document: DocumentRecord
    version: DocumentVersionRecord
    tags: tuple[DocumentTagRecord, ...]
    job_kind: str
    idempotency_scope: str
    idempotency_key: str
    created_by: str
    audit_events: tuple[AuditEventRecord, ...]
    progress_total: int | None = None


@dataclass(frozen=True, slots=True)
class NewDocumentUploadJourneyInput:
    artifact: ArtifactUploadJourney
    request: NewDocumentUploadRequestFacts


DispatchAcceptedJob = Callable[[ProcessingJobRecord], None]


def _validate_input(command: NewDocumentUploadInput) -> None:
    document = command.document
    version = command.version
    publication = command.artifact_publication
    scopes = frozenset((tag.tag_type, tag.tag_id) for tag in command.tags)
    owner_scope = (
        publication.artifact.owner_scope_type,
        publication.artifact.owner_scope_id,
    )
    decisions = command.authorization_decisions
    decision_scopes = {
        (decision.scope_type, decision.scope_id) for decision in decisions
    }
    decision_ids = sorted(decision.decision_id for decision in decisions)
    expected_target = f"document:{document.document_id}"
    if (
        not document.document_id
        or version.document_id != document.document_id
        or version.title != document.title
        or version.source_kind != document.source_kind
        or version.document_format != document.document_format
        or version.source_digest
        not in {document.source_digest, document.raw_sha256}
        or version.document_version_id
        != publication.artifact.document_version_id
        or publication.attempt.parent_resource_id != document.document_id
        or publication.artifact.parent_resource_id != document.document_id
        or publication.verified_tag_scopes != scopes
        or document.original_artifact_id != publication.artifact.artifact_id
        or version.original_artifact_id != publication.artifact.artifact_id
        or len(scopes) != len(command.tags)
        or any(tag.document_id != document.document_id for tag in command.tags)
        or document.scope_type not in {"team", "project"}
        or not document.scope_id
        or (document.scope_type, document.scope_id) != owner_scope
        or owner_scope not in scopes
        or document.lifecycle_status != "active"
        or document.resource_lifecycle_epoch != 0
        or version.status != "active"
        or version.supersedes_version_id is not None
        or command.job_kind != "ingest"
        or not command.idempotency_scope
        or not command.idempotency_key
        or document.source_kind != "file_upload"
        or publication.artifact.parent_lifecycle_epoch
        != document.resource_lifecycle_epoch
        or document.content_type != version.content_type
        or document.content_type != publication.artifact.content_type
        or document.content_type != publication.blob.content_type
        or document.raw_sha256 != publication.blob.checksum_value
        or document.source_digest != publication.blob.checksum_value
        or document.source_byte_size != publication.blob.byte_size
        or command.execution_snapshot.acceptance_request_digest == ""
        or len(decision_scopes) != len(decisions)
        or decision_scopes != scopes
        or any(
            not decision.allowed
            or decision.actor_id != command.created_by
            or decision.action != "document_register"
            for decision in decisions
        )
        or not command.audit_events
        or len({event.event_id for event in command.audit_events})
        != len(command.audit_events)
        or not command.created_by
        or any(
            event.actor_id != command.created_by
            or event.target_ref != expected_target
            or event.document_id != document.document_id
            or event.scope_type != document.scope_type
            or event.scope_id != document.scope_id
            or sorted(event.metadata.get("access_decision_ids", [])) != decision_ids
            or event.metadata.get("operation") != command.idempotency_scope
            or event.metadata.get("request_id") != command.idempotency_key
            or event.project_id
            != (document.scope_id if document.scope_type == "project" else None)
            for event in command.audit_events
        )
    ):
        raise ValueError("new-document upload graph is incomplete")


def _document_payload(document: DocumentRecord) -> dict[str, object]:
    return {
        name: getattr(document, name) for name in DocumentRecord.__dataclass_fields__
    }


_PROCESSING_PROJECTION_FIELDS = frozenset(
    {
        "intake_status",
        "active_processing_generation",
        "active_index_generation_id",
        "processing_profile_id",
        "processing_profile_revision",
        "current_stage",
        "warning_codes",
        "failure_code",
        "processing_job_id",
    }
)


def _audit_row_matches(row: AtlasAuditEventRow, event: AuditEventRecord) -> bool:
    return all(
        (
            row.event_type == event.event_type,
            row.actor_id == event.actor_id,
            row.target_ref == event.target_ref,
            row.project_id == event.project_id,
            row.scope_type == event.scope_type,
            row.scope_id == event.scope_id,
            row.document_id == event.document_id,
            row.message_code == event.message_code,
            row.message_params == event.message_params,
            row.event_metadata == event.metadata,
            row.created_at == event.created_at,
        )
    )


def _decision_row_matches(
    row: AtlasAccessDecisionRow,
    decision: AccessDecisionRecord,
) -> bool:
    return all(
        getattr(row, field_name) == getattr(decision, field_name)
        for field_name in decision.__dataclass_fields__
    )


def _audit_semantic_payload(metadata: dict[str, object]) -> dict[str, object]:
    """Exclude request-local evidence identities from idempotent result matching."""

    return {
        key: value
        for key, value in metadata.items()
        if key != "access_decision_ids"
    }


def _audit_semantically_matches(
    row: AtlasAuditEventRow,
    event: AuditEventRecord,
) -> bool:
    return all(
        (
            row.event_type == event.event_type,
            row.actor_id == event.actor_id,
            row.target_ref == event.target_ref,
            row.project_id == event.project_id,
            row.scope_type == event.scope_type,
            row.scope_id == event.scope_id,
            row.document_id == event.document_id,
            row.message_code == event.message_code,
            row.message_params == event.message_params,
            _audit_semantic_payload(dict(row.event_metadata))
            == _audit_semantic_payload(dict(event.metadata)),
        )
    )


def _terminal_audit_rows(
    rows: tuple[AtlasAuditEventRow, ...] | list[AtlasAuditEventRow],
    expected_events: tuple[AuditEventRecord, ...],
) -> tuple[AtlasAuditEventRow, ...]:
    """Exclude byte-plane lifecycle audits from terminal upload replay."""

    expected_event_types = {event.event_type for event in expected_events}
    return tuple(row for row in rows if row.event_type in expected_event_types)


@dataclass(frozen=True, slots=True)
class NewDocumentUploadCommand:
    """Commit document, artifact, processing request, job/outbox and audit once."""

    session_factory: SessionFactory

    def canonical_result(
        self,
        *,
        terminal: NewDocumentUploadTerminalFacts,
        artifact_journey: ArtifactUploadJourney,
        canonical_attempt_id: str,
    ) -> NewDocumentUploadResult:
        document_id = terminal.document.document_id
        document_version_id = terminal.version.document_version_id
        artifact_id = terminal.document.original_artifact_id or ""
        identity = document_processing_acceptance_identity(
            document_id=document_id,
            idempotency_scope=terminal.idempotency_scope,
            idempotency_key=terminal.idempotency_key,
        )
        with self.session_factory() as session:
            document = session.get(AtlasDocumentRow, document_id)
            version = session.get(AtlasDocumentVersionRow, document_version_id)
            artifact = session.get(AtlasArtifactRow, artifact_id)
            blob = (
                session.get(AtlasStorageBlobRow, artifact.blob_id)
                if artifact is not None
                else None
            )
            job_row = session.scalar(
                select(AtlasProcessingJobRow).where(
                    AtlasProcessingJobRow.idempotency_scope
                    == terminal.idempotency_scope,
                    AtlasProcessingJobRow.idempotency_key == terminal.idempotency_key,
                )
            )
            snapshot = session.get(AtlasProcessingRequestSnapshotRow, identity.job_id)
            generation = session.get(
                AtlasProcessingGenerationRow,
                (document_id, identity.processing_generation),
            )
            index_generation = session.get(
                AtlasIndexGenerationRow,
                identity.index_generation_id,
            )
            outbox = session.get(AtlasTaskOutboxRow, identity.outbox_id)
            attempt = session.get(
                AtlasArtifactWriteAttemptRow,
                canonical_attempt_id,
            )
            lease = (
                session.scalar(
                    select(AtlasStorageRequestLeaseRow).where(
                        AtlasStorageRequestLeaseRow.request_kind == "artifact_write",
                        AtlasStorageRequestLeaseRow.owner == attempt.lease_owner,
                        AtlasStorageRequestLeaseRow.target_id == attempt.target_id,
                        AtlasStorageRequestLeaseRow.target_revision
                        == attempt.target_revision,
                        AtlasStorageRequestLeaseRow.parent_resource_id
                        == attempt.parent_resource_id,
                        AtlasStorageRequestLeaseRow.attempt_generation
                        == attempt.attempt_generation,
                    )
                )
                if attempt is not None
                else None
            )
            reconciliation_finding = session.scalar(
                select(AtlasStorageReconciliationFindingRow).where(
                    (
                        AtlasStorageReconciliationFindingRow.write_attempt_id
                        == canonical_attempt_id
                    )
                    | (
                        AtlasStorageReconciliationFindingRow.blob_id
                        == (artifact.blob_id if artifact is not None else "")
                    )
                )
            )
            decisions = {
                item.decision_id: session.get(AtlasAccessDecisionRow, item.decision_id)
                for item in terminal.authorization_decisions
            }
            tags = {
                (row.tag_type, row.tag_id, row.created_at)
                for row in session.scalars(
                    select(AtlasDocumentTagRow).where(
                        AtlasDocumentTagRow.document_id == document_id
                    )
                ).all()
            }
            bindings = {
                (row.binding_kind, row.scope_type, row.scope_id)
                for row in session.scalars(
                    select(AtlasArtifactScopeBindingRow).where(
                        AtlasArtifactScopeBindingRow.artifact_id == artifact_id
                    )
                ).all()
            }
            semantic_audits = _terminal_audit_rows(
                session.scalars(
                    select(AtlasAuditEventRow).where(
                        AtlasAuditEventRow.document_id == document_id,
                        AtlasAuditEventRow.event_metadata["operation"].as_string()
                        == terminal.idempotency_scope,
                        AtlasAuditEventRow.event_metadata["request_id"].as_string()
                        == terminal.idempotency_key,
                    )
                ).all(),
                terminal.audit_events,
            )
            accepted_snapshot = dict(snapshot.payload) if snapshot is not None else {}
            common_replay_invalid = (
                document is None
                or version is None
                or artifact is None
                or blob is None
                or attempt is None
                or lease is not None
                or reconciliation_finding is not None
                or any(row is None for row in decisions.values())
                or artifact.parent_resource_id != document_id
                or artifact.document_version_id != document_version_id
                or attempt.idempotency_scope
                != artifact_journey.attempt.idempotency_scope
                or attempt.idempotency_key != artifact_journey.attempt.idempotency_key
                or attempt.request_fingerprint
                != artifact_journey.attempt.request_fingerprint
                or attempt.parent_resource_id != document_id
                or attempt.parent_lifecycle_epoch
                != terminal.document.resource_lifecycle_epoch
                or attempt.status != "succeeded"
                or attempt.blob_id != artifact.blob_id
                or attempt.byte_size != terminal.document.source_byte_size
                or attempt.checksum_sha256 != terminal.document.raw_sha256
                or any(
                    name not in _PROCESSING_PROJECTION_FIELDS
                    and getattr(document, name) != value
                    for name, value in _document_payload(terminal.document).items()
                )
                or version.payload != _document_version_payload(terminal.version)
                or tags
                != {
                    (tag.tag_type, tag.tag_id, tag.created_at)
                    for tag in terminal.tags
                }
                or artifact.owner_scope_type != terminal.document.scope_type
                or artifact.owner_scope_id != terminal.document.scope_id
                or artifact.content_type != terminal.document.content_type
                or artifact.checksum_value != terminal.document.raw_sha256
                or artifact.byte_size != terminal.document.source_byte_size
                or blob.content_type != terminal.document.content_type
                or blob.checksum_value != terminal.document.raw_sha256
                or blob.byte_size != terminal.document.source_byte_size
                or ("owner", terminal.document.scope_type, terminal.document.scope_id)
                not in bindings
                or {
                    (scope_type, scope_id)
                    for kind, scope_type, scope_id in bindings
                    if kind == "authorization"
                }
                != {(tag.tag_type, tag.tag_id) for tag in terminal.tags}
                or any(
                    row is None or not _decision_row_matches(row, expected)
                    for expected in terminal.authorization_decisions
                    for row in (decisions[expected.decision_id],)
                )
                or len(semantic_audits) != len(terminal.audit_events)
                or any(
                    not any(
                        _audit_semantically_matches(row, expected)
                        for row in semantic_audits
                    )
                    for expected in terminal.audit_events
                )
            )
            if common_replay_invalid:
                raise DocumentUploadReplayConflict(
                    "new-document upload canonical replay is incomplete"
                )
            if job_row is None:
                processing_identity = (
                    session.get(
                        AtlasProcessingIdentityRow,
                        document.processing_identity_id,
                    )
                    if document.processing_identity_id is not None
                    else None
                )
                source_artifact = (
                    session.get(
                        AtlasArtifactRow,
                        processing_identity.source_artifact_id,
                    )
                    if processing_identity is not None
                    else None
                )
                current_revision = (
                    session.get(
                        AtlasProcessingRevisionRow,
                        processing_identity.current_revision_id,
                    )
                    if processing_identity is not None
                    and processing_identity.current_revision_id is not None
                    else None
                )
                latest_revision = (
                    session.scalar(
                        select(AtlasProcessingRevisionRow)
                        .where(
                            AtlasProcessingRevisionRow.processing_identity_id
                            == processing_identity.processing_identity_id
                        )
                        .order_by(
                            AtlasProcessingRevisionRow.revision_number.desc()
                        )
                        .limit(1)
                    )
                    if processing_identity is not None
                    else None
                )
                shared_job_row = (
                    session.get(
                        AtlasProcessingJobRow,
                        document.processing_job_id,
                    )
                    if document.processing_job_id is not None
                    else None
                )
                expected_spec = canonical_processing_spec_from_snapshot(
                    terminal.execution_snapshot
                )
                valid_current_hit = (
                    current_revision is not None
                    and current_revision.processing_identity_id
                    == processing_identity.processing_identity_id
                    and current_revision.state == "ready"
                )
                valid_terminal_hit = (
                    current_revision is None
                    and latest_revision is not None
                    and latest_revision.state in {"failed", "cancelled"}
                )
                valid_joined_build = (
                    shared_job_row is not None
                    and processing_identity is not None
                    and latest_revision is not None
                    and shared_job_row.processing_identity_id
                    == processing_identity.processing_identity_id
                    and shared_job_row.processing_revision_id
                    == latest_revision.processing_revision_id
                    and shared_job_row.status
                    in {"queued", "running", "retry_wait"}
                    and latest_revision.state == "building"
                )
                if (
                    snapshot is not None
                    or generation is not None
                    or index_generation is not None
                    or outbox is not None
                    or processing_identity is None
                    or source_artifact is None
                    or processing_identity.source_sha256 != document.raw_sha256
                    or processing_identity.source_artifact_checksum_sha256
                    != document.raw_sha256
                    or source_artifact.checksum_value != document.raw_sha256
                    or processing_identity.processing_spec != expected_spec
                    or processing_identity.processing_fingerprint
                    != processing_fingerprint_from_snapshot(
                        terminal.execution_snapshot
                    )
                    or not (
                        valid_current_hit
                        or valid_terminal_hit
                        or valid_joined_build
                    )
                ):
                    raise DocumentUploadReplayConflict(
                        "new-document zero-job replay is incomplete"
                    )
                audit_event_id = (
                    min(row.event_id for row in semantic_audits)
                    if semantic_audits
                    else None
                )
                if valid_joined_build:
                    assert shared_job_row is not None
                    shared_job = ProcessingJobRecord(
                        **{
                            field: getattr(shared_job_row, field)
                            for field in ProcessingJobRecord.__dataclass_fields__
                        }
                    )
                    return NewDocumentUploadResult(
                        document_id=document_id,
                        document_version_id=document_version_id,
                        artifact_id=artifact_id,
                        job=shared_job,
                        audit_event_id=audit_event_id,
                    )
                return NewDocumentUploadResult(
                    document_id=document_id,
                    document_version_id=document_version_id,
                    artifact_id=artifact_id,
                    job=None,
                    audit_event_id=audit_event_id,
                )
            if (
                snapshot is None
                or generation is None
                or index_generation is None
                or outbox is None
                or job_row.document_id != document_id
                or job_row.document_version_id != document_version_id
                or job_row.job_id != identity.job_id
                or job_row.index_generation_id != identity.index_generation_id
                or snapshot.document_id != document_id
                or snapshot.processing_generation != identity.processing_generation
                or accepted_snapshot.get("acceptance_request_digest")
                != terminal.execution_snapshot.acceptance_request_digest
                or generation.document_version_id != document_version_id
                or generation.profile_id != accepted_snapshot.get("profile_id")
                or generation.profile_revision
                != accepted_snapshot.get("profile_revision")
                or index_generation.document_id != document_id
                or index_generation.document_version_id != document_version_id
                or index_generation.source_processing_generation
                != identity.processing_generation
                or job_row.job_kind != terminal.job_kind
                or job_row.created_by != terminal.created_by
                or outbox.payload != {
                    "job_id": identity.job_id,
                    "attempt": 1,
                    "schema_version": 1,
                }
                or outbox.outbox_id != identity.outbox_id
                or outbox.task_name != "atlas.processing.prepare_job"
                or outbox.queue_name != "atlas.processing"
                or outbox.payload_schema_version != 1
            ):
                raise DocumentUploadReplayConflict(
                    "new-document upload canonical replay is incomplete"
                )
            job_id = job_row.job_id
            audit_event_id = (
                min(row.event_id for row in semantic_audits)
                if semantic_audits
                else None
            )
        job = JobTransitionCommand(self.session_factory).get_job(job_id)
        if job is None:
            raise ValueError("new-document upload canonical job is missing")
        return NewDocumentUploadResult(
            document_id=document_id,
            document_version_id=document_version_id,
            artifact_id=artifact_id,
            job=job,
            audit_event_id=audit_event_id,
        )

    def execute(self, command: NewDocumentUploadInput) -> NewDocumentUploadResult:
        _validate_input(command)
        publication = command.artifact_publication
        acceptance_identity = document_processing_acceptance_identity(
            document_id=command.document.document_id,
            idempotency_scope=command.idempotency_scope,
            idempotency_key=command.idempotency_key,
        )
        identity_keys = tuple(
            sorted(
                {
                    *new_document_original_artifact_lock_identities(publication),
                    *document_processing_acceptance_lock_identities(
                        document_id=command.document.document_id,
                        document_version_id=command.version.document_version_id,
                        idempotency_scope=command.idempotency_scope,
                        idempotency_key=command.idempotency_key,
                        identity=acceptance_identity,
                    ),
                    *(
                        f"document:tag:{tag.document_id}:{tag.tag_type}:{tag.tag_id}"
                        for tag in command.tags
                    ),
                    *(
                        f"audit:decision:{decision.decision_id}"
                        for decision in command.authorization_decisions
                    ),
                    *(
                        f"audit:event:{event.event_id}"
                        for event in command.audit_events
                    ),
                }
            )
        )
        session = self.session_factory()
        with session:
            try:
                # Domain controls must precede every identity/row lock in the graph.
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=(
                        "artifact:control",
                    ),
                    exclusive_identity_keys=identity_keys,
                )
                NewDocumentOriginalArtifactPublicationWriter(
                    session
                ).publish_new_document_original(publication)
                existing = session.scalar(
                    select(AtlasDocumentRow)
                    .where(AtlasDocumentRow.document_id == command.document.document_id)
                    .with_for_update()
                )
                decisions = {
                    item.decision_id: session.get(
                        AtlasAccessDecisionRow, item.decision_id
                    )
                    for item in command.authorization_decisions
                }
                if any(
                    row is None or not _decision_row_matches(row, expected)
                    for expected in command.authorization_decisions
                    for row in (decisions[expected.decision_id],)
                ):
                    raise ValueError(
                        "new-document upload request-boundary decision is missing"
                    )
                if existing is not None and any(
                    name not in _PROCESSING_PROJECTION_FIELDS
                    and getattr(existing, name) != value
                    for name, value in _document_payload(command.document).items()
                ):
                    raise ValueError("new-document upload idempotency conflict")
                existing_version = session.get(
                    AtlasDocumentVersionRow, command.version.document_version_id
                )
                version_payload = _document_version_payload(command.version)
                if existing_version is not None and (
                    existing_version.document_id != command.version.document_id
                    or existing_version.payload != version_payload
                ):
                    raise ValueError("new-document upload version conflict")
                if existing is None:
                    session.add(_document_row(command.document))
                session.merge(
                    AtlasDocumentVersionRow(
                        document_version_id=command.version.document_version_id,
                        document_id=command.version.document_id,
                        payload=version_payload,
                    )
                )
                existing_tags = {
                    (row.tag_type, row.tag_id, row.created_at)
                    for row in session.scalars(
                        select(AtlasDocumentTagRow).where(
                            AtlasDocumentTagRow.document_id
                            == command.document.document_id
                        )
                    ).all()
                }
                expected_tags = {
                    (tag.tag_type, tag.tag_id, tag.created_at) for tag in command.tags
                }
                if existing_tags and existing_tags != expected_tags:
                    raise ValueError("new-document upload tag conflict")
                for tag in command.tags:
                    session.merge(
                        AtlasDocumentTagRow(
                            document_id=tag.document_id,
                            tag_type=tag.tag_type,
                            tag_id=tag.tag_id,
                            created_at=tag.created_at,
                        )
                    )
                session.flush()
                job = ProcessingExecutionAcceptanceWriter(session).accept_job(
                    media_type=command.media_type,
                    document_id=command.document.document_id,
                    document_version_id=command.version.document_version_id,
                    job_kind=command.job_kind,
                    idempotency_scope=command.idempotency_scope,
                    idempotency_key=command.idempotency_key,
                    created_by=command.created_by,
                    progress_total=command.progress_total,
                    execution_snapshot=command.execution_snapshot,
                    acceptance_identity=acceptance_identity,
                )
                existing_audits = {
                    row.event_id: row
                    for row in _terminal_audit_rows(
                        session.scalars(
                            select(AtlasAuditEventRow).where(
                                AtlasAuditEventRow.document_id
                                == command.document.document_id,
                                AtlasAuditEventRow.event_metadata["operation"].as_string()
                                == command.idempotency_scope,
                                AtlasAuditEventRow.event_metadata["request_id"].as_string()
                                == command.idempotency_key,
                            )
                        ).all(),
                        command.audit_events,
                    )
                }
                if existing_audits:
                    if len(existing_audits) != len(command.audit_events) or any(
                        event.event_id not in existing_audits
                        or not _audit_row_matches(
                            existing_audits[event.event_id], event
                        )
                        for event in command.audit_events
                    ):
                        raise ValueError("new-document upload audit replay conflict")
                else:
                    AuditEventWriter(session).append_many(command.audit_events)
                session.commit()
                return NewDocumentUploadResult(
                    document_id=command.document.document_id,
                    document_version_id=command.version.document_version_id,
                    artifact_id=publication.artifact.artifact_id,
                    job=job,
                    audit_event_id=(
                        min(event.event_id for event in command.audit_events)
                        if command.audit_events
                        else None
                    ),
                )
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class NewDocumentUploadJourneyCommand:
    """Run byte-plane work outside SQL, then one terminal owner transaction."""

    artifact_adapter: PostgresArtifactStorageAdapter
    boundary_command: NewDocumentUploadRequestBoundaryCommand
    terminal_command: NewDocumentUploadCommand
    dispatch_accepted_job: DispatchAcceptedJob

    def execute(self, request: NewDocumentUploadJourneyInput) -> NewDocumentUploadResult:
        facts = request.request
        boundary = self.boundary_command.execute(
            actor_type=facts.actor_type,
            actor_id=facts.created_by,
            presented_browser_session_token=facts.presented_browser_session_token,
            scope_type=facts.document.scope_type or "",
            scope_id=facts.document.scope_id or "",
            tag_scopes=tuple(
                (tag.tag_type, tag.tag_id) for tag in facts.tags
            ),
            media_type=facts.media_type,
            document_id=facts.document.document_id,
            document_version_id=facts.version.document_version_id,
            job_kind=facts.job_kind,
            progress_total=facts.progress_total,
        )
        decision_ids = sorted(
            decision.decision_id for decision in boundary.authorization_decisions
        )
        audit_events = tuple(
            dataclass_replace(
                event,
                metadata={
                    **event.metadata,
                    "access_decision_ids": decision_ids,
                    "operation": facts.idempotency_scope,
                    "request_id": facts.idempotency_key,
                },
            )
            for event in facts.audit_events
        )
        terminal = NewDocumentUploadTerminalFacts(
            media_type=facts.media_type,
            document=facts.document,
            version=facts.version,
            tags=facts.tags,
            job_kind=facts.job_kind,
            idempotency_scope=facts.idempotency_scope,
            idempotency_key=facts.idempotency_key,
            created_by=facts.created_by,
            audit_events=audit_events,
            execution_snapshot=boundary.execution_snapshot,
            authorization_decisions=boundary.authorization_decisions,
            progress_total=facts.progress_total,
        )
        plan = request.artifact.plan
        begin = self.artifact_adapter.begin_write_command.execute(plan.begin)
        if begin.replayed:
            if begin.continue_external_work:
                raise DocumentUploadReplayConflict(
                    "an earlier live request still owns this upload identity"
                )
            if not begin.canonical_id:
                raise DocumentUploadReplayConflict(
                    "artifact replay did not identify its canonical attempt"
                )
            return self.terminal_command.canonical_result(
                terminal=terminal,
                artifact_journey=request.artifact,
                canonical_attempt_id=begin.canonical_id,
            )
        expected_attempt: ArtifactWriteAttemptRecord = request.artifact.attempt
        expected_lease: StorageRequestLeaseRecord = request.artifact.lease
        heartbeat_started = monotonic()

        def heartbeat(_cumulative_bytes: int) -> None:
            nonlocal expected_attempt, expected_lease, heartbeat_started
            if monotonic() - heartbeat_started < 30:
                return
            now = datetime.now(timezone.utc)
            observed_at = now.isoformat()
            expires_at = (now + timedelta(seconds=90)).isoformat()
            next_attempt = dataclass_replace(
                expected_attempt,
                lease_expires_at=expires_at,
                last_heartbeat_at=observed_at,
                updated_at=observed_at,
            )
            next_lease = dataclass_replace(
                expected_lease,
                expires_at=expires_at,
                last_heartbeat_at=observed_at,
            )
            self.artifact_adapter.heartbeat_write(
                HeartbeatArtifactWriteInput(
                    expected_attempt,
                    expected_lease,
                    next_attempt,
                    next_lease,
                    observed_at,
                )
            )
            expected_attempt, expected_lease = next_attempt, next_lease
            heartbeat_started = monotonic()

        temp_name = plan.begin.attempt.opaque_temp_name
        filesystem = self.artifact_adapter.filesystem
        try:
            size, digest = filesystem.write_temp(
                temp_name,
                plan.chunks,
                max_bytes=plan.max_bytes,
                progress_callback=heartbeat,
            )
            finalized = plan.finalize(
                size,
                digest,
                expected_attempt,
                expected_lease,
                datetime.now(timezone.utc).isoformat(),
            )
            filesystem.publish_no_overwrite(temp_name, finalized.blob.opaque_ref)
            filesystem.verify_full(
                finalized.blob.opaque_ref,
                expected_size=size,
                expected_sha256=digest,
            )
            publication = ArtifactUploadJourneyBuilder.caller_session_publication(
                finalized,
                verified_tag_scopes=frozenset(
                    (tag.tag_type, tag.tag_id) for tag in terminal.tags
                ),
            )
            result = self.terminal_command.execute(
                NewDocumentUploadInput(
                    media_type=terminal.media_type,
                    document=terminal.document,
                    version=terminal.version,
                    tags=terminal.tags,
                    artifact_publication=publication,
                    job_kind=terminal.job_kind,
                    idempotency_scope=terminal.idempotency_scope,
                    idempotency_key=terminal.idempotency_key,
                    created_by=terminal.created_by,
                    audit_events=terminal.audit_events,
                    execution_snapshot=terminal.execution_snapshot,
                    authorization_decisions=terminal.authorization_decisions,
                    progress_total=terminal.progress_total,
                )
            )
        except Exception:
            try:
                filesystem.remove_temp(temp_name)
            except Exception:
                pass
            raise
        if result.job is not None:
            try:
                self.dispatch_accepted_job(result.job)
            except Exception:
                # Durable outbox delivery owns recovery after the acceptance commit.
                pass
        return result


__all__ = [
    "NewDocumentUploadBoundaryFacts",
    "NewDocumentUploadCommand",
    "NewDocumentUploadInput",
    "NewDocumentUploadJourneyCommand",
    "NewDocumentUploadJourneyInput",
    "NewDocumentUploadResult",
    "NewDocumentUploadRequestBoundaryCommand",
    "NewDocumentUploadRequestFacts",
    "NewDocumentUploadTerminalFacts",
]
