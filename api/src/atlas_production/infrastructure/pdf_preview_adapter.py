from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
from time import monotonic
from typing import Any, BinaryIO, Callable


PDF_PREVIEW_RENDERER_VERSION = "pikepdf-document-pages-v2"
PDF_PREVIEW_RESULT_SCHEMA = "atlas-pdf-preview-result-v2"
PDF_PREVIEW_MAX_INPUT_BYTES = 250 * 1024 * 1024
PDF_PREVIEW_MAX_OUTPUT_BYTES = 250 * 1024 * 1024
PDF_PREVIEW_MEMORY_BYTES = 512 * 1024 * 1024
PDF_PREVIEW_TIMEOUT_SECONDS = 300
_FRAME_MANIFEST_MAX_BYTES = 4096
_FRAME_READ_CHUNK_BYTES = 1024 * 1024
_CHILD_DETERMINISTIC_FAILURE = 20
_CHILD_INFRASTRUCTURE_FAILURE = 21


@dataclass(frozen=True, slots=True)
class PdfPreviewResult:
    content: bytes
    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float]
    rotation: int
    renderer_version: str


class PdfPreviewError(RuntimeError):
    """A deterministic PDF page-copy or resource-envelope failure."""

    def __init__(self, code: str, *, page_index: int | None = None) -> None:
        super().__init__(
            f"{code}:page:{page_index + 1}" if page_index is not None else code
        )
        self.code = code
        self.page_index = page_index


def _child_limits() -> None:
    """Bound the pikepdf/qpdf child without changing the worker process."""

    import resource

    # The parent enforces an inactivity timeout for every bounded frame. A
    # document-level child may legitimately process thousands of pages, so a
    # single page-era cumulative CPU limit would incorrectly terminate valid
    # documents as their page count grows.
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (PDF_PREVIEW_MAX_OUTPUT_BYTES, PDF_PREVIEW_MAX_OUTPUT_BYTES),
    )
    if hasattr(resource, "RLIMIT_CORE"):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(
            resource.RLIMIT_AS,
            (PDF_PREVIEW_MEMORY_BYTES, PDF_PREVIEW_MEMORY_BYTES),
        )
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))


