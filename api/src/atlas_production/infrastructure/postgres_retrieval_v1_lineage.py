"""Private canonical PostgreSQL lineage reads for Retrieval V1."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.async_processing import (
    AtlasIndexGenerationRow,
    AtlasProcessingGenerationRetentionEntryRow,
    AtlasProcessingGenerationRetentionRow,
    AtlasSearchChunkRow,
)
from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentVersionRow,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidencePageArtifactRow,
    AtlasEvidenceRow,
    AtlasProcessingRevisionRow,
)
from atlas_production.infrastructure.persistence.retrieval import AtlasTurnCatalogDocumentRow
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY,
    PROCESSING_REVISION_REF_DESCRIPTOR_KEY,
    CatalogDocumentInput,
    CreateCatalogInput,
    EvidencePackLineageInput,
    MaterializeEvidencePackInput,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_retrieval_v1_actions import _digest


SessionFactory = Callable[[], Session]


def _opaque_evidence_ref(evidence_id: str) -> str:
    return f"evidence-resource-{_digest(['evidence-resource-v1', evidence_id])}"


def _canonical_document_resource_ref(document_id: str) -> str:
    return f"document-resource-{_digest(['document-resource-v1', document_id])}"


def _visual_page_number(evidence_ref: str) -> int | None:
    parts = evidence_ref.split("|")
    if len(parts) != 5 or parts[0] != "visual":
        return None
    try:
        value = int(parts[2])
    except ValueError:
        return None
    return value if value > 0 else None


class PostgresCanonicalRetrievalLineage:
    """Resolve cross-owner canonical lineage before Retrieval-owned writes."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def canonicalize_catalog(self, command: CreateCatalogInput) -> CreateCatalogInput:
        exact_documents: list[CatalogDocumentInput] = []
        with self._session_factory() as session:
            for document in command.documents:
                index = session.get(
                    AtlasIndexGenerationRow, document.index_generation_ref
                )
                revision = (
                    session.get(AtlasProcessingRevisionRow, index.processing_revision_id)
                    if index is not None and index.processing_revision_id is not None
                    else None
                )
                retained = session.scalar(
                    select(AtlasProcessingGenerationRetentionEntryRow.retention_ref)
                    .join(
                        AtlasProcessingGenerationRetentionRow,
                        AtlasProcessingGenerationRetentionRow.retention_ref
                        == AtlasProcessingGenerationRetentionEntryRow.retention_ref,
                    )
                    .where(
                        AtlasProcessingGenerationRetentionEntryRow.retention_ref
                        == command.generation_retention_ref,
                        AtlasProcessingGenerationRetentionEntryRow.index_generation_id
                        == document.index_generation_ref,
                        AtlasProcessingGenerationRetentionRow.status == "active",
                    )
                    .limit(1)
                )
                binding_version = session.get(
                    AtlasDocumentVersionRow, document.document_version_ref
                )
                binding = (
                    session.get(AtlasDocumentRow, binding_version.document_id)
                    if binding_version is not None
                    else None
                )
                if (
                    index is None
                    or revision is None
                    or revision.state != "ready"
                    or retained is None
                    or binding is None
                    or document.resource_ref
                    != _canonical_document_resource_ref(binding.document_id)
                    or document.lifecycle_epoch
                    != binding.resource_lifecycle_epoch + 1
                    or binding.processing_identity_id
                    != revision.processing_identity_id
                    or index.processing_revision_id
                    != revision.processing_revision_id
                    or index.manifest_digest != document.manifest_digest
                    or revision.manifest_digest != document.manifest_digest
                    or document.processing_generation_ref
                    != f"processing-generation-{index.source_processing_generation}"
                    or (
                        document.processing_revision_ref is not None
                        and document.processing_revision_ref
                        != revision.processing_revision_id
                    )
                ):
                    raise RetrievalStoreConflict(
                        "catalog document revision pin is unavailable"
                    )
                exact_documents.append(
                    replace(
                        document,
                        processing_revision_ref=revision.processing_revision_id,
                    )
                )
        return replace(command, documents=tuple(exact_documents))

    def canonicalize_evidence_pack(
        self,
        command: MaterializeEvidencePackInput,
        resolved: tuple[ResultHandleInput, ...],
    ) -> MaterializeEvidencePackInput:
        exact_items: list[EvidencePackLineageInput] = []
        with self._session_factory() as session:
            catalog_documents = {
                row.document_handle: row
                for row in session.scalars(
                    select(AtlasTurnCatalogDocumentRow).where(
                        AtlasTurnCatalogDocumentRow.catalog_ref == command.catalog_ref
                    )
                ).all()
            }
            for proposed, actual in zip(command.items, resolved, strict=True):
                catalog_document = catalog_documents.get(
                    actual.document_handle or ""
                )
                revision_ref = (
                    catalog_document.descriptor.get(
                        PROCESSING_REVISION_REF_DESCRIPTOR_KEY
                    )
                    if catalog_document is not None
                    else None
                )
                binding_version = session.get(
                    AtlasDocumentVersionRow, proposed.document_version_ref
                )
                binding = (
                    session.get(AtlasDocumentRow, binding_version.document_id)
                    if binding_version is not None
                    else None
                )
                revision = session.get(
                    AtlasProcessingRevisionRow, proposed.processing_revision_ref
                )
                index = session.get(
                    AtlasIndexGenerationRow, proposed.index_generation_ref
                )
                if (
                    catalog_document is None
                    or catalog_document.descriptor.get(
                        AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY
                    )
                    != proposed.resource_ref
                    or revision_ref != proposed.processing_revision_ref
                    or catalog_document.document_version_ref
                    != proposed.document_version_ref
                    or catalog_document.index_generation_ref
                    != proposed.index_generation_ref
                    or binding is None
                    or revision is None
                    or binding.processing_identity_id
                    != revision.processing_identity_id
                    or revision.state != "ready"
                    or index is None
                    or index.processing_revision_id
                    != proposed.processing_revision_ref
                ):
                    raise RetrievalStoreConflict(
                        "evidence pack document revision lineage changed"
                    )

                page_number = _visual_page_number(proposed.evidence_ref)
                if page_number is None:
                    evidence_rows = session.execute(
                        select(AtlasEvidenceRow, AtlasSearchChunkRow)
                        .join(
                            AtlasSearchChunkRow,
                            AtlasSearchChunkRow.evidence_id
                            == AtlasEvidenceRow.evidence_id,
                        )
                        .where(
                            AtlasEvidenceRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasSearchChunkRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasSearchChunkRow.index_generation_id
                            == proposed.index_generation_ref,
                        )
                    ).all()
                    evidence = next(
                        (
                            row
                            for row, _chunk in evidence_rows
                            if _opaque_evidence_ref(row.evidence_id)
                            == proposed.evidence_ref
                        ),
                        None,
                    )
                    if evidence is None:
                        raise RetrievalStoreConflict(
                            "evidence pack evidence revision lineage changed"
                        )
                    raw_page = evidence.locator_payload.get("page_number")
                    page_number = raw_page if isinstance(raw_page, int) else None

                page_artifact_ref = None
                if page_number is not None:
                    page = session.scalar(
                        select(AtlasEvidencePageArtifactRow).where(
                            AtlasEvidencePageArtifactRow.processing_revision_id
                            == proposed.processing_revision_ref,
                            AtlasEvidencePageArtifactRow.source_page_index
                            == page_number - 1,
                        )
                    )
                    if page is None:
                        raise RetrievalStoreConflict(
                            "evidence pack page artifact lineage changed"
                        )
                    page_artifact_ref = page.id
                exact_items.append(
                    replace(proposed, page_artifact_ref=page_artifact_ref)
                )
        return replace(command, items=tuple(exact_items))
