from atlas_production.modules.processing_pipeline.public import (
    NavigationEvidenceSource,
    NavigationPageSource,
    build_document_navigation_map,
)


def test_navigation_map_is_deterministic_and_structure_first() -> None:
    kwargs = {
        "document_version_ref": "document-version-1",
        "processing_revision_ref": "processing-revision-1",
        "processing_generation_ref": "processing-generation-1",
        "media_type": "application/pdf",
        "pages": [
            NavigationPageSource(2, "第 2 頁", True),
            NavigationPageSource(1, "第 1 頁", True),
        ],
        "evidence": [
            NavigationEvidenceSource(
                "evidence-figure",
                2,
                "Figure 1. Pin Assignments",
                "RTL8111G pin assignments",
                "figure",
            ),
            NavigationEvidenceSource(
                "evidence-text",
                1,
                "Contents",
                "Table of contents",
                "text",
            ),
        ],
    }

    first = build_document_navigation_map(**kwargs)
    second = build_document_navigation_map(
        **{
            **kwargs,
            "pages": list(reversed(kwargs["pages"])),
            "evidence": list(reversed(kwargs["evidence"])),
        }
    )

    assert first is not None
    assert second == first
    assert [node.kind for node in first.nodes] == ["page", "page", "figure"]
    assert first.nodes[-1].parent_node_ref == first.nodes[1].node_ref
    assert "Pin Assignments" in first.nodes[1].search_text


def test_navigation_map_uses_slide_labels_and_rejects_unsupported_media() -> None:
    slides = build_document_navigation_map(
        document_version_ref="document-version-1",
        processing_revision_ref="processing-revision-1",
        processing_generation_ref="processing-generation-1",
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        pages=[NavigationPageSource(1, "投影片 1", True)],
        evidence=[],
    )
    unsupported = build_document_navigation_map(
        document_version_ref="document-version-1",
        processing_revision_ref="processing-revision-1",
        processing_generation_ref="processing-generation-1",
        media_type="text/plain",
        pages=[NavigationPageSource(1, "第 1 頁", False)],
        evidence=[],
    )

    assert slides is not None
    assert slides.nodes[0].kind == "slide"
    assert unsupported is None
