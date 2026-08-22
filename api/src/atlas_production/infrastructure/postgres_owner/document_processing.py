from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Event, Thread
from typing import Any, Callable, Protocol, cast
from uuid import uuid4

from sqlalchemy import Integer, and_, case, cast as sql_cast, delete, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import artifact_storage as artifact_rows
from atlas_production.infrastructure.persistence import async_processing as async_rows
from atlas_production.infrastructure.persistence import document_intake as document_rows
from atlas_production.infrastructure.persistence import identity_access as identity_rows
from atlas_production.infrastructure.persistence import processing_pipeline as processing_rows
from atlas_production.infrastructure.persistence import project_governance as project_rows
from atlas_production.infrastructure.persistence.payload_policy import (
    RUNTIME_POLICY_MAX_BYTES,
    validate_typed_payload,
)
from atlas_production.infrastructure.postgres_locks import (
    acquire_mixed_owner_locks,
    acquire_owner_locks,
)
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.infrastructure.postgres_lock_keys import (identity_actor_owner_key,
project_acl_subject_owner_key,
project_owner_key,
team_owner_key,
team_subject_owner_key,)
from atlas_production.modules.artifact_storage.records import (
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageFence,
    StorageRequestLeaseRecord,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.processing_pipeline.records import (
    EvidencePageArtifact,
    EvidenceRecord,
)
from atlas_production.modules.processing_pipeline.job_contracts import (
    ProcessingControlDenied,
)
from atlas_production.modules.processing_pipeline.job_records import (
    DocumentJobRequestAuthorityProjection,
    DocumentLifecycleDenied,
    DocumentLifecycleProcessingAcceptance,
    DocumentProcessingCurrentnessConflict,
    ProcessingControlResult,
    ProcessingExecutionSnapshot,
    ProcessingJobAuthorizationState,
    ProcessingJobListBatch,
    ProcessingJobRecord,
    ProcessingJobView,
    ProcessingProfilePin,
    VerifiedDocumentRestoreSet,
)
from atlas_production.modules.processing_pipeline.canonical_processing import (
    PROCESSING_SPEC_SCHEMA_VERSION,
    canonical_processing_spec,
    processing_fingerprint,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.rbac import (
    direct_team_role,
    document_owner_is_active,
    effective_document_scope,
    is_system_admin,
    resolve_access,
    team_role_covers,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]
ArtifactPublicationReaderFactory = Callable[[Session], "ArtifactPublicationReader"]
ArtifactMetadataRecord = (
    ArtifactWriteAttemptRecord
    | StorageBlobRecord
    | ArtifactRecord
    | ArtifactScopeBindingRecord
    | StorageRequestLeaseRecord
)
_DOCUMENT_ARTIFACT_METADATA_TYPES = (
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    StorageRequestLeaseRecord,
)


@dataclass(frozen=True, slots=True)
class GenerationPublicationArtifactExpectation:
    artifact_id: str
    artifact_class: str
    content_type: str
    checksum_algorithm: str
    checksum_value: str
    byte_size: int
    page_number: int


@dataclass(frozen=True, slots=True)
class GenerationArtifactPublicationExpectation:
    document_id: str
    document_version_id: str
    processing_generation: int
    expected_parent_lifecycle_epoch: int
    source_artifact_id: str
    owner_scope_type: str
    owner_scope_id: str | None
    require_current_derived_parent_epoch: bool
    artifacts: tuple[GenerationPublicationArtifactExpectation, ...]

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    self.source_artifact_id,
                    *(item.artifact_id for item in self.artifacts),
                }
            )
        )


@dataclass(frozen=True, slots=True)
class CurrentArtifactIdentity:
    artifact_id: str
    blob_id: str
    write_attempt_id: str
    binding_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurrentArtifactLockInventory:
    identities: tuple[CurrentArtifactIdentity, ...]
    identity_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurrentArtifactGraphEntry:
    artifact_id: str
    artifact_class: str
    logical_identity: str
    blob_id: str
    write_attempt_id: str
    opaque_ref: str
    checksum_algorithm: str
    checksum_value: str
    byte_size: int
    content_type: str
    owner_scope_type: str
    owner_scope_id: str | None
    document_id: str
    document_version_id: str
    parent_lifecycle_epoch: int
    processing_generation: int | None
    source_artifact_id: str | None
    generation: int | None
    page_number: int | None
    fence: StorageFence
    bindings: tuple[tuple[str, str, str, str | None], ...]


@dataclass(frozen=True, slots=True)
class CurrentArtifactGraphResult:
    entries: tuple[CurrentArtifactGraphEntry, ...]
    active_fence: StorageFence


class ArtifactPublicationReader(Protocol):
    _session: Session

    def discover_identity_inventory(
        self,
        expectation: GenerationArtifactPublicationExpectation,
    ) -> CurrentArtifactLockInventory: ...

    def read_locked_current(
        self,
        *,
        inventory: CurrentArtifactLockInventory,
        expectation: GenerationArtifactPublicationExpectation,
    ) -> CurrentArtifactGraphResult: ...


def artifact_metadata_lock_keys(
    records: tuple[ArtifactMetadataRecord, ...],
) -> tuple[str, ...]:
    if records:
        raise ValueError(
            "artifact metadata must be finalized by the named artifact command"
        )
    return ()


@dataclass(frozen=True, slots=True)
class _GenerationArtifactPublicationReader:
    """Session-bound exact reader for already-finalized artifact metadata."""

    _session: Session

    def discover_identity_inventory(
        self,
        expectation: GenerationArtifactPublicationExpectation,
    ) -> CurrentArtifactLockInventory:
        if not expectation.artifact_ids or len(expectation.artifact_ids) > 3_001:
            raise ValueError("publication artifact inventory is outside the page bound")
        artifacts = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactRow)
                .where(
                    artifact_rows.AtlasArtifactRow.artifact_id.in_(
                        expectation.artifact_ids
                    )
                )
                .order_by(artifact_rows.AtlasArtifactRow.artifact_id)
            ).all()
        )
        if tuple(row.artifact_id for row in artifacts) != expectation.artifact_ids:
            raise DocumentProcessingCurrentnessConflict(
                "publication artifact inventory is incomplete"
            )
        blob_ids = tuple(sorted({row.blob_id for row in artifacts}))
        blobs = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasStorageBlobRow)
                .where(artifact_rows.AtlasStorageBlobRow.blob_id.in_(blob_ids))
                .order_by(artifact_rows.AtlasStorageBlobRow.blob_id)
            ).all()
        )
        if tuple(row.blob_id for row in blobs) != blob_ids or any(
            not row.write_attempt_id for row in blobs
        ):
            raise DocumentProcessingCurrentnessConflict(
                "publication blob inventory is incomplete"
            )
        attempt_ids = tuple(sorted({cast(str, row.write_attempt_id) for row in blobs}))
        attempts = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactWriteAttemptRow)
                .where(
                    artifact_rows.AtlasArtifactWriteAttemptRow.write_attempt_id.in_(
                        attempt_ids
                    )
                )
                .order_by(
                    artifact_rows.AtlasArtifactWriteAttemptRow.write_attempt_id
                )
            ).all()
        )
        bindings = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactScopeBindingRow)
                .where(
                    artifact_rows.AtlasArtifactScopeBindingRow.artifact_id.in_(
                        expectation.artifact_ids
                    )
                )
                .order_by(artifact_rows.AtlasArtifactScopeBindingRow.binding_id)
            ).all()
        )
        binding_ids_by_artifact: dict[str, list[str]] = {
            artifact_id: [] for artifact_id in expectation.artifact_ids
        }
        for binding in bindings:
            binding_ids_by_artifact[binding.artifact_id].append(binding.binding_id)
        if tuple(row.write_attempt_id for row in attempts) != attempt_ids or any(
            not binding_ids_by_artifact[artifact_id]
            for artifact_id in expectation.artifact_ids
        ):
            raise DocumentProcessingCurrentnessConflict(
                "publication attempt or binding inventory is incomplete"
            )
        blob_by_id = {row.blob_id: row for row in blobs}
        identities = tuple(
            CurrentArtifactIdentity(
                artifact_id=artifact.artifact_id,
                blob_id=artifact.blob_id,
                write_attempt_id=cast(
                    str,
                    blob_by_id[artifact.blob_id].write_attempt_id,
                ),
                binding_ids=tuple(binding_ids_by_artifact[artifact.artifact_id]),
            )
            for artifact in artifacts
        )
        return CurrentArtifactLockInventory(
            identities=identities,
            identity_keys=tuple(
                sorted(
                    {
                        *(f"artifact:artifact:{item.artifact_id}" for item in identities),
                        *(f"artifact:blob:{item.blob_id}" for item in identities),
                        *(f"artifact:attempt:{item.write_attempt_id}" for item in identities),
                        *(
                            f"artifact:binding:{binding_id}"
                            for item in identities
                            for binding_id in item.binding_ids
                        ),
                    }
                )
            ),
        )

    def read_locked_current(
        self,
        *,
        inventory: CurrentArtifactLockInventory,
        expectation: GenerationArtifactPublicationExpectation,
    ) -> CurrentArtifactGraphResult:
        rediscovered = self.discover_identity_inventory(expectation)
        if rediscovered != inventory:
            raise DocumentProcessingCurrentnessConflict(
                "publication artifact inventory changed after coordination"
            )
        artifacts = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactRow)
                .where(
                    artifact_rows.AtlasArtifactRow.artifact_id.in_(
                        expectation.artifact_ids
                    )
                )
                .order_by(artifact_rows.AtlasArtifactRow.artifact_id)
                .with_for_update()
            ).all()
        )
        blobs = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasStorageBlobRow)
                .where(
                    artifact_rows.AtlasStorageBlobRow.blob_id.in_(
                        tuple(item.blob_id for item in inventory.identities)
                    )
                )
                .order_by(artifact_rows.AtlasStorageBlobRow.blob_id)
                .with_for_update()
            ).all()
        )
        attempts = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactWriteAttemptRow)
                .where(
                    artifact_rows.AtlasArtifactWriteAttemptRow.write_attempt_id.in_(
                        tuple(item.write_attempt_id for item in inventory.identities)
                    )
                )
                .order_by(
                    artifact_rows.AtlasArtifactWriteAttemptRow.write_attempt_id
                )
                .with_for_update()
            ).all()
        )
        bindings = tuple(
            self._session.scalars(
                select(artifact_rows.AtlasArtifactScopeBindingRow)
                .where(
                    artifact_rows.AtlasArtifactScopeBindingRow.binding_id.in_(
                        tuple(
                            binding_id
                            for item in inventory.identities
                            for binding_id in item.binding_ids
                        )
                    )
                )
                .order_by(artifact_rows.AtlasArtifactScopeBindingRow.binding_id)
                .with_for_update()
            ).all()
        )
        control = self._session.scalar(
            select(artifact_rows.AtlasArtifactStorageControlRow)
            .where(artifact_rows.AtlasArtifactStorageControlRow.control_id == "global")
            .with_for_update()
        )
        if (
            control is None
            or control.mode != "active"
            or control.active_target_id is None
            or control.active_target_revision is None
            or control.root_identity_digest is None
        ):
            raise DocumentProcessingCurrentnessConflict(
                "publication artifact control is not active"
            )
        active_fence = StorageFence(
            control.active_target_id,
            control.active_target_revision,
            control.root_identity_digest,
            control.storage_epoch,
        )
        blob_by_id = {row.blob_id: row for row in blobs}
        attempt_by_id = {row.write_attempt_id: row for row in attempts}
        bindings_by_artifact: dict[str, list[Any]] = {
            artifact_id: [] for artifact_id in expectation.artifact_ids
        }
        for binding in bindings:
            bindings_by_artifact[binding.artifact_id].append(binding)
        expected_derived = {item.artifact_id: item for item in expectation.artifacts}
        entries: list[CurrentArtifactGraphEntry] = []
        for artifact in artifacts:
            blob = blob_by_id.get(artifact.blob_id)
            attempt = (
                attempt_by_id.get(blob.write_attempt_id)
                if blob is not None and blob.write_attempt_id is not None
                else None
            )
            artifact_bindings = bindings_by_artifact[artifact.artifact_id]
            owners = [item for item in artifact_bindings if item.binding_kind == "owner"]
            fence = (
                StorageFence(
                    blob.target_id,
                    blob.target_revision,
                    blob.root_identity_digest,
                    blob.storage_epoch,
                )
                if blob is not None
                else None
            )
            if (
                blob is None
                or attempt is None
                or blob.status != "committed"
                or attempt.status != "succeeded"
                or artifact.lifecycle_status != "active"
                or attempt.blob_id != blob.blob_id
                or blob.write_attempt_id != attempt.write_attempt_id
                or artifact.parent_resource_id != expectation.document_id
                or artifact.document_version_id != expectation.document_version_id
                or artifact.owner_scope_type != expectation.owner_scope_type
                or artifact.owner_scope_id != expectation.owner_scope_id
                or len(owners) != 1
                or owners[0].scope_type != expectation.owner_scope_type
                or owners[0].scope_id != expectation.owner_scope_id
                or fence != active_fence
            ):
                raise DocumentProcessingCurrentnessConflict(
                    "publication artifact graph is cross-wired"
                )
            if artifact.artifact_id == expectation.source_artifact_id:
                if artifact.artifact_class not in {
                    "original_document", "original_inline_source"
                } or cast(int, artifact.parent_lifecycle_epoch) > expectation.expected_parent_lifecycle_epoch:
                    raise DocumentProcessingCurrentnessConflict(
                        "publication source artifact is stale or foreign"
                    )
            else:
                expected = expected_derived.get(artifact.artifact_id)
                if (
                    expected is None
                    or artifact.artifact_class != expected.artifact_class
                    or artifact.content_type != expected.content_type
                    or artifact.checksum_algorithm != expected.checksum_algorithm
                    or artifact.checksum_value != expected.checksum_value
                    or artifact.byte_size != expected.byte_size
                    or artifact.page_number != expected.page_number
                    or artifact.processing_generation != expectation.processing_generation
                    or artifact.source_artifact_id != expectation.source_artifact_id
                    or cast(int, artifact.parent_lifecycle_epoch)
                    > expectation.expected_parent_lifecycle_epoch
                    or (
                        expectation.require_current_derived_parent_epoch
                        and artifact.parent_lifecycle_epoch
                        != expectation.expected_parent_lifecycle_epoch
                    )
                ):
                    raise DocumentProcessingCurrentnessConflict(
                        "publication derived artifact is stale or foreign"
                    )
            entries.append(
                CurrentArtifactGraphEntry(
                    artifact_id=artifact.artifact_id,
                    artifact_class=artifact.artifact_class,
                    logical_identity=artifact.logical_identity,
                    blob_id=blob.blob_id,
                    write_attempt_id=attempt.write_attempt_id,
                    opaque_ref=blob.opaque_ref,
                    checksum_algorithm=blob.checksum_algorithm,
                    checksum_value=blob.checksum_value,
                    byte_size=blob.byte_size,
                    content_type=blob.content_type,
                    owner_scope_type=artifact.owner_scope_type,
                    owner_scope_id=artifact.owner_scope_id,
                    document_id=expectation.document_id,
                    document_version_id=expectation.document_version_id,
                    parent_lifecycle_epoch=cast(int, artifact.parent_lifecycle_epoch),
                    processing_generation=artifact.processing_generation,
                    source_artifact_id=artifact.source_artifact_id,
                    generation=artifact.generation,
                    page_number=artifact.page_number,
                    fence=active_fence,
                    bindings=tuple(
                        (
                            item.binding_id,
                            item.binding_kind,
                            item.scope_type,
                            item.scope_id,
                        )
                        for item in artifact_bindings
                    ),
                )
            )
        return CurrentArtifactGraphResult(tuple(entries), active_fence)






_JOB_TRANSITIONS = {
    "queued": frozenset({"queued", "running", "retry_wait", "failed", "cancelled"}),
    "running": frozenset({"running", "retry_wait", "succeeded", "failed", "cancelled"}),
    "retry_wait": frozenset({"retry_wait", "running", "failed", "cancelled"}),
    "succeeded": frozenset({"succeeded"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}
_OUTBOX_TRANSITIONS = {
    "pending": frozenset({"pending", "dispatching", "cancelled"}),
    "dispatching": frozenset({"dispatching", "pending", "dispatched", "cancelled"}),
    "dispatched": frozenset({"dispatched"}),
    "cancelled": frozenset({"cancelled"}),
}
_GENERATION_TRANSITIONS = {
    "building": frozenset({"building", "active", "failed"}),
    "active": frozenset({"active", "retired"}),
    "retired": frozenset({"retired"}),
    "failed": frozenset({"failed"}),
}
_DOCUMENT_LIFECYCLE_TRANSITIONS = {
    "active": frozenset({("active", 0), ("disabled", 1)}),
    "disabled": frozenset({("disabled", 0), ("restoring", 0)}),
    "restoring": frozenset(
        {("restoring", 0), ("active", 1), ("disabled", 1)}
    ),
}
_SEARCH_CHUNK_TRANSITIONS = {
    "staged": frozenset({"staged", "active", "retired"}),
    "active": frozenset({"active", "retired"}),
    "retired": frozenset({"retired"}),
}
_VERSION_TRANSITIONS = {
    "active": frozenset({"active", "superseded"}),
    "staged": frozenset({"staged", "active", "superseded"}),
    "superseded": frozenset({"superseded"}),
}
_EVIDENCE_TRANSITIONS = {
    "staged": frozenset({"staged", "ready"}),
    "ready": frozenset({"ready", "superseded", "blocked"}),
    "blocked": frozenset({"blocked", "ready", "superseded"}),
    "superseded": frozenset({"superseded"}),
}
_OPAQUE_TASK_FIELDS = frozenset(
    {
        "job_id",
        "batch_id",
        "index_generation_id",
        "processing_generation",
        "cursor",
        "schema_version",
        "attempt",
    }
)
_NON_JOB_BOUND_OUTBOX_TASKS = frozenset(
    {
        "atlas.maintenance.cleanup_old_index",
        "atlas.maintenance.cleanup_staging",
        "atlas.maintenance.reconcile_jobs",
        "atlas.maintenance.reconcile_storage",
    }
)
_MAX_PROCESSING_PAGE_COUNT = 3_000
_MAX_RETRY_CHECKPOINT_ROWS = _MAX_PROCESSING_PAGE_COUNT
# A supported attempt can produce one processing and one indexing task per
# page, plus the singleton prepare and finalize tasks. Keep terminal mutation
# bounded to that complete product-sized identity set rather than an arbitrary
# pagination limit.
_MAX_CURRENT_ATTEMPT_OUTBOX_ROWS = (_MAX_PROCESSING_PAGE_COUNT * 2) + 2
_PROCESSING_BATCH_CLAIM_LEASE_SECONDS = 300
_PROCESSING_BATCH_CLAIM_HEARTBEAT_SECONDS = 60


def _validate_progress_total_bound(progress_total: int | None) -> None:
    if (
        progress_total is not None
        and progress_total > _MAX_PROCESSING_PAGE_COUNT
    ):
        raise ValueError(
            "processing job progress_total exceeds supported page limit"
        )


@dataclass(frozen=True, slots=True)
class CurrentRowExpectation:
    exists: bool
    status: str | None
    attempt: int | None
    fence: int | None
    claim_owner: str | None
    preimage: object | None = None

    @classmethod
    def absent(cls) -> CurrentRowExpectation:
        return cls(
            exists=False,
            status=None,
            attempt=None,
            fence=None,
            claim_owner=None,
            preimage=None,
        )

    def __post_init__(self) -> None:
        if not self.exists and any(
            value is not None
            for value in (
                self.status,
                self.attempt,
                self.fence,
                self.claim_owner,
                self.preimage,
            )
        ):
            raise ValueError("absent row expectation cannot carry current values")
        if self.exists and self.preimage is None:
            raise ValueError("existing row expectation requires an exact preimage")
        if self.attempt is not None and self.attempt < 0:
            raise ValueError("expected attempt must be non-negative")
        if self.fence is not None and self.fence < 0:
            raise ValueError("expected fence must be non-negative")








class _ProcessingControlAuthorizationDenied(PermissionError):
    def __init__(self, actor: UserRecord):
        super().__init__("processing control request is not authorized")
        self.actor = actor


@dataclass(frozen=True, slots=True)
class DocumentProcessingAcceptanceIdentity:
    """Preallocated identities needed in a complete generation lock plan."""

    job_id: str
    index_generation_id: str
    processing_generation: int
    outbox_id: str
    outbox_work_identity_key: str












def canonical_processing_spec_from_snapshot(
    snapshot: ProcessingExecutionSnapshot,
) -> dict[str, Any]:
    """Project only material processing rules from one accepted execution pin."""

    # These are module constants: importing them does not construct VectorIndex
    # or load/download an embedding model.
    from atlas_production.async_runtime.embedding_model_contract import (
        MODEL_NAME,
        MODEL_REVISION,
    )
    from atlas_production.async_runtime.vector_index import (
        CHUNKING_CONTRACT_VERSION,
        COLLECTION_NAME,
        EMBEDDING_CONTRACT_VERSION,
        INDEX_CONTRACT_VERSION,
        NORMALIZATION_CONTRACT_VERSION,
        VECTOR_DIMENSION,
    )
    from atlas_production.infrastructure.office_renderer_adapter import (
        PDF_PAGE_RASTER_RENDERER_VERSION,
        RENDERER_VERSION,
    )
    from atlas_production.infrastructure.pdf_preview_adapter import (
        PDF_PREVIEW_RENDERER_VERSION,
    )

    profile = snapshot.profile_snapshot
    version_pins: list[dict[str, Any]] = []
    ocr_pins: list[dict[str, Any]] = []
    for item in snapshot.plugin_versions:
        descriptor = item.get("descriptor")
        pin = {
            "plugin_id": item.get("plugin_id"),
            "plugin_version": item.get("plugin_version"),
            "package_digest": item.get("package_digest"),
            "runtime_profile": item.get("runtime_profile"),
            "plugin_kind": item.get("plugin_kind"),
            "entrypoint": (
                descriptor.get("entrypoint")
                if isinstance(descriptor, Mapping)
                else None
            ),
            "output_contract_version": (
                descriptor.get("output_contract_version")
                if isinstance(descriptor, Mapping)
                else None
            ),
        }
        target = (
            ocr_pins
            if "ocr" in str(item.get("plugin_id", "")).lower()
            else version_pins
        )
        target.append(pin)

    runtime_pins = [
        {
            "runtime_profile_id": item.get("runtime_profile_id"),
            "available_packages": item.get("available_packages"),
        }
        for item in snapshot.runtime_profiles
    ]
    material_profile = {
        name: deepcopy(profile.get(name))
        for name in (
            "accepted_media_types",
            "base_parser_plugin_ref",
            "mandatory_processor_plugin_refs",
            "eligible_processor_plugin_refs",
            "plugin_priority",
            "planner_enabled",
            "planner_model_route_id",
            "channel_registry_version",
            "trait_registry_version",
            "max_regions_per_plan",
            "max_modules_per_region",
            "max_total_plugin_invocations",
            "planner_failure_behavior",
        )
    }
    return canonical_processing_spec(
        {
            "schema_version": PROCESSING_SPEC_SCHEMA_VERSION,
            "parser": {
                "profile_id": snapshot.profile_id,
                "profile_revision": snapshot.profile_revision,
                "profile": material_profile,
                "plugins": sorted(
                    version_pins,
                    key=lambda item: (
                        str(item["plugin_id"]),
                        str(item["plugin_version"]),
                    ),
                ),
                "runtimes": sorted(
                    runtime_pins,
                    key=lambda item: str(item["runtime_profile_id"]),
                ),
            },
            "ocr": {
                "plugins": sorted(
                    ocr_pins,
                    key=lambda item: (
                        str(item["plugin_id"]),
                        str(item["plugin_version"]),
                    ),
                ),
                "empty_behavior": "no-ocr-plugin",
            },
            "renderer": {
                "office": RENDERER_VERSION,
                "pdf_page_raster": PDF_PAGE_RASTER_RENDERER_VERSION,
                "pdf_preview": PDF_PREVIEW_RENDERER_VERSION,
            },
            "normalization": {"contract": NORMALIZATION_CONTRACT_VERSION},
            "chunking": {"contract": CHUNKING_CONTRACT_VERSION},
            "embedding": {
                "contract": EMBEDDING_CONTRACT_VERSION,
                "model": MODEL_NAME,
                "revision": MODEL_REVISION,
                "dimension": VECTOR_DIMENSION,
            },
            "indexing": {
                "contract": INDEX_CONTRACT_VERSION,
                "collection": COLLECTION_NAME,
            },
        }
    )


def processing_fingerprint_from_snapshot(
    snapshot: ProcessingExecutionSnapshot,
) -> str:
    return processing_fingerprint(canonical_processing_spec_from_snapshot(snapshot))


@dataclass(frozen=True, slots=True)
class _CanonicalProcessingTarget:
    processing_identity_id: str
    processing_revision_id: str | None
    existing_job: ProcessingJobRecord | None = None
    current_hit: bool = False


def _resolve_canonical_processing_target(
    session: Session,
    *,
    document: document_rows.AtlasDocumentRow,
    execution_snapshot: ProcessingExecutionSnapshot,
    job_kind: str,
) -> _CanonicalProcessingTarget:
    """Resolve/bind one identity and, when needed, allocate its next build."""

    source_sha256 = document.raw_sha256
    source_artifact_id = document.original_artifact_id
    if not source_sha256 or not source_artifact_id:
        raise ValueError("canonical_processing_source_unavailable")
    spec = canonical_processing_spec_from_snapshot(execution_snapshot)
    fingerprint = processing_fingerprint(spec)
    bound_identity_id = document.processing_identity_id
    identity = None
    if bound_identity_id is not None:
        identity = session.get(
            processing_rows.AtlasProcessingIdentityRow,
            bound_identity_id,
        )
        if (
            identity is None
            or identity.source_sha256 != source_sha256
            or identity.processing_fingerprint != fingerprint
            or identity.processing_spec != spec
        ):
            raise DocumentProcessingCurrentnessConflict(
                "document canonical processing identity no longer matches current configuration"
            )

    acquire_owner_locks(
        session,
        identity_keys=(
            f"processing:canonical-identity:{source_sha256}:{fingerprint}",
        ),
    )
    session.expire_all()
    identity = session.scalar(
        select(processing_rows.AtlasProcessingIdentityRow)
        .where(
            processing_rows.AtlasProcessingIdentityRow.source_sha256
            == source_sha256,
            processing_rows.AtlasProcessingIdentityRow.processing_fingerprint
            == fingerprint,
        )
        .with_for_update()
    )
    now_iso = utc_now_iso()
    if identity is None:
        identity = processing_rows.AtlasProcessingIdentityRow(
            processing_identity_id=f"procid-{uuid4().hex}",
            source_sha256=source_sha256,
            processing_fingerprint=fingerprint,
            processing_spec=spec,
            source_artifact_id=source_artifact_id,
            source_artifact_checksum_sha256=source_sha256,
            current_revision_id=None,
            created_at=now_iso,
        )
        session.add(identity)
        session.flush()
    elif identity.processing_spec != spec:
        raise DocumentProcessingCurrentnessConflict(
            "canonical processing fingerprint resolved to a different specification"
        )

    if (
        document.processing_identity_id is not None
        and document.processing_identity_id != identity.processing_identity_id
    ):
        raise DocumentProcessingCurrentnessConflict(
            "document cannot move to a different canonical processing identity"
        )
    document.processing_identity_id = identity.processing_identity_id
    session.flush()

    building = session.scalar(
        select(processing_rows.AtlasProcessingRevisionRow)
        .where(
            processing_rows.AtlasProcessingRevisionRow.processing_identity_id
            == identity.processing_identity_id,
            processing_rows.AtlasProcessingRevisionRow.state == "building",
        )
        .with_for_update()
    )
    if building is not None:
        active_job = session.scalar(
            select(async_rows.AtlasProcessingJobRow).where(
                async_rows.AtlasProcessingJobRow.processing_revision_id
                == building.processing_revision_id,
                async_rows.AtlasProcessingJobRow.status.in_(
                    ("queued", "running", "retry_wait")
                ),
            )
        )
        if active_job is None:
            raise DocumentProcessingCurrentnessConflict(
                "canonical building revision has no active job"
            )
        document.intake_status = "queued"
        document.current_stage = active_job.stage
        document.failure_code = None
        document.processing_job_id = active_job.job_id
        return _CanonicalProcessingTarget(
            identity.processing_identity_id,
            building.processing_revision_id,
            existing_job=_job_record(active_job),
        )

    if job_kind == "ingest":
        if identity.current_revision_id is not None:
            document.intake_status = "ready"
            document.current_stage = "completed"
            document.failure_code = None
            document.processing_job_id = None
            return _CanonicalProcessingTarget(
                identity.processing_identity_id,
                identity.current_revision_id,
                current_hit=True,
            )
        terminal = session.scalar(
            select(processing_rows.AtlasProcessingRevisionRow)
            .where(
                processing_rows.AtlasProcessingRevisionRow.processing_identity_id
                == identity.processing_identity_id,
                processing_rows.AtlasProcessingRevisionRow.state.in_(
                    ("failed", "cancelled")
                ),
            )
            .order_by(
                processing_rows.AtlasProcessingRevisionRow.revision_number.desc()
            )
            .limit(1)
        )
        if terminal is not None:
            document.intake_status = "failed"
            document.current_stage = "completed"
            document.failure_code = "canonical_processing_requires_retry"
            document.processing_job_id = None
            return _CanonicalProcessingTarget(
                identity.processing_identity_id,
                None,
                current_hit=True,
            )

    latest_number = session.scalar(
        select(func.max(processing_rows.AtlasProcessingRevisionRow.revision_number))
        .where(
            processing_rows.AtlasProcessingRevisionRow.processing_identity_id
            == identity.processing_identity_id
        )
    )
    revision = processing_rows.AtlasProcessingRevisionRow(
        processing_revision_id=f"procrev-{uuid4().hex}",
        processing_identity_id=identity.processing_identity_id,
        revision_number=int(latest_number or 0) + 1,
        state="building",
        manifest_digest=None,
        page_artifact_count=None,
        evidence_count=None,
        chunk_count=None,
        index_point_count=None,
        created_at=now_iso,
        finalized_at=None,
    )
    session.add(revision)
    session.flush()
    return _CanonicalProcessingTarget(
        identity.processing_identity_id,
        revision.processing_revision_id,
    )


def _terminalize_canonical_revision(
    session: Session,
    *,
    processing_revision_id: str | None,
    state: str,
) -> None:
    if processing_revision_id is None:
        return
    revision = session.scalar(
        select(processing_rows.AtlasProcessingRevisionRow)
        .where(
            processing_rows.AtlasProcessingRevisionRow.processing_revision_id
            == processing_revision_id
        )
        .with_for_update()
    )
    if revision is None:
        raise DocumentProcessingCurrentnessConflict(
            "canonical processing revision is missing"
        )
    if revision.state == state:
        return
    if revision.state != "building" or state not in {"failed", "cancelled"}:
        raise DocumentProcessingCurrentnessConflict(
            "canonical processing revision terminal transition is invalid"
        )
    revision.state = state
    revision.finalized_at = utc_now_iso()


def _publish_canonical_revision(
    session: Session,
    snapshot: "_GenerationPublicationSnapshot",
    *,
    manifest_digest: str,
) -> None:
    job = snapshot.job
    if job.processing_identity_id is None or job.processing_revision_id is None:
        return
    if snapshot.index.processing_revision_id != job.processing_revision_id:
        raise DocumentProcessingCurrentnessConflict(
            "published index is not linked to the canonical revision"
        )
    revision = session.scalar(
        select(processing_rows.AtlasProcessingRevisionRow)
        .where(
            processing_rows.AtlasProcessingRevisionRow.processing_revision_id
            == job.processing_revision_id,
            processing_rows.AtlasProcessingRevisionRow.processing_identity_id
            == job.processing_identity_id,
        )
        .with_for_update()
    )
    identity = session.scalar(
        select(processing_rows.AtlasProcessingIdentityRow)
        .where(
            processing_rows.AtlasProcessingIdentityRow.processing_identity_id
            == job.processing_identity_id
        )
        .with_for_update()
    )
    if revision is None or identity is None:
        raise DocumentProcessingCurrentnessConflict(
            "canonical publication target is missing"
        )
    if revision.state == "ready":
        if (
            identity.current_revision_id != revision.processing_revision_id
            or revision.manifest_digest != manifest_digest
        ):
            raise DocumentProcessingCurrentnessConflict(
                "canonical publication replay is inconsistent"
            )
        return
    if revision.state != "building":
        raise DocumentProcessingCurrentnessConflict(
            "only a building canonical revision can be published"
        )
    revision.state = "ready"
    revision.manifest_digest = manifest_digest
    revision.page_artifact_count = len(snapshot.pages)
    revision.evidence_count = len(snapshot.evidence)
    revision.chunk_count = snapshot.generation.actual_chunk_count
    revision.index_point_count = snapshot.index.actual_point_count
    revision.finalized_at = utc_now_iso()
    # The database trigger validates the referenced row at pointer update time.
    session.flush()
    identity.current_revision_id = revision.processing_revision_id


def _processing_execution_payload(
    snapshot: ProcessingExecutionSnapshot,
) -> dict[str, Any]:
    body = {
        "schema_version": "processing-execution-snapshot-v1",
        "profile_id": snapshot.profile_id,
        "profile_revision": snapshot.profile_revision,
        "profile_snapshot": deepcopy(snapshot.profile_snapshot),
        "plugin_versions": deepcopy(list(snapshot.plugin_versions)),
        "plugin_packages": deepcopy(list(snapshot.plugin_packages)),
        "runtime_profiles": deepcopy(list(snapshot.runtime_profiles)),
        "acceptance_request_digest": snapshot.acceptance_request_digest,
    }
    payload = {**body, "snapshot_digest": _request_digest(body)}
    return validate_typed_payload(
        payload,
        family="processing execution snapshot",
        allowed_fields=payload,
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )


def processing_execution_snapshot_payload(
    snapshot: ProcessingExecutionSnapshot,
) -> dict[str, Any]:
    """Canonical persisted payload for exact owner-boundary replay checks."""

    return _processing_execution_payload(snapshot)


def _processing_execution_snapshot(
    payload: Mapping[str, Any],
) -> ProcessingExecutionSnapshot:
    values = validate_typed_payload(
        payload,
        family="processing execution snapshot",
        allowed_fields={
            "schema_version",
            "profile_id",
            "profile_revision",
            "profile_snapshot",
            "plugin_versions",
            "plugin_packages",
            "runtime_profiles",
            "acceptance_request_digest",
            "snapshot_digest",
        },
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )
    if values["schema_version"] != "processing-execution-snapshot-v1":
        raise ValueError("processing execution snapshot schema is unsupported")
    body = {key: value for key, value in values.items() if key != "snapshot_digest"}
    if values["snapshot_digest"] != _request_digest(body):
        raise ValueError("processing execution snapshot digest is invalid")
    profile_snapshot = values["profile_snapshot"]
    if not isinstance(profile_snapshot, dict):
        raise ValueError("processing execution profile snapshot is invalid")
    for field_name in (
        "plugin_versions",
        "plugin_packages",
        "runtime_profiles",
    ):
        items = values[field_name]
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise ValueError(f"processing execution {field_name} is invalid")
    return ProcessingExecutionSnapshot(
        profile_id=str(values["profile_id"]),
        profile_revision=int(values["profile_revision"]),
        profile_snapshot=profile_snapshot,
        plugin_versions=tuple(deepcopy(values["plugin_versions"])),
        plugin_packages=tuple(deepcopy(values["plugin_packages"])),
        runtime_profiles=tuple(deepcopy(values["runtime_profiles"])),
        acceptance_request_digest=str(values["acceptance_request_digest"]),
    )








def attach_document_job_request_projections(
    batch: ProcessingJobListBatch,
) -> tuple[DocumentJobRequestAuthorityProjection, ...]:
    if len(batch.authorization_state.users) != 1:
        raise ValueError("processing request actor projection is incomplete")
    authenticated_actor = next(iter(batch.authorization_state.users.values()))
    projections: list[DocumentJobRequestAuthorityProjection] = []
    for job in batch.jobs:
        document = batch.documents.get(job.document_id)
        if document is None or job.document_version_id == "":
            raise ValueError("processing job authority projection is incomplete")
        tag_refs = batch.tag_refs_by_document.get(job.document_id, ())
        if (
            document.scope_type not in {"team", "project"}
            or not document.scope_id
            or (document.scope_type, document.scope_id) not in tag_refs
        ):
            raise ValueError("processing job authority scope is incomplete")
        profile_pin = (
            batch.profile_pins.get(
                (job.document_id, int(job.processing_generation))
            )
            if job.processing_generation is not None
            else None
        )
        if job.processing_generation is not None and profile_pin is None:
            raise ValueError("processing job profile pin is incomplete")
        projections.append(
            DocumentJobRequestAuthorityProjection(
                job=job,
                document=document,
                tag_refs=tag_refs,
                profile_pin=profile_pin,
                authorization_state=batch.authorization_state,
                authenticated_actor=authenticated_actor,
            )
        )
    return tuple(projections)


@dataclass(frozen=True, slots=True)
class ProcessingJobTransition:
    record: ProcessingJobRecord
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not ProcessingJobRecord:
            raise TypeError("processing job transition requires ProcessingJobRecord")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("processing job transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class TaskOutboxRecord:
    outbox_id: str
    task_name: str
    queue_name: str
    payload_schema_version: int
    payload: dict[str, Any]
    celery_task_id: str
    status: str
    claim_owner: str | None
    claim_expires_at: datetime | None
    attempts: int
    available_at: datetime
    last_error_code: str | None
    created_at: datetime
    dispatched_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskOutboxTransition:
    record: TaskOutboxRecord
    expected: CurrentRowExpectation
    allowed_dispatching_predecessor_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.record) is not TaskOutboxRecord:
            raise TypeError("outbox transition requires TaskOutboxRecord")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("outbox transition requires a current expectation")
        if self.allowed_dispatching_predecessor_id is not None and (
            self.expected.exists
            or self.record.status != "pending"
            or not self.allowed_dispatching_predecessor_id
        ):
            raise ValueError(
                "retry successor authority requires one new pending delivery"
            )


@dataclass(frozen=True, slots=True)
class ProcessingBatchClaimRecord:
    batch_id: str
    job_id: str
    attempt: int
    claim_token: str
    unit_kind: str
    unit_start: int
    unit_end: int
    lease_expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessingBatchClaimTransition:
    record: ProcessingBatchClaimRecord
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not ProcessingBatchClaimRecord:
            raise TypeError("batch claim transition requires ProcessingBatchClaimRecord")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("batch claim transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class ProcessingCheckpointRecord:
    job_id: str
    unit_kind: str
    unit_start: int
    unit_end: int
    batch_id: str
    claim_token: str
    fence: int
    input_fingerprint: str
    output_digest: str
    evidence_count: int
    chunk_count: int
    preview_count: int
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessingCheckpointTransition:
    record: ProcessingCheckpointRecord
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not ProcessingCheckpointRecord:
            raise TypeError("checkpoint transition requires ProcessingCheckpointRecord")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("checkpoint transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class ProcessingGenerationProjection:
    document_id: str
    processing_generation: int
    document_version_id: str
    profile_id: str
    profile_revision: int
    status: str
    expected_page_count: int | None
    actual_page_count: int
    expected_evidence_count: int | None
    actual_evidence_count: int
    expected_chunk_count: int | None
    actual_chunk_count: int
    manifest_digest: str | None
    created_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProcessingGenerationTransition:
    record: ProcessingGenerationProjection
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not ProcessingGenerationProjection:
            raise TypeError(
                "generation transition requires ProcessingGenerationProjection"
            )
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("generation transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class IndexGenerationProjection:
    index_generation_id: str
    document_id: str
    document_version_id: str
    source_processing_generation: int
    embedding_profile_id: str
    embedding_profile: dict[str, Any]
    qdrant_collection: str
    status: str
    expected_point_count: int | None
    actual_point_count: int
    expected_fts_count: int | None
    actual_fts_count: int
    manifest_digest: str | None
    supersedes_index_generation_id: str | None
    created_at: datetime
    published_at: datetime | None
    processing_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class IndexGenerationTransition:
    record: IndexGenerationProjection
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not IndexGenerationProjection:
            raise TypeError("index transition requires IndexGenerationProjection")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("index transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class SearchChunkProjection:
    chunk_id: str
    batch_id: str
    document_id: str
    document_version_id: str
    processing_generation: int
    index_generation_id: str
    evidence_id: str
    segment_id: str
    window_ordinal: int
    normalized_text: str
    locator: dict[str, Any]
    content_fingerprint: str
    processing_fingerprint: str
    search_vector: Any | None
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SearchChunkTransition:
    record: SearchChunkProjection
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not SearchChunkProjection:
            raise TypeError("search chunk transition requires SearchChunkProjection")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("search chunk transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class VectorPointMappingRecord:
    index_generation_id: str
    point_id: str
    chunk_id: str
    payload_digest: str
    vector_digest: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VectorPointMappingTransition:
    record: VectorPointMappingRecord
    expected: CurrentRowExpectation

    def __post_init__(self) -> None:
        if type(self.record) is not VectorPointMappingRecord:
            raise TypeError("vector transition requires VectorPointMappingRecord")
        if type(self.expected) is not CurrentRowExpectation:
            raise TypeError("vector transition requires a current expectation")


@dataclass(frozen=True, slots=True)
class IndexPublicationPoint:
    point_id: str
    chunk_id: str
    payload_digest: str
    vector_digest: str


@dataclass(frozen=True, slots=True)
class IndexPublicationManifest:
    index_generation_id: str
    processing_revision_id: str
    qdrant_collection: str | None
    points: tuple[IndexPublicationPoint, ...]
    manifest_digest: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _request_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _processing_profile_plugin_refs(
    profile: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    raw_refs: list[object] = [profile.get("base_parser_plugin_ref")]
    for field_name in (
        "mandatory_processor_plugin_refs",
        "eligible_processor_plugin_refs",
        "plugin_priority",
    ):
        value = profile.get(field_name, [])
        if not isinstance(value, list):
            raise ValueError("processing profile plugin references are invalid")
        raw_refs.extend(value)
    refs: dict[tuple[str, str, str], dict[str, Any]] = {}
    required = {"plugin_id", "plugin_version", "package_digest", "runtime_profile"}
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping) or set(raw_ref) != required:
            raise ValueError("processing profile plugin reference is invalid")
        ref = dict(raw_ref)
        if any(not isinstance(ref[field], str) or not ref[field].strip() for field in required):
            raise ValueError("processing profile plugin reference is invalid")
        identity = (
            ref["plugin_id"],
            ref["plugin_version"],
            ref["package_digest"],
        )
        refs[identity] = ref
    return tuple(refs[key] for key in sorted(refs))


def _processing_acceptance_request_digest(
    *,
    media_type: str,
    document_id: str,
    document_version_id: str,
    job_kind: str,
    created_by: str | None,
    progress_total: int | None,
) -> str:
    return _request_digest(
        {
            "media_type": media_type,
            "document_id": document_id,
            "document_version_id": document_version_id,
            "job_kind": job_kind,
            "created_by": created_by,
            "progress_total": progress_total,
        }
    )


def _capture_processing_execution_snapshot(
    session: Session,
    *,
    media_type: str,
    acceptance_request_digest: str,
    configuration_locks_held: bool = False,
) -> ProcessingExecutionSnapshot:
    """Read one executable config while both configuration owners are frozen."""

    if not media_type.strip():
        raise ValueError("processing media type is required")
    if not configuration_locks_held:
        acquire_mixed_owner_locks(
            session,
            shared_domain_keys=(
                "model-routing:configuration-control",
                "processing-registry:configuration-control",
            ),
        )
    profiles = session.scalars(
        select(processing_rows.AtlasProcessingProfileRevisionRow)
    ).all()
    matches = [
        deepcopy(row.payload)
        for row in profiles
        if row.payload.get("status") == "active"
        and media_type in row.payload.get("accepted_media_types", [])
    ]
    if len(matches) != 1:
        raise ValueError("processing_profile_unavailable")
    profile = matches[0]
    refs = _processing_profile_plugin_refs(profile)

    version_rows = session.scalars(
        select(processing_rows.AtlasPluginVersionRow)
    ).all()
    versions = {
        (row.payload.get("plugin_id"), row.payload.get("plugin_version")): row.payload
        for row in version_rows
    }
    package_rows = session.scalars(
        select(processing_rows.AtlasPluginPackageRow)
    ).all()
    packages = {
        (
            row.payload.get("plugin_id"),
            row.payload.get("plugin_version"),
            row.payload.get("package_digest"),
        ): row.payload
        for row in package_rows
    }
    runtime_rows = session.scalars(
        select(processing_rows.AtlasRuntimeProfileRow)
    ).all()
    runtimes = {
        row.payload.get("runtime_profile_id"): row.payload for row in runtime_rows
    }

    pinned_versions: list[dict[str, Any]] = []
    pinned_packages: list[dict[str, Any]] = []
    pinned_runtimes: dict[str, dict[str, Any]] = {}
    for ref in refs:
        version = versions.get((ref["plugin_id"], ref["plugin_version"]))
        if (
            version is None
            or version.get("status") != "verified"
            or version.get("package_digest") != ref["package_digest"]
            or version.get("runtime_profile") != ref["runtime_profile"]
        ):
            raise ValueError("processing_plugin_revision_unavailable")
        descriptor = version.get("descriptor")
        if (
            not isinstance(version.get("plugin_kind"), str)
            or not isinstance(descriptor, Mapping)
            or not isinstance(descriptor.get("entrypoint"), str)
            or not descriptor["entrypoint"].strip()
        ):
            raise ValueError("processing_plugin_descriptor_unavailable")
        runtime = runtimes.get(ref["runtime_profile"])
        if runtime is None or runtime.get("enabled") is not True:
            raise ValueError("processing_runtime_profile_unavailable")
        package = packages.get(
            (ref["plugin_id"], ref["plugin_version"], ref["package_digest"])
        )
        if version.get("trust_provenance") != "platform_builtin" and package is None:
            raise ValueError("processing_plugin_package_unavailable")
        if package is not None and (
            not isinstance(package.get("artifact_ref"), str)
            or not package["artifact_ref"].strip()
        ):
            raise ValueError("processing_plugin_package_unavailable")
        pinned_versions.append(deepcopy(version))
        if package is not None:
            pinned_packages.append(deepcopy(package))
        pinned_runtimes[ref["runtime_profile"]] = deepcopy(runtime)

    return ProcessingExecutionSnapshot(
        profile_id=str(profile["profile_id"]),
        profile_revision=int(profile["revision"]),
        profile_snapshot=profile,
        plugin_versions=tuple(pinned_versions),
        plugin_packages=tuple(pinned_packages),
        runtime_profiles=tuple(
            pinned_runtimes[key] for key in sorted(pinned_runtimes)
        ),
        acceptance_request_digest=acceptance_request_digest,
    )


def _validated_task_payload(payload: Mapping[str, object]) -> dict[str, object]:
    values = dict(payload)
    if not set(values).issubset(_OPAQUE_TASK_FIELDS):
        raise ValueError("task payload contains non-opaque fields")
    for key, value in values.items():
        if key in {"processing_generation", "schema_version", "attempt"}:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        elif value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"{key} must be an opaque identifier")
    return values


def _new_task_outbox_record(
    *,
    task_name: str,
    queue_name: str,
    payload: Mapping[str, object],
    available_at: datetime,
    last_error_code: str | None = None,
    identity_salt: str | None = None,
) -> TaskOutboxRecord:
    validated = _validated_task_payload(payload)
    identity_payload: dict[str, object] = {
        "task_name": task_name,
        "queue_name": queue_name,
        "payload": validated,
    }
    if identity_salt is not None:
        identity_payload["identity_salt"] = identity_salt
    identity = _request_digest(identity_payload)
    return TaskOutboxRecord(
        outbox_id=f"outbox-{identity[:32]}",
        task_name=task_name,
        queue_name=queue_name,
        payload_schema_version=1,
        payload=validated,
        celery_task_id=f"task-{identity}",
        status="pending",
        claim_owner=None,
        claim_expires_at=None,
        attempts=0,
        available_at=available_at,
        last_error_code=last_error_code,
        created_at=available_at,
        dispatched_at=None,
    )


def _outbox_work_identity_owner_key(
    *,
    task_name: str,
    queue_name: str,
    payload: Mapping[str, object],
) -> str:
    """Serialize one exact logical delivery across every creation path.

    ``attempt`` is included explicitly as well as inside the normalized payload.
    That redundancy makes the D-026 task/queue/payload/attempt identity visible
    in the lock contract while preserving the complete opaque payload digest.
    Physical retry rows remain distinct through their deterministic outbox id.
    """

    return (
        "document:outbox-work:"
        + _request_digest(
            {
                "task_name": task_name,
                "queue_name": queue_name,
                "payload": _validated_task_payload(payload),
                "attempt": payload.get("attempt"),
            }
        )
    )


def document_processing_acceptance_identity(
    *,
    document_id: str,
    idempotency_scope: str,
    idempotency_key: str,
    processing_generation: int = 1,
) -> DocumentProcessingAcceptanceIdentity:
    """Deterministically preallocate one generation graph before row locking."""

    seed = _request_digest(
        {
            "document_id": document_id,
            "idempotency_scope": idempotency_scope,
            "idempotency_key": idempotency_key,
            "processing_generation": processing_generation,
            "kind": "document-processing-acceptance",
        }
    )
    job_id = f"job-{seed[:32]}"
    index_generation_id = f"idxgen-{seed[32:]}"
    payload = {"job_id": job_id, "attempt": 1, "schema_version": 1}
    outbox = _new_task_outbox_record(
        task_name="atlas.processing.prepare_job",
        queue_name="atlas.processing",
        payload=payload,
        available_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )
    return DocumentProcessingAcceptanceIdentity(
        job_id=job_id,
        index_generation_id=index_generation_id,
        processing_generation=processing_generation,
        outbox_id=outbox.outbox_id,
        outbox_work_identity_key=_outbox_work_identity_owner_key(
            task_name=outbox.task_name,
            queue_name=outbox.queue_name,
            payload=outbox.payload,
        ),
    )


def document_processing_acceptance_lock_identities(
    *,
    document_id: str,
    document_version_id: str,
    idempotency_scope: str,
    idempotency_key: str,
    identity: DocumentProcessingAcceptanceIdentity,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"document:allocation:{document_id}",
                f"document:document:{document_id}",
                f"document:version:{document_version_id}",
                f"document:job-idempotency:{idempotency_scope}:{idempotency_key}",
                f"document:job:{identity.job_id}",
                f"document:outbox:{identity.outbox_id}",
                identity.outbox_work_identity_key,
                f"document:generation:{document_id}:{identity.processing_generation}",
                f"document:index:{identity.index_generation_id}",
            }
        )
    )


def _internal_event(
    *,
    operation: str,
    job_id: str | None,
    document_id: str | None,
    actor_id: str | None = "atlas-worker",
    message_code: str = "processing.retry_is_completed",
    attempt: int | None = None,
    status: str | None = None,
    failure_code: str | None = None,
    batch_id: str | None = None,
) -> AuditEventRecord:
    metadata: dict[str, Any] = {"operation": operation}
    for key, value in (
        ("job_id", job_id),
        ("document_id", document_id),
        ("attempt", attempt),
        ("status", status),
        ("failure_code", failure_code),
        ("batch_id", batch_id),
    ):
        if value is not None:
            metadata[key] = value
    return AuditEventRecord(
        event_id=f"audit-{uuid4().hex}",
        event_type=operation,
        actor_id=actor_id,
        target_ref=f"processing-job:{job_id}" if job_id else None,
        project_id=None,
        message_code=message_code,
        metadata=metadata,
        created_at=utc_now_iso(),
        document_id=document_id,
    )


def _processing_control_event(
    *,
    event_type: str,
    actor_id: str,
    job: ProcessingJobRecord,
    command: str,
    message_code: str,
    terminal_status: str,
    replayed: bool = False,
    next_attempt: int | None = None,
    reason: str | None = None,
) -> AuditEventRecord:
    ended_at = job.updated_at
    metadata: dict[str, Any] = {
        "job_id": job.job_id,
        "document_id": job.document_id,
        "command": command,
        "attempt": job.attempt,
        "attempt_started_at": job.attempt_started_at.isoformat(),
        "attempt_ended_at": ended_at.isoformat(),
        "elapsed_seconds": max(
            0, int((ended_at - job.attempt_started_at).total_seconds())
        ),
        "terminal_status": terminal_status,
        "replayed": replayed,
    }
    if next_attempt is not None:
        metadata["next_attempt"] = next_attempt
    if reason is not None:
        metadata["reason"] = reason
    return AuditEventRecord(
        event_id=f"audit-{uuid4().hex}",
        event_type=event_type,
        actor_id=actor_id,
        target_ref=f"processing-job:{job.job_id}",
        project_id=None,
        message_code=message_code,
        metadata=metadata,
        created_at=utc_now_iso(),
        document_id=job.document_id,
    )


def _require_records(
    values: tuple[object, ...],
    expected_type: type[object],
    *,
    field_name: str,
) -> None:
    if any(type(value) is not expected_type for value in values):
        raise TypeError(f"{field_name} requires {expected_type.__name__} records")


def _job_row(record: ProcessingJobRecord) -> async_rows.AtlasProcessingJobRow:
    return async_rows.AtlasProcessingJobRow(**asdict(record))


def _job_record(row: async_rows.AtlasProcessingJobRow) -> ProcessingJobRecord:
    return ProcessingJobRecord(
        **{
            field: getattr(row, field)
            for field in ProcessingJobRecord.__dataclass_fields__
        }
    )


def _job_execution_record(
    row: async_rows.AtlasProcessingJobRow | Mapping[str, Any],
    *,
    batch_claim_token: str | None = None,
) -> ProcessingJobView:
    def value(field: str) -> Any:
        if isinstance(row, Mapping):
            if field in {"processing_identity_id", "processing_revision_id"}:
                return row.get(field)
            return row[field]
        return getattr(row, field)

    return ProcessingJobView(
        **{
            field: value(field)
            for field in ProcessingJobView.__dataclass_fields__
            if field != "batch_claim_token"
        },
        batch_claim_token=batch_claim_token,
    )


def _row_payload(row: object) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name)
        for column in cast(Any, row).__table__.columns
    }


def _record_from_row(row: object, record_type: type[Any]) -> Any:
    return record_type(
        **{
            field: getattr(row, field)
            for field in record_type.__dataclass_fields__
        }
    )


def _document_record(row: document_rows.AtlasDocumentRow) -> DocumentRecord:
    return cast(DocumentRecord, _record_from_row(row, DocumentRecord))


def _document_version_record(
    row: document_rows.AtlasDocumentVersionRow,
) -> DocumentVersionRecord:
    try:
        record = DocumentVersionRecord(**dict(row.payload))
    except (TypeError, ValueError) as exc:
        raise ValueError("document version preimage is invalid") from exc
    if (
        record.document_version_id != row.document_version_id
        or record.document_id != row.document_id
    ):
        raise ValueError("document version row identity is inconsistent")
    return record


def _document_tag_record(
    row: document_rows.AtlasDocumentTagRow,
) -> DocumentTagRecord:
    return cast(DocumentTagRecord, _record_from_row(row, DocumentTagRecord))


def _evidence_record(row: processing_rows.AtlasEvidenceRow) -> EvidenceRecord:
    return cast(EvidenceRecord, _record_from_row(row, EvidenceRecord))


def _page_artifact_row(
    record: EvidencePageArtifact,
) -> processing_rows.AtlasEvidencePageArtifactRow:
    return processing_rows.AtlasEvidencePageArtifactRow(
        id=record.artifact_id,
        tenant_id=record.tenant_id,
        document_version_id=record.document_version_id,
        source_page_index=record.source_page_index,
        renderer_version=record.renderer_version,
        processing_generation=record.processing_generation,
        payload=processing_rows.evidence_page_artifact_payload(record),
    )


def _page_artifact_record(
    row: processing_rows.AtlasEvidencePageArtifactRow,
) -> EvidencePageArtifact:
    try:
        record = EvidencePageArtifact(**dict(row.payload))
    except (TypeError, ValueError) as exc:
        raise ValueError("page artifact preimage is invalid") from exc
    if (
        record.artifact_id != row.id
        or record.tenant_id != row.tenant_id
        or record.document_version_id != row.document_version_id
        or record.source_page_index != row.source_page_index
        or record.renderer_version != row.renderer_version
        or record.processing_generation != row.processing_generation
    ):
        raise ValueError("page artifact row identity is inconsistent")
    return record


def _artifact_binding_record(
    row: artifact_rows.AtlasArtifactScopeBindingRow,
) -> ArtifactScopeBindingRecord:
    return ArtifactScopeBindingRecord(
        binding_id=row.binding_id,
        artifact_id=row.artifact_id,
        binding_kind=cast(Any, row.binding_kind),
        scope_type=cast(Any, row.scope_type),
        scope_id=row.scope_id,
        created_at=row.created_at,
    )


def _outbox_record(row: async_rows.AtlasTaskOutboxRow) -> TaskOutboxRecord:
    return cast(TaskOutboxRecord, _record_from_row(row, TaskOutboxRecord))


def _batch_claim_record(
    row: async_rows.AtlasProcessingBatchClaimRow,
) -> ProcessingBatchClaimRecord:
    return cast(
        ProcessingBatchClaimRecord,
        _record_from_row(row, ProcessingBatchClaimRecord),
    )


def _checkpoint_record(
    row: async_rows.AtlasProcessingCheckpointRow,
) -> ProcessingCheckpointRecord:
    return cast(
        ProcessingCheckpointRecord,
        _record_from_row(row, ProcessingCheckpointRecord),
    )


def _processing_generation_record(
    row: async_rows.AtlasProcessingGenerationRow,
) -> ProcessingGenerationProjection:
    return cast(
        ProcessingGenerationProjection,
        _record_from_row(row, ProcessingGenerationProjection),
    )


def _index_generation_record(
    row: async_rows.AtlasIndexGenerationRow,
) -> IndexGenerationProjection:
    return cast(
        IndexGenerationProjection,
        _record_from_row(row, IndexGenerationProjection),
    )


def _search_chunk_record(
    row: async_rows.AtlasSearchChunkRow,
) -> SearchChunkProjection:
    return cast(
        SearchChunkProjection,
        _record_from_row(row, SearchChunkProjection),
    )


def _vector_mapping_record(
    row: async_rows.AtlasVectorPointMappingRow,
) -> VectorPointMappingRecord:
    return cast(
        VectorPointMappingRecord,
        _record_from_row(row, VectorPointMappingRecord),
    )


def _validate_current_source_version(
    document: document_rows.AtlasDocumentRow,
    row: document_rows.AtlasDocumentVersionRow | None,
    *,
    document_version_id: str,
) -> DocumentVersionRecord:
    if row is None:
        raise ValueError("document_version_not_found")
    try:
        version = DocumentVersionRecord(**dict(row.payload))
    except (TypeError, ValueError) as exc:
        raise ValueError("document_version_invalid") from exc
    if (
        row.document_version_id != document_version_id
        or row.document_id != document.document_id
        or version.document_version_id != document_version_id
        or version.document_id != document.document_id
    ):
        raise ValueError("document_version_mismatch")
    if version.status not in {"active", "staged"}:
        raise ValueError("document_version_not_current_source")
    expected_source_digest = document.raw_sha256 or document.source_digest
    current_source = (
        version.source_kind == document.source_kind
        and version.document_format == document.document_format
        and version.source_digest == expected_source_digest
        and version.original_artifact_id == document.original_artifact_id
        and version.content_type == document.content_type
    )
    if not current_source:
        raise ValueError("document_version_not_current_source")
    return version


def _evidence_row(record: EvidenceRecord) -> processing_rows.AtlasEvidenceRow:
    return processing_rows.AtlasEvidenceRow(
        evidence_id=record.evidence_id,
        document_id=record.document_id,
        document_title=record.document_title,
        locator_label=record.locator_label,
        snippet=record.snippet,
        content=record.content,
        status=record.status,
        document_version_id=record.document_version_id,
        processing_generation=record.processing_generation,
        source_region_id=record.source_region_id,
        channel_id=record.channel_id,
        output_contract_version=record.output_contract_version,
        claim_support_role=record.claim_support_role,
        locator_payload=processing_rows.validate_typed_patch(
            record.locator_payload,
            family="evidence locator metadata",
            allowed_fields=processing_rows.EVIDENCE_LOCATOR_FIELDS,
        ),
        content_fingerprint=record.content_fingerprint,
        processing_fingerprint=record.processing_fingerprint,
        profile_id=record.profile_id,
        profile_revision=record.profile_revision,
        promotion_decision_id=record.promotion_decision_id,
        quality_flag_refs=processing_rows.validate_typed_sequence(
            record.quality_flag_refs,
            family="evidence quality flag refs",
        ),
        trace_ref=record.trace_ref,
        supersedes_evidence_id=record.supersedes_evidence_id,
        evidence_artifact_id=record.evidence_artifact_id,
    )


def _validate_document_identity(change_set: _MutationDefaults) -> None:
    if (
        change_set.document is not None
        and change_set.document.document_id != change_set.document_id
    ):
        raise ValueError("document record does not match named document")
    if any(
        version.document_id != change_set.document_id
        or (
            version.document_version_id != change_set.document_version_id
            and version.status != "superseded"
        )
        for version in change_set.versions
    ):
        raise ValueError("document version does not match named document/version")
    if any(tag.document_id != change_set.document_id for tag in change_set.tags):
        raise ValueError("document tag does not match named document")

    job_ids = {transition.record.job_id for transition in change_set.jobs}
    if change_set.job_id is not None:
        if job_ids and job_ids != {change_set.job_id}:
            raise ValueError("processing job does not match named job")
        job_ids.add(change_set.job_id)
    if any(
        transition.record.document_id != change_set.document_id
        or transition.record.document_version_id != change_set.document_version_id
        or (
            transition.record.processing_generation is None
            and (
                transition.record.job_kind != "reindex"
                or change_set.processing_generation is None
            )
        )
        or (
            transition.record.processing_generation is not None
            and transition.record.processing_generation
            != change_set.processing_generation
        )
        for transition in change_set.jobs
    ):
        raise ValueError("processing job does not match named document/version/generation")
    if any(
        transition.record.job_id not in job_ids
        for transition in (*change_set.batch_claims, *change_set.checkpoints)
    ):
        raise ValueError("processing worker row does not match named job")
    for transition in change_set.outbox:
        payload = _validated_task_payload(transition.record.payload)
        payload_job_id = payload.get("job_id")
        if transition.record.task_name in _NON_JOB_BOUND_OUTBOX_TASKS:
            if "job_id" in payload:
                raise ValueError("non-job outbox task cannot carry a job identity")
            continue
        if payload_job_id not in job_ids:
            raise ValueError("outbox task does not match named job")

    if any(
        record.document_id != change_set.document_id
        or record.document_version_id != change_set.document_version_id
        or record.processing_generation != change_set.processing_generation
        for record in change_set.evidence
    ):
        raise ValueError("evidence does not match named document/version/generation")
    if any(
        record.document_version_id != change_set.document_version_id
        or record.processing_generation != change_set.processing_generation
        for record in change_set.page_artifacts
    ):
        raise ValueError("page projection does not match named version/generation")
    if any(
        transition.record.document_id != change_set.document_id
        or (
            transition.record.status != "retired"
            and (
                transition.record.document_version_id
                != change_set.document_version_id
                or transition.record.processing_generation
                != change_set.processing_generation
            )
        )
        for transition in change_set.generations
    ):
        raise ValueError("generation projection does not match named document/version")
    if any(
        transition.record.document_id != change_set.document_id
        or (
            transition.record.status != "retired"
            and transition.record.document_version_id
            != change_set.document_version_id
        )
        or (
            transition.record.status != "retired"
            and change_set.processing_generation is not None
            and transition.record.source_processing_generation
            != change_set.processing_generation
        )
        for transition in change_set.index_generations
    ):
        raise ValueError("index generation does not match named document/version/generation")

    if (change_set.search_chunks or change_set.vector_mappings) and len(job_ids) != 1:
        raise ValueError("search/vector rows require one named processing job")
    for transition in change_set.search_chunks:
        record = transition.record
        if (
            record.document_id != change_set.document_id
            or record.document_version_id != change_set.document_version_id
            or record.processing_generation != change_set.processing_generation
        ):
            raise ValueError("search chunk does not match named owner graph")


def _validate_document_transition(
    current: document_rows.AtlasDocumentRow | None,
    record: DocumentRecord,
) -> None:
    if current is None:
        if record.lifecycle_status != "active" or record.resource_lifecycle_epoch != 0:
            raise ValueError("new document must begin active at lifecycle epoch 0")
        return
    immutable_provenance = (
        "document_id",
        "source_digest",
        "source_kind",
        "document_format",
        "content_type",
        "source_filename",
        "source_byte_size",
        "source_download_restricted",
        "uploader_actor_id",
        "scope_type",
        "scope_id",
        "original_artifact_id",
        "raw_sha256",
        "uploaded_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_provenance
    ):
        raise ValueError("document source/owner provenance is immutable")
    epoch_delta = record.resource_lifecycle_epoch - current.resource_lifecycle_epoch
    if (record.lifecycle_status, epoch_delta) not in _DOCUMENT_LIFECYCLE_TRANSITIONS.get(
        current.lifecycle_status,
        frozenset(),
    ):
        raise ValueError("document lifecycle transition is not monotonic")


def _validate_artifact_metadata(change_set: _MutationDefaults) -> None:
    if not change_set.artifact_metadata:
        if change_set.artifact_fence is not None:
            raise ValueError("artifact fence requires document artifact metadata")
        return
    raise ValueError(
        "artifact metadata must be finalized by FinalizeArtifactWriteCommand "
        "before document checkpoint publication"
    )


class _MutationDefaults:
    """Shared read-only shape for sealed family mutation inputs."""

    job_id: str | None = None
    expected_document_lifecycle_epoch: int | None = None
    document: DocumentRecord | None = None
    versions: tuple[DocumentVersionRecord, ...] = ()
    tags: tuple[DocumentTagRecord, ...] = ()
    jobs: tuple[ProcessingJobTransition, ...] = ()
    outbox: tuple[TaskOutboxTransition, ...] = ()
    batch_claims: tuple[ProcessingBatchClaimTransition, ...] = ()
    checkpoints: tuple[ProcessingCheckpointTransition, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    page_artifacts: tuple[EvidencePageArtifact, ...] = ()
    generations: tuple[ProcessingGenerationTransition, ...] = ()
    index_generations: tuple[IndexGenerationTransition, ...] = ()
    search_chunks: tuple[SearchChunkTransition, ...] = ()
    vector_mappings: tuple[VectorPointMappingTransition, ...] = ()
    artifact_metadata: tuple[ArtifactMetadataRecord, ...] = ()
    artifact_fence: StorageFence | None = None
    requires_artifact_control_lock: bool = False
    require_current_page_artifact_epoch: bool = True
    coordination_identity_keys: tuple[str, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.document_id or not self.document_version_id:
            raise ValueError("document and version identities are required")
        if self.processing_generation is not None and self.processing_generation < 0:
            raise ValueError("processing generation must be non-negative")
        if type(self.requires_artifact_control_lock) is not bool:
            raise TypeError("artifact control lock requirement must be boolean")
        if type(self.require_current_page_artifact_epoch) is not bool:
            raise TypeError("page artifact epoch requirement must be boolean")
        if self.coordination_identity_keys != tuple(
            sorted(set(self.coordination_identity_keys))
        ) or any(
            not key or ":" not in key
            for key in self.coordination_identity_keys
        ):
            raise ValueError(
                "coordination identity keys must be canonical namespaced identities"
            )
        if self.document is not None and type(self.document) is not DocumentRecord:
            raise TypeError("document requires DocumentRecord")
        _require_records(self.versions, DocumentVersionRecord, field_name="versions")
        _require_records(self.tags, DocumentTagRecord, field_name="tags")
        _require_records(self.jobs, ProcessingJobTransition, field_name="jobs")
        _require_records(self.outbox, TaskOutboxTransition, field_name="outbox")
        _require_records(
            self.batch_claims,
            ProcessingBatchClaimTransition,
            field_name="batch_claims",
        )
        _require_records(
            self.checkpoints,
            ProcessingCheckpointTransition,
            field_name="checkpoints",
        )
        _require_records(self.evidence, EvidenceRecord, field_name="evidence")
        _require_records(
            self.page_artifacts,
            EvidencePageArtifact,
            field_name="page_artifacts",
        )
        _require_records(
            self.generations,
            ProcessingGenerationTransition,
            field_name="generations",
        )
        _require_records(
            self.index_generations,
            IndexGenerationTransition,
            field_name="index_generations",
        )
        _require_records(
            self.search_chunks,
            SearchChunkTransition,
            field_name="search_chunks",
        )
        _require_records(
            self.vector_mappings,
            VectorPointMappingTransition,
            field_name="vector_mappings",
        )
        _require_records(self.audit_events, AuditEventRecord, field_name="audit_events")
        for record in self.artifact_metadata:
            if type(record) not in _DOCUMENT_ARTIFACT_METADATA_TYPES:
                raise TypeError(
                    "document artifact metadata accepts only attempt/blob/artifact/"
                    "binding/lease records"
                )
        has_mutation = any(
            (
                self.document is not None,
                self.versions,
                self.tags,
                self.jobs,
                self.outbox,
                self.batch_claims,
                self.checkpoints,
                self.evidence,
                self.page_artifacts,
                self.generations,
                self.index_generations,
                self.search_chunks,
                self.vector_mappings,
                self.artifact_metadata,
            )
        )
        if has_mutation and not self.audit_events:
            raise ValueError("document processing mutation requires audit events")
        _validate_document_identity(self)
        _validate_artifact_metadata(self)


@dataclass(frozen=True, slots=True)
class _DocumentLifecycleMutation(_MutationDefaults):
    document_id: str
    document_version_id: str
    processing_generation: int | None
    expected_document_lifecycle_epoch: int | None = None
    document: DocumentRecord | None = None
    versions: tuple[DocumentVersionRecord, ...] = ()
    tags: tuple[DocumentTagRecord, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _JobMutation(_MutationDefaults):
    document_id: str
    document_version_id: str
    processing_generation: int | None
    job_id: str | None = None
    expected_document_lifecycle_epoch: int | None = None
    document: DocumentRecord | None = None
    jobs: tuple[ProcessingJobTransition, ...] = ()
    outbox: tuple[TaskOutboxTransition, ...] = ()
    generations: tuple[ProcessingGenerationTransition, ...] = ()
    index_generations: tuple[IndexGenerationTransition, ...] = ()
    coordination_identity_keys: tuple[str, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _BatchMutation(_MutationDefaults):
    document_id: str
    document_version_id: str
    processing_generation: int | None
    job_id: str | None = None
    expected_document_lifecycle_epoch: int | None = None
    document: DocumentRecord | None = None
    jobs: tuple[ProcessingJobTransition, ...] = ()
    outbox: tuple[TaskOutboxTransition, ...] = ()
    batch_claims: tuple[ProcessingBatchClaimTransition, ...] = ()
    checkpoints: tuple[ProcessingCheckpointTransition, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    page_artifacts: tuple[EvidencePageArtifact, ...] = ()
    generations: tuple[ProcessingGenerationTransition, ...] = ()
    index_generations: tuple[IndexGenerationTransition, ...] = ()
    search_chunks: tuple[SearchChunkTransition, ...] = ()
    vector_mappings: tuple[VectorPointMappingTransition, ...] = ()
    require_current_page_artifact_epoch: bool = True
    coordination_identity_keys: tuple[str, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _FinalPublicationMutation(_MutationDefaults):
    document_id: str
    document_version_id: str
    processing_generation: int | None
    job_id: str | None = None
    expected_document_lifecycle_epoch: int | None = None
    document: DocumentRecord | None = None
    versions: tuple[DocumentVersionRecord, ...] = ()
    jobs: tuple[ProcessingJobTransition, ...] = ()
    checkpoints: tuple[ProcessingCheckpointTransition, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    page_artifacts: tuple[EvidencePageArtifact, ...] = ()
    generations: tuple[ProcessingGenerationTransition, ...] = ()
    index_generations: tuple[IndexGenerationTransition, ...] = ()
    search_chunks: tuple[SearchChunkTransition, ...] = ()
    vector_mappings: tuple[VectorPointMappingTransition, ...] = ()
    requires_artifact_control_lock: bool = True
    require_current_page_artifact_epoch: bool = True
    coordination_identity_keys: tuple[str, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()


def _validated_artifact_publication_reader(
    factory: ArtifactPublicationReaderFactory,
    session: Session,
) -> ArtifactPublicationReader:
    reader = factory(session)
    if getattr(reader, "_session", None) is not session:
        raise TypeError(
            "artifact publication reader must use the exact coordinator Session"
        )
    for forbidden in ("commit", "rollback", "query", "session_factory", "new_session"):
        if hasattr(reader, forbidden):
            raise TypeError(
                f"artifact publication reader cannot expose {forbidden}"
            )
    for method_name in ("discover_identity_inventory", "read_locked_current"):
        if not callable(getattr(reader, method_name, None)):
            raise TypeError(
                f"artifact publication reader requires {method_name}"
            )
    return cast(ArtifactPublicationReader, reader)


def _expect_current(
    current: object | None,
    expected: CurrentRowExpectation,
    *,
    family: str,
    status_attr: str | None = None,
    attempt_attr: str | None = None,
    fence_attr: str | None = None,
    claim_attr: str | None = None,
) -> None:
    if type(expected) is not CurrentRowExpectation:
        raise TypeError(f"{family} transition requires CurrentRowExpectation")
    if not expected.exists:
        if current is not None:
            raise DocumentProcessingCurrentnessConflict(f"{family} already exists")
        return
    if current is None:
        raise DocumentProcessingCurrentnessConflict(f"{family} no longer exists")
    checks = (
        (status_attr, expected.status, "status"),
        (attempt_attr, expected.attempt, "attempt"),
        (fence_attr, expected.fence, "fence"),
        (claim_attr, expected.claim_owner, "claim owner"),
    )
    for attribute, value, label in checks:
        if attribute is None:
            if value is not None:
                raise ValueError(f"{family} does not expose expected {label}")
        elif getattr(current, attribute) != value:
            raise DocumentProcessingCurrentnessConflict(
                f"{family} {label} is stale"
            )


def _expect_current_or_exact_replay(
    current: object | None,
    current_record: object | None,
    desired_record: object,
    expected: CurrentRowExpectation,
    *,
    family: str,
    status_attr: str | None = None,
    attempt_attr: str | None = None,
    fence_attr: str | None = None,
    claim_attr: str | None = None,
) -> bool:
    if not expected.exists:
        if current is None:
            return False
        if current_record != desired_record:
            raise DocumentProcessingCurrentnessConflict(
                f"{family} already exists with a different preimage"
            )
        return True
    _expect_current(
        current,
        expected,
        family=family,
        status_attr=status_attr,
        attempt_attr=attempt_attr,
        fence_attr=fence_attr,
        claim_attr=claim_attr,
    )
    if current_record == desired_record:
        return True
    if expected.preimage is not None and current_record != expected.preimage:
        raise DocumentProcessingCurrentnessConflict(
            f"{family} exact preimage is stale"
        )
    return False


def _validate_version_transition(
    current: document_rows.AtlasDocumentVersionRow | None,
    record: DocumentVersionRecord,
) -> None:
    if record.status not in _VERSION_TRANSITIONS:
        raise ValueError("document version status is invalid")
    if current is None:
        if record.status not in {"active", "staged"}:
            raise ValueError("new document version must begin active or staged")
        return
    current_record = _document_version_record(current)
    immutable_fields = tuple(
        field for field in DocumentVersionRecord.__dataclass_fields__ if field != "status"
    )
    if any(
        getattr(record, field) != getattr(current_record, field)
        for field in immutable_fields
    ):
        raise ValueError("document version identity/provenance is immutable")
    if record.status not in _VERSION_TRANSITIONS.get(
        current_record.status,
        frozenset(),
    ):
        raise ValueError("document version transition is not monotonic")


def _validate_tag_preimage(
    current: document_rows.AtlasDocumentTagRow | None,
    record: DocumentTagRecord,
) -> None:
    if current is not None and _document_tag_record(current) != record:
        raise ValueError("document tag identity/provenance is immutable")


def _validate_evidence_transition(
    current: processing_rows.AtlasEvidenceRow | None,
    record: EvidenceRecord,
) -> None:
    if record.status not in _EVIDENCE_TRANSITIONS:
        raise ValueError("evidence status is invalid")
    if current is None:
        if record.status not in {"staged", "ready"}:
            raise ValueError("new evidence must begin staged or ready")
        return
    current_record = _evidence_record(current)
    immutable_fields = tuple(
        field for field in EvidenceRecord.__dataclass_fields__ if field != "status"
    )
    if any(
        getattr(record, field) != getattr(current_record, field)
        for field in immutable_fields
    ):
        raise ValueError("evidence identity/provenance is immutable")
    if record.status not in _EVIDENCE_TRANSITIONS.get(
        current_record.status,
        frozenset(),
    ):
        raise ValueError("evidence transition is not monotonic")


def _validate_page_artifact_preimage(
    current: processing_rows.AtlasEvidencePageArtifactRow | None,
    record: EvidencePageArtifact,
) -> None:
    if current is not None and _page_artifact_record(current) != record:
        raise ValueError("page artifact identity/provenance is immutable")


def _validate_checkpoint_preimage(
    current: async_rows.AtlasProcessingCheckpointRow | None,
    record: ProcessingCheckpointRecord,
) -> None:
    if (
        record.unit_start < 1
        or record.unit_end < record.unit_start
        or record.fence < 0
        or min(
            record.evidence_count,
            record.chunk_count,
            record.preview_count,
        )
        < 0
    ):
        raise ValueError("processing checkpoint is invalid")
    if current is not None and _checkpoint_record(current) != record:
        raise ValueError("processing checkpoint identity/provenance is immutable")


def _validate_vector_preimage(
    current: async_rows.AtlasVectorPointMappingRow | None,
    record: VectorPointMappingRecord,
) -> None:
    if current is not None and _vector_mapping_record(current) != record:
        raise ValueError("vector point mapping identity/provenance is immutable")


def _validate_job_transition(
    current: async_rows.AtlasProcessingJobRow | None,
    record: ProcessingJobRecord,
    *,
    allow_operator_retry: bool = False,
) -> None:
    _validate_progress_total_bound(record.progress_total)
    if len(record.request_fingerprint) != 64 or any(
        character not in "0123456789abcdef"
        for character in record.request_fingerprint
    ):
        raise ValueError("processing job request fingerprint is invalid")
    if record.attempt < 1 or record.fence < 0:
        raise ValueError("processing job attempt/fence is invalid")
    if current is None:
        if record.status != "queued" or record.attempt != 1 or record.fence != 0:
            raise ValueError("new processing job must begin queued at attempt 1/fence 0")
        return
    operator_retry = (
        allow_operator_retry
        and current.status in {"failed", "cancelled"}
        and record.status == "queued"
    )
    immutable_fields = (
        "job_id",
        "job_kind",
        "document_id",
        "document_version_id",
        "processing_generation",
        "index_generation_id",
        "idempotency_scope",
        "idempotency_key",
        "request_fingerprint",
        "created_by",
        "created_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_fields
    ) or (
        not operator_retry
        and record.attempt_started_at != current.attempt_started_at
    ):
        raise ValueError("processing job identity/provenance is immutable")
    if not operator_retry and record.status not in _JOB_TRANSITIONS.get(
        current.status, frozenset()
    ):
        raise ValueError("processing job transition is not monotonic")
    if operator_retry:
        if (
            record.attempt != current.attempt + 1
            or record.fence != current.fence + 1
            or record.lease_owner is not None
            or record.lease_expires_at is not None
        ):
            raise ValueError("operator retry must advance attempt/fence exactly once")
    elif record.attempt != current.attempt:
        raise ValueError("processing transition cannot change logical attempt")
    if not operator_retry and (
        record.fence < current.fence or record.fence > current.fence + 1
    ):
        raise ValueError("processing job fence must stay current or advance once")
    if record.progress_current < current.progress_current:
        raise ValueError("processing job progress cannot decrease")


def _validate_outbox_transition(
    current: async_rows.AtlasTaskOutboxRow | None,
    record: TaskOutboxRecord,
) -> None:
    _validated_task_payload(record.payload)
    if current is None:
        if (
            record.status != "pending"
            or record.attempts != 0
            or record.claim_owner is not None
        ):
            raise ValueError("new outbox task must begin unclaimed and pending")
        return
    immutable_fields = (
        "outbox_id",
        "task_name",
        "queue_name",
        "payload_schema_version",
        "payload",
        "celery_task_id",
        "created_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_fields
    ):
        raise ValueError("outbox task identity/provenance is immutable")
    if record.status not in _OUTBOX_TRANSITIONS.get(current.status, frozenset()):
        raise ValueError("outbox transition is not monotonic")
    if record.attempts < current.attempts or record.attempts > current.attempts + 1:
        raise ValueError("outbox attempts must stay current or advance once")


def _validate_outbox_cas(
    current: async_rows.AtlasTaskOutboxRow | None,
    transition: TaskOutboxTransition,
) -> bool:
    current_record = _outbox_record(current) if current is not None else None
    replay = _expect_current_or_exact_replay(
        current,
        current_record,
        transition.record,
        transition.expected,
        family="task outbox",
        status_attr="status",
        attempt_attr="attempts",
        claim_attr="claim_owner",
    )
    if replay:
        assert current is not None
        _validate_outbox_transition(current, transition.record)
        return True
    _validate_outbox_transition(current, transition.record)
    return replay


_OUTBOX_NOT_LOADED = object()


def _publish_outbox_cas(
    session: Session,
    transition: TaskOutboxTransition,
    *,
    current: async_rows.AtlasTaskOutboxRow | None | object = _OUTBOX_NOT_LOADED,
    reconciliation_at: datetime | None = None,
) -> bool:
    if not transition.expected.exists:
        work_identity_key = _outbox_work_identity_owner_key(
            task_name=transition.record.task_name,
            queue_name=transition.record.queue_name,
            payload=transition.record.payload,
        )
        acquire_owner_locks(
            session,
            identity_keys=(
                work_identity_key,
                f"document:outbox:{transition.record.outbox_id}",
            ),
        )
        active_identity_rows = session.scalars(
            select(async_rows.AtlasTaskOutboxRow)
            .where(
                async_rows.AtlasTaskOutboxRow.task_name
                == transition.record.task_name,
                async_rows.AtlasTaskOutboxRow.queue_name
                == transition.record.queue_name,
                async_rows.AtlasTaskOutboxRow.payload
                == dict(transition.record.payload),
                async_rows.AtlasTaskOutboxRow.status.in_(
                    ("pending", "dispatching")
                ),
            )
            .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
            .limit(2)
            .with_for_update()
        ).all()
        active_others = tuple(
            row
            for row in active_identity_rows
            if row.outbox_id != transition.record.outbox_id
        )
        allowed_predecessor_id = transition.allowed_dispatching_predecessor_id
        if allowed_predecessor_id is not None:
            expected_successor = _new_task_outbox_record(
                task_name=transition.record.task_name,
                queue_name=transition.record.queue_name,
                payload=transition.record.payload,
                available_at=transition.record.available_at,
                last_error_code=transition.record.last_error_code,
                identity_salt=f"retry-after:{allowed_predecessor_id}",
            )
            allowed = (
                transition.record.outbox_id == expected_successor.outbox_id
                and len(active_others) == 1
                and active_others[0].outbox_id == allowed_predecessor_id
                and active_others[0].status == "dispatching"
            )
        else:
            allowed = not active_others and len(active_identity_rows) <= 1
        if not allowed:
            raise DocumentProcessingCurrentnessConflict(
                "active outbox work identity already has a delivery"
            )
    if current is _OUTBOX_NOT_LOADED:
        acquire_owner_locks(
            session,
            identity_keys=(
                f"document:outbox:{transition.record.outbox_id}",
            ),
        )
        current = session.scalar(
            select(async_rows.AtlasTaskOutboxRow)
            .where(
                async_rows.AtlasTaskOutboxRow.outbox_id
                == transition.record.outbox_id
            )
            .with_for_update()
        )
    locked_current = cast(
        async_rows.AtlasTaskOutboxRow | None,
        current,
    )
    replay = _validate_outbox_cas(locked_current, transition)
    if reconciliation_at is not None:
        desired_status = transition.record.status
        if desired_status not in {"dispatched", "cancelled"} or (
            transition.record.claim_owner is not None
            or transition.record.claim_expires_at is not None
        ):
            raise DocumentProcessingCurrentnessConflict(
                "outbox reconciliation desired state is invalid"
            )
        if desired_status == "dispatched" and (
            transition.record.dispatched_at != reconciliation_at
            or transition.record.last_error_code is not None
        ):
            raise DocumentProcessingCurrentnessConflict(
                "outbox reconciliation desired state is invalid"
            )
        if desired_status == "cancelled" and (
            transition.record.dispatched_at is not None
            or transition.record.last_error_code
            != "dispatch_claim_expired_superseded"
        ):
            raise DocumentProcessingCurrentnessConflict(
                "outbox reconciliation desired state is invalid"
            )
        if replay:
            return False
        if locked_current is None:
            raise DocumentProcessingCurrentnessConflict(
                "outbox reconciliation preimage no longer exists"
            )
        current_record = _outbox_record(locked_current)
        reconciliation_mutable_fields = {
            "status",
            "claim_owner",
            "claim_expires_at",
            "last_error_code",
        }
        if desired_status == "dispatched":
            reconciliation_mutable_fields.add("dispatched_at")
        if any(
            getattr(transition.record, field) != getattr(current_record, field)
            for field in TaskOutboxRecord.__dataclass_fields__
            if field not in reconciliation_mutable_fields
        ):
            raise ValueError(
                "outbox reconciliation cannot publish non-reconciliation state"
            )
        if (
            locked_current.status != "dispatching"
            or locked_current.claim_owner is None
            or locked_current.claim_expires_at is None
            or locked_current.claim_expires_at > reconciliation_at
        ):
            raise DocumentProcessingCurrentnessConflict(
                "outbox claim is not expired at reconciliation preimage"
            )
    if replay:
        return False
    session.merge(async_rows.AtlasTaskOutboxRow(**asdict(transition.record)))
    return True


def _publish_outbox_cas_many(
    session: Session,
    transitions: tuple[TaskOutboxTransition, ...],
) -> tuple[bool, ...]:
    indexed = cast(
        dict[str, TaskOutboxTransition],
        _unique_index(
            transitions,
            key=lambda transition: transition.record.outbox_id,
            family="task outbox",
        ),
    )
    ordered_ids = tuple(sorted(indexed))
    if not ordered_ids:
        return ()
    acquire_owner_locks(
        session,
        identity_keys=(
            *(
                f"document:outbox:{outbox_id}" for outbox_id in ordered_ids
            ),
            *(
                _outbox_work_identity_owner_key(
                    task_name=transition.record.task_name,
                    queue_name=transition.record.queue_name,
                    payload=transition.record.payload,
                )
                for transition in indexed.values()
                if not transition.expected.exists
            ),
        ),
    )
    current_rows = session.scalars(
        select(async_rows.AtlasTaskOutboxRow)
        .where(async_rows.AtlasTaskOutboxRow.outbox_id.in_(ordered_ids))
        .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
        .with_for_update()
    ).all()
    current_by_id = {
        row.outbox_id: row
        for row in current_rows
    }
    if len(current_by_id) != len(current_rows) or any(
        outbox_id not in indexed for outbox_id in current_by_id
    ):
        raise DocumentProcessingCurrentnessConflict(
            "task outbox preimage set is inconsistent"
        )
    for outbox_id in ordered_ids:
        _validate_outbox_cas(
            current_by_id.get(outbox_id),
            indexed[outbox_id],
        )
    return tuple(
        _publish_outbox_cas(
            session,
            indexed[outbox_id],
            current=current_by_id.get(outbox_id),
        )
        for outbox_id in ordered_ids
    )


def _publish_job_lease_reconciliation_cas(
    session: Session,
    transition: ProcessingJobTransition,
    *,
    current: async_rows.AtlasProcessingJobRow,
    reconciliation_at: datetime | None = None,
) -> bool:
    """Own lease-only Job claim/reconciliation transitions on a locked row."""

    if transition.record.status not in {"running", "retry_wait"}:
        raise ValueError("job lease CAS cannot publish a lifecycle terminal state")
    current_record = _job_record(current)
    lease_mutable_fields = {
        "status",
        "lease_owner",
        "lease_expires_at",
        "failure_code",
        "failure_detail",
        "updated_at",
    }
    if any(
        getattr(transition.record, field) != getattr(current_record, field)
        for field in ProcessingJobRecord.__dataclass_fields__
        if field not in lease_mutable_fields
    ):
        raise ValueError("job lease CAS cannot publish non-lease job state")
    replay = _expect_current_or_exact_replay(
        current,
        current_record,
        transition.record,
        transition.expected,
        family="processing job lease",
        status_attr="status",
        attempt_attr="attempt",
        fence_attr="fence",
        claim_attr="lease_owner",
    )
    if reconciliation_at is not None:
        if (
            transition.record.status != "retry_wait"
            or transition.record.updated_at != reconciliation_at
            or transition.record.lease_owner is not None
            or transition.record.lease_expires_at is not None
        ):
            raise DocumentProcessingCurrentnessConflict(
                "job reconciliation desired state is invalid"
            )
        if replay:
            _validate_job_transition(current, transition.record)
            return False
        if (
            current.status != "running"
            or current.lease_owner is None
            or current.lease_expires_at is None
            or current.lease_expires_at > reconciliation_at
        ):
            raise DocumentProcessingCurrentnessConflict(
                "running job lease is not expired at reconciliation preimage"
            )
    elif current.status == "running" and transition.record.status == "retry_wait":
        raise ValueError("running lease reconciliation requires a timestamp")
    _validate_job_transition(current, transition.record)
    if replay:
        return False
    session.merge(_job_row(transition.record))
    return True


def _validate_batch_transition(
    current: async_rows.AtlasProcessingBatchClaimRow | None,
    record: ProcessingBatchClaimRecord,
    *,
    allow_claim_token_takeover: bool = False,
) -> None:
    if record.attempt < 1 or record.unit_start < 1 or record.unit_end < record.unit_start:
        raise ValueError("processing batch claim is invalid")
    if current is None:
        return
    immutable_fields = (
        "batch_id",
        "job_id",
        "unit_kind",
        "unit_start",
        "unit_end",
        "created_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_fields
    ):
        raise ValueError("processing batch identity/provenance is immutable")
    if record.attempt < current.attempt or record.attempt > current.attempt + 1:
        raise ValueError("processing batch claim attempt must stay current or advance once")
    if (
        record.attempt == current.attempt
        and record.claim_token != current.claim_token
        and not allow_claim_token_takeover
    ):
        raise ValueError("processing batch claim token cannot be taken over")


def _publish_batch_lease_cas(
    session: Session,
    transition: ProcessingBatchClaimTransition,
    *,
    current: async_rows.AtlasProcessingBatchClaimRow | None,
    claim_takeover_at: datetime | None = None,
) -> bool:
    """Own one locked batch-claim lease transition with an exact preimage."""

    current_record = _batch_claim_record(current) if current is not None else None
    replay = _expect_current_or_exact_replay(
        current,
        current_record,
        transition.record,
        transition.expected,
        family="processing batch lease",
        attempt_attr="attempt",
        claim_attr="claim_token",
    )
    validation_current = (
        None if replay and not transition.expected.exists else current
    )
    allow_claim_token_takeover = claim_takeover_at is not None
    if allow_claim_token_takeover and (
        current is None
        or current.lease_expires_at > claim_takeover_at
        or transition.record.updated_at != claim_takeover_at
    ):
        raise DocumentProcessingCurrentnessConflict(
            "processing batch claim is not expired at takeover preimage"
        )
    _validate_batch_transition(
        validation_current,
        transition.record,
        allow_claim_token_takeover=allow_claim_token_takeover,
    )
    if replay:
        return False
    session.merge(
        async_rows.AtlasProcessingBatchClaimRow(**asdict(transition.record))
    )
    return True


def _delete_reconciliation_rows(
    session: Session,
    rows: tuple[object, ...],
    *,
    allowed_types: tuple[type[object], ...],
) -> int:
    """Own bounded cleanup/claim-consumption deletes selected under row locks."""

    if any(type(row) not in allowed_types for row in rows):
        raise TypeError("reconciliation cleanup received an unsupported row")
    for row in rows:
        session.delete(row)
    return len(rows)


def _validate_generation_transition(
    current: async_rows.AtlasProcessingGenerationRow | None,
    record: ProcessingGenerationProjection,
    *,
    allow_operator_retry: bool = False,
) -> None:
    if current is None:
        if record.status != "building":
            raise ValueError("new processing generation must begin building")
        return
    immutable_fields = (
        "document_id",
        "processing_generation",
        "document_version_id",
        "profile_id",
        "profile_revision",
        "created_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_fields
    ):
        raise ValueError("processing generation identity/provenance is immutable")
    operator_retry = (
        allow_operator_retry
        and current.status == "failed"
        and record.status == "building"
    )
    if not operator_retry and record.status not in _GENERATION_TRANSITIONS.get(
        current.status, frozenset()
    ):
        raise ValueError("processing generation transition is not monotonic")
    for count in ("actual_page_count", "actual_evidence_count", "actual_chunk_count"):
        if getattr(record, count) < getattr(current, count):
            raise ValueError("processing generation counts cannot decrease")


def _validate_index_transition(
    current: async_rows.AtlasIndexGenerationRow | None,
    record: IndexGenerationProjection,
) -> None:
    if current is None:
        if record.status != "building":
            raise ValueError("new index generation must begin building")
        return
    immutable_fields = (
        "index_generation_id",
        "processing_revision_id",
        "document_id",
        "document_version_id",
        "source_processing_generation",
        "embedding_profile_id",
        "embedding_profile",
        "qdrant_collection",
        "supersedes_index_generation_id",
        "created_at",
    )
    if any(
        getattr(record, field) != getattr(current, field)
        for field in immutable_fields
    ):
        raise ValueError("index generation identity/provenance is immutable")
    if record.status not in _GENERATION_TRANSITIONS.get(current.status, frozenset()):
        raise ValueError("index generation transition is not monotonic")
    if (
        record.actual_point_count < current.actual_point_count
        or record.actual_fts_count < current.actual_fts_count
    ):
        raise ValueError("index generation counts cannot decrease")


def _validate_search_chunk_transition(
    current: async_rows.AtlasSearchChunkRow | None,
    record: SearchChunkProjection,
) -> None:
    if current is None:
        if record.status != "staged":
            raise ValueError("new search chunk must begin staged")
        return
    if record.status not in _SEARCH_CHUNK_TRANSITIONS.get(
        current.status, frozenset()
    ):
        raise ValueError("search chunk transition is not monotonic")
    immutable_fields = (
        "chunk_id",
        "batch_id",
        "document_id",
        "document_version_id",
        "processing_generation",
        "index_generation_id",
        "evidence_id",
        "segment_id",
        "window_ordinal",
        "normalized_text",
        "locator",
        "content_fingerprint",
        "processing_fingerprint",
        "created_at",
    )
    if any(getattr(record, field) != getattr(current, field) for field in immutable_fields):
        raise ValueError("search chunk identity/provenance is immutable")


def _unique_index(
    values: tuple[Any, ...],
    *,
    key: Callable[[Any], object],
    family: str,
) -> dict[object, Any]:
    indexed: dict[object, Any] = {}
    for value in values:
        identity = key(value)
        if identity in indexed:
            raise ValueError(f"duplicate {family} identity in change set")
        indexed[identity] = value
    return indexed


def _requires_index_supersedes_parent(
    transition: IndexGenerationTransition,
) -> bool:
    if not transition.expected.exists:
        return True
    preimage = transition.expected.preimage
    if not isinstance(preimage, IndexGenerationProjection):
        raise TypeError("index transition requires an index generation preimage")
    return (
        preimage.supersedes_index_generation_id
        != transition.record.supersedes_index_generation_id
    )


@dataclass(frozen=True, slots=True)
class _DocumentProcessingCandidates:
    versions: dict[str, DocumentVersionRecord]
    tags: dict[tuple[str, str, str], DocumentTagRecord]
    jobs: dict[str, ProcessingJobTransition]
    outbox: dict[str, TaskOutboxTransition]
    batches: dict[str, ProcessingBatchClaimTransition]
    checkpoints: dict[
        tuple[str, str, int, int],
        ProcessingCheckpointTransition,
    ]
    generations: dict[
        tuple[str, int],
        ProcessingGenerationTransition,
    ]
    indexes: dict[str, IndexGenerationTransition]
    evidence: dict[str, EvidenceRecord]
    pages: dict[str, EvidencePageArtifact]
    chunks: dict[str, SearchChunkTransition]
    vectors: dict[tuple[str, str], VectorPointMappingTransition]


def _document_processing_candidates(
    change_set: _MutationDefaults,
) -> _DocumentProcessingCandidates:
    return _DocumentProcessingCandidates(
        versions=cast(
            dict[str, DocumentVersionRecord],
            _unique_index(
                change_set.versions,
                key=lambda item: item.document_version_id,
                family="document version",
            ),
        ),
        tags=cast(
            dict[tuple[str, str, str], DocumentTagRecord],
            _unique_index(
                change_set.tags,
                key=lambda item: (item.document_id, item.tag_type, item.tag_id),
                family="document tag",
            ),
        ),
        jobs=cast(
            dict[str, ProcessingJobTransition],
            _unique_index(
                change_set.jobs,
                key=lambda item: item.record.job_id,
                family="processing job",
            ),
        ),
        outbox=cast(
            dict[str, TaskOutboxTransition],
            _unique_index(
                change_set.outbox,
                key=lambda item: item.record.outbox_id,
                family="task outbox",
            ),
        ),
        batches=cast(
            dict[str, ProcessingBatchClaimTransition],
            _unique_index(
                change_set.batch_claims,
                key=lambda item: item.record.batch_id,
                family="processing batch",
            ),
        ),
        checkpoints=cast(
            dict[tuple[str, str, int, int], ProcessingCheckpointTransition],
            _unique_index(
                change_set.checkpoints,
                key=lambda item: (
                    item.record.job_id,
                    item.record.unit_kind,
                    item.record.unit_start,
                    item.record.unit_end,
                ),
                family="processing checkpoint",
            ),
        ),
        generations=cast(
            dict[tuple[str, int], ProcessingGenerationTransition],
            _unique_index(
                change_set.generations,
                key=lambda item: (
                    item.record.document_id,
                    item.record.processing_generation,
                ),
                family="processing generation",
            ),
        ),
        indexes=cast(
            dict[str, IndexGenerationTransition],
            _unique_index(
                change_set.index_generations,
                key=lambda item: item.record.index_generation_id,
                family="index generation",
            ),
        ),
        evidence=cast(
            dict[str, EvidenceRecord],
            _unique_index(
                change_set.evidence,
                key=lambda item: item.evidence_id,
                family="evidence",
            ),
        ),
        pages=cast(
            dict[str, EvidencePageArtifact],
            _unique_index(
                change_set.page_artifacts,
                key=lambda item: item.artifact_id,
                family="page artifact",
            ),
        ),
        chunks=cast(
            dict[str, SearchChunkTransition],
            _unique_index(
                change_set.search_chunks,
                key=lambda item: item.record.chunk_id,
                family="search chunk",
            ),
        ),
        vectors=cast(
            dict[tuple[str, str], VectorPointMappingTransition],
            _unique_index(
                change_set.vector_mappings,
                key=lambda item: (
                    item.record.index_generation_id,
                    item.record.point_id,
                ),
                family="vector point mapping",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class _DocumentProcessingGraphIdentities:
    version_ids: tuple[str, ...]
    tag_keys: tuple[tuple[str, str, str], ...]
    job_ids: tuple[str, ...]
    outbox_ids: tuple[str, ...]
    batch_ids: tuple[str, ...]
    checkpoint_keys: tuple[tuple[str, str, int, int], ...]
    generation_keys: tuple[tuple[str, int], ...]
    index_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    page_ids: tuple[str, ...]
    storage_artifact_ids: tuple[str, ...]
    storage_binding_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    vector_keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _LockedDocumentProcessingMutation:
    """Proof that one exact graph identity set is coordinated in this Session."""

    _session: Session
    identities: _DocumentProcessingGraphIdentities
    owner_lock_keys: tuple[str, ...]
    artifact_lock_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LockedFinalGenerationPublication:
    """Capability issued only after the locked artifact graph reread succeeds."""

    _session: Session
    mutation: _LockedDocumentProcessingMutation
    change_set: _FinalPublicationMutation
    artifact_inventory: CurrentArtifactLockInventory
    artifact_graph: CurrentArtifactGraphResult


@dataclass(frozen=True, slots=True)
class _DocumentProcessingDiscoveredGraph:
    chunks: tuple[async_rows.AtlasSearchChunkRow, ...] = ()
    batches: tuple[async_rows.AtlasProcessingBatchClaimRow, ...] = ()
    checkpoints: tuple[async_rows.AtlasProcessingCheckpointRow, ...] = ()
    jobs: tuple[async_rows.AtlasProcessingJobRow, ...] = ()
    indexes: tuple[async_rows.AtlasIndexGenerationRow, ...] = ()
    evidence: tuple[processing_rows.AtlasEvidenceRow, ...] = ()
    pages: tuple[processing_rows.AtlasEvidencePageArtifactRow, ...] = ()
    storage_bindings: tuple[
        artifact_rows.AtlasArtifactScopeBindingRow, ...
    ] = ()


def _discover_document_processing_graph(
    session: Session,
    change_set: _MutationDefaults,
    candidates: _DocumentProcessingCandidates,
) -> _DocumentProcessingDiscoveredGraph:
    def read(
        keys: tuple[object, ...],
        statement: Any,
        *,
        key: Callable[[Any], object],
        family: str,
    ) -> tuple[Any, ...]:
        if not keys:
            return ()
        expected = frozenset(keys)
        rows = tuple(session.scalars(statement).all())
        observed: set[object] = set()
        for row in rows:
            identity = key(row)
            if identity not in expected or identity in observed:
                raise DocumentProcessingCurrentnessConflict(
                    f"{family} discovery set is inconsistent"
                )
            observed.add(identity)
        return rows

    chunk_ids = tuple(
        sorted(
            {
                transition.record.chunk_id
                for transition in candidates.vectors.values()
                if transition.record.chunk_id not in candidates.chunks
            }
        )
    )
    chunks = cast(
        tuple[async_rows.AtlasSearchChunkRow, ...],
        read(
            cast(tuple[object, ...], chunk_ids),
            select(async_rows.AtlasSearchChunkRow)
            .where(async_rows.AtlasSearchChunkRow.chunk_id.in_(chunk_ids))
            .order_by(async_rows.AtlasSearchChunkRow.chunk_id),
            key=lambda row: row.chunk_id,
            family="search chunk",
        ),
    )

    batch_ids = {
        transition.record.batch_id
        for transition in candidates.checkpoints.values()
    }
    batch_ids.update(
        transition.record.batch_id for transition in candidates.chunks.values()
    )
    batch_ids.update(row.batch_id for row in chunks)
    batch_ids.update(
        str(batch_id)
        for transition in candidates.outbox.values()
        if isinstance((batch_id := transition.record.payload.get("batch_id")), str)
    )
    batch_ids.difference_update(candidates.batches)
    ordered_batch_ids = tuple(sorted(batch_ids))
    batches = cast(
        tuple[async_rows.AtlasProcessingBatchClaimRow, ...],
        read(
            cast(tuple[object, ...], ordered_batch_ids),
            select(async_rows.AtlasProcessingBatchClaimRow)
            .where(
                async_rows.AtlasProcessingBatchClaimRow.batch_id.in_(
                    ordered_batch_ids
                )
            )
            .order_by(async_rows.AtlasProcessingBatchClaimRow.batch_id),
            key=lambda row: row.batch_id,
            family="processing batch",
        ),
    )
    checkpoints = cast(
        tuple[async_rows.AtlasProcessingCheckpointRow, ...],
        read(
            cast(tuple[object, ...], ordered_batch_ids),
            select(async_rows.AtlasProcessingCheckpointRow)
            .where(
                async_rows.AtlasProcessingCheckpointRow.batch_id.in_(
                    ordered_batch_ids
                )
            )
            .order_by(async_rows.AtlasProcessingCheckpointRow.batch_id),
            key=lambda row: row.batch_id,
            family="processing checkpoint by batch",
        ),
    )

    job_ids: set[str] = set()
    if change_set.job_id is not None:
        job_ids.add(change_set.job_id)
    job_ids.update(
        payload_job_id
        for transition in candidates.outbox.values()
        if isinstance(
            (payload_job_id := transition.record.payload.get("job_id")),
            str,
        )
    )
    job_ids.update(
        transition.record.job_id for transition in candidates.batches.values()
    )
    job_ids.update(
        transition.record.job_id for transition in candidates.checkpoints.values()
    )
    job_ids.update(row.job_id for row in batches)
    job_ids.difference_update(candidates.jobs)
    ordered_job_ids = tuple(sorted(job_ids))
    jobs = cast(
        tuple[async_rows.AtlasProcessingJobRow, ...],
        read(
            cast(tuple[object, ...], ordered_job_ids),
            select(async_rows.AtlasProcessingJobRow)
            .where(async_rows.AtlasProcessingJobRow.job_id.in_(ordered_job_ids))
            .order_by(async_rows.AtlasProcessingJobRow.job_id),
            key=lambda row: row.job_id,
            family="processing job",
        ),
    )

    index_ids = {
        transition.record.index_generation_id
        for transition in candidates.jobs.values()
    }
    index_ids.update(
        transition.record.index_generation_id
        for transition in candidates.chunks.values()
    )
    index_ids.update(
        transition.record.index_generation_id
        for transition in candidates.vectors.values()
    )
    index_ids.update(
        record.supersedes_index_generation_id
        for transition in candidates.indexes.values()
        if (record := transition.record).supersedes_index_generation_id is not None
    )
    index_ids.update(row.index_generation_id for row in chunks)
    index_ids.update(row.index_generation_id for row in jobs)
    index_ids.update(
        str(index_id)
        for transition in candidates.outbox.values()
        if isinstance(
            (index_id := transition.record.payload.get("index_generation_id")),
            str,
        )
    )
    index_ids.difference_update(candidates.indexes)
    ordered_index_ids = tuple(sorted(index_ids))
    indexes = cast(
        tuple[async_rows.AtlasIndexGenerationRow, ...],
        read(
            cast(tuple[object, ...], ordered_index_ids),
            select(async_rows.AtlasIndexGenerationRow)
            .where(
                async_rows.AtlasIndexGenerationRow.index_generation_id.in_(
                    ordered_index_ids
                )
            )
            .order_by(async_rows.AtlasIndexGenerationRow.index_generation_id),
            key=lambda row: row.index_generation_id,
            family="index generation",
        ),
    )

    evidence_ids = {
        transition.record.evidence_id
        for transition in candidates.chunks.values()
    }
    evidence_ids.update(row.evidence_id for row in chunks)
    evidence_ids.update(
        record.supersedes_evidence_id
        for record in candidates.evidence.values()
        if record.supersedes_evidence_id is not None
    )
    evidence_ids.difference_update(candidates.evidence)
    ordered_evidence_ids = tuple(sorted(evidence_ids))
    evidence = cast(
        tuple[processing_rows.AtlasEvidenceRow, ...],
        read(
            cast(tuple[object, ...], ordered_evidence_ids),
            select(processing_rows.AtlasEvidenceRow)
            .where(
                processing_rows.AtlasEvidenceRow.evidence_id.in_(
                    ordered_evidence_ids
                )
            )
            .order_by(processing_rows.AtlasEvidenceRow.evidence_id),
            key=lambda row: row.evidence_id,
            family="evidence",
        ),
    )
    page_ids = {
        artifact_id
        for record in candidates.evidence.values()
        if (artifact_id := record.evidence_artifact_id) is not None
        and not artifact_id.startswith("evidence-projection:")
    }
    page_ids.update(
        artifact_id
        for row in evidence
        if (artifact_id := row.evidence_artifact_id) is not None
        and not artifact_id.startswith("evidence-projection:")
    )
    page_ids.difference_update(candidates.pages)
    ordered_page_ids = tuple(sorted(page_ids))
    pages = cast(
        tuple[processing_rows.AtlasEvidencePageArtifactRow, ...],
        read(
            cast(tuple[object, ...], ordered_page_ids),
            select(processing_rows.AtlasEvidencePageArtifactRow)
            .where(
                processing_rows.AtlasEvidencePageArtifactRow.id.in_(
                    ordered_page_ids
                )
            )
            .order_by(processing_rows.AtlasEvidencePageArtifactRow.id),
            key=lambda row: row.id,
            family="page artifact",
        ),
    )
    storage_artifact_ids = {
        record.storage_artifact_id for record in candidates.pages.values()
    }
    storage_artifact_ids.update(
        _page_artifact_record(row).storage_artifact_id for row in pages
    )
    if storage_artifact_ids:
        storage_bindings = tuple(
            session.scalars(
                select(artifact_rows.AtlasArtifactScopeBindingRow)
                .where(
                    artifact_rows.AtlasArtifactScopeBindingRow.artifact_id.in_(
                        tuple(sorted(storage_artifact_ids))
                    ),
                    artifact_rows.AtlasArtifactScopeBindingRow.binding_kind
                    == "owner",
                )
                .order_by(
                    artifact_rows.AtlasArtifactScopeBindingRow.binding_id
                )
            ).all()
        )
        observed_binding_ids: set[str] = set()
        for row in storage_bindings:
            if (
                row.artifact_id not in storage_artifact_ids
                or row.binding_id in observed_binding_ids
            ):
                raise DocumentProcessingCurrentnessConflict(
                    "page storage binding discovery set is inconsistent"
                )
            observed_binding_ids.add(row.binding_id)
    else:
        storage_bindings = ()
    return _DocumentProcessingDiscoveredGraph(
        chunks=chunks,
        batches=batches,
        checkpoints=checkpoints,
        jobs=jobs,
        indexes=indexes,
        evidence=evidence,
        pages=pages,
        storage_bindings=storage_bindings,
    )


def _document_processing_graph_identities(
    change_set: _MutationDefaults,
    candidates: _DocumentProcessingCandidates,
    *,
    discovered: _DocumentProcessingDiscoveredGraph = (
        _DocumentProcessingDiscoveredGraph()
    ),
) -> _DocumentProcessingGraphIdentities:
    version_ids = {change_set.document_version_id, *candidates.versions}
    job_ids = set(candidates.jobs)
    if change_set.job_id is not None:
        job_ids.add(change_set.job_id)
    batch_ids = set(candidates.batches)
    generation_keys = set(candidates.generations)
    if change_set.processing_generation is not None:
        generation_keys.add(
            (change_set.document_id, change_set.processing_generation)
        )
    index_ids = set(candidates.indexes)
    evidence_ids = set(candidates.evidence)
    page_ids = set(candidates.pages)
    storage_artifact_ids = {
        record.storage_artifact_id for record in candidates.pages.values()
    }
    chunk_ids = set(candidates.chunks)

    for version in candidates.versions.values():
        if version.supersedes_version_id is not None:
            version_ids.add(version.supersedes_version_id)
    for transition in candidates.jobs.values():
        record = transition.record
        version_ids.add(record.document_version_id)
        job_ids.add(record.job_id)
        index_ids.add(record.index_generation_id)
        if record.processing_generation is not None:
            generation_keys.add((record.document_id, record.processing_generation))
    for transition in candidates.outbox.values():
        payload = transition.record.payload
        payload_job_id = payload.get("job_id")
        if isinstance(payload_job_id, str):
            job_ids.add(payload_job_id)
        payload_batch_id = payload.get("batch_id")
        if isinstance(payload_batch_id, str):
            batch_ids.add(payload_batch_id)
        payload_index_id = payload.get("index_generation_id")
        if isinstance(payload_index_id, str):
            index_ids.add(payload_index_id)
        payload_generation = payload.get("processing_generation")
        if isinstance(payload_generation, int) and not isinstance(
            payload_generation,
            bool,
        ):
            generation_keys.add((change_set.document_id, payload_generation))
    for transition in candidates.batches.values():
        job_ids.add(transition.record.job_id)
    for transition in candidates.checkpoints.values():
        record = transition.record
        job_ids.add(record.job_id)
        batch_ids.add(record.batch_id)
    for transition in candidates.generations.values():
        record = transition.record
        version_ids.add(record.document_version_id)
        generation_keys.add((record.document_id, record.processing_generation))
    for transition in candidates.indexes.values():
        record = transition.record
        version_ids.add(record.document_version_id)
        index_ids.add(record.index_generation_id)
        generation_keys.add((record.document_id, record.source_processing_generation))
        if record.supersedes_index_generation_id is not None:
            index_ids.add(record.supersedes_index_generation_id)
    for evidence in candidates.evidence.values():
        version_ids.add(evidence.document_version_id)
        if (
            evidence.evidence_artifact_id is not None
            and not evidence.evidence_artifact_id.startswith(
                "evidence-projection:"
            )
        ):
            page_ids.add(evidence.evidence_artifact_id)
        if evidence.processing_generation is not None:
            generation_keys.add((evidence.document_id, evidence.processing_generation))
        if evidence.supersedes_evidence_id is not None:
            evidence_ids.add(evidence.supersedes_evidence_id)
    for page in candidates.pages.values():
        version_ids.add(page.document_version_id)
        generation_keys.add((change_set.document_id, page.processing_generation))
    for transition in candidates.chunks.values():
        record = transition.record
        version_ids.add(record.document_version_id)
        generation_keys.add((record.document_id, record.processing_generation))
        index_ids.add(record.index_generation_id)
        evidence_ids.add(record.evidence_id)
        batch_ids.add(record.batch_id)
        chunk_ids.add(record.chunk_id)
    for transition in candidates.vectors.values():
        index_ids.add(transition.record.index_generation_id)
        chunk_ids.add(transition.record.chunk_id)
    for row in discovered.chunks:
        version_ids.add(row.document_version_id)
        generation_keys.add((row.document_id, row.processing_generation))
        index_ids.add(row.index_generation_id)
        evidence_ids.add(row.evidence_id)
        batch_ids.add(row.batch_id)
        chunk_ids.add(row.chunk_id)
    for row in discovered.batches:
        batch_ids.add(row.batch_id)
        job_ids.add(row.job_id)
    checkpoint_keys = set(candidates.checkpoints)
    for row in discovered.checkpoints:
        batch_ids.add(row.batch_id)
        job_ids.add(row.job_id)
        checkpoint_keys.add(
            (row.job_id, row.unit_kind, row.unit_start, row.unit_end)
        )
    for row in discovered.jobs:
        version_ids.add(row.document_version_id)
        job_ids.add(row.job_id)
        index_ids.add(row.index_generation_id)
        if row.processing_generation is not None:
            generation_keys.add((row.document_id, row.processing_generation))
    for row in discovered.indexes:
        version_ids.add(row.document_version_id)
        index_ids.add(row.index_generation_id)
        generation_keys.add((row.document_id, row.source_processing_generation))
        if row.supersedes_index_generation_id is not None:
            index_ids.add(row.supersedes_index_generation_id)
    for row in discovered.evidence:
        version_ids.add(row.document_version_id)
        evidence_ids.add(row.evidence_id)
        if (
            row.evidence_artifact_id is not None
            and not row.evidence_artifact_id.startswith("evidence-projection:")
        ):
            page_ids.add(row.evidence_artifact_id)
        if row.processing_generation is not None:
            generation_keys.add((row.document_id, row.processing_generation))
        if row.supersedes_evidence_id is not None:
            evidence_ids.add(row.supersedes_evidence_id)
    for row in discovered.pages:
        page_ids.add(row.id)
        storage_artifact_ids.add(
            _page_artifact_record(row).storage_artifact_id
        )
        version_ids.add(row.document_version_id)
        generation_keys.add((change_set.document_id, row.processing_generation))

    storage_binding_ids = {
        record.binding_id
        for record in change_set.artifact_metadata
        if type(record) is ArtifactScopeBindingRecord
        and record.binding_kind == "owner"
        and record.artifact_id in storage_artifact_ids
    }
    storage_binding_ids.update(
        row.binding_id for row in discovered.storage_bindings
    )

    return _DocumentProcessingGraphIdentities(
        version_ids=tuple(sorted(version_ids)),
        tag_keys=tuple(sorted(candidates.tags)),
        job_ids=tuple(sorted(job_ids)),
        outbox_ids=tuple(sorted(candidates.outbox)),
        batch_ids=tuple(sorted(batch_ids)),
        checkpoint_keys=tuple(sorted(checkpoint_keys)),
        generation_keys=tuple(sorted(generation_keys)),
        index_ids=tuple(sorted(index_ids)),
        evidence_ids=tuple(sorted(evidence_ids)),
        page_ids=tuple(sorted(page_ids)),
        storage_artifact_ids=tuple(sorted(storage_artifact_ids)),
        storage_binding_ids=tuple(sorted(storage_binding_ids)),
        chunk_ids=tuple(sorted(chunk_ids)),
        vector_keys=tuple(sorted(candidates.vectors)),
    )


def _document_processing_owner_lock_keys(
    change_set: _MutationDefaults,
    identities: _DocumentProcessingGraphIdentities,
    candidates: _DocumentProcessingCandidates,
) -> tuple[str, ...]:
    return (
        *change_set.coordination_identity_keys,
        f"document:document:{change_set.document_id}",
        *(f"document:version:{value}" for value in identities.version_ids),
        *(
            f"document:tag:{document_id}:{tag_type}:{tag_id}"
            for document_id, tag_type, tag_id in identities.tag_keys
        ),
        *(f"document:job:{value}" for value in identities.job_ids),
        *(f"document:outbox:{value}" for value in identities.outbox_ids),
        *(
            _outbox_work_identity_owner_key(
                task_name=transition.record.task_name,
                queue_name=transition.record.queue_name,
                payload=transition.record.payload,
            )
            for transition in candidates.outbox.values()
            if not transition.expected.exists
        ),
        *(f"document:batch:{value}" for value in identities.batch_ids),
        *(
            f"document:checkpoint:{job_id}:{unit_kind}:{unit_start}:{unit_end}"
            for job_id, unit_kind, unit_start, unit_end in identities.checkpoint_keys
        ),
        *(
            f"document:generation:{document_id}:{generation}"
            for document_id, generation in identities.generation_keys
        ),
        *(f"document:index:{value}" for value in identities.index_ids),
        *(f"document:evidence:{value}" for value in identities.evidence_ids),
        *(f"document:page:{value}" for value in identities.page_ids),
        *(
            f"artifact:artifact:{value}"
            for value in identities.storage_artifact_ids
        ),
        *(
            f"artifact:binding:{value}"
            for value in identities.storage_binding_ids
        ),
        *(f"document:chunk:{value}" for value in identities.chunk_ids),
        *(
            f"document:vector:{index_id}:{point_id}"
            for index_id, point_id in identities.vector_keys
        ),
    )


def _locked_rows_by_key(
    session: Session,
    statement: Any,
    *,
    expected_keys: tuple[object, ...],
    key: Callable[[Any], object],
    family: str,
) -> dict[object, Any]:
    rows = session.scalars(statement.with_for_update()).all()
    expected = frozenset(expected_keys)
    indexed: dict[object, Any] = {}
    for row in rows:
        identity = key(row)
        if identity not in expected or identity in indexed:
            raise DocumentProcessingCurrentnessConflict(
                f"{family} preimage set is inconsistent"
            )
        indexed[identity] = row
    return indexed


@dataclass(frozen=True, slots=True)
class _DocumentProcessingPreimage:
    document: document_rows.AtlasDocumentRow | None
    versions: dict[str, document_rows.AtlasDocumentVersionRow]
    tags: dict[tuple[str, str, str], document_rows.AtlasDocumentTagRow]
    jobs: dict[str, async_rows.AtlasProcessingJobRow]
    outbox: dict[str, async_rows.AtlasTaskOutboxRow]
    batches: dict[str, async_rows.AtlasProcessingBatchClaimRow]
    checkpoints: dict[
        tuple[str, str, int, int],
        async_rows.AtlasProcessingCheckpointRow,
    ]
    generations: dict[tuple[str, int], async_rows.AtlasProcessingGenerationRow]
    indexes: dict[str, async_rows.AtlasIndexGenerationRow]
    evidence: dict[str, processing_rows.AtlasEvidenceRow]
    pages: dict[str, processing_rows.AtlasEvidencePageArtifactRow]
    storage_artifacts: dict[str, artifact_rows.AtlasArtifactRow]
    storage_bindings: dict[
        str, artifact_rows.AtlasArtifactScopeBindingRow
    ]
    chunks: dict[str, async_rows.AtlasSearchChunkRow]
    vectors: dict[tuple[str, str], async_rows.AtlasVectorPointMappingRow]


def _load_document_processing_preimage(
    session: Session,
    change_set: _MutationDefaults,
    identities: _DocumentProcessingGraphIdentities,
) -> _DocumentProcessingPreimage:
    document = session.scalar(
        select(document_rows.AtlasDocumentRow)
        .where(document_rows.AtlasDocumentRow.document_id == change_set.document_id)
        .with_for_update()
    )

    def load(
        keys: tuple[object, ...],
        statement: Any,
        *,
        key: Callable[[Any], object],
        family: str,
    ) -> dict[object, Any]:
        if not keys:
            return {}
        return _locked_rows_by_key(
            session,
            statement,
            expected_keys=keys,
            key=key,
            family=family,
        )

    versions = load(
        cast(tuple[object, ...], identities.version_ids),
        select(document_rows.AtlasDocumentVersionRow)
        .where(
            document_rows.AtlasDocumentVersionRow.document_version_id.in_(
                identities.version_ids
            )
        )
        .order_by(document_rows.AtlasDocumentVersionRow.document_version_id),
        key=lambda row: row.document_version_id,
        family="document version",
    )
    tags = load(
        cast(tuple[object, ...], identities.tag_keys),
        select(document_rows.AtlasDocumentTagRow)
        .where(
            tuple_(
                document_rows.AtlasDocumentTagRow.document_id,
                document_rows.AtlasDocumentTagRow.tag_type,
                document_rows.AtlasDocumentTagRow.tag_id,
            ).in_(identities.tag_keys)
        )
        .order_by(
            document_rows.AtlasDocumentTagRow.document_id,
            document_rows.AtlasDocumentTagRow.tag_type,
            document_rows.AtlasDocumentTagRow.tag_id,
        ),
        key=lambda row: (row.document_id, row.tag_type, row.tag_id),
        family="document tag",
    )
    generations = load(
        cast(tuple[object, ...], identities.generation_keys),
        select(async_rows.AtlasProcessingGenerationRow)
        .where(
            tuple_(
                async_rows.AtlasProcessingGenerationRow.document_id,
                async_rows.AtlasProcessingGenerationRow.processing_generation,
            ).in_(identities.generation_keys)
        )
        .order_by(
            async_rows.AtlasProcessingGenerationRow.document_id,
            async_rows.AtlasProcessingGenerationRow.processing_generation,
        ),
        key=lambda row: (row.document_id, row.processing_generation),
        family="processing generation",
    )
    indexes = load(
        cast(tuple[object, ...], identities.index_ids),
        select(async_rows.AtlasIndexGenerationRow)
        .where(
            async_rows.AtlasIndexGenerationRow.index_generation_id.in_(
                identities.index_ids
            )
        )
        .order_by(async_rows.AtlasIndexGenerationRow.index_generation_id),
        key=lambda row: row.index_generation_id,
        family="index generation",
    )
    jobs = load(
        cast(tuple[object, ...], identities.job_ids),
        select(async_rows.AtlasProcessingJobRow)
        .where(async_rows.AtlasProcessingJobRow.job_id.in_(identities.job_ids))
        .order_by(async_rows.AtlasProcessingJobRow.job_id),
        key=lambda row: row.job_id,
        family="processing job",
    )
    outbox = load(
        cast(tuple[object, ...], identities.outbox_ids),
        select(async_rows.AtlasTaskOutboxRow)
        .where(async_rows.AtlasTaskOutboxRow.outbox_id.in_(identities.outbox_ids))
        .order_by(async_rows.AtlasTaskOutboxRow.outbox_id),
        key=lambda row: row.outbox_id,
        family="task outbox",
    )
    batches = load(
        cast(tuple[object, ...], identities.batch_ids),
        select(async_rows.AtlasProcessingBatchClaimRow)
        .where(
            async_rows.AtlasProcessingBatchClaimRow.batch_id.in_(
                identities.batch_ids
            )
        )
        .order_by(async_rows.AtlasProcessingBatchClaimRow.batch_id),
        key=lambda row: row.batch_id,
        family="processing batch",
    )
    checkpoints = load(
        cast(tuple[object, ...], identities.checkpoint_keys),
        select(async_rows.AtlasProcessingCheckpointRow)
        .where(
            tuple_(
                async_rows.AtlasProcessingCheckpointRow.job_id,
                async_rows.AtlasProcessingCheckpointRow.unit_kind,
                async_rows.AtlasProcessingCheckpointRow.unit_start,
                async_rows.AtlasProcessingCheckpointRow.unit_end,
            ).in_(identities.checkpoint_keys)
        )
        .order_by(
            async_rows.AtlasProcessingCheckpointRow.job_id,
            async_rows.AtlasProcessingCheckpointRow.unit_kind,
            async_rows.AtlasProcessingCheckpointRow.unit_start,
            async_rows.AtlasProcessingCheckpointRow.unit_end,
        ),
        key=lambda row: (
            row.job_id,
            row.unit_kind,
            row.unit_start,
            row.unit_end,
        ),
        family="processing checkpoint",
    )
    evidence = load(
        cast(tuple[object, ...], identities.evidence_ids),
        select(processing_rows.AtlasEvidenceRow)
        .where(processing_rows.AtlasEvidenceRow.evidence_id.in_(identities.evidence_ids))
        .order_by(processing_rows.AtlasEvidenceRow.evidence_id),
        key=lambda row: row.evidence_id,
        family="evidence",
    )
    pages = load(
        cast(tuple[object, ...], identities.page_ids),
        select(processing_rows.AtlasEvidencePageArtifactRow)
        .where(processing_rows.AtlasEvidencePageArtifactRow.id.in_(identities.page_ids))
        .order_by(processing_rows.AtlasEvidencePageArtifactRow.id),
        key=lambda row: row.id,
        family="page artifact",
    )
    storage_artifacts = load(
        cast(tuple[object, ...], identities.storage_artifact_ids),
        select(artifact_rows.AtlasArtifactRow)
        .where(
            artifact_rows.AtlasArtifactRow.artifact_id.in_(
                identities.storage_artifact_ids
            )
        )
        .order_by(artifact_rows.AtlasArtifactRow.artifact_id),
        key=lambda row: row.artifact_id,
        family="page storage artifact",
    )
    storage_bindings = load(
        cast(tuple[object, ...], identities.storage_binding_ids),
        select(artifact_rows.AtlasArtifactScopeBindingRow)
        .where(
            artifact_rows.AtlasArtifactScopeBindingRow.binding_id.in_(
                identities.storage_binding_ids
            )
        )
        .order_by(artifact_rows.AtlasArtifactScopeBindingRow.binding_id),
        key=lambda row: row.binding_id,
        family="page storage owner binding",
    )
    chunks = load(
        cast(tuple[object, ...], identities.chunk_ids),
        select(async_rows.AtlasSearchChunkRow)
        .where(async_rows.AtlasSearchChunkRow.chunk_id.in_(identities.chunk_ids))
        .order_by(async_rows.AtlasSearchChunkRow.chunk_id),
        key=lambda row: row.chunk_id,
        family="search chunk",
    )
    vectors = load(
        cast(tuple[object, ...], identities.vector_keys),
        select(async_rows.AtlasVectorPointMappingRow)
        .where(
            tuple_(
                async_rows.AtlasVectorPointMappingRow.index_generation_id,
                async_rows.AtlasVectorPointMappingRow.point_id,
            ).in_(identities.vector_keys)
        )
        .order_by(
            async_rows.AtlasVectorPointMappingRow.index_generation_id,
            async_rows.AtlasVectorPointMappingRow.point_id,
        ),
        key=lambda row: (row.index_generation_id, row.point_id),
        family="vector point mapping",
    )
    return _DocumentProcessingPreimage(
        document=document,
        versions=cast(dict[str, document_rows.AtlasDocumentVersionRow], versions),
        tags=cast(
            dict[tuple[str, str, str], document_rows.AtlasDocumentTagRow],
            tags,
        ),
        jobs=cast(dict[str, async_rows.AtlasProcessingJobRow], jobs),
        outbox=cast(dict[str, async_rows.AtlasTaskOutboxRow], outbox),
        batches=cast(dict[str, async_rows.AtlasProcessingBatchClaimRow], batches),
        checkpoints=cast(
            dict[
                tuple[str, str, int, int],
                async_rows.AtlasProcessingCheckpointRow,
            ],
            checkpoints,
        ),
        generations=cast(
            dict[tuple[str, int], async_rows.AtlasProcessingGenerationRow],
            generations,
        ),
        indexes=cast(dict[str, async_rows.AtlasIndexGenerationRow], indexes),
        evidence=cast(dict[str, processing_rows.AtlasEvidenceRow], evidence),
        pages=cast(
            dict[str, processing_rows.AtlasEvidencePageArtifactRow],
            pages,
        ),
        storage_artifacts=cast(
            dict[str, artifact_rows.AtlasArtifactRow],
            storage_artifacts,
        ),
        storage_bindings=cast(
            dict[str, artifact_rows.AtlasArtifactScopeBindingRow],
            storage_bindings,
        ),
        chunks=cast(dict[str, async_rows.AtlasSearchChunkRow], chunks),
        vectors=cast(
            dict[tuple[str, str], async_rows.AtlasVectorPointMappingRow],
            vectors,
        ),
    )


def _replay_key(family: str, *identity: object) -> tuple[object, ...]:
    return (family, *identity)


def _validate_nonfinal_mutation_boundary(
    change_set: _MutationDefaults,
    candidates: _DocumentProcessingCandidates,
    preimage: _DocumentProcessingPreimage,
) -> None:
    """Keep final-generation publication behind its dedicated exact-CAS seam."""

    if any(
        transition.record.status in {"active", "retired"}
        for transition in candidates.generations.values()
    ) or any(
        transition.record.status in {"active", "retired"}
        for transition in candidates.indexes.values()
    ) or any(
        record.processing_generation is not None and record.status == "ready"
        for record in candidates.evidence.values()
    ) or any(
        transition.record.status in {"active", "retired"}
        for transition in candidates.chunks.values()
    ):
        raise ValueError("final generation publication requires publish_job")

    desired_document = change_set.document
    if desired_document is None:
        return
    current_document = preimage.document
    if current_document is None:
        unchanged = (
            desired_document.active_processing_generation == 0
            and desired_document.active_index_generation_id is None
        )
    else:
        unchanged = (
            desired_document.active_processing_generation
            == current_document.active_processing_generation
            and desired_document.active_index_generation_id
            == current_document.active_index_generation_id
        )
    if not unchanged:
        raise ValueError("final generation publication requires publish_job")


def _validate_document_processing_preimage(
    change_set: _MutationDefaults,
    candidates: _DocumentProcessingCandidates,
    preimage: _DocumentProcessingPreimage,
    *,
    allow_operator_retry: bool = False,
) -> frozenset[tuple[object, ...]]:
    exact_replays: set[tuple[object, ...]] = set()

    current_document = preimage.document
    expected_epoch = change_set.expected_document_lifecycle_epoch
    if current_document is None:
        if change_set.document is None or expected_epoch is not None:
            raise DocumentProcessingCurrentnessConflict(
                "document currentness changed"
            )
        _validate_document_transition(None, change_set.document)
    else:
        if (
            expected_epoch is None
            or current_document.resource_lifecycle_epoch != expected_epoch
        ):
            raise DocumentProcessingCurrentnessConflict(
                "document currentness changed"
            )
        if change_set.document is not None:
            _validate_document_transition(current_document, change_set.document)
            if _document_record(current_document) == change_set.document:
                exact_replays.add(
                    _replay_key("document", change_set.document_id)
                )

    for version_id, record in candidates.versions.items():
        current = preimage.versions.get(version_id)
        _validate_version_transition(current, record)
        if current is not None and _document_version_record(current) == record:
            exact_replays.add(_replay_key("version", version_id))
    for tag_key, record in candidates.tags.items():
        current = preimage.tags.get(tag_key)
        _validate_tag_preimage(current, record)
        if current is not None:
            exact_replays.add(_replay_key("tag", *tag_key))
    for job_id, transition in candidates.jobs.items():
        current = preimage.jobs.get(job_id)
        current_record = _job_record(current) if current is not None else None
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="processing job",
            status_attr="status",
            attempt_attr="attempt",
            fence_attr="fence",
            claim_attr="lease_owner",
        )
        _validate_job_transition(
            current,
            transition.record,
            allow_operator_retry=allow_operator_retry,
        )
        if replay:
            exact_replays.add(_replay_key("job", job_id))
    for outbox_id, transition in candidates.outbox.items():
        current = preimage.outbox.get(outbox_id)
        replay = _validate_outbox_cas(current, transition)
        if replay:
            exact_replays.add(_replay_key("outbox", outbox_id))
    for batch_id, transition in candidates.batches.items():
        current = preimage.batches.get(batch_id)
        current_record = (
            _batch_claim_record(current) if current is not None else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="processing batch claim",
            attempt_attr="attempt",
            claim_attr="claim_token",
        )
        _validate_batch_transition(current, transition.record)
        if replay:
            exact_replays.add(_replay_key("batch", batch_id))
    for checkpoint_key, transition in candidates.checkpoints.items():
        current = preimage.checkpoints.get(checkpoint_key)
        current_record = (
            _checkpoint_record(current) if current is not None else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="processing checkpoint",
            fence_attr="fence",
            claim_attr="claim_token",
        )
        _validate_checkpoint_preimage(current, transition.record)
        if replay:
            exact_replays.add(_replay_key("checkpoint", *checkpoint_key))
    for generation_key, transition in candidates.generations.items():
        current = preimage.generations.get(generation_key)
        current_record = (
            _processing_generation_record(current)
            if current is not None
            else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="processing generation",
            status_attr="status",
        )
        _validate_generation_transition(
            current,
            transition.record,
            allow_operator_retry=allow_operator_retry,
        )
        if replay:
            exact_replays.add(_replay_key("generation", *generation_key))
    for index_id, transition in candidates.indexes.items():
        current = preimage.indexes.get(index_id)
        current_record = (
            _index_generation_record(current) if current is not None else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="index generation",
            status_attr="status",
        )
        _validate_index_transition(current, transition.record)
        if replay:
            exact_replays.add(_replay_key("index", index_id))
    for evidence_id, record in candidates.evidence.items():
        current = preimage.evidence.get(evidence_id)
        _validate_evidence_transition(current, record)
        if current is not None and _evidence_record(current) == record:
            exact_replays.add(_replay_key("evidence", evidence_id))
    for page_id, record in candidates.pages.items():
        current = preimage.pages.get(page_id)
        _validate_page_artifact_preimage(current, record)
        if current is not None:
            exact_replays.add(_replay_key("page", page_id))
    for chunk_id, transition in candidates.chunks.items():
        current = preimage.chunks.get(chunk_id)
        current_record = (
            replace(_search_chunk_record(current), search_vector=None)
            if current is not None
            else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            replace(transition.record, search_vector=None),
            transition.expected,
            family="search chunk",
            status_attr="status",
        )
        _validate_search_chunk_transition(current, transition.record)
        if replay:
            exact_replays.add(_replay_key("chunk", chunk_id))
    for vector_key, transition in candidates.vectors.items():
        current = preimage.vectors.get(vector_key)
        current_record = (
            _vector_mapping_record(current) if current is not None else None
        )
        replay = _expect_current_or_exact_replay(
            current,
            current_record,
            transition.record,
            transition.expected,
            family="vector point mapping",
        )
        _validate_vector_preimage(current, transition.record)
        if replay:
            exact_replays.add(_replay_key("vector", *vector_key))

    document_record = change_set.document
    if document_record is None and current_document is not None:
        document_record = _document_record(current_document)
    if document_record is None or document_record.document_id != change_set.document_id:
        raise DocumentProcessingCurrentnessConflict(
            "named document parent is missing"
        )

    def require_version(version_id: str) -> DocumentVersionRecord:
        record = candidates.versions.get(version_id)
        if record is None:
            row = preimage.versions.get(version_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "document version parent is missing"
                )
            record = _document_version_record(row)
        if record.document_id != change_set.document_id:
            raise ValueError("document version has a foreign owner graph")
        return record

    def require_generation(
        generation_key: tuple[str, int],
    ) -> ProcessingGenerationProjection:
        transition = candidates.generations.get(generation_key)
        record = transition.record if transition is not None else None
        if record is None:
            row = preimage.generations.get(generation_key)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "processing generation parent is missing"
                )
            record = _processing_generation_record(row)
        if (
            record.document_id != change_set.document_id
            or (record.document_id, record.processing_generation) != generation_key
            or (
                record.status != "retired"
                and record.document_version_id
                != change_set.document_version_id
            )
        ):
            raise ValueError("processing generation has a foreign owner graph")
        require_version(record.document_version_id)
        return record

    def require_index(index_id: str) -> IndexGenerationProjection:
        transition = candidates.indexes.get(index_id)
        record = transition.record if transition is not None else None
        if record is None:
            row = preimage.indexes.get(index_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "index generation parent is missing"
                )
            record = _index_generation_record(row)
        if (
            record.index_generation_id != index_id
            or record.document_id != change_set.document_id
            or (
                record.status != "retired"
                and record.document_version_id
                != change_set.document_version_id
            )
        ):
            raise ValueError("index generation has a foreign owner graph")
        require_version(record.document_version_id)
        require_generation(
            (record.document_id, record.source_processing_generation)
        )
        return record

    def require_job(job_id: str) -> ProcessingJobRecord:
        transition = candidates.jobs.get(job_id)
        record = transition.record if transition is not None else None
        if record is None:
            row = preimage.jobs.get(job_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "processing job parent is missing"
                )
            record = _job_record(row)
        if (
            record.job_id != job_id
            or record.document_id != change_set.document_id
            or record.document_version_id != change_set.document_version_id
        ):
            raise ValueError("processing job has a foreign owner graph")
        require_version(record.document_version_id)
        index_record = require_index(record.index_generation_id)
        if record.processing_generation is None:
            if record.job_kind != "reindex":
                raise ValueError("processing job has a foreign owner graph")
        else:
            require_generation((record.document_id, record.processing_generation))
            if index_record.source_processing_generation != record.processing_generation:
                raise ValueError("processing job has a foreign owner graph")
        return record

    def require_batch(
        batch_id: str,
    ) -> ProcessingBatchClaimRecord | ProcessingCheckpointRecord:
        transition = candidates.batches.get(batch_id)
        record = transition.record if transition is not None else None
        if record is None:
            row = preimage.batches.get(batch_id)
            if row is not None:
                record = _batch_claim_record(row)
        if record is None:
            record = next(
                (
                    _checkpoint_record(checkpoint)
                    for checkpoint in preimage.checkpoints.values()
                    if checkpoint.batch_id == batch_id
                ),
                None,
            )
        if record is None:
            record = next(
                (
                    checkpoint.record
                    for checkpoint_key, checkpoint in candidates.checkpoints.items()
                    if checkpoint.record.batch_id == batch_id
                    and _replay_key("checkpoint", *checkpoint_key)
                    in exact_replays
                ),
                None,
            )
        if record is None:
            raise DocumentProcessingCurrentnessConflict(
                "processing batch parent is missing"
            )
        if record.batch_id != batch_id:
            raise ValueError("processing batch has a foreign owner graph")
        require_job(record.job_id)
        return record

    artifact_candidates = {
        record.artifact_id: record
        for record in change_set.artifact_metadata
        if type(record) is ArtifactRecord
    }
    binding_candidates = {
        record.binding_id: record
        for record in change_set.artifact_metadata
        if type(record) is ArtifactScopeBindingRecord
    }

    def require_page(
        page_id: str,
        *,
        expected_document_version_id: str | None = None,
        expected_processing_generation: int | None = None,
    ) -> EvidencePageArtifact:
        if expected_document_version_id is None:
            expected_document_version_id = change_set.document_version_id
        if expected_processing_generation is None:
            expected_processing_generation = change_set.processing_generation
        record = candidates.pages.get(page_id)
        if record is None:
            row = preimage.pages.get(page_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "page artifact parent is missing"
                )
            record = _page_artifact_record(row)
        if (
            record.artifact_id != page_id
            or record.document_version_id != expected_document_version_id
            or record.processing_generation != expected_processing_generation
        ):
            raise ValueError("page artifact has a foreign owner graph")
        require_version(record.document_version_id)
        require_generation((change_set.document_id, record.processing_generation))

        storage = artifact_candidates.get(record.storage_artifact_id)
        if storage is None:
            storage = preimage.storage_artifacts.get(record.storage_artifact_id)
        if storage is None:
            raise DocumentProcessingCurrentnessConflict(
                "page storage artifact is missing"
            )
        expected_class, expected_content_type = {
            "pdf_single_page": ("document_page_pdf", "application/pdf"),
            "page_image": ("page_image", "image/png"),
        }[record.artifact_kind]
        publication_parent_epoch = (
            change_set.expected_document_lifecycle_epoch
            if change_set.expected_document_lifecycle_epoch is not None
            else document_record.resource_lifecycle_epoch
        )
        document_owner_scope_type = document_record.scope_type or "system"
        if (
            storage.artifact_id != record.storage_artifact_id
            or storage.artifact_class != expected_class
            or storage.content_type != expected_content_type
            or storage.lifecycle_status != "active"
            or storage.checksum_algorithm != "sha256"
            or storage.checksum_value != record.artifact_digest
            or storage.byte_size != record.content_length
            or storage.document_version_id != record.document_version_id
            or storage.processing_generation != record.processing_generation
            or storage.generation != record.processing_generation
            or storage.page_number != record.source_page_index + 1
            or storage.parent_resource_id != change_set.document_id
            or (
                change_set.require_current_page_artifact_epoch
                and storage.parent_lifecycle_epoch != publication_parent_epoch
            )
            or (
                not change_set.require_current_page_artifact_epoch
                and (
                    storage.parent_lifecycle_epoch is None
                    or storage.parent_lifecycle_epoch > publication_parent_epoch
                )
            )
            or storage.source_artifact_id != document_record.original_artifact_id
            or storage.owner_scope_type != document_owner_scope_type
            or storage.owner_scope_id != document_record.scope_id
        ):
            raise ValueError("page storage artifact has a foreign owner graph")
        effective_owner_bindings = {
            binding_id: _artifact_binding_record(binding)
            for binding_id, binding in preimage.storage_bindings.items()
            if binding.artifact_id == record.storage_artifact_id
            and binding.binding_kind == "owner"
        }
        effective_owner_bindings.update(
            {
                binding_id: binding
                for binding_id, binding in binding_candidates.items()
                if binding.artifact_id == record.storage_artifact_id
                and binding.binding_kind == "owner"
            }
        )
        matching_owner_bindings = tuple(effective_owner_bindings.values())
        if (
            len(matching_owner_bindings) != 1
            or matching_owner_bindings[0].scope_type
            != document_owner_scope_type
            or matching_owner_bindings[0].scope_id != document_record.scope_id
            or matching_owner_bindings[0].scope_type
            != storage.owner_scope_type
            or matching_owner_bindings[0].scope_id != storage.owner_scope_id
        ):
            raise ValueError(
                "page storage artifact requires exactly one matching owner binding"
            )
        return record

    def require_evidence(
        evidence_id: str,
        *,
        named_generation: bool = True,
    ) -> EvidenceRecord:
        record = candidates.evidence.get(evidence_id)
        if record is None:
            row = preimage.evidence.get(evidence_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "evidence parent is missing"
                )
            record = _evidence_record(row)
        if record.evidence_id != evidence_id or record.document_id != change_set.document_id:
            raise ValueError("evidence has a foreign owner graph")
        require_version(record.document_version_id)
        if record.processing_generation is not None:
            if named_generation and (
                record.document_version_id != change_set.document_version_id
                or record.processing_generation != change_set.processing_generation
            ):
                raise ValueError("evidence has a foreign owner graph")
            require_generation((record.document_id, record.processing_generation))
        elif named_generation and change_set.processing_generation is not None:
            raise ValueError("evidence has a foreign owner graph")
        if record.evidence_artifact_id is not None:
            if record.evidence_artifact_id.startswith("evidence-projection:"):
                if record.evidence_artifact_id != f"evidence-projection:{evidence_id}":
                    raise ValueError("evidence artifact has a foreign owner graph")
            else:
                if record.processing_generation is None:
                    raise ValueError("evidence artifact has a foreign owner graph")
                page = require_page(
                    record.evidence_artifact_id,
                    expected_document_version_id=record.document_version_id,
                    expected_processing_generation=record.processing_generation,
                )
                if (
                    page.document_version_id != record.document_version_id
                    or page.processing_generation != record.processing_generation
                ):
                    raise ValueError("evidence artifact has a foreign owner graph")
                locator_page = record.locator_payload.get("page_number")
                if locator_page is not None and (
                    not isinstance(locator_page, int)
                    or isinstance(locator_page, bool)
                    or locator_page != page.source_page_index + 1
                ):
                    raise ValueError("evidence artifact has a foreign owner graph")
        return record

    def require_chunk(chunk_id: str) -> SearchChunkProjection:
        transition = candidates.chunks.get(chunk_id)
        record = transition.record if transition is not None else None
        if record is None:
            row = preimage.chunks.get(chunk_id)
            if row is None:
                raise DocumentProcessingCurrentnessConflict(
                    "search chunk parent is missing"
                )
            record = _search_chunk_record(row)
        if (
            record.chunk_id != chunk_id
            or record.document_id != change_set.document_id
            or record.document_version_id != change_set.document_version_id
            or record.processing_generation != change_set.processing_generation
        ):
            raise ValueError("search chunk has a foreign owner graph")
        index_record = require_index(record.index_generation_id)
        if index_record.source_processing_generation != record.processing_generation:
            raise ValueError("search chunk has a foreign owner graph")
        require_evidence(record.evidence_id)
        if change_set.job_id is None:
            raise ValueError("search chunk has a foreign owner graph")
        job_record = require_job(change_set.job_id)
        if job_record.job_kind == "reindex":
            if not record.batch_id.startswith(f"{change_set.job_id}:reindex:"):
                raise ValueError("search chunk has a foreign owner graph")
        else:
            batch_record = require_batch(record.batch_id)
            if batch_record.job_id != change_set.job_id:
                raise ValueError("search chunk has a foreign owner graph")
        return record

    require_version(change_set.document_version_id)
    if change_set.processing_generation is not None:
        require_generation(
            (change_set.document_id, change_set.processing_generation)
        )
    for record in candidates.versions.values():
        if record.supersedes_version_id is not None:
            previous = require_version(record.supersedes_version_id)
            if previous.document_version_id == record.document_version_id:
                raise ValueError("document version has a foreign owner graph")
    for record in candidates.tags.values():
        if record.document_id != document_record.document_id:
            raise ValueError("document tag has a foreign owner graph")
    for transition in candidates.generations.values():
        require_generation(
            (
                transition.record.document_id,
                transition.record.processing_generation,
            )
        )
    for transition in candidates.indexes.values():
        record = transition.record
        require_index(record.index_generation_id)
        if (
            record.supersedes_index_generation_id is not None
            and _requires_index_supersedes_parent(transition)
        ):
            previous = require_index(record.supersedes_index_generation_id)
            if previous.index_generation_id == record.index_generation_id:
                raise ValueError("index generation has a foreign owner graph")
    for transition in candidates.jobs.values():
        require_job(transition.record.job_id)
    for transition in candidates.outbox.values():
        if transition.record.task_name in _NON_JOB_BOUND_OUTBOX_TASKS:
            continue
        payload_job_id = transition.record.payload.get("job_id")
        if not isinstance(payload_job_id, str):
            raise ValueError("outbox task has a foreign owner graph")
        job_record = require_job(payload_job_id)
        payload_batch_id = transition.record.payload.get("batch_id")
        if isinstance(payload_batch_id, str):
            try:
                batch_record = require_batch(payload_batch_id)
            except DocumentProcessingCurrentnessConflict:
                expected_prefix = f"{payload_job_id}:page:"
                raw_page = payload_batch_id.removeprefix(expected_prefix)
                if (
                    transition.record.task_name
                    != "atlas.processing.process_batch"
                    or not payload_batch_id.startswith(expected_prefix)
                    or not raw_page.isdigit()
                    or int(raw_page) <= 0
                    or job_record.progress_total is None
                    or int(raw_page) > job_record.progress_total
                    or job_record.job_kind == "reindex"
                ):
                    raise
            else:
                if batch_record.job_id != payload_job_id:
                    raise ValueError("outbox task has a foreign owner graph")
        payload_index_id = transition.record.payload.get("index_generation_id")
        if isinstance(payload_index_id, str):
            require_index(payload_index_id)
            if payload_index_id != job_record.index_generation_id:
                raise ValueError("outbox task has a foreign owner graph")
        payload_generation = transition.record.payload.get("processing_generation")
        if isinstance(payload_generation, int) and not isinstance(
            payload_generation,
            bool,
        ):
            require_generation((change_set.document_id, payload_generation))
            if payload_generation != job_record.processing_generation:
                raise ValueError("outbox task has a foreign owner graph")
    for transition in candidates.batches.values():
        batch_record = require_batch(transition.record.batch_id)
        if change_set.job_id is None or batch_record.job_id != change_set.job_id:
            raise ValueError("processing batch has a foreign owner graph")
    for transition in candidates.checkpoints.values():
        record = transition.record
        require_job(record.job_id)
        batch_record = require_batch(record.batch_id)
        if not _checkpoint_has_matching_batch_owner(transition, batch_record):
            raise ValueError("processing checkpoint has a foreign owner graph")
    for record in candidates.evidence.values():
        require_evidence(record.evidence_id)
        if record.supersedes_evidence_id is not None:
            require_evidence(
                record.supersedes_evidence_id,
                named_generation=False,
            )
    for record in candidates.pages.values():
        require_page(record.artifact_id)
    for transition in candidates.chunks.values():
        require_chunk(transition.record.chunk_id)
    for transition in candidates.vectors.values():
        record = transition.record
        index_record = require_index(record.index_generation_id)
        chunk_record = require_chunk(record.chunk_id)
        if chunk_record.index_generation_id != index_record.index_generation_id:
            raise ValueError("vector point mapping has a foreign owner graph")

    return frozenset(exact_replays)


def _checkpoint_has_matching_batch_owner(
    transition: ProcessingCheckpointTransition,
    batch_record: ProcessingBatchClaimRecord,
) -> bool:
    record = transition.record
    return (
        batch_record.job_id == record.job_id
        and batch_record.unit_kind == record.unit_kind
        and batch_record.unit_start == record.unit_start
        and batch_record.unit_end == record.unit_end
        and (
            transition.expected.exists
            or batch_record.claim_token == record.claim_token
        )
    )


def _acquire_document_processing_mutation(
    session: Session,
    change_set: _MutationDefaults,
    candidates: _DocumentProcessingCandidates,
) -> _LockedDocumentProcessingMutation:
    """Discover once, coordinate the complete set, then refresh the inventory."""

    discovered = _discover_document_processing_graph(
        session,
        change_set,
        candidates,
    )
    identities = _document_processing_graph_identities(
        change_set,
        candidates,
        discovered=discovered,
    )
    artifact_lock_keys = artifact_metadata_lock_keys(change_set.artifact_metadata)
    owner_lock_keys = tuple(
        sorted(
            {
                *_document_processing_owner_lock_keys(
                    change_set,
                    identities,
                    candidates,
                ),
                *artifact_lock_keys,
            }
        )
    )
    if (
        change_set.artifact_metadata
        or change_set.requires_artifact_control_lock
    ):
        acquire_mixed_owner_locks(
            session,
            shared_domain_keys=("artifact:control",),
            exclusive_identity_keys=owner_lock_keys,
        )
    else:
        acquire_owner_locks(session, identity_keys=owner_lock_keys)
    # The mixed helper above acquires the shared artifact domain before every
    # exclusive document identity.  The non-artifact branch uses the same
    # sorted exclusive identity plan without a domain lock.
    expire_all = getattr(session, "expire_all", None)
    if not callable(expire_all):
        raise TypeError("document processing mutation requires Session.expire_all")
    expire_all()
    refreshed_discovered = _discover_document_processing_graph(
        session,
        change_set,
        candidates,
    )
    refreshed_identities = _document_processing_graph_identities(
        change_set,
        candidates,
        discovered=refreshed_discovered,
    )
    if refreshed_identities != identities:
        raise DocumentProcessingCurrentnessConflict(
            "document processing identity inventory changed before locked reread"
        )
    return _LockedDocumentProcessingMutation(
        _session=session,
        identities=identities,
        owner_lock_keys=owner_lock_keys,
        artifact_lock_keys=artifact_lock_keys,
    )


def _apply_sealed_family_mutation(
    session: Session,
    change_set: _MutationDefaults,
    *,
    require_exact_replay: bool = False,
    allow_operator_retry: bool = False,
    lock_token: _LockedDocumentProcessingMutation | None = None,
    final_publication_authority: _LockedFinalGenerationPublication | None = None,
) -> frozenset[tuple[object, ...]]:
    """Validate a sealed family input in the caller's transaction."""

    if type(change_set) not in {
        _DocumentLifecycleMutation,
        _JobMutation,
        _BatchMutation,
        _FinalPublicationMutation,
    }:
        raise TypeError("mutation input must be issued by one command family")

    if (
        not change_set.require_current_page_artifact_epoch
        and final_publication_authority is None
    ):
        raise TypeError(
            "historical page artifact epochs require locked publication authority"
        )
    candidates = _document_processing_candidates(change_set)
    expected_artifact_keys = artifact_metadata_lock_keys(
        change_set.artifact_metadata
    )
    if lock_token is None:
        lock_token = _acquire_document_processing_mutation(
            session,
            change_set,
            candidates,
        )
    expected_owner_keys = tuple(
        sorted(
            {
                *_document_processing_owner_lock_keys(
                    change_set,
                    lock_token.identities,
                    candidates,
                ),
                *expected_artifact_keys,
            }
        )
    )
    if (
        lock_token._session is not session
        or lock_token.owner_lock_keys != expected_owner_keys
        or lock_token.artifact_lock_keys != expected_artifact_keys
    ):
        raise TypeError(
            "document processing lock token does not match the exact Session/graph"
        )
    identities = lock_token.identities
    preimage = _load_document_processing_preimage(
        session,
        change_set,
        identities,
    )
    if final_publication_authority is not None and (
        type(final_publication_authority) is not _LockedFinalGenerationPublication
        or final_publication_authority._session is not session
        or final_publication_authority.mutation is not lock_token
        or final_publication_authority.change_set is not change_set
    ):
        raise TypeError(
            "final publication authority does not match the exact Session/graph"
        )
    if final_publication_authority is None:
        _validate_nonfinal_mutation_boundary(change_set, candidates, preimage)
    exact_replays = _validate_document_processing_preimage(
        change_set,
        candidates,
        preimage,
        allow_operator_retry=allow_operator_retry,
    )
    if require_exact_replay:
        required_replays = {
            *(
                (_replay_key("document", change_set.document_id),)
                if change_set.document is not None
                else ()
            ),
            *(
                _replay_key("version", record.document_version_id)
                for record in change_set.versions
            ),
            *(
                _replay_key("tag", record.document_id, record.tag_type, record.tag_id)
                for record in change_set.tags
            ),
            *(
                _replay_key("job", transition.record.job_id)
                for transition in change_set.jobs
            ),
            *(
                _replay_key("outbox", transition.record.outbox_id)
                for transition in change_set.outbox
            ),
            *(
                _replay_key("batch", transition.record.batch_id)
                for transition in change_set.batch_claims
            ),
            *(
                _replay_key(
                    "checkpoint",
                    transition.record.job_id,
                    transition.record.unit_kind,
                    transition.record.unit_start,
                    transition.record.unit_end,
                )
                for transition in change_set.checkpoints
            ),
            *(
                _replay_key("evidence", record.evidence_id)
                for record in change_set.evidence
            ),
            *(
                _replay_key("page", record.artifact_id)
                for record in change_set.page_artifacts
            ),
            *(
                _replay_key(
                    "generation",
                    transition.record.document_id,
                    transition.record.processing_generation,
                )
                for transition in change_set.generations
            ),
            *(
                _replay_key("index", transition.record.index_generation_id)
                for transition in change_set.index_generations
            ),
            *(
                _replay_key("chunk", transition.record.chunk_id)
                for transition in change_set.search_chunks
            ),
            *(
                _replay_key(
                    "vector",
                    transition.record.index_generation_id,
                    transition.record.point_id,
                )
                for transition in change_set.vector_mappings
            ),
        }
        if change_set.artifact_metadata or not required_replays.issubset(
            exact_replays
        ):
            raise DocumentProcessingCurrentnessConflict(
                "complete document graph replay is not exact"
            )
    changed = False
    processing_revision_ids = {
        transition.record.processing_revision_id
        for transition in change_set.jobs
        if transition.record.processing_revision_id is not None
    }
    if change_set.job_id is not None:
        current_job = preimage.jobs.get(change_set.job_id)
        if (
            current_job is not None
            and current_job.processing_revision_id is not None
        ):
            processing_revision_ids.add(current_job.processing_revision_id)
    if len(processing_revision_ids) > 1:
        raise DocumentProcessingCurrentnessConflict(
            "processing outputs span canonical revisions"
        )
    processing_revision_id = next(iter(processing_revision_ids), None)
    if (
        change_set.document is not None
        and _replay_key("document", change_set.document_id) not in exact_replays
    ):
        session.merge(document_rows._document_row(change_set.document))
        changed = True
    for version in change_set.versions:
        if _replay_key("version", version.document_version_id) not in exact_replays:
            session.merge(
                document_rows.AtlasDocumentVersionRow(
                    document_version_id=version.document_version_id,
                    document_id=version.document_id,
                    payload=document_rows._document_version_payload(version),
                )
            )
            changed = True
    for tag in change_set.tags:
        tag_key = (tag.document_id, tag.tag_type, tag.tag_id)
        if _replay_key("tag", *tag_key) not in exact_replays:
            session.merge(document_rows.AtlasDocumentTagRow(**asdict(tag)))
            changed = True
    generation_transitions = tuple(change_set.generations)
    if final_publication_authority is not None:
        generation_transitions = tuple(
            sorted(
                generation_transitions,
                key=lambda item: item.record.status == "active",
            )
        )
    retired_generation_changed = False
    for transition in generation_transitions:
        generation_key = (
            transition.record.document_id,
            transition.record.processing_generation,
        )
        if _replay_key("generation", *generation_key) not in exact_replays:
            if (
                final_publication_authority is not None
                and transition.record.status == "active"
                and retired_generation_changed
            ):
                # Partial unique indexes are checked per SQL statement. Flush
                # the prior active row's retirement before activating its
                # replacement in the same publication transaction.
                session.flush()
                retired_generation_changed = False
            session.merge(
                async_rows.AtlasProcessingGenerationRow(
                    **asdict(transition.record)
                )
            )
            retired_generation_changed = (
                retired_generation_changed
                or (
                    final_publication_authority is not None
                    and transition.record.status == "retired"
                    and transition.expected.status == "active"
                )
            )
            changed = True
    index_transitions = tuple(change_set.index_generations)
    if final_publication_authority is not None:
        index_transitions = tuple(
            sorted(
                index_transitions,
                key=lambda item: item.record.status == "active",
            )
        )
    retired_index_changed = False
    for transition in index_transitions:
        if (
            _replay_key("index", transition.record.index_generation_id)
            not in exact_replays
        ):
            if (
                final_publication_authority is not None
                and transition.record.status == "active"
                and retired_index_changed
            ):
                session.flush()
                retired_index_changed = False
            session.merge(
                async_rows.AtlasIndexGenerationRow(**asdict(transition.record))
            )
            retired_index_changed = (
                retired_index_changed
                or (
                    final_publication_authority is not None
                    and transition.record.status == "retired"
                    and transition.expected.status == "active"
                )
            )
            changed = True
    for transition in change_set.jobs:
        if _replay_key("job", transition.record.job_id) not in exact_replays:
            session.merge(_job_row(transition.record))
            changed = True
    for transition in change_set.outbox:
        changed = _publish_outbox_cas(
            session,
            transition,
            current=preimage.outbox.get(transition.record.outbox_id),
        ) or changed
    for transition in change_set.batch_claims:
        if _replay_key("batch", transition.record.batch_id) not in exact_replays:
            session.merge(
                async_rows.AtlasProcessingBatchClaimRow(
                    **asdict(transition.record)
                )
            )
            changed = True
    for transition in change_set.checkpoints:
        checkpoint_key = (
            transition.record.job_id,
            transition.record.unit_kind,
            transition.record.unit_start,
            transition.record.unit_end,
        )
        if _replay_key("checkpoint", *checkpoint_key) not in exact_replays:
            session.merge(
                async_rows.AtlasProcessingCheckpointRow(
                    **asdict(transition.record)
                )
            )
            changed = True
    for record in change_set.evidence:
        if _replay_key("evidence", record.evidence_id) not in exact_replays:
            evidence_row = _evidence_row(record)
            evidence_row.processing_revision_id = processing_revision_id
            session.merge(evidence_row)
            changed = True
    for record in change_set.page_artifacts:
        if _replay_key("page", record.artifact_id) not in exact_replays:
            page_row = _page_artifact_row(record)
            page_row.processing_revision_id = processing_revision_id
            session.merge(page_row)
            changed = True
    for transition in change_set.search_chunks:
        if _replay_key("chunk", transition.record.chunk_id) not in exact_replays:
            chunk_payload = asdict(transition.record)
            chunk_payload["processing_revision_id"] = processing_revision_id
            session.merge(
                async_rows.AtlasSearchChunkRow(**chunk_payload)
            )
            changed = True
    for transition in change_set.vector_mappings:
        vector_key = (
            transition.record.index_generation_id,
            transition.record.point_id,
        )
        if _replay_key("vector", *vector_key) not in exact_replays:
            session.merge(
                async_rows.AtlasVectorPointMappingRow(
                    **asdict(transition.record)
                )
            )
            changed = True
    requested_publication = any(
        (
            change_set.document is not None,
            change_set.versions,
            change_set.tags,
            change_set.jobs,
            change_set.outbox,
            change_set.batch_claims,
            change_set.checkpoints,
            change_set.evidence,
            change_set.page_artifacts,
            change_set.generations,
            change_set.index_generations,
            change_set.search_chunks,
            change_set.vector_mappings,
            change_set.artifact_metadata,
        )
    )
    if changed or not requested_publication:
        AuditEventWriter(session).append_many(change_set.audit_events)
    return exact_replays


@dataclass(frozen=True, slots=True)
class _GenerationPublicationSnapshot:
    document: DocumentRecord
    version: DocumentVersionRecord
    superseded_version: DocumentVersionRecord | None
    job: ProcessingJobRecord
    generation: ProcessingGenerationProjection
    index: IndexGenerationProjection
    checkpoints: tuple[ProcessingCheckpointRecord, ...]
    evidence: tuple[EvidenceRecord, ...]
    pages: tuple[EvidencePageArtifact, ...]
    chunks: tuple[SearchChunkProjection, ...]
    vectors: tuple[VectorPointMappingRecord, ...]
    prior_generations: tuple[ProcessingGenerationProjection, ...]
    prior_indexes: tuple[IndexGenerationProjection, ...]
    evidence_revision_ids: tuple[str | None, ...] = ()
    page_revision_ids: tuple[str | None, ...] = ()
    chunk_revision_ids: tuple[str | None, ...] = ()


def _bounded_publication_rows(
    session: Session,
    statement: Any,
    *,
    limit: int,
    family: str,
) -> tuple[Any, ...]:
    values = tuple(session.scalars(statement.limit(limit + 1)).all())
    if len(values) > limit:
        raise RuntimeError(f"publication {family} exceeds bounded contract")
    return values


def _load_generation_publication_snapshot(
    session: Session,
    job_id: str,
    *,
    expected_attempt: int,
) -> _GenerationPublicationSnapshot | None:
    job_row = session.scalar(
        select(async_rows.AtlasProcessingJobRow).where(
            async_rows.AtlasProcessingJobRow.job_id == job_id
        )
    )
    if job_row is None or job_row.attempt != expected_attempt:
        return None
    index_row = session.scalar(
        select(async_rows.AtlasIndexGenerationRow).where(
            async_rows.AtlasIndexGenerationRow.index_generation_id
            == job_row.index_generation_id
        )
    )
    document_row = session.scalar(
        select(document_rows.AtlasDocumentRow).where(
            document_rows.AtlasDocumentRow.document_id == job_row.document_id
        )
    )
    version_row = session.scalar(
        select(document_rows.AtlasDocumentVersionRow).where(
            document_rows.AtlasDocumentVersionRow.document_id
            == job_row.document_id,
            document_rows.AtlasDocumentVersionRow.document_version_id
            == job_row.document_version_id,
        )
    )
    if index_row is None or document_row is None or version_row is None:
        raise DocumentProcessingCurrentnessConflict(
            "publication document/version/index parent is missing"
        )
    generation_row = session.scalar(
        select(async_rows.AtlasProcessingGenerationRow).where(
            async_rows.AtlasProcessingGenerationRow.document_id
            == job_row.document_id,
            async_rows.AtlasProcessingGenerationRow.processing_generation
            == index_row.source_processing_generation,
        )
    )
    if generation_row is None:
        raise DocumentProcessingCurrentnessConflict(
            "publication processing generation parent is missing"
        )
    version = _document_version_record(version_row)
    superseded_version: DocumentVersionRecord | None = None
    if version.supersedes_version_id is not None:
        superseded_row = session.scalar(
            select(document_rows.AtlasDocumentVersionRow).where(
                document_rows.AtlasDocumentVersionRow.document_id
                == job_row.document_id,
                document_rows.AtlasDocumentVersionRow.document_version_id
                == version.supersedes_version_id,
            )
        )
        if superseded_row is None:
            raise DocumentProcessingCurrentnessConflict(
                "publication superseded version parent is missing"
            )
        superseded_version = _document_version_record(superseded_row)

    checkpoints = _bounded_publication_rows(
        session,
        select(async_rows.AtlasProcessingCheckpointRow)
        .where(async_rows.AtlasProcessingCheckpointRow.job_id == job_id)
        .order_by(
            async_rows.AtlasProcessingCheckpointRow.unit_kind,
            async_rows.AtlasProcessingCheckpointRow.unit_start,
            async_rows.AtlasProcessingCheckpointRow.unit_end,
        ),
        limit=5000,
        family="checkpoints",
    )
    evidence = _bounded_publication_rows(
        session,
        select(processing_rows.AtlasEvidenceRow)
        .where(
            processing_rows.AtlasEvidenceRow.document_id == job_row.document_id,
            processing_rows.AtlasEvidenceRow.document_version_id
            == job_row.document_version_id,
            processing_rows.AtlasEvidenceRow.processing_generation
            == index_row.source_processing_generation,
        )
        .order_by(processing_rows.AtlasEvidenceRow.evidence_id),
        limit=5000,
        family="evidence",
    )
    pages = _bounded_publication_rows(
        session,
        select(processing_rows.AtlasEvidencePageArtifactRow)
        .where(
            processing_rows.AtlasEvidencePageArtifactRow.document_version_id
            == job_row.document_version_id,
            processing_rows.AtlasEvidencePageArtifactRow.processing_generation
            == index_row.source_processing_generation,
        )
        .order_by(processing_rows.AtlasEvidencePageArtifactRow.id),
        limit=5000,
        family="page projections",
    )
    chunks = _bounded_publication_rows(
        session,
        select(async_rows.AtlasSearchChunkRow)
        .where(
            async_rows.AtlasSearchChunkRow.index_generation_id
            == job_row.index_generation_id
        )
        .order_by(async_rows.AtlasSearchChunkRow.chunk_id),
        limit=5000,
        family="search chunks",
    )
    vectors = _bounded_publication_rows(
        session,
        select(async_rows.AtlasVectorPointMappingRow)
        .where(
            async_rows.AtlasVectorPointMappingRow.index_generation_id
            == job_row.index_generation_id
        )
        .order_by(
            async_rows.AtlasVectorPointMappingRow.chunk_id,
            async_rows.AtlasVectorPointMappingRow.point_id,
        ),
        limit=5000,
        family="vector mappings",
    )
    prior_indexes = _bounded_publication_rows(
        session,
        select(async_rows.AtlasIndexGenerationRow)
        .where(
            async_rows.AtlasIndexGenerationRow.document_id == job_row.document_id,
            async_rows.AtlasIndexGenerationRow.status == "active",
            async_rows.AtlasIndexGenerationRow.index_generation_id
            != job_row.index_generation_id,
        )
        .order_by(async_rows.AtlasIndexGenerationRow.index_generation_id),
        limit=2,
        family="active index generations",
    )
    prior_generations: tuple[Any, ...] = ()
    if job_row.processing_generation is not None:
        prior_generations = _bounded_publication_rows(
            session,
            select(async_rows.AtlasProcessingGenerationRow)
            .where(
                async_rows.AtlasProcessingGenerationRow.document_id
                == job_row.document_id,
                async_rows.AtlasProcessingGenerationRow.status == "active",
                async_rows.AtlasProcessingGenerationRow.processing_generation
                != job_row.processing_generation,
            )
            .order_by(
                async_rows.AtlasProcessingGenerationRow.processing_generation
            ),
            limit=2,
            family="active processing generations",
        )
    return _GenerationPublicationSnapshot(
        document=_document_record(document_row),
        version=version,
        superseded_version=superseded_version,
        job=_job_record(job_row),
        generation=_processing_generation_record(generation_row),
        index=_index_generation_record(index_row),
        checkpoints=tuple(_checkpoint_record(row) for row in checkpoints),
        evidence=tuple(_evidence_record(row) for row in evidence),
        evidence_revision_ids=tuple(row.processing_revision_id for row in evidence),
        pages=tuple(_page_artifact_record(row) for row in pages),
        page_revision_ids=tuple(row.processing_revision_id for row in pages),
        chunks=tuple(_search_chunk_record(row) for row in chunks),
        chunk_revision_ids=tuple(row.processing_revision_id for row in chunks),
        vectors=tuple(_vector_mapping_record(row) for row in vectors),
        prior_generations=tuple(
            _processing_generation_record(row) for row in prior_generations
        ),
        prior_indexes=tuple(
            _index_generation_record(row) for row in prior_indexes
        ),
    )


def _publication_artifact_expectation(
    snapshot: _GenerationPublicationSnapshot,
    *,
    require_current_derived_parent_epoch: bool,
) -> GenerationArtifactPublicationExpectation:
    source_artifact_id = snapshot.document.original_artifact_id
    if not source_artifact_id:
        raise DocumentProcessingCurrentnessConflict(
            "publication source artifact identity is missing"
        )
    expected: list[GenerationPublicationArtifactExpectation] = []
    observed_storage_ids: set[str] = set()
    for page in sorted(snapshot.pages, key=lambda item: item.storage_artifact_id):
        if page.storage_artifact_id in observed_storage_ids:
            raise DocumentProcessingCurrentnessConflict(
                "publication page storage identity is duplicated"
            )
        observed_storage_ids.add(page.storage_artifact_id)
        artifact_class, content_type = {
            "pdf_single_page": ("document_page_pdf", "application/pdf"),
            "page_image": ("page_image", "image/png"),
        }[page.artifact_kind]
        expected.append(
            GenerationPublicationArtifactExpectation(
                artifact_id=page.storage_artifact_id,
                artifact_class=artifact_class,
                content_type=content_type,
                checksum_algorithm="sha256",
                checksum_value=page.artifact_digest,
                byte_size=page.content_length,
                page_number=page.source_page_index + 1,
            )
        )
    return GenerationArtifactPublicationExpectation(
        document_id=snapshot.document.document_id,
        document_version_id=snapshot.version.document_version_id,
        processing_generation=snapshot.index.source_processing_generation,
        expected_parent_lifecycle_epoch=(
            snapshot.document.resource_lifecycle_epoch
        ),
        source_artifact_id=source_artifact_id,
        owner_scope_type=snapshot.document.scope_type or "system",
        owner_scope_id=snapshot.document.scope_id,
        require_current_derived_parent_epoch=(
            require_current_derived_parent_epoch
        ),
        artifacts=tuple(expected),
    )


def _validate_generation_publication_snapshot(
    snapshot: _GenerationPublicationSnapshot,
    *,
    expected_attempt: int,
    require_recorded_manifest: bool,
) -> tuple[IndexPublicationPoint, ...]:
    document = snapshot.document
    version = snapshot.version
    job = snapshot.job
    generation = snapshot.generation
    index = snapshot.index
    succeeded = job.status == "succeeded"
    processing_revision_id = job.processing_revision_id
    if (
        job.attempt != expected_attempt
        or job.document_id != document.document_id
        or job.document_version_id != version.document_version_id
        or index.index_generation_id != job.index_generation_id
        or index.document_id != document.document_id
        or index.document_version_id != version.document_version_id
        or generation.document_id != document.document_id
        or generation.document_version_id != version.document_version_id
        or generation.processing_generation
        != index.source_processing_generation
        or version.document_id != document.document_id
        or version.status not in {"active", "staged"}
        or version.original_artifact_id != document.original_artifact_id
        or document.lifecycle_status not in {"active", "restoring"}
        or index.processing_revision_id != processing_revision_id
        or any(
            revision_id != processing_revision_id
            for revision_id in snapshot.evidence_revision_ids
        )
        or any(
            revision_id != processing_revision_id
            for revision_id in snapshot.page_revision_ids
        )
        or any(
            revision_id != processing_revision_id
            for revision_id in snapshot.chunk_revision_ids
        )
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication owner graph is no longer current"
        )
    if job.status in {"failed", "cancelled"}:
        raise DocumentProcessingCurrentnessConflict(
            "terminal failed job cannot publish"
        )
    if not succeeded and (
        len(job.request_fingerprint) != 64
        or any(
            character not in "0123456789abcdef"
            for character in job.request_fingerprint
        )
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication job lifecycle fence changed"
        )
    if (
        job.progress_total is None
        or job.progress_total <= 0
        or job.progress_current != job.progress_total
        or not snapshot.chunks
    ):
        raise ValueError("no_searchable_evidence")
    if job.job_kind == "reindex":
        if (
            job.processing_generation is not None
            or generation.status != "active"
            or document.active_processing_generation
            != index.source_processing_generation
            or snapshot.checkpoints
        ):
            raise ValueError("publication_source_generation_changed")
    elif (
        job.processing_generation != index.source_processing_generation
        or generation.status != ("active" if succeeded else "building")
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication processing generation changed"
        )
    if index.status != ("active" if succeeded else "building"):
        raise DocumentProcessingCurrentnessConflict(
            "publication index generation changed"
        )
    if succeeded:
        if (
            document.active_index_generation_id != index.index_generation_id
            or (
                job.processing_generation is not None
                and document.active_processing_generation
                != job.processing_generation
            )
        ):
            raise DocumentProcessingCurrentnessConflict(
                "published generation pointers changed"
            )
    else:
        if (
            document.active_index_generation_id
            != index.supersedes_index_generation_id
        ):
            raise ValueError("publication_source_generation_changed")
        if len(snapshot.prior_indexes) > 1 or (
            document.active_index_generation_id is None
            and snapshot.prior_indexes
        ) or (
            document.active_index_generation_id is not None
            and (
                len(snapshot.prior_indexes) != 1
                or snapshot.prior_indexes[0].index_generation_id
                != document.active_index_generation_id
            )
        ):
            raise DocumentProcessingCurrentnessConflict(
                "active index pointer is stale"
            )
        if job.processing_generation is not None and (
            len(snapshot.prior_generations) > 1
            or (
                document.active_processing_generation == 0
                and snapshot.prior_generations
            )
            or (
                document.active_processing_generation > 0
                and (
                    len(snapshot.prior_generations) != 1
                    or snapshot.prior_generations[0].processing_generation
                    != document.active_processing_generation
                )
            )
        ):
            raise DocumentProcessingCurrentnessConflict(
                "active processing pointer is stale"
            )

    evidence_by_id = {record.evidence_id: record for record in snapshot.evidence}
    if len(evidence_by_id) != len(snapshot.evidence) or any(
        record.document_id != document.document_id
        or record.document_version_id != version.document_version_id
        or record.processing_generation != index.source_processing_generation
        or record.status not in {"staged", "ready"}
        for record in snapshot.evidence
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication evidence inventory is inconsistent"
        )
    page_by_id = {record.artifact_id: record for record in snapshot.pages}
    if len(page_by_id) != len(snapshot.pages) or any(
        record.document_version_id != version.document_version_id
        or record.processing_generation != index.source_processing_generation
        for record in snapshot.pages
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication page inventory is inconsistent"
        )
    chunk_by_id = {record.chunk_id: record for record in snapshot.chunks}
    if len(chunk_by_id) != len(snapshot.chunks) or any(
        record.document_id != document.document_id
        or record.document_version_id != version.document_version_id
        or record.processing_generation != index.source_processing_generation
        or record.index_generation_id != index.index_generation_id
        or record.status not in {"staged", "active"}
        or record.evidence_id not in evidence_by_id
        for record in snapshot.chunks
    ) or {record.evidence_id for record in snapshot.chunks} != set(evidence_by_id):
        raise DocumentProcessingCurrentnessConflict(
            "publication search chunk inventory is inconsistent"
        )
    mapping_by_chunk: dict[str, VectorPointMappingRecord] = {}
    point_ids: set[str] = set()
    for record in snapshot.vectors:
        if (
            record.index_generation_id != index.index_generation_id
            or record.chunk_id not in chunk_by_id
            or record.chunk_id in mapping_by_chunk
            or record.point_id in point_ids
        ):
            raise DocumentProcessingCurrentnessConflict(
                "publication vector mapping inventory is inconsistent"
            )
        mapping_by_chunk[record.chunk_id] = record
        point_ids.add(record.point_id)
    if set(mapping_by_chunk) != set(chunk_by_id):
        raise DocumentProcessingCurrentnessConflict(
            "publication vector mapping coverage is incomplete"
        )

    if job.job_kind != "reindex":
        ordered_checkpoints = tuple(
            sorted(
                snapshot.checkpoints,
                key=lambda item: (item.unit_start, item.unit_end),
            )
        )
        cursor = 1
        for checkpoint in ordered_checkpoints:
            if (
                checkpoint.job_id != job.job_id
                or checkpoint.unit_kind != "page"
                or checkpoint.unit_start != cursor
                or checkpoint.unit_end < checkpoint.unit_start
            ):
                raise DocumentProcessingCurrentnessConflict(
                    "publication checkpoint coverage is inconsistent"
                )
            cursor = checkpoint.unit_end + 1
        if cursor != job.progress_total + 1:
            raise DocumentProcessingCurrentnessConflict(
                "publication checkpoint coverage is incomplete"
            )
        checkpoint_evidence_count = sum(
            item.evidence_count for item in ordered_checkpoints
        )
        checkpoint_chunk_count = sum(
            item.chunk_count for item in ordered_checkpoints
        )
        checkpoint_preview_count = sum(
            item.preview_count for item in ordered_checkpoints
        )
        if (
            checkpoint_evidence_count != len(snapshot.evidence)
            or checkpoint_chunk_count != len(snapshot.chunks)
            or checkpoint_preview_count != len(snapshot.pages)
            or generation.actual_page_count != job.progress_total
        ):
            raise DocumentProcessingCurrentnessConflict(
                "publication checkpoint counts are inconsistent"
            )
    if (
        generation.actual_evidence_count != len(snapshot.evidence)
        or generation.actual_chunk_count != len(snapshot.chunks)
        or index.actual_fts_count != len(snapshot.chunks)
        or index.actual_point_count != len(snapshot.vectors)
        or index.actual_fts_count != index.actual_point_count
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication generation counts are inconsistent"
        )
    if require_recorded_manifest and (
        generation.expected_page_count != generation.actual_page_count
        or generation.expected_evidence_count != generation.actual_evidence_count
        or generation.expected_chunk_count != generation.actual_chunk_count
        or index.expected_fts_count != index.actual_fts_count
        or index.expected_point_count != index.actual_point_count
        or not index.manifest_digest
        or (
            job.processing_generation is not None
            and generation.manifest_digest != index.manifest_digest
        )
    ):
        raise DocumentProcessingCurrentnessConflict(
            "publication manifest counts are not recorded"
        )
    return tuple(
        IndexPublicationPoint(
            point_id=mapping_by_chunk[chunk.chunk_id].point_id,
            chunk_id=chunk.chunk_id,
            payload_digest=mapping_by_chunk[chunk.chunk_id].payload_digest,
            vector_digest=mapping_by_chunk[chunk.chunk_id].vector_digest,
        )
        for chunk in snapshot.chunks
    )


def _publication_manifest_digest(
    snapshot: _GenerationPublicationSnapshot,
    artifact_graph: CurrentArtifactGraphResult,
    points: tuple[IndexPublicationPoint, ...],
) -> str:
    return _request_digest(
        {
            "job_id": snapshot.job.job_id,
            "attempt": snapshot.job.attempt,
            "document_id": snapshot.document.document_id,
            "document_version_id": snapshot.version.document_version_id,
            "processing_generation": snapshot.index.source_processing_generation,
            "index_generation_id": snapshot.index.index_generation_id,
            "source_processing_generation": (
                snapshot.index.source_processing_generation
            ),
            "supersedes_index_generation_id": (
                snapshot.index.supersedes_index_generation_id
            ),
            "processing_profile_id": snapshot.generation.profile_id,
            "processing_profile_revision": snapshot.generation.profile_revision,
            "qdrant_collection": snapshot.index.qdrant_collection,
            "embedding_profile_digest": _request_digest(
                snapshot.index.embedding_profile
            ),
            "checkpoints": [
                {
                    "job_id": item.job_id,
                    "unit_kind": item.unit_kind,
                    "unit_start": item.unit_start,
                    "unit_end": item.unit_end,
                    "batch_id": item.batch_id,
                    "claim_token": item.claim_token,
                    "fence": item.fence,
                    "input_fingerprint": item.input_fingerprint,
                    "output_digest": item.output_digest,
                    "evidence_count": item.evidence_count,
                    "chunk_count": item.chunk_count,
                    "preview_count": item.preview_count,
                }
                for item in snapshot.checkpoints
            ],
            "pages": [
                {
                    "artifact_id": item.artifact_id,
                    "storage_artifact_id": item.storage_artifact_id,
                    "source_page_index": item.source_page_index,
                    "artifact_kind": item.artifact_kind,
                    "artifact_digest": item.artifact_digest,
                    "content_length": item.content_length,
                }
                for item in snapshot.pages
            ],
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "source_region_id": item.source_region_id,
                    "channel_id": item.channel_id,
                    "locator_payload": item.locator_payload,
                    "content_fingerprint": item.content_fingerprint,
                    "processing_fingerprint": item.processing_fingerprint,
                    "evidence_artifact_id": item.evidence_artifact_id,
                }
                for item in snapshot.evidence
            ],
            "chunks": [
                {
                    "chunk_id": item.chunk_id,
                    "batch_id": item.batch_id,
                    "evidence_id": item.evidence_id,
                    "segment_id": item.segment_id,
                    "window_ordinal": item.window_ordinal,
                    "locator": item.locator,
                    "content_fingerprint": item.content_fingerprint,
                    "processing_fingerprint": item.processing_fingerprint,
                    "normalized_text_digest": hashlib.sha256(
                        item.normalized_text.encode("utf-8")
                    ).hexdigest(),
                }
                for item in snapshot.chunks
            ],
            "points": [asdict(point) for point in points],
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_class": item.artifact_class,
                    "logical_identity": item.logical_identity,
                    "blob_id": item.blob_id,
                    "write_attempt_id": item.write_attempt_id,
                    "opaque_ref": item.opaque_ref,
                    "checksum_algorithm": item.checksum_algorithm,
                    "checksum_value": item.checksum_value,
                    "byte_size": item.byte_size,
                    "content_type": item.content_type,
                    "owner_scope_type": item.owner_scope_type,
                    "owner_scope_id": item.owner_scope_id,
                    "parent_lifecycle_epoch": item.parent_lifecycle_epoch,
                    "processing_generation": item.processing_generation,
                    "source_artifact_id": item.source_artifact_id,
                    "generation": item.generation,
                    "page_number": item.page_number,
                    "fence": asdict(item.fence),
                    "bindings": item.bindings,
                }
                for item in artifact_graph.entries
            ],
        }
    )


def _publication_inventory_identities(
    snapshot: _GenerationPublicationSnapshot,
) -> tuple[tuple[str, object], ...]:
    return tuple(
        sorted(
            {
                ("version", snapshot.version.document_version_id),
                *(
                    {
                        (
                            "version",
                            snapshot.superseded_version.document_version_id,
                        )
                    }
                    if snapshot.superseded_version is not None
                    else set()
                ),
                ("job", snapshot.job.job_id),
                (
                    "generation",
                    f"{snapshot.generation.document_id}:"
                    f"{snapshot.generation.processing_generation}",
                ),
                ("index", snapshot.index.index_generation_id),
                *(
                    ("checkpoint", f"{item.unit_kind}:{item.unit_start}:{item.unit_end}")
                    for item in snapshot.checkpoints
                ),
                *(("evidence", item.evidence_id) for item in snapshot.evidence),
                *(("page", item.artifact_id) for item in snapshot.pages),
                *(("chunk", item.chunk_id) for item in snapshot.chunks),
                *(
                    ("vector", f"{item.index_generation_id}:{item.point_id}")
                    for item in snapshot.vectors
                ),
                *(
                    (
                        "prior_generation",
                        f"{item.document_id}:{item.processing_generation}",
                    )
                    for item in snapshot.prior_generations
                ),
                *(
                    ("prior_index", item.index_generation_id)
                    for item in snapshot.prior_indexes
                ),
            }
        )
    )


def _generation_publication_change_set(
    snapshot: _GenerationPublicationSnapshot,
    *,
    manifest_digest: str,
    finalize: bool,
    now: datetime,
    artifact_inventory: CurrentArtifactLockInventory,
) -> _FinalPublicationMutation:
    document = snapshot.document
    job = snapshot.job
    generation = snapshot.generation
    index = snapshot.index
    desired_document = document
    if finalize:
        desired_document = replace(
            document,
            active_processing_generation=(
                job.processing_generation
                if job.processing_generation is not None
                else document.active_processing_generation
            ),
            active_index_generation_id=index.index_generation_id,
            intake_status=(
                "ready_with_warnings" if document.warning_codes else "ready"
            ),
            current_stage="completed",
            failure_code=None,
            processing_job_id=job.job_id,
            lifecycle_status=(
                "active"
                if document.lifecycle_status == "restoring"
                else document.lifecycle_status
            ),
            resource_lifecycle_epoch=(
                document.resource_lifecycle_epoch + 1
                if document.lifecycle_status == "restoring"
                else document.resource_lifecycle_epoch
            ),
            restored_at=(
                now.isoformat()
                if document.lifecycle_status == "restoring"
                else document.restored_at
            ),
        )
    desired_job = job
    if finalize:
        desired_job = replace(
            job,
            stage="completed",
            status="succeeded",
            lease_owner=None,
            lease_expires_at=None,
            failure_code=None,
            failure_detail=None,
            updated_at=now if job.status != "succeeded" else job.updated_at,
        )
    desired_version = (
        replace(snapshot.version, status="active")
        if finalize and snapshot.version.status == "staged"
        else snapshot.version
    )
    versions = [desired_version]
    if finalize and snapshot.superseded_version is not None:
        versions.append(
            replace(snapshot.superseded_version, status="superseded")
        )

    generation_transitions: list[ProcessingGenerationTransition] = []
    if job.job_kind != "reindex" or finalize:
        desired_generation = generation
        if job.job_kind != "reindex":
            desired_generation = replace(
                generation,
                expected_page_count=generation.actual_page_count,
                expected_evidence_count=generation.actual_evidence_count,
                expected_chunk_count=generation.actual_chunk_count,
                manifest_digest=manifest_digest,
                status="active" if finalize else generation.status,
                published_at=(
                    now
                    if finalize and job.status != "succeeded"
                    else generation.published_at
                ),
            )
        generation_transitions.append(
            ProcessingGenerationTransition(
                desired_generation,
                CurrentRowExpectation(
                    exists=True,
                    status=generation.status,
                    attempt=None,
                    fence=None,
                    claim_owner=None,
                    preimage=generation,
                ),
            )
        )
    if finalize:
        generation_transitions.extend(
            ProcessingGenerationTransition(
                replace(item, status="retired"),
                CurrentRowExpectation(
                    exists=True,
                    status=item.status,
                    attempt=None,
                    fence=None,
                    claim_owner=None,
                    preimage=item,
                ),
            )
            for item in snapshot.prior_generations
        )
    desired_index = replace(
        index,
        expected_point_count=index.actual_point_count,
        expected_fts_count=index.actual_fts_count,
        manifest_digest=manifest_digest,
        status="active" if finalize else index.status,
        published_at=(
            now
            if finalize and job.status != "succeeded"
            else index.published_at
        ),
    )
    index_transitions = [
        IndexGenerationTransition(
            desired_index,
            CurrentRowExpectation(
                exists=True,
                status=index.status,
                attempt=None,
                fence=None,
                claim_owner=None,
                preimage=index,
            ),
        )
    ]
    if finalize:
        index_transitions.extend(
            IndexGenerationTransition(
                replace(item, status="retired"),
                CurrentRowExpectation(
                    exists=True,
                    status=item.status,
                    attempt=None,
                    fence=None,
                    claim_owner=None,
                    preimage=item,
                ),
            )
            for item in snapshot.prior_indexes
        )
    finalizing_first_publication = not (
        job.job_kind == "reindex" or job.status == "succeeded"
    )
    return _FinalPublicationMutation(
        document_id=document.document_id,
        document_version_id=snapshot.version.document_version_id,
        processing_generation=index.source_processing_generation,
        job_id=job.job_id,
        expected_document_lifecycle_epoch=document.resource_lifecycle_epoch,
        document=desired_document,
        versions=tuple(versions),
        jobs=(
            ProcessingJobTransition(
                desired_job,
                CurrentRowExpectation(
                    exists=True,
                    status=job.status,
                    attempt=job.attempt,
                    fence=job.fence,
                    claim_owner=job.lease_owner,
                    preimage=job,
                ),
            ),
        ),
        checkpoints=tuple(
            ProcessingCheckpointTransition(
                item,
                CurrentRowExpectation(
                    exists=True,
                    status=None,
                    attempt=None,
                    fence=item.fence,
                    claim_owner=item.claim_token,
                    preimage=item,
                ),
            )
            for item in snapshot.checkpoints
        ),
        evidence=tuple(
            replace(item, status="ready")
            if (
                finalize
                and job.job_kind != "reindex"
                and item.status == "staged"
            )
            else item
            for item in snapshot.evidence
        ),
        page_artifacts=snapshot.pages,
        generations=tuple(generation_transitions),
        index_generations=tuple(index_transitions),
        search_chunks=tuple(
            SearchChunkTransition(
                replace(item, status="active") if finalize else item,
                CurrentRowExpectation(
                    exists=True,
                    status=item.status,
                    attempt=None,
                    fence=None,
                    claim_owner=None,
                    preimage=replace(item, search_vector=None),
                ),
            )
            for item in snapshot.chunks
        ),
        vector_mappings=tuple(
            VectorPointMappingTransition(
                item,
                CurrentRowExpectation(
                    exists=True,
                    status=None,
                    attempt=None,
                    fence=None,
                    claim_owner=None,
                    preimage=item,
                ),
            )
            for item in snapshot.vectors
        ),
        requires_artifact_control_lock=True,
        require_current_page_artifact_epoch=finalizing_first_publication,
        coordination_identity_keys=tuple(
            key
            for key in artifact_inventory.identity_keys
            if key != "artifact:control"
        ),
        audit_events=(
            _internal_event(
                operation=(
                    "processing_job.published"
                    if finalize
                    else "index_generation.manifest_recorded"
                ),
                job_id=job.job_id,
                document_id=document.document_id,
                attempt=job.attempt,
                status="succeeded" if finalize else job.status,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class _DocumentMutationSql:
    session_factory: SessionFactory

    def _apply_validated_mutation(self, change_set: _DocumentLifecycleMutation) -> None:
        session = self.session_factory()
        with session:
            try:
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

@dataclass(frozen=True, slots=True)
class _JobTransitionReadSql:
    session_factory: SessionFactory

    def _bind(self) -> Any:
        session = self.session_factory()
        try:
            return session.get_bind()
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._bind().begin() as connection:
            yield connection

    def list_jobs(
        self,
        *,
        document_id: str | None = None,
        limit: int = 100,
    ) -> list[ProcessingJobRecord]:
        if limit < 1 or limit > 200:
            raise ValueError("processing job limit must be between 1 and 200")
        statement = select(async_rows.AtlasProcessingJobRow)
        if document_id is not None:
            requested_identity = (
                select(document_rows.AtlasDocumentRow.processing_identity_id)
                .where(
                    document_rows.AtlasDocumentRow.document_id == document_id
                )
                .scalar_subquery()
            )
            statement = statement.where(
                or_(
                    async_rows.AtlasProcessingJobRow.document_id == document_id,
                    and_(
                        async_rows.AtlasProcessingJobRow.processing_identity_id.is_not(
                            None
                        ),
                        async_rows.AtlasProcessingJobRow.processing_identity_id
                        == requested_identity,
                    ),
                )
            )
        statement = statement.order_by(
            async_rows.AtlasProcessingJobRow.created_at.desc(),
            async_rows.AtlasProcessingJobRow.job_id,
        ).limit(limit)
        with self.session_factory() as session:
            return [_job_record(row) for row in session.scalars(statement).all()]

    def get_job(self, job_id: str) -> ProcessingJobRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasProcessingJobRow).where(
                    async_rows.AtlasProcessingJobRow.job_id == job_id
                )
            )
            return _job_record(row) if row is not None else None

    def list_job_projection_batch(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
    ) -> ProcessingJobListBatch:
        if limit is not None and (limit < 1 or limit > 200):
            raise ValueError("processing job projection limit must be between 1 and 200")
        statement = select(async_rows.AtlasProcessingJobRow)
        if document_id is not None:
            requested_identity = (
                select(document_rows.AtlasDocumentRow.processing_identity_id)
                .where(
                    document_rows.AtlasDocumentRow.document_id == document_id
                )
                .scalar_subquery()
            )
            statement = statement.where(
                or_(
                    async_rows.AtlasProcessingJobRow.document_id == document_id,
                    and_(
                        async_rows.AtlasProcessingJobRow.processing_identity_id.is_not(
                            None
                        ),
                        async_rows.AtlasProcessingJobRow.processing_identity_id
                        == requested_identity,
                    ),
                )
            )
        if job_id is not None:
            statement = statement.where(async_rows.AtlasProcessingJobRow.job_id == job_id)
        statement = statement.order_by(
            async_rows.AtlasProcessingJobRow.created_at.desc(),
            async_rows.AtlasProcessingJobRow.job_id,
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self.session_factory() as discovery_session:
            discovered_jobs = tuple(
                _job_execution_record(row)
                for row in discovery_session.scalars(statement).all()
            )
            discovered_document_ids = {job.document_id for job in discovered_jobs}
            if document_id is not None:
                discovered_document_ids.add(document_id)
            discovered_identity_ids = {
                job.processing_identity_id
                for job in discovered_jobs
                if job.processing_identity_id is not None
            }
            if discovered_identity_ids:
                discovered_document_ids.update(
                    discovery_session.scalars(
                        select(document_rows.AtlasDocumentRow.document_id).where(
                            document_rows.AtlasDocumentRow.processing_identity_id.in_(
                                discovered_identity_ids
                            )
                        )
                    ).all()
                )
            discovered_tag_rows = discovery_session.scalars(
                select(document_rows.AtlasDocumentTagRow).where(
                    document_rows.AtlasDocumentTagRow.document_id.in_(
                        discovered_document_ids or {""}
                    )
                )
            ).all()
            discovered_scopes = tuple(
                sorted({(row.tag_type, row.tag_id) for row in discovered_tag_rows})
            )
        with self.session_factory() as session:
            acquire_mixed_owner_locks(
                session,
                exclusive_domain_keys=(
                    "team:hierarchy-control",
                    "team:membership-control",
                    *(
                        f"project:acl-control:{scope_id}"
                        for scope_type, scope_id in discovered_scopes
                        if scope_type == "project"
                    ),
                ),
                exclusive_identity_keys=(
                    f"identity:session:{presented_browser_session_token}",
                    identity_actor_owner_key(actor_id),
                    team_subject_owner_key(actor_type, actor_id),
                    *(f"document:job:{job.job_id}" for job in discovered_jobs),
                    *(
                        f"document:document:{current_id}"
                        for current_id in discovered_document_ids
                    ),
                    *(
                        f"document:tag:{row.document_id}:{row.tag_type}:{row.tag_id}"
                        for row in discovered_tag_rows
                    ),
                    *(
                        team_owner_key(scope_id)
                        if scope_type == "team"
                        else project_owner_key(scope_id)
                        for scope_type, scope_id in discovered_scopes
                    ),
                    *(
                        project_acl_subject_owner_key(actor_type, actor_id)
                        for scope_type, _scope_id in discovered_scopes
                        if scope_type == "project"
                    ),
                ),
            )
            authenticated_actor = identity_rows.read_session_actor(
                session, presented_browser_session_token
            )
            if (
                authenticated_actor is None
                or authenticated_actor.actor_type != actor_type
                or authenticated_actor.actor_id != actor_id
            ):
                raise PermissionError("processing request is unauthenticated")
            base_jobs = tuple(
                _job_execution_record(row)
                for row in session.scalars(statement).all()
            )
            if {job.job_id for job in base_jobs} != {
                job.job_id for job in discovered_jobs
            }:
                raise DocumentProcessingCurrentnessConflict(
                    "processing list changed during boundary discovery"
                )
            document_ids = {job.document_id for job in base_jobs}
            if document_id is not None:
                document_ids.add(document_id)
            if {
                job.processing_identity_id
                for job in base_jobs
                if job.processing_identity_id is not None
            }:
                document_ids.update(
                    row.document_id
                    for row in session.scalars(
                        select(document_rows.AtlasDocumentRow).where(
                            document_rows.AtlasDocumentRow.processing_identity_id.in_(
                                {
                                    job.processing_identity_id
                                    for job in base_jobs
                                    if job.processing_identity_id is not None
                                }
                                or {""}
                            )
                        )
                    ).all()
                )
            documents = {
                row.document_id: _document_record(row)
                for row in session.scalars(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id.in_(
                            document_ids or {""}
                        )
                    )
                ).all()
            }
            grouped_tags: dict[str, list[tuple[str, str]]] = {
                current_id: [] for current_id in document_ids
            }
            tag_rows = session.scalars(
                select(document_rows.AtlasDocumentTagRow)
                .where(
                    document_rows.AtlasDocumentTagRow.document_id.in_(
                        document_ids or {""}
                    )
                )
                .order_by(
                    document_rows.AtlasDocumentTagRow.document_id,
                    document_rows.AtlasDocumentTagRow.tag_type,
                    document_rows.AtlasDocumentTagRow.tag_id,
                )
            ).all()
            for row in tag_rows:
                grouped_tags[row.document_id].append((row.tag_type, row.tag_id))
            tag_refs = {
                current_id: tuple(refs)
                for current_id, refs in grouped_tags.items()
            }
            snapshot_rows = session.scalars(
                select(async_rows.AtlasProcessingRequestSnapshotRow).where(
                    async_rows.AtlasProcessingRequestSnapshotRow.job_id.in_(
                        {job.job_id for job in base_jobs} or {""}
                    )
                )
            ).all()
            pins = {
                (row.document_id, int(row.processing_generation)): ProcessingProfilePin(
                    profile_id=str(row.payload["profile_id"]),
                    profile_revision=int(row.payload["profile_revision"]),
                )
                for row in snapshot_rows
            }
            expanded: list[ProcessingJobView] = []
            for job in base_jobs:
                bound_ids = (
                    [document_id]
                    if document_id is not None
                    and document_id in documents
                    and job.processing_identity_id is not None
                    and documents[document_id].processing_identity_id
                    == job.processing_identity_id
                    else sorted(
                        current_id
                        for current_id, bound_document in documents.items()
                        if job.processing_identity_id is not None
                        and bound_document.processing_identity_id
                        == job.processing_identity_id
                    )
                )
                source_pin = (
                    pins.get((job.document_id, int(job.processing_generation)))
                    if job.processing_generation is not None
                    else None
                )
                for current_id in bound_ids or [job.document_id]:
                    expanded.append(replace(job, document_id=current_id))
                    if (
                        source_pin is not None
                        and job.processing_generation is not None
                    ):
                        pins[(current_id, int(job.processing_generation))] = (
                            source_pin
                        )
            jobs = tuple(expanded)
            scope_bindings = tuple(
                sorted({ref for refs in tag_refs.values() for ref in refs})
            )
            if not set(scope_bindings).issubset(discovered_scopes):
                raise DocumentProcessingCurrentnessConflict(
                    "processing authority scope changed during boundary discovery"
                )
            authorization_state = self._authorization_state(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                scope_bindings=scope_bindings,
            )
        return ProcessingJobListBatch(
            jobs=jobs,
            documents=documents,
            tag_refs_by_document=tag_refs,
            profile_pins=pins,
            authorization_state=authorization_state,
        )

    def list_document_job_request_projections(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[DocumentJobRequestAuthorityProjection, ...]:
        if limit is not None and (limit < 1 or limit > 200):
            raise ValueError(
                "processing job projection limit must be between 1 and 200"
            )
        batch = self.list_job_projection_batch(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            document_id=document_id,
            limit=None,
        )
        allowed_projections: list[DocumentJobRequestAuthorityProjection] = []
        seen_job_ids: set[str] = set()
        for projection in attach_document_job_request_projections(batch):
            allowed = effective_document_scope(
                projection.authorization_state,
                actor_type=actor_type,
                actor_id=actor_id,
                action="read_derived",
            )
            if not any(ref in allowed for ref in projection.tag_refs):
                continue
            if document_id is None and projection.job.job_id in seen_job_ids:
                continue
            seen_job_ids.add(projection.job.job_id)
            allowed_projections.append(projection)
            if limit is not None and len(allowed_projections) >= limit:
                break
        return tuple(allowed_projections)

    def get_document_job_request_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        job_id: str,
    ) -> DocumentJobRequestAuthorityProjection | None:
        batch = self.list_job_projection_batch(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            job_id=job_id,
            limit=1,
        )
        projections = attach_document_job_request_projections(batch)
        for projection in projections:
            allowed = effective_document_scope(
                projection.authorization_state,
                actor_type=actor_type,
                actor_id=actor_id,
                action="read_derived",
            )
            if any(ref in allowed for ref in projection.tag_refs):
                return projection
        return None

    @staticmethod
    def _authorization_state(
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        scope_bindings: tuple[tuple[str, str], ...],
    ) -> ProcessingJobAuthorizationState:
        actor_row = session.get(identity_rows.AtlasUserRow, actor_id)
        membership_rows = session.scalars(
            select(identity_rows.AtlasTeamMembershipRow).where(
                identity_rows.AtlasTeamMembershipRow.member_actor_type == actor_type,
                identity_rows.AtlasTeamMembershipRow.member_actor_id == actor_id,
            )
        ).all()
        unresolved = {row.team_id for row in membership_rows}
        unresolved.update(
            scope_id
            for scope_type, scope_id in scope_bindings
            if scope_type == "team"
        )
        team_by_id: dict[str, Any] = {}
        while unresolved:
            rows = session.scalars(
                select(identity_rows.AtlasTeamRow).where(
                    identity_rows.AtlasTeamRow.team_id.in_(unresolved)
                )
            ).all()
            for row in rows:
                team_by_id[row.team_id] = row
            unresolved = {
                row.parent_team_id
                for row in rows
                if row.parent_team_id is not None
                and row.parent_team_id not in team_by_id
            }
        scoped_project_ids = {
            scope_id
            for scope_type, scope_id in scope_bindings
            if scope_type == "project"
        }
        grant_rows = session.scalars(
            select(identity_rows.AtlasPermissionGrantRow).where(
                identity_rows.AtlasPermissionGrantRow.project_id.in_(
                    scoped_project_ids or {""}
                ),
                or_(
                    (
                        identity_rows.AtlasPermissionGrantRow.subject_type
                        == actor_type
                    )
                    & (
                        identity_rows.AtlasPermissionGrantRow.subject_id == actor_id
                    ),
                    (
                        identity_rows.AtlasPermissionGrantRow.subject_type == "team"
                    )
                    & identity_rows.AtlasPermissionGrantRow.subject_id.in_(
                        set(team_by_id) or {""}
                    ),
                ),
            )
        ).all()
        project_ids = scoped_project_ids | {row.project_id for row in grant_rows}
        project_records = session.scalars(
            select(project_rows.AtlasProjectRow).where(
                project_rows.AtlasProjectRow.project_id.in_(project_ids or {""})
            )
        ).all()
        return ProcessingJobAuthorizationState(
            users=(
                {actor_id: UserRecord(**_row_payload(actor_row))}
                if actor_row is not None
                else {}
            ),
            projects={
                row.project_id: ProjectRecord(**_row_payload(row))
                for row in project_records
            },
            teams={
                row.team_id: TeamRecord(**_row_payload(row))
                for row in team_by_id.values()
            },
            team_memberships={
                row.membership_id: TeamMembershipRecord(**_row_payload(row))
                for row in membership_rows
            },
            permission_grants={
                row.grant_id: PermissionGrantRecord(**_row_payload(row))
                for row in grant_rows
            },
        )


def _authorize_document_control(
    session: Session,
    *,
    document: DocumentRecord,
    presented_browser_session_token: str,
    expected_actor_type: str,
    expected_actor_id: str,
) -> UserRecord:
    authority_domain_keys = [
        "team:hierarchy-control",
        "team:membership-control",
    ]
    authority_identity_keys = [
        f"identity:session:{presented_browser_session_token}",
        identity_actor_owner_key(expected_actor_id),
        team_subject_owner_key(expected_actor_type, expected_actor_id),
        f"document:document:{document.document_id}",
    ]
    if document.scope_type == "team" and document.scope_id:
        authority_identity_keys.append(team_owner_key(document.scope_id))
    elif document.scope_type == "project" and document.scope_id:
        authority_domain_keys.append(f"project:acl-control:{document.scope_id}")
        authority_identity_keys.extend(
            (
                project_owner_key(document.scope_id),
                project_acl_subject_owner_key(
                    expected_actor_type, expected_actor_id
                ),
            )
        )
    acquire_mixed_owner_locks(
        session,
        exclusive_domain_keys=tuple(authority_domain_keys),
        exclusive_identity_keys=tuple(authority_identity_keys),
    )
    current_document = session.scalar(
        select(document_rows.AtlasDocumentRow)
        .where(
            document_rows.AtlasDocumentRow.document_id == document.document_id
        )
        .execution_options(populate_existing=True)
    )
    if current_document is None or asdict(_document_record(current_document)) != asdict(
        document
    ):
        raise DocumentProcessingCurrentnessConflict(
            "processing control document preimage changed"
        )
    actor = identity_rows.read_session_actor(session, presented_browser_session_token)
    if (
        actor is None
        or actor.actor_type != expected_actor_type
        or actor.actor_id != expected_actor_id
    ):
        raise PermissionError("processing control request is unauthenticated")
    authority = _JobTransitionReadSql._authorization_state(
        session,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        scope_bindings=((document.scope_type, document.scope_id or ""),),
    )
    owner_active = document_owner_is_active(
        authority, document.scope_type, document.scope_id
    )
    allowed = owner_active and bool(
        document.uploader_actor_id == actor.actor_id
        or is_system_admin(authority, actor.actor_type, actor.actor_id)
    )
    if document.scope_type == "team":
        allowed = allowed or (
            owner_active
            and team_role_covers(
                direct_team_role(
                    authority,
                    actor.actor_type,
                    actor.actor_id,
                    document.scope_id or "",
                ),
                "admin",
            )
        )
    elif document.scope_type == "project" and document.scope_id:
        allowed = allowed or (
            owner_active
            and resolve_access(
                authority,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                project_id=document.scope_id,
                action="permission_manage",
                persist=False,
            ).allowed
        )
    if not allowed:
        raise _ProcessingControlAuthorizationDenied(actor)
    return actor

@dataclass(frozen=True, slots=True)
class _OutboxDeliveryReadSql:
    session_factory: SessionFactory

    def get_outbox(self, outbox_id: str) -> TaskOutboxRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasTaskOutboxRow).where(
                    async_rows.AtlasTaskOutboxRow.outbox_id == outbox_id
                )
            )
            return _outbox_record(row) if row is not None else None

    def list_outbox(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TaskOutboxRecord]:
        if limit < 1 or limit > 200:
            raise ValueError("outbox limit must be between 1 and 200")
        statement = select(async_rows.AtlasTaskOutboxRow)
        if status is not None:
            statement = statement.where(async_rows.AtlasTaskOutboxRow.status == status)
        statement = statement.order_by(
            async_rows.AtlasTaskOutboxRow.created_at,
            async_rows.AtlasTaskOutboxRow.outbox_id,
        ).limit(limit)
        with self.session_factory() as session:
            return [_outbox_record(row) for row in session.scalars(statement).all()]

    @staticmethod
    def _new_outbox(
        *,
        task_name: str,
        queue_name: str,
        payload: Mapping[str, object],
        available_at: datetime,
        last_error_code: str | None = None,
        identity_salt: str | None = None,
    ) -> TaskOutboxRecord:
        validated = _validated_task_payload(payload)
        identity_payload: dict[str, object] = {
            "task_name": task_name,
            "queue_name": queue_name,
            "payload": validated,
        }
        if identity_salt is not None:
            identity_payload["identity_salt"] = identity_salt
        identity = _request_digest(identity_payload)
        return TaskOutboxRecord(
            outbox_id=f"outbox-{identity[:32]}",
            task_name=task_name,
            queue_name=queue_name,
            payload_schema_version=1,
            payload=validated,
            celery_task_id=f"task-{identity}",
            status="pending",
            claim_owner=None,
            claim_expires_at=None,
            attempts=0,
            available_at=available_at,
            last_error_code=last_error_code,
            created_at=available_at,
            dispatched_at=None,
        )

@dataclass(frozen=True, slots=True)
class _JobTransitionSql(_JobTransitionReadSql):
    def _publish_job_state_graph(
        self,
        session: Session,
        *,
        current_job: async_rows.AtlasProcessingJobRow,
        desired_job: ProcessingJobRecord,
        document: document_rows.AtlasDocumentRow,
        desired_document: DocumentRecord | None = None,
        outbox: tuple[TaskOutboxTransition, ...] = (),
        generations: tuple[ProcessingGenerationTransition, ...] = (),
        coordination_identity_keys: tuple[str, ...] = (),
        allow_operator_retry: bool = False,
        audit_events: tuple[AuditEventRecord, ...],
    ) -> None:
        """Publish a non-final job lifecycle transition through the owner graph."""

        _apply_sealed_family_mutation(
            session,
            _JobMutation(
                document_id=current_job.document_id,
                document_version_id=current_job.document_version_id,
                processing_generation=current_job.processing_generation,
                job_id=current_job.job_id,
                expected_document_lifecycle_epoch=(
                    document.resource_lifecycle_epoch
                ),
                document=desired_document,
                jobs=(
                    ProcessingJobTransition(
                        desired_job,
                        CurrentRowExpectation(
                            exists=True,
                            status=current_job.status,
                            attempt=current_job.attempt,
                            fence=current_job.fence,
                            claim_owner=current_job.lease_owner,
                            preimage=_job_record(current_job),
                        ),
                    ),
                ),
                outbox=outbox,
                generations=generations,
                coordination_identity_keys=coordination_identity_keys,
                audit_events=audit_events,
            ),
            allow_operator_retry=allow_operator_retry,
        )
        if desired_job.status in {"failed", "cancelled"}:
            _terminalize_canonical_revision(
                session,
                processing_revision_id=desired_job.processing_revision_id,
                state=desired_job.status,
            )

    def prepare_job(
        self,
        job_id: str,
        *,
        total_units: int,
        profile_id: str,
        profile_revision: int,
        expected_attempt: int,
        enqueue_batches: bool = True,
    ) -> list[str]:
        if total_units <= 0:
            raise ValueError("processing_source_empty")
        _validate_progress_total_bound(total_units)
        if not profile_id.strip() or profile_revision <= 0:
            raise ValueError("processing_profile_invalid")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if job is None:
                    raise ValueError("processing_job_not_found")
                if job.attempt != expected_attempt or job.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return []
                job.stage = "parsing"
                job.status = "running"
                job.progress_total = total_units
                job.progress_unit = "page"
                job.updated_at = now
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                    .with_for_update()
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                document.intake_status = "processing"
                document.current_stage = "parsing"
                document.processing_profile_id = profile_id
                document.processing_profile_revision = profile_revision
                document.failure_code = None
                document.processing_job_id = job_id
                if job.processing_generation is not None:
                    generation = session.scalar(
                        select(async_rows.AtlasProcessingGenerationRow)
                        .where(
                            async_rows.AtlasProcessingGenerationRow.document_id
                            == job.document_id,
                            async_rows.AtlasProcessingGenerationRow.processing_generation
                            == job.processing_generation,
                        )
                        .with_for_update()
                    )
                    if generation is None:
                        raise DocumentProcessingCurrentnessConflict(
                            "processing generation is missing"
                        )
                    generation.expected_page_count = total_units
                    if generation.profile_id == "pending":
                        generation.profile_id = profile_id
                        generation.profile_revision = profile_revision
                    elif (
                        generation.profile_id != profile_id
                        or generation.profile_revision != profile_revision
                    ):
                        raise DocumentProcessingCurrentnessConflict(
                            "processing execution pin changed before preparation"
                        )
                batch_ids: list[str] = []
                for page in range(1, total_units + 1):
                    batch_id = f"{job_id}:page:{page}"
                    batch_ids.append(batch_id)
                    checkpoint = session.scalar(
                        select(async_rows.AtlasProcessingCheckpointRow).where(
                            async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                            async_rows.AtlasProcessingCheckpointRow.unit_kind == "page",
                            async_rows.AtlasProcessingCheckpointRow.unit_start == page,
                            async_rows.AtlasProcessingCheckpointRow.unit_end == page,
                        )
                    )
                    if checkpoint is not None or not enqueue_batches:
                        continue
                    outbox = _new_task_outbox_record(
                        task_name="atlas.processing.process_batch",
                        queue_name="atlas.processing",
                        payload={
                            "job_id": job_id,
                            "batch_id": batch_id,
                            "attempt": expected_attempt,
                            "schema_version": 1,
                        },
                        available_at=now,
                    )
                    _publish_outbox_cas(
                        session,
                        TaskOutboxTransition(
                            outbox,
                            CurrentRowExpectation.absent(),
                        ),
                    )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="processing_job.prepared",
                        job_id=job_id,
                        document_id=job.document_id,
                        attempt=expected_attempt,
                        status="running",
                    )
                )
                session.commit()
                return batch_ids
            except Exception:
                session.rollback()
                raise

    def prepare_reindex(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        batch_size: int = 100,
    ) -> int:
        if batch_size <= 0 or batch_size > 2_000:
            raise ValueError("reindex batch size must be between 1 and 2000")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if job is None or job.job_kind != "reindex":
                    raise ValueError("reindex_job_invalid")
                if job.attempt != expected_attempt or job.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return 0
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                )
                generation = session.get(
                    async_rows.AtlasIndexGenerationRow,
                    job.index_generation_id,
                )
                if generation is None:
                    raise ValueError("active_index_generation_unavailable")
                source_index = generation.supersedes_index_generation_id
                if not source_index:
                    raise ValueError("active_index_generation_unavailable")
                if (
                    document is None
                    or document.active_index_generation_id != source_index
                    or document.active_processing_generation
                    != generation.source_processing_generation
                ):
                    raise ValueError("reindex_source_generation_changed")
                chunk_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(async_rows.AtlasSearchChunkRow)
                        .where(
                            async_rows.AtlasSearchChunkRow.index_generation_id
                            == source_index,
                            async_rows.AtlasSearchChunkRow.status == "active",
                        )
                    )
                    or 0
                )
                total_batches = max(1, (chunk_count + batch_size - 1) // batch_size)
                job.stage = "indexing"
                job.status = "running"
                job.progress_total = total_batches
                job.progress_unit = "batch"
                job.updated_at = now
                for ordinal in range(total_batches):
                    batch_id = f"{job_id}:reindex:{ordinal}"
                    outbox = _new_task_outbox_record(
                        task_name="atlas.indexing.reindex_generation",
                        queue_name="atlas.indexing",
                        payload={
                            "job_id": job_id,
                            "batch_id": batch_id,
                            "attempt": expected_attempt,
                            "schema_version": 1,
                        },
                        available_at=now,
                    )
                    _publish_outbox_cas(
                        session,
                        TaskOutboxTransition(
                            outbox,
                            CurrentRowExpectation.absent(),
                        ),
                    )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="reindex_job.prepared",
                        job_id=job_id,
                        document_id=job.document_id,
                        attempt=expected_attempt,
                        status="running",
                    )
                )
                session.commit()
                return total_batches
            except Exception:
                session.rollback()
                raise

    def mark_failure(
        self,
        job_id: str,
        *,
        fence: int,
        code: str,
        detail: str,
        transient: bool,
    ) -> None:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if (
                    job is None
                    or job.fence != fence
                    or job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return
                job.status = "retry_wait" if transient else "failed"
                job.failure_code = code
                job.failure_detail = detail[:512]
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
                if not transient:
                    _terminalize_canonical_revision(
                        session,
                        processing_revision_id=job.processing_revision_id,
                        state="failed",
                    )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="processing_job.failure_marked",
                        job_id=job_id,
                        document_id=job.document_id,
                        attempt=job.attempt,
                        status=job.status,
                        failure_code=code,
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def create_processing_job(
        self,
        *,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        progress_total: int | None = None,
        connection: Connection | None = None,
        execution_snapshot: ProcessingExecutionSnapshot | None = None,
        acceptance_identity: DocumentProcessingAcceptanceIdentity | None = None,
    ) -> ProcessingJobRecord | ProcessingControlResult | None:
        if connection is not None:
            joined_factory: SessionFactory = lambda: Session(
                bind=connection,
                join_transaction_mode="rollback_only",
            )
            return _JobTransitionSql(joined_factory).create_processing_job(
                document_id=document_id,
                document_version_id=document_version_id,
                job_kind=job_kind,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
                created_by=created_by,
                progress_total=progress_total,
                execution_snapshot=execution_snapshot,
                acceptance_identity=acceptance_identity,
            )
        if job_kind not in {"ingest", "reprocess", "reindex", "finalize"}:
            raise ValueError("unsupported processing job kind")
        _validate_progress_total_bound(progress_total)
        if acceptance_identity is not None:
            expected_identity = document_processing_acceptance_identity(
                document_id=document_id,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
                processing_generation=acceptance_identity.processing_generation,
            )
            if (
                job_kind not in {"ingest", "reprocess"}
                or execution_snapshot is None
                or acceptance_identity != expected_identity
            ):
                raise ValueError("document processing identity is invalid")
        command = {
            "document_id": document_id,
            "document_version_id": document_version_id,
            "job_kind": job_kind,
            "created_by": created_by,
            "progress_total": progress_total,
            "execution_snapshot": (
                _processing_execution_payload(execution_snapshot)
                if execution_snapshot is not None
                else None
            ),
        }
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    identity_keys=(
                        "document:job-idempotency:"
                        f"{idempotency_scope}:{idempotency_key}",
                    ),
                )
                existing = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(
                        async_rows.AtlasProcessingJobRow.idempotency_scope
                        == idempotency_scope,
                        async_rows.AtlasProcessingJobRow.idempotency_key
                        == idempotency_key,
                    )
                )
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(document_rows.AtlasDocumentRow.document_id == document_id)
                )
                if document is None:
                    raise ValueError("document_not_found")
                version_status = (
                    document_rows.AtlasDocumentVersionRow.payload["status"].as_string()
                )
                version = session.scalar(
                    select(document_rows.AtlasDocumentVersionRow)
                    .where(
                        document_rows.AtlasDocumentVersionRow.document_id
                        == document_id,
                        version_status.in_(("active", "staged")),
                    )
                    .order_by(
                        case((version_status == "staged", 0), else_=1),
                        document_rows.AtlasDocumentVersionRow.payload[
                            "created_at"
                        ].as_string().desc(),
                        document_rows.AtlasDocumentVersionRow.document_version_id.desc(),
                    )
                    .limit(1)
                )
                _validate_current_source_version(
                    document,
                    version,
                    document_version_id=document_version_id,
                )
                fingerprint = _request_digest(
                    {
                        **command,
                        "parent_lifecycle_epoch": (
                            document.resource_lifecycle_epoch
                        ),
                    }
                )
                if existing is not None:
                    if existing.request_fingerprint != fingerprint or (
                        acceptance_identity is not None
                        and existing.job_id != acceptance_identity.job_id
                    ):
                        raise ValueError("idempotency_key_reused")
                    return _job_record(existing)
                allocation_key = f"document:allocation:{document_id}"
                acquire_owner_locks(
                    session,
                    identity_keys=(
                        allocation_key,
                        f"document:document:{document_id}",
                    ),
                )
                session.expire_all()
                current_existing = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.idempotency_scope
                        == idempotency_scope,
                        async_rows.AtlasProcessingJobRow.idempotency_key
                        == idempotency_key,
                    )
                )
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id == document_id
                    )
                )
                version = session.scalar(
                    select(document_rows.AtlasDocumentVersionRow)
                    .where(
                        document_rows.AtlasDocumentVersionRow.document_id
                        == document_id,
                        version_status.in_(("active", "staged")),
                    )
                    .order_by(
                        case((version_status == "staged", 0), else_=1),
                        document_rows.AtlasDocumentVersionRow.payload[
                            "created_at"
                        ].as_string().desc(),
                        document_rows.AtlasDocumentVersionRow.document_version_id.desc(),
                    )
                    .limit(1)
                )
                if document is None:
                    raise ValueError("document_not_found")
                _validate_current_source_version(
                    document,
                    version,
                    document_version_id=document_version_id,
                )
                fingerprint = _request_digest(
                    {
                        **command,
                        "parent_lifecycle_epoch": (
                            document.resource_lifecycle_epoch
                        ),
                    }
                )
                if current_existing is not None:
                    if current_existing.request_fingerprint != fingerprint or (
                        acceptance_identity is not None
                        and current_existing.job_id != acceptance_identity.job_id
                    ):
                        raise ValueError("idempotency_key_reused")
                    return _job_record(current_existing)
                canonical_target = (
                    _resolve_canonical_processing_target(
                        session,
                        document=document,
                        execution_snapshot=execution_snapshot,
                        job_kind=job_kind,
                    )
                    if execution_snapshot is not None
                    and job_kind in {"ingest", "reprocess"}
                    else None
                )
                if canonical_target is not None:
                    if canonical_target.existing_job is not None:
                        session.commit()
                        return canonical_target.existing_job
                    if canonical_target.current_hit:
                        session.commit()
                        return None
                source_generation = int(document.active_processing_generation or 0)
                processing_generation = None
                if job_kind != "reindex":
                    latest_generation = session.scalar(
                        select(async_rows.AtlasProcessingGenerationRow)
                        .where(
                            async_rows.AtlasProcessingGenerationRow.document_id
                            == document_id
                        )
                        .order_by(
                            async_rows.AtlasProcessingGenerationRow.processing_generation.desc()
                        )
                        .limit(1)
                    )
                    latest_allocated = (
                        int(latest_generation.processing_generation)
                        if latest_generation is not None
                        else 0
                    )
                    processing_generation = max(
                        source_generation,
                        latest_allocated,
                    ) + 1
                if execution_snapshot is not None and processing_generation is None:
                    raise ValueError(
                        "processing execution snapshot requires a processing generation"
                    )
                if acceptance_identity is not None and (
                    processing_generation
                    != acceptance_identity.processing_generation
                ):
                    raise ValueError(
                        "new-document processing generation is not first"
                    )
                job_id = (
                    acceptance_identity.job_id
                    if acceptance_identity is not None
                    else f"job-{uuid4().hex}"
                )
                index_generation_id = (
                    acceptance_identity.index_generation_id
                    if acceptance_identity is not None
                    else f"idxgen-{uuid4().hex}"
                )
                record = ProcessingJobRecord(
                    job_id=job_id,
                    job_kind=job_kind,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    processing_identity_id=(
                        canonical_target.processing_identity_id
                        if canonical_target is not None
                        else None
                    ),
                    processing_revision_id=(
                        canonical_target.processing_revision_id
                        if canonical_target is not None
                        else None
                    ),
                    processing_generation=processing_generation,
                    index_generation_id=index_generation_id,
                    stage="queued",
                    status="queued",
                    progress_current=0,
                    progress_total=progress_total,
                    progress_unit="page",
                    attempt=1,
                    lease_owner=None,
                    lease_expires_at=None,
                    fence=0,
                    failure_code=None,
                    failure_detail=None,
                    idempotency_scope=idempotency_scope,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    created_by=created_by,
                    attempt_started_at=now,
                    created_at=now,
                    updated_at=now,
                )
                outbox = _new_task_outbox_record(
                    task_name="atlas.processing.prepare_job",
                    queue_name="atlas.processing",
                    payload={"job_id": job_id, "attempt": 1, "schema_version": 1},
                    available_at=now,
                )
                if (
                    acceptance_identity is not None
                    and outbox.outbox_id != acceptance_identity.outbox_id
                ):
                    raise RuntimeError(
                        "new-document processing outbox identity drifted"
                    )
                desired_document = replace(
                    _document_record(document),
                    intake_status="queued",
                    current_stage="queued",
                    warning_codes=[],
                    failure_code=None,
                    processing_job_id=job_id,
                )
                generation_transitions = (
                    (
                        ProcessingGenerationTransition(
                            ProcessingGenerationProjection(
                                document_id=document_id,
                                processing_generation=processing_generation,
                                document_version_id=document_version_id,
                                profile_id=(
                                    execution_snapshot.profile_id
                                    if execution_snapshot is not None
                                    else "pending"
                                ),
                                profile_revision=(
                                    execution_snapshot.profile_revision
                                    if execution_snapshot is not None
                                    else 1
                                ),
                                status="building",
                                expected_page_count=progress_total,
                                actual_page_count=0,
                                expected_evidence_count=None,
                                actual_evidence_count=0,
                                expected_chunk_count=None,
                                actual_chunk_count=0,
                                manifest_digest=None,
                                created_at=now,
                                published_at=None,
                            ),
                            CurrentRowExpectation.absent(),
                        ),
                    )
                    if processing_generation is not None
                    else ()
                )
                change_set = _JobMutation(
                        document_id=document_id,
                        document_version_id=document_version_id,
                        processing_generation=processing_generation,
                        job_id=job_id,
                        expected_document_lifecycle_epoch=(
                            document.resource_lifecycle_epoch
                        ),
                        document=desired_document,
                        jobs=(
                            ProcessingJobTransition(
                                record,
                                CurrentRowExpectation.absent(),
                            ),
                        ),
                        outbox=(
                            TaskOutboxTransition(
                                outbox,
                                CurrentRowExpectation.absent(),
                            ),
                        ),
                        generations=generation_transitions,
                        index_generations=(
                            IndexGenerationTransition(
                                IndexGenerationProjection(
                                    index_generation_id=index_generation_id,
                                    document_id=document_id,
                                    document_version_id=document_version_id,
                                    source_processing_generation=(
                                        processing_generation
                                        if processing_generation is not None
                                        else source_generation
                                    ),
                                    embedding_profile_id=(
                                        "multilingual-e5-small-v1"
                                    ),
                                    embedding_profile={},
                                    qdrant_collection="atlas_evidence_v1",
                                    status="building",
                                    expected_point_count=None,
                                    actual_point_count=0,
                                    expected_fts_count=None,
                                    actual_fts_count=0,
                                    manifest_digest=None,
                                    supersedes_index_generation_id=(
                                        document.active_index_generation_id
                                    ),
                                    created_at=now,
                                    published_at=None,
                                    processing_revision_id=(
                                        canonical_target.processing_revision_id
                                        if canonical_target is not None
                                        else None
                                    ),
                                ),
                                CurrentRowExpectation.absent(),
                            ),
                        ),
                        coordination_identity_keys=(
                            allocation_key,
                            "document:job-idempotency:"
                            f"{idempotency_scope}:{idempotency_key}",
                        ),
                        audit_events=(
                            _internal_event(
                                operation="processing_job.created",
                                job_id=job_id,
                                document_id=document_id,
                                actor_id=created_by,
                                message_code=(
                                    "document.upload_is_accepted_for_asynchronous_processing"
                                ),
                                attempt=1,
                                status="queued",
                            ),
                        ),
                    )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    change_set,
                    _document_processing_candidates(change_set),
                )
                current_existing = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.idempotency_scope
                        == idempotency_scope,
                        async_rows.AtlasProcessingJobRow.idempotency_key
                        == idempotency_key,
                    )
                )
                if current_existing is not None:
                    if current_existing.request_fingerprint != fingerprint:
                        raise ValueError("idempotency_key_reused")
                    return _job_record(current_existing)
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    lock_token=lock_token,
                )
                if execution_snapshot is not None:
                    if processing_generation is None:
                        raise RuntimeError(
                            "processing request snapshot lost its generation coordinate"
                        )
                    session.add(
                        async_rows.AtlasProcessingRequestSnapshotRow(
                            job_id=job_id,
                            document_id=document_id,
                            processing_generation=processing_generation,
                            accepted_attempt=record.attempt,
                            payload=_processing_execution_payload(
                                execution_snapshot
                            ),
                            created_at=now,
                        )
                    )
                session.commit()
                return record
            except Exception:
                session.rollback()
                raise

    def is_current_task_attempt(
        self,
        *,
        task_name: str,
        identity: Mapping[str, str],
        attempt: int | None,
    ) -> bool:
        if attempt is None or attempt < 0:
            return False
        if not set(identity).issubset(_OPAQUE_TASK_FIELDS):
            raise ValueError("task identity contains non-opaque fields")
        statement = select(
            func.max(
                sql_cast(
                    async_rows.AtlasTaskOutboxRow.payload["attempt"].as_string(),
                    Integer,
                )
            )
        ).where(async_rows.AtlasTaskOutboxRow.task_name == task_name)
        for key, value in sorted(identity.items()):
            if not isinstance(value, str) or not value:
                raise ValueError("task identity values must be opaque strings")
            statement = statement.where(
                async_rows.AtlasTaskOutboxRow.payload[key].as_string() == value
            )
        with self.session_factory() as session:
            latest = session.scalar(statement)
        return latest is not None and int(latest) == int(attempt)

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 90,
    ) -> tuple[ProcessingJobRecord, int] | None:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("job lease must be between 1 and 3600 seconds")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if row is None or row.status not in {"queued", "retry_wait", "running"}:
                    return None
                if (
                    row.lease_expires_at is not None
                    and row.lease_expires_at > now
                    and row.lease_owner != worker_id
                ):
                    return None
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id
                        == row.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                desired = replace(
                    _job_record(row),
                    status="running",
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    fence=row.fence + 1,
                    updated_at=now,
                )
                self._publish_job_state_graph(
                    session,
                    current_job=row,
                    desired_job=desired,
                    document=document,
                    audit_events=(
                        _internal_event(
                            operation="processing_job.claimed",
                            job_id=job_id,
                            document_id=row.document_id,
                            actor_id=worker_id,
                            attempt=row.attempt,
                            status=desired.status,
                        ),
                    ),
                )
                session.commit()
                return desired, desired.fence
            except Exception:
                session.rollback()
                raise

    def cancel_processing_job(
        self,
        job_id: str,
        *,
        presented_browser_session_token: str | None = None,
        expected_actor_type: str | None = None,
        expected_actor_id: str | None = None,
    ) -> ProcessingJobRecord:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if row is None:
                    raise ValueError("processing_job_not_found")
                outbox_rows = session.scalars(
                    select(async_rows.AtlasTaskOutboxRow)
                    .where(
                        async_rows.AtlasTaskOutboxRow.payload["job_id"].as_string()
                        == job_id,
                        async_rows.AtlasTaskOutboxRow.payload["attempt"].as_integer()
                        == row.attempt,
                        async_rows.AtlasTaskOutboxRow.status.in_(
                            ("pending", "dispatching")
                        ),
                    )
                    .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
                    .limit(_MAX_CURRENT_ATTEMPT_OUTBOX_ROWS + 1)
                ).all()
                if len(outbox_rows) > _MAX_CURRENT_ATTEMPT_OUTBOX_ROWS:
                    raise RuntimeError("job outbox set exceeds bounded mutation contract")
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id == row.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                control_actor = None
                if presented_browser_session_token is not None:
                    if expected_actor_type is None or expected_actor_id is None:
                        raise ValueError("processing control authority is incomplete")
                    try:
                        control_actor = _authorize_document_control(
                            session,
                            document=_document_record(document),
                            presented_browser_session_token=presented_browser_session_token,
                            expected_actor_type=expected_actor_type,
                            expected_actor_id=expected_actor_id,
                        )
                    except _ProcessingControlAuthorizationDenied as denied:
                        denial_event = _processing_control_event(
                            event_type="processing_job_control_denied",
                            actor_id=denied.actor.actor_id,
                            job=_job_record(row),
                            command="cancel",
                            message_code="processing.only_the_uploader_or_scope_admin_can_control_this_job",
                            terminal_status=row.status,
                            reason="missing_document_control_role",
                        )
                        AuditEventWriter(session).append_many((denial_event,))
                        session.commit()
                        raise ProcessingControlDenied(
                            "missing_document_control_role", denial_event
                        )
                if row.status not in {"queued", "running", "retry_wait", "cancelled"}:
                    if control_actor is None:
                        raise ValueError("processing_job_not_cancellable")
                    denial_event = _processing_control_event(
                        event_type="processing_job_control_denied",
                        actor_id=control_actor.actor_id,
                        job=_job_record(row),
                        command="cancel",
                        message_code="processing.only_an_active_processing_job_can_be_stopped",
                        terminal_status=row.status,
                        reason="processing_job_not_cancellable",
                    )
                    AuditEventWriter(session).append_many((denial_event,))
                    session.commit()
                    raise ProcessingControlDenied(
                        "processing_job_not_cancellable", denial_event
                    )
                if row.status == "cancelled":
                    replayed_job = _job_record(row)
                    if control_actor is None:
                        return replayed_job
                    replay_event = _processing_control_event(
                        event_type="processing_job_cancelled",
                        actor_id=control_actor.actor_id,
                        job=replayed_job,
                        command="cancel",
                        message_code="processing.processing_job_is_stopped",
                        terminal_status="cancelled",
                        replayed=True,
                    )
                    AuditEventWriter(session).append_many((replay_event,))
                    session.commit()
                    return ProcessingControlResult(replayed_job, replay_event)
                desired = replace(
                    _job_record(row),
                    status="cancelled",
                    fence=row.fence + 1,
                    lease_owner=None,
                    lease_expires_at=None,
                    failure_code=None,
                    failure_detail=None,
                    updated_at=now,
                )
                desired_document = (
                    replace(
                        _document_record(document),
                        intake_status="cancelled",
                        current_stage=row.stage,
                        failure_code=None,
                    )
                    if document.processing_job_id == job_id
                    else None
                )
                success_event = (
                    _processing_control_event(
                        event_type="processing_job_cancelled",
                        actor_id=control_actor.actor_id,
                        job=desired,
                        command="cancel",
                        message_code="processing.processing_job_is_stopped",
                        terminal_status="cancelled",
                    )
                    if control_actor is not None
                    else _internal_event(
                        operation="processing_job.cancelled",
                        job_id=job_id,
                        document_id=row.document_id,
                        message_code="processing.processing_job_is_stopped",
                        attempt=row.attempt,
                        status=desired.status,
                    )
                )
                self._publish_job_state_graph(
                    session,
                    current_job=row,
                    desired_job=desired,
                    document=document,
                    desired_document=desired_document,
                    outbox=tuple(
                        TaskOutboxTransition(
                            replace(
                                _outbox_record(outbox_row),
                                status="cancelled",
                                claim_owner=None,
                                claim_expires_at=None,
                            ),
                            CurrentRowExpectation(
                                exists=True,
                                status=outbox_row.status,
                                attempt=outbox_row.attempts,
                                fence=None,
                                claim_owner=outbox_row.claim_owner,
                                preimage=_outbox_record(outbox_row),
                            ),
                        )
                        for outbox_row in outbox_rows
                    ),
                    audit_events=(success_event,),
                )
                session.commit()
                return (
                    ProcessingControlResult(desired, success_event)
                    if control_actor is not None
                    else desired
                )
            except Exception:
                session.rollback()
                raise

    def retry_terminal_job(
        self,
        job_id: str,
        *,
        presented_browser_session_token: str | None = None,
        expected_actor_type: str | None = None,
        expected_actor_id: str | None = None,
    ) -> ProcessingJobRecord | ProcessingControlResult:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if row is None:
                    raise ValueError("processing_job_not_found")
                document = None
                if (
                    presented_browser_session_token is not None
                    or row.processing_revision_id is not None
                ):
                    document = session.scalar(
                        select(document_rows.AtlasDocumentRow).where(
                            document_rows.AtlasDocumentRow.document_id
                            == row.document_id
                        )
                    )
                    if document is None:
                        raise DocumentProcessingCurrentnessConflict(
                            "named document parent is missing"
                        )
                control_actor = None
                if presented_browser_session_token is not None:
                    assert document is not None
                    if expected_actor_type is None or expected_actor_id is None:
                        raise ValueError("processing control authority is incomplete")
                    try:
                        control_actor = _authorize_document_control(
                            session,
                            document=_document_record(document),
                            presented_browser_session_token=presented_browser_session_token,
                            expected_actor_type=expected_actor_type,
                            expected_actor_id=expected_actor_id,
                        )
                    except _ProcessingControlAuthorizationDenied as denied:
                        denial_event = _processing_control_event(
                            event_type="processing_job_control_denied",
                            actor_id=denied.actor.actor_id,
                            job=_job_record(row),
                            command="retry",
                            message_code="processing.only_the_uploader_or_scope_admin_can_control_this_job",
                            terminal_status=row.status,
                            reason="missing_document_control_role",
                        )
                        AuditEventWriter(session).append_many((denial_event,))
                        session.commit()
                        raise ProcessingControlDenied(
                            "missing_document_control_role", denial_event
                        )
                    if document.lifecycle_status != "active":
                        denial_event = _processing_control_event(
                            event_type="processing_job_control_denied",
                            actor_id=control_actor.actor_id,
                            job=_job_record(row),
                            command="retry",
                            message_code="processing.only_a_failed_or_stopped_job_can_start_a_new_attempt",
                            terminal_status=row.status,
                            reason="document_not_active",
                        )
                        AuditEventWriter(session).append_many((denial_event,))
                        session.commit()
                        raise ProcessingControlDenied(
                            "document_not_active", denial_event
                        )
                if row.status not in {"failed", "cancelled"}:
                    if control_actor is None:
                        raise ValueError("processing_job_not_retryable")
                    denial_event = _processing_control_event(
                        event_type="processing_job_control_denied",
                        actor_id=control_actor.actor_id,
                        job=_job_record(row),
                        command="retry",
                        message_code="processing.only_a_failed_or_stopped_job_can_start_a_new_attempt",
                        terminal_status=row.status,
                        reason="processing_job_not_retryable",
                    )
                    AuditEventWriter(session).append_many((denial_event,))
                    session.commit()
                    raise ProcessingControlDenied(
                        "processing_job_not_retryable", denial_event
                    )
                if (
                    row.processing_identity_id is not None
                    and row.processing_revision_id is not None
                ):
                    assert document is not None
                    snapshot_row = session.get(
                        async_rows.AtlasProcessingRequestSnapshotRow,
                        row.job_id,
                    )
                    if snapshot_row is None:
                        raise DocumentProcessingCurrentnessConflict(
                            "canonical retry snapshot is missing"
                        )
                    snapshot = _processing_execution_snapshot(snapshot_row.payload)
                    joined_factory: SessionFactory = lambda: Session(
                        bind=session.connection(),
                        join_transaction_mode="rollback_only",
                    )
                    successor = _JobTransitionSql(
                        joined_factory
                    ).create_processing_job(
                        document_id=row.document_id,
                        document_version_id=row.document_version_id,
                        job_kind="reprocess",
                        idempotency_scope=f"{row.idempotency_scope}:retry",
                        idempotency_key=f"{row.job_id}:{row.attempt + 1}",
                        created_by=row.created_by,
                        progress_total=row.progress_total,
                        execution_snapshot=snapshot,
                    )
                    if not isinstance(successor, ProcessingJobRecord):
                        raise DocumentProcessingCurrentnessConflict(
                            "canonical retry did not create a successor job"
                        )
                    success_event = (
                        _processing_control_event(
                            event_type="processing_job_retried",
                            actor_id=control_actor.actor_id,
                            job=successor,
                            command="retry",
                            message_code="processing.processing_job_retry_is_queued",
                            terminal_status=successor.status,
                        )
                        if control_actor is not None
                        else _internal_event(
                            operation="processing_job.retry_successor_created",
                            job_id=successor.job_id,
                            document_id=successor.document_id,
                            attempt=successor.attempt,
                            status=successor.status,
                        )
                    )
                    AuditEventWriter(session).append_many((success_event,))
                    session.commit()
                    return (
                        ProcessingControlResult(successor, success_event)
                        if control_actor is not None
                        else successor
                    )
                _validate_progress_total_bound(row.progress_total)
                next_attempt = int(row.attempt) + 1
                checkpoints = session.scalars(
                    select(async_rows.AtlasProcessingCheckpointRow)
                    .where(async_rows.AtlasProcessingCheckpointRow.job_id == job_id)
                    .order_by(async_rows.AtlasProcessingCheckpointRow.unit_start)
                    .limit(_MAX_RETRY_CHECKPOINT_ROWS + 1)
                ).all()
                if len(checkpoints) > _MAX_RETRY_CHECKPOINT_ROWS:
                    raise RuntimeError("checkpoint set exceeds bounded retry contract")
                tasks: list[tuple[str, str, dict[str, object]]] = []
                total = row.progress_total
                publication_retry = row.stage == "publishing" or (
                    row.stage == "indexing"
                    and total is not None
                    and row.progress_current >= total
                )
                if publication_retry:
                    tasks.append(
                        (
                            "atlas.processing.finalize_generation",
                            "atlas.processing",
                            {"job_id": job_id},
                        )
                    )
                elif total is None or row.stage in {"queued", "parsing"}:
                    tasks.append(
                        (
                            "atlas.processing.prepare_job",
                            "atlas.processing",
                            {"job_id": job_id},
                        )
                    )
                elif row.job_kind == "reindex":
                    tasks.extend(
                        (
                            "atlas.indexing.reindex_generation",
                            "atlas.indexing",
                            {
                                "job_id": job_id,
                                "batch_id": f"{job_id}:reindex:{ordinal}",
                            },
                        )
                        for ordinal in range(int(total))
                    )
                else:
                    committed = {
                        int(checkpoint.unit_start): checkpoint.batch_id
                        for checkpoint in checkpoints
                        if checkpoint.unit_kind == "page"
                    }
                    committed_batch_ids = tuple(sorted(committed.values()))
                    retry_chunks = session.scalars(
                        select(async_rows.AtlasSearchChunkRow).where(
                            async_rows.AtlasSearchChunkRow.index_generation_id
                            == row.index_generation_id,
                            async_rows.AtlasSearchChunkRow.batch_id.in_(
                                committed_batch_ids
                            ),
                        ).limit(5001)
                    ).all()
                    if len(retry_chunks) > 5000:
                        raise RuntimeError(
                            "retry search chunk set exceeds bounded contract"
                        )
                    chunk_ids_by_batch: dict[str, set[str]] = {}
                    for chunk in retry_chunks:
                        chunk_ids_by_batch.setdefault(chunk.batch_id, set()).add(
                            chunk.chunk_id
                        )
                    retry_chunk_ids = tuple(
                        sorted(
                            chunk_id
                            for chunk_ids in chunk_ids_by_batch.values()
                            for chunk_id in chunk_ids
                        )
                    )
                    mapped_chunk_ids = set(
                        session.scalars(
                            select(
                                async_rows.AtlasVectorPointMappingRow.chunk_id
                            ).where(
                                async_rows.AtlasVectorPointMappingRow.index_generation_id
                                == row.index_generation_id,
                                async_rows.AtlasVectorPointMappingRow.chunk_id.in_(
                                    retry_chunk_ids
                                ),
                            )
                        ).all()
                    )
                    checkpoint_by_batch = {
                        checkpoint.batch_id: checkpoint
                        for checkpoint in checkpoints
                        if checkpoint.unit_kind == "page"
                    }
                    indexed_batches = {
                        batch_id
                        for batch_id, checkpoint in checkpoint_by_batch.items()
                        if len(chunk_ids_by_batch.get(batch_id, set()))
                        == checkpoint.chunk_count
                        and chunk_ids_by_batch.get(batch_id, set()).issubset(
                            mapped_chunk_ids
                        )
                    }
                    tasks.extend(
                        (
                            "atlas.indexing.index_batch",
                            "atlas.indexing",
                            {"job_id": job_id, "batch_id": batch_id},
                        )
                        for _page, batch_id in sorted(committed.items())
                        if batch_id not in indexed_batches
                    )
                    tasks.extend(
                        (
                            "atlas.processing.process_batch",
                            "atlas.processing",
                            {"job_id": job_id, "batch_id": f"{job_id}:page:{page}"},
                        )
                        for page in range(1, int(total) + 1)
                        if page not in committed
                    )
                outbox_records = tuple(
                    _new_task_outbox_record(
                        task_name=task_name,
                        queue_name=queue_name,
                        payload={
                            **base_payload,
                            "attempt": next_attempt,
                            "schema_version": 1,
                        },
                        available_at=now,
                    )
                    for task_name, queue_name, base_payload in tasks
                )
                generation = None
                if row.processing_generation is not None:
                    generation = session.scalar(
                        select(async_rows.AtlasProcessingGenerationRow)
                        .where(
                            async_rows.AtlasProcessingGenerationRow.document_id
                            == row.document_id,
                            async_rows.AtlasProcessingGenerationRow.processing_generation
                            == row.processing_generation,
                        )
                    )
                if presented_browser_session_token is None:
                    document = session.scalar(
                        select(document_rows.AtlasDocumentRow).where(
                            document_rows.AtlasDocumentRow.document_id == row.document_id
                        )
                    )
                    if document is None:
                        raise DocumentProcessingCurrentnessConflict(
                            "named document parent is missing"
                        )
                desired = replace(
                    _job_record(row),
                    stage="publishing" if publication_retry else row.stage,
                    status="queued",
                    attempt=next_attempt,
                    fence=row.fence + 1,
                    lease_owner=None,
                    lease_expires_at=None,
                    failure_code=None,
                    failure_detail=None,
                    attempt_started_at=now,
                    updated_at=now,
                )
                desired_document = replace(
                    _document_record(document),
                    intake_status=(
                        "queued"
                        if row.stage not in {"parsing", "indexing", "publishing"}
                        else "processing"
                    ),
                    current_stage=(
                        ("publishing" if publication_retry else row.stage)
                        if row.stage in {"parsing", "indexing", "publishing"}
                        else "queued"
                    ),
                    failure_code=None,
                    processing_job_id=job_id,
                )
                generation_transitions = (
                    (
                        ProcessingGenerationTransition(
                            replace(
                                _processing_generation_record(generation),
                                status="building",
                            ),
                            CurrentRowExpectation(
                                exists=True,
                                status=generation.status,
                                attempt=None,
                                fence=None,
                                claim_owner=None,
                                preimage=_processing_generation_record(generation),
                            ),
                        ),
                    )
                    if generation is not None and generation.status == "failed"
                    else ()
                )
                success_event = (
                    _processing_control_event(
                        event_type="processing_job_retried",
                        actor_id=control_actor.actor_id,
                        job=_job_record(row),
                        command="retry",
                        message_code="processing.processing_job_retry_is_queued",
                        terminal_status=row.status,
                        next_attempt=desired.attempt,
                    )
                    if control_actor is not None
                    else _internal_event(
                        operation="processing_job.operator_retry_queued",
                        job_id=job_id,
                        document_id=row.document_id,
                        message_code="processing.processing_job_retry_is_queued",
                        attempt=next_attempt,
                        status="queued",
                    )
                )
                self._publish_job_state_graph(
                    session,
                    current_job=row,
                    desired_job=desired,
                    document=document,
                    desired_document=desired_document,
                    outbox=tuple(
                        TaskOutboxTransition(
                            outbox_record,
                            CurrentRowExpectation.absent(),
                        )
                        for outbox_record in outbox_records
                    ),
                    generations=generation_transitions,
                    allow_operator_retry=True,
                    audit_events=(success_event,),
                )
                session.commit()
                return (
                    ProcessingControlResult(desired, success_event)
                    if control_actor is not None
                    else desired
                )
            except Exception:
                session.rollback()
                raise

    def fail_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        code: str,
        detail: str,
    ) -> None:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if (
                    row is None
                    or row.attempt != expected_attempt
                    or row.status in {"succeeded", "failed", "cancelled"}
                ):
                    return
                outbox_rows = session.scalars(
                    select(async_rows.AtlasTaskOutboxRow)
                    .where(
                        async_rows.AtlasTaskOutboxRow.payload["job_id"].as_string()
                        == job_id,
                        async_rows.AtlasTaskOutboxRow.payload["attempt"].as_integer()
                        == expected_attempt,
                        async_rows.AtlasTaskOutboxRow.status.in_(
                            ("pending", "dispatching")
                        ),
                    )
                    .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
                    .limit(_MAX_CURRENT_ATTEMPT_OUTBOX_ROWS + 1)
                ).all()
                if len(outbox_rows) > _MAX_CURRENT_ATTEMPT_OUTBOX_ROWS:
                    raise RuntimeError("job outbox set exceeds bounded mutation contract")
                generation = None
                if row.processing_generation is not None:
                    generation = session.scalar(
                        select(async_rows.AtlasProcessingGenerationRow)
                        .where(
                            async_rows.AtlasProcessingGenerationRow.document_id
                            == row.document_id,
                            async_rows.AtlasProcessingGenerationRow.processing_generation
                            == row.processing_generation,
                        )
                    )
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id == row.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                desired = replace(
                    _job_record(row),
                    status="failed",
                    failure_code=code,
                    failure_detail=detail[:512],
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
                self._publish_job_state_graph(
                    session,
                    current_job=row,
                    desired_job=desired,
                    document=document,
                    desired_document=replace(
                        _document_record(document),
                        intake_status="failed",
                        current_stage="failed",
                        failure_code=code,
                        processing_job_id=job_id,
                    ),
                    outbox=tuple(
                        TaskOutboxTransition(
                            replace(
                                _outbox_record(outbox_row),
                                status="cancelled",
                                claim_owner=None,
                                claim_expires_at=None,
                            ),
                            CurrentRowExpectation(
                                exists=True,
                                status=outbox_row.status,
                                attempt=outbox_row.attempts,
                                fence=None,
                                claim_owner=outbox_row.claim_owner,
                                preimage=_outbox_record(outbox_row),
                            ),
                        )
                        for outbox_row in outbox_rows
                    ),
                    generations=(
                        (
                            ProcessingGenerationTransition(
                                replace(
                                    _processing_generation_record(generation),
                                    status="failed",
                                ),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=generation.status,
                                    attempt=None,
                                    fence=None,
                                    claim_owner=None,
                                    preimage=_processing_generation_record(generation),
                                ),
                            ),
                        )
                        if generation is not None
                        and generation.status == "building"
                        else ()
                    ),
                    audit_events=(
                        _internal_event(
                            operation="processing_job.failed",
                            job_id=job_id,
                            document_id=row.document_id,
                            message_code="processing.failed_safely",
                            attempt=expected_attempt,
                            status="failed",
                            failure_code=code,
                        ),
                    ),
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def schedule_retry(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        task_name: str,
        queue_name: str,
        payload: Mapping[str, str],
        code: str,
        detail: str,
        delay_seconds: int = 2,
    ) -> None:
        if delay_seconds < 0 or delay_seconds > 3600:
            raise ValueError("retry delay must be between 0 and 3600 seconds")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if (
                    row is None
                    or row.attempt != expected_attempt
                    or row.status in {"succeeded", "failed", "cancelled"}
                ):
                    return
                if payload.get("job_id") != job_id:
                    raise ValueError("retry outbox task does not match locked job")
                retry_payload: dict[str, object] = {
                    **dict(payload),
                    "attempt": expected_attempt,
                    "schema_version": 1,
                }
                work_identity_key = _outbox_work_identity_owner_key(
                    task_name=task_name,
                    queue_name=queue_name,
                    payload=retry_payload,
                )
                exact_identity = select(async_rows.AtlasTaskOutboxRow).where(
                    async_rows.AtlasTaskOutboxRow.task_name == task_name,
                    async_rows.AtlasTaskOutboxRow.queue_name == queue_name,
                    async_rows.AtlasTaskOutboxRow.payload["job_id"].as_string()
                    == job_id,
                    async_rows.AtlasTaskOutboxRow.payload["attempt"].as_integer()
                    == expected_attempt,
                    async_rows.AtlasTaskOutboxRow.payload == retry_payload,
                )
                pending_rows = session.scalars(
                    exact_identity
                    .where(
                        async_rows.AtlasTaskOutboxRow.status == "pending"
                    )
                    .order_by(
                        async_rows.AtlasTaskOutboxRow.created_at.desc(),
                        async_rows.AtlasTaskOutboxRow.outbox_id.desc(),
                    )
                    .limit(2)
                ).all()
                if len(pending_rows) > 1:
                    raise DocumentProcessingCurrentnessConflict(
                        "retry outbox pending identity is ambiguous"
                    )
                pending_outbox = pending_rows[0] if pending_rows else None
                latest_row = None
                if pending_outbox is None:
                    latest_rows = session.scalars(
                        exact_identity
                        .order_by(
                            async_rows.AtlasTaskOutboxRow.created_at.desc(),
                            async_rows.AtlasTaskOutboxRow.outbox_id.desc(),
                        )
                        .limit(1)
                    ).all()
                    latest_row = latest_rows[0] if latest_rows else None
                    if latest_row is not None and latest_row.status == "pending":
                        pending_outbox = latest_row
                available_at = now + timedelta(seconds=delay_seconds)
                if pending_outbox is None:
                    desired_outbox = _new_task_outbox_record(
                        task_name=task_name,
                        queue_name=queue_name,
                        payload=retry_payload,
                        available_at=available_at,
                        last_error_code=code,
                        identity_salt=(
                            f"retry-after:{latest_row.outbox_id}"
                            if latest_row is not None
                            else "retry-after:initial"
                        ),
                    )
                    outbox_transitions = (
                        TaskOutboxTransition(
                            desired_outbox,
                            CurrentRowExpectation.absent(),
                            allowed_dispatching_predecessor_id=(
                                latest_row.outbox_id
                                if latest_row is not None
                                and latest_row.status == "dispatching"
                                else None
                            ),
                        ),
                    )
                else:
                    # A pending exact successor already guarantees another
                    # delivery. Do not mutate it here: the dispatcher may claim
                    # or complete it while this job-only retry_wait transition
                    # commits. Dispatching predecessors never enter this branch,
                    # so their final dispatcher CAS stays independent.
                    outbox_transitions = ()
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id
                        == row.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                self._publish_job_state_graph(
                    session,
                    current_job=row,
                    desired_job=replace(
                        _job_record(row),
                        status="retry_wait",
                        failure_code=code,
                        failure_detail=detail[:512],
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    ),
                    document=document,
                    outbox=outbox_transitions,
                    coordination_identity_keys=(work_identity_key,),
                    audit_events=(
                        _internal_event(
                            operation=(
                                "processing_job.infrastructure_retry_scheduled"
                            ),
                            job_id=job_id,
                            document_id=row.document_id,
                            message_code=(
                                "processing.processing_job_retry_is_queued"
                            ),
                            attempt=expected_attempt,
                            status="retry_wait",
                            failure_code=code,
                        ),
                    ),
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

@dataclass(frozen=True, slots=True)
class _OutboxDeliverySql(_OutboxDeliveryReadSql):
    def claim_pending_outbox(
        self,
        worker_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        if limit < 1 or limit > 100:
            raise ValueError("outbox claim limit must be between 1 and 100")
        now = _utc_now()
        expires_at = now + timedelta(seconds=60)
        session = self.session_factory()
        with session:
            try:
                rows_to_claim = session.scalars(
                    select(async_rows.AtlasTaskOutboxRow)
                    .where(
                        async_rows.AtlasTaskOutboxRow.status == "pending",
                        async_rows.AtlasTaskOutboxRow.available_at <= now,
                    )
                    .order_by(
                        async_rows.AtlasTaskOutboxRow.created_at,
                        async_rows.AtlasTaskOutboxRow.outbox_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).all()
                claims: list[dict[str, object]] = []
                for row in rows_to_claim:
                    desired = replace(
                        _outbox_record(row),
                        status="dispatching",
                        claim_owner=worker_id,
                        claim_expires_at=expires_at,
                        attempts=row.attempts + 1,
                    )
                    _publish_outbox_cas(
                        session,
                        TaskOutboxTransition(
                            desired,
                            CurrentRowExpectation(
                                exists=True,
                                status=row.status,
                                attempt=row.attempts,
                                fence=None,
                                claim_owner=row.claim_owner,
                                preimage=_outbox_record(row),
                            ),
                        ),
                        current=row,
                    )
                    claims.append(
                        {
                            "outbox_id": desired.outbox_id,
                            "task_name": desired.task_name,
                            "queue_name": desired.queue_name,
                            "payload": dict(desired.payload),
                            "celery_task_id": desired.celery_task_id,
                        }
                    )
                if claims:
                    AuditEventWriter(session).append(
                        _internal_event(
                            operation="task_outbox.claimed",
                            job_id=None,
                            document_id=None,
                            actor_id=worker_id,
                            status="dispatching",
                        )
                    )
                    session.commit()
                return claims
            except Exception:
                session.rollback()
                raise

    def release_outbox(
        self,
        outbox_id: str,
        worker_id: str,
        error_code: str,
    ) -> None:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasTaskOutboxRow)
                    .where(async_rows.AtlasTaskOutboxRow.outbox_id == outbox_id)
                    .with_for_update()
                )
                if (
                    row is None
                    or row.status != "dispatching"
                    or row.claim_owner != worker_id
                ):
                    return
                desired = replace(
                    _outbox_record(row),
                    status="pending",
                    claim_owner=None,
                    claim_expires_at=None,
                    last_error_code=error_code,
                    available_at=now,
                )
                _publish_outbox_cas(
                    session,
                    TaskOutboxTransition(
                        desired,
                        CurrentRowExpectation(
                            exists=True,
                            status=row.status,
                            attempt=row.attempts,
                            fence=None,
                            claim_owner=row.claim_owner,
                            preimage=_outbox_record(row),
                        ),
                    ),
                    current=row,
                )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="task_outbox.released",
                        job_id=cast(str | None, desired.payload.get("job_id")),
                        document_id=None,
                        actor_id=worker_id,
                        status="pending",
                        failure_code=error_code,
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def complete_outbox(self, outbox_id: str, worker_id: str) -> None:
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                row = session.scalar(
                    select(async_rows.AtlasTaskOutboxRow)
                    .where(async_rows.AtlasTaskOutboxRow.outbox_id == outbox_id)
                    .with_for_update()
                )
                if (
                    row is None
                    or row.status != "dispatching"
                    or row.claim_owner != worker_id
                ):
                    return
                desired = replace(
                    _outbox_record(row),
                    status="dispatched",
                    claim_owner=None,
                    claim_expires_at=None,
                    last_error_code=None,
                    dispatched_at=now,
                )
                _publish_outbox_cas(
                    session,
                    TaskOutboxTransition(
                        desired,
                        CurrentRowExpectation(
                            exists=True,
                            status=row.status,
                            attempt=row.attempts,
                            fence=None,
                            claim_owner=row.claim_owner,
                            preimage=_outbox_record(row),
                        ),
                    ),
                    current=row,
                )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="task_outbox.completed",
                        job_id=cast(str | None, desired.payload.get("job_id")),
                        document_id=None,
                        actor_id=worker_id,
                        status="dispatched",
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

    def reconcile_expired_claims(self, *, limit: int = 100) -> None:
        if limit < 1 or limit > 200:
            raise ValueError("reconcile limit must be between 1 and 200")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                discovered_job_ids = tuple(
                    session.scalars(
                    select(async_rows.AtlasProcessingJobRow.job_id)
                    .where(
                        async_rows.AtlasProcessingJobRow.status == "running",
                        async_rows.AtlasProcessingJobRow.lease_expires_at <= now,
                    )
                    .order_by(
                        async_rows.AtlasProcessingJobRow.lease_expires_at,
                        async_rows.AtlasProcessingJobRow.job_id,
                    )
                    .limit(limit)
                    ).all()
                )
                discovered_outboxes = tuple(
                    session.execute(
                    select(
                        async_rows.AtlasTaskOutboxRow.outbox_id,
                        async_rows.AtlasTaskOutboxRow.task_name,
                        async_rows.AtlasTaskOutboxRow.queue_name,
                        async_rows.AtlasTaskOutboxRow.payload,
                    )
                    .where(
                        async_rows.AtlasTaskOutboxRow.status == "dispatching",
                        async_rows.AtlasTaskOutboxRow.claim_expires_at <= now,
                    )
                    .order_by(
                        async_rows.AtlasTaskOutboxRow.claim_expires_at,
                        async_rows.AtlasTaskOutboxRow.outbox_id,
                    )
                    .limit(limit)
                    ).all()
                )
                acquire_owner_locks(
                    session,
                    identity_keys=(
                        *(
                            f"document:job:{job_id}"
                            for job_id in discovered_job_ids
                        ),
                        *(
                            f"document:outbox:{row.outbox_id}"
                            for row in discovered_outboxes
                        ),
                        *(
                            _outbox_work_identity_owner_key(
                                task_name=row.task_name,
                                queue_name=row.queue_name,
                                payload=dict(row.payload),
                            )
                            for row in discovered_outboxes
                        )
                    ),
                )
                expire_all = getattr(session, "expire_all", None)
                if not callable(expire_all):
                    raise TypeError(
                        "expired reconciliation requires Session.expire_all"
                    )
                expire_all()
                jobs = (
                    session.scalars(
                        select(async_rows.AtlasProcessingJobRow)
                        .where(
                            async_rows.AtlasProcessingJobRow.job_id.in_(
                                discovered_job_ids
                            )
                        )
                        .order_by(async_rows.AtlasProcessingJobRow.job_id)
                        .with_for_update()
                    ).all()
                    if discovered_job_ids
                    else []
                )
                jobs = [
                    row
                    for row in jobs
                    if row.status == "running"
                    and row.lease_expires_at is not None
                    and row.lease_expires_at <= now
                ]
                discovered_outbox_ids = tuple(
                    row.outbox_id for row in discovered_outboxes
                )
                outboxes = (
                    session.scalars(
                        select(async_rows.AtlasTaskOutboxRow)
                        .where(
                            async_rows.AtlasTaskOutboxRow.outbox_id.in_(
                                discovered_outbox_ids
                            )
                        )
                        .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
                        .with_for_update()
                    ).all()
                    if discovered_outbox_ids
                    else []
                )
                outboxes = [
                    row
                    for row in outboxes
                    if row.status == "dispatching"
                    and row.claim_expires_at is not None
                    and row.claim_expires_at <= now
                ]
                completed_predecessors: list[
                    async_rows.AtlasTaskOutboxRow
                ] = []
                superseded_predecessors: list[
                    async_rows.AtlasTaskOutboxRow
                ] = []
                for row in outboxes:
                    desired_successor = _new_task_outbox_record(
                        task_name=row.task_name,
                        queue_name=row.queue_name,
                        payload=dict(row.payload),
                        available_at=now,
                        last_error_code="dispatch_claim_expired",
                        identity_salt=f"retry-after:{row.outbox_id}",
                    )
                    exact_pending = session.scalars(
                        select(async_rows.AtlasTaskOutboxRow)
                        .where(
                            async_rows.AtlasTaskOutboxRow.task_name
                            == row.task_name,
                            async_rows.AtlasTaskOutboxRow.queue_name
                            == row.queue_name,
                            async_rows.AtlasTaskOutboxRow.payload
                            == dict(row.payload),
                            async_rows.AtlasTaskOutboxRow.status == "pending",
                        )
                        .order_by(async_rows.AtlasTaskOutboxRow.outbox_id)
                        .limit(2)
                    ).all()
                    if len(exact_pending) > 1:
                        raise DocumentProcessingCurrentnessConflict(
                            "expired outbox retry identity is ambiguous"
                        )
                    pending_delivery = (
                        exact_pending[0] if exact_pending else None
                    )
                    successor = None
                    if pending_delivery is None:
                        successor = session.scalar(
                            select(async_rows.AtlasTaskOutboxRow).where(
                                async_rows.AtlasTaskOutboxRow.outbox_id
                                == desired_successor.outbox_id
                            )
                        )
                    if successor is not None and (
                        successor.task_name != row.task_name
                        or successor.queue_name != row.queue_name
                        or dict(successor.payload) != dict(row.payload)
                    ):
                        raise DocumentProcessingCurrentnessConflict(
                            "retry successor identity is inconsistent"
                        )
                    if pending_delivery is None and successor is None:
                        _publish_outbox_cas(
                            session,
                            TaskOutboxTransition(
                                desired_successor,
                                CurrentRowExpectation.absent(),
                                allowed_dispatching_predecessor_id=row.outbox_id,
                            ),
                            current=None,
                        )
                        # No worker-authored successor proves broker delivery.
                        # Cancel the unknowable predecessor and advance one
                        # deterministic recovery successor instead of reviving it.
                        desired_outbox = replace(
                            _outbox_record(row),
                            status="cancelled",
                            claim_owner=None,
                            claim_expires_at=None,
                            last_error_code=(
                                "dispatch_claim_expired_superseded"
                            ),
                        )
                        superseded_predecessors.append(row)
                    elif (
                        pending_delivery is not None
                        and pending_delivery.outbox_id
                        != desired_successor.outbox_id
                    ):
                        # A pre-existing exact pending delivery is authoritative,
                        # but it does not prove this predecessor reached a worker.
                        desired_outbox = replace(
                            _outbox_record(row),
                            status="cancelled",
                            claim_owner=None,
                            claim_expires_at=None,
                            last_error_code=(
                                "dispatch_claim_expired_superseded"
                            ),
                        )
                        superseded_predecessors.append(row)
                    else:
                        # A worker-authored successor proves this predecessor was
                        # delivered before the dispatcher crashed.
                        desired_outbox = replace(
                            _outbox_record(row),
                            status="dispatched",
                            claim_owner=None,
                            claim_expires_at=None,
                            last_error_code=None,
                            dispatched_at=now,
                        )
                        completed_predecessors.append(row)
                    _publish_outbox_cas(
                        session,
                        TaskOutboxTransition(
                            desired_outbox,
                            CurrentRowExpectation(
                                exists=True,
                                status=row.status,
                                attempt=row.attempts,
                                fence=None,
                                claim_owner=row.claim_owner,
                                preimage=_outbox_record(row),
                            ),
                        ),
                        current=row,
                        reconciliation_at=now,
                    )
                for row in jobs:
                    _publish_job_lease_reconciliation_cas(
                        session,
                        ProcessingJobTransition(
                            replace(
                                _job_record(row),
                                status="retry_wait",
                                lease_owner=None,
                                lease_expires_at=None,
                                failure_code="worker_lease_expired",
                                updated_at=now,
                            ),
                            CurrentRowExpectation(
                                exists=True,
                                status=row.status,
                                attempt=row.attempt,
                                fence=row.fence,
                                claim_owner=row.lease_owner,
                                preimage=_job_record(row),
                            ),
                        ),
                        current=row,
                        reconciliation_at=now,
                    )
                audit_writer = AuditEventWriter(session)
                for predecessor in completed_predecessors:
                    audit_writer.append(
                        _internal_event(
                            operation=(
                                "task_outbox.expired_predecessors_completed"
                            ),
                            job_id=cast(
                                str | None,
                                predecessor.payload.get("job_id"),
                            ),
                            document_id=None,
                            status="dispatched",
                            failure_code=(
                                "dispatch_claim_expired_after_retry_successor"
                            ),
                        )
                    )
                for predecessor in superseded_predecessors:
                    audit_writer.append(
                        _internal_event(
                            operation=(
                                "task_outbox.expired_predecessors_superseded"
                            ),
                            job_id=cast(
                                str | None,
                                predecessor.payload.get("job_id"),
                            ),
                            document_id=None,
                            status="cancelled",
                            failure_code=(
                                "dispatch_claim_expired_superseded"
                            ),
                        )
                    )
                if jobs:
                    audit_writer.append(
                        _internal_event(
                            operation="processing_jobs.leases_reconciled",
                            job_id=None,
                            document_id=None,
                            status="retry_wait",
                            failure_code="worker_lease_expired",
                        )
                    )
                if outboxes or jobs:
                    session.commit()
            except Exception:
                session.rollback()
                raise

@dataclass(frozen=True, slots=True)
class _BatchCheckpointSql:
    session_factory: SessionFactory

    def _bind(self) -> Any:
        session = self.session_factory()
        try:
            return session.get_bind()
        finally:
            session.close()

    def reconcile_incomplete_page_batches(self, *, limit: int = 100) -> int:
        """Schedule bounded page successors for orphaned processing/index work."""

        if limit <= 0 or limit > 100:
            raise ValueError("page reconciliation limit must be between 1 and 100")
        now = _utc_now()
        grace_started_at = now - timedelta(seconds=300)
        candidates: list[tuple[str, str, int, str, str]] = []
        with self.session_factory() as session:
            expired_claim_exists = (
                select(async_rows.AtlasProcessingBatchClaimRow.batch_id)
                .where(
                    async_rows.AtlasProcessingBatchClaimRow.job_id
                    == async_rows.AtlasProcessingJobRow.job_id,
                    async_rows.AtlasProcessingBatchClaimRow.attempt
                    == async_rows.AtlasProcessingJobRow.attempt,
                    async_rows.AtlasProcessingBatchClaimRow.lease_expires_at <= now,
                )
                .exists()
            )
            recent_processing_activity = session.scalar(
                select(async_rows.AtlasProcessingJobRow.job_id)
                .where(
                    async_rows.AtlasProcessingJobRow.status == "running",
                    async_rows.AtlasProcessingJobRow.updated_at
                    >= grace_started_at,
                )
                .limit(1)
            ) is not None
            jobs = session.scalars(
                select(async_rows.AtlasProcessingJobRow)
                .where(
                    async_rows.AtlasProcessingJobRow.progress_total.is_not(None),
                    or_(
                        and_(
                            async_rows.AtlasProcessingJobRow.status == "running",
                            or_(
                                async_rows.AtlasProcessingJobRow.updated_at
                                < grace_started_at,
                                expired_claim_exists,
                            ),
                        ),
                        and_(
                            async_rows.AtlasProcessingJobRow.status == "retry_wait",
                            async_rows.AtlasProcessingJobRow.failure_code
                            == "worker_lease_expired",
                        ),
                    ),
                )
                .order_by(async_rows.AtlasProcessingJobRow.updated_at)
                .limit(100)
            ).all()
            for job in jobs:
                total = int(job.progress_total or 0)
                for page_number in range(1, total + 1):
                    if len(candidates) >= limit:
                        break
                    batch_id = f"{job.job_id}:page:{page_number}"
                    claim = session.scalar(
                        select(async_rows.AtlasProcessingBatchClaimRow).where(
                            async_rows.AtlasProcessingBatchClaimRow.batch_id
                            == batch_id,
                            async_rows.AtlasProcessingBatchClaimRow.job_id
                            == job.job_id,
                            async_rows.AtlasProcessingBatchClaimRow.attempt
                            == job.attempt,
                        )
                    )
                    if claim is not None and claim.lease_expires_at > now:
                        continue
                    checkpoint = session.scalar(
                        select(async_rows.AtlasProcessingCheckpointRow).where(
                            async_rows.AtlasProcessingCheckpointRow.job_id
                            == job.job_id,
                            async_rows.AtlasProcessingCheckpointRow.batch_id
                            == batch_id,
                        )
                    )
                    if checkpoint is None:
                        if claim is None and recent_processing_activity:
                            continue
                        task_name = "atlas.processing.process_batch"
                        code = "processing_page_orphaned"
                    else:
                        chunk_ids = tuple(
                            session.scalars(
                                select(async_rows.AtlasSearchChunkRow.chunk_id).where(
                                    async_rows.AtlasSearchChunkRow.batch_id == batch_id,
                                    async_rows.AtlasSearchChunkRow.index_generation_id
                                    == job.index_generation_id,
                                ).limit(2001)
                            ).all()
                        )
                        if len(chunk_ids) > 2000:
                            raise RuntimeError(
                                "search chunk batch exceeds bounded contract"
                            )
                        mapped_chunk_ids = set(
                            session.scalars(
                                select(
                                    async_rows.AtlasVectorPointMappingRow.chunk_id
                                ).where(
                                    async_rows.AtlasVectorPointMappingRow.index_generation_id
                                    == job.index_generation_id,
                                    async_rows.AtlasVectorPointMappingRow.chunk_id.in_(
                                        chunk_ids
                                    ),
                                )
                            ).all()
                        )
                        if not set(chunk_ids).difference(mapped_chunk_ids):
                            continue
                        task_name = "atlas.indexing.index_batch"
                        code = "index_page_orphaned"
                    queue_name = (
                        "atlas.processing"
                        if task_name == "atlas.processing.process_batch"
                        else "atlas.indexing"
                    )
                    payload = {
                        "job_id": job.job_id,
                        "batch_id": batch_id,
                        "attempt": job.attempt,
                        "schema_version": 1,
                    }
                    live_delivery = session.scalar(
                        select(async_rows.AtlasTaskOutboxRow.outbox_id).where(
                            async_rows.AtlasTaskOutboxRow.task_name == task_name,
                            async_rows.AtlasTaskOutboxRow.queue_name == queue_name,
                            async_rows.AtlasTaskOutboxRow.payload == payload,
                            or_(
                                async_rows.AtlasTaskOutboxRow.status.in_(
                                    ("pending", "dispatching")
                                ),
                                and_(
                                    async_rows.AtlasTaskOutboxRow.status
                                    == "dispatched",
                                    async_rows.AtlasTaskOutboxRow.dispatched_at
                                    >= grace_started_at,
                                ),
                            ),
                        ).limit(1)
                    )
                    if live_delivery is not None:
                        continue
                    candidates.append(
                        (job.job_id, batch_id, job.attempt, task_name, code)
                    )
                if len(candidates) >= limit:
                    break

        scheduled = 0
        for job_id, batch_id, attempt, task_name, code in candidates:
            if self.schedule_page_batch_retry(
                job_id,
                batch_id,
                expected_attempt=attempt,
                task_name=task_name,
                code=code,
            ):
                scheduled += 1
        return scheduled

    @contextmanager
    def batch_execution(
        self,
        job_id: str,
        batch_id: str,
    ) -> Iterator[ProcessingJobView | None]:
        try:
            unit_start = unit_end = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_batch_id") from exc
        if batch_id != f"{job_id}:page:{unit_start}" or unit_start <= 0:
            raise ValueError("invalid_batch_id")
        with self._claim_execution(
            job_id=job_id,
            batch_id=batch_id,
            expected_attempt=None,
            unit_kind="page",
            unit_start=unit_start,
            unit_end=unit_end,
            identity=f"atlas-processing-batch:{job_id}:{batch_id}",
        ) as claimed:
            yield claimed

    def schedule_page_batch_retry(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
        task_name: str,
        code: str,
        delay_seconds: int = 2,
    ) -> bool:
        """Publish one page successor without changing lifecycle state."""

        task_queues = {
            "atlas.processing.process_batch": "atlas.processing",
            "atlas.indexing.index_batch": "atlas.indexing",
        }
        queue_name = task_queues.get(task_name)
        if queue_name is None:
            raise ValueError("page retry task is not supported")
        if delay_seconds < 0 or delay_seconds > 3600:
            raise ValueError("retry delay must be between 0 and 3600 seconds")
        try:
            page_number = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_batch_id") from exc
        if page_number <= 0 or batch_id != f"{job_id}:page:{page_number}":
            raise ValueError("invalid_batch_id")

        retry_payload: dict[str, object] = {
            "job_id": job_id,
            "batch_id": batch_id,
            "attempt": expected_attempt,
            "schema_version": 1,
        }
        work_identity_key = _outbox_work_identity_owner_key(
            task_name=task_name,
            queue_name=queue_name,
            payload=retry_payload,
        )
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.job_id == job_id
                    )
                )
                if (
                    job is None
                    or job.attempt != expected_attempt
                    or job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return False
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                )
                generation = session.scalar(
                    select(async_rows.AtlasIndexGenerationRow).where(
                        async_rows.AtlasIndexGenerationRow.index_generation_id
                        == job.index_generation_id
                    )
                )
                if document is None or generation is None:
                    return False

                exact_identity = select(async_rows.AtlasTaskOutboxRow).where(
                    async_rows.AtlasTaskOutboxRow.task_name == task_name,
                    async_rows.AtlasTaskOutboxRow.queue_name == queue_name,
                    async_rows.AtlasTaskOutboxRow.payload == retry_payload,
                )
                pending_rows = session.scalars(
                    exact_identity.where(
                        async_rows.AtlasTaskOutboxRow.status == "pending"
                    )
                    .order_by(
                        async_rows.AtlasTaskOutboxRow.created_at.desc(),
                        async_rows.AtlasTaskOutboxRow.outbox_id.desc(),
                    )
                    .limit(2)
                ).all()
                if len(pending_rows) > 1:
                    raise DocumentProcessingCurrentnessConflict(
                        "page retry outbox pending identity is ambiguous"
                    )
                latest = session.scalar(
                    exact_identity.order_by(
                        async_rows.AtlasTaskOutboxRow.created_at.desc(),
                        async_rows.AtlasTaskOutboxRow.outbox_id.desc(),
                    ).limit(1)
                )
                desired_outbox = _new_task_outbox_record(
                    task_name=task_name,
                    queue_name=queue_name,
                    payload=retry_payload,
                    available_at=_utc_now() + timedelta(seconds=delay_seconds),
                    last_error_code=code,
                    identity_salt=(
                        f"retry-after:{latest.outbox_id}"
                        if latest is not None
                        else "retry-after:initial"
                    ),
                )
                outbox_transition = TaskOutboxTransition(
                    desired_outbox,
                    CurrentRowExpectation.absent(),
                    allowed_dispatching_predecessor_id=(
                        latest.outbox_id
                        if latest is not None and latest.status == "dispatching"
                        else None
                    ),
                )
                change_set = _BatchMutation(
                    document_id=job.document_id,
                    document_version_id=job.document_version_id,
                    processing_generation=job.processing_generation,
                    job_id=job_id,
                    expected_document_lifecycle_epoch=(
                        document.resource_lifecycle_epoch
                    ),
                    jobs=(
                        ProcessingJobTransition(
                            _job_record(job),
                            CurrentRowExpectation(
                                exists=True,
                                status=job.status,
                                attempt=job.attempt,
                                fence=job.fence,
                                claim_owner=job.lease_owner,
                                preimage=_job_record(job),
                            ),
                        ),
                    ),
                    outbox=(() if pending_rows else (outbox_transition,)),
                    coordination_identity_keys=tuple(
                        sorted((f"document:batch:{batch_id}", work_identity_key))
                    ),
                    audit_events=(
                        _internal_event(
                            operation="processing_page.retry_scheduled",
                            job_id=job_id,
                            document_id=job.document_id,
                            attempt=expected_attempt,
                            status=job.status,
                            failure_code=code,
                            batch_id=batch_id,
                        ),
                    ),
                )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    change_set,
                    _document_processing_candidates(change_set),
                )
                session.expire_all()
                locked_job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.job_id == job_id
                    )
                )
                locked_document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                )
                locked_generation = session.scalar(
                    select(async_rows.AtlasIndexGenerationRow).where(
                        async_rows.AtlasIndexGenerationRow.index_generation_id
                        == job.index_generation_id
                    )
                )
                checkpoint = session.scalar(
                    select(async_rows.AtlasProcessingCheckpointRow).where(
                        async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                        async_rows.AtlasProcessingCheckpointRow.batch_id == batch_id,
                    )
                )
                locked_pending_rows = session.scalars(
                    exact_identity.where(
                        async_rows.AtlasTaskOutboxRow.status == "pending"
                    )
                    .order_by(
                        async_rows.AtlasTaskOutboxRow.created_at.desc(),
                        async_rows.AtlasTaskOutboxRow.outbox_id.desc(),
                    )
                    .limit(2)
                ).all()
                if len(locked_pending_rows) > 1:
                    raise DocumentProcessingCurrentnessConflict(
                        "page retry outbox pending identity is ambiguous"
                    )
                if (
                    locked_job is None
                    or locked_job.attempt != expected_attempt
                    or locked_job.status in {"succeeded", "failed", "cancelled"}
                    or locked_document is None
                    or locked_document.resource_lifecycle_epoch
                    != document.resource_lifecycle_epoch
                    or locked_document.lifecycle_status not in {"active", "restoring"}
                    or locked_generation is None
                    or locked_generation.status != "building"
                    or locked_generation.document_id != locked_job.document_id
                    or locked_generation.document_version_id
                    != locked_job.document_version_id
                    or locked_generation.source_processing_generation
                    != locked_job.processing_generation
                ):
                    return False
                if task_name == "atlas.processing.process_batch":
                    if checkpoint is not None:
                        return False
                else:
                    if checkpoint is None:
                        return False
                    chunk_ids = tuple(
                        session.scalars(
                            select(async_rows.AtlasSearchChunkRow.chunk_id).where(
                                async_rows.AtlasSearchChunkRow.batch_id == batch_id,
                                async_rows.AtlasSearchChunkRow.index_generation_id
                                == locked_job.index_generation_id,
                            ).limit(2001)
                        ).all()
                    )
                    if len(chunk_ids) > 2000:
                        raise RuntimeError("search chunk batch exceeds bounded contract")
                    mapped_chunk_ids = set(
                        session.scalars(
                            select(async_rows.AtlasVectorPointMappingRow.chunk_id).where(
                                async_rows.AtlasVectorPointMappingRow.index_generation_id
                                == locked_job.index_generation_id,
                                async_rows.AtlasVectorPointMappingRow.chunk_id.in_(chunk_ids),
                            )
                        ).all()
                    )
                    if not set(chunk_ids).difference(mapped_chunk_ids):
                        return False
                if locked_pending_rows:
                    session.rollback()
                    return True
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    lock_token=lock_token,
                )
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    @contextmanager
    def index_batch_execution(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
    ) -> Iterator[ProcessingJobView | None]:
        try:
            unit_start = unit_end = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_batch_id") from exc
        if batch_id != f"{job_id}:page:{unit_start}" or unit_start <= 0:
            raise ValueError("invalid_batch_id")
        with self._claim_execution(
            job_id=job_id,
            batch_id=batch_id,
            expected_attempt=expected_attempt,
            unit_kind="page",
            unit_start=unit_start,
            unit_end=unit_end,
            identity=f"atlas-processing-batch:{job_id}:{batch_id}",
            set_job_running=False,
        ) as claimed:
            yield claimed

    @contextmanager
    def preparation_execution(
        self,
        job_id: str,
        *,
        expected_attempt: int,
    ) -> Iterator[ProcessingJobView | None]:
        with self._claim_execution(
            job_id=job_id,
            batch_id=f"{job_id}:prepare",
            expected_attempt=expected_attempt,
            unit_kind="batch",
            unit_start=1,
            unit_end=1,
            identity=f"atlas-processing-prepare:{job_id}",
        ) as claimed:
            yield claimed

    @contextmanager
    def _claim_execution(
        self,
        *,
        job_id: str,
        batch_id: str,
        expected_attempt: int | None,
        unit_kind: str,
        unit_start: int,
        unit_end: int,
        identity: str,
        set_job_running: bool = True,
    ) -> Iterator[ProcessingJobView | None]:
        with self._bind().connect() as connection:
            acquired = bool(
                connection.execute(
                    text(
                        "SELECT pg_try_advisory_lock("
                        "hashtextextended(:identity, 0))"
                    ),
                    {"identity": identity},
                ).scalar_one()
            )
            connection.commit()
            claim_token: str | None = None
            try:
                if not acquired:
                    yield None
                    return
                now = _utc_now()
                claim_token = f"claim-{uuid4().hex}"
                with connection.begin():
                    row = connection.execute(
                        select(async_rows.AtlasProcessingJobRow.__table__).where(
                            async_rows.AtlasProcessingJobRow.job_id == job_id
                        )
                    ).mappings().one_or_none()
                    if (
                        row is None
                        or (
                            expected_attempt is not None
                            and int(row["attempt"]) != expected_attempt
                        )
                        or row["status"] in {"succeeded", "failed", "cancelled"}
                    ):
                        row = None
                    else:
                        if set_job_running:
                            connection.execute(
                                update(async_rows.AtlasProcessingJobRow)
                                .where(
                                    async_rows.AtlasProcessingJobRow.job_id == job_id
                                )
                                .values(status="running", updated_at=now)
                            )
                        connection.execute(
                            pg_insert(async_rows.AtlasProcessingBatchClaimRow)
                            .values(
                                batch_id=batch_id,
                                job_id=job_id,
                                attempt=int(row["attempt"]),
                                claim_token=claim_token,
                                unit_kind=unit_kind,
                                unit_start=unit_start,
                                unit_end=unit_end,
                                lease_expires_at=now
                                + timedelta(
                                    seconds=_PROCESSING_BATCH_CLAIM_LEASE_SECONDS
                                ),
                                created_at=now,
                                updated_at=now,
                            )
                            .on_conflict_do_update(
                                index_elements=[
                                    async_rows.AtlasProcessingBatchClaimRow.batch_id
                                ],
                                set_={
                                    "attempt": int(row["attempt"]),
                                    "claim_token": claim_token,
                                    "unit_kind": unit_kind,
                                    "unit_start": unit_start,
                                    "unit_end": unit_end,
                                    "lease_expires_at": now
                                    + timedelta(
                                        seconds=(
                                            _PROCESSING_BATCH_CLAIM_LEASE_SECONDS
                                        )
                                    ),
                                    "updated_at": now,
                                },
                            )
                        )
                        if set_job_running:
                            row = {**row, "status": "running", "updated_at": now}
                heartbeat_stop = Event()
                heartbeat_thread: Thread | None = None
                if row is not None:
                    claim_attempt = int(row["attempt"])
                    claim_fence = int(row["fence"])

                    def heartbeat_claim() -> None:
                        while not heartbeat_stop.wait(
                            _PROCESSING_BATCH_CLAIM_HEARTBEAT_SECONDS
                        ):
                            try:
                                if not self.renew_batch_claim(
                                    job_id=job_id,
                                    batch_id=batch_id,
                                    attempt=claim_attempt,
                                    claim_fence=claim_fence,
                                    claim_token=cast(str, claim_token),
                                    unit_kind=unit_kind,
                                ):
                                    return
                            except Exception:
                                return

                    heartbeat_thread = Thread(
                        target=heartbeat_claim,
                        name=f"atlas-{unit_kind}-heartbeat:{batch_id}",
                        daemon=True,
                    )
                    heartbeat_thread.start()
                try:
                    yield (
                        _job_execution_record(
                            row,
                            batch_claim_token=claim_token,
                        )
                        if row is not None
                        else None
                    )
                finally:
                    heartbeat_stop.set()
                    if heartbeat_thread is not None:
                        heartbeat_thread.join(timeout=1)
            finally:
                if acquired:
                    if claim_token is not None:
                        with connection.begin():
                            connection.execute(
                                delete(async_rows.AtlasProcessingBatchClaimRow).where(
                                    async_rows.AtlasProcessingBatchClaimRow.batch_id
                                    == batch_id,
                                    async_rows.AtlasProcessingBatchClaimRow.claim_token
                                    == claim_token,
                                )
                            )
                    connection.execute(
                        text(
                            "SELECT pg_advisory_unlock("
                            "hashtextextended(:identity, 0))"
                        ),
                        {"identity": identity},
                    )
                    connection.commit()

    def finalize_document_page_preparation(
        self,
        connection: Connection,
        *,
        job_id: str,
        expected_attempt: int,
        claim_fence: int,
        claim_token: str,
        page_record: dict[str, Any],
    ) -> str:
        page = EvidencePageArtifact(**page_record)
        page_number = page.source_page_index + 1
        batch_id = f"{job_id}:page:{page_number}"
        prepare_batch_id = f"{job_id}:prepare"
        current = connection.execute(
            select(
                async_rows.AtlasProcessingJobRow.attempt,
                async_rows.AtlasProcessingJobRow.fence,
                async_rows.AtlasProcessingJobRow.status,
                async_rows.AtlasProcessingJobRow.document_version_id,
                async_rows.AtlasProcessingJobRow.processing_generation,
                async_rows.AtlasProcessingJobRow.processing_revision_id,
            ).where(async_rows.AtlasProcessingJobRow.job_id == job_id)
        ).mappings().one_or_none()
        claim = connection.execute(
            select(async_rows.AtlasProcessingBatchClaimRow.__table__).where(
                async_rows.AtlasProcessingBatchClaimRow.batch_id == prepare_batch_id,
                async_rows.AtlasProcessingBatchClaimRow.job_id == job_id,
            )
        ).mappings().one_or_none()
        now = _utc_now()
        if (
            current is None
            or int(current["attempt"]) != expected_attempt
            or int(current["fence"]) != claim_fence
            or current["status"] != "running"
            or current["document_version_id"] != page.document_version_id
            or int(current["processing_generation"] or 0)
            != page.processing_generation
            or not isinstance(current["processing_revision_id"], str)
            or not current["processing_revision_id"]
            or claim is None
            or int(claim["attempt"]) != expected_attempt
            or claim["claim_token"] != claim_token
            or claim["unit_kind"] != "batch"
            or claim["lease_expires_at"] <= now
        ):
            raise ValueError("document_page_preparation_fence_rejected")
        payload = processing_rows.evidence_page_artifact_payload(page)
        connection.execute(
            pg_insert(processing_rows.AtlasEvidencePageArtifactRow)
            .values(
                id=page.artifact_id,
                tenant_id=page.tenant_id,
                document_version_id=page.document_version_id,
                source_page_index=page.source_page_index,
                renderer_version=page.renderer_version,
                processing_generation=page.processing_generation,
                processing_revision_id=current["processing_revision_id"],
                payload=payload,
            )
            .on_conflict_do_nothing()
        )
        existing = connection.execute(
            select(
                processing_rows.AtlasEvidencePageArtifactRow.payload,
                processing_rows.AtlasEvidencePageArtifactRow.processing_revision_id,
            ).where(
                processing_rows.AtlasEvidencePageArtifactRow.id == page.artifact_id
            )
        ).mappings().one_or_none()
        if (
            existing is None
            or existing["payload"] != payload
            or existing["processing_revision_id"]
            != current["processing_revision_id"]
        ):
            raise ValueError("document_page_preparation_identity_conflict")
        outbox = _new_task_outbox_record(
            task_name="atlas.processing.process_batch",
            queue_name="atlas.processing",
            payload={
                "job_id": job_id,
                "batch_id": batch_id,
                "attempt": expected_attempt,
                "schema_version": 1,
            },
            available_at=now,
        )
        session = Session(bind=connection, join_transaction_mode="rollback_only")
        try:
            _publish_outbox_cas(
                session,
                TaskOutboxTransition(outbox, CurrentRowExpectation.absent()),
            )
            session.flush()
        finally:
            session.close()
        return outbox.outbox_id

    def prepared_page_artifact(
        self,
        job_id: str,
        batch_id: str,
    ) -> dict[str, Any]:
        try:
            page_number = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_batch_id") from exc
        if batch_id != f"{job_id}:page:{page_number}" or page_number <= 0:
            raise ValueError("invalid_batch_id")
        page_id = f"epa-{hashlib.sha256(batch_id.encode()).hexdigest()[:32]}"
        with self.session_factory() as session:
            row = session.execute(
                select(
                    async_rows.AtlasProcessingJobRow.document_version_id,
                    async_rows.AtlasProcessingJobRow.processing_generation,
                    async_rows.AtlasProcessingJobRow.processing_revision_id,
                    processing_rows.AtlasEvidencePageArtifactRow.processing_revision_id,
                    processing_rows.AtlasEvidencePageArtifactRow.payload,
                )
                .join(
                    processing_rows.AtlasEvidencePageArtifactRow,
                    processing_rows.AtlasEvidencePageArtifactRow.id == page_id,
                )
                .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
            ).one_or_none()
        if row is None:
            raise ValueError("document_page_source_unavailable")
        (
            document_version_id,
            processing_generation,
            job_revision_id,
            page_revision_id,
            raw_payload,
        ) = row
        payload = dict(raw_payload)
        if (
            payload.get("artifact_kind") != "pdf_single_page"
            or payload.get("document_version_id") != document_version_id
            or payload.get("processing_generation") != processing_generation
            or not isinstance(job_revision_id, str)
            or not job_revision_id
            or page_revision_id != job_revision_id
            or payload.get("source_page_index") != page_number - 1
            or not isinstance(payload.get("storage_artifact_id"), str)
        ):
            raise ValueError("document_page_source_invalid")
        return payload

    def get_processing_profile_pin(
        self,
        *,
        document_id: str,
        processing_generation: int,
    ) -> ProcessingProfilePin:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasProcessingGenerationRow).where(
                    async_rows.AtlasProcessingGenerationRow.document_id == document_id,
                    async_rows.AtlasProcessingGenerationRow.processing_generation
                    == processing_generation,
                )
            )
        if row is None or row.profile_id == "pending":
            raise ValueError("processing_profile_not_pinned")
        return ProcessingProfilePin(
            profile_id=row.profile_id,
            profile_revision=int(row.profile_revision),
        )

    def chunks_for_batch(
        self,
        job_id: str,
        batch_id: str,
    ) -> tuple[ProcessingJobView, list[dict[str, Any]]]:
        with self.session_factory() as session:
            job = session.get(async_rows.AtlasProcessingJobRow, job_id)
            if job is None:
                raise ValueError("processing_job_not_found")
            rows = session.scalars(
                select(async_rows.AtlasSearchChunkRow)
                .where(
                    async_rows.AtlasSearchChunkRow.index_generation_id
                    == job.index_generation_id,
                    async_rows.AtlasSearchChunkRow.batch_id == batch_id,
                )
                .order_by(async_rows.AtlasSearchChunkRow.chunk_id)
            ).all()
            return _job_execution_record(job), [_row_payload(row) for row in rows]

    def set_embedding_profile(
        self,
        job_id: str,
        index_generation_id: str,
        profile: dict[str, Any],
        *,
        expected_attempt: int,
    ) -> bool:
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if (
                    job is None
                    or job.attempt != expected_attempt
                    or job.status in {"succeeded", "failed", "cancelled"}
                    or job.index_generation_id != index_generation_id
                ):
                    return False
                generation = session.get(
                    async_rows.AtlasIndexGenerationRow,
                    index_generation_id,
                )
                if generation is None or generation.status != "building":
                    return False
                if generation.embedding_profile != profile:
                    generation.embedding_profile = deepcopy(profile)
                    generation.expected_point_count = None
                    generation.expected_fts_count = None
                    generation.manifest_digest = None
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def stage_reindex_batch(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
        batch_size: int = 100,
    ) -> bool:
        if batch_size <= 0 or batch_size > 2_000:
            raise ValueError("reindex batch size must be between 1 and 2000")
        try:
            ordinal = int(batch_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid_reindex_batch_id") from exc
        if batch_id != f"{job_id}:reindex:{ordinal}" or ordinal < 0:
            raise ValueError("invalid_reindex_batch_id")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if job is None:
                    raise ValueError("processing_job_not_found")
                if job.attempt != expected_attempt or job.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return False
                document = session.get(document_rows.AtlasDocumentRow, job.document_id)
                generation = session.get(
                    async_rows.AtlasIndexGenerationRow,
                    job.index_generation_id,
                )
                if document is None or generation is None:
                    raise ValueError("reindex_source_generation_changed")
                source_index = generation.supersedes_index_generation_id
                if (
                    not source_index
                    or document.active_index_generation_id != source_index
                    or document.active_processing_generation
                    != generation.source_processing_generation
                ):
                    raise ValueError("reindex_source_generation_changed")
                existing = session.scalar(
                    select(func.count())
                    .select_from(async_rows.AtlasSearchChunkRow)
                    .where(
                        async_rows.AtlasSearchChunkRow.index_generation_id
                        == job.index_generation_id,
                        async_rows.AtlasSearchChunkRow.batch_id == batch_id,
                    )
                )
                if existing:
                    return True
                old_rows = session.scalars(
                    select(async_rows.AtlasSearchChunkRow)
                    .where(
                        async_rows.AtlasSearchChunkRow.index_generation_id
                        == source_index,
                        async_rows.AtlasSearchChunkRow.status == "active",
                    )
                    .order_by(async_rows.AtlasSearchChunkRow.chunk_id)
                    .offset(ordinal * batch_size)
                    .limit(batch_size)
                ).all()
                for old in old_rows:
                    values = _row_payload(old)
                    values.update(
                        chunk_id=(
                            "chunk-"
                            + _request_digest(
                                {
                                    "generation": job.index_generation_id,
                                    "old": old.chunk_id,
                                }
                            )[:32]
                        ),
                        batch_id=batch_id,
                        index_generation_id=job.index_generation_id,
                        status="staged",
                        created_at=now,
                    )
                    session.execute(
                        pg_insert(async_rows.AtlasSearchChunkRow)
                        .values(**values)
                        .on_conflict_do_nothing()
                    )
                generation.actual_fts_count += len(old_rows)
                job.progress_current = min(
                    job.progress_current + 1,
                    cast(int, job.progress_total),
                )
                job.updated_at = now
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def cleanup_staging(self, *, limit: int = 100) -> None:
        if limit < 1 or limit > 200:
            raise ValueError("staging cleanup limit must be between 1 and 200")
        session = self.session_factory()
        with session:
            try:
                checkpoints = session.scalars(
                    select(async_rows.AtlasProcessingCheckpointRow)
                    .join(
                        async_rows.AtlasProcessingJobRow,
                        async_rows.AtlasProcessingJobRow.job_id
                        == async_rows.AtlasProcessingCheckpointRow.job_id,
                    )
                    .join(
                        async_rows.AtlasIndexGenerationRow,
                        async_rows.AtlasIndexGenerationRow.index_generation_id
                        == async_rows.AtlasProcessingJobRow.index_generation_id,
                    )
                    .join(
                        document_rows.AtlasDocumentRow,
                        document_rows.AtlasDocumentRow.document_id
                        == async_rows.AtlasProcessingJobRow.document_id,
                    )
                    .where(
                        async_rows.AtlasProcessingJobRow.status == "succeeded",
                        async_rows.AtlasIndexGenerationRow.status == "retired",
                        document_rows.AtlasDocumentRow.active_index_generation_id.is_distinct_from(
                            async_rows.AtlasIndexGenerationRow.index_generation_id
                        ),
                    )
                    .order_by(async_rows.AtlasProcessingCheckpointRow.committed_at)
                    .limit(limit)
                    .with_for_update(
                        of=async_rows.AtlasProcessingCheckpointRow,
                        skip_locked=True,
                    )
                ).all()
                _delete_reconciliation_rows(
                    session,
                    tuple(checkpoints),
                    allowed_types=(async_rows.AtlasProcessingCheckpointRow,),
                )
                if checkpoints:
                    AuditEventWriter(session).append(
                        _internal_event(
                            operation="processing_staging.cleaned",
                            job_id=None,
                            document_id=None,
                            status="succeeded",
                        )
                    )
                    session.commit()
            except Exception:
                session.rollback()
                raise

    def get_batch_claim(
        self, batch_id: str
    ) -> ProcessingBatchClaimRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasProcessingBatchClaimRow).where(
                    async_rows.AtlasProcessingBatchClaimRow.batch_id == batch_id
                )
            )
            return _batch_claim_record(row) if row is not None else None

    def list_batch_claims(
        self,
        *,
        job_id: str,
        limit: int = 100,
    ) -> list[ProcessingBatchClaimRecord]:
        if limit < 1 or limit > 200:
            raise ValueError("batch claim limit must be between 1 and 200")
        statement = (
            select(async_rows.AtlasProcessingBatchClaimRow)
            .where(async_rows.AtlasProcessingBatchClaimRow.job_id == job_id)
            .order_by(async_rows.AtlasProcessingBatchClaimRow.batch_id)
            .limit(limit)
        )
        with self.session_factory() as session:
            return [
                _batch_claim_record(row) for row in session.scalars(statement).all()
            ]

    def get_checkpoint(
        self,
        *,
        job_id: str,
        unit_kind: str,
        unit_start: int,
        unit_end: int,
    ) -> ProcessingCheckpointRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasProcessingCheckpointRow).where(
                    async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                    async_rows.AtlasProcessingCheckpointRow.unit_kind == unit_kind,
                    async_rows.AtlasProcessingCheckpointRow.unit_start == unit_start,
                    async_rows.AtlasProcessingCheckpointRow.unit_end == unit_end,
                )
            )
            return _checkpoint_record(row) if row is not None else None

    def list_checkpoints(
        self,
        *,
        job_id: str,
        limit: int = 200,
    ) -> list[ProcessingCheckpointRecord]:
        if limit < 1 or limit > 2000:
            raise ValueError("checkpoint limit must be between 1 and 2000")
        statement = (
            select(async_rows.AtlasProcessingCheckpointRow)
            .where(async_rows.AtlasProcessingCheckpointRow.job_id == job_id)
            .order_by(
                async_rows.AtlasProcessingCheckpointRow.unit_kind,
                async_rows.AtlasProcessingCheckpointRow.unit_start,
                async_rows.AtlasProcessingCheckpointRow.unit_end,
            )
            .limit(limit)
        )
        with self.session_factory() as session:
            return [
                _checkpoint_record(row) for row in session.scalars(statement).all()
            ]

    def claim_processing_batch(
        self,
        *,
        job_id: str,
        batch_id: str,
        expected_attempt: int,
        expected_fence: int,
        unit_kind: str,
        unit_start: int,
        unit_end: int,
        lease_seconds: int = 300,
    ) -> ProcessingBatchClaimRecord | None:
        if unit_kind not in {"page", "batch"}:
            raise ValueError("processing_claim_kind_invalid")
        if unit_start < 1 or unit_end < unit_start:
            raise ValueError("processing claim range is invalid")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("batch lease must be between 1 and 3600 seconds")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                if (
                    job is None
                    or job.attempt != expected_attempt
                    or job.fence != expected_fence
                    or job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return None
                current = session.scalar(
                    select(async_rows.AtlasProcessingBatchClaimRow)
                    .where(
                        async_rows.AtlasProcessingBatchClaimRow.batch_id == batch_id
                    )
                    .with_for_update()
                )
                if current is not None and current.lease_expires_at > now:
                    return None
                claim = ProcessingBatchClaimRecord(
                    batch_id=batch_id,
                    job_id=job_id,
                    attempt=expected_attempt,
                    claim_token=f"claim-{uuid4().hex}",
                    unit_kind=unit_kind,
                    unit_start=unit_start,
                    unit_end=unit_end,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    created_at=(current.created_at if current is not None else now),
                    updated_at=now,
                )
                _publish_batch_lease_cas(
                    session,
                    ProcessingBatchClaimTransition(
                        claim,
                        (
                            CurrentRowExpectation.absent()
                            if current is None
                            else CurrentRowExpectation(
                                exists=True,
                                status=None,
                                attempt=current.attempt,
                                fence=None,
                                claim_owner=current.claim_token,
                                preimage=_batch_claim_record(current),
                            )
                        ),
                    ),
                    current=current,
                    claim_takeover_at=(now if current is not None else None),
                )
                desired_job = replace(
                    _job_record(job),
                    status="running",
                    updated_at=now,
                )
                _publish_job_lease_reconciliation_cas(
                    session,
                    ProcessingJobTransition(
                        desired_job,
                        CurrentRowExpectation(
                            exists=True,
                            status=job.status,
                            attempt=job.attempt,
                            fence=job.fence,
                            claim_owner=job.lease_owner,
                            preimage=_job_record(job),
                        ),
                    ),
                    current=job,
                )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="processing_batch.claimed",
                        job_id=job_id,
                        document_id=job.document_id,
                        attempt=expected_attempt,
                        status="running",
                    )
                )
                session.commit()
                return claim
            except Exception:
                session.rollback()
                raise

    def renew_batch_claim(
        self,
        *,
        job_id: str,
        batch_id: str,
        attempt: int,
        claim_fence: int,
        claim_token: str,
        unit_kind: str = "page",
        lease_seconds: int = 300,
    ) -> bool:
        if unit_kind not in {"page", "batch"}:
            raise ValueError("processing_claim_kind_invalid")
        now = _utc_now()
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                    .with_for_update()
                )
                claim = session.scalar(
                    select(async_rows.AtlasProcessingBatchClaimRow)
                    .where(
                        async_rows.AtlasProcessingBatchClaimRow.batch_id == batch_id
                    )
                    .with_for_update()
                )
                if (
                    job is None
                    or job.attempt != attempt
                    or job.fence != claim_fence
                    or job.status in {"succeeded", "failed", "cancelled"}
                    or claim is None
                    or claim.job_id != job_id
                    or claim.attempt != attempt
                    or claim.claim_token != claim_token
                    or claim.unit_kind != unit_kind
                    or claim.lease_expires_at <= now
                ):
                    return False
                desired_claim = replace(
                    _batch_claim_record(claim),
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                _publish_batch_lease_cas(
                    session,
                    ProcessingBatchClaimTransition(
                        desired_claim,
                        CurrentRowExpectation(
                            exists=True,
                            status=None,
                            attempt=claim.attempt,
                            fence=None,
                            claim_owner=claim.claim_token,
                            preimage=_batch_claim_record(claim),
                        ),
                    ),
                    current=claim,
                )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="processing_batch.renewed",
                        job_id=job_id,
                        document_id=job.document_id,
                        attempt=attempt,
                        status=job.status,
                    )
                )
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def checkpoint_for_batch(
        self, job_id: str, batch_id: str
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(async_rows.AtlasProcessingCheckpointRow).where(
                    async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                    async_rows.AtlasProcessingCheckpointRow.batch_id == batch_id,
                )
            )
            return asdict(_checkpoint_record(row)) if row is not None else None

    def commit_checkpoint(
        self,
        *,
        job_id: str,
        attempt: int,
        claim_fence: int,
        claim_token: str,
        batch_id: str,
        unit_start: int,
        unit_end: int,
        input_fingerprint: str,
        output_digest: str,
        evidence_rows: list[dict[str, Any]],
        chunk_rows: list[dict[str, Any]],
        page_artifact_rows: list[dict[str, Any]] | None = None,
        preview_count: int = 0,
        warning_codes: list[str] | None = None,
    ) -> bool:
        now = _utc_now()
        evidence = tuple(
            EvidenceRecord(**{**values, "status": values.get("status", "staged")})
            for values in evidence_rows
        )
        chunks = tuple(
            SearchChunkProjection(
                **{
                    **values,
                    "search_vector": (
                        values["search_vector"]
                        if values.get("search_vector") is not None
                        else func.to_tsvector("simple", values["normalized_text"])
                    ),
                }
            )
            for values in chunk_rows
        )
        pages = tuple(
            EvidencePageArtifact(**values) for values in (page_artifact_rows or [])
        )
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if job is None or job.attempt != attempt or job.fence != claim_fence:
                    return False
                existing = session.scalar(
                    select(async_rows.AtlasProcessingCheckpointRow)
                    .where(
                        async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                        async_rows.AtlasProcessingCheckpointRow.unit_kind == "page",
                        async_rows.AtlasProcessingCheckpointRow.unit_start == unit_start,
                        async_rows.AtlasProcessingCheckpointRow.unit_end == unit_end,
                    )
                )
                checkpoint = ProcessingCheckpointRecord(
                    job_id=job_id,
                    unit_kind="page",
                    unit_start=unit_start,
                    unit_end=unit_end,
                    batch_id=batch_id,
                    claim_token=claim_token,
                    fence=claim_fence,
                    input_fingerprint=input_fingerprint,
                    output_digest=output_digest,
                    evidence_count=len(evidence),
                    chunk_count=len(chunks),
                    preview_count=preview_count,
                    committed_at=(existing.committed_at if existing is not None else now),
                )
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                )
                if document is None:
                    return False
                replay = existing is not None
                claim = None
                generation = None
                index_generation = None
                if not replay:
                    if job.status in {"succeeded", "failed", "cancelled"}:
                        return False
                    claim = session.scalar(
                        select(async_rows.AtlasProcessingBatchClaimRow).where(
                            async_rows.AtlasProcessingBatchClaimRow.batch_id == batch_id
                        )
                    )
                    if (
                        claim is None
                        or claim.job_id != job_id
                        or claim.attempt != attempt
                        or claim.claim_token != claim_token
                        or claim.unit_kind != "page"
                        or claim.unit_start != unit_start
                        or claim.unit_end != unit_end
                        or claim.lease_expires_at <= now
                    ):
                        return False
                if not replay and job.processing_generation is not None:
                    generation = session.scalar(
                        select(async_rows.AtlasProcessingGenerationRow)
                        .where(
                            async_rows.AtlasProcessingGenerationRow.document_id
                            == job.document_id,
                            async_rows.AtlasProcessingGenerationRow.processing_generation
                            == job.processing_generation,
                        )
                    )
                    if generation is None or generation.status != "building":
                        return False
                if not replay:
                    index_generation = session.scalar(
                        select(async_rows.AtlasIndexGenerationRow).where(
                            async_rows.AtlasIndexGenerationRow.index_generation_id
                            == job.index_generation_id
                        )
                    )
                    if (
                        index_generation is None
                        or index_generation.status != "building"
                    ):
                        return False

                change_set = _BatchMutation(
                    document_id=job.document_id,
                    document_version_id=job.document_version_id,
                    processing_generation=job.processing_generation,
                    job_id=job_id,
                    expected_document_lifecycle_epoch=(
                        document.resource_lifecycle_epoch
                    ),
                    document=(
                        None
                        if replay
                        else replace(
                            _document_record(document),
                            intake_status="processing",
                            current_stage="indexing",
                            warning_codes=list(
                                dict.fromkeys(
                                    [
                                        *list(document.warning_codes or []),
                                        *(warning_codes or []),
                                    ]
                                )
                            ),
                            failure_code=None,
                            processing_job_id=job_id,
                        )
                    ),
                    jobs=(
                        ()
                        if replay
                        else (
                            ProcessingJobTransition(
                                replace(
                                    _job_record(job),
                                    progress_current=job.progress_current + 1,
                                    stage="indexing",
                                    status="running",
                                    failure_code=None,
                                    failure_detail=None,
                                    updated_at=now,
                                ),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=job.status,
                                    attempt=job.attempt,
                                    fence=job.fence,
                                    claim_owner=job.lease_owner,
                                    preimage=_job_record(job),
                                ),
                            ),
                        )
                    ),
                    batch_claims=(
                        ()
                        if replay
                        else (
                            ProcessingBatchClaimTransition(
                                _batch_claim_record(cast(
                                    async_rows.AtlasProcessingBatchClaimRow,
                                    claim,
                                )),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=None,
                                    attempt=attempt,
                                    fence=None,
                                    claim_owner=claim_token,
                                    preimage=_batch_claim_record(cast(
                                        async_rows.AtlasProcessingBatchClaimRow,
                                        claim,
                                    )),
                                ),
                            ),
                        )
                    ),
                    checkpoints=(
                        ProcessingCheckpointTransition(
                            checkpoint,
                            CurrentRowExpectation.absent(),
                        ),
                    ),
                    evidence=evidence,
                    page_artifacts=pages,
                    generations=(
                        ()
                        if replay or generation is None
                        else (
                            ProcessingGenerationTransition(
                                replace(
                                    _processing_generation_record(generation),
                                    actual_page_count=generation.actual_page_count + 1,
                                    actual_evidence_count=(
                                        generation.actual_evidence_count
                                        + len(evidence)
                                    ),
                                    actual_chunk_count=(
                                        generation.actual_chunk_count + len(chunks)
                                    ),
                                ),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=generation.status,
                                    attempt=None,
                                    fence=None,
                                    claim_owner=None,
                                    preimage=_processing_generation_record(generation),
                                ),
                            ),
                        )
                    ),
                    index_generations=(
                        ()
                        if replay
                        else (
                            IndexGenerationTransition(
                                replace(
                                    _index_generation_record(cast(
                                        async_rows.AtlasIndexGenerationRow,
                                        index_generation,
                                    )),
                                    actual_fts_count=(
                                        cast(
                                            async_rows.AtlasIndexGenerationRow,
                                            index_generation,
                                        ).actual_fts_count
                                        + len(chunks)
                                    ),
                                ),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=cast(
                                        async_rows.AtlasIndexGenerationRow,
                                        index_generation,
                                    ).status,
                                    attempt=None,
                                    fence=None,
                                    claim_owner=None,
                                    preimage=_index_generation_record(cast(
                                        async_rows.AtlasIndexGenerationRow,
                                        index_generation,
                                    )),
                                ),
                            ),
                        )
                    ),
                    search_chunks=tuple(
                        SearchChunkTransition(
                            record,
                            CurrentRowExpectation.absent(),
                        )
                        for record in chunks
                    ),
                    audit_events=(
                        _internal_event(
                            operation="processing_checkpoint.committed",
                            job_id=job_id,
                            document_id=job.document_id,
                            attempt=attempt,
                            status="running",
                        ),
                    ),
                )
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    require_exact_replay=replay,
                )
                if not replay:
                    _delete_reconciliation_rows(
                        session,
                        (
                            cast(
                                async_rows.AtlasProcessingBatchClaimRow,
                                claim,
                            ),
                        ),
                        allowed_types=(
                            async_rows.AtlasProcessingBatchClaimRow,
                        ),
                    )
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def enqueue_index_batch(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
    ) -> bool:
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                checkpoint = session.scalar(
                    select(async_rows.AtlasProcessingCheckpointRow).where(
                        async_rows.AtlasProcessingCheckpointRow.job_id == job_id,
                        async_rows.AtlasProcessingCheckpointRow.batch_id == batch_id,
                    )
                )
                if (
                    job is None
                    or job.attempt != expected_attempt
                    or job.status in {"succeeded", "failed", "cancelled"}
                    or checkpoint is None
                ):
                    return False
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow).where(
                        document_rows.AtlasDocumentRow.document_id
                        == job.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                outbox = _new_task_outbox_record(
                    task_name="atlas.indexing.index_batch",
                    queue_name="atlas.indexing",
                    payload={
                        "job_id": job_id,
                        "batch_id": batch_id,
                        "attempt": expected_attempt,
                        "schema_version": 1,
                    },
                    available_at=checkpoint.committed_at,
                )
                change_set = _BatchMutation(
                        document_id=job.document_id,
                        document_version_id=job.document_version_id,
                        processing_generation=job.processing_generation,
                        job_id=job_id,
                        expected_document_lifecycle_epoch=(
                            document.resource_lifecycle_epoch
                        ),
                        outbox=(
                            TaskOutboxTransition(
                                outbox,
                                CurrentRowExpectation.absent(),
                            ),
                        ),
                        checkpoints=(
                            ProcessingCheckpointTransition(
                                _checkpoint_record(checkpoint),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=None,
                                    attempt=None,
                                    fence=checkpoint.fence,
                                    claim_owner=checkpoint.claim_token,
                                    preimage=_checkpoint_record(checkpoint),
                                ),
                            ),
                        ),
                        audit_events=(
                            _internal_event(
                                operation="index_batch.queued",
                                job_id=job_id,
                                document_id=job.document_id,
                                attempt=expected_attempt,
                                status="pending",
                            ),
                        ),
                    )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    change_set,
                    _document_processing_candidates(change_set),
                )
                session.expire_all()
                locked_job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.job_id == job_id
                    )
                )
                if (
                    locked_job is None
                    or locked_job.attempt != expected_attempt
                    or locked_job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return False
                exact_replays = _apply_sealed_family_mutation(
                    session,
                    change_set,
                    lock_token=lock_token,
                )
                if _replay_key("outbox", outbox.outbox_id) in exact_replays:
                    return True
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def mark_batch_indexed(
        self,
        *,
        job_id: str,
        batch_id: str,
        mappings: list[dict[str, Any]],
        expected_attempt: int,
    ) -> bool:
        if len(mappings) > 2000:
            raise ValueError("vector mapping batch exceeds 2000 rows")
        now = _utc_now()
        mapped_records = tuple(
            VectorPointMappingRecord(
                index_generation_id=values["index_generation_id"],
                point_id=values["point_id"],
                chunk_id=values["chunk_id"],
                payload_digest=values["payload_digest"],
                vector_digest=values["vector_digest"],
                created_at=values.get("created_at", now),
            )
            for values in mappings
        )
        _unique_index(
            mapped_records,
            key=lambda record: (
                record.index_generation_id,
                record.point_id,
            ),
            family="vector point mapping",
        )
        session = self.session_factory()
        with session:
            try:
                job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow)
                    .where(async_rows.AtlasProcessingJobRow.job_id == job_id)
                )
                if (
                    job is None
                    or job.attempt != expected_attempt
                    or job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return False
                generation = session.scalar(
                    select(async_rows.AtlasIndexGenerationRow)
                    .where(
                        async_rows.AtlasIndexGenerationRow.index_generation_id
                        == job.index_generation_id
                    )
                )
                if generation is None or generation.status != "building":
                    return False
                chunks = session.scalars(
                    select(async_rows.AtlasSearchChunkRow)
                    .where(
                        async_rows.AtlasSearchChunkRow.batch_id == batch_id,
                        async_rows.AtlasSearchChunkRow.index_generation_id
                        == job.index_generation_id,
                    )
                    .order_by(async_rows.AtlasSearchChunkRow.chunk_id)
                    .limit(2001)
                ).all()
                if len(chunks) > 2000:
                    raise RuntimeError("search chunk batch exceeds bounded contract")
                chunk_ids = {row.chunk_id for row in chunks}
                if any(
                    record.index_generation_id != job.index_generation_id
                    or record.chunk_id not in chunk_ids
                    for record in mapped_records
                ):
                    raise ValueError("vector mapping has a foreign batch owner")
                mapping_preimages: list[
                    tuple[
                        VectorPointMappingRecord,
                        async_rows.AtlasVectorPointMappingRow | None,
                    ]
                ] = []
                for record in sorted(mapped_records, key=lambda item: item.point_id):
                    current = session.scalar(
                        select(async_rows.AtlasVectorPointMappingRow)
                        .where(
                            async_rows.AtlasVectorPointMappingRow.index_generation_id
                            == record.index_generation_id,
                            async_rows.AtlasVectorPointMappingRow.point_id
                            == record.point_id,
                        )
                    )
                    if current is not None and any(
                        getattr(current, field) != getattr(record, field)
                        for field in (
                            "index_generation_id",
                            "point_id",
                            "chunk_id",
                            "payload_digest",
                            "vector_digest",
                        )
                    ):
                        raise ValueError(
                            "vector point mapping is immutable"
                        )
                    mapping_preimages.append((record, current))
                inserted = sum(
                    current is None for _record, current in mapping_preimages
                )
                projected_point_count = generation.actual_point_count + inserted
                generation_chunk_ids = set(
                    session.scalars(
                        select(async_rows.AtlasSearchChunkRow.chunk_id)
                        .where(
                            async_rows.AtlasSearchChunkRow.index_generation_id
                            == job.index_generation_id
                        )
                        .limit(5001)
                    ).all()
                )
                generation_mapped_chunk_ids = tuple(
                    session.scalars(
                        select(async_rows.AtlasVectorPointMappingRow.chunk_id)
                        .where(
                            async_rows.AtlasVectorPointMappingRow.index_generation_id
                            == job.index_generation_id
                        )
                        .limit(5001)
                    ).all()
                )
                if (
                    len(generation_chunk_ids) > 5000
                    or len(generation_mapped_chunk_ids) > 5000
                ):
                    raise RuntimeError(
                        "index generation exceeds bounded publication contract"
                    )
                projected_mapped_chunk_ids = {
                    *generation_mapped_chunk_ids,
                    *(record.chunk_id for record, current in mapping_preimages if current is None),
                }
                publication_ready = (
                    job.progress_total is not None
                    and job.progress_current == job.progress_total
                    and len(generation_chunk_ids) == generation.actual_fts_count
                    and len(projected_mapped_chunk_ids) == len(generation_chunk_ids)
                    and len(generation_mapped_chunk_ids) + inserted
                    == len(projected_mapped_chunk_ids)
                    and projected_point_count == generation.actual_fts_count
                )
                outbox_transitions: tuple[TaskOutboxTransition, ...] = ()
                if publication_ready:
                    outbox = _new_task_outbox_record(
                        task_name="atlas.processing.finalize_generation",
                        queue_name="atlas.processing",
                        payload={
                            "job_id": job_id,
                            "attempt": expected_attempt,
                            "schema_version": 1,
                        },
                        available_at=job.attempt_started_at,
                    )
                    outbox_transitions = (
                        TaskOutboxTransition(
                            outbox,
                            CurrentRowExpectation.absent(),
                        ),
                    )
                document = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id == job.document_id
                    )
                )
                if document is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "named document parent is missing"
                    )
                desired_stage = "publishing" if publication_ready else "indexing"
                desired_index = replace(
                    _index_generation_record(generation),
                    actual_point_count=projected_point_count,
                )
                desired_job = (
                    replace(_job_record(job), stage="publishing", updated_at=now)
                    if publication_ready
                    else None
                )
                desired_document = (
                    replace(
                        _document_record(document),
                        intake_status="processing",
                        current_stage="publishing",
                        processing_job_id=job_id,
                    )
                    if publication_ready
                    else None
                )
                change_set = _BatchMutation(
                        job_id=job_id,
                        document_id=job.document_id,
                        document_version_id=job.document_version_id,
                        processing_generation=(
                            generation.source_processing_generation
                        ),
                        expected_document_lifecycle_epoch=(
                            document.resource_lifecycle_epoch
                        ),
                        document=desired_document,
                        jobs=(
                            (
                                ProcessingJobTransition(
                                    cast(ProcessingJobRecord, desired_job),
                                    CurrentRowExpectation(
                                        exists=True,
                                        status=job.status,
                                        attempt=job.attempt,
                                        fence=job.fence,
                                        claim_owner=job.lease_owner,
                                        preimage=_job_record(job),
                                    ),
                                ),
                            )
                            if publication_ready
                            else ()
                        ),
                        outbox=outbox_transitions,
                        index_generations=(
                            IndexGenerationTransition(
                                desired_index,
                                CurrentRowExpectation(
                                    exists=True,
                                    status=generation.status,
                                    attempt=None,
                                    fence=None,
                                    claim_owner=None,
                                    preimage=_index_generation_record(generation),
                                ),
                            ),
                        ),
                        search_chunks=tuple(
                            SearchChunkTransition(
                                _search_chunk_record(chunk),
                                CurrentRowExpectation(
                                    exists=True,
                                    status=chunk.status,
                                    attempt=None,
                                    fence=None,
                                    claim_owner=None,
                                    preimage=_search_chunk_record(chunk),
                                ),
                            )
                            for chunk in chunks
                        ),
                        vector_mappings=tuple(
                            VectorPointMappingTransition(
                                record,
                                (
                                    CurrentRowExpectation.absent()
                                    if current is None
                                    else CurrentRowExpectation(
                                        exists=True,
                                        status=None,
                                        attempt=None,
                                        fence=None,
                                        claim_owner=None,
                                        preimage=_vector_mapping_record(current),
                                    )
                                ),
                            )
                            for record, current in mapping_preimages
                        ),
                        audit_events=(
                            _internal_event(
                                operation="index_batch.committed",
                                job_id=job_id,
                                document_id=job.document_id,
                                attempt=expected_attempt,
                                status=desired_stage,
                            ),
                        ),
                    )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    change_set,
                    _document_processing_candidates(change_set),
                )
                session.expire_all()
                locked_job = session.scalar(
                    select(async_rows.AtlasProcessingJobRow).where(
                        async_rows.AtlasProcessingJobRow.job_id == job_id
                    )
                )
                if (
                    locked_job is None
                    or locked_job.attempt != expected_attempt
                    or locked_job.status in {"succeeded", "failed", "cancelled"}
                ):
                    return False
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    lock_token=lock_token,
                )
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

@dataclass(frozen=True, slots=True)
class _FinalGenerationPublicationSql:
    session_factory: SessionFactory
    artifact_publication_reader_factory: ArtifactPublicationReaderFactory = (
        lambda session: _GenerationArtifactPublicationReader(session)
    )

    def load_publication_manifest(
        self,
        job_id: str,
        *,
        expected_attempt: int,
    ) -> IndexPublicationManifest | None:
        session = self.session_factory()
        with session:
            try:
                snapshot = _load_generation_publication_snapshot(
                    session,
                    job_id,
                    expected_attempt=expected_attempt,
                )
                if snapshot is None:
                    return None
                if snapshot.job.status == "succeeded":
                    if (
                        snapshot.index.status != "active"
                        or not snapshot.index.manifest_digest
                        or snapshot.index.processing_revision_id is None
                    ):
                        return None
                    return IndexPublicationManifest(
                        index_generation_id=snapshot.index.index_generation_id,
                        processing_revision_id=(
                            snapshot.index.processing_revision_id
                        ),
                        qdrant_collection=None,
                        points=(),
                        manifest_digest=snapshot.index.manifest_digest,
                    )
                if (
                    snapshot.job.status != "running"
                    or snapshot.index.status != "building"
                    or snapshot.job.progress_total is None
                    or snapshot.job.progress_current
                    != snapshot.job.progress_total
                ):
                    return None
                points = _validate_generation_publication_snapshot(
                    snapshot,
                    expected_attempt=expected_attempt,
                    require_recorded_manifest=False,
                )
                artifact_expectation = _publication_artifact_expectation(
                    snapshot,
                    require_current_derived_parent_epoch=(
                        snapshot.job.job_kind != "reindex"
                    ),
                )
                artifact_reader = _validated_artifact_publication_reader(
                    self.artifact_publication_reader_factory,
                    session,
                )
                artifact_inventory = (
                    artifact_reader.discover_identity_inventory(
                        artifact_expectation
                    )
                )
                provisional = _generation_publication_change_set(
                    snapshot,
                    manifest_digest=(
                        snapshot.index.manifest_digest or ("0" * 64)
                    ),
                    finalize=False,
                    now=_utc_now(),
                    artifact_inventory=artifact_inventory,
                )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    provisional,
                    _document_processing_candidates(provisional),
                )
                artifact_graph = artifact_reader.read_locked_current(
                    inventory=artifact_inventory,
                    expectation=artifact_expectation,
                )
                locked_snapshot = _load_generation_publication_snapshot(
                    session,
                    job_id,
                    expected_attempt=expected_attempt,
                )
                if locked_snapshot is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "publication inventory changed after coordination"
                    )
                points = _validate_generation_publication_snapshot(
                    locked_snapshot,
                    expected_attempt=expected_attempt,
                    require_recorded_manifest=False,
                )
                if (
                    _publication_inventory_identities(locked_snapshot)
                    != _publication_inventory_identities(snapshot)
                    or _publication_artifact_expectation(
                        locked_snapshot,
                        require_current_derived_parent_epoch=(
                            locked_snapshot.job.job_kind != "reindex"
                        ),
                    )
                    != artifact_expectation
                ):
                    raise DocumentProcessingCurrentnessConflict(
                        "publication inventory changed after coordination"
                    )
                if locked_snapshot.index.processing_revision_id is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "publication revision authority is unavailable"
                    )
                manifest_digest = _publication_manifest_digest(
                    locked_snapshot,
                    artifact_graph,
                    points,
                )
                change_set = _generation_publication_change_set(
                    locked_snapshot,
                    manifest_digest=manifest_digest,
                    finalize=False,
                    now=_utc_now(),
                    artifact_inventory=artifact_inventory,
                )
                authority = _LockedFinalGenerationPublication(
                    _session=session,
                    mutation=lock_token,
                    change_set=change_set,
                    artifact_inventory=artifact_inventory,
                    artifact_graph=artifact_graph,
                )
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    lock_token=lock_token,
                    final_publication_authority=authority,
                )
                session.commit()
                return IndexPublicationManifest(
                    index_generation_id=locked_snapshot.index.index_generation_id,
                    processing_revision_id=(
                        locked_snapshot.index.processing_revision_id
                    ),
                    qdrant_collection=locked_snapshot.index.qdrant_collection,
                    points=points,
                    manifest_digest=manifest_digest,
                )
            except Exception:
                session.rollback()
                raise

    def _publish_final_generation(
        self,
        session: Session,
        job_id: str,
        *,
        expected_attempt: int,
        verified_manifest_digest: str | None = None,
    ) -> bool:
        with session:
            try:
                snapshot = _load_generation_publication_snapshot(
                    session,
                    job_id,
                    expected_attempt=expected_attempt,
                )
                if snapshot is None:
                    return False
                if snapshot.job.status == "succeeded" and (
                    snapshot.document.active_index_generation_id
                    != snapshot.index.index_generation_id
                    or (
                        snapshot.job.processing_generation is not None
                        and snapshot.document.active_processing_generation
                        != snapshot.job.processing_generation
                    )
                ):
                    return False
                if snapshot.job.status != "succeeded" and (
                    snapshot.job.status != "running"
                    or snapshot.index.status != "building"
                    or snapshot.job.progress_total is None
                    or snapshot.job.progress_current
                    != snapshot.job.progress_total
                ):
                    return False
                _validate_generation_publication_snapshot(
                    snapshot,
                    expected_attempt=expected_attempt,
                    require_recorded_manifest=True,
                )
                if (
                    not verified_manifest_digest
                    or snapshot.index.manifest_digest
                    != verified_manifest_digest
                ):
                    return False
                require_current_derived_epoch = not (
                    snapshot.job.job_kind == "reindex"
                    or snapshot.job.status == "succeeded"
                )
                artifact_expectation = _publication_artifact_expectation(
                    snapshot,
                    require_current_derived_parent_epoch=(
                        require_current_derived_epoch
                    ),
                )
                artifact_reader = _validated_artifact_publication_reader(
                    self.artifact_publication_reader_factory,
                    session,
                )
                artifact_inventory = (
                    artifact_reader.discover_identity_inventory(
                        artifact_expectation
                    )
                )
                provisional = _generation_publication_change_set(
                    snapshot,
                    manifest_digest=verified_manifest_digest,
                    finalize=True,
                    now=_utc_now(),
                    artifact_inventory=artifact_inventory,
                )
                lock_token = _acquire_document_processing_mutation(
                    session,
                    provisional,
                    _document_processing_candidates(provisional),
                )
                artifact_graph = artifact_reader.read_locked_current(
                    inventory=artifact_inventory,
                    expectation=artifact_expectation,
                )
                locked_snapshot = _load_generation_publication_snapshot(
                    session,
                    job_id,
                    expected_attempt=expected_attempt,
                )
                if locked_snapshot is None:
                    raise DocumentProcessingCurrentnessConflict(
                        "publication inventory changed after coordination"
                    )
                points = _validate_generation_publication_snapshot(
                    locked_snapshot,
                    expected_attempt=expected_attempt,
                    require_recorded_manifest=True,
                )
                if (
                    _publication_inventory_identities(locked_snapshot)
                    != _publication_inventory_identities(snapshot)
                    or _publication_artifact_expectation(
                        locked_snapshot,
                        require_current_derived_parent_epoch=(
                            require_current_derived_epoch
                        ),
                    )
                    != artifact_expectation
                ):
                    raise DocumentProcessingCurrentnessConflict(
                        "publication inventory changed after coordination"
                    )
                manifest_digest = _publication_manifest_digest(
                    locked_snapshot,
                    artifact_graph,
                    points,
                )
                if (
                    manifest_digest != verified_manifest_digest
                    or locked_snapshot.index.manifest_digest
                    != verified_manifest_digest
                    or (
                        locked_snapshot.job.processing_generation is not None
                        and locked_snapshot.generation.manifest_digest
                        != verified_manifest_digest
                    )
                ):
                    return False
                change_set = _generation_publication_change_set(
                    locked_snapshot,
                    manifest_digest=verified_manifest_digest,
                    finalize=True,
                    now=_utc_now(),
                    artifact_inventory=artifact_inventory,
                )
                authority = _LockedFinalGenerationPublication(
                    _session=session,
                    mutation=lock_token,
                    change_set=change_set,
                    artifact_inventory=artifact_inventory,
                    artifact_graph=artifact_graph,
                )
                replay = locked_snapshot.job.status == "succeeded"
                _apply_sealed_family_mutation(
                    session,
                    change_set,
                    require_exact_replay=replay,
                    lock_token=lock_token,
                    final_publication_authority=authority,
                )
                _publish_canonical_revision(
                    session,
                    locked_snapshot,
                    manifest_digest=verified_manifest_digest,
                )
                if not replay:
                    session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def publish_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        verified_manifest_digest: str | None = None,
    ) -> bool:
        """Publish only through the private Session-bound exact-CAS primitive."""

        return self._publish_final_generation(
            self.session_factory(),
            job_id,
            expected_attempt=expected_attempt,
            verified_manifest_digest=verified_manifest_digest,
        )

    def retired_vector_points(self, *, limit: int = 100) -> dict[str, list[str]]:
        if limit < 1 or limit > 1000:
            raise ValueError("retired vector limit must be between 1 and 1000")
        statement = (
            select(async_rows.AtlasVectorPointMappingRow)
            .join(
                async_rows.AtlasIndexGenerationRow,
                async_rows.AtlasIndexGenerationRow.index_generation_id
                == async_rows.AtlasVectorPointMappingRow.index_generation_id,
            )
            .where(
                async_rows.AtlasIndexGenerationRow.status == "retired",
                ~select(async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref)
                .join(
                    async_rows.AtlasProcessingGenerationRetentionRow,
                    async_rows.AtlasProcessingGenerationRetentionRow.retention_ref
                    == async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref,
                )
                .where(
                    async_rows.AtlasProcessingGenerationRetentionEntryRow.index_generation_id
                    == async_rows.AtlasIndexGenerationRow.index_generation_id,
                    async_rows.AtlasProcessingGenerationRetentionRow.status == "active",
                )
                .exists(),
            )
            .order_by(
                async_rows.AtlasVectorPointMappingRow.index_generation_id,
                async_rows.AtlasVectorPointMappingRow.point_id,
            )
            .limit(limit)
        )
        with self.session_factory() as session:
            rows = session.scalars(statement).all()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row.index_generation_id, []).append(row.point_id)
        return result

    def delete_retired_vector_points(
        self, points: Mapping[str, list[str]]
    ) -> None:
        if sum(len(point_ids) for point_ids in points.values()) > 1000:
            raise ValueError("retired vector deletion exceeds 1000 rows")
        session = self.session_factory()
        with session:
            try:
                deleted = 0
                for generation_id, point_ids in sorted(points.items()):
                    if not point_ids:
                        continue
                    generation = session.scalar(
                        select(async_rows.AtlasIndexGenerationRow)
                        .where(
                            async_rows.AtlasIndexGenerationRow.index_generation_id
                            == generation_id
                        )
                        .with_for_update()
                    )
                    if generation is None or generation.status != "retired":
                        raise DocumentProcessingCurrentnessConflict(
                            "index generation is not retired"
                        )
                    if session.scalar(
                        select(
                            async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref
                        )
                        .join(
                            async_rows.AtlasProcessingGenerationRetentionRow,
                            async_rows.AtlasProcessingGenerationRetentionRow.retention_ref
                            == async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref,
                        )
                        .where(
                            async_rows.AtlasProcessingGenerationRetentionEntryRow.index_generation_id
                            == generation_id,
                            async_rows.AtlasProcessingGenerationRetentionRow.status == "active",
                        )
                        .limit(1)
                    ) is not None:
                        raise DocumentProcessingCurrentnessConflict(
                            "index generation has an active processing-owned retention claim"
                        )
                    rows_to_delete = session.scalars(
                        select(async_rows.AtlasVectorPointMappingRow)
                        .where(
                            async_rows.AtlasVectorPointMappingRow.index_generation_id
                            == generation_id,
                            async_rows.AtlasVectorPointMappingRow.point_id.in_(point_ids),
                        )
                        .order_by(async_rows.AtlasVectorPointMappingRow.point_id)
                        .with_for_update()
                    ).all()
                    deleted += _delete_reconciliation_rows(
                        session,
                        tuple(rows_to_delete),
                        allowed_types=(
                            async_rows.AtlasVectorPointMappingRow,
                        ),
                    )
                if deleted:
                    AuditEventWriter(session).append(
                        _internal_event(
                            operation="retired_vector_points.deleted",
                            job_id=None,
                            document_id=None,
                            status="retired",
                        )
                    )
                    session.commit()
            except Exception:
                session.rollback()
                raise

    def cleanup_retired_generations(self, *, limit: int = 10) -> None:
        if limit < 1 or limit > 100:
            raise ValueError("retired generation limit must be between 1 and 100")
        session = self.session_factory()
        with session:
            try:
                retired = session.scalars(
                    select(async_rows.AtlasIndexGenerationRow)
                    .where(
                        async_rows.AtlasIndexGenerationRow.status == "retired",
                        ~select(async_rows.AtlasVectorPointMappingRow.point_id)
                        .where(
                            async_rows.AtlasVectorPointMappingRow.index_generation_id
                            == async_rows.AtlasIndexGenerationRow.index_generation_id
                        )
                        .exists(),
                        ~select(
                            async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref
                        )
                        .join(
                            async_rows.AtlasProcessingGenerationRetentionRow,
                            async_rows.AtlasProcessingGenerationRetentionRow.retention_ref
                            == async_rows.AtlasProcessingGenerationRetentionEntryRow.retention_ref,
                        )
                        .where(
                            async_rows.AtlasProcessingGenerationRetentionEntryRow.index_generation_id
                            == async_rows.AtlasIndexGenerationRow.index_generation_id,
                            async_rows.AtlasProcessingGenerationRetentionRow.status == "active",
                        )
                        .exists(),
                    )
                    .order_by(async_rows.AtlasIndexGenerationRow.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).all()
                if not retired:
                    return
                _delete_reconciliation_rows(
                    session,
                    tuple(retired),
                    allowed_types=(async_rows.AtlasIndexGenerationRow,),
                )
                AuditEventWriter(session).append(
                    _internal_event(
                        operation="retired_index_generations.cleaned",
                        job_id=None,
                        document_id=None,
                        status="retired",
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class DocumentMutationCommand:
    """Own document/version/tag lifecycle mutations with a named input."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        document: DocumentRecord,
        document_version_id: str,
        expected_document_lifecycle_epoch: int | None,
        versions: tuple[DocumentVersionRecord, ...] = (),
        tags: tuple[DocumentTagRecord, ...] = (),
        audit_events: tuple[AuditEventRecord, ...],
    ) -> None:
        _DocumentMutationSql(
            self.session_factory
        )._apply_validated_mutation(
            _DocumentLifecycleMutation(
                document_id=document.document_id,
                document_version_id=document_version_id,
                processing_generation=(
                    document.active_processing_generation or None
                ),
                expected_document_lifecycle_epoch=(
                    expected_document_lifecycle_epoch
                ),
                document=document,
                versions=versions,
                tags=tags,
                audit_events=audit_events,
            )
        )


@dataclass(frozen=True, slots=True)
class DocumentLifecycleMutationCommand:
    """Apply one exact document/tag lifecycle CAS with its audit evidence."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        expected_document: DocumentRecord | None,
        document: DocumentRecord,
        versions: tuple[DocumentVersionRecord, ...] = (),
        tags: tuple[DocumentTagRecord, ...] | None,
        audit_events: tuple[AuditEventRecord, ...],
        processing_acceptance: DocumentLifecycleProcessingAcceptance | None = None,
        presented_browser_session_token: str | None = None,
        expected_actor_type: str | None = None,
        expected_actor_id: str | None = None,
        control_action: str | None = None,
        denial_audit_event: AuditEventRecord | None = None,
        restore_verification: VerifiedDocumentRestoreSet | None = None,
    ) -> ProcessingJobRecord | None:
        request_control = (
            presented_browser_session_token,
            expected_actor_type,
            expected_actor_id,
            control_action,
        )
        if any(value is not None for value in request_control) and any(
            value is None for value in request_control
        ):
            raise ValueError("document lifecycle request authority is incomplete")
        if control_action not in {None, "edit", "admin"}:
            raise ValueError("document lifecycle control action is invalid")
        if not audit_events:
            raise ValueError("document lifecycle mutation requires audit evidence")
        if expected_document is not None and (
            expected_document.document_id != document.document_id
        ):
            raise ValueError("document lifecycle identity changed")
        if (
            any(version.document_id != document.document_id for version in versions)
            or len({version.document_version_id for version in versions})
            != len(versions)
        ):
            raise ValueError("document lifecycle versions are cross-wired")
        if expected_document is None and (
            len(versions) != 1
            or versions[0].status != "active"
            or versions[0].supersedes_version_id is not None
            or tags is None
        ):
            raise ValueError(
                "new document requires one active first version and exact tags"
            )
        if tags is not None and (
            len({(tag.tag_type, tag.tag_id) for tag in tags}) != len(tags)
            or any(tag.document_id != document.document_id for tag in tags)
            or any(
                tag.tag_type not in {"team", "project"} or not tag.tag_id
                for tag in tags
            )
        ):
            raise ValueError("document lifecycle tags are cross-wired")
        if tags is not None and document.scope_type in {"team", "project"} and (
            document.scope_type,
            document.scope_id,
        ) not in {(tag.tag_type, tag.tag_id) for tag in tags}:
            raise ValueError("document lifecycle owner scope is missing from tags")
        expected_target = f"document:{document.document_id}"
        if any(
            event.target_ref != expected_target
            or event.document_id != document.document_id
            or event.scope_type != document.scope_type
            or event.scope_id != document.scope_id
            or event.project_id
            != (document.scope_id if document.scope_type == "project" else None)
            for event in audit_events
        ):
            raise ValueError("document lifecycle audit is cross-wired")
        starts_restore = bool(
            expected_document is not None
            and expected_document.lifecycle_status == "disabled"
            and document.lifecycle_status == "restoring"
        )
        rebuilds_restore = bool(
            expected_document is not None
            and expected_document.lifecycle_status == "restoring"
            and document.lifecycle_status == "restoring"
            and processing_acceptance is not None
        )
        refreshes_active = bool(
            expected_document is not None
            and expected_document.lifecycle_status == "active"
            and document.lifecycle_status == "active"
            and processing_acceptance is not None
        )
        finishes_restore = bool(
            expected_document is not None
            and expected_document.lifecycle_status == "restoring"
            and (
                document.lifecycle_status == "active"
                or processing_acceptance is not None
            )
        )
        if finishes_restore != (restore_verification is not None):
            raise ValueError(
                "restore terminal mutation requires exact byte verification proof"
            )
        if restore_verification is not None and (
            restore_verification.document_id != document.document_id
            or restore_verification.resource_lifecycle_epoch
            != expected_document.resource_lifecycle_epoch
            or type(restore_verification.active_fence) is not StorageFence
            or not restore_verification.active_fence.target_id
            or not restore_verification.artifacts
            or len({item[0] for item in restore_verification.artifacts})
            != len(restore_verification.artifacts)
            or (
                document.lifecycle_status == "active"
                and not restore_verification.reusable_processing_generation
            )
            or (
                processing_acceptance is not None
                and restore_verification.reusable_processing_generation
            )
        ):
            raise ValueError("restore verification proof is cross-wired")
        if starts_restore and processing_acceptance is not None:
            raise ValueError(
                "restore verification must run outside the begin-restoring transaction"
            )
        if (
            processing_acceptance is not None
            and not rebuilds_restore
            and not refreshes_active
        ):
            raise ValueError(
                "processing acceptance requires an active refresh or restoring rebuild"
            )
        acceptance_identity = None
        processing_lock_keys: tuple[str, ...] = ()
        if processing_acceptance is not None:
            next_generation = (
                int(expected_document.active_processing_generation or 0)
                if expected_document is not None
                else 0
            ) + 1
            acceptance_identity = document_processing_acceptance_identity(
                document_id=document.document_id,
                idempotency_scope=processing_acceptance.idempotency_scope,
                idempotency_key=processing_acceptance.idempotency_key,
                processing_generation=next_generation,
            )
            processing_lock_keys = document_processing_acceptance_lock_identities(
                document_id=document.document_id,
                document_version_id=processing_acceptance.document_version_id,
                idempotency_scope=processing_acceptance.idempotency_scope,
                idempotency_key=processing_acceptance.idempotency_key,
                identity=acceptance_identity,
            )
        session = self.session_factory()
        with session:
            try:
                authority_domain_keys: tuple[str, ...] = ()
                authority_identity_keys: tuple[str, ...] = ()
                if presented_browser_session_token is not None:
                    assert expected_actor_type is not None
                    assert expected_actor_id is not None
                    authority_domain_keys = (
                        "team:hierarchy-control",
                        "team:membership-control",
                        *((
                            f"project:acl-control:{expected_document.scope_id}",
                        ) if expected_document is not None
                        and expected_document.scope_type == "project"
                        and expected_document.scope_id else ()),
                    )
                    authority_identity_keys = (
                        f"identity:session:{presented_browser_session_token}",
                        identity_actor_owner_key(expected_actor_id),
                        team_subject_owner_key(
                            expected_actor_type, expected_actor_id
                        ),
                        *((
                            team_owner_key(expected_document.scope_id),
                        ) if expected_document is not None
                        and expected_document.scope_type == "team"
                        and expected_document.scope_id else ()),
                        *((
                            project_owner_key(expected_document.scope_id),
                            project_acl_subject_owner_key(
                                expected_actor_type, expected_actor_id
                            ),
                        ) if expected_document is not None
                        and expected_document.scope_type == "project"
                        and expected_document.scope_id else ()),
                    )
                acquire_mixed_owner_locks(
                    session,
                    exclusive_domain_keys=authority_domain_keys,
                    exclusive_identity_keys=(
                        *authority_identity_keys,
                        f"document:allocation:{document.document_id}",
                        f"document:document:{document.document_id}",
                        *processing_lock_keys,
                        *(
                            f"document:version:{version.document_version_id}"
                            for version in versions
                        ),
                        *(
                            (f"document:job:{expected_document.processing_job_id}",)
                            if expected_document is not None
                            and expected_document.processing_job_id
                            else ()
                        ),
                        *(
                            f"document:tag:{tag.document_id}:{tag.tag_type}:{tag.tag_id}"
                            for tag in tags or ()
                        ),
                        *(
                            f"audit:event:{event.event_id}"
                            for event in audit_events
                        ),
                        *(
                            f"artifact:artifact:{artifact_id}"
                            for artifact_id, _blob_id, _checksum, _byte_size
                            in (restore_verification.artifacts if restore_verification else ())
                        ),
                        *(
                            f"artifact:blob:{blob_id}"
                            for _artifact_id, blob_id, _checksum, _byte_size
                            in (restore_verification.artifacts if restore_verification else ())
                        ),
                    ),
                )
                current = session.scalar(
                    select(document_rows.AtlasDocumentRow)
                    .where(
                        document_rows.AtlasDocumentRow.document_id
                        == document.document_id
                    )
                    .with_for_update()
                )
                if presented_browser_session_token is not None:
                    actor = identity_rows.read_session_actor(
                        session, presented_browser_session_token
                    )
                    if (
                        actor is None
                        or actor.actor_type != expected_actor_type
                        or actor.actor_id != expected_actor_id
                        or expected_document is None
                    ):
                        raise PermissionError(
                            "document lifecycle request is unauthenticated"
                        )
                    authority = _JobTransitionReadSql._authorization_state(
                        session,
                        actor_type=actor.actor_type,
                        actor_id=actor.actor_id,
                        scope_bindings=tuple(
                            sorted(
                                {
                                    (tag.tag_type, tag.tag_id)
                                    for tag in tags or ()
                                }
                            )
                        )
                        or ((expected_document.scope_type, expected_document.scope_id or ""),),
                    )
                    owner_active = document_owner_is_active(
                        authority,
                        expected_document.scope_type,
                        expected_document.scope_id,
                    )
                    allowed = owner_active and is_system_admin(
                        authority, actor.actor_type, actor.actor_id
                    )
                    if (
                        owner_active
                        and control_action == "edit"
                        and expected_document.uploader_actor_id == actor.actor_id
                    ):
                        allowed = True
                    if expected_document.scope_type == "team":
                        required_team_role = (
                            "uploader" if control_action == "edit" else "admin"
                        )
                        allowed = allowed or (
                            owner_active
                            and team_role_covers(
                                direct_team_role(
                                    authority,
                                    actor.actor_type,
                                    actor.actor_id,
                                    expected_document.scope_id or "",
                                ),
                                required_team_role,
                            )
                        )
                    elif (
                        expected_document.scope_type == "project"
                        and expected_document.scope_id
                    ):
                        allowed = allowed or (
                            owner_active
                            and resolve_access(
                                authority,
                                actor_type=actor.actor_type,
                                actor_id=actor.actor_id,
                                project_id=expected_document.scope_id,
                                action=(
                                    "document_register"
                                    if control_action == "edit"
                                    else "permission_manage"
                                ),
                                persist=False,
                            ).allowed
                        )
                    if not allowed:
                        if (
                            denial_audit_event is None
                            or denial_audit_event.actor_id != actor.actor_id
                            or denial_audit_event.document_id != document.document_id
                            or denial_audit_event.target_ref != expected_target
                        ):
                            raise ValueError(
                                "document lifecycle denial audit is missing or cross-wired"
                            )
                        AuditEventWriter(session).append_many(
                            (denial_audit_event,)
                        )
                        session.commit()
                        raise DocumentLifecycleDenied(denial_audit_event)
                    if any(
                        event.actor_id != actor.actor_id for event in audit_events
                    ) or (
                        processing_acceptance is not None
                        and processing_acceptance.created_by != actor.actor_id
                    ):
                        raise ValueError(
                            "document lifecycle attribution is cross-wired"
                        )
                if expected_document is not None and expected_document.processing_job_id:
                    session.scalar(
                        select(async_rows.AtlasProcessingJobRow)
                        .where(
                            async_rows.AtlasProcessingJobRow.job_id
                            == expected_document.processing_job_id
                        )
                        .with_for_update()
                    )
                if (current is None) != (expected_document is None) or (
                    current is not None
                    and asdict(_document_record(current))
                    != asdict(cast(DocumentRecord, expected_document))
                ):
                    raise DocumentProcessingCurrentnessConflict(
                        "document lifecycle preimage changed"
                    )
                if restore_verification is not None:
                    control = session.scalar(
                        select(artifact_rows.AtlasArtifactStorageControlRow)
                        .where(
                            artifact_rows.AtlasArtifactStorageControlRow.control_id
                            == "global"
                        )
                        .with_for_update()
                    )
                    proof_fence = restore_verification.active_fence
                    if (
                        control is None
                        or control.mode != "active"
                        or control.active_target_id != proof_fence.target_id
                        or control.active_target_revision
                        != proof_fence.target_revision
                        or control.root_identity_digest
                        != proof_fence.root_identity_digest
                        or control.storage_epoch != proof_fence.storage_epoch
                    ):
                        raise DocumentProcessingCurrentnessConflict(
                            "restore storage fence changed"
                        )
                    artifact_rows_by_id = {
                        row.artifact_id: row
                        for row in session.scalars(
                            select(artifact_rows.AtlasArtifactRow)
                            .where(
                                artifact_rows.AtlasArtifactRow.artifact_id.in_(
                                    {item[0] for item in restore_verification.artifacts}
                                )
                            )
                            .order_by(artifact_rows.AtlasArtifactRow.artifact_id)
                            .with_for_update()
                        ).all()
                    }
                    blob_rows_by_id = {
                        row.blob_id: row
                        for row in session.scalars(
                            select(artifact_rows.AtlasStorageBlobRow)
                            .where(
                                artifact_rows.AtlasStorageBlobRow.blob_id.in_(
                                    {item[1] for item in restore_verification.artifacts}
                                )
                            )
                            .order_by(artifact_rows.AtlasStorageBlobRow.blob_id)
                            .with_for_update()
                        ).all()
                    }
                    if any(
                        (artifact := artifact_rows_by_id.get(artifact_id)) is None
                        or (blob := blob_rows_by_id.get(blob_id)) is None
                        or artifact.blob_id != blob_id
                        or artifact.parent_resource_id != document.document_id
                        or artifact.owner_scope_type != document.scope_type
                        or artifact.owner_scope_id != document.scope_id
                        or artifact.lifecycle_status != "active"
                        or blob.status != "committed"
                        or artifact.checksum_value != checksum
                        or blob.checksum_value != checksum
                        or artifact.byte_size != byte_size
                        or blob.byte_size != byte_size
                        or blob.target_id != proof_fence.target_id
                        or blob.target_revision != proof_fence.target_revision
                        or blob.root_identity_digest
                        != proof_fence.root_identity_digest
                        or blob.storage_epoch != proof_fence.storage_epoch
                        for artifact_id, blob_id, checksum, byte_size
                        in restore_verification.artifacts
                    ) or (
                        expected_document.original_artifact_id
                        not in {item[0] for item in restore_verification.artifacts}
                    ):
                        raise DocumentProcessingCurrentnessConflict(
                            "restore storage verification became stale"
                        )
                _validate_document_transition(current, document)
                version_rows = {
                    row.document_version_id: row
                    for row in session.scalars(
                        select(document_rows.AtlasDocumentVersionRow)
                        .where(
                            document_rows.AtlasDocumentVersionRow.document_version_id.in_(
                                {version.document_version_id for version in versions}
                                or {""}
                            )
                        )
                        .order_by(
                            document_rows.AtlasDocumentVersionRow.document_version_id
                        )
                        .with_for_update()
                    ).all()
                }
                for version in versions:
                    _validate_version_transition(
                        version_rows.get(version.document_version_id), version
                    )
                if expected_document is None:
                    session.add(document_rows._document_row(document))
                else:
                    session.merge(document_rows._document_row(document))
                for version in versions:
                    session.merge(
                        document_rows.AtlasDocumentVersionRow(
                            document_version_id=version.document_version_id,
                            document_id=version.document_id,
                            payload=document_rows._document_version_payload(version),
                        )
                    )
                if tags is not None:
                    session.execute(
                        delete(document_rows.AtlasDocumentTagRow).where(
                            document_rows.AtlasDocumentTagRow.document_id
                            == document.document_id
                        )
                    )
                    session.add_all(
                        document_rows.AtlasDocumentTagRow(**asdict(tag))
                        for tag in tags
                    )
                AuditEventWriter(session).append_many(audit_events)
                job = None
                if processing_acceptance is not None:
                    session.flush()
                    job = ProcessingExecutionAcceptanceWriter(session).accept_job(
                        media_type=processing_acceptance.media_type,
                        document_id=document.document_id,
                        document_version_id=processing_acceptance.document_version_id,
                        job_kind=processing_acceptance.job_kind,
                        idempotency_scope=processing_acceptance.idempotency_scope,
                        idempotency_key=processing_acceptance.idempotency_key,
                        created_by=processing_acceptance.created_by,
                        progress_total=processing_acceptance.progress_total,
                        execution_snapshot=processing_acceptance.execution_snapshot,
                        acceptance_identity=acceptance_identity,
                    )
                session.commit()
                return job
            except Exception:
                session.rollback()
                raise


@dataclass(frozen=True, slots=True)
class ProcessingExecutionAcceptanceWriter:
    """Stage one accepted processing request in its caller-owned Session."""

    _session: Session

    def accept_job(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        execution_snapshot: ProcessingExecutionSnapshot,
        progress_total: int | None = None,
        acceptance_identity: DocumentProcessingAcceptanceIdentity | None = None,
    ) -> ProcessingJobRecord | None:
        expected_digest = _processing_acceptance_request_digest(
            media_type=media_type,
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            created_by=created_by,
            progress_total=progress_total,
        )
        if execution_snapshot.acceptance_request_digest != expected_digest:
            raise ValueError("processing execution snapshot request is mismatched")
        joined_factory: SessionFactory = lambda: Session(
            bind=self._session.connection(),
            join_transaction_mode="rollback_only",
        )
        return _JobTransitionSql(joined_factory).create_processing_job(
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            created_by=created_by,
            progress_total=progress_total,
            execution_snapshot=execution_snapshot,
            acceptance_identity=acceptance_identity,
        )


@dataclass(frozen=True, slots=True)
class AcceptProcessingExecutionCommand:
    """Atomically accept one job with its complete executable configuration."""

    session_factory: SessionFactory

    def accept_job(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        idempotency_scope: str,
        idempotency_key: str,
        created_by: str | None,
        progress_total: int | None = None,
        connection: Connection | None = None,
        execution_snapshot: ProcessingExecutionSnapshot | None = None,
        acceptance_identity: DocumentProcessingAcceptanceIdentity | None = None,
    ) -> ProcessingJobRecord | None:
        if job_kind == "reindex":
            raise ValueError("reindex does not accept a processing execution snapshot")
        if connection is not None and execution_snapshot is None:
            raise ValueError(
                "caller-owned processing acceptance requires a captured snapshot"
            )
        acceptance_request_digest = _processing_acceptance_request_digest(
            media_type=media_type,
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            created_by=created_by,
            progress_total=progress_total,
        )
        session = (
            self.session_factory()
            if connection is None
            else Session(bind=connection, join_transaction_mode="rollback_only")
        )
        with session:
            acquire_mixed_owner_locks(
                session,
                shared_domain_keys=(
                    (
                        "model-routing:configuration-control",
                        "processing-registry:configuration-control",
                    )
                    if execution_snapshot is None
                    else ()
                ),
                exclusive_identity_keys=(
                    "document:job-idempotency:"
                    f"{idempotency_scope}:{idempotency_key}",
                ),
            )
            existing = session.scalar(
                select(async_rows.AtlasProcessingJobRow).where(
                    async_rows.AtlasProcessingJobRow.idempotency_scope
                    == idempotency_scope,
                    async_rows.AtlasProcessingJobRow.idempotency_key
                    == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.document_id != document_id
                    or existing.document_version_id != document_version_id
                    or existing.job_kind != job_kind
                    or existing.processing_generation is None
                ):
                    raise ValueError("idempotency_key_reused")
                request_snapshot = session.get(
                    async_rows.AtlasProcessingRequestSnapshotRow,
                    existing.job_id,
                )
                if request_snapshot is None:
                    raise ValueError("processing execution snapshot is missing")
                accepted_snapshot = _processing_execution_snapshot(
                    request_snapshot.payload
                )
                if (
                    accepted_snapshot.acceptance_request_digest
                    != acceptance_request_digest
                ):
                    raise ValueError("idempotency_key_reused")
                return _job_record(existing)
            snapshot = execution_snapshot
            if snapshot is None:
                snapshot = _capture_processing_execution_snapshot(
                    session,
                    media_type=media_type,
                    acceptance_request_digest=acceptance_request_digest,
                    configuration_locks_held=True,
                )
            elif snapshot.acceptance_request_digest != acceptance_request_digest:
                raise ValueError("processing execution snapshot request is mismatched")
            job = ProcessingExecutionAcceptanceWriter(session).accept_job(
                media_type=media_type,
                document_id=document_id,
                document_version_id=document_version_id,
                job_kind=job_kind,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
                created_by=created_by,
                progress_total=progress_total,
                execution_snapshot=snapshot,
                acceptance_identity=acceptance_identity,
            )
            session.commit()
            return job


@dataclass(frozen=True, slots=True)
class ProcessingExecutionCaptureWriter:
    """Capture executable configuration in its caller-owned Session."""

    _session: Session

    def execute(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        created_by: str | None,
        progress_total: int | None = None,
        configuration_locks_held: bool = False,
    ) -> ProcessingExecutionSnapshot:
        digest = _processing_acceptance_request_digest(
            media_type=media_type,
            document_id=document_id,
            document_version_id=document_version_id,
            job_kind=job_kind,
            created_by=created_by,
            progress_total=progress_total,
        )
        return _capture_processing_execution_snapshot(
            self._session,
            media_type=media_type,
            acceptance_request_digest=digest,
            configuration_locks_held=configuration_locks_held,
        )


@dataclass(frozen=True, slots=True)
class CaptureProcessingExecutionCommand:
    """Capture current executable configuration once at the request boundary."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        media_type: str,
        document_id: str,
        document_version_id: str,
        job_kind: str,
        created_by: str | None,
        progress_total: int | None = None,
    ) -> ProcessingExecutionSnapshot:
        with self.session_factory() as session:
            return ProcessingExecutionCaptureWriter(session).execute(
                media_type=media_type,
                document_id=document_id,
                document_version_id=document_version_id,
                job_kind=job_kind,
                created_by=created_by,
                progress_total=progress_total,
            )


@dataclass(frozen=True, slots=True)
class LoadProcessingExecutionCommand:
    """Load only the request-owned snapshot after attempt/fence integrity checks."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        job_id: str,
        expected_attempt: int,
        expected_fence: int,
    ) -> ProcessingExecutionSnapshot:
        if expected_attempt <= 0 or expected_fence < 0:
            raise ValueError("processing execution identity is invalid")
        with self.session_factory() as session:
            job = session.scalar(
                select(async_rows.AtlasProcessingJobRow).where(
                    async_rows.AtlasProcessingJobRow.job_id == job_id
                )
            )
            if job is None:
                raise ValueError("processing_job_not_found")
            if (
                job.attempt != expected_attempt
                or job.fence != expected_fence
                or job.status in {"succeeded", "failed", "cancelled"}
            ):
                raise DocumentProcessingCurrentnessConflict(
                    "processing execution attempt is no longer current"
                )
            if job.processing_generation is None:
                raise ValueError("processing execution generation is missing")
            request_snapshot = session.get(
                async_rows.AtlasProcessingRequestSnapshotRow,
                job.job_id,
            )
            if request_snapshot is None:
                raise ValueError("processing execution snapshot is missing")
            if (
                request_snapshot.accepted_attempt != 1
                or request_snapshot.accepted_attempt > expected_attempt
            ):
                raise ValueError("processing request snapshot attempt is invalid")
            snapshot = _processing_execution_snapshot(
                request_snapshot.payload
            )
            if (
                request_snapshot.document_id != job.document_id
                or request_snapshot.processing_generation
                != job.processing_generation
            ):
                raise ValueError("processing request snapshot lineage is mismatched")
            return snapshot


@dataclass(frozen=True, slots=True)
class JobTransitionCommand:
    """Own processing job state, Stop/Retry, and worker lease transitions."""

    session_factory: SessionFactory

    def transaction(self) -> AbstractContextManager[Connection]:
        return _JobTransitionSql(self.session_factory).transaction()

    def list_jobs(self, *, document_id: str | None = None, limit: int = 100) -> list[ProcessingJobRecord]:
        return _JobTransitionSql(self.session_factory).list_jobs(document_id=document_id, limit=limit)

    def get_job(self, job_id: str) -> ProcessingJobRecord | None:
        return _JobTransitionSql(self.session_factory).get_job(job_id)

    def list_job_projection_batch(self, *, actor_type: str, actor_id: str, presented_browser_session_token: str, document_id: str | None = None) -> ProcessingJobListBatch:
        return _JobTransitionSql(self.session_factory).list_job_projection_batch(actor_type=actor_type, actor_id=actor_id, presented_browser_session_token=presented_browser_session_token, document_id=document_id)

    def list_document_job_request_projections(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[DocumentJobRequestAuthorityProjection, ...]:
        return _JobTransitionSql(
            self.session_factory
        ).list_document_job_request_projections(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            document_id=document_id,
            limit=limit,
        )

    def get_document_job_request_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        job_id: str,
    ) -> DocumentJobRequestAuthorityProjection | None:
        return _JobTransitionSql(
            self.session_factory
        ).get_document_job_request_projection(
            actor_type=actor_type,
            actor_id=actor_id,
            presented_browser_session_token=presented_browser_session_token,
            job_id=job_id,
        )

    def create_processing_job(self, *, document_id: str, document_version_id: str, job_kind: str, idempotency_scope: str, idempotency_key: str, created_by: str | None, progress_total: int | None = None, connection: Connection | None = None) -> ProcessingJobRecord:
        return _JobTransitionSql(self.session_factory).create_processing_job(document_id=document_id, document_version_id=document_version_id, job_kind=job_kind, idempotency_scope=idempotency_scope, idempotency_key=idempotency_key, created_by=created_by, progress_total=progress_total, connection=connection)

    def is_current_task_attempt(self, *, task_name: str, identity: Mapping[str, str], attempt: int | None) -> bool:
        return _JobTransitionSql(self.session_factory).is_current_task_attempt(task_name=task_name, identity=identity, attempt=attempt)

    def claim_job(self, job_id: str, worker_id: str, *, lease_seconds: int = 90) -> tuple[ProcessingJobRecord, int] | None:
        return _JobTransitionSql(self.session_factory).claim_job(job_id, worker_id, lease_seconds=lease_seconds)

    def cancel_processing_job(self, job_id: str) -> ProcessingJobRecord:
        return _JobTransitionSql(self.session_factory).cancel_processing_job(job_id)

    def stop_processing_job_request(
        self,
        *,
        job_id: str,
        presented_browser_session_token: str,
        actor_type: str,
        actor_id: str,
    ) -> ProcessingControlResult:
        result = _JobTransitionSql(self.session_factory).cancel_processing_job(
            job_id,
            presented_browser_session_token=presented_browser_session_token,
            expected_actor_type=actor_type,
            expected_actor_id=actor_id,
        )
        if not isinstance(result, ProcessingControlResult):
            raise RuntimeError("processing Stop request lost its audit outcome")
        return result

    def retry_terminal_job(self, job_id: str) -> ProcessingJobRecord:
        return _JobTransitionSql(self.session_factory).retry_terminal_job(job_id)

    def retry_processing_job_request(
        self,
        *,
        job_id: str,
        presented_browser_session_token: str,
        actor_type: str,
        actor_id: str,
    ) -> ProcessingControlResult:
        result = _JobTransitionSql(self.session_factory).retry_terminal_job(
            job_id,
            presented_browser_session_token=presented_browser_session_token,
            expected_actor_type=actor_type,
            expected_actor_id=actor_id,
        )
        if not isinstance(result, ProcessingControlResult):
            raise RuntimeError("processing Retry request lost its audit outcome")
        return result

    def fail_job(self, job_id: str, *, expected_attempt: int, code: str, detail: str) -> None:
        _JobTransitionSql(self.session_factory).fail_job(job_id, expected_attempt=expected_attempt, code=code, detail=detail)

    def schedule_retry(self, job_id: str, *, expected_attempt: int, task_name: str, queue_name: str, payload: Mapping[str, str], code: str, detail: str, delay_seconds: int = 2) -> None:
        _JobTransitionSql(self.session_factory).schedule_retry(job_id, expected_attempt=expected_attempt, task_name=task_name, queue_name=queue_name, payload=payload, code=code, detail=detail, delay_seconds=delay_seconds)

    def prepare_job(self, job_id: str, *, total_units: int, profile_id: str, profile_revision: int, expected_attempt: int, enqueue_batches: bool = True) -> list[str]:
        return _JobTransitionSql(self.session_factory).prepare_job(job_id, total_units=total_units, profile_id=profile_id, profile_revision=profile_revision, expected_attempt=expected_attempt, enqueue_batches=enqueue_batches)

    def prepare_reindex(self, job_id: str, *, expected_attempt: int, batch_size: int = 100) -> int:
        return _JobTransitionSql(self.session_factory).prepare_reindex(job_id, expected_attempt=expected_attempt, batch_size=batch_size)

    def mark_failure(self, job_id: str, *, fence: int, code: str, detail: str, transient: bool) -> None:
        _JobTransitionSql(self.session_factory).mark_failure(job_id, fence=fence, code=code, detail=detail, transient=transient)


@dataclass(frozen=True, slots=True)
class OutboxDeliveryCommand:
    """Own exact work-identity dispatch, completion, and crash recovery."""

    session_factory: SessionFactory

    def get_outbox(self, outbox_id: str) -> TaskOutboxRecord | None:
        return _OutboxDeliverySql(self.session_factory).get_outbox(outbox_id)

    def list_outbox(self, *, status: str | None = None, limit: int = 100) -> list[TaskOutboxRecord]:
        return _OutboxDeliverySql(self.session_factory).list_outbox(status=status, limit=limit)

    def claim_pending_outbox(self, worker_id: str, *, limit: int = 50) -> list[dict[str, object]]:
        return _OutboxDeliverySql(self.session_factory).claim_pending_outbox(worker_id, limit=limit)

    def release_outbox(self, outbox_id: str, worker_id: str, error_code: str) -> None:
        _OutboxDeliverySql(self.session_factory).release_outbox(outbox_id, worker_id, error_code)

    def complete_outbox(self, outbox_id: str, worker_id: str) -> None:
        _OutboxDeliverySql(self.session_factory).complete_outbox(outbox_id, worker_id)

    def reconcile_expired_claims(self, *, limit: int = 100) -> None:
        _OutboxDeliverySql(self.session_factory).reconcile_expired_claims(limit=limit)


@dataclass(frozen=True, slots=True)
class BatchCheckpointCommand:
    """Own batch claims, checkpoints, index fan-out, and bounded cleanup."""

    session_factory: SessionFactory

    def reconcile_incomplete_page_batches(self, *, limit: int = 100) -> int:
        return _BatchCheckpointSql(
            self.session_factory
        ).reconcile_incomplete_page_batches(limit=limit)

    def batch_execution(
        self, job_id: str, batch_id: str
    ) -> AbstractContextManager[ProcessingJobView | None]:
        return _BatchCheckpointSql(self.session_factory).batch_execution(job_id, batch_id)

    def index_batch_execution(
        self, job_id: str, batch_id: str, *, expected_attempt: int
    ) -> AbstractContextManager[ProcessingJobView | None]:
        return _BatchCheckpointSql(self.session_factory).index_batch_execution(
            job_id, batch_id, expected_attempt=expected_attempt
        )

    def schedule_page_batch_retry(
        self,
        job_id: str,
        batch_id: str,
        *,
        expected_attempt: int,
        task_name: str,
        code: str,
        delay_seconds: int = 2,
    ) -> bool:
        return _BatchCheckpointSql(self.session_factory).schedule_page_batch_retry(
            job_id,
            batch_id,
            expected_attempt=expected_attempt,
            task_name=task_name,
            code=code,
            delay_seconds=delay_seconds,
        )

    def preparation_execution(
        self, job_id: str, *, expected_attempt: int
    ) -> AbstractContextManager[ProcessingJobView | None]:
        return _BatchCheckpointSql(self.session_factory).preparation_execution(job_id, expected_attempt=expected_attempt)

    def finalize_document_page_preparation(self, connection: Connection, *, job_id: str, expected_attempt: int, claim_fence: int, claim_token: str, page_record: dict[str, Any]) -> str:
        return _BatchCheckpointSql(self.session_factory).finalize_document_page_preparation(connection, job_id=job_id, expected_attempt=expected_attempt, claim_fence=claim_fence, claim_token=claim_token, page_record=page_record)

    def prepared_page_artifact(self, job_id: str, batch_id: str) -> dict[str, Any]:
        return _BatchCheckpointSql(self.session_factory).prepared_page_artifact(job_id, batch_id)

    def get_processing_profile_pin(self, *, document_id: str, processing_generation: int) -> ProcessingProfilePin:
        return _BatchCheckpointSql(self.session_factory).get_processing_profile_pin(document_id=document_id, processing_generation=processing_generation)

    def chunks_for_batch(self, job_id: str, batch_id: str) -> tuple[ProcessingJobView, list[dict[str, Any]]]:
        return _BatchCheckpointSql(self.session_factory).chunks_for_batch(job_id, batch_id)

    def set_embedding_profile(self, job_id: str, index_generation_id: str, profile: dict[str, Any], *, expected_attempt: int) -> bool:
        return _BatchCheckpointSql(self.session_factory).set_embedding_profile(job_id, index_generation_id, profile, expected_attempt=expected_attempt)

    def stage_reindex_batch(self, job_id: str, batch_id: str, *, expected_attempt: int, batch_size: int = 100) -> bool:
        return _BatchCheckpointSql(self.session_factory).stage_reindex_batch(job_id, batch_id, expected_attempt=expected_attempt, batch_size=batch_size)

    def cleanup_staging(self, *, limit: int = 100) -> None:
        _BatchCheckpointSql(self.session_factory).cleanup_staging(limit=limit)

    def get_batch_claim(self, batch_id: str) -> ProcessingBatchClaimRecord | None:
        return _BatchCheckpointSql(self.session_factory).get_batch_claim(batch_id)

    def list_batch_claims(self, *, job_id: str, limit: int = 100) -> list[ProcessingBatchClaimRecord]:
        return _BatchCheckpointSql(self.session_factory).list_batch_claims(job_id=job_id, limit=limit)

    def get_checkpoint(self, *, job_id: str, unit_kind: str, unit_start: int, unit_end: int) -> ProcessingCheckpointRecord | None:
        return _BatchCheckpointSql(self.session_factory).get_checkpoint(job_id=job_id, unit_kind=unit_kind, unit_start=unit_start, unit_end=unit_end)

    def list_checkpoints(self, *, job_id: str, limit: int = 200) -> list[ProcessingCheckpointRecord]:
        return _BatchCheckpointSql(self.session_factory).list_checkpoints(job_id=job_id, limit=limit)

    def claim_processing_batch(self, *, job_id: str, batch_id: str, expected_attempt: int, expected_fence: int, unit_kind: str, unit_start: int, unit_end: int, lease_seconds: int = 300) -> ProcessingBatchClaimRecord | None:
        return _BatchCheckpointSql(self.session_factory).claim_processing_batch(job_id=job_id, batch_id=batch_id, expected_attempt=expected_attempt, expected_fence=expected_fence, unit_kind=unit_kind, unit_start=unit_start, unit_end=unit_end, lease_seconds=lease_seconds)

    def renew_batch_claim(self, *, job_id: str, batch_id: str, attempt: int, claim_fence: int, claim_token: str, unit_kind: str = "page", lease_seconds: int = 300) -> bool:
        return _BatchCheckpointSql(self.session_factory).renew_batch_claim(job_id=job_id, batch_id=batch_id, attempt=attempt, claim_fence=claim_fence, claim_token=claim_token, unit_kind=unit_kind, lease_seconds=lease_seconds)

    def checkpoint_for_batch(self, job_id: str, batch_id: str) -> dict[str, Any] | None:
        return _BatchCheckpointSql(self.session_factory).checkpoint_for_batch(job_id, batch_id)

    def commit_checkpoint(self, *, job_id: str, attempt: int, claim_fence: int, claim_token: str, batch_id: str, unit_start: int, unit_end: int, input_fingerprint: str, output_digest: str, evidence_rows: list[dict[str, Any]], chunk_rows: list[dict[str, Any]], page_artifact_rows: list[dict[str, Any]] | None = None, preview_count: int = 0, warning_codes: list[str] | None = None) -> bool:
        return _BatchCheckpointSql(self.session_factory).commit_checkpoint(job_id=job_id, attempt=attempt, claim_fence=claim_fence, claim_token=claim_token, batch_id=batch_id, unit_start=unit_start, unit_end=unit_end, input_fingerprint=input_fingerprint, output_digest=output_digest, evidence_rows=evidence_rows, chunk_rows=chunk_rows, page_artifact_rows=page_artifact_rows, preview_count=preview_count, warning_codes=warning_codes)

    def enqueue_index_batch(self, job_id: str, batch_id: str, *, expected_attempt: int) -> bool:
        return _BatchCheckpointSql(self.session_factory).enqueue_index_batch(job_id, batch_id, expected_attempt=expected_attempt)

    def mark_batch_indexed(self, *, job_id: str, batch_id: str, mappings: list[dict[str, Any]], expected_attempt: int) -> bool:
        return _BatchCheckpointSql(self.session_factory).mark_batch_indexed(job_id=job_id, batch_id=batch_id, mappings=mappings, expected_attempt=expected_attempt)


@dataclass(frozen=True, slots=True)
class FinalGenerationPublicationCommand:
    """Own the complete manifest CAS and retired-vector reconciliation."""

    session_factory: SessionFactory
    artifact_publication_reader_factory: ArtifactPublicationReaderFactory = (
        lambda session: _GenerationArtifactPublicationReader(session)
    )

    def load_publication_manifest(self, job_id: str, *, expected_attempt: int) -> IndexPublicationManifest | None:
        return _FinalGenerationPublicationSql(self.session_factory, self.artifact_publication_reader_factory).load_publication_manifest(job_id, expected_attempt=expected_attempt)

    def publish_job(self, job_id: str, *, expected_attempt: int, verified_manifest_digest: str | None = None) -> bool:
        return _FinalGenerationPublicationSql(self.session_factory, self.artifact_publication_reader_factory).publish_job(job_id, expected_attempt=expected_attempt, verified_manifest_digest=verified_manifest_digest)

    def retired_vector_points(self, *, limit: int = 100) -> dict[str, list[str]]:
        return _FinalGenerationPublicationSql(self.session_factory, self.artifact_publication_reader_factory).retired_vector_points(limit=limit)

    def delete_retired_vector_points(self, points: Mapping[str, list[str]]) -> None:
        _FinalGenerationPublicationSql(self.session_factory, self.artifact_publication_reader_factory).delete_retired_vector_points(points)

    def cleanup_retired_generations(self, *, limit: int = 10) -> None:
        _FinalGenerationPublicationSql(self.session_factory, self.artifact_publication_reader_factory).cleanup_retired_generations(limit=limit)


__all__ = [
    "AcceptProcessingExecutionCommand",
    "BatchCheckpointCommand",
    "CaptureProcessingExecutionCommand",
    "CurrentRowExpectation",
    "DocumentMutationCommand",
    "DocumentLifecycleMutationCommand",
    "FinalGenerationPublicationCommand",
    "IndexGenerationProjection",
    "IndexGenerationTransition",
    "IndexPublicationManifest",
    "IndexPublicationPoint",
    "JobTransitionCommand",
    "LoadProcessingExecutionCommand",
    "DocumentProcessingAcceptanceIdentity",
    "OutboxDeliveryCommand",
    "ProcessingBatchClaimRecord",
    "ProcessingBatchClaimTransition",
    "ProcessingCheckpointRecord",
    "ProcessingCheckpointTransition",
    "ProcessingGenerationProjection",
    "ProcessingGenerationTransition",
    "ProcessingExecutionAcceptanceWriter",
    "ProcessingExecutionCaptureWriter",
    "SearchChunkProjection",
    "SearchChunkTransition",
    "TaskOutboxRecord",
    "TaskOutboxTransition",
    "VectorPointMappingRecord",
    "VectorPointMappingTransition",
    "document_processing_acceptance_identity",
    "document_processing_acceptance_lock_identities",
    "processing_execution_snapshot_payload",
    "attach_document_job_request_projections",
]
