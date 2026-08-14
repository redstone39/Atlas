"""Private deterministic action helpers for Retrieval V1."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Sequence

from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY,
    CatalogDocumentInput,
    CatalogRecord,
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_retrieval_v1_contracts import (
    BackendCatalogDocument,
    BackendEvidence,
)
from atlas_production.modules.retrieval.public import (
    EvidenceDescriptorV1,
    FindKnowledgeDocumentsV1,
    KnowledgeCatalogPageV1,
    KnowledgeDocumentDescriptorV1,
    KnowledgeExpansionResultV1,
    KnowledgeSearchResultV1,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _opaque(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return f"kh_{kind}_{digest[:32]}"


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in normalized
        ).split()
    )


def _page_resource(document_handle: str, page_number: int) -> str:
    return f"page|{document_handle}|{page_number}"


def _visual_resource(
    document_handle: str,
    page_number: int,
    bbox: tuple[int, int, int, int],
    image_digest: str,
) -> str:
    return (
        f"visual|{document_handle}|{page_number}|"
        f"{','.join(str(value) for value in bbox)}|{image_digest}"
    )


def _parse_visual_resource(
    kind: str, resource_ref: str
) -> tuple[str, int, tuple[int, int, int, int]]:
    parts = resource_ref.split("|")
    if kind == "page" and len(parts) == 3 and parts[0] == "page":
        document_handle, raw_page = parts[1:]
        raw_bbox = "0,0,10000,10000"
    elif kind == "visual" and len(parts) == 5 and parts[0] == "visual":
        document_handle, raw_page, raw_bbox = parts[1:4]
    else:
        raise RetrievalStoreConflict("visual handle lineage is invalid")
    try:
        page_number = int(raw_page)
        bbox = tuple(int(value) for value in raw_bbox.split(","))
    except ValueError:
        raise RetrievalStoreConflict("visual handle lineage is invalid") from None
    if (
        page_number < 1
        or len(bbox) != 4
        or any(value < 0 or value > 10_000 for value in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise RetrievalStoreConflict("visual handle lineage is invalid")
    return document_handle, page_number, bbox  # type: ignore[return-value]


def _compose_bbox(
    parent: tuple[int, int, int, int],
    child: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = parent
    width = right - left
    height = bottom - top
    return (
        left + width * child[0] // 10_000,
        top + height * child[1] // 10_000,
        left + width * child[2] // 10_000,
        top + height * child[3] // 10_000,
    )


def catalog_document_matches(
    document: CatalogDocumentInput,
    normalized_keyword: str,
) -> bool:
    value = document.descriptor
    identity_values = [
        str(value.get("display_name", "")),
        str(value.get("version_label") or ""),
        *(str(tag) for tag in value.get("tags", [])),
    ]
    return any(
        normalized_keyword in _normalize_identity(identity_value)
        for identity_value in identity_values
    )


def public_document_descriptor(
    document: CatalogDocumentInput,
) -> KnowledgeDocumentDescriptorV1:
    value = document.descriptor
    return KnowledgeDocumentDescriptorV1(
        document_handle=document.document_handle,
        display_name=str(value["display_name"]),
        media_type=str(value["media_type"]),
        modalities=list(value["modalities"]),
        tags=list(value.get("tags", [])),
        version_label=value.get("version_label"),
    )


def backend_documents(catalog: CatalogRecord) -> tuple[BackendCatalogDocument, ...]:
    return tuple(
        BackendCatalogDocument(
            document_handle=document.document_handle,
            lifecycle_epoch=document.lifecycle_epoch,
            document_version_ref=document.document_version_ref,
            processing_generation_ref=document.processing_generation_ref,
            processing_revision_ref=document.processing_revision_ref or "",
            index_generation_ref=document.index_generation_ref,
            manifest_digest=document.manifest_digest,
            descriptor={
                key: value
                for key, value in document.descriptor.items()
                if key != AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY
            },
        )
        for document in catalog.documents
    )


def catalog_page(catalog: CatalogRecord, action) -> KnowledgeCatalogPageV1:
    documents = list(catalog.documents)
    if isinstance(action, FindKnowledgeDocumentsV1):
        normalized_keyword = _normalize_identity(action.keyword)
        documents = [
            item
            for item in documents
            if catalog_document_matches(item, normalized_keyword)
        ]
        page_size = 10
        cursor_scope = f"find:{normalized_keyword}"
    else:
        page_size = action.page_size
        cursor_scope = "list"
    offset = cursor_offset(catalog.catalog_ref, cursor_scope, action.cursor)
    page = documents[offset : offset + page_size]
    next_cursor = (
        cursor(catalog.catalog_ref, cursor_scope, offset + page_size)
        if offset + page_size < len(documents)
        else None
    )
    return KnowledgeCatalogPageV1(
        result_type="knowledge_catalog_page",
        documents=[public_document_descriptor(item) for item in page],
        next_cursor=next_cursor,
    )


def evidence_result(
    catalog: CatalogRecord,
    evidence: Sequence[BackendEvidence],
    *,
    expansion_direction: str | None,
    limit: int,
):
    documents = backend_documents(catalog)
    documents_by_handle = {item.document_handle: item for item in documents}
    validate_backend_evidence(documents, evidence)
    descriptors = []
    handles = []
    for item in evidence[:limit]:
        handle = _opaque(
            "evidence",
            catalog.execution_id,
            catalog.catalog_ref,
            item.evidence_identity,
        )
        page_handle = (
            _opaque(
                "page",
                catalog.execution_id,
                catalog.catalog_ref,
                item.document_handle,
                str(item.page_number),
            )
            if item.page_number is not None
            else None
        )
        descriptors.append(
            EvidenceDescriptorV1(
                evidence_handle=handle,
                document_handle=item.document_handle,
                document_display_name=str(
                    documents_by_handle[item.document_handle].descriptor["display_name"]
                ),
                locator_label=item.locator_label,
                snippet=item.snippet,
                modalities=list(item.modalities),
                page_handle=page_handle,
                page_number=item.page_number,
            )
        )
        handles.append(
            ResultHandleInput(
                handle=handle,
                handle_kind="evidence",
                resource_ref=item.evidence_ref,
                evidence_identity=item.evidence_identity,
                document_handle=item.document_handle,
            )
        )
        if page_handle is not None and item.page_number is not None:
            handles.append(
                ResultHandleInput(
                    handle=page_handle,
                    handle_kind="page",
                    resource_ref=_page_resource(
                        item.document_handle, item.page_number
                    ),
                    document_handle=item.document_handle,
                )
            )
    if expansion_direction is None:
        observation = KnowledgeSearchResultV1(
            result_type="knowledge_search_result",
            evidence=descriptors,
            next_cursor=None,
        )
    else:
        observation = KnowledgeExpansionResultV1(
            result_type="knowledge_expansion_result",
            direction=expansion_direction,
            evidence=descriptors,
        )
    return observation, tuple({item.handle: item for item in handles}.values())


def validate_backend_evidence(
    documents,
    evidence,
    expected_refs=None,
) -> None:
    allowed = {document.document_handle for document in documents}
    seen: set[str] = set()
    for item in evidence:
        if item.document_handle not in allowed or item.evidence_identity in seen:
            raise RetrievalStoreConflict(
                "backend returned catalog-external or duplicate evidence"
            )
        if expected_refs is not None and item.evidence_ref not in expected_refs:
            raise RetrievalStoreConflict(
                "backend inspection returned unrequested evidence"
            )
        seen.add(item.evidence_identity)


def cursor(catalog_ref: str, scope: str, offset: int) -> str:
    return f"kc_{offset}_{_digest([catalog_ref, scope, offset])[:16]}"


def cursor_offset(
    catalog_ref: str,
    scope: str,
    cursor_value: str | None,
) -> int:
    if cursor_value is None:
        return 0
    try:
        prefix, raw_offset, proof = cursor_value.split("_", 2)
        offset = int(raw_offset)
    except (ValueError, TypeError):
        raise RetrievalStoreConflict("catalog cursor is invalid") from None
    if (
        prefix != "kc"
        or offset < 0
        or proof != _digest([catalog_ref, scope, offset])[:16]
    ):
        raise RetrievalStoreConflict("catalog cursor is invalid")
    return offset


def evidence_digest(item: ResultHandleInput) -> str:
    return _digest(
        {
            "evidence_ref": item.resource_ref,
            "evidence_identity": item.evidence_identity,
            "document_handle": item.document_handle,
        }
    )
