"""Lines on the terrain: roads leveled into the ground, ski runs and roads painted as terrain materials.

Every line is a polyline of world points (metres, see ``geo``). ``corridor`` finds, for each vertex near a line, its
distance to the line and the line's height there. Roads use it twice: the height along each road is the ground
height averaged over ``PROFILE_WINDOW_M`` of road, then vertices within half the road width take that height and
vertices in the shoulder blend from it back to the ground. Bridges and tunnels are left out before this step (the
ground under a bridge is a valley, the ground over a tunnel is a hill).

The drawing widths and the slope that turns grass into rock are assumptions of this tool, kept here in one place.
They shape how the level looks; they are not measurements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from tools.beamng.geo import Grid

# Materials, in the order of the .ter layer indices.
GRASS, ROCK, SKI_RUN, DIRT, ASPHALT = range(5)

ROCK_SLOPE_DEG = 38.0          # steeper ground is drawn as rock
RUN_WIDTH_M = 30.0             # OpenSkiMap runs are centerlines; drawn this wide
SHOULDER_M = 8.0               # blend from road height back to the ground beyond the road edge
PROFILE_WINDOW_M = 60.0        # road height = ground height averaged over this much road
STEP_M = 4.0                   # road lines are resampled at this spacing before leveling


@dataclass(frozen=True)
class Line:
    kind: str                   # road class (OpenStreetMap highway value), "transit" or "run"
    points: np.ndarray          # (k, 2) world x, y in metres
    width_m: float
    material: int
    level: bool = True          # roads are leveled into the terrain; runs are only painted
    name: Optional[str] = None
    tags: dict = field(default_factory=dict)


def resample(points: np.ndarray, step: float) -> np.ndarray:
    """The polyline with points at most ``step`` apart (original vertices kept)."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.hypot(*(b - a)) / step)))
        t = np.arange(1, n + 1)[:, None] / n
        out.append(a + (b - a) * t)
    return np.concatenate(out)


def running_mean(values: np.ndarray, chain: np.ndarray, window: float) -> np.ndarray:
    """Mean of ``values`` over ``window`` metres of chainage centered on each point."""
    c = np.concatenate([[0.0], np.cumsum(values)])
    lo = np.searchsorted(chain, chain - window / 2, side="left")
    hi = np.searchsorted(chain, chain + window / 2, side="right")
    return (c[hi] - c[lo]) / (hi - lo)


def corridor(grid: Grid, points: np.ndarray, reach: float, values: Optional[np.ndarray] = None):
    """Yield (rows slice, cols slice, distance, value) for each segment: the distance from the vertices of the
    segment's box (grown by ``reach``) to the segment, and ``values`` (one per point) interpolated along it."""
    axis = grid.axis()
    s = grid.square_m
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        lo = np.minimum(a, b) - reach
        hi = np.maximum(a, b) + reach
        i0, j0 = np.maximum(0, np.floor((lo - grid.origin) / s).astype(int))
        i1, j1 = np.minimum(grid.size, np.ceil((hi - grid.origin) / s).astype(int) + 1)
        if i0 >= i1 or j0 >= j1:
            continue
        px = axis[i0:i1][None, :] - a[0]
        py = axis[j0:j1][:, None] - a[1]
        d = b - a
        dd = float(d @ d)
        t = np.clip((px * d[0] + py * d[1]) / dd, 0, 1) if dd > 0 else np.zeros_like(px + py)
        dist = np.hypot(px - t * d[0], py - t * d[1])
        val = None if values is None else values[k] + (values[k + 1] - values[k]) * t
        yield slice(j0, j1), slice(i0, i1), dist, val


def level_roads(grid: Grid, ground: np.ndarray, lines: Iterable[Line], shoulder: float = SHOULDER_M,
                window: float = PROFILE_WINDOW_M, step: float = STEP_M) -> np.ndarray:
    """Ground heights with every leveled line cut or filled to its smoothed profile."""
    weight = np.zeros_like(ground)
    target = np.zeros_like(ground)
    for line in lines:
        if not line.level or len(line.points) < 2:
            continue
        pts = resample(line.points, step)
        chain = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        profile = running_mean(grid.sample(ground, pts[:, 0], pts[:, 1]), chain, window)
        half = line.width_m / 2
        for rows, cols, dist, val in corridor(grid, pts, half + shoulder, profile):
            w = np.clip(1 - (dist - half) / shoulder, 0, 1) if shoulder > 0 else (dist <= half).astype(float)
            better = w > weight[rows, cols]
            weight[rows, cols] = np.where(better, w, weight[rows, cols])
            target[rows, cols] = np.where(better, val, target[rows, cols])
    w = weight * weight * (3 - 2 * weight)  # smoothstep: no kink where the shoulder meets the ground
    return ground * (1 - w) + target * w


def slope_deg(grid: Grid, h: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(h, grid.square_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def layers(grid: Grid, h: np.ndarray, lines: Sequence[Line], rock_slope: float = ROCK_SLOPE_DEG) -> np.ndarray:
    """Material index of every vertex: rock on steep ground, grass elsewhere, then each line painted over its
    width (runs first, then dirt roads, then paved roads, so a road stays visible where it crosses a run)."""
    out = np.where(slope_deg(grid, h) > rock_slope, ROCK, GRASS).astype(np.uint8)
    for mat in (SKI_RUN, DIRT, ASPHALT):
        for line in lines:
            if line.material != mat or len(line.points) < 2:
                continue
            half = line.width_m / 2
            for rows, cols, dist, _ in corridor(grid, resample(line.points, 4 * grid.square_m), half):
                out[rows, cols] = np.where(dist <= half, mat, out[rows, cols])
    return out


def nearest_on_lines(lines: Sequence[Line], x: float, y: float, kinds: Optional[set] = None):
    """(distance, point, unit direction) of the closest point on any leveled line, or None without lines."""
    best = None
    p = np.array([x, y])
    for line in lines:
        if not line.level or len(line.points) < 2 or (kinds and line.kind not in kinds):
            continue
        a, b = line.points[:-1], line.points[1:]
        d = b - a
        dd = np.maximum((d * d).sum(axis=1), 1e-12)
        t = np.clip(((p - a) * d).sum(axis=1) / dd, 0, 1)
        q = a + d * t[:, None]
        dist = np.hypot(*(q - p).T)
        k = int(np.argmin(dist))
        if best is None or dist[k] < best[0]:
            best = (float(dist[k]), q[k], d[k] / np.sqrt(dd[k]))
    return best
