"""ERA5 surface fetcher, pure parts only (pipeline/climate/era5_surface.py). Synthetic arrays, no network."""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.climate import era5_surface as es


def test_season_days_pooled_runs_december_to_march():
    days = es.season_days(2015)
    assert days[0] == dt.date(2015, 12, 1) and days[-1] == dt.date(2016, 3, 31)
    assert len(days) == 31 + 31 + 29 + 31          # 2016 is a leap year


def test_season_days_monthly_runs_november_to_may():
    days = es.season_days(2016, monthly=True)
    assert days[0] == dt.date(2016, 11, 1) and days[-1] == dt.date(2017, 5, 31)
    assert len(days) == 30 + 31 + 31 + 28 + 31 + 30 + 31
    assert len(set(days)) == len(days)
    assert {d.month for d in days} == set(es.MONTHS)
    assert [m for i, m in enumerate(d.month for d in days) if i == 0 or days[i - 1].month != m] == list(es.MONTHS)


def test_monthly_speed_bins_are_thirteen():
    assert len(es.MONTHLY_SPEED_EDGES_MS) == 13 and es.MONTHLY_SPEED_EDGES_MS[-1] == 12


def test_speed_bin_one_metre_steps_with_open_last_bin():
    np = pytest.importorskip("numpy")
    s = np.array([[0.0, 0.99, 1.0], [11.99, 12.0, 40.0]])
    assert es.speed_bin(s, 13).tolist() == [[0, 0, 1], [11, 12, 12]]


def test_direction_sector_names_where_the_wind_comes_from():
    np = pytest.importorskip("numpy")
    # u is eastward, v is northward. A wind from the north blows south: v < 0.
    u = np.array([0.0, -5.0, -5.0, 0.0, 5.0, 5.0, 5.0, -5.0])
    v = np.array([-5.0, -5.0, 0.0, 5.0, 5.0, 0.0, -5.0, 5.0])
    #            N     NE    E    S    SW   W    NW    SE
    assert es.direction_sector(u, v).tolist() == [0, 1, 2, 4, 5, 6, 7, 3]
    # Sectors are centered on the compass point: 20 degrees is still north, 25 degrees is northeast.
    for deg, want in ((20, 0), (25, 1), (340, 0), (335, 7)):
        rad = np.radians(deg)
        assert int(es.direction_sector(np.array([-np.sin(rad)]), np.array([-np.cos(rad)]))[0]) == want


def test_depth_bin_uses_left_closed_edges():
    np = pytest.importorskip("numpy")
    snow = np.array([0.0, 0.004, 0.005, 0.02, 0.0999, 0.1, 0.3, 5.0])
    assert es.depth_bin(snow, es.SNOW_EDGES_M).tolist() == [0, 0, 1, 2, 3, 4, 5, 5]
    ice = np.array([0.0, 0.1, 0.25, 0.3, 0.5, 2.0])
    assert es.depth_bin(ice, es.ICE_EDGES_M).tolist() == [0, 1, 2, 3, 4, 4]


def test_tally_counts_one_per_cell_and_honors_the_mask():
    np = pytest.importorskip("numpy")
    bins = np.array([[0, 2], [1, 2]])
    out = es.tally(bins, 3)
    assert out.shape == (2, 2, 3) and out.sum() == 4
    assert out[0, 1].tolist() == [0, 0, 1] and out[1, 0].tolist() == [0, 1, 0]
    masked = es.tally(bins, 3, np.array([[True, False], [False, True]]))
    assert masked.sum() == 2 and masked[0, 1].sum() == 0 and masked[1, 1].tolist() == [0, 0, 1]


def test_wind_stats_of_a_small_synthetic_day():
    np = pytest.importorskip("numpy")
    # Two cells, three hours. Cell 0: calm, then 4 m/s from the west, then 13 m/s from the north.
    # Cell 1: 2 m/s (below the direction floor) every hour.
    u = [np.array([[0.5, 2.0]]), np.array([[4.0, 0.0]]), np.array([[0.0, 0.0]])]
    v = [np.array([[0.0, 0.0]]), np.array([[0.0, 2.0]]), np.array([[-13.0, -2.0]])]
    g = [np.array([[1.0, 3.0]]), np.array([[6.0, 3.0]]), np.array([[np.nan, 3.0]])]
    out = es.wind_stats(u, v, g, 13)
    assert out["speed"].shape == (1, 2, 13)
    assert out["speed"][0, 0].tolist() == [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1]
    assert out["speed"][0, 1].tolist() == [0, 0, 3] + [0] * 10
    assert out["dir"][0, 0].tolist() == [1, 0, 0, 0, 0, 0, 1, 0]        # one hour from N, one from W
    assert out["dir"][0, 1].sum() == 0
    # Gust ratio counts windy hours with a gust value: only the 4 m/s hour, 6 / 4.
    assert out["gust_n"].tolist() == [[1, 0]]
    assert out["gust_sum"][0, 0] == pytest.approx(1.5)


def test_keep_cell_snow_rule():
    # Histogram bins follow SNOW_EDGES_M: [0, 0.005, 0.02, 0.05, 0.1, 0.3]. 900 December to February days.
    no_ice = [900, 0, 0, 0, 0]
    need = round(es.KEEP_SHARE * 900)
    assert es.keep_cell([900 - need, 0, need, 0, 0, 0], no_ice, 0.0)              # exactly the share at 0.02 m or more
    assert es.keep_cell([0, 0, 0, 0, 0, 900], no_ice, 0.0)
    assert not es.keep_cell([901 - need, 0, need - 1, 0, 0, 0], no_ice, 0.0)      # one day short
    assert not es.keep_cell([0, 900, 0, 0, 0, 0], no_ice, 0.0)                    # thin snow every day stays out
    assert not es.keep_cell([0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0], 100.0)           # no days sampled


def test_keep_cell_ice_rule_needs_a_real_lake():
    bare = [900, 0, 0, 0, 0, 0]
    need = round(es.KEEP_SHARE * 900)
    icy = [900 - need, need - 1, 1, 0, 0]                                         # the share at 0.1 m or more
    assert es.keep_cell(bare, icy, es.KEEP_LAKE_COVER_PCT)
    assert es.keep_cell(bare, icy, 60.0)
    assert not es.keep_cell(bare, icy, es.KEEP_LAKE_COVER_PCT - 0.1)              # ice on a lake that covers too little
    assert not es.keep_cell(bare, icy, 0.0)
    assert not es.keep_cell(bare, [901 - need, need - 1, 0, 0, 0], 60.0)          # too few ice days


def test_keep_cell_month_share_decides_which_months_keep_wind():
    # A 300 day month: 30 snowy days pass the month share of 10 percent, 29 do not.
    no_ice = [300, 0, 0, 0, 0]
    assert es.KEEP_MONTH_SHARE == 0.10
    assert es.keep_cell([270, 0, 30, 0, 0, 0], no_ice, 0.0, es.KEEP_MONTH_SHARE)
    assert not es.keep_cell([271, 0, 29, 0, 0, 0], no_ice, 0.0, es.KEEP_MONTH_SHARE)
    assert es.keep_cell([300, 0, 0, 0, 0, 0], [0, 0, 0, 0, 300], 50.0, es.KEEP_MONTH_SHARE)


def test_keep_constants_sit_on_histogram_edges():
    assert es.KEEP_SNOW_M_WE in es.SNOW_EDGES_M and es.KEEP_ICE_M in es.ICE_EDGES_M
    assert set(es.KEEP_MONTHS) <= set(es.MONTHS)
