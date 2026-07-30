from io import BytesIO
import struct
import zlib

from PIL import Image, PngImagePlugin
import pytest

from atlas_production.shared.png import (
    normalize_lossless_rgb_png,
    validated_rgb_png_dimensions,
)


def test_image_input_is_normalized_to_deterministic_metadata_free_rgb_png() -> None:
    source = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-note", "must not leave processing")
    Image.new("RGBA", (3, 2), (10, 20, 30, 120)).save(
        source, format="PNG", pnginfo=metadata
    )

    first, width, height = normalize_lossless_rgb_png(source.getvalue())
    second, second_width, second_height = normalize_lossless_rgb_png(
        source.getvalue()
    )

    assert (width, height) == (second_width, second_height) == (3, 2)
    assert first == second
    assert validated_rgb_png_dimensions(first) == (3, 2)
    assert b"private-note" not in first


def test_image_normalization_rejects_non_image_bytes() -> None:
    with pytest.raises(ValueError, match="image_input_invalid"):
        normalize_lossless_rgb_png(b"not-an-image")


def test_oversized_compressed_png_is_rejected_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            len(payload).to_bytes(4, "big")
            + kind
            + payload
            + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    oversized = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 12_000_001, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )

    def unexpected_decode(*_args, **_kwargs):
        raise AssertionError("oversized image must be rejected before load")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", unexpected_decode)
    with pytest.raises(ValueError, match="image_input_invalid"):
        normalize_lossless_rgb_png(oversized)
