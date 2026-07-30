import hashlib
from io import BytesIO
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from atlas_production.infrastructure.postgres_turn_knowledge_production import (
    PostgresProductionKnowledgeRowSource,
    _parse_visual_citation_ref,
)
from atlas_production.modules.artifact_storage.errors import ArtifactIntegrityError

from atlas_production.modules.citation_preview.protected_read import (
    ProtectedCitationReadService,
    ProtectedDeclaredEvidenceReadService,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftV1,
    CitationBindingDraftV2,
    CitationBindingV1,
    ProtectedCitationEvidenceV1,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidencePageIntegrityError,
    ReadProtectedCitationV1,
    ReadProtectedDeclaredEvidenceV1,
    declared_evidence_protected_open_ref,
)
from atlas_production.modules.retrieval.public import (
    EvidencePackLineageItemV1,
    EvidencePackRefV1,
)


NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


class Bindings:
    def read(self, draft_ref):
        if draft_ref != "draft-1":
            return None
        return CitationBindingDraftV1(
            draft_ref=draft_ref,
            execution_id="execution-1",
            governed_answer_draft_ref="answer-1",
            governed_answer_digest="a" * 64,
            bindings=[
                CitationBindingV1(
                    citation_ref="citation-1",
                    segment_id="segment-1",
                    claim_id="claim-1",
                    evidence_ref="evidence-1",
                )
            ],
            digest="b" * 64,
            created_at=NOW,
        )


class Evidence:
    def __init__(self):
        self.calls = []

    def read_exact_citation_evidence(self, **facts):
        self.calls.append(facts)
        return ProtectedCitationEvidenceV1(
            citation_ref=facts["evidence_ref"],
            locator_label="Page 1",
            snippet="exact snippet",
            content="exact content",
            modality="text",
        )


class Pages:
    def __init__(
        self,
        result: ProtectedDeclaredEvidencePageV1 | None,
    ) -> None:
        self.result = result
        self.calls = []

    def read_exact_declared_evidence_page(
        self, command, *, accepted_media_types
    ):
        self.calls.append((command, accepted_media_types))
        return self.result


def _command(**updates):
    values = {
        "draft_ref": "draft-1",
        "citation_ref": "citation-1",
        "evidence_ref": "evidence-1",
        "document_version_ref": "version-1",
        "processing_revision_ref": "revision-1",
        "processing_generation_ref": "processing-generation-1",
        "index_generation_ref": "index-1",
    }
    values.update(updates)
    return ReadProtectedCitationV1(**values)


def test_protected_read_requires_exact_immutable_binding_before_evidence_read() -> None:
    evidence = Evidence()
    service = ProtectedCitationReadService(bindings=Bindings(), evidence=evidence)

    result = service.read_protected(_command())

    assert result is not None
    assert result.citation_ref == "citation-1"
    assert evidence.calls == [
        {
            "evidence_ref": "evidence-1",
            "document_version_ref": "version-1",
            "processing_revision_ref": "revision-1",
            "processing_generation_ref": "processing-generation-1",
            "index_generation_ref": "index-1",
            "page_artifact_ref": None,
        }
    ]


def test_protected_read_rejects_citation_or_evidence_substitution_without_source_call() -> None:
    evidence = Evidence()
    service = ProtectedCitationReadService(bindings=Bindings(), evidence=evidence)

    assert service.read_protected(_command(citation_ref="citation-other")) is None
    assert service.read_protected(_command(evidence_ref="evidence-other")) is None
    assert evidence.calls == []


class RawDeclarations:
    def read_raw_declared_evidence(self, execution_id):
        if execution_id != "execution-1":
            return None
        return ["kh_evidence_A", "kh_evidence_A", "kh_evidence_B"]


class EvidencePacks:
    def __init__(self, page_artifact_ref=None):
        self.page_artifact_ref = page_artifact_ref

    def read_evidence_pack(self, evidence_pack_ref):
        if evidence_pack_ref != "evidence-pack-1":
            return None
        return EvidencePackRefV1(
            evidence_pack_ref=evidence_pack_ref,
            execution_id="execution-1",
            catalog_ref="catalog-1",
            items=[
                EvidencePackLineageItemV1(
                    evidence_handle="kh_evidence_A",
                    evidence_ref="evidence-1",
                    evidence_digest="a" * 64,
                    resource_ref="resource-1",
                    lifecycle_epoch=3,
                    document_version_ref="version-1",
                    processing_revision_ref="revision-1",
                    processing_generation_ref="processing-generation-1",
                    index_generation_ref="index-1",
                    page_artifact_ref=self.page_artifact_ref,
                    result_ref="result-1",
                    invocation_ordinal=2,
                )
            ],
            digest="b" * 64,
            created_at=NOW,
        )


