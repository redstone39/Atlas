from __future__ import annotations

import hashlib
import json
from typing import Any, BinaryIO, Iterator

from atlas_production.modules.artifact_storage.public import (
    ArtifactStorageError,
    MAX_ARTIFACT_BYTES,
)
from atlas_production.modules.document_intake.formats import (
    SupportedDocumentFormat,
    detect_document_format,
)


CHUNK_SIZE = 1024 * 1024


def inspect_document_upload(
    stream: BinaryIO,
    *,
    filename: str | None,
    client_mime: str | None = None,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> SupportedDocumentFormat:
    """Inspect a seekable multipart spool without copying or hashing its payload."""

    stream.seek(0, 2)
    total = stream.tell()
    stream.seek(0)
    if total > max_bytes:
        raise ArtifactStorageError(
            "artifact_too_large", 'artifact.exceeds_the_upload_size_limit', 413
        )
    try:
        return detect_document_format(
            stream,
            filename=filename,
            client_mime=client_mime,
        )
    finally:
        stream.seek(0)


def uploaded_chunks(stream: BinaryIO) -> Iterator[bytes]:
    stream.seek(0)
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def upload_request_fingerprint(metadata: dict[str, Any], checksum: str | None = None) -> str:
    return hashlib.sha256(
        json.dumps(
            {"metadata": metadata, "content_sha256": checksum},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
