"""Private retrieval projection backend for production turn knowledge."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from atlas_production.async_runtime.vector_index import VectorIndex
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    BackendCatalogDocument,
    BackendDiscoveryHit,
    BackendEvidence,
    BackendVisualImage,
    KnowledgeRetrievalBackend,
)
from atlas_production.modules.processing_pipeline.public import DocumentNavigationMapV1

from atlas_production.infrastructure.postgres_turn_knowledge_contracts import (
    CurrentDiscoveryMatch,
    CurrentDocumentResource,
    CurrentEvidenceResource,
    ProductionKnowledgeRowSource,
    _digest,
    _remaining_seconds,
)
from atlas_production.infrastructure.postgres_turn_knowledge_visual import (
    PostgresVisualPageRenderer,
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
