"""GTFS transit: extract routes and stops near housing and base areas; bus commute model.

    python -m pipeline.transit fetch            # download every feed in data/raw/transit/feeds.yaml
    python -m pipeline.transit extract          # gtfs.zip -> data/raw/transit/<agency>.json (committed)

feeds.yaml: ``{feeds: [{agency: summit-stage, url: https://.../gtfs.zip, title: Summit Stage GTFS}]}``.
The zip lives at data/raw/transit/<agency>/gtfs.zip (gitignored).

Extraction keeps stops within ``transit.search_radius_km`` of any anchor point
(Tier A base areas and housing sites), the routes serving them (geometry from
shapes.txt, else from the stop sequences of their trips), and stop patterns:
each distinct sequence of kept stops on a route, with the median minutes from
its first stop over all trips with that pattern.

Bus commute model (documented in ARCHITECTURE.md): walk from the housing site
to a kept stop within ``transit.max_walk_km``, ride a pattern to a stop within
``max_walk_km`` of the base area, walk to the base. Minutes = walk + scheduled
ride + walk at ``transit.walk_speed_kmh``, with no waiting time. The fastest
option is kept. No commute is produced while those settings are null.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.geo import haversine_km  # noqa: E402
from pipeline.schemas.common import Source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSIT_DIR = REPO_ROOT / "data" / "raw" / "transit"
KM_PER_MI = 1.609344


def _table(zf: zipfile.ZipFile, name: str) -> list[dict]:
    names = {n.split("/")[-1]: n for n in zf.namelist()}
    if name not in names:
        return []
    with zf.open(names[name]) as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))]


def gtfs_minutes(t: str) -> Optional[float]:
    """GTFS time "HH:MM:SS" (hours may exceed 24) to minutes after midnight."""
    if not t:
        return None
    h, m, s = (int(x) for x in t.split(":"))
    return h * 60 + m + s / 60


def _color(c: str) -> Optional[str]:
    c = (c or "").strip().lstrip("#")
    return f"#{c.upper()}" if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c) else None


def extract_gtfs(zip_path: Path, agency: str, anchors: Sequence[tuple[float, float]], radius_km: float,
                 source: Source, decimals: int = 5) -> dict:
    """TransitExtract dict for one feed. ``anchors`` are (lat, lon) points."""
    with zipfile.ZipFile(zip_path) as zf:
        routes = {r["route_id"]: r for r in _table(zf, "routes.txt")}
        trips = {t["trip_id"]: t for t in _table(zf, "trips.txt")}
        stops = {s["stop_id"]: s for s in _table(zf, "stops.txt")}
        stop_times = _table(zf, "stop_times.txt")
        shapes_raw = _table(zf, "shapes.txt")

    def near(s: dict) -> bool:
        if s.get("location_type") not in ("", "0", None):
            return False
        try:
            lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
        except (KeyError, ValueError):
            return False
        return any(haversine_km(lat, lon, a, b) <= radius_km for a, b in anchors)

    kept = {sid for sid, s in stops.items() if near(s)}
    by_trip: dict[str, list[dict]] = {}
    for st in stop_times:
        by_trip.setdefault(st["trip_id"], []).append(st)
    for seq in by_trip.values():
        seq.sort(key=lambda st: int(st.get("stop_sequence") or 0))

    serving: dict[str, set[str]] = {}
    used_trips: list[str] = []
    for tid, seq in by_trip.items():
        if tid not in trips or not any(st["stop_id"] in kept for st in seq):
            continue
        used_trips.append(tid)
        rid = trips[tid]["route_id"]
        for st in seq:
            if st["stop_id"] in kept:
                serving.setdefault(st["stop_id"], set()).add(rid)

    shapes: dict[str, list[tuple[int, float, float]]] = {}
    for p in shapes_raw:
        shapes.setdefault(p["shape_id"], []).append(
            (int(p.get("shape_pt_sequence") or 0), float(p["shape_pt_lon"]), float(p["shape_pt_lat"])))
    route_lines: dict[str, set[tuple]] = {}
    for tid in used_trips:
        t = trips[tid]
        sid = t.get("shape_id")
        if sid and sid in shapes:
            line = tuple((round(x, decimals), round(y, decimals)) for _, x, y in sorted(shapes[sid]))
        else:
            line = tuple((round(float(stops[st["stop_id"]]["stop_lon"]), decimals),
                          round(float(stops[st["stop_id"]]["stop_lat"]), decimals))
                         for st in by_trip[tid] if st["stop_id"] in stops)
        if len(line) >= 2:
            route_lines.setdefault(t["route_id"], set()).add(line)

    patterns: dict[tuple[str, tuple[str, ...]], list[list[float]]] = {}
    for tid in used_trips:
        seq = [st for st in by_trip[tid] if st["stop_id"] in kept]
        if len(seq) < 2:
            continue
        t0 = gtfs_minutes(seq[0].get("departure_time") or seq[0].get("arrival_time"))
        offs = [gtfs_minutes(st.get("arrival_time") or st.get("departure_time")) for st in seq]
        if t0 is None or any(o is None for o in offs):
            continue
        key = (trips[tid]["route_id"], tuple(st["stop_id"] for st in seq))
        patterns.setdefault(key, []).append([max(0.0, o - t0) for o in offs])

    pfx = f"{agency}:"
    out_routes = []
    for rid in sorted(route_lines):
        r = routes.get(rid, {})
        out_routes.append({
            "id": pfx + rid, "agency": agency, "short_name": r.get("route_short_name") or None,
            "long_name": r.get("route_long_name") or None, "color": _color(r.get("route_color", "")),
            "geometry": {"type": "MultiLineString", "coordinates": [[list(p) for p in line] for line in sorted(route_lines[rid])]},
        })
    out_stops = [{
        "id": pfx + sid, "name": stops[sid].get("stop_name") or sid,
        "lat": round(float(stops[sid]["stop_lat"]), decimals), "lon": round(float(stops[sid]["stop_lon"]), decimals),
        "route_ids": sorted(pfx + r for r in serving[sid]),
    } for sid in sorted(serving)]
    out_patterns = []
    for (rid, sids), runs in sorted(patterns.items()):
        med = [round(statistics.median(col), 1) for col in zip(*runs)]
        for i in range(1, len(med)):  # medians of monotone runs can still dip by rounding
            med[i] = max(med[i], med[i - 1])
        out_patterns.append({"route_id": pfx + rid, "stop_ids": [pfx + s for s in sids], "offsets_min": med,
                             "trip_count": len(runs)})
    return {"version": 1, "agency": agency, "source": source.model_dump(mode="json"), "search_radius_km": radius_km,
            "routes": out_routes, "stops": out_stops, "patterns": out_patterns}


def nearby(extract: dict, anchors: Sequence[tuple[float, float]], radius_km: float) -> Optional[dict]:
    """Routes and stops of one extract within ``radius_km`` of any anchor (for a detail file)."""
    stops = [s for s in extract["stops"] if any(haversine_km(s["lat"], s["lon"], a, b) <= radius_km for a, b in anchors)]
    if not stops:
        return None
    rids = {r for s in stops for r in s["route_ids"]}
    return {"routes": [r for r in extract["routes"] if r["id"] in rids], "stops": stops}


def bus_commute(site: tuple[float, float], base: tuple[float, float], extract: dict,
                walk_speed_kmh: Optional[float], max_walk_km: Optional[float]) -> Optional[dict]:
    """Fastest walk + ride + walk commute from ``site`` to ``base`` (lat, lon), or None."""
    if not walk_speed_kmh or not max_walk_km:
        return None
    stops = {s["id"]: s for s in extract["stops"]}

    def walk(a: tuple[float, float], s: dict) -> float:
        return haversine_km(a[0], a[1], s["lat"], s["lon"])
    best = None
    for p in extract["patterns"]:
        ids = p["stop_ids"]
        for i, oi in enumerate(ids):
            so = stops.get(oi)
            if so is None or walk(site, so) > max_walk_km:
                continue
            for j in range(i + 1, len(ids)):
                sd = stops.get(ids[j])
                if sd is None or walk(base, sd) > max_walk_km:
                    continue
                walk_km = walk(site, so) + walk(base, sd)
                minutes = walk_km / walk_speed_kmh * 60 + p["offsets_min"][j] - p["offsets_min"][i]
                if best is None or minutes < best[0]:
                    best = (minutes, p, i, j)
    if best is None:
        return None
    minutes, p, i, j = best
    path = [(site[1], site[0])] + [(stops[s]["lon"], stops[s]["lat"]) for s in p["stop_ids"][i:j + 1]] + [(base[1], base[0])]
    km = sum(haversine_km(a[1], a[0], b[1], b[0]) for a, b in zip(path, path[1:]))
    return {"mode": "bus", "minutes": round(minutes, 1), "distance_mi": round(km / KM_PER_MI, 2),
            "transit_route_ids": [p["route_id"]], "geometry": {"type": "LineString", "coordinates": [list(c) for c in path]},
            "source_id": extract["source"]["id"]}


def load_extracts(transit_dir: Path) -> list[dict]:
    if not transit_dir.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(transit_dir.glob("*.json"))]


# ----------------------------------------------------------------------- CLI
def _feeds(transit_dir: Path) -> list[dict]:
    import yaml

    path = transit_dir / "feeds.yaml"
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("feeds") or []


def _anchors(repo_root: Path) -> list[tuple[float, float]]:
    resorts = json.loads((repo_root / "data" / "build" / "resorts.json").read_text())["resorts"]
    pts = [(r["lat"], r["lon"]) for r in resorts if r["tier"] == "A"]
    import yaml

    for p in sorted((repo_root / "data" / "raw" / "housing").glob("*.yaml")):
        for s in (yaml.safe_load(p.read_text()) or {}).get("sites") or []:
            pts.append((float(s["lat"]), float(s["lon"])))
    return pts


def main(argv: Optional[list[str]] = None) -> int:
    from pipeline.config import load_config
    from pipeline.openskimap import fetch as download

    ap = argparse.ArgumentParser(description="Fetch GTFS feeds and extract transit near Tier A resorts.")
    ap.add_argument("command", choices=["fetch", "extract"])
    args = ap.parse_args(argv)
    feeds = _feeds(TRANSIT_DIR)
    if not feeds:
        print(f"no feeds listed in {TRANSIT_DIR / 'feeds.yaml'}; nothing to do")
        return 0
    radius = load_config().definitions.transit.search_radius_km
    for feed in feeds:
        zpath = TRANSIT_DIR / feed["agency"] / "gtfs.zip"
        if args.command == "fetch":
            try:
                download(zpath, feed["url"])
                print(f"downloaded {feed['agency']}")
            except Exception as e:  # noqa: BLE001
                print(f"{feed['agency']}: download failed ({type(e).__name__}: {e})")
            continue
        if not zpath.exists():
            print(f"{feed['agency']}: no {zpath}; run fetch first")
            continue
        src = Source(id=f"gtfs-{feed['agency']}", url=feed["url"], title=feed.get("title") or f"{feed['agency']} GTFS",
                     accessed_date=dt.date.fromtimestamp(zpath.stat().st_mtime))
        data = extract_gtfs(zpath, feed["agency"], _anchors(REPO_ROOT), radius, src)
        (TRANSIT_DIR / f"{feed['agency']}.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"{feed['agency']}: {len(data['routes'])} routes, {len(data['stops'])} stops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
