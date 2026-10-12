"""OpenSkiMap runs for Tier A trail maps.

Schema (runs.geojson, verified against the 2026-10 download, about 835 MB): a
FeatureCollection written one feature per line. LineString or Polygon geometry
with [lon, lat, elevation] positions; properties include ``type: "run"``,
``id``, ``name``, ``uses`` (downhill, nordic, snow_park, skitour, hike, sled,
...), ``difficulty`` (novice, easy, intermediate, advanced, expert, freeride,
extreme or null), ``difficultyConvention`` (north_america, europe, japan),
``grooming`` (classic, skating, classic+skating, mogul, backcountry, scooter or
null), ``lit``, ``status`` and ``skiAreas``: ski area Features whose
``properties.id`` is the OpenSkiMap ski area id (as in ski_areas.geojson).

``filter_runs`` streams the download line by line (the full file never sits in
memory), keeps downhill and terrain park runs that are not abandoned or disused
and belong to an area matched to a Tier A resort, drops elevations, simplifies
with Douglas Peucker and writes the committed extract
data/raw/openskimap/us_runs_tier_a.geojson.

North American trail sign mapping (``difficulty_us``), following the OSM
``piste:difficulty`` key that OpenSkiMap passes through
(https://wiki.openstreetmap.org/wiki/Key:piste:difficulty) and OpenSkiMap's
north_america convention (https://github.com/russellporter/openskidata-format):
novice and easy -> green, intermediate -> blue, advanced -> black, expert and
extreme -> double_black, freeride -> null (off piste, no sign colour), any run
whose uses include snow_park -> terrain_park. Other conventions give null.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .geo import LocalProjection
from .openskimap import fetch as _fetch_url
from .schemas.common import Source

URL = "https://tiles.openskimap.org/geojson/runs.geojson"
FULL_NAME = "runs.full.geojson"
EXTRACT_NAME = "us_runs_tier_a.geojson"
SOURCE_ID = "openskimap-runs"
SOURCE_URL = "https://openskimap.org/"
SOURCE_TITLE = "OpenSkiMap runs (derived from OpenStreetMap, © OpenStreetMap contributors, ODbL)"
KEEP_USES = {"downhill", "snow_park"}
SKIP_STATUS = {"abandoned", "disused", "proposed", "planned", "construction"}
US_SIGNS = {"novice": "green", "easy": "green", "intermediate": "blue", "advanced": "black",
            "expert": "double_black", "extreme": "double_black", "freeride": None}


def fetch(dest: Path) -> Path:
    return _fetch_url(dest, URL, timeout=900)


def source(fetched: dt.date) -> Source:
    return Source(id=SOURCE_ID, url=SOURCE_URL, title=SOURCE_TITLE, accessed_date=fetched)


def difficulty_us(difficulty: Optional[str], convention: Optional[str], uses: Iterable[str]) -> Optional[str]:
    if "snow_park" in set(uses or []):
        return "terrain_park"
    if convention != "north_america":
        return None
    return US_SIGNS.get(difficulty or "")


# ------------------------------------------------------------ simplification
def douglas_peucker(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Iterative Douglas Peucker on planar points (keeps first and last)."""
    if len(points) <= 2 or tolerance <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        seg = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if seg == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg))
                d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d > best:
                best, idx = d, i
        if best > tolerance:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def _dedupe(coords: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for c in coords:
        if not out or out[-1] != c:
            out.append(c)
    return out


def simplify_line(coords: list, tolerance_m: float, decimals: int = 5) -> Optional[list[list[float]]]:
    """[[lon, lat, (ele)], ...] -> simplified 2D line rounded to ``decimals``; None when degenerate."""
    pts = [(float(c[0]), float(c[1])) for c in coords]
    if len(pts) < 2:
        return None
    proj = LocalProjection.around(pts)
    xy = [proj.to_xy(*p) for p in pts]
    kept = douglas_peucker(xy, tolerance_m / 1000.0)
    out = _dedupe([[round(lon, decimals), round(lat, decimals)] for lon, lat in (proj.to_lonlat(x, y) for x, y in kept)])
    return out if len(out) >= 2 else None


def simplify_ring(coords: list, tolerance_m: float, decimals: int = 5) -> Optional[list[list[float]]]:
    """Closed ring, simplified; None when fewer than 4 positions remain."""
    line = simplify_line(coords, tolerance_m, decimals)
    if line is None:
        return None
    if line[0] != line[-1]:
        line.append(line[0])
    return line if len(line) >= 4 else None


def simplify_geometry(geom: dict, tolerance_m: float, decimals: int = 5) -> Optional[dict]:
    t = geom.get("type")
    if t == "LineString":
        line = simplify_line(geom["coordinates"], tolerance_m, decimals)
        return {"type": "LineString", "coordinates": line} if line else None
    if t == "MultiLineString":
        lines = [l for l in (simplify_line(c, tolerance_m, decimals) for c in geom["coordinates"]) if l]
        return {"type": "MultiLineString", "coordinates": lines} if lines else None
    if t in ("Polygon", "MultiPolygon"):
        polys = [geom["coordinates"]] if t == "Polygon" else geom["coordinates"]
        out = []
        for rings in polys:
            outer = simplify_ring(rings[0], tolerance_m, decimals) if rings else None
            if outer:
                out.append([outer] + [r for r in (simplify_ring(h, tolerance_m, decimals) for h in rings[1:]) if r])
        if not out:
            return None
        return {"type": "Polygon", "coordinates": out[0]} if len(out) == 1 else {"type": "MultiPolygon", "coordinates": out}
    return None


# ---------------------------------------------------------------- filtering
def iter_features(path: Path) -> Iterator[dict]:
    """Stream features from a one feature per line GeoJSON file without loading it whole.

    Lines that cannot be a kept run are skipped before JSON parsing (fast path).
    """
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith('{"type":"Feature"') and not line.startswith('{"type": "Feature"'):
                continue
            if '"skiAreas":[]' in line or not any(f'"{u}"' in line for u in KEEP_USES):
                continue
            yield json.loads(line.rstrip(","))


def _ski_area_ids(props: dict) -> list[str]:
    return [sid for sa in props.get("skiAreas") or [] if isinstance(sa, dict)
            if isinstance(sid := (sa.get("properties") or {}).get("id"), str)]


def keep_run(props: dict, wanted: set[str]) -> list[str]:
    """Matched ski area ids when the run is kept, else []."""
    if props.get("type", "run") != "run" or props.get("status") in SKIP_STATUS:
        return []
    if not KEEP_USES & set(props.get("uses") or []):
        return []
    return sorted(set(_ski_area_ids(props)) & wanted)


def filter_runs(features: Iterable[dict], area_ids: Iterable[str], fetched: dt.date, tolerance_m: float,
                decimals: int = 5) -> dict:
    wanted = set(area_ids)
    out = []
    for f in features:
        p = f.get("properties") or {}
        areas = keep_run(p, wanted)
        if not areas:
            continue
        geom = simplify_geometry(f.get("geometry") or {}, tolerance_m, decimals)
        if geom is None:
            continue
        out.append({"type": "Feature", "geometry": geom, "properties": {
            "id": p.get("id"), "name": p.get("name"), "difficulty": p.get("difficulty"),
            "difficulty_convention": p.get("difficultyConvention"), "uses": sorted(set(p.get("uses") or []) & KEEP_USES),
            "grooming": p.get("grooming"), "lit": p.get("lit"), "ski_area_ids": areas}})
    out.sort(key=lambda f: (f["properties"]["ski_area_ids"][0], str(f["properties"]["name"]), str(f["properties"]["id"])))
    return {"type": "FeatureCollection",
            "metadata": {"source_url": URL, "fetched_date": fetched.isoformat(), "area_ids": sorted(wanted),
                         "simplify_tolerance_m": tolerance_m,
                         "filter": "uses include downhill or snow_park, not abandoned or disused, "
                                   "skiAreas include an area matched to a Tier A resort"},
            "features": out}


def refresh_extract(full: Path, extract: Path, area_ids: Iterable[str], tolerance_m: float) -> Optional[int]:
    """Rewrite the extract from the cached download when it is newer, or when the matched areas or the
    tolerance changed for the same download. Returns the run count when written."""
    ids = sorted(set(area_ids))
    if not full.exists():
        return None
    full_date = dt.date.fromtimestamp(full.stat().st_mtime)
    if extract.exists():
        try:
            meta = json.loads(extract.read_text(encoding="utf-8")).get("metadata", {})
            ex_date = dt.date.fromisoformat(meta["fetched_date"])
        except (ValueError, KeyError):
            meta, ex_date = {}, None
        if ex_date and ex_date > full_date:
            return None  # a newer committed extract wins over an older local download
        if ex_date == full_date and meta.get("area_ids") == ids and meta.get("simplify_tolerance_m") == tolerance_m:
            return None
    data = filter_runs(iter_features(full), ids, full_date, tolerance_m)
    extract.parent.mkdir(parents=True, exist_ok=True)
    extract.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    return len(data["features"])


def load_extract(path: Path) -> tuple[Optional[dt.date], list[dict]]:
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    return dt.date.fromisoformat(data["metadata"]["fetched_date"]), data.get("features", [])


# ----------------------------------------------------------------- assembly
def _extend_bounds(b: list[float], coords: Any) -> None:
    if coords and isinstance(coords[0], (int, float)):
        b[0], b[1] = min(b[0], coords[0]), min(b[1], coords[1])
        b[2], b[3] = max(b[2], coords[0]), max(b[3], coords[1])
    else:
        for c in coords:
            _extend_bounds(b, c)


def trailmap(resort_id: str, run_features: Iterable[dict], area_ids: Iterable[str], lifts_fc: dict,
             header: dict) -> Optional[dict]:
    """data/build/trailmap/<resort_id>.json, or None when the resort has no runs."""
    wanted = set(area_ids)
    runs = []
    for f in run_features:
        p = f["properties"]
        if not wanted & set(p.get("ski_area_ids") or []):
            continue
        props = {"name": p.get("name"), "difficulty": p.get("difficulty"),
                 "difficulty_us": difficulty_us(p.get("difficulty"), p.get("difficulty_convention"), p.get("uses") or [])}
        # grooming and lit are mostly null in OpenStreetMap; they are omitted when null to keep files small
        props.update({k: p[k] for k in ("grooming", "lit") if p.get(k) is not None})
        runs.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
    if not runs:
        return None
    b = [math.inf, math.inf, -math.inf, -math.inf]
    for f in runs + list(lifts_fc.get("features") or []):
        _extend_bounds(b, f["geometry"]["coordinates"])
    return {**header, "resort_id": resort_id, "source_id": SOURCE_ID,
            "runs": {"type": "FeatureCollection", "features": runs}, "lifts": lifts_fc,
            "bounds": [round(x, 5) for x in b]}
