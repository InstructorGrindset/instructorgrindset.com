"""GTFS transit: extract routes and stops near housing and base areas; bus commute model.

    python -m pipeline.transit fetch            # download every feed in data/raw/transit/feeds.yaml
    python -m pipeline.transit check            # print each downloaded feed's validity and currency
    python -m pipeline.transit extract          # gtfs.zip -> data/raw/transit/<agency>.json (committed)

feeds.yaml lists one entry per agency (format in data/raw/transit/README.md): the
agency's own feed ``url``, the Mobility Database catalog id ``mdb_source_id``, the
catalog's mirror copy ``mirror_url`` (downloaded first; the agency hosts are often
unreachable), the catalog ``license`` and optional ``agency_ids`` that keep only
some agencies of a shared feed. The zip lives at data/raw/transit/<agency>/gtfs.zip
(gitignored).

Extraction keeps stops within ``transit.search_radius_km`` of any anchor point
(Tier A base areas and housing sites), the routes serving them (geometry from
shapes.txt, else from the stop sequences of their trips, simplified to
``transit.simplify_tolerance_m``), and stop patterns: each distinct sequence of
kept stops on a route, with the median minutes from its first stop over all
trips with that pattern.

Feed currency: the validity period comes from feed_info.txt (feed_start_date,
feed_end_date), else from calendar.txt and calendar_dates.txt; ``as_of`` is its
start. A feed is current when that period overlaps ``transit.target_season`` and
spans at most ``transit.max_validity_years``, or when ``as_of`` is within
``staleness.max_age_months`` of the build date. ``extract`` skips feeds that are
not current and the build drops such extracts.

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
from typing import Any, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "pipeline"

from pipeline.career import add_months, is_stale  # noqa: E402
from pipeline.geo import haversine_km  # noqa: E402
from pipeline.runs import simplify_line  # noqa: E402
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


def _gtfs_date(v: str) -> Optional[dt.date]:
    v = (v or "").strip()
    try:
        return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8])) if len(v) == 8 and v.isdigit() else None
    except ValueError:
        return None


def feed_validity(zf: zipfile.ZipFile) -> dict:
    """The feed's own validity: ``{version, valid_from, valid_to, basis}`` (ISO dates or None).

    feed_info.txt feed_start_date and feed_end_date win; a missing bound falls back to the
    earliest calendar.txt start_date (latest end_date) and the dates added in calendar_dates.txt.
    ``basis`` names where the dates came from: feed_info, calendar, or feed_info+calendar."""
    info = (_table(zf, "feed_info.txt") or [{}])[0]
    start, end = _gtfs_date(info.get("feed_start_date", "")), _gtfs_date(info.get("feed_end_date", ""))
    from_info = {start is not None, end is not None}
    if start is None or end is None:
        added = [d for r in _table(zf, "calendar_dates.txt") if r.get("exception_type") == "1"
                 if (d := _gtfs_date(r.get("date", "")))]
        cal = _table(zf, "calendar.txt")
        starts = [d for r in cal if (d := _gtfs_date(r.get("start_date", "")))] + added
        ends = [d for r in cal if (d := _gtfs_date(r.get("end_date", "")))] + added
        start = start or (min(starts) if starts else None)
        end = end or (max(ends) if ends else None)
    basis = None
    if start or end:
        basis = "feed_info" if from_info == {True} else "feed_info+calendar" if True in from_info else "calendar"
    return {"version": info.get("feed_version") or None, "valid_from": start.isoformat() if start else None,
            "valid_to": end.isoformat() if end else None, "basis": basis}


def feed_status(valid_from: Optional[dt.date], valid_to: Optional[dt.date], today: dt.date, max_age_months: int,
                season: Optional[tuple[dt.date, dt.date]] = None, max_validity_years: float = 3) -> tuple[bool, str]:
    """(current, reason). Current when the validity period overlaps ``season`` without being an open
    ended placeholder (longer than ``max_validity_years``), or when ``valid_from`` (the as_of) is at most
    ``max_age_months`` before ``today``."""
    if valid_from is None:
        return False, "no validity dates in feed_info.txt or calendar.txt"
    open_ended = valid_to is not None and valid_to > add_months(valid_from, round(max_validity_years * 12))
    if season and valid_to is not None and not open_ended and valid_from <= season[1] and valid_to >= season[0]:
        return True, f"valid {valid_from} to {valid_to}, overlapping the {season[0]} to {season[1]} season"
    if not is_stale(valid_from, today, max_age_months):
        return True, f"valid from {valid_from}, within {max_age_months} months"
    tail = " (open ended placeholder)" if open_ended else ""
    return False, f"valid {valid_from} to {valid_to}{tail}: older than {max_age_months} months and not valid for the target season"


def defs_status(feed: dict, today: dt.date, defs: Any) -> tuple[bool, str]:
    """feed_status for an extract's ``feed`` block with the transit and staleness definitions."""
    def d(k: str) -> Optional[dt.date]:
        v = feed.get(k)
        return dt.date.fromisoformat(str(v)) if v else None
    t = defs.transit
    season = (t.target_season.start, t.target_season.end) if t.target_season else None
    return feed_status(d("valid_from"), d("valid_to"), today, defs.staleness.max_age_months, season, t.max_validity_years)


