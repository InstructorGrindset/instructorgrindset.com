"""Heat rasters for the Career Map (docs/map/ARCHITECTURE.md, "Heat maps"): snowfall and season length.

Snowfall background: NOAA NCEI U.S. Climate Normals 1991 to 2020 station values (ANN-SNOW-NORMAL, inches),
from the committed extract that ``pipeline/climate/noaa_normals.py`` writes. Pockets: each resort's own
published annual snowfall (``snow.annual_avg_in``) raised over the background within
``heat.snowfall.pocket_radius_km``. A pocket only ever raises the field.

Season length: the resorts' own sourced season lengths (``season.length_days``, else a sourced open and close
pair), the ``heat.field`` method with the ``heat.season`` resolution and fade.

Method (config/definitions.yaml ``heat``):

* Interpolation: adaptive modified Shepard. At a point, take the ``neighbors`` nearest points and the
  distance R to the next one; weight w_i = ((R - d_i) / (R * max(d_i, min_distance_km)))^2. A point's
  weight reaches zero just as it leaves the set, so the surface is continuous. The field is solved on
  every ``grid_step_px`` pixel and blended bilinearly between.
* Pockets (snowfall): v = b + g(d) * max(0, resort - b), g(d) = (1 - (d / r)^2)^2 for d < r, the highest wins.
* Color: classed modes take the color of the value's class (``breaks``, ``colors``); other modes use
  ``heat.ramp`` over the mode's scale.
* Opacity: from the distance to the nearest station or resort with data, times the share of the pixel inside
  the state outlines (antialiased scanline coverage). Season length also ends its colors within
  ``no_data_reach_km`` of the nearest resort inside states with no resort data.
* Isolines (snowfall): marching squares on the same pixel field, joined into lines, simplified with
  Douglas Peucker, written as small GeoJSON files.

Output (``data/build/heat/``): ``<mode>-lower48.png`` and ``<mode>-alaska.png`` (RGBA, rows evenly spaced in
Web Mercator, as MapLibre image sources expect), ``<mode>.json`` with each image's exact corner coordinates,
the classes, the legend ticks and the sources, and ``snowfall-isolines-<region>.geojson``. Stdlib only
(``pipeline/png.py``). The JavaScript twin of the interpolation, for the browser fallback, is ``map/js/heat.js``.
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
from typing import Any, Callable, Iterable, Optional, Sequence

from pipeline import png

KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON = 111.32
RAD = math.pi / 180
MANIFEST_VERSION = 2
# States and territories each region draws (the map hides Hawaii, DC and the territories).
HIDDEN_CODES = {"AS", "GU", "MP", "PR", "VI", "HI", "DC"}
REGION_STATES = {"lower48": lambda code: code not in HIDDEN_CODES and code != "AK", "alaska": lambda code: code == "AK"}


@dataclass(frozen=True)
class Point:
    lon: float
    lat: float
    value: float
    elev_m: Optional[float] = None   # station height (snowfall stations); None for resorts and season points


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

    def nearest_points(self, lon: float, lat: float, k: int) -> list[tuple[float, Point]]:
        """The ``k`` nearest points as [(distance km, point)], nearest first (as :meth:`nearest`)."""
        if not self.count:
            return []
        k = min(k, self.count)
        kmlon = KM_PER_DEG_LON * math.cos(lat * RAD)
        cx, cy = math.floor(lon / self.cell), math.floor(lat / self.cell)
        max_ring = max(abs(cx - self.ix[0]), abs(cx - self.ix[1]), abs(cy - self.iy[0]), abs(cy - self.iy[1]))
        step_km = self.cell * min(kmlon, KM_PER_DEG_LAT)
        heap: list[tuple[float, int, Point]] = []
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
                        heapq.heappush(heap, (-d, id(p), p))
                    elif d < -heap[0][0]:
                        heapq.heapreplace(heap, (-d, id(p), p))
            if (len(heap) == k and ring * step_km >= -heap[0][0]) or ring > max_ring:
                break
            ring += 1
        return sorted(((-nd, p) for nd, _, p in heap), key=lambda t: t[0])


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


# ------------------------------------------------------------------ elevation aware snowfall field
@dataclass(frozen=True)
class NodeFit:
    """The local snowfall model at one node, in u = ln(snowfall + c) (c = ``log_offset_in``):
    u(z) = a + beta * clamp(z, z_lo, z_hi), then clamped to [u_lo, u_hi]. z in km."""

    a: float
    beta: float
    z_lo: float
    z_hi: float
    u_lo: float
    u_hi: float
    dist_km: float


def gids_fit(nbrs: Sequence[tuple[float, Point]], k: int, min_km: float, c: float, max_gradient: float,
             spread_prior_km: float, extrapolation_km: float) -> Optional[NodeFit]:
    """Gradient plus inverse distance (after Nalder and Wein 1998) with a multiplicative height gradient, from up
    to k + 1 nearest [(distance km, station)], nearest first.

    The value weights w_i are the adaptive modified Shepard weights of :func:`shepard`. The local gradient beta of
    u = ln(v + c) with station height is a weighted least squares slope with the square roots of those weights
    (inverse distance with the same taper, so it changes smoothly between nodes), shrunk toward zero when the
    neighbours span little height: beta = Sxy / (Sxx + spread_prior^2), then clamped to [0, max_gradient]
    (snowfall never falls with height). Each station's value is moved to the pixel's height along that gradient
    and the moved values are averaged: v(z) = sum w_i ((v_i + c) exp(beta (z - z_i)) - c) = exp(a + beta z) - c
    with a = ln sum w_i (v_i + c) exp(-beta z_i). With beta 0 this is the plain Shepard value. The height a pixel
    is read at is clamped to the neighbours' range widened by ``extrapolation_km``, and u to the neighbours'
    range, so the field never goes above the highest or below the lowest neighbour.
    """
    if not nbrs:
        return None
    if len(nbrs) <= k:
        pairs = [(1 / max(d, min_km) ** 2, p) for d, p in nbrs]
    else:
        r = nbrs[k][0]
        pairs = [(((r - d) / (r * max(d, min_km))) ** 2 if r > d else 0.0, p) for d, p in nbrs[:k]]
    sw = sum(w for w, _ in pairs)
    if sw <= 0:
        pairs = [(1.0, nbrs[0][1])]
        sw = 1.0
    us = [math.log(max(p.value, 0.0) + c) for _, p in pairs]
    zs = [(p.elev_m or 0.0) / 1000.0 for _, p in pairs]
    ws = [w / sw for w, _ in pairs]
    zbar = sum(w * z for w, z in zip(ws, zs))
    beta = 0.0
    if all(p.elev_m is not None for _, p in pairs):
        rw = [math.sqrt(w) for w in ws]
        srw = sum(rw) or 1.0
        rw = [x / srw for x in rw]
        zr = sum(w * z for w, z in zip(rw, zs))
        ur = sum(w * u for w, u in zip(rw, us))
        sxx = sum(w * (z - zr) ** 2 for w, z in zip(rw, zs))
        sxy = sum(w * (z - zr) * (u - ur) for w, z, u in zip(rw, zs, us))
        beta = min(max_gradient, max(0.0, sxy / (sxx + spread_prior_km ** 2)))
    used = [u for (w, _), u in zip(pairs, us) if w > 0] or us
    zused = [z for (w, _), z in zip(pairs, zs) if w > 0] or zs
    a = math.log(sum(w * math.exp(u - beta * (z - zbar)) for w, u, z in zip(ws, us, zs))) - beta * zbar
    return NodeFit(a=a, beta=beta, z_lo=min(zused) - extrapolation_km,
                   z_hi=max(zused) + extrapolation_km, u_lo=min(used), u_hi=max(used), dist_km=nbrs[0][0])


def gids_value(fit: NodeFit, z_km: float, c: float) -> float:
    """Snowfall (in) of a node fit at height z (km)."""
    u = fit.a + fit.beta * min(fit.z_hi, max(fit.z_lo, z_km))
    return max(0.0, math.exp(min(fit.u_hi, max(fit.u_lo, u))) - c)


def terrain_heights(grid: Grid, dem: Any, cov: Sequence[float]) -> list[Optional[float]]:
    """Ground height (km) at every covered pixel center from the terrain grid (``pipeline.terrain.Dem``)."""
    w = grid.width
    out: list[Optional[float]] = [None] * (w * grid.height)
    lons = [grid.lon_at(i) for i in range(w)]
    for j in range(grid.height):
        lat = grid.lat_at(j)
        base = j * w
        for i in range(w):
            if cov[base + i] > 0:
                z = dem.sample(lons[i], lat)
                out[base + i] = None if z is None else z / 1000.0
    return out


def solve_elevation_field(grid: Grid, cov: Sequence[float], points: Sequence[Point], box: Any, k: int,
                          min_km: float, step: int, heights_km: Sequence[Optional[float]],
                          elev: Any) -> tuple[list, list, list]:
    """(value, distance to the nearest station km, local gradient per km) at every covered pixel.

    The node fits (:func:`gids_fit`) are solved on every ``step`` pixel; their six parameters blend bilinearly
    to each pixel, which then reads the model at its own ground height. A pixel without a height reads it at
    the neighbours' weighted mean height (plain interpolation)."""
    cols = node_positions(grid.width, step)
    rows = node_positions(grid.height, step)
    index = index_for(points, box, k)
    need = needed_nodes(grid, cols, rows, cov)
    nc = len(cols)
    c = elev.log_offset_in
    fits: list[Optional[NodeFit]] = [None] * (nc * len(rows))
    for r, j in enumerate(rows):
        lat = grid.lat_at(j)
        for ci, i in enumerate(cols):
            q = r * nc + ci
            if need[q]:
                fits[q] = gids_fit(index.nearest_points(grid.lon_at(i), lat, k + 1), k, min_km, c,
                                   elev.max_gradient_per_km, elev.spread_prior_m / 1000.0,
                                   elev.max_extrapolation_m / 1000.0)
    cx, fx = _cell_of(cols, grid.width)
    cy, fy = _cell_of(rows, grid.height)
    w = grid.width
    n = w * grid.height
    values: list[Optional[float]] = [None] * n
    dists: list[Optional[float]] = [None] * n
    betas: list[Optional[float]] = [None] * n
    exp = math.exp
    for j in range(grid.height):
        top_row = cy[j] * nc
        bot_row = top_row + nc
        ty = fy[j]
        base = j * w
        for i in range(w):
            if cov[base + i] <= 0:
                continue
            ci = cx[i]
            f00 = fits[top_row + ci]; f10 = fits[top_row + ci + 1]; f01 = fits[bot_row + ci]; f11 = fits[bot_row + ci + 1]
            if f00 is None or f10 is None or f01 is None or f11 is None:
                continue
            tx = fx[i]
            w00 = (1 - tx) * (1 - ty); w10 = tx * (1 - ty); w01 = (1 - tx) * ty; w11 = tx * ty
            a = w00 * f00.a + w10 * f10.a + w01 * f01.a + w11 * f11.a
            beta = w00 * f00.beta + w10 * f10.beta + w01 * f01.beta + w11 * f11.beta
            z_lo = w00 * f00.z_lo + w10 * f10.z_lo + w01 * f01.z_lo + w11 * f11.z_lo
            z_hi = w00 * f00.z_hi + w10 * f10.z_hi + w01 * f01.z_hi + w11 * f11.z_hi
            u_lo = w00 * f00.u_lo + w10 * f10.u_lo + w01 * f01.u_lo + w11 * f11.u_lo
            u_hi = w00 * f00.u_hi + w10 * f10.u_hi + w01 * f01.u_hi + w11 * f11.u_hi
            z = heights_km[base + i]
            if z is None:
                z = (z_lo + z_hi) / 2
            u = a + beta * (z_hi if z > z_hi else z_lo if z < z_lo else z)
            u = u_hi if u > u_hi else u_lo if u < u_lo else u
            v = exp(u) - c
            values[base + i] = v if v > 0 else 0.0
            dists[base + i] = w00 * f00.dist_km + w10 * f10.dist_km + w01 * f01.dist_km + w11 * f11.dist_km
            betas[base + i] = beta
    return values, dists, betas


