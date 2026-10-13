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


# ------------------------------------------------------------- feed validity and currency
def _zip(tmp_path, gtfs_writer, tables, name="v.zip"):
    import zipfile
    return zipfile.ZipFile(gtfs_writer(tmp_path / name, tables))


CAL_HEAD = ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "start_date", "end_date"]


def test_feed_validity_prefers_feed_info(tmp_path, gtfs_writer):
    zf = _zip(tmp_path, gtfs_writer, {
        "feed_info.txt": [["feed_publisher_name", "feed_start_date", "feed_end_date", "feed_version"],
                          ["Fixture", "20260402", "20261114", "v7"]],
        "calendar.txt": [CAL_HEAD, ["wk", "1", "1", "1", "1", "1", "0", "0", "20200101", "20991231"]]})
    assert transit.feed_validity(zf) == {"version": "v7", "valid_from": "2026-04-02", "valid_to": "2026-11-14",
                                         "basis": "feed_info"}


def test_feed_validity_falls_back_to_calendar(tmp_path, gtfs_writer):
    zf = _zip(tmp_path, gtfs_writer, {
        "calendar.txt": [CAL_HEAD, ["a", "1", "1", "1", "1", "1", "0", "0", "20251201", "20260412"],
                         ["b", "0", "0", "0", "0", "0", "1", "1", "20251115", "20260301"]],
        "calendar_dates.txt": [["service_id", "date", "exception_type"], ["a", "20260420", "1"], ["a", "20251101", "2"]]})
    # the earliest start, the latest end, plus added dates (exception_type 1); removed dates do not count
    assert transit.feed_validity(zf) == {"version": None, "valid_from": "2025-11-15", "valid_to": "2026-04-20",
                                         "basis": "calendar"}
    zf = _zip(tmp_path, gtfs_writer, {
        "feed_info.txt": [["feed_publisher_name", "feed_start_date", "feed_end_date"], ["Fixture", "20240629", ""]],
        "calendar.txt": [CAL_HEAD, ["a", "1", "1", "1", "1", "1", "0", "0", "20240630", "20991231"]]}, "mixed.zip")
    assert transit.feed_validity(zf)["valid_to"] == "2099-12-31" and transit.feed_validity(zf)["basis"] == "feed_info+calendar"
    assert transit.feed_validity(_zip(tmp_path, gtfs_writer, {"stops.txt": [["stop_id"]]}, "none.zip")) == {
        "version": None, "valid_from": None, "valid_to": None, "basis": None}


@pytest.mark.parametrize("start, end, ok, why", [
    ("2026-04-02", "2026-11-14", True, "overlapping"),          # overlaps the season
    ("2026-05-24", "2026-06-20", True, "within 18 months"),      # summer feed, published recently
    ("2025-04-13", "2025-06-01", True, "within 18 months"),      # exactly 18 months old is still current
    ("2025-04-12", "2025-06-01", False, "older than 18 months"),
    ("2025-01-23", "2027-12-31", True, "overlapping"),           # old but explicitly valid for the season
    ("2024-06-30", "2099-12-31", False, "open ended"),           # placeholder end date proves nothing
    ("2023-01-23", "2023-04-15", False, "older than 18 months"),
    (None, None, False, "no validity dates"),
])
def test_feed_status(start, end, ok, why):
    d = lambda v: dt.date.fromisoformat(v) if v else None  # noqa: E731
    season = (dt.date(2026, 11, 1), dt.date(2027, 4, 30))
    got_ok, got_why = transit.feed_status(d(start), d(end), dt.date(2026, 10, 13), 18, season, 3)
    assert got_ok is ok and why in got_why


def test_current_extracts_drops_stale_with_warning(cfg):
    fresh = {"agency": "fresh", "feed": {"valid_from": "2026-04-02", "valid_to": "2026-11-14"}}
    stale = {"agency": "stale", "feed": {"valid_from": "2023-01-23", "valid_to": "2023-04-15"}}
    undated = {"agency": "undated"}
    kept, warnings = transit.current_extracts([fresh, stale, undated], dt.date(2026, 10, 13), cfg.definitions)
    assert kept == [fresh]
    assert len(warnings) == 2 and "'stale' is not current" in warnings[0] and "'undated'" in warnings[1]


# ------------------------------------------------------------- extraction options
def test_extract_feed_block_from_feed_dates_and_meta(tmp_path, gtfs_writer, gtfs_tables):
    z = gtfs_writer(tmp_path / "fixture" / "gtfs.zip", gtfs_tables)
    meta = {"mdb_source_id": 820, "url": "https://example.org/gtfs.zip", "mirror_url": "https://mirror.example.org/x.zip",
            "license": None, "agency_ids": None}
    ex = transit.extract_gtfs(z, "fixture", [BASE, TOWN], 1.0, SRC, feed_meta=meta)
    assert ex["feed"] == {**meta, "version": None, "valid_from": "2026-01-01", "valid_to": "2027-12-31",
                          "validity_basis": "calendar", "as_of": "2026-01-01"}
    TransitExtract.model_validate(ex)


def test_extract_agency_filter(tmp_path, gtfs_writer, gtfs_tables):
    gtfs_tables["agency.txt"].append(["shuttle", "Airport Shuttle"])
    gtfs_tables["routes.txt"][2][1] = "shuttle"  # the red line now belongs to another agency
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    assert sorted(r["id"] for r in ex["routes"]) == ["fixture:blue", "fixture:red"]
    z = tmp_path / "fixture" / "gtfs.zip"
    ex = transit.extract_gtfs(z, "fixture", [BASE, TOWN], 1.0, SRC, agency_ids=["fx"])
    assert [r["id"] for r in ex["routes"]] == ["fixture:blue"]
    assert {s["id"]: s["route_ids"] for s in ex["stops"]}["fixture:base"] == ["fixture:blue"]
    assert [p["route_id"] for p in ex["patterns"]] == ["fixture:blue"]


