from dataclasses import replace
from datetime import date, timedelta

import pytest

from pipeline.climate.aggregate import DailyRecord
from pipeline.climate.density_estimate import estimate_density_pct
from pipeline.climate.snotel_snowfall import derive_new_snow, era5_snow_allowed, station_temp_c, with_snowfall
from pipeline.climate.units import c_to_f

D0 = date(2023, 1, 1)


def series(rows, start=D0):
    """rows of (swe, depth) from start, one per day."""
    return {start + timedelta(days=i): r for i, r in enumerate(rows)}


def era5(temp_c, start=D0, n=10):
    t = c_to_f(temp_c)
    return {start + timedelta(days=i): DailyRecord(start + timedelta(days=i), 0.0, t, t) for i in range(n)}


def derive(rows, s, temp_c=-8.0, month_density=None, **kw):
    return derive_new_snow(
        series(rows), era5(temp_c, n=len(rows)), kw.get("era5_m"), kw.get("station_ft"),
        month_density or {}, s, 2022, 2023, 0.0,
    ).new_snow_in


def test_station_month_density(settings):
    out = derive([(10.0, 50.0), (10.8, 58.0)], settings.snotel, month_density={1: 8.0})
    assert out[D0 + timedelta(days=1)] == pytest.approx(10.0)  # 0.8 in / 8 percent
    assert out[D0] is None  # no previous day


def test_estimated_density_when_month_has_no_station_value(settings):
    out = derive([(10.0, 50.0), (10.8, 52.0)], settings.snotel, temp_c=-8.0)
    expected = 0.8 / (estimate_density_pct(-8.0) / 100)
    assert out[D0 + timedelta(days=1)] == pytest.approx(expected, abs=1e-3)


def test_depth_gain_is_a_floor(settings):
    # 0.4 in SWE at 12 percent is 3.3 in, but depth rose 6 in
    out = derive([(10.0, 50.0), (10.4, 56.0)], settings.snotel, month_density={1: 12.0})
    assert out[D0 + timedelta(days=1)] == 6.0


def test_rain_on_snow_and_warm_gains_are_not_snow(settings):
    # SWE up while depth drops: rain on snow
    assert derive([(10.0, 50.0), (10.8, 47.0)], settings.snotel, temp_c=-8.0)[D0 + timedelta(days=1)] == 0.0
    # SWE up, depth flat, warm: rain
    assert derive([(10.0, 50.0), (10.5, 50.0)], settings.snotel, temp_c=3.0)[D0 + timedelta(days=1)] == 0.0
    # SWE up, depth flat, cold: snow that settled
    cold = derive([(10.0, 50.0), (10.5, 50.0)], settings.snotel, temp_c=-5.0, month_density={1: 10.0})
    assert cold[D0 + timedelta(days=1)] == pytest.approx(5.0)


def test_depth_only_day_and_small_swe_gain(settings):
    # 2 in depth with no measurable SWE gain counts as the depth gain
    assert derive([(10.0, 50.0), (10.0, 52.0)], settings.snotel)[D0 + timedelta(days=1)] == 2.0
    # 0.05 in SWE is under pillow precision: no snow
    assert derive([(10.0, 50.0), (10.05, 50.0)], settings.snotel)[D0 + timedelta(days=1)] == 0.0


def test_spikes_cap_and_missing(settings):
    s = settings.snotel
    assert derive([(10.0, 50.0), (17.0, 60.0)], s)[D0 + timedelta(days=1)] is None  # SWE spike over 6 in
    assert derive([(10.0, 50.0), (11.0, 95.0)], s)[D0 + timedelta(days=1)] is None  # depth spike over 40 in
    capped = derive([(10.0, 50.0), (15.0, 85.0)], s, month_density={1: 5.0})  # 100 in, capped at 60
    assert capped[D0 + timedelta(days=1)] == 60.0
    assert derive([(10.0, 50.0), (None, 52.0)], s)[D0 + timedelta(days=1)] is None
    # missing depth: temperature decides
    assert derive([(10.0, None), (10.5, None)], s, temp_c=-5.0, month_density={1: 10.0})[D0 + timedelta(days=1)] == 5.0


