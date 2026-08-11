"""Production ACL/currentness and knowledge-read adapters for strict turns.

The adapters are deliberately read-only.  Each PostgreSQL read opens and
closes its own Session, takes no row/advisory locks, and returns detached
values before an Authorization or Retrieval owner transaction can begin.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Mapping, Protocol, Sequence

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingGenerationRow,
    AtlasSearchChunkRow,
)
from atlas_production.infrastructure.persistence.artifact_storage import (
    AtlasArtifactRow,
    AtlasStorageBlobRow,
)
from atlas_production.infrastructure.persistence.conversation import (
    AtlasTurnConversationRow,
    AtlasTurnConversationScopeTagRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.identity_access import AtlasUserRow
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasEvidenceRow,
    AtlasProcessingIdentityRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.retrieval_currentness import (
    read_effective_document_scope,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendCatalogDocument,
    BackendDiscoveryHit,
    BackendEvidence,
    BackendVisualImage,
    KnowledgeRetrievalBackend,
)
from atlas_production.async_runtime.vector_index import VectorIndex
from atlas_production.infrastructure.office_renderer_adapter import OfficeRendererAdapter
from atlas_production.modules.authorization.public import (
    CurrentGrantAuthorizationSnapshotV1,
    CurrentResourceAuthorizationSnapshotV1,
    GrantDocumentResourceV1,
)
from atlas_production.modules.citation_preview.public import (
    ProtectedCitationEvidenceV1,
    ProtectedDeclaredEvidencePageIntegrityError,
    ProtectedDeclaredEvidencePageV1,
    ReadProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.artifact_storage.ports import ArtifactFilesystemPort
from atlas_production.modules.artifact_storage.errors import ArtifactStorageError
from atlas_production.modules.processing_pipeline.public import (
    DocumentNavigationMapV1,
    NavigationEvidenceSource,
    NavigationPageSource,
    ProcessingRevisionPin,
    build_document_navigation_map,
)


SessionFactory = Callable[[], Session]


def _remaining_seconds(deadline_at: datetime | None) -> float | None:
    if deadline_at is None:
        return None
    remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("retrieval tool deadline elapsed")
    return remaining


def _apply_statement_deadline(session: Session, deadline_at: datetime | None) -> None:
    remaining = _remaining_seconds(deadline_at)
    if remaining is None:
        return
    timeout_ms = max(1, int(remaining * 1000))
    session.execute(select(func.set_config("statement_timeout", f"{timeout_ms}ms", True)))


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def canonical_document_resource_ref(document_id: str) -> str:
    """Return a stable opaque authorization ref without exposing document_id."""

    return f"document-resource-{_digest(['document-resource-v1', document_id])}"


def _opaque_evidence_ref(evidence_id: str) -> str:
    return f"evidence-resource-{_digest(['evidence-resource-v1', evidence_id])}"


def _parse_visual_citation_ref(
    evidence_ref: str,
) -> tuple[int, tuple[int, int, int, int], str] | None:
    parts = evidence_ref.split("|")
    if len(parts) != 5 or parts[0] != "visual":
        return None
    try:
        page_number = int(parts[2])
        bbox = tuple(int(value) for value in parts[3].split(","))
    except ValueError:
        return None
    image_digest = parts[4]
    if (
        page_number < 1
        or len(bbox) != 4
        or any(value < 0 or value > 10_000 for value in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
        or len(image_digest) != 64
        or any(value not in "0123456789abcdef" for value in image_digest)
    ):
        return None
    return page_number, bbox, image_digest  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class CurrentDocumentResource:
    document_id: str
    resource_ref: str
    lifecycle_epoch: int
    document_version_ref: str
    processing_identity_ref: str
    processing_revision_ref: str
    source_artifact_ref: str
    source_artifact_checksum_sha256: str
    processing_generation_ref: str
    index_generation_ref: str
    manifest_digest: str
    display_name: str
    media_type: str
    searchable_content: str
    uploaded_at: str | None


@dataclass(frozen=True, slots=True)
class CurrentEvidenceResource:
    evidence_id: str
    evidence_ref: str
    document_id: str
    document_version_ref: str
    processing_revision_ref: str
    processing_generation_ref: str
    index_generation_ref: str
    manifest_digest: str
    locator_label: str
    snippet: str
    content: str
    modality: str
    page_number: int | None
    page_artifact_ref: str | None
    content_fingerprint: str


@dataclass(frozen=True, slots=True)
class CurrentDiscoveryMatch:
    chunk_id: str
    evidence: CurrentEvidenceResource


@dataclass(frozen=True, slots=True)
class GrantAuthorityState:
    actor_id: str
    conversation_id: str
    authorized: bool
    snapshot_ref: str
    authorization_revision: int


@dataclass(frozen=True, slots=True)
class GrantResourceSnapshot:
    authority: GrantAuthorityState
    documents: tuple[CurrentDocumentResource, ...]


@dataclass(frozen=True, slots=True)
class CurrentResourceState:
    resource_ref: str
    authorized: bool
    document: CurrentDocumentResource | None


class ProductionKnowledgeRowSource(Protocol):
    def grant_authority(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> GrantAuthorityState: ...
    def current_scope(self, *, actor_id: str) -> frozenset[tuple[str, str]]: ...
    def grant_resources(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> GrantResourceSnapshot: ...
    def authorized_documents(self, *, actor_id: str) -> tuple[CurrentDocumentResource, ...]: ...
    def authorized_resource_refs(self, *, actor_id: str) -> frozenset[str]: ...
    def current_ready_pins(
        self, document_ids: set[str]
    ) -> tuple[ProcessingRevisionPin, ...]: ...
    def resources(self, *, resource_refs: tuple[str, ...]) -> tuple[CurrentDocumentResource, ...]: ...
    def resource_authorizations(
        self,
        *,
        actor_id: str,
        resource_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentResourceState, ...]: ...
    def pinned_documents(
        self, *, pins: tuple[tuple[str, str, str, str], ...], deadline_at: datetime | None = None
    ) -> tuple[CurrentDocumentResource, ...]: ...
    def evidence(self, *, documents: tuple[CurrentDocumentResource, ...], deadline_at: datetime | None = None) -> tuple[CurrentEvidenceResource, ...]: ...
    def navigation_map(
        self, *, document: CurrentDocumentResource, deadline_at: datetime | None = None
    ) -> DocumentNavigationMapV1 | None: ...
    def lexical_discovery(
        self,
        *,
        documents: tuple[CurrentDocumentResource, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentDiscoveryMatch, ...]: ...
    def vector_discovery(
        self,
        *,
        documents: tuple[CurrentDocumentResource, ...],
        chunk_ids: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentDiscoveryMatch, ...]: ...
    def read_exact_citation_evidence(
        self,
        *,
        evidence_ref: str,
        document_version_ref: str,
        processing_generation_ref: str,
        index_generation_ref: str,
        processing_revision_ref: str | None = None,
        page_artifact_ref: str | None = None,
    ) -> ProtectedCitationEvidenceV1 | None: ...


class PostgresProductionKnowledgeRowSource:
    """Read current production rows without calling another owner repository."""

    def __init__(
        self,
        session_factory: SessionFactory,
        filesystem: ArtifactFilesystemPort | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._filesystem = filesystem

    def grant_authority(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> GrantAuthorityState:
        return self.grant_resources(
            actor_id=actor_id,
            conversation_id=conversation_id,
            deadline_at=deadline_at,
        ).authority
    def current_scope(self, *, actor_id: str) -> frozenset[tuple[str, str]]:
        with self._session_factory() as session:
            return frozenset(
                read_effective_document_scope(
                    session, actor_type="user", actor_id=actor_id
                )
            )


    def grant_resources(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> GrantResourceSnapshot:
        with self._session_factory() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            _apply_statement_deadline(session, deadline_at)
            actor = session.get(AtlasUserRow, actor_id)
            conversation = session.get(AtlasTurnConversationRow, conversation_id)
            authorized = bool(
                actor is not None
                and actor.active
                and actor.actor_type == "user"
                and conversation is not None
                and conversation.status == "active"
                and (conversation.owner_actor_id == actor_id or actor.system_role == "admin")
            )
            scope_rows = session.execute(
                select(
                    AtlasTurnConversationScopeTagRow.tag_type,
                    AtlasTurnConversationScopeTagRow.tag_id,
                ).where(
                    AtlasTurnConversationScopeTagRow.conversation_id
                    == conversation_id
                )
            ).all()
            requested_scope = (
                {(row.tag_type, row.tag_id) for row in scope_rows}
                if scope_rows
                else None
            )
            documents = (
                self._authorized_documents_in_session(
                    session,
                    actor_id=actor_id,
                    requested_scope=requested_scope,
                )
                if authorized
                else ()
            )
            material = {
                "actor_id": actor_id,
                "conversation_id": conversation_id,
                "actor_active": bool(actor and actor.active),
                "actor_role": None if actor is None else actor.system_role,
                "conversation_status": None if conversation is None else conversation.status,
                "conversation_owner": None if conversation is None else conversation.owner_actor_id,
                "authorized": authorized,
                "resources": [
                    {
                        "resource_ref": item.resource_ref,
                        "lifecycle_epoch": item.lifecycle_epoch,
                        "document_version_ref": item.document_version_ref,
                        "processing_generation_ref": item.processing_generation_ref,
                        "index_generation_ref": item.index_generation_ref,
                        "manifest_digest": item.manifest_digest,
                    }
                    for item in documents
                ],
            }
            digest = _digest(material)
            state = GrantAuthorityState(
                actor_id=actor_id,
                conversation_id=conversation_id,
                authorized=authorized,
                snapshot_ref=f"grant-authority-{digest}",
                authorization_revision=int(digest[:15], 16) + 1,
            )
            session.rollback()
            return GrantResourceSnapshot(authority=state, documents=documents)

    def authorized_documents(self, *, actor_id: str) -> tuple[CurrentDocumentResource, ...]:
        with self._session_factory() as session:
            return self._authorized_documents_in_session(session, actor_id=actor_id)

    def _authorized_documents_in_session(
        self,
        session: Session,
        *,
        actor_id: str,
        requested_scope: set[tuple[str, str]] | None = None,
    ) -> tuple[CurrentDocumentResource, ...]:
        scope = read_effective_document_scope(
            session,
            actor_type="user",
            actor_id=actor_id,
            requested_scope=requested_scope,
        )
        if not scope:
            return ()
        document_ids = set(
            session.scalars(
                select(AtlasDocumentTagRow.document_id).where(
                    tuple_(AtlasDocumentTagRow.tag_type, AtlasDocumentTagRow.tag_id).in_(
                        sorted(scope)
                    )
                )
            ).all()
        )
        return self._current_documents(session, document_ids=document_ids)

    def authorized_resource_refs(self, *, actor_id: str) -> frozenset[str]:
        """Return ACL scope independently of current lifecycle state."""

        with self._session_factory() as session:
            scope = read_effective_document_scope(
                session, actor_type="user", actor_id=actor_id
            )
            if not scope:
                return frozenset()
            document_ids = session.scalars(
                select(AtlasDocumentTagRow.document_id)
                .where(
                    tuple_(AtlasDocumentTagRow.tag_type, AtlasDocumentTagRow.tag_id).in_(
                        sorted(scope)
                    )
                )
                .distinct()
            ).all()
        return frozenset(canonical_document_resource_ref(value) for value in document_ids)

    def current_ready_pins(
        self,
        document_ids: set[str],
        *,
        _session: Session | None = None,
    ) -> tuple[ProcessingRevisionPin, ...]:
        """Map already-authorized Document bindings to identity current revisions.

        The caller owns Document authorization. Shared identity, revision, and
        index rows are loaded once per unique id before binding-specific pins
        are projected.
        """

        if not document_ids:
            return ()

        def read(session: Session) -> tuple[ProcessingRevisionPin, ...]:
            documents = session.scalars(
                select(AtlasDocumentRow)
                .where(
                    AtlasDocumentRow.document_id.in_(sorted(document_ids)),
                    AtlasDocumentRow.lifecycle_status == "active",
                    AtlasDocumentRow.processing_identity_id.is_not(None),
                )
                .order_by(AtlasDocumentRow.document_id)
            ).all()
            if not documents:
                return ()
            versions = session.scalars(
                select(AtlasDocumentVersionRow)
                .where(
                    AtlasDocumentVersionRow.document_id.in_(
                        [row.document_id for row in documents]
                    ),
                    AtlasDocumentVersionRow.payload["status"]
                    .as_string()
                    .in_(("active", "staged")),
                )
                .order_by(
                    AtlasDocumentVersionRow.document_id,
                    (
                        AtlasDocumentVersionRow.payload["status"].as_string()
                        != "staged"
                    ),
                    AtlasDocumentVersionRow.payload["created_at"].as_string().desc(),
                    AtlasDocumentVersionRow.document_version_id.desc(),
                )
            ).all()
            versions_by_document: dict[str, AtlasDocumentVersionRow] = {}
            for version in versions:
                versions_by_document.setdefault(version.document_id, version)

            identity_ids = {
                row.processing_identity_id
                for row in documents
                if row.processing_identity_id is not None
            }
            identities = {
                row.processing_identity_id: row
                for row in session.scalars(
                    select(AtlasProcessingIdentityRow).where(
                        AtlasProcessingIdentityRow.processing_identity_id.in_(
                            sorted(identity_ids)
                        )
                    )
                ).all()
            }
            revision_ids = {
                row.current_revision_id
                for row in identities.values()
                if row.current_revision_id is not None
            }
            revisions = {
                row.processing_revision_id: row
                for row in session.scalars(
                    select(AtlasProcessingRevisionRow).where(
                        AtlasProcessingRevisionRow.processing_revision_id.in_(
                            sorted(revision_ids)
                        ),
                        AtlasProcessingRevisionRow.state == "ready",
                    )
                ).all()
            }
            indexes = {
                row.processing_revision_id: row
                for row in session.scalars(
                    select(AtlasIndexGenerationRow).where(
                        AtlasIndexGenerationRow.processing_revision_id.in_(
                            sorted(revisions)
                        ),
                        AtlasIndexGenerationRow.status == "active",
                    )
                ).all()
                if row.processing_revision_id is not None
            }

            pins: list[ProcessingRevisionPin] = []
            for document in documents:
                version = versions_by_document.get(document.document_id)
                identity = identities.get(document.processing_identity_id or "")
                revision = (
                    revisions.get(identity.current_revision_id)
                    if identity is not None and identity.current_revision_id is not None
                    else None
                )
                index = (
                    indexes.get(revision.processing_revision_id)
                    if revision is not None
                    else None
                )
                if (
                    version is None
                    or identity is None
                    or revision is None
                    or index is None
                    or revision.processing_identity_id
                    != identity.processing_identity_id
                    or index.processing_revision_id
                    != revision.processing_revision_id
                    or version.payload.get("source_digest") != document.raw_sha256
                    or document.raw_sha256 != identity.source_sha256
                    or version.payload.get("original_artifact_id")
                    != document.original_artifact_id
                    or not revision.manifest_digest
                    or index.manifest_digest != revision.manifest_digest
                    or identity.source_artifact_checksum_sha256
                    != identity.source_sha256
                ):
                    continue
                pins.append(
                    ProcessingRevisionPin(
                        document_binding_id=document.document_id,
                        processing_identity_id=identity.processing_identity_id,
                        processing_revision_id=revision.processing_revision_id,
                        document_version_ref=version.document_version_id,
                        source_artifact_id=identity.source_artifact_id,
                        source_artifact_checksum_sha256=(
                            identity.source_artifact_checksum_sha256
                        ),
                        revision_state="ready",
                        processing_generation_ref=(
                            f"processing-generation-{index.source_processing_generation}"
                        ),
                        index_generation_id=index.index_generation_id,
                        manifest_digest=revision.manifest_digest,
                    )
                )
            return tuple(pins)

        if _session is not None:
            return read(_session)
        with self._session_factory() as session:
            return read(session)

    def resources(self, *, resource_refs: tuple[str, ...]) -> tuple[CurrentDocumentResource, ...]:
        wanted = set(resource_refs)
        if not wanted:
            return ()
        with self._session_factory() as session:
            # Canonical refs are one-way opaque values; resolve only by comparing
            # current rows and never expose the underlying document identity.
            document_ids = set(session.scalars(select(AtlasDocumentRow.document_id)).all())
            matches = {
                document_id
                for document_id in document_ids
                if canonical_document_resource_ref(document_id) in wanted
            }
            return self._current_documents(session, document_ids=matches)

    def resource_authorizations(
        self,
        *,
        actor_id: str,
        resource_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentResourceState, ...]:
        wanted = tuple(dict.fromkeys(resource_refs))
        if not wanted:
            return ()
        with self._session_factory() as session:
            # One repeatable-read snapshot closes the ACL/currentness race while
            # remaining fully read-only and lock-free.
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            _apply_statement_deadline(session, deadline_at)
            scope = read_effective_document_scope(
                session, actor_type="user", actor_id=actor_id
            )
            authorized_ids = (
                set(
                    session.scalars(
                        select(AtlasDocumentTagRow.document_id)
                        .where(
                            tuple_(
                                AtlasDocumentTagRow.tag_type,
                                AtlasDocumentTagRow.tag_id,
                            ).in_(sorted(scope))
                        )
                        .distinct()
                    ).all()
                )
                if scope
                else set()
            )
            all_ids = set(session.scalars(select(AtlasDocumentRow.document_id)).all())
            by_ref = {
                canonical_document_resource_ref(document_id): document_id
                for document_id in all_ids
                if canonical_document_resource_ref(document_id) in set(wanted)
            }
            current = {
                document.resource_ref: document
                for document in self._current_documents(
                    session, document_ids=set(by_ref.values())
                )
            }
            result = tuple(
                CurrentResourceState(
                    resource_ref=ref,
                    authorized=by_ref.get(ref) in authorized_ids,
                    document=current.get(ref),
                )
                for ref in wanted
            )
            session.rollback()
            return result

    def pinned_documents(
        self, *, pins: tuple[tuple[str, str, str, str], ...], deadline_at: datetime | None = None
    ) -> tuple[CurrentDocumentResource, ...]:
        if not pins:
            return ()
        wanted = set(pins)
        index_generation_ids = {pin[2] for pin in wanted}
        version_ids = {pin[0] for pin in wanted}
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            versions = session.scalars(
                select(AtlasDocumentVersionRow).where(
                    AtlasDocumentVersionRow.document_version_id.in_(
                        sorted(version_ids)
                    )
                )
            ).all()
            documents = {
                row.document_id: row
                for row in session.scalars(
                    select(AtlasDocumentRow).where(
                        AtlasDocumentRow.document_id.in_(
                            [version.document_id for version in versions] or [""]
                        ),
                        AtlasDocumentRow.lifecycle_status == "active",
                    )
                ).all()
            }
            indexes = session.scalars(
                select(AtlasIndexGenerationRow)
                .where(
                    AtlasIndexGenerationRow.index_generation_id.in_(
                        sorted(index_generation_ids)
                    ),
                    AtlasIndexGenerationRow.status.in_(("active", "retired")),
                    AtlasIndexGenerationRow.processing_revision_id.is_not(None),
                )
            ).all()
            revisions = {
                row.processing_revision_id: row
                for row in session.scalars(
                    select(AtlasProcessingRevisionRow).where(
                        AtlasProcessingRevisionRow.processing_revision_id.in_(
                            [
                                index.processing_revision_id
                                for index in indexes
                                if index.processing_revision_id is not None
                            ]
                            or [""]
                        ),
                        AtlasProcessingRevisionRow.state == "ready",
                    )
                ).all()
            }
            identities = {
                row.processing_identity_id: row
                for row in session.scalars(
                    select(AtlasProcessingIdentityRow).where(
                        AtlasProcessingIdentityRow.processing_identity_id.in_(
                            [
                                revision.processing_identity_id
                                for revision in revisions.values()
                            ]
                            or [""]
                        )
                    )
                ).all()
            }
        result: list[CurrentDocumentResource] = []
        versions_by_ref = {row.document_version_id: row for row in versions}
        indexes_by_ref = {row.index_generation_id: row for row in indexes}
        for document_version_ref, processing_ref, index_ref, manifest in wanted:
            version = versions_by_ref.get(document_version_ref)
            document = documents.get(version.document_id) if version is not None else None
            index = indexes_by_ref.get(index_ref)
            revision = (
                revisions.get(index.processing_revision_id)
                if index is not None and index.processing_revision_id is not None
                else None
            )
            identity = (
                identities.get(revision.processing_identity_id)
                if revision is not None
                else None
            )
            pin = (
                document_version_ref,
                processing_ref,
                index_ref,
                manifest,
            )
            if (
                document is None
                or index is None
                or revision is None
                or identity is None
                or processing_ref
                != f"processing-generation-{index.source_processing_generation}"
                or index.manifest_digest != manifest
                or revision.manifest_digest != manifest
                or index.processing_revision_id != revision.processing_revision_id
                or revision.processing_identity_id != identity.processing_identity_id
                or document.processing_identity_id != identity.processing_identity_id
            ):
                continue
            result.append(
                CurrentDocumentResource(
                    document_id=document.document_id,
                    resource_ref=canonical_document_resource_ref(document.document_id),
                    lifecycle_epoch=document.resource_lifecycle_epoch + 1,
                    document_version_ref=document_version_ref,
                    processing_identity_ref=identity.processing_identity_id,
                    processing_revision_ref=revision.processing_revision_id,
                    source_artifact_ref=identity.source_artifact_id,
                    source_artifact_checksum_sha256=(
                        identity.source_artifact_checksum_sha256
                    ),
                    processing_generation_ref=processing_ref,
                    index_generation_ref=index.index_generation_id,
                    manifest_digest=manifest,
                    display_name=document.title,
                    media_type=document.content_type or "application/octet-stream",
                    searchable_content=document.searchable_projection,
                    uploaded_at=document.uploaded_at,
                )
            )
        return tuple(result)

    def evidence(self, *, documents: tuple[CurrentDocumentResource, ...], deadline_at: datetime | None = None) -> tuple[CurrentEvidenceResource, ...]:
        if not documents:
            return ()
        exact_pairs = {
            (document.processing_revision_ref, document.index_generation_ref)
            for document in documents
        }
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            rows = session.execute(
                select(
                    AtlasEvidenceRow,
                    AtlasSearchChunkRow,
                    AtlasIndexGenerationRow,
                    AtlasDocumentRow,
                )
                .join(
                    AtlasSearchChunkRow,
                    AtlasSearchChunkRow.evidence_id == AtlasEvidenceRow.evidence_id,
                )
                .join(
                    AtlasIndexGenerationRow,
                    AtlasIndexGenerationRow.index_generation_id
                    == AtlasSearchChunkRow.index_generation_id,
                )
                .join(
                    AtlasDocumentRow,
                    AtlasDocumentRow.document_id == AtlasEvidenceRow.document_id,
                )
                .where(
                    tuple_(
                        AtlasSearchChunkRow.processing_revision_id,
                        AtlasSearchChunkRow.index_generation_id,
                    ).in_(sorted(exact_pairs)),
                    AtlasEvidenceRow.status == "ready",
                    AtlasSearchChunkRow.status.in_(("active", "retired")),
                )
                .order_by(AtlasEvidenceRow.evidence_id, AtlasSearchChunkRow.chunk_id)
            ).all()
            page_rows = session.scalars(
                select(AtlasEvidencePageArtifactRow).where(
                    AtlasEvidencePageArtifactRow.processing_revision_id.in_(
                        sorted({pair[0] for pair in exact_pairs})
                    )
                )
            ).all()
        page_by_revision_and_number = {
            (row.processing_revision_id, row.source_page_index + 1): row.id
            for row in page_rows
            if row.processing_revision_id is not None
        }
        documents_by_pair: dict[
            tuple[str, str], list[CurrentDocumentResource]
        ] = {}
        for document in documents:
            documents_by_pair.setdefault(
                (
                    document.processing_revision_ref,
                    document.index_generation_ref,
                ),
                [],
            ).append(document)
        result: list[CurrentEvidenceResource] = []
        seen: set[tuple[str, str]] = set()
        for evidence, chunk, generation, _source_document in rows:
            pair = (chunk.processing_revision_id, chunk.index_generation_id)
            if (
                pair[0] is None
                or pair not in exact_pairs
                or evidence.processing_revision_id != pair[0]
                or generation.processing_revision_id != pair[0]
            ):
                continue
            if (
                chunk.content_fingerprint != evidence.content_fingerprint
                or chunk.processing_fingerprint != evidence.processing_fingerprint
                or generation.status not in {"active", "retired"}
            ):
                continue
            modality = str(evidence.locator_payload.get("evidence_modality") or "text")
            if modality not in {"text", "table", "figure"}:
                modality = "text"
            page = evidence.locator_payload.get("page_number")
            page_number = page if isinstance(page, int) else None
            for pin in documents_by_pair.get((pair[0], pair[1]), []):
                seen_key = (pin.document_id, evidence.evidence_id)
                if seen_key in seen:
                    continue
                result.append(
                    CurrentEvidenceResource(
                        evidence_id=evidence.evidence_id,
                        evidence_ref=_opaque_evidence_ref(evidence.evidence_id),
                        document_id=pin.document_id,
                        document_version_ref=pin.document_version_ref,
                        processing_revision_ref=pin.processing_revision_ref,
                        processing_generation_ref=pin.processing_generation_ref,
                        index_generation_ref=pin.index_generation_ref,
                        manifest_digest=pin.manifest_digest,
                        locator_label=evidence.locator_label,
                        snippet=evidence.snippet,
                        content=evidence.content,
                        modality=modality,
                        page_number=page_number,
                        page_artifact_ref=page_by_revision_and_number.get(
                            (pin.processing_revision_ref, page_number)
                        )
                        if page_number is not None
                        else None,
                        content_fingerprint=evidence.content_fingerprint,
                    )
                )
                seen.add(seen_key)
        return tuple(result)

    def navigation_map(
        self, *, document: CurrentDocumentResource, deadline_at: datetime | None = None
    ) -> DocumentNavigationMapV1 | None:
        prefix = "processing-generation-"
        if not document.processing_generation_ref.startswith(prefix):
            return None
        try:
            generation = int(
                document.processing_generation_ref.removeprefix(prefix)
            )
        except ValueError:
            return None
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            revision = session.get(
                AtlasProcessingRevisionRow, document.processing_revision_ref
            )
            if (
                revision is None
                or revision.processing_identity_id
                != document.processing_identity_ref
                or revision.state != "ready"
                or revision.manifest_digest != document.manifest_digest
            ):
                return None
            page_rows = session.scalars(
                select(AtlasEvidencePageArtifactRow)
                .where(
                    AtlasEvidencePageArtifactRow.processing_revision_id
                    == document.processing_revision_ref,
                    AtlasEvidencePageArtifactRow.document_version_id
                    == document.document_version_ref,
                    AtlasEvidencePageArtifactRow.processing_generation == generation,
                )
                .order_by(AtlasEvidencePageArtifactRow.source_page_index)
            ).all()
            evidence_rows = session.scalars(
                select(AtlasEvidenceRow)
                .where(
                    AtlasEvidenceRow.processing_revision_id
                    == document.processing_revision_ref,
                    AtlasEvidenceRow.document_version_id
                    == document.document_version_ref,
                    AtlasEvidenceRow.processing_generation == generation,
                    AtlasEvidenceRow.status == "ready",
                )
                .order_by(AtlasEvidenceRow.evidence_id)
            ).all()
            pages = [
                NavigationPageSource(
                    page_number=row.source_page_index + 1,
                    label=str(
                        row.payload.get("source_page_label")
                        or (
                            f"投影片 {row.source_page_index + 1}"
                            if "presentation" in document.media_type
                            or document.media_type == "application/vnd.ms-powerpoint"
                            else f"第 {row.source_page_index + 1} 頁"
                        )
                    ),
                    has_page_visual=row.payload.get("artifact_kind")
                    in {"pdf_single_page", "page_image"},
                )
                for row in page_rows
            ]
            evidence = []
            for row in evidence_rows:
                page_number = row.locator_payload.get("page_number")
                if not isinstance(page_number, int) or page_number < 1:
                    continue
                modality = str(
                    row.locator_payload.get("evidence_modality") or "text"
                )
                if modality not in {"text", "table", "figure"}:
                    modality = "text"
                evidence.append(
                    NavigationEvidenceSource(
                        stable_ref=row.evidence_id,
                        page_number=page_number,
                        locator_label=row.locator_label,
                        content=row.content,
                        modality=modality,  # type: ignore[arg-type]
                    )
                )
            session.rollback()
        return build_document_navigation_map(
            document_version_ref=document.document_version_ref,
            processing_revision_ref=document.processing_revision_ref,
            processing_generation_ref=document.processing_generation_ref,
            media_type=document.media_type,
            pages=pages,
            evidence=evidence,
        )

    def lexical_discovery(
        self,
        *,
        documents: tuple[CurrentDocumentResource, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentDiscoveryMatch, ...]:
        if not documents or limit <= 0:
            return ()
        exact_pairs = {
            (document.processing_revision_ref, document.index_generation_ref)
            for document in documents
        }
        query = func.plainto_tsquery("simple", query_text)
        rank = func.ts_rank_cd(AtlasSearchChunkRow.search_vector, query)
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            rows = session.execute(
                select(
                    AtlasSearchChunkRow.chunk_id,
                    AtlasSearchChunkRow.evidence_id,
                )
                .join(
                    AtlasEvidenceRow,
                    AtlasEvidenceRow.evidence_id
                    == AtlasSearchChunkRow.evidence_id,
                )
                .join(
                    AtlasIndexGenerationRow,
                    AtlasIndexGenerationRow.index_generation_id
                    == AtlasSearchChunkRow.index_generation_id,
                )
                .where(
                    tuple_(
                        AtlasSearchChunkRow.processing_revision_id,
                        AtlasSearchChunkRow.index_generation_id,
                    ).in_(sorted(exact_pairs)),
                    AtlasSearchChunkRow.search_vector.op("@@")(query),
                    AtlasSearchChunkRow.status.in_(("active", "retired")),
                    AtlasEvidenceRow.status == "ready",
                    AtlasEvidenceRow.processing_revision_id
                    == AtlasSearchChunkRow.processing_revision_id,
                    AtlasIndexGenerationRow.processing_revision_id
                    == AtlasSearchChunkRow.processing_revision_id,
                    AtlasIndexGenerationRow.status.in_(("active", "retired")),
                    AtlasSearchChunkRow.content_fingerprint
                    == AtlasEvidenceRow.content_fingerprint,
                    AtlasSearchChunkRow.processing_fingerprint
                    == AtlasEvidenceRow.processing_fingerprint,
                )
                .order_by(
                    rank.desc(),
                    AtlasSearchChunkRow.chunk_id,
                )
                .limit(limit)
            ).all()
        return self._ordered_discovery_matches(
            documents=documents,
            chunk_evidence_pairs=tuple(
                (str(chunk_id), str(evidence_id))
                for chunk_id, evidence_id in rows
            ),
            deadline_at=deadline_at,
        )

    def vector_discovery(
        self,
        *,
        documents: tuple[CurrentDocumentResource, ...],
        chunk_ids: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentDiscoveryMatch, ...]:
        if not documents or not chunk_ids:
            return ()
        exact_pairs = {
            (document.processing_revision_ref, document.index_generation_ref)
            for document in documents
        }
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            rows = session.execute(
                select(
                    AtlasSearchChunkRow.chunk_id,
                    AtlasSearchChunkRow.evidence_id,
                )
                .join(
                    AtlasEvidenceRow,
                    AtlasEvidenceRow.evidence_id
                    == AtlasSearchChunkRow.evidence_id,
                )
                .join(
                    AtlasIndexGenerationRow,
                    AtlasIndexGenerationRow.index_generation_id
                    == AtlasSearchChunkRow.index_generation_id,
                )
                .where(
                    AtlasSearchChunkRow.chunk_id.in_(chunk_ids),
                    tuple_(
                        AtlasSearchChunkRow.processing_revision_id,
                        AtlasSearchChunkRow.index_generation_id,
                    ).in_(sorted(exact_pairs)),
                    AtlasSearchChunkRow.status.in_(("active", "retired")),
                    AtlasEvidenceRow.status == "ready",
                    AtlasEvidenceRow.processing_revision_id
                    == AtlasSearchChunkRow.processing_revision_id,
                    AtlasIndexGenerationRow.processing_revision_id
                    == AtlasSearchChunkRow.processing_revision_id,
                    AtlasIndexGenerationRow.status.in_(("active", "retired")),
                    AtlasSearchChunkRow.content_fingerprint
                    == AtlasEvidenceRow.content_fingerprint,
                    AtlasSearchChunkRow.processing_fingerprint
                    == AtlasEvidenceRow.processing_fingerprint,
                )
            ).all()
        by_chunk = {
            str(chunk_id): str(evidence_id)
            for chunk_id, evidence_id in rows
        }
        return self._ordered_discovery_matches(
            documents=documents,
            chunk_evidence_pairs=tuple(
                (chunk_id, by_chunk[chunk_id])
                for chunk_id in chunk_ids
                if chunk_id in by_chunk
            ),
            deadline_at=deadline_at,
        )

    def _ordered_discovery_matches(
        self,
        *,
        documents: tuple[CurrentDocumentResource, ...],
        chunk_evidence_pairs: tuple[tuple[str, str], ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentDiscoveryMatch, ...]:
        if not chunk_evidence_pairs:
            return ()
        evidence = self.evidence(documents=documents, deadline_at=deadline_at)
        by_evidence_id: dict[str, list[CurrentEvidenceResource]] = {}
        for item in evidence:
            by_evidence_id.setdefault(item.evidence_id, []).append(item)
        result: list[CurrentDiscoveryMatch] = []
        for chunk_id, evidence_id in chunk_evidence_pairs:
            result.extend(
                CurrentDiscoveryMatch(chunk_id=chunk_id, evidence=item)
                for item in by_evidence_id.get(evidence_id, ())
            )
        return tuple(result)

    def read_exact_citation_evidence(
        self,
        *,
        evidence_ref: str,
        document_version_ref: str,
        processing_generation_ref: str,
        index_generation_ref: str,
        processing_revision_ref: str | None = None,
        page_artifact_ref: str | None = None,
    ) -> ProtectedCitationEvidenceV1 | None:
        prefix = "processing-generation-"
        if not processing_generation_ref.startswith(prefix):
            return None
        try:
            processing_generation = int(processing_generation_ref.removeprefix(prefix))
        except ValueError:
            return None
        with self._session_factory() as session:
            version = session.get(AtlasDocumentVersionRow, document_version_ref)
            document = (
                session.get(AtlasDocumentRow, version.document_id)
                if version is not None
                else None
            )
            index = session.get(AtlasIndexGenerationRow, index_generation_ref)
            revision = (
                session.get(
                    AtlasProcessingRevisionRow,
                    index.processing_revision_id,
                )
                if index is not None and index.processing_revision_id is not None
                else None
            )
            if (
                version is None
                or document is None
                or document.lifecycle_status != "active"
                or index is None
                or index.status not in {"active", "retired"}
                or revision is None
                or revision.state != "ready"
                or document.processing_identity_id
                != revision.processing_identity_id
                or index.processing_revision_id != revision.processing_revision_id
                or index.source_processing_generation != processing_generation
                or index.manifest_digest != revision.manifest_digest
                or (
                    processing_revision_ref is not None
                    and processing_revision_ref != revision.processing_revision_id
                )
            ):
                return None
            exact_revision_ref = revision.processing_revision_id
        visual = _parse_visual_citation_ref(evidence_ref)
        if visual is not None:
            page_number, bbox, image_digest = visual
            with self._session_factory() as session:
                row = session.execute(
                    select(
                        AtlasEvidencePageArtifactRow,
                        AtlasIndexGenerationRow,
                    )
                    .join(
                        AtlasIndexGenerationRow,
                        AtlasIndexGenerationRow.processing_revision_id
                        == AtlasEvidencePageArtifactRow.processing_revision_id,
                    )
                    .where(
                        AtlasEvidencePageArtifactRow.processing_revision_id
                        == exact_revision_ref,
                        AtlasEvidencePageArtifactRow.source_page_index
                        == page_number - 1,
                        *(
                            (
                                AtlasEvidencePageArtifactRow.id
                                == page_artifact_ref,
                            )
                            if page_artifact_ref is not None
                            else ()
                        ),
                        AtlasIndexGenerationRow.index_generation_id
                        == index_generation_ref,
                        AtlasIndexGenerationRow.status.in_(("active", "retired")),
                    )
                ).one_or_none()
                if row is None:
                    return None
                page, _index = row
                payload = page.payload
                artifact_id = payload.get("storage_artifact_id")
                artifact = (
                    session.get(AtlasArtifactRow, artifact_id)
                    if isinstance(artifact_id, str)
                    else None
                )
                if (
                    payload.get("artifact_kind") != "pdf_single_page"
                    or artifact is None
                    or artifact.artifact_class != "document_page_pdf"
                    or artifact.lifecycle_status != "active"
                    or artifact.document_version_id != page.document_version_id
                    or artifact.processing_generation != processing_generation
                    or artifact.page_number != page_number
                    or page.processing_revision_id != exact_revision_ref
                ):
                    return None
            bbox_label = ",".join(str(value) for value in bbox)
            content = (
                f"Inspected PDF page {page_number}, normalized bbox "
                f"[{bbox_label}], image digest {image_digest}."
            )
            return ProtectedCitationEvidenceV1(
                citation_ref=evidence_ref,
                locator_label=f"Page {page_number} bbox [{bbox_label}]",
                snippet=content,
                content=content,
                modality="figure",
            )
        with self._session_factory() as session:
            rows = session.execute(
                select(AtlasEvidenceRow, AtlasSearchChunkRow, AtlasIndexGenerationRow)
                .join(
                    AtlasSearchChunkRow,
                    AtlasSearchChunkRow.evidence_id == AtlasEvidenceRow.evidence_id,
                )
                .join(
                    AtlasIndexGenerationRow,
                    AtlasIndexGenerationRow.index_generation_id
                    == AtlasSearchChunkRow.index_generation_id,
                )
                .where(
                    AtlasEvidenceRow.processing_revision_id == exact_revision_ref,
                    AtlasEvidenceRow.status == "ready",
                    AtlasSearchChunkRow.processing_revision_id == exact_revision_ref,
                    AtlasSearchChunkRow.index_generation_id == index_generation_ref,
                    AtlasSearchChunkRow.status == "active",
                    AtlasIndexGenerationRow.status.in_(("active", "retired")),
                )
                .order_by(AtlasEvidenceRow.evidence_id, AtlasSearchChunkRow.chunk_id)
            ).all()
        for evidence, chunk, generation in rows:
            if (
                _opaque_evidence_ref(evidence.evidence_id) != evidence_ref
                or chunk.content_fingerprint != evidence.content_fingerprint
                or chunk.processing_fingerprint != evidence.processing_fingerprint
                or generation.document_id != evidence.document_id
                or generation.source_processing_generation != processing_generation
                or generation.processing_revision_id != exact_revision_ref
            ):
                continue
            modality = str(evidence.locator_payload.get("evidence_modality") or "text")
            if modality not in {"text", "table", "figure"}:
                modality = "text"
            return ProtectedCitationEvidenceV1(
                citation_ref=evidence_ref,
                locator_label=evidence.locator_label,
                snippet=evidence.snippet,
                content=evidence.content,
                modality=modality,
            )
        return None

    def read_exact_declared_evidence_page(
        self,
        command: ReadProtectedDeclaredEvidenceV1,
        *,
        accepted_media_types: frozenset[str],
    ) -> ProtectedDeclaredEvidencePageV1 | None:
        """Read only the complete page pinned by the declared-evidence lineage."""

        page_artifact_ref = command.page_artifact_ref
        if page_artifact_ref is None:
            return None
        if self._filesystem is None:
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "pinned page storage is unavailable"
            )
        prefix = "processing-generation-"
        if not command.processing_generation_ref.startswith(prefix):
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "pinned processing generation is invalid"
            )
        try:
            processing_generation = int(
                command.processing_generation_ref.removeprefix(prefix)
            )
        except ValueError:
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "pinned processing generation is invalid"
            ) from None

        with self._session_factory() as session:
            version = session.get(
                AtlasDocumentVersionRow, command.document_version_ref
            )
            revision = session.get(
                AtlasProcessingRevisionRow, command.processing_revision_ref
            )
            page = session.get(AtlasEvidencePageArtifactRow, page_artifact_ref)
            if page is None:
                raise ProtectedDeclaredEvidencePageIntegrityError(
                    "pinned page record is missing"
                )
            if (
                version is None
                or revision is None
                or revision.state != "ready"
                or page.id != page_artifact_ref
                or page.document_version_id != command.document_version_ref
                or page.processing_revision_id
                != command.processing_revision_ref
                or page.processing_generation != processing_generation
            ):
                raise ProtectedDeclaredEvidencePageIntegrityError(
                    "pinned page lineage is inconsistent"
                )

            payload = dict(page.payload)
            page_kind = payload.get("artifact_kind")
            expected = {
                "pdf_single_page": ("document_page_pdf", "application/pdf"),
                "page_image": ("page_image", "image/png"),
            }.get(page_kind)
            if expected is None:
                return None
            artifact_class, media_type = expected
            if media_type not in accepted_media_types:
                return None

            storage_artifact_id = payload.get("storage_artifact_id")
            artifact = (
                session.get(AtlasArtifactRow, storage_artifact_id)
                if isinstance(storage_artifact_id, str)
                else None
            )
            blob = (
                session.get(AtlasStorageBlobRow, artifact.blob_id)
                if artifact is not None
                else None
            )
            if (
                artifact is None
                or blob is None
                or artifact.lifecycle_status != "active"
                or blob.status != "committed"
            ):
                raise ProtectedDeclaredEvidencePageIntegrityError(
                    "pinned page artifact is unavailable"
                )

            page_number = page.source_page_index + 1
            if (
                artifact.artifact_id != storage_artifact_id
                or artifact.artifact_class != artifact_class
                or artifact.content_type != media_type
                or artifact.document_version_id != command.document_version_ref
                or artifact.processing_generation != processing_generation
                or artifact.page_number != page_number
                or artifact.checksum_algorithm != "sha256"
                or artifact.checksum_value != payload.get("artifact_digest")
                or artifact.byte_size != payload.get("content_length")
                or blob.blob_id != artifact.blob_id
                or blob.checksum_algorithm != "sha256"
                or blob.checksum_value != artifact.checksum_value
                or blob.byte_size != artifact.byte_size
                or blob.content_type != media_type
            ):
                raise ProtectedDeclaredEvidencePageIntegrityError(
                    "pinned page artifact metadata is inconsistent"
                )
            opaque_ref = blob.opaque_ref
            expected_size = blob.byte_size
            expected_digest = blob.checksum_value

        try:
            with self._filesystem.open_read(
                opaque_ref, expected_size=expected_size
            ) as stream:
                content = stream.read(expected_size + 1)
        except (ArtifactStorageError, OSError) as exc:
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "pinned page bytes are unavailable"
            ) from exc
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_digest
        ):
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "pinned page bytes failed integrity verification"
            )
        return ProtectedDeclaredEvidencePageV1(
            media_type=media_type,
            content=content,
        )

    def _current_documents(
        self, session: Session, *, document_ids: set[str]
    ) -> tuple[CurrentDocumentResource, ...]:
        pins = self.current_ready_pins(document_ids, _session=session)
        documents = {
            row.document_id: row
            for row in session.scalars(
                select(AtlasDocumentRow).where(
                    AtlasDocumentRow.document_id.in_(
                        [pin.document_binding_id for pin in pins] or [""]
                    )
                )
            ).all()
        }
        return tuple(
            CurrentDocumentResource(
                document_id=pin.document_binding_id,
                resource_ref=canonical_document_resource_ref(pin.document_binding_id),
                lifecycle_epoch=documents[pin.document_binding_id].resource_lifecycle_epoch
                + 1,
                document_version_ref=pin.document_version_ref,
                processing_identity_ref=pin.processing_identity_id,
                processing_revision_ref=pin.processing_revision_id,
                source_artifact_ref=pin.source_artifact_id,
                source_artifact_checksum_sha256=(
                    pin.source_artifact_checksum_sha256
                ),
                processing_generation_ref=pin.processing_generation_ref,
                index_generation_ref=pin.index_generation_id,
                manifest_digest=pin.manifest_digest,
                display_name=documents[pin.document_binding_id].title,
                media_type=documents[pin.document_binding_id].content_type
                or "application/octet-stream",
                searchable_content=documents[
                    pin.document_binding_id
                ].searchable_projection,
                uploaded_at=documents[pin.document_binding_id].uploaded_at,
            )
            for pin in pins
            if pin.document_binding_id in documents
        )


class ProductionCurrentResourceAuthorizationReader:
    def __init__(self, rows: ProductionKnowledgeRowSource) -> None:
        self._rows = rows

    def current_grant_authorization(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> CurrentGrantAuthorizationSnapshotV1:
        state = self._rows.grant_authority(
            actor_id=actor_id,
            conversation_id=conversation_id,
            deadline_at=deadline_at,
        )
        return CurrentGrantAuthorizationSnapshotV1(**asdict(state))

    def current_resource_authorizations(
        self,
        *,
        actor_id: str,
        resource_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentResourceAuthorizationSnapshotV1, ...]:
        states = {
            state.resource_ref: state
            for state in self._rows.resource_authorizations(
                actor_id=actor_id,
                resource_refs=resource_refs,
                deadline_at=deadline_at,
            )
        }
        digest = _digest(
            [
                {
                    "resource_ref": ref,
                    "authorized": bool(states.get(ref) and states[ref].authorized),
                    "current": (
                        None
                        if states.get(ref) is None or states[ref].document is None
                        else asdict(states[ref].document)
                    ),
                }
                for ref in sorted(set(resource_refs))
            ]
        )
        revision = int(digest[:15], 16) + 1
        snapshots = []
        for ref in resource_refs:
            state = states.get(ref)
            document = None if state is None else state.document
            snapshots.append(
                CurrentResourceAuthorizationSnapshotV1(
                    actor_id=actor_id,
                    resource_ref=ref,
                    authorization_revision=revision,
                    snapshot_ref=f"resource-authority-{digest}",
                    authorized=bool(state and state.authorized),
                    active=document is not None,
                    lifecycle_epoch=None if document is None else document.lifecycle_epoch,
                    version_ref=None if document is None else document.document_version_ref,
                    generation_ref=None if document is None else document.index_generation_ref,
                    processing_generation_ref=None if document is None else document.processing_generation_ref,
                    index_generation_ref=None if document is None else document.index_generation_ref,
                )
            )
        return tuple(snapshots)


class ProductionAuthorizedGrantResourceSource:
    """Build only the actor's current authorized exact document pins."""

    def __init__(self, rows: ProductionKnowledgeRowSource) -> None:
        self._rows = rows
    def current_scope(self, *, actor_id: str) -> frozenset[tuple[str, str]]:
        return self._rows.current_scope(actor_id=actor_id)


    def resources_for_grant(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        authorization_revision: int,
    ) -> list[GrantDocumentResourceV1]:
        snapshot = self._rows.grant_resources(
            actor_id=actor_id, conversation_id=conversation_id
        )
        if (
            not snapshot.authority.authorized
            or snapshot.authority.authorization_revision != authorization_revision
        ):
            raise PermissionError("grant resource authority changed before materialization")
        return self._resources_with_modalities(snapshot.documents)

    def authorized_document_resources(self, *, actor_id: str) -> tuple[GrantDocumentResourceV1, ...]:
        """Narrow compatibility helper for adapter unit tests only."""
        return tuple(
            self._resources_with_modalities(
                self._rows.authorized_documents(actor_id=actor_id)
            )
        )

    def _resources_with_modalities(
        self, documents: tuple[CurrentDocumentResource, ...]
    ) -> list[GrantDocumentResourceV1]:
        modalities_by_document: dict[str, list[str]] = {
            document.document_id: [] for document in documents
        }
        for evidence in self._rows.evidence(documents=documents):
            values = modalities_by_document.get(evidence.document_id)
            if values is not None and evidence.modality not in values:
                values.append(evidence.modality)
        return [
            self._resource(
                document,
                modalities=modalities_by_document[document.document_id] or ["text"],
            )
            for document in documents
        ]

    @staticmethod
    def _resource(
        document: CurrentDocumentResource, *, modalities: list[str]
    ) -> GrantDocumentResourceV1:
        return GrantDocumentResourceV1(
            resource_ref=document.resource_ref,
            lifecycle_epoch=document.lifecycle_epoch,
            document_version_ref=document.document_version_ref,
            processing_generation_ref=document.processing_generation_ref,
            index_generation_ref=document.index_generation_ref,
            manifest_digest=document.manifest_digest,
            display_name=document.display_name,
            media_type=document.media_type,
            modalities=modalities,
            tags=[],
            language=None,
            created_at_label=document.uploaded_at,
            searchable_content=document.searchable_content,
            version_label=None,
        )


