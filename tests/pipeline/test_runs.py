import datetime as dt
import json
import os

import pytest
from pydantic import ValidationError

from pipeline import runs
from pipeline.geo import haversine_km
from pipeline.schemas import TrailmapFile

FETCHED = dt.date(2026, 10, 1)


def run(id, name, coords, areas, uses=("downhill",), difficulty="intermediate", status="operating", geom="LineString",
        convention="north_america", grooming=None, lit=None):
    return {"type": "Feature", "geometry": {"type": geom, "coordinates": coords},
            "properties": {"id": id, "name": name, "type": "run", "uses": list(uses), "difficulty": difficulty,
                           "difficultyConvention": convention, "status": status, "grooming": grooming, "lit": lit,
                           "skiAreas": [{"type": "Feature", "properties": {"id": a, "name": "x"}} for a in areas]}}


LINE = [[-106.30, 39.600, 2500.0], [-106.29, 39.605, 2450.0], [-106.28, 39.610, 2400.0]]
SQUARE = [[[-106.30, 39.60], [-106.29, 39.60], [-106.29, 39.61], [-106.30, 39.61], [-106.30, 39.60]]]


def features():
    return [
        run("r1", "Blue Run", LINE, ["osm-peak"], grooming="classic"),
        run("r2", None, LINE, ["osm-peak"], difficulty="expert"),
        run("r3", "Old Run", LINE, ["osm-peak"], status="abandoned"),
        run("r4", "Nordic Loop", LINE, ["osm-peak"], uses=("nordic",)),
        run("r5", "Park", LINE, ["osm-peak"], uses=("snow_park",), difficulty=None),
        run("r6", "Bowl", SQUARE, ["osm-peak"], geom="Polygon", difficulty="advanced"),
        run("r7", "Elsewhere", LINE, ["somewhere-else"]),
        run("r8", "Disused", LINE, ["osm-peak"], status="disused"),
        run("r9", "Basin Run", LINE, ["osm-basin", "osm-peak"], difficulty="easy", lit=True),
    ]


def write_full(path, feats):
    lines = ['{"type": "FeatureCollection", "features":[']
    lines += [json.dumps(f, separators=(",", ":")) + ("," if i < len(feats) - 1 else "") for i, f in enumerate(feats)]
    lines.append("]}")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_difficulty_us_mapping():
    m = {d: runs.difficulty_us(d, "north_america", ["downhill"]) for d in
         ("novice", "easy", "intermediate", "advanced", "expert", "extreme", "freeride", None)}
    assert m == {"novice": "green", "easy": "green", "intermediate": "blue", "advanced": "black",
                 "expert": "double_black", "extreme": "double_black", "freeride": None, None: None}
    assert runs.difficulty_us("easy", "europe", ["downhill"]) is None
    assert runs.difficulty_us(None, "europe", ["snow_park"]) == "terrain_park"
    assert runs.difficulty_us("advanced", "north_america", ["downhill", "snow_park"]) == "terrain_park"


def test_douglas_peucker():
    pts = [(0, 0), (1, 0.001), (2, -0.001), (3, 0), (3, 3)]
    assert runs.douglas_peucker(pts, 0.01) == [(0, 0), (3, 0), (3, 3)]
    assert runs.douglas_peucker(pts, 0) == pts
    assert runs.douglas_peucker([(0, 0), (1, 1)], 5) == [(0, 0), (1, 1)]
    zig = [(0, 0), (1, 1), (2, 0), (3, 1), (4, 0)]
    assert runs.douglas_peucker(zig, 0.5) == zig


def test_simplify_geometry_metres():
    # a straight run with a 2 m wiggle: gone at 6 m tolerance, kept at 1 m
    lon0, lat0 = -106.3, 39.6
    wiggle = lat0 + 0.002 / 111.0 * 1.0  # about 2 m north
    coords = [[lon0, lat0, 1], [lon0 + 0.001, wiggle, 1], [lon0 + 0.002, lat0, 1]]
    assert runs.simplify_line(coords, 6) == [[-106.3, 39.6], [-106.298, 39.6]]
    assert len(runs.simplify_line(coords, 1)) == 3
    assert runs.simplify_line([[1, 1, 0], [1, 1, 0]], 6) is None  # degenerate after dedupe
    poly = runs.simplify_geometry({"type": "Polygon", "coordinates": SQUARE}, 6)
    assert poly["coordinates"][0][0] == poly["coordinates"][0][-1] and len(poly["coordinates"][0]) == 5
    tiny = [[[-106.3, 39.6], [-106.30001, 39.6], [-106.30001, 39.60001], [-106.3, 39.6]]]
    assert runs.simplify_geometry({"type": "Polygon", "coordinates": tiny}, 6) is None
    ml = runs.simplify_geometry({"type": "MultiLineString", "coordinates": [LINE, [[0, 0, 0], [0, 0, 0]]]}, 6)
    assert ml["type"] == "MultiLineString" and len(ml["coordinates"]) == 1
    # simplified points stay on the original line within the tolerance
    for lon, lat in runs.simplify_line(LINE, 6):
        assert min(haversine_km(lat, lon, c[1], c[0]) for c in LINE) < 0.01


