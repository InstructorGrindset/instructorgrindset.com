"""Snowfall heat rasters for the Career Map (docs/map/ARCHITECTURE.md, "Heat maps").

Background: NOAA NCEI U.S. Climate Normals 1991 to 2020 station values (ANN-SNOW-NORMAL, inches), from
the committed extract that ``pipeline/climate/noaa_normals.py`` writes. Pockets: each resort's own
published annual snowfall (``snow.annual_avg_in``) raised over the background within
``heat.snowfall.pocket_radius_km``. A pocket only ever raises the field.

Method (config/definitions.yaml ``heat``):

* Interpolation: adaptive modified Shepard. At a point, take the ``neighbors`` nearest stations and the
  distance R to the next one; weight w_i = ((R - d_i) / (R * max(d_i, min_distance_km)))^2. A station's
  weight reaches zero just as it leaves the set, so the surface is continuous. The field is solved on
  every ``grid_step_px`` pixel and blended bilinearly between.
* Pockets: v = b + g(d) * max(0, resort - b), g(d) = (1 - (d / r)^2)^2 for d < r, the highest pocket wins.
* Color: ``heat.ramp`` over the mode's scale (``heat.modes.snowfall``, square root from 0).
* Opacity: from the distance to the nearest station or resort (``heat.field``), times the share of the
  pixel inside the state outlines (antialiased scanline coverage).

Output (``data/build/heat/``): ``snowfall-lower48.png`` and ``snowfall-alaska.png`` (RGBA, rows evenly
spaced in Web Mercator, as MapLibre image sources expect) and ``snowfall.json`` with each image's exact
corner coordinates, the scale, the legend ticks and the sources. Stdlib only (``pipeline/png.py``).
The JavaScript twin of the interpolation, for the resort only surfaces, is ``map/js/heat.js``.
"""
from __future__ import annotations

import bisect
import hashlib
import heapq
import itertools
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pipeline import png

KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON = 111.32
RAD = math.pi / 180
MANIFEST_VERSION = 1
# States and territories each region draws (the map hides Hawaii, DC and the territories).
HIDDEN_CODES = {"AS", "GU", "MP", "PR", "VI", "HI", "DC"}
REGION_STATES = {"lower48": lambda code: code not in HIDDEN_CODES and code != "AK", "alaska": lambda code: code == "AK"}


@dataclass(frozen=True)
class Point:
    lon: float
    lat: float
    value: float


# ------------------------------------------------------------------ Web Mercator grid
def merc_y(lat: float) -> float:
    """Web Mercator y (radians) of a latitude in degrees."""
    return math.log(math.tan(math.pi / 4 + lat * RAD / 2))


def lat_of(y: float) -> float:
    return (2 * math.atan(math.exp(y)) - math.pi / 2) / RAD


@dataclass(frozen=True)
class Grid:
    """Pixel grid of one region: columns evenly spaced in longitude, rows evenly spaced in Web Mercator y."""

    west: float
    south: float
    east: float
    north: float
    width: int
    height: int

    @classmethod
    def for_box(cls, west: float, south: float, east: float, north: float, px_per_degree: float,
                max_pixels: Optional[int] = None) -> "Grid":
        width = max(2, round((east - west) * px_per_degree))
        height = max(2, round((merc_y(north) - merc_y(south)) / RAD * px_per_degree))
        if max_pixels and width * height > max_pixels:
            k = math.sqrt(max_pixels / (width * height))
            width, height = max(2, round(width * k)), max(2, round(height * k))
        return cls(west, south, east, north, width, height)

    @property
    def y_north(self) -> float:
        return merc_y(self.north)

    @property
    def y_south(self) -> float:
        return merc_y(self.south)

    def lon_at(self, i: float) -> float:
        """Longitude of the center of pixel column i."""
        return self.west + (i + 0.5) / self.width * (self.east - self.west)

    def lat_at(self, j: float) -> float:
        """Latitude of the center of pixel row j (row 0 is the top)."""
        return lat_of(self.y_north - (j + 0.5) / self.height * (self.y_north - self.y_south))

    def x_of(self, lon: float) -> float:
        """Continuous pixel x (0 at the west edge, width at the east edge)."""
        return (lon - self.west) / (self.east - self.west) * self.width

    def y_of(self, lat: float) -> float:
        """Continuous pixel y (0 at the north edge, height at the south edge)."""
        lat = max(-85.0, min(85.0, lat))
        return (self.y_north - merc_y(lat)) / (self.y_north - self.y_south) * self.height

    def coordinates(self) -> list[list[float]]:
        """MapLibre image source corners: top left, top right, bottom right, bottom left."""
        return [[self.west, self.north], [self.east, self.north], [self.east, self.south], [self.west, self.south]]


