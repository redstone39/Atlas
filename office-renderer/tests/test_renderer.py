from io import BytesIO
import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from pypdf import PdfWriter

from atlas_office_renderer import app as renderer


def pdf_page_bytes(
    *, pages: int = 1, encrypted: bool = False, width: int = 144, height: int = 72
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    if encrypted:
        writer.encrypt("")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_health_exposes_the_pinned_renderer_revision() -> None:
    response = TestClient(renderer.create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "renderer_version": renderer.RENDERER_VERSION,
        "renderer_config_digest": renderer.RENDERER_CONFIG_DIGEST,
    }


def test_render_returns_bounded_no_store_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "_render_archive", lambda content, source_mime: b"archive")
    response = TestClient(renderer.create_app()).post(
        "/v1/render",
        data={"source_mime": renderer.DOCX},
        files={"file": ("source.docx", b"document", renderer.DOCX)},
    )
    assert response.status_code == 200
    assert response.content == b"archive"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-atlas-renderer-config"] == renderer.RENDERER_CONFIG_DIGEST


def test_render_rejects_unknown_format_and_converter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(renderer.create_app())
    unsupported = client.post(
        "/v1/render",
        data={"source_mime": "application/octet-stream"},
        files={"file": ("source.bin", b"document", "application/octet-stream")},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == "unsupported_source_mime"

    monkeypatch.setattr(
        renderer,
        "_render_archive",
        lambda content, source_mime: (_ for _ in ()).throw(
            RuntimeError("office_converter_unavailable")
        ),
    )
    unavailable = client.post(
        "/v1/render",
        data={"source_mime": renderer.DOCX},
        files={"file": ("source.docx", b"document", renderer.DOCX)},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "office_converter_unavailable"


def test_rendered_page_limit_is_capped_and_rejected_before_page_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_pages: list[int] = []

    class FakePdfDocument:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def __len__(self) -> int:
            return 3001

        def __getitem__(self, index: int):
            rendered_pages.append(index)
            raise AssertionError("oversized Office page was decoded")

    def fake_run_lo(source, output_dir, target, _filter_name, _profile):
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{source.stem}.{target}"
        output.write_bytes(b"converted")
        return output

    monkeypatch.setenv("ATLAS_PDF_MAX_PAGES", "9999")
    monkeypatch.setattr(renderer, "_run_lo", fake_run_lo)
    monkeypatch.setattr(renderer.pdfium, "PdfDocument", FakePdfDocument)

    with pytest.raises(RuntimeError, match="artifact_too_large"):
        renderer._render_archive(b"document", renderer.DOCX)
    assert rendered_pages == []
    assert renderer._max_rendered_pages() == 3000


def test_render_projects_oversized_office_output_as_existing_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        renderer,
        "_render_archive",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("artifact_too_large")),
    )

    response = TestClient(renderer.create_app()).post(
        "/v1/render",
        data={"source_mime": renderer.DOCX},
        files={"file": ("source.docx", b"document", renderer.DOCX)},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "artifact_too_large"


def test_libreoffice_adapter_uses_an_argument_vector_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"document")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        output = tmp_path / "out" / "source.pdf"
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b"pdf")
        return CompletedProcess(command, 0)

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    result = renderer._run_lo(
        source,
        tmp_path / "out",
        "pdf",
        "writer_pdf_Export",
        tmp_path / "profile",
    )
    assert result == tmp_path / "out" / "source.pdf"
    assert captured["command"][0] == "libreoffice"
    assert "shell" not in captured
    assert captured["timeout"] == 180


def test_pdf_page_raster_is_deterministic_144_dpi_rgb_archive() -> None:
    source = pdf_page_bytes()
    source_digest = hashlib.sha256(source).hexdigest()

    first = renderer._pdf_page_raster_archive(source, source_digest)
    second = renderer._pdf_page_raster_archive(source, source_digest)

    assert first == second
    with zipfile.ZipFile(BytesIO(first)) as archive:
        assert archive.namelist() == ["manifest.json", "page.png"]
        manifest = json.loads(archive.read("manifest.json"))
        page = archive.read("page.png")
    assert manifest == {
        "schema_version": "atlas-pdf-page-raster-v2",
        "renderer_version": renderer.PDF_PAGE_RASTER_RENDERER_VERSION,
        "renderer_config_digest": renderer.PDF_PAGE_RASTER_CONFIG_DIGEST,
        "source_mime": "application/pdf",
        "source_sha256": source_digest,
        "source_byte_length": len(source),
        "render_dpi": 144,
        "normalized_bbox": [0, 0, 10_000, 10_000],
        "output": {
            "path": "page.png",
            "mode": "RGB",
            "width": 288,
            "height": 144,
            "sha256": hashlib.sha256(page).hexdigest(),
            "byte_length": len(page),
        },
    }
    with Image.open(BytesIO(page)) as image:
        assert image.mode == "RGB"
        assert image.size == (288, 144)


