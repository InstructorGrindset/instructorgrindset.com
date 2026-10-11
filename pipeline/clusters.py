"""Cluster assignment and polygon generation (convex hull + rounded buffer).

Pure Python: monotone chain hull in a local equirectangular km projection,
buffered by offsetting each hull edge outward and joining edges with circular
arcs (the Minkowski sum of a convex polygon and a disc), then projected back
to lon/lat.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .config import ClustersConfig
from .geo import LocalProjection, LonLat, haversine_km

Point = tuple[float, float]


# ------------------------------------------------------------------ geometry
def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: Iterable[Point]) -> list[Point]:
    """Andrew's monotone chain. Counter clockwise, no repeated closing point.

    Collinear points on the hull boundary are dropped. Returns 1 point for a
    single input point and 2 for collinear input.
    """
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts
    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return hull


def buffer_hull(hull: Sequence[Point], radius: float, segments: int = 32) -> list[Point]:
    """Rounded outward offset of a CCW convex hull. Returns a closed CCW ring.

    A single point becomes a circle; two points become a stadium.
    """
    if not hull:
        raise ValueError("cannot buffer an empty hull")
    if radius <= 0:
        raise ValueError("radius must be positive")
    step = 2 * math.pi / segments
    if len(hull) == 1:
        cx, cy = hull[0]
        ring = [(cx + radius * math.cos(i * step), cy + radius * math.sin(i * step)) for i in range(segments)]
        return ring + [ring[0]]

    n = len(hull)

    def normal_angle(a: Point, b: Point) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        return math.atan2(-dx, dy)  # outward normal (dy, -dx) for a CCW ring

    ring: list[Point] = []
    for i in range(n):
        prev_pt, cur, nxt = hull[i - 1], hull[i], hull[(i + 1) % n]
        a0 = normal_angle(prev_pt, cur)
        a1 = normal_angle(cur, nxt)
        sweep = (a1 - a0) % (2 * math.pi)
        k = max(1, math.ceil(sweep / step - 1e-9))
        for j in range(k + 1):
            t = a0 + sweep * j / k
            ring.append((cur[0] + radius * math.cos(t), cur[1] + radius * math.sin(t)))
    # remove consecutive duplicates (zero sweep joins)
    cleaned: list[Point] = []
    for p in ring:
        if not cleaned or math.dist(p, cleaned[-1]) > 1e-9:
            cleaned.append(p)
    if math.dist(cleaned[0], cleaned[-1]) <= 1e-9:
        cleaned.pop()
    return cleaned + [cleaned[0]]


def ring_area(ring: Sequence[Point]) -> float:
    """Signed shoelace area; positive for counter clockwise."""
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))


def cluster_polygon(points: Sequence[LonLat], buffer_km: float, segments: int = 32, decimals: int = 5) -> dict:
    """GeoJSON Polygon: convex hull of lon/lat points buffered by ``buffer_km``."""
    if not points:
        raise ValueError("cluster has no points")
    proj = LocalProjection.around(points)
    hull = convex_hull(proj.to_xy(lon, lat) for lon, lat in points)
    ring_xy = buffer_hull(hull, buffer_km, segments)
    ring = [proj.to_lonlat(x, y) for x, y in ring_xy]
    coords = [[round(lon, decimals), round(lat, decimals)] for lon, lat in ring]
    coords[-1] = coords[0]
    return {"type": "Polygon", "coordinates": [coords]}


# ---------------------------------------------------------------- assignment
@dataclass
class Member:
    id: str
    state: str
    lat: float
    lon: float
    tier: str
    cluster_hint: Optional[str] = None


@dataclass
class ClusterInfo:
    id: str
    name: str
    states: list[str]
    resort_ids: list[str] = field(default_factory=list)
    polygon: Optional[dict] = None
    sources: list[str] = field(default_factory=list)
    fallback: bool = False


class ClusterAssignmentError(ValueError):
    pass


def fallback_cluster_id(cfg: ClustersConfig, state: str) -> str:
    return f"{cfg.membership.fallback_id_prefix}-{state.lower()}"


def assign_clusters(members: Sequence[Member], cfg: ClustersConfig, state_names: dict[str, str]) -> dict[str, str]:
    """Map every resort id to exactly one cluster id.

    * Tier A: ``cluster_hint`` must be a configured cluster that lists the resort's state,
      or the resort's own fallback cluster ``other-<state>``.
    * Tier B: cluster of the nearest Tier A member within ``tier_b_radius_km``
      (whose cluster lists the resort's state when ``require_state_match``),
      else the per state fallback cluster.
    """
    defs = cfg.by_id()
    rule = cfg.membership
    errors: list[str] = []
    out: dict[str, str] = {}
    anchors: list[tuple[Member, str]] = []
    for m in members:
        if m.tier != "A":
            continue
        cid = m.cluster_hint
        if cid == fallback_cluster_id(cfg, m.state):
            out[m.id] = cid  # Tier A outside every named cluster; not an anchor for Tier B
            continue
        if cid and cid.startswith(rule.fallback_id_prefix + "-"):
            errors.append(
                f"resort '{m.id}': cluster_hint '{cid}' does not match its state {m.state} "
                f"(use '{fallback_cluster_id(cfg, m.state)}')"
            )
            continue
        if cid not in defs:
            errors.append(f"resort '{m.id}': unknown cluster_hint '{cid}' (see config/clusters.yaml)")
            continue
        if m.state not in defs[cid].states:
            errors.append(f"resort '{m.id}': state {m.state} is not listed in cluster '{cid}' states {defs[cid].states}")
            continue
        out[m.id] = cid
        anchors.append((m, cid))
    for m in members:
        if m.tier == "A":
            continue
        if m.state not in state_names:
            errors.append(f"resort '{m.id}': unknown state code {m.state}")
            continue
        best: tuple[float, str, str] | None = None
        for a, cid in anchors:
            if rule.require_state_match and m.state not in defs[cid].states:
                continue
            d = haversine_km(m.lat, m.lon, a.lat, a.lon)
            if d <= rule.tier_b_radius_km and (best is None or (d, a.id) < best[:2]):
                best = (d, a.id, cid)
        out[m.id] = best[2] if best else fallback_cluster_id(cfg, m.state)
    if errors:
        raise ClusterAssignmentError("\n".join(errors))
    return out


def build_clusters(
    members: Sequence[Member],
    assignment: dict[str, str],
    cfg: ClustersConfig,
    state_names: dict[str, str],
) -> list[ClusterInfo]:
    """ClusterInfo with members, ordered states and polygon, for clusters with members."""
    defs = cfg.by_id()
    by_id = {m.id: m for m in members}
    grouped: dict[str, list[Member]] = {}
    for rid, cid in assignment.items():
        grouped.setdefault(cid, []).append(by_id[rid])
    out: list[ClusterInfo] = []
    for cid in sorted(grouped):
        ms = sorted(grouped[cid], key=lambda m: m.id)
        member_states = {m.state for m in ms}
        if cid in defs:
            d = defs[cid]
            states = [s for s in d.states if s in member_states] + sorted(member_states - set(d.states))
            info = ClusterInfo(cid, d.name, states, sources=list(d.sources))
            polygon = d.polygon.model_dump() if d.polygon else None
        else:
            state = cid[len(cfg.membership.fallback_id_prefix) + 1:].upper()
            info = ClusterInfo(cid, f"{cfg.membership.fallback_name_prefix} {state_names[state]}", [state], fallback=True)
            polygon = None
        info.resort_ids = [m.id for m in ms]
        info.polygon = polygon or cluster_polygon(
            [(m.lon, m.lat) for m in ms], cfg.polygon.buffer_km, cfg.polygon.arc_segments,
            cfg.polygon.coordinate_decimals,
        )
        out.append(info)
    return out
