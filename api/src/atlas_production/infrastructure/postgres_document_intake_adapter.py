"""Bounded PostgreSQL adapter for the public Document Intake port.

The adapter is deliberately not a unit of work.  Cross-family journeys (most
notably a new upload) use their named owner command instead of composing these
individually committing compatibility methods.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasAuditEventRow,
)
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.async_processing import (
    AtlasProcessingJobRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
    read_session_actor,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidenceRow,
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_locks import acquire_mixed_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AccessDecisionWriter
from atlas_production.infrastructure.postgres_owner.document_processing import (
    DocumentLifecycleMutationCommand,
    DocumentLifecycleProcessingAcceptance,
    ProcessingJobRecord,
    ProcessingJobAuthorizationState,
    SessionFactory,
    VerifiedDocumentRestoreSet,
)
from atlas_production.infrastructure.postgres_owner.lock_keys import (
    identity_actor_owner_key,
    project_acl_subject_owner_key,
    project_owner_key,
    team_owner_key,
    team_subject_owner_key,
)
from atlas_production.infrastructure.postgres_owner.project import (
    ActionAwareAclAuthority,
)
from atlas_production.modules.audit.public import safe_audit_metadata
from atlas_production.modules.document_intake.api_models import DocumentTagRef
from atlas_production.modules.document_intake.contracts import DocumentAuditCommand
from atlas_production.modules.document_intake.records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    PermissionGrantRecord,
    TeamMembershipRecord,
    TeamRecord,
    UserRecord,
)
from atlas_production.modules.project_governance.records import ProjectRecord
from atlas_production.rbac import (
    ACTION_REQUIRED_ROLE,
    TEAM_ROLE_ORDER,
    direct_team_role,
    effective_document_scope,
    is_system_admin,
    resolve_access,
    team_role_covers,
)
from atlas_production.modules.document_intake.formats import (
    source_allows_original_download,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


def _document_record(row: AtlasDocumentRow) -> DocumentRecord:
    return DocumentRecord(
        **{name: getattr(row, name) for name in DocumentRecord.__dataclass_fields__}
    )


def _version_record(row: AtlasDocumentVersionRow) -> DocumentVersionRecord:
    record = DocumentVersionRecord(**dict(row.payload))
    if (
        record.document_version_id != row.document_version_id
        or record.document_id != row.document_id
    ):
        raise ValueError("document version row identity is inconsistent")
    return record


def _audit_record(row: AtlasAuditEventRow) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=row.event_id,
        event_type=row.event_type,
        actor_id=row.actor_id,
        target_ref=row.target_ref,
        project_id=row.project_id,
        message_code=row.message_code,
        message_params=row.message_params,
        metadata=row.event_metadata,
        created_at=row.created_at,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        document_id=row.document_id,
    )


def _audit_event(command: DocumentAuditCommand) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"audit-{uuid4().hex}",
        event_type=command.event_type,
        actor_id=command.actor_id,
        target_ref=command.target_ref,
        project_id=command.project_id,
        message_code=command.message_code,
        message_params=command.message_params,
        metadata=safe_audit_metadata(command.metadata),
        created_at=utc_now_iso(),
        scope_type=command.scope_type,
        scope_id=command.scope_id,
        document_id=command.document_id,
    )


@dataclass(frozen=True, slots=True)
class DocumentLibraryItemProjection:
    """All mutable SQL facts used to render one library item in one request."""

    document: DocumentRecord
    tags: tuple[DocumentTagRecord, ...]
    scope_labels: tuple[tuple[str, str, str], ...]
    ready_evidence_count: int
    original_artifact_available: bool
    can_view: bool
    can_administer: bool
    can_edit: bool
    can_view_logs: bool
    download_available: bool
    events: tuple[AuditEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentLibraryRequestProjection:
    authenticated_actor: UserRecord
    items: tuple[DocumentLibraryItemProjection, ...]
    authorization_state: ProcessingJobAuthorizationState


@dataclass(frozen=True, slots=True)
class RequestedDocumentScopeProjection:
    scope_type: str
    scope_id: str
    exists: bool
    active: bool
    label: str | None
    can_upload: bool
    denial_audit_event: AuditEventRecord | None = None


@dataclass(frozen=True, slots=True)
class DocumentLifecycleRequestInput:
    presented_browser_session_token: str
    actor_type: str
    actor_id: str
    expected_document: DocumentRecord
    document: DocumentRecord
    tags: tuple[DocumentTagRecord, ...]
    audit_events: tuple[AuditEventRecord, ...]
    denial_audit_event: AuditEventRecord
    versions: tuple[DocumentVersionRecord, ...] = ()
    processing_acceptance: DocumentLifecycleProcessingAcceptance | None = None
    restore_verification: VerifiedDocumentRestoreSet | None = None


@dataclass(frozen=True, slots=True)
class DocumentIntakeJourneyFacade:
    """Route-facing named document mutations; never buffers an aggregate."""

    session_factory: SessionFactory

    def _apply(
        self,
        request: DocumentLifecycleRequestInput,
        *,
        control_action: str,
    ) -> ProcessingJobRecord | None:
        return DocumentLifecycleMutationCommand(self.session_factory).execute(
            expected_document=request.expected_document,
            document=request.document,
            versions=request.versions,
            tags=request.tags,
            audit_events=request.audit_events,
            processing_acceptance=request.processing_acceptance,
            presented_browser_session_token=request.presented_browser_session_token,
            expected_actor_type=request.actor_type,
            expected_actor_id=request.actor_id,
            control_action=control_action,
            denial_audit_event=request.denial_audit_event,
            restore_verification=request.restore_verification,
        )

    def patch_document(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None:
        changes_download_policy = (
            request.expected_document.allow_member_download
            != request.document.allow_member_download
        )
        return self._apply(
            request,
            control_action="admin" if changes_download_policy else "edit",
        )

    def disable_document(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None:
        return self._apply(request, control_action="admin")

    def begin_restore(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None:
        return self._apply(request, control_action="admin")

    def finish_restore(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord | None:
        return self._apply(request, control_action="admin")

    def refresh_or_reindex(
        self, request: DocumentLifecycleRequestInput
    ) -> ProcessingJobRecord:
        job = self._apply(request, control_action="edit")
        if job is None:
            raise RuntimeError("processing acceptance did not create a job")
        return job


def _authorization_state(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    scope_bindings: tuple[tuple[str, str], ...],
) -> ProcessingJobAuthorizationState:
    actor_row = session.get(AtlasUserRow, actor_id)
    memberships = session.scalars(
        select(AtlasTeamMembershipRow).where(
            AtlasTeamMembershipRow.member_actor_type == actor_type,
            AtlasTeamMembershipRow.member_actor_id == actor_id,
        )
    ).all()
    unresolved = {row.team_id for row in memberships} | {
        scope_id for scope_type, scope_id in scope_bindings if scope_type == "team"
    }
    teams: dict[str, AtlasTeamRow] = {}
    while unresolved:
        rows = session.scalars(
            select(AtlasTeamRow).where(AtlasTeamRow.team_id.in_(unresolved))
        ).all()
        for row in rows:
            teams[row.team_id] = row
        unresolved = {
            row.parent_team_id
            for row in rows
            if row.parent_team_id and row.parent_team_id not in teams
        }
    project_ids = {
        scope_id for scope_type, scope_id in scope_bindings if scope_type == "project"
    }
    grants = session.scalars(
        select(AtlasPermissionGrantRow).where(
            AtlasPermissionGrantRow.project_id.in_(project_ids or {""}),
            or_(
                (AtlasPermissionGrantRow.subject_type == actor_type)
                & (AtlasPermissionGrantRow.subject_id == actor_id),
                (AtlasPermissionGrantRow.subject_type == "team")
                & (AtlasPermissionGrantRow.subject_id.in_(set(teams) or {""})),
            ),
        )
    ).all()
    project_rows = session.scalars(
        select(AtlasProjectRow).where(AtlasProjectRow.project_id.in_(project_ids or {""}))
    ).all()
    return ProcessingJobAuthorizationState(
        users=(
            {
                actor_id: UserRecord(
                    actor_id=actor_row.actor_id,
                    display_name=actor_row.display_name,
                    email=actor_row.email,
                    system_role=actor_row.system_role,
                    password_digest=actor_row.password_digest,
                    active=actor_row.active,
                    actor_type=actor_row.actor_type,
                    created_at=actor_row.created_at,
                )
            }
            if actor_row is not None
            else {}
        ),
        projects={
            row.project_id: ProjectRecord(
                project_id=row.project_id,
                name=row.name,
                policy_profile_id=row.policy_profile_id,
            )
            for row in project_rows
        },
        teams={
            row.team_id: TeamRecord(
                team_id=row.team_id,
                name=row.name,
                parent_team_id=row.parent_team_id,
                status=row.status,
                created_at=row.created_at,
                inherit_parent_documents=row.inherit_parent_documents,
            )
            for row in teams.values()
        },
        team_memberships={
            row.membership_id: TeamMembershipRecord(
                membership_id=row.membership_id,
                team_id=row.team_id,
                member_actor_type=row.member_actor_type,
                member_actor_id=row.member_actor_id,
                role=row.role,
                status=row.status,
                created_at=row.created_at,
                removed_at=row.removed_at,
            )
            for row in memberships
        },
        permission_grants={
            row.grant_id: PermissionGrantRecord(
                grant_id=row.grant_id,
                project_id=row.project_id,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                role=row.role,
                effect=row.effect,
                status=row.status,
                created_at=row.created_at,
                revoked_at=row.revoked_at,
            )
            for row in grants
        },
    )


def document_upload_authority_lock_plan(
    *,
    actor_type: str,
    actor_id: str,
    scopes: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    exact_scopes = tuple(sorted(set(scopes)))
    if len(exact_scopes) != len(scopes) or not exact_scopes or any(
        scope_type not in {"team", "project"} or not scope_id
        for scope_type, scope_id in exact_scopes
    ):
        raise ValueError("document upload scope is invalid")
    domain_keys = (
        "team:hierarchy-control",
        "team:membership-control",
        *(
            f"project:acl-control:{scope_id}"
            for scope_type, scope_id in exact_scopes
            if scope_type == "project"
        ),
    )
    identity_keys = (
        identity_actor_owner_key(actor_id),
        team_subject_owner_key(actor_type, actor_id),
        *(
            project_owner_key(scope_id)
            if scope_type == "project"
            else team_owner_key(scope_id)
            for scope_type, scope_id in exact_scopes
        ),
        *(
            project_acl_subject_owner_key(actor_type, actor_id)
            for scope_type, _scope_id in exact_scopes
            if scope_type == "project"
        ),
    )
    return tuple(domain_keys), tuple(identity_keys), exact_scopes


@dataclass(frozen=True, slots=True)
class DocumentUploadAuthorityWriter:
    """Resolve and stage exact scope decisions in its caller-owned Session."""

    _session: Session

    def execute_many(
        self,
        *,
        actor_type: str,
        actor_id: str,
        scopes: tuple[tuple[str, str], ...],
        locks_held: bool = False,
    ) -> tuple[AccessDecisionRecord, ...]:
        domain_keys, identity_keys, exact_scopes = document_upload_authority_lock_plan(
            actor_type=actor_type,
            actor_id=actor_id,
            scopes=scopes,
        )
        session = self._session
        if not locks_held:
            acquire_mixed_owner_locks(
                session,
                exclusive_domain_keys=domain_keys,
                exclusive_identity_keys=identity_keys,
            )
        decisions = tuple(
            replace(
                ActionAwareAclAuthority.resolve_in_session(
                    session,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    project_id=scope_id,
                    action="document_register",
                    lock_rows=True,
                ),
                scope_type="project",
                scope_id=scope_id,
            )
            if scope_type == "project"
            else DocumentUploadAuthorityCommand._team_decision(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                team_id=scope_id,
            )
            for scope_type, scope_id in exact_scopes
        )
        for decision in decisions:
            AccessDecisionWriter(session).append(decision)
        return decisions


@dataclass(frozen=True, slots=True)
class DocumentUploadAuthorityCommand:
    """Persist the exact upload decision once at the live request boundary."""

    session_factory: SessionFactory

    def execute(
        self,
        *,
        actor_type: str,
        actor_id: str,
        scope_type: str,
        scope_id: str,
    ) -> AccessDecisionRecord:
        return self.execute_many(
            actor_type=actor_type,
            actor_id=actor_id,
            scopes=((scope_type, scope_id),),
        )[0]

    def execute_many(
        self,
        *,
        actor_type: str,
        actor_id: str,
        scopes: tuple[tuple[str, str], ...],
    ) -> tuple[AccessDecisionRecord, ...]:
        session = self.session_factory()
        with session:
            try:
                decisions = DocumentUploadAuthorityWriter(session).execute_many(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    scopes=scopes,
                )
                session.commit()
                return decisions
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _team_decision(
        session,
        *,
        actor_type: str,
        actor_id: str,
        team_id: str,
    ) -> AccessDecisionRecord:
        actor = session.scalar(
            select(AtlasUserRow)
            .where(AtlasUserRow.actor_id == actor_id)
            .with_for_update()
        )
        team = session.scalar(
            select(AtlasTeamRow)
            .where(AtlasTeamRow.team_id == team_id)
            .with_for_update()
        )
        memberships = session.scalars(
            select(AtlasTeamMembershipRow)
            .where(
                AtlasTeamMembershipRow.team_id == team_id,
                AtlasTeamMembershipRow.member_actor_type == actor_type,
                AtlasTeamMembershipRow.member_actor_id == actor_id,
                AtlasTeamMembershipRow.status == "active",
            )
            .order_by(AtlasTeamMembershipRow.membership_id)
            .with_for_update()
        ).all()
        winning = max(
            memberships,
            key=lambda row: (TEAM_ROLE_ORDER.get(row.role, 0), row.membership_id),
            default=None,
        )
        active_actor = bool(
            actor is not None
            and actor.actor_type == actor_type
            and actor.active
        )
        system_admin = bool(active_actor and actor.system_role == "admin")
        allowed = bool(
            active_actor
            and team is not None
            and team.status == "active"
            and (system_admin or (winning and team_role_covers(winning.role, "uploader")))
        )
        reason = (
            "system_admin"
            if system_admin and team is not None and team.status == "active"
            else "team_role"
            if allowed
            else "actor_inactive_or_missing"
            if not active_actor
            else "team_missing_or_inactive"
            if team is None or team.status != "active"
            else "missing_required_role"
        )
        return AccessDecisionRecord(
            decision_id=f"access-{uuid4().hex}",
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=None,
            action="document_register",
            required_role=ACTION_REQUIRED_ROLE["document_register"],
            allowed=allowed,
            reason=reason,
            effective_role=(
                "admin"
                if system_admin
                else "contributor"
                if winning and team_role_covers(winning.role, "uploader")
                else None
            ),
            source_type=("system" if system_admin else "team_membership" if winning else None),
            source_id=(actor_id if system_admin else winning.membership_id if winning else None),
            explanation="Upload authority was captured at the live request boundary.",
            created_at=utc_now_iso(),
            scope_type="team",
            scope_id=team_id,
        )


@dataclass(frozen=True, slots=True)
class PostgresDocumentIntakeAdapter:
    """Port parity without hydrating or retaining a full-system aggregate."""

    session_factory: SessionFactory

    def journey_facade(self) -> DocumentIntakeJourneyFacade:
        return DocumentIntakeJourneyFacade(self.session_factory)

    def requested_scope_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        scope_type: str,
        scope_id: str,
        record_upload_denial: bool = False,
    ) -> RequestedDocumentScopeProjection:
        if scope_type not in {"team", "project"} or not scope_id:
            raise ValueError("requested document scope is invalid")
        with self.session_factory() as session:
            actor = read_session_actor(session, presented_browser_session_token)
            if (
                actor is None
                or actor.actor_type != actor_type
                or actor.actor_id != actor_id
            ):
                raise PermissionError("document scope request is unauthenticated")
            state = _authorization_state(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                scope_bindings=((scope_type, scope_id),),
            )
            if scope_type == "team":
                row = session.get(AtlasTeamRow, scope_id)
                exists = row is not None
                active = bool(row is not None and row.status == "active")
                label = row.name if row is not None else None
                can_upload = bool(
                    active
                    and (
                        is_system_admin(state, actor_type, actor_id)
                        or team_role_covers(
                            direct_team_role(state, actor_type, actor_id, scope_id),
                            "uploader",
                        )
                    )
                )
            else:
                row = session.get(AtlasProjectRow, scope_id)
                exists = row is not None
                active = exists
                label = row.name if row is not None else None
                can_upload = bool(
                    exists
                    and resolve_access(
                        state,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        project_id=scope_id,
                        action="document_register",
                        persist=False,
                    ).allowed
                )
            denial_audit = None
            if exists and active and not can_upload and record_upload_denial:
                denial_audit = AuditEventRecord(
                    event_id=f"audit-{uuid4().hex}",
                    event_type="document_upload_denied",
                    actor_id=actor_id,
                    target_ref=f"{scope_type}:{scope_id}",
                    project_id=scope_id if scope_type == "project" else None,
                    message_code="document.upload_requires_uploader_or_admin_access_to_this_scope",
                    metadata={"reason": "missing_scope_role"},
                    created_at=utc_now_iso(),
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
                AuditEventWriter(session).append_many((denial_audit,))
                session.commit()
            return RequestedDocumentScopeProjection(
                scope_type=scope_type,
                scope_id=scope_id,
                exists=exists,
                active=active,
                label=label,
                can_upload=can_upload,
                denial_audit_event=denial_audit,
            )

    def document_library_projection(
        self,
        *,
        actor_type: str,
        actor_id: str,
        presented_browser_session_token: str,
        document_id: str | None = None,
        include_events: bool = False,
    ) -> DocumentLibraryRequestProjection:
        """Read list/detail/events facts from one request-bounded Session."""

        discovery_statement = select(AtlasDocumentRow.document_id)
        if document_id is not None:
            discovery_statement = discovery_statement.where(
                AtlasDocumentRow.document_id == document_id
            )
        with self.session_factory() as discovery_session:
            discovered_document_ids = set(
                discovery_session.scalars(discovery_statement).all()
            )
            discovered_tag_rows = discovery_session.scalars(
                select(AtlasDocumentTagRow).where(
                    AtlasDocumentTagRow.document_id.in_(
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
            actor = read_session_actor(session, presented_browser_session_token)
            if (
                actor is None
                or actor.actor_type != actor_type
                or actor.actor_id != actor_id
            ):
                raise PermissionError("document library request is unauthenticated")
            statement = select(AtlasDocumentRow)
            if document_id is not None:
                statement = statement.where(AtlasDocumentRow.document_id == document_id)
            document_rows = session.scalars(
                statement.order_by(AtlasDocumentRow.document_id)
            ).all()
            documents = tuple(_document_record(row) for row in document_rows)
            document_ids = {document.document_id for document in documents}
            if not document_ids.issubset(discovered_document_ids):
                raise RuntimeError(
                    "document library membership changed during boundary discovery"
                )
            tag_rows = session.scalars(
                select(AtlasDocumentTagRow)
                .where(AtlasDocumentTagRow.document_id.in_(document_ids or {""}))
                .order_by(
                    AtlasDocumentTagRow.document_id,
                    AtlasDocumentTagRow.tag_type,
                    AtlasDocumentTagRow.tag_id,
                )
            ).all()
            grouped_tags: dict[str, list[DocumentTagRecord]] = {
                current_id: [] for current_id in document_ids
            }
            for row in tag_rows:
                grouped_tags[row.document_id].append(
                    DocumentTagRecord(
                        row.document_id, row.tag_type, row.tag_id, row.created_at
                    )
                )
            scope_bindings = tuple(
                sorted({(row.tag_type, row.tag_id) for row in tag_rows})
            )
            if not set(scope_bindings).issubset(discovered_scopes):
                raise RuntimeError(
                    "document library authority scope changed during boundary discovery"
                )
            team_ids = {
                scope_id for scope_type, scope_id in scope_bindings if scope_type == "team"
            }
            project_ids = {
                scope_id
                for scope_type, scope_id in scope_bindings
                if scope_type == "project"
            }
            label_map = {
                ("team", row.team_id): row.name
                for row in session.scalars(
                    select(AtlasTeamRow).where(AtlasTeamRow.team_id.in_(team_ids or {""}))
                ).all()
            }
            label_map.update(
                {
                    ("project", row.project_id): row.name
                    for row in session.scalars(
                        select(AtlasProjectRow).where(
                            AtlasProjectRow.project_id.in_(project_ids or {""})
                        )
                    ).all()
                }
            )
            evidence_counts = {
                row.document_id: int(row.ready_count)
                for row in session.execute(
                    select(
                        AtlasEvidenceRow.document_id,
                        func.count().label("ready_count"),
                    )
                    .join(
                        AtlasDocumentRow,
                        AtlasDocumentRow.document_id == AtlasEvidenceRow.document_id,
                    )
                    .where(
                        AtlasEvidenceRow.document_id.in_(document_ids or {""}),
                        AtlasEvidenceRow.status == "ready",
                        AtlasEvidenceRow.processing_generation
                        == AtlasDocumentRow.active_processing_generation,
                    )
                    .group_by(AtlasEvidenceRow.document_id)
                )
            }
            artifact_ids = {
                document.original_artifact_id
                for document in documents
                if document.original_artifact_id
            }
            available_artifacts = {
                row.artifact_id: row
                for row in session.execute(
                    select(AtlasArtifactRow)
                    .join(
                        AtlasStorageBlobRow,
                        AtlasStorageBlobRow.blob_id == AtlasArtifactRow.blob_id,
                    )
                    .where(
                        AtlasArtifactRow.artifact_id.in_(artifact_ids or {""}),
                        AtlasArtifactRow.lifecycle_status == "active",
                        AtlasStorageBlobRow.status == "committed",
                    )
                ).scalars()
            }
            grouped_events: dict[str, list[AuditEventRecord]] = {
                current_id: [] for current_id in document_ids
            }
            if include_events and document_ids:
                event_rows = session.scalars(
                    select(AtlasAuditEventRow)
                    .where(AtlasAuditEventRow.document_id.in_(document_ids))
                    .order_by(AtlasAuditEventRow.created_at, AtlasAuditEventRow.event_id)
                ).all()
                for row in event_rows:
                    if row.document_id in grouped_events:
                        grouped_events[row.document_id].append(_audit_record(row))
            authorization_state = _authorization_state(
                session,
                actor_type=actor_type,
                actor_id=actor_id,
                scope_bindings=scope_bindings,
            )
            system_admin = is_system_admin(authorization_state, actor_type, actor_id)
            workspace_scope = effective_document_scope(
                authorization_state,
                actor_type=actor_type,
                actor_id=actor_id,
                action="workspace_query",
            )
            original_scope = effective_document_scope(
                authorization_state,
                actor_type=actor_type,
                actor_id=actor_id,
                action="read_original",
            )
            processing_identity_ids = {
                document.processing_identity_id
                for document in documents
                if document.processing_identity_id is not None
            }
            processing_identities = {
                row.processing_identity_id: row
                for row in session.scalars(
                    select(AtlasProcessingIdentityRow).where(
                        AtlasProcessingIdentityRow.processing_identity_id.in_(
                            processing_identity_ids or {""}
                        )
                    )
                ).all()
            }
            current_revision_ids = {
                row.current_revision_id
                for row in processing_identities.values()
                if row.current_revision_id is not None
            }
            current_processing_revisions = {
                row.processing_revision_id: row
                for row in session.scalars(
                    select(AtlasProcessingRevisionRow).where(
                        AtlasProcessingRevisionRow.processing_revision_id.in_(
                            current_revision_ids or {""}
                        )
                    )
                ).all()
            }
            latest_terminal_revisions = {
                row.processing_identity_id: row
                for row in session.scalars(
                    select(AtlasProcessingRevisionRow)
                    .where(
                        AtlasProcessingRevisionRow.processing_identity_id.in_(
                            processing_identity_ids or {""}
                        ),
                        AtlasProcessingRevisionRow.state.in_(
                            ("failed", "cancelled")
                        ),
                    )
                    .distinct(AtlasProcessingRevisionRow.processing_identity_id)
                    .order_by(
                        AtlasProcessingRevisionRow.processing_identity_id,
                        AtlasProcessingRevisionRow.revision_number.desc(),
                    )
                ).all()
            }
            active_processing_jobs = {
                row.processing_identity_id: row
                for row in session.scalars(
                    select(AtlasProcessingJobRow)
                    .where(
                        AtlasProcessingJobRow.processing_identity_id.in_(
                            processing_identity_ids or {""}
                        ),
                        AtlasProcessingJobRow.status.in_(
                            ("queued", "running", "retry_wait")
                        ),
                    )
                    .order_by(
                        AtlasProcessingJobRow.processing_identity_id,
                        AtlasProcessingJobRow.created_at.desc(),
                    )
                ).all()
                if row.processing_identity_id is not None
            }

            def project_processing_presentation(
                document: DocumentRecord,
            ) -> DocumentRecord:
                identity_id = document.processing_identity_id
                if identity_id is None:
                    return document
                identity = processing_identities.get(identity_id)
                if identity is None:
                    return document
                current_revision = (
                    current_processing_revisions.get(identity.current_revision_id)
                    if identity.current_revision_id is not None
                    else None
                )
                active_job = active_processing_jobs.get(identity_id)
                if active_job is not None:
                    return replace(
                        document,
                        intake_status=(
                            "processing"
                            if current_revision is not None
                            and current_revision.state == "ready"
                            else "queued"
                        ),
                        current_stage=active_job.stage,
                        failure_code=active_job.failure_code,
                        processing_job_id=active_job.job_id,
                    )
                if current_revision is not None and current_revision.state == "ready":
                    return replace(
                        document,
                        intake_status="ready",
                        current_stage="completed",
                        failure_code=None,
                        processing_job_id=None,
                    )
                latest_revision = latest_terminal_revisions.get(identity_id)
                if (
                    identity.current_revision_id is None
                    and latest_revision is not None
                    and latest_revision.state in {"failed", "cancelled"}
                ):
                    return replace(
                        document,
                        intake_status="failed",
                        current_stage="completed",
                        failure_code="canonical_processing_requires_retry",
                        processing_job_id=None,
                    )
                return document

            def project(document: DocumentRecord) -> DocumentLibraryItemProjection:
                document = project_processing_presentation(document)
                tags = tuple(grouped_tags[document.document_id])
                original_artifact = (
                    available_artifacts.get(document.original_artifact_id)
                    if document.original_artifact_id
                    else None
                )
                artifact_available = bool(
                    original_artifact is not None
                    and original_artifact.parent_resource_id == document.document_id
                    and original_artifact.owner_scope_type == document.scope_type
                    and original_artifact.owner_scope_id == document.scope_id
                )
                if document.scope_type == "team":
                    admin = system_admin or team_role_covers(
                        direct_team_role(
                            authorization_state,
                            actor_type,
                            actor_id,
                            document.scope_id or "",
                        ),
                        "admin",
                    )
                else:
                    admin = system_admin or bool(
                        document.scope_id
                        and resolve_access(
                            authorization_state,
                            actor_type=actor_type,
                            actor_id=actor_id,
                            project_id=document.scope_id,
                            action="permission_manage",
                            persist=False,
                        ).allowed
                    )
                edit = admin or document.uploader_actor_id == actor_id
                download = bool(
                    document.lifecycle_status == "active"
                    and artifact_available
                    and source_allows_original_download(
                        document.content_type,
                        source_download_restricted=document.source_download_restricted,
                    )
                    and any(
                        (tag.tag_type, tag.tag_id) in original_scope for tag in tags
                    )
                    and (document.allow_member_download or admin)
                )
                view = edit or download or bool(
                    document.allow_member_download
                    and any(
                        (tag.tag_type, tag.tag_id) in workspace_scope for tag in tags
                    )
                )
                return DocumentLibraryItemProjection(
                    document=document,
                    tags=tags,
                    scope_labels=tuple(
                        (
                            tag.tag_type,
                            tag.tag_id,
                            label_map.get((tag.tag_type, tag.tag_id), tag.tag_id),
                        )
                        for tag in tags
                    ),
                    ready_evidence_count=evidence_counts.get(document.document_id, 0),
                    original_artifact_available=artifact_available,
                    can_view=view,
                    can_administer=admin,
                    can_edit=edit,
                    can_view_logs=edit,
                    download_available=download,
                    events=tuple(grouped_events[document.document_id]),
                )
            return DocumentLibraryRequestProjection(
                authenticated_actor=actor,
                items=tuple(project(document) for document in documents),
                authorization_state=authorization_state,
            )

    def capture_upload_authority(
        self,
        *,
        actor_type: str,
        actor_id: str,
        scope_type: str,
        scope_id: str,
    ) -> AccessDecisionRecord:
        return DocumentUploadAuthorityCommand(self.session_factory).execute(
            actor_type=actor_type,
            actor_id=actor_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )

    def capture_upload_authorities(
        self,
        *,
        actor_type: str,
        actor_id: str,
        scopes: tuple[tuple[str, str], ...],
    ) -> tuple[AccessDecisionRecord, ...]:
        return DocumentUploadAuthorityCommand(self.session_factory).execute_many(
            actor_type=actor_type,
            actor_id=actor_id,
            scopes=scopes,
        )

    def apply_lifecycle_mutation(
        self,
        *,
        expected_document: DocumentRecord | None,
        document: DocumentRecord,
        versions: tuple[DocumentVersionRecord, ...] = (),
        tags: tuple[DocumentTagRecord, ...] | None,
        audit_events: tuple[AuditEventRecord, ...],
        processing_acceptance: DocumentLifecycleProcessingAcceptance | None = None,
    ) -> ProcessingJobRecord | None:
        return DocumentLifecycleMutationCommand(self.session_factory).execute(
            expected_document=expected_document,
            document=document,
            versions=versions,
            tags=tags,
            audit_events=audit_events,
            processing_acceptance=processing_acceptance,
        )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self.session_factory() as session:
            row = session.get(AtlasDocumentRow, document_id)
            return _document_record(row) if row is not None else None

    def list_documents(self) -> list[DocumentRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasDocumentRow).order_by(AtlasDocumentRow.document_id)
            ).all()
            return [_document_record(row) for row in rows]

    def put_document(self, document: DocumentRecord) -> None:
        raise RuntimeError(
            "raw document writes are disabled; use apply_lifecycle_mutation"
        )

    def document_exists(self, document_id: str) -> bool:
        with self.session_factory() as session:
            return session.get(AtlasDocumentRow, document_id) is not None

    def replace_tags(self, document_id: str, tag_refs: list[DocumentTagRef]) -> None:
        raise RuntimeError(
            "raw tag writes are disabled; use apply_lifecycle_mutation"
        )

    def tags_for_document(self, document_id: str) -> list[DocumentTagRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasDocumentTagRow)
                .where(AtlasDocumentTagRow.document_id == document_id)
                .order_by(AtlasDocumentTagRow.tag_type, AtlasDocumentTagRow.tag_id)
            ).all()
            return [
                DocumentTagRecord(row.document_id, row.tag_type, row.tag_id, row.created_at)
                for row in rows
            ]

    def scope_label(self, tag: DocumentTagRecord) -> str | None:
        with self.session_factory() as session:
            if tag.tag_type == "team":
                row = session.get(AtlasTeamRow, tag.tag_id)
            elif tag.tag_type == "project":
                row = session.get(AtlasProjectRow, tag.tag_id)
            else:
                return None
            return row.name if row is not None else None

    def _version_id(self, document_id: str, statuses: tuple[str, ...]) -> str | None:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasDocumentVersionRow).where(
                    AtlasDocumentVersionRow.document_id == document_id,
                    AtlasDocumentVersionRow.payload["status"].as_string().in_(statuses),
                )
            ).all()
            records = [_version_record(row) for row in rows]
            for status in statuses:
                candidates = [record for record in records if record.status == status]
                if candidates:
                    return max(candidates, key=lambda record: record.created_at).document_version_id
            return None

    def active_document_version_id(self, document_id: str) -> str | None:
        return self._version_id(document_id, ("active",))

    def processing_document_version_id(self, document_id: str) -> str | None:
        return self._version_id(document_id, ("staged", "active"))

    def create_document_version(self, document: DocumentRecord) -> DocumentVersionRecord:
        raise RuntimeError(
            "raw version writes are disabled; use a named upload/lifecycle command"
        )

    def count_ready_evidence(self, document_id: str) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(AtlasEvidenceRow)
                    .join(
                        AtlasDocumentRow,
                        AtlasDocumentRow.document_id == AtlasEvidenceRow.document_id,
                    )
                    .where(
                        AtlasEvidenceRow.document_id == document_id,
                        AtlasEvidenceRow.status == "ready",
                        AtlasEvidenceRow.processing_generation
                        == AtlasDocumentRow.active_processing_generation,
                    )
                )
                or 0
            )

    def append_audit(
        self, command: DocumentAuditCommand, *, persist: bool = True
    ) -> AuditEventRecord:
        event = _audit_event(command)
        if not persist:
            return event
        raise RuntimeError(
            "standalone document audit writes are disabled; use a named command"
        )

    def list_document_audit_events(self, document_id: str) -> list[AuditEventRecord]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasAuditEventRow)
                .where(
                    or_(
                        AtlasAuditEventRow.document_id == document_id,
                        AtlasAuditEventRow.target_ref == f"document:{document_id}",
                        AtlasAuditEventRow.event_metadata["document_id"].as_string()
                        == document_id,
                    )
                )
                .order_by(AtlasAuditEventRow.created_at, AtlasAuditEventRow.event_id)
            ).all()
            return [_audit_record(row) for row in rows]


__all__ = [
    "DocumentIntakeJourneyFacade",
    "DocumentLifecycleRequestInput",
    "DocumentLibraryItemProjection",
    "DocumentLibraryRequestProjection",
    "DocumentUploadAuthorityCommand",
    "DocumentUploadAuthorityWriter",
    "PostgresDocumentIntakeAdapter",
    "RequestedDocumentScopeProjection",
    "document_upload_authority_lock_plan",
]