def node_positions(n: int, step: int) -> list[int]:
    """Pixel indices where the field is solved: every ``step`` pixels plus the last one."""
    out = list(range(0, n, step))
    if out[-1] != n - 1:
        out.append(n - 1)
    return out


# ------------------------------------------------------------------ interpolation
def normalize_lon(lon: float, lat: float) -> float:
    """The Aleutians west of 180 degrees wrap to negative longitudes, as the map draws them."""
    return lon - 360 if lon > 0 and lat > 45 else lon


def decluster(points: Sequence[Point], km: float) -> list[Point]:
    """Greedy merge of points within ``km`` of a group's first point: mean place, mean value.

    Deterministic (north to south, then west to east). Small inputs only (resorts): quadratic.
    """
    if km <= 0 or len(points) < 2:
        return list(points)
    groups: list[list[float]] = []  # seed lon, seed lat, sum lon, sum lat, sum value, count
    for p in sorted(points, key=lambda q: (-q.lat, q.lon, q.value)):
        kmlon = KM_PER_DEG_LON * math.cos(p.lat * RAD)
        for g in groups:
            if math.hypot((g[0] - p.lon) * kmlon, (g[1] - p.lat) * KM_PER_DEG_LAT) <= km:
                g[2] += p.lon; g[3] += p.lat; g[4] += p.value; g[5] += 1
                break
        else:
            groups.append([p.lon, p.lat, p.lon, p.lat, p.value, 1])
    return [Point(g[2] / g[5], g[3] / g[5], g[4] / g[5]) for g in groups]


class PointIndex:
    """Buckets of points on a grid of ``cell_deg`` degrees, for nearest neighbor queries."""

    def __init__(self, points: Iterable[Point], cell_deg: float = 1.0):
        self.cell = cell_deg
        self.buckets: dict[tuple[int, int], list[Point]] = defaultdict(list)
        for p in points:
            self.buckets[(math.floor(p.lon / cell_deg), math.floor(p.lat / cell_deg))].append(p)
        self.count = sum(len(v) for v in self.buckets.values())
        keys = list(self.buckets)
        self.ix = (min(k[0] for k in keys), max(k[0] for k in keys)) if keys else (0, 0)
        self.iy = (min(k[1] for k in keys), max(k[1] for k in keys)) if keys else (0, 0)

    def nearest(self, lon: float, lat: float, k: int) -> list[tuple[float, float]]:
        """The ``k`` nearest points as [(distance km, value)], nearest first (fewer when there are fewer)."""
        if not self.count:
            return []
        k = min(k, self.count)
        kmlon = KM_PER_DEG_LON * math.cos(lat * RAD)
        cx, cy = math.floor(lon / self.cell), math.floor(lat / self.cell)
        max_ring = max(abs(cx - self.ix[0]), abs(cx - self.ix[1]), abs(cy - self.iy[0]), abs(cy - self.iy[1]))
        step_km = self.cell * min(kmlon, KM_PER_DEG_LAT)
        heap: list[tuple[float, float]] = []  # (-distance, value), the k best so far
        ring = 0
        while True:
            if ring == 0:
                cells = [(cx, cy)]
            else:
                cells = [(cx + dx, cy + dy) for dx in range(-ring, ring + 1) for dy in (-ring, ring)]
                cells += [(cx + dx, cy + dy) for dx in (-ring, ring) for dy in range(-ring + 1, ring)]
            for c in cells:
                for p in self.buckets.get(c, ()):
                    d = math.hypot((p.lon - lon) * kmlon, (p.lat - lat) * KM_PER_DEG_LAT)
                    if len(heap) < k:
                        heapq.heappush(heap, (-d, p.value))
                    elif d < -heap[0][0]:
                        heapq.heapreplace(heap, (-d, p.value))
            # Every point outside this ring is at least ring * cell degrees away along one axis.
            if (len(heap) == k and ring * step_km >= -heap[0][0]) or ring > max_ring:
                break
            ring += 1
        return sorted(((-nd, v) for nd, v in heap), key=lambda t: t[0])


