"""Private immutable contracts and deterministic helpers for production turn knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas_production.modules.citation_preview.public import ProtectedCitationEvidenceV1
from atlas_production.modules.processing_pipeline.public import (
    DocumentNavigationMapV1,
    ProcessingRevisionPin,
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
    def authorized_documents_for_projects(
        self, *, actor_id: str, project_ids: tuple[str, ...]
    ) -> tuple[CurrentDocumentResource, ...]: ...
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
