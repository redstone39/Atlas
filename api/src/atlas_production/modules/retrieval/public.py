from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


OpaqueKnowledgeHandle = Annotated[str, Field(min_length=8, max_length=200)]
Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Modality: TypeAlias = Literal["text", "table", "figure"]


class KnowledgeCatalogSnapshotRefV1(_StrictModel):
    catalog_ref: OpaqueRef
    schema_version: Literal["knowledge-catalog-snapshot-v1"] = "knowledge-catalog-snapshot-v1"
    grant_ref: OpaqueRef
    generation_retention_ref: OpaqueRef
    retrieval_generation_ref: OpaqueRef
    document_count: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class KnowledgeDocumentDescriptorV1(_StrictModel):
    document_handle: OpaqueKnowledgeHandle
    display_name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    modalities: list[Modality] = Field(min_length=1, max_length=3)
    tags: list[str] = Field(max_length=50)
    version_label: str | None = Field(max_length=200)


class FacetHintsV1(_StrictModel):
    document_types: list[str] = Field(max_length=20)
    date_from: str | None
    date_to: str | None
    languages: list[str] = Field(max_length=20)
    tags: list[str] = Field(max_length=20)


class ListKnowledgeDocumentsV1(_StrictModel):
    action: Literal["list_knowledge_documents"]
    cursor: str | None = Field(max_length=500)
    page_size: int = Field(ge=1, le=10)
    max_output_tokens: int = Field(ge=256, le=64_000)


class FindKnowledgeDocumentsV1(_StrictModel):
    action: Literal["find_knowledge_documents"]
    keyword: str = Field(min_length=1, max_length=500)
    cursor: str | None = Field(max_length=500)

    @field_validator("keyword")
    @classmethod
    def require_identity_characters(cls, value: str) -> str:
        if not any(character.isalnum() for character in unicodedata.normalize("NFKC", value)):
            raise ValueError("keyword must contain at least one letter or number")
        return value


class DiscoverRelevantDocumentsV1(_StrictModel):
    action: Literal["discover_relevant_documents"]
    query_text: str = Field(min_length=1, max_length=4000)
    limit: int = Field(ge=1, le=20)


class SearchKnowledgeV1(_StrictModel):
    action: Literal["search_knowledge"]
    query_text: str = Field(min_length=1, max_length=4000)
    document_handles: list[OpaqueKnowledgeHandle] = Field(min_length=1, max_length=20)
    required_modalities: list[Modality] = Field(max_length=3)
    facet_hints: FacetHintsV1
    limit: int = Field(ge=1, le=20)
    max_output_tokens: int = Field(ge=256, le=64_000)


class InspectKnowledgeV1(_StrictModel):
    action: Literal["inspect_knowledge"]
    handles: list[OpaqueKnowledgeHandle] = Field(min_length=1, max_length=20)
    max_output_tokens: int = Field(ge=256, le=64_000)


class NormalizedBboxV1(_StrictModel):
    left: int = Field(ge=0, le=10_000)
    top: int = Field(ge=0, le=10_000)
    right: int = Field(ge=0, le=10_000)
    bottom: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def require_area(self) -> "NormalizedBboxV1":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("bbox must have positive area")
        return self


class InspectVisualV1(_StrictModel):
    action: Literal["inspect_visual"]
    handle: OpaqueKnowledgeHandle
    scope: Literal["full", "rect"]
    bbox: NormalizedBboxV1 | None

    @model_validator(mode="after")
    def require_scope_bbox(self) -> "InspectVisualV1":
        if (self.scope == "full") != (self.bbox is None):
            raise ValueError("full forbids bbox and rect requires bbox")
        return self


class ExpandKnowledgeV1(_StrictModel):
    action: Literal["expand_knowledge"]
    anchor_handles: list[OpaqueKnowledgeHandle] = Field(min_length=1, max_length=20)
    direction: Literal["previous_page", "next_page", "figure_context", "related_evidence"]
    limit: int = Field(ge=1, le=20)
    max_output_tokens: int = Field(ge=256, le=64_000)