def test_lapse_rate_moves_era5_temperature_to_station():
    rec = DailyRecord(D0, 0.0, c_to_f(2.0), c_to_f(2.0))
    # station 1000 m above the ERA5 downscaling elevation: 6.5 C colder
    assert station_temp_c(rec, 2000.0, 3000 / 0.3048, 6.5) == pytest.approx(-4.5)
    assert station_temp_c(rec, None, 10000.0, 6.5) == pytest.approx(2.0)
    assert station_temp_c(None, 2000.0, 9000.0, 6.5) is None


def test_lapse_rate_turns_base_rain_into_station_snow(settings):
    rows = [(10.0, 50.0), (10.5, 50.0)]
    warm_base = derive_new_snow(series(rows), era5(2.0, n=2), 2000.0, None, {1: 10.0}, settings.snotel, 2022, 2023, 0.0).new_snow_in
    assert warm_base[D0 + timedelta(days=1)] == 0.0
    high_station = derive_new_snow(series(rows), era5(2.0, n=2), 2000.0, 3000 / 0.3048, {1: 10.0}, settings.snotel, 2022, 2023, 0.0).new_snow_in
    assert high_station[D0 + timedelta(days=1)] == pytest.approx(5.0)


def test_season_totals_and_depth_comparison(settings):
    start = date(2022, 10, 1)
    rows, swe, depth = [], 0.0, 0.0
    d = start
    while d <= date(2023, 9, 30):
        if d.month in (12, 1) and d.day % 5 == 0:
            swe += 1.0
            depth += 8.0  # 12.5 percent apparent density after settlement
        rows.append((round(swe, 1), depth))
        d += timedelta(days=1)
    ser = series(rows, start)
    e5 = {start + timedelta(days=i): DailyRecord(start + timedelta(days=i), 0.0, c_to_f(-8), c_to_f(-8)) for i in range(len(rows))}
    out = derive_new_snow(ser, e5, None, None, {12: 8.0, 1: 8.0}, settings.snotel, 2022, 2023, 0.9)
    assert out.seasons_with_data == 1
    assert out.depth_gain_annual_in == pytest.approx(12 * 8.0)
    assert out.swe_annual_in == pytest.approx(12 * 12.5)  # 1 in / 8 percent, above the depth gain
    assert out.density_from == {"station_month": 12, "estimated": 0}


def test_with_snowfall_replaces_only_snowfall():
    recs = [DailyRecord(D0, 1.0, 30, 10, 5, 1, 2), DailyRecord(D0 + timedelta(days=1), 2.0, 31, 11, 6, 1, 2)]
    out = with_snowfall(recs, {D0: 9.0})
    assert out[0] == replace(recs[0], snowfall_in=9.0)
    assert out[1].snowfall_in is None and out[1].tmax_f == 31


@pytest.mark.parametrize(
    "base,summit,vertical,grid,ok,frag",
    [
        (350, 700, None, 846, True, "low relief"),
        (1260, 1500, 252, None, True, "low relief"),
        (8530, 11068, 2538, 6453, False, "2,077 ft below"),
        (10780, 13050, 2270, 10436, True, "within 1,000 ft"),
        (622, 2284, 1750, 1700, False, "1,078 ft above"),
        (622, 2284, 1750, 1500, True, "within"),
        (3900, 5100, None, None, True, "low relief"),     # vertical 1200 from summit minus base
        (4100, 5000, 900, None, False, "grid cell height is unknown"),  # base too high for low relief
        (None, None, 300, 500, False, "base elevation is unknown"),
    ],
)
def test_era5_snow_allowed(base, summit, vertical, grid, ok, frag):
    allowed, reason = era5_snow_allowed(base, summit, vertical, grid, 1000, 1500, 4000)
    assert allowed is ok
    assert frag in reason
