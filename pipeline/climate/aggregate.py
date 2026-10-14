"""Daily ERA5 series to 12 climatological months.

Definitions (thresholds come from config/definitions.yaml through
:class:`pipeline.climate.definitions.ClimateSettings`):

* Powder day: daily new snow >= ``powder_day_in`` (6.0 in counts).
* Banner day: daily new snow >= ``banner_day_in``.
* Storm: a run of consecutive days, each with new snow >= ``storm_min_daily_in``.
  A missing day ends the run. Storm depth is the sum of daily snowfall over the
  run. A storm belongs to the month of its first day.
* A season month counts toward the means only when at least
  ``min_month_coverage`` of its days have snowfall data.

Monthly outputs are means over the season months in the period:
``snowfall_in`` is mean monthly total, ``powder_days`` and ``banner_days`` are
mean days per month, ``avg_storm_depth_in`` is the mean depth of all storms
that started in that month across the period, temperatures and wind are means
of daily values, and ``sun_pct`` is total sunshine over total daylight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean
from typing import Iterable, Optional, Sequence

from pipeline.climate.seasons import SEASON_MONTH_ORDER, calendar_year, days_in_month, season_years

EPS = 1e-9


@dataclass(frozen=True)
class DailyRecord:
    date: date
    snowfall_in: Optional[float] = None
    tmax_f: Optional[float] = None
    tmin_f: Optional[float] = None
    wind_mph: Optional[float] = None
    sunshine_s: Optional[float] = None
    daylight_s: Optional[float] = None

    @property
    def tmean_f(self) -> Optional[float]:
        if self.tmax_f is None or self.tmin_f is None:
            return None
        return (self.tmax_f + self.tmin_f) / 2.0


@dataclass(frozen=True)
class Storm:
    start: date
    end: date
    depth_in: float
    days: tuple[DailyRecord, ...] = field(repr=False, default=())

    @property
    def month(self) -> int:
        return self.start.month


def is_at_least(value: Optional[float], threshold: float) -> bool:
    return value is not None and value >= threshold - EPS


def detect_storms(daily: Iterable[DailyRecord], min_daily_in: float) -> list[Storm]:
    """Runs of consecutive days with snowfall >= ``min_daily_in``."""
    storms: list[Storm] = []
    run: list[DailyRecord] = []

    def close() -> None:
        if run:
            storms.append(
                Storm(
                    start=run[0].date,
                    end=run[-1].date,
                    depth_in=round(sum(r.snowfall_in for r in run), 3),
                    days=tuple(run),
                )
            )
            run.clear()

    for rec in sorted(daily, key=lambda r: r.date):
        if is_at_least(rec.snowfall_in, min_daily_in):
            if run and rec.date - run[-1].date != timedelta(days=1):
                close()
            run.append(rec)
        else:
            close()
    close()
    return storms


@dataclass
class MonthStats:
    month: int
    seasons_used: int
    snowfall_in: Optional[float]
    powder_days: Optional[float]
    banner_days: Optional[float]
    avg_storm_depth_in: Optional[float]
    storm_count: int
    temp_high_f: Optional[float]
    temp_low_f: Optional[float]
    wind_mph: Optional[float]
    sun_pct: Optional[float]


def _mean(values: Sequence[float]) -> Optional[float]:
    return fmean(values) if values else None


def aggregate_months(
    daily: Sequence[DailyRecord],
    start_year: int,
    end_year: int,
    *,
    powder_day_in: float,
    banner_day_in: float,
    storm_min_daily_in: float,
    min_month_coverage: float,
) -> list[MonthStats]:
    """Climatological month statistics in season order (Oct..Sep)."""
    by_date = {r.date: r for r in daily}
    storms = detect_storms(
        [r for r in daily if date(start_year, 10, 1) <= r.date <= date(end_year, 9, 30)],
        storm_min_daily_in,
    )
    out: list[MonthStats] = []
    for month in SEASON_MONTH_ORDER:
        totals: list[float] = []
        powder: list[int] = []
        banner: list[int] = []
        highs: list[float] = []
        lows: list[float] = []
        winds: list[float] = []
        sun = 0.0
        light = 0.0
        for season in season_years(start_year, end_year):
            year = calendar_year(season, month)
            n_days = days_in_month(year, month)
            recs = [by_date.get(date(year, month, d)) for d in range(1, n_days + 1)]
            recs = [r for r in recs if r is not None]
            snow = [r.snowfall_in for r in recs if r.snowfall_in is not None]
            if len(snow) >= min_month_coverage * n_days - EPS and snow:
                totals.append(sum(snow))
                powder.append(sum(1 for s in snow if is_at_least(s, powder_day_in)))
                banner.append(sum(1 for s in snow if is_at_least(s, banner_day_in)))
            for r in recs:
                if r.tmax_f is not None:
                    highs.append(r.tmax_f)
                if r.tmin_f is not None:
                    lows.append(r.tmin_f)
                if r.wind_mph is not None:
                    winds.append(r.wind_mph)
                if r.sunshine_s is not None and r.daylight_s is not None:
                    sun += r.sunshine_s
                    light += r.daylight_s
        month_storms = [s for s in storms if s.month == month]
        out.append(
            MonthStats(
                month=month,
                seasons_used=len(totals),
                snowfall_in=_mean(totals),
                powder_days=_mean(powder),
                banner_days=_mean(banner),
                avg_storm_depth_in=_mean([s.depth_in for s in month_storms]),
                storm_count=len(month_storms),
                temp_high_f=_mean(highs),
                temp_low_f=_mean(lows),
                wind_mph=_mean(winds),
                sun_pct=(100.0 * min(sun, light) / light) if light > 0 else None,
            )
        )
    return out