def _box(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PdfPreviewError("pdf_preview_geometry_invalid")
    if any(isinstance(item, bool) for item in value):
        raise PdfPreviewError("pdf_preview_geometry_invalid")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise PdfPreviewError("pdf_preview_geometry_invalid") from exc
    if (
        not all(math.isfinite(item) for item in result)
        or result[2] <= result[0]
        or result[3] <= result[1]
    ):
        raise PdfPreviewError("pdf_preview_geometry_invalid")
    return result  # type: ignore[return-value]


def _result_from_frame(value: dict[str, Any], content: bytes) -> PdfPreviewResult:
    expected_keys = {
        "kind",
        "page_index",
        "schema_version",
        "renderer_version",
        "content_sha256",
        "content_length",
        "media_box",
        "crop_box",
        "rotation",
    }
    if set(value) != expected_keys:
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    digest = hashlib.sha256(content).hexdigest()
    if (
        value["kind"] != "page"
        or type(value["page_index"]) is not int
        or value["page_index"] < 0
        or value["schema_version"] != PDF_PREVIEW_RESULT_SCHEMA
        or value["renderer_version"] != PDF_PREVIEW_RENDERER_VERSION
        or value["content_sha256"] != digest
        or type(value["content_length"]) is not int
        or value["content_length"] != len(content)
        or type(value["rotation"]) is not int
    ):
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    return PdfPreviewResult(
        content=content,
        media_box=_box(value["media_box"]),
        crop_box=_box(value["crop_box"]),
        rotation=value["rotation"] % 360,
        renderer_version=value["renderer_version"],
    )


class PdfPreviewAdapter:
    def __init__(self, *, timeout_seconds: int = PDF_PREVIEW_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("pdf_preview_timeout_invalid")
        self.timeout_seconds = timeout_seconds

    def render_document(
        self,
        source: BinaryIO,
        *,
        expected_size: int,
        expected_sha256: str,
        max_pages: int,
        on_document: Callable[[int], None],
        on_page: Callable[[int, PdfPreviewResult], None],
    ) -> int:
        """Stream independently validated pages from one inherited source fd."""

        if os.name != "posix":
            raise RuntimeError("pdf_preview_fd_passing_unavailable")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or not 0 < expected_size <= PDF_PREVIEW_MAX_INPUT_BYTES
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
        ):
            raise PdfPreviewError("pdf_preview_input_invalid")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("pdf_page_limit_invalid")
        try:
            source_fd = source.fileno()
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise PdfPreviewError("pdf_preview_input_invalid") from exc

        with tempfile.TemporaryDirectory(prefix="atlas-pdf-preview-") as temp:
            root = Path(temp)
            command = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--document",
                str(source_fd),
                str(expected_size),
                expected_sha256,
                str(max_pages),
            ]
            process = subprocess.Popen(
                command,
                cwd=root,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONUTF8": "1",
                    "TMPDIR": str(root),
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(source_fd,),
            )
            assert process.stdout is not None
            try:
                first, first_content = _read_frame(
                    process.stdout, timeout_seconds=self.timeout_seconds
                )
                if first.get("kind") == "error":
                    _raise_child_error(first)
                if first_content or set(first) != {
                    "kind", "schema_version", "renderer_version",
                    "page_count", "content_length",
                } or (
                    first["kind"] != "document"
                    or first["schema_version"] != PDF_PREVIEW_RESULT_SCHEMA
                    or first["renderer_version"] != PDF_PREVIEW_RENDERER_VERSION
                    or type(first["page_count"]) is not int
                    or not 0 < first["page_count"] <= max_pages
                    or first["content_length"] != 0
                ):
                    raise PdfPreviewError("pdf_preview_manifest_invalid")
                page_count = first["page_count"]
                on_document(page_count)
                for expected_page_index in range(page_count):
                    frame, content = _read_frame(
                        process.stdout, timeout_seconds=self.timeout_seconds
                    )
                    if frame.get("kind") == "error":
                        _raise_child_error(frame)
                    result = _result_from_frame(frame, content)
                    if frame["page_index"] != expected_page_index:
                        raise PdfPreviewError("pdf_preview_frame_order_invalid")
                    on_page(expected_page_index, result)
                final, final_content = _read_frame(
                    process.stdout, timeout_seconds=self.timeout_seconds
                )
                if final.get("kind") == "error":
                    _raise_child_error(final)
                if final_content or final != {
                    "kind": "complete",
                    "schema_version": PDF_PREVIEW_RESULT_SCHEMA,
                    "renderer_version": PDF_PREVIEW_RENDERER_VERSION,
                    "page_count": page_count,
                    "content_length": 0,
                }:
                    raise PdfPreviewError("pdf_preview_manifest_invalid")
            except TimeoutError as exc:
                process.kill()
                process.wait()
                raise PdfPreviewError("pdf_preview_timeout") from exc
            except EOFError as exc:
                returncode = process.wait()
                if returncode == _CHILD_INFRASTRUCTURE_FAILURE:
                    raise RuntimeError("pdf_preview_infrastructure_unavailable") from exc
                raise PdfPreviewError("pdf_preview_child_failed") from exc
            except BaseException:
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise
            try:
                returncode = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise PdfPreviewError("pdf_preview_timeout") from exc
            if returncode == _CHILD_INFRASTRUCTURE_FAILURE:
                raise RuntimeError("pdf_preview_infrastructure_unavailable")
            if returncode != 0:
                raise PdfPreviewError("pdf_preview_child_failed")
            if process.stdout.read(1):
                raise PdfPreviewError("pdf_preview_frame_order_invalid")
            return page_count


