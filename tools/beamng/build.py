"""Build one real world level: heights, roads, runs, materials, spawn points, then the mod zip.

Commands (from the repository root)::

    python -m tools.beamng --resort palisades-tahoe
    python -m tools.beamng --lat 39.1968 --lon -120.2354 --name olympic_valley --size 2048 --square 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from pipeline.climate import http
from tools.beamng import elevation, level, paint, sources
from tools.beamng.geo import Grid, LocalFrame

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = TOOL_DIR / "out"
DEFAULT_CACHE = TOOL_DIR / "cache"
STABLE_EXTENT_M = 10_000  # BeamNG physics and rendering stay precise within about this distance of the origin
SPAWN_LIFT_HEIGHT_M = 0.5
SPAWN_ROAD_SNAP_M = 400.0


def level_name(text: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not name:
        raise ValueError(f"cannot make a level name from {text!r}")
    return name


def make_spawns(grid: Grid, h: np.ndarray, frame: LocalFrame, roads: list, base: Optional[tuple[float, float]],
                tops: list) -> list[level.Spawn]:
    """The base area (or the center), moved onto the nearest road when one is close, then the highest lift top."""
    bx, by = (float(v) for v in frame.to_xy(*base)) if base else (0.0, 0.0)
    if not grid.contains(bx, by):
        bx, by = 0.0, 0.0
    heading = (0.0, 1.0)
    near = paint.nearest_on_lines(roads, bx, by)
    if near and near[0] <= SPAWN_ROAD_SNAP_M:
        (bx, by), heading = near[1], (float(near[2][0]), float(near[2][1]))
    out = [level.Spawn("spawn_base", bx, by, float(grid.sample(h, bx, by)) + SPAWN_LIFT_HEIGHT_M, heading,
                       "Base area" if base else "Center")]
    inside = []
    for name, lon, lat, _ in tops:
        x, y = (float(v) for v in frame.to_xy(lon, lat))
        if grid.contains(x, y):
            inside.append((float(grid.sample(h, x, y)), name, x, y))
    if inside:
        z, name, x, y = max(inside)
        out.append(level.Spawn("spawn_summit", x, y, z + SPAWN_LIFT_HEIGHT_M, (0.0, 1.0), f"Top of {name}"))
    return out


def build(*, name: str, title: str, lon: float, lat: float, size: int, square_m: float, roads_mode: str,
          out_dir: Path, cache_dir: Path, resort: Optional[sources.Resort] = None, smooth_m: float = 0.0,
          get_json: Callable = http.get_json, log: Callable[[str], None] = print) -> Path:
    grid = Grid(size, square_m)
    frame = LocalFrame(lon, lat)
    if grid.extent_m > STABLE_EXTENT_M:
        log(f"note: {grid.extent_m / 1000:.1f} km a side; BeamNG is most precise within about "
            f"{STABLE_EXTENT_M / 1000:.0f} km, so expect jitter near the edges")
    ground, elev_meta = elevation.heights(frame, grid, cache_dir, smooth_m=smooth_m, log=log)

    roads, roads_meta = [], {"mode": roads_mode, "source": None, "count": 0}
    if roads_mode in ("auto", "osm"):
        try:
            roads = sources.fetch_roads(elevation.bounds(frame, grid), frame, get_json=get_json)
            roads_meta.update(source="OpenStreetMap via the Overpass API", url=sources.OVERPASS_URL,
                              license="ODbL 1.0, (c) OpenStreetMap contributors")
        except (http.NetworkBlocked, http.HttpStatusError, http.RateLimited) as e:
            if roads_mode == "osm":
                raise
            log(f"roads: OpenStreetMap unavailable ({e}); using the resort's transit routes")
    if roads_mode == "transit" or (roads_mode == "auto" and not roads and resort):
        roads = sources.transit_lines(resort, frame) if resort else []
        if roads:
            roads_meta.update(source="Resort transit routes (GTFS shapes) from data/build/detail",
                              source_ids=[s for s in resort.source_ids if s.startswith("gtfs")])
    roads_meta["count"] = len(roads)
    log(f"roads: {len(roads)} lines")

    runs = sources.run_lines(resort, frame) if resort else []
    h = paint.level_roads(grid, ground, roads)
    layer = paint.layers(grid, h, runs + roads)

    spawns = make_spawns(grid, h, frame, roads, resort.base if resort else None,
                         sources.lift_tops(resort) if resort else [])
    provenance = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "center": {"lon": lon, "lat": lat},
        "projection": "local frame on WGS84: x metres east along each parallel, y metres north along the "
                      "central meridian (tools/beamng/geo.py)",
        "elevation": elev_meta,
        "roads": roads_meta,
        "runs": {"count": len(runs), "source_ids": resort.source_ids if resort else [],
                 "license": "ODbL 1.0, OpenSkiMap.org and (c) OpenStreetMap contributors"} if runs else None,
        "resort_id": resort.id if resort else None,
        "spawns": [{"name": s.name, "title": s.title, "x": s.x, "y": s.y, "z": s.z} for s in spawns],
    }
    description = (f"{title} at real size: {grid.extent_m / 1000:.1f} km a side, one terrain step = "
                   f"{square_m:g} m, heights in metres above sea level. Elevation: AWS Terrain Tiles. "
                   + ("Roads: (c) OpenStreetMap contributors. " if roads_meta.get("url") else "")
                   + ("Ski runs: OpenSkiMap.org. " if runs else ""))
    with tempfile.TemporaryDirectory() as tmp:
        level.write_level(Path(tmp), name, title, description.strip(), grid, h, layer, spawns, provenance,
                          authors="instructorgrindset.com real world generator",
                          heightmap=out_dir / f"{name}_heightmap.png")
        zip_path = level.pack(Path(tmp), out_dir / f"{name}.zip")
    log(f"wrote {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB); copy it into your BeamNG mods folder")
    log(f"heights also saved as {out_dir / (name + '_heightmap.png')} (north up, 16 bit, "
        f"{grid.square_m:g} m per pixel) for a manual World Editor import")
    return zip_path


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build a real world BeamNG.drive level at 1:1 scale.")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--resort", help="resort id from data/build (centers on its runs, spawns at its base)")
    where.add_argument("--lat", type=float, help="center latitude (with --lon and --name)")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--name", help="level folder name (default: the resort id)")
    ap.add_argument("--title", help="title in the level list")
    ap.add_argument("--size", type=int, default=4096, help="terrain vertices a side, a power of two (256 to 8192)")
    ap.add_argument("--square", type=float, default=2.0, help="metres between terrain vertices (default 2)")
    ap.add_argument("--roads", choices=["auto", "osm", "transit", "none"], default="auto",
                    help="auto: OpenStreetMap, else the resort's transit routes")
    ap.add_argument("--smooth", type=float, default=0.0, help="blur the ground over this many metres")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args(argv)

    resort = None
    if args.resort:
        resort = sources.load_resort(args.resort)
        lon, lat = resort.center()
        name = level_name(args.name or resort.id)
        title = args.title or f"{resort.name} 1:1"
    else:
        if args.lon is None or not args.name:
            ap.error("--lat needs --lon and --name")
        lon, lat, name = args.lon, args.lat, level_name(args.name)
        title = args.title or f"{args.name} 1:1"
    if not (-85 < lat < 85 and -180 <= lon <= 180) or math.isnan(lat + lon):
        ap.error("center must be a real longitude and latitude")
    try:
        build(name=name, title=title, lon=lon, lat=lat, size=args.size, square_m=args.square,
              roads_mode=args.roads, out_dir=args.out, cache_dir=args.cache, resort=resort, smooth_m=args.smooth)
    except (http.NetworkBlocked, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0
