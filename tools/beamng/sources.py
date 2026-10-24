"""Lines and places for the level: OpenStreetMap roads, and for a resort its base area, runs, lifts and transit
routes from ``data/build``.

Roads come from the Overpass API (``OVERPASS_URL``), ways with a ``highway`` value in ``ROAD_WIDTH_M``. The widths
are drawing widths for a class of road (assumptions of this tool, not measurements); a way's own ``width`` tag
wins when it is a plain number of metres. Ways tagged ``bridge`` or ``tunnel`` are dropped, since the terrain
cannot carry them, and ``tracks`` are drawn as dirt.

When Overpass cannot be reached, ``--roads auto`` falls back to the resort's transit routes (GTFS shapes in
``data/build/detail/<id>.json``), which follow public roads.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from pipeline.climate import http
from tools.beamng.geo import LocalFrame
from tools.beamng.paint import ASPHALT, DIRT, RUN_WIDTH_M, SKI_RUN, Line

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "data" / "build"

ROAD_WIDTH_M = {
    "motorway": 11.0, "trunk": 9.0, "primary": 8.0, "secondary": 7.5, "tertiary": 7.0,
    "motorway_link": 5.0, "trunk_link": 5.0, "primary_link": 5.0, "secondary_link": 5.0, "tertiary_link": 5.0,
    "unclassified": 6.0, "residential": 6.0, "living_street": 5.0, "service": 4.0, "track": 3.5,
}
DIRT_CLASSES = {"track"}
TRANSIT_WIDTH_M = 7.0


def overpass_query(west: float, south: float, east: float, north: float, timeout_s: int = 180) -> str:
    classes = "|".join(sorted(ROAD_WIDTH_M))
    return (f'[out:json][timeout:{timeout_s}];'
            f'way["highway"~"^({classes})$"]({south},{west},{north},{east});'
            f'out geom;')


def _width(tags: dict, kind: str) -> float:
    raw = str(tags.get("width", "")).strip().removesuffix(" m").removesuffix("m").strip()
    try:
        w = float(raw)
        if 1.0 <= w <= 40.0:
            return w
    except ValueError:
        pass
    return ROAD_WIDTH_M[kind]


def roads_from_overpass(doc: dict, frame: LocalFrame) -> list[Line]:
    """Road lines from an Overpass ``out geom`` answer."""
    out = []
    for el in doc.get("elements", []):
        tags = el.get("tags", {})
        kind = tags.get("highway")
        geom = el.get("geometry") or []
        if el.get("type") != "way" or kind not in ROAD_WIDTH_M or len(geom) < 2:
            continue
        if tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no":
            continue
        x, y = frame.to_xy([g["lon"] for g in geom], [g["lat"] for g in geom])
        out.append(Line(kind, np.column_stack([x, y]), _width(tags, kind),
                        DIRT if kind in DIRT_CLASSES else ASPHALT, name=tags.get("name"),
                        tags={"osm_way": el.get("id")}))
    return out


def fetch_roads(bounds: tuple[float, float, float, float], frame: LocalFrame,
                get_json: Callable = http.get_json) -> list[Line]:
    """OpenStreetMap roads in ``bounds`` (west, south, east, north). Raises ``http.NetworkBlocked`` when Overpass
    cannot be reached."""
    doc = get_json(OVERPASS_URL, {"data": overpass_query(*bounds)}, timeout=240.0, retries=2)
    return roads_from_overpass(doc, frame)


# ------------------------------------------------------------------ resort data (data/build)
@dataclass(frozen=True)
class Resort:
    id: str
    name: str
    base: Optional[tuple[float, float]]        # (lon, lat) of the base area
    run_bounds: Optional[tuple[float, float, float, float]]
    runs: list                                 # GeoJSON features
    lifts: list
    transit: list                              # GeoJSON geometries
    source_ids: list

    def center(self) -> tuple[float, float]:
        """Middle of the mapped runs, else the base area."""
        if self.run_bounds:
            w, s, e, n = self.run_bounds
            return (w + e) / 2, (s + n) / 2
        if self.base:
            return self.base
        raise ValueError(f"{self.id}: no base area and no mapped runs to center the level on")


def _read(path: Path) -> Optional[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_resort(resort_id: str, build_dir: Path = BUILD_DIR) -> Resort:
    index = _read(build_dir / "resorts.json") or {}
    record = next((r for r in index.get("resorts", []) if r.get("id") == resort_id), None)
    detail = _read(build_dir / "detail" / f"{resort_id}.json")
    trail = _read(build_dir / "trailmap" / f"{resort_id}.json")
    if record is None and detail is None and trail is None:
        raise ValueError(f"no resort {resort_id!r} in {build_dir}")
    base = None
    sources = []
    if detail and detail.get("base_area"):
        b = detail["base_area"]
        base = (b["lon"], b["lat"])
        sources.append(b.get("source_id"))
    transit = []
    if detail and detail.get("transit"):
        transit = [r["geometry"] for r in detail["transit"].get("routes", []) if r.get("geometry")]
        sources += detail["transit"].get("source_ids", [])
    if trail:
        sources.append(trail.get("source_id"))
    return Resort(
        id=resort_id,
        name=(record or {}).get("name") or resort_id.replace("-", " ").title(),
        base=base,
        run_bounds=tuple(trail["bounds"]) if trail and trail.get("bounds") else None,
        runs=trail["runs"]["features"] if trail else [],
        lifts=trail["lifts"]["features"] if trail else [],
        transit=transit,
        source_ids=[s for s in dict.fromkeys(sources) if s],
    )


def _parts(geometry: dict) -> list:
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def run_lines(resort: Resort, frame: LocalFrame) -> list[Line]:
    out = []
    for f in resort.runs:
        for coords in _parts(f["geometry"]):
            x, y = frame.to_xy([c[0] for c in coords], [c[1] for c in coords])
            out.append(Line("run", np.column_stack([x, y]), RUN_WIDTH_M, SKI_RUN, level=False,
                            name=f.get("properties", {}).get("name")))
    return out


def transit_lines(resort: Resort, frame: LocalFrame) -> list[Line]:
    out = []
    for geom in resort.transit:
        for coords in _parts(geom):
            x, y = frame.to_xy([c[0] for c in coords], [c[1] for c in coords])
            out.append(Line("transit", np.column_stack([x, y]), TRANSIT_WIDTH_M, ASPHALT))
    return out


def lift_tops(resort: Resort) -> list[tuple[str, float, float, Optional[float]]]:
    """(lift name, lon, lat, elevation) of the upper end of each lift (the end with the higher mapped elevation,
    else the last point)."""
    out = []
    for f in resort.lifts:
        for coords in _parts(f["geometry"]):
            a, b = coords[0], coords[-1]
            top = a if len(a) > 2 and len(b) > 2 and a[2] > b[2] else b
            out.append((f.get("properties", {}).get("name") or "lift", top[0], top[1],
                        top[2] if len(top) > 2 else None))
    return out