def test_pdf_page_raster_renders_rect_from_the_original_page() -> None:
    source = pdf_page_bytes()
    archive_bytes = renderer._pdf_page_raster_archive(
        source,
        hashlib.sha256(source).hexdigest(),
        normalized_bbox=(2_500, 2_500, 7_500, 7_500),
    )

    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        page = archive.read("page.png")

    assert manifest["normalized_bbox"] == [2_500, 2_500, 7_500, 7_500]
    assert manifest["output"]["width"] == 144
    assert manifest["output"]["height"] == 72
    with Image.open(BytesIO(page)) as image:
        assert image.size == (144, 72)


@pytest.mark.parametrize(
    "source,expected_detail",
    [
        (b"not-a-pdf", "pdf_page_invalid"),
        (pdf_page_bytes(pages=2), "pdf_page_count_invalid"),
        (pdf_page_bytes(encrypted=True), "pdf_page_encrypted"),
    ],
)
def test_pdf_page_raster_rejects_non_sanitized_input(
    source: bytes, expected_detail: str
) -> None:
    response = TestClient(renderer.create_app()).post(
        "/v1/pdf-page-raster",
        data={"source_sha256": hashlib.sha256(source).hexdigest()},
        files={"file": ("page.pdf", source, "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == expected_detail


def test_pdf_page_raster_rejects_digest_mismatch_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = pdf_page_bytes()
    decoded = False

    def unexpected_decode(*_args, **_kwargs):
        nonlocal decoded
        decoded = True
        raise AssertionError("digest-invalid PDF was decoded")

    monkeypatch.setattr(renderer, "_pdf_page_raster_archive", unexpected_decode)
    response = TestClient(renderer.create_app()).post(
        "/v1/pdf-page-raster",
        data={"source_sha256": "0" * 64},
        files={"file": ("page.pdf", source, "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "pdf_page_digest_mismatch"
    assert decoded is False


def test_pdf_page_raster_endpoint_is_bounded_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        renderer,
        "_pdf_page_raster_archive",
        lambda *_args, **_kwargs: b"raster-archive",
    )
    source = pdf_page_bytes()
    response = TestClient(renderer.create_app()).post(
        "/v1/pdf-page-raster",
        data={"source_sha256": hashlib.sha256(source).hexdigest()},
        files={"file": ("page.pdf", source, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.content == b"raster-archive"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        response.headers["x-atlas-renderer-config"]
        == renderer.PDF_PAGE_RASTER_CONFIG_DIGEST
    )


@pytest.mark.parametrize(
    "bbox",
    [
        {"bbox_left": -1},
        {"bbox_left": 5_000, "bbox_right": 5_000},
        {"bbox_top": 8_000, "bbox_bottom": 7_000},
        {"bbox_right": 10_001},
    ],
)
def test_pdf_page_raster_rejects_invalid_bbox(bbox: dict[str, int]) -> None:
    source = pdf_page_bytes()
    response = TestClient(renderer.create_app()).post(
        "/v1/pdf-page-raster",
        data={"source_sha256": hashlib.sha256(source).hexdigest(), **bbox},
        files={"file": ("page.pdf", source, "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "pdf_page_bbox_invalid"


def test_pdf_page_raster_rejects_oversized_geometry_before_bitmap_decode() -> None:
    source = pdf_page_bytes(width=20_000, height=20_000)
    response = TestClient(renderer.create_app()).post(
        "/v1/pdf-page-raster",
        data={"source_sha256": hashlib.sha256(source).hexdigest()},
        files={"file": ("page.pdf", source, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "artifact_too_large"