def test_extract_simplifies_route_lines(tmp_path, gtfs_writer, gtfs_tables):
    # a shape with a midpoint 1 m off the straight line, and a second shape identical after simplification
    gtfs_tables["shapes.txt"] = [gtfs_tables["shapes.txt"][0],
                                 ["sh1", "39.700", "-106.200", "1"], ["sh1", "39.65001", "-106.250", "2"],
                                 ["sh1", "39.600", "-106.300", "3"],
                                 ["sh2", "39.700", "-106.200", "1"], ["sh2", "39.600", "-106.300", "2"]]
    gtfs_tables["trips.txt"][3][3] = "sh2"
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    blue = next(r for r in ex["routes"] if r["id"] == "fixture:blue")
    assert len(blue["geometry"]["coordinates"]) == 2  # unsimplified: two distinct lines
    ex = transit.extract_gtfs(tmp_path / "fixture" / "gtfs.zip", "fixture", [BASE, TOWN], 1.0, SRC, simplify_tolerance_m=10)
    blue = next(r for r in ex["routes"] if r["id"] == "fixture:blue")
    assert blue["geometry"]["coordinates"] == [[[-106.2, 39.7], [-106.3, 39.6]]]
    assert ex["simplify_tolerance_m"] == 10


def test_extract_ignores_flexible_stop_time_rows(tmp_path, gtfs_writer, gtfs_tables):
    # GTFS Flex rows name a location instead of a stop; they carry no stop_id
    head = gtfs_tables["stop_times.txt"][0] + ["location_id"]
    rows = [r + [""] for r in gtfs_tables["stop_times.txt"][1:]] + [["b1", "07:30:00", "07:30:00", "", "4", "zone-1"]]
    gtfs_tables["stop_times.txt"] = [head] + rows
    gtfs_tables["stops.txt"].append(["blank", "No Coordinates", "", "", "0"])
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    assert [s["id"] for s in ex["stops"]] == ["fixture:base", "fixture:town"]
    assert ex["patterns"][0]["offsets_min"] == [0.0, 30.0]


def test_dump_extract_round_trips_one_record_per_line(tmp_path, gtfs_writer, gtfs_tables):
    import json
    ex = _extract(tmp_path, gtfs_writer, gtfs_tables)
    text = transit.dump_extract(ex)
    assert json.loads(text) == ex
    lines = text.splitlines()
    assert lines[0] == "{" and lines[-1] == "}"
    assert sum(1 for line in lines if line.startswith('  {"id":"fixture:')) == len(ex["routes"]) + len(ex["stops"])


# ------------------------------------------------------------- feeds.yaml to extracts
FEED = {"agency": "fixture", "title": "Fixture GTFS", "mdb_source_id": 1, "url": "https://example.org/gtfs.zip",
        "mirror_url": "https://mirror.example.org/fixture.zip", "license": "https://example.org/license"}


def test_feed_source_names_catalog_mirror_dates_and_license():
    src = transit.feed_source(FEED, dt.date(2026, 10, 13), {"valid_from": "2026-01-01", "valid_to": "2027-12-31"})
    assert src.id == "gtfs-fixture" and src.url == "https://example.org/gtfs.zip" and src.accessed_date == dt.date(2026, 10, 13)
    assert src.note == ("Mobility Database catalog entry 1; downloaded from the catalog mirror https://mirror.example.org/fixture.zip; "
                        "feed valid 2026-01-01 to 2027-12-31; license: https://example.org/license.")
    assert "none listed in the catalog" in transit.feed_source({**FEED, "license": None}, dt.date(2026, 10, 13), {}).note


def test_run_extract_writes_current_feeds_and_skips_the_rest(tmp_path, gtfs_writer, gtfs_tables, cfg):
    import json
    gtfs_writer(tmp_path / "fixture" / "gtfs.zip", gtfs_tables)
    old = {k: [list(r) for r in v] for k, v in gtfs_tables.items()}
    old["calendar.txt"][1][-2:] = ["20230123", "20230415"]
    gtfs_writer(tmp_path / "old" / "gtfs.zip", old)
    gtfs_writer(tmp_path / "far" / "gtfs.zip", gtfs_tables)
    feeds = [FEED, {**FEED, "agency": "old"}, {**FEED, "agency": "missing"}]
    log = transit.run_extract(feeds, tmp_path, [BASE, TOWN], cfg.definitions, dt.date(2026, 10, 13))
    assert log[0].startswith("fixture: 2 routes, 2 stops") and "skipped, not current" in log[1] and "run fetch first" in log[2]
    assert not (tmp_path / "old.json").exists()
    ex = json.loads((tmp_path / "fixture.json").read_text())
    TransitExtract.model_validate(ex)
    assert ex["source"]["id"] == "gtfs-fixture" and ex["feed"]["mdb_source_id"] == 1 and ex["feed"]["as_of"] == "2026-01-01"
    assert ex["simplify_tolerance_m"] == cfg.definitions.transit.simplify_tolerance_m
    log = transit.run_extract([{**FEED, "agency": "far"}], tmp_path, [(10.0, 10.0)], cfg.definitions, dt.date(2026, 10, 13))
    assert "no stop within" in log[0] and not (tmp_path / "far.json").exists()
