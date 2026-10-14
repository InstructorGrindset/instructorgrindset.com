"""Zillow ZORI fetch, parsing, extract and matching (pipeline/rent_zillow.py).

SYNTHETIC TEST FIXTURES: every region, rent and centroid below is invented. Never copy to data/.
"""
import csv
import datetime as dt
import json
import urllib.error

import pytest

from pipeline import rent_zillow as z

MONTHS = [f"2025-{m:02d}-{d}" for m, d in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31),
                                           (9, 30), (10, 31), (11, 30), (12, 31))] + \
         ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31"]


def _csv(path, header, rows):
    """A ZORI shaped CSV (names with commas are quoted, as in Zillow's files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header + MONTHS)
        for meta, vals in rows:
            w.writerow(list(meta) + [("" if v is None else str(v)) for v in vals])
    return path


def series(start, step=10.0, blanks_tail=0, blanks_head=0):
    vals = [start + step * i for i in range(len(MONTHS))]
    for i in range(blanks_head):
        vals[i] = None
    for i in range(1, blanks_tail + 1):
        vals[-i] = None
    return vals


COUNTY_HEADER = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State", "Metro", "StateCodeFIPS",
                 "MunicipalCodeFIPS"]
ZIP_HEADER = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State", "City", "Metro", "CountyName"]
METRO_HEADER = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName"]


def county_file(path):
    return _csv(path, COUNTY_HEADER, [
        (("901", "1", "Fixture County", "county", "CO", "CO", "Fixture Springs-Peakville, CO", "8", "998"), series(2000)),
        (("902", "2", "Basin Fixture County", "county", "UT", "UT", "", "49", "998"), series(1500, blanks_tail=3)),
        (("903", "3", "Elsewhere County", "county", "WY", "WY", "", "56", "1"), series(900)),
    ])


def zip_file(path):
    return _csv(path, ZIP_HEADER, [
        (("701", "1", "80999", "zip", "CO", "CO", "Peakville", "Fixture Springs, CO", "Fixture County"), series(2400)),
        (("702", "2", "80998", "zip", "CO", "CO", "Far Town", "Fixture Springs, CO", "Fixture County"), series(2600)),
        (("703", "3", "5672", "zip", "VT", "VT", "Leading Zero", "", "Green County"), series(1800)),
        # A ZIP with no value at all
        (("704", "4", "80997", "zip", "CO", "CO", "Empty", "Fixture Springs, CO", "Fixture County"), [None] * len(MONTHS)),
    ])


def metro_file(path):
    return _csv(path, METRO_HEADER, [
        (("102001", "0", "United States", "country", ""), series(1900)),
        (("801", "1", "Fixture Springs, CO", "msa", "CO"), series(2200)),
        (("802", "2", "Other Place, NV", "msa", "NV"), series(2100)),
    ])


GAZETTEER = "GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG                                                                                                               \n" \
            "80999\t1\t0\t1\t0\t39.6300\t-106.3000\n" \
            "80998\t1\t0\t1\t0\t39.9000\t-106.3000\n" \
            "80997\t1\t0\t1\t0\t39.6000\t-106.3100\n" \
            "05672\t1\t0\t1\t0\t44.5000\t-72.7000\n"
TARGETS = z.Targets({"08998": ("Fixture", "CO"), "49998": ("Basin Fixture", "UT")}, [(39.60, -106.30)], 10)


# ------------------------------------------------------------------- parsing
def test_parse_month_and_label():
    assert z.parse_month("2026-08-31") == dt.date(2026, 8, 31)
    assert z.parse_month("2026-02") == dt.date(2026, 2, 28)
    assert z.parse_month("RegionName") is None and z.parse_month("2026-13-01") is None
    assert z.month_label(dt.date(2026, 8, 31)) == "Aug 2026"


def test_latest_month_with_a_value_and_trailing_mean():
    months = [(z.parse_month(m), v) for m, v in zip(MONTHS, series(1000))]
    latest, value, mean, n = z.series_summary(months, 12)
    assert (latest, value, n) == (dt.date(2026, 8, 31), 1190.0, 12)
    assert mean == pytest.approx(sum(1000 + 10 * i for i in range(8, 20)) / 12)
    # trailing blanks: the latest month with a value wins, and the window ends there
    latest, value, mean, n = z.series_summary([(z.parse_month(m), v) for m, v in zip(MONTHS, series(1000, blanks_tail=3))], 12)
    assert (latest, value) == (dt.date(2026, 5, 31), 1160.0) and n == 12
    # a gap inside the window: the mean stays null (never a mean of fewer months), the latest value stays
    vals = series(1000)
    vals[-5] = None
    latest, value, mean, n = z.series_summary([(z.parse_month(m), v) for m, v in zip(MONTHS, vals)], 12)
    assert (value, mean, n) == (1190.0, None, 11)
    assert z.series_summary([(dt.date(2026, 1, 31), None)]) == (None, None, None, 0)


def test_parse_county_file_pads_fips_and_skips_nothing_real(tmp_path):
    rows = list(z.parse_zori_csv(county_file(tmp_path / "County.csv"), "county"))
    by = {r.region_id: r for r in rows}
    assert by["901"].county_fips == "08998" and by["901"].state == "CO" and by["901"].metro == "Fixture Springs-Peakville, CO"
    assert by["903"].county_fips == "56001"
    assert by["902"].latest_month == dt.date(2026, 5, 31) and by["902"].latest_value == 1500 + 10 * 16
    assert by["901"].latest_value == 2190.0 and by["901"].trailing_months == 12


def test_parse_zip_and_metro_files(tmp_path):
    zips = {r.region_name: r for r in z.parse_zori_csv(zip_file(tmp_path / "Zip.csv"), "zip")}
    assert set(zips) == {"80999", "80998", "05672", "80997"}   # a ZIP that lost its leading zero is padded
    assert zips["80999"].city == "Peakville" and zips["80999"].county_name == "Fixture County"
    assert zips["80997"].latest_value is None and zips["80997"].latest_month is None
    metros = list(z.parse_zori_csv(metro_file(tmp_path / "Metro.csv"), "metro"))
    assert [m.region_name for m in metros] == ["Fixture Springs, CO", "Other Place, NV"]   # the country row is skipped


def test_parse_rejects_an_unexpected_header(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("Region,Name,Value\n1,x,2\n")
    with pytest.raises(z.ZoriFormatError, match="RegionID, RegionName"):
        list(z.parse_zori_csv(p, "county"))


def test_name_helpers():
    assert z.norm_county("Summit County") == z.norm_county("Summit") == "summit"
    assert z.norm_county("Matanuska-Susitna Borough") == "matanuska-susitna"
    assert z.metro_key("Denver-Aurora-Lakewood, CO") == z.metro_key("Denver, CO") == ("denver", "co")
    assert round(z.haversine_km(39.6, -106.3, 39.63, -106.3), 2) == 3.34


# ------------------------------------------------------------------ extract
def _rows(tmp_path):
    return {"county": list(z.parse_zori_csv(county_file(tmp_path / "c.csv"), "county")),
            "zip": list(z.parse_zori_csv(zip_file(tmp_path / "z.csv"), "zip")),
            "metro": list(z.parse_zori_csv(metro_file(tmp_path / "m.csv"), "metro"))}


def test_select_rows_keeps_only_what_the_build_needs(tmp_path):
    (tmp_path / "gaz.txt").write_text(GAZETTEER)
    keep, notes = z.select_rows(_rows(tmp_path), TARGETS, z.load_gazetteer(tmp_path / "gaz.txt"))
    got = {(r.level, r.region_name) for r in keep}
    # ZIPs whose centroid is within 10 km of a base (80998 is 33 km away); counties with a resort; their metro
    assert got == {("zip", "80999"), ("zip", "80997"), ("county", "Fixture County"), ("county", "Basin Fixture County"),
                   ("metro", "Fixture Springs, CO")}
    assert notes == []
    zrow = next(r for r in keep if r.region_name == "80999")
    assert (zrow.lat, zrow.lon) == (39.63, -106.3)
    metro = next(r for r in keep if r.level == "metro")
    assert metro.county_fips_list == ["08998"]   # linked through the county row's Metro name (principal city)


def test_select_rows_without_centroids_skips_zip_matching(tmp_path):
    keep, notes = z.select_rows(_rows(tmp_path), TARGETS, None)
    assert not [r for r in keep if r.level == "zip"]
    assert notes == ["no Census Gazetteer ZCTA file in data/raw/rent/zillow/; ZIP matching skipped"]


def test_metro_link_from_zip_rows_when_the_county_has_no_row(tmp_path):
    rows = _rows(tmp_path)
    rows["county"] = [r for r in rows["county"] if r.county_fips != "08998"]
    keep, _ = z.select_rows(rows, TARGETS, None)
    metro = next(r for r in keep if r.level == "metro")
    assert metro.region_name == "Fixture Springs, CO" and metro.county_fips_list == ["08998"]


def test_extract_round_trip(tmp_path):
    (tmp_path / "gaz.txt").write_text(GAZETTEER)
    keep, _ = z.select_rows(_rows(tmp_path), TARGETS, z.load_gazetteer(tmp_path / "gaz.txt"))
    path = tmp_path / "zori_extract.csv"
    assert z.write_extract(keep, path) is True and z.write_extract(keep, path) is False   # unchanged: untouched
    back = z.load_extract(path)
    assert [(r.level, r.region_id, r.latest_month, r.latest_value, r.county_fips) for r in back.rows] == \
        [(r.level, r.region_id, r.latest_month, round(r.latest_value, 2) if r.latest_value else None, r.county_fips) for r in keep]
    assert path.read_text().splitlines()[0] == ",".join(z.EXTRACT_COLUMNS)


def test_refresh_extract_from_hand_placed_files(tmp_path):
    zdir = tmp_path / "zillow"
    county_file(zdir / z.FILES["county"])
    metro_file(zdir / z.DOWNLOAD_SUBDIR / z.FILES["metro"])
    (zdir / (z.FILES["county"] + ".json")).write_text(json.dumps({"fetched_date": "2026-09-30"}))
    found = z.find_inputs(zdir)
    assert set(found.files) == {"county", "metro"} and found.fetched["county"] == dt.date(2026, 9, 30)
    n, notes = z.refresh_extract(zdir, TARGETS)
    assert n == 3 and any("no Zillow zip ZORI file" in x for x in notes)
    ex = z.load_extract(zdir / z.EXTRACT_NAME)
    assert {r.level for r in ex.rows} == {"county", "metro"}
    assert next(r for r in ex.rows if r.level == "county").fetched_date == dt.date(2026, 9, 30)
    # a later refresh with only the ZIP file keeps the committed county and metro rows
    for p in list(zdir.rglob("*.csv")):
        if p.name != z.EXTRACT_NAME:
            p.unlink()
    zip_file(zdir / z.FILES["zip"])
    (zdir / "2024_Gaz_zcta_national.txt").write_text(GAZETTEER)
    n, _ = z.refresh_extract(zdir, TARGETS)
    assert {r.level for r in z.load_extract(zdir / z.EXTRACT_NAME).rows} == {"zip", "county", "metro"} and n == 5
    assert z.refresh_extract(tmp_path / "empty", TARGETS) == (None, [])


# -------------------------------------------------------------------- match
def _extract(tmp_path):
    (tmp_path / "gaz.txt").write_text(GAZETTEER)
    keep, _ = z.select_rows(_rows(tmp_path), TARGETS, z.load_gazetteer(tmp_path / "gaz.txt"))
    return z.ZoriExtract(keep)


def test_match_order_zip_then_county_then_metro_then_none(tmp_path):
    ex = _extract(tmp_path)
    m = z.match_resort(39.60, -106.30, "08998", ex, 10)
    assert m.level == "zip" and m.row.region_name == "80999" and m.distance_km == 3.34   # 80997 is nearer but has no value
    m = z.match_resort(39.60, -106.30, "08998", ex, 2)                                    # no ZIP within 2 km
    assert m.level == "county" and m.row.county_fips == "08998"
    ex.rows = [r for r in ex.rows if r.level != "county"]
    m = z.match_resort(39.60, -106.30, "08998", ex, 2)
    assert m.level == "metro" and m.row.region_name == "Fixture Springs, CO"
    assert z.match_resort(39.60, -106.30, "56001", ex, 2) is None                          # never interpolated
    assert z.match_resort(39.60, -106.30, None, None, 10) is None
    assert z.match_resort(39.60, -106.30, "08998", _extract(tmp_path), 10, ["county"]).level == "county"


def test_zori_rent_block_and_source(tmp_path):
    ex = _extract(tmp_path)
    block = z.zori_rent(z.match_resort(39.60, -106.30, "08998", ex, 2))
    assert block["value"] == 2190.0 and block["as_of"] == "2026-08-31" and block["confidence"] == "published"
    assert block["source_ids"] == ["zillow-zori"]
    assert block["note"].startswith("ZORI is a typical market rent for all homes and multifamily, smoothed")
    assert block["geography"] == {"level": "county", "id": "08998", "name": "Fixture County, CO"}
    assert z.zori_rent(z.match_resort(39.60, -106.30, "08998", ex, 2), "trailing_mean")["value"] == \
        pytest.approx(sum(2000 + 10 * i for i in range(8, 20)) / 12, abs=0.01)
    zblock = z.zori_rent(z.match_resort(39.60, -106.30, "08998", ex, 10))
    assert zblock["geography"]["name"] == "ZIP 80999 (Peakville, CO)" and "3.34 km from the base" in zblock["note"]
    for r in ex.rows:
        r.fetched_date = dt.date(2026, 9, 1)
    src = z.source(ex)
    assert (src.id, src.title, src.url, src.accessed_date) == (
        "zillow-zori", "Zillow Observed Rent Index (ZORI), Zillow Research", "https://www.zillow.com/research/data/",
        dt.date(2026, 9, 1))
    assert "Data through Aug 2026" in src.note


# -------------------------------------------------------------------- fetch
def test_fetch_fails_cleanly_naming_the_host(tmp_path):
    def blocked(url, dest):
        raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
    with pytest.raises(z.FetchBlocked) as e:
        z.fetch(tmp_path, blocked)
    msg = str(e.value)
    assert "files.zillowstatic.com could not be reached" in msg and "https://www.zillow.com/research/data/" in msg
    assert "County_zori_uc_sfrcondomfr_sm_month.csv" in msg and "data/raw/rent/zillow/" in msg


def test_fetch_writes_each_file_with_a_sidecar(tmp_path):
    seen = []

    def fake(url, dest):
        seen.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("RegionID,RegionName,2026-08-31\n")
    paths = z.fetch(tmp_path, fake, today=dt.date(2026, 10, 14))
    assert seen == [z.BASE_URL + n for n in z.FILES.values()] and all(u.startswith("https://files.zillowstatic.com/") for u in seen)
    assert json.loads((tmp_path / (z.FILES["zip"] + ".json")).read_text()) == \
        {"fetched_date": "2026-10-14", "url": z.BASE_URL + z.FILES["zip"]}
    assert len(paths) == 3


def test_requests_carry_a_generic_user_agent_and_no_email():
    assert "@" not in z.USER_AGENT and "instructorgrindset" not in z.USER_AGENT.lower()


def test_fetch_command_exits_2_when_blocked(monkeypatch, capsys):
    def blocked(dest, *a, **k):
        raise z.FetchBlocked(f"{z.HOST} could not be reached (URLError)")
    monkeypatch.setattr(z, "fetch", blocked)
    assert z.main(["fetch"]) == 2
    assert "files.zillowstatic.com" in capsys.readouterr().err
