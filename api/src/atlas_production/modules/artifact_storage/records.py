from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


StorageMode = Literal["setup", "required", "active"]
WriteAttemptStatus = Literal[
    "receiving",
    "bytes_verified",
    "reserved",
    "published",
    "succeeded",
    "failed",
    "quarantined",
]
BlobStatus = Literal["pending", "committed", "failed", "quarantined"]
ArtifactLifecycleStatus = Literal[
    "staged", "active", "tombstoned", "failed", "quarantined"
]
OperationStatus = Literal["succeeded"]
OwnerScopeType = Literal["team", "project", "conversation", "system"]
BindingKind = Literal[
    "owner", "authorization", "provenance", "inherited", "audit", "source"
]
TargetVerificationMode = Literal["full_hash", "operator_accepted_unverified"]
TargetEvidenceClaim = Literal[
    "TARGET_COPY_CHECKSUM_VERIFIED", "OPERATOR_ACCEPTED_UNVERIFIED_TARGET"
]

UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT = (
    "I_ACCEPT_UNVERIFIED_BLOB_MAPPING_AND_CONTENT"
)


STORAGE_TARGET_CAPABILITY_FIELDS = frozenset(
    {
        "create_file",
        "modify_file",
        "remove_file",
    }
)

WRITE_ATTEMPT_INTENT_FIELDS = frozenset(
    {
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
        "authorization_bindings",
        "allowed_parent_statuses",
    }
)

# Artifact identity and lineage have normalized columns. The metadata envelope
# currently has no permitted keys; adding one requires an explicit policy change.
ARTIFACT_METADATA_FIELDS = frozenset()


@dataclass(frozen=True)
class StorageFence:
    target_id: str
    target_revision: int
    root_identity_digest: str
    storage_epoch: int


@dataclass(frozen=True)
class CurrentArtifactAccessDecision:
    actor_type: str
    actor_id: str
    action: str
    allowed_scopes: frozenset[tuple[str, str]]


@dataclass
class StorageControlRecord:
    control_id: str = "global"
    mode: StorageMode = "setup"
    active_target_id: str | None = None
    active_target_revision: int | None = None
    root_identity_digest: str | None = None
    storage_epoch: int = 1
    updated_at: str = ""

    def active_fence(self) -> StorageFence | None:
        if (
            self.mode != "active"
            or self.active_target_id is None
            or self.active_target_revision is None
            or self.root_identity_digest is None
        ):
            return None
        return StorageFence(
            target_id=self.active_target_id,
            target_revision=self.active_target_revision,
            root_identity_digest=self.root_identity_digest,
            storage_epoch=self.storage_epoch,
        )


@dataclass
class StorageTargetRecord:
    target_id: str
    target_revision: int
    target_kind: Literal["local", "smb_mount"]
    masked_label: str
    config_key: str
    root_identity_digest: str
    capabilities: dict[str, bool]
    status: Literal["registered", "probed", "active", "rejected"]
    created_at: str
    updated_at: str
    created_by: str
    verification_mode: TargetVerificationMode
    evidence_claim: TargetEvidenceClaim
    failure_code: str | None = None
    registration_idempotency_key: str | None = None
    registration_request_fingerprint: str | None = None


@dataclass
class ArtifactWriteAttemptRecord:
    write_attempt_id: str
    idempotency_scope: str
    idempotency_key: str
    request_fingerprint: str
    fence: StorageFence
    parent_resource_id: str
    parent_lifecycle_epoch: int
    status: WriteAttemptStatus
    lease_owner: str
    lease_expires_at: str
    attempt_generation: int
    last_heartbeat_at: str
    opaque_temp_name: str
    created_at: str
    updated_at: str
    intent: dict[str, Any] = field(default_factory=dict)
    blob_id: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None
    failure_code: str | None = None
    failure_detail_summary: str | None = None
    reconciliation_required_at: str | None = None
    reconciled_at: str | None = None
    reconciled_by: str | None = None


@dataclass
class StorageBlobRecord:
    blob_id: str
    opaque_ref: str
    status: BlobStatus
    dedup_mode: Literal["original", "none"]
    checksum_algorithm: Literal["sha256"]
    checksum_value: str
    byte_size: int
    content_type: str
    fence: StorageFence
    created_at: str
    updated_at: str
    dedup_scope_type: Literal["team", "project"] | None = None
    dedup_scope_id: str | None = None
    write_attempt_id: str | None = None
    committed_at: str | None = None
    failure_code: str | None = None
    failure_detail_summary: str | None = None
    reconciliation_required_at: str | None = None
    reconciled_at: str | None = None
    reconciled_by: str | None = None


@dataclass
class ArtifactRecord:
    artifact_id: str
    artifact_class: str
    blob_id: str
    checksum_algorithm: Literal["sha256"]
    checksum_value: str
    byte_size: int
    content_type: str
    owner_scope_type: OwnerScopeType
    owner_scope_id: str | None
    lifecycle_status: ArtifactLifecycleStatus
    created_at: str
    updated_at: str
    logical_identity: str
    source_artifact_id: str | None = None
    document_version_id: str | None = None
    parent_resource_id: str | None = None
    parent_lifecycle_epoch: int | None = None
    processing_generation: int | None = None
    pipeline_id: str | None = None
    pipeline_version: str | None = None
    generation: int | None = None
    page_number: int | None = None
    block_id: str | None = None
    acl_policy_version: str | None = None
    acl_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactScopeBindingRecord:
    binding_id: str
    artifact_id: str
    binding_kind: BindingKind
    scope_type: OwnerScopeType
    scope_id: str | None
    created_at: str


@dataclass
class ArtifactOperationRecord:
    operation_id: str
    operation_type: Literal["target_configuration"]
    idempotency_scope: str
    idempotency_key: str
    request_fingerprint: str
    status: OperationStatus
    fence: StorageFence
    created_at: str
    updated_at: str
    verification_mode: TargetVerificationMode
    evidence_claim: TargetEvidenceClaim
    committed_blob_count: int
    total_bytes: int
    blob_set_digest: str


@dataclass
class StorageRequestLeaseRecord:
    lease_id: str
    request_kind: str
    owner: str
    fence: StorageFence
    acquired_at: str
    expires_at: str
    last_heartbeat_at: str
    attempt_generation: int
    parent_resource_id: str | None = None
    parent_lifecycle_epoch: int | None = None


@dataclass
class StorageReconciliationFindingRecord:
    finding_id: str
    finding_kind: str
    status: Literal["open", "resolved", "quarantined"]
    detected_at: str
    safe_summary: str
    blob_id: str | None = None
    write_attempt_id: str | None = None
    operation_id: str | None = None
    reconciled_at: str | None = None
    reconciled_by: str | None = None
