from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import artifact_storage as artifact_rows
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.identity_access import read_session_actor
from atlas_production.infrastructure.persistence.retrieval_currentness import (
    read_effective_document_scope_with_team_ids,
)
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_owner.audit import (
    AccessDecisionWriter,
    AuditEventWriter,
)

from atlas_production.infrastructure.postgres_artifact_storage_adapter import (
    ArtifactWriteJourneyPlan,
    OfflineTargetInput,
    PortainerTargetInput,
)
from atlas_production.infrastructure.postgres_owner.artifact import (
    BeginArtifactWriteInput,
    DocumentParentCurrentness,
    FinalizeArtifactWriteInput,
    HeartbeatArtifactWriteInput,
    NewDocumentOriginalArtifactPublication,
    ProtectedArtifactOpenInput,
    TargetControlInput,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
)
from atlas_production.modules.artifact_storage.records import (
    ArtifactOperationRecord,
    ArtifactRecord,
    ArtifactScopeBindingRecord,
    ArtifactWriteAttemptRecord,
    StorageBlobRecord,
    StorageControlRecord,
    StorageFence,
    StorageRequestLeaseRecord,
    StorageTargetRecord,
    UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT,
)
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    UserRecord,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]


class ArtifactJourneyCurrentnessError(RuntimeError):
    """Typed journey facts are incomplete, stale, or cross-wired."""


@dataclass(frozen=True, slots=True)
class ProtectedOriginalFacts:
    actor: UserRecord
    presented_browser_session_token: str = field(repr=False)
    action: str
    method: str
    document: DocumentRecord
    version: DocumentVersionRecord
    tags: tuple[DocumentTagRecord, ...]
    artifact: ArtifactRecord
    blob: StorageBlobRecord
    bindings: tuple[ArtifactScopeBindingRecord, ...]
    candidate_team_ids: frozenset[str]
    can_administer_owner_scope: bool
    observed_at: str
    read_lease: StorageRequestLeaseRecord
    access_decision: AccessDecisionRecord | None
    audit_events: tuple[AuditEventRecord, ...]
    filename: str
    if_match: str | None = None
    if_none_match: str | None = None
    if_range: str | None = None
    range_header: str | None = None


@dataclass(frozen=True, slots=True)
class ProtectedOriginalJourney:
    request: ProtectedArtifactOpenInput = field(repr=False)
    method: str
    filename: str
    if_match: str | None
    if_none_match: str | None
    if_range: str | None
    range_header: str | None


