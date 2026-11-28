"""Open snowkite ground inside the promising ERA5 cells (docs/map/SNOWKITE.md, phase 25).

ERA5 (data/raw/climate/era5_surface) says where wind, snow and lake ice are reliable on a grid of about 31 km. It
cannot see terrain or trees. This module looks inside each promising cell at about 100 to 170 m and measures the
open, low angle, treeless ground a kiter could use. Everything here is our own deduction from public rasters; every
number is an estimate (``confidence: estimated``) and every threshold sits in ``SNOWKITE_TERRAIN`` below.

Data route (chosen because both sources allow small reads over large areas):

* Elevation: AWS Terrain Tiles, terrarium PNG, read with the project's own PNG reader (pipeline/png.py) exactly as
  pipeline/terrain.py does. Zoom 10 south of 56 N (pixels of 100 to 130 m in the lower 48) and zoom 9 north of it
  (105 to 170 m in Alaska). The working grid of a cell is the Web Mercator pixel grid of those tiles.
* Land cover: ESA WorldCover 2021 v200 (10 m, cloud optimized GeoTIFF, 3 degree files). The reader below parses the
  TIFF directory itself and fetches one overview (``worldcover_level`` 3 = 80 m pixels, about 5 MB per file) with a
  single HTTP range request. Each terrain pixel gets the share of each class among the overview pixels inside it.
  WorldCover defines tree cover as 10 percent canopy or more, which matches the "under 10 percent canopy" rule.

Steps: candidate cells from the ERA5 extract; a kiteable mask per cell; connected patches (cut at the cell edge, then
joined across cells into groups for the connected acreage); metrics, classes and a kite size estimate per patch.

Commands::

    python -m pipeline.snowkite_terrain fetch [--cells 400|all] [--workers 4] [--max-patches 1500]
    python -m pipeline.snowkite_terrain quicklook --lat 41.8 --lon -107.9 --out /tmp/x.png [--half 0.2]
    python -m pipeline.snowkite_terrain summary

``fetch`` needs numpy; the build does not import this module's raster code. Downloads are cached under
``data/raw/snowkite/terrain/download/`` (gitignored). The committed extract is ``patches_<region>.json``,
``cells_processed.json`` and ``source.json`` in ``data/raw/snowkite/terrain/``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import functools
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Optional, Sequence

try:  # numpy is a fetch time dependency only
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

from pipeline import png
from pipeline import terrain as tt

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "raw" / "snowkite" / "terrain"
DOWNLOAD_DIR = OUT_DIR / "download"
ERA5_EXTRACT = ROOT / "data" / "raw" / "climate" / "era5_surface" / "us_cells_dec_mar.json"
USER_AGENT = "SnowsportsCareerMapResearch/1.0"
WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{name}"
ACRE_M2 = 4046.8564224
MPH = 0.44704

# Every number of the model. To move into config/definitions.yaml `snowkite` later.
SNOWKITE_TERRAIN: dict[str, Any] = {
    # --- 1. candidate ERA5 cells
    # A kite with a mid sized quiver flies from about 3 m/s (7 mph, the working floor in snowkite-criteria.md 1.1)
    # to about 11 m/s (25 mph), where most riders are on their smallest kite.
    "usable_wind_ms": [3.0, 11.0],
    # Wind you can plan a trip around: usable in more than half of the sampled daylight hours.
    "min_usable_wind_share": 0.55,
    # 0.02 m of water is about 10 to 20 cm of settled snow: enough to cover grass and stubble.
    "min_snow_we_m": 0.02,
    "min_snow_day_share": 0.60,
    # 0.2 m (8 inches) of lake ice carries people and snowmobiles with margin; ERA5 lake ice is a model value.
    "min_lake_ice_m": 0.20,
    "min_ice_day_share": 0.60,
    "default_cells": 400,                  # best cells first: usable wind share x snow (or ice) share
    # --- data
    "zoom_low_lat": 10,                    # 153 m x cos(latitude): 100 to 130 m in the lower 48
    "zoom_high_lat": 9,                    # 306 m x cos(latitude): 105 to 170 m in Alaska
    "zoom_split_lat": 56.0,
    "worldcover_level": 3,                 # overview 3 = 80 m pixels (10 m x 2^3)
    # --- 2. open ground
    # A terrain pixel holds 3 to 7 land cover samples. Up to one in five may be tree before the pixel counts as
    # wooded: single stray trees are fine to kite around, a third of the pixel in trees is a grove.
    "max_tree_share": 0.20,
    "max_built_share": 0.20,
    "water_pixel_share": 0.50,             # a pixel is water (lake ice when the cell's ice is reliable) from here
    "ice_pixel_share": 0.50,               # permanent snow and ice (WorldCover class 70): flagged glacier
    # The Great Lakes never freeze reliably offshore, yet ERA5 gives the land cells along their shores thick ice
    # (its lake model describes the small inland lakes of the cell). Water at a Great Lake's level, as the terrain
    # tiles store it, inside the box is treated as open water. This also drops bays that do freeze (Green Bay,
    # Chequamegon Bay, Saginaw Bay): those need local knowledge, not this model.
    "open_water": {"box": [-92.5, 41.0, -75.5, 49.5], "tolerance_m": 0.6,
                   "levels_m": {"Superior": 179.0, "Michigan, Huron and St. Clair": 175.0, "Erie": 174.0, "Ontario": 75.0}},
    "max_kite_slope_deg": 25.0,            # steeper ground is ski mountaineering with a kite, outside this model
    "slope_classes_deg": [3.0, 10.0, 25.0],
    # --- 3. patches
    "min_patch_acres": 40.0,
    # Turbulence shadow: a kite flies 20 to 25 m up; rotor behind an obstacle reaches about ten obstacle heights.
    # Ten heights of a 30 m conifer is 300 m. A pixel counts as an obstacle when half of it is tree.
    "tree_obstacle_share": 0.50,
    "tree_shadow_m": 300.0,
    # Avalanche reach rule: a patch pixel is "under steep ground" when a pixel of 30 degrees or more lies within
    # 1,000 m horizontally and the line of sight up to it rises at 20 degrees or more (a conservative alpha angle;
    # long running slides stop near 18 to 25 degrees). Steepness for this test is the steepest drop to any of the
    # 8 neighbours, because the centred gradient of a 100 m grid smooths start zones away.
    "steep_deg": 30.0,
    "runout_reach_m": 1000.0,
    "runout_alpha_deg": 20.0,
    "upslope_tolerance_deg": 60.0,         # slope faces the wind within this angle: wind blows up it
    # --- 4. classes
    "size_acres": {"minimum": 40, "enough": 160, "good": 640, "excellent": 2500, "expedition": 25000},
    # Beginner ground is mostly flat, roomy and clean. A big mixed patch (a prairie joined to its foothills) also
    # counts when it holds one connected flat core (under 3 degrees) of the same size with the same low shadow.
    "beginner": {"min_flat_share": 0.60, "min_acres": 160, "max_tree_shadow_share": 0.15},
    "advanced": {"min_rolling_share": 0.40, "min_low_angle_share": 0.60},
    "freeride": {"min_relief_m": 150, "min_steep_share": 0.15, "min_upslope_share": 0.40},
    # --- 5. kite size (see kite_size_m2)
    "kite": {
        "rider_kg": 80.0,
        "calibration_wind_ms": 13.5 * MPH,  # middle of 12 to 15 mph
        "calibration_area_m2": 10.5,        # middle of 9 to 12 square metres
        "range": 0.15,                      # one kite covers about 15 percent either side by trimming
        "lift_coefficient": 1.0,            # lumped, only used to express the calibration as a pull
        "traverse_factor": 0.5,             # a rider climbs across the slope: half the fall line gradient
    },
    # --- 6. output
    "max_patches_per_cell": 3,             # the largest patches of a cell; ponds and slivers beyond that are cut
    "max_patches_per_region": 1000,
    "max_outline_points": 40,
}

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
REGION_OF_STATE = {
    "AK": "alaska",
    "WA": "pacific", "OR": "pacific", "CA": "pacific", "NV": "pacific",
    "MT": "rockies", "WY": "rockies", "ID": "rockies", "CO": "rockies", "UT": "rockies", "NM": "rockies", "AZ": "rockies",
    "ME": "northeast", "NH": "northeast", "VT": "northeast", "NY": "northeast", "MA": "northeast", "CT": "northeast",
    "RI": "northeast", "PA": "northeast", "NJ": "northeast",
}
# WorldCover class code -> column of the cover share array
COVER_NAMES = ["nodata", "tree", "shrub", "grass", "crop", "built", "bare", "ice", "water", "wetland", "mangrove", "moss"]
COVER_CODES = {0: 0, 10: 1, 20: 2, 30: 3, 40: 4, 50: 5, 60: 6, 70: 7, 80: 8, 90: 9, 95: 10, 100: 11}
TREE, BUILT, ICE, WATER = 1, 5, 7, 8


# ============================================================================ kite size (pure Python)
def air_density(elev_m: float) -> float:
    """Air density (kg/m3) at a height in the ICAO standard atmosphere (15 C and 1013.25 hPa at sea level, 6.5 K
    per km). Winter air is colder and so a few percent denser than this; the model ignores that."""
    return 1.225 * (1.0 - 2.25577e-5 * max(-500.0, min(elev_m, 11000.0))) ** 4.25588


def kite_pull_fraction(cfg: Optional[dict] = None) -> float:
    """The kite pull, as a share of rider weight, that the calibration implies on flat firm snow."""
    k = (cfg or SNOWKITE_TERRAIN)["kite"]
    return (0.5 * 1.225 * k["calibration_wind_ms"] ** 2 * k["calibration_area_m2"] * k["lift_coefficient"]
            / (k["rider_kg"] * 9.80665))


def kite_size_m2(wind_ms: float, slope_deg: float = 0.0, elev_m: float = 0.0, rider_kg: Optional[float] = None,
                 cfg: Optional[dict] = None) -> tuple[float, float, float]:
    """(small, typical, large) kite area in square metres. A model, never a recommendation.

    Physics. A kite pulls with F = 1/2 x rho x V^2 x A x C (air density, wind speed, flat area, a lumped
    coefficient). Riding needs a pull that is a share of rider weight: a base share on the flat (ski friction and
    edging) plus, on a climb, the weight component along the track, m g sin(slope) x ``traverse_factor`` (riders
    tack up a slope instead of climbing the fall line). Solving for A:

        A = A_cal x (rho_0 / rho) x (V_cal / V)^2 x (1 + traverse_factor x sin(slope) / base_share) x (m / m_cal)

    Calibration. One point fixes the scale: flat firm snow at sea level, an 80 kg rider, 13.5 mph, 10.5 m2 (the
    middle of "12 to 15 mph, about 9 to 12 m2"). With C = 1 that is a base pull of about 0.30 of rider weight. The
    returned range is the typical area plus and minus ``range`` (15 percent), so 13.5 mph gives 8.9 to 12.1 m2.
    Honest limits: the inverse square law is steeper than shop charts (it gives 13.3 m2 at 12 mph and 8.5 m2 at
    15 mph), because a moving rider adds apparent wind; soft or deep snow needs more area; gusts call for less.
    """
    c = cfg or SNOWKITE_TERRAIN
    k = c["kite"]
    if wind_ms <= 0:
        raise ValueError("wind speed must be positive")
    mass = k["rider_kg"] if rider_kg is None else rider_kg
    base = kite_pull_fraction(c)
    climb = k["traverse_factor"] * math.sin(math.radians(max(0.0, slope_deg)))
    typical = (k["calibration_area_m2"] * (1.225 / air_density(elev_m)) * (k["calibration_wind_ms"] / wind_ms) ** 2
               * (1.0 + climb / base) * (mass / k["rider_kg"]))
    return typical * (1 - k["range"]), typical, typical * (1 + k["range"])


# ============================================================================ ERA5 cells
def histogram_share(hist: Sequence[float], edges: Sequence[float], lo: float, hi: Optional[float] = None) -> float:
    """Share of a histogram in bins whose lower edge is in [lo, hi) (bins are [edge k, edge k+1), last one open)."""
    total = sum(hist)
    if not total:
        return 0.0
    return sum(n for n, e in zip(hist, edges) if e >= lo and (hi is None or e < hi)) / total


def median_usable_wind(speed_hist: Sequence[float], edges: Sequence[float], lo: float, hi: float) -> Optional[float]:
    """Median speed (m/s) of the hours inside [lo, hi), interpolated inside the 1 m/s bin."""
    bins = [(e, n) for n, e in zip(speed_hist, edges) if lo <= e < hi]
    total = sum(n for _, n in bins)
    if not total:
        return None
    run = 0.0
    for e, n in bins:
        if n and run + n >= total / 2:
            return e + (total / 2 - run) / n
        run += n
    return None


def top_directions(dir_hist: Sequence[float]) -> tuple[int, int]:
    """Sector index (0 = N, clockwise) with the most hours, and the runner up."""
    order = sorted(range(len(dir_hist)), key=lambda k: (-dir_hist[k], k))
    return order[0], order[1]


def candidate_cells(extract: dict, cfg: Optional[dict] = None) -> list[dict]:
    """Every ERA5 cell that passes the wind and snow (or lake ice) rule, best first."""
    c = cfg or SNOWKITE_TERRAIN
    cells, grid = extract["cells"], extract["grid"]
    lo, hi = c["usable_wind_ms"]
    out = []
    for k in range(len(cells["row"])):
        wind = histogram_share(cells["speed"][k], extract["speed_edges_ms"], lo, hi)
        snow = histogram_share(cells["snow"][k], extract["snow_edges_m_we"], c["min_snow_we_m"])
        ice = histogram_share(cells["ice"][k], extract["ice_edges_m"], c["min_lake_ice_m"])
        snow_ok, ice_ok = snow >= c["min_snow_day_share"], ice >= c["min_ice_day_share"]
        if wind < c["min_usable_wind_share"] or not (snow_ok or ice_ok):
            continue
        d1, d2 = top_directions(cells["dir"][k])
        dir_total = sum(cells["dir"][k]) or 1
        out.append({
            "row": cells["row"][k], "col": cells["col"][k], "state": cells["state"][k],
            "lat": grid["lat_max"] - grid["step_deg"] * cells["row"][k],
            "lon": grid["lon_min"] + grid["step_deg"] * cells["col"][k],
            "step": grid["step_deg"],
            "wind_share": round(wind, 3), "snow_share": round(snow, 3), "ice_share": round(ice, 3),
            "snow_ok": snow_ok, "ice_ok": ice_ok,
            "median_wind_ms": median_usable_wind(cells["speed"][k], extract["speed_edges_ms"], lo, hi),
            "dir": d1, "dir2": d2,
            "dir_share": round(cells["dir"][k][d1] / dir_total, 3), "dir2_share": round(cells["dir"][k][d2] / dir_total, 3),
            "score": wind * max(snow, ice),
        })
    out.sort(key=lambda r: (-r["score"], r["row"], r["col"]))
    return out


def cell_at(extract: dict, lat: float, lon: float, cfg: Optional[dict] = None) -> Optional[dict]:
    """The ERA5 cell record nearest a point, candidate or not (for quicklooks)."""
    c = cfg or SNOWKITE_TERRAIN
    grid, cells = extract["grid"], extract["cells"]
    row = round((grid["lat_max"] - lat) / grid["step_deg"])
    col = round((lon - grid["lon_min"]) / grid["step_deg"])
    for k in range(len(cells["row"])):
        if cells["row"][k] == row and cells["col"][k] == col:
            lo, hi = c["usable_wind_ms"]
            d1, d2 = top_directions(cells["dir"][k])
            wind = histogram_share(cells["speed"][k], extract["speed_edges_ms"], lo, hi)
            snow = histogram_share(cells["snow"][k], extract["snow_edges_m_we"], c["min_snow_we_m"])
            ice = histogram_share(cells["ice"][k], extract["ice_edges_m"], c["min_lake_ice_m"])
            return {"row": row, "col": col, "state": cells["state"][k], "dir": d1, "dir2": d2, "wind_share": wind,
                    "snow_share": snow, "ice_share": ice, "ice_ok": ice >= c["min_ice_day_share"],
                    "snow_ok": snow >= c["min_snow_day_share"],
                    "median_wind_ms": median_usable_wind(cells["speed"][k], extract["speed_edges_ms"], lo, hi)}
    return None


# ============================================================================ raster maths (numpy, pure)
def slope_aspect(elev: Any, px_m: float) -> tuple[Any, Any]:
    """(slope in degrees, aspect in degrees) of a height grid with rows from the north and square pixels of
    ``px_m`` metres. Aspect is the compass direction the ground faces (downhill), 0 = north, 90 = east."""
    dz_drow, dz_dx = np.gradient(np.asarray(elev, dtype=np.float64), px_m)
    dz_dn = -dz_drow
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dn)))
    aspect = (np.degrees(np.arctan2(-dz_dx, -dz_dn)) + 360.0) % 360.0
    return slope, aspect


def shifted(a: Any, dr: int, dc: int, fill: Any = 0) -> Any:
    """out[r, c] = a[r + dr, c + dc], ``fill`` where that falls outside."""
    h, w = a.shape
    out = np.full_like(a, fill)
    r0, r1 = max(0, -dr), min(h, h - dr)
    c0, c1 = max(0, -dc), min(w, w - dc)
    if r0 < r1 and c0 < c1:
        out[r0:r1, c0:c1] = a[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def steepest_neighbor_slope(elev: Any, px_m: float) -> Any:
    """Steepest slope (degrees) from each pixel to any of its 8 neighbours."""
    z = np.asarray(elev, dtype=np.float64)
    best = np.zeros_like(z)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                other = shifted(z, dr, dc, np.nan)
                best = np.fmax(best, np.abs(other - z) / (px_m * math.hypot(dr, dc)))
    return np.degrees(np.arctan(best))


def label_patches(mask: Any) -> tuple[Any, int]:
    """Connected areas of a boolean grid (edge neighbours only). Returns (labels, count); 0 is background."""
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    labels = np.zeros((h, w), np.int32)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    prev: list[tuple[int, int, int]] = []
    for r in range(h):
        d = np.diff(np.concatenate(([0], mask[r].astype(np.int8), [0])))
        cur = []
        j = 0
        for s, e in zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()):
            while j < len(prev) and prev[j][1] <= s:
                j += 1
            lab, k = 0, j
            while k < len(prev) and prev[k][0] < e:
                root = find(prev[k][2])
                if lab == 0:
                    lab = root
                elif root != lab:
                    lo, hi = min(root, lab), max(root, lab)
                    parent[hi] = lo
                    lab = lo
                k += 1
            if lab == 0:
                parent.append(len(parent))
                lab = len(parent) - 1
            cur.append((s, e, lab))
            labels[r, s:e] = lab
            j = max(j, k - 1)
        prev = cur
    roots = np.array([find(a) for a in range(len(parent))], dtype=np.int32)
    uniq = np.unique(roots[1:]) if len(parent) > 1 else np.array([], dtype=np.int32)
    lookup = np.zeros(len(parent), np.int32)
    lookup[uniq] = np.arange(1, len(uniq) + 1, dtype=np.int32)
    return lookup[roots][labels], int(len(uniq))


def wind_extents(east_m: Any, north_m: Any, from_deg: float, px_m: float = 0.0) -> tuple[float, float]:
    """(extent along the wind, extent across the wind) in metres of a set of pixel centres, each ``px_m`` wide."""
    a = math.radians(from_deg)
    along = np.asarray(east_m) * math.sin(a) + np.asarray(north_m) * math.cos(a)
    across = np.asarray(east_m) * math.cos(a) - np.asarray(north_m) * math.sin(a)
    return float(np.ptp(along)) + px_m, float(np.ptp(across)) + px_m


def angle_between(a_deg: Any, b_deg: float) -> Any:
    """Smallest angle (0 to 180) between compass directions."""
    return np.abs((np.asarray(a_deg, dtype=np.float64) - b_deg + 180.0) % 360.0 - 180.0)


def upslope_share(aspect_deg: Any, from_deg: float, tolerance_deg: float) -> Optional[float]:
    """Share of the given ground that faces the wind within the tolerance, so the wind blows up it."""
    a = np.asarray(aspect_deg)
    if a.size == 0:
        return None
    return float(np.mean(angle_between(a, from_deg) <= tolerance_deg))


def aspect_histogram(aspect_deg: Any) -> list[int]:
    """Pixels per facing sector (N, NE, ..., NW)."""
    a = np.asarray(aspect_deg)
    return np.bincount((np.floor((a + 22.5) / 45.0).astype(np.int64)) % 8, minlength=8).tolist()


def tree_shadow(tree: Any, from_deg: float, px_m: float, reach_m: float) -> Any:
    """Pixels within ``reach_m`` downwind of a tree pixel (the tree pixels themselves excluded)."""
    tree = np.asarray(tree, dtype=bool)
    out = np.zeros_like(tree)
    a = math.radians(from_deg)
    steps = max(1, int(math.ceil(reach_m / px_m * 2)))
    seen = set()
    for k in range(1, steps + 1):
        dist = min(reach_m, k * px_m / 2) / px_m          # pixels toward the wind
        dr, dc = int(round(-math.cos(a) * dist)), int(round(math.sin(a) * dist))
        if (dr, dc) == (0, 0) or (dr, dc) in seen:
            continue
        seen.add((dr, dc))
        out |= shifted(tree, dr, dc, False)
    return out & ~tree


def runout_mask(elev: Any, steep: Any, px_m: float, reach_m: float, alpha_deg: float) -> Any:
    """Pixels with a steep pixel within ``reach_m`` whose line of sight rises at ``alpha_deg`` or more."""
    steep = np.asarray(steep, dtype=bool)
    out = np.zeros(steep.shape, bool)
    if not steep.any():
        return out
    z = np.asarray(elev, dtype=np.float32)
    zs = np.where(steep, z, np.float32(-1e9))
    radius = int(reach_m // px_m)
    tan_a = math.tan(math.radians(alpha_deg))
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            dist = math.hypot(dr, dc) * px_m
            if 0 < dist <= reach_m:
                out |= shifted(zs, dr, dc, np.float32(-1e9)) - z >= tan_a * dist
    return out


def outline(mask: Any) -> list[tuple[int, int]]:
    """Outer boundary ring of one connected area as pixel corner points (x, y), clockwise on screen, open."""
    m = np.pad(np.asarray(mask, dtype=bool), 1)
    rr, cc = np.nonzero(m)
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(sel: Any, x0: Any, y0: Any, x1: Any, y1: Any) -> None:
        for a, b, c, d in zip(x0[sel].tolist(), y0[sel].tolist(), x1[sel].tolist(), y1[sel].tolist()):
            nxt.setdefault((a, b), []).append((c, d))

    add(~m[rr - 1, cc], cc, rr, cc + 1, rr)                # top edge, heading east
    add(~m[rr, cc + 1], cc + 1, rr, cc + 1, rr + 1)        # right edge, heading south
    add(~m[rr + 1, cc], cc + 1, rr + 1, cc, rr + 1)        # bottom edge, heading west
    add(~m[rr, cc - 1], cc, rr + 1, cc, rr)                # left edge, heading north
    if not len(rr):
        return []
    start = (int(cc[0]), int(rr[0]))                       # top left corner of the first pixel: on the outer ring
    ring = [start]
    cur, heading = start, (1, 0)
    for _ in range(4 * len(rr) + 8):
        options = nxt[cur]
        if len(options) == 1:
            new = options[0]
        else:   # two areas touch at a corner: turn right to stay on the same pixel block
            right = (-heading[1], heading[0])
            new = next((o for o in options if (o[0] - cur[0], o[1] - cur[1]) == right), options[0])
        heading = (new[0] - cur[0], new[1] - cur[1])
        cur = new
        if cur == start:
            break
        ring.append(cur)
    return [(x - 1, y - 1) for x, y in ring]


def _dp(points: Any, tol: float) -> list[int]:
    keep = [0, len(points) - 1]
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = points[a], points[b]
        seg = q - p
        length = math.hypot(seg[0], seg[1])
        rel = points[a + 1:b] - p
        d = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / length if length else np.hypot(rel[:, 0], rel[:, 1])
        k = int(np.argmax(d))
        if d[k] > tol:
            mid = a + 1 + k
            keep.append(mid)
            stack += [(a, mid), (mid, b)]
    return sorted(keep)


def simplify_ring(ring: Sequence[tuple[float, float]], max_points: int, tol: float = 0.75) -> list[tuple[float, float]]:
    """Douglas Peucker on a closed ring, the tolerance growing until at most ``max_points`` corners remain."""
    pts = np.asarray(ring, dtype=np.float64)
    if len(pts) <= 4:
        return [tuple(p) for p in pts.tolist()]
    far = int(np.argmax(np.hypot(pts[:, 0] - pts[0, 0], pts[:, 1] - pts[0, 1])))
    halves = [np.vstack([pts[:far + 1]]), np.vstack([pts[far:], pts[:1]])]
    while True:
        out: list[tuple[float, float]] = []
        for half in halves:
            idx = _dp(half, tol)
            out += [tuple(half[k].tolist()) for k in idx[:-1]]
        if len(out) <= max_points:
            return out
        tol *= 1.4


def size_rating(acres: float, cfg: Optional[dict] = None) -> Optional[str]:
    best = None
    for name, floor in sorted((cfg or SNOWKITE_TERRAIN)["size_acres"].items(), key=lambda kv: kv[1]):
        if acres >= floor:
            best = name
    return best


SIZE_POINTS = {"minimum": 1, "enough": 2, "good": 3, "excellent": 4, "expedition": 5}


def classify(m: dict, cfg: Optional[dict] = None) -> list[str]:
    """Rider classes of a patch from its metrics (``acres``, ``slope_share`` [flat, rolling, steep],
    ``tree_shadow_share``, ``relief_m``, ``upslope_share``, optional ``flat_core_acres`` and
    ``flat_core_shadow_share``). A patch can carry several."""
    c = cfg or SNOWKITE_TERRAIN
    flat, rolling, steep = m["slope_share"]
    out = []
    b, a, f = c["beginner"], c["advanced"], c["freeride"]
    beginner = ((flat >= b["min_flat_share"] and m["acres"] >= b["min_acres"]
                 and m["tree_shadow_share"] <= b["max_tree_shadow_share"])
                or (m.get("flat_core_acres", 0.0) >= b["min_acres"]
                    and m.get("flat_core_shadow_share", 1.0) <= b["max_tree_shadow_share"]))
    if beginner:
        out.append("beginner")
    # advanced: rolling ground, or low angle ground that is too tight or too turbulent for a first day
    if rolling >= a["min_rolling_share"] or (not beginner and flat + rolling >= a["min_low_angle_share"]):
        out.append("advanced")
    if (m["relief_m"] >= f["min_relief_m"] and steep >= f["min_steep_share"]
            and (m.get("upslope_share") or 0.0) >= f["min_upslope_share"]):
        out.append("freeride")
    return out


# ============================================================================ grids and downloads
def zoom_for(lat: float, cfg: Optional[dict] = None) -> int:
    c = cfg or SNOWKITE_TERRAIN
    return c["zoom_low_lat"] if abs(lat) < c["zoom_split_lat"] else c["zoom_high_lat"]


def pixel_m(lat: float, z: int) -> float:
    return 40075016.686 * math.cos(math.radians(lat)) / (256 << z)


def px_x(lon: float, z: int) -> float:
    return tt.tile_x(lon, z) * 256


def px_y(lat: float, z: int) -> float:
    return tt.tile_y(lat, z) * 256


def lon_of_px(x: float, z: int) -> float:
    return x / (256 << z) * 360.0 - 180.0


def lat_of_px(y: float, z: int) -> float:
    return tt.lat_of_tile_y(y / 256, z)


def http_get(url: str, start: Optional[int] = None, end: Optional[int] = None, attempts: int = 6) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if start is not None and len(data) != end - start + 1:
                raise IOError(f"short read {len(data)} of {end - start + 1}")
            return data
        except urllib.error.HTTPError as err:
            if err.code in (403, 404):
                raise FileNotFoundError(url) from err
            if attempt == attempts - 1:
                raise
        except Exception:  # noqa: BLE001 - network hiccups: retry with backoff
            if attempt == attempts - 1:
                raise
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError("unreachable")


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.part")
    tmp.write_bytes(data)
    tmp.replace(path)


def tile_bytes(z: int, x: int, y: int) -> bytes:
    path = DOWNLOAD_DIR / "terrarium" / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        return path.read_bytes()
    data = http_get(tt.TILE_URL.format(z=z, x=x, y=y))
    _save(path, data)
    return data


@functools.lru_cache(maxsize=48)
def tile_heights(z: int, x: int, y: int) -> Any:
    """256 x 256 heights (m) of a terrarium tile; the sea floor keeps its negative height."""
    w, h, ch, depth, px = png.decode(tile_bytes(z, x, y))
    if (w, h, ch, depth) != (256, 256, 3, 8):
        raise ValueError(f"unexpected terrain tile {z}/{x}/{y}: {w}x{h}, {ch} channels, {depth} bit")
    rgb = np.frombuffer(px, dtype=np.uint8).reshape(256, 256, 3).astype(np.float32)
    return rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0


def elevation_window(z: int, x0: int, y0: int, x1: int, y1: int) -> Any:
    """Heights of the global pixel box [x0, x1) x [y0, y1) at zoom z."""
    out = np.zeros((y1 - y0, x1 - x0), np.float32)
    for ty in range(y0 // 256, (y1 - 1) // 256 + 1):
        for tx in range(x0 // 256, (x1 - 1) // 256 + 1):
            t = tile_heights(z, tx, ty)
            ax, bx = max(x0, tx * 256), min(x1, tx * 256 + 256)
            ay, by = max(y0, ty * 256), min(y1, ty * 256 + 256)
            out[ay - y0:by - y0, ax - x0:bx - x0] = t[ay - ty * 256:by - ty * 256, ax - tx * 256:bx - tx * 256]
    return out


# ---------------------------------------------------------------------------- WorldCover COG reader
def parse_tiff_ifds(buf: bytes) -> list[dict]:
    """Image directories of a classic little or big endian TIFF whose header lies inside ``buf``: width, height,
    tile size, compression, predictor and the tile byte offsets and counts."""
    bo = {b"II": "<", b"MM": ">"}.get(buf[:2])
    if bo is None or struct.unpack(bo + "H", buf[2:4])[0] != 42:
        raise ValueError("not a classic TIFF")
    sizes = {1: (1, "B"), 2: (1, "c"), 3: (2, "H"), 4: (4, "I"), 16: (8, "Q")}
    (off,) = struct.unpack(bo + "I", buf[4:8])
    out = []
    while off:
        (n,) = struct.unpack(bo + "H", buf[off:off + 2])
        tags: dict[int, Any] = {}
        for k in range(n):
            e = buf[off + 2 + 12 * k:off + 14 + 12 * k]
            tag, typ, cnt = struct.unpack(bo + "HHI", e[:8])
            if tag not in (256, 257, 259, 317, 322, 323, 324, 325) or typ not in sizes:
                continue
            size, code = sizes[typ]
            if size * cnt <= 4:
                raw = e[8:8 + size * cnt]
            else:
                (at,) = struct.unpack(bo + "I", e[8:12])
                raw = buf[at:at + size * cnt]
                if len(raw) < size * cnt:
                    raise ValueError("TIFF header longer than the bytes read")
            tags[tag] = struct.unpack(bo + code * cnt, raw)
        out.append({"width": tags[256][0], "height": tags[257][0], "compression": tags.get(259, (1,))[0],
                    "predictor": tags.get(317, (1,))[0], "tile_w": tags[322][0], "tile_h": tags[323][0],
                    "offsets": list(tags[324]), "counts": list(tags[325])})
        (off,) = struct.unpack(bo + "I", buf[off + 2 + 12 * n:off + 6 + 12 * n])
    return out


def assemble_tiles(ifd: dict, blob: bytes, blob_start: int) -> Any:
    """The full 8 bit image of one directory from the bytes that hold its deflate tiles."""
    if ifd["compression"] not in (8, 32946) or ifd["predictor"] != 1:
        raise ValueError(f"unsupported TIFF compression {ifd['compression']} predictor {ifd['predictor']}")
    tw, th = ifd["tile_w"], ifd["tile_h"]
    across, down = -(-ifd["width"] // tw), -(-ifd["height"] // th)
    full = np.zeros((down * th, across * tw), np.uint8)
    for k, (o, n) in enumerate(zip(ifd["offsets"], ifd["counts"])):
        if n:
            tile = np.frombuffer(zlib.decompress(blob[o - blob_start:o - blob_start + n]), np.uint8).reshape(th, tw)
            full[(k // across) * th:(k // across + 1) * th, (k % across) * tw:(k % across + 1) * tw] = tile
    return full[:ifd["height"], :ifd["width"]]


def worldcover_name(lat0: int, lon0: int) -> str:
    """File of the 3 degree tile whose south west corner is (lat0, lon0)."""
    return (f"ESA_WorldCover_10m_2021_v200_{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}"
            f"{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}_Map.tif")


def worldcover_fetch(lat0: int, lon0: int, level: int) -> Optional[Path]:
    """Download one overview of a WorldCover file (header, then one range request). None when the file does not
    exist (open sea)."""
    name = worldcover_name(lat0, lon0)
    blob_path = DOWNLOAD_DIR / "worldcover" / f"{name}.L{level}.bin"
    meta_path = DOWNLOAD_DIR / "worldcover" / f"{name}.L{level}.json"
    if meta_path.is_file():
        return blob_path if json.loads(meta_path.read_text())["exists"] else None
    url = WORLDCOVER_URL.format(name=name)
    try:
        head = http_get(url, 0, 131071)
    except FileNotFoundError:
        _save(meta_path, json.dumps({"exists": False}).encode())
        return None
    except IOError:
        head = http_get(url)          # a file shorter than the header read
    ifd = parse_tiff_ifds(head)[level]
    used = [(o, n) for o, n in zip(ifd["offsets"], ifd["counts"]) if n]
    start = min(o for o, _ in used)
    end = max(o + n for o, n in used) - 1
    blob = head[start:end + 1] if end < len(head) else http_get(url, start, end)
    _save(blob_path, blob)
    _save(meta_path, json.dumps({"exists": True, "start": start, "ifd": ifd}).encode())
    return blob_path


@functools.lru_cache(maxsize=6)
def worldcover_level(lat0: int, lon0: int, level: int) -> Any:
    path = worldcover_fetch(lat0, lon0, level)
    if path is None:
        return None
    meta = json.loads(path.with_suffix(".json").read_text())
    return assemble_tiles(meta["ifd"], path.read_bytes(), meta["start"])


def worldcover_tiles(west: float, south: float, east: float, north: float) -> list[tuple[int, int]]:
    return [(la, lo) for la in range(math.floor(south / 3) * 3, math.floor((north - 1e-9) / 3) * 3 + 1, 3)
            for lo in range(math.floor(west / 3) * 3, math.floor((east - 1e-9) / 3) * 3 + 1, 3)]


def cover_shares(z: int, x0: int, y0: int, x1: int, y1: int, level: int, loader: Any = None) -> Any:
    """Share of each land cover class (``COVER_NAMES``) inside every pixel of the global pixel box. Pixels with no
    land cover sample at all count as nodata."""
    load = loader or worldcover_level
    w, h, nk = x1 - x0, y1 - y0, len(COVER_NAMES)
    counts = np.zeros(w * h * nk, np.int64)
    lut = np.zeros(256, np.int64)
    for code, k in COVER_CODES.items():
        lut[code] = k
    west, east = lon_of_px(x0, z), lon_of_px(x1, z)
    north, south = lat_of_px(y0, z), lat_of_px(y1, z)
    for lat0, lon0 in worldcover_tiles(west, south, east, north):
        img = load(lat0, lon0, level)
        if img is None:
            continue
        size = img.shape[0]
        step = 3.0 / size
        c0, c1 = max(0, int((west - lon0) / step)), min(size, int((east - lon0) / step) + 1)
        r0, r1 = max(0, int((lat0 + 3 - north) / step)), min(size, int((lat0 + 3 - south) / step) + 1)
        if c0 >= c1 or r0 >= r1:
            continue
        lons = lon0 + (np.arange(c0, c1) + 0.5) * step
        lats = lat0 + 3 - (np.arange(r0, r1) + 0.5) * step
        ix = np.floor((lons + 180.0) / 360.0 * (256 << z)).astype(np.int64) - x0
        rad = np.radians(lats)
        iy = np.floor((1 - np.log(np.tan(rad) + 1 / np.cos(rad)) / math.pi) / 2 * (256 << z)).astype(np.int64) - y0
        okx, oky = (ix >= 0) & (ix < w), (iy >= 0) & (iy < h)
        block = lut[img[r0:r1, c0:c1][np.ix_(oky, okx)]]
        idx = (iy[oky][:, None] * w + ix[okx][None, :]) * nk + block
        counts += np.bincount(idx.ravel(), minlength=w * h * nk)
    counts = counts.reshape(h, w, nk).astype(np.float32)
    total = counts.sum(axis=2, keepdims=True)
    shares = counts / np.maximum(total, 1)
    shares[:, :, 0] = np.where(total[:, :, 0] > 0, shares[:, :, 0], 1.0)
    return shares


# ============================================================================ analysis
def open_water_mask(elev: Any, z: int, x0: int, y0: int, cfg: Optional[dict] = None) -> Any:
    """Pixels at the stored level of a Great Lake inside the configured box (window origin = global pixel x0, y0)."""
    ow = (cfg or SNOWKITE_TERRAIN)["open_water"]
    h, w = elev.shape
    west, south, east, north = ow["box"]
    lons = np.array([lon_of_px(x0 + k + 0.5, z) for k in range(w)])
    lats = np.array([lat_of_px(y0 + k + 0.5, z) for k in range(h)])
    inside = ((lats >= south) & (lats <= north))[:, None] & ((lons >= west) & (lons <= east))[None, :]
    level = np.zeros(elev.shape, bool)
    if inside.any():
        for value in ow["levels_m"].values():
            level |= np.abs(elev - value) <= ow["tolerance_m"]
    return inside & level


def ground(elev: Any, cover: Any, px_m: float, ice_ok: bool, from_deg: float, cfg: Optional[dict] = None,
           snow_ok: bool = True, open_water: Any = None) -> dict:
    """Every per pixel layer of a window: slope, aspect, the kiteable mask and its ingredients. Water counts only
    when the cell's lake ice is reliable (``ice_ok``); land counts only when its snow is (``snow_ok``)."""
    c = cfg or SNOWKITE_TERRAIN
    slope, aspect = slope_aspect(elev, px_m)
    tree = cover[:, :, TREE] >= c["tree_obstacle_share"]
    water = cover[:, :, WATER] >= c["water_pixel_share"]
    sea = water & (elev <= 0.5)
    if open_water is not None:
        sea |= water & open_water
    glacier = cover[:, :, ICE] >= c["ice_pixel_share"]
    kiteable = ((cover[:, :, TREE] <= c["max_tree_share"]) & (cover[:, :, BUILT] <= c["max_built_share"])
                & (cover[:, :, 0] < 0.5) & (slope < c["max_kite_slope_deg"]) & ~sea & np.where(water, ice_ok, snow_ok))
    steep = steepest_neighbor_slope(elev, px_m) >= c["steep_deg"]
    return {
        "slope": slope, "aspect": aspect, "tree": tree, "water": water, "glacier": glacier, "kiteable": kiteable,
        "steep": steep, "shadow": tree_shadow(tree, from_deg, px_m, c["tree_shadow_m"]),
        "runout": runout_mask(elev, steep, px_m, c["runout_reach_m"], c["runout_alpha_deg"]),
    }


