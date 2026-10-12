import datetime as dt
import json
import os

from pipeline import lifts

FETCHED = dt.date(2026, 10, 1)


def test_filter_lifts(lifts_full):
    out = lifts.filter_lifts(lifts_full, ["osm-peak", "osm-basin"], FETCHED)
    ids = [f["properties"]["id"] for f in out["features"]]
    assert sorted(ids) == ["l1", "l2", "l4"]  # abandoned, other areas and unattached lifts are dropped
    l2 = next(f for f in out["features"] if f["properties"]["id"] == "l2")
    assert l2["properties"] == {"id": "l2", "name": None, "ref": None, "lift_type": "magic_carpet", "ski_area_ids": ["osm-peak"]}
    assert out["metadata"]["area_ids"] == ["osm-basin", "osm-peak"] and out["metadata"]["fetched_date"] == "2026-10-01"


def test_ski_area_ids():
    assert lifts.ski_area_ids({"skiAreas": [{"properties": {"id": "a"}}, {"properties": {}}, None, "x"]}) == ["a"]
    assert lifts.ski_area_ids({}) == []


def test_lifts_for_detail_properties(lifts_full):
    feats = lifts.filter_lifts(lifts_full, ["osm-peak", "osm-basin"], FETCHED)["features"]
    fc = lifts.lifts_for(feats, ["osm-peak"])
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2
    assert fc["features"][0]["properties"] == {"name": "Fixture Express", "lift_type": "chair_lift", "source_id": "openskimap-lifts"}
    assert lifts.lifts_for(feats, []) == {"type": "FeatureCollection", "features": []}


def test_refresh_extract(tmp_path, lifts_full):
    full, extract = tmp_path / "lifts.full.geojson", tmp_path / "us_lifts_tier_a.geojson"
    assert lifts.refresh_extract(full, extract, ["osm-peak"]) is None  # no download: nothing to do
    full.write_text(json.dumps(lifts_full))
    assert lifts.refresh_extract(full, extract, ["osm-peak"]) == 2
    assert lifts.refresh_extract(full, extract, ["osm-peak"]) is None  # unchanged areas: skipped
    assert lifts.refresh_extract(full, extract, ["osm-peak", "osm-basin"]) == 3
    os.utime(full, (extract.stat().st_mtime + 10,) * 2)  # newer download: refiltered
    assert lifts.refresh_extract(full, extract, ["osm-peak", "osm-basin"]) == 3
    fetched, feats = lifts.load_extract(extract)
    assert len(feats) == 3 and isinstance(fetched, dt.date)
    assert lifts.load_extract(tmp_path / "missing.geojson") == (None, [])
