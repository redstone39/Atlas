"""Bounded validation for original Notes image bytes without transcoding."""

from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

from .public import MAX_NOTE_BINARY_BYTES, NotesError


ImageMime = Literal["image/png", "image/jpeg", "image/webp"]
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_WORKING_BYTES = 128 * 1024 * 1024
_FORMAT_MIME: dict[str, ImageMime] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


def inspect_note_image(
    content: bytes, claimed_mime_type: str | None
) -> tuple[ImageMime, int, int]:
    if not content or len(content) > MAX_NOTE_BINARY_BYTES:
        raise NotesError("payload_oversize", "Attachment is empty or too large", 413)
    try:
        with Image.open(BytesIO(content)) as source:
            mime = _FORMAT_MIME.get(source.format or "")
            width, height = source.size
            bands = len(source.getbands())
            if (
                mime is None
                or width < 1
                or height < 1
                or width * height > MAX_IMAGE_PIXELS
                or width * height * max(bands, 1) > MAX_IMAGE_WORKING_BYTES
            ):
                raise NotesError("invalid_image", "Image exceeds safe decode limits", 422)
            source.verify()
        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
    except NotesError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise NotesError("invalid_image", "Image bytes are invalid or damaged", 422) from exc
    if claimed_mime_type != mime:
        raise NotesError(
            "unsupported_media_type",
            "Clipboard MIME does not match PNG, JPEG, or WebP bytes",
            415,
        )
    return mime, width, height


__all__ = ["ImageMime", "inspect_note_image"]
