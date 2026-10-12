import datetime as dt
import json

import pytest

from pipeline import rent

HUD_COUNTY = {"data": {"county_name": "Fixture County", "smallarea_status": "0",
                       "basicdata": {"zip_code": "MSA level", "Efficiency": 1478, "One-Bedroom": 1640,
                                     "Two-Bedroom": 2094, "Three-Bedroom": 2773, "year": "2026"}}}
HUD_SAFMR = {"data": {"county_name": "Fixture Metro County", "smallarea_status": "1", "basicdata": [
    {"zip_code": "MSA level", "Efficiency": 1200, "One-Bedroom": 1300, "Two-Bedroom": 1600, "year": "2026"},
    {"zip_code": "80001", "Efficiency": 1100, "One-Bedroom": 1250, "Two-Bedroom": 1500, "year": "2026"},
    {"zip_code": "80002", "Efficiency": "", "One-Bedroom": 1400, "Two-Bedroom": 1700, "year": "2026"},
]}}
ACS_COUNTY = [["NAME", "B25064_001E", "state", "county"], ["Fixture County, Colorado", "1950", "08", "998"]]
ACS_MULTI = [["NAME", "B25064_001E", "state", "county"], ["A", "1000", "08", "001"], ["B", "-666666666", "08", "002"]]
ACS_ZCTA = [["NAME", "B25064_001E", "zip code tabulation area"], ["ZCTA5 80001", "1777", "80001"]]


def test_county_for(counties_geojson):
    counties = rent.load_counties(counties_geojson)
    assert rent.county_for(39.6, -106.3, counties).fips == "08998"
    assert rent.county_for(40.6, -111.6, counties).fips == "49998"  # MultiPolygon
    assert rent.county_for(30.0, -90.0, counties) is None


def test_point_in_real_counties(repo_root):
    path = repo_root / "data" / "raw" / "geo" / "us-counties.geojson"
    if not path.exists():
        pytest.skip("us-counties.geojson not built")
    counties = rent.load_counties(path)
    assert rent.county_for(39.6403, -106.3742, counties).fips == "08037"   # Vail, Eagle County CO
    assert rent.county_for(40.5884, -111.6386, counties).fips == "49035"   # Alta, Salt Lake County UT


def test_parse_hud_fmr_county_and_zip():
    p = rent.parse_hud_fmr(HUD_COUNTY)
    assert (p["county_name"], p["year"], p["studio"], p["one_br"], p["two_br"], p["zips"]) == \
        ("Fixture County", 2026, 1478, 1640, 2094, {})
    p = rent.parse_hud_fmr(HUD_SAFMR)
    assert p["studio"] == 1200 and p["year"] == 2026
    assert p["zips"] == {"80001": {"studio": 1100, "one_br": 1250, "two_br": 1500},
                         "80002": {"studio": None, "one_br": 1400, "two_br": 1700}}
    assert rent.parse_hud_fmr({})["studio"] is None


def test_parse_hud_csv(tmp_path):
    path = tmp_path / "hud_fmr_2026.csv"
    path.write_text("fips,countyname,fmr_0,fmr_1,fmr_2\n0899899999,Fixture County,1478,1640,2094\n49998,Basin,900,1000,1200\nbad,x,1,2,3\n")
    rows = rent.parse_hud_fmr_csv(path)
    assert rows["08998"] == {"county_name": "Fixture County", "studio": 1478, "one_br": 1640, "two_br": 2094}
    assert set(rows) == {"08998", "49998"}


def test_parse_acs():
    assert rent.parse_acs(ACS_COUNTY) == {"08998": 1950}
    assert rent.parse_acs(ACS_MULTI) == {"08001": 1000, "08002": None}  # Census null sentinel
    assert rent.parse_acs(ACS_ZCTA) == {"80001": 1777}
    assert rent.parse_acs([]) == {}


