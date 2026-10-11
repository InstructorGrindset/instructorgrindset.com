from datetime import date, timedelta

import pytest

from pipeline.climate.aggregate import DailyRecord, aggregate_months, detect_storms
from pipeline.schemas.climate import SEASON_MONTH_ORDER


def series(start: date, snow: list[float | None], **const):
    return [DailyRecord(date=start + timedelta(days=i), snowfall_in=s, **const) for i, s in enumerate(snow)]


def full_period(start_year, end_year, snow_fn, tmax=30.0, tmin=10.0, wind=12.0, sun=18000.0, light=36000.0):
    out = []
    d = date(start_year, 10, 1)
    while d <= date(end_year, 9, 30):
        out.append(DailyRecord(d, snow_fn(d), tmax, tmin, wind, sun, light))
        d += timedelta(days=1)
    return out


def agg(daily, start_year=2022, end_year=2024, **over):
    kw = dict(powder_day_in=6.0, banner_day_in=12.0, storm_min_daily_in=1.0, min_month_coverage=0.9)
    kw.update(over)
    return {m.month: m for m in aggregate_months(daily, start_year, end_year, **kw)}


def test_storm_detection_runs_and_depth():
    daily = series(date(2024, 1, 1), [0, 2.0, 3.5, 1.0, 0.5, 6.0, 0, 0.99, 1.0])
    storms = detect_storms(daily, 1.0)
    assert [(s.start, s.end, s.depth_in) for s in storms] == [
        (date(2024, 1, 2), date(2024, 1, 4), 6.5),
        (date(2024, 1, 6), date(2024, 1, 6), 6.0),
        (date(2024, 1, 9), date(2024, 1, 9), 1.0),  # exactly the minimum counts
    ]


def test_storm_broken_by_missing_day_and_gap():
    daily = series(date(2024, 1, 1), [2.0, None, 2.0])
    assert len(detect_storms(daily, 1.0)) == 2
    gap = [DailyRecord(date(2024, 1, 1), 2.0), DailyRecord(date(2024, 1, 3), 2.0)]
    assert len(detect_storms(gap, 1.0)) == 2


def test_storm_month_is_start_month_across_boundary():
    daily = series(date(2024, 1, 30), [3.0, 4.0, 5.0, 2.0])  # Jan 30 to Feb 2
    storms = detect_storms(daily, 1.0)
    assert len(storms) == 1
    assert storms[0].month == 1
    assert storms[0].depth_in == 14.0
    by_month = agg(full_period(2022, 2024, lambda d: {date(2023, 1, 30): 3.0, date(2023, 1, 31): 4.0,
                                                      date(2023, 2, 1): 5.0, date(2023, 2, 2): 2.0}.get(d, 0.0)))
    assert by_month[1].avg_storm_depth_in == 14.0
    assert by_month[1].storm_count == 1
    assert by_month[2].storm_count == 0 and by_month[2].avg_storm_depth_in is None
    # Snowfall itself stays in the calendar month it fell in: 7 in Jan, 7 in Feb, over 2 seasons.
    assert by_month[1].snowfall_in == pytest.approx(3.5)
    assert by_month[2].snowfall_in == pytest.approx(3.5)


def test_powder_and_banner_thresholds_inclusive():
    special = {
        date(2023, 1, 5): 6.0,      # powder exactly at threshold
        date(2023, 1, 7): 5.999,    # just under
        date(2023, 1, 9): 12.0,     # banner exactly at threshold (also powder)
        date(2023, 1, 11): 11.99,   # powder only
    }
    by_month = agg(full_period(2022, 2024, lambda d: special.get(d, 0.0)))
    jan = by_month[1]
    assert jan.powder_days == pytest.approx(3 / 2)  # 3 days in one of two seasons
    assert jan.banner_days == pytest.approx(1 / 2)
    assert jan.seasons_used == 2


def test_powder_threshold_from_converted_cm():
    from pipeline.climate.units import cm_to_in

    by_month = agg(full_period(2022, 2024, lambda d: cm_to_in(15.24) if d == date(2023, 3, 3) else 0.0))
    assert by_month[3].powder_days == pytest.approx(0.5)


def test_aggregation_twelve_months_in_season_order():
    daily = full_period(2022, 2024, lambda d: 10.0 if (d.month == 12 and d.day == 25) else 0.0)
    out = aggregate_months(daily, 2022, 2024, powder_day_in=6, banner_day_in=12, storm_min_daily_in=1, min_month_coverage=0.9)
    assert [m.month for m in out] == list(SEASON_MONTH_ORDER)
    dec = out[2]
    assert dec.month == 12
    assert dec.snowfall_in == pytest.approx(10.0)
    assert dec.powder_days == pytest.approx(1.0)
    assert dec.avg_storm_depth_in == pytest.approx(10.0)
    jul = out[9]
    assert jul.month == 7 and jul.snowfall_in == 0.0 and jul.powder_days == 0.0 and jul.avg_storm_depth_in is None
    assert all(m.temp_high_f == pytest.approx(30.0) and m.temp_low_f == pytest.approx(10.0) for m in out)
    assert all(m.wind_mph == pytest.approx(12.0) for m in out)
    assert all(m.sun_pct == pytest.approx(50.0) for m in out)


def test_monthly_mean_over_seasons_and_avg_storm_depth_pooled():
    snow = {date(2022, 12, 1): 4.0, date(2023, 12, 1): 10.0, date(2023, 12, 2): 2.0, date(2023, 12, 20): 3.0}
    by_month = agg(full_period(2022, 2024, lambda d: snow.get(d, 0.0)))
    assert by_month[12].snowfall_in == pytest.approx((4 + 15) / 2)
    # Storms: 4, 12, 3 -> pooled mean 19/3
    assert by_month[12].avg_storm_depth_in == pytest.approx(19 / 3)
    assert by_month[12].storm_count == 3


def test_incomplete_month_is_excluded():
    def snow(d):
        if d.year == 2023 and d.month == 1 and d.day > 20:
            return None  # 11 of 31 days missing in the 2022 season
        return 1.0 if d.month == 1 else 0.0

    by_month = agg(full_period(2022, 2024, snow))
    assert by_month[1].seasons_used == 1
    assert by_month[1].snowfall_in == pytest.approx(31.0)


def test_sun_pct_is_ratio_of_totals():
    daily = [
        DailyRecord(date(2023, 1, 1), 0.0, 20, 10, 5, 0.0, 30000.0),
        DailyRecord(date(2023, 1, 2), 0.0, 20, 10, 5, 30000.0, 30000.0),
    ]
    by_month = agg(daily, min_month_coverage=0.0)
    assert by_month[1].sun_pct == pytest.approx(50.0)
    assert by_month[2].sun_pct is None
