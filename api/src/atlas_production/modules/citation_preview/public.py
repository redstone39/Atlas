from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from atlas_production.modules.result_governance.public import (
    GovernedAnswerDraftV1,
    GovernedAnswerDraftV2,
)
from atlas_production.modules.retrieval.public import EvidencePackRefV1


class _StrictCitationDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identity = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=300)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MaterializeCitationBindingDraftV1(_StrictCitationDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    governed_answer: GovernedAnswerDraftV1
    idempotency_key: Identity


class CitationBindingV1(_StrictCitationDraftModel):
    citation_ref: OpaqueRef
    segment_id: Identity
    claim_id: Identity
    evidence_ref: OpaqueRef


class CitationBindingDraftV1(_StrictCitationDraftModel):
    draft_ref: OpaqueRef
    schema_version: Literal["citation-binding-draft-v1"] = "citation-binding-draft-v1"
    execution_id: Identity
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    bindings: list[CitationBindingV1]
    digest: Digest
    created_at: AwareDatetime


class MaterializeCitationBindingDraftV2(_StrictCitationDraftModel):
    draft_ref: OpaqueRef
    execution_id: Identity
    governed_answer: GovernedAnswerDraftV2
    idempotency_key: Identity


class CitationBindingDraftV2(_StrictCitationDraftModel):
    """A soft-review terminal link that grants no formal citation authority."""

    draft_ref: OpaqueRef
    schema_version: Literal["citation-binding-draft-v2"] = "citation-binding-draft-v2"
    execution_id: Identity
    governed_answer_draft_ref: OpaqueRef
    governed_answer_digest: Digest
    bindings: list[CitationBindingV1] = Field(default_factory=list, max_length=0)
    digest: Digest
    created_at: AwareDatetime


class ReleaseCitationBindingDraftV1(_StrictCitationDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    idempotency_key: Identity


class CitationBindingDraftReleaseV1(_StrictCitationDraftModel):
    release_ref: OpaqueRef
    execution_id: Identity
    draft_ref: OpaqueRef
    schema_version: Literal["citation-binding-draft-release-v1"] = "citation-binding-draft-release-v1"
    released_at: AwareDatetime


class ReadProtectedCitationV1(_StrictCitationDraftModel):
    draft_ref: OpaqueRef
    citation_ref: OpaqueRef
    evidence_ref: OpaqueRef
    document_version_ref: OpaqueRef
    processing_revision_ref: OpaqueRef
    processing_generation_ref: OpaqueRef
    index_generation_ref: OpaqueRef
    page_artifact_ref: OpaqueRef | None = None


class ProtectedCitationEvidenceV1(_StrictCitationDraftModel):
    citation_ref: OpaqueRef
    locator_label: str = Field(min_length=1, max_length=500)
    snippet: str = Field(max_length=4096)
    content: str = Field(max_length=12000)
    modality: Literal["text", "table", "figure"]


class ReadProtectedDeclaredEvidenceV1(_StrictCitationDraftModel):
    execution_id: Identity
    declaration_position: int = Field(ge=1, le=100)
    evidence_handle: Identity
    evidence_pack_ref: OpaqueRef
    evidence_pack_digest: Digest
    evidence_ref: OpaqueRef
    evidence_digest: Digest
    resource_ref: OpaqueRef
    lifecycle_epoch: int = Field(ge=1)
    document_version_ref: OpaqueRef
    processing_revision_ref: OpaqueRef
    processing_generation_ref: OpaqueRef
    index_generation_ref: OpaqueRef
    page_artifact_ref: OpaqueRef | None = None
    result_ref: OpaqueRef
    invocation_ordinal: int = Field(ge=1)
    protected_open_ref: OpaqueRef | None = None


def declared_evidence_protected_open_ref(
    command: ReadProtectedDeclaredEvidenceV1,
) -> str:
    """Derive a non-authoritative opaque ref from the exact protected-read pins."""

    payload = command.model_dump(
        mode="json",
        exclude={"protected_open_ref"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return "declared-evidence-open-" + hashlib.sha256(canonical).hexdigest()


class ProtectedDeclaredEvidenceV1(_StrictCitationDraftModel):
    """Exact declared evidence without a formal CitationBindingV1 identity."""

    evidence_handle: Identity
    locator_label: str = Field(min_length=1, max_length=500)
    snippet: str = Field(max_length=4096)
    content: str = Field(max_length=12000)
    modality: Literal["text", "table", "figure"]


@dataclass(frozen=True, slots=True)
class ProtectedDeclaredEvidencePageV1:
    """Internal exact-page representation returned only after protected read."""

    media_type: Literal["application/pdf", "image/png"]
    content: bytes


class ProtectedDeclaredEvidencePageIntegrityError(RuntimeError):
    """The pinned page graph or bytes do not match their immutable metadata."""


class ProtectedCitationEvidenceSource(Protocol):
    def read_exact_citation_evidence(
        self,
        *,
        evidence_ref: OpaqueRef,
        document_version_ref: OpaqueRef,
        processing_revision_ref: OpaqueRef,
        processing_generation_ref: OpaqueRef,
        index_generation_ref: OpaqueRef,
        page_artifact_ref: OpaqueRef | None = None,
    ) -> ProtectedCitationEvidenceV1 | None: ...


class ProtectedDeclaredEvidencePageSource(Protocol):
    def read_exact_declared_evidence_page(
        self,
        command: ReadProtectedDeclaredEvidenceV1,
        *,
        accepted_media_types: frozenset[str],
    ) -> ProtectedDeclaredEvidencePageV1 | None: ...


class ProtectedCitationReadOwner(Protocol):
    def read_protected(
        self, command: ReadProtectedCitationV1
    ) -> ProtectedCitationEvidenceV1 | None: ...


class RawDeclaredEvidenceSource(Protocol):
    def read_raw_declared_evidence(
        self, execution_id: Identity
    ) -> list[Identity] | None: ...


class DeclaredEvidencePackSource(Protocol):
    def read_evidence_pack(
        self, evidence_pack_ref: OpaqueRef
    ) -> EvidencePackRefV1 | None: ...


class ProtectedDeclaredEvidenceReadOwner(Protocol):
    def read_protected_declared(
        self,
        command: ReadProtectedDeclaredEvidenceV1,
        *,
        accepted_page_media_types: frozenset[str] = frozenset(),
    ) -> ProtectedDeclaredEvidenceV1 | ProtectedDeclaredEvidencePageV1 | None: ...


class CitationBindingDraftOwner(Protocol):
    def materialize(self, command: MaterializeCitationBindingDraftV1) -> CitationBindingDraftV1: ...

    def read(self, draft_ref: OpaqueRef) -> CitationBindingDraftV1 | None: ...

    def release(self, command: ReleaseCitationBindingDraftV1) -> CitationBindingDraftReleaseV1: ...


class CitationBindingDraftOwnerV2(Protocol):
    def materialize_v2(
        self, command: MaterializeCitationBindingDraftV2
    ) -> CitationBindingDraftV2: ...

    def read_v2(self, draft_ref: OpaqueRef) -> CitationBindingDraftV2 | None: ...


__all__ = [
    "CitationBindingDraftOwner",
    "CitationBindingDraftOwnerV2",
    "CitationBindingDraftReleaseV1",
    "CitationBindingDraftV1",
    "CitationBindingDraftV2",
    "CitationBindingV1",
    "DeclaredEvidencePackSource",
    "MaterializeCitationBindingDraftV1",
    "MaterializeCitationBindingDraftV2",
    "ProtectedCitationEvidenceSource",
    "ProtectedCitationEvidenceV1",
    "ProtectedCitationReadOwner",
    "ProtectedDeclaredEvidencePageIntegrityError",
    "ProtectedDeclaredEvidencePageSource",
    "ProtectedDeclaredEvidencePageV1",
    "ProtectedDeclaredEvidenceReadOwner",
    "ProtectedDeclaredEvidenceV1",
    "RawDeclaredEvidenceSource",
    "ReadProtectedDeclaredEvidenceV1",
    "ReadProtectedCitationV1",
    "ReleaseCitationBindingDraftV1",
    "declared_evidence_protected_open_ref",
]