def shepard(nbrs: Sequence[tuple[float, float]], k: int, min_km: float) -> Optional[float]:
    """Adaptive modified Shepard value from up to k + 1 nearest [(distance, value)], nearest first."""
    if not nbrs:
        return None
    if len(nbrs) <= k:
        # Fewer than k + 1 points in all: plain inverse distance weighting over every point.
        pairs = [(1 / max(d, min_km) ** 2, v) for d, v in nbrs]
    else:
        r = nbrs[k][0]
        pairs = [(((r - d) / (r * max(d, min_km))) ** 2 if r > d else 0.0, v) for d, v in nbrs[:k]]
    sw = sum(w for w, _ in pairs)
    if sw <= 0:
        return nbrs[0][1]
    return sum(w * v for w, v in pairs) / sw


def solve_nodes(grid: Grid, cols: Sequence[int], rows: Sequence[int], index: PointIndex, k: int, min_km: float,
                needed: Optional[Sequence[bool]] = None) -> tuple[list, list]:
    """Field value and nearest distance (km) at the needed nodes, as flat lists indexed r * len(cols) + c
    (None where a node is not needed)."""
    n = len(cols) * len(rows)
    values: list[Optional[float]] = [None] * n
    dists: list[Optional[float]] = [None] * n
    for r, j in enumerate(rows):
        lat = grid.lat_at(j)
        for c, i in enumerate(cols):
            q = r * len(cols) + c
            if needed is not None and not needed[q]:
                continue
            nbrs = index.nearest(grid.lon_at(i), lat, k + 1)
            v = shepard(nbrs, k, min_km)
            if v is None:
                continue
            values[q] = v
            dists[q] = nbrs[0][0]
    return values, dists


def _cell_of(positions: Sequence[int], n: int) -> tuple[list[int], list[float]]:
    """Per pixel: the node interval it falls in and its fraction along that interval."""
    cell, frac = [], []
    for i in range(n):
        c = min(bisect.bisect_right(positions, i) - 1, len(positions) - 2)
        a, b = positions[c], positions[c + 1]
        cell.append(c)
        frac.append((i - a) / (b - a))
    return cell, frac


def upsample(grid: Grid, cols: Sequence[int], rows: Sequence[int], nodes: Sequence[Optional[float]],
             covered: Sequence[float]) -> list:
    """Bilinear blend of node values (flat, r * len(cols) + c) to every covered pixel (None elsewhere)."""
    cx, fx = _cell_of(cols, grid.width)
    cy, fy = _cell_of(rows, grid.height)
    nc = len(cols)
    w = grid.width
    out: list[Optional[float]] = [None] * (w * grid.height)
    for j in range(grid.height):
        top_row = cy[j] * nc
        bot_row = top_row + nc
        ty = fy[j]
        base = j * w
        for i in range(w):
            if covered[base + i] <= 0:
                continue
            c = cx[i]
            a = nodes[top_row + c]; b = nodes[top_row + c + 1]; d = nodes[bot_row + c]; e = nodes[bot_row + c + 1]
            if a is None or b is None or d is None or e is None:
                continue
            tx = fx[i]
            top = a + (b - a) * tx
            out[base + i] = top + (d + (e - d) * tx - top) * ty
    return out


