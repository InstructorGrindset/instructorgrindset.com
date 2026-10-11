import math

import pytest

from pipeline.clusters import (
    ClusterAssignmentError, Member, assign_clusters, buffer_hull, build_clusters, cluster_polygon,
    convex_hull, ring_area,
)
from pipeline.geo import haversine_km
from pipeline.states import STATE_NAMES


def test_hull_square_with_interior_and_duplicates():
    pts = [(0, 0), (2, 0), (2, 2), (0, 2), (1, 1), (1, 0), (0, 0)]
    hull = convex_hull(pts)
    assert hull == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    assert ring_area(hull + [hull[0]]) > 0  # counter clockwise


def test_hull_degenerate():
    assert convex_hull([(1, 1)]) == [(1.0, 1.0)]
    assert convex_hull([(1, 1), (1, 1)]) == [(1.0, 1.0)]
    assert convex_hull([(0, 0), (1, 1), (2, 2)]) == [(0.0, 0.0), (2.0, 2.0)]


def _dist_point_segment(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / (ax * ax + ay * ay)))
    return math.dist(p, (a[0] + t * ax, a[1] + t * ay))


def test_buffer_is_at_radius_and_contains_hull():
    hull = convex_hull([(0, 0), (10, 0), (10, 6), (0, 6), (5, 9)])
    ring = buffer_hull(hull, 2.0, 32)
    assert ring[0] == ring[-1] and ring_area(ring) > 0
    edges = list(zip(hull, hull[1:] + hull[:1]))
    for p in ring:
        d = min(_dist_point_segment(p, a, b) for a, b in edges)
        assert d == pytest.approx(2.0, abs=1e-9)
    exact = abs(ring_area(hull + [hull[0]])) + 2.0 * sum(math.dist(a, b) for a, b in edges) + math.pi * 4
    assert ring_area(ring) == pytest.approx(exact, rel=0.01)


def test_buffer_point_and_segment():
    circle = buffer_hull([(0, 0)], 1.0, 32)
    assert len(circle) == 33 and all(math.hypot(*p) == pytest.approx(1.0) for p in circle)
    stadium = buffer_hull([(0, 0), (10, 0)], 1.0, 32)
    assert ring_area(stadium) == pytest.approx(20 + math.pi, rel=0.01)


def test_cluster_polygon_single_point_is_15km_circle():
    lon, lat = -106.3, 39.6
    poly = cluster_polygon([(lon, lat)], 15, 32, 5)
    ring = poly["coordinates"][0]
    assert poly["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) == 33
    for x, y in ring:
        assert haversine_km(lat, lon, y, x) == pytest.approx(15, rel=0.01)


def test_cluster_polygon_contains_points_with_margin():
    pts = [(-106.3, 39.6), (-106.0, 39.5), (-105.9, 39.7)]
    ring = cluster_polygon(pts, 15)["coordinates"][0]
    from pipeline.geo import point_in_ring
    for lon, lat in pts:
        assert point_in_ring(lon, lat, ring)
    lons = [p[0] for p in ring]
    assert min(lons) < -106.3 - 0.15 and max(lons) > -105.9 + 0.15


def members():
    return [
        Member("a1", "CO", 39.60, -106.30, "A", "i70-corridor"),
        Member("a2", "CO", 39.20, -106.90, "A", "roaring-fork"),
        Member("a3", "CA", 39.20, -120.20, "A", "lake-tahoe"),
        Member("b-near-a1", "CO", 39.75, -106.10, "B"),       # ~24 km from a1
        Member("b-near-a2", "CO", 39.25, -106.85, "B"),       # ~7 km from a2
        Member("b-far", "CO", 37.50, -105.00, "B"),           # > 80 km from all
        Member("b-nv", "NV", 39.30, -119.90, "B"),            # NV, near Tahoe (CA, NV)
        Member("b-wy", "WY", 39.70, -106.30, "B"),            # synthetic: near a1 but WY
    ]


def test_assignment(cfg):
    a = assign_clusters(members(), cfg.clusters, STATE_NAMES)
    assert a == {
        "a1": "i70-corridor", "a2": "roaring-fork", "a3": "lake-tahoe",
        "b-near-a1": "i70-corridor", "b-near-a2": "roaring-fork", "b-far": "other-co",
        "b-nv": "lake-tahoe", "b-wy": "other-wy",
    }


def test_assignment_radius_is_configurable(cfg):
    cfg.clusters.membership.tier_b_radius_km = 5
    a = assign_clusters(members(), cfg.clusters, STATE_NAMES)
    assert a["b-near-a1"] == "other-co" and a["b-near-a2"] == "other-co"
    cfg.clusters.membership.require_state_match = False
    cfg.clusters.membership.tier_b_radius_km = 80
    assert assign_clusters(members(), cfg.clusters, STATE_NAMES)["b-wy"] == "i70-corridor"


def test_assignment_errors(cfg):
    with pytest.raises(ClusterAssignmentError, match="unknown cluster_hint 'nowhere'"):
        assign_clusters([Member("x", "CO", 39, -106, "A", "nowhere")], cfg.clusters, STATE_NAMES)
    with pytest.raises(ClusterAssignmentError, match="not listed"):
        assign_clusters([Member("x", "UT", 40, -111, "A", "i70-corridor")], cfg.clusters, STATE_NAMES)


def test_build_clusters_every_resort_once(cfg):
    ms = members()
    a = assign_clusters(ms, cfg.clusters, STATE_NAMES)
    infos = build_clusters(ms, a, cfg.clusters, STATE_NAMES)
    all_ids = [rid for c in infos for rid in c.resort_ids]
    assert sorted(all_ids) == sorted(m.id for m in ms)
    by = {c.id: c for c in infos}
    assert by["other-co"].name == "Other Colorado" and by["other-co"].fallback
    assert by["other-wy"].name == "Other Wyoming"
    assert by["lake-tahoe"].states == ["CA", "NV"]
    assert by["i70-corridor"].resort_ids == ["a1", "b-near-a1"]
    assert [c.id for c in infos] == sorted(c.id for c in infos)


def test_hand_drawn_polygon_wins(cfg):
    ring = [[-107, 39], [-105, 39], [-105, 40], [-107, 39]]
    from pipeline.schemas import Geometry
    cfg.clusters.by_id()["i70-corridor"].polygon = Geometry(type="Polygon", coordinates=[ring])
    ms = members()
    infos = build_clusters(ms, assign_clusters(ms, cfg.clusters, STATE_NAMES), cfg.clusters, STATE_NAMES)
    assert next(c for c in infos if c.id == "i70-corridor").polygon["coordinates"] == [ring]
