"""OpenSkiMap ski areas: fetch, filter to US operating downhill areas, load.

Schema (openskidata-format, ski_areas.geojson): a FeatureCollection whose
features have Point, Polygon or MultiPolygon geometry and properties::

    type: "skiArea"
    id: str
    name: str | null
    activities: ["downhill", "nordic", ...]
    status: "operating" | "disused" | "abandoned" | "proposed" | "planned" | ... | null
    websites: [str]
    location: { iso3166_1Alpha2: "US", iso3166_2: "US-CO",
                localized: { en: { country, region, locality } } } | null
    statistics: { minElevation?, maxElevation?,
                  lifts: { byType: { chair_lift: { count, ... }, ... } },
                  runs: { byActivity: { downhill: { byDifficulty: { easy: { count, ... } } } } } }

``filter_us`` keeps type == skiArea, "downhill" in activities, status ==
operating, US country code and a named area with a state code, and writes a
compact extract (data/raw/openskimap/us_ski_areas.geojson, committed).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .geo import representative_point
from .schemas.common import Source

URL = "https://tiles.openskimap.org/geojson/ski_areas.geojson"
SOURCE_ID = "openskimap-ski-areas"
SOURCE_URL = "https://openskimap.org/"
SOURCE_TITLE = "OpenSkiMap ski areas (derived from OpenStreetMap, ODbL)"
M_TO_FT = 3.280839895

RAW_DIR = Path("data/raw/openskimap")
FULL_NAME = "ski_areas.full.geojson"
US_NAME = "us_ski_areas.geojson"


def fetch(dest: Path, url: str = URL, timeout: float = 300) -> Path:
    """Download the full OpenSkiMap ski areas file to ``dest`` (gitignored)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "instructorgrindset-career-map/1"})
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(resp, out)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dest


def _round_coords(c: Any, nd: int) -> Any:
    if isinstance(c, (int, float)):
        return round(float(c), nd)
    return [_round_coords(x, nd) for x in c]


def _state_code(location: dict | None) -> Optional[str]:
    if not location:
        return None
    code = location.get("iso3166_2") or ""
    if isinstance(code, str) and code.startswith("US-") and len(code) == 5:
        return code[3:].upper()
    return None


def _count_by(stats: dict | None, *path: str) -> Optional[int]:
    node: Any = stats or {}
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if not isinstance(node, dict):
        return None
    total = 0
    found = False
    for entry in node.values():
        if isinstance(entry, dict) and isinstance(entry.get("count"), (int, float)):
            total += int(entry["count"])
            found = True
    return total if found else None


def _elevations(stats: dict | None) -> tuple[Optional[float], Optional[float]]:
    stats = stats or {}
    lo, hi = stats.get("minElevation"), stats.get("maxElevation")
    if lo is None or hi is None:
        runs = stats.get("runs") or {}
        lo, hi = runs.get("minElevation", lo), runs.get("maxElevation", hi)
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi >= lo:
        return float(lo), float(hi)
    return None, None


def keep_feature(props: dict) -> bool:
    """True for a named, operating, downhill US ski area with a state code."""
    if props.get("type") != "skiArea":
        return False
    if "downhill" not in (props.get("activities") or []):
        return False
    if props.get("status") != "operating":
        return False
    loc = props.get("location") or {}
    if loc.get("iso3166_1Alpha2") != "US":
        return False
    if not _state_code(loc):
        return False
    return bool((props.get("name") or "").strip())


def filter_us(collection: dict, fetched_date: dt.date, decimals: int = 5) -> dict:
    """Reduce a full ski_areas FeatureCollection to the committed US extract."""
    out = []
    for feat in collection.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom or not keep_feature(props):
            continue
        stats = props.get("statistics") or {}
        lo, hi = _elevations(stats)
        out.append({
            "type": "Feature",
            "properties": {
                "id": props.get("id"),
                "name": props["name"].strip(),
                "state": _state_code(props.get("location")),
                "locality": (((props.get("location") or {}).get("localized") or {}).get("en") or {}).get("locality"),
                "websites": list(props.get("websites") or []),
                "lift_count": _count_by(stats, "lifts", "byType"),
                "downhill_run_count": _count_by(stats, "runs", "byActivity", "downhill", "byDifficulty"),
                "min_elevation_m": lo,
                "max_elevation_m": hi,
            },
            "geometry": {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"], decimals)},
        })
    out.sort(key=lambda f: (f["properties"]["state"], f["properties"]["name"].lower(), str(f["properties"]["id"])))
    return {
        "type": "FeatureCollection",
        "metadata": {"source_url": URL, "fetched_date": fetched_date.isoformat(), "filter":
                     "type == skiArea, downhill in activities, status == operating, country US"},
        "features": out,
    }


def write_us_extract(full_path: Path, out_path: Path, fetched_date: dt.date | None = None) -> int:
    with full_path.open(encoding="utf-8") as fh:
        collection = json.load(fh)
    if fetched_date is None:
        fetched_date = dt.date.fromtimestamp(full_path.stat().st_mtime)
    extract = filter_us(collection, fetched_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(extract, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    return len(extract["features"])


@dataclass
class SkiArea:
    osm_id: str
    name: str
    state: str
    lon: float
    lat: float
    geometry: dict
    lift_count: Optional[int] = None
    downhill_run_count: Optional[int] = None
    vertical_ft: Optional[int] = None
    websites: list[str] = field(default_factory=list)


@dataclass
class Extract:
    fetched_date: dt.date
    areas: list[SkiArea]

    @property
    def source(self) -> Source:
        return Source(id=SOURCE_ID, url=SOURCE_URL, title=SOURCE_TITLE, accessed_date=self.fetched_date)


def load_us_extract(path: Path) -> Extract:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    fetched = dt.date.fromisoformat(data["metadata"]["fetched_date"])
    areas = []
    for feat in data.get("features", []):
        p = feat["properties"]
        rep = representative_point(feat["geometry"])
        if rep is None:
            continue
        lo, hi = p.get("min_elevation_m"), p.get("max_elevation_m")
        vertical = round((hi - lo) * M_TO_FT) if lo is not None and hi is not None and hi > lo else None
        areas.append(SkiArea(
            osm_id=str(p["id"]), name=p["name"], state=p["state"], lon=rep[0], lat=rep[1],
            geometry=feat["geometry"], lift_count=p.get("lift_count"),
            downhill_run_count=p.get("downhill_run_count"), vertical_ft=vertical,
            websites=list(p.get("websites") or []),
        ))
    return Extract(fetched_date=fetched, areas=areas)
