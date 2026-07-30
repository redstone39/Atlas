from atlas_production.async_runtime.workflows import (
    _aligned_office_page,
    _alignment_method,
    _page_label,
)
from atlas_production.infrastructure.office_renderer_adapter import (
    OfficeRenderResult,
    RenderedOfficePage,
)


def rendered(*page_texts: str) -> OfficeRenderResult:
    return OfficeRenderResult(
        renderer_version="test",
        renderer_config_digest="0" * 64,
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_sha256="0" * 64,
        render_dpi=144,
        pdf_filter="writer_pdf_Export",
        pages=tuple(
            RenderedOfficePage(
                page_number=index,
                width=100,
                height=200,
                sha256="0" * 64,
                content=b"png",
                normalized_text=text,
            )
            for index, text in enumerate(page_texts, start=1)
        ),
    )


def test_alignment_requires_one_unambiguous_normalized_page_span() -> None:
    assert _aligned_office_page({}, "1", rendered("value 10")) is None
    assert _aligned_office_page({}, "Target value", rendered(
        "other page", "Target\n   value is here"
    )) == 2
    assert _aligned_office_page({}, "repeated", rendered(
        "repeated here", "also repeated here"
    )) is None


def test_powerpoint_declared_page_requires_text_or_valid_image_geometry() -> None:
    pages = rendered("first slide", "second slide")
    assert _aligned_office_page(
        {"selector_kind": "powerpoint_shape", "slide_number": True},
        "first slide",
        pages,
    ) is None
    assert _aligned_office_page(
        {"selector_kind": "powerpoint_shape", "slide_number": 2},
        "first slide",
        pages,
    ) is None
    assert _aligned_office_page(
        {
            "selector_kind": "powerpoint_image",
            "slide_number": 2,
            "left": 10,
            "top": 20,
            "width": 30,
            "height": 40,
            "slide_width": 100,
            "slide_height": 100,
        },
        "offline visual inference summary",
        pages,
    ) == 2


def test_word_and_excel_image_anchors_require_one_shared_page() -> None:
    pages = rendered(
        "intro text before image image caption after image",
        "other worksheet row",
    )
    assert _aligned_office_page(
        {
            "selector_kind": "word_image",
            "alignment_anchors": ["text before image", "image caption"],
        },
        "OCR text that is not in the PDF text layer",
        pages,
    ) == 1
    assert _aligned_office_page(
        {
            "selector_kind": "excel_image",
            "alignment_anchors": ["image caption", "other worksheet row"],
        },
        "visual summary",
        pages,
    ) is None


def test_citation_locator_labels_match_each_office_format() -> None:
    assert _page_label("docx", 3) == "第 3 頁"
    assert _page_label("pptx", 4) == "投影片 4"
    assert _page_label(
        "xlsx", 2, locator={"sheet_name": "BOM"}
    ) == "BOM · 預覽第 2 頁"


def test_alignment_provenance_uses_the_public_contract_values() -> None:
    assert _alignment_method({"selector_kind": "powerpoint_image"}) == "image_region"
    assert _alignment_method({"slide_number": 2}) == "slide_identity"
    assert _alignment_method({"table_index": 1}) == "table_text_exact"
    assert _alignment_method({"alignment_anchors": ["before", "after"]}) == (
        "normalized_text_exact"
    )
