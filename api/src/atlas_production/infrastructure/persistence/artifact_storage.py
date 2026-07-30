from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    or_,
    String,
    Text,
    UniqueConstraint,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

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
    ARTIFACT_METADATA_FIELDS,
    STORAGE_TARGET_CAPABILITY_FIELDS,
    WRITE_ATTEMPT_INTENT_FIELDS,
)
from atlas_production.modules.artifact_storage.errors import ArtifactFenceRejected

from .base import OrmBase
from .payload_policy import (
    GENERAL_METADATA_MAX_BYTES,
    RUNTIME_POLICY_MAX_BYTES,
    validate_typed_payload,
)


SHA256_SQL = "^[0-9a-f]{64}$"


class AtlasArtifactStorageControlRow(OrmBase):
    __tablename__ = "atlas_artifact_storage_control"
    control_id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, index=True)
    active_target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_target_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_identity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_target_id", "active_target_revision"],
            [
                "atlas_artifact_storage_targets.target_id",
                "atlas_artifact_storage_targets.target_revision",
            ],
            name="fk_atlas_storage_control_active_target",
            match="FULL",
            use_alter=True,
        ),
        CheckConstraint("control_id = 'global'", name="ck_atlas_storage_control_singleton"),
        CheckConstraint(
            "mode IN ('setup','required','active')",
            name="ck_atlas_storage_control_mode",
        ),
        CheckConstraint("storage_epoch >= 1", name="ck_atlas_storage_control_epoch"),
        CheckConstraint(
            "(active_target_id IS NULL) = (active_target_revision IS NULL)",
            name="ck_atlas_storage_control_target_tuple",
        ),
        CheckConstraint(
            f"root_identity_digest IS NULL OR root_identity_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_storage_control_root_digest",
        ),
        CheckConstraint(
            "mode <> 'active' OR (active_target_id IS NOT NULL AND active_target_revision IS NOT NULL AND root_identity_digest IS NOT NULL)",
            name="ck_atlas_storage_control_active_target",
        ),
    )