@dataclass(frozen=True)
class PocketTerrain:
    """Terrain aware pockets: pixel ground heights (km) and local gradients (per km of u = ln(v + c))."""

    heights_km: Sequence[Optional[float]]
    gradients: Sequence[Optional[float]]
    log_offset_in: float


def pocket_target(value: float, resort_km: Optional[float], z_km: Optional[float], beta: Optional[float],
                  c: float) -> float:
    """The value a resort's pocket pulls a pixel toward: the resort's own value at or above the resort's ground
    height, and below it the value moved down the local gradient, (value + c) exp(beta (z - z_resort)) - c.
    Never above the resort's value."""
    if resort_km is None or z_km is None or not beta or z_km >= resort_km:
        return value
    return max(0.0, (value + c) * math.exp(beta * (z_km - resort_km)) - c)


def apply_pockets(grid: Grid, background: Sequence[Optional[float]], pockets: Iterable[Point],
                  radius_km: float, terrain: Optional[PocketTerrain] = None,
                  pocket_heights_km: Optional[Sequence[Optional[float]]] = None) -> tuple[list[Optional[float]], dict[int, float]]:
    """Raise the background toward each resort's value within ``radius_km``; never lower it.

    v = b + g(d) * max(0, T - b), g(d) = (1 - (d / r)^2)^2; overlapping pockets take the highest. T is the
    resort's value, or with ``terrain`` its :func:`pocket_target` at the pixel's height (``pocket_heights_km``
    holds each resort's ground height), so a pocket fills the slopes and stays out of the valleys below.
    Returns (values, {pixel index: distance km to the nearest pocket center} inside any pocket).
    """
    out = list(background)
    near: dict[int, float] = {}
    w, h = grid.width, grid.height
    pockets = list(pockets)
    for n, p in enumerate(pockets):
        p_km = pocket_heights_km[n] if terrain is not None and pocket_heights_km is not None else None
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
                target = p.value if p_km is None else pocket_target(
                    p.value, p_km, terrain.heights_km[idx], terrain.gradients[idx], terrain.log_offset_in)
                if target <= b:
                    continue
                q = d / radius_km
                cand = b + (1 - q * q) ** 2 * (target - b)
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


