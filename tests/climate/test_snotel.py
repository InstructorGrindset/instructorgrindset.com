from datetime import date

import pytest

from climate_helpers import load_fixture
from pipeline.climate import snotel
from pipeline.climate.snotel import (
    detect_snotel_storms,
    evaluate_station,
    haversine_km,
    parse_station_daily,
    parse_stations,
    select_candidates,
)


@pytest.fixture
def stations():
    return parse_stations(load_fixture("awdb_stations.json"))


def test_parse_stations_keeps_snotel_only(stations):
    assert [s.triplet for s in stations] == ["9001:CO:SNTL", "9002:CO:SNTL", "9003:CO:SNTL", "9005:CO:SNTL"]
    s = stations[0]
    assert (s.name, s.lat, s.lon, s.elevation_ft) == ("Synthetic Near", 39.63, -106.37, 10300.0)
    assert s.end_date == date(2100, 1, 1)
    assert s.station_id == "9001" and s.state == "CO"


def test_haversine():
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.195, abs=0.01)
    assert haversine_km(39.6, -106.35, 39.6, -106.35) == 0


def test_select_distance_only(stations, settings):
    period = (date(2001, 10, 1), date(2026, 9, 30))
    cands = select_candidates(39.60, -106.35, None, stations, settings.snotel, period)
    # Retired station is outside the period; far station is outside 25 km.
    assert [c.station.triplet for c in cands] == ["9005:CO:SNTL", "9001:CO:SNTL"]
    assert all(c.mode == "distance_only" and c.elevation_diff_ft is None for c in cands)
    assert cands[0].distance_km < cands[1].distance_km


def test_select_with_elevation_band(stations, settings):
    period = (date(2001, 10, 1), date(2026, 9, 30))
    cands = select_candidates(39.60, -106.35, 8100.0, stations, settings.snotel, period)
    # Low station sits 2100 ft below the resort, outside the 1500 ft band.
    assert [c.station.triplet for c in cands] == ["9001:CO:SNTL"]
    assert cands[0].elevation_diff_ft == pytest.approx(2200.0)
    assert cands[0].mode == "distance_and_elevation"
    assert select_candidates(44.0, -100.0, None, stations, settings.snotel, period) == []


def test_parse_station_daily_drops_negative_readings():
    series = parse_station_daily(load_fixture("awdb_station_data.json"), "9001:CO:SNTL")
    assert series[date(2024, 1, 1)] == (10.0, 50.0)
    assert series[date(2024, 1, 9)] == (None, 75.0)
    assert len(series) == 10


def test_storm_density_and_qc(settings):
    series = parse_station_daily(load_fixture("awdb_station_data.json"), "9001:CO:SNTL")
    storms = detect_snotel_storms(series, settings.snotel)
    assert [(s.start, s.end, s.qc) for s in storms] == [
        (date(2024, 1, 2), date(2024, 1, 3), None),
        (date(2024, 1, 5), date(2024, 1, 5), "small_depth_gain"),
        (date(2024, 1, 7), date(2024, 1, 8), "negative_swe_jump"),
    ]
    first = storms[0]
    assert first.depth_gain_in == 10.0
    assert first.swe_gain_in == pytest.approx(0.8)
    assert first.density_pct == pytest.approx(8.0)
    assert first.month == 1


def _series(rows):
    return {date(2024, 2, i + 1): v for i, v in enumerate(rows)}


def test_qc_density_bounds_and_spikes(settings):
    s = settings.snotel
    # 10 in of depth with 5 in of SWE: 50 percent, outside 2 to 40
    st = detect_snotel_storms(_series([(10.0, 40.0), (15.0, 50.0), (15.0, 49.0)]), s)
    assert [x.qc for x in st] == ["density_out_of_range"]
    # 10 in of depth with 0.1 in of SWE: 1 percent
    st = detect_snotel_storms(_series([(10.0, 40.0), (10.1, 50.0), (10.1, 49.0)]), s)
    assert [x.qc for x in st] == ["density_out_of_range"]
    # depth jump of 60 in in a day is a sensor spike
    st = detect_snotel_storms(_series([(10.0, 40.0), (13.0, 100.0), (13.0, 99.0)]), s)
    assert [x.qc for x in st] == ["depth_spike"]
    # depth gain with SWE flat
    st = detect_snotel_storms(_series([(10.0, 40.0), (10.0, 46.0), (10.0, 45.0)]), s)
    assert [x.qc for x in st] == ["no_swe_gain"]
    # boundaries inclusive: exactly 2 and 40 percent pass
    st = detect_snotel_storms(_series([(10.0, 40.0), (10.2, 50.0), (10.2, 49.0), (14.2, 59.0)]), s)
    assert [x.qc for x in st] == [None, None]
    assert [x.density_pct for x in st] == [pytest.approx(2.0), pytest.approx(40.0)]


def test_missing_day_breaks_storm(settings):
    series = {
        date(2024, 2, 1): (10.0, 40.0),
        date(2024, 2, 2): (10.5, 46.0),
        date(2024, 2, 4): (11.0, 52.0),  # Feb 3 missing: no delta for Feb 4
        date(2024, 2, 5): (11.5, 58.0),
    }
    st = detect_snotel_storms(series, settings.snotel)
    assert [(x.start, x.end) for x in st] == [(date(2024, 2, 2), date(2024, 2, 2)), (date(2024, 2, 5), date(2024, 2, 5))]


def test_evaluate_station_counts(settings, stations):
    ev = evaluate_station(stations[0], load_fixture("awdb_station_data.json"), settings.snotel)
    assert ev.qc_counts == {"ok": 1, "small_depth_gain": 1, "negative_swe_jump": 1}
    assert len(ev.valid) == 1


def test_fetch_station_daily_caches(tmp_path, fake_api):
    start, end = date(2022, 10, 1), date(2024, 9, 30)
    a = snotel.fetch_station_daily(tmp_path, "9001:CO:SNTL", start, end)
    b = snotel.fetch_station_daily(tmp_path, "9001:CO:SNTL", start, end)
    assert a == b
    assert fake_api.count(snotel.DATA_URL) == 1
    _, params = fake_api.calls[0]
    assert params["elements"] == "WTEQ,SNWD" and params["duration"] == "DAILY"
    assert params["beginDate"] == "2022-10-01" and params["endDate"] == "2024-09-30"
    assert snotel.load_station_daily(tmp_path, "9001:CO:SNTL", date(2020, 10, 1), end) is None


def test_source_entry(stations):
    e = snotel.source_entry(stations[0], date(2026, 10, 11))
    assert e["id"] == "nrcs-snotel-9001-co"
    assert e["url"].endswith("sitenum=9001")
    assert e["accessed_date"] == "2026-10-11"


def test_summit_caps_band_top(stations, settings):
    period = (date(2001, 10, 1), date(2026, 9, 30))
    # Summit below the 10300 ft station excludes it even though base + 3500 would allow it.
    assert select_candidates(39.60, -106.35, 8100.0, stations, settings.snotel, period, summit_ft=10000.0) == []
    # Big vertical: summit above base + 3500 widens the band to the summit.
    cands = select_candidates(39.60, -106.35, 6500.0, stations, settings.snotel, period, summit_ft=10500.0)
    assert [c.station.triplet for c in cands] == ["9005:CO:SNTL", "9001:CO:SNTL"]
    assert cands[1].elevation_diff_ft == pytest.approx(3800.0)
    # Summit unknown: base + elevation_band_above_ft.
    assert [c.station.triplet for c in select_candidates(39.60, -106.35, 6500.0, stations, settings.snotel, period)] == ["9005:CO:SNTL"]
