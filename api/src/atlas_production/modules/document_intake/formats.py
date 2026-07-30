from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO, TextIOWrapper
from pathlib import PurePath
import csv
import os
import struct
import zipfile
import xml.etree.ElementTree as ET
from typing import BinaryIO

from pypdf import PdfReader


PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TXT = "text/plain"
CSV = "text/csv"
DOC = "application/msword"
PPT = "application/vnd.ms-powerpoint"
XLS = "application/vnd.ms-excel"


@dataclass(frozen=True, slots=True)
class SupportedDocumentFormat:
    extension: str
    canonical_mime: str
    document_format: str
    profile_id: str
    parser_id: str
    preview_kind: str
    modern_mime: str | None = None
    source_download_restricted: bool = False


SUPPORTED_FORMATS: dict[str, SupportedDocumentFormat] = {
    ".pdf": SupportedDocumentFormat(".pdf", PDF, "pdf", "default-pdf", "atlas-pypdf", "pdf_page"),
    ".docx": SupportedDocumentFormat(".docx", DOCX, "docx", "default-docx", "atlas-python-docx", "page_image"),
    ".pptx": SupportedDocumentFormat(".pptx", PPTX, "pptx", "default-pptx", "atlas-python-pptx", "page_image"),
    ".xlsx": SupportedDocumentFormat(".xlsx", XLSX, "xlsx", "default-xlsx", "atlas-openpyxl", "page_image"),
    ".txt": SupportedDocumentFormat(".txt", TXT, "txt", "default-text", "atlas-plain-text", "inline_text"),
    ".csv": SupportedDocumentFormat(".csv", CSV, "csv", "default-csv", "atlas-csv", "inline_text"),
    ".doc": SupportedDocumentFormat(".doc", DOC, "doc", "default-doc", "atlas-libreoffice-doc", "page_image", DOCX),
    ".ppt": SupportedDocumentFormat(".ppt", PPT, "ppt", "default-ppt", "atlas-libreoffice-ppt", "page_image", PPTX),
    ".xls": SupportedDocumentFormat(".xls", XLS, "xls", "default-xls", "atlas-libreoffice-xls", "page_image", XLSX),
}

SUPPORTED_MIME_TYPES = frozenset(item.canonical_mime for item in SUPPORTED_FORMATS.values())
OFFICE_MIME_TYPES = frozenset({DOCX, PPTX, XLSX, DOC, PPT, XLS})
LEGACY_OFFICE_MIME_TYPES = frozenset({DOC, PPT, XLS})
INLINE_PREVIEW_MIME_TYPES = frozenset({TXT, CSV})


def source_allows_original_download(
    content_type: str | None, *, source_download_restricted: bool
) -> bool:
    """Allow current supported source bytes unless the format is restricted."""

    return (
        not source_download_restricted
        and content_type in SUPPORTED_MIME_TYPES
    )


class DocumentFormatError(ValueError):
    def __init__(self, error_code: str, message_code: str) -> None:
        self.error_code = error_code
        self.message_code = message_code
        super().__init__(error_code)


_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_OLE_FREE_SECTOR = 0xFFFFFFFF
_OLE_END_OF_CHAIN = 0xFFFFFFFE
_OLE_SPECIAL_SECTORS = {
    _OLE_FREE_SECTOR,
    _OLE_END_OF_CHAIN,
    0xFFFFFFFD,  # FAT sector
    0xFFFFFFFC,  # DIFAT sector
}
_OLE_ENCRYPTION_STREAMS = {"EncryptedPackage", "EncryptionInfo"}
_OLE_FAMILY_STREAMS = {
    "WordDocument": ".doc",
    "PowerPoint Document": ".ppt",
    "Workbook": ".xls",
}