@dataclass(frozen=True, slots=True)
class ProtectedOriginalJourneyBuilder:
    authority: ActionAwareAclAuthority

    def build(self, facts: ProtectedOriginalFacts) -> ProtectedOriginalJourney:
        if facts.method not in {"GET", "HEAD"} or facts.action != "read_original":
            raise ArtifactJourneyCurrentnessError("unsupported protected original action")
        document = facts.document
        artifact = facts.artifact
        blob = facts.blob
        expected_class = (
            "original_document"
            if document.source_kind == "file_upload"
            else "original_inline_source"
        )
        tag_scope = frozenset(
            (tag.tag_type, tag.tag_id)
            for tag in facts.tags
            if tag.document_id == document.document_id
            and tag.tag_type in {"team", "project"}
        )
        binding_scope = {
            (binding.scope_type, binding.scope_id)
            for binding in facts.bindings
            if binding.artifact_id == artifact.artifact_id
            and binding.binding_kind in {"owner", "authorization"}
            and binding.scope_id is not None
        }
        if (
            not facts.actor.active
            or document.lifecycle_status != "active"
            or document.original_artifact_id != artifact.artifact_id
            or facts.version.document_id != document.document_id
            or facts.version.status != "active"
            or facts.version.original_artifact_id != artifact.artifact_id
            or artifact.artifact_class != expected_class
            or artifact.lifecycle_status != "active"
            or artifact.document_version_id != facts.version.document_version_id
            or artifact.parent_resource_id != document.document_id
            or artifact.parent_lifecycle_epoch != document.resource_lifecycle_epoch
            or artifact.owner_scope_type != document.scope_type
            or artifact.owner_scope_id != document.scope_id
            or artifact.blob_id != blob.blob_id
            or blob.status != "committed"
            or blob.fence != facts.read_lease.fence
            or blob.checksum_algorithm != "sha256"
            or artifact.checksum_value != blob.checksum_value
            or artifact.byte_size != blob.byte_size
            or artifact.content_type != blob.content_type
            or facts.version.content_type != blob.content_type
            or not tag_scope
            or not binding_scope
        ):
            raise ArtifactJourneyCurrentnessError(
                "protected original facts are not current"
            )
        allowed_scope = self.authority.effective_document_scope(
            actor_type=facts.actor.actor_type,
            actor_id=facts.actor.actor_id,
            action=facts.action,
        )
        visible_scope = frozenset(
            tag_scope.intersection(allowed_scope).intersection(binding_scope)
        )
        policy_denial_reason = (
            "source_download_restricted"
            if document.source_download_restricted
            else (
                "member_download_policy"
                if not document.allow_member_download
                and not facts.can_administer_owner_scope
                else None
            )
        )
        policy_allowed = policy_denial_reason is None
        allowed = bool(visible_scope) and policy_allowed
        candidate_scope = visible_scope if visible_scope else tag_scope
        decision = facts.access_decision
        if allowed and facts.method == "HEAD":
            if decision is not None or facts.audit_events:
                raise ArtifactJourneyCurrentnessError(
                    "successful HEAD must not include success evidence"
                )
        elif (
            decision is None
            or not facts.audit_events
            or decision.allowed != allowed
            or decision.actor_type != facts.actor.actor_type
            or decision.actor_id != facts.actor.actor_id
            or decision.action != facts.action
            or (
                policy_denial_reason is not None
                and decision.reason != policy_denial_reason
            )
        ):
            raise ArtifactJourneyCurrentnessError(
                "protected original decision evidence is incomplete"
            )
        request = ProtectedArtifactOpenInput(
            expected_document=document,
            expected_version=facts.version,
            expected_tag_refs=tag_scope,
            expected_artifact=artifact,
            expected_blob=blob,
            actor_type=facts.actor.actor_type,
            actor_id=facts.actor.actor_id,
            presented_browser_session_token=facts.presented_browser_session_token,
            action=facts.action,
            record_success_evidence=facts.method == "GET",
            candidate_scope=candidate_scope,
            candidate_team_ids=facts.candidate_team_ids,
            expected_can_administer_owner_scope=(
                facts.can_administer_owner_scope
            ),
            access_decision=decision,
            audit_events=facts.audit_events,
            observed_at=facts.observed_at,
            read_lease=facts.read_lease,
        )
        return ProtectedOriginalJourney(
            request=request,
            method=facts.method,
            filename=facts.filename,
            if_match=facts.if_match,
            if_none_match=facts.if_none_match,
            if_range=facts.if_range,
            range_header=facts.range_header,
        )


class ProtectedOriginalUnavailable(LookupError):
    """The request-bounded original-document graph does not exist."""

    def __init__(self, message: str, audit_event_ref: str | None = None) -> None:
        super().__init__(message)
        self.audit_event_ref = audit_event_ref


class ProtectedOriginalUnauthenticated(PermissionError):
    """The presented browser session does not resolve to a current actor."""


