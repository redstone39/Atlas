from io import BytesIO
import hashlib
import json
import struct
import zipfile
import zlib

import httpx
import pytest

from atlas_production.infrastructure.office_renderer_adapter import (
    MAX_RENDERED_PAGES,
    OfficeRendererError,
    OfficeRendererAdapter,
    parse_office_render_archive,
    parse_pdf_page_raster_archive,
)


def rgb_png(width: int = 100, height: int = 200) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + kind
            + data
            + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    rows = b"".join(b"\0" + b"\x20\x40\x60" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def rgb_png_with_unapproved_chunk(width: int = 2, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + kind
            + data
            + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    rows = b"".join(b"\0" + b"\x20\x40\x60" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"unapproved=metadata")
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def archive_for(
    source: bytes,
    page: bytes | None = None,
    *,
    width: int = 100,
    height: int = 200,
) -> bytes:
    page = page if page is not None else rgb_png(width, height)
    source_digest = hashlib.sha256(source).hexdigest()
    page_digest = hashlib.sha256(page).hexdigest()
    manifest = {
        "schema_version": "atlas-office-render-result-v1",
        "renderer_version": "atlas-office-renderer-v1",
        "renderer_config_digest": "ffa7b5894ee3b14fb0dbb3eb3025c284381530f2584b19b5af3703404560ea79",
        "source_mime": "application/msword",
        "source_sha256": source_digest,
        "render_dpi": 144,
        "pdf_filter": "writer_pdf_Export",
        "pages": [{
            "page_number": 1, "width": width, "height": height,
            "sha256": page_digest, "byte_length": len(page),
            "path": "pages/1.png", "normalized_text": "Visible page text",
        }],
        "converted_document": {
            "path": "converted.docx",
            "canonical_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "sha256": hashlib.sha256(b"docx").hexdigest(), "byte_length": 4,
        },
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("pages/1.png", page)
        archive.writestr("converted.docx", b"docx")
    return output.getvalue()


def archive_with_page_count(source: bytes, page_count: int) -> bytes:
    page = rgb_png(1, 1)
    manifest = {
        "schema_version": "atlas-office-render-result-v1",
        "renderer_version": "atlas-office-renderer-v1",
        "renderer_config_digest": "ffa7b5894ee3b14fb0dbb3eb3025c284381530f2584b19b5af3703404560ea79",
        "source_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "render_dpi": 144,
        "pdf_filter": "writer_pdf_Export",
        "pages": [
            {
                "page_number": number,
                "width": 1,
                "height": 1,
                "sha256": hashlib.sha256(page).hexdigest(),
                "byte_length": len(page),
                "path": f"pages/{number}.png",
                "normalized_text": "",
            }
            for number in range(1, page_count + 1)
        ],
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for number in range(1, page_count + 1):
            archive.writestr(f"pages/{number}.png", page)
    return output.getvalue()


def test_archive_is_digest_bound_and_returns_pages_and_conversion() -> None:
    source = b"legacy"
    result = parse_office_render_archive(
        archive_for(source),
        expected_source_mime="application/msword",
        expected_source_sha256=hashlib.sha256(source).hexdigest(),
    )
    assert result.pages[0].normalized_text == "Visible page text"
    assert result.pages[0].width == 100
    assert result.renderer_config_digest == "ffa7b5894ee3b14fb0dbb3eb3025c284381530f2584b19b5af3703404560ea79"
    assert result.converted_document == b"docx"


def test_archive_accepts_3000_pages_and_rejects_3001_before_page_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"office-capacity-boundary"
    source_digest = hashlib.sha256(source).hexdigest()
    monkeypatch.setenv("ATLAS_PDF_MAX_PAGES", "9999")

    accepted = parse_office_render_archive(
        archive_with_page_count(source, MAX_RENDERED_PAGES),
        expected_source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        expected_source_sha256=source_digest,
    )
    assert len(accepted.pages) == MAX_RENDERED_PAGES

    oversized = archive_with_page_count(source, MAX_RENDERED_PAGES + 1)
    with pytest.raises(OfficeRendererError, match="artifact_too_large"):
        parse_office_render_archive(
            oversized,
            expected_source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            expected_source_sha256=source_digest,
        )


def test_archive_rejects_page_digest_mismatch() -> None:
    source = b"legacy"
    payload = archive_for(source)
    source_archive = zipfile.ZipFile(BytesIO(payload))
    manifest = source_archive.read("manifest.json")
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("pages/1.png", b"tampered")
        archive.writestr("converted.docx", b"docx")
    with pytest.raises(OfficeRendererError, match="office_render_page_integrity_failed"):
        parse_office_render_archive(
            output.getvalue(),
            expected_source_mime="application/msword",
            expected_source_sha256=hashlib.sha256(source).hexdigest(),
        )


@pytest.mark.parametrize(
    "page,width,height",
    [
        (b"\x89PNG\r\n\x1a\n" + b"truncated", 100, 200),
        (rgb_png(2, 3), 3, 2),
        (rgb_png_with_unapproved_chunk(), 2, 3),
    ],
)
def test_archive_rejects_invalid_or_dimension_mismatched_png(
    page: bytes, width: int, height: int
) -> None:
    source = b"legacy"
    with pytest.raises(OfficeRendererError, match="office_render_manifest_invalid"):
        parse_office_render_archive(
            archive_for(source, page, width=width, height=height),
            expected_source_mime="application/msword",
            expected_source_sha256=hashlib.sha256(source).hexdigest(),
        )


def pdf_page_raster_archive(
    source: bytes,
    page: bytes | None = None,
    *,
    normalized_bbox: tuple[int, int, int, int] = (0, 0, 10_000, 10_000),
) -> bytes:
    page = page if page is not None else rgb_png(288, 144)
    manifest = {
        "schema_version": "atlas-pdf-page-raster-v2",
        "renderer_version": "pypdfium2-pdf-page-raster-v2",
        "renderer_config_digest": (
            "5bab5444e15c27ef31063752bb983962b116a5dcbfc5a7071b398188c5ae0bab"
        ),
        "source_mime": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_byte_length": len(source),
        "render_dpi": 144,
        "normalized_bbox": list(normalized_bbox),
        "output": {
            "path": "page.png",
            "mode": "RGB",
            "width": 288,
            "height": 144,
            "sha256": hashlib.sha256(page).hexdigest(),
            "byte_length": len(page),
        },
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("page.png", page)
    return output.getvalue()


def test_pdf_page_raster_archive_is_strict_and_digest_bound() -> None:
    source = b"sanitized-single-page-pdf"

    result = parse_pdf_page_raster_archive(
        pdf_page_raster_archive(source),
        expected_source_sha256=hashlib.sha256(source).hexdigest(),
        expected_source_byte_length=len(source),
    )

    assert result.render_dpi == 144
    assert result.normalized_bbox == (0, 0, 10_000, 10_000)
    assert result.width == 288
    assert result.height == 144
    assert result.content_type == "image/png"
    assert result.content == rgb_png(288, 144)


@pytest.mark.parametrize(
    "expected_digest,expected_length,page",
    [
        ("0" * 64, len(b"source"), None),
        (hashlib.sha256(b"source").hexdigest(), 999, None),
        (hashlib.sha256(b"source").hexdigest(), len(b"source"), b"tampered"),
    ],
)
def test_pdf_page_raster_archive_rejects_source_or_output_mismatch(
    expected_digest: str, expected_length: int, page: bytes | None
) -> None:
    source = b"source"

    with pytest.raises(OfficeRendererError):
        parse_pdf_page_raster_archive(
            pdf_page_raster_archive(source, page),
            expected_source_sha256=expected_digest,
            expected_source_byte_length=expected_length,
        )


def test_pdf_page_raster_transport_binds_the_source_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"sanitized-single-page-pdf"
    captured: dict[str, object] = {}

    def post(url: str, **kwargs) -> httpx.Response:
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(200, content=pdf_page_raster_archive(source))

    monkeypatch.setattr(
        "atlas_production.infrastructure.office_renderer_adapter.httpx.post", post
    )

    result = OfficeRendererAdapter("http://renderer").raster_pdf_page(source)

    digest = hashlib.sha256(source).hexdigest()
    assert result.source_sha256 == digest
    assert captured["url"] == "http://renderer/v1/pdf-page-raster"
    assert captured["data"] == {
        "source_sha256": digest,
        "bbox_left": 0,
        "bbox_top": 0,
        "bbox_right": 10_000,
        "bbox_bottom": 10_000,
    }
    assert captured["files"] == {
        "file": ("page.pdf", source, "application/pdf")
    }


def test_pdf_page_raster_transport_sends_requested_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"sanitized-single-page-pdf"
    bbox = (1_000, 2_000, 9_000, 8_000)
    captured: dict[str, object] = {}

    def post(url: str, **kwargs) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(
            200,
            content=pdf_page_raster_archive(source, normalized_bbox=bbox),
        )

    monkeypatch.setattr(
        "atlas_production.infrastructure.office_renderer_adapter.httpx.post", post
    )

    result = OfficeRendererAdapter("http://renderer").raster_pdf_page(
        source, normalized_bbox=bbox
    )

    assert result.normalized_bbox == bbox
    assert captured["data"] == {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "bbox_left": 1_000,
        "bbox_top": 2_000,
        "bbox_right": 9_000,
        "bbox_bottom": 8_000,
    }


@pytest.mark.parametrize(
    "bbox",
    [
        (-1, 0, 10_000, 10_000),
        (5_000, 0, 5_000, 10_000),
        (0, 9_000, 10_000, 8_000),
    ],
)
def test_pdf_page_raster_transport_rejects_invalid_bbox_before_http(
    monkeypatch: pytest.MonkeyPatch,
    bbox: tuple[int, int, int, int],
) -> None:
    monkeypatch.setattr(
        "atlas_production.infrastructure.office_renderer_adapter.httpx.post",
        lambda *_args, **_kwargs: pytest.fail("invalid bbox reached renderer"),
    )

    with pytest.raises(OfficeRendererError, match="pdf_page_bbox_invalid"):
        OfficeRendererAdapter("http://renderer").raster_pdf_page(
            b"page", normalized_bbox=bbox
        )