def _read_exact(stream: BinaryIO, size: int, *, timeout_seconds: int) -> bytes:
    deadline = monotonic() + timeout_seconds
    result = bytearray()
    fd = stream.fileno()
    while len(result) < size:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise TimeoutError
        chunk = os.read(fd, min(size - len(result), _FRAME_READ_CHUNK_BYTES))
        if not chunk:
            raise EOFError
        result.extend(chunk)
    return bytes(result)


def _read_frame(
    stream: BinaryIO, *, timeout_seconds: int
) -> tuple[dict[str, Any], bytes]:
    manifest_size = struct.unpack(
        ">I", _read_exact(stream, 4, timeout_seconds=timeout_seconds)
    )[0]
    if not 0 < manifest_size <= _FRAME_MANIFEST_MAX_BYTES:
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    try:
        manifest = json.loads(
            _read_exact(
                stream, manifest_size, timeout_seconds=timeout_seconds
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PdfPreviewError("pdf_preview_manifest_invalid") from exc
    if not isinstance(manifest, dict) or type(manifest.get("content_length")) is not int:
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    content_length = manifest["content_length"]
    if not 0 <= content_length <= PDF_PREVIEW_MAX_OUTPUT_BYTES:
        raise PdfPreviewError("pdf_preview_output_limit_exceeded")
    return manifest, _read_exact(
        stream, content_length, timeout_seconds=timeout_seconds
    ) if content_length else b""


def _raise_child_error(frame: dict[str, Any]) -> None:
    if set(frame) != {
        "kind", "schema_version", "renderer_version", "page_index",
        "code", "content_length",
    } or (
        frame["schema_version"] != PDF_PREVIEW_RESULT_SCHEMA
        or frame["renderer_version"] != PDF_PREVIEW_RENDERER_VERSION
        or frame["content_length"] != 0
        or not isinstance(frame["code"], str)
        or frame["page_index"] is not None
        and (type(frame["page_index"]) is not int or frame["page_index"] < 0)
    ):
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    raise PdfPreviewError(frame["code"], page_index=frame["page_index"])


def _drop_source_state(pdf) -> None:
    import pikepdf

    page = pdf.pages[0]
    allowed_page_keys = {
        pikepdf.Name.Type,
        pikepdf.Name.Parent,
        pikepdf.Name.Resources,
        pikepdf.Name.MediaBox,
        pikepdf.Name.CropBox,
        pikepdf.Name.Rotate,
        pikepdf.Name.Contents,
        pikepdf.Name.Group,
        pikepdf.Name.UserUnit,
    }
    for key in list(page.obj.keys()):
        if key not in allowed_page_keys:
            del page.obj[key]

    allowed_resource_keys = {
        pikepdf.Name.ExtGState,
        pikepdf.Name.ColorSpace,
        pikepdf.Name.Pattern,
        pikepdf.Name.Shading,
        pikepdf.Name.XObject,
        pikepdf.Name.Font,
        pikepdf.Name.Properties,
    }
    for obj in list(pdf.objects):
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            continue
        resources = obj.get(pikepdf.Name.Resources)
        if resources is None:
            continue
        if not isinstance(resources, pikepdf.Dictionary):
            raise PdfPreviewError("pdf_preview_resources_invalid")
        for key in list(resources.keys()):
            if key not in allowed_resource_keys:
                del resources[key]
    page.remove_unreferenced_resources()

    sanitizer = (
        pikepdf.sanitize.Sanitizer()
        .remove_javascript()
        .remove_external_access()
        .remove_attachments()
        .remove_multimedia()
        .remove_thumbnails()
        .remove_search_index()
        .remove_web_capture()
        .remove_private_app_data()
        .remove_collection()
    )
    sanitizer.apply(pdf)
    forbidden_payload_keys = {
        pikepdf.Name.AA,
        pikepdf.Name.AF,
        pikepdf.Name.Metadata,
        pikepdf.Name.PieceInfo,
        pikepdf.Name.OpenAction,
        pikepdf.Name("/EmbeddedFiles"),
        pikepdf.Name("/EF"),
        pikepdf.Name("/JavaScript"),
        pikepdf.Name("/JS"),
        pikepdf.Name("/RichMediaContent"),
        pikepdf.Name("/RichMediaSettings"),
    }
    for obj in list(pdf.objects):
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            for key in forbidden_payload_keys.intersection(obj.keys()):
                del obj[key]
    for key in list(pdf.Root.keys()):
        if key not in {pikepdf.Name.Type, pikepdf.Name.Pages}:
            del pdf.Root[key]


def _validate_sanitized_objects(pdf) -> None:
    import pikepdf

    allowed_resource_keys = {
        pikepdf.Name.ExtGState,
        pikepdf.Name.ColorSpace,
        pikepdf.Name.Pattern,
        pikepdf.Name.Shading,
        pikepdf.Name.XObject,
        pikepdf.Name.Font,
        pikepdf.Name.Properties,
    }
    forbidden_keys = {
        pikepdf.Name.AA,
        pikepdf.Name.AF,
        pikepdf.Name.Metadata,
        pikepdf.Name.PieceInfo,
        pikepdf.Name.OpenAction,
        pikepdf.Name("/EmbeddedFiles"),
        pikepdf.Name("/EF"),
        pikepdf.Name("/JavaScript"),
        pikepdf.Name("/JS"),
        pikepdf.Name("/RichMediaContent"),
        pikepdf.Name("/RichMediaSettings"),
    }
    forbidden_types = {
        pikepdf.Name.Action,
        pikepdf.Name.Annot,
        pikepdf.Name.EmbeddedFile,
        pikepdf.Name.Filespec,
        pikepdf.Name.Metadata,
    }
    forbidden_action_subtypes = {
        pikepdf.Name("/GoTo"),
        pikepdf.Name("/GoToR"),
        pikepdf.Name("/GoToE"),
        pikepdf.Name("/Launch"),
        pikepdf.Name("/Thread"),
        pikepdf.Name("/URI"),
        pikepdf.Name("/Sound"),
        pikepdf.Name("/Movie"),
        pikepdf.Name("/Hide"),
        pikepdf.Name("/Named"),
        pikepdf.Name("/SubmitForm"),
        pikepdf.Name("/ResetForm"),
        pikepdf.Name("/ImportData"),
        pikepdf.Name.JavaScript,
        pikepdf.Name("/SetOCGState"),
        pikepdf.Name("/Rendition"),
        pikepdf.Name("/Trans"),
        pikepdf.Name("/GoTo3DView"),
    }
    forbidden_subtypes = {
        pikepdf.Name("/FileAttachment"),
        pikepdf.Name("/RichMedia"),
        pikepdf.Name("/Movie"),
        pikepdf.Name("/Sound"),
        pikepdf.Name("/Screen"),
        pikepdf.Name("/3D"),
    }

    for obj in pdf.objects:
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if forbidden_keys.intersection(obj.keys()):
            raise PdfPreviewError("pdf_preview_output_invalid")
        if obj.get(pikepdf.Name.Type) in forbidden_types:
            raise PdfPreviewError("pdf_preview_output_invalid")
        if obj.get(pikepdf.Name.S) in forbidden_action_subtypes:
            raise PdfPreviewError("pdf_preview_output_invalid")
        if obj.get(pikepdf.Name.Subtype) in forbidden_subtypes:
            raise PdfPreviewError("pdf_preview_output_invalid")
        resources = obj.get(pikepdf.Name.Resources)
        if resources is not None and (
            not isinstance(resources, pikepdf.Dictionary)
            or set(resources.keys()) - allowed_resource_keys
        ):
            raise PdfPreviewError("pdf_preview_output_invalid")


def _validated_geometry(pdf) -> tuple[list[float], list[float], int]:
    import pikepdf

    if len(pdf.pages) != 1 or pdf.is_encrypted:
        raise PdfPreviewError("pdf_preview_output_invalid")
    page = pdf.pages[0]
    media_box = list(_box(list(page.MediaBox)))
    crop_value = page.get(pikepdf.Name.CropBox, page.MediaBox)
    crop_box = list(_box(list(crop_value)))
    rotation_value = page.get(pikepdf.Name.Rotate, 0)
    if isinstance(rotation_value, bool):
        raise PdfPreviewError("pdf_preview_geometry_invalid")
    try:
        rotation = int(rotation_value)
    except (TypeError, ValueError) as exc:
        raise PdfPreviewError("pdf_preview_geometry_invalid") from exc
    return media_box, crop_box, rotation


def _render_page_from_source(source, page_index: int) -> PdfPreviewResult:
    import pikepdf

    if page_index >= len(source.pages):
        raise PdfPreviewError("pdf_preview_page_invalid", page_index=page_index)
    output = BytesIO()
    with pikepdf.Pdf.new() as preview:
        preview.pages.append(source.pages[page_index])
        _drop_source_state(preview)
        preview.save(
            output,
            preserve_pdfa=False,
            fix_metadata_version=False,
            normalize_content=False,
            recompress_flate=False,
            deterministic_id=True,
        )
    content = output.getvalue()
    if not 0 < len(content) <= PDF_PREVIEW_MAX_OUTPUT_BYTES:
        raise PdfPreviewError(
            "pdf_preview_output_limit_exceeded", page_index=page_index
        )
    try:
        with pikepdf.Pdf.open(
            BytesIO(content),
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=True,
        ) as reopened:
            media_box, crop_box, rotation = _validated_geometry(reopened)
            if set(reopened.Root.keys()) - {pikepdf.Name.Type, pikepdf.Name.Pages}:
                raise PdfPreviewError("pdf_preview_output_invalid")
            page = reopened.pages[0]
            forbidden_page_keys = {
                pikepdf.Name.Annots,
                pikepdf.Name.AA,
                pikepdf.Name.Metadata,
                pikepdf.Name.PieceInfo,
                pikepdf.Name.Thumb,
                pikepdf.Name.AF,
            }
            if forbidden_page_keys.intersection(page.obj.keys()):
                raise PdfPreviewError("pdf_preview_output_invalid")
            _validate_sanitized_objects(reopened)
    except (
        pikepdf.DataDecodingError,
        pikepdf.PasswordError,
        pikepdf.PdfError,
    ) as exc:
        raise PdfPreviewError(
            "pdf_preview_source_invalid", page_index=page_index
        ) from exc
    return PdfPreviewResult(
        content=content,
        media_box=tuple(media_box),  # type: ignore[arg-type]
        crop_box=tuple(crop_box),  # type: ignore[arg-type]
        rotation=rotation % 360,
        renderer_version=PDF_PREVIEW_RENDERER_VERSION,
    )


def _write_frame(stream: BinaryIO, manifest: dict[str, Any], content: bytes = b"") -> None:
    value = {**manifest, "content_length": len(content)}
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > _FRAME_MANIFEST_MAX_BYTES:
        raise PdfPreviewError("pdf_preview_manifest_invalid")
    stream.write(struct.pack(">I", len(encoded)))
    stream.write(encoded)
    if content:
        stream.write(content)
    stream.flush()


def _error_frame(
    stream: BinaryIO, code: str, *, page_index: int | None = None
) -> None:
    _write_frame(stream, {
        "kind": "error",
        "schema_version": PDF_PREVIEW_RESULT_SCHEMA,
        "renderer_version": PDF_PREVIEW_RENDERER_VERSION,
        "page_index": page_index,
        "code": code,
    })


def _verify_source(
    stream: BinaryIO, *, expected_size: int, expected_sha256: str
) -> None:
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    while chunk := stream.read(_FRAME_READ_CHUNK_BYTES):
        size += len(chunk)
        digest.update(chunk)
        if size > expected_size:
            raise PdfPreviewError("pdf_preview_input_invalid")
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise PdfPreviewError("artifact_checksum_mismatch")
    stream.seek(0)


def _render_document_child(
    source_fd: int,
    *,
    expected_size: int,
    expected_sha256: str,
    max_pages: int,
) -> int:
    import pikepdf

    framed = sys.stdout.buffer
    page_index: int | None = None
    try:
        with os.fdopen(source_fd, "rb", closefd=False) as stream:
            _verify_source(
                stream,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            with pikepdf.Pdf.open(
                stream,
                password="",
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=True,
            ) as source:
                page_count = len(source.pages)
                if page_count <= 0:
                    raise PdfPreviewError("processing_source_empty")
                if page_count > max_pages:
                    raise PdfPreviewError("artifact_too_large")
                _write_frame(framed, {
                    "kind": "document",
                    "schema_version": PDF_PREVIEW_RESULT_SCHEMA,
                    "renderer_version": PDF_PREVIEW_RENDERER_VERSION,
                    "page_count": page_count,
                })
                for page_index in range(page_count):
                    rendered = _render_page_from_source(source, page_index)
                    _write_frame(framed, {
                        "kind": "page",
                        "page_index": page_index,
                        "schema_version": PDF_PREVIEW_RESULT_SCHEMA,
                        "renderer_version": rendered.renderer_version,
                        "content_sha256": hashlib.sha256(
                            rendered.content
                        ).hexdigest(),
                        "media_box": list(rendered.media_box),
                        "crop_box": list(rendered.crop_box),
                        "rotation": rendered.rotation,
                    }, rendered.content)
                _write_frame(framed, {
                    "kind": "complete",
                    "schema_version": PDF_PREVIEW_RESULT_SCHEMA,
                    "renderer_version": PDF_PREVIEW_RENDERER_VERSION,
                    "page_count": page_count,
                })
    except PdfPreviewError as exc:
        try:
            _error_frame(
                framed,
                exc.code,
                page_index=(
                    exc.page_index if exc.page_index is not None else page_index
                ),
            )
        except (BrokenPipeError, OSError):
            pass
        return _CHILD_DETERMINISTIC_FAILURE
    except (
        pikepdf.DataDecodingError,
        pikepdf.PasswordError,
        pikepdf.PdfError,
    ):
        try:
            _error_frame(framed, "pdf_preview_source_invalid", page_index=page_index)
        except (BrokenPipeError, OSError):
            pass
        return _CHILD_DETERMINISTIC_FAILURE
    return 0


def _main(argv: list[str]) -> int:
    if len(argv) != 6 or argv[1] != "--document":
        return 64
    try:
        source_fd = int(argv[2])
        expected_size = int(argv[3])
        expected_sha256 = argv[4]
        max_pages = int(argv[5])
        if (
            source_fd < 0
            or not 0 < expected_size <= PDF_PREVIEW_MAX_INPUT_BYTES
            or len(expected_sha256) != 64
            or any(value not in "0123456789abcdef" for value in expected_sha256)
            or max_pages <= 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        return _CHILD_DETERMINISTIC_FAILURE

    try:
        if os.name == "posix":
            _child_limits()
    except BaseException:
        return _CHILD_INFRASTRUCTURE_FAILURE

    try:
        return _render_document_child(
            source_fd,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            max_pages=max_pages,
        )
    except OSError as exc:
        return (
            _CHILD_DETERMINISTIC_FAILURE
            if exc.errno == errno.EFBIG
            else _CHILD_INFRASTRUCTURE_FAILURE
        )
    except BaseException:
        return _CHILD_INFRASTRUCTURE_FAILURE


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
