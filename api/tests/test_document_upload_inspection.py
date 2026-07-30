from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

from atlas_production.modules.artifact_storage.public import (
    ArtifactStorageError,
    MAX_ARTIFACT_BYTES,
)
from atlas_production.modules.document_intake.formats import DOCX
from atlas_production.modules.document_intake.upload_stream import (
    CHUNK_SIZE,
    inspect_document_upload,
    uploaded_chunks,
)


def _large_docx() -> bytes:
    content_types = b"""\
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" />
</Types>
"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", b"<document />")
        archive.writestr("word/media/large.bin", b"x" * (2 * CHUNK_SIZE + 17))
    return output.getvalue()


class _TrackingStream(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.payload_size = len(payload)
        self.read_sizes: list[int] = []
        self.total_bytes_read = 0
        self.full_passes = 0
        self._sequential_position: int | None = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        position = super().seek(offset, whence)
        if position == 0:
            self._sequential_position = 0
        elif (
            self._sequential_position is not None
            and position != self._sequential_position
        ):
            self._sequential_position = None
        return position

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("upload inspection requested an unbounded read")
        before = self.tell()
        chunk = super().read(size)
        after = self.tell()
        self.total_bytes_read += len(chunk)
        if self._sequential_position is not None:
            if before != self._sequential_position:
                self._sequential_position = None
            else:
                self._sequential_position = after
                if chunk and after == self.payload_size:
                    self.full_passes += 1
                    self._sequential_position = None
        return chunk


def test_seekable_upload_inspection_is_bounded_before_single_streaming_pass() -> None:
    payload = _large_docx()
    stream = _TrackingStream(payload)

    detected = inspect_document_upload(
        stream,
        filename="bounded.docx",
        client_mime="application/octet-stream",
    )
    streamed = b"".join(uploaded_chunks(stream))

    assert detected.canonical_mime == DOCX
    assert streamed == payload
    assert stream.full_passes == 1
    assert all(size >= 0 for size in stream.read_sizes)
    assert max(stream.read_sizes) <= CHUNK_SIZE
    assert stream.total_bytes_read < len(payload) + CHUNK_SIZE


class _OversizedSeekableSpool:
    def __init__(self) -> None:
        self.position = 0
        self.read_calls = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 2:
            self.position = MAX_ARTIFACT_BYTES + 1 + offset
        elif whence == 1:
            self.position += offset
        else:
            self.position = offset
        return self.position

    def tell(self) -> int:
        return self.position

    def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        raise AssertionError("oversized spools must be rejected without reading")


def test_oversized_seekable_spool_is_rejected_without_hashing_or_reading() -> None:
    stream = _OversizedSeekableSpool()

    with pytest.raises(ArtifactStorageError) as caught:
        inspect_document_upload(stream, filename="oversized.pdf")

    assert caught.value.error_code == "artifact_too_large"
    assert stream.read_calls == 0