class AtlasArtifactStorageTargetRow(OrmBase):
    __tablename__ = "atlas_artifact_storage_targets"
    target_id: Mapped[str] = mapped_column(String, primary_key=True)
    target_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    masked_label: Mapped[str] = mapped_column(String, nullable=False)
    config_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    root_identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    capabilities: Mapped[dict[str, bool]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    verification_mode: Mapped[str] = mapped_column(String, nullable=False)
    evidence_claim: Mapped[str] = mapped_column(String, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    registration_idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    registration_request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        CheckConstraint("target_revision > 0", name="ck_atlas_storage_target_revision"),
        CheckConstraint(
            "target_kind IN ('local','smb_mount')", name="ck_atlas_storage_target_kind"
        ),
        CheckConstraint(
            "verification_mode IN ('full_hash','operator_accepted_unverified')",
            name="ck_atlas_storage_target_verification_mode",
        ),
        CheckConstraint(
            "(verification_mode = 'full_hash' AND evidence_claim = 'TARGET_COPY_CHECKSUM_VERIFIED') "
            "OR (verification_mode = 'operator_accepted_unverified' AND evidence_claim = 'OPERATOR_ACCEPTED_UNVERIFIED_TARGET')",
            name="ck_atlas_storage_target_evidence_claim",
        ),
        CheckConstraint(
            "status IN ('registered','probed','active','rejected')",
            name="ck_atlas_storage_target_status",
        ),
        CheckConstraint(
            f"root_identity_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_storage_target_root_digest",
        ),
        CheckConstraint(
            "capabilities = '{\"create_file\": true, \"modify_file\": true, "
            "\"remove_file\": true}'::jsonb",
            name="ck_atlas_storage_target_capabilities",
        ),
        CheckConstraint(
            f"registration_request_fingerprint IS NULL OR registration_request_fingerprint ~ '{SHA256_SQL}'",
            name="ck_atlas_storage_target_registration_fingerprint",
        ),
        CheckConstraint(
            "(registration_idempotency_key IS NULL) = (registration_request_fingerprint IS NULL)",
            name="ck_atlas_storage_target_registration_identity",
        ),
        UniqueConstraint(
            "created_by",
            "registration_idempotency_key",
            name="ux_atlas_storage_target_registration_idempotency",
        ),
    )


class _FenceColumns:
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    root_identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AtlasArtifactWriteAttemptRow(_FenceColumns, OrmBase):
    __tablename__ = "atlas_artifact_write_attempts"
    write_attempt_id: Mapped[str] = mapped_column(String, primary_key=True)
    idempotency_scope: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_resource_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_lifecycle_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lease_owner: Mapped[str] = mapped_column(String, nullable=False)
    lease_expires_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    attempt_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    last_heartbeat_at: Mapped[str] = mapped_column(String, nullable=False)
    opaque_temp_name: Mapped[str] = mapped_column(String, nullable=False)
    intent_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    blob_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reconciliation_required_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reconciled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reconciled_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "target_revision"],
            [
                "atlas_artifact_storage_targets.target_id",
                "atlas_artifact_storage_targets.target_revision",
            ],
            name="fk_atlas_write_attempt_target",
        ),
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="ux_atlas_write_attempt_idempotency"
        ),
        CheckConstraint(
            "status IN ('receiving','bytes_verified','reserved','published','succeeded','failed','quarantined')",
            name="ck_atlas_write_attempt_status",
        ),
        CheckConstraint("attempt_generation > 0", name="ck_atlas_write_attempt_generation"),
        CheckConstraint("storage_epoch >= 0", name="ck_atlas_write_attempt_epoch"),
        CheckConstraint("target_revision > 0", name="ck_atlas_write_attempt_target_revision"),
        CheckConstraint("parent_lifecycle_epoch >= 0", name="ck_atlas_write_attempt_lifecycle_epoch"),
        CheckConstraint(
            f"root_identity_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_write_attempt_root_digest",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{SHA256_SQL}'",
            name="ck_atlas_write_attempt_request_fingerprint",
        ),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_atlas_write_attempt_size"),
        CheckConstraint(
            f"checksum_sha256 IS NULL OR checksum_sha256 ~ '{SHA256_SQL}'",
            name="ck_atlas_write_attempt_checksum",
        ),
        CheckConstraint(
            "status NOT IN ('failed','quarantined') OR failure_code IS NOT NULL",
            name="ck_atlas_write_attempt_failure",
        ),
        CheckConstraint(
            "jsonb_typeof(intent_json) = 'object' AND octet_length(intent_json::text) <= 16384",
            name="ck_atlas_write_attempt_intent_allowlist_size",
        ),
        Index(
            "ix_atlas_write_attempt_reconcile",
            "status", "lease_expires_at", "reconciliation_required_at",
        ),
    )