@dataclass(frozen=True, slots=True)
class ProtectedOriginalPreimageDenialCommand:
    """Commit one authenticated preimage denial before returning not-found."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        actor: UserRecord,
        document: DocumentRecord | None,
        artifact_id: str | None,
        reason: str,
    ) -> AuditEventRecord:
        observed_at = utc_now_iso()
        decision = AccessDecisionRecord(
            decision_id=f"access-{uuid4().hex}",
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            project_id=(
                document.scope_id
                if document is not None and document.scope_type == "project"
                else None
            ),
            action="read_original",
            required_role="viewer",
            allowed=False,
            reason=reason,
            effective_role=None,
            source_type=None,
            source_id=None,
            explanation="Original-content preimage was incomplete or unavailable.",
            created_at=observed_at,
            scope_type=document.scope_type if document is not None else None,
            scope_id=document.scope_id if document is not None else None,
        )
        audit = build_audit_event(
            event_type="document_original_content_denied",
            actor_id=actor.actor_id,
            target_ref=(
                f"artifact:{artifact_id}"
                if artifact_id
                else "document-content:unavailable"
            ),
            project_id=decision.project_id,
            message_code="document.was_not_found",
            metadata={
                "reason": reason,
                "access_decision_id": decision.decision_id,
            },
            scope_type=decision.scope_type,
            scope_id=decision.scope_id,
            document_id=document.document_id if document is not None else None,
        )
        session = self.session_factory()
        with session:
            try:
                AccessDecisionWriter(session).append(decision)
                AuditEventWriter(session).append_many((audit,))
                session.commit()
                return audit
            except Exception:
                session.rollback()
                raise


def _row_record(row: object, record_type):
    return record_type(
        **{
            name: getattr(row, name)
            for name in record_type.__dataclass_fields__
        }
    )


@dataclass(frozen=True, slots=True)
class PostgresProtectedOriginalJourneyProvider:
    """Load one exact original-content preimage without hydrating an aggregate.

    The returned facts are deliberately only an optimistic request-boundary
    preimage. ``ProtectedArtifactOpenCommand`` remains authoritative: it locks,
    rereads, records the decision/audit/lease, and commits before byte I/O.
    """

    session_factory: SessionFactory
    lease_seconds: int = 90

    def build(self, **kwargs) -> ProtectedOriginalJourney:
        facts = self.load(**kwargs)
        return ProtectedOriginalJourneyBuilder(
            ActionAwareAclAuthority(self.session_factory)
        ).build(facts)

    def _unavailable(
        self,
        *,
        actor: UserRecord,
        document: DocumentRecord | None,
        artifact_id: str | None,
        reason: str,
    ) -> None:
        audit = ProtectedOriginalPreimageDenialCommand(
            self.session_factory
        ).execute(
            actor=actor,
            document=document,
            artifact_id=artifact_id,
            reason=reason,
        )
        raise ProtectedOriginalUnavailable(
            "document original is unavailable",
            audit_event_ref=audit.event_id,
        )

    def load(
        self,
        *,
        document_id: str,
        presented_browser_session_token: str,
        method: str,
        if_match: str | None = None,
        if_none_match: str | None = None,
        if_range: str | None = None,
        range_header: str | None = None,
    ) -> ProtectedOriginalFacts:
        if method not in {"GET", "HEAD"}:
            raise ValueError("protected original supports only GET or HEAD")
        observed = datetime.now(timezone.utc)
        observed_at = observed.isoformat()
        with self.session_factory() as session:
            actor = read_session_actor(session, presented_browser_session_token)
            if actor is None:
                raise ProtectedOriginalUnauthenticated(
                    "browser session is not authenticated"
                )
            document_row = session.get(AtlasDocumentRow, document_id)
            if document_row is None:
                self._unavailable(
                    actor=actor,
                    document=None,
                    artifact_id=None,
                    reason="document_unavailable",
                )
            document = _row_record(document_row, DocumentRecord)
            if not document_row.original_artifact_id:
                self._unavailable(
                    actor=actor,
                    document=document,
                    artifact_id=None,
                    reason="source_unavailable",
                )
            version_rows = session.scalars(
                select(AtlasDocumentVersionRow).where(
                    AtlasDocumentVersionRow.document_id == document_id,
                )
            ).all()
            matching_versions = [
                row
                for row in version_rows
                if row.payload.get("status") == "active"
                and row.payload.get("original_artifact_id")
                == document_row.original_artifact_id
            ]
            if len(matching_versions) != 1:
                self._unavailable(
                    actor=actor,
                    document=document,
                    artifact_id=document_row.original_artifact_id,
                    reason="document_version_unavailable",
                )
            version_row = matching_versions[0]
            artifact_row = session.get(
                artifact_rows.AtlasArtifactRow,
                document_row.original_artifact_id,
            )
            if artifact_row is None:
                self._unavailable(
                    actor=actor,
                    document=document,
                    artifact_id=document_row.original_artifact_id,
                    reason="source_unavailable",
                )
            blob_row = session.get(
                artifact_rows.AtlasStorageBlobRow,
                artifact_row.blob_id,
            )
            if blob_row is None:
                self._unavailable(
                    actor=actor,
                    document=document,
                    artifact_id=artifact_row.artifact_id,
                    reason="source_unavailable",
                )
            tag_rows = session.scalars(
                select(AtlasDocumentTagRow).where(
                    AtlasDocumentTagRow.document_id == document_id
                )
            ).all()
            binding_rows = session.scalars(
                select(artifact_rows.AtlasArtifactScopeBindingRow).where(
                    artifact_rows.AtlasArtifactScopeBindingRow.artifact_id
                    == artifact_row.artifact_id
                )
            ).all()

            version = DocumentVersionRecord(**dict(version_row.payload))
            artifact = _row_record(artifact_row, ArtifactRecord)
            blob = StorageBlobRecord(
                blob_id=blob_row.blob_id,
                opaque_ref=blob_row.opaque_ref,
                status=blob_row.status,
                dedup_mode=blob_row.dedup_mode,
                dedup_scope_type=blob_row.dedup_scope_type,
                dedup_scope_id=blob_row.dedup_scope_id,
                checksum_algorithm=blob_row.checksum_algorithm,
                checksum_value=blob_row.checksum_value,
                byte_size=blob_row.byte_size,
                content_type=blob_row.content_type,
                write_attempt_id=blob_row.write_attempt_id,
                committed_at=blob_row.committed_at,
                failure_code=blob_row.failure_code,
                failure_detail_summary=blob_row.failure_detail_summary,
                reconciliation_required_at=blob_row.reconciliation_required_at,
                reconciled_at=blob_row.reconciled_at,
                reconciled_by=blob_row.reconciled_by,
                fence=StorageFence(
                    blob_row.target_id,
                    blob_row.target_revision,
                    blob_row.root_identity_digest,
                    blob_row.storage_epoch,
                ),
                created_at=blob_row.created_at,
                updated_at=blob_row.updated_at,
            )
            tags = tuple(
                DocumentTagRecord(
                    row.document_id, row.tag_type, row.tag_id, row.created_at
                )
                for row in tag_rows
            )
            bindings = tuple(
                _row_record(row, ArtifactScopeBindingRecord)
                for row in binding_rows
            )
            tag_scope = {
                (tag.tag_type, tag.tag_id)
                for tag in tags
                if tag.tag_type in {"team", "project"}
            }
            binding_scope = {
                (binding.scope_type, binding.scope_id)
                for binding in bindings
                if binding.binding_kind in {"owner", "authorization"}
                and binding.scope_id is not None
            }
            requested_scope = tag_scope.intersection(binding_scope)
            if not tag_scope or not requested_scope:
                self._unavailable(
                    actor=actor,
                    document=document,
                    artifact_id=artifact.artifact_id,
                    reason="authorization_binding_unavailable",
                )
            (
                resolved_scope,
                team_ids,
                can_administer_owner_scope,
            ) = read_effective_document_scope_with_team_ids(
                session,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                requested_scope=requested_scope or tag_scope,
                owner_scope_type=document.scope_type,
                owner_scope_id=document.scope_id,
            )
            visible_scope = requested_scope.intersection(resolved_scope)
            policy_reason = (
                "source_download_restricted"
                if document.source_download_restricted
                else "member_download_policy"
                if not document.allow_member_download
                and not can_administer_owner_scope
                else None
            )
            allowed = bool(visible_scope) and policy_reason is None
            reason = policy_reason or ("authorized_scope" if allowed else "scope_missing")
            evidence_required = method == "GET" or not allowed
            decision = None
            audit_events: tuple[AuditEventRecord, ...] = ()
            if evidence_required:
                chosen_scope = sorted(visible_scope or requested_scope)[0]
                decision = AccessDecisionRecord(
                    decision_id=f"access-{uuid4().hex}",
                    actor_type=actor.actor_type,
                    actor_id=actor.actor_id,
                    project_id=(
                        document.scope_id if document.scope_type == "project" else None
                    ),
                    action="read_original",
                    required_role="viewer",
                    allowed=allowed,
                    reason=reason,
                    effective_role="viewer" if allowed else None,
                    source_type=chosen_scope[0] if allowed else None,
                    source_id=chosen_scope[1] if allowed else None,
                    explanation="Request-boundary original-content authorization.",
                    created_at=observed_at,
                    scope_type=document.scope_type,
                    scope_id=document.scope_id,
                )
                audit_events = (
                    build_audit_event(
                        event_type=(
                            "document_original_content_read_authorized"
                            if allowed
                            else "document_original_content_denied"
                        ),
                        actor_id=actor.actor_id,
                        target_ref=f"artifact:{artifact.artifact_id}",
                        project_id=decision.project_id,
                        scope_type=document.scope_type,
                        scope_id=document.scope_id,
                        document_id=document.document_id,
                        message_code=(
                            "document.original_content_access_was_authorized"
                            if allowed
                            else "document.was_not_found"
                        ),
                        metadata={
                            "operation": "direct_original_content",
                            "reason": reason,
                            "access_decision_id": decision.decision_id,
                        },
                    ),
                )
            lease = StorageRequestLeaseRecord(
                lease_id=f"lease-{uuid4().hex}",
                request_kind="artifact_read",
                owner=f"api:{actor.actor_id}",
                fence=blob.fence,
                acquired_at=observed_at,
                expires_at=(observed + timedelta(seconds=self.lease_seconds)).isoformat(),
                last_heartbeat_at=observed_at,
                attempt_generation=1,
                parent_resource_id=document.document_id,
                parent_lifecycle_epoch=artifact.parent_lifecycle_epoch,
            )
            return ProtectedOriginalFacts(
                actor=actor,
                presented_browser_session_token=presented_browser_session_token,
                action="read_original",
                method=method,
                document=document,
                version=version,
                tags=tags,
                artifact=artifact,
                blob=blob,
                bindings=bindings,
                candidate_team_ids=frozenset(team_ids),
                can_administer_owner_scope=can_administer_owner_scope,
                observed_at=observed_at,
                read_lease=lease,
                access_decision=decision,
                audit_events=audit_events,
                filename=document.source_filename or document.title,
                if_match=if_match,
                if_none_match=if_none_match,
                if_range=if_range,
                range_header=range_header,
            )


@dataclass(frozen=True, slots=True)
class ArtifactUploadFacts:
    write_attempt_id: str
    lease_id: str
    idempotency_scope: str
    idempotency_key: str
    request_fingerprint: str
    fence: StorageFence
    parent_resource_id: str
    parent_lifecycle_epoch: int
    worker_id: str
    lease_expires_at: str
    observed_at: str
    opaque_temp_name: str
    artifact_id: str
    blob_id: str
    opaque_ref: str
    logical_identity: str
    document_version_id: str
    content_type: str
    owner_scope_type: str
    owner_scope_id: str
    authorization_bindings: tuple[tuple[str, str], ...]
    owner_binding_id: str
    authorization_binding_ids: tuple[str, ...]
    chunks: Iterable[bytes] = field(repr=False)
    max_bytes: int
    begin_audit_events: tuple[AuditEventRecord, ...]
    finalize_audit_events: tuple[AuditEventRecord, ...]
    expected_parent: DocumentParentCurrentness
    processing_generation: int | None = None
    pipeline_id: str | None = None
    pipeline_version: str | None = None
    generation: int | None = None
    page_number: int | None = None
    block_id: str | None = None
    acl_policy_version: str | None = None
    acl_action: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactUploadJourney:
    plan: ArtifactWriteJourneyPlan
    attempt: ArtifactWriteAttemptRecord
    lease: StorageRequestLeaseRecord

    def heartbeat(
        self,
        *,
        observed_at: str,
        expires_at: str,
    ) -> HeartbeatArtifactWriteInput:
        attempt = replace(
            self.attempt,
            lease_expires_at=expires_at,
            last_heartbeat_at=observed_at,
            updated_at=observed_at,
        )
        lease = replace(
            self.lease,
            expires_at=expires_at,
            last_heartbeat_at=observed_at,
        )
        return HeartbeatArtifactWriteInput(
            expected_attempt=self.attempt,
            expected_lease=self.lease,
            attempt=attempt,
            lease=lease,
            observed_at=observed_at,
        )


@dataclass(frozen=True, slots=True)
class ArtifactUploadJourneyBuilder:
    def build(self, facts: ArtifactUploadFacts) -> ArtifactUploadJourney:
        if (
            facts.owner_scope_type not in {"team", "project"}
            or not facts.owner_scope_id
            or facts.max_bytes <= 0
            or len(facts.authorization_bindings)
            != len(facts.authorization_binding_ids)
            or (facts.owner_scope_type, facts.owner_scope_id)
            not in facts.authorization_bindings
            or facts.expected_parent.document_id != facts.parent_resource_id
            or facts.expected_parent.lifecycle_status != "active"
            or facts.expected_parent.resource_lifecycle_epoch
            != facts.parent_lifecycle_epoch
            or (
                facts.processing_generation is not None
                and facts.expected_parent.active_processing_generation
                != facts.processing_generation
            )
        ):
            raise ArtifactJourneyCurrentnessError("upload authority facts are invalid")
        intent = {
            "artifact_class": "original_document",
            "logical_identity": facts.logical_identity,
            "content_type": facts.content_type,
            "owner_scope_type": facts.owner_scope_type,
            "owner_scope_id": facts.owner_scope_id,
            "document_version_id": facts.document_version_id,
            "source_artifact_id": None,
            "processing_generation": facts.processing_generation,
            "pipeline_id": facts.pipeline_id,
            "pipeline_version": facts.pipeline_version,
            "generation": facts.generation,
            "page_number": facts.page_number,
            "block_id": facts.block_id,
            "acl_policy_version": facts.acl_policy_version,
            "acl_action": facts.acl_action,
            "authorization_bindings": [
                list(item) for item in facts.authorization_bindings
            ],
            "allowed_parent_statuses": ["active"],
        }
        attempt = ArtifactWriteAttemptRecord(
            write_attempt_id=facts.write_attempt_id,
            idempotency_scope=facts.idempotency_scope,
            idempotency_key=facts.idempotency_key,
            request_fingerprint=facts.request_fingerprint,
            fence=facts.fence,
            parent_resource_id=facts.parent_resource_id,
            parent_lifecycle_epoch=facts.parent_lifecycle_epoch,
            status="receiving",
            lease_owner=facts.worker_id,
            lease_expires_at=facts.lease_expires_at,
            attempt_generation=1,
            last_heartbeat_at=facts.observed_at,
            opaque_temp_name=facts.opaque_temp_name,
            created_at=facts.observed_at,
            updated_at=facts.observed_at,
            intent=intent,
        )
        lease = StorageRequestLeaseRecord(
            lease_id=facts.lease_id,
            request_kind="artifact_write",
            owner=facts.worker_id,
            fence=facts.fence,
            acquired_at=facts.observed_at,
            expires_at=facts.lease_expires_at,
            last_heartbeat_at=facts.observed_at,
            attempt_generation=1,
            parent_resource_id=facts.parent_resource_id,
            parent_lifecycle_epoch=facts.parent_lifecycle_epoch,
        )

        def finalize(
            size: int,
            digest: str,
            expected_attempt: ArtifactWriteAttemptRecord,
            expected_lease: StorageRequestLeaseRecord,
            timestamp: str,
        ) -> FinalizeArtifactWriteInput:
            blob = StorageBlobRecord(
                blob_id=facts.blob_id,
                opaque_ref=facts.opaque_ref,
                status="committed",
                dedup_mode="original",
                dedup_scope_type=facts.owner_scope_type,
                dedup_scope_id=facts.owner_scope_id,
                checksum_algorithm="sha256",
                checksum_value=digest,
                byte_size=size,
                content_type=facts.content_type,
                fence=facts.fence,
                created_at=timestamp,
                updated_at=timestamp,
                write_attempt_id=facts.write_attempt_id,
                committed_at=timestamp,
            )
            final_attempt = replace(
                expected_attempt,
                status="succeeded",
                blob_id=blob.blob_id,
                byte_size=size,
                checksum_sha256=digest,
                updated_at=timestamp,
            )
            artifact = ArtifactRecord(
                artifact_id=facts.artifact_id,
                artifact_class="original_document",
                blob_id=blob.blob_id,
                checksum_algorithm="sha256",
                checksum_value=digest,
                byte_size=size,
                content_type=facts.content_type,
                owner_scope_type=facts.owner_scope_type,
                owner_scope_id=facts.owner_scope_id,
                lifecycle_status="active",
                created_at=timestamp,
                updated_at=timestamp,
                logical_identity=facts.logical_identity,
                document_version_id=facts.document_version_id,
                parent_resource_id=facts.parent_resource_id,
                parent_lifecycle_epoch=facts.parent_lifecycle_epoch,
                processing_generation=facts.processing_generation,
                pipeline_id=facts.pipeline_id,
                pipeline_version=facts.pipeline_version,
                generation=facts.generation,
                page_number=facts.page_number,
                block_id=facts.block_id,
                acl_policy_version=facts.acl_policy_version,
                acl_action=facts.acl_action,
            )
            bindings = (
                ArtifactScopeBindingRecord(
                    binding_id=facts.owner_binding_id,
                    artifact_id=facts.artifact_id,
                    binding_kind="owner",
                    scope_type=facts.owner_scope_type,
                    scope_id=facts.owner_scope_id,
                    created_at=timestamp,
                ),
                *(
                    ArtifactScopeBindingRecord(
                        binding_id=binding_id,
                        artifact_id=facts.artifact_id,
                        binding_kind="authorization",
                        scope_type=scope_type,
                        scope_id=scope_id,
                        created_at=timestamp,
                    )
                    for binding_id, (scope_type, scope_id) in zip(
                        facts.authorization_binding_ids,
                        facts.authorization_bindings,
                        strict=True,
                    )
                ),
            )
            return FinalizeArtifactWriteInput(
                expected_attempt=expected_attempt,
                expected_lease=expected_lease,
                expected_parent=facts.expected_parent,
                attempt=final_attempt,
                blob=blob,
                artifact=artifact,
                bindings=bindings,
                audit_events=facts.finalize_audit_events,
            )

        return ArtifactUploadJourney(
            plan=ArtifactWriteJourneyPlan(
                begin=BeginArtifactWriteInput(
                    attempt,
                    lease,
                    facts.begin_audit_events,
                ),
                chunks=facts.chunks,
                max_bytes=facts.max_bytes,
                finalize=finalize,
            ),
            attempt=attempt,
            lease=lease,
        )

    @staticmethod
    def caller_session_publication(
        finalized: FinalizeArtifactWriteInput,
        *,
        verified_tag_scopes: frozenset[tuple[str, str]],
        existing_blob: StorageBlobRecord | None = None,
    ) -> NewDocumentOriginalArtifactPublication:
        blob = existing_blob or finalized.blob
        attempt = replace(
            finalized.attempt,
            blob_id=blob.blob_id,
            byte_size=blob.byte_size,
            checksum_sha256=blob.checksum_value,
        )
        artifact = replace(
            finalized.artifact,
            blob_id=blob.blob_id,
            byte_size=blob.byte_size,
            checksum_value=blob.checksum_value,
            content_type=blob.content_type,
        )
        return NewDocumentOriginalArtifactPublication(
            fence=attempt.fence,
            expected_attempt=finalized.expected_attempt,
            expected_lease=finalized.expected_lease,
            attempt=attempt,
            blob=blob,
            artifact=artifact,
            bindings=finalized.bindings,
            verified_tag_scopes=verified_tag_scopes,
            reuse_committed_blob=existing_blob is not None,
        )


def _blob_set_digest(blobs: tuple[StorageBlobRecord, ...]) -> str:
    payload = json.dumps(
        [
            [
                blob.blob_id,
                blob.opaque_ref,
                blob.checksum_algorithm,
                blob.checksum_value,
                blob.byte_size,
            ]
            for blob in sorted(blobs, key=lambda item: item.blob_id)
        ],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactTargetFacts:
    expected_control: StorageControlRecord | None
    committed_blobs: tuple[StorageBlobRecord, ...]
    target_id: str
    target_revision: int
    target_kind: str
    masked_label: str
    config_key: str
    root_identity_digest: str
    capabilities: dict[str, bool]
    created_by: str
    operation_id: str
    idempotency_scope: str
    idempotency_key: str
    request_fingerprint: str
    observed_at: str
    audit_events: tuple[AuditEventRecord, ...]
    verification_mode: str
    evidence_claim: str


@dataclass(frozen=True, slots=True)
class ArtifactTargetJourneyBuilder:
    @staticmethod
    def _command(facts: ArtifactTargetFacts) -> TargetControlInput:
        expected_epoch = (
            1
            if facts.expected_control is None
            else facts.expected_control.storage_epoch + 1
        )
        fence = StorageFence(
            facts.target_id,
            facts.target_revision,
            facts.root_identity_digest,
            expected_epoch,
        )
        target = StorageTargetRecord(
            target_id=facts.target_id,
            target_revision=facts.target_revision,
            target_kind=facts.target_kind,  # type: ignore[arg-type]
            masked_label=facts.masked_label,
            config_key=facts.config_key,
            root_identity_digest=facts.root_identity_digest,
            capabilities=facts.capabilities,
            status="active",
            created_at=facts.observed_at,
            updated_at=facts.observed_at,
            created_by=facts.created_by,
            verification_mode=facts.verification_mode,  # type: ignore[arg-type]
            evidence_claim=facts.evidence_claim,  # type: ignore[arg-type]
        )
        control = StorageControlRecord(
            mode="active",
            active_target_id=facts.target_id,
            active_target_revision=facts.target_revision,
            root_identity_digest=facts.root_identity_digest,
            storage_epoch=expected_epoch,
            updated_at=facts.observed_at,
        )
        operation = ArtifactOperationRecord(
            operation_id=facts.operation_id,
            operation_type="target_configuration",
            idempotency_scope=facts.idempotency_scope,
            idempotency_key=facts.idempotency_key,
            request_fingerprint=facts.request_fingerprint,
            status="succeeded",
            fence=fence,
            created_at=facts.observed_at,
            updated_at=facts.observed_at,
            verification_mode=facts.verification_mode,  # type: ignore[arg-type]
            evidence_claim=facts.evidence_claim,  # type: ignore[arg-type]
            committed_blob_count=len(facts.committed_blobs),
            total_bytes=sum(blob.byte_size for blob in facts.committed_blobs),
            blob_set_digest=_blob_set_digest(facts.committed_blobs),
        )
        return TargetControlInput(
            expected_control=facts.expected_control,
            expected_committed_blobs=tuple(
                sorted(facts.committed_blobs, key=lambda item: item.blob_id)
            ),
            target=target,
            control=control,
            operation=operation,
            audit_events=facts.audit_events,
            observed_at=facts.observed_at,
        )

    def offline(self, facts: ArtifactTargetFacts) -> OfflineTargetInput:
        if (
            facts.verification_mode != "full_hash"
            or facts.evidence_claim != "TARGET_COPY_CHECKSUM_VERIFIED"
        ):
            raise ArtifactJourneyCurrentnessError(
                "offline target requires full hash evidence"
            )
        command = self._command(facts)
        return OfflineTargetInput(command, command.expected_committed_blobs)

    def portainer(
        self,
        facts: ArtifactTargetFacts,
        *,
        generation_prefix: str,
        switch_mode: str,
        risk_acknowledgement: str | None,
    ) -> PortainerTargetInput:
        if (
            not generation_prefix
            or facts.target_id
            != f"{generation_prefix}{facts.target_revision}"
            or switch_mode != "explicit"
            or (
                facts.verification_mode == "operator_accepted_unverified"
                and risk_acknowledgement
                != UNVERIFIED_TARGET_RISK_ACKNOWLEDGEMENT
            )
        ):
            raise ArtifactJourneyCurrentnessError(
                "Portainer target generation or evidence is invalid"
            )
        command = replace(
            self._command(facts),
            generation_prefix=generation_prefix,
            monotonic_generation=facts.target_revision,
        )
        return PortainerTargetInput(
            command=command,
            committed_blobs=command.expected_committed_blobs,
            generation=facts.target_revision,
            generation_prefix=generation_prefix,
            switch_mode=switch_mode,
            risk_acknowledgement=risk_acknowledgement,
        )


__all__ = [
    "ArtifactJourneyCurrentnessError",
    "ArtifactTargetFacts",
    "ArtifactTargetJourneyBuilder",
    "ArtifactUploadFacts",
    "ArtifactUploadJourney",
    "ArtifactUploadJourneyBuilder",
    "ProtectedOriginalFacts",
    "ProtectedOriginalJourney",
    "ProtectedOriginalJourneyBuilder",
    "PostgresProtectedOriginalJourneyProvider",
    "ProtectedOriginalUnauthenticated",
    "ProtectedOriginalUnavailable",
]
