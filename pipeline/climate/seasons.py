"""Ski season calendar helpers.

A season runs October 1 through September 30 and is named by the year of its
October. A period (start_year, end_year) covers October 1 of ``start_year``
through September 30 of ``end_year``, which is ``end_year - start_year``
seasons.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

from pipeline.schemas.climate import SEASON_MONTH_ORDER

__all__ = [
    "SEASON_MONTH_ORDER",
    "season_of",
    "period_dates",
    "season_years",
    "default_period",
    "season_chunks",
    "calendar_year",
    "days_in_month",
]


def season_of(d: date) -> int:
    """Season (October year) that contains ``d``."""
    return d.year if d.month >= 10 else d.year - 1


def calendar_year(season: int, month: int) -> int:
    return season if month >= 10 else season + 1


def days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days


def period_dates(start_year: int, end_year: int) -> tuple[date, date]:
    if end_year <= start_year:
        raise ValueError("end_year must be after start_year")
    return date(start_year, 10, 1), date(end_year, 9, 30)


def season_years(start_year: int, end_year: int) -> list[int]:
    return list(range(start_year, end_year))


def default_period(today: date, n_seasons: int, era5_lag_days: int) -> tuple[int, int]:
    """Last ``n_seasons`` full seasons whose ERA5 data should be published by ``today``."""
    end_year = today.year
    if today < date(today.year, 9, 30) + timedelta(days=era5_lag_days):
        end_year -= 1
    return end_year - n_seasons, end_year


def season_chunks(start_year: int, end_year: int, seasons_per_chunk: int) -> Iterator[tuple[date, date]]:
    """Split a period into request windows of whole seasons."""
    if seasons_per_chunk < 1:
        raise ValueError("seasons_per_chunk must be at least 1")
    s = start_year
    while s < end_year:
        e = min(s + seasons_per_chunk, end_year)
        yield date(s, 10, 1), date(e, 9, 30)
        s = e
