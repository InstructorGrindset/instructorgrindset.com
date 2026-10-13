import copy

import pytest
from pydantic import ValidationError

from pipeline.config import load_config
from pipeline.housing import assemble_detail, cheapest_employee_site, display_rent, housing_summary, rent_input
from pipeline.schemas import DetailFile, HousingBatch
from pipeline.schemas.housing import HousingSite

CTX = {"source_ids": {"src-a", "src-b", "hud-fmr-fy2026", "census-acs5-2024-b25064", "openskimap-lifts",
                      "routing-valhalla", "gtfs-fixture"}}
SV = {"source_id": "src-a", "as_of": "2026-10-01", "confidence": "published"}


def site(**kw):
    base = {"id": "s1", "resort_id": "fixture-peak", "name": "Staff Lodge", "type": "resort_owned", "lat": 39.61,
            "lon": -106.31, "location_source_id": "src-a", "precision": "neighborhood",
            "rent_monthly": {"value": {"low": 600, "high": 900}, **SV}, "confidence": "published",
            "verified_date": "2026-10-01"}
    return HousingSite.model_validate({**base, **kw}, context=CTX)


BASELINE = {"county_fips": "08998", "county_name": "Fixture",
            "fmr": {"studio": {"value": 1478, "source_id": "hud-fmr-fy2026", "as_of": "2026-10-02", "confidence": "published"},
                    "one_br": None, "two_br": None},
            "acs_median_gross_rent": {"value": 1950, "source_id": "census-acs5-2024-b25064", "as_of": "2026-10-03",
                                      "confidence": "published"}}


def test_precision_needs_address():
    with pytest.raises(ValidationError, match="publicly listed address"):
        site(precision="building")
    s = site(precision="building", address={"value": "1 Example Way", **SV})
    assert s.address.value == "1 Example Way"
    with pytest.raises(ValidationError, match="not defined"):
        site(precision="building", address={"value": "1 Example Way", **SV, "source_id": "nowhere"})


def test_housing_site_rules():
    with pytest.raises(ValidationError, match="low must not exceed"):
        site(rent_monthly={"value": {"low": 900, "high": 600}, **SV})
    with pytest.raises(ValidationError, match="not defined"):
        site(location_source_id="nowhere")
    with pytest.raises(ValidationError):
        site(type="hotel")
    with pytest.raises(ValidationError):
        site(occupancy="dorm")
    with pytest.raises(ValidationError, match="negative"):
        site(deposit={"value": -1, **SV})
    raw = site().model_dump(mode="json")
    with pytest.raises(ValidationError, match="duplicate housing site id"):
        HousingBatch.model_validate({"batch": "b", "researched_date": "2026-10-01", "sites": [raw, raw]}, context=CTX)


def test_display_rent_priority():
    assert display_rent(site(), BASELINE, ["fmr_studio"])["value"] == {"low": 600, "high": 900}
    no_rent = site(rent_monthly=None, type="market")
    r = display_rent(no_rent, BASELINE, ["fmr_studio", "acs_median"])
    assert r["value"] == {"low": 1478, "high": 1478} and r["confidence"] == "estimated"
    assert r["source_id"] == "hud-fmr-fy2026" and "HUD FMR studio" in r["note"]
    assert display_rent(no_rent, BASELINE, ["acs_median"])["source_id"] == "census-acs5-2024-b25064"
    assert display_rent(no_rent, None, ["fmr_studio"]) is None


