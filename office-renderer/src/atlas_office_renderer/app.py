from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
import pypdfium2 as pdfium
from pypdf import PdfReader


RENDERER_VERSION = "atlas-office-renderer-v1"
RENDERER_CONFIG_DIGEST = "ffa7b5894ee3b14fb0dbb3eb3025c284381530f2584b19b5af3703404560ea79"
RENDER_SCALE = 2.0
MAX_DOCUMENT_BYTES = 262_144_000
MAX_RENDERED_PAGES = 3000
MAX_PDF_PAGE_RASTER_PIXELS = 50_000_000
PDF_PAGE_RASTER_RENDERER_VERSION = "pypdfium2-pdf-page-raster-v2"
# SHA-256 of canonical JSON for archive/dpi/dimension/pixel/mode/compression/
# pypdfium2 settings; changing any setting creates a new config digest.
PDF_PAGE_RASTER_CONFIG_DIGEST = (
    "5bab5444e15c27ef31063752bb983962b116a5dcbfc5a7071b398188c5ae0bab"
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOC = "application/msword"
PPT = "application/vnd.ms-powerpoint"
XLS = "application/vnd.ms-excel"

SOURCE_CONFIG = {
    DOCX: ("docx", None, "writer_pdf_Export"),
    PPTX: ("pptx", None, "impress_pdf_Export"),
    XLSX: ("xlsx", None, "calc_pdf_Export"),
    DOC: ("doc", ("docx", "Office Open XML Text"), "writer_pdf_Export"),
    PPT: ("ppt", ("pptx", "Impress MS PowerPoint 2007 XML"), "impress_pdf_Export"),
    XLS: ("xls", ("xlsx", "Calc MS Excel 2007 XML"), "calc_pdf_Export"),
}


def _deterministic_zip_member(name: str) -> zipfile.ZipInfo:
    member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_STORED
    member.external_attr = 0o600 << 16
    return member


def _normalized_bbox(
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> tuple[int, int, int, int]:
    values = (left, top, right, bottom)
    if (
        any(isinstance(value, bool) or not isinstance(value, int) for value in values)
        or any(value < 0 or value > 10_000 for value in values)
        or left >= right
        or top >= bottom
    ):
        raise ValueError("pdf_page_bbox_invalid")
    return values


def _pdf_page_raster_archive(
    content: bytes,
    source_sha256: str,
    *,
    normalized_bbox: tuple[int, int, int, int] = (0, 0, 10_000, 10_000),
) -> bytes:
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
        or hashlib.sha256(content).hexdigest() != source_sha256
    ):
        raise ValueError("pdf_page_digest_mismatch")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except Exception as exc:
        raise ValueError("pdf_page_invalid") from exc
    if reader.is_encrypted:
        raise ValueError("pdf_page_encrypted")
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ValueError("pdf_page_invalid") from exc
    if page_count != 1:
        raise ValueError("pdf_page_count_invalid")

    left, top, right, bottom = _normalized_bbox(*normalized_bbox)
    try:
        with pdfium.PdfDocument(content) as document:
            if len(document) != 1:
                raise ValueError("pdf_page_count_invalid")
            page = document[0]
            width_points, height_points = page.get_size()
            crop = (
                width_points * left / 10_000,
                height_points * (10_000 - bottom) / 10_000,
                width_points * (10_000 - right) / 10_000,
                height_points * top / 10_000,
            )
            width = round(width_points * (right - left) / 10_000 * RENDER_SCALE)
            height = round(height_points * (bottom - top) / 10_000 * RENDER_SCALE)
            if (
                width <= 0
                or height <= 0
                or width > 20_000
                or height > 20_000
                or width * height > MAX_PDF_PAGE_RASTER_PIXELS
            ):
                raise RuntimeError("artifact_too_large")
            bitmap = page.render(scale=RENDER_SCALE, crop=crop)
            image = bitmap.to_pil().convert("RGB")
            output = BytesIO()
            image.save(output, format="PNG", optimize=False, compress_level=9)
            png = output.getvalue()
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("pdf_page_raster_failed") from exc

    manifest = {
        "schema_version": "atlas-pdf-page-raster-v2",
        "renderer_version": PDF_PAGE_RASTER_RENDERER_VERSION,
        "renderer_config_digest": PDF_PAGE_RASTER_CONFIG_DIGEST,
        "source_mime": "application/pdf",
        "source_sha256": source_sha256,
        "source_byte_length": len(content),
        "render_dpi": 144,
        "normalized_bbox": [left, top, right, bottom],
        "output": {
            "path": "page.png",
            "mode": "RGB",
            "width": image.width,
            "height": image.height,
            "sha256": hashlib.sha256(png).hexdigest(),
            "byte_length": len(png),
        },
    }
    archive_bytes = BytesIO()
    with zipfile.ZipFile(
        archive_bytes, "w", compression=zipfile.ZIP_STORED
    ) as archive:
        archive.writestr(
            _deterministic_zip_member("manifest.json"),
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )
        archive.writestr(_deterministic_zip_member("page.png"), png)
    return archive_bytes.getvalue()


def _max_rendered_pages() -> int:
    try:
        configured = int(os.getenv("ATLAS_PDF_MAX_PAGES", "3000"))
    except ValueError as exc:
        raise RuntimeError("office_page_limit_invalid") from exc
    if configured <= 0:
        raise RuntimeError("office_page_limit_invalid")
    return min(configured, MAX_RENDERED_PAGES)


def _run_lo(source: Path, output_dir: Path, target: str, filter_name: str, profile: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    command = [
        "libreoffice",
        "--headless",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to",
        f"{target}:{filter_name}",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("office_converter_unavailable") from exc
    output = output_dir / f"{source.stem}.{target}"
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("office_conversion_failed")
    return output


def _render_archive(content: bytes, source_mime: str) -> bytes:
    if source_mime not in SOURCE_CONFIG:
        raise ValueError("unsupported_source_mime")
    extension, legacy_conversion, pdf_filter = SOURCE_CONFIG[source_mime]
    with TemporaryDirectory(prefix="atlas-office-render-") as temporary:
        root = Path(temporary)
        source = root / f"source.{extension}"
        source.write_bytes(content)
        converted: Path | None = None
        native = source
        if legacy_conversion is not None:
            target, filter_name = legacy_conversion
            converted = _run_lo(source, root / "converted", target, filter_name, root / "lo-convert")
            native = converted
        pdf = _run_lo(native, root / "pdf", "pdf", pdf_filter, root / "lo-pdf")
        pages: list[dict[str, object]] = []
        page_bytes: list[bytes] = []
        with pdfium.PdfDocument(str(pdf)) as pdf_document:
            page_count = len(pdf_document)
            if page_count <= 0:
                raise RuntimeError("office_render_produced_no_pages")
            if page_count > _max_rendered_pages():
                # Reject before decoding or retaining the first page, so an
                # oversized Office conversion cannot produce a partial archive.
                raise RuntimeError("artifact_too_large")
            text_reader = PdfReader(pdf, strict=True)
            if len(text_reader.pages) != page_count:
                raise RuntimeError("office_render_page_count_mismatch")
            for index in range(page_count):
                bitmap = pdf_document[index].render(scale=RENDER_SCALE)
                image = bitmap.to_pil().convert("RGB")
                output = BytesIO()
                image.save(output, format="PNG", optimize=False, compress_level=9)
                payload = output.getvalue()
                page_bytes.append(payload)
                pages.append({
                    "page_number": index + 1,
                    "width": image.width,
                    "height": image.height,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_length": len(payload),
                    "path": f"pages/{index + 1}.png",
                    "normalized_text": (text_reader.pages[index].extract_text() or "").strip(),
                })
        if not pages:
            raise RuntimeError("office_render_produced_no_pages")
        manifest: dict[str, object] = {
            "schema_version": "atlas-office-render-result-v1",
            "renderer_version": RENDERER_VERSION,
            "renderer_config_digest": RENDERER_CONFIG_DIGEST,
            "source_mime": source_mime,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "render_dpi": 144,
            "render_scale": RENDER_SCALE,
            "pdf_filter": pdf_filter,
            "libreoffice_version": "7.4.7.2",
            "pypdfium2_version": "5.11.0",
            "pages": pages,
        }
        converted_bytes = converted.read_bytes() if converted is not None else None
        if converted_bytes is not None:
            manifest["converted_document"] = {
                "path": f"converted.{converted.suffix.lstrip('.')}",
                "canonical_mime": {DOC: DOCX, PPT: PPTX, XLS: XLSX}[source_mime],
                "sha256": hashlib.sha256(converted_bytes).hexdigest(),
                "byte_length": len(converted_bytes),
            }
        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            )
            for index, payload in enumerate(page_bytes, start=1):
                archive.writestr(f"pages/{index}.png", payload)
            converted_manifest = manifest.get("converted_document")
            if converted_bytes is not None and isinstance(converted_manifest, dict):
                archive.writestr(str(converted_manifest["path"]), converted_bytes)
        return archive_bytes.getvalue()


def create_app() -> FastAPI:
    app = FastAPI(title="Atlas Office Renderer", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "renderer_version": RENDERER_VERSION,
            "renderer_config_digest": RENDERER_CONFIG_DIGEST,
        }

    @app.post("/v1/render")
    async def render(
        source_mime: str = Form(...),
        file: UploadFile = File(...),
    ) -> Response:
        content = await file.read(MAX_DOCUMENT_BYTES + 1)
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=422, detail="invalid_document_size")
        try:
            archive = _render_archive(content, source_mime)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=413 if str(exc) == "artifact_too_large" else 503,
                detail=str(exc),
            ) from exc
        return Response(
            content=archive,
            media_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Atlas-Renderer-Version": RENDERER_VERSION,
                "X-Atlas-Renderer-Config": RENDERER_CONFIG_DIGEST,
            },
        )

    @app.post("/v1/pdf-page-raster")
    async def pdf_page_raster(
        source_sha256: str = Form(...),
        bbox_left: int = Form(0),
        bbox_top: int = Form(0),
        bbox_right: int = Form(10_000),
        bbox_bottom: int = Form(10_000),
        file: UploadFile = File(...),
    ) -> Response:
        content = await file.read(MAX_DOCUMENT_BYTES + 1)
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=422, detail="invalid_document_size")
        if hashlib.sha256(content).hexdigest() != source_sha256:
            raise HTTPException(status_code=422, detail="pdf_page_digest_mismatch")
        try:
            archive = _pdf_page_raster_archive(
                content,
                source_sha256,
                normalized_bbox=(bbox_left, bbox_top, bbox_right, bbox_bottom),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=413 if str(exc) == "artifact_too_large" else 503,
                detail=str(exc),
            ) from exc
        return Response(
            content=archive,
            media_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Atlas-Renderer-Version": PDF_PAGE_RASTER_RENDERER_VERSION,
                "X-Atlas-Renderer-Config": PDF_PAGE_RASTER_CONFIG_DIGEST,
            },
        )

    return app