class NavigateDocumentV1(_StrictModel):
    action: Literal["navigate_document"]
    mode: Literal["overview", "search", "around"]
    document_handle: OpaqueKnowledgeHandle | None
    navigation_handle: OpaqueKnowledgeHandle | None
    query_text: str | None = Field(max_length=4000)
    relation: Literal[
        "previous", "next", "parent", "children", "same_page"
    ] | None
    cursor: str | None = Field(max_length=500)
    limit: int = Field(ge=1, le=20)
    max_output_tokens: int = Field(ge=256, le=64_000)

    @model_validator(mode="after")
    def require_mode_shape(self) -> "NavigateDocumentV1":
        if self.mode == "overview":
            valid = (
                self.document_handle is not None
                and self.navigation_handle is None
                and self.query_text is None
                and self.relation is None
            )
        elif self.mode == "search":
            valid = (
                self.document_handle is not None
                and self.navigation_handle is None
                and self.query_text is not None
                and bool(self.query_text.strip())
                and self.relation is None
                and self.cursor is None
            )
        else:
            valid = (
                self.document_handle is None
                and self.navigation_handle is not None
                and self.query_text is None
                and self.relation is not None
                and self.cursor is None
            )
        if not valid:
            raise ValueError("navigate_document arguments do not match mode")
        return self


KnowledgeToolActionV1: TypeAlias = Annotated[
    ListKnowledgeDocumentsV1
    | FindKnowledgeDocumentsV1
    | DiscoverRelevantDocumentsV1
    | SearchKnowledgeV1
    | InspectKnowledgeV1
    | InspectVisualV1
    | ExpandKnowledgeV1
    | NavigateDocumentV1,
    Field(discriminator="action"),
]


class KnowledgeCatalogPageV1(_StrictModel):
    result_type: Literal["knowledge_catalog_page"]
    documents: list[KnowledgeDocumentDescriptorV1] = Field(max_length=20)
    next_cursor: str | None


class EvidenceDescriptorV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    document_display_name: str = Field(min_length=1, max_length=500)
    locator_label: str = Field(min_length=1, max_length=500)
    snippet: str = Field(max_length=4096)
    modalities: list[Modality] = Field(min_length=1, max_length=3)
    page_handle: OpaqueKnowledgeHandle | None
    page_number: int | None = Field(ge=1)


class KnowledgeSearchResultV1(_StrictModel):
    result_type: Literal["knowledge_search_result"]
    evidence: list[EvidenceDescriptorV1] = Field(max_length=20)
    next_cursor: str | None


class RelevantDocumentCandidateV1(_StrictModel):
    document_handle: OpaqueKnowledgeHandle
    document_display_name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    modalities: list[Modality] = Field(min_length=1, max_length=3)
    preview: str = Field(max_length=1000)
    locator_label: str = Field(min_length=1, max_length=500)
    page_number: int | None = Field(ge=1)


class RelevantDocumentDiscoveryResultV1(_StrictModel):
    result_type: Literal["relevant_document_discovery_result"]
    candidates: list[RelevantDocumentCandidateV1] = Field(max_length=20)
    ranking_contract: Literal["equal-reciprocal-rank-v1"]
    channels: list[Literal["lexical", "vector"]] = Field(max_length=2)
    degraded: bool
    vector_coverage: int = Field(ge=0)
    catalog_document_count: int = Field(ge=0)
    truncated_by_budget: bool


class KnowledgeInspectionItemV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    document_display_name: str = Field(min_length=1, max_length=500)
    locator_label: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=12000)
    modalities: list[Modality] = Field(min_length=1, max_length=3)


class KnowledgeInspectionResultV1(_StrictModel):
    result_type: Literal["knowledge_inspection_result"]
    items: list[KnowledgeInspectionItemV1] = Field(max_length=20)


class VisualInspectionResultV1(_StrictModel):
    result_type: Literal["visual_inspection_result"]
    visual_handle: OpaqueKnowledgeHandle
    source_handle: OpaqueKnowledgeHandle
    page_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    page_number: int = Field(ge=1)
    scope: Literal["full", "rect"]
    bbox: NormalizedBboxV1
    image_ref: OpaqueRef
    image_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class KnowledgeExpansionResultV1(_StrictModel):
    result_type: Literal["knowledge_expansion_result"]
    direction: Literal["previous_page", "next_page", "figure_context", "related_evidence"]
    evidence: list[EvidenceDescriptorV1] = Field(max_length=20)


class NavigationTargetV1(_StrictModel):
    navigation_handle: OpaqueKnowledgeHandle
    document_handle: OpaqueKnowledgeHandle
    document_display_name: str = Field(min_length=1, max_length=500)
    kind: Literal["page", "slide", "heading", "figure", "table"]
    label: str = Field(min_length=1, max_length=500)
    structure_path: list[str] = Field(min_length=1, max_length=20)
    page_number: int = Field(ge=1)
    content_traits: list[Modality] = Field(min_length=1, max_length=3)
    page_handle: OpaqueKnowledgeHandle | None