def test_rent_input_selection():
    """Owner decision 4: mean of employee housing rent mids, else ACS median, else HUD FMR one bedroom."""
    order, pref = ["employee_housing", "acs_median", "fmr_one_br"], ["fmr_studio"]
    sites = [site(id="a", type="market", rent_monthly={"value": {"low": 300, "high": 400}, **SV}),
             site(id="b", type="master_lease", rent_monthly={"value": {"low": 700, "high": 800}, **SV}),
             site(id="c", type="resort_owned", rent_monthly={"value": {"low": 650, "high": 950}, **SV, "source_id": "src-b"}),
             site(id="d", type="resort_owned", rent_monthly=None)]
    # (750 + 800) / 2; the market site and the site without rent are left out
    assert rent_input(sites, BASELINE, order, pref) == (775.0, "employee_housing_mean:b,c", "src-a, src-b")
    assert cheapest_employee_site(sites)[0].id == "c"
    assert rent_input(sites[:1], BASELINE, order, pref) == (1950.0, "acs_median", "census-acs5-2024-b25064")
    no_acs = {**BASELINE, "acs_median_gross_rent": None,
              "fmr": {**BASELINE["fmr"], "one_br": {"value": 1640, "source_id": "hud-fmr-fy2026", "as_of": "2026-10-02",
                                                     "confidence": "published"}}}
    assert rent_input([], no_acs, order, pref) == (1640.0, "fmr_one_br", "hud-fmr-fy2026")
    assert rent_input([], {**no_acs, "fmr": {"studio": None, "one_br": None, "two_br": None}}, order, pref) == (None, None, None)
    assert rent_input([], BASELINE, ["baseline"], ["fmr_studio"]) == (1478.0, "fmr_studio", "hud-fmr-fy2026")
    area = {"value": {"low": 1000, "high": 1200}, "source_id": "rep", "as_of": "2026-10-01", "confidence": "reported"}
    assert rent_input([], None, ["reported_area"], pref, area_reported=area) == (1100.0, "reported_area", "rep")
    assert rent_input([], None, order, pref) == (None, None, None)


def test_housing_summary():
    assert housing_summary([], True) is None
    s = housing_summary([site(id="a", rent_monthly={"value": {"low": 800, "high": 900}, **SV}), site(id="b"),
                         site(id="c", rent_monthly=None)], has_transit=False)
    assert s == {"site_count": 3, "cheapest_rent_monthly_low": 600, "cheapest_site_id": "b", "has_transit": False}
    assert housing_summary([site(rent_monthly=None)], True)["cheapest_rent_monthly_low"] is None


