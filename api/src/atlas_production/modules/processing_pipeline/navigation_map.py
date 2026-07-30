from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field


SUPPORTED_NAVIGATION_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
NAVIGATION_MAP_SCHEMA_VERSION = "document-navigation-map-v1"
NAVIGATION_MAP_RULE_VERSION = "structure-first-v1"
MAX_NAVIGATION_NODES = 500
MAX_NODE_SEARCH_TEXT = 2_000

NavigationNodeKind = Literal["page", "slide", "heading", "figure", "table"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentNavigationNodeV1(_StrictModel):
    node_ref: str = Field(min_length=8, max_length=200)
    parent_node_ref: str | None = Field(default=None, max_length=200)
    kind: NavigationNodeKind
    label: str = Field(min_length=1, max_length=500)
    structure_path: list[str] = Field(min_length=1, max_length=20)
    ordinal: int = Field(ge=1)
    page_number: int = Field(ge=1)
    search_text: str = Field(max_length=MAX_NODE_SEARCH_TEXT)
    content_traits: list[Literal["text", "table", "figure"]] = Field(
        min_length=1, max_length=3
    )
    has_page_visual: bool


class DocumentNavigationMapV1(_StrictModel):
    schema_version: Literal["document-navigation-map-v1"] = (
        NAVIGATION_MAP_SCHEMA_VERSION
    )
    rule_version: Literal["structure-first-v1"] = NAVIGATION_MAP_RULE_VERSION
    document_version_ref: str = Field(min_length=1, max_length=300)
    processing_revision_ref: str = Field(min_length=1, max_length=300)
    processing_generation_ref: str = Field(min_length=1, max_length=300)
    media_type: str = Field(min_length=1, max_length=200)
    nodes: list[DocumentNavigationNodeV1] = Field(max_length=MAX_NAVIGATION_NODES)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class NavigationPageSource:
    page_number: int
    label: str
    has_page_visual: bool


@dataclass(frozen=True, slots=True)
class NavigationEvidenceSource:
    stable_ref: str
    page_number: int
    locator_label: str
    content: str
    modality: Literal["text", "table", "figure"]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _bounded_text(parts: Sequence[str]) -> str:
    joined = " ".join(
        dict.fromkeys(part.strip() for part in parts if part and part.strip())
    )
    return joined[:MAX_NODE_SEARCH_TEXT]


def build_document_navigation_map(
    *,
    document_version_ref: str,
    processing_revision_ref: str,
    processing_generation_ref: str,
    media_type: str,
    pages: Sequence[NavigationPageSource],
    evidence: Sequence[NavigationEvidenceSource],
) -> DocumentNavigationMapV1 | None:
    """Build a deterministic, bounded map from immutable generation outputs."""

    if media_type not in SUPPORTED_NAVIGATION_MEDIA_TYPES or not pages:
        return None
    page_kind: NavigationNodeKind = (
        "slide"
        if media_type
        in {
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        else "page"
    )
    sorted_pages = sorted(pages, key=lambda item: (item.page_number, item.label))
    evidence_by_page: dict[int, list[NavigationEvidenceSource]] = {}
    for item in sorted(
        evidence,
        key=lambda value: (
            value.page_number,
            value.locator_label.casefold(),
            value.stable_ref,
        ),
    ):
        evidence_by_page.setdefault(item.page_number, []).append(item)

    nodes: list[DocumentNavigationNodeV1] = []
    ordinal = 0
    for page in sorted_pages:
        page_evidence = evidence_by_page.get(page.page_number, [])
        traits = [
            trait
            for trait in ("text", "table", "figure")
            if any(item.modality == trait for item in page_evidence)
        ] or ["text"]
        ordinal += 1
        page_ref = (
            "nav-node-"
            + _digest(
                [
                    NAVIGATION_MAP_SCHEMA_VERSION,
                    document_version_ref,
                    processing_generation_ref,
                    page_kind,
                    page.page_number,
                ]
            )
        )
        nodes.append(
            DocumentNavigationNodeV1(
                node_ref=page_ref,
                kind=page_kind,
                label=page.label,
                structure_path=[page.label],
                ordinal=ordinal,
                page_number=page.page_number,
                search_text=_bounded_text(
                    [
                        page.label,
                        *(item.locator_label for item in page_evidence),
                        *(item.content for item in page_evidence),
                    ]
                ),
                content_traits=traits,
                has_page_visual=page.has_page_visual,
            )
        )
        for item in page_evidence:
            if item.modality not in {"table", "figure"}:
                continue
            if len(nodes) >= MAX_NAVIGATION_NODES:
                break
            ordinal += 1
            nodes.append(
                DocumentNavigationNodeV1(
                    node_ref=(
                        "nav-node-"
                        + _digest(
                            [
                                NAVIGATION_MAP_SCHEMA_VERSION,
                                document_version_ref,
                                processing_generation_ref,
                                item.stable_ref,
                            ]
                        )
                    ),
                    parent_node_ref=page_ref,
                    kind=item.modality,
                    label=item.locator_label,
                    structure_path=[page.label, item.locator_label],
                    ordinal=ordinal,
                    page_number=page.page_number,
                    search_text=_bounded_text([item.locator_label, item.content]),
                    content_traits=[item.modality],
                    has_page_visual=page.has_page_visual,
                )
            )
        if len(nodes) >= MAX_NAVIGATION_NODES:
            break

    payload = {
        "schema_version": NAVIGATION_MAP_SCHEMA_VERSION,
        "rule_version": NAVIGATION_MAP_RULE_VERSION,
        "document_version_ref": document_version_ref,
        "processing_revision_ref": processing_revision_ref,
        "processing_generation_ref": processing_generation_ref,
        "media_type": media_type,
        "nodes": [node.model_dump(mode="json") for node in nodes],
    }
    return DocumentNavigationMapV1(**payload, digest=_digest(payload))


__all__ = [
    "DocumentNavigationMapV1",
    "DocumentNavigationNodeV1",
    "NAVIGATION_MAP_RULE_VERSION",
    "NavigationEvidenceSource",
    "NavigationPageSource",
    "SUPPORTED_NAVIGATION_MEDIA_TYPES",
    "build_document_navigation_map",
]
