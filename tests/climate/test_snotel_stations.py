"""SNOTEL station snowfall for the snowfall heat map (pipeline/climate/snotel_stations.py). Synthetic data only."""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json

import pytest

from pipeline.climate import http, snotel
from pipeline.climate import snotel_stations as ss
from pipeline.climate.density_estimate import estimate_density_pct
from pipeline.climate.units import f_to_c


def station(triplet="766:UT:SNTL", lat=40.57, lon=-111.65, elev=8640.0, begin="1989-10-01", end="2100-01-01"):
    return {"stationTriplet": triplet, "networkCode": triplet.split(":")[2], "name": " Snowbird ",
            "latitude": lat, "longitude": lon, "elevation": elev, "beginDate": f"{begin} 00:00",
            "endDate": f"{end} 00:00"}


def daily_payload(triplet, days):
    """AWDB /data payload from {date: (swe, depth, tavg)}."""
    blocks = []
    for code, k in (("WTEQ", 0), ("SNWD", 1), ("TAVG", 2)):
        blocks.append({"stationElement": {"elementCode": code},
                       "values": [{"date": d.isoformat(), "value": v[k]} for d, v in sorted(days.items())
                                  if v[k] is not None]})
    return [{"stationTriplet": triplet, "data": blocks}]


# ---------------------------------------------------------------- station parsing
def test_station_list_keeps_snotel_stations_with_coordinates():
    rows = [station(), station("1000:OR:SNTL", elev=6020.0), station("2000:CA:SCAN"),
            {**station("1001:AK:SNTL"), "latitude": None}]
    out = ss.parse_station_list(rows)
    assert [s.triplet for s in out] == ["766:UT:SNTL", "1000:OR:SNTL"]
    assert out[0].name == "Snowbird" and out[0].elevation_ft == 8640.0 and out[0].state == "UT"
    assert out[0].begin_date == dt.date(1989, 10, 1)


def test_possible_years_and_candidates_follow_the_station_record():
    full = snotel.parse_stations([station()])[0]
    late = snotel.parse_stations([station("1:CO:SNTL", begin="2019-06-01")])[0]
    gone = snotel.parse_stations([station("2:CO:SNTL", end="2014-08-01")])[0]
    assert ss.possible_years(full, 2011, 2025) == 15
    assert ss.possible_years(late, 2011, 2025) == 6      # water years 2020 to 2025
    assert ss.possible_years(gone, 2011, 2025) == 3      # 2011 to 2013
    assert [s.triplet for s in ss.candidates([late, full, gone], 2011, 2025, 8)] == ["766:UT:SNTL"]


def test_parse_daily_keeps_negative_air_temperatures_and_drops_faults():
    d0 = dt.date(2020, 1, 1)
    days = {d0: (10.0, 40.0, -12.5), d0 + dt.timedelta(1): (-0.2, 41.0, 150.0),
            d0 + dt.timedelta(2): (10.5, 44.0, None)}
    series, tavg = ss.parse_daily(daily_payload("766:UT:SNTL", days), "766:UT:SNTL")
    assert series[d0] == (10.0, 40.0) and series[d0 + dt.timedelta(1)] == (None, 41.0)
    assert tavg == {d0: -12.5}
    other, none = ss.parse_daily(daily_payload("1:CO:SNTL", days), "766:UT:SNTL")
    assert other == {} and none == {}


def test_temperature_records_feed_the_station_temperature_as_the_daily_mean():
    d0 = dt.date(2020, 1, 1)
    rec = ss.temperature_records({d0: 20.0})[d0]
    assert rec.tmean_f == 20.0 and rec.snowfall_in is None


# ---------------------------------------------------------------- water years, completeness, median
def _snow_days(wy, per_day=1.0, missing=()):
    """Daily new snow over one water year; ``missing`` dates are None."""
    out = {}
    d = dt.date(wy - 1, 10, 1)
    while d <= dt.date(wy, 9, 30):
        out[d] = None if d in missing else (per_day if d.month in (11, 12, 1, 2, 3, 4) else 0.0)
        d += dt.timedelta(1)
    return out


def test_water_year_totals_sum_each_october_to_september_year():
    snow = {**_snow_days(2020, 1.0), **_snow_days(2021, 2.0)}
    out = ss.water_year_totals(snow, 2020, 2021, 0.9)
    # Nov to Apr of WY 2020 (a leap year): 30 + 31 + 31 + 29 + 31 + 30 days.
    assert out == {2020: 182.0, 2021: 2 * 181.0}


def test_completeness_filter_drops_years_with_gaps_in_the_snow_months():
    gaps = {dt.date(2020, 1, 1) + dt.timedelta(k) for k in range(20)}       # 20 of 182 snow days missing
    few = {dt.date(2021, 2, 1) + dt.timedelta(k) for k in range(15)}        # 15 of 181: still 91.7 percent
    summer = {dt.date(2021, 7, 1) + dt.timedelta(k) for k in range(60)}     # summer gaps do not count
    snow = {**_snow_days(2020, missing=gaps), **_snow_days(2021, missing=few | summer)}
    out = ss.water_year_totals(snow, 2020, 2022, 0.9)
    assert out[2020] is None
    assert out[2021] == pytest.approx(181.0 - 15)
    assert out[2022] is None   # no data at all