def test_stream_and_filter(tmp_path):
    full = write_full(tmp_path / "runs.full.geojson", features())
    streamed = list(runs.iter_features(full))
    assert len(streamed) == len(features()) - 1  # the nordic only line is skipped before JSON parsing
    out = runs.filter_runs(streamed, ["osm-peak", "osm-basin"], FETCHED, 6)
    ids = [f["properties"]["id"] for f in out["features"]]
    assert sorted(ids) == ["r1", "r2", "r5", "r6", "r9"]
    r9 = next(f for f in out["features"] if f["properties"]["id"] == "r9")
    assert r9["properties"]["ski_area_ids"] == ["osm-basin", "osm-peak"] and r9["properties"]["lit"] is True
    assert all(len(c) == 2 for f in out["features"] if f["geometry"]["type"] == "LineString" for c in f["geometry"]["coordinates"])
    assert out["metadata"]["simplify_tolerance_m"] == 6 and out["metadata"]["fetched_date"] == "2026-10-01"


def test_refresh_extract_rules(tmp_path):
    full = write_full(tmp_path / "runs.full.geojson", features())
    ts = dt.datetime(2026, 10, 1, 12).timestamp()
    os.utime(full, (ts, ts))
    extract = tmp_path / "us_runs_tier_a.geojson"
    assert runs.refresh_extract(tmp_path / "missing.geojson", extract, ["osm-peak"], 6) is None
    assert runs.refresh_extract(full, extract, ["osm-peak"], 6) == 5
    assert runs.refresh_extract(full, extract, ["osm-peak"], 6) is None          # unchanged
    assert runs.refresh_extract(full, extract, ["osm-peak"], 4) == 5             # tolerance changed
    assert runs.refresh_extract(full, extract, ["osm-basin"], 4) == 1            # matched areas changed
    data = json.loads(extract.read_text())
    data["metadata"]["fetched_date"] = "2026-10-05"
    extract.write_text(json.dumps(data))
    assert runs.refresh_extract(full, extract, ["osm-peak"], 6) is None          # newer committed extract wins
    fetched, feats = runs.load_extract(extract)
    assert fetched == dt.date(2026, 10, 5) and len(feats) == 1
    assert runs.load_extract(tmp_path / "none.geojson") == (None, [])


LIFTS = {"type": "FeatureCollection", "features": [{"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [[-106.31, 39.59], [-106.30, 39.60]]},
         "properties": {"name": "Lift", "lift_type": "chair_lift", "source_id": "openskimap-lifts"}}]}
HEADER = {"version": 1, "generated_at": "2026-10-12T00:00:00Z"}
CTX = {"source_ids": {"openskimap-runs", "openskimap-lifts"}}


def test_trailmap_assembly():
    feats = runs.filter_runs(features(), ["osm-peak", "osm-basin"], FETCHED, 6)["features"]
    tm = runs.trailmap("fixture-peak", feats, ["osm-peak"], LIFTS, HEADER)
    TrailmapFile.model_validate(tm, context=CTX)
    assert tm["source_id"] == "openskimap-runs" and tm["lifts"] == LIFTS
    props = {f["properties"]["name"]: f["properties"] for f in tm["runs"]["features"]}
    assert props["Blue Run"] == {"name": "Blue Run", "difficulty": "intermediate", "difficulty_us": "blue", "grooming": "classic"}
    assert props[None] == {"name": None, "difficulty": "expert", "difficulty_us": "double_black"}
    assert props["Park"]["difficulty_us"] == "terrain_park" and props["Basin Run"]["lit"] is True
    assert props["Bowl"]["difficulty_us"] == "black"
    assert tm["bounds"] == [-106.31, 39.59, -106.28, 39.61]  # runs and lifts
    assert runs.trailmap("x", feats, ["somewhere-else"], LIFTS, HEADER) is None
    basin = runs.trailmap("fixture-basin", feats, ["osm-basin"], {"type": "FeatureCollection", "features": []}, HEADER)
    assert [f["properties"]["name"] for f in basin["runs"]["features"]] == ["Basin Run"]


def test_trailmap_schema_rejects_bad_values():
    feats = runs.filter_runs(features(), ["osm-peak"], FETCHED, 6)["features"]
    tm = runs.trailmap("fixture-peak", feats, ["osm-peak"], LIFTS, HEADER)
    bad = json.loads(json.dumps(tm))
    bad["runs"]["features"][0]["properties"]["difficulty_us"] = "orange"
    with pytest.raises(ValidationError):
        TrailmapFile.model_validate(bad, context=CTX)
    bad = json.loads(json.dumps(tm))
    bad["bounds"] = [1, 2, 0, 1]
    with pytest.raises(ValidationError, match="west, south, east, north"):
        TrailmapFile.model_validate(bad, context=CTX)
    with pytest.raises(ValidationError, match="not defined"):
        TrailmapFile.model_validate(tm, context={"source_ids": {"openskimap-lifts"}})
