import json

from pipeline import openskimap


def test_filter_us_keeps_operating_downhill_us(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched)
    names = sorted(f["properties"]["name"] for f in out["features"])
    assert names == sorted(["Fixture Peak Ski Resort", "Fixture Valley", "Faraway Hill", "Fixture Basin",
                            "Fixture Valley", "Unrelated Bowl"])
    assert out["metadata"]["fetched_date"] == "2026-10-01"
    assert out["metadata"]["source_url"] == openskimap.URL


def test_filter_extracts_state_and_statistics(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched)
    peak = next(f for f in out["features"] if f["properties"]["id"] == "osm-peak")["properties"]
    assert peak["state"] == "CO"
    assert peak["lift_count"] == 11
    assert peak["downhill_run_count"] == 7
    assert peak["min_elevation_m"] == 2400.123456 and peak["max_elevation_m"] == 3500.0
    basin = next(f for f in out["features"] if f["properties"]["id"] == "osm-basin")["properties"]
    assert basin["state"] == "UT" and basin["lift_count"] is None


def test_filter_is_sorted_and_rounded(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched, decimals=3)
    keys = [(f["properties"]["state"], f["properties"]["name"].lower()) for f in out["features"]]
    assert keys == sorted(keys)
    ring = next(f for f in out["features"] if f["properties"]["id"] == "osm-peak")["geometry"]["coordinates"][0]
    assert ring[0] == [-106.36, 39.56]


def test_keep_feature_rules():
    base = {"type": "skiArea", "name": "X", "activities": ["downhill"], "status": "operating",
            "location": {"iso3166_1Alpha2": "US", "iso3166_2": "US-VT"}}
    assert openskimap.keep_feature(base)
    assert not openskimap.keep_feature({**base, "type": "run"})
    assert not openskimap.keep_feature({**base, "activities": ["nordic"]})
    assert not openskimap.keep_feature({**base, "status": None})
    assert not openskimap.keep_feature({**base, "name": "  "})
    assert not openskimap.keep_feature({**base, "location": {"iso3166_1Alpha2": "US", "iso3166_2": None}})
    assert not openskimap.keep_feature({**base, "location": None})


def test_load_extract(raw_dir, fetched):
    ext = openskimap.load_us_extract(raw_dir / "openskimap" / openskimap.US_NAME)
    assert ext.fetched_date == fetched
    assert ext.source.id == openskimap.SOURCE_ID
    peak = next(a for a in ext.areas if a.osm_id == "osm-peak")
    assert abs(peak.lon - -106.30) < 1e-6 and abs(peak.lat - 39.60) < 1e-6  # polygon centroid
    assert peak.vertical_ft == round((3500.0 - 2400.12346) * openskimap.M_TO_FT)
    assert peak.lift_count == 11


def test_write_extract_is_compact_json(raw_dir):
    text = (raw_dir / "openskimap" / openskimap.US_NAME).read_text()
    data = json.loads(text)
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 6