def needed_nodes(grid: Grid, cols: Sequence[int], rows: Sequence[int], covered: Sequence[float]) -> list[bool]:
    """Flat flags (r * len(cols) + c): the corners of every node cell that holds a covered pixel."""
    cy, _ = _cell_of(rows, grid.height)
    nc = len(cols)
    need = [False] * (nc * len(rows))
    w = grid.width
    for j in range(grid.height):
        r = cy[j]
        row = covered[j * w:(j + 1) * w]
        for c in range(nc - 1):
            if any(row[cols[c]:cols[c + 1] + 1]):
                q = r * nc + c
                need[q] = need[q + 1] = need[q + nc] = need[q + nc + 1] = True
    return need


def apply_pockets(grid: Grid, background: Sequence[Optional[float]], pockets: Iterable[Point],
                  radius_km: float) -> tuple[list[Optional[float]], dict[int, float]]:
    """Raise the background toward each resort's value within ``radius_km``; never lower it.

    v = b + g(d) * max(0, resort - b), g(d) = (1 - (d / r)^2)^2; overlapping pockets take the highest.
    Returns (values, {pixel index: distance km to the nearest pocket center} inside any pocket).
    """
    out = list(background)
    near: dict[int, float] = {}
    w, h = grid.width, grid.height
    for p in pockets:
        dlat = radius_km / KM_PER_DEG_LAT
        j0 = max(0, math.floor(grid.y_of(p.lat + dlat)))
        j1 = min(h - 1, math.ceil(grid.y_of(p.lat - dlat)))
        for j in range(j0, j1 + 1):
            lat = grid.lat_at(j)
            kmlon = KM_PER_DEG_LON * math.cos(lat * RAD)
            dlon = radius_km / max(kmlon, 1e-6)
            i0 = max(0, math.floor(grid.x_of(p.lon - dlon)))
            i1 = min(w - 1, math.ceil(grid.x_of(p.lon + dlon)))
            dy = (p.lat - lat) * KM_PER_DEG_LAT
            for i in range(i0, i1 + 1):
                d = math.hypot((p.lon - grid.lon_at(i)) * kmlon, dy)
                if d >= radius_km:
                    continue
                idx = j * w + i
                if idx not in near or d < near[idx]:
                    near[idx] = d
                b = background[idx]
                if b is None or p.value <= b:
                    continue
                q = d / radius_km
                cand = b + (1 - q * q) ** 2 * (p.value - b)
                if cand > out[idx]:
                    out[idx] = cand
    return out, near


# ------------------------------------------------------------------ outlines
def _rings(geometry: dict, wrap_west: bool) -> list[list[list[tuple[float, float]]]]:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    out = []
    for poly in polys:
        out.append([[(lon - 360 if wrap_west and lon > 0 else lon, lat) for lon, lat in ring] for ring in poly])
    return out