def _declared_command(**updates):
    values = {
        "execution_id": "execution-1",
        "declaration_position": 1,
        "evidence_handle": "kh_evidence_A",
        "evidence_pack_ref": "evidence-pack-1",
        "evidence_pack_digest": "b" * 64,
        "evidence_ref": "evidence-1",
        "evidence_digest": "a" * 64,
        "resource_ref": "resource-1",
        "lifecycle_epoch": 3,
        "document_version_ref": "version-1",
        "processing_revision_ref": "revision-1",
        "processing_generation_ref": "processing-generation-1",
        "index_generation_ref": "index-1",
        "result_ref": "result-1",
        "invocation_ordinal": 2,
    }
    values.update(updates)
    return ReadProtectedDeclaredEvidenceV1(**values)


def test_declared_evidence_exact_read_has_no_formal_citation_authority() -> None:
    evidence = Evidence()
    service = ProtectedDeclaredEvidenceReadService(
        declarations=RawDeclarations(),
        evidence_packs=EvidencePacks(),
        evidence=evidence,
    )
    command = _declared_command()
    protected_open_ref = declared_evidence_protected_open_ref(command)

    result = service.read_protected_declared(
        command.model_copy(update={"protected_open_ref": protected_open_ref})
    )

    assert result is not None
    assert result.evidence_handle == "kh_evidence_A"
    assert "citation_ref" not in type(result).model_fields
    assert evidence.calls == [
        {
            "evidence_ref": "evidence-1",
            "document_version_ref": "version-1",
            "processing_revision_ref": "revision-1",
            "processing_generation_ref": "processing-generation-1",
            "index_generation_ref": "index-1",
            "page_artifact_ref": None,
        }
    ]


def test_declared_evidence_rejects_wrong_position_or_lineage_substitution() -> None:
    evidence = Evidence()
    pages = Pages(
        ProtectedDeclaredEvidencePageV1(
            media_type="application/pdf",
            content=b"%PDF exact page",
        )
    )
    service = ProtectedDeclaredEvidenceReadService(
        declarations=RawDeclarations(),
        evidence_packs=EvidencePacks("page-artifact-1"),
        evidence=evidence,
        pages=pages,
    )

    assert service.read_protected_declared(
        _declared_command(declaration_position=3)
    ) is None
    assert service.read_protected_declared(
        _declared_command(evidence_ref="evidence-substituted")
    ) is None
    assert service.read_protected_declared(
        _declared_command(protected_open_ref="opaque-substituted"),
        accepted_page_media_types=frozenset({"application/pdf"}),
    ) is None
    assert evidence.calls == []
    assert pages.calls == []


def test_declared_evidence_page_is_read_only_after_exact_evidence_validation() -> None:
    evidence = Evidence()
    pages = Pages(
        ProtectedDeclaredEvidencePageV1(
            media_type="application/pdf",
            content=b"%PDF exact page",
        )
    )
    service = ProtectedDeclaredEvidenceReadService(
        declarations=RawDeclarations(),
        evidence_packs=EvidencePacks("page-artifact-1"),
        evidence=evidence,
        pages=pages,
    )
    command = _declared_command(page_artifact_ref="page-artifact-1")
    command = command.model_copy(
        update={
            "protected_open_ref": declared_evidence_protected_open_ref(command)
        }
    )

    result = service.read_protected_declared(
        command,
        accepted_page_media_types=frozenset({"application/pdf"}),
    )

    assert result == ProtectedDeclaredEvidencePageV1(
        media_type="application/pdf",
        content=b"%PDF exact page",
    )
    assert len(evidence.calls) == 1
    assert pages.calls == [(command, frozenset({"application/pdf"}))]


def test_declared_evidence_page_unavailable_preserves_json_representation() -> None:
    service = ProtectedDeclaredEvidenceReadService(
        declarations=RawDeclarations(),
        evidence_packs=EvidencePacks(),
        evidence=Evidence(),
        pages=Pages(None),
    )

    result = service.read_protected_declared(
        _declared_command(),
        accepted_page_media_types=frozenset({"image/png"}),
    )

    assert result is not None
    assert result.content == "exact content"