def test_median_needs_the_minimum_number_of_complete_years():
    totals = {2011: 300.0, 2012: 100.0, 2013: None, 2014: 350.0, 2015: 1000.0}
    assert ss.median_annual(totals, 4) == (325.0, [2011, 2012, 2014, 2015])
    assert ss.median_annual(totals, 5) == (None, [2011, 2012, 2014, 2015])
    # The median shrugs off one odd year that a mean would not.
    assert ss.median_annual({**totals, 2016: 5000.0}, 4)[0] == 350.0


def test_station_snowfall_derives_the_median_of_complete_years(settings):
    """Every snow month day gains 0.1 in SWE and 1 in of depth at -6.7 C (20 F); the pack melts out each May.
    Each winter is one long storm that starts in November: three November storms at 10 percent give November a
    station density (0.1 in / 10 percent = 1 in a day). December to April have none, so they take the Anderson
    density at the station's own temperature, never below the 1 in depth gain."""
    st = snotel.parse_stations([station()])[0]
    days = {}
    for wy in (2018, 2019, 2020):
        d = dt.date(wy - 1, 9, 30)
        swe = depth = 0.0
        while d <= dt.date(wy, 9, 30):
            if d.month in (11, 12, 1, 2, 3, 4):
                swe += 0.1
                depth += 1.0
            elif d.month == 5 and d.day == 1:
                swe = depth = 0.0
            days[d] = (round(swe, 1), depth, 20.0)
            d += dt.timedelta(1)
    r = ss.station_snowfall(st, daily_payload(st.triplet, days), settings, 2018, 2020, 3)
    assert r.years_used == 3 and r.complete_years == "2018 2019 2020"
    per_day = max(1.0, 0.1 / (estimate_density_pct(f_to_c(20.0)) / 100))
    # WY 2018 and 2019: 30 November days and 151 later snow days (182 in WY 2020, Feb 29); the median is the 181.
    assert r.annual_snowfall_in == pytest.approx(30 * 1.0 + 151 * per_day, abs=0.2)
    assert r.method == ss.METHOD and r.first_water_year == 2018
    short = ss.station_snowfall(st, daily_payload(st.triplet, days), settings, 2018, 2020, 4)
    assert short.annual_snowfall_in is None and short.years_used == 0


# ---------------------------------------------------------------- extract and cache
def test_extract_round_trips_and_usable_needs_years_and_elevation(tmp_path):
    rows = [ss.StationSnowfall("766:UT:SNTL", "Snowbird", "UT", 40.57, -111.65, 8640.0, 402.5, 15,
                               "2011 2012", 2011, 2025),
            ss.StationSnowfall("1:CO:SNTL", "Short", "CO", 39.0, -106.0, 10000.0, None, 0, "2011", 2011, 2025),
            ss.StationSnowfall("2:CO:SNTL", "No height", "CO", 39.1, -106.1, None, 200.0, 9, "", 2011, 2025)]
    ss.write_extract(rows, tmp_path, {"source": {"id": ss.SOURCE_ID}})
    text = (tmp_path / ss.EXTRACT_NAME).read_text()
    assert next(csv.reader(io.StringIO(text))) == ss.COLUMNS
    back, src = ss.load_extract(tmp_path)
    assert src["source"]["id"] == ss.SOURCE_ID
    assert [r.station_id for r in back] == ["1:CO:SNTL", "2:CO:SNTL", "766:UT:SNTL"]
    snowbird = back[-1]
    assert (snowbird.annual_snowfall_in, snowbird.years_used, snowbird.elevation_ft) == (402.5, 15, 8640.0)
    assert [r.station_id for r in ss.usable(back, 8)] == ["766:UT:SNTL"]
    with pytest.raises(ValueError):
        ss.parse_extract_row({"station_id": "", "lat": "1", "lon": "2"})


def test_fetch_daily_caches_gzip_and_skips_the_api_the_second_time(tmp_path, monkeypatch):
    calls = []

    def fake(url, params=None, **kw):
        calls.append(params)
        return [{"stationTriplet": params["stationTriplets"], "data": []}]

    monkeypatch.setattr(http, "get_json", fake)
    start, end = ss.period_for(2011, 2025)
    assert start == dt.date(2010, 9, 30) and end == dt.date(2025, 9, 30)
    first = ss.fetch_daily(tmp_path, "766:UT:SNTL", start, end)
    again = ss.fetch_daily(tmp_path, "766:UT:SNTL", start, end)
    assert len(calls) == 1 and calls[0]["elements"] == "WTEQ,SNWD,TAVG" and calls[0]["duration"] == "DAILY"
    assert again["response"] == first["response"]
    path = ss.daily_cache_path(tmp_path, "766:UT:SNTL")
    assert path.parent.name == "download" and json.loads(gzip.decompress(path.read_bytes()))["triplet"] == "766:UT:SNTL"
    # A cache that does not cover the window is fetched again.
    ss.fetch_daily(tmp_path, "766:UT:SNTL", start, dt.date(2026, 9, 30))
    assert len(calls) == 2