def coverage(grid: Grid, polygons: Iterable[list[list[tuple[float, float]]]], subrows: int = 4) -> list[float]:
    """Share of each pixel inside the polygons (0 to 1), even odd per polygon, antialiased.

    Each pixel row is sampled at ``subrows`` scanlines; along a scanline the covered span is exact.
    """
    w, h = grid.width, grid.height
    part = [0.0] * (w * h)
    diff = [0.0] * ((w + 1) * h)
    wt = 1.0 / subrows
    for poly in polygons:
        edges = []
        for ring in poly:
            pts = [(grid.x_of(lon), grid.y_of(lat)) for lon, lat in ring]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if y0 == y1:
                    continue
                if y0 > y1:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0)))
        if not edges:
            continue
        edges.sort()
        ymin = max(0.0, edges[0][0])
        ymax = min(float(h), max(e[1] for e in edges))
        active: list = []
        nxt = 0
        s0 = max(0, math.floor(ymin * subrows))
        for s in range(s0, math.ceil(ymax * subrows)):
            y = (s + 0.5) / subrows
            j = s // subrows
            if j >= h:
                break
            while nxt < len(edges) and edges[nxt][0] <= y:
                active.append(edges[nxt]); nxt += 1
            active = [e for e in active if e[1] > y]
            xs = sorted(e[2] + (y - e[0]) * e[3] for e in active if e[0] <= y)
            for xa, xb in zip(xs[0::2], xs[1::2]):
                xa, xb = max(0.0, xa), min(float(w), xb)
                if xb <= xa:
                    continue
                ia, ib = int(xa), int(xb)
                if ia == ib:
                    part[j * w + ia] += (xb - xa) * wt
                    continue
                part[j * w + ia] += (ia + 1 - xa) * wt
                if ib < w:
                    part[j * w + ib] += (xb - ib) * wt
                diff[j * (w + 1) + ia + 1] += wt
                diff[j * (w + 1) + ib] -= wt
    out = [0.0] * (w * h)
    for j in range(h):
        run = itertools.accumulate(diff[j * (w + 1):j * (w + 1) + w])
        base = j * w
        for i, full in enumerate(run):
            v = full + part[base + i]
            if v > 1e-6:
                out[base + i] = 1.0 if v > 1 else v
    return out


def region_polygons(outlines: dict, region: str) -> list:
    keep = REGION_STATES[region]
    polys = []
    for f in outlines.get("features", []):
        code = str((f.get("properties") or {}).get("code", "")).upper()
        g = f.get("geometry")
        if keep(code) and g and g.get("type") in ("Polygon", "MultiPolygon"):
            polys += _rings(g, wrap_west=region == "alaska")
    return polys


