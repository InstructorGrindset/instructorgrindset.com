"""Minimal PNG writer and reader (stdlib only: zlib and struct).

Writes 8 bit RGBA images (colour type 6) with one filter per scanline. The pipeline uses it for the
heat map rasters in data/build/heat/ (docs/map/ARCHITECTURE.md, "Heat maps"), so the build needs no
imaging library. Also writes and reads 16 bit greyscale images (the committed terrain extract,
``pipeline/terrain.py``), reads 8 bit RGB tiles (the Terrain Tiles download) and writes and reads 8 bit
palette images (colour type 3, the share cards of ``pipeline/share.py``).
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


def encode_gray16(width: int, height: int, values: list[int] | tuple[int, ...], *, filter_type: int = FILTER_UP,
                  level: int = 9) -> bytes:
    """PNG bytes of a 16 bit greyscale image (colour type 0): ``height`` rows of ``width`` values 0 to 65535."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(values) != width * height:
        raise ValueError(f"expected {width * height} values, got {len(values)}")
    raw = bytearray()
    prev = bytes(width * 2)
    for j in range(height):
        row = struct.pack(f">{width}H", *values[j * width:(j + 1) * width])
        raw.append(filter_type)
        raw += _filter_row(row, prev, filter_type, bpp=2)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), level)) + _chunk(b"IEND", b"")


def encode_indexed(width: int, height: int, indices: bytes | bytearray, palette: list[tuple[int, int, int]], *,
                   level: int = 9) -> bytes:
    """PNG bytes of an 8 bit palette image (colour type 3): ``height`` rows of ``width`` palette indices, top row
    first, and up to 256 RGB palette entries. Rows are unfiltered, which compresses palette images best."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(indices) != width * height:
        raise ValueError(f"expected {width * height} indices, got {len(indices)}")
    if not 0 < len(palette) <= 256 or max(indices) >= len(palette):
        raise ValueError("palette must hold 1 to 256 colors and cover every index")
    raw = bytearray()
    for j in range(height):
        raw.append(FILTER_NONE)
        raw += indices[j * width:(j + 1) * width]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    return (SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", plte) + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _chunk(b"IEND", b""))


def palette(data: bytes) -> list[tuple[int, int, int]]:
    """The PLTE entries of a PNG (empty when it has none)."""
    pos = 8
    while pos + 8 <= len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        if data[pos + 4:pos + 8] == b"PLTE":
            body = data[pos + 8:pos + 8 + n]
            return [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        pos += 12 + n
    return []


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def decode(data: bytes) -> tuple[int, int, int, int, bytes]:
    """(width, height, channels, bit depth, unfiltered pixel bytes, top row first) of a non interlaced PNG in
    colour type 0, 2, 4 or 6 at 8 or 16 bits, or colour type 3 at 8 bits (the bytes are palette indices, see
    ``palette``). Checks the signature and every chunk CRC."""
    if data[:8] != SIGNATURE:
        raise ValueError("not a PNG")
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"bad CRC in {kind!r}")
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if ihdr is None:
        raise ValueError("PNG without IHDR")
    width, height, depth, ctype, comp, filt, inter = ihdr
    if ctype not in _CHANNELS or depth not in (8, 16) or (ctype == 3 and depth != 8) or comp or filt or inter:
        raise ValueError(f"unsupported PNG (colour type {ctype}, depth {depth}, interlace {inter})")
    bpp = _CHANNELS[ctype] * depth // 8
    stride = width * bpp
    raw = zlib.decompress(b"".join(idat))
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG data has the wrong length")
    out = bytearray()
    prev = bytes(stride)
    for j in range(height):
        ft = raw[j * (stride + 1)]
        row = bytearray(raw[j * (stride + 1) + 1:(j + 1) * (stride + 1)])
        if ft == FILTER_UP:
            row = bytearray((x + p) & 0xFF for x, p in zip(row, prev))
        elif ft == FILTER_SUB:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ft == FILTER_AVERAGE:
            for i in range(stride):
                row[i] = (row[i] + (((row[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 0xFF
        elif ft == FILTER_PAETH:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ft != FILTER_NONE:
            raise ValueError(f"unknown PNG filter {ft}")
        out += row
        prev = bytes(row)
    return width, height, _CHANNELS[ctype], depth, bytes(out)


def decode_gray16(data: bytes) -> tuple[int, int, list[int]]:
    """(width, height, values) of a 16 bit greyscale PNG."""
    w, h, ch, depth, px = decode(data)
    if ch != 1 or depth != 16:
        raise ValueError("expected a 16 bit greyscale PNG")
    return w, h, list(struct.unpack(f">{w * h}H", px))