def patch_metrics(sel: Any, elev: Any, cover: Any, g: dict, px_m: float, from_deg: float, second_deg: float,
                  cfg: Optional[dict] = None) -> dict:
    """Metrics of one patch (``sel`` = boolean mask over the window)."""
    c = cfg or SNOWKITE_TERRAIN
    rr, cc = np.nonzero(sel)
    slope, aspect, z = g["slope"][rr, cc], g["aspect"][rr, cc], elev[rr, cc]
    s1, s2, s3 = c["slope_classes_deg"]
    n = len(rr)
    flat, rolling = float(np.mean(slope < s1)), float(np.mean((slope >= s1) & (slope < s2)))
    steep_sel = (slope >= s2) & (slope < s3)
    along, across = wind_extents(cc * px_m, -rr * px_m, from_deg, px_m)
    sloped = slope >= s1
    faces = aspect[sloped]
    ne = float(np.mean((faces >= 337.5) | (faces < 112.5))) if faces.size else None
    cov = cover[rr, cc].mean(axis=0)
    typical = float(np.median(slope[sloped])) if sloped.mean() >= 0.05 else float(np.median(slope))
    m = {
        "pixels": n, "acres": n * px_m * px_m / ACRE_M2,
        "slope_share": [flat, rolling, float(np.mean(steep_sel))],
        "slope_median_deg": float(np.median(slope)), "typical_slope_deg": typical,
        "relief_m": float(z.max() - z.min()), "elev_m": float(z.mean()),
        "along_m": along, "across_m": across, "shape_ratio": across / along,
        "tree_shadow_share": float(np.mean(g["shadow"][rr, cc])),
        "runout_share": float(np.mean(g["runout"][rr, cc])),
        "aspect_10_25": aspect_histogram(aspect[steep_sel]) if steep_sel.any() else None,
        "upslope_share": upslope_share(aspect[steep_sel], from_deg, c["upslope_tolerance_deg"]),
        "upslope_share_second": upslope_share(aspect[steep_sel], second_deg, c["upslope_tolerance_deg"]),
        "north_east_share": ne,
        "cover": {name: float(cov[k]) for k, name in enumerate(COVER_NAMES) if k and cov[k] >= 0.005},
        "glacier_share": float(np.mean(g["glacier"][rr, cc])), "water_share": float(np.mean(g["water"][rr, cc])),
    }
    # the largest connected piece of ground under the first slope class inside the patch
    r_a, r_b, c_a, c_b = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
    flat_mask = sel[r_a:r_b, c_a:c_b] & (g["slope"][r_a:r_b, c_a:c_b] < s1)
    core_labels, count = label_patches(flat_mask)
    m["flat_core_acres"], m["flat_core_shadow_share"] = 0.0, None
    if count:
        sizes = np.bincount(core_labels.ravel())
        sizes[0] = 0
        core = core_labels == int(np.argmax(sizes))
        m["flat_core_acres"] = float(sizes.max() * px_m * px_m / ACRE_M2)
        m["flat_core_shadow_share"] = float(np.mean(g["shadow"][r_a:r_b, c_a:c_b][core]))
    m["steep_above"] = m["runout_share"] > 0
    m["surface"] = "lake_ice" if m["water_share"] >= 0.5 else "glacier" if m["glacier_share"] >= 0.5 else "land"
    m["classes"] = classify(m, c)
    return m


