"""Real ground heights for the terrain grid from the AWS Terrain Tiles.

The tiles are Mapzen "terrarium" PNGs (https://registry.opendata.aws/terrain-tiles/), the same source the site's
snowfall heat map reads (``pipeline/terrain.py``): height = R x 256 + G + B / 256 - 32768 metres. In the United
States they are built from USGS 3DEP (about 10 m, 3 m in parts), elsewhere mostly from SRTM (about 30 m). Zoom 15,
the finest, has pixels of 4.8 m x cos(latitude).

Each grid vertex takes the bilinear height of the tile pixels around its longitude and latitude, so the terrain
keeps real horizontal distances and real heights. Heights under sea level stay as they are. Tiles are cached on
disk by ``pipeline.terrain.get_tile``.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np

from pipeline import png, terrain
from tools.beamng.geo import Grid, LocalFrame

TILE_PX = 256
MAX_ZOOM = 15
EQUATOR_M_PER_PX = 2 * math.pi * 6378137.0 / TILE_PX  # web mercator pixel size at zoom 0 on the equator
MAX_TILES = 3000
ROW_CHUNK = 256


def zoom_for(square_m: float, lat: float) -> int:
    """The coarsest zoom whose pixels are no larger than one grid step (capped at the finest tiles)."""
    z = math.ceil(math.log2(EQUATOR_M_PER_PX * math.cos(math.radians(lat)) / square_m))
    return max(1, min(MAX_ZOOM, z))


def tile_heights(data: bytes) -> np.ndarray:
    """Heights (m) of one terrarium tile, rows from the north edge."""
    w, h, ch, depth, px = png.decode(data)
    if ch not in (3, 4) or depth != 8:
        raise ValueError(f"unexpected terrain tile: {w}x{h}, {ch} channels, {depth} bit")
    rgb = np.frombuffer(px, dtype=np.uint8).reshape(h, w, ch).astype(np.float64)
    return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768


def _tile_x(lon, z):
    return (np.asarray(lon) + 180.0) / 360.0 * (1 << z)


def _tile_y(lat, z):
    r = np.radians(np.asarray(lat))
    return (1 - np.log(np.tan(r) + 1 / np.cos(r)) / math.pi) / 2 * (1 << z)


def bounds(frame: LocalFrame, grid: Grid) -> tuple[float, float, float, float]:
    """(west, south, east, north) degrees that hold every vertex."""
    lo, hi = grid.origin, grid.origin + (grid.size - 1) * grid.square_m
    xs = np.array([lo, hi, lo, hi, 0.0, 0.0])
    ys = np.array([lo, lo, hi, hi, lo, hi])
    lon, lat = frame.to_lonlat(xs, ys)
    return float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())


def mosaic(west: float, south: float, east: float, north: float, z: int,
           get_tile: Callable[[int, int, int], bytes]) -> tuple[np.ndarray, int, int]:
    """(heights, first tile x, first tile y): the tiles covering the box joined into one array."""
    keys = terrain.tiles_for_box(west, south, east, north, z)
    if len(keys) > MAX_TILES:
        raise ValueError(f"{len(keys)} tiles at zoom {z}; pick a smaller area or a larger square size")
    xs = sorted({x for x, _ in keys})
    ys = sorted({y for _, y in keys})
    out = np.zeros((len(ys) * TILE_PX, len(xs) * TILE_PX))
    for x, y in keys:
        tile = tile_heights(get_tile(z, x, y))
        r, c = (y - ys[0]) * TILE_PX, (x - xs[0]) * TILE_PX
        out[r:r + TILE_PX, c:c + TILE_PX] = tile
    return out, xs[0], ys[0]


def _bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = img.shape
    u = np.clip(u, 0, w - 1)
    v = np.clip(v, 0, h - 1)
    u0 = np.minimum(np.floor(u).astype(int), w - 2)
    v0 = np.minimum(np.floor(v).astype(int), h - 2)
    tu, tv = u - u0, v - v0
    top = img[v0, u0] * (1 - tu) + img[v0, u0 + 1] * tu
    bot = img[v0 + 1, u0] * (1 - tu) + img[v0 + 1, u0 + 1] * tu
    return top * (1 - tv) + bot * tv


def sample_grid(frame: LocalFrame, grid: Grid, img: np.ndarray, tx0: int, ty0: int, z: int) -> np.ndarray:
    """Height of every vertex, ``[j, i]`` with row 0 at the south edge."""
    axis = grid.axis()
    out = np.empty((grid.size, grid.size))
    lat_rows = frame.to_lonlat(np.zeros_like(axis), axis)[1]
    for start in range(0, grid.size, ROW_CHUNK):
        lat = lat_rows[start:start + ROW_CHUNK, None]
        lon = frame.lon0 + axis[None, :] / frame.m_per_deg_lon(lat)
        u = (_tile_x(lon, z) - tx0) * TILE_PX - 0.5
        v = np.broadcast_to((_tile_y(lat, z) - ty0) * TILE_PX - 0.5, u.shape)
        out[start:start + ROW_CHUNK] = _bilinear(img, u, v)
    return out


def box_blur(a: np.ndarray, radius: int, passes: int = 2) -> np.ndarray:
    """Separable box mean over (2 radius + 1) vertices each way, shrinking at the edges; ``passes`` rounds make it
    close to a Gaussian. Radius 0 returns the array unchanged."""
    if radius <= 0 or passes <= 0:
        return a
    out = np.asarray(a, dtype=float)
    for _ in range(passes):
        for axis in (0, 1):
            n = out.shape[axis]
            c = np.cumsum(out, axis=axis)
            c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
            k = np.arange(n)
            hi, lo = np.minimum(n, k + radius + 1), np.maximum(0, k - radius)
            shape = [1, 1]
            shape[axis] = n
            out = (np.take(c, hi, axis=axis) - np.take(c, lo, axis=axis)) / (hi - lo).reshape(shape)
    return out


def heights(frame: LocalFrame, grid: Grid, cache_dir: Path, *, smooth_m: float = 0.0,
            log: Callable[[str], None] = print) -> tuple[np.ndarray, dict]:
    """(vertex heights in metres, provenance) for the grid around the frame origin."""
    z = zoom_for(grid.square_m, frame.lat0)
    west, south, east, north = bounds(frame, grid)
    keys = terrain.tiles_for_box(west, south, east, north, z)
    log(f"elevation: {len(keys)} terrain tiles at zoom {z}")
    img, tx0, ty0 = mosaic(west, south, east, north, z, lambda tz, x, y: terrain.get_tile(cache_dir, tz, x, y))
    h = sample_grid(frame, grid, img, tx0, ty0, z)
    h = box_blur(h, round(smooth_m / grid.square_m))
    px_m = EQUATOR_M_PER_PX * math.cos(math.radians(frame.lat0)) / (1 << z)
    meta = {"source_id": terrain.SOURCE_ID, "url": "https://registry.opendata.aws/terrain-tiles/",
            "tile_url": terrain.TILE_URL, "zoom": z, "tiles": len(keys), "tile_pixel_m": round(px_m, 2),
            "smooth_m": smooth_m, "bounds": [west, south, east, north],
            "attribution": "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"}
    return h, meta