def _cache(rent_dir, hud=HUD_COUNTY, acs=ACS_COUNTY):
    (rent_dir / "hud_fmr").mkdir(parents=True)
    (rent_dir / "acs").mkdir()
    if hud is not None:
        (rent_dir / "hud_fmr" / "08998.json").write_text(json.dumps({"fetched_date": "2026-10-02", "url": "u", "response": hud}))
    if acs is not None:
        (rent_dir / "acs" / "2024_county_08998.json").write_text(json.dumps({"fetched_date": "2026-10-03", "url": "u", "response": acs}))


def test_baseline_for(tmp_path, counties_geojson):
    county = rent.load_counties(counties_geojson)[0]
    _cache(tmp_path / "rent")
    baseline, sources = rent.baseline_for(county, tmp_path / "rent", 2024)
    assert [s.id for s in sources] == ["hud-fmr-fy2026", "census-acs5-2024-b25064"]
    assert sources[0].accessed_date == dt.date(2026, 10, 2)
    assert baseline["county_fips"] == "08998" and baseline["county_name"] == "Fixture County"
    assert baseline["fmr"]["studio"] == {"value": 1478, "source_id": "hud-fmr-fy2026", "as_of": "2026-10-02",
                                         "confidence": "published", "note": "HUD FMR FY2026, Fixture County"}
    assert baseline["acs_median_gross_rent"]["value"] == 1950
    from pipeline.schemas import RentBaseline
    RentBaseline.model_validate(baseline, context={"source_ids": {s.id for s in sources}})


def test_baseline_acs_only_and_csv_fallback(tmp_path, counties_geojson):
    county = rent.load_counties(counties_geojson)[0]
    _cache(tmp_path / "rent", hud=None)
    baseline, sources = rent.baseline_for(county, tmp_path / "rent", 2024)
    assert [s.id for s in sources] == ["census-acs5-2024-b25064"] and baseline["fmr"]["studio"] is None
    (tmp_path / "rent" / "hud_fmr_2025.csv").write_text("fips,countyname,fmr_0,fmr_1,fmr_2\n08998,Fixture,1400,1500,1900\n")
    baseline, sources = rent.baseline_for(county, tmp_path / "rent", 2024)
    assert sources[0].id == "hud-fmr-fy2025" and baseline["fmr"]["studio"]["value"] == 1400
    assert rent.baseline_for(county, tmp_path / "empty", 2024) == (None, [])


def test_baseline_value_preference():
    b = {"fmr": {"studio": None, "one_br": {"value": 1600}, "two_br": None},
         "acs_median_gross_rent": {"value": 1950}, "county_name": "F"}
    assert rent.baseline_value(b, ["fmr_studio", "acs_median"]) == ("acs_median", {"value": 1950})
    assert rent.baseline_value(b, ["fmr_one_br", "acs_median"])[0] == "fmr_one_br"
    assert rent.baseline_value(None, ["fmr_studio"]) is None
    assert rent.baseline_value(b, ["fmr_studio", "fmr_two_br"]) is None


def test_fetch_counties_mocked(tmp_path, monkeypatch):
    calls = []

    def getter(url, headers):
        calls.append((url, headers))
        if "huduser" in url:
            return HUD_COUNTY
        if "county%3A999" in url:
            raise OSError("blocked")
        return ACS_COUNTY
    monkeypatch.delenv("HUD_API_TOKEN", raising=False)
    msgs = rent.fetch_counties(["08998"], tmp_path, 2024, getter=getter)
    assert "set HUD_API_TOKEN" in msgs[0] and all("huduser" not in c[0] for c in calls)
    assert (tmp_path / "acs" / "2024_county_08998.json").exists()
    monkeypatch.setenv("HUD_API_TOKEN", "t0ken")
    msgs = rent.fetch_counties(["08998", "08999"], tmp_path, 2024, hud_year=2026, getter=getter)
    hud = [c for c in calls if "huduser" in c[0]]
    assert hud[0][0].endswith("/fmr/data/0899899999?year=2026") and hud[0][1] == {"Authorization": "Bearer t0ken"}
    assert any("ACS 08999: OSError" in m for m in msgs)
    assert json.loads((tmp_path / "hud_fmr" / "08998.json").read_text())["response"] == HUD_COUNTY