def current_extracts(extracts: Sequence[dict], today: dt.date, defs: Any) -> tuple[list[dict], list[str]]:
    """Extracts whose feed is current on ``today``, and a warning for each one dropped."""
    kept, warnings = [], []
    for ex in extracts:
        ok, why = defs_status(ex.get("feed") or {}, today, defs)
        if ok:
            kept.append(ex)
        else:
            warnings.append(f"transit extract '{ex.get('agency')}' is not current ({why}); not shown. "
                            "Refresh it with `python -m pipeline.transit fetch` and `extract`")
    return kept, warnings


def _color(c: str) -> Optional[str]:
    c = (c or "").strip().lstrip("#")
    return f"#{c.upper()}" if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c) else None


def extract_gtfs(zip_path: Path, agency: str, anchors: Sequence[tuple[float, float]], radius_km: float,
                 source: Source, decimals: int = 5, agency_ids: Optional[Sequence[str]] = None,
                 simplify_tolerance_m: Optional[float] = None, feed_meta: Optional[dict] = None) -> dict:
    """TransitExtract dict for one feed. ``anchors`` are (lat, lon) points.

    ``agency_ids`` keeps only the routes of those GTFS agencies (a shared feed may also carry an airport
    shuttle or paratransit). ``simplify_tolerance_m`` simplifies route lines (Douglas Peucker).
    ``feed_meta`` (catalog id, urls, license) is merged into the ``feed`` block with the feed's own dates."""
    with zipfile.ZipFile(zip_path) as zf:
        routes = {r["route_id"]: r for r in _table(zf, "routes.txt")}
        trips = {t["trip_id"]: t for t in _table(zf, "trips.txt")}
        stops = {s["stop_id"]: s for s in _table(zf, "stops.txt")}
        stop_times = _table(zf, "stop_times.txt")
        shapes_raw = _table(zf, "shapes.txt")
        validity = feed_validity(zf)
    if agency_ids is not None:
        wanted = {str(a) for a in agency_ids}
        routes = {rid: r for rid, r in routes.items() if r.get("agency_id", "") in wanted}
        trips = {tid: t for tid, t in trips.items() if t.get("route_id") in routes}

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
        if st.get("stop_id"):  # flexible service rows name a location or zone, not a stop
            by_trip.setdefault(st.get("trip_id", ""), []).append(st)
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
            pts = [(x, y) for _, x, y in sorted(shapes[sid])]
        else:
            pts = [_lonlat(stops[st["stop_id"]]) for st in by_trip[tid] if st["stop_id"] in stops]
            pts = [p for p in pts if p is not None]
        if simplify_tolerance_m:
            pts = [tuple(p) for p in (simplify_line([list(p) for p in pts], simplify_tolerance_m, decimals) or [])]
        line = tuple((round(x, decimals), round(y, decimals)) for x, y in pts)
        line = tuple(p for i, p in enumerate(line) if i == 0 or p != line[i - 1])
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
    feed = {**(feed_meta or {}), "version": validity["version"], "valid_from": validity["valid_from"],
            "valid_to": validity["valid_to"], "validity_basis": validity["basis"], "as_of": validity["valid_from"]}
    return {"version": 1, "agency": agency, "source": source.model_dump(mode="json"), "feed": feed,
            "search_radius_km": radius_km, "simplify_tolerance_m": simplify_tolerance_m,
            "routes": out_routes, "stops": out_stops, "patterns": out_patterns}


def _lonlat(stop: dict) -> Optional[tuple[float, float]]:
    try:
        return float(stop["stop_lon"]), float(stop["stop_lat"])
    except (KeyError, ValueError):
        return None


def dump_extract(data: dict) -> str:
    """Compact, diff friendly JSON: top level keys on their own lines, one route, stop or pattern per line."""
    lines = ["{"]
    keys = list(data)
    for i, k in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        v = data[k]
        if isinstance(v, list) and v:
            items = [json.dumps(x, separators=(",", ":"), ensure_ascii=False) for x in v]
            lines.append(f" {json.dumps(k)}: [")
            lines += [f"  {x}," for x in items[:-1]] + [f"  {items[-1]}", f" ]{comma}"]
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v, separators=(',', ':'), ensure_ascii=False)}{comma}")
    return "\n".join(lines + ["}"]) + "\n"


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