def analyse_window(z: int, x0: int, y0: int, x1: int, y1: int, from_deg: float, second_deg: float, ice_ok: bool,
                   cfg: Optional[dict] = None, snow_ok: bool = True) -> dict:
    """Layers and patches of the global pixel box [x0, x1) x [y0, y1); a margin is read around it so shadows,
    slopes and steep ground just outside count."""
    c = cfg or SNOWKITE_TERRAIN
    px = pixel_m(lat_of_px((y0 + y1) / 2, z), z)
    margin = int(math.ceil(max(c["runout_reach_m"], c["tree_shadow_m"]) / px)) + 2
    elev = elevation_window(z, x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    cover = cover_shares(z, x0 - margin, y0 - margin, x1 + margin, y1 + margin, c["worldcover_level"])
    g = ground(elev, cover, px, ice_ok, from_deg, c, snow_ok, open_water_mask(elev, z, x0 - margin, y0 - margin, c))
    inner = np.zeros(elev.shape, bool)
    inner[margin:-margin, margin:-margin] = True
    labels, count = label_patches(g["kiteable"] & inner)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    min_px = c["min_patch_acres"] * ACRE_M2 / (px * px)
    patches = []
    for k in np.flatnonzero(sizes >= min_px).tolist():
        if k == 0:
            continue
        sel = labels == k
        m = patch_metrics(sel, elev, cover, g, px, from_deg, second_deg, c)
        rr, cc = np.nonzero(sel)
        near = int(np.argmin((rr - rr.mean()) ** 2 + (cc - cc.mean()) ** 2))    # a pixel of the patch near its center
        gx, gy = cc + x0 - margin, rr + y0 - margin
        m["lon"], m["lat"] = lon_of_px(gx[near] + 0.5, z), lat_of_px(gy[near] + 0.5, z)
        r_a, r_b, c_a, c_b = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
        ring = simplify_ring(outline(sel[r_a:r_b, c_a:c_b]), c["max_outline_points"])
        m["outline"] = [[round(lon_of_px(x + c_a + x0 - margin, z), 4), round(lat_of_px(y + r_a + y0 - margin, z), 4)]
                        for x, y in ring]
        # pixels on the box edge, keyed so the neighbour box produces the same key (for joining across cells)
        m["_border"] = ([f"v{z}:{x0}:{y}" for y in gy[gx == x0].tolist()] + [f"v{z}:{x1}:{y}" for y in gy[gx == x1 - 1].tolist()]
                        + [f"h{z}:{y0}:{x}" for x in gx[gy == y0].tolist()] + [f"h{z}:{y1}:{x}" for x in gx[gy == y1 - 1].tolist()])
        m["edge"] = bool(m["_border"])
        patches.append(m)
    core = (slice(margin, -margin), slice(margin, -margin))
    return {"z": z, "px_m": px, "margin": margin, "elev": elev, "cover": cover, "ground": g, "labels": labels,
            "patches": patches,
            "open_share": float(g["kiteable"][core].mean()), "tree_share": float(cover[core][:, :, TREE].mean()),
            "flat_open_share": float((g["kiteable"][core] & (g["slope"][core] < c["slope_classes_deg"][0])).mean())}


def cell_box(cell: dict, cfg: Optional[dict] = None) -> tuple[int, int, int, int, int]:
    """(zoom, x0, y0, x1, y1): the global pixel box of an ERA5 cell. Neighbouring cells share their edges."""
    z = zoom_for(cell["lat"], cfg)
    half = cell["step"] / 2
    return (z, round(px_x(cell["lon"] - half, z)), round(px_y(cell["lat"] + half, z)),
            round(px_x(cell["lon"] + half, z)), round(px_y(cell["lat"] - half, z)))


def process_cell(cell: dict, cfg: Optional[dict] = None) -> dict:
    c = cfg or SNOWKITE_TERRAIN
    z, x0, y0, x1, y1 = cell_box(cell, c)
    res = analyse_window(z, x0, y0, x1, y1, cell["dir"] * 45.0, cell["dir2"] * 45.0, cell["ice_ok"], c, cell["snow_ok"])
    out = []
    for k, m in enumerate(sorted(res["patches"], key=lambda p: -p["acres"]), 1):
        v = cell["median_wind_ms"]
        kite = None
        if v:
            kite = {"flat": [round(a, 1) for a in kite_size_m2(v, 0.0, m["elev_m"], cfg=c)],
                    "slope_deg": round(m["typical_slope_deg"], 1),
                    "on_slope": [round(a, 1) for a in kite_size_m2(v, m["typical_slope_deg"], m["elev_m"], cfg=c)]}
        m.update({"id": f"r{cell['row']}c{cell['col']}-{k}", "cell": [cell["row"], cell["col"]], "state": cell["state"],
                  "kite_m2": kite})
        out.append(m)
    return {"cell": cell, "patches": out, "open_share": res["open_share"], "tree_share": res["tree_share"],
            "flat_open_share": res["flat_open_share"], "px_m": res["px_m"]}


def _process_group(args: tuple[list[dict], dict]) -> list[dict]:
    cells, cfg = args
    return [process_cell(cell, cfg) for cell in cells]


def join_groups(patches: list[dict]) -> None:
    """Give every patch a ``group`` (patches that touch across cell edges) and the group's ``connected_acres``."""
    parent = list(range(len(patches)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    seen: dict[str, int] = {}
    for k, p in enumerate(patches):
        for key in p.pop("_border", []):
            if key in seen:
                a, b = find(seen[key]), find(k)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                seen[key] = k
    total: dict[int, float] = {}
    for k, p in enumerate(patches):
        total[find(k)] = total.get(find(k), 0.0) + p["acres"]
    for k, p in enumerate(patches):
        p["group"] = patches[find(k)]["id"]
        p["connected_acres"] = total[find(k)]


def rank_patch(p: dict, cell: dict) -> float:
    """Simple rank: size rating points (1 to 5, from connected acres) x usable wind share x snow or ice share."""
    cover = cell["ice_share"] if p["surface"] == "lake_ice" else cell["snow_share"]
    return SIZE_POINTS[p["size"]] * cell["wind_share"] * cover


def _round(v: Any, digits: int) -> Any:
    return None if v is None else round(v, digits)


def patch_record(p: dict, cell: dict) -> dict:
    """The compact committed record of a patch."""
    hist = p["aspect_10_25"]
    total = sum(hist) if hist else 0
    return {
        "id": p["id"], "cell": p["cell"], "state": p["state"], "lat": round(p["lat"], 4), "lon": round(p["lon"], 4),
        "acres": round(p["acres"]), "connected_acres": round(p["connected_acres"]), "group": p["group"],
        "edge": p["edge"], "size": p["size"], "classes": p["classes"], "surface": p["surface"], "rank": round(p["rank"], 3),
        "slope_share": [round(v, 3) for v in p["slope_share"]], "slope_median_deg": round(p["slope_median_deg"], 1),
        "flat_core_acres": round(p["flat_core_acres"]), "relief_m": round(p["relief_m"]), "elev_m": round(p["elev_m"]),
        "wind": {"from": SECTORS[cell["dir"]], "from_share": cell["dir_share"], "second": SECTORS[cell["dir2"]],
                 "second_share": cell["dir2_share"], "usable_share": cell["wind_share"],
                 "median_usable_ms": _round(cell["median_wind_ms"], 1),
                 "along_m": round(p["along_m"]), "across_m": round(p["across_m"]), "shape_ratio": round(p["shape_ratio"], 2)},
        "snow_share": cell["snow_share"], "ice_share": cell["ice_share"],
        "tree_shadow_share": round(p["tree_shadow_share"], 3),
        "steep_above": p["steep_above"], "runout_share": round(p["runout_share"], 3),
        "aspect_10_25": [round(v / total, 2) for v in hist] if total else None,
        "upslope_share": _round(p["upslope_share"], 2), "upslope_share_second": _round(p["upslope_share_second"], 2),
        "north_east_share": _round(p["north_east_share"], 2),
        "cover": {k: round(v, 2) for k, v in p["cover"].items()},
        "kite_m2": p["kite_m2"],
        "outline": p["outline"],
    }


# ============================================================================ commands
def needed_downloads(cells: list[dict], cfg: dict) -> tuple[set, set]:
    tiles, covers = set(), set()
    for cell in cells:
        z, x0, y0, x1, y1 = cell_box(cell, cfg)
        px = pixel_m(cell["lat"], z)
        m = int(math.ceil(max(cfg["runout_reach_m"], cfg["tree_shadow_m"]) / px)) + 2
        for ty in range((y0 - m) // 256, (y1 + m - 1) // 256 + 1):
            for tx in range((x0 - m) // 256, (x1 + m - 1) // 256 + 1):
                tiles.add((z, tx, ty))
        covers.update(worldcover_tiles(lon_of_px(x0 - m, z), lat_of_px(y1 + m, z), lon_of_px(x1 + m, z), lat_of_px(y0 - m, z)))
    return tiles, covers


def fetch(count: Optional[int], workers: int, max_patches: Optional[int] = None, cfg: Optional[dict] = None,
          log: Any = print) -> dict:
    c = dict(cfg or SNOWKITE_TERRAIN)
    extract = json.loads(ERA5_EXTRACT.read_text(encoding="utf-8"))
    candidates = candidate_cells(extract, c)
    cells = candidates if count is None else candidates[:count]
    log(f"{len(candidates)} candidate cells, processing {len(cells)}")
    tiles, covers = needed_downloads(cells, c)
    log(f"{len(tiles)} terrain tiles, {len(covers)} WorldCover files")
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda t: len(tile_bytes(*t)), sorted(tiles)))
        list(pool.map(lambda t: worldcover_fetch(t[0], t[1], c["worldcover_level"]), sorted(covers)))
    log(f"downloads ready in {time.time() - t0:.0f} s")
    # group neighbours so each worker keeps its tiles and land cover in memory
    groups: dict[tuple, list[dict]] = {}
    for cell in cells:
        groups.setdefault((math.floor(cell["lat"] / 3), math.floor(cell["lon"] / 3)), []).append(cell)
    results = []
    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        for n, part in enumerate(pool.map(_process_group, [(g, c) for g in groups.values()]), 1):
            results += part
            log(f"group {n} of {len(groups)}: {len(results)} cells, {time.time() - t0:.0f} s")
    results.sort(key=lambda r: (r["cell"]["row"], r["cell"]["col"]))
    found = sum(len(r["patches"]) for r in results)
    # join every patch into its group first, then keep the largest few of each cell
    everything = [p for r in results for p in r["patches"]]
    join_groups(everything)
    patches = [p for r in results for p in r["patches"][:c["max_patches_per_cell"]]]
    log(f"{found} patches of {c['min_patch_acres']:g} acres or more; {len(patches)} after keeping the "
        f"{c['max_patches_per_cell']} largest of each cell")
    cell_of = {(r["cell"]["row"], r["cell"]["col"]): r["cell"] for r in results}
    for p in patches:
        p["size"] = size_rating(p["connected_acres"], c)
        p["rank"] = rank_patch(p, cell_of[tuple(p["cell"])])
    today = dt.date.today().isoformat()
    limit = max_patches or c["max_patches_per_region"]
    by_region: dict[str, list[dict]] = {}
    found_in: dict[str, int] = {}
    for p in everything:
        region = REGION_OF_STATE.get(p["state"], "midwest")
        found_in[region] = found_in.get(region, 0) + 1
    for p in patches:
        by_region.setdefault(REGION_OF_STATE.get(p["state"], "midwest"), []).append(p)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("patches_*.json"):
        old.unlink()
    files = {}
    for region, items in sorted(by_region.items()):
        items.sort(key=lambda p: (-p["rank"], -p["acres"], p["id"]))
        kept = items[:limit]
        body = {
            "region": region, "as_of": today, "confidence": "estimated",
            "source_ids": ["aws-terrain-tiles", "esa-worldcover-2021", "era5-arco-surface"],
            "note": "Computed patches of open ground. Every value is a model estimate; records inherit as_of, "
                    "confidence and source_ids from this header. See pipeline/snowkite_terrain.py.",
            "patches_found": found_in[region], "patches_kept": len(kept),
            "cut": (f"the {c['max_patches_per_cell']} largest patches of each cell ({len(items)} patches), then the "
                    f"{limit} best by rank (size rating from connected acres x usable wind share x snow or ice share, "
                    "then acres)"),
            "patches": [patch_record(p, cell_of[tuple(p["cell"])]) for p in kept],
        }
        path = OUT_DIR / f"patches_{region}.json"
        path.write_text(json.dumps(body, separators=(",", ":")) + "\n", encoding="utf-8")
        files[region] = {"file": path.name, "patches_found": found_in[region], "after_cell_cap": len(items),
                         "patches_kept": len(kept), "bytes": path.stat().st_size}
    (OUT_DIR / "cells_processed.json").write_text(json.dumps({
        "as_of": today, "confidence": "estimated",
        "source_ids": ["era5-arco-surface", "aws-terrain-tiles", "esa-worldcover-2021"],
        "candidates": len(candidates), "processed": len(results),
        "order": "candidates ranked by usable wind share x the larger of snow share and lake ice share; the best were processed",
        "columns": ["row", "col", "state", "wind_share", "snow_share", "ice_share", "open_share", "flat_open_share",
                    "tree_share", "patches", "patch_acres", "largest_patch_acres"],
        "cells": [[r["cell"]["row"], r["cell"]["col"], r["cell"]["state"], r["cell"]["wind_share"], r["cell"]["snow_share"],
                   r["cell"]["ice_share"], round(r["open_share"], 3), round(r["flat_open_share"], 3),
                   round(r["tree_share"], 3), len(r["patches"]), round(sum(p["acres"] for p in r["patches"])),
                   round(max((p["acres"] for p in r["patches"]), default=0))] for r in results],
    }, separators=(",", ":")) + "\n", encoding="utf-8")
    source = {
        "sources": [
            {"id": "aws-terrain-tiles", "url": "https://registry.opendata.aws/terrain-tiles/",
             "title": "Terrain Tiles on AWS (Mapzen terrarium elevation tiles)", "accessed_date": today,
             "license": "public domain (US sources), attribution in the note",
             "note": (f"Heights from the terrarium tiles at zoom {c['zoom_low_lat']} (zoom {c['zoom_high_lat']} north of "
                      f"{c['zoom_split_lat']:g} N), {tt.TILE_URL}. In the United States the tiles are built from USGS 3DEP "
                      "(formerly NED), SRTM and GMTED2010 (courtesy of the U.S. Geological Survey) and NOAA ETOPO1, all "
                      "public domain; ArcticDEM data were created from DigitalGlobe, Inc., imagery and funded under "
                      "National Science Foundation awards 1043681, 1559691 and 1542736.")},
            {"id": "esa-worldcover-2021", "url": "https://esa-worldcover.org/en/data-access",
             "title": "ESA WorldCover 10 m 2021 v200", "accessed_date": today, "license": "CC BY 4.0",
             "note": ("Zanaga, D., Van De Kerchove, R., Daems, D., et al. (2022). ESA WorldCover 10 m 2021 v200. "
                      "doi:10.5281/zenodo.7254221. Contains modified Copernicus Sentinel data (2021) processed by the ESA "
                      f"WorldCover consortium. Read as overview {c['worldcover_level']} (80 m pixels) of the cloud optimized "
                      "files at " + WORLDCOVER_URL.format(name="<tile>") + ".")},
            {"id": "era5-arco-surface", "url": "https://cloud.google.com/storage/docs/public-datasets/era5",
             "title": "ERA5 surface extract (data/raw/climate/era5_surface)", "accessed_date": extract["as_of"],
             "license": "Copernicus licence (CC BY 4.0 compatible); attribution required",
             "note": "Wind, snow and lake ice per 0.25 degree cell; picks the cells and gives each patch its wind."},
        ],
        "not_used": ("Hansen Global Forest Change tree cover 2000 (CC BY 4.0, Hansen et al. 2013, Science 342:850) was "
                     "considered for canopy percent. WorldCover was chosen because its overviews give 80 m land cover in "
                     "one small request per 3 degree file, it is from 2021, and it also maps water, built and permanent ice."),
        "confidence": "estimated",
        "as_of": today,
        "model": c,
        "regions": files,
    }
    (OUT_DIR / "source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return source


def quicklook(lat: float, lon: float, half: float, out: Path, cfg: Optional[dict] = None, scale: int = 2,
              snow_rule: bool = False) -> dict:
    """Render the layers around a point to a PNG (the project's own encoder) to check the mask by eye. Colours:
    pale blue = kiteable under 3 degrees, green = 3 to 10, orange = 10 to 25, dark green = trees, grey = other
    ground too steep or built, red = 30 degrees or steeper, purple tint = tree turbulence shadow, pink tint = under
    steep ground, blue = water that does not count, black line = patch edge. Land shows whatever the cell's snow
    says unless ``snow_rule`` is set, so the terrain mask of any place can be checked."""
    c = cfg or SNOWKITE_TERRAIN
    extract = json.loads(ERA5_EXTRACT.read_text(encoding="utf-8"))
    cell = cell_at(extract, lat, lon, c) or {"dir": 6, "dir2": 7, "ice_ok": False}
    z = zoom_for(lat, c)
    x0, x1 = round(px_x(lon - half, z)), round(px_x(lon + half, z))
    y0, y1 = round(px_y(lat + half, z)), round(px_y(lat - half, z))
    res = analyse_window(z, x0, y0, x1, y1, cell["dir"] * 45.0, cell["dir2"] * 45.0, cell["ice_ok"], c,
                         cell.get("snow_ok", True) if snow_rule else True)
    m = res["margin"]
    core = (slice(m, -m), slice(m, -m))
    g = {k: v[core] for k, v in res["ground"].items()}
    elev, labels = res["elev"][core], res["labels"][core]
    s1, s2, _ = c["slope_classes_deg"]
    # hillshade from the north west
    dzr, dzc = np.gradient(elev.astype(np.float64), res["px_m"])
    shade = np.clip(0.75 + 1.5 * (dzc + dzr) / math.sqrt(2), 0.45, 1.05)
    rgb = np.empty(elev.shape + (3,), np.float64)
    rgb[:] = (150, 150, 150)
    rgb[g["tree"]] = (30, 90, 40)
    rgb[g["water"]] = (60, 110, 200)
    k = g["kiteable"]
    rgb[k & (g["slope"] < s1)] = (200, 230, 250)
    rgb[k & (g["slope"] >= s1) & (g["slope"] < s2)] = (150, 215, 130)
    rgb[k & (g["slope"] >= s2)] = (250, 170, 60)
    rgb[k & g["shadow"]] = rgb[k & g["shadow"]] * 0.6 + np.array((150, 60, 200)) * 0.4
    rgb[k & g["runout"]] = rgb[k & g["runout"]] * 0.6 + np.array((255, 80, 160)) * 0.4
    rgb[g["steep"]] = (210, 30, 30)
    rgb *= shade[:, :, None]
    big = np.bincount(labels.ravel())
    keep = np.isin(labels, np.flatnonzero(big >= c["min_patch_acres"] * ACRE_M2 / res["px_m"] ** 2)) & (labels > 0)
    edge = keep & ((shifted(labels, 1, 0) != labels) | (shifted(labels, 0, 1) != labels)
                   | (shifted(labels, -1, 0) != labels) | (shifted(labels, 0, -1) != labels))
    rgb[edge] = (0, 0, 0)
    img = np.clip(rgb, 0, 255).astype(np.uint8).repeat(scale, axis=0).repeat(scale, axis=1)
    h, w = img.shape[:2]
    rgba = np.dstack([img, np.full((h, w), 255, np.uint8)])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png.encode_rgba(w, h, rgba.tobytes()))
    return {"cell": cell, "open_share": res["open_share"], "tree_share": res["tree_share"], "px_m": res["px_m"],
            "patches": sorted(res["patches"], key=lambda p: -p["acres"])}


def summary() -> None:
    src = json.loads((OUT_DIR / "source.json").read_text(encoding="utf-8"))
    for region, meta in src["regions"].items():
        print(f"{region}: {meta['patches_kept']} of {meta['patches_found']} patches, {meta['bytes']} bytes")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.snowkite_terrain")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--cells", default=str(SNOWKITE_TERRAIN["default_cells"]), help="how many of the best cells, or all")
    f.add_argument("--workers", type=int, default=4)
    f.add_argument("--max-patches", type=int, default=None, help="patches kept per region")
    q = sub.add_parser("quicklook")
    q.add_argument("--lat", type=float, required=True)
    q.add_argument("--lon", type=float, required=True)
    q.add_argument("--half", type=float, default=0.2, help="half size of the box in degrees")
    q.add_argument("--out", type=Path, required=True)
    sub.add_parser("summary")
    args = ap.parse_args(argv)
    if np is None:
        print("ERROR: numpy is required (pip install numpy)", file=sys.stderr)
        return 1
    if args.cmd == "fetch":
        src = fetch(None if args.cells == "all" else int(args.cells), args.workers, args.max_patches)
        print(json.dumps(src["regions"]))
    elif args.cmd == "quicklook":
        res = quicklook(args.lat, args.lon, args.half, args.out)
        print(json.dumps({"cell": res["cell"], "open_share": round(res["open_share"], 3), "px_m": round(res["px_m"], 1),
                          "patches": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()
                                       if k not in ("outline", "_border", "cover", "aspect_10_25")} for p in res["patches"][:6]]}))
    else:
        summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