class AtlasStorageBlobRow(_FenceColumns, OrmBase):
    __tablename__ = "atlas_storage_blobs"
    blob_id: Mapped[str] = mapped_column(String, primary_key=True)
    opaque_ref: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dedup_mode: Mapped[str] = mapped_column(String, nullable=False)
    dedup_scope_type: Mapped[str | None] = mapped_column(String, nullable=True)
    dedup_scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    checksum_algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    checksum_value: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    write_attempt_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("atlas_artifact_write_attempts.write_attempt_id"), nullable=True
    )
    committed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reconciliation_required_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reconciled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reconciled_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "target_revision"],
            [
                "atlas_artifact_storage_targets.target_id",
                "atlas_artifact_storage_targets.target_revision",
            ],
            name="fk_atlas_storage_blob_target",
        ),
        UniqueConstraint(
            "blob_id", "checksum_algorithm", "checksum_value", "byte_size", "content_type",
            name="ux_atlas_blob_identity",
        ),
        CheckConstraint(
            "status IN ('pending','committed','failed','quarantined')",
            name="ck_atlas_blob_status",
        ),
        CheckConstraint(
            "dedup_mode IN ('original','none')", name="ck_atlas_blob_dedup_mode"
        ),
        CheckConstraint(
            "(dedup_mode = 'original' AND dedup_scope_type IN ('team','project') AND dedup_scope_id IS NOT NULL) "
            "OR (dedup_mode = 'none' AND dedup_scope_type IS NULL AND dedup_scope_id IS NULL)",
            name="ck_atlas_blob_dedup_scope",
        ),
        CheckConstraint("checksum_algorithm = 'sha256'", name="ck_atlas_blob_checksum_algorithm"),
        CheckConstraint(
            f"checksum_value ~ '{SHA256_SQL}'", name="ck_atlas_blob_checksum_value"
        ),
        CheckConstraint("byte_size > 0", name="ck_atlas_blob_size"),
        CheckConstraint("target_revision > 0", name="ck_atlas_blob_target_revision"),
        CheckConstraint("storage_epoch >= 0", name="ck_atlas_blob_epoch"),
        CheckConstraint(
            f"root_identity_digest ~ '{SHA256_SQL}'", name="ck_atlas_blob_root_digest"
        ),
        CheckConstraint(
            "status <> 'pending' OR (write_attempt_id IS NOT NULL AND target_id IS NOT NULL AND target_revision > 0 AND root_identity_digest IS NOT NULL AND storage_epoch >= 0)",
            name="ck_atlas_blob_pending_fields",
        ),
        CheckConstraint(
            "status <> 'committed' OR committed_at IS NOT NULL",
            name="ck_atlas_blob_committed_at",
        ),
        CheckConstraint(
            "status NOT IN ('failed','quarantined') OR failure_code IS NOT NULL",
            name="ck_atlas_blob_failure_code",
        ),
        Index(
            "ux_atlas_blob_original_dedup",
            "dedup_scope_type", "dedup_scope_id", "checksum_algorithm", "checksum_value", "byte_size",
            unique=True,
            postgresql_where=(dedup_mode == "original"),
        ),
        Index(
            "ix_atlas_blob_operation_reconcile",
            "status", "reconciliation_required_at",
        ),
    )


