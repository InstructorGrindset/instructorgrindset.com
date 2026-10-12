"""OpenSkiMap ski areas: fetch, filter to US operating downhill areas, load.

Schema (OpenSkiMap ski_areas.geojson, verified against the 2026-10 download):
a FeatureCollection of Point, Polygon or MultiPolygon features whose
properties are::

    type: "skiArea"
    id: str
    name: str | null
    activities: ["downhill", "nordic", ...]
    status: "operating" | "abandoned" | "proposed" | "never_opened" | "disused" | null
    sources: [{type: "openstreetmap" | "skimap.org", id}]
    websites: [str]
    wikidataID: str | null
    places: [{ iso3166_1Alpha2: "US", iso3166_2: "US-CO",
               localized: { en: { country, region, locality } } }, ...]
    statistics: { minElevation, maxElevation,
                  lifts: { byType: { chair_lift: { count, minElevation, maxElevation, ... } } },
                  runs: { minElevation, maxElevation,
                          byActivity: { downhill: { byDifficulty: { easy: { count, ... } } } } } }
    viewportHint, runConvention

An area can list several places (Heavenly is in CA and NV). The state is the
single US state listed, else the US state whose outline contains the area's
representative point, else the first US place. An older ``location`` object
(one place) is accepted too.

``filter_us`` keeps type == skiArea, "downhill" in activities, status ==
operating, a US place and a name; optionally drops areas with no mapped lift
and no mapped downhill run (skimap.org stubs), and merges near duplicates
(close together, near identical names). It writes a compact extract
(data/raw/openskimap/us_ski_areas.geojson, committed).
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

from .geo import haversine_km, point_in_geometry, representative_point
from .names import name_similarity
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


def _places(props: dict) -> list[dict]:
    places = props.get("places")
    if isinstance(places, list):
        return [p for p in places if isinstance(p, dict)]
    loc = props.get("location")
    return [loc] if isinstance(loc, dict) else []


def us_state_codes(props: dict) -> list[str]:
    """Distinct US state codes of an area's places, in listed order."""
    out: list[str] = []
    for place in _places(props):
        if place.get("iso3166_1Alpha2") != "US":
            continue
        code = place.get("iso3166_2") or ""
        if isinstance(code, str) and code.startswith("US-") and len(code) == 5:
            c = code[3:].upper()
            if c not in out:
                out.append(c)
    return out


def choose_state(codes: list[str], point: Optional[tuple[float, float]],
                 state_shapes: Optional[dict[str, dict]] = None) -> Optional[str]:
    """One state for an area: the only one, else the one whose outline contains
    ``point`` (lon, lat), else the first listed."""
    if not codes:
        return None
    if len(codes) == 1 or point is None or not state_shapes:
        return codes[0]
    for code in codes:
        shape = state_shapes.get(code)
        if shape and point_in_geometry(point[0], point[1], shape):
            return code
    return codes[0]


def load_state_shapes(path: Path) -> dict[str, dict]:
    """{USPS code: geometry} from data/build/us-states.geojson."""
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {f["properties"]["code"]: f["geometry"] for f in data.get("features", [])}


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
    """Lowest and highest mapped lift or downhill run elevation (m).

    Nordic runs are left out so valley trails do not inflate the vertical.
    Falls back to the area wide statistics when the breakdown has no elevations.
    """
    stats = stats or {}
    entries: list[dict] = []
    entries += list(((stats.get("lifts") or {}).get("byType") or {}).values())
    downhill = (((stats.get("runs") or {}).get("byActivity") or {}).get("downhill") or {})
    entries += list((downhill.get("byDifficulty") or {}).values())
    los = [e["minElevation"] for e in entries if isinstance(e, dict) and isinstance(e.get("minElevation"), (int, float))]
    his = [e["maxElevation"] for e in entries if isinstance(e, dict) and isinstance(e.get("maxElevation"), (int, float))]
    if los and his:
        lo, hi = min(los), max(his)
    else:
        lo, hi = stats.get("minElevation"), stats.get("maxElevation")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi >= lo:
        return float(lo), float(hi)
    return None, None


def keep_feature(props: dict) -> bool:
    """True for a named, operating, downhill ski area with a US place."""
    if props.get("type") != "skiArea":
        return False
    if "downhill" not in (props.get("activities") or []):
        return False
    if props.get("status") != "operating":
        return False
    if not us_state_codes(props):
        return False
    return bool((props.get("name") or "").strip())


def _score(p: dict) -> tuple:
    """Which of two duplicates to keep: more mapped infrastructure, more sources, then id."""
    return (-((p["lift_count"] or 0) + (p["downhill_run_count"] or 0)), -len(p["source_types"]), str(p["id"]))


def merge_duplicates(features: list[dict], radius_km: float, similarity_min: float) -> list[dict]:
    """Drop areas that sit within ``radius_km`` of a better mapped area with a name at
    least ``similarity_min`` alike. The kept feature lists the dropped ids in ``merged_ids``."""
    ranked = sorted(features, key=lambda f: _score(f["properties"]))
    kept: list[dict] = []
    for f in ranked:
        p = f["properties"]
        twin = None
        for k in kept:
            kp = k["properties"]
            if haversine_km(p["lat"], p["lon"], kp["lat"], kp["lon"]) <= radius_km and \
                    name_similarity(p["name"], kp["name"]) >= similarity_min:
                twin = k
                break
        if twin is None:
            kept.append(f)
        else:
            twin["properties"]["merged_ids"].append(p["id"])
    return kept


