from datetime import datetime, timezone

from atlas_production.modules.citation_preview.protected_read import (
    ProtectedCitationReadService,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftV1,
    CitationBindingV1,
    ProtectedCitationEvidenceV1,
    ReadProtectedCitationV1,
)


class _Bindings:
    def read(self, draft_ref):
        if draft_ref != "draft-old":
            return None
        return CitationBindingDraftV1(
            draft_ref="draft-old",
            execution_id="execution-old",
            governed_answer_draft_ref="answer-old",
            governed_answer_digest="a" * 64,
            bindings=[
                CitationBindingV1(
                    citation_ref="citation-old",
                    segment_id="segment-old",
                    claim_id="claim-old",
                    evidence_ref="evidence-old",
                )
            ],
            digest="b" * 64,
            created_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )


class _Evidence:
    def __init__(self) -> None:
        self.calls = []

    def read_exact_citation_evidence(self, **facts):
        self.calls.append(facts)
        return ProtectedCitationEvidenceV1(
            citation_ref=facts["evidence_ref"],
            locator_label="Page 1",
            snippet="old exact snippet",
            content="old exact content",
            modality="text",
        )


def test_old_citation_keeps_exact_revision_after_new_current_publication() -> None:
    evidence = _Evidence()
    service = ProtectedCitationReadService(
        bindings=_Bindings(),
        evidence=evidence,
    )
    command = ReadProtectedCitationV1(
        draft_ref="draft-old",
        citation_ref="citation-old",
        evidence_ref="evidence-old",
        document_version_ref="binding-version",
        processing_revision_ref="revision-old",
        processing_generation_ref="processing-generation-1",
        index_generation_ref="index-old",
        page_artifact_ref="page-artifact-old",
    )

    result = service.read_protected(command)

    assert result is not None
    assert result.content == "old exact content"
    assert evidence.calls == [
        {
            "evidence_ref": "evidence-old",
            "document_version_ref": "binding-version",
            "processing_revision_ref": "revision-old",
            "processing_generation_ref": "processing-generation-1",
            "index_generation_ref": "index-old",
            "page_artifact_ref": "page-artifact-old",
        }
    ]
