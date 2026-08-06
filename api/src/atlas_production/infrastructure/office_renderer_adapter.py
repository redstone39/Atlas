from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
import zipfile

import httpx

from atlas_production.shared.png import validated_rgb_png_dimensions


RENDERER_VERSION = "atlas-office-renderer-v1"
RENDERER_CONFIG_DIGEST = "ffa7b5894ee3b14fb0dbb3eb3025c284381530f2584b19b5af3703404560ea79"
PDF_PAGE_RASTER_RENDERER_VERSION = "pypdfium2-pdf-page-raster-v2"
PDF_PAGE_RASTER_CONFIG_DIGEST = (
    "5bab5444e15c27ef31063752bb983962b116a5dcbfc5a7071b398188c5ae0bab"
)
MAX_RENDERED_PAGES = 3000
EXPECTED_PDF_FILTERS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "writer_pdf_Export",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "impress_pdf_Export",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "calc_pdf_Export",
    "application/msword": "writer_pdf_Export",
    "application/vnd.ms-powerpoint": "impress_pdf_Export",
    "application/vnd.ms-excel": "calc_pdf_Export",
}
LEGACY_CONVERTED_MIME = {
    "application/msword": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-powerpoint": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
LEGACY_CONVERTED_PATH = {
    "application/msword": "converted.docx",
    "application/vnd.ms-powerpoint": "converted.pptx",
    "application/vnd.ms-excel": "converted.xlsx",
}


@dataclass(frozen=True, slots=True)
class RenderedOfficePage:
    page_number: int
    width: int
    height: int
    sha256: str
    content: bytes
    normalized_text: str


@dataclass(frozen=True, slots=True)
class OfficeRenderResult:
    renderer_version: str
    renderer_config_digest: str
    source_mime: str
    source_sha256: str
    render_dpi: int
    pdf_filter: str
    pages: tuple[RenderedOfficePage, ...]
    converted_document: bytes | None = None
    converted_mime: str | None = None


@dataclass(frozen=True, slots=True)
class PdfPageRasterResult:
    renderer_version: str
    renderer_config_digest: str
    source_sha256: str
    source_byte_length: int
    render_dpi: int
    normalized_bbox: tuple[int, int, int, int]
    width: int
    height: int
    sha256: str
    byte_length: int
    content_type: str
    content: bytes


class OfficeRendererError(RuntimeError):
    pass


def _normalized_bbox(
    value: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    if (
        not isinstance(value, tuple)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise OfficeRendererError("pdf_page_bbox_invalid")
    left, top, right, bottom = value
    if (
        any(item < 0 or item > 10_000 for item in value)
        or left >= right
        or top >= bottom
    ):
        raise OfficeRendererError("pdf_page_bbox_invalid")
    return value


def _max_rendered_pages() -> int:
    try:
        configured = int(os.getenv("ATLAS_PDF_MAX_PAGES", "3000"))
    except ValueError as exc:
        raise OfficeRendererError("office_page_limit_invalid") from exc
    if configured <= 0:
        raise OfficeRendererError("office_page_limit_invalid")
    return min(configured, MAX_RENDERED_PAGES)


class OfficeRendererAdapter:
    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or os.getenv("ATLAS_OFFICE_RENDERER_URL", "")).rstrip("/")

    def render(self, content: bytes, source_mime: str) -> OfficeRenderResult:
        if not self.endpoint:
            raise OfficeRendererError("office_renderer_unavailable")
        try:
            response = httpx.post(
                f"{self.endpoint}/v1/render",
                data={"source_mime": source_mime},
                files={"file": ("source", content, source_mime)},
                timeout=210,
            )
        except httpx.HTTPError as exc:
            raise OfficeRendererError("office_renderer_unavailable") from exc
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise OfficeRendererError(str(detail or "office_render_failed"))
        return parse_office_render_archive(
            response.content,
            expected_source_mime=source_mime,
            expected_source_sha256=hashlib.sha256(content).hexdigest(),
        )

    def raster_pdf_page(
        self,
        content: bytes,
        *,
        normalized_bbox: tuple[int, int, int, int] = (0, 0, 10_000, 10_000),
        timeout_seconds: float | None = None,
    ) -> PdfPageRasterResult:
        if not self.endpoint:
            raise OfficeRendererError("office_renderer_unavailable")
        normalized_bbox = _normalized_bbox(normalized_bbox)
        source_sha256 = hashlib.sha256(content).hexdigest()
        try:
            response = httpx.post(
                f"{self.endpoint}/v1/pdf-page-raster",
                data={
                    "source_sha256": source_sha256,
                    "bbox_left": normalized_bbox[0],
                    "bbox_top": normalized_bbox[1],
                    "bbox_right": normalized_bbox[2],
                    "bbox_bottom": normalized_bbox[3],
                },
                files={"file": ("page.pdf", content, "application/pdf")},
                timeout=210 if timeout_seconds is None else timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("pdf_page_raster_timeout") from exc
        except httpx.HTTPError as exc:
            raise OfficeRendererError("office_renderer_unavailable") from exc
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise OfficeRendererError(str(detail or "pdf_page_raster_failed"))
        return parse_pdf_page_raster_archive(
            response.content,
            expected_source_sha256=source_sha256,
            expected_source_byte_length=len(content),
            expected_normalized_bbox=normalized_bbox,
        )


def parse_pdf_page_raster_archive(
    archive_bytes: bytes,
    *,
    expected_source_sha256: str,
    expected_source_byte_length: int,
    expected_normalized_bbox: tuple[int, int, int, int] = (0, 0, 10_000, 10_000),
) -> PdfPageRasterResult:
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            archive_names = archive.namelist()
            if archive_names != ["manifest.json", "page.png"]:
                raise OfficeRendererError("pdf_page_raster_archive_invalid")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or set(manifest) != {
                "schema_version",
                "renderer_version",
                "renderer_config_digest",
                "source_mime",
                "source_sha256",
                "source_byte_length",
                "render_dpi",
                "normalized_bbox",
                "output",
            }:
                raise OfficeRendererError("pdf_page_raster_manifest_invalid")
            if (
                manifest.get("schema_version") != "atlas-pdf-page-raster-v2"
                or manifest.get("renderer_version")
                != PDF_PAGE_RASTER_RENDERER_VERSION
                or manifest.get("renderer_config_digest")
                != PDF_PAGE_RASTER_CONFIG_DIGEST
                or manifest.get("source_mime") != "application/pdf"
                or manifest.get("source_sha256") != expected_source_sha256
                or type(manifest.get("source_byte_length")) is not int
                or manifest.get("source_byte_length")
                != expected_source_byte_length
                or manifest.get("render_dpi") != 144
                or manifest.get("normalized_bbox") != list(expected_normalized_bbox)
            ):
                raise OfficeRendererError("pdf_page_raster_manifest_invalid")
            output = manifest.get("output")
            if not isinstance(output, dict) or set(output) != {
                "path",
                "mode",
                "width",
                "height",
                "sha256",
                "byte_length",
            }:
                raise OfficeRendererError("pdf_page_raster_manifest_invalid")
            content = archive.read("page.png")
            digest = hashlib.sha256(content).hexdigest()
            width = output.get("width")
            height = output.get("height")
            if (
                output.get("path") != "page.png"
                or output.get("mode") != "RGB"
                or type(width) is not int
                or width <= 0
                or type(height) is not int
                or height <= 0
                or output.get("sha256") != digest
                or type(output.get("byte_length")) is not int
                or output.get("byte_length") != len(content)
                or validated_rgb_png_dimensions(content) != (width, height)
            ):
                raise OfficeRendererError("pdf_page_raster_integrity_failed")
    except OfficeRendererError:
        raise
    except (
        KeyError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise OfficeRendererError("pdf_page_raster_archive_invalid") from exc
    return PdfPageRasterResult(
        renderer_version=manifest["renderer_version"],
        renderer_config_digest=manifest["renderer_config_digest"],
        source_sha256=manifest["source_sha256"],
        source_byte_length=manifest["source_byte_length"],
        render_dpi=manifest["render_dpi"],
        normalized_bbox=expected_normalized_bbox,
        width=width,
        height=height,
        sha256=digest,
        byte_length=len(content),
        content_type="image/png",
        content=content,
    )


def parse_office_render_archive(
    archive_bytes: bytes,
    *,
    expected_source_mime: str,
    expected_source_sha256: str,
) -> OfficeRenderResult:
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            archive_names = archive.namelist()
            if len(archive_names) != len(set(archive_names)):
                raise OfficeRendererError("office_render_archive_invalid")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise OfficeRendererError("office_render_manifest_invalid")
            if (
                manifest.get("schema_version") != "atlas-office-render-result-v1"
                or manifest.get("source_mime") != expected_source_mime
                or manifest.get("source_sha256") != expected_source_sha256
                or type(manifest.get("render_dpi")) is not int
                or manifest.get("render_dpi") != 144
                or manifest.get("renderer_version") != RENDERER_VERSION
                or manifest.get("renderer_config_digest") != RENDERER_CONFIG_DIGEST
                or manifest.get("pdf_filter") != EXPECTED_PDF_FILTERS.get(expected_source_mime)
            ):
                raise OfficeRendererError("office_render_manifest_invalid")
            page_values = manifest.get("pages")
            if not isinstance(page_values, list) or not page_values:
                raise OfficeRendererError("office_render_manifest_invalid")
            if len(page_values) > _max_rendered_pages():
                # Check the manifest before opening any page member.  This is a
                # defense against a compromised or stale renderer bypassing its
                # own pre-render capacity guard.
                raise OfficeRendererError("artifact_too_large")
            pages: list[RenderedOfficePage] = []
            for expected_number, item in enumerate(page_values, start=1):
                if (
                    not isinstance(item, dict)
                    or type(item.get("page_number")) is not int
                    or item.get("page_number") != expected_number
                ):
                    raise OfficeRendererError("office_render_manifest_invalid")
                path = item.get("path")
                if path != f"pages/{expected_number}.png":
                    raise OfficeRendererError("office_render_manifest_invalid")
                content = archive.read(path)
                digest = hashlib.sha256(content).hexdigest()
                if (
                    digest != item.get("sha256")
                    or type(item.get("byte_length")) is not int
                    or len(content) != item.get("byte_length")
                ):
                    raise OfficeRendererError("office_render_page_integrity_failed")
                width, height = item.get("width"), item.get("height")
                dimensions = validated_rgb_png_dimensions(content)
                if (
                    type(width) is not int
                    or width <= 0
                    or type(height) is not int
                    or height <= 0
                    or dimensions != (width, height)
                ):
                    raise OfficeRendererError("office_render_manifest_invalid")
                pages.append(RenderedOfficePage(
                    page_number=expected_number,
                    width=width,
                    height=height,
                    sha256=digest,
                    content=content,
                    normalized_text=(
                        item["normalized_text"]
                        if isinstance(item.get("normalized_text"), str)
                        else ""
                    ),
                ))
            converted = manifest.get("converted_document")
            converted_content = converted_mime = None
            expected_converted_mime = LEGACY_CONVERTED_MIME.get(expected_source_mime)
            expected_converted_path = LEGACY_CONVERTED_PATH.get(expected_source_mime)
            if (converted is None) != (expected_converted_mime is None):
                raise OfficeRendererError("office_render_manifest_invalid")
            if converted is not None:
                if (
                    not isinstance(converted, dict)
                    or converted.get("path") != expected_converted_path
                ):
                    raise OfficeRendererError("office_render_manifest_invalid")
                converted_content = archive.read(converted["path"])
                if (
                    hashlib.sha256(converted_content).hexdigest() != converted.get("sha256")
                    or type(converted.get("byte_length")) is not int
                    or len(converted_content) != converted.get("byte_length")
                    or converted.get("canonical_mime") != expected_converted_mime
                ):
                    raise OfficeRendererError("office_conversion_integrity_failed")
                converted_mime = converted["canonical_mime"]
            expected_names = {"manifest.json", *(f"pages/{page.page_number}.png" for page in pages)}
            if expected_converted_path is not None:
                expected_names.add(expected_converted_path)
            if set(archive_names) != expected_names:
                raise OfficeRendererError("office_render_archive_invalid")
    except OfficeRendererError:
        raise
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise OfficeRendererError("office_render_archive_invalid") from exc
    return OfficeRenderResult(
        renderer_version=manifest["renderer_version"],
        renderer_config_digest=manifest["renderer_config_digest"],
        source_mime=manifest["source_mime"],
        source_sha256=manifest["source_sha256"],
        render_dpi=manifest["render_dpi"],
        pdf_filter=manifest["pdf_filter"],
        pages=tuple(pages),
        converted_document=converted_content,
        converted_mime=converted_mime,
    )