_CONTENT_TYPES_XML = "[Content_Types].xml"
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_OOXML_MAIN_PARTS = {
    ".docx": (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    ),
    ".pptx": (
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    ),
    ".xlsx": (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
}

_FORMAT_READ_CHUNK = 1024 * 1024


def _seekable_content_stream(
    content: bytes | bytearray | memoryview | BinaryIO,
) -> BinaryIO:
    if isinstance(content, (bytes, bytearray, memoryview)):
        return BytesIO(content)
    content.seek(0)
    return content


def _stream_size(stream: BinaryIO) -> int:
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(position)
    return size


def _read_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    return stream.read(size)


def _ole_directory_stream_names(stream: BinaryIO) -> set[str]:
    """Read exact stream names from a Compound File Binary directory.

    Format detection needs only the directory chain, not the mini-stream contents. Parsing
    directory entries prevents payload bytes that merely contain a product name from being
    mistaken for a Word, PowerPoint, Excel, or encrypted Office container.
    """

    size = _stream_size(stream)
    header = _read_at(stream, 0, 512)
    if len(header) != 512 or header[:8] != _OLE_SIGNATURE:
        raise ValueError("invalid OLE signature")
    if header[28:30] != b"\xfe\xff":
        raise ValueError("unsupported OLE byte order")
    major_version = struct.unpack_from("<H", header, 26)[0]
    sector_shift = struct.unpack_from("<H", header, 30)[0]
    if (major_version, sector_shift) not in {(3, 9), (4, 12)}:
        raise ValueError("unsupported OLE sector version")
    sector_size = 1 << sector_shift
    if size < sector_size or size % sector_size:
        raise ValueError("truncated OLE sectors")
    sector_count = size // sector_size - 1
    if sector_count < 2:
        raise ValueError("OLE file has no directory and FAT sectors")

    def read_sector(sector_id: int) -> bytes:
        if sector_id in _OLE_SPECIAL_SECTORS or not 0 <= sector_id < sector_count:
            raise ValueError("invalid OLE sector reference")
        start = (sector_id + 1) * sector_size
        sector = _read_at(stream, start, sector_size)
        if len(sector) != sector_size:
            raise ValueError("truncated OLE sector")
        return sector

    fat_sector_count = struct.unpack_from("<I", header, 44)[0]
    first_directory_sector = struct.unpack_from("<I", header, 48)[0]
    first_difat_sector = struct.unpack_from("<I", header, 68)[0]
    difat_sector_count = struct.unpack_from("<I", header, 72)[0]
    if not 1 <= fat_sector_count <= sector_count:
        raise ValueError("invalid OLE FAT count")
    if difat_sector_count > sector_count:
        raise ValueError("invalid OLE DIFAT count")

    header_difat = struct.unpack_from("<109I", header, 76)
    fat_sector_ids = [
        sector_id for sector_id in header_difat
        if sector_id != _OLE_FREE_SECTOR
    ]
    current_difat = first_difat_sector
    seen_difat: set[int] = set()
    entries_per_difat_sector = sector_size // 4
    for _ in range(difat_sector_count):
        if current_difat in seen_difat:
            raise ValueError("cyclic OLE DIFAT chain")
        seen_difat.add(current_difat)
        values = struct.unpack(
            f"<{entries_per_difat_sector}I", read_sector(current_difat)
        )
        fat_sector_ids.extend(
            sector_id for sector_id in values[:-1]
            if sector_id != _OLE_FREE_SECTOR
        )
        current_difat = values[-1]
    if difat_sector_count and current_difat != _OLE_END_OF_CHAIN:
        raise ValueError("unterminated OLE DIFAT chain")
    if len(fat_sector_ids) < fat_sector_count:
        raise ValueError("incomplete OLE FAT allocation")
    fat_sector_ids = fat_sector_ids[:fat_sector_count]
    if len(set(fat_sector_ids)) != len(fat_sector_ids):
        raise ValueError("duplicate OLE FAT sector")

    fat: list[int] = []
    entries_per_fat_sector = sector_size // 4
    for sector_id in fat_sector_ids:
        fat.extend(struct.unpack(
            f"<{entries_per_fat_sector}I", read_sector(sector_id)
        ))
    if len(fat) < sector_count:
        raise ValueError("incomplete OLE FAT")

    def iter_chain(first_sector: int):
        current = first_sector
        seen: set[int] = set()
        while current != _OLE_END_OF_CHAIN:
            if current in seen:
                raise ValueError("cyclic OLE sector chain")
            seen.add(current)
            yield read_sector(current)
            if current >= len(fat):
                raise ValueError("OLE FAT reference is out of bounds")
            current = fat[current]
            if current == _OLE_FREE_SECTOR:
                raise ValueError("unterminated OLE sector chain")

    stream_names: set[str] = set()
    saw_root = False
    for directory_sector in iter_chain(first_directory_sector):
        for offset in range(0, len(directory_sector), 128):
            entry = directory_sector[offset : offset + 128]
            if len(entry) != 128:
                raise ValueError("truncated OLE directory entry")
            object_type = entry[66]
            if object_type == 0:
                continue
            if object_type not in {1, 2, 5}:
                raise ValueError("invalid OLE directory object type")
            name_length = struct.unpack_from("<H", entry, 64)[0]
            if not 2 <= name_length <= 64 or name_length % 2:
                raise ValueError("invalid OLE directory name length")
            encoded_name = entry[:name_length]
            if encoded_name[-2:] != b"\0\0":
                raise ValueError("unterminated OLE directory name")
            name = encoded_name[:-2].decode("utf-16le")
            if not name or "\0" in name:
                raise ValueError("invalid OLE directory name")
            if object_type == 5:
                saw_root = True
            elif object_type == 2:
                stream_names.add(name)
    if not saw_root:
        raise ValueError("OLE root directory is missing")
    return stream_names


def _validated_ole_stream_names(stream: BinaryIO) -> set[str]:
    try:
        return _ole_directory_stream_names(stream)
    except (UnicodeDecodeError, ValueError, struct.error) as exc:
        raise DocumentFormatError(
            "document_corrupt", "document.file_is_corrupt_or_incomplete"
        ) from exc


def _validate_legacy_ole(stream: BinaryIO, extension: str) -> None:
    if _read_at(stream, 0, len(_OLE_SIGNATURE)) != _OLE_SIGNATURE:
        raise DocumentFormatError(
            "document_format_mismatch", "document.file_extension_and_content_do_not_match"
        )
    stream_names = _validated_ole_stream_names(stream)
    if stream_names & _OLE_ENCRYPTION_STREAMS:
        raise DocumentFormatError(
            "document_encrypted_unsupported", "document.encrypted_files_are_not_supported"
        )
    detected_families = {
        family for name, family in _OLE_FAMILY_STREAMS.items()
        if name in stream_names
    }
    if detected_families != {extension}:
        raise DocumentFormatError(
            "document_format_mismatch", "document.file_extension_and_content_do_not_match"
        )


def _extension(filename: str | None) -> str:
    if not filename:
        raise DocumentFormatError(
            "document_filename_missing", "document.file_upload_requires_a_named_file"
        )
    suffix = PurePath(filename.replace("\\", "/")).suffix.casefold()
    if suffix not in SUPPORTED_FORMATS:
        raise DocumentFormatError(
            "document_format_unsupported", "document.file_format_is_not_supported"
        )
    return suffix


def _validate_pdf(stream: BinaryIO) -> bool:
    if _read_at(stream, 0, 5) != b"%PDF-":
        raise DocumentFormatError(
            "document_format_mismatch", "document.file_extension_and_content_do_not_match"
        )
    try:
        stream.seek(0)
        reader = PdfReader(stream, strict=True)
        source_download_restricted = reader.is_encrypted
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise DocumentFormatError(
                    "document_encrypted_unsupported",
                    "document.encrypted_files_are_not_supported",
                ) from exc
            if not unlocked:
                raise DocumentFormatError(
                    "document_encrypted_unsupported",
                    "document.encrypted_files_are_not_supported",
                )
        if not reader.pages:
            raise ValueError("pdf has no pages")
        max_pages = min(int(os.getenv("ATLAS_PDF_MAX_PAGES", "3000")), 3000)
        if max_pages <= 0:
            raise RuntimeError("pdf_page_limit_invalid")
        if len(reader.pages) > max_pages:
            raise DocumentFormatError(
                "artifact_too_large", "artifact.exceeds_the_upload_size_limit"
            )
        return source_download_restricted
    except DocumentFormatError:
        raise
    except Exception as exc:
        raise DocumentFormatError(
            "document_corrupt", "document.file_is_corrupt_or_incomplete"
        ) from exc


def _ooxml_content_type_metadata(
    archive: zipfile.ZipFile,
) -> tuple[set[tuple[str | None, str | None]], set[str]]:
    expected_root = f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
    override_tag = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
    root_tag: str | None = None
    overrides: set[tuple[str | None, str | None]] = set()
    content_type_values: set[str] = set()
    with archive.open(_CONTENT_TYPES_XML) as content_types:
        for event, item in ET.iterparse(
            content_types,
            events=("start", "end"),
        ):
            if root_tag is None and event == "start":
                root_tag = item.tag
            if event != "end":
                continue
            content_type = item.get("ContentType")
            if content_type:
                content_type_values.add(content_type.casefold())
            if item.tag == override_tag:
                overrides.add((item.get("PartName"), content_type))
            item.clear()
    if root_tag != expected_root:
        raise ValueError("invalid content types root")
    return overrides, content_type_values


def _ooxml_family(stream: BinaryIO) -> str:
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise ValueError("duplicate package member")
            if _CONTENT_TYPES_XML not in names:
                raise ValueError("missing content types")
            if any(member.flag_bits & 0x1 for member in members):
                raise DocumentFormatError(
                    "document_encrypted_unsupported",
                    "document.encrypted_files_are_not_supported",
                )
            overrides, content_type_values = _ooxml_content_type_metadata(archive)
            member_names = {name.casefold() for name in names}
            if (
                any(
                    name.endswith("vbaproject.bin")
                    or name.endswith("vbaprojectsignature.bin")
                    for name in member_names
                )
                or any(
                    "macroenabled" in content_type or "vbaproject" in content_type
                    for content_type in content_type_values
                )
            ):
                raise DocumentFormatError(
                    "document_format_unsupported",
                    "document.file_format_is_not_supported",
                )

            detected_families = {
                family
                for family, (main_part, main_content_type) in _OOXML_MAIN_PARTS.items()
                if main_part in names
                and (f"/{main_part}", main_content_type) in overrides
            }
            if len(detected_families) == 1:
                return detected_families.pop()
    except DocumentFormatError:
        raise
    except (
        ET.ParseError,
        KeyError,
        RuntimeError,
        zipfile.BadZipFile,
        OSError,
        ValueError,
    ) as exc:
        raise DocumentFormatError(
            "document_corrupt", "document.file_is_corrupt_or_incomplete"
        ) from exc
    raise DocumentFormatError(
        "document_format_mismatch", "document.file_extension_and_content_do_not_match"
    )


def _validate_text(stream: BinaryIO, *, csv_expected: bool) -> None:
    stream.seek(0)
    text_stream = TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    try:
        if csv_expected:
            for row in csv.reader(text_stream):
                if any("\x00" in field for field in row):
                    raise DocumentFormatError(
                        "document_format_mismatch",
                        "document.file_extension_and_content_do_not_match",
                    )
        else:
            while text := text_stream.read(_FORMAT_READ_CHUNK):
                if "\x00" in text:
                    raise DocumentFormatError(
                        "document_format_mismatch",
                        "document.file_extension_and_content_do_not_match",
                    )
    except DocumentFormatError:
        raise
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DocumentFormatError(
            "document_corrupt", "document.file_is_corrupt_or_incomplete"
        ) from exc
    finally:
        text_stream.detach()


def detect_document_format(
    content: bytes | bytearray | memoryview | BinaryIO,
    *,
    filename: str | None,
    client_mime: str | None = None,
) -> SupportedDocumentFormat:
    """Determine the canonical format from extension and authoritative container bytes.

    ``client_mime`` is intentionally not authoritative. It is accepted for observability
    and future diagnostics but cannot override a verified file signature/container.
    """

    del client_mime
    extension = _extension(filename)
    selected = SUPPORTED_FORMATS[extension]
    stream = _seekable_content_stream(content)
    try:
        if _stream_size(stream) == 0:
            raise DocumentFormatError(
                "document_empty", "document.the_uploaded_file_is_empty"
            )
        if extension == ".pdf":
            source_download_restricted = _validate_pdf(stream)
        elif extension in {".docx", ".pptx", ".xlsx"}:
            if _read_at(stream, 0, len(_OLE_SIGNATURE)) == _OLE_SIGNATURE:
                stream_names = _validated_ole_stream_names(stream)
                if stream_names & _OLE_ENCRYPTION_STREAMS:
                    raise DocumentFormatError(
                        "document_encrypted_unsupported",
                        "document.encrypted_files_are_not_supported",
                    )
                raise DocumentFormatError(
                    "document_format_mismatch",
                    "document.file_extension_and_content_do_not_match",
                )
            family = _ooxml_family(stream)
            if family != extension:
                raise DocumentFormatError(
                    "document_format_mismatch",
                    "document.file_extension_and_content_do_not_match",
                )
        elif extension in {".doc", ".ppt", ".xls"}:
            _validate_legacy_ole(stream, extension)
        else:
            _validate_text(stream, csv_expected=extension == ".csv")
        return (
            replace(
                selected,
                source_download_restricted=source_download_restricted,
            )
            if extension == ".pdf"
            else selected
        )
    finally:
        stream.seek(0)
