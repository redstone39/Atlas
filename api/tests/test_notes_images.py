from io import BytesIO

import pytest
from PIL import Image

from atlas_production.modules.notes.images import inspect_note_image
from atlas_production.modules.notes.public import MAX_NOTE_BINARY_BYTES, NotesError


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), color=(12, 34, 56)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")),
)
def test_inspect_note_image_accepts_supported_original_bytes(
    image_format: str, mime_type: str
) -> None:
    content = _image_bytes(image_format)

    assert inspect_note_image(content, mime_type) == (mime_type, 3, 2)


@pytest.mark.parametrize(
    ("content", "claimed_mime", "code"),
    (
        (b"", "image/png", "payload_oversize"),
        (b"not-an-image", "image/png", "invalid_image"),
        (_image_bytes("PNG"), "image/jpeg", "unsupported_media_type"),
        (_image_bytes("GIF"), "image/gif", "invalid_image"),
    ),
)
def test_inspect_note_image_rejects_invalid_or_unsupported_bytes(
    content: bytes, claimed_mime: str, code: str
) -> None:
    with pytest.raises(NotesError) as caught:
        inspect_note_image(content, claimed_mime)

    assert caught.value.code == code


def test_inspect_note_image_rejects_oversize_before_decode() -> None:
    with pytest.raises(NotesError) as caught:
        inspect_note_image(b"x" * (MAX_NOTE_BINARY_BYTES + 1), "image/png")

    assert caught.value.code == "payload_oversize"
