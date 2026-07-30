from __future__ import annotations

import struct
from io import BytesIO
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_DECODED_PAGE_BYTES = 262_144_000
_MAX_IMAGE_PIXELS = 12_000_000
_MAX_IMAGE_WORKING_BYTES = 128 * 1024 * 1024


def normalize_lossless_rgb_png(content: bytes) -> tuple[bytes, int, int]:
    """Decode one bounded image and remove metadata while producing canonical RGB PNG."""
    from PIL import Image, UnidentifiedImageError

    if not content or len(content) > _MAX_DECODED_PAGE_BYTES:
        raise ValueError("image_input_invalid")
    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            pixels = width * height
            source_bands = max(1, len(source.getbands()))
            # Reject from header metadata before Pillow materializes the image.
            # The budget covers the source decode, RGB conversion and encoded
            # output buffer so two 1 GiB workers cannot be exhausted by one
            # highly compressed, oversized image.
            estimated_working_bytes = pixels * (source_bands + 6)
            if (
                width <= 0
                or height <= 0
                or pixels > _MAX_IMAGE_PIXELS
                or pixels * 3 > _MAX_DECODED_PAGE_BYTES
                or estimated_working_bytes > _MAX_IMAGE_WORKING_BYTES
            ):
                raise ValueError("image_input_invalid")
            source.load()
            normalized = source.convert("RGB")
            output = BytesIO()
            normalized.save(output, format="PNG", optimize=False, compress_level=9)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("image_input_invalid") from exc
    png = output.getvalue()
    if validated_rgb_png_dimensions(png) != (width, height):
        raise ValueError("image_normalization_failed")
    return png, width, height


def validated_rgb_png_dimensions(content: bytes) -> tuple[int, int] | None:
    """Validate the renderer's lossless RGB PNG contract and return dimensions."""
    if not content.startswith(PNG_SIGNATURE):
        return None
    offset = len(PNG_SIGNATURE)
    width = height = None
    compressed = bytearray()
    saw_iend = False
    saw_idat = False
    first_chunk = True
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4], "big")
        chunk_type = content[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if data_end < data_start or crc_end > len(content):
            return None
        data = content[data_start:data_end]
        expected_crc = int.from_bytes(content[data_end:crc_end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            return None
        if first_chunk:
            if chunk_type != b"IHDR" or length != 13:
                return None
            first_chunk = False
        if chunk_type == b"IHDR":
            if width is not None or length != 13:
                return None
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if (
                width <= 0
                or height <= 0
                or bit_depth != 8
                or color_type != 2
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                return None
        elif chunk_type == b"IDAT":
            if saw_iend:
                return None
            saw_idat = True
            compressed.extend(data)
        elif chunk_type == b"IEND":
            if length != 0 or not saw_idat:
                return None
            saw_iend = True
            offset = crc_end
            break
        else:
            # Atlas renderer pages intentionally use a closed RGB profile:
            # IHDR, one or more consecutive IDAT chunks, then IEND.
            return None
        offset = crc_end
    if (
        width is None
        or height is None
        or not compressed
        or not saw_iend
        or offset != len(content)
    ):
        return None
    expected_length = height * (1 + width * 3)
    if expected_length > _MAX_DECODED_PAGE_BYTES:
        return None
    try:
        decoder = zlib.decompressobj()
        decompressed = decoder.decompress(bytes(compressed), expected_length + 1)
        if len(decompressed) > expected_length or decoder.unconsumed_tail:
            return None
        decompressed += decoder.flush(expected_length + 1 - len(decompressed))
    except zlib.error:
        return None
    if (
        len(decompressed) != expected_length
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        return None
    stride = 1 + width * 3
    if any(decompressed[row * stride] > 4 for row in range(height)):
        return None
    return width, height