# ------------------------------------------------------------------ color
def hex_rgb(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def ramp_rgb(t: float, colors: Sequence[str]) -> tuple[int, int, int]:
    """Color at t in [0, 1], linear between the ramp stops in sRGB (the CSS gradient does the same)."""
    rgb = [hex_rgb(c) for c in colors]
    x = min(1.0, max(0.0, t)) * (len(rgb) - 1)
    i = min(len(rgb) - 2, int(x))
    f = x - i
    return tuple(round(a + (b - a) * f) for a, b in zip(rgb[i], rgb[i + 1]))  # type: ignore[return-value]


def scale_t(v: float, scale: str, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.5
    t = min(1.0, max(0.0, (v - lo) / (hi - lo)))
    return math.sqrt(t) if scale == "sqrt" else t


def nice_ceil(x: float) -> float:
    """Smallest of 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8 or 10 times a power of ten that is at least x."""
    if x <= 0:
        return 1.0
    m = 10 ** math.floor(math.log10(x))
    for f in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10):
        if f * m >= x - 1e-9:
            return f * m
    return 10 * m


def sqrt_ticks(candidates: Sequence[float], lo: float, hi: float, min_gap: float = 0.12) -> list[float]:
    """Candidate ticks inside the domain and clear of the top end, then the domain maximum."""
    out = [t for t in candidates if lo <= t < hi and 1 - scale_t(t, "sqrt", lo, hi) >= min_gap]
    return out + [hi]


def opacity(d_km: float, full_km: float, fade_end_km: float, max_alpha: float, floor_alpha: float) -> float:
    """max_alpha within full_km of data, a smooth fade to floor_alpha at fade_end_km, floor_alpha beyond."""
    if d_km <= full_km:
        return max_alpha
    if d_km >= fade_end_km:
        return floor_alpha
    t = (d_km - full_km) / (fade_end_km - full_km)
    return max_alpha - (max_alpha - floor_alpha) * t * t * (3 - 2 * t)


# ------------------------------------------------------------------ raster
def index_for(points: Sequence[Point], box: Any, k: int) -> PointIndex:
    """Bucket index over the points near a region, cells sized so a cell holds about k points."""
    margin_lat, margin_lon = 6.0, 12.0
    near = [p for p in points if box.west - margin_lon <= p.lon <= box.east + margin_lon
            and box.south - margin_lat <= p.lat <= box.north + margin_lat]
    pts = near if len(near) > k else list(points)
    area = (box.east - box.west + 2 * margin_lon) * (box.north - box.south + 2 * margin_lat)
    cell = max(0.5, min(10.0, math.sqrt(area * k / max(1, len(pts))) / 2))
    return PointIndex(pts, cell_deg=cell)


def render_region(grid: Grid, *, stations: Sequence[Point], pockets: Sequence[Point], polygons: list,
                  heat: Any, domain: tuple[float, float], scale: str, box: Any) -> tuple[bytes, dict]:
    """RGBA bytes of one region and a summary (covered pixels, pockets drawn)."""
    snow, field = heat.snowfall, heat.field
    cov = coverage(grid, polygons)
    cols = node_positions(grid.width, snow.grid_step_px)
    rows = node_positions(grid.height, snow.grid_step_px)
    index = index_for(stations, box, snow.neighbors)
    need = needed_nodes(grid, cols, rows, cov)
    node_v, node_d = solve_nodes(grid, cols, rows, index, snow.neighbors, snow.min_distance_km, need)
    background = upsample(grid, cols, rows, node_v, cov)
    dist = upsample(grid, cols, rows, node_d, cov)
    values, near = apply_pockets(grid, background, pockets, snow.pocket_radius_km)
    lo, hi = domain
    span = hi - lo if hi > lo else 1.0
    lut_n = 1024
    lut = [bytes(ramp_rgb(i / (lut_n - 1), heat.ramp.colors)) for i in range(lut_n)]
    use_sqrt = scale == "sqrt"
    full, fade_end, amax, afloor = field.full_opacity_km, field.fade_end_km, field.max_alpha, field.floor_alpha
    buf = bytearray(grid.width * grid.height * 4)
    covered = 0
    for k, c in enumerate(cov):
        if c <= 0:
            continue
        v = values[k]
        if v is None:
            continue
        covered += 1
        t = (v - lo) / span
        t = 0.0 if t < 0 else 1.0 if t > 1 else t
        if use_sqrt:
            t = math.sqrt(t)
        d = dist[k]
        if d is None:
            d = math.inf
        pd = near.get(k)
        if pd is not None and pd < d:
            d = pd
        a = amax if d <= full else opacity(d, full, fade_end, amax, afloor)
        buf[4 * k:4 * k + 3] = lut[int(t * (lut_n - 1) + 0.5)]
        buf[4 * k + 3] = round(a * c * 255)
    drawn = sum(1 for p in pockets if 0 <= grid.x_of(p.lon) < grid.width and 0 <= grid.y_of(p.lat) < grid.height)
    return bytes(buf), {"covered_pixels": covered, "pockets": drawn}


def inputs_digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, separators=(",", ":"), default=str).encode())
    return h.hexdigest()


def resort_pockets(resorts: Iterable[dict]) -> list[Point]:
    """Resorts with a sourced annual snowfall (``snow.annual_avg_in``) as pocket points."""
    out = []
    for r in resorts:
        s = (r.get("snow") or {}).get("annual_avg_in")
        if s and isinstance(s.get("value"), (int, float)) and r.get("lat") is not None and r.get("lon") is not None:
            out.append(Point(float(r["lon"]), float(r["lat"]), float(s["value"])))
    return out


