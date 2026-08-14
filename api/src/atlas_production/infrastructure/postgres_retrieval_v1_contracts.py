"""Private backend contracts for Retrieval V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from atlas_production.modules.processing_pipeline.public import DocumentNavigationMapV1


@dataclass(frozen=True, slots=True)
class BackendCatalogDocument:
    document_handle: str
    lifecycle_epoch: int
    document_version_ref: str
    processing_generation_ref: str
    processing_revision_ref: str
    index_generation_ref: str
    manifest_digest: str
    descriptor: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BackendEvidence:
    evidence_ref: str
    evidence_identity: str
    document_handle: str
    locator_label: str
    snippet: str
    content: str
    modalities: tuple[str, ...]
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class BackendDiscoveryHit:
    match_ref: str
    document_handle: str
    preview: str
    locator_label: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class BackendVisualImage:
    content: bytes
    digest: str
    width: int
    height: int


class KnowledgeRetrievalBackend(Protocol):
    def discover_lexical(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendDiscoveryHit]: ...

    def discover_vector(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendDiscoveryHit]: ...

    def search(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        query_text: str,
        required_modalities: tuple[str, ...],
        facet_hints: Mapping[str, object],
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]: ...

    def inspect(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        evidence_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]: ...

    def expand(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        anchor_evidence_refs: tuple[str, ...],
        direction: str,
        limit: int,
        deadline_at: datetime | None = None,
    ) -> Sequence[BackendEvidence]: ...

    def read_exact(
        self,
        *,
        documents: tuple[BackendCatalogDocument, ...],
        evidence_requests: tuple[tuple[str, str], ...],
    ) -> Sequence[BackendEvidence]: ...

    def render_visual(
        self,
        *,
        document: BackendCatalogDocument,
        page_number: int,
        normalized_bbox: tuple[int, int, int, int],
        deadline_at: datetime | None = None,
    ) -> BackendVisualImage: ...

    def navigation_map(
        self, *, document: BackendCatalogDocument, deadline_at: datetime | None = None
    ) -> DocumentNavigationMapV1 | None: ...