def region_polygons(outlines: dict, region: str, only: Optional[Iterable[str]] = None) -> list:
    """Polygons of the region's states, or of the states among ``only`` (state codes) when given."""
    keep = REGION_STATES[region]
    pick = None if only is None else {c.upper() for c in only}
    polys = []
    for f in outlines.get("features", []):
        code = str((f.get("properties") or {}).get("code", "")).upper()
        g = f.get("geometry")
        if keep(code) and (pick is None or code in pick) and g and g.get("type") in ("Polygon", "MultiPolygon"):
            polys += _rings(g, wrap_west=region == "alaska")
    return polys


def region_codes(outlines: dict, region: str) -> set[str]:
    """State codes drawn in the region."""
    keep = REGION_STATES[region]
    codes = {str((f.get("properties") or {}).get("code", "")).upper() for f in outlines.get("features", [])}
    return {c for c in codes if keep(c)}




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


def class_index(v: float, breaks: Sequence[float]) -> int:
    """Class of a value: 0 below breaks[0], i for breaks[i - 1] <= v < breaks[i], len(breaks) from the last break."""
    return bisect.bisect_right(breaks, v)


def colorizer(mode: Any, ramp: Sequence[str], domain: tuple[float, float]) -> Callable[[float], tuple[int, int, int]]:
    """Value to RGB for a heat mode: its class color when classed, else ``ramp`` on the mode's scale."""
    if mode.scale == "classed":
        cols = [hex_rgb(c) for c in mode.colors]
        breaks = list(mode.breaks)
        return lambda v: cols[bisect.bisect_right(breaks, v)]
    lo, hi = domain
    n = 1024
    lut = [ramp_rgb(i / (n - 1), ramp) for i in range(n)]
    scale = mode.scale
    return lambda v: lut[int(scale_t(v, scale, lo, hi) * (n - 1) + 0.5)]


def smoothstep(t: float) -> float:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def opacity(d_km: float, full_km: float, fade_end_km: float, max_alpha: float, floor_alpha: float) -> float:
    """max_alpha within full_km of data, a smooth fade to floor_alpha at fade_end_km, floor_alpha beyond."""
    if d_km <= full_km:
        return max_alpha
    if d_km >= fade_end_km:
        return floor_alpha
    return max_alpha - (max_alpha - floor_alpha) * smoothstep((d_km - full_km) / (fade_end_km - full_km))


def reach(d_km: float, reach_km: float) -> float:
    """Share of the color kept inside a state with no data, d_km past its border: a smooth fade from 1 at the
    border to 0 at reach_km."""
    if reach_km <= 0:
        return 0.0
    return 1.0 - smoothstep(d_km / reach_km)


def border_distance(grid: Grid, inside: Sequence[float], step: int, cov: Sequence[float]) -> list:
    """Distance (km) from each covered pixel to the nearest pixel mostly inside ``inside`` (coverage over 0.5).

    A chamfer distance transform on every ``step`` pixel (two passes, weights 1 and the square root of 2), blended
    bilinearly between. Web Mercator is conformal, so a pixel spans KM_PER_DEG_LON cos(lat) / px_per_degree km
    both ways at its latitude.
    """
    cols = node_positions(grid.width, step)
    rows = node_positions(grid.height, step)
    nc, nr = len(cols), len(rows)
    inf = float("inf")
    d = [0.0 if inside[j * grid.width + i] > 0.5 else inf for j in rows for i in cols]
    diag = math.sqrt(2)
    order = [(r, c) for r in range(nr) for c in range(nc)]
    for passes in (order, order[::-1]):
        sgn = 1 if passes is order else -1
        for r, c in passes:
            q = r * nc + c
            v = d[q]
            if v == 0:
                continue
            cp = c - sgn
            rp = r - sgn
            if 0 <= cp < nc:
                v = min(v, d[q - sgn] + 1)
            if 0 <= rp < nr:
                up = q - sgn * nc
                v = min(v, d[up] + 1)
                if 0 <= cp < nc:
                    v = min(v, d[up - sgn] + diag)
                cn = c + sgn
                if 0 <= cn < nc:
                    v = min(v, d[up + sgn] + diag)
            d[q] = v
    width_deg = (grid.east - grid.west) / grid.width
    node_km = []
    for r, j in enumerate(rows):
        km_px = KM_PER_DEG_LON * math.cos(grid.lat_at(j) * RAD) * width_deg * step
        node_km += [x * km_px if x != inf else 1e9 for x in d[r * nc:(r + 1) * nc]]
    return upsample(grid, cols, rows, node_km, cov)


@dataclass(frozen=True)
class Fade:
    """The opacity rule of one raster (see ``paint``)."""

    full_km: float
    end_km: float
    max_alpha: float
    floor_alpha: float
    fade_to: str            # "neutral": the color blends into ``neutral`` as it fades; "alpha": only opacity drops
    neutral: tuple[int, int, int]
    no_data_reach_km: Optional[float] = None


# Subsamples of a pixel for antialiased class edges: offsets from its center, in pixels.
_AA = ((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25))