class AtlasArtifactRow(OrmBase):
    __tablename__ = "atlas_artifacts"
    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    artifact_class: Mapped[str] = mapped_column(String, nullable=False, index=True)
    blob_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    checksum_algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    checksum_value: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    owner_scope_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_scope_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    logical_identity: Mapped[str] = mapped_column(String, nullable=False)
    source_artifact_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("atlas_artifacts.artifact_id", deferrable=True, initially="DEFERRED"),
        nullable=True, index=True,
    )
    document_version_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_resource_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_lifecycle_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processing_generation: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    acl_policy_version: Mapped[str | None] = mapped_column(String, nullable=True)
    acl_action: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["blob_id", "checksum_algorithm", "checksum_value", "byte_size", "content_type"],
            [
                "atlas_storage_blobs.blob_id",
                "atlas_storage_blobs.checksum_algorithm",
                "atlas_storage_blobs.checksum_value",
                "atlas_storage_blobs.byte_size",
                "atlas_storage_blobs.content_type",
            ],
            name="fk_atlas_artifact_blob_identity",
        ),
        UniqueConstraint(
            "artifact_class", "logical_identity", name="ux_atlas_artifact_logical_identity"
        ),
        CheckConstraint(
            "owner_scope_type IN ('team','project','conversation','system')",
            name="ck_atlas_artifact_owner_type",
        ),
        CheckConstraint(
            "(owner_scope_type = 'system' AND owner_scope_id IS NULL) OR (owner_scope_type <> 'system' AND owner_scope_id IS NOT NULL)",
            name="ck_atlas_artifact_owner_id",
        ),
        CheckConstraint(
            "lifecycle_status IN ('staged','active','tombstoned','failed','quarantined')",
            name="ck_atlas_artifact_lifecycle",
        ),
        CheckConstraint("checksum_algorithm = 'sha256'", name="ck_atlas_artifact_checksum_algorithm"),
        CheckConstraint(
            f"checksum_value ~ '{SHA256_SQL}'", name="ck_atlas_artifact_checksum_value"
        ),
        CheckConstraint("byte_size > 0", name="ck_atlas_artifact_size"),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object' AND octet_length(metadata_json::text) <= 65536",
            name="ck_atlas_artifact_metadata_allowlist_size",
        ),
        CheckConstraint(
            "artifact_class IN ('original_document','original_inline_source','document_page_pdf','page_image','processing_native_image','preview','conversation_turn_input','conversation_turn_answer','conversation_summary','protected_model_payload','evidence_pack')",
            name="ck_atlas_artifact_class",
        ),
        CheckConstraint(
            "artifact_class NOT IN ('document_page_pdf','page_image','processing_native_image','preview') "
            "OR (source_artifact_id IS NOT NULL AND processing_generation IS NOT NULL)",
            name="ck_atlas_artifact_derived_lineage",
        ),
        Index(
            "ux_atlas_artifact_canonical_original",
            "document_version_id",
            unique=True,
            postgresql_where=(artifact_class.in_(("original_document", "original_inline_source"))),
        ),
        Index(
            "ix_atlas_artifact_consumer_lookup",
            "parent_resource_id", "lifecycle_status", "artifact_class", "processing_generation",
        ),
    )


class AtlasArtifactScopeBindingRow(OrmBase):
    __tablename__ = "atlas_artifact_scope_bindings"
    binding_id: Mapped[str] = mapped_column(String, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String, ForeignKey("atlas_artifacts.artifact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    binding_kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "binding_kind IN ('owner','authorization','provenance','inherited','audit','source')",
            name="ck_atlas_artifact_binding_kind",
        ),
        CheckConstraint(
            "scope_type IN ('team','project','conversation','system')",
            name="ck_atlas_artifact_binding_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'system' AND scope_id IS NULL) OR (scope_type <> 'system' AND scope_id IS NOT NULL)",
            name="ck_atlas_artifact_binding_scope_id",
        ),
        Index(
            "ux_atlas_artifact_owner_binding",
            "artifact_id",
            unique=True,
            postgresql_where=(binding_kind == "owner"),
        ),
    )


class AtlasArtifactOperationRow(_FenceColumns, OrmBase):
    __tablename__ = "atlas_artifact_operations"
    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_type: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_scope: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    committed_blob_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blob_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_mode: Mapped[str] = mapped_column(String, nullable=False)
    evidence_claim: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "target_revision"],
            [
                "atlas_artifact_storage_targets.target_id",
                "atlas_artifact_storage_targets.target_revision",
            ],
            name="fk_atlas_artifact_operation_target",
        ),
        UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="ux_atlas_artifact_operation_idempotency"
        ),
        CheckConstraint(
            "status = 'succeeded'",
            name="ck_atlas_artifact_operation_status",
        ),
        CheckConstraint(
            "operation_type = 'target_configuration'",
            name="ck_atlas_artifact_operation_type",
        ),
        CheckConstraint(
            "verification_mode IS NOT NULL AND evidence_claim IS NOT NULL",
            name="ck_atlas_artifact_operation_verification_presence",
        ),
        CheckConstraint(
            "(verification_mode = 'full_hash' AND evidence_claim = 'TARGET_COPY_CHECKSUM_VERIFIED') OR "
            "(verification_mode = 'operator_accepted_unverified' AND evidence_claim = 'OPERATOR_ACCEPTED_UNVERIFIED_TARGET')",
            name="ck_atlas_artifact_operation_evidence_claim",
        ),
        CheckConstraint("target_revision > 0", name="ck_atlas_artifact_operation_target_revision"),
        CheckConstraint("storage_epoch >= 0", name="ck_atlas_artifact_operation_epoch"),
        CheckConstraint(
            "committed_blob_count >= 0 AND total_bytes >= 0",
            name="ck_atlas_artifact_operation_totals",
        ),
        CheckConstraint(
            f"blob_set_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_artifact_operation_blob_set_digest",
        ),
        CheckConstraint(
            f"root_identity_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_artifact_operation_root_digest",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{SHA256_SQL}'",
            name="ck_atlas_artifact_operation_request_fingerprint",
        ),
    )


