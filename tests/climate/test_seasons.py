from datetime import date

import pytest

from pipeline.climate.seasons import (
    SEASON_MONTH_ORDER,
    calendar_year,
    default_period,
    period_dates,
    season_chunks,
    season_of,
    season_years,
)


def test_season_order():
    assert SEASON_MONTH_ORDER == (10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9)


def test_season_of_and_calendar_year():
    assert season_of(date(2024, 10, 1)) == 2024
    assert season_of(date(2025, 9, 30)) == 2024
    assert calendar_year(2024, 12) == 2024
    assert calendar_year(2024, 1) == 2025


def test_default_period_last_25_full_seasons():
    # 2025-26 season ended Sep 30 2026; ERA5 lag of 7 days has passed by Oct 11.
    assert default_period(date(2026, 10, 11), 25, 7) == (2001, 2026)
    # Before the lag passes the newest season is not complete yet.
    assert default_period(date(2026, 10, 3), 25, 7) == (2000, 2025)
    assert default_period(date(2027, 3, 1), 25, 7) == (2001, 2026)


def test_period_dates_and_years():
    assert period_dates(2001, 2026) == (date(2001, 10, 1), date(2026, 9, 30))
    assert len(season_years(2001, 2026)) == 25
    with pytest.raises(ValueError):
        period_dates(2026, 2026)


def test_season_chunks():
    chunks = list(season_chunks(2001, 2026, 5))
    assert chunks[0] == (date(2001, 10, 1), date(2006, 9, 30))
    assert chunks[-1] == (date(2021, 10, 1), date(2026, 9, 30))
    assert len(chunks) == 5
    assert list(season_chunks(2020, 2023, 2)) == [
        (date(2020, 10, 1), date(2022, 9, 30)),
        (date(2022, 10, 1), date(2023, 9, 30)),
    ]