def test_citation_binding_v2_is_structurally_empty_and_non_authoritative() -> None:
    draft = CitationBindingDraftV2(
        draft_ref="citation-draft-v2",
        execution_id="execution-1",
        governed_answer_draft_ref="answer-draft-v2",
        governed_answer_digest="a" * 64,
        digest="b" * 64,
        created_at=NOW,
    )

    assert draft.bindings == []
    assert draft.schema_version == "citation-binding-draft-v2"


class _VisualCitationSession:
    def __init__(self, *, artifact_status: str = "active") -> None:
        self.page = SimpleNamespace(
            id="page-artifact-2",
            document_version_id="version-1",
            processing_revision_id="revision-1",
            payload={
                "artifact_kind": "pdf_single_page",
                "storage_artifact_id": "artifact-page-2",
            }
        )
        self.artifact = SimpleNamespace(
            artifact_class="document_page_pdf",
            lifecycle_status=artifact_status,
            document_version_id="version-1",
            processing_generation=3,
            page_number=2,
        )
        self.version = SimpleNamespace(
            document_id="document-1",
        )
        self.document = SimpleNamespace(
            document_id="document-1",
            lifecycle_status="active",
            processing_identity_id="identity-1",
        )
        self.index = SimpleNamespace(
            processing_revision_id="revision-1",
            source_processing_generation=3,
            manifest_digest="a" * 64,
            status="retired",
        )
        self.revision = SimpleNamespace(
            processing_revision_id="revision-1",
            processing_identity_id="identity-1",
            state="ready",
            manifest_digest="a" * 64,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _statement):
        return SimpleNamespace(
            one_or_none=lambda: (
                self.page,
                self.index,
            )
        )

    def get(self, row_type, identity):
        values = {
            ("AtlasDocumentVersionRow", "version-1"): self.version,
            ("AtlasDocumentRow", "document-1"): self.document,
            ("AtlasIndexGenerationRow", "index-1"): self.index,
            ("AtlasProcessingRevisionRow", "revision-1"): self.revision,
            ("AtlasArtifactRow", "artifact-page-2"): self.artifact,
        }
        return values.get((row_type.__name__, identity))


def test_visual_citation_returns_exact_page_bbox_only_for_current_page_artifact() -> None:
    digest = "d" * 64
    evidence_ref = f"visual|kh_document_A|2|1000,2000,9000,8000|{digest}"
    assert _parse_visual_citation_ref(evidence_ref) == (
        2,
        (1000, 2000, 9000, 8000),
        digest,
    )
    source = PostgresProductionKnowledgeRowSource(
        lambda: _VisualCitationSession()
    )

    result = source.read_exact_citation_evidence(
        evidence_ref=evidence_ref,
        document_version_ref="version-1",
        processing_revision_ref="revision-1",
        processing_generation_ref="processing-generation-3",
        index_generation_ref="index-1",
        page_artifact_ref="page-artifact-2",
    )

    assert result is not None
    assert result.locator_label == "Page 2 bbox [1000,2000,9000,8000]"
    assert result.modality == "figure"

    stale = PostgresProductionKnowledgeRowSource(
        lambda: _VisualCitationSession(artifact_status="tombstoned")
    )
    assert stale.read_exact_citation_evidence(
        evidence_ref=evidence_ref,
        document_version_ref="version-1",
        processing_revision_ref="revision-1",
        processing_generation_ref="processing-generation-3",
        index_generation_ref="index-1",
        page_artifact_ref="page-artifact-2",
    ) is None


class _PageFilesystem:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = []

    def open_read(self, opaque_ref, *, expected_size):
        self.calls.append((opaque_ref, expected_size))
        return BytesIO(self.content)


class _FailingPageFilesystem:
    def open_read(self, _opaque_ref, *, expected_size):
        raise ArtifactIntegrityError(
            f"missing page bytes with expected size {expected_size}"
        )


