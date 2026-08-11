from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from atlas_production.modules.artifact_storage.records import StorageFence
from atlas_production.modules.document_intake.records import DocumentRecord
from atlas_production.modules.identity_access.records import (
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.shared.public import AuditEventRecord
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class DocumentProcessingCurrentnessConflict(RuntimeError):
    pass


class DocumentLifecycleDenied(PermissionError):
    def __init__(self, audit_event: AuditEventRecord):
        super().__init__("document lifecycle request is not authorized")
        self.audit_event = audit_event


@dataclass(frozen=True, slots=True)
class ProcessingJobRecord:
    job_id: str
    job_kind: str
    document_id: str
    document_version_id: str
    processing_generation: int | None
    index_generation_id: str
    stage: str
    status: str
    progress_current: int
    progress_total: int | None
    progress_unit: str
    attempt: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    fence: int
    failure_code: str | None
    failure_detail: str | None
    idempotency_scope: str
    idempotency_key: str
    request_fingerprint: str
    created_by: str | None
    attempt_started_at: datetime
    created_at: datetime
    updated_at: datetime
    processing_identity_id: str | None = None
    processing_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingControlResult:
    job: ProcessingJobRecord
    audit_event: AuditEventRecord


@dataclass(frozen=True, slots=True)
class ProcessingExecutionSnapshot:
    """Complete executable processing input accepted at one request boundary."""

    profile_id: str
    profile_revision: int
    profile_snapshot: dict[str, JsonValue]
    plugin_versions: tuple[dict[str, JsonValue], ...]
    plugin_packages: tuple[dict[str, JsonValue], ...]
    runtime_profiles: tuple[dict[str, JsonValue], ...]
    acceptance_request_digest: str

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or self.profile_revision <= 0:
            raise ValueError("processing execution profile identity is invalid")
        if (
            self.profile_snapshot.get("profile_id") != self.profile_id
            or self.profile_snapshot.get("revision") != self.profile_revision
            or self.profile_snapshot.get("status") != "active"
        ):
            raise ValueError("processing execution profile snapshot is mismatched")
        if not self.plugin_versions or not self.runtime_profiles:
            raise ValueError("processing execution plugin snapshot is incomplete")
        if len(self.acceptance_request_digest) != 64:
            raise ValueError("processing acceptance request digest is invalid")


@dataclass(frozen=True, slots=True)
class DocumentLifecycleProcessingAcceptance:
    """Optional processing acceptance committed with one lifecycle mutation."""

    media_type: str
    document_version_id: str
    job_kind: str
    idempotency_scope: str
    idempotency_key: str
    created_by: str | None
    execution_snapshot: ProcessingExecutionSnapshot
    progress_total: int | None = None


@dataclass(frozen=True, slots=True)
class VerifiedDocumentRestoreSet:
    """Full-hash byte-plane proof consumed only by the restore terminal commit."""

    document_id: str
    resource_lifecycle_epoch: int
    active_fence: StorageFence
    artifacts: tuple[tuple[str, str, str, int], ...]
    reusable_processing_generation: bool


@dataclass(frozen=True, slots=True)
class ProcessingJobView:
    """Consumer-facing job projection with an ephemeral batch claim token."""

    job_id: str
    job_kind: str
    document_id: str
    document_version_id: str
    processing_generation: int | None
    index_generation_id: str
    stage: str
    status: str
    progress_current: int
    progress_total: int | None
    progress_unit: str
    attempt: int
    fence: int
    failure_code: str | None
    failure_detail: str | None
    created_by: str | None
    attempt_started_at: datetime
    created_at: datetime
    updated_at: datetime
    batch_claim_token: str | None = None
    processing_identity_id: str | None = None
    processing_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingProfilePin:
    profile_id: str
    profile_revision: int


@dataclass(slots=True)
class ProcessingJobAuthorizationState:
    users: dict[str, UserRecord]
    projects: dict[str, ProjectRecord]
    teams: dict[str, TeamRecord]
    team_memberships: dict[str, TeamMembershipRecord]
    permission_grants: dict[str, PermissionGrantRecord]


@dataclass(frozen=True, slots=True)
class ProcessingJobListBatch:
    jobs: tuple[ProcessingJobView, ...]
    documents: dict[str, DocumentRecord]
    tag_refs_by_document: dict[str, tuple[tuple[str, str], ...]]
    profile_pins: dict[tuple[str, int], ProcessingProfilePin]
    authorization_state: ProcessingJobAuthorizationState


@dataclass(frozen=True, slots=True)
class DocumentJobRequestAuthorityProjection:
    """One attached document/job fact graph for a single request."""

    job: ProcessingJobView
    document: DocumentRecord
    tag_refs: tuple[tuple[str, str], ...]
    profile_pin: ProcessingProfilePin | None
    authorization_state: ProcessingJobAuthorizationState
    authenticated_actor: UserRecord