class DocumentNavigationResultV1(_StrictModel):
    result_type: Literal["document_navigation_result"]
    mode: Literal["overview", "search", "around"]
    map_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    targets: list[NavigationTargetV1] = Field(max_length=20)
    next_cursor: str | None = Field(max_length=500)


class KnowledgeToolErrorV1(_StrictModel):
    result_type: Literal["knowledge_tool_error"]
    error_code: Literal[
        "access_denied",
        "invalid_handle",
        "catalog_stale",
        "budget_exhausted",
        "navigation_unavailable",
        "tool_failed",
    ]
    message_code: str = Field(min_length=1, max_length=200)
    retryable: bool


KnowledgeToolObservationV1: TypeAlias = Annotated[
    KnowledgeCatalogPageV1
    | RelevantDocumentDiscoveryResultV1
    | KnowledgeSearchResultV1
    | KnowledgeInspectionResultV1
    | VisualInspectionResultV1
    | KnowledgeExpansionResultV1
    | DocumentNavigationResultV1
    | KnowledgeToolErrorV1,
    Field(discriminator="result_type"),
]

ProviderKnowledgeToolObservationV1: TypeAlias = (
    KnowledgeCatalogPageV1
    | RelevantDocumentDiscoveryResultV1
    | KnowledgeSearchResultV1
    | KnowledgeInspectionResultV1
    | VisualInspectionResultV1
    | KnowledgeExpansionResultV1
    | DocumentNavigationResultV1
    | KnowledgeToolErrorV1
)


class KnowledgeToolObservationEnvelopeV1(_StrictModel):
    observation: ProviderKnowledgeToolObservationV1


class RetrievalEvidenceLineageV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    evidence_ref: OpaqueRef
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_identity: str = Field(min_length=1, max_length=300)
    document_handle: OpaqueKnowledgeHandle
    result_ref: OpaqueRef
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_ordinal: int = Field(ge=1)


class VisualImagePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visual_handle: OpaqueKnowledgeHandle
    image_ref: OpaqueRef
    image_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    content: bytes = Field(repr=False)

    @model_validator(mode="after")
    def require_digest(self) -> "VisualImagePayloadV1":
        import hashlib

        if hashlib.sha256(self.content).hexdigest() != self.image_digest:
            raise ValueError("visual image digest does not match content")
        return self


class RetrievalInvocationEnvelopeV1(_StrictModel):
    observation: ProviderKnowledgeToolObservationV1
    result_ref: OpaqueRef
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_candidate_handles: list[OpaqueKnowledgeHandle] = Field(max_length=20)
    evidence_lineage: list[RetrievalEvidenceLineageV1] = Field(max_length=20)
    catalog_pages: int = Field(ge=0)
    search_rounds: int = Field(ge=0)
    tool_tokens: int = Field(ge=0)
    replayed: bool
    visual_image: VisualImagePayloadV1 | None = Field(
        default=None, repr=False, exclude=True
    )


class EvidencePackLineageItemV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    evidence_ref: OpaqueRef
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_ref: OpaqueRef
    lifecycle_epoch: int = Field(ge=1)
    document_version_ref: OpaqueRef
    processing_revision_ref: OpaqueRef
    processing_generation_ref: OpaqueRef
    index_generation_ref: OpaqueRef
    page_artifact_ref: OpaqueRef | None = None
    result_ref: OpaqueRef
    invocation_ordinal: int = Field(ge=1)


class EvidencePackRefV1(_StrictModel):
    evidence_pack_ref: OpaqueRef
    schema_version: Literal["retrieval-evidence-pack-v1"] = "retrieval-evidence-pack-v1"
    execution_id: Identity
    catalog_ref: OpaqueRef
    items: list[EvidencePackLineageItemV1] = Field(max_length=40)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class GovernanceEvidenceItemV1(_StrictModel):
    evidence_handle: OpaqueKnowledgeHandle
    evidence_ref: OpaqueRef
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_ref: OpaqueRef
    invocation_ordinal: int = Field(ge=1)
    locator_label: str = Field(min_length=1, max_length=500)
    snippet: str = Field(max_length=4096)
    content: str = Field(max_length=12000)
    modalities: list[Modality] = Field(min_length=1, max_length=3)


class GovernanceEvidencePackV1(_StrictModel):
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: Identity
    catalog_ref: OpaqueRef
    items: list[GovernanceEvidenceItemV1] = Field(max_length=40)
    visual_images: list[VisualImagePayloadV1] = Field(
        default_factory=list, max_length=40, repr=False, exclude=True
    )


