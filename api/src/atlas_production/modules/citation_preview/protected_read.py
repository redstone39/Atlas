"""Protected citation read coordinator over immutable bindings and exact evidence."""

from __future__ import annotations

from .public import (
    CitationBindingDraftOwner,
    DeclaredEvidencePackSource,
    ProtectedCitationEvidenceSource,
    ProtectedCitationEvidenceV1,
    ProtectedDeclaredEvidencePageSource,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceV1,
    RawDeclaredEvidenceSource,
    ReadProtectedCitationV1,
    ReadProtectedDeclaredEvidenceV1,
    declared_evidence_protected_open_ref,
)


class ProtectedCitationReadService:
    """Resolve only a binding-authorized exact evidence ref.

    Caller authorization and lineage visibility are checked by the Workspace
    application immediately before this service is invoked. This owner then
    prevents citation/evidence substitution and performs a read-only exact-pin
    lookup without opening an owner write transaction.
    """

    def __init__(
        self,
        *,
        bindings: CitationBindingDraftOwner,
        evidence: ProtectedCitationEvidenceSource,
    ) -> None:
        self._bindings = bindings
        self._evidence = evidence

    def read_protected(
        self, command: ReadProtectedCitationV1
    ) -> ProtectedCitationEvidenceV1 | None:
        draft = self._bindings.read(command.draft_ref)
        if draft is None:
            return None
        binding = next(
            (
                item
                for item in draft.bindings
                if item.citation_ref == command.citation_ref
                and item.evidence_ref == command.evidence_ref
            ),
            None,
        )
        if binding is None:
            return None
        evidence = self._evidence.read_exact_citation_evidence(
            evidence_ref=command.evidence_ref,
            document_version_ref=command.document_version_ref,
            processing_revision_ref=command.processing_revision_ref,
            processing_generation_ref=command.processing_generation_ref,
            index_generation_ref=command.index_generation_ref,
            page_artifact_ref=command.page_artifact_ref,
        )
        if evidence is None:
            return None
        return evidence.model_copy(update={"citation_ref": command.citation_ref})


class ProtectedDeclaredEvidenceReadService:
    """Open raw-declared exact evidence without minting formal citation authority.

    Workspace owns the request-time ACL check. This service verifies the raw
    declaration position and the caller-supplied immutable evidence-pack
    lineage before reusing the existing exact evidence source.
    """

    def __init__(
        self,
        *,
        declarations: RawDeclaredEvidenceSource,
        evidence_packs: DeclaredEvidencePackSource,
        evidence: ProtectedCitationEvidenceSource,
        pages: ProtectedDeclaredEvidencePageSource | None = None,
    ) -> None:
        self._declarations = declarations
        self._evidence_packs = evidence_packs
        self._evidence = evidence
        self._pages = pages

    def read_protected_declared(
        self,
        command: ReadProtectedDeclaredEvidenceV1,
        *,
        accepted_page_media_types: frozenset[str] = frozenset(),
    ) -> ProtectedDeclaredEvidenceV1 | ProtectedDeclaredEvidencePageV1 | None:
        declared = self._declarations.read_raw_declared_evidence(
            command.execution_id
        )
        if (
            declared is None
            or command.declaration_position > len(declared)
            or declared[command.declaration_position - 1] != command.evidence_handle
        ):
            return None

        pack = self._evidence_packs.read_evidence_pack(command.evidence_pack_ref)
        if (
            pack is None
            or pack.execution_id != command.execution_id
            or pack.evidence_pack_ref != command.evidence_pack_ref
            or pack.digest != command.evidence_pack_digest
        ):
            return None
        lineage = next(
            (
                item
                for item in pack.items
                if item.evidence_handle == command.evidence_handle
                and item.evidence_ref == command.evidence_ref
            ),
            None,
        )
        if lineage is None or any(
            (
                lineage.evidence_digest != command.evidence_digest,
                lineage.resource_ref != command.resource_ref,
                lineage.lifecycle_epoch != command.lifecycle_epoch,
                lineage.document_version_ref != command.document_version_ref,
                lineage.processing_revision_ref != command.processing_revision_ref,
                lineage.processing_generation_ref
                != command.processing_generation_ref,
                lineage.index_generation_ref != command.index_generation_ref,
                lineage.page_artifact_ref != command.page_artifact_ref,
                lineage.result_ref != command.result_ref,
                lineage.invocation_ordinal != command.invocation_ordinal,
            )
        ):
            return None
        if (
            command.protected_open_ref is not None
            and command.protected_open_ref
            != declared_evidence_protected_open_ref(command)
        ):
            return None

        evidence = self._evidence.read_exact_citation_evidence(
            evidence_ref=command.evidence_ref,
            document_version_ref=command.document_version_ref,
            processing_revision_ref=command.processing_revision_ref,
            processing_generation_ref=command.processing_generation_ref,
            index_generation_ref=command.index_generation_ref,
            page_artifact_ref=command.page_artifact_ref,
        )
        if evidence is None:
            return None
        if accepted_page_media_types and self._pages is not None:
            page = self._pages.read_exact_declared_evidence_page(
                command,
                accepted_media_types=accepted_page_media_types,
            )
            if page is not None:
                return page
        return ProtectedDeclaredEvidenceV1(
            evidence_handle=command.evidence_handle,
            locator_label=evidence.locator_label,
            snippet=evidence.snippet,
            content=evidence.content,
            modality=evidence.modality,
        )


__all__ = [
    "ProtectedCitationReadService",
    "ProtectedDeclaredEvidenceReadService",
]
