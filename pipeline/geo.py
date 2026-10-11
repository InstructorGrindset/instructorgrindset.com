"""Small geodesy helpers (no third party geometry libraries)."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQUATOR = 111.320

LonLat = tuple[float, float]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


class LocalProjection:
    """Equirectangular projection to kilometres around a reference point."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0 = lon0
        self.lat0 = lat0
        self.kx = KM_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0))
        self.ky = KM_PER_DEG_LAT

    @classmethod
    def around(cls, points: Sequence[LonLat]) -> "LocalProjection":
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        return cls((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def to_lonlat(self, x: float, y: float) -> LonLat:
        return (self.lon0 + x / self.kx, self.lat0 + y / self.ky)


def point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """Ray casting point in polygon test for one linear ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def iter_polygons(geometry: dict) -> Iterable[list]:
    """Yield the rings list of each polygon in a Polygon or MultiPolygon."""
    t = geometry.get("type")
    if t == "Polygon":
        yield geometry["coordinates"]
    elif t == "MultiPolygon":
        yield from geometry["coordinates"]


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    for rings in iter_polygons(geometry):
        if rings and point_in_ring(lon, lat, rings[0]) and not any(
            point_in_ring(lon, lat, hole) for hole in rings[1:]
        ):
            return True
    return False


def ring_centroid(ring: Sequence[Sequence[float]]) -> tuple[LonLat, float]:
    """Area weighted centroid and absolute planar area (degrees^2) of a ring."""
    a = cx = cy = 0.0
    for (x0, y0, *_), (x1, y1, *_) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-15:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return ((sum(xs) / len(xs), sum(ys) / len(ys)), 0.0)
    a *= 0.5
    return ((cx / (6 * a), cy / (6 * a)), abs(a))


def representative_point(geometry: dict) -> LonLat | None:
    """Point for a Point geometry, else centroid of the largest polygon."""
    t = geometry.get("type") if geometry else None
    if t == "Point":
        lon, lat = geometry["coordinates"][:2]
        return (float(lon), float(lat))
    if t == "MultiPoint" and geometry["coordinates"]:
        lon, lat = geometry["coordinates"][0][:2]
        return (float(lon), float(lat))
    best: tuple[LonLat, float] | None = None
    for rings in iter_polygons(geometry or {}):
        if not rings:
            continue
        c, area = ring_centroid(rings[0])
        if best is None or area > best[1]:
            best = (c, area)
    return best[0] if best else None


def distance_to_geometry_km(lat: float, lon: float, geometry: dict, rep: LonLat | None) -> float:
    """0 when inside a polygon, else the distance to the nearest vertex or the representative point."""
    if point_in_geometry(lon, lat, geometry or {}):
        return 0.0
    best = math.inf
    if rep is not None:
        best = haversine_km(lat, lon, rep[1], rep[0])
    for rings in iter_polygons(geometry or {}):
        for ring in rings[:1]:
            for p in ring:
                best = min(best, haversine_km(lat, lon, p[1], p[0]))
    return best