def feed_source(feed: dict, accessed: dt.date, validity: dict) -> Source:
    """Source ``gtfs-<agency>`` for one feeds.yaml entry: the agency's own feed URL, with the catalog id,
    the mirror and the feed's validity in the note."""
    parts = []
    if feed.get("mdb_source_id") is not None:
        parts.append(f"Mobility Database catalog entry {feed['mdb_source_id']}")
    if feed.get("mirror_url"):
        parts.append(f"downloaded from the catalog mirror {feed['mirror_url']}")
    parts.append(f"feed valid {validity.get('valid_from') or 'unknown'} to {validity.get('valid_to') or 'unknown'}")
    parts.append(f"license: {feed['license']}" if feed.get("license") else "license: none listed in the catalog")
    return Source(id=f"gtfs-{feed['agency']}", url=feed["url"], title=feed.get("title") or f"{feed['agency']} GTFS",
                  accessed_date=accessed, note="; ".join(parts) + ".")


def feed_meta(feed: dict) -> dict:
    """The catalog fields of a feeds.yaml entry copied into the extract's ``feed`` block."""
    return {"mdb_source_id": feed.get("mdb_source_id"), "url": feed["url"], "mirror_url": feed.get("mirror_url"),
            "license": feed.get("license"), "agency_ids": [str(a) for a in feed["agency_ids"]] if feed.get("agency_ids") else None}


def run_extract(feeds: Sequence[dict], transit_dir: Path, anchors: Sequence[tuple[float, float]], defs: Any,
                today: dt.date) -> list[str]:
    """Write <agency>.json for every downloaded, current feed with stops near an anchor. Returns log lines."""
    log = []
    t = defs.transit
    for feed in feeds:
        zpath = transit_dir / feed["agency"] / "gtfs.zip"
        if not zpath.exists():
            log.append(f"{feed['agency']}: no {zpath}; run fetch first")
            continue
        with zipfile.ZipFile(zpath) as zf:
            validity = feed_validity(zf)
        ok, why = defs_status(validity, today, defs)
        if not ok:
            log.append(f"{feed['agency']}: skipped, not current ({why})")
            continue
        src = feed_source(feed, dt.date.fromtimestamp(zpath.stat().st_mtime), validity)
        data = extract_gtfs(zpath, feed["agency"], anchors, t.search_radius_km, src, agency_ids=feed.get("agency_ids"),
                            simplify_tolerance_m=t.simplify_tolerance_m, feed_meta=feed_meta(feed))
        out = transit_dir / f"{feed['agency']}.json"
        if not data["stops"]:
            log.append(f"{feed['agency']}: skipped, no stop within {t.search_radius_km} km of a Tier A base or housing site")
            continue
        out.write_text(dump_extract(data), encoding="utf-8")
        log.append(f"{feed['agency']}: {len(data['routes'])} routes, {len(data['stops'])} stops, "
                   f"{out.stat().st_size // 1024} KB ({why})")
    return log


def main(argv: Optional[list[str]] = None) -> int:
    from pipeline.build import build_date
    from pipeline.config import load_config
    from pipeline.openskimap import fetch as download

    ap = argparse.ArgumentParser(description="Fetch GTFS feeds and extract transit near Tier A resorts.")
    ap.add_argument("command", choices=["fetch", "check", "extract"])
    args = ap.parse_args(argv)
    feeds = _feeds(TRANSIT_DIR)
    if not feeds:
        print(f"no feeds listed in {TRANSIT_DIR / 'feeds.yaml'}; nothing to do")
        return 0
    defs = load_config().definitions
    today = build_date()
    if args.command == "extract":
        for line in run_extract(feeds, TRANSIT_DIR, _anchors(REPO_ROOT), defs, today):
            print(line)
        return 0
    for feed in feeds:
        zpath = TRANSIT_DIR / feed["agency"] / "gtfs.zip"
        if args.command == "fetch":
            for url in [u for u in (feed.get("mirror_url"), feed["url"]) if u]:
                try:
                    download(zpath, url)
                    print(f"downloaded {feed['agency']} from {url}")
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"{feed['agency']}: download from {url} failed ({type(e).__name__}: {e})")
            continue
        if not zpath.exists():
            print(f"{feed['agency']}: not downloaded")
            continue
        with zipfile.ZipFile(zpath) as zf:
            validity = feed_validity(zf)
        ok, why = defs_status(validity, today, defs)
        print(f"{feed['agency']}: {'current' if ok else 'NOT CURRENT'}; {why}; version {validity['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
