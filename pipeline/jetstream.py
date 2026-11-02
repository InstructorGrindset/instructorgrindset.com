"""Jet stream layer: average 250 hPa jet paths per ski season group (docs/map/JETSTREAM.md).

Inputs: the ERA5 season means (``data/raw/climate/era5_jet/season_means_250hpa.json``, pipeline/climate/era5_jet.py)
and NOAA CPC's RONI (``data/raw/climate/enso/RONI.ascii.txt``, pipeline/climate/enso.py). Every threshold is in
``config/definitions.yaml`` ``jetstream`` (pipeline/config.py ``JetstreamDefs``). Output: ``data/build/jetstream.json``.

Steps: classify each season by ENSO phase and strength; average the season mean winds of each layer's seasons; find
the jet axis (local wind speed maxima per longitude, linked across longitudes into chains, smoothed); measure a band
around each axis; list the states and resorts under the bands; decide per phase whether strength classes split.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pipeline.climate.enso import parse_index
from pipeline.geo import point_in_geometry

ERA5_DIR = Path("climate") / "era5_jet"   # under data/raw
ERA5_EXTRACT = "season_means_250hpa.json"
ENSO_DIR = Path("climate") / "enso"
RONI_FILE = "RONI.ascii.txt"
OUT_NAME = "jetstream.json"

PHASES = ("el_nino", "la_nina", "neutral")
PHASE_LABELS = {"el_nino": "El Niño", "la_nina": "La Niña", "neutral": "Neutral"}
STRENGTHS = ("weak", "moderate", "strong")
BRANCH_LABELS = {"main": "Main jet", "north": "Northern branch", "south": "Southern branch"}


# ----------------------------------------------------------------------------------------------- seasons


def season_label(start_year: int) -> str:
    return f"{start_year}/{str(start_year + 1)[2:]}"


def season_start(label: str) -> int:
    return int(label.split("/")[0])


def classify_seasons(rows: Sequence[tuple[str, int, float]], seasons: Iterable[str], enso: Any) -> list[dict]:
    """Phase and strength of each season ("1997/98") from the RONI rows (season code, year, value).

    An episode is a run of at least ``enso.episode_min_seasons`` consecutive three month values at or above
    ``+enso.threshold`` (El Niño) or at or below ``-enso.threshold`` (La Niña). A season takes the phase of the
    episode holding its ``enso.season_value`` row (DJF of the second year). Strength is the largest class of
    ``enso.strength`` whose floor the episode holds for at least ``enso.strength_min_seasons`` consecutive values
    (1 means its peak value alone decides). ``peak`` is the episode's largest absolute value either way.
    """
    phase_at: dict[int, tuple[str, float, Optional[str]]] = {}
    for sign, phase in ((1, "el_nino"), (-1, "la_nina")):
        run: list[int] = []
        for i, (_, _, value) in enumerate(list(rows) + [("", 0, 0.0)]):
            if sign * value >= enso.threshold and i < len(rows):
                run.append(i)
                continue
            if len(run) >= enso.episode_min_seasons:
                peak = max((rows[j][2] for j in run), key=abs)
                strength = episode_strength([sign * rows[j][2] for j in run], enso)
                for j in run:
                    phase_at[j] = (phase, peak, strength)
            run = []
    where = {(code, year): i for i, (code, year, _) in enumerate(rows)}
    out = []
    for label in seasons:
        start = season_start(label)
        # DJF belongs to the year of its January; the other three month codes follow the same rule except NDJ and OND.
        year = start + 1 if enso.season_value in ("DJF", "JFM", "FMA", "MAM") else start
        i = where.get((enso.season_value, year))
        if i is None:
            out.append({"season": label, "phase": None, "strength": None, "value": None, "peak": None})
            continue
        phase, peak, strength = phase_at.get(i, ("neutral", None, None))
        out.append({"season": label, "phase": phase, "strength": strength, "value": rows[i][2], "peak": peak})
    return out


def episode_strength(values: Sequence[float], enso: Any) -> str:
    """The largest strength class whose floor ``values`` (signed toward the phase, so positive) hold for at least
    ``enso.strength_min_seasons`` consecutive entries; the weakest class otherwise."""
    for name in reversed(STRENGTHS):
        floor, run = getattr(enso.strength, name), 0
        for v in values:
            run = run + 1 if v >= floor else 0
            if run >= enso.strength_min_seasons:
                return name
    return STRENGTHS[0]


# ----------------------------------------------------------------------------------------------- winds


@dataclass
class Grid:
    lats: list[float]
    lons: list[float]
    speed: list[list[float]]  # [lat][lon]

    def column(self, j: int) -> list[float]:
        return [row[j] for row in self.speed]


def composite(seasons: dict[str, dict], labels: Sequence[str], lats: list[float], lons: list[float]) -> Grid:
    """Speed of the vector mean wind over the given seasons."""
    if not labels:
        raise ValueError("a layer needs at least one season")
    nlat, nlon = len(lats), len(lons)
    su = [[0.0] * nlon for _ in range(nlat)]
    sv = [[0.0] * nlon for _ in range(nlat)]
    n = [[0] * nlon for _ in range(nlat)]
    for label in labels:
        s = seasons[label]
        for i in range(nlat):
            ru, rv = s["u"][i], s["v"][i]
            for j in range(nlon):
                if ru[j] is not None and rv[j] is not None:
                    su[i][j] += ru[j]
                    sv[i][j] += rv[j]
                    n[i][j] += 1
    speed = [[math.hypot(su[i][j] / n[i][j], sv[i][j] / n[i][j]) if n[i][j] else float("nan") for j in range(nlon)]
             for i in range(nlat)]
    return Grid(lats, lons, speed)


def find_peaks(values: Sequence[float], lats: Sequence[float], axis: Any) -> list[tuple[float, float]]:
    """(latitude, speed) of the jet peaks in one longitude column.

    A peak is a local maximum inside ``axis.lat_min..axis.lat_max`` with at least ``axis.min_speed_ms`` and a
    topographic prominence (height above the higher of the two lowest points between it and the next higher value
    on each side, or the column end) of at least ``axis.min_prominence_ms``. Its latitude is refined with a parabola
    through the three points around it.
    """
    out = []
    n = len(values)
    for i in range(1, n - 1):
        v = values[i]
        if not (lats[i] >= axis.lat_min and lats[i] <= axis.lat_max) or math.isnan(v):
            continue
        if not (v >= values[i - 1] and v > values[i + 1]) or v < axis.min_speed_ms:
            continue
        low_left = v
        k = i - 1
        while k >= 0 and values[k] <= v:
            low_left = min(low_left, values[k])
            k -= 1
        low_right = v
        k = i + 1
        while k < n and values[k] <= v:
            low_right = min(low_right, values[k])
            k += 1
        if v - max(low_left, low_right) < axis.min_prominence_ms:
            continue
        a, b, c = values[i - 1], v, values[i + 1]
        denom = a - 2 * b + c
        offset = 0.5 * (a - c) / denom if denom else 0.0
        step = lats[i + 1] - lats[i]
        out.append((lats[i] + max(-0.5, min(0.5, offset)) * step, b - 0.25 * (a - c) * offset))
    return out


def link_chains(peaks: Sequence[Sequence[tuple[float, float]]], lons: Sequence[float], axis: Any) -> list[list[tuple[float, float, float]]]:
    """Chains of (lon, lat, speed) linking peaks at neighboring longitudes that move less than axis.max_step_deg."""
    done: list[list[tuple[float, float, float]]] = []
    active: list[list[tuple[float, float, float]]] = []
    for j, column in enumerate(peaks):
        lon = lons[j]
        pairs = sorted(
            ((abs(chain[-1][1] - lat), ci, pi) for ci, chain in enumerate(active) for pi, (lat, _) in enumerate(column)
             if abs(chain[-1][1] - lat) <= axis.max_step_deg),
        )
        used_c, used_p, nxt = set(), set(), []
        for _, ci, pi in pairs:
            if ci in used_c or pi in used_p:
                continue
            used_c.add(ci)
            used_p.add(pi)
            lat, speed = column[pi]
            nxt.append(active[ci] + [(lon, lat, speed)])
        done.extend(chain for ci, chain in enumerate(active) if ci not in used_c)
        nxt.extend([(lon, lat, speed)] for pi, (lat, speed) in enumerate(column) if pi not in used_p)
        active = nxt
    done.extend(active)
    return [c for c in done if c[-1][0] - c[0][0] >= axis.min_length_deg]


def smooth(chain: Sequence[tuple[float, float, float]], window_deg: float) -> list[tuple[float, float, float]]:
    """Running mean of latitude and speed over window_deg of longitude (shorter at the chain ends)."""
    half = window_deg / 2
    out = []
    for lon, _, _ in chain:
        near = [(la, sp) for lo, la, sp in chain if abs(lo - lon) <= half]
        out.append((lon, sum(la for la, _ in near) / len(near), sum(sp for _, sp in near) / len(near)))
    return out


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    for k in range(1, len(xs)):
        if x <= xs[k]:
            t = (x - xs[k - 1]) / (xs[k] - xs[k - 1])
            return ys[k - 1] + t * (ys[k] - ys[k - 1])
    return ys[-1]


def band_edges(grid: Grid, chain: Sequence[tuple[float, float, float]], band: Any) -> list[tuple[float, float, float]]:
    """(lon, lat_south, lat_north): where speed falls to band.fraction of the axis speed, at most
    band.max_half_width_deg from the axis, in steps of 0.05 degree."""
    out = []
    for lon, lat, speed in chain:
        j = grid.lons.index(lon)
        col = grid.column(j)
        floor = band.fraction * speed
        edges = []
        for direction in (-1, 1):
            d = 0.0
            while d < band.max_half_width_deg - 1e-9:
                nd = min(d + 0.05, band.max_half_width_deg)
                if _interp(grid.lats, col, lat + direction * nd) < floor:
                    break
                d = nd
            edges.append(lat + direction * d)
        out.append((lon, edges[0], edges[1]))
    return out


def label_branches(chains: list[list[tuple[float, float, float]]]) -> list[tuple[str, list[tuple[float, float, float]]]]:
    """The chain with the most integrated speed is the main jet; the others are northern or southern branches by
    their mean latitude against the main jet where they overlap in longitude (else over their whole length)."""
    if not chains:
        return []
    main = max(chains, key=lambda c: sum(p[2] for p in c))
    main_lat = {p[0]: p[1] for p in main}
    main_mean = sum(main_lat.values()) / len(main_lat)
    out = [("main", main)]
    counts = {"north": 0, "south": 0}
    for chain in sorted((c for c in chains if c is not main), key=lambda c: c[0][0]):
        overlap = [(p[1], main_lat[p[0]]) for p in chain if p[0] in main_lat]
        diff = (sum(a - b for a, b in overlap) / len(overlap)) if overlap else (sum(p[1] for p in chain) / len(chain) - main_mean)
        side = "north" if diff >= 0 else "south"
        counts[side] += 1
        out.append((side if counts[side] == 1 else f"{side}_{counts[side]}", chain))
    return out


def jet_branches(grid: Grid, cfg: Any) -> list[dict]:
    peaks = [find_peaks(grid.column(j), grid.lats, cfg.axis) for j in range(len(grid.lons))]
    branches = []
    for bid, chain in label_branches(link_chains(peaks, grid.lons, cfg.axis)):
        chain = smooth(chain, cfg.axis.smooth_deg)
        base = bid.split("_")[0]
        branches.append({
            "id": bid,
            "label": BRANCH_LABELS[base],
            "axis": [[lon, round(lat, 2), round(sp, 1)] for lon, lat, sp in chain],
            "band": [[lon, round(s, 2), round(n, 2)] for lon, s, n in taper(chain, band_edges(grid, chain, cfg.band), cfg.band)],
        })
    return branches


def taper(chain: Sequence[tuple[float, float, float]], edges: Sequence[tuple[float, float, float]], band: Any) -> list[tuple[float, float, float]]:
    """Narrow the band toward each end of its chain: within band.taper_deg of longitude of an end, the half widths
    shrink linearly to band.taper_min_fraction at the end point, so a branch fades out instead of stopping square."""
    if band.taper_deg <= 0 or not chain:
        return list(edges)
    first, last = chain[0][0], chain[-1][0]
    out = []
    for (lon, lat, _), (_, south, north) in zip(chain, edges):
        to_end = min(lon - first, last - lon)
        f = 1.0 if to_end >= band.taper_deg else band.taper_min_fraction + (1 - band.taper_min_fraction) * to_end / band.taper_deg
        out.append((lon, lat - (lat - south) * f, lat + (north - lat) * f))
    return out


# ----------------------------------------------------------------------------------------------- overflies


def _edges_at(band: Sequence[Sequence[float]], lon: float) -> Optional[tuple[float, float]]:
    if not band or lon < band[0][0] or lon > band[-1][0]:
        return None
    xs = [b[0] for b in band]
    return _interp(xs, [b[1] for b in band], lon), _interp(xs, [b[2] for b in band], lon)


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for poly in polys:
        for x, y in poly[0]:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def overflies(branches: Sequence[dict], states: dict[str, dict], resorts: Sequence[dict], sample_deg: float) -> dict:
    """States with any sampled point under a band and resorts whose point lies under a band, west to east.

    ``states`` is {code: {"name", "geometry"}}; ``resorts`` are dicts with id, state, lat and lon.
    """
    boxes = {code: _bbox(s["geometry"]) for code, s in states.items()}
    first_lon: dict[str, float] = {}
    by_state_branch: dict[str, list[str]] = {}
    for br in branches:
        band = br["band"]
        if not band:
            continue
        lon = band[0][0]
        while lon <= band[-1][0] + 1e-9:
            edges = _edges_at(band, lon)
            if edges:
                lat = math.ceil(edges[0] / sample_deg) * sample_deg
                while lat <= edges[1] + 1e-9:
                    for code, (x0, y0, x1, y1) in boxes.items():
                        if x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_geometry(lon, lat, states[code]["geometry"]):
                            first_lon.setdefault(code, lon)
                            first_lon[code] = min(first_lon[code], lon)
                            if br["id"] not in by_state_branch.setdefault(code, []):
                                by_state_branch[code].append(br["id"])
                    lat += sample_deg
            lon += sample_deg
    out_resorts = []
    for r in sorted(resorts, key=lambda r: (r["lon"], r["id"])):
        for br in branches:
            edges = _edges_at(br["band"], r["lon"])
            if edges and edges[0] <= r["lat"] <= edges[1]:
                out_resorts.append({"id": r["id"], "state": r["state"], "branch": br["id"]})
                # A resort under the band puts its state under it too, even where the sample grid missed a sliver.
                if r["state"] in states:
                    first_lon[r["state"]] = min(first_lon.get(r["state"], r["lon"]), r["lon"])
                    if br["id"] not in by_state_branch.setdefault(r["state"], []):
                        by_state_branch[r["state"]].append(br["id"])
                break
    out_states = [{"code": c, "name": states[c]["name"], "branches": by_state_branch[c]}
                  for c in sorted(first_lon, key=lambda c: (first_lon[c], c))]
    return {"states": out_states, "resorts": out_resorts}


# ----------------------------------------------------------------------------------------------- strength split


def mean_shift(a: Sequence[dict], b: Sequence[dict], lon_west: float, lon_east: float) -> Optional[float]:
    """Mean absolute latitude difference of the two main axes over lon_west..lon_east where both exist."""
    def main_axis(branches: Sequence[dict]) -> dict[float, float]:
        for br in branches:
            if br["id"] == "main":
                return {p[0]: p[1] for p in br["axis"] if lon_west <= p[0] <= lon_east}
        return {}
    ma, mb = main_axis(a), main_axis(b)
    common = sorted(set(ma) & set(mb))
    if not common:
        return None
    return sum(abs(ma[x] - mb[x]) for x in common) / len(common)


def strength_groups(by_strength: dict[str, list[str]], branches_for, split: Any) -> tuple[list[list[str]], list[dict]]:
    """Group a phase's strength classes (weak, moderate, strong) into toggles.

    A class with fewer than ``split.min_seasons`` seasons first joins its weaker neighbor (or the stronger one when
    it is the weakest). Then, from weak to strong, each class joins the group before it unless their main axes differ
    by at least ``split.min_mean_shift_deg`` on average over ``split.lon_west..split.lon_east`` (owner rule: strength
    classes get their own toggles only when their paths differ enough). ``branches_for(seasons)`` gives the jet
    branches of a list of seasons. Returns the groups and the compared pairs.
    """
    units = [[s] for s in STRENGTHS if by_strength.get(s)]
    size = lambda u: sum(len(by_strength[s]) for s in u)  # noqa: E731
    while len(units) > 1:
        small = next((i for i, u in enumerate(units) if size(u) < split.min_seasons), None)
        if small is None:
            break
        into = small - 1 if small > 0 else 1
        lo, hi = sorted((small, into))
        units[lo:hi + 1] = [units[lo] + units[hi]]
    if not units:
        return [], []
    groups, pairs = [units[0]], []
    for unit in units[1:]:
        before = [x for s in groups[-1] for x in by_strength[s]]
        here = [x for s in unit for x in by_strength[s]]
        d = mean_shift(branches_for(before), branches_for(here), split.lon_west, split.lon_east)
        pairs.append({"a": "_".join(groups[-1]), "b": "_".join(unit), "shift_deg": None if d is None else round(d, 2)})
        if d is not None and d >= split.min_mean_shift_deg:
            groups.append(unit)
        else:
            groups[-1] = groups[-1] + unit
    return groups, pairs


# ----------------------------------------------------------------------------------------------- compile


@dataclass
class JetInputs:
    extract: dict
    era5_source: dict
    roni_rows: list[tuple[str, int, float]]
    enso_source: dict
    errors: list[str] = field(default_factory=list)


def load_inputs(raw_dir: Path) -> Optional[JetInputs]:
    """The four input files under raw_dir (data/raw), or None when any is missing."""
    era5 = raw_dir / ERA5_DIR
    enso = raw_dir / ENSO_DIR
    paths = [era5 / ERA5_EXTRACT, era5 / "source.json", enso / RONI_FILE, enso / "source.json"]
    if not all(p.is_file() for p in paths):
        return None
    return JetInputs(
        extract=json.loads(paths[0].read_text(encoding="utf-8")),
        era5_source=json.loads(paths[1].read_text(encoding="utf-8")),
        roni_rows=parse_index(paths[2].read_text(encoding="utf-8")),
        enso_source=json.loads(paths[3].read_text(encoding="utf-8")),
    )


def compile_jetstream(inputs: JetInputs, cfg: Any, states: dict[str, dict], resorts: Sequence[dict], header: dict) -> dict:
    ex = inputs.extract
    g = ex["grid"]
    lats = list(range(g["lat_min"], g["lat_max"] + 1, g["step_deg"]))
    lons = list(range(g["lon_min"], g["lon_max"] + 1, g["step_deg"]))
    by_label = {s["season"]: s for s in ex["seasons"]}
    labels = [s["season"] for s in ex["seasons"] if season_start(s["season"]) >= season_start(cfg.first_season)]
    classes = classify_seasons(inputs.roni_rows, labels, cfg.enso)
    winds = {"source_id": inputs.era5_source["source"]["id"], "as_of": ex["as_of"], "confidence": "published"}
    enso_ref = {"source_id": inputs.enso_source["source"]["id"], "as_of": inputs.enso_source["source"]["accessed_date"],
                "confidence": "published"}
    path_ref = {"source_id": winds["source_id"], "as_of": winds["as_of"], "confidence": "estimated"}

    def layer(lid: str, label: str, kind: str, phase: Optional[str], strength: Optional[str], seasons: list[str],
              branches: Optional[list[dict]] = None) -> dict:
        if branches is None:
            branches = jet_branches(composite(by_label, seasons, lats, lons), cfg)
        return {"id": lid, "label": label, "kind": kind, "phase": phase, "strength": strength,
                "color": cfg.colors.get(lid) or cfg.colors.get(phase or "", "#ffffff"), "seasons": seasons,
                "branches": branches, "overflies": overflies(branches, states, resorts, cfg.overflies.sample_deg),
                **path_ref}

    layers = [layer("last10", f"Last {cfg.baseline_seasons} winters", "baseline", None, None, labels[-cfg.baseline_seasons:])]
    split_info: dict[str, dict] = {}
    cache: dict[tuple[str, ...], list[dict]] = {}

    def branches_for(seasons: Sequence[str]) -> list[dict]:
        key = tuple(sorted(seasons))
        if key not in cache:
            cache[key] = jet_branches(composite(by_label, list(key), lats, lons), cfg)
        return cache[key]

    for phase in PHASES:
        members = [c["season"] for c in classes if c["phase"] == phase]
        if not members:
            continue
        if phase == "neutral":
            layers.append(layer(phase, PHASE_LABELS[phase], "enso", phase, None, members))
            continue
        by_strength = {s: [c["season"] for c in classes if c["phase"] == phase and c["strength"] == s] for s in STRENGTHS}
        groups, pairs = strength_groups(by_strength, branches_for, cfg.split)
        split_info[phase] = {
            "split": len(groups) > 1,
            "groups": groups,
            "shift_deg": max((p["shift_deg"] for p in pairs if p["shift_deg"] is not None), default=None),
            "pairs": pairs,
        }
        if len(groups) == 1:
            layers.append(layer(phase, PHASE_LABELS[phase], "enso", phase, None, members, branches_for(members)))
            continue
        for group in groups:
            seasons = [x for s in group for x in by_strength[s]]
            lid = f"{phase}_{'_'.join(group)}"
            label = f"{' and '.join(group).capitalize()} {PHASE_LABELS[phase]}"
            lay = layer(lid, label, "enso", phase, "_".join(group), sorted(seasons, key=season_start), branches_for(seasons))
            lay["color"] = cfg.colors.get(lid) or cfg.colors.get(f"{phase}_{group[-1]}") or cfg.colors.get(phase, "#ffffff")
            lay["strengths"] = group
            layers.append(lay)
    return {
        **header,
        "season_label": cfg.season_label,
        "level_hpa": 250,
        "sources": {"winds": winds, "enso": enso_ref},
        "layers": layers,
        "seasons": [{**{k: c[k] for k in ("season", "phase", "strength")}, "value": c["value"], "peak": c["peak"],
                     **enso_ref} for c in classes],
        "split": split_info,
        "method": cfg.method,
    }


def write(out_dir: Path, data: dict, write_json) -> None:
    write_json(out_dir / OUT_NAME, data, compact=True)
