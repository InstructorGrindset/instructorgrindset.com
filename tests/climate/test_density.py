import pytest

from pipeline.climate.density import MonthDensity, classify, monthly_density, pick_month_density


@pytest.mark.parametrize(
    "pct,expected",
    [(2.0, "dry"), (6.99, "dry"), (7.0, "average"), (9.5, "average"), (12.0, "average"), (12.01, "moist"), (30.0, "moist")],
)
def test_classify_boundaries(pct, expected):
    assert classify(pct, 7, 12) == expected


def test_monthly_density_median_and_mix():
    storms = [(1, 5.0), (1, 8.0), (1, 9.0), (1, 15.0), (2, 10.0), (12, 6.0), (12, 6.5), (12, 13.0)]
    out = monthly_density(storms, 7, 12, min_storms=3)
    assert set(out) == {1, 12}  # February has one storm, below the minimum
    jan = out[1]
    assert jan.n_storms == 4
    assert jan.median_pct == pytest.approx(8.5)
    assert jan.mix == {"dry": 0.25, "average": 0.5, "moist": 0.25}
    dec = out[12]
    assert dec.mix == {"dry": 0.667, "average": 0.0, "moist": 0.333}
    assert sum(dec.mix.values()) == pytest.approx(1.0, abs=1e-9)


def test_mix_rounding_always_sums_to_one():
    storms = [(1, 5.0)] * 1 + [(1, 9.0)] * 1 + [(1, 14.0)] * 1
    mix = monthly_density(storms, 7, 12, 1)[1].mix
    assert round(sum(mix.values()), 9) == 1.0
    storms = [(1, 5.0)] * 2 + [(1, 9.0)] * 2 + [(1, 14.0)] * 3
    mix = monthly_density(storms, 7, 12, 1)[1].mix
    assert round(sum(mix.values()), 9) == 1.0


def test_pick_prefers_snotel_then_estimate():
    s = {1: MonthDensity(1, 5, 8.0, {"dry": 0, "average": 1, "moist": 0})}
    e = {1: MonthDensity(1, 9, 11.0, {"dry": 0, "average": 1, "moist": 0}),
         4: MonthDensity(4, 9, 13.0, {"dry": 0, "average": 0, "moist": 1})}
    assert pick_month_density(1, s, e) == (s[1], "snotel")
    assert pick_month_density(4, s, e) == (e[4], "estimated")
    assert pick_month_density(7, s, e) == (None, None)
    assert pick_month_density(1, None, None) == (None, None)