def filter_description(require_infrastructure: bool = False, dedupe_radius_km: Optional[float] = None,
                       dedupe_similarity_min: float = 0.8, **_: Any) -> str:
    """Human readable filter rules, stored in the extract metadata."""
    return ("type == skiArea, downhill in activities, status == operating, a US place, named"
            + (", at least one mapped lift or downhill run" if require_infrastructure else "")
            + (f", duplicates within {dedupe_radius_km} km with name similarity >= {dedupe_similarity_min} merged"
               if dedupe_radius_km else ""))


def extract_needs_refresh(full: Path, extract: Path, **filter_kw: Any) -> bool:
    """True when the cached full download should be re-filtered into the extract.

    Never when the extract was fetched after the download (a newer committed extract wins over an
    older local download); on the same date only when the filter rules changed.
    """
    if not full.exists():
        return False
    if not extract.exists():
        return True
    try:
        meta = json.loads(extract.read_text(encoding="utf-8")).get("metadata") or {}
        extract_date = dt.date.fromisoformat(meta["fetched_date"])
    except (ValueError, KeyError, TypeError):
        return True
    full_date = dt.date.fromtimestamp(full.stat().st_mtime)
    if extract_date > full_date:
        return False
    if extract_date == full_date:
        return meta.get("filter") != filter_description(**filter_kw)
    return True


def filter_us(
    collection: dict,
    fetched_date: dt.date,
    decimals: int = 5,
    *,
    require_infrastructure: bool = False,
    dedupe_radius_km: Optional[float] = None,
    dedupe_similarity_min: float = 0.8,
    state_shapes: Optional[dict[str, dict]] = None,
) -> dict:
    """Reduce a full ski_areas FeatureCollection to the committed US extract."""
    out = []
    counts = {"us_operating_downhill_named": 0, "dropped_no_lifts_or_runs": 0, "merged_duplicates": 0}
    for feat in collection.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom or not keep_feature(props):
            continue
        counts["us_operating_downhill_named"] += 1
        rep = representative_point(geom)
        if rep is None:
            continue
        stats = props.get("statistics") or {}
        lifts = _count_by(stats, "lifts", "byType")
        runs = _count_by(stats, "runs", "byActivity", "downhill", "byDifficulty")
        if require_infrastructure and not (lifts or runs):
            counts["dropped_no_lifts_or_runs"] += 1
            continue
        codes = us_state_codes(props)
        lo, hi = _elevations(stats)
        us_places = [p for p in _places(props) if p.get("iso3166_1Alpha2") == "US"]
        locality = ((us_places[0].get("localized") or {}).get("en") or {}).get("locality") if us_places else None
        out.append({
            "type": "Feature",
            "properties": {
                "id": props.get("id"),
                "name": props["name"].strip(),
                "state": choose_state(codes, rep, state_shapes),
                "states": codes,
                "locality": locality,
                "lat": round(rep[1], decimals),
                "lon": round(rep[0], decimals),
                "websites": list(props.get("websites") or []),
                "wikidata_id": props.get("wikidataID"),
                "source_types": sorted({s.get("type") for s in props.get("sources") or [] if isinstance(s, dict) and s.get("type")}),
                "lift_count": lifts,
                "downhill_run_count": runs,
                "min_elevation_m": lo,
                "max_elevation_m": hi,
                "merged_ids": [],
            },
            "geometry": {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"], decimals)},
        })
    if dedupe_radius_km:
        before = len(out)
        out = merge_duplicates(out, dedupe_radius_km, dedupe_similarity_min)
        counts["merged_duplicates"] = before - len(out)
    for f in out:
        f["properties"]["merged_ids"].sort()
    out.sort(key=lambda f: (f["properties"]["state"], f["properties"]["name"].lower(), str(f["properties"]["id"])))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source_url": URL,
            "fetched_date": fetched_date.isoformat(),
            "filter": filter_description(require_infrastructure, dedupe_radius_km, dedupe_similarity_min),
            "counts": {**counts, "kept": len(out)},
        },
        "features": out,
    }


def write_us_extract(full_path: Path, out_path: Path, fetched_date: dt.date | None = None, **filter_kw: Any) -> int:
    with full_path.open(encoding="utf-8") as fh:
        collection = json.load(fh)
    if fetched_date is None:
        fetched_date = dt.date.fromtimestamp(full_path.stat().st_mtime)
    extract = filter_us(collection, fetched_date, **filter_kw)
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
    base_ft: Optional[int] = None
    summit_ft: Optional[int] = None
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
        rep = (p["lon"], p["lat"]) if "lat" in p and "lon" in p else representative_point(feat["geometry"])
        if rep is None:
            continue
        lo, hi = p.get("min_elevation_m"), p.get("max_elevation_m")
        vertical = round((hi - lo) * M_TO_FT) if lo is not None and hi is not None and hi > lo else None
        areas.append(SkiArea(
            osm_id=str(p["id"]), name=p["name"], state=p["state"], lon=rep[0], lat=rep[1],
            geometry=feat["geometry"], lift_count=p.get("lift_count"),
            downhill_run_count=p.get("downhill_run_count"), vertical_ft=vertical,
            base_ft=round(lo * M_TO_FT) if lo is not None else None,
            summit_ft=round(hi * M_TO_FT) if hi is not None else None,
            websites=list(p.get("websites") or []),
        ))
    return Extract(fetched_date=fetched, areas=areas)
