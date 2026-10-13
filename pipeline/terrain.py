"""Terrain heights for the snowfall heat map (docs/map/ARCHITECTURE.md, "Heat maps").

The snowfall field follows elevation between stations (``heat.snowfall.elevation`` in config/definitions.yaml),
so the build needs the ground height of every raster pixel. It comes from the AWS Terrain Tiles (Mapzen
"terrarium" PNG tiles, AWS Open Data registry, https://registry.opendata.aws/terrain-tiles/): each pixel stores
height = R x 256 + G + B / 256 - 32768 metres. In the United States the tiles are built from USGS 3DEP (formerly
NED), SRTM and GMTED2010, and NOAA ETOPO1 (attribution: https://github.com/tilezen/joerd/blob/master/docs/attribution.md).

The fetcher reads the tiles at zoom ``ZOOM`` over each heat region box and averages them into cells of
``STEP_DEG`` degrees (a regular longitude and latitude grid, cell centers at half steps from the box's west and
north edges). Heights under sea level (the sea floor, Death Valley) count as 0. The committed extract is one 16 bit greyscale PNG per region, ``terrain-<region>.png`` in
``data/raw/terrain/`` (value = metres + ``OFFSET_M``, clamped to 0..65535), plus ``source.json``. The tile
download stays in ``data/raw/terrain/download/`` (gitignored).

Commands::

    python -m pipeline.terrain fetch      # download the tiles, write the extract
    python -m pipeline.terrain summary
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from pipeline import png
from pipeline.climate import http

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
SOURCE_ID = "aws-terrain-tiles"
ZOOM = 6
STEP_DEG = 1 / 16
OFFSET_M = 0             # stored value = metres + OFFSET_M (heights under sea level are stored as 0)
SOURCE_NAME = "source.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "data" / "raw" / "terrain"
PAUSE_S = 0.1


def file_name(region: str) -> str:
    return f"terrain-{region}.png"


@dataclass(frozen=True)
class Dem:
    """Mean ground height (m) on a regular grid: ``width`` columns from ``west``, ``height`` rows from ``north``,
    each ``step`` degrees, values row major (top row first)."""

    west: float
    north: float
    step: float
    width: int
    height: int
    values: Sequence[float]

    def sample(self, lon: float, lat: float) -> Optional[float]:
        """Bilinear height at a point (cell centers at half steps); None outside the grid."""
        x = (lon - self.west) / self.step - 0.5
        y = (self.north - lat) / self.step - 0.5
        if x < -0.5 or y < -0.5 or x > self.width - 0.5 or y > self.height - 0.5:
            return None
        x = min(max(x, 0.0), self.width - 1.0)
        y = min(max(y, 0.0), self.height - 1.0)
        i0, j0 = min(int(x), self.width - 2) if self.width > 1 else 0, min(int(y), self.height - 2) if self.height > 1 else 0
        tx, ty = x - i0, y - j0
        w = self.width
        i1, j1 = min(i0 + 1, self.width - 1), min(j0 + 1, self.height - 1)
        v = self.values
        top = v[j0 * w + i0] * (1 - tx) + v[j0 * w + i1] * tx
        bot = v[j1 * w + i0] * (1 - tx) + v[j1 * w + i1] * tx
        return top * (1 - ty) + bot * ty


# ------------------------------------------------------------------ tiles
def tile_x(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * (1 << z)


def tile_y(lat: float, z: int) -> float:
    r = math.radians(lat)
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * (1 << z)


def lat_of_tile_y(y: float, z: int) -> float:
    n = math.pi - 2 * math.pi * y / (1 << z)
    return math.degrees(math.atan(math.sinh(n)))


def tiles_for_box(west: float, south: float, east: float, north: float, z: int) -> list[tuple[int, int]]:
    x0, x1 = math.floor(tile_x(west, z)), min((1 << z) - 1, math.floor(tile_x(east, z) - 1e-9))
    y0, y1 = math.floor(tile_y(north, z)), math.floor(tile_y(south, z) - 1e-9)
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def terrarium_heights(rgb: bytes, n: int) -> list[float]:
    """Heights (m) of ``n`` terrarium pixels (RGB bytes), the sea floor raised to 0 (sea level)."""
    return [max(0.0, rgb[3 * k] * 256 + rgb[3 * k + 1] + rgb[3 * k + 2] / 256 - 32768) for k in range(n)]


def tile_path(directory: Path, z: int, x: int, y: int) -> Path:
    return directory / "download" / str(z) / str(x) / f"{y}.png"


def get_tile(directory: Path, z: int, x: int, y: int, sleep: Callable[[float], None] = time.sleep) -> bytes:
    path = tile_path(directory, z, x, y)
    if path.is_file():
        return path.read_bytes()
    data = http.get_bytes(TILE_URL.format(z=z, x=x, y=y), accept="image/png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    sleep(PAUSE_S)
    return data


def grid_size(west: float, south: float, east: float, north: float, step: float) -> tuple[int, int]:
    return max(1, round((east - west) / step)), max(1, round((north - south) / step))


def average_into_grid(box: Any, step: float, z: int, tiles: Mapping[tuple[int, int], Sequence[float]],
                      tile_px: int = 256) -> tuple[int, int, list[Optional[float]]]:
    """Mean height of the tile pixels whose centers fall in each grid cell (None for a cell with none)."""
    width, height = grid_size(box.west, box.south, box.east, box.north, step)
    sums = [0.0] * (width * height)
    counts = [0] * (width * height)
    n = 1 << z
    for (tx, ty), heights in tiles.items():
        for py in range(tile_px):
            lat = lat_of_tile_y(ty + (py + 0.5) / tile_px, z)
            j = math.floor((box.north - lat) / step)
            if not 0 <= j < height:
                continue
            row = py * tile_px
            for px in range(tile_px):
                lon = (tx + (px + 0.5) / tile_px) / n * 360.0 - 180.0
                i = math.floor((lon - box.west) / step)
                if 0 <= i < width:
                    sums[j * width + i] += heights[row + px]
                    counts[j * width + i] += 1
    return width, height, [s / c if c else None for s, c in zip(sums, counts)]


def fill_gaps(width: int, height: int, values: list[Optional[float]]) -> list[float]:
    """Cells without a tile pixel take the mean of their filled neighbours (repeated until none is left)."""
    vals = list(values)
    for _ in range(64):
        missing = [k for k, v in enumerate(vals) if v is None]
        if not missing:
            break
        upd = {}
        for k in missing:
            i, j = k % width, k // width
            nb = [vals[q] for q in (k - 1 if i > 0 else None, k + 1 if i < width - 1 else None,
                                    k - width if j > 0 else None, k + width if j < height - 1 else None)
                  if q is not None and vals[q] is not None]
            if nb:
                upd[k] = sum(nb) / len(nb)
        if not upd:
            break
        for k, v in upd.items():
            vals[k] = v
    return [0.0 if v is None else v for v in vals]


def encode(width: int, height: int, values: Sequence[float]) -> bytes:
    return png.encode_gray16(width, height, [max(0, min(65535, round(v) + OFFSET_M)) for v in values])


def decode(data: bytes) -> tuple[int, int, list[float]]:
    w, h, vals = png.decode_gray16(data)
    return w, h, [float(v - OFFSET_M) for v in vals]


# ------------------------------------------------------------------ extract
def fetch(regions: Mapping[str, Any], directory: Path = DEFAULT_DIR, *, today: Optional[dt.date] = None,
          log: Callable[[str], None] = print) -> dict:
    today = today or dt.date.today()
    files = {}
    for name, box in regions.items():
        keys = tiles_for_box(box.west, box.south, box.east, box.north, ZOOM)
        tiles = {}
        for n, (x, y) in enumerate(keys, 1):
            w, h, ch, depth, px = png.decode(get_tile(directory, ZOOM, x, y))
            if (w, h, ch, depth) != (256, 256, 3, 8):
                raise ValueError(f"unexpected terrain tile {ZOOM}/{x}/{y}: {w}x{h}, {ch} channels, {depth} bit")
            tiles[(x, y)] = terrarium_heights(px, w * h)
            if n % 20 == 0:
                log(f"{name}: {n} of {len(keys)} tiles")
        width, height, cells = average_into_grid(box, STEP_DEG, ZOOM, tiles)
        vals = fill_gaps(width, height, cells)
        data = encode(width, height, vals)
        (directory / file_name(name)).write_bytes(data)
        files[name] = {"file": file_name(name), "west": box.west, "north": box.north, "step_deg": STEP_DEG,
                       "width": width, "height": height, "tiles": len(keys), "bytes": len(data)}
    source = {
        "source": {
            "id": SOURCE_ID,
            "url": "https://registry.opendata.aws/terrain-tiles/",
            "title": "Terrain Tiles on AWS (Mapzen terrarium elevation tiles)",
            "accessed_date": today.isoformat(),
            "note": (f"Mean ground height per {STEP_DEG:g} degree cell from the terrarium tiles at zoom {ZOOM} "
                     f"({TILE_URL}). In the United States the tiles are built from USGS 3DEP (formerly NED), "
                     "SRTM and GMTED2010 (courtesy of the U.S. Geological Survey) and NOAA ETOPO1, all public "
                     "domain; ArcticDEM data were created from DigitalGlobe, Inc., imagery and funded under "
                     "National Science Foundation awards 1043681, 1559691 and 1542736. Used only to shape the "
                     "snowfall heat map between stations; no height is displayed."),
        },
        "confidence": "published",
        "license": "public domain (US sources), attribution in the note",
        "units": "m",
        "encoding": f"16 bit greyscale PNG, value = metres + {OFFSET_M}; rows from the north edge, columns from the "
                    "west edge, cell centers at half steps",
        "zoom": ZOOM,
        "regions": files,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SOURCE_NAME).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return source


def load(directory: Path = DEFAULT_DIR) -> tuple[dict[str, Dem], dict]:
    """({region: Dem}, source record) from the committed extract. Raises FileNotFoundError when absent."""
    source = json.loads((directory / SOURCE_NAME).read_text(encoding="utf-8"))
    out = {}
    for name, meta in source["regions"].items():
        w, h, vals = decode((directory / meta["file"]).read_bytes())
        if (w, h) != (meta["width"], meta["height"]):
            raise ValueError(f"{meta['file']}: {w}x{h} differs from source.json {meta['width']}x{meta['height']}")
        out[name] = Dem(meta["west"], meta["north"], meta["step_deg"], w, h, vals)
    return out, source


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["fetch", "summary"])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args(argv)
    if args.command == "fetch":
        from pipeline.config import load_config  # noqa: PLC0415
        heat = load_config(REPO_ROOT / "config").definitions.heat
        try:
            src = fetch({"lower48": heat.regions.lower48, "alaska": heat.regions.alaska}, args.dir)
        except http.NetworkBlocked as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(json.dumps(src["regions"]))
        return 0
    dems, source = load(args.dir)
    for name, d in dems.items():
        print(f"{name}: {d.width} x {d.height}, {min(d.values):.0f} to {max(d.values):.0f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