def write_snowfall(out_dir: Path, *, stations: Sequence[Point], pockets: Sequence[Point], outlines: dict, heat: Any,
                   source: dict, generated_at: str, as_of: str) -> dict:
    """Write ``heat/snowfall-<region>.png`` and ``heat/snowfall.json``; skip the work when inputs are unchanged.

    ``stations`` are the usable NOAA stations; ``source`` is the NOAA source record (``id`` used).
    Returns the manifest.
    """
    heat_dir = out_dir / "heat"
    mode = heat.modes.snowfall
    regions = {"lower48": heat.regions.lower48, "alaska": heat.regions.alaska}
    stations = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value) for p in stations]
    pockets = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value) for p in pockets]

    def inside(p: Point, box: Any) -> bool:
        return box.west <= p.lon <= box.east and box.south <= p.lat <= box.north

    lo = mode.domain[0] if mode.domain else (mode.domain_min if mode.domain_min is not None else 0.0)
    vals = [p.value for p in stations if any(inside(p, b) for b in regions.values())] + [p.value for p in pockets]
    hi = mode.domain[1] if mode.domain else nice_ceil(max(vals) if vals else 1.0)
    ticks = list(mode.ticks or []) if mode.domain else (sqrt_ticks(mode.ticks or [], lo, hi) if mode.scale == "sqrt"
                                                        else [lo, hi])
    digest = inputs_digest([(p.lon, p.lat, p.value) for p in stations], [(p.lon, p.lat, p.value) for p in pockets],
                           outlines.get("features", []), heat.model_dump(mode="json"), MANIFEST_VERSION,
                           # the method itself: a code change rebuilds the rasters too
                           hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           hashlib.sha256(Path(png.__file__).read_bytes()).hexdigest())
    manifest_path = heat_dir / "snowfall.json"
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if old and old.get("inputs_sha256") == digest and all((heat_dir / r["image"]).is_file() for r in old.get("rasters", [])):
        return old

    rasters = []
    for name, box in regions.items():
        polys = region_polygons(outlines, name)
        if not polys:
            continue
        grid = Grid.for_box(box.west, box.south, box.east, box.north, heat.snowfall.px_per_degree)
        rgba, info = render_region(grid, stations=stations, pockets=pockets, polygons=polys, heat=heat,
                                   domain=(lo, hi), scale=mode.scale, box=box)
        if not info["covered_pixels"]:
            continue
        data = png.encode_rgba(grid.width, grid.height, rgba)
        image = f"snowfall-{name}.png"
        heat_dir.mkdir(parents=True, exist_ok=True)
        path = heat_dir / image
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
        rasters.append({"region": name, "image": image, "width": grid.width, "height": grid.height,
                        "coordinates": grid.coordinates(), "bytes": len(data),
                        "stations": sum(1 for p in stations if inside(p, box)), "pockets": info["pockets"]})
    for stale in heat_dir.glob("snowfall-*.png") if heat_dir.is_dir() else []:
        if stale.name not in {r["image"] for r in rasters}:
            stale.unlink()
    snow = heat.snowfall
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "mode": "snowfall",
        "units": "in",
        "scale": mode.scale,
        "domain": {"min": lo, "max": hi},
        "ticks": ticks,
        "ramp": list(heat.ramp.colors),
        "background": {"source_id": source["id"], "as_of": as_of, "confidence": "published",
                       "field": "ANN-SNOW-NORMAL", "stations": sum(r["stations"] for r in rasters)},
        # Each pocket value is a resort's own sourced snow.annual_avg_in in resorts.json (source there).
        "pockets": {"field": "snow.annual_avg_in", "resorts": len(pockets), "radius_km": snow.pocket_radius_km},
        "method": {"interpolation": "adaptive modified Shepard", "neighbors": snow.neighbors,
                   "min_distance_km": snow.min_distance_km, "grid_step_px": snow.grid_step_px,
                   "px_per_degree": snow.px_per_degree, "full_opacity_km": heat.field.full_opacity_km,
                   "fade_end_km": heat.field.fade_end_km, "floor_alpha": heat.field.floor_alpha},
        "attribution": "Snowfall normals: NOAA NCEI 1991 to 2020",
        "inputs_sha256": digest,
        "rasters": rasters,
    }
    heat_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
