"""Local metric frame and the terrain grid.

``LocalFrame`` maps longitude and latitude to metres east (x) and north (y) of a center point on the WGS84
ellipsoid. x is measured along each point's own parallel and y along the central meridian, so east west distances
along every parallel and north south distances are true; over a 10 km level the remaining shear is under 0.05
degrees. BeamNG world x points east and y points north, so these metres are the level's world coordinates.

``Grid`` is the square terrain: ``size`` vertices a side, ``square_m`` metres apart, centered on the frame origin.
Vertex (i, j) sits at x = x0 + i * square_m, y = y0 + j * square_m, and arrays index it as ``[j, i]`` with row 0 at
the south edge, the order the BeamNG .ter file stores heights in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def meridian_radius(lat_deg: float) -> float:
    """Radius of curvature in the meridian (m) at a latitude."""
    s = math.sin(math.radians(lat_deg))
    return WGS84_A * (1 - WGS84_E2) / (1 - WGS84_E2 * s * s) ** 1.5


def normal_radius(lat_deg):
    """Radius of curvature in the prime vertical (m); accepts arrays."""
    s = np.sin(np.radians(lat_deg))
    return WGS84_A / np.sqrt(1 - WGS84_E2 * s * s)


@dataclass(frozen=True)
class LocalFrame:
    lon0: float
    lat0: float

    @property
    def m_per_deg_lat(self) -> float:
        return meridian_radius(self.lat0) * math.pi / 180

    def m_per_deg_lon(self, lat):
        return normal_radius(lat) * np.cos(np.radians(lat)) * math.pi / 180

    def to_xy(self, lon, lat):
        lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
        return (lon - self.lon0) * self.m_per_deg_lon(lat), (lat - self.lat0) * self.m_per_deg_lat

    def to_lonlat(self, x, y):
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        lat = self.lat0 + y / self.m_per_deg_lat
        return self.lon0 + x / self.m_per_deg_lon(lat), lat


MIN_SIZE, MAX_SIZE = 256, 8192


@dataclass(frozen=True)
class Grid:
    size: int
    square_m: float

    def __post_init__(self):
        if self.size < MIN_SIZE or self.size > MAX_SIZE or self.size & (self.size - 1):
            raise ValueError(f"terrain size must be a power of two from {MIN_SIZE} to {MAX_SIZE}, got {self.size}")
        if not self.square_m > 0:
            raise ValueError(f"square size must be positive, got {self.square_m}")

    @property
    def extent_m(self) -> float:
        return self.size * self.square_m

    @property
    def origin(self) -> float:
        """World x and y of vertex (0, 0), the south west corner."""
        return -self.extent_m / 2

    def axis(self) -> np.ndarray:
        """World coordinate of each vertex column (x) or row (y)."""
        return self.origin + np.arange(self.size) * self.square_m

    def index(self, x, y):
        """Fractional (i, j) grid position of world points."""
        return (np.asarray(x) - self.origin) / self.square_m, (np.asarray(y) - self.origin) / self.square_m

    def contains(self, x: float, y: float) -> bool:
        i, j = self.index(x, y)
        return bool(0 <= i <= self.size - 1 and 0 <= j <= self.size - 1)

    def sample(self, values: np.ndarray, x, y):
        """Bilinear value of a ``[j, i]`` grid array at world points (clamped to the edge)."""
        i, j = self.index(x, y)
        n = self.size
        i = np.clip(i, 0, n - 1)
        j = np.clip(j, 0, n - 1)
        i0 = np.minimum(np.floor(i).astype(int), n - 2)
        j0 = np.minimum(np.floor(j).astype(int), n - 2)
        ti, tj = i - i0, j - j0
        v00, v10 = values[j0, i0], values[j0, i0 + 1]
        v01, v11 = values[j0 + 1, i0], values[j0 + 1, i0 + 1]
        return (v00 * (1 - ti) + v10 * ti) * (1 - tj) + (v01 * (1 - ti) + v11 * ti) * tj