def test_heat_settings_are_read_and_checked():
    defs = {"heat": {"snowfall": {"snotel": {"first_water_year": 2011, "last_water_year": 2025,
                                             "min_complete_years": 8}}}}
    assert ss.heat_snotel_settings(defs)["min_complete_years"] == 8
    with pytest.raises(ValueError):
        ss.heat_snotel_settings({"heat": {"snowfall": {"snotel": {"first_water_year": 2020, "last_water_year": 2011,
                                                                    "min_complete_years": 8}}}})
    with pytest.raises(ValueError):
        ss.heat_snotel_settings({})


# ---------------------------------------------------------------- validation
def _resort(rid, lat, lon, snow, base=None, summit=None):
    src = {"source_id": "s", "as_of": "2026-10-13", "confidence": "published"}
    return {"id": rid, "name": rid.title(), "state": "UT", "lat": lat, "lon": lon,
            "snow": {"annual_avg_in": {"value": snow, **src}} if snow else None,
            "elevation": {"base_ft": {"value": base, **src} if base else None,
                          "summit_ft": {"value": summit, **src} if summit else None}}


def test_match_resorts_needs_a_station_within_10_km_and_300_m():
    stations = [ss.StationSnowfall("766:UT:SNTL", "Snowbird", "UT", 40.57, -111.65, 9600.0, 400.0, 15, "", 2011, 2025),
                ss.StationSnowfall("9:UT:SNTL", "Low", "UT", 40.58, -111.64, 6000.0, 150.0, 15, "", 2011, 2025)]
    resorts = [
        _resort("alta", 40.58, -111.64, 500, 8500, 11000),       # mid 9,750 ft: Snowbird is 150 ft off
        _resort("far", 41.5, -111.64, 300, 8500, 11000),         # 100 km away
        _resort("tall", 40.58, -111.64, 300, 11000, 13000),      # mid 12,000 ft: 731 m above the station
        _resort("nofacts", 40.58, -111.64, None, 8500, 11000),
        _resort("baseonly", 40.58, -111.64, 200, 6200),           # base 6,200 ft: the Low station fits
    ]
    out = ss.match_resorts(resorts, stations, 10, 300)
    assert [(m.resort_id, m.station_id) for m in out] == [("alta", "766:UT:SNTL"), ("baseonly", "9:UT:SNTL")]
    alta = out[0]
    assert alta.diff_pct == pytest.approx(-20.0) and alta.resort_elevation_ft == 9750.0
    s = ss.summarize(out)
    assert s["n"] == 2 and s["median_diff_pct"] == pytest.approx((-20.0 - 25.0) / 2)
    assert s["within_25_pct"] == 1.0 and ss.summarize([])["median_diff_pct"] is None


def test_validation_section_replaces_itself_in_the_report(tmp_path):
    doc = tmp_path / "climate-validation.md"
    doc.write_text("# Climate validation\n\nResort table.\n")
    m = ss.Match("alta", "Alta", "UT", 500.0, "s", 9750.0, "766:UT:SNTL", "Snowbird", 1.2, 9600.0, 400.0)
    summary = ss.summarize([m])
    noaa = {"n": 0, "median_diff_pct": None, "median_abs_diff_pct": None, "within_50_pct": None}
    cfg = {"first_water_year": 2011, "last_water_year": 2025, "min_complete_years": 8}
    sec = ss.validation_section([m], [], summary, noaa, 10, 9, cfg, 10, 300, "2026-10-13T00:00:00Z")
    ss.write_validation(doc, sec)
    ss.write_validation(doc, sec.replace("Alta", "Alta Ski Area"))
    text = doc.read_text()
    assert text.startswith("# Climate validation\n\nResort table.\n\n" + ss.VALIDATION_MARKER)
    assert text.count(ss.VALIDATION_MARKER) == 1 and "Alta Ski Area" in text and "Result: ships" in text
    assert "-20%" in text and "—" not in text


def test_climate_run_report_keeps_the_snotel_section(tmp_path):
    from pipeline.climate.run import write_validation_report

    doc = tmp_path / "climate-validation.md"
    ss.write_validation(doc, ss.VALIDATION_MARKER + "\n\nStation table.\n")
    write_validation_report(doc, {}, "2026-10-13T00:00:00Z")
    text = doc.read_text()
    assert text.startswith("# Climate validation") and "Summary: no resorts." in text
    assert text.count(ss.VALIDATION_MARKER) == 1 and text.rstrip().endswith("Station table.")
