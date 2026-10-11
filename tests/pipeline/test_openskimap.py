import json

from pipeline import openskimap

ALL_KEPT = ["Faraway Hill", "Fixture Basin", "Fixture Lake Area", "Fixture Peak Ski Resort", "Fixture Stub Hill",
            "Fixture Valley", "Fixture Valley", "Fixture Valley Ski Area", "Legacy Location Hill", "Unrelated Bowl"]


def _props(out, osm_id):
    return next(f for f in out["features"] if f["properties"]["id"] == osm_id)["properties"]


def test_filter_us_keeps_operating_downhill_us(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched)
    assert sorted(f["properties"]["name"] for f in out["features"]) == ALL_KEPT
    assert out["metadata"]["fetched_date"] == "2026-10-01"
    assert out["metadata"]["source_url"] == openskimap.URL
    assert out["metadata"]["counts"] == {"us_operating_downhill_named": 10, "dropped_no_lifts_or_runs": 0,
                                         "merged_duplicates": 0, "kept": 10}


def test_filter_drops_unmapped_stubs_and_merges_duplicates(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched, require_infrastructure=True, dedupe_radius_km=2,
                               dedupe_similarity_min=0.8)
    names = sorted(f["properties"]["name"] for f in out["features"])
    assert "Fixture Stub Hill" not in names and "Fixture Valley Ski Area" not in names
    assert "Faraway Hill" in names  # runs but no lifts is still a mapped ski area
    assert _props(out, "osm-valley")["merged_ids"] == ["osm-valley-dup"]
    # same name far apart is not a duplicate
    assert names.count("Fixture Valley") == 2
    assert out["metadata"]["counts"] == {"us_operating_downhill_named": 10, "dropped_no_lifts_or_runs": 1,
                                         "merged_duplicates": 1, "kept": 8}


def test_filter_extracts_state_and_statistics(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched)
    peak = _props(out, "osm-peak")
    assert peak["state"] == "CO" and peak["states"] == ["CO"]
    assert peak["lift_count"] == 11
    assert peak["downhill_run_count"] == 7
    # lifts and downhill runs only; the area wide minimum (2000, nordic) is ignored
    assert peak["min_elevation_m"] == 2400.123456 and peak["max_elevation_m"] == 3500.0
    assert peak["source_types"] == ["openstreetmap", "skimap.org"]
    assert (peak["lon"], peak["lat"]) == (-106.3, 39.6)
    basin = _props(out, "osm-basin")
    assert basin["state"] == "UT" and basin["downhill_run_count"] is None
    assert _props(out, "osm-faraway")["lift_count"] is None
    assert _props(out, "osm-legacy")["state"] == "MT"  # older `location` object still works


def test_multi_place_area(osm_full, fetched, fixtures_dir):
    shapes = openskimap.load_state_shapes(fixtures_dir / "state_shapes.geojson")
    lake = _props(openskimap.filter_us(osm_full, fetched, state_shapes=shapes), "osm-lake")
    assert lake["states"] == ["NV", "CA"] and lake["state"] == "CA"  # the state containing the point
    lake = _props(openskimap.filter_us(osm_full, fetched), "osm-lake")
    assert lake["state"] == "NV"  # no outlines: first US place


def test_choose_state():
    shapes = {"CA": {"type": "Polygon", "coordinates": [[[-125, 32], [-120, 32], [-120, 42], [-125, 42], [-125, 32]]]}}
    assert openskimap.choose_state([], (0, 0), shapes) is None
    assert openskimap.choose_state(["NV"], (-121, 39), shapes) == "NV"
    assert openskimap.choose_state(["NV", "CA"], (-121, 39), shapes) == "CA"
    assert openskimap.choose_state(["NV", "CA"], (-119, 39), shapes) == "NV"
    props = {"places": [{"iso3166_1Alpha2": "CA", "iso3166_2": "CA-BC"}, {"iso3166_1Alpha2": "US", "iso3166_2": "US-WA"},
                        {"iso3166_1Alpha2": "US", "iso3166_2": "US-WA"}, {"iso3166_1Alpha2": "US", "iso3166_2": None}]}
    assert openskimap.us_state_codes(props) == ["WA"]


def test_filter_is_sorted_and_rounded(osm_full, fetched):
    out = openskimap.filter_us(osm_full, fetched, decimals=3)
    keys = [(f["properties"]["state"], f["properties"]["name"].lower()) for f in out["features"]]
    assert keys == sorted(keys)
    ring = next(f for f in out["features"] if f["properties"]["id"] == "osm-peak")["geometry"]["coordinates"][0]
    assert ring[0] == [-106.36, 39.56]


def test_keep_feature_rules():
    us = [{"iso3166_1Alpha2": "US", "iso3166_2": "US-VT"}]
    base = {"type": "skiArea", "name": "X", "activities": ["downhill"], "status": "operating", "places": us}
    assert openskimap.keep_feature(base)
    assert openskimap.keep_feature({**base, "activities": ["downhill", "nordic"]})
    assert not openskimap.keep_feature({**base, "type": "run"})
    assert not openskimap.keep_feature({**base, "activities": ["nordic"]})
    assert not openskimap.keep_feature({**base, "status": None})
    assert not openskimap.keep_feature({**base, "status": "never_opened"})
    assert not openskimap.keep_feature({**base, "name": "  "})
    assert not openskimap.keep_feature({**base, "places": [{"iso3166_1Alpha2": "US", "iso3166_2": None}]})
    assert not openskimap.keep_feature({**base, "places": []})


def test_load_extract(raw_dir, fetched):
    ext = openskimap.load_us_extract(raw_dir / "openskimap" / openskimap.US_NAME)
    assert ext.fetched_date == fetched
    assert ext.source.id == openskimap.SOURCE_ID
    peak = next(a for a in ext.areas if a.osm_id == "osm-peak")
    assert abs(peak.lon - -106.30) < 1e-6 and abs(peak.lat - 39.60) < 1e-6  # polygon centroid
    assert peak.vertical_ft == round((3500.0 - 2400.123456) * openskimap.M_TO_FT)
    assert peak.base_ft == round(2400.123456 * openskimap.M_TO_FT) and peak.summit_ft == round(3500 * openskimap.M_TO_FT)
    assert peak.lift_count == 11


def test_write_extract_is_compact_json(raw_dir):
    data = json.loads((raw_dir / "openskimap" / openskimap.US_NAME).read_text())
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 8