def class_color_aa(values: Sequence[Optional[float]], k: int, width: int, height: int, breaks: Sequence[float],
                   colors: Sequence[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Class color of pixel k, antialiased: the mean of the class colors at four subsamples, each bilinear between
    the pixel centers around it. Pixels whose neighbours share their class keep the plain color."""
    v = values[k]
    ci = bisect.bisect_right(breaks, v)
    i, j = k % width, k // width
    same = True
    for q in (k - 1 if i > 0 else None, k + 1 if i < width - 1 else None,
              k - width if j > 0 else None, k + width if j < height - 1 else None):
        if q is not None and values[q] is not None and bisect.bisect_right(breaks, values[q]) != ci:
            same = False
            break
    if same:
        return colors[ci]
    r = g = b = 0
    for dx, dy in _AA:
        ni, nj = i + (1 if dx > 0 else -1), j + (1 if dy > 0 else -1)
        sv = v
        if 0 <= ni < width and 0 <= nj < height:
            vx, vy, vxy = values[j * width + ni], values[nj * width + i], values[nj * width + ni]
            if vx is not None and vy is not None and vxy is not None:
                tx, ty = abs(dx), abs(dy)
                sv = v * (1 - tx) * (1 - ty) + vx * tx * (1 - ty) + vy * (1 - tx) * ty + vxy * tx * ty
        cr, cg, cb = colors[bisect.bisect_right(breaks, sv)]
        r += cr; g += cg; b += cb
    return round(r / 4), round(g / 4), round(b / 4)


def paint(values: Sequence[Optional[float]], dist: Sequence[Optional[float]], cov: Sequence[float],
          color_of: Callable[[float], tuple[int, int, int]], fade: Fade,
          no_data: Optional[Sequence[float]] = None, border_km: Optional[Sequence[Optional[float]]] = None,
          classes: Optional[tuple[Sequence[float], Sequence[str], int, int]] = None) -> tuple[bytearray, int]:
    """RGBA pixels: the value's color, opacity from the distance to data, times the outline coverage.

    Beyond ``fade.full_km`` the opacity falls smoothly to ``fade.floor_alpha`` at ``fade.end_km`` (and the color
    blends into ``fade.neutral`` by the same share when ``fade_to`` is neutral). ``no_data`` is each pixel's share
    inside states with no data and ``border_km`` its distance past the border of the states with data: that share
    keeps its color only up to ``fade.no_data_reach_km`` past the border (``reach``). ``classes`` (breaks, colors,
    width, height) antialiases the class edges (``class_color_aa``) instead of calling ``color_of``.
    Returns (pixels, colored pixel count). Fully transparent pixels are zero, which keeps the PNG small.
    """
    buf = bytearray(len(cov) * 4)
    colored = 0
    span = fade.end_km - fade.full_km
    nr, ng, nb = fade.neutral
    if classes:
        breaks, hexes, width, height = classes
        class_rgb = [hex_rgb(x) for x in hexes]
    for k, c in enumerate(cov):
        if c <= 0:
            continue
        v = values[k]
        if v is None:
            continue
        d = dist[k]
        if d is None:
            d = math.inf
        if classes:
            r, g, b = class_color_aa(values, k, width, height, breaks, class_rgb)
        else:
            r, g, b = color_of(v)
        if d <= fade.full_km:
            a = fade.max_alpha
        else:
            f = smoothstep((d - fade.full_km) / span) if span > 0 else 1.0
            a = fade.max_alpha - (fade.max_alpha - fade.floor_alpha) * f
            if fade.fade_to == "neutral" and f > 0:
                r, g, b = round(r + (nr - r) * f), round(g + (ng - g) * f), round(b + (nb - b) * f)
        if no_data is not None and fade.no_data_reach_km is not None:
            nd = no_data[k]
            if nd > 0:
                bd = border_km[k] if border_km is not None and border_km[k] is not None else 0.0
                a *= (1 - nd) + nd * reach(bd, fade.no_data_reach_km)
        alpha = round(a * c * 255)
        if alpha <= 0:
            continue
        colored += 1
        buf[4 * k] = r; buf[4 * k + 1] = g; buf[4 * k + 2] = b; buf[4 * k + 3] = alpha
    return buf, colored


# ------------------------------------------------------------------ isolines
# Marching squares cases: corner bits top left 8, top right 4, bottom right 2, bottom left 1 (set when the
# corner is at or above the level). Edges: 0 top, 1 right, 2 bottom, 3 left. Saddles (5, 10) use the cell mean.
_CASES: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((3, 2),), 2: ((2, 1),), 3: ((3, 1),), 4: ((0, 1),), 6: ((0, 2),), 7: ((3, 0),), 8: ((3, 0),),
    9: ((0, 2),), 11: ((0, 1),), 12: ((3, 1),), 13: ((2, 1),), 14: ((3, 2),),
}
_SADDLE = {5: (((3, 0), (2, 1)), ((3, 2), (0, 1))), 10: (((0, 1), (3, 2)), ((3, 0), (2, 1)))}


def _march(cells: Iterable[tuple[int, int]], get: Callable[[int, int], Optional[float]], stride: int,
           levels: Sequence[float], scale: float = 1.0, offset: float = 0.5) -> dict[float, list[list[tuple[float, float]]]]:
    """Marching squares over the given cells of a node field, joined into lines.

    A cell (i, j) has corners (i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1); ``get`` gives a corner's value or None
    (the cell is skipped). ``stride`` exceeds every i (it numbers the shared edges). Points are returned as
    offset + (node position) / scale, with linear interpolation along the cell edges. Saddles use the cell mean.
    """
    levels = sorted(levels)
    segs: dict[float, list[tuple[int, int]]] = {lv: [] for lv in levels}
    pts: dict[float, dict[int, tuple[float, float]]] = {lv: {} for lv in levels}
    for i, j in cells:
        a = get(i, j); b = get(i + 1, j); c = get(i + 1, j + 1); d = get(i, j + 1)
        if a is None or b is None or c is None or d is None:
            continue
        lo = min(a, b, c, d); hi = max(a, b, c, d)
        first = bisect.bisect_right(levels, lo)
        last = bisect.bisect_right(levels, hi)
        if first == last:
            continue
        top, below = j * stride + i, (j + 1) * stride + i
        # Edge ids: top = horizontal edge (i, j), bottom = (i, j + 1); left = vertical (i, j), right = (i + 1, j).
        ids = (2 * top, 2 * (top + 1) + 1, 2 * below, 2 * top + 1)
        for lv in levels[first:last]:          # lo < lv <= hi: the level crosses this cell
            case = (8 if a >= lv else 0) | (4 if b >= lv else 0) | (2 if c >= lv else 0) | (1 if d >= lv else 0)
            if case in _SADDLE:
                pairs = _SADDLE[case][0 if (a + b + c + d) / 4 >= lv else 1]
            else:
                pairs = _CASES[case]
            book = pts[lv]
            for e0, e1 in pairs:
                for e in (e0, e1):
                    eid = ids[e]
                    if eid in book:
                        continue
                    if e == 0:
                        x, y = i + (lv - a) / (b - a), j
                    elif e == 2:
                        x, y = i + (lv - d) / (c - d), j + 1
                    elif e == 3:
                        x, y = i, j + (lv - a) / (d - a)
                    else:
                        x, y = i + 1, j + (lv - b) / (c - b)
                    book[eid] = (offset + x / scale, offset + y / scale)
                segs[lv].append((ids[e0], ids[e1]))
    return {lv: _join(segs[lv], pts[lv]) for lv in levels}


def contour_lines(values: Sequence[Optional[float]], width: int, height: int,
                  levels: Sequence[float]) -> dict[float, list[list[tuple[float, float]]]]:
    """Lines of equal value through a pixel field (flat, row major, None where undefined).

    Marching squares over cells whose four corners (pixel centers) all hold a value, then the segments joined
    into lines. Points are continuous pixel coordinates (pixel i's center is at x = i + 0.5). Closed lines repeat
    their first point at the end. Returns {level: [line]}.
    """
    cells = ((i, j) for j in range(height - 1) for i in range(width - 1))
    return _march(cells, lambda i, j: values[j * width + i], width, levels)


def refined_isolines(grid: Grid, background: Sequence[Optional[float]], values: Sequence[Optional[float]],
                     pockets: Sequence[Point], radius_km: float, levels: Sequence[float],
                     supersample: int, terrain: Optional[PocketTerrain] = None,
                     pocket_heights_km: Optional[Sequence[Optional[float]]] = None) -> dict[float, list[list[tuple[float, float]]]]:
    """Isolines of the snowfall field on a grid ``supersample`` times finer than the pixels, where they can occur.

    Active cells: pixel cells with a corner within one level step of the lowest level, and their neighbours. Inside
    them the background is bilinear between pixel centers (it varies slowly) and every pocket is evaluated exactly
    (``apply_pockets``), so pocket edges come out smooth instead of as pixel polygons. With ``terrain`` the pocket
    target reads the ground height and gradient bilinear between pixel centers too. At pixel centers the field
    equals the raster's. Points are continuous pixel coordinates, as ``contour_lines``.
    """
    levels = sorted(levels)
    w, h = grid.width, grid.height
    if not levels or w < 2 or h < 2:
        return {lv: [] for lv in levels}
    s = max(1, int(supersample))
    step = levels[1] - levels[0] if len(levels) > 1 else 0.0
    floor = levels[0] - step

    def corners(i: int, j: int) -> Optional[tuple[float, float, float, float]]:
        q = j * w + i
        a, b, c, d = values[q], values[q + 1], values[q + w + 1], values[q + w]
        if a is None or b is None or c is None or d is None:
            return None
        return a, b, c, d

    seeds = []
    for j in range(h - 1):
        for i in range(w - 1):
            q = j * w + i
            a, b, c, d = values[q], values[q + 1], values[q + w + 1], values[q + w]
            if a is None or b is None or c is None or d is None:
                continue
            if a >= floor or b >= floor or c >= floor or d >= floor:
                seeds.append((i, j))
    active = set()
    for i, j in seeds:
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                cell = (i + di, j + dj)
                if cell not in active and 0 <= cell[0] < w - 1 and 0 <= cell[1] < h - 1 and corners(*cell):
                    active.add(cell)
    # Pockets that can reach each active cell (the pixel box of the radius, one pixel wider).
    near: dict[tuple[int, int], list[tuple[Point, Optional[float]]]] = defaultdict(list)
    for n, p in enumerate(pockets):
        p_km = pocket_heights_km[n] if terrain is not None and pocket_heights_km is not None else None
        dlat = radius_km / KM_PER_DEG_LAT
        j0 = max(0, math.floor(grid.y_of(p.lat + dlat)) - 1)
        j1 = min(h - 2, math.ceil(grid.y_of(p.lat - dlat)) + 1)
        dlon = radius_km / max(KM_PER_DEG_LON * math.cos(min(abs(p.lat) + dlat, 89.0) * RAD), 1e-6)
        i0 = max(0, math.floor(grid.x_of(p.lon - dlon)) - 1)
        i1 = min(w - 2, math.ceil(grid.x_of(p.lon + dlon)) + 1)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if (i, j) in active:
                    near[(i, j)].append((p, p_km))
    stride = (w - 1) * s + 1
    yn, ys = grid.y_north, grid.y_south
    span_x = (grid.east - grid.west) / w
    field: dict[int, Optional[float]] = {}

    def get(fi: int, fj: int) -> Optional[float]:
        key = fj * stride + fi
        if key in field:
            return field[key]
        i, si = divmod(fi, s)
        j, sj = divmod(fj, s)
        if i == w - 1:
            i, si = w - 2, s
        if j == h - 1:
            j, sj = h - 2, s
        q = j * w + i
        b00, b10, b11, b01 = background[q], background[q + 1], background[q + w + 1], background[q + w]
        if b00 is None or b10 is None or b11 is None or b01 is None:
            field[key] = None
            return None
        tx, ty = si / s, sj / s
        top = b00 + (b10 - b00) * tx
        bg = top + (b01 + (b11 - b01) * tx - top) * ty
        v = bg
        cand_pockets = near.get((i, j))
        if cand_pockets:
            lon = grid.west + (0.5 + fi / s) * span_x
            lat = lat_of(yn - (0.5 + fj / s) / h * (yn - ys))
            kmlon = KM_PER_DEG_LON * math.cos(lat * RAD)
            z_here = beta_here = None
            if terrain is not None:
                z_here = _bilinear(terrain.heights_km, q, w, tx, ty)
                beta_here = _bilinear(terrain.gradients, q, w, tx, ty)
            for p, p_km in cand_pockets:
                if p.value <= bg:
                    continue
                dd = math.hypot((p.lon - lon) * kmlon, (p.lat - lat) * KM_PER_DEG_LAT)
                if dd >= radius_km:
                    continue
                target = p.value if p_km is None else pocket_target(p.value, p_km, z_here, beta_here,
                                                                    terrain.log_offset_in)
                if target <= bg:
                    continue
                t = dd / radius_km
                cand = bg + (1 - t * t) ** 2 * (target - bg)
                if cand > v:
                    v = cand
        field[key] = v
        return v

    cells = ((ci * s + si, cj * s + sj) for ci, cj in sorted(active, key=lambda c: (c[1], c[0]))
             for sj in range(s) for si in range(s))
    return _march(cells, get, stride, levels, scale=s)


def _bilinear(field: Sequence[Optional[float]], q: int, w: int, tx: float, ty: float) -> Optional[float]:
    """Bilinear value inside the pixel cell whose top left center is pixel q (None when a corner has none)."""
    a, b, c, d = field[q], field[q + 1], field[q + w], field[q + w + 1]
    if a is None or b is None or c is None or d is None:
        return None
    top = a + (b - a) * tx
    return top + (c + (d - c) * tx - top) * ty


def _join(segments: list[tuple[int, int]], book: dict[int, tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Chain segments that share an edge point into lines: open lines from their ends first, then loops."""
    at: dict[int, list[int]] = defaultdict(list)
    for n, (e0, e1) in enumerate(segments):
        at[e0].append(n)
        at[e1].append(n)
    used = [False] * len(segments)
    lines = []

    def walk(start_edge: int, seg: int) -> list[int]:
        chain = [start_edge]
        edge = start_edge
        while seg is not None and not used[seg]:
            used[seg] = True
            e0, e1 = segments[seg]
            edge = e1 if e0 == edge else e0
            chain.append(edge)
            seg = next((s for s in at[edge] if not used[s]), None)
        return chain

    for edge, owners in sorted(at.items()):
        if len(owners) == 1 and not used[owners[0]]:
            lines.append(walk(edge, owners[0]))
    for n, (e0, _) in enumerate(segments):
        if not used[n]:
            lines.append(walk(e0, n))
    return [[book[e] for e in chain] for chain in lines]


def simplify(line: Sequence[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas Peucker: drop points within ``tolerance`` of the simplified line; the ends stay."""
    if tolerance <= 0 or len(line) < 3:
        return list(line)
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    closed = line[0] == line[-1]
    if closed:
        # A loop's ends coincide: split it at the point farthest from the start first.
        far = max(range(1, len(line) - 1), key=lambda k: math.dist(line[0], line[k]))
        keep[far] = True
        stack = [(0, far), (far, len(line) - 1)]
    while stack:
        s, e = stack.pop()
        (x0, y0), (x1, y1) = line[s], line[e]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for k in range(s + 1, e):
            px, py = line[k]
            dist = abs(dy * (px - x0) - dx * (py - y0)) / norm if norm > 0 else math.hypot(px - x0, py - y0)
            if dist > best:
                best, idx = dist, k
        if idx > 0 and best > tolerance:
            keep[idx] = True
            stack += [(s, idx), (idx, e)]
    return [p for p, k in zip(line, keep) if k]


def line_length(line: Sequence[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(line, line[1:]))


def isoline_features(grid: Grid, lines: dict[float, list[list[tuple[float, float]]]], iso: Any,
                     region: str) -> list[dict]:
    """GeoJSON LineString features from isolines in pixel coordinates (properties: value, major, label, region).

    Lines shorter than ``iso.min_length_px`` are dropped; the rest are simplified (``iso.simplify_px``) and carry
    a label when at least ``iso.label_min_px`` long. Coordinates round to 0.001 degree (about 100 m)."""
    out = []
    yn, ys = grid.y_north, grid.y_south
    span_x = (grid.east - grid.west) / grid.width
    for lv in sorted(lines):
        major = abs(lv / iso.major_step - round(lv / iso.major_step)) < 1e-9
        for line in lines[lv]:
            if line_length(line) < iso.min_length_px:
                continue
            simple = simplify(line, iso.simplify_px)
            coords = []
            for x, y in simple:
                lon = round(grid.west + x * span_x, 3)
                lat = round(lat_of(yn - y / grid.height * (yn - ys)), 3)
                if not coords or coords[-1] != [lon, lat]:
                    coords.append([lon, lat])
            if len(coords) < 2:
                continue
            value = int(lv) if float(lv).is_integer() else lv
            out.append({"type": "Feature",
                        "properties": {"value": value, "major": major, "label": line_length(simple) >= iso.label_min_px,
                                       "region": region},
                        "geometry": {"type": "LineString", "coordinates": coords}})
    return out


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


def solve_field(grid: Grid, cov: Sequence[float], points: Sequence[Point], box: Any, k: int, min_km: float,
                step: int) -> tuple[list, list]:
    """Field value and the distance to the nearest point (km) at every covered pixel (None elsewhere)."""
    cols = node_positions(grid.width, step)
    rows = node_positions(grid.height, step)
    index = index_for(points, box, k)
    need = needed_nodes(grid, cols, rows, cov)
    node_v, node_d = solve_nodes(grid, cols, rows, index, k, min_km, need)
    return upsample(grid, cols, rows, node_v, cov), upsample(grid, cols, rows, node_d, cov)


def render_region(grid: Grid, *, stations: Sequence[Point], pockets: Sequence[Point], polygons: list,
                  heat: Any, domain: tuple[float, float], scale: str, box: Any, dem: Any = None) -> tuple[bytes, dict]:
    """Snowfall RGBA bytes of one region and a summary (colored pixels, pockets drawn, the pixel field).

    With a terrain grid ``dem`` (``pipeline.terrain.Dem``) and ``heat.snowfall.elevation`` set, the background
    follows the ground height (:func:`solve_elevation_field`) and, with ``pocket_terrain``, so do the pockets.
    Otherwise it is plain station interpolation (:func:`solve_field`)."""
    snow = heat.snowfall
    cov = coverage(grid, polygons)
    elev = snow.elevation
    terrain: Optional[PocketTerrain] = None
    pocket_km: Optional[list[Optional[float]]] = None
    if dem is not None and elev is not None and all(p.elev_m is not None for p in stations):
        heights = terrain_heights(grid, dem, cov)
        background, dist, betas = solve_elevation_field(grid, cov, stations, box, snow.neighbors, snow.min_distance_km,
                                                        snow.grid_step_px, heights, elev)
        if elev.pocket_terrain:
            terrain = PocketTerrain(heights, betas, elev.log_offset_in)
            pocket_km = [None if (z := dem.sample(p.lon, p.lat)) is None else z / 1000.0 for p in pockets]
    else:
        background, dist = solve_field(grid, cov, stations, box, snow.neighbors, snow.min_distance_km, snow.grid_step_px)
    values, near = apply_pockets(grid, background, pockets, snow.pocket_radius_km, terrain, pocket_km)
    for k, pd in near.items():
        if dist[k] is not None and pd < dist[k]:
            dist[k] = pd
    mode = heat.modes.snowfall
    color_of = colorizer(mode if mode.scale == scale else mode.model_copy(update={"scale": scale}), heat.ramp.colors, domain)
    fade = Fade(snow.full_opacity_km, snow.fade_end_km, heat.field.max_alpha, snow.floor_alpha, snow.fade_to,
                hex_rgb(heat.neutral))
    classes = (list(mode.breaks), list(mode.colors), grid.width, grid.height) if scale == "classed" else None
    buf, colored = paint(values, dist, cov, color_of, fade, classes=classes)
    drawn = sum(1 for p in pockets if 0 <= grid.x_of(p.lon) < grid.width and 0 <= grid.y_of(p.lat) < grid.height)
    return bytes(buf), {"covered_pixels": colored, "pockets": drawn, "values": values, "background": background,
                        "terrain": terrain, "pocket_heights_km": pocket_km}


def inputs_digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, separators=(",", ":"), default=str).encode())
    return h.hexdigest()


def _code_digest() -> list[str]:
    """The method itself: a code change rebuilds the rasters too."""
    return [hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), hashlib.sha256(Path(png.__file__).read_bytes()).hexdigest()]


def resort_pockets(resorts: Iterable[dict]) -> list[Point]:
    """Resorts with a sourced annual snowfall (``snow.annual_avg_in``) as pocket points."""
    out = []
    for r in resorts:
        s = (r.get("snow") or {}).get("annual_avg_in")
        if s and isinstance(s.get("value"), (int, float)) and r.get("lat") is not None and r.get("lon") is not None:
            out.append(Point(float(r["lon"]), float(r["lat"]), float(s["value"])))
    return out


def _sourced(field: Any) -> bool:
    return isinstance(field, dict) and field.get("value") is not None and bool(field.get("source_id"))


def days_open(open_mmdd: str, close_mmdd: str) -> int:
    """Days from open to close, both counted (a non leap year; a close before the open is next year)."""
    import datetime as dt  # noqa: PLC0415
    om, od = (int(x) for x in open_mmdd.split("-"))
    cm, cd = (int(x) for x in close_mmdd.split("-"))
    o = dt.date(2001, om, min(od, 28) if om == 2 else od)
    c = dt.date(2001, cm, min(cd, 28) if cm == 2 else cd)
    if c < o:
        c = c.replace(year=2002)
    return (c - o).days + 1


def season_days(resort: dict) -> Optional[float]:
    """A resort's sourced season length: ``season.length_days``, else its sourced open and close pair."""
    s = resort.get("season") or {}
    length = s.get("length_days")
    if _sourced(length) and isinstance(length["value"], (int, float)):
        return float(length["value"])
    o, c = s.get("open_typical"), s.get("close_typical")
    if _sourced(o) and _sourced(c):
        try:
            return float(days_open(str(o["value"]), str(c["value"])))
        except (ValueError, TypeError):
            return None
    return None


def season_points(resorts: Iterable[dict]) -> list[tuple[Point, str]]:
    """(point, state code) of every resort with a sourced season length and coordinates."""
    out = []
    for r in resorts:
        v = season_days(r)
        if v is not None and r.get("lat") is not None and r.get("lon") is not None:
            out.append((Point(float(r["lon"]), float(r["lat"]), v), str(r.get("state", "")).upper()))
    return out


def _load_manifest(path: Path, digest: str) -> Optional[dict]:
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = [r["image"] for r in old.get("rasters", [])] + [f["file"] for f in (old.get("isolines") or {}).get("files", [])]
    if old.get("inputs_sha256") == digest and all((path.parent / f).is_file() for f in files):
        return old
    return None


def _write_if_changed(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def _drop_stale(heat_dir: Path, pattern: str, keep: set[str]) -> None:
    for stale in heat_dir.glob(pattern) if heat_dir.is_dir() else []:
        if stale.name not in keep:
            stale.unlink()


def _classes(mode: Any) -> dict:
    """The class and legend settings a classed manifest carries (empty for other scales)."""
    if mode.scale != "classed":
        return {}
    return {"breaks": list(mode.breaks), "colors": list(mode.colors),
            "legend": {"ticks": list(mode.legend_ticks or mode.breaks), "compress_below": mode.legend_compress_below}}


def _dem_digest(dems: Optional[dict]) -> Optional[list]:
    if not dems:
        return None
    out = []
    for name in sorted(dems):
        d = dems[name]
        out.append([name, d.west, d.north, d.step, d.width, d.height,
                    hashlib.sha256(json.dumps([round(v, 1) for v in d.values]).encode()).hexdigest()])
    return out


SNOTEL_ATTRIBUTION = "Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL"
NOAA_ATTRIBUTION = "Snowfall normals: NOAA NCEI 1991 to 2020"


def write_snowfall(out_dir: Path, *, stations: Sequence[Point], pockets: Sequence[Point], outlines: dict, heat: Any,
                   source: dict, generated_at: str, as_of: str, mountain_stations: Sequence[Point] = (),
                   mountain_source: Optional[dict] = None, dems: Optional[dict] = None,
                   terrain_source: Optional[dict] = None) -> dict:
    """Write ``heat/snowfall-<region>.png``, ``heat/snowfall-isolines-<region>.geojson`` and ``heat/snowfall.json``;
    skip the work when inputs are unchanged.

    ``stations`` are the usable NOAA stations; ``source`` is the NOAA source record (``id`` used).
    ``mountain_stations`` are the usable NRCS SNOTEL stations (``mountain_source``: their record, with ``id``,
    ``accessed_date`` and ``water_years``), blended into the same field. ``dems`` ({region: Dem}) makes the
    field follow the ground height (``heat.snowfall.elevation``; ``terrain_source`` is its record). Returns the
    manifest.
    """
    heat_dir = out_dir / "heat"
    mode = heat.modes.snowfall
    iso = heat.snowfall.isolines
    regions = {"lower48": heat.regions.lower48, "alaska": heat.regions.alaska}
    noaa = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value, p.elev_m) for p in stations]
    mountain = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value, p.elev_m) for p in mountain_stations]
    stations = noaa + mountain
    pockets = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value) for p in pockets]
    use_terrain = bool(dems) and heat.snowfall.elevation is not None and all(p.elev_m is not None for p in stations)
    if use_terrain and heat.snowfall.elevation.terrain_smooth_cells:
        dems = {k: d.smoothed(heat.snowfall.elevation.terrain_smooth_cells) for k, d in dems.items()}

    def inside(p: Point, box: Any) -> bool:
        return box.west <= p.lon <= box.east and box.south <= p.lat <= box.north

    lo = mode.domain[0] if mode.domain else (mode.domain_min if mode.domain_min is not None else 0.0)
    vals = [p.value for p in stations if any(inside(p, b) for b in regions.values())] + [p.value for p in pockets]
    hi = mode.domain[1] if mode.domain else nice_ceil(max(vals) if vals else 1.0)
    if mode.scale == "classed":
        ticks = list(mode.legend_ticks or mode.breaks)
    elif mode.domain:
        ticks = list(mode.ticks or [lo, hi])
    else:
        ticks = sqrt_ticks(mode.ticks or [], lo, hi) if mode.scale == "sqrt" else [lo, hi]
    digest = inputs_digest([(p.lon, p.lat, p.value, p.elev_m) for p in stations],
                           [(p.lon, p.lat, p.value) for p in pockets], outlines.get("features", []),
                           heat.model_dump(mode="json"), MANIFEST_VERSION, _code_digest(),
                           _dem_digest(dems) if use_terrain else None, len(mountain),
                           (mountain_source or {}).get("id"), (terrain_source or {}).get("id"))
    manifest_path = heat_dir / "snowfall.json"
    old = _load_manifest(manifest_path, digest)
    if old:
        return old

    rasters, iso_files = [], []
    for name, box in regions.items():
        polys = region_polygons(outlines, name)
        if not polys:
            continue
        grid = Grid.for_box(box.west, box.south, box.east, box.north, heat.snowfall.px_per_degree)
        rgba, info = render_region(grid, stations=stations, pockets=pockets, polygons=polys, heat=heat,
                                   domain=(lo, hi), scale=mode.scale, box=box,
                                   dem=(dems or {}).get(name) if use_terrain else None)
        if not info["covered_pixels"]:
            continue
        data = png.encode_rgba(grid.width, grid.height, rgba)
        image = f"snowfall-{name}.png"
        _write_if_changed(heat_dir / image, data)
        rasters.append({"region": name, "image": image, "width": grid.width, "height": grid.height,
                        "coordinates": grid.coordinates(), "bytes": len(data),
                        "stations": sum(1 for p in noaa if inside(p, box)),
                        "mountain_stations": sum(1 for p in mountain if inside(p, box)),
                        "terrain": bool(use_terrain and (dems or {}).get(name)),
                        "pockets": info["pockets"]})
        lines = refined_isolines(grid, info["background"], info["values"], pockets, heat.snowfall.pocket_radius_km,
                                 iso.levels(), iso.supersample, info["terrain"], info["pocket_heights_km"])
        feats = isoline_features(grid, lines, iso, name)
        body = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")).encode()
        file = f"snowfall-isolines-{name}.geojson"
        _write_if_changed(heat_dir / file, body)
        iso_files.append({"region": name, "file": file, "bytes": len(body), "features": len(feats)})
    _drop_stale(heat_dir, "snowfall-*.png", {r["image"] for r in rasters})
    _drop_stale(heat_dir, "snowfall-isolines-*.geojson", {f["file"] for f in iso_files})
    snow = heat.snowfall
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "mode": "snowfall",
        "units": "in",
        "scale": mode.scale,
        "domain": {"min": lo, "max": hi},
        "ticks": ticks,
        **_classes(mode),
        **({} if mode.scale == "classed" else {"ramp": list(heat.ramp.colors)}),
        "background": {"source_id": source["id"], "as_of": as_of, "confidence": "published",
                       "field": "ANN-SNOW-NORMAL", "stations": sum(r["stations"] for r in rasters)},
        **({"mountain_stations": {
            "source_id": mountain_source["id"], "as_of": str(mountain_source.get("accessed_date", as_of)),
            "confidence": "estimated", "field": "annual_snowfall_in",
            "method": "median of water year totals of daily SWE gain / new snow density",
            "water_years": list(mountain_source.get("water_years") or []),
            "stations": sum(r["mountain_stations"] for r in rasters)}} if mountain and mountain_source else {}),
        **({"terrain": {"source_id": terrain_source["id"], "as_of": str(terrain_source.get("accessed_date", as_of)),
                        "confidence": "published", "step_deg": next(iter(dems.values())).step}}
           if use_terrain and terrain_source else {}),
        # Each pocket value is a resort's own sourced snow.annual_avg_in in resorts.json (source there).
        "pockets": {"field": "snow.annual_avg_in", "resorts": len(pockets), "radius_km": snow.pocket_radius_km,
                    "terrain": bool(use_terrain and snow.elevation and snow.elevation.pocket_terrain)},
        "method": {"interpolation": "gradient plus inverse distance on log snowfall" if use_terrain
                   else "adaptive modified Shepard", "neighbors": snow.neighbors,
                   "min_distance_km": snow.min_distance_km, "grid_step_px": snow.grid_step_px,
                   "px_per_degree": snow.px_per_degree, "full_opacity_km": snow.full_opacity_km,
                   "fade_end_km": snow.fade_end_km, "floor_alpha": snow.floor_alpha, "fade_to": snow.fade_to,
                   **({"elevation": snow.elevation.model_dump(mode="json")} if use_terrain else {})},
        "attribution": SNOTEL_ATTRIBUTION if mountain else NOAA_ATTRIBUTION,
        "isolines": {"levels": iso.levels(), "major_step": iso.major_step, "files": iso_files},
        "inputs_sha256": digest,
        "rasters": rasters,
    }
    heat_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_season(out_dir: Path, *, resorts: Sequence[dict], outlines: dict, heat: Any, generated_at: str) -> Optional[dict]:
    """Write ``heat/season-<region>.png`` and ``heat/season.json`` from the resorts' sourced season lengths.

    The field (``heat.field``: decluster, neighbors, min distance) at the ``heat.season`` resolution, colored by
    the season classes, faded by ``heat.season``. Returns the manifest, or None when no resort has a value.
    """
    heat_dir = out_dir / "heat"
    mode = heat.modes.season
    cfg, field = heat.season, heat.field
    sp = season_points(resorts)
    if not sp:
        _drop_stale(heat_dir, "season*", set())
        return None
    data_states = {code for _, code in sp}
    raw = [Point(normalize_lon(p.lon, p.lat), p.lat, p.value) for p, _ in sp]
    pts = decluster(raw, field.decluster_km)
    values = [p.value for p, _ in sp]
    lo, hi = min(values), max(values)
    regions = {"lower48": heat.regions.lower48, "alaska": heat.regions.alaska}
    digest = inputs_digest(sorted((p.lon, p.lat, p.value) for p in raw), sorted(data_states), outlines.get("features", []),
                           heat.model_dump(mode="json"), MANIFEST_VERSION, _code_digest())
    manifest_path = heat_dir / "season.json"
    old = _load_manifest(manifest_path, digest)
    if old:
        return old
    color_of = colorizer(mode, heat.ramp.colors, (lo, hi))
    fade = Fade(cfg.full_opacity_km, cfg.fade_end_km, field.max_alpha, cfg.floor_alpha, cfg.fade_to,
                hex_rgb(heat.neutral), cfg.no_data_reach_km)
    rasters = []
    for name, box in regions.items():
        polys = region_polygons(outlines, name)
        if not polys:
            continue
        grid = Grid.for_box(box.west, box.south, box.east, box.north, cfg.px_per_degree)
        cov = coverage(grid, polys)
        empty = region_codes(outlines, name) - data_states
        no_data = border = None
        if empty:
            no_data = coverage(grid, region_polygons(outlines, name, only=empty))
            with_data = coverage(grid, region_polygons(outlines, name, only=region_codes(outlines, name) - empty))
            border = border_distance(grid, with_data, cfg.grid_step_px, cov)
        vals, dist = solve_field(grid, cov, pts, box, field.neighbors, field.min_distance_km, cfg.grid_step_px)
        classes = (list(mode.breaks), list(mode.colors), grid.width, grid.height) if mode.scale == "classed" else None
        buf, colored = paint(vals, dist, cov, color_of, fade, no_data, border, classes)
        if not colored:
            continue
        data = png.encode_rgba(grid.width, grid.height, bytes(buf))
        image = f"season-{name}.png"
        _write_if_changed(heat_dir / image, data)
        rasters.append({"region": name, "image": image, "width": grid.width, "height": grid.height,
                        "coordinates": grid.coordinates(), "bytes": len(data),
                        "resorts": sum(1 for p in raw if box.west <= p.lon <= box.east and box.south <= p.lat <= box.north),
                        "no_data_states": sorted(empty)})
    _drop_stale(heat_dir, "season-*.png", {r["image"] for r in rasters})
    if not rasters:
        return None
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "mode": "season",
        "units": "days",
        "scale": mode.scale,
        "domain": {"min": lo, "max": hi},
        "ticks": list(mode.legend_ticks or mode.breaks or [lo, hi]),
        **_classes(mode),
        **({} if mode.scale == "classed" else {"ramp": list(heat.ramp.colors)}),
        # Each value is a resort's own sourced season.length_days (or its sourced open and close pair) in
        # resorts.json, with its source there.
        "values": {"field": "season.length_days", "resorts": len(sp), "points": len(pts)},
        "method": {"interpolation": "adaptive modified Shepard", "neighbors": field.neighbors,
                   "min_distance_km": field.min_distance_km, "decluster_km": field.decluster_km,
                   "grid_step_px": cfg.grid_step_px, "px_per_degree": cfg.px_per_degree,
                   "full_opacity_km": cfg.full_opacity_km, "fade_end_km": cfg.fade_end_km,
                   "floor_alpha": cfg.floor_alpha, "fade_to": cfg.fade_to, "no_data_reach_km": cfg.no_data_reach_km},
        "attribution": None,
        "inputs_sha256": digest,
        "rasters": rasters,
    }
    heat_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