class FakeRouter:
    source_id = "routing-valhalla"

    def route(self, origin, dest):
        return {"minutes": 12.0, "distance_mi": 5.5,
                "geometry": {"type": "LineString", "coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]]}}

    def isochrones(self, point):
        ring = [[point[1] - 0.1, point[0] - 0.1], [point[1] + 0.1, point[0] - 0.1], [point[1], point[0] + 0.1],
                [point[1] - 0.1, point[0] - 0.1]]
        return [{"minutes": m, "geometry": {"type": "Polygon", "coordinates": [ring]}} for m in (15, 30, 45)]


RESORT = {"id": "fixture-peak", "lat": 39.6, "lon": -106.3, "location_source_id": "src-a"}
HEADER = {"version": 1, "generated_at": "2026-10-12T00:00:00Z"}
LIFTS = {"type": "FeatureCollection", "features": [{"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [[-106.3, 39.6], [-106.28, 39.62]]},
         "properties": {"name": "Fixture Express", "lift_type": "chair_lift", "source_id": "openskimap-lifts"}}]}


def test_assemble_detail_full(tmp_path, gtfs_writer, gtfs_tables):
    import datetime as dt

    from pipeline.schemas.common import Source
    from pipeline.transit import extract_gtfs

    src = Source(id="gtfs-fixture", url="https://example.org/g.zip", title="t", accessed_date=dt.date(2026, 10, 1))
    ex = extract_gtfs(gtfs_writer(tmp_path / "g.zip", gtfs_tables), "fixture", [(39.6, -106.3), (39.7, -106.2)], 1.0, src)
    defs = load_config().definitions.model_copy(deep=True)
    defs.transit.walk_speed_kmh, defs.transit.max_walk_km = 5.0, 1.0  # synthetic, test only
    sites = [site(id="town-flat", type="market", rent_monthly=None, lat=39.7, lon=-106.2005), site()]
    d = assemble_detail(RESORT, sites, LIFTS, BASELINE, [ex], FakeRouter(), defs, HEADER)
    DetailFile.model_validate(d, context=CTX)
    assert d["base_area"] == {"lat": 39.6, "lon": -106.3, "source_id": "src-a"}
    assert [i["minutes"] for i in d["isochrones"]] == [15, 30, 45] and d["isochrones"][0]["mode"] == "drive"
    town = d["housing"][0]
    assert town["rent_monthly"]["confidence"] == "estimated" and town["rent_monthly"]["value"]["low"] == 1478
    assert [c["mode"] for c in town["commutes"]] == ["drive", "bus"]
    assert town["commutes"][0] == {"mode": "drive", "minutes": 12.0, "distance_mi": 5.5, "transit_route_ids": [],
                                   "geometry": {"type": "LineString", "coordinates": [[-106.2005, 39.7], [-106.3, 39.6]]},
                                   "source_id": "routing-valhalla"}
    assert town["commutes"][1]["transit_route_ids"] == ["fixture:blue"]
    assert d["transit"]["source_ids"] == ["gtfs-fixture"] and d["transit"]["stops"]
    assert d["housing"][1]["rent_monthly"]["confidence"] == "published"


def test_assemble_detail_minimal():
    defs = load_config().definitions
    d = assemble_detail(RESORT, [], LIFTS, None, [], None, defs, HEADER)
    DetailFile.model_validate(d, context=CTX)
    assert d["housing"] == [] and d["transit"] is None and d["isochrones"] == [] and d["rent_baseline"] is None


def test_detail_file_validation():
    defs = load_config().definitions
    d = assemble_detail(RESORT, [], LIFTS, None, [], None, defs, HEADER)
    bad = copy.deepcopy(d)
    bad["lifts"]["features"][0]["properties"]["extra"] = 1
    with pytest.raises(ValidationError, match="exactly name, lift_type, source_id"):
        DetailFile.model_validate(bad, context=CTX)
    bad = copy.deepcopy(d)
    bad["isochrones"] = [{"minutes": 15, "mode": "drive", "geometry": LIFTS["features"][0]["geometry"], "source_id": "routing-valhalla"}]
    with pytest.raises(ValidationError):
        DetailFile.model_validate(bad, context=CTX)
    bad = copy.deepcopy(d)
    bad["base_area"]["source_id"] = "src-a"
    bad["isochrones"] = [{"minutes": 15, "mode": "drive", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]},
                          "source_id": "nowhere"}]
    with pytest.raises(ValidationError, match="not defined"):
        DetailFile.model_validate(bad, context=CTX)


REPORTED = {"value": {"low": 700, "high": 800}, "source_id": "src-b", "as_of": "2026-10-03", "confidence": "reported",
            "note": "range of 3 approved reports, season 2025-26"}


def test_reported_aggregate_beats_estimated_site_rent():
    """Regression: an estimated site rent must not hide a reported aggregate (Published > Reported > Estimated)."""
    est = site(id="e", rent_monthly={"value": {"low": 500, "high": 600}, **SV, "confidence": "estimated"})
    assert display_rent(est, BASELINE, ["fmr_studio"], REPORTED)["confidence"] == "reported"
    assert display_rent(est, BASELINE, ["fmr_studio"])["confidence"] == "estimated"   # no aggregate: site estimate
    pub = site(id="p", rent_monthly={"value": {"low": 900, "high": 950}, **SV})
    assert display_rent(pub, BASELINE, ["fmr_studio"], REPORTED)["confidence"] == "published"
    # the same rule feeds lucrativeness and the housing summary
    assert rent_input([est], None, ["employee_housing"], [], {"e": REPORTED}) == (750.0, "employee_housing_mean:e", "src-b")
    assert housing_summary([est], False, {"e": REPORTED})["cheapest_rent_monthly_low"] == 700


def test_raw_housing_rejects_reported_rent():
    raw = site().model_dump(mode="json")
    raw["rent_monthly"]["confidence"] = "reported"
    with pytest.raises(ValidationError, match="cannot be confidence reported in a housing file"):
        HousingBatch.model_validate({"batch": "b", "researched_date": "2026-10-01", "sites": [raw]}, context=CTX)