class AtlasStorageRequestLeaseRow(_FenceColumns, OrmBase):
    __tablename__ = "atlas_storage_request_leases"
    lease_id: Mapped[str] = mapped_column(String, primary_key=True)
    request_kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    acquired_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False, index=True)
    last_heartbeat_at: Mapped[str] = mapped_column(String, nullable=False)
    attempt_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_resource_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_lifecycle_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "target_revision"],
            [
                "atlas_artifact_storage_targets.target_id",
                "atlas_artifact_storage_targets.target_revision",
            ],
            name="fk_atlas_storage_request_lease_target",
        ),
        CheckConstraint("attempt_generation > 0", name="ck_atlas_storage_request_lease_generation"),
        CheckConstraint("target_revision > 0", name="ck_atlas_storage_request_lease_target_revision"),
        CheckConstraint("storage_epoch >= 0", name="ck_atlas_storage_request_lease_epoch"),
        CheckConstraint(
            f"root_identity_digest ~ '{SHA256_SQL}'",
            name="ck_atlas_storage_request_lease_root_digest",
        ),
        CheckConstraint(
            "parent_lifecycle_epoch IS NULL OR parent_lifecycle_epoch >= 0",
            name="ck_atlas_storage_request_lease_lifecycle_epoch",
        ),
        Index("ix_atlas_storage_request_lease_drain", "expires_at", "storage_epoch"),
    )


class AtlasStorageReconciliationFindingRow(OrmBase):
    __tablename__ = "atlas_storage_reconciliation_findings"
    finding_id: Mapped[str] = mapped_column(String, primary_key=True)
    finding_kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    detected_at: Mapped[str] = mapped_column(String, nullable=False)
    safe_summary: Mapped[str] = mapped_column(String(512), nullable=False)
    blob_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    write_attempt_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    operation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reconciled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reconciled_by: Mapped[str | None] = mapped_column(String, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','resolved','quarantined')",
            name="ck_atlas_storage_reconciliation_status",
        ),
        Index("ix_atlas_storage_reconciliation_queue", "status", "detected_at"),
    )


PAYLOAD_KEYS = (
    "artifact_storage_control",
    "artifact_storage_targets",
    "artifact_write_attempts",
    "storage_blobs",
    "artifacts",
    "artifact_scope_bindings",
    "artifact_operations",
    "storage_request_leases",
    "storage_reconciliation_findings",
)

DATA_PLANE_COLLECTIONS = (
    ("artifact_write_attempts", AtlasArtifactWriteAttemptRow, "write_attempt_id"),
    ("storage_blobs", AtlasStorageBlobRow, "blob_id"),
    ("artifacts", AtlasArtifactRow, "artifact_id"),
    ("artifact_scope_bindings", AtlasArtifactScopeBindingRow, "binding_id"),
    ("storage_request_leases", AtlasStorageRequestLeaseRow, "lease_id"),
    (
        "storage_reconciliation_findings",
        AtlasStorageReconciliationFindingRow,
        "finding_id",
    ),
)


def _fence(item: dict[str, Any]) -> StorageFence:
    nested = item.pop("fence", None)
    if nested is not None:
        return StorageFence(**nested)
    return StorageFence(
        target_id=item.pop("target_id"),
        target_revision=item.pop("target_revision"),
        root_identity_digest=item.pop("root_identity_digest"),
        storage_epoch=item.pop("storage_epoch"),
    )


