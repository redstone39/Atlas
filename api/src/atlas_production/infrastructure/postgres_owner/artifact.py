from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import artifact_storage as rows
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasSessionRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.retrieval_currentness import (
    read_effective_document_scope_with_team_ids,
)
from atlas_production.infrastructure.postgres_locks import (
    acquire_mixed_owner_locks,
    acquire_owner_locks,
)
from atlas_production.infrastructure.postgres_owner.audit import (
    AccessDecisionWriter,
    AuditEventWriter,
)
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_owner_key,
    team_subject_owner_key,
)
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
)
from atlas_production.modules.identity_access.records import AccessDecisionRecord
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentVersionRecord,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]


class ArtifactCommandConflict(RuntimeError):
    """A named artifact command observed a stale or cross-wired preimage."""


class ArtifactProtectedOpenDenied(RuntimeError):
    """Protected output was withheld after denial evidence committed."""


class ArtifactProtectedOpenUnauthenticated(RuntimeError):
    """The presented browser session stopped authorizing its expected actor."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    replayed: bool = False
    canonical_id: str | None = None
    continue_external_work: bool = True


@dataclass(frozen=True, slots=True)
class TargetControlInput:
    expected_control: StorageControlRecord | None
    expected_committed_blobs: tuple[StorageBlobRecord, ...]
    target: StorageTargetRecord
    control: StorageControlRecord
    operation: ArtifactOperationRecord
    audit_events: tuple[AuditEventRecord, ...]
    observed_at: str
    generation_prefix: str | None = None
    monotonic_generation: int | None = None


@dataclass(frozen=True, slots=True)
class BeginArtifactWriteInput:
    attempt: ArtifactWriteAttemptRecord
    lease: StorageRequestLeaseRecord
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class HeartbeatArtifactWriteInput:
    expected_attempt: ArtifactWriteAttemptRecord
    expected_lease: StorageRequestLeaseRecord
    attempt: ArtifactWriteAttemptRecord
    lease: StorageRequestLeaseRecord
    observed_at: str


@dataclass(frozen=True, slots=True)
class DocumentParentCurrentness:
    document_id: str
    lifecycle_status: str
    resource_lifecycle_epoch: int
    active_processing_generation: int


@dataclass(frozen=True, slots=True)
class FinalizeArtifactWriteInput:
    expected_attempt: ArtifactWriteAttemptRecord
    expected_lease: StorageRequestLeaseRecord
    expected_parent: DocumentParentCurrentness | None
    attempt: ArtifactWriteAttemptRecord
    blob: StorageBlobRecord
    artifact: ArtifactRecord
    bindings: tuple[ArtifactScopeBindingRecord, ...]
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class ConversationArtifactPublication:
    """One bounded, conversation-owned metadata publication."""

    conversation_id: str
    fence: StorageFence
    expected_attempts: tuple[ArtifactWriteAttemptRecord | None, ...]
    attempts: tuple[ArtifactWriteAttemptRecord, ...]
    expected_blobs: tuple[StorageBlobRecord | None, ...]
    blobs: tuple[StorageBlobRecord, ...]
    expected_artifacts: tuple[ArtifactRecord | None, ...]
    artifacts: tuple[ArtifactRecord, ...]
    expected_bindings: tuple[ArtifactScopeBindingRecord | None, ...]
    bindings: tuple[ArtifactScopeBindingRecord, ...]
    expected_leases: tuple[StorageRequestLeaseRecord | None, ...] = ()
    leases: tuple[StorageRequestLeaseRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class NewDocumentOriginalArtifactPublication:
    """One terminal original-artifact graph staged in its caller's Session."""

    fence: StorageFence
    expected_attempt: ArtifactWriteAttemptRecord
    expected_lease: StorageRequestLeaseRecord
    attempt: ArtifactWriteAttemptRecord
    blob: StorageBlobRecord
    artifact: ArtifactRecord
    bindings: tuple[ArtifactScopeBindingRecord, ...]
    verified_tag_scopes: frozenset[tuple[str, str]]
    reuse_committed_blob: bool = False


@dataclass(frozen=True, slots=True)
class ProtectedArtifactOpenInput:
    expected_document: DocumentRecord
    expected_version: DocumentVersionRecord
    expected_tag_refs: frozenset[tuple[str, str]]
    expected_artifact: ArtifactRecord
    expected_blob: StorageBlobRecord
    actor_type: str
    actor_id: str
    presented_browser_session_token: str
    action: str
    record_success_evidence: bool
    candidate_scope: frozenset[tuple[str, str]]
    candidate_team_ids: frozenset[str]
    access_decision: AccessDecisionRecord | None
    audit_events: tuple[AuditEventRecord, ...]
    observed_at: str
    read_lease: StorageRequestLeaseRecord


@dataclass(frozen=True, slots=True)
class PostCommitArtifactOpener:
    artifact_id: str
    blob_id: str
    opaque_ref: str
    byte_size: int
    checksum_sha256: str
    content_type: str
    read_lease: StorageRequestLeaseRecord


@dataclass(frozen=True, slots=True)
class HeartbeatArtifactReadInput:
    expected_lease: StorageRequestLeaseRecord
    lease: StorageRequestLeaseRecord


@dataclass(frozen=True, slots=True)
class CompleteArtifactReadInput:
    expected_lease: StorageRequestLeaseRecord


@dataclass(frozen=True, slots=True)
class ClaimArtifactReconciliationInput:
    expected_finding: StorageReconciliationFindingRecord
    lease: StorageRequestLeaseRecord
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationClaim:
    finding_id: str
    lease_id: str
    attempt_generation: int


@dataclass(frozen=True, slots=True)
class FinalizeArtifactReconciliationInput:
    expected_finding: StorageReconciliationFindingRecord
    finding: StorageReconciliationFindingRecord
    expected_lease: StorageRequestLeaseRecord
    expected_attempt: ArtifactWriteAttemptRecord | None
    attempt: ArtifactWriteAttemptRecord | None
    expected_blob: StorageBlobRecord | None
    blob: StorageBlobRecord | None
    audit_events: tuple[AuditEventRecord, ...]


def _record_payload(record: object) -> dict[str, Any]:
    payload = asdict(record)
    fence = payload.pop("fence", None)
    if fence is not None:
        payload.update(fence)
    if "intent" in payload:
        payload["intent_json"] = payload.pop("intent")
    if "metadata" in payload:
        payload["metadata_json"] = payload.pop("metadata")
    return payload


def _row(record: object, row_type: type[Any]) -> Any:
    return row_type(**_record_payload(record))


def _matches(row: object | None, record: object | None) -> bool:
    if row is None or record is None:
        return row is None and record is None
    return all(
        getattr(row, name) == value
        for name, value in _record_payload(record).items()
    )


def _replace(row: object, record: object) -> None:
    for name, value in _record_payload(record).items():
        setattr(row, name, value)


def _require_immutable_fields(
    expected: object,
    updated: object,
    *,
    fields: tuple[str, ...],
    label: str,
) -> None:
    if any(getattr(expected, name) != getattr(updated, name) for name in fields):
        raise ValueError(f"{label} immutable authority cannot move")


_RECONCILIATION_ATTEMPT_TRANSITIONS = {
    "receiving": frozenset({"receiving", "failed", "quarantined"}),
    "bytes_verified": frozenset({"bytes_verified", "failed", "quarantined"}),
    "reserved": frozenset({"reserved", "failed", "quarantined"}),
    "published": frozenset({"published", "succeeded", "failed", "quarantined"}),
    "succeeded": frozenset({"succeeded"}),
    "failed": frozenset({"failed"}),
    "quarantined": frozenset({"quarantined"}),
}

_RECONCILIATION_BLOB_TRANSITIONS = {
    "pending": frozenset({"pending", "failed", "quarantined"}),
    "committed": frozenset({"committed", "quarantined"}),
    "failed": frozenset({"failed", "pending"}),
    "quarantined": frozenset({"quarantined", "pending"}),
}


def _require_reconciliation_transition(
    expected_status: str,
    updated_status: str,
    *,
    allowed: dict[str, frozenset[str]],
    label: str,
) -> None:
    if updated_status not in allowed.get(expected_status, frozenset()):
        raise ValueError(
            f"{label} status transition is not allowed during reconciliation"
        )


def _require_reconciliation_attempt_blob_result(
    attempt: ArtifactWriteAttemptRecord,
    blob: StorageBlobRecord | None,
) -> None:
    result_fields_present = any(
        value is not None
        for value in (
            attempt.blob_id,
            attempt.byte_size,
            attempt.checksum_sha256,
        )
    )
    result_required = attempt.status in {"reserved", "published", "succeeded"}
    if result_required or result_fields_present:
        if blob is None:
            raise ValueError(
                "reconciliation attempt result requires an authoritative blob"
            )
        if (
            attempt.blob_id != blob.blob_id
            or attempt.byte_size != blob.byte_size
            or attempt.checksum_sha256 != blob.checksum_value
        ):
            raise ValueError(
                "reconciliation attempt result does not match its authoritative blob"
            )


def _fence_tuple(fence: StorageFence) -> tuple[str, int, str, int]:
    return (
        fence.target_id,
        fence.target_revision,
        fence.root_identity_digest,
        fence.storage_epoch,
    )


def _control_fence(row: rows.AtlasArtifactStorageControlRow) -> tuple[str, int, str, int] | None:
    if (
        row.mode != "active"
        or row.active_target_id is None
        or row.active_target_revision is None
        or row.root_identity_digest is None
    ):
        return None
    return (
        row.active_target_id,
        row.active_target_revision,
        row.root_identity_digest,
        row.storage_epoch,
    )


def _require_audit(events: tuple[AuditEventRecord, ...]) -> None:
    if not events or any(type(event) is not AuditEventRecord for event in events):
        raise ValueError("artifact command requires complete audit events")
    ids = tuple(event.event_id for event in events)
    if len(ids) != len(set(ids)):
        raise ValueError("artifact audit event ids must be unique")


def _evidence_keys(
    events: tuple[AuditEventRecord, ...],
    decisions: tuple[AccessDecisionRecord, ...] = (),
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *(f"audit:event:{event.event_id}" for event in events),
                *(f"audit:decision:{decision.decision_id}" for decision in decisions),
            }
        )
    )


def _active_control(session: Session, fence: StorageFence) -> None:
    control = session.get(
        rows.AtlasArtifactStorageControlRow,
        "global",
        populate_existing=True,
    )
    if control is None or _control_fence(control) != _fence_tuple(fence):
        raise ArtifactCommandConflict("artifact storage fence is stale")


_WRITE_HEARTBEAT_EXTENSION_SECONDS = 90


