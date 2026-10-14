import pytest

from pipeline.climate.units import c_to_f, cm_to_in, f_to_c, kmh_to_mph, m_to_ft, mm_to_in, mps_to_mph


def test_cm_to_in():
    assert cm_to_in(2.54) == 1.0
    assert cm_to_in(15.24) == 6.0  # rounds clean so the powder threshold is exact
    assert cm_to_in(30.48) == 12.0
    assert cm_to_in(0.0) == 0.0
    assert cm_to_in(None) is None


def test_mm_to_in():
    assert mm_to_in(25.4) == 1.0
    assert mm_to_in(None) is None


def test_temperature():
    assert c_to_f(0) == 32
    assert c_to_f(100) == 212
    assert c_to_f(-40) == -40
    assert f_to_c(32) == 0
    assert f_to_c(c_to_f(-7.3)) == pytest.approx(-7.3)
    assert c_to_f(None) is None


def test_wind_and_length():
    assert kmh_to_mph(1.609344) == pytest.approx(1.0)
    assert kmh_to_mph(100) == pytest.approx(62.137, abs=1e-3)
    assert mps_to_mph(1.0) == pytest.approx(2.23694, abs=1e-5)
    assert m_to_ft(0.3048) == pytest.approx(1.0)
    assert m_to_ft(2475) == pytest.approx(8120.08, abs=0.01)
    assert kmh_to_mph(None) is None