class DeclaredEvidenceMappingV1(_StrictModel):
    position: int = Field(ge=1)
    handle: Identity
    resolution_status: Literal["resolved", "unresolved"]
    duplicate_of_position: int | None = Field(default=None, ge=1)
    subset_position: int | None = Field(default=None, ge=1)
    reason_code: Literal[
        "resolved",
        "unknown_or_out_of_execution",
        "wrong_handle_kind",
        "model_visible_observation_unavailable",
    ]

    @model_validator(mode="after")
    def require_resolution_shape(self) -> "DeclaredEvidenceMappingV1":
        if self.resolution_status == "resolved":
            if self.subset_position is None or self.reason_code != "resolved":
                raise ValueError("resolved declaration requires a subset position")
        elif self.subset_position is not None or self.reason_code == "resolved":
            raise ValueError("unresolved declaration cannot bind a subset position")
        if (
            self.duplicate_of_position is not None
            and self.duplicate_of_position >= self.position
        ):
            raise ValueError("duplicate declaration must reference an earlier position")
        return self


class ModelVisibleEvidenceObservationV1(_StrictModel):
    result_ref: OpaqueRef
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_ordinal: int = Field(ge=1)
    result_type: Literal[
        "knowledge_search_result",
        "knowledge_inspection_result",
        "knowledge_expansion_result",
        "visual_inspection_result",
    ]
    content_kind: Literal["snippet", "content", "visual"]
    locator_label: str = Field(min_length=1, max_length=500)
    model_visible_content: str = Field(max_length=12000)
    modalities: list[Modality] = Field(min_length=1, max_length=3)


class DeclaredEvidenceItemV1(_StrictModel):
    subset_position: int = Field(ge=1)
    first_declared_position: int = Field(ge=1)
    evidence_handle: OpaqueKnowledgeHandle
    handle_kind: Literal["evidence", "visual"]
    evidence_ref: OpaqueRef
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_result_ref: OpaqueRef
    source_result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_invocation_ordinal: int = Field(ge=1)
    observations: list[ModelVisibleEvidenceObservationV1] = Field(
        min_length=1, max_length=40
    )


class DeclaredEvidenceSubsetV1(_StrictModel):
    schema_version: Literal["declared-evidence-subset-v1"] = (
        "declared-evidence-subset-v1"
    )
    execution_id: Identity
    catalog_ref: OpaqueRef
    mappings: list[DeclaredEvidenceMappingV1] = Field(max_length=100)
    items: list[DeclaredEvidenceItemV1] = Field(max_length=40)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_images: list[VisualImagePayloadV1] = Field(
        default_factory=list, max_length=40, repr=False, exclude=True
    )

    @model_validator(mode="after")
    def require_ordered_subset_mapping(self) -> "DeclaredEvidenceSubsetV1":
        if [item.position for item in self.mappings] != list(
            range(1, len(self.mappings) + 1)
        ):
            raise ValueError("declared evidence mapping positions must be contiguous")
        if [item.subset_position for item in self.items] != list(
            range(1, len(self.items) + 1)
        ):
            raise ValueError("declared evidence subset positions must be contiguous")
        item_by_position = {item.subset_position: item for item in self.items}
        for mapping in self.mappings:
            if mapping.subset_position is None:
                continue
            item = item_by_position.get(mapping.subset_position)
            if item is None or item.evidence_handle != mapping.handle:
                raise ValueError("declared evidence mapping does not match subset")
        image_handles = [image.visual_handle for image in self.visual_images]
        expected_images = [
            item.evidence_handle for item in self.items if item.handle_kind == "visual"
        ]
        if image_handles != expected_images:
            raise ValueError("declared visual evidence carrier order changed")
        return self


class ClaimedEvidenceLineageV1(_StrictModel):
    position: int = Field(ge=1)
    handle: Identity
    resolution_status: Literal["resolved", "unresolved"]
    duplicate_of_position: int | None = Field(default=None, ge=1)
    handle_kind: Literal["evidence", "visual"] | None = None
    evidence_ref: OpaqueRef | None = None
    result_ref: OpaqueRef | None = None
    invocation_ordinal: int | None = Field(default=None, ge=1)
    document_ref: OpaqueRef | None = None
    document_handle: OpaqueKnowledgeHandle | None = None
    lifecycle_epoch: int | None = Field(default=None, ge=1)
    document_version_ref: OpaqueRef | None = None
    processing_revision_ref: OpaqueRef | None = None
    processing_generation_ref: OpaqueRef | None = None
    index_generation_ref: OpaqueRef | None = None
    document_display_name: str | None = Field(default=None, max_length=500)
    document_version_label: str | None = Field(default=None, max_length=200)
    page_number: int | None = Field(default=None, ge=1)
    locator_label: str | None = Field(default=None, max_length=500)


