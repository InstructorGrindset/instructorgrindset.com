"""Minimal PNG writer (stdlib only: zlib and struct).

Writes 8 bit RGBA images (colour type 6) with one filter per scanline. The pipeline uses it for the
heat map rasters in data/build/heat/ (docs/map/ARCHITECTURE.md, "Heat maps"), so the build needs no
imaging library.
"""
from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG filter types (ISO/IEC 15948, section 9.2).
FILTER_NONE, FILTER_SUB, FILTER_UP, FILTER_AVERAGE, FILTER_PAETH = range(5)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(row: bytes, prev: bytes, kind: int, bpp: int = 4) -> bytes:
    if kind == FILTER_NONE:
        return row
    if kind == FILTER_UP:
        return bytes((x - p) & 0xFF for x, p in zip(row, prev))
    if kind == FILTER_SUB:
        return row[:bpp] + bytes((row[i] - row[i - bpp]) & 0xFF for i in range(bpp, len(row)))
    if kind == FILTER_AVERAGE:
        out = bytearray(len(row))
        for i, x in enumerate(row):
            left = row[i - bpp] if i >= bpp else 0
            out[i] = (x - ((left + prev[i]) >> 1)) & 0xFF
        return bytes(out)
    if kind == FILTER_PAETH:
        out = bytearray(len(row))
        for i, x in enumerate(row):
            left = row[i - bpp] if i >= bpp else 0
            upleft = prev[i - bpp] if i >= bpp else 0
            out[i] = (x - _paeth(left, prev[i], upleft)) & 0xFF
        return bytes(out)
    raise ValueError(f"unknown PNG filter {kind}")


def encode_rgba(width: int, height: int, pixels: bytes | bytearray, *, filter_type: int = FILTER_UP,
                level: int = 9) -> bytes:
    """PNG bytes for ``pixels``: ``height`` rows of ``width`` RGBA quadruples, top row first."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    stride = width * 4
    if len(pixels) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGBA, got {len(pixels)}")
    raw = bytearray()
    prev = bytes(stride)
    for j in range(height):
        row = bytes(pixels[j * stride:(j + 1) * stride])
        raw.append(filter_type)
        raw += _filter_row(row, prev, filter_type)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), level)) + _chunk(b"IEND", b"")
