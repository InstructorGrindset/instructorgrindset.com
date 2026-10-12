import datetime as dt

import pytest

from pipeline import transit
from pipeline.schemas import TransitExtract
from pipeline.schemas.common import Source

SRC = Source(id="gtfs-fixture", url="https://example.org/gtfs.zip", title="Fixture GTFS", accessed_date=dt.date(2026, 10, 1))
BASE = (39.6, -106.3)
TOWN = (39.7, -106.2)


def _extract(tmp_path, gtfs_writer, tables, anchors=(BASE, TOWN), radius=1.0):
    z = gtfs_writer(tmp_path / "fixture" / "gtfs.zip", tables)
    return transit.extract_gtfs(z, "fixture", list(anchors), radius, SRC)


def test_gtfs_minutes():
    assert transit.gtfs_minutes("07:30:30") == 450.5
    assert transit.gtfs_minutes("25:00:00") == 1500
    assert transit.gtfs_minutes("") is None


def test_extract_radius_routes_stops(tmp_path, gtfs_writer, gtfs_tables):
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    TransitExtract.model_validate(ex, context={"source_ids": {"gtfs-fixture"}})
    # mid (about 7 km away) and the far stop are outside 1 km; the parent station is not a stop
    assert [s["id"] for s in ex["stops"]] == ["fixture:base", "fixture:town"]
    assert {s["id"]: s["route_ids"] for s in ex["stops"]} == {"fixture:base": ["fixture:blue", "fixture:red"],
                                                             "fixture:town": ["fixture:blue"]}
    routes = {r["id"]: r for r in ex["routes"]}
    assert sorted(routes) == ["fixture:blue", "fixture:red"]
    assert routes["fixture:blue"]["color"] == "#0055AA" and routes["fixture:red"]["color"] is None
    # blue from shapes.txt, red from its stop sequence (no shape)
    assert routes["fixture:blue"]["geometry"]["coordinates"] == [[[-106.2, 39.7], [-106.25, 39.65], [-106.3, 39.6]]]
    assert routes["fixture:red"]["geometry"]["coordinates"] == [[[-106.25, 39.65], [-106.3005, 39.6005]]]


def test_extract_patterns_median(tmp_path, gtfs_writer, gtfs_tables):
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    assert ex["patterns"] == [{"route_id": "fixture:blue", "stop_ids": ["fixture:town", "fixture:base"],
                               "offsets_min": [0.0, 30.0], "trip_count": 3}]  # median of 25, 30, 30 (after midnight)


def test_extract_wider_radius_keeps_mid(tmp_path, gtfs_writer, gtfs_tables):
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables, radius=8)
    assert "fixture:mid" in [s["id"] for s in ex["stops"]]
    red = next(p for p in ex["patterns"] if p["route_id"] == "fixture:red")
    assert red["offsets_min"] == [0.0, 5.0]


def test_nearby(tmp_path, gtfs_writer, gtfs_tables):
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    hit = transit.nearby(ex, [BASE], 0.5)
    assert [s["id"] for s in hit["stops"]] == ["fixture:base"]
    assert sorted(r["id"] for r in hit["routes"]) == ["fixture:blue", "fixture:red"]
    assert transit.nearby(ex, [(10.0, 10.0)], 1) is None


def test_bus_commute(tmp_path, gtfs_writer, gtfs_tables):
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    site = (39.7, -106.2005)
    assert transit.bus_commute(site, BASE, ex, None, 1.0) is None  # model settings unset
    c = transit.bus_commute(site, BASE, ex, walk_speed_kmh=6.0, max_walk_km=1.0)
    walk_km = 0.0427 + 0.0697  # site to town stop, base stop to base point
    assert c["mode"] == "bus" and c["transit_route_ids"] == ["fixture:blue"] and c["source_id"] == "gtfs-fixture"
    assert c["minutes"] == pytest.approx(30 + walk_km / 6 * 60, abs=0.1)
    assert c["geometry"]["coordinates"][0] == [-106.2005, 39.7] and c["geometry"]["coordinates"][-1] == [-106.3, 39.6]
    assert c["distance_mi"] > 0
    assert transit.bus_commute(site, BASE, ex, 6.0, 0.01) is None  # stops beyond walking distance


def test_load_extracts(tmp_path):
    assert transit.load_extracts(tmp_path / "none") == []