class _DeclaredPageSession:
    def __init__(
        self,
        *,
        media_type: str,
        content: bytes,
        processing_revision_ref: str = "revision-1",
        artifact_status: str = "active",
    ) -> None:
        digest = hashlib.sha256(content).hexdigest()
        page_kind, artifact_class = {
            "application/pdf": ("pdf_single_page", "document_page_pdf"),
            "image/png": ("page_image", "page_image"),
        }[media_type]
        self.version = SimpleNamespace(document_version_id="version-1")
        self.revision = SimpleNamespace(
            processing_revision_id="revision-1",
            state="ready",
        )
        self.page = SimpleNamespace(
            id="page-artifact-1",
            document_version_id="version-1",
            processing_revision_id=processing_revision_ref,
            processing_generation=3,
            source_page_index=0,
            payload={
                "artifact_kind": page_kind,
                "storage_artifact_id": "storage-page-1",
                "artifact_digest": digest,
                "content_length": len(content),
            },
        )
        self.artifact = SimpleNamespace(
            artifact_id="storage-page-1",
            artifact_class=artifact_class,
            blob_id="blob-page-1",
            lifecycle_status=artifact_status,
            content_type=media_type,
            document_version_id="version-1",
            processing_generation=3,
            page_number=1,
            checksum_algorithm="sha256",
            checksum_value=digest,
            byte_size=len(content),
        )
        self.blob = SimpleNamespace(
            blob_id="blob-page-1",
            opaque_ref="opaque-page-1",
            status="committed",
            checksum_algorithm="sha256",
            checksum_value=digest,
            byte_size=len(content),
            content_type=media_type,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, row_type, identity):
        values = {
            ("AtlasDocumentVersionRow", "version-1"): self.version,
            ("AtlasProcessingRevisionRow", "revision-1"): self.revision,
            ("AtlasEvidencePageArtifactRow", "page-artifact-1"): self.page,
            ("AtlasArtifactRow", "storage-page-1"): self.artifact,
            ("AtlasStorageBlobRow", "blob-page-1"): self.blob,
        }
        return values.get((row_type.__name__, identity))


def _page_command() -> ReadProtectedDeclaredEvidenceV1:
    return _declared_command(
        processing_generation_ref="processing-generation-3",
        page_artifact_ref="page-artifact-1",
    )


@pytest.mark.parametrize(
    ("media_type", "content"),
    [
        ("application/pdf", b"%PDF exact complete page"),
        ("image/png", b"\\x89PNG exact complete page"),
    ],
)
def test_exact_declared_page_reads_pinned_complete_pdf_or_png(
    media_type: str,
    content: bytes,
) -> None:
    filesystem = _PageFilesystem(content)
    source = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type=media_type,
            content=content,
        ),
        filesystem,
    )

    result = source.read_exact_declared_evidence_page(
        _page_command(),
        accepted_media_types=frozenset({media_type}),
    )

    assert result == ProtectedDeclaredEvidencePageV1(
        media_type=media_type,
        content=content,
    )
    assert filesystem.calls == [("opaque-page-1", len(content))]


def test_exact_declared_page_wrong_revision_or_missing_blob_fails_closed() -> None:
    content = b"%PDF exact complete page"
    filesystem = _PageFilesystem(content)
    wrong_revision = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type="application/pdf",
            content=content,
            processing_revision_ref="revision-other",
        ),
        filesystem,
    )
    inactive = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type="application/pdf",
            content=content,
            artifact_status="tombstoned",
        ),
        filesystem,
    )

    with pytest.raises(ProtectedDeclaredEvidencePageIntegrityError):
        wrong_revision.read_exact_declared_evidence_page(
            _page_command(),
            accepted_media_types=frozenset({"application/pdf"}),
        )
    with pytest.raises(ProtectedDeclaredEvidencePageIntegrityError):
        inactive.read_exact_declared_evidence_page(
            _page_command(),
            accepted_media_types=frozenset({"application/pdf"}),
        )
    assert filesystem.calls == []


def test_exact_declared_page_unsupported_accept_falls_back_without_byte_read() -> None:
    content = b"%PDF exact complete page"
    filesystem = _PageFilesystem(content)
    source = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type="application/pdf",
            content=content,
        ),
        filesystem,
    )

    assert source.read_exact_declared_evidence_page(
        _page_command(),
        accepted_media_types=frozenset({"image/png"}),
    ) is None
    assert filesystem.calls == []


def test_exact_declared_page_truncated_bytes_fail_integrity() -> None:
    content = b"%PDF exact complete page"
    source = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type="application/pdf",
            content=content,
        ),
        _PageFilesystem(content[:-1]),
    )

    with pytest.raises(ProtectedDeclaredEvidencePageIntegrityError):
        source.read_exact_declared_evidence_page(
            _page_command(),
            accepted_media_types=frozenset({"application/pdf"}),
        )


def test_exact_declared_page_translates_storage_integrity_failure() -> None:
    content = b"%PDF exact complete page"
    source = PostgresProductionKnowledgeRowSource(
        lambda: _DeclaredPageSession(
            media_type="application/pdf",
            content=content,
        ),
        _FailingPageFilesystem(),
    )

    with pytest.raises(ProtectedDeclaredEvidencePageIntegrityError):
        source.read_exact_declared_evidence_page(
            _page_command(),
            accepted_media_types=frozenset({"application/pdf"}),
        )
