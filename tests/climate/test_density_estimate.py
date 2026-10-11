from datetime import date

import pytest

from pipeline.climate.aggregate import DailyRecord, Storm
from pipeline.climate.density_estimate import (
    anderson_1976_kgm3,
    density_pct,
    estimate_density_pct,
    estimate_storm_densities,
    hedstrom_pomeroy_1998_kgm3,
    storm_temperature_c,
)


def test_anderson_1976_reference_values():
    # 0.05 g/cm3 at and below -15 C
    assert anderson_1976_kgm3(-15.0) == pytest.approx(50.0)
    assert anderson_1976_kgm3(-25.0) == pytest.approx(50.0)
    # "about 0.15" at 0 C per the SNOW-17 documentation: 0.05 + 0.0017 * 15^1.5
    assert anderson_1976_kgm3(0.0) == pytest.approx(148.76, abs=0.01)
    assert anderson_1976_kgm3(-10.0) == pytest.approx(50 + 1.7 * 5**1.5)
    # capped at +2 C
    assert anderson_1976_kgm3(2.0) == pytest.approx(169.15, abs=0.01)
    assert anderson_1976_kgm3(8.0) == pytest.approx(anderson_1976_kgm3(2.0))
    # monotonic over the snowfall range
    temps = [-20, -15, -12, -9, -6, -3, 0, 1, 2]
    vals = [anderson_1976_kgm3(t) for t in temps]
    assert vals == sorted(vals)


def test_hedstrom_pomeroy_1998_reference_values():
    assert hedstrom_pomeroy_1998_kgm3(0.0) == pytest.approx(119.17, abs=0.01)
    assert hedstrom_pomeroy_1998_kgm3(-10.0) == pytest.approx(69.0, abs=0.05)
    assert hedstrom_pomeroy_1998_kgm3(3.0) == pytest.approx(119.17, abs=0.01)  # capped at 0 C


def test_density_pct_and_classes():
    assert density_pct(100.0) == 10.0
    assert estimate_density_pct(-15.0) == pytest.approx(5.0)
    assert estimate_density_pct(0.0) == pytest.approx(14.876, abs=1e-3)
    assert estimate_density_pct(-12.0, "hedstrom_pomeroy_1998") < 7.0


def test_storm_temperature_is_snowfall_weighted():
    days = (
        DailyRecord(date(2024, 1, 1), snowfall_in=3.0, tmax_f=32.0, tmin_f=32.0),   # 0 C
        DailyRecord(date(2024, 1, 2), snowfall_in=1.0, tmax_f=14.0, tmin_f=14.0),   # -10 C
    )
    storm = Storm(date(2024, 1, 1), date(2024, 1, 2), 4.0, days)
    assert storm_temperature_c(storm) == pytest.approx(-2.5)
    out = estimate_storm_densities([storm], "anderson_1976")
    assert out == [(1, pytest.approx(estimate_density_pct(-2.5)))]


def test_storm_without_temperature_is_skipped():
    days = (DailyRecord(date(2024, 1, 1), snowfall_in=3.0),)
    assert estimate_storm_densities([Storm(date(2024, 1, 1), date(2024, 1, 1), 3.0, days)], "anderson_1976") == []