class PostgresVisualPageRenderer:
    """Read one pinned single-page PDF and render the requested root bbox."""

    def __init__(
        self,
        session_factory: SessionFactory,
        filesystem: ArtifactFilesystemPort,
    ) -> None:
        self._session_factory = session_factory
        self._filesystem = filesystem

    def render(
        self,
        *,
        document: CurrentDocumentResource,
        page_number: int,
        normalized_bbox: tuple[int, int, int, int],
        deadline_at: datetime | None = None,
    ) -> BackendVisualImage:
        prefix = "processing-generation-"
        if (
            document.media_type != "application/pdf"
            or page_number < 1
            or not document.processing_generation_ref.startswith(prefix)
        ):
            raise OSError("visual_page_unavailable")
        try:
            generation = int(document.processing_generation_ref.removeprefix(prefix))
        except ValueError:
            raise OSError("visual_page_unavailable") from None
        with self._session_factory() as session:
            _apply_statement_deadline(session, deadline_at)
            page = session.scalar(
                select(AtlasEvidencePageArtifactRow).where(
                    AtlasEvidencePageArtifactRow.processing_revision_id
                    == document.processing_revision_ref,
                    AtlasEvidencePageArtifactRow.source_page_index == page_number - 1,
                )
            )
            payload = dict(page.payload) if page is not None else {}
            artifact = (
                session.get(AtlasArtifactRow, payload.get("storage_artifact_id"))
                if payload.get("artifact_kind") == "pdf_single_page"
                else None
            )
            blob = (
                session.get(AtlasStorageBlobRow, artifact.blob_id)
                if artifact is not None
                else None
            )
            if (
                page is None
                or artifact is None
                or blob is None
                or artifact.artifact_class != "document_page_pdf"
                or artifact.content_type != "application/pdf"
                or artifact.lifecycle_status != "active"
                or artifact.document_version_id != page.document_version_id
                or artifact.processing_generation != generation
                or artifact.page_number != page_number
                or artifact.checksum_value != payload.get("artifact_digest")
                or artifact.byte_size != payload.get("content_length")
                or blob.status != "committed"
                or blob.checksum_value != artifact.checksum_value
                or blob.byte_size != artifact.byte_size
            ):
                raise OSError("visual_page_unavailable")
            opaque_ref = blob.opaque_ref
            expected_size = blob.byte_size
            expected_digest = blob.checksum_value
        with self._filesystem.open_read(
            opaque_ref, expected_size=expected_size
        ) as stream:
            content = stream.read(expected_size + 1)
        _remaining_seconds(deadline_at)
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_digest
        ):
            raise OSError("visual_page_integrity_failed")
        rendered = OfficeRendererAdapter().raster_pdf_page(
            content,
            normalized_bbox=normalized_bbox,
            timeout_seconds=_remaining_seconds(deadline_at),
        )
        _remaining_seconds(deadline_at)
        return BackendVisualImage(
            content=rendered.content,
            digest=rendered.sha256,
            width=rendered.width,
            height=rendered.height,
        )