class DiscoveryChannelTraceV1(_StrictModel):
    channel: Literal["lexical", "vector"]
    status: Literal["completed", "failed"]


class DiscoveryCandidateComponentV1(_StrictModel):
    channel: Literal["lexical", "vector"]
    rank: int = Field(ge=1)
    match_ref: OpaqueRef
    locator_label: str = Field(min_length=1, max_length=500)
    page_number: int | None = Field(default=None, ge=1)


class DiscoveryCandidateLineageV1(_StrictModel):
    position: int = Field(ge=1)
    document_handle: OpaqueKnowledgeHandle
    fused_score: str = Field(min_length=3, max_length=100)
    best_component_rank: int = Field(ge=1)
    components: list[DiscoveryCandidateComponentV1] = Field(
        min_length=1, max_length=2
    )
    document_ref: OpaqueRef
    lifecycle_epoch: int = Field(ge=1)
    document_version_ref: OpaqueRef
    processing_revision_ref: OpaqueRef | None = None
    processing_generation_ref: OpaqueRef
    index_generation_ref: OpaqueRef
    document_display_name: str = Field(min_length=1, max_length=500)
    document_version_label: str | None = Field(default=None, max_length=200)
    preview: str | None = Field(default=None, max_length=1000)
    locator_label: str | None = Field(default=None, max_length=500)
    page_number: int | None = Field(default=None, ge=1)


class RelevantDocumentDiscoveryTraceV1(_StrictModel):
    invocation_id: Identity
    result_ref: OpaqueRef
    invocation_ordinal: int = Field(ge=1)
    query_text: str = Field(min_length=1, max_length=4000)
    requested_limit: int = Field(ge=1, le=20)
    ranking_contract: Literal["equal-reciprocal-rank-v1"]
    channels: list[DiscoveryChannelTraceV1] = Field(max_length=2)
    degraded: bool
    failure_code: str | None = None
    candidates: list[DiscoveryCandidateLineageV1]


def knowledge_tool_observation_schema() -> dict:
    return KnowledgeToolObservationEnvelopeV1.model_json_schema()


class RetrievalOwner(Protocol):
    def create_catalog(
        self,
        *,
        execution_id: Identity,
        grant_ref: OpaqueRef,
        generation_retention_ref: OpaqueRef,
        idempotency_key: Identity,
    ) -> KnowledgeCatalogSnapshotRefV1: ...

    def invoke(
        self,
        *,
        execution_id: Identity,
        grant_ref: OpaqueRef,
        catalog_ref: OpaqueRef,
        invocation_ordinal: int,
        action: KnowledgeToolActionV1,
        max_output_tokens: int,
        tokenizer_profile: str,
        max_output_bytes: int = 262_144,
        deadline_at: AwareDatetime | None = None,
    ) -> RetrievalInvocationEnvelopeV1: ...

    def materialize_evidence_pack(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
        evidence_handles: list[OpaqueKnowledgeHandle],
        idempotency_key: Identity,
    ) -> EvidencePackRefV1: ...

    def read_evidence_pack(self, evidence_pack_ref: OpaqueRef) -> EvidencePackRefV1 | None: ...

    def read_governance_evidence_pack(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
        evidence_pack_ref: OpaqueRef,
        evidence_pack_digest: str,
    ) -> GovernanceEvidencePackV1: ...

    def read_declared_evidence_subset(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
        handles: list[Identity],
        visual_images: list[VisualImagePayloadV1],
    ) -> DeclaredEvidenceSubsetV1: ...

    def read_claimed_evidence_lineage(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
        handles: list[Identity],
    ) -> list[ClaimedEvidenceLineageV1]: ...

    def read_discovery_traces(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
    ) -> list[RelevantDocumentDiscoveryTraceV1]: ...

    def count_page_and_visual_handles(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
    ) -> int: ...

    def release_catalog(
        self,
        *,
        execution_id: Identity,
        catalog_ref: OpaqueRef,
        idempotency_key: Identity,
    ) -> None: ...

    def release_execution_catalog(
        self, *, execution_id: Identity, idempotency_key: Identity
    ) -> None: ...


__all__ = [
    name
    for name in globals()
    if name.endswith("V1")
    or name
    in {
        "Modality",
        "OpaqueKnowledgeHandle",
        "RetrievalOwner",
        "knowledge_tool_observation_schema",
    }
]
