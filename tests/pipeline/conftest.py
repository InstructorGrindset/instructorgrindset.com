import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest

from pipeline import openskimap
from pipeline.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
FETCHED = dt.date(2026, 10, 1)


# Builds in tests run on a fixed date unless a test sets its own, so date checks (the ownership source age, one build
# cycle from the fixture sources accessed 2026-10-01) give the same answer every day.
PINNED_BUILD_EPOCH = "1791892800"  # 2026-10-13T12:00:00Z


@pytest.fixture(autouse=True)
def _pinned_build_date(monkeypatch):
    if "SOURCE_DATE_EPOCH" not in os.environ:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", PINNED_BUILD_EPOCH)


@pytest.fixture
def cfg():
    return load_config(REPO_ROOT / "config")


@pytest.fixture
def osm_full():
    return json.loads((FIXTURES / "openskimap_ski_areas.geojson").read_text())


@pytest.fixture
def raw_dir(tmp_path):
    """A copy of the fixture raw dir: cached full OpenSkiMap download (mtime = FETCHED)
    plus the extract filtered with the real config rules."""
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw", raw)
    (raw / "openskimap").mkdir()
    full = raw / "openskimap" / openskimap.FULL_NAME
    shutil.copy(FIXTURES / "openskimap_ski_areas.geojson", full)
    ts = dt.datetime.combine(FETCHED, dt.time(12)).timestamp()
    os.utime(full, (ts, ts))
    kw = load_config(REPO_ROOT / "config").definitions.openskimap.filter_kwargs()
    kw["state_shapes"] = openskimap.load_state_shapes(FIXTURES / "state_shapes.geojson")
    openskimap.write_us_extract(full, raw / "openskimap" / openskimap.US_NAME, FETCHED, **kw)
    return raw


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def fetched():
    return FETCHED


@pytest.fixture
def fixtures_dir():
    return FIXTURES


# ------------------------------------------------------------- Phase 5 fixtures (synthetic, test only)
def _box(lon0, lat0, lon1, lat1):
    return {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]]}


@pytest.fixture
def counties_geojson(tmp_path):
    """Two fictional counties: 08998 covers fixture-peak, 49998 covers fixture-basin."""
    path = tmp_path / "geo" / "us-counties.geojson"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"fips": "08998", "name": "Fixture", "state": "CO"},
         "geometry": _box(-106.6, 39.4, -106.0, 39.9)},
        {"type": "Feature", "properties": {"fips": "49998", "name": "Basin Fixture", "state": "UT"},
         "geometry": {"type": "MultiPolygon", "coordinates": [_box(-111.8, 40.4, -111.4, 40.8)["coordinates"]]}},
    ]}))
    return path


def lift(id, name, coords, area_ids, status="operating", lift_type="chair_lift"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": id, "name": name, "ref": None, "type": "lift", "liftType": lift_type, "status": status,
                           "skiAreas": [{"type": "Feature", "properties": {"id": a, "name": "x"}, "geometry": None}
                                        for a in area_ids]}}


@pytest.fixture
def lifts_full():
    return {"type": "FeatureCollection", "features": [
        lift("l1", "Fixture Express", [[-106.30, 39.60], [-106.28, 39.62]], ["osm-peak"]),
        lift("l2", None, [[-106.31, 39.60], [-106.30, 39.61]], ["osm-peak", "osm-other-near"], lift_type="magic_carpet"),
        lift("l3", "Old Chair", [[-106.32, 39.60], [-106.31, 39.61]], ["osm-peak"], status="abandoned"),
        lift("l4", "Basin Lift", [[-111.585, 40.61], [-111.58, 40.62]], ["osm-basin"]),
        lift("l5", "Elsewhere", [[-100, 40], [-100.1, 40.1]], ["somewhere-else"]),
        lift("l6", "Unattached", [[-106.3, 39.6], [-106.29, 39.6]], []),
    ]}


def write_gtfs(path, tables):
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in tables.items():
            zf.writestr(name, "\n".join(",".join(r) for r in rows) + "\n")
    return path


def encode_polyline(points, precision=6):
    """[[lon, lat], ...] -> encoded polyline (test helper mirroring Valhalla's encoding)."""
    out, plat, plon, f = [], 0, 0, 10 ** precision
    for lon, lat in points:
        for v, prev in ((round(lat * f), plat), (round(lon * f), plon)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(lat * f), round(lon * f)
    return "".join(out)


@pytest.fixture
def gtfs_writer():
    return write_gtfs


@pytest.fixture
def polyline_encoder():
    return encode_polyline


GTFS_TABLES = {
    "agency.txt": [["agency_id", "agency_name"], ["fx", "Fixture Transit"]],
    "routes.txt": [["route_id", "agency_id", "route_short_name", "route_long_name", "route_color"],
                   ["blue", "fx", "B", "Blue Line", "0055aa"], ["red", "fx", "R", "Red Line", ""],
                   ["far", "fx", "F", "Far Line", ""]],
    "trips.txt": [["route_id", "service_id", "trip_id", "shape_id"],
                  ["blue", "wk", "b1", "sh1"], ["blue", "wk", "b2", "sh1"], ["blue", "wk", "b3", "sh1"],
                  ["red", "wk", "r1", ""], ["far", "wk", "f1", ""]],
    "shapes.txt": [["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
                   ["sh1", "39.700", "-106.200", "1"], ["sh1", "39.650", "-106.250", "2"], ["sh1", "39.600", "-106.300", "3"]],
    "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"],
                  ["town", "Town Center", "39.7000", "-106.2000", "0"],
                  ["mid", "Midway", "39.6500", "-106.2500", "0"],
                  ["base", "Base Area", "39.6005", "-106.3005", "0"],
                  ["station", "Parent Station", "39.6000", "-106.3000", "1"],
                  ["remote", "Remote Stop", "45.0000", "-110.0000", "0"]],
    # Valid through the configured 2026 to 27 target season, so the fixture feed stays current.
    "calendar.txt": [["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                      "start_date", "end_date"], ["wk", "1", "1", "1", "1", "1", "0", "0", "20260101", "20271231"]],
    "stop_times.txt": [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
                       ["b1", "07:00:00", "07:00:00", "town", "1"], ["b1", "07:10:00", "07:10:00", "mid", "2"],
                       ["b1", "07:25:00", "07:25:00", "base", "3"],
                       ["b2", "08:00:00", "08:00:00", "town", "1"], ["b2", "08:12:00", "08:12:00", "mid", "2"],
                       ["b2", "08:30:00", "08:30:00", "base", "3"],
                       ["b3", "24:50:00", "24:50:00", "town", "1"], ["b3", "25:00:00", "25:00:00", "mid", "2"],
                       ["b3", "25:20:00", "25:20:00", "base", "3"],
                       ["r1", "09:00:00", "09:00:00", "mid", "1"], ["r1", "09:05:00", "09:05:00", "base", "2"],
                       ["f1", "10:00:00", "10:00:00", "remote", "1"], ["f1", "10:30:00", "10:30:00", "remote", "2"]],
}


@pytest.fixture
def gtfs_tables():
    return {k: [list(r) for r in v] for k, v in GTFS_TABLES.items()}