class ProductionKnowledgeRetrievalBackend(KnowledgeRetrievalBackend):
    def __init__(
        self,
        rows: ProductionKnowledgeRowSource,
        visual_pages: "PostgresVisualPageRenderer | None" = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self._rows = rows
        self._visual_pages = visual_pages
        self._vector_index = vector_index

    def discover_lexical(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendDiscoveryHit]:
        current, handles_by_id = self._current_documents(
            documents, deadline_at=deadline_at
        )
        if not current:
            return ()
        return tuple(
            self._discovery_hit(match, handles_by_id)
            for match in self._rows.lexical_discovery(
                documents=current,
                query_text=query_text,
                limit=limit,
                deadline_at=deadline_at,
            )
            if match.evidence.document_id in handles_by_id
        )

    def discover_vector(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendDiscoveryHit]:
        if self._vector_index is None:
            raise OSError("vector_index_unavailable")
        current, handles_by_id = self._current_documents(
            documents, deadline_at=deadline_at
        )
        if not current:
            return ()
        exact_pairs = {
            (
                document.processing_revision_ref,
                document.index_generation_ref,
            )
            for document in current
        }
        hits = self._vector_index.search_hits(
            query_text,
            limit=limit,
            revision_index_pairs=exact_pairs,
            timeout_seconds=_remaining_seconds(deadline_at),
        )
        matches = self._rows.vector_discovery(
            documents=current,
            chunk_ids=tuple(hit.chunk_id for hit in hits),
            deadline_at=deadline_at,
        )
        return tuple(
            self._discovery_hit(match, handles_by_id)
            for match in matches
            if match.evidence.document_id in handles_by_id
        )

    def search(
        self, *, documents: tuple[BackendCatalogDocument, ...], query_text: str,
        required_modalities: tuple[str, ...], facet_hints: Mapping[str, object], limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]:
        del facet_hints
        evidence = self._eligible(documents, deadline_at=deadline_at)
        terms = tuple(term for term in query_text.casefold().split() if term)
        ranked = []
        for item, document_handle in evidence:
            _remaining_seconds(deadline_at)
            if required_modalities and item.modality not in required_modalities:
                continue
            haystack = f"{item.locator_label} {item.snippet} {item.content}".casefold()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append((-score, item.evidence_ref, item, document_handle))
        ranked.sort(key=lambda value: (value[0], value[1]))
        return tuple(self._backend(item, handle) for _, _, item, handle in ranked[:limit])

    def navigation_map(
        self, *, document: BackendCatalogDocument, deadline_at: datetime | None = None
    ) -> DocumentNavigationMapV1 | None:
        current = self._rows.pinned_documents(
            pins=(
                (
                    document.document_version_ref,
                    document.processing_generation_ref,
                    document.index_generation_ref,
                    document.manifest_digest,
                ),
            ),
            deadline_at=deadline_at,
        )
        if len(current) != 1 or current[0].lifecycle_epoch != document.lifecycle_epoch:
            return None
        return self._rows.navigation_map(
            document=current[0], deadline_at=deadline_at
        )

    def inspect(
        self, *, documents: tuple[BackendCatalogDocument, ...], evidence_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]:
        wanted = set(evidence_refs)
        return tuple(
            self._backend(item, handle)
            for item, handle in self._eligible(documents, deadline_at=deadline_at)
            if item.evidence_ref in wanted
        )

    def read_exact(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        evidence_requests: tuple[tuple[str, str], ...],
    ) -> Sequence[BackendEvidence]:
        """Governance-only exact read; it neither searches nor expands evidence."""

        documents_by_handle = {item.document_handle: item for item in documents}
        result: list[BackendEvidence] = []
        for evidence_ref, document_handle in evidence_requests:
            document = documents_by_handle.get(document_handle)
            if document is None:
                continue
            item = self._rows.read_exact_citation_evidence(
                evidence_ref=evidence_ref,
                document_version_ref=document.document_version_ref,
                processing_generation_ref=document.processing_generation_ref,
                index_generation_ref=document.index_generation_ref,
                processing_revision_ref=document.processing_revision_ref,
            )
            if item is None:
                continue
            result.append(
                BackendEvidence(
                    evidence_ref=evidence_ref,
                    evidence_identity=evidence_ref,
                    document_handle=document_handle,
                    locator_label=item.locator_label,
                    snippet=item.snippet,
                    content=item.content,
                    modalities=(item.modality,),
                    page_number=None,
                )
            )
        return tuple(result)

    def render_visual(
        self,
        *,
        document: BackendCatalogDocument,
        page_number: int,
        normalized_bbox: tuple[int, int, int, int],
        deadline_at: datetime | None = None,
    ) -> BackendVisualImage:
        if self._visual_pages is None:
            raise OSError("visual_page_renderer_unavailable")
        current = self._rows.pinned_documents(
            pins=((
                document.document_version_ref,
                document.processing_generation_ref,
                document.index_generation_ref,
                document.manifest_digest,
            ),),
            deadline_at=deadline_at,
        )
        if len(current) != 1 or current[0].lifecycle_epoch != document.lifecycle_epoch:
            raise OSError("visual_page_currentness_changed")
        return self._visual_pages.render(
            document=current[0],
            page_number=page_number,
            normalized_bbox=normalized_bbox,
            deadline_at=deadline_at,
        )

    def expand(
        self, *, documents: tuple[BackendCatalogDocument, ...],
        anchor_evidence_refs: tuple[str, ...], direction: str, limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]:
        eligible = self._eligible(documents, deadline_at=deadline_at)
        anchors = {item.evidence_ref: item for item, _ in eligible if item.evidence_ref in anchor_evidence_refs}
        selected: list[tuple[CurrentEvidenceResource, str]] = []
        for item, handle in eligible:
            _remaining_seconds(deadline_at)
            for anchor in anchors.values():
                if item.document_id != anchor.document_id or item.evidence_ref == anchor.evidence_ref:
                    continue
                if direction == "previous_page" and item.page_number == (anchor.page_number or 0) - 1:
                    selected.append((item, handle))
                elif direction == "next_page" and item.page_number == (anchor.page_number or 0) + 1:
                    selected.append((item, handle))
                elif direction == "figure_context" and item.page_number == anchor.page_number:
                    selected.append((item, handle))
                elif direction == "related_evidence":
                    selected.append((item, handle))
        selected.sort(key=lambda value: value[0].evidence_ref)
        return tuple(self._backend(item, handle) for item, handle in selected[:limit])

    def _eligible(
        self,
        documents: tuple[BackendCatalogDocument, ...],
        *,
        deadline_at: datetime | None = None,
    ) -> tuple[tuple[CurrentEvidenceResource, str], ...]:
        current, handles_by_id = self._current_documents(
            documents, deadline_at=deadline_at
        )
        if not current:
            return ()
        return tuple(
            (item, handles_by_id[item.document_id])
            for item in self._rows.evidence(
                documents=current, deadline_at=deadline_at
            )
            if item.document_id in handles_by_id
        )

    def _current_documents(
        self,
        documents: tuple[BackendCatalogDocument, ...],
        *,
        deadline_at: datetime | None = None,
    ) -> tuple[tuple[CurrentDocumentResource, ...], dict[str, str]]:
        current = self._rows.pinned_documents(
            pins=tuple(
                (
                    document.document_version_ref,
                    document.processing_generation_ref,
                    document.index_generation_ref,
                    document.manifest_digest,
                )
                for document in documents
            ),
            deadline_at=deadline_at,
        )
        current_by_pin = {
            (
                item.document_version_ref,
                item.processing_generation_ref,
                item.index_generation_ref,
                item.manifest_digest,
            ): item
            for item in current
        }
        handles_by_id: dict[str, str] = {}
        pins = []
        for document in documents:
            key = (
                document.document_version_ref,
                document.processing_generation_ref,
                document.index_generation_ref,
                document.manifest_digest,
            )
            current_document = current_by_pin.get(key)
            if current_document is not None and current_document.lifecycle_epoch == document.lifecycle_epoch:
                pins.append(current_document)
                handles_by_id[current_document.document_id] = document.document_handle
        if not pins:
            return (), {}
        return tuple(pins), handles_by_id

    @staticmethod
    def _discovery_hit(
        match: CurrentDiscoveryMatch,
        handles_by_id: Mapping[str, str],
    ) -> BackendDiscoveryHit:
        item = match.evidence
        return BackendDiscoveryHit(
            match_ref=item.evidence_ref,
            document_handle=handles_by_id[item.document_id],
            preview=item.content,
            locator_label=item.locator_label,
            page_number=item.page_number,
        )

    @staticmethod
    def _backend(item: CurrentEvidenceResource, document_handle: str) -> BackendEvidence:
        identity = _digest(
            [
                "evidence-identity-v1",
                item.evidence_ref,
                item.document_version_ref,
                item.processing_revision_ref,
                item.processing_generation_ref, item.index_generation_ref,
                item.content_fingerprint,
            ]
        )
        return BackendEvidence(
            evidence_ref=item.evidence_ref,
            evidence_identity=identity,
            document_handle=document_handle,
            locator_label=item.locator_label,
            snippet=item.snippet,
            content=item.content,
            modalities=(item.modality,),
            page_number=item.page_number,
        )


__all__ = [
    "PostgresProductionKnowledgeRowSource",
    "PostgresVisualPageRenderer",
    "ProductionAuthorizedGrantResourceSource",
    "ProductionCurrentResourceAuthorizationReader",
    "ProductionKnowledgeRetrievalBackend",
    "canonical_document_resource_ref",
]
