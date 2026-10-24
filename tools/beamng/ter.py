"""BeamNG terrain files: the binary ``.ter`` and its ``.terrain.json`` companion.

Version 9 layout (BeamNG documentation, "Terrain Files (.ter and .terrain.json)", checked 2026-10-24), little
endian::

    u8   version            9
    u32  size               vertices a side
    u16  heights[size*size] row major, row 0 at the south edge; metres = value / 65535 * TerrainBlock.maxHeight
    u8   layers[size*size]  index into the material names, same order
    u32  material count
    then per material: u8 length, that many ASCII bytes (the TerrainMaterial internalName)

The ``.terrain.json`` ``binaryFormat`` string that the game writes still names a ``layerTextureMap`` block, but the
version 9 data written by the engine has none; this module follows the saved layout. Version 8 files (older levels)
carry extra layer blocks and are not read here.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Sequence

import numpy as np

VERSION = 9
BINARY_FORMAT = ("version(char), size(unsigned int), heightMap(heightMapSize * heightMapItemSize), "
                 "layerMap(layerMapSize * layerMapItemSize), materialNames")


def quantize(heights: np.ndarray) -> tuple[np.ndarray, float, float]:
    """(u16 heights, base_z, max_height): metres = base_z + value / 65535 * max_height. ``max_height`` is at least
    1 m so a flat area still encodes."""
    base = float(np.min(heights))
    span = max(1.0, float(np.max(heights)) - base)
    q = np.rint((heights - base) / span * 65535)
    return np.clip(q, 0, 65535).astype("<u2"), base, span


def encode(heights_u16: np.ndarray, layers: np.ndarray, materials: Sequence[str]) -> bytes:
    n = heights_u16.shape[0]
    if heights_u16.shape != (n, n) or layers.shape != (n, n):
        raise ValueError(f"heights {heights_u16.shape} and layers {layers.shape} must both be square and equal")
    if not materials or len(materials) > 255:
        raise ValueError("a terrain needs 1 to 255 materials")
    if int(layers.max()) >= len(materials):
        raise ValueError(f"layer index {int(layers.max())} has no material ({len(materials)} names)")
    out = bytearray(struct.pack("<BI", VERSION, n))
    out += np.ascontiguousarray(heights_u16, dtype="<u2").tobytes()
    out += np.ascontiguousarray(layers, dtype=np.uint8).tobytes()
    out += struct.pack("<I", len(materials))
    for name in materials:
        raw = name.encode("ascii")
        if not 0 < len(raw) < 255:
            raise ValueError(f"material name must be 1 to 254 ASCII characters: {name!r}")
        out += struct.pack("<B", len(raw)) + raw
    return bytes(out)


def decode(data: bytes) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(heights u16 [j, i], layers u8 [j, i], material names) of a version 9 file."""
    version, n = struct.unpack_from("<BI", data, 0)
    if version != VERSION:
        raise ValueError(f"terrain file version {version}; only version {VERSION} is read")
    off = 5
    heights = np.frombuffer(data, dtype="<u2", count=n * n, offset=off).reshape(n, n)
    off += 2 * n * n
    layers = np.frombuffer(data, dtype=np.uint8, count=n * n, offset=off).reshape(n, n)
    off += n * n
    (count,) = struct.unpack_from("<I", data, off)
    off += 4
    names = []
    for _ in range(count):
        length = data[off]
        names.append(data[off + 1:off + 1 + length].decode("ascii"))
        off += 1 + length
    if off != len(data):
        raise ValueError(f"{len(data) - off} unexpected bytes after the material names")
    return heights, layers, names


def terrain_json(size: int, materials: Sequence[str], datafile: str, heightmap_image: str) -> dict:
    return {
        "binaryFormat": BINARY_FORMAT,
        "datafile": datafile,
        "heightMapItemSize": 2,
        "heightMapSize": size * size,
        "heightmapImage": heightmap_image,
        "layerMapItemSize": 1,
        "layerMapSize": size * size,
        "materials": list(materials),
        "size": size,
        "version": VERSION,
    }


def write(directory: Path, stem: str, level_path: str, heights_u16: np.ndarray, layers: np.ndarray,
          materials: Sequence[str]) -> None:
    """``<stem>.ter`` and ``<stem>.terrain.json`` in ``directory``; ``level_path`` is the in game folder
    (``/levels/<name>``)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.ter").write_bytes(encode(heights_u16, layers, materials))
    meta = terrain_json(heights_u16.shape[0], materials, f"{level_path}/{stem}.ter",
                        f"{level_path}/{stem}.terrainheightmap.png")
    (directory / f"{stem}.terrain.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