def _timestamp(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


_CONVERSATION_PUBLICATION_LIMIT = 64
_CONVERSATION_ARTIFACT_CLASSES = frozenset(
    {
        "conversation_turn_input",
        "conversation_turn_answer",
        "protected_model_payload",
        "conversation_summary",
        "evidence_pack",
    }
)
_CONVERSATION_LINEAGE_FIELDS = (
    "document_version_id", "source_artifact_id", "processing_generation",
    "pipeline_id", "pipeline_version", "generation", "page_number", "block_id",
    "acl_policy_version", "acl_action",
)


def _require_conversation_publication(request: ConversationArtifactPublication) -> None:
    families = (
        (request.expected_attempts, request.attempts),
        (request.expected_blobs, request.blobs),
        (request.expected_artifacts, request.artifacts),
        (request.expected_bindings, request.bindings),
        (request.expected_leases, request.leases),
    )
    if not request.conversation_id:
        raise ValueError("conversation artifact publication requires an owner")
    typed_families = (
        (request.expected_attempts, request.attempts, ArtifactWriteAttemptRecord),
        (request.expected_blobs, request.blobs, StorageBlobRecord),
        (request.expected_artifacts, request.artifacts, ArtifactRecord),
        (request.expected_bindings, request.bindings, ArtifactScopeBindingRecord),
        (request.expected_leases, request.leases, StorageRequestLeaseRecord),
    )
    if any(
        type(item) is not expected_type
        for _preimages, candidates, expected_type in typed_families
        for item in candidates
    ) or any(
        item is not None and type(item) is not expected_type
        for preimages, _candidates, expected_type in typed_families
        for item in preimages
    ):
        raise TypeError("conversation artifact publication record type is not allowed")
    if any(
        len(expected) != len(candidate)
        or len(candidate) > _CONVERSATION_PUBLICATION_LIMIT
        for expected, candidate in families
    ):
        raise ValueError("conversation artifact publication is not bounded")
    graph_size = len(request.attempts)
    if graph_size == 0 or any(
        len(items) != graph_size
        for items in (request.blobs, request.artifacts, request.bindings)
    ):
        raise ValueError("conversation artifact publication graph is incomplete")

    identity_groups = (
        ({item.write_attempt_id for item in request.attempts}, request.attempts),
        ({item.blob_id for item in request.blobs}, request.blobs),
        ({item.artifact_id for item in request.artifacts}, request.artifacts),
        ({item.binding_id for item in request.bindings}, request.bindings),
        ({item.lease_id for item in request.leases}, request.leases),
    )
    if any(len(identities) != len(items) for identities, items in identity_groups):
        raise ValueError("conversation artifact publication identities must be unique")
    if len({(item.idempotency_scope, item.idempotency_key) for item in request.attempts}) != graph_size:
        raise ValueError("conversation artifact publication idempotency is ambiguous")

    attempts_by_id = {item.write_attempt_id: item for item in request.attempts}
    blobs_by_id = {item.blob_id: item for item in request.blobs}
    artifacts_by_blob = {item.blob_id: item for item in request.artifacts}
    bindings_by_artifact = {item.artifact_id: item for item in request.bindings}
    artifact_ids = {item.artifact_id for item in request.artifacts}
    if (
        {item.blob_id for item in request.attempts} != set(blobs_by_id)
        or {item.write_attempt_id for item in request.blobs} != set(attempts_by_id)
        or set(artifacts_by_blob) != set(blobs_by_id)
        or set(bindings_by_artifact) != artifact_ids
    ):
        raise ValueError("conversation artifact publication graph is cross-wired")

    for artifact in request.artifacts:
        blob = blobs_by_id[artifact.blob_id]
        assert blob.write_attempt_id is not None
        attempt = attempts_by_id[blob.write_attempt_id]
        binding = bindings_by_artifact[artifact.artifact_id]
        intent = attempt.intent
        if (
            attempt.status != "succeeded"
            or blob.status != "committed"
            or artifact.lifecycle_status != "active"
            or artifact.artifact_class not in _CONVERSATION_ARTIFACT_CLASSES
            or attempt.fence != request.fence
            or blob.fence != request.fence
            or attempt.idempotency_scope != "conversation_payload"
            or attempt.idempotency_key != artifact.logical_identity
            or attempt.request_fingerprint != artifact.checksum_value
            or attempt.parent_resource_id != request.conversation_id
            or attempt.parent_lifecycle_epoch != 0
            or attempt.blob_id != blob.blob_id
            or attempt.byte_size != blob.byte_size
            or attempt.checksum_sha256 != blob.checksum_value
            or artifact.owner_scope_type != "conversation"
            or artifact.owner_scope_id != request.conversation_id
            or artifact.parent_resource_id != request.conversation_id
            or artifact.parent_lifecycle_epoch != 0
            or artifact.checksum_algorithm != blob.checksum_algorithm
            or artifact.checksum_value != blob.checksum_value
            or artifact.byte_size != blob.byte_size
            or artifact.content_type != blob.content_type
            or binding.binding_kind != "owner"
            or binding.scope_type != "conversation"
            or binding.scope_id != request.conversation_id
            or intent.get("artifact_class") != artifact.artifact_class
            or intent.get("logical_identity") != artifact.logical_identity
            or intent.get("content_type") != artifact.content_type
            or intent.get("owner_scope_type") != "conversation"
            or intent.get("owner_scope_id") != request.conversation_id
            or intent.get("authorization_bindings", [])
        ):
            raise ValueError("conversation artifact publication lineage is invalid")
        if any(
            getattr(artifact, field_name) is not None
            or intent.get(field_name) is not None
            for field_name in _CONVERSATION_LINEAGE_FIELDS
        ):
            raise ValueError("conversation artifact publication has foreign lineage")

    for lease in request.leases:
        if (
            lease.request_kind != "artifact_write"
            or lease.fence != request.fence
            or lease.parent_resource_id != request.conversation_id
            or lease.parent_lifecycle_epoch != 0
        ):
            raise ValueError("conversation artifact publication lease is foreign")


def _require_conversation_preimages(request: ConversationArtifactPublication) -> None:
    for expected, candidate in zip(request.expected_attempts, request.attempts, strict=True):
        if expected is not None:
            _require_immutable_fields(
                expected, candidate,
                fields=(
                    "write_attempt_id", "idempotency_scope", "idempotency_key",
                    "request_fingerprint", "fence", "parent_resource_id",
                    "parent_lifecycle_epoch", "lease_owner", "attempt_generation",
                    "opaque_temp_name", "created_at", "intent",
                ),
                label="conversation artifact attempt",
            )
            if expected.status not in {"receiving", "bytes_verified", "reserved", "published", "succeeded"}:
                raise ValueError("conversation artifact attempt is not publishable")
    for expected, candidate in zip(request.expected_blobs, request.blobs, strict=True):
        if expected is not None:
            _require_immutable_fields(
                expected, candidate,
                fields=(
                    "blob_id", "opaque_ref", "dedup_mode", "dedup_scope_type",
                    "dedup_scope_id", "checksum_algorithm", "fence", "created_at",
                    "write_attempt_id",
                ),
                label="conversation artifact blob",
            )
            if expected.status not in {"pending", "committed"}:
                raise ValueError("conversation artifact blob is not publishable")
    for expected, candidate in zip(request.expected_artifacts, request.artifacts, strict=True):
        if expected is not None:
            _require_immutable_fields(
                expected, candidate,
                fields=(
                    "artifact_id", "artifact_class", "blob_id", "checksum_algorithm",
                    "owner_scope_type", "owner_scope_id", "created_at", "logical_identity",
                    "source_artifact_id", "document_version_id", "parent_resource_id",
                    "parent_lifecycle_epoch", "processing_generation", "pipeline_id",
                    "pipeline_version", "generation", "page_number", "block_id",
                    "acl_policy_version", "acl_action",
                ),
                label="conversation artifact",
            )
            if expected.lifecycle_status not in {"staged", "active"}:
                raise ValueError("conversation artifact is not publishable")
    for expected, candidate in zip(request.expected_bindings, request.bindings, strict=True):
        if expected is not None and expected != candidate:
            raise ValueError("conversation artifact binding identity cannot move")
    for expected, candidate in zip(request.expected_leases, request.leases, strict=True):
        if expected is not None:
            _require_immutable_fields(
                expected, candidate,
                fields=(
                    "lease_id", "request_kind", "owner", "fence", "acquired_at",
                    "attempt_generation", "parent_resource_id", "parent_lifecycle_epoch",
                ),
                label="conversation artifact lease",
            )


@dataclass(frozen=True, slots=True)
class ConversationArtifactPublicationWriter:
    """Closed metadata writer; its coordinator retains transaction authority."""

    _session: Session

    def publish_conversation_metadata(
        self, request: ConversationArtifactPublication
    ) -> CommandResult:
        _require_conversation_publication(request)
        _require_conversation_preimages(request)
        identity_keys = (
            *(f"artifact:attempt:{item.write_attempt_id}" for item in request.attempts),
            *(
                f"artifact:idempotency:{item.idempotency_scope}:{item.idempotency_key}"
                for item in request.attempts
            ),
            *(f"artifact:blob:{item.blob_id}" for item in request.blobs),
            *(f"artifact:artifact:{item.artifact_id}" for item in request.artifacts),
            *(
                f"artifact:logical:{item.artifact_class}:{item.logical_identity}"
                for item in request.artifacts
            ),
            *(f"artifact:binding:{item.binding_id}" for item in request.bindings),
            *(f"artifact:lease:{item.lease_id}" for item in request.leases),
        )
        acquire_mixed_owner_locks(
            self._session,
            shared_domain_keys=("artifact:control",),
            exclusive_identity_keys=identity_keys,
        )
        _active_control(self._session, request.fence)
        for attempt in request.attempts:
            alternate = self._session.execute(
                select(rows.AtlasArtifactWriteAttemptRow)
                .where(
                    rows.AtlasArtifactWriteAttemptRow.idempotency_scope
                    == attempt.idempotency_scope,
                    rows.AtlasArtifactWriteAttemptRow.idempotency_key
                    == attempt.idempotency_key,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if (
                alternate is not None
                and alternate.write_attempt_id != attempt.write_attempt_id
            ):
                raise ArtifactCommandConflict(
                    "conversation artifact attempt idempotency identity is owned elsewhere"
                )
        for artifact in request.artifacts:
            alternate = self._session.execute(
                select(rows.AtlasArtifactRow)
                .where(
                    rows.AtlasArtifactRow.artifact_class
                    == artifact.artifact_class,
                    rows.AtlasArtifactRow.logical_identity
                    == artifact.logical_identity,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if alternate is not None and alternate.artifact_id != artifact.artifact_id:
                raise ArtifactCommandConflict(
                    "conversation artifact logical identity is owned elsewhere"
                )
        for binding in request.bindings:
            alternate = self._session.execute(
                select(rows.AtlasArtifactScopeBindingRow)
                .where(
                    rows.AtlasArtifactScopeBindingRow.artifact_id
                    == binding.artifact_id,
                    rows.AtlasArtifactScopeBindingRow.binding_kind == "owner",
                )
                .with_for_update()
            ).scalar_one_or_none()
            if alternate is not None and alternate.binding_id != binding.binding_id:
                raise ArtifactCommandConflict(
                    "conversation artifact owner binding is owned elsewhere"
                )
        typed_families = (
            (
                rows.AtlasArtifactWriteAttemptRow,
                tuple(item.write_attempt_id for item in request.attempts),
                request.expected_attempts,
                request.attempts,
            ),
            (
                rows.AtlasStorageBlobRow,
                tuple(item.blob_id for item in request.blobs),
                request.expected_blobs,
                request.blobs,
            ),
            (
                rows.AtlasArtifactRow,
                tuple(item.artifact_id for item in request.artifacts),
                request.expected_artifacts,
                request.artifacts,
            ),
            (
                rows.AtlasArtifactScopeBindingRow,
                tuple(item.binding_id for item in request.bindings),
                request.expected_bindings,
                request.bindings,
            ),
            (
                rows.AtlasStorageRequestLeaseRow,
                tuple(item.lease_id for item in request.leases),
                request.expected_leases,
                request.leases,
            ),
        )
        locked = tuple(
            (
                row_type,
                current,
                expected,
                candidate,
            )
            for row_type, identities, expected_items, candidates in typed_families
            for identity, expected, candidate in zip(
                identities, expected_items, candidates, strict=True
            )
            for current in (
                self._session.get(
                    row_type,
                    identity,
                    with_for_update=True,
                    populate_existing=True,
                ),
            )
        )
        exact = tuple(_matches(current, candidate) for _, current, _, candidate in locked)
        if all(exact):
            return CommandResult(replayed=True, continue_external_work=False)
        if any(exact):
            raise ArtifactCommandConflict(
                "conversation artifact publication is only partially replayed"
            )
        if any(not _matches(current, expected) for _, current, expected, _ in locked):
            raise ArtifactCommandConflict(
                "conversation artifact publication preimage changed"
            )
        # These persistence mappings intentionally do not expose ORM
        # relationships.  Flush each FK dependency family in declaration order
        # (attempt -> blob -> artifact -> binding -> lease) so SQLAlchemy cannot
        # insert an artifact before its newly committed blob.
        for (
            family_row_type,
            _identities,
            _expected_items,
            _candidates,
        ) in typed_families:
            for row_type, current, _expected, candidate in locked:
                if row_type is not family_row_type:
                    continue
                if current is None:
                    self._session.add(_row(candidate, row_type))
                else:
                    _replace(current, candidate)
            self._session.flush()
        return CommandResult()


_NEW_DOCUMENT_ORIGINAL_CLASSES = frozenset({"original_document"})
_NEW_DOCUMENT_ORIGINAL_LINEAGE_FIELDS = (
    "artifact_class",
    "logical_identity",
    "content_type",
    "owner_scope_type",
    "owner_scope_id",
    "document_version_id",
    "source_artifact_id",
    "processing_generation",
    "pipeline_id",
    "pipeline_version",
    "generation",
    "page_number",
    "block_id",
    "acl_policy_version",
    "acl_action",
)


def _artifact_unique_lock_identities(
    blob: StorageBlobRecord,
    artifact: ArtifactRecord,
) -> tuple[str, ...]:
    identities = {
        f"artifact:blob-opaque:{blob.opaque_ref}",
        f"artifact:logical:{artifact.artifact_class}:{artifact.logical_identity}",
    }
    if blob.dedup_mode == "original":
        identities.add(
            "artifact:original-dedup:"
            + json.dumps(
                [
                    blob.dedup_scope_type,
                    blob.dedup_scope_id,
                    blob.checksum_algorithm,
                    blob.checksum_value,
                    blob.byte_size,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if (
        artifact.artifact_class
        in {"original_document", "original_inline_source"}
        and artifact.document_version_id is not None
    ):
        identities.add(
            "artifact:canonical-original:"
            f"{artifact.document_version_id}"
        )
    return tuple(sorted(identities))


def _require_artifact_unique_owners(
    session: Session,
    *,
    blob: StorageBlobRecord,
    artifact: ArtifactRecord,
) -> None:
    alternate_artifact_logical = session.execute(
        select(rows.AtlasArtifactRow)
        .where(
            rows.AtlasArtifactRow.artifact_class == artifact.artifact_class,
            rows.AtlasArtifactRow.logical_identity == artifact.logical_identity,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        alternate_artifact_logical is not None
        and alternate_artifact_logical.artifact_id != artifact.artifact_id
    ):
        raise ArtifactCommandConflict(
            "artifact logical identity is owned elsewhere"
        )
    alternate_blob_opaque = session.execute(
        select(rows.AtlasStorageBlobRow)
        .where(rows.AtlasStorageBlobRow.opaque_ref == blob.opaque_ref)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        alternate_blob_opaque is not None
        and alternate_blob_opaque.blob_id != blob.blob_id
    ):
        raise ArtifactCommandConflict(
            "artifact blob opaque identity is owned elsewhere"
        )
    if blob.dedup_mode == "original":
        alternate_blob_dedup = session.execute(
            select(rows.AtlasStorageBlobRow)
            .where(
                rows.AtlasStorageBlobRow.dedup_mode == "original",
                rows.AtlasStorageBlobRow.dedup_scope_type
                == blob.dedup_scope_type,
                rows.AtlasStorageBlobRow.dedup_scope_id
                == blob.dedup_scope_id,
                rows.AtlasStorageBlobRow.checksum_algorithm
                == blob.checksum_algorithm,
                rows.AtlasStorageBlobRow.checksum_value == blob.checksum_value,
                rows.AtlasStorageBlobRow.byte_size == blob.byte_size,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            alternate_blob_dedup is not None
            and alternate_blob_dedup.blob_id != blob.blob_id
        ):
            raise ArtifactCommandConflict(
                "artifact original dedup identity is owned elsewhere"
            )
    if (
        artifact.artifact_class
        in {"original_document", "original_inline_source"}
        and artifact.document_version_id is not None
    ):
        alternate_canonical_original = session.execute(
            select(rows.AtlasArtifactRow)
            .where(
                rows.AtlasArtifactRow.document_version_id
                == artifact.document_version_id,
                rows.AtlasArtifactRow.artifact_class.in_(
                    ("original_document", "original_inline_source")
                ),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            alternate_canonical_original is not None
            and alternate_canonical_original.artifact_id
            != artifact.artifact_id
        ):
            raise ArtifactCommandConflict(
                "artifact canonical original identity is owned elsewhere"
            )


def new_document_original_artifact_lock_identities(
    request: NewDocumentOriginalArtifactPublication,
) -> tuple[str, ...]:
    """Canonical identities a caller may include in its complete lock plan."""

    return tuple(
        sorted(
            {
                f"artifact:attempt:{request.attempt.write_attempt_id}",
                (
                    "artifact:idempotency:"
                    f"{request.attempt.idempotency_scope}:"
                    f"{request.attempt.idempotency_key}"
                ),
                f"artifact:lease:{request.expected_lease.lease_id}",
                f"artifact:parent:{request.attempt.parent_resource_id}",
                f"artifact:blob:{request.blob.blob_id}",
                f"artifact:artifact:{request.artifact.artifact_id}",
                *_artifact_unique_lock_identities(
                    request.blob,
                    request.artifact,
                ),
                *(
                    f"artifact:binding:{binding.binding_id}"
                    for binding in request.bindings
                ),
                *(
                    project_owner_key(scope_id)
                    if scope_type == "project"
                    else team_owner_key(scope_id)
                    for scope_type, scope_id in request.verified_tag_scopes
                ),
            }
        )
    )


def _require_new_document_original_publication(
    request: NewDocumentOriginalArtifactPublication,
) -> None:
    expected = request.expected_attempt
    attempt = request.attempt
    blob = request.blob
    artifact = request.artifact
    if any(
        type(item) is not expected_type
        for item, expected_type in (
            (expected, ArtifactWriteAttemptRecord),
            (request.expected_lease, StorageRequestLeaseRecord),
            (attempt, ArtifactWriteAttemptRecord),
            (blob, StorageBlobRecord),
            (artifact, ArtifactRecord),
        )
    ) or any(
        type(item) is not ArtifactScopeBindingRecord for item in request.bindings
    ) or type(request.reuse_committed_blob) is not bool:
        raise TypeError("new-document original publication record type is not allowed")
    _require_immutable_fields(
        expected,
        attempt,
        fields=(
            "write_attempt_id",
            "idempotency_scope",
            "idempotency_key",
            "request_fingerprint",
            "fence",
            "parent_resource_id",
            "parent_lifecycle_epoch",
            "lease_owner",
            "attempt_generation",
            "opaque_temp_name",
            "created_at",
            "intent",
        ),
        label="new-document original attempt",
    )
    lease = request.expected_lease
    if (
        expected.status not in {"receiving", "bytes_verified", "reserved", "published"}
        or attempt.status != "succeeded"
        or blob.status != "committed"
        or blob.dedup_mode != "original"
        or blob.dedup_scope_type != artifact.owner_scope_type
        or blob.dedup_scope_id != artifact.owner_scope_id
        or artifact.lifecycle_status != "active"
        or artifact.artifact_class not in _NEW_DOCUMENT_ORIGINAL_CLASSES
        or artifact.owner_scope_type not in {"team", "project"}
        or not artifact.owner_scope_id
        or expected.fence != request.fence
        or attempt.fence != request.fence
        or blob.fence != request.fence
        or lease.request_kind != "artifact_write"
        or lease.owner != expected.lease_owner
        or lease.fence != request.fence
        or lease.parent_resource_id != expected.parent_resource_id
        or lease.parent_lifecycle_epoch != expected.parent_lifecycle_epoch
        or lease.attempt_generation != expected.attempt_generation
        or expected.lease_expires_at != lease.expires_at
        or expected.last_heartbeat_at != lease.last_heartbeat_at
        or attempt.blob_id != blob.blob_id
        or (
            not request.reuse_committed_blob
            and blob.write_attempt_id != attempt.write_attempt_id
        )
        or artifact.blob_id != blob.blob_id
        or artifact.document_version_id is None
        or artifact.parent_resource_id != attempt.parent_resource_id
        or artifact.parent_lifecycle_epoch != attempt.parent_lifecycle_epoch
        or attempt.byte_size != blob.byte_size
        or attempt.checksum_sha256 != blob.checksum_value
        or artifact.checksum_algorithm != blob.checksum_algorithm
        or artifact.checksum_value != blob.checksum_value
        or artifact.byte_size != blob.byte_size
        or artifact.content_type != blob.content_type
        or attempt.reconciliation_required_at is not None
        or blob.reconciliation_required_at is not None
        or (
            request.reuse_committed_blob
            and (
                blob.committed_at is None
                or blob.failure_code is not None
                or blob.failure_detail_summary is not None
            )
        )
    ):
        raise ValueError("new-document original publication graph is invalid")
    if (
        not request.bindings
        or len({item.binding_id for item in request.bindings}) != len(request.bindings)
        or any(item.artifact_id != artifact.artifact_id for item in request.bindings)
        or any(
            item.binding_kind not in {"owner", "authorization"}
            for item in request.bindings
        )
        or sum(item.binding_kind == "owner" for item in request.bindings) != 1
    ):
        raise ValueError("new-document original publication bindings are incomplete")
    owner_binding = next(
        item for item in request.bindings if item.binding_kind == "owner"
    )
    if (
        owner_binding.scope_type != artifact.owner_scope_type
        or owner_binding.scope_id != artifact.owner_scope_id
    ):
        raise ValueError("new-document original owner binding is cross-wired")
    intent = expected.intent
    expected_lineage = {
        field_name: getattr(artifact, field_name)
        for field_name in _NEW_DOCUMENT_ORIGINAL_LINEAGE_FIELDS
    }
    if any(intent.get(name) != value for name, value in expected_lineage.items()):
        raise ValueError("new-document original artifact differs from durable intent")
    intended_authorization = {
        tuple(item) for item in intent.get("authorization_bindings", [])
    }
    actual_authorization = {
        (item.scope_type, item.scope_id)
        for item in request.bindings
        if item.binding_kind == "authorization"
    }
    if (
        not request.verified_tag_scopes
        or any(
            scope_type not in {"team", "project"} or not scope_id
            for scope_type, scope_id in request.verified_tag_scopes
        )
        or intended_authorization != request.verified_tag_scopes
        or actual_authorization != request.verified_tag_scopes
        or sum(
            item.binding_kind == "authorization" for item in request.bindings
        )
        != len(request.verified_tag_scopes)
        or (
            artifact.owner_scope_type,
            artifact.owner_scope_id,
        )
        not in request.verified_tag_scopes
    ):
        raise ValueError("new-document original authorization bindings are incomplete")


@dataclass(frozen=True, slots=True)
class NewDocumentOriginalArtifactPublicationWriter:
    """Stages one closed artifact graph; the caller owns transaction outcome."""

    _session: Session

    def publish_new_document_original(
        self,
        request: NewDocumentOriginalArtifactPublication,
    ) -> CommandResult:
        _require_new_document_original_publication(request)
        acquire_mixed_owner_locks(
            self._session,
            shared_domain_keys=("artifact:control",),
            exclusive_identity_keys=new_document_original_artifact_lock_identities(
                request
            ),
        )
        _active_control(self._session, request.fence)
        alternate_attempt = self._session.execute(
            select(rows.AtlasArtifactWriteAttemptRow)
            .where(
                rows.AtlasArtifactWriteAttemptRow.idempotency_scope
                == request.attempt.idempotency_scope,
                rows.AtlasArtifactWriteAttemptRow.idempotency_key
                == request.attempt.idempotency_key,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            alternate_attempt is not None
            and alternate_attempt.write_attempt_id
            != request.attempt.write_attempt_id
        ):
            raise ArtifactCommandConflict(
                "new-document original idempotency identity is owned elsewhere"
            )
        _require_artifact_unique_owners(
            self._session,
            blob=request.blob,
            artifact=request.artifact,
        )
        existing_findings = tuple(
            self._session.execute(
                select(rows.AtlasStorageReconciliationFindingRow)
                .where(
                    (
                        rows.AtlasStorageReconciliationFindingRow.write_attempt_id
                        == request.attempt.write_attempt_id
                    )
                    | (
                        rows.AtlasStorageReconciliationFindingRow.blob_id
                        == request.blob.blob_id
                    )
                )
                .with_for_update()
            ).scalars()
        )
        if existing_findings:
            raise ArtifactCommandConflict(
                "new-document original graph has reconciliation findings"
            )
        persisted_bindings = tuple(
            self._session.execute(
                select(rows.AtlasArtifactScopeBindingRow)
                .where(
                    rows.AtlasArtifactScopeBindingRow.artifact_id
                    == request.artifact.artifact_id
                )
                .with_for_update()
            ).scalars()
        )
        locked = (
            (
                rows.AtlasArtifactWriteAttemptRow,
                self._session.get(
                    rows.AtlasArtifactWriteAttemptRow,
                    request.attempt.write_attempt_id,
                    with_for_update=True,
                    populate_existing=True,
                ),
                request.expected_attempt,
                request.attempt,
            ),
            (
                rows.AtlasStorageRequestLeaseRow,
                self._session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    request.expected_lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                ),
                request.expected_lease,
                None,
            ),
            (
                rows.AtlasStorageBlobRow,
                self._session.get(
                    rows.AtlasStorageBlobRow,
                    request.blob.blob_id,
                    with_for_update=True,
                    populate_existing=True,
                ),
                None,
                request.blob,
            ),
            (
                rows.AtlasArtifactRow,
                self._session.get(
                    rows.AtlasArtifactRow,
                    request.artifact.artifact_id,
                    with_for_update=True,
                    populate_existing=True,
                ),
                None,
                request.artifact,
            ),
        )
        bindings = tuple(
            (
                rows.AtlasArtifactScopeBindingRow,
                self._session.get(
                    rows.AtlasArtifactScopeBindingRow,
                    binding.binding_id,
                    with_for_update=True,
                    populate_existing=True,
                ),
                None,
                binding,
            )
            for binding in request.bindings
        )
        artifact_rows = (locked[3],) + bindings
        current_blob = locked[2][1]
        expected_blob = request.blob if request.reuse_committed_blob else None
        terminal_replay = (
            _matches(locked[0][1], request.attempt)
            and locked[1][1] is None
            and _matches(current_blob, request.blob)
            and all(_matches(current, candidate) for _, current, _, candidate in artifact_rows)
            and len(persisted_bindings) == len(request.bindings)
            and {
                item.binding_id for item in persisted_bindings
            }
            == {item.binding_id for item in request.bindings}
        )
        if terminal_replay:
            return CommandResult(replayed=True, continue_external_work=False)
        if (
            not _matches(locked[0][1], request.expected_attempt)
            or not _matches(locked[1][1], request.expected_lease)
            or not _matches(current_blob, expected_blob)
            or any(current is not None for _, current, _, _ in artifact_rows)
            or persisted_bindings
        ):
            raise ArtifactCommandConflict(
                "new-document original publication preimage changed"
            )
        assert locked[0][1] is not None and locked[1][1] is not None
        _replace(locked[0][1], request.attempt)
        self._session.delete(locked[1][1])
        if not request.reuse_committed_blob:
            self._session.add(_row(request.blob, rows.AtlasStorageBlobRow))
            # The composite artifact FK is immediate. Materialize the blob
            # inside the caller-owned transaction before staging its artifact.
            self._session.flush()
        for row_type, _current, _expected, candidate in artifact_rows:
            assert candidate is not None
            self._session.add(_row(candidate, row_type))
        return CommandResult()


def _target_row(session: Session, target_id: str, revision: int) -> object | None:
    return session.execute(
        select(rows.AtlasArtifactStorageTargetRow).where(
            rows.AtlasArtifactStorageTargetRow.target_id == target_id,
            rows.AtlasArtifactStorageTargetRow.target_revision == revision,
        )
    ).scalar_one_or_none()


def _blob_set_digest(blobs: tuple[StorageBlobRecord, ...]) -> str:
    canonical = json.dumps(
        [
            [
                item.blob_id,
                item.opaque_ref,
                item.checksum_algorithm,
                item.checksum_value,
                item.byte_size,
            ]
            for item in sorted(blobs, key=lambda item: item.blob_id)
        ],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _matches_rebound_blob(
    row: object,
    expected: StorageBlobRecord,
    *,
    fence: StorageFence,
    updated_at: str,
) -> bool:
    payload = _record_payload(expected)
    payload.update(
        {
            "target_id": fence.target_id,
            "target_revision": fence.target_revision,
            "root_identity_digest": fence.root_identity_digest,
            "storage_epoch": fence.storage_epoch,
            "updated_at": updated_at,
        }
    )
    return all(getattr(row, name) == value for name, value in payload.items())


def _linked_decisions(events: tuple[AuditEventRecord, ...]) -> set[str]:
    linked: set[str] = set()
    for event in events:
        one = event.metadata.get("access_decision_id")
        many = event.metadata.get("access_decision_ids")
        if isinstance(one, str) and one:
            linked.add(one)
        if isinstance(many, list) and all(isinstance(item, str) for item in many):
            linked.update(many)
    return linked


@dataclass(frozen=True, slots=True)
class TargetControlCommand:
    session_factory: SessionFactory

    def execute(self, request: TargetControlInput) -> CommandResult:
        _require_audit(request.audit_events)
        expected_blobs = tuple(
            sorted(request.expected_committed_blobs, key=lambda item: item.blob_id)
        )
        if len({blob.blob_id for blob in expected_blobs}) != len(expected_blobs):
            raise ValueError("target control committed blob ids must be unique")
        if request.control.control_id != "global":
            raise ValueError("target control must update the global control row")
        if request.control.active_fence() != request.operation.fence:
            raise ValueError("target control and operation fences must agree")
        if (
            request.target.target_id != request.operation.fence.target_id
            or request.target.target_revision != request.operation.fence.target_revision
            or request.target.root_identity_digest
            != request.operation.fence.root_identity_digest
        ):
            raise ValueError("target, control, and operation identities must agree")
        if (
            request.target.status != "active"
            or request.target.verification_mode != request.operation.verification_mode
            or request.target.evidence_claim != request.operation.evidence_claim
            or request.operation.committed_blob_count != len(expected_blobs)
            or request.operation.total_bytes
            != sum(blob.byte_size for blob in expected_blobs)
            or request.operation.blob_set_digest != _blob_set_digest(expected_blobs)
        ):
            raise ValueError("target operation evidence does not match committed blobs")
        if (
            request.operation.verification_mode == "full_hash"
            and request.operation.evidence_claim != "TARGET_COPY_CHECKSUM_VERIFIED"
        ) or (
            request.operation.verification_mode == "operator_accepted_unverified"
            and request.operation.evidence_claim
            != "OPERATOR_ACCEPTED_UNVERIFIED_TARGET"
        ):
            raise ValueError("target verification mode and evidence claim differ")
        expected_epoch = (
            1 if request.expected_control is None else request.expected_control.storage_epoch + 1
        )
        if request.control.storage_epoch != expected_epoch:
            raise ValueError("target control storage epoch must advance exactly once")
        if (request.generation_prefix is None) != (request.monotonic_generation is None):
            raise ValueError("target generation identity must be complete")
        if request.generation_prefix is not None:
            assert request.monotonic_generation is not None
            if (
                request.monotonic_generation < 1
                or request.target.target_id
                != f"{request.generation_prefix}{request.monotonic_generation}"
                or request.target.target_revision != request.monotonic_generation
            ):
                raise ValueError("target generation identity is invalid")
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=("artifact:control",),
                    identity_keys=(
                        f"artifact:target:{request.target.target_id}:{request.target.target_revision}",
                        f"artifact:operation:{request.operation.operation_id}",
                        *(f"artifact:blob:{blob.blob_id}" for blob in expected_blobs),
                        *_evidence_keys(request.audit_events),
                    ),
                )
                current = session.get(
                    rows.AtlasArtifactStorageControlRow,
                    "global",
                    with_for_update=True,
                    populate_existing=True,
                )
                target_rows = tuple(
                    session.execute(select(rows.AtlasArtifactStorageTargetRow)).scalars()
                )
                if request.generation_prefix is not None:
                    assert request.monotonic_generation is not None
                    persisted_generations = {
                        target.target_revision
                        for target in target_rows
                        if target.target_id
                        == f"{request.generation_prefix}{target.target_revision}"
                    }
                    active_is_requested = (
                        current is not None
                        and current.active_target_id == request.target.target_id
                        and current.active_target_revision == request.target.target_revision
                    )
                    if persisted_generations:
                        latest = max(persisted_generations)
                        if request.monotonic_generation < latest or (
                            request.monotonic_generation == latest
                            and not active_is_requested
                        ):
                            raise ArtifactCommandConflict("target generation is stale")
                operation = session.get(
                    rows.AtlasArtifactOperationRow,
                    request.operation.operation_id,
                    populate_existing=True,
                )
                if operation is None:
                    operation = session.execute(
                        select(rows.AtlasArtifactOperationRow).where(
                            rows.AtlasArtifactOperationRow.idempotency_scope
                            == request.operation.idempotency_scope,
                            rows.AtlasArtifactOperationRow.idempotency_key
                            == request.operation.idempotency_key,
                        )
                    ).scalar_one_or_none()
                if operation is not None:
                    operation_fields = (
                        "operation_type",
                        "idempotency_scope",
                        "idempotency_key",
                        "request_fingerprint",
                        "status",
                        "target_id",
                        "target_revision",
                        "root_identity_digest",
                        "storage_epoch",
                        "verification_mode",
                        "evidence_claim",
                        "committed_blob_count",
                        "total_bytes",
                        "blob_set_digest",
                    )
                    request_payload = _record_payload(request.operation)
                    if any(
                        getattr(operation, name) != request_payload[name]
                        for name in operation_fields
                    ):
                        raise ArtifactCommandConflict("target operation replay changed")
                    if not _matches(current, request.control):
                        raise ArtifactCommandConflict("target operation replay control changed")
                    persisted_target = _target_row(
                        session,
                        request.target.target_id,
                        request.target.target_revision,
                    )
                    if not _matches(persisted_target, request.target):
                        raise ArtifactCommandConflict("target operation replay target changed")
                    persisted_blobs = tuple(
                        session.execute(
                            select(rows.AtlasStorageBlobRow).where(
                                rows.AtlasStorageBlobRow.status == "committed"
                            )
                        ).scalars()
                    )
                    expected_by_id = {
                        blob.blob_id: blob for blob in expected_blobs
                    }
                    if (
                        len(persisted_blobs) != len(expected_by_id)
                        or any(
                            blob.blob_id not in expected_by_id
                            or not _matches_rebound_blob(
                                blob,
                                expected_by_id[blob.blob_id],
                                fence=request.operation.fence,
                                updated_at=request.operation.updated_at,
                            )
                            for blob in persisted_blobs
                        )
                    ):
                        raise ArtifactCommandConflict("target operation replay blob fence changed")
                    session.rollback()
                    return CommandResult(
                        replayed=True,
                        canonical_id=operation.operation_id,
                    )
                if not _matches(current, request.expected_control):
                    raise ArtifactCommandConflict("artifact control preimage changed")
                if _target_row(
                    session,
                    request.target.target_id,
                    request.target.target_revision,
                ) is not None:
                    raise ArtifactCommandConflict("target revision already exists")
                attempts = tuple(
                    session.execute(select(rows.AtlasArtifactWriteAttemptRow)).scalars()
                )
                blobs = tuple(session.execute(select(rows.AtlasStorageBlobRow)).scalars())
                findings = tuple(
                    session.execute(select(rows.AtlasStorageReconciliationFindingRow)).scalars()
                )
                leases = tuple(
                    session.execute(select(rows.AtlasStorageRequestLeaseRow)).scalars()
                )
                if any(
                    attempt.status not in {"succeeded", "failed", "quarantined"}
                    or attempt.reconciliation_required_at is not None
                    for attempt in attempts
                ) or any(blob.status == "pending" for blob in blobs):
                    raise ArtifactCommandConflict("artifact writes require reconciliation")
                if any(finding.status != "resolved" for finding in findings):
                    raise ArtifactCommandConflict("artifact findings require reconciliation")
                if any(lease.expires_at > request.observed_at for lease in leases):
                    raise ArtifactCommandConflict("artifact storage still has active leases")
                committed = tuple(
                    sorted(
                        (blob for blob in blobs if blob.status == "committed"),
                        key=lambda blob: blob.blob_id,
                    )
                )
                if (
                    len(committed) != len(expected_blobs)
                    or any(
                        not _matches(actual, expected)
                        for actual, expected in zip(committed, expected_blobs, strict=True)
                    )
                ):
                    raise ArtifactCommandConflict("committed blob set changed")
                session.add(_row(request.target, rows.AtlasArtifactStorageTargetRow))
                # The operation and control rows both reference the new target.
                # Flush its identity first because these mappings intentionally
                # have no ORM relationship that could otherwise order inserts.
                session.flush()
                session.add(_row(request.operation, rows.AtlasArtifactOperationRow))
                for target in target_rows:
                    if target.status == "active":
                        target.status = "probed"
                        target.updated_at = request.operation.updated_at
                for blob in committed:
                    blob.target_id = request.operation.fence.target_id
                    blob.target_revision = request.operation.fence.target_revision
                    blob.root_identity_digest = request.operation.fence.root_identity_digest
                    blob.storage_epoch = request.operation.fence.storage_epoch
                    blob.updated_at = request.operation.updated_at
                if current is None:
                    session.add(_row(request.control, rows.AtlasArtifactStorageControlRow))
                else:
                    _replace(current, request.control)
                AuditEventWriter(session).append_many(request.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult()


@dataclass(frozen=True, slots=True)
class BeginArtifactWriteCommand:
    session_factory: SessionFactory

    def execute(self, request: BeginArtifactWriteInput) -> CommandResult:
        _require_audit(request.audit_events)
        attempt = request.attempt
        if attempt.fence != request.lease.fence:
            raise ValueError("write attempt and lease fences must agree")
        if (
            request.lease.parent_resource_id != attempt.parent_resource_id
            or request.lease.parent_lifecycle_epoch != attempt.parent_lifecycle_epoch
            or request.lease.attempt_generation != attempt.attempt_generation
        ):
            raise ValueError("write attempt and lease lineage must agree")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:attempt:{attempt.write_attempt_id}",
                        f"artifact:idempotency:{attempt.idempotency_scope}:{attempt.idempotency_key}",
                        f"artifact:lease:{request.lease.lease_id}",
                        f"artifact:parent:{attempt.parent_resource_id}",
                        *_evidence_keys(request.audit_events),
                    ),
                )
                _active_control(session, attempt.fence)
                existing = session.get(
                    rows.AtlasArtifactWriteAttemptRow,
                    attempt.write_attempt_id,
                    populate_existing=True,
                )
                if existing is not None:
                    if not _matches(existing, attempt):
                        payload = _record_payload(attempt)
                        immutable_names = (
                            "write_attempt_id",
                            "idempotency_scope",
                            "idempotency_key",
                            "request_fingerprint",
                            "target_id",
                            "target_revision",
                            "root_identity_digest",
                            "storage_epoch",
                            "parent_resource_id",
                            "parent_lifecycle_epoch",
                            "lease_owner",
                            "attempt_generation",
                            "opaque_temp_name",
                            "created_at",
                            "intent_json",
                        )
                        if (
                            existing.status
                            not in {"succeeded", "failed", "quarantined"}
                            or any(
                                getattr(existing, name) != payload[name]
                                for name in immutable_names
                            )
                        ):
                            raise ArtifactCommandConflict("write attempt replay changed")
                        session.rollback()
                        return CommandResult(
                            replayed=True,
                            canonical_id=attempt.write_attempt_id,
                            continue_external_work=False,
                        )
                    existing_lease = session.get(
                        rows.AtlasStorageRequestLeaseRow,
                        request.lease.lease_id,
                        populate_existing=True,
                    )
                    if not _matches(existing_lease, request.lease):
                        raise ArtifactCommandConflict("write attempt replay lease changed")
                    session.rollback()
                    return CommandResult(
                        replayed=True,
                        canonical_id=attempt.write_attempt_id,
                    )
                duplicate = session.execute(
                    select(rows.AtlasArtifactWriteAttemptRow).where(
                        rows.AtlasArtifactWriteAttemptRow.idempotency_scope
                        == attempt.idempotency_scope,
                        rows.AtlasArtifactWriteAttemptRow.idempotency_key
                        == attempt.idempotency_key,
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    if duplicate.request_fingerprint != attempt.request_fingerprint:
                        raise ArtifactCommandConflict(
                            "write idempotency identity already exists"
                        )
                    if duplicate.status not in {"succeeded", "failed", "quarantined"}:
                        leases = tuple(
                            session.execute(
                                select(rows.AtlasStorageRequestLeaseRow)
                            ).scalars()
                        )
                        matching_leases = tuple(
                            lease
                            for lease in leases
                            if lease.request_kind == "artifact_write"
                            and lease.owner == duplicate.lease_owner
                            and lease.target_id == duplicate.target_id
                            and lease.target_revision == duplicate.target_revision
                            and lease.root_identity_digest
                            == duplicate.root_identity_digest
                            and lease.storage_epoch == duplicate.storage_epoch
                            and lease.parent_resource_id
                            == duplicate.parent_resource_id
                            and lease.parent_lifecycle_epoch
                            == duplicate.parent_lifecycle_epoch
                            and lease.attempt_generation
                            == duplicate.attempt_generation
                        )
                        if len(matching_leases) != 1:
                            raise ArtifactCommandConflict(
                                "write idempotency replay lease changed"
                            )
                    session.rollback()
                    return CommandResult(
                        replayed=True,
                        canonical_id=duplicate.write_attempt_id,
                        continue_external_work=False,
                    )
                if session.get(rows.AtlasStorageRequestLeaseRow, request.lease.lease_id):
                    raise ArtifactCommandConflict("write lease already exists")
                session.add(_row(attempt, rows.AtlasArtifactWriteAttemptRow))
                session.add(_row(request.lease, rows.AtlasStorageRequestLeaseRow))
                AuditEventWriter(session).append_many(request.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult()


@dataclass(frozen=True, slots=True)
class HeartbeatArtifactWriteCommand:
    session_factory: SessionFactory

    def execute(
        self,
        request: HeartbeatArtifactWriteInput,
    ) -> CommandResult:
        expected_attempt = request.expected_attempt
        expected_lease = request.expected_lease
        attempt = request.attempt
        lease = request.lease
        _require_immutable_fields(
            expected_attempt,
            attempt,
            fields=(
                "write_attempt_id",
                "idempotency_scope",
                "idempotency_key",
                "request_fingerprint",
                "fence",
                "parent_resource_id",
                "parent_lifecycle_epoch",
                "status",
                "lease_owner",
                "attempt_generation",
                "opaque_temp_name",
                "created_at",
                "intent",
                "blob_id",
                "byte_size",
                "checksum_sha256",
                "failure_code",
                "failure_detail_summary",
                "reconciliation_required_at",
                "reconciled_at",
                "reconciled_by",
            ),
            label="artifact write heartbeat attempt",
        )
        _require_immutable_fields(
            expected_lease,
            lease,
            fields=(
                "lease_id",
                "request_kind",
                "owner",
                "fence",
                "acquired_at",
                "attempt_generation",
                "parent_resource_id",
                "parent_lifecycle_epoch",
            ),
            label="artifact write heartbeat lease",
        )
        observed_at = _timestamp(request.observed_at, label="heartbeat observation")
        expected_expiry = _timestamp(
            expected_lease.expires_at,
            label="expected write lease expiry",
        )
        expected_heartbeat = _timestamp(
            expected_lease.last_heartbeat_at,
            label="expected write heartbeat",
        )
        next_expiry = _timestamp(lease.expires_at, label="write lease expiry")
        next_heartbeat = _timestamp(
            lease.last_heartbeat_at,
            label="write heartbeat",
        )
        if (
            expected_attempt.status
            not in {"receiving", "bytes_verified", "reserved", "published"}
            or expected_lease.request_kind != "artifact_write"
            or expected_attempt.fence != expected_lease.fence
            or expected_attempt.lease_owner != expected_lease.owner
            or expected_attempt.parent_resource_id
            != expected_lease.parent_resource_id
            or expected_attempt.parent_lifecycle_epoch
            != expected_lease.parent_lifecycle_epoch
            or expected_attempt.attempt_generation
            != expected_lease.attempt_generation
            or expected_attempt.lease_expires_at != expected_lease.expires_at
            or expected_attempt.last_heartbeat_at
            != expected_lease.last_heartbeat_at
            or attempt.lease_expires_at != lease.expires_at
            or attempt.last_heartbeat_at != lease.last_heartbeat_at
            or attempt.updated_at != request.observed_at
            or expected_expiry <= observed_at
            or expected_heartbeat > observed_at
            or next_heartbeat != observed_at
            or next_heartbeat <= expected_heartbeat
            or next_expiry <= expected_expiry
            or next_expiry <= observed_at
            or next_expiry
            > observed_at + timedelta(seconds=_WRITE_HEARTBEAT_EXTENSION_SECONDS)
        ):
            raise ValueError("artifact write lease heartbeat is invalid")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:attempt:{attempt.write_attempt_id}",
                        (
                            "artifact:idempotency:"
                            f"{attempt.idempotency_scope}:{attempt.idempotency_key}"
                        ),
                        f"artifact:lease:{lease.lease_id}",
                        f"artifact:parent:{attempt.parent_resource_id}",
                    ),
                )
                _active_control(session, attempt.fence)
                current_attempt = session.get(
                    rows.AtlasArtifactWriteAttemptRow,
                    attempt.write_attempt_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                current_lease = session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if _matches(current_attempt, attempt) and _matches(
                    current_lease,
                    lease,
                ):
                    session.rollback()
                    return CommandResult(
                        replayed=True,
                        canonical_id=attempt.write_attempt_id,
                        continue_external_work=True,
                    )
                if (
                    not _matches(current_attempt, expected_attempt)
                    or not _matches(current_lease, expected_lease)
                ):
                    raise ArtifactCommandConflict(
                        "artifact write lease heartbeat preimage changed"
                    )
                assert current_attempt is not None and current_lease is not None
                _replace(current_attempt, attempt)
                _replace(current_lease, lease)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult(canonical_id=attempt.write_attempt_id)


@dataclass(frozen=True, slots=True)
class FinalizeArtifactWriteCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeArtifactWriteInput) -> CommandResult:
        _require_audit(request.audit_events)
        if request.expected_attempt.write_attempt_id != request.attempt.write_attempt_id:
            raise ValueError("finalize attempt identity cannot move")
        _require_immutable_fields(
            request.expected_attempt,
            request.attempt,
            fields=(
                "write_attempt_id",
                "idempotency_scope",
                "idempotency_key",
                "request_fingerprint",
                "fence",
                "parent_resource_id",
                "parent_lifecycle_epoch",
                "lease_owner",
                "attempt_generation",
                "opaque_temp_name",
                "created_at",
                "intent",
            ),
            label="write attempt",
        )
        if (
            request.expected_attempt.status
            not in {"receiving", "bytes_verified", "reserved", "published"}
            or request.attempt.status != "succeeded"
            or request.blob.status != "committed"
            or request.artifact.lifecycle_status != "active"
        ):
            raise ValueError("finalize requires a successful active artifact graph")
        if (
            request.expected_lease.request_kind != "artifact_write"
            or request.expected_lease.fence != request.expected_attempt.fence
            or request.expected_lease.parent_resource_id
            != request.expected_attempt.parent_resource_id
            or request.expected_lease.parent_lifecycle_epoch
            != request.expected_attempt.parent_lifecycle_epoch
            or request.expected_lease.attempt_generation
            != request.expected_attempt.attempt_generation
        ):
            raise ValueError("finalize lease and attempt lineage must agree")
        if request.attempt.fence != request.blob.fence:
            raise ValueError("attempt and blob fences must agree")
        if (
            request.attempt.blob_id != request.blob.blob_id
            or request.blob.write_attempt_id != request.attempt.write_attempt_id
            or request.artifact.blob_id != request.blob.blob_id
            or request.artifact.checksum_value != request.blob.checksum_value
            or request.artifact.byte_size != request.blob.byte_size
            or request.artifact.content_type != request.blob.content_type
            or request.attempt.byte_size != request.blob.byte_size
            or request.attempt.checksum_sha256 != request.blob.checksum_value
            or request.artifact.parent_resource_id
            != request.attempt.parent_resource_id
            or request.artifact.parent_lifecycle_epoch
            != request.attempt.parent_lifecycle_epoch
        ):
            raise ValueError("final artifact graph is cross-wired")
        if not request.bindings or any(
            binding.artifact_id != request.artifact.artifact_id
            for binding in request.bindings
        ):
            raise ValueError("final artifact bindings must be complete")
        if sum(binding.binding_kind == "owner" for binding in request.bindings) != 1:
            raise ValueError("final artifact requires exactly one owner binding")
        owner_binding = next(
            binding for binding in request.bindings if binding.binding_kind == "owner"
        )
        if (
            owner_binding.scope_type != request.artifact.owner_scope_type
            or owner_binding.scope_id != request.artifact.owner_scope_id
        ):
            raise ValueError("final artifact owner binding is cross-wired")
        intent = request.expected_attempt.intent
        if request.artifact.document_version_id is not None:
            allowed_parent_statuses = set(intent.get("allowed_parent_statuses", ["active"]))
            if (
                request.expected_parent is None
                or request.expected_parent.document_id
                != request.expected_attempt.parent_resource_id
                or request.expected_parent.lifecycle_status not in allowed_parent_statuses
                or request.expected_parent.resource_lifecycle_epoch
                != request.expected_attempt.parent_lifecycle_epoch
                or (
                    intent.get("processing_generation") is not None
                    and request.expected_parent.active_processing_generation
                    != intent["processing_generation"]
                )
            ):
                raise ValueError("final artifact parent currentness is invalid")
        elif request.expected_parent is not None:
            raise ValueError("non-document artifact cannot claim document currentness")
        expected_intent = {
            "artifact_class": request.artifact.artifact_class,
            "logical_identity": request.artifact.logical_identity,
            "content_type": request.artifact.content_type,
            "owner_scope_type": request.artifact.owner_scope_type,
            "owner_scope_id": request.artifact.owner_scope_id,
            "document_version_id": request.artifact.document_version_id,
            "source_artifact_id": request.artifact.source_artifact_id,
            "processing_generation": request.artifact.processing_generation,
            "pipeline_id": request.artifact.pipeline_id,
            "pipeline_version": request.artifact.pipeline_version,
            "generation": request.artifact.generation,
            "page_number": request.artifact.page_number,
            "block_id": request.artifact.block_id,
            "acl_policy_version": request.artifact.acl_policy_version,
            "acl_action": request.artifact.acl_action,
        }
        if any(intent.get(key) != value for key, value in expected_intent.items()):
            raise ValueError("final artifact does not match durable write intent")
        expected_authorization = {
            tuple(item) for item in intent.get("authorization_bindings", [])
        }
        actual_authorization = {
            (binding.scope_type, binding.scope_id)
            for binding in request.bindings
            if binding.binding_kind == "authorization"
        }
        if expected_authorization != actual_authorization:
            raise ValueError("final artifact authorization bindings are incomplete")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:attempt:{request.attempt.write_attempt_id}",
                        f"artifact:lease:{request.expected_lease.lease_id}",
                        f"artifact:blob:{request.blob.blob_id}",
                        f"artifact:artifact:{request.artifact.artifact_id}",
                        *_artifact_unique_lock_identities(
                            request.blob,
                            request.artifact,
                        ),
                        *(f"artifact:binding:{item.binding_id}" for item in request.bindings),
                        *_evidence_keys(request.audit_events),
                    ),
                )
                _active_control(session, request.attempt.fence)
                _require_artifact_unique_owners(
                    session,
                    blob=request.blob,
                    artifact=request.artifact,
                )
                current = session.get(
                    rows.AtlasArtifactWriteAttemptRow,
                    request.attempt.write_attempt_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if _matches(current, request.attempt):
                    if session.get(
                        rows.AtlasStorageRequestLeaseRow,
                        request.expected_lease.lease_id,
                        populate_existing=True,
                    ) is not None:
                        raise ArtifactCommandConflict(
                            "artifact finalize replay retained its write lease"
                        )
                    existing_blob = session.get(
                        rows.AtlasStorageBlobRow,
                        request.blob.blob_id,
                        populate_existing=True,
                    )
                    existing_artifact = session.get(
                        rows.AtlasArtifactRow,
                        request.artifact.artifact_id,
                        populate_existing=True,
                    )
                    existing_bindings = tuple(
                        session.execute(
                            select(rows.AtlasArtifactScopeBindingRow).where(
                                rows.AtlasArtifactScopeBindingRow.artifact_id
                                == request.artifact.artifact_id
                            )
                        ).scalars()
                    )
                    expected_bindings = {
                        binding.binding_id: binding for binding in request.bindings
                    }
                    if (
                        not _matches(existing_blob, request.blob)
                        or not _matches(existing_artifact, request.artifact)
                        or len(existing_bindings) != len(expected_bindings)
                        or any(
                            binding.binding_id not in expected_bindings
                            or not _matches(
                                binding,
                                expected_bindings[binding.binding_id],
                            )
                            for binding in existing_bindings
                        )
                    ):
                        raise ArtifactCommandConflict(
                            "artifact finalize replay graph changed"
                        )
                    session.rollback()
                    return CommandResult(replayed=True)
                if not _matches(current, request.expected_attempt):
                    raise ArtifactCommandConflict("write attempt finalize preimage changed")
                if request.expected_parent is not None:
                    parent = session.get(
                        AtlasDocumentRow,
                        request.expected_parent.document_id,
                        populate_existing=True,
                    )
                    if (
                        parent is None
                        or parent.lifecycle_status
                        != request.expected_parent.lifecycle_status
                        or parent.resource_lifecycle_epoch
                        != request.expected_parent.resource_lifecycle_epoch
                        or parent.active_processing_generation
                        != request.expected_parent.active_processing_generation
                    ):
                        raise ArtifactCommandConflict(
                            "write parent currentness changed"
                        )
                lease = session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    request.expected_lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if not _matches(lease, request.expected_lease):
                    raise ArtifactCommandConflict("write lease finalize preimage changed")
                if session.get(rows.AtlasStorageBlobRow, request.blob.blob_id):
                    raise ArtifactCommandConflict("blob identity already exists")
                if session.get(rows.AtlasArtifactRow, request.artifact.artifact_id):
                    raise ArtifactCommandConflict("artifact identity already exists")
                session.add(_row(request.blob, rows.AtlasStorageBlobRow))
                # No ORM relationship expresses the composite blob identity
                # dependency, so make the FK parent durable in the flush order
                # before adding the artifact child.
                session.flush()
                session.add(_row(request.artifact, rows.AtlasArtifactRow))
                for binding in request.bindings:
                    if session.get(rows.AtlasArtifactScopeBindingRow, binding.binding_id):
                        raise ArtifactCommandConflict("artifact binding already exists")
                    session.add(_row(binding, rows.AtlasArtifactScopeBindingRow))
                assert current is not None
                assert lease is not None
                _replace(current, request.attempt)
                session.delete(lease)
                AuditEventWriter(session).append_many(request.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult()


@dataclass(frozen=True, slots=True)
class ProtectedArtifactOpenCommand:
    session_factory: SessionFactory

    def execute(self, request: ProtectedArtifactOpenInput) -> PostCommitArtifactOpener:
        artifact_id = request.expected_artifact.artifact_id
        document_id = request.expected_document.document_id
        presented_session_token = request.presented_browser_session_token
        if (
            request.actor_type != "user"
            or not presented_session_token
            or not presented_session_token.strip()
        ):
            raise ValueError(
                "protected open requires a presented browser session credential"
            )
        expected_artifact_class = (
            "original_document"
            if request.expected_document.source_kind == "file_upload"
            else "original_inline_source"
        )
        if (
            request.expected_document.lifecycle_status != "active"
            or request.expected_document.original_artifact_id != artifact_id
            or request.expected_version.document_id != document_id
            or request.expected_version.status != "active"
            or request.expected_version.original_artifact_id != artifact_id
            or request.expected_artifact.parent_resource_id != document_id
            or request.expected_artifact.parent_lifecycle_epoch is None
            or request.expected_artifact.parent_lifecycle_epoch
            > request.expected_document.resource_lifecycle_epoch
            or request.expected_artifact.document_version_id
            != request.expected_version.document_version_id
            or request.expected_artifact.owner_scope_type
            != request.expected_document.scope_type
            or request.expected_artifact.owner_scope_id
            != request.expected_document.scope_id
            or request.expected_artifact.artifact_class != expected_artifact_class
            or request.expected_version.content_type
            != request.expected_artifact.content_type
            or not request.candidate_scope
            or not set(request.candidate_scope).issubset(request.expected_tag_refs)
        ):
            raise ValueError("protected open document lineage must be current")
        if request.expected_artifact.blob_id != request.expected_blob.blob_id:
            raise ValueError("protected open artifact and blob must be connected")
        if (
            request.read_lease.request_kind != "artifact_read"
            or request.read_lease.fence != request.expected_blob.fence
            or request.read_lease.parent_resource_id != document_id
            or request.read_lease.parent_lifecycle_epoch
            != request.expected_artifact.parent_lifecycle_epoch
            or request.read_lease.attempt_generation < 1
            or request.read_lease.expires_at <= request.observed_at
        ):
            raise ValueError("protected open read lease is cross-wired")
        if (
            request.expected_artifact.checksum_algorithm != "sha256"
            or request.expected_blob.checksum_algorithm != "sha256"
            or request.expected_artifact.checksum_value
            != request.expected_blob.checksum_value
            or request.expected_artifact.byte_size != request.expected_blob.byte_size
            or request.expected_artifact.content_type != request.expected_blob.content_type
        ):
            raise ValueError("protected open artifact and blob metadata must agree")
        decision = request.access_decision
        if decision is not None:
            _require_audit(request.audit_events)
            if (
                decision.actor_type != request.actor_type
                or decision.actor_id != request.actor_id
                or decision.action != request.action
                or decision.scope_type != request.expected_document.scope_type
                or decision.scope_id != request.expected_document.scope_id
                or decision.project_id
                != (
                    request.expected_document.scope_id
                    if request.expected_document.scope_type == "project"
                    else None
                )
            ):
                raise ValueError("protected open requires the exact decision")
            if _linked_decisions(request.audit_events) != {decision.decision_id}:
                raise ValueError("protected open audit must link the exact decision")
            if any(
                event.actor_id != request.actor_id
                or event.target_ref != f"artifact:{artifact_id}"
                or event.project_id != decision.project_id
                for event in request.audit_events
            ):
                raise ValueError(
                    "protected open audit must match the exact actor and target"
                )
        elif request.audit_events:
            raise ValueError("protected open audit requires an access decision")
        session = self.session_factory()
        opener: PostCommitArtifactOpener | None = None
        denied = False
        with session:
            try:
                coordinated_scope = set(request.candidate_scope) | set(
                    request.expected_tag_refs
                )
                scope_keys = tuple(
                    project_owner_key(scope_id)
                    if scope_type == "project"
                    else team_owner_key(scope_id)
                    for scope_type, scope_id in coordinated_scope
                )
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    shared_identity_keys=(
                        f"artifact:artifact:{artifact_id}",
                        f"artifact:parent:{document_id}",
                        identity_actor_owner_key(request.actor_id),
                        team_subject_owner_key(request.actor_type, request.actor_id),
                        project_acl_subject_owner_key(
                            request.actor_type,
                            request.actor_id,
                        ),
                        *scope_keys,
                        *(team_owner_key(team_id) for team_id in request.candidate_team_ids),
                        *(
                            project_acl_subject_owner_key("team", team_id)
                            for team_id in request.candidate_team_ids
                        ),
                    ),
                    exclusive_identity_keys=_evidence_keys(
                        request.audit_events,
                        (decision,) if decision is not None else (),
                    ) + (f"artifact:lease:{request.read_lease.lease_id}",),
                )
                browser_session = session.scalar(
                    select(AtlasSessionRow)
                    .where(
                        AtlasSessionRow.session_token
                        == presented_session_token
                    )
                )
                if (
                    browser_session is None
                    or browser_session.actor_id != request.actor_id
                ):
                    raise ArtifactProtectedOpenUnauthenticated(
                        "protected browser session is no longer authenticated"
                    )
                artifact = session.get(
                    rows.AtlasArtifactRow,
                    artifact_id,
                    populate_existing=True,
                )
                if (
                    artifact is None
                    or artifact.lifecycle_status != "active"
                    or not _matches(artifact, request.expected_artifact)
                ):
                    raise ArtifactCommandConflict("artifact is not readable")
                document = session.get(
                    AtlasDocumentRow,
                    document_id,
                    populate_existing=True,
                )
                if document is None or not _matches(
                    document,
                    request.expected_document,
                ):
                    raise ArtifactCommandConflict("document currentness changed")
                version = session.get(
                    AtlasDocumentVersionRow,
                    request.expected_version.document_version_id,
                    populate_existing=True,
                )
                if (
                    version is None
                    or version.document_id != document_id
                    or version.payload != asdict(request.expected_version)
                ):
                    raise ArtifactCommandConflict("document version currentness changed")
                tag_rows = tuple(
                    session.execute(
                        select(AtlasDocumentTagRow).where(
                            AtlasDocumentTagRow.document_id == document_id
                        )
                    ).scalars()
                )
                if {
                    (tag.tag_type, tag.tag_id) for tag in tag_rows
                } != set(request.expected_tag_refs):
                    raise ArtifactCommandConflict("document tag currentness changed")
                actor = session.get(
                    AtlasUserRow,
                    request.actor_id,
                    populate_existing=True,
                )
                if (
                    actor is None
                    or not actor.active
                    or actor.actor_type != request.actor_type
                ):
                    raise ArtifactProtectedOpenUnauthenticated(
                        "protected browser actor is no longer authenticated"
                    )
                blob = session.get(
                    rows.AtlasStorageBlobRow,
                    artifact.blob_id,
                    populate_existing=True,
                )
                if (
                    blob is None
                    or blob.status != "committed"
                    or not _matches(blob, request.expected_blob)
                ):
                    raise ArtifactCommandConflict("artifact blob is not committed")
                _active_control(
                    session,
                    StorageFence(
                        target_id=blob.target_id,
                        target_revision=blob.target_revision,
                        root_identity_digest=blob.root_identity_digest,
                        storage_epoch=blob.storage_epoch,
                    ),
                )
                bindings = tuple(
                    session.execute(
                        select(rows.AtlasArtifactScopeBindingRow).where(
                            rows.AtlasArtifactScopeBindingRow.artifact_id
                            == artifact_id
                        )
                    ).scalars()
                )
                authority_scope = {
                    (binding.scope_type, binding.scope_id)
                    for binding in bindings
                    if binding.binding_kind in {"owner", "authorization"}
                    and binding.scope_id is not None
                }
                resolved_scope, team_ids = read_effective_document_scope_with_team_ids(
                    session,
                    actor_type=request.actor_type,
                    actor_id=request.actor_id,
                    requested_scope=set(request.candidate_scope),
                )
                allowed = (
                    resolved_scope == set(request.candidate_scope)
                    and team_ids == set(request.candidate_team_ids)
                    and bool(authority_scope.intersection(resolved_scope))
                    and not document.source_download_restricted
                    and (
                        document.allow_member_download
                        or actor.system_role == "admin"
                    )
                )
                policy_denial_reason = (
                    "source_download_restricted"
                    if document.source_download_restricted
                    else (
                        "member_download_policy"
                        if not document.allow_member_download
                        and actor.system_role != "admin"
                        else None
                    )
                )
                if not allowed and decision is None:
                    raise ValueError("protected denial requires decision and audit evidence")
                if allowed and request.record_success_evidence and decision is None:
                    raise ValueError("protected GET requires decision and audit evidence")
                if allowed and not request.record_success_evidence and decision is not None:
                    raise ValueError("successful protected HEAD must not write success evidence")
                if decision is not None and decision.allowed != allowed:
                    raise ArtifactCommandConflict("access decision currentness changed")
                if (
                    decision is not None
                    and not decision.allowed
                    and policy_denial_reason is not None
                    and decision.reason != policy_denial_reason
                ):
                    raise ArtifactCommandConflict(
                        "access decision policy reason changed"
                    )
                if (
                    decision is not None
                    and decision.allowed
                    and decision.scope_type is not None
                    and decision.scope_id is not None
                    and (decision.scope_type, decision.scope_id) not in resolved_scope
                ):
                    raise ArtifactCommandConflict("access decision scope is stale")
                if decision is not None:
                    AccessDecisionWriter(session).append(decision)
                    AuditEventWriter(session).append_many(request.audit_events)
                if allowed:
                    if session.get(
                        rows.AtlasStorageRequestLeaseRow,
                        request.read_lease.lease_id,
                        populate_existing=True,
                    ) is not None:
                        raise ArtifactCommandConflict("artifact read lease already exists")
                    session.add(
                        _row(request.read_lease, rows.AtlasStorageRequestLeaseRow)
                    )
                    opener = PostCommitArtifactOpener(
                        artifact_id=artifact.artifact_id,
                        blob_id=blob.blob_id,
                        opaque_ref=blob.opaque_ref,
                        byte_size=blob.byte_size,
                        checksum_sha256=blob.checksum_value,
                        content_type=blob.content_type,
                        read_lease=request.read_lease,
                    )
                else:
                    denied = True
                session.commit()
            except Exception:
                session.rollback()
                raise
        if denied:
            raise ArtifactProtectedOpenDenied("artifact access denied")
        assert opener is not None
        return opener

    def heartbeat(self, request: HeartbeatArtifactReadInput) -> StorageRequestLeaseRecord:
        _require_immutable_fields(
            request.expected_lease,
            request.lease,
            fields=(
                "lease_id",
                "request_kind",
                "owner",
                "fence",
                "acquired_at",
                "attempt_generation",
                "parent_resource_id",
                "parent_lifecycle_epoch",
            ),
            label="artifact read lease",
        )
        if (
            request.lease.request_kind != "artifact_read"
            or request.lease.expires_at <= request.expected_lease.expires_at
            or request.lease.last_heartbeat_at
            <= request.expected_lease.last_heartbeat_at
        ):
            raise ValueError("artifact read lease heartbeat must advance")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:lease:{request.lease.lease_id}",
                    ),
                )
                _active_control(session, request.lease.fence)
                current = session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    request.lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if not _matches(current, request.expected_lease):
                    raise ArtifactCommandConflict("artifact read lease heartbeat changed")
                assert current is not None
                _replace(current, request.lease)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return request.lease

    def complete(self, request: CompleteArtifactReadInput) -> CommandResult:
        if request.expected_lease.request_kind != "artifact_read":
            raise ValueError("artifact read completion requires its exact lease kind")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:lease:{request.expected_lease.lease_id}",
                    ),
                )
                _active_control(session, request.expected_lease.fence)
                current = session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    request.expected_lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if not _matches(current, request.expected_lease):
                    raise ArtifactCommandConflict("artifact read completion lease changed")
                assert current is not None
                session.delete(current)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult()


@dataclass(frozen=True, slots=True)
class ClaimArtifactReconciliationCommand:
    session_factory: SessionFactory

    def execute(self, request: ClaimArtifactReconciliationInput) -> ReconciliationClaim:
        _require_audit(request.audit_events)
        if request.expected_finding.status != "open":
            raise ValueError("only open reconciliation findings can be claimed")
        if request.lease.request_kind != "artifact_reconciliation":
            raise ValueError("reconciliation claim requires its exact lease kind")
        session = self.session_factory()
        with session:
            try:
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=(
                        f"artifact:reconciliation:{request.expected_finding.finding_id}",
                        f"artifact:lease:{request.lease.lease_id}",
                        *_evidence_keys(request.audit_events),
                    ),
                )
                current = session.get(
                    rows.AtlasStorageReconciliationFindingRow,
                    request.expected_finding.finding_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if not _matches(current, request.expected_finding):
                    raise ArtifactCommandConflict("reconciliation finding preimage changed")
                if session.get(rows.AtlasStorageRequestLeaseRow, request.lease.lease_id):
                    raise ArtifactCommandConflict("reconciliation lease already exists")
                session.add(_row(request.lease, rows.AtlasStorageRequestLeaseRow))
                AuditEventWriter(session).append_many(request.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return ReconciliationClaim(
            finding_id=request.expected_finding.finding_id,
            lease_id=request.lease.lease_id,
            attempt_generation=request.lease.attempt_generation,
        )


@dataclass(frozen=True, slots=True)
class FinalizeArtifactReconciliationCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeArtifactReconciliationInput) -> CommandResult:
        _require_audit(request.audit_events)
        if request.expected_finding.finding_id != request.finding.finding_id:
            raise ValueError("reconciliation finding identity cannot move")
        if any(
            getattr(request.expected_finding, name) != getattr(request.finding, name)
            for name in (
                "finding_kind",
                "detected_at",
                "safe_summary",
                "blob_id",
                "write_attempt_id",
                "operation_id",
            )
        ):
            raise ValueError("reconciliation finding authority cannot move")
        if request.finding.status not in {"resolved", "quarantined"}:
            raise ValueError("reconciliation must resolve or quarantine the finding")
        if request.expected_finding.status != "open":
            raise ValueError("reconciliation finalize requires an open preimage")
        if request.expected_lease.request_kind != "artifact_reconciliation":
            raise ValueError("reconciliation finalize requires its exact lease kind")
        if request.expected_finding.write_attempt_id is not None:
            if (
                request.expected_attempt is None
                or request.expected_attempt.write_attempt_id
                != request.expected_finding.write_attempt_id
                or (
                    request.attempt is not None
                    and request.attempt.write_attempt_id
                    != request.expected_attempt.write_attempt_id
                )
            ):
                raise ValueError("reconciliation attempt is cross-wired")
        elif request.expected_attempt is not None or request.attempt is not None:
            raise ValueError("reconciliation finding does not own an attempt")
        if request.expected_finding.blob_id is not None:
            if (
                request.expected_blob is None
                or request.expected_blob.blob_id != request.expected_finding.blob_id
                or (
                    request.blob is not None
                    and request.blob.blob_id != request.expected_blob.blob_id
                )
            ):
                raise ValueError("reconciliation blob is cross-wired")
        elif request.expected_blob is not None or request.blob is not None:
            raise ValueError("reconciliation finding does not own a blob")
        authoritative_attempt = request.attempt or request.expected_attempt
        authoritative_blob = request.blob or request.expected_blob
        if request.attempt is not None and request.expected_attempt is not None:
            _require_immutable_fields(
                request.expected_attempt,
                request.attempt,
                fields=(
                    "write_attempt_id",
                    "idempotency_scope",
                    "idempotency_key",
                    "request_fingerprint",
                    "fence",
                    "parent_resource_id",
                    "parent_lifecycle_epoch",
                    "lease_owner",
                    "attempt_generation",
                    "opaque_temp_name",
                    "created_at",
                    "intent",
                ),
                label="reconciliation attempt",
            )
            _require_reconciliation_transition(
                request.expected_attempt.status,
                request.attempt.status,
                allowed=_RECONCILIATION_ATTEMPT_TRANSITIONS,
                label="reconciliation attempt",
            )
        if request.blob is not None and request.expected_blob is not None:
            _require_immutable_fields(
                request.expected_blob,
                request.blob,
                fields=(
                    "blob_id",
                    "opaque_ref",
                    "dedup_mode",
                    "checksum_algorithm",
                    "checksum_value",
                    "byte_size",
                    "content_type",
                    "fence",
                    "created_at",
                    "dedup_scope_type",
                    "dedup_scope_id",
                    "write_attempt_id",
                    "committed_at",
                ),
                label="reconciliation blob",
            )
            _require_reconciliation_transition(
                request.expected_blob.status,
                request.blob.status,
                allowed=_RECONCILIATION_BLOB_TRANSITIONS,
                label="reconciliation blob",
            )
        if (
            authoritative_attempt is not None
            and request.expected_lease.fence != authoritative_attempt.fence
        ):
            raise ValueError("reconciliation lease and attempt fences differ")
        if authoritative_blob is not None:
            if request.expected_lease.fence != authoritative_blob.fence:
                raise ValueError("reconciliation lease and blob fences differ")
            if (
                authoritative_attempt is not None
                and authoritative_blob.write_attempt_id
                != authoritative_attempt.write_attempt_id
            ):
                raise ValueError("reconciliation blob attempt is cross-wired")
        if authoritative_attempt is not None:
            _require_reconciliation_attempt_blob_result(
                authoritative_attempt,
                authoritative_blob,
            )
        session = self.session_factory()
        with session:
            try:
                keys = [
                    f"artifact:reconciliation:{request.finding.finding_id}",
                    f"artifact:lease:{request.expected_lease.lease_id}",
                    *_evidence_keys(request.audit_events),
                ]
                if authoritative_attempt is not None:
                    keys.append(
                        f"artifact:attempt:{authoritative_attempt.write_attempt_id}"
                    )
                if authoritative_blob is not None:
                    keys.append(f"artifact:blob:{authoritative_blob.blob_id}")
                acquire_mixed_owner_locks(
                    session,
                    shared_domain_keys=("artifact:control",),
                    exclusive_identity_keys=keys,
                )
                finding = session.get(
                    rows.AtlasStorageReconciliationFindingRow,
                    request.finding.finding_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if _matches(finding, request.finding):
                    remaining_lease = session.get(
                        rows.AtlasStorageRequestLeaseRow,
                        request.expected_lease.lease_id,
                        populate_existing=True,
                    )
                    current_attempt = (
                        session.get(
                            rows.AtlasArtifactWriteAttemptRow,
                            authoritative_attempt.write_attempt_id,
                            populate_existing=True,
                        )
                        if authoritative_attempt is not None
                        else None
                    )
                    current_blob = (
                        session.get(
                            rows.AtlasStorageBlobRow,
                            authoritative_blob.blob_id,
                            populate_existing=True,
                        )
                        if authoritative_blob is not None
                        else None
                    )
                    if (
                        remaining_lease is not None
                        or (
                            authoritative_attempt is not None
                            and not _matches(current_attempt, authoritative_attempt)
                        )
                        or (
                            authoritative_blob is not None
                            and not _matches(current_blob, authoritative_blob)
                        )
                    ):
                        raise ArtifactCommandConflict(
                            "reconciliation replay state changed"
                        )
                    session.rollback()
                    return CommandResult(replayed=True)
                if not _matches(finding, request.expected_finding):
                    raise ArtifactCommandConflict("reconciliation finding changed")
                lease = session.get(
                    rows.AtlasStorageRequestLeaseRow,
                    request.expected_lease.lease_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                if not _matches(lease, request.expected_lease):
                    raise ArtifactCommandConflict("reconciliation claim changed")
                if request.expected_attempt is not None:
                    attempt = session.get(
                        rows.AtlasArtifactWriteAttemptRow,
                        request.expected_attempt.write_attempt_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    if not _matches(attempt, request.expected_attempt):
                        raise ArtifactCommandConflict(
                            "reconciliation attempt preimage changed"
                        )
                    if request.attempt is not None:
                        _replace(attempt, request.attempt)
                if request.expected_blob is not None:
                    blob = session.get(
                        rows.AtlasStorageBlobRow,
                        request.expected_blob.blob_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    if not _matches(blob, request.expected_blob):
                        raise ArtifactCommandConflict(
                            "reconciliation blob preimage changed"
                        )
                    if request.blob is not None:
                        _replace(blob, request.blob)
                assert finding is not None and lease is not None
                _replace(finding, request.finding)
                session.delete(lease)
                AuditEventWriter(session).append_many(request.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return CommandResult()


__all__ = [
    "ArtifactCommandConflict",
    "ArtifactProtectedOpenDenied",
    "ArtifactProtectedOpenUnauthenticated",
    "BeginArtifactWriteCommand",
    "BeginArtifactWriteInput",
    "ClaimArtifactReconciliationCommand",
    "ClaimArtifactReconciliationInput",
    "CommandResult",
    "ConversationArtifactPublication",
    "ConversationArtifactPublicationWriter",
    "CompleteArtifactReadInput",
    "DocumentParentCurrentness",
    "FinalizeArtifactReconciliationCommand",
    "FinalizeArtifactReconciliationInput",
    "FinalizeArtifactWriteCommand",
    "FinalizeArtifactWriteInput",
    "HeartbeatArtifactReadInput",
    "HeartbeatArtifactWriteCommand",
    "HeartbeatArtifactWriteInput",
    "NewDocumentOriginalArtifactPublication",
    "NewDocumentOriginalArtifactPublicationWriter",
    "PostCommitArtifactOpener",
    "ProtectedArtifactOpenCommand",
    "ProtectedArtifactOpenInput",
    "ReconciliationClaim",
    "TargetControlCommand",
    "TargetControlInput",
    "new_document_original_artifact_lock_identities",
]
