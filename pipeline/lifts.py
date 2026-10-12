"""OpenSkiMap lifts for Tier A resorts.

Schema (lifts.geojson, verified against the 2026-10 download): LineString
features whose properties include ``type: "lift"``, ``id``, ``name``,
``ref``, ``liftType`` (chair_lift, gondola, t-bar, platter, drag_lift,
magic_carpet, rope_tow, cable_car, funicular, j-bar, mixed_lift, railway),
``status`` (operating, abandoned, disused, proposed, construction, planned)
and ``skiAreas``: a list of ski area Features whose ``properties.id`` is the
OpenSkiMap ski area id (the same id as in ski_areas.geojson).

``filter_lifts`` keeps operating lifts that belong to an OpenSkiMap area
matched to a Tier A resort and writes the committed extract
data/raw/openskimap/us_lifts_tier_a.geojson. The full download
(lifts.full.geojson, about 120 MB) is gitignored.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable, Optional

from .openskimap import _round_coords, fetch as _fetch_url
from .schemas.common import Source

URL = "https://tiles.openskimap.org/geojson/lifts.geojson"
FULL_NAME = "lifts.full.geojson"
EXTRACT_NAME = "us_lifts_tier_a.geojson"
SOURCE_ID = "openskimap-lifts"
SOURCE_URL = "https://openskimap.org/"
SOURCE_TITLE = "OpenSkiMap lifts (derived from OpenStreetMap, ODbL)"


def fetch(dest: Path) -> Path:
    return _fetch_url(dest, URL)


def source(fetched: dt.date) -> Source:
    return Source(id=SOURCE_ID, url=SOURCE_URL, title=SOURCE_TITLE, accessed_date=fetched)


def ski_area_ids(props: dict) -> list[str]:
    out = []
    for sa in props.get("skiAreas") or []:
        sid = ((sa or {}).get("properties") or {}).get("id") if isinstance(sa, dict) else None
        if isinstance(sid, str):
            out.append(sid)
    return out


def filter_lifts(collection: dict, area_ids: Iterable[str], fetched: dt.date, decimals: int = 5) -> dict:
    """Operating LineString lifts whose skiAreas include one of ``area_ids``."""
    wanted = set(area_ids)
    out = []
    for f in collection.get("features", []):
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        if p.get("type", "lift") != "lift" or p.get("status") != "operating" or g.get("type") != "LineString":
            continue
        areas = sorted(set(ski_area_ids(p)) & wanted)
        if not areas:
            continue
        out.append({
            "type": "Feature",
            "properties": {"id": p.get("id"), "name": p.get("name"), "ref": p.get("ref"),
                           "lift_type": p.get("liftType"), "ski_area_ids": areas},
            "geometry": {"type": "LineString", "coordinates": _round_coords(g["coordinates"], decimals)},
        })
    out.sort(key=lambda f: (f["properties"]["ski_area_ids"][0], str(f["properties"]["name"]), str(f["properties"]["id"])))
    return {
        "type": "FeatureCollection",
        "metadata": {"source_url": URL, "fetched_date": fetched.isoformat(),
                     "filter": "status == operating, skiAreas include an area matched to a Tier A resort",
                     "area_ids": sorted(wanted)},
        "features": out,
    }


def refresh_extract(full: Path, extract: Path, area_ids: Iterable[str]) -> Optional[int]:
    """Rewrite the extract from the cached full download when the matched areas or the download changed."""
    ids = sorted(set(area_ids))
    if not full.exists():
        return None
    if extract.exists() and extract.stat().st_mtime >= full.stat().st_mtime:
        try:
            meta = json.loads(extract.read_text(encoding="utf-8")).get("metadata", {})
        except ValueError:
            meta = {}
        if meta.get("area_ids") == ids:
            return None
    with full.open(encoding="utf-8") as fh:
        collection = json.load(fh)
    fetched = dt.date.fromtimestamp(full.stat().st_mtime)
    data = filter_lifts(collection, ids, fetched)
    extract.parent.mkdir(parents=True, exist_ok=True)
    extract.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    return len(data["features"])


def load_extract(path: Path) -> tuple[Optional[dt.date], list[dict]]:
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    return dt.date.fromisoformat(data["metadata"]["fetched_date"]), data.get("features", [])


def lifts_for(features: Iterable[dict], area_ids: Iterable[str]) -> dict:
    """Detail file lift FeatureCollection for one resort (properties per contract)."""
    wanted = set(area_ids)
    out = [{
        "type": "Feature",
        "geometry": f["geometry"],
        "properties": {"name": f["properties"].get("name"), "lift_type": f["properties"].get("lift_type"),
                       "source_id": SOURCE_ID},
    } for f in features if wanted & set(f["properties"].get("ski_area_ids") or [])]
    return {"type": "FeatureCollection", "features": out}