def _flatten(record: Any) -> dict[str, Any]:
    payload = asdict(record)
    fence = payload.pop("fence", None)
    if fence is not None:
        payload.update(fence)
    if "metadata" in payload:
        payload["metadata_json"] = payload.pop("metadata")
    if "intent" in payload:
        payload["intent_json"] = payload.pop("intent")
    return payload


def _target_payload(record: StorageTargetRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["capabilities"] = validate_typed_payload(
        record.capabilities,
        family="artifact storage target capabilities",
        allowed_fields=STORAGE_TARGET_CAPABILITY_FIELDS,
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )
    return payload


def _attempt_payload(record: ArtifactWriteAttemptRecord) -> dict[str, Any]:
    payload = _flatten(record)
    intent = record.intent
    allowed = WRITE_ATTEMPT_INTENT_FIELDS if intent else frozenset()
    payload["intent_json"] = validate_typed_payload(
        intent,
        family="artifact write attempt intent",
        allowed_fields=allowed,
        max_bytes=RUNTIME_POLICY_MAX_BYTES,
    )
    return payload


def _artifact_payload(record: ArtifactRecord) -> dict[str, Any]:
    payload = _flatten(record)
    payload["metadata_json"] = validate_typed_payload(
        record.metadata,
        family="artifact metadata",
        allowed_fields=ARTIFACT_METADATA_FIELDS,
        max_bytes=GENERAL_METADATA_MAX_BYTES,
    )
    return payload


def _decode_payload(payload: dict[str, Any]) -> dict[str, dict[Any, Any]]:
    decoded: dict[str, dict[Any, Any]] = {
        "artifact_storage_control": {
            item["control_id"]: StorageControlRecord(**item)
            for item in payload["artifact_storage_control"]
        },
        "artifact_storage_targets": {
            (item["target_id"], item["target_revision"]): StorageTargetRecord(**item)
            for item in payload["artifact_storage_targets"]
        },
        "artifact_write_attempts": {},
        "storage_blobs": {},
        "artifacts": {
            item["artifact_id"]: ArtifactRecord(**item)
            for item in payload["artifacts"]
        },
        "artifact_scope_bindings": {
            item["binding_id"]: ArtifactScopeBindingRecord(**item)
            for item in payload["artifact_scope_bindings"]
        },
        "artifact_operations": {},
        "storage_request_leases": {},
        "storage_reconciliation_findings": {
            item["finding_id"]: StorageReconciliationFindingRecord(**item)
            for item in payload["storage_reconciliation_findings"]
        },
    }
    for raw in payload["artifact_write_attempts"]:
        item = dict(raw)
        fence = _fence(item)
        record = ArtifactWriteAttemptRecord(fence=fence, **item)
        decoded["artifact_write_attempts"][record.write_attempt_id] = record
    for raw in payload["storage_blobs"]:
        item = dict(raw)
        fence = _fence(item)
        record = StorageBlobRecord(fence=fence, **item)
        decoded["storage_blobs"][record.blob_id] = record
    for raw in payload["artifact_operations"]:
        item = dict(raw)
        fence = _fence(item)
        record = ArtifactOperationRecord(fence=fence, **item)
        decoded["artifact_operations"][record.operation_id] = record
    for raw in payload["storage_request_leases"]:
        item = dict(raw)
        fence = _fence(item)
        record = StorageRequestLeaseRecord(fence=fence, **item)
        decoded["storage_request_leases"][record.lease_id] = record
    return decoded


def _row_payload(row: Any) -> dict[str, Any]:
    item = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    }
    if "metadata_json" in item:
        item["metadata"] = item.pop("metadata_json")
    if "intent_json" in item:
        item["intent"] = item.pop("intent_json")
    return item
