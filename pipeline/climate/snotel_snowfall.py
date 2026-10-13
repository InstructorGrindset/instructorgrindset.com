"""Daily new snow at a SNOTEL station from snow water equivalent gains.

ERA5 smooths mountain terrain (25 km grid), so its snowfall runs far below
what falls on the mountain. Where a usable SNOTEL station exists, daily new
snow comes from the station instead:

    new_snow_in = daily SWE gain (in) / new snow density (fraction)

This is the same water to depth step NWS SNOW-17 uses for new snowfall,
``HCN = PX / DHN`` (new snow depth = new water / new snow density), in
NOAA-OWP/snow17 src/snow19/SNEW.f, commit ea142f94a4788ddf0be7b4d7da59c1f9f552df45:
https://github.com/NOAA-OWP/snow17/blob/ea142f94a4788ddf0be7b4d7da59c1f9f552df45/src/snow19/SNEW.f

Data: NRCS SNOTEL daily WTEQ (snow water equivalent, inches, 0.1 in
precision) and SNWD (snow depth, inches) from the AWDB REST API.

Rules, all thresholds from config/definitions.yaml ``climate.snotel``:

* Density: the station's own median storm density for that calendar month
  (see :mod:`pipeline.climate.snotel`) when the month has at least
  ``min_storms_per_month`` QC passed storms, else the Anderson (1976) estimate
  (:mod:`pipeline.climate.density_estimate`) from ERA5 daily mean air
  temperature moved to the station elevation with ``lapse_rate_c_per_km``.
* Snow day: SWE gain of at least ``swe_min_daily_gain_in``, depth did not drop
  (a SWE gain with falling depth is rain on snow), and either the depth rose by
  at least ``storm_min_daily_depth_gain_in`` or the station air temperature is
  at or below ``rain_snow_threshold_c``. When SNWD is missing only the
  temperature test applies.
* Depth gain is a lower bound on new snow (the pack settles while it snows), so
  derived new snow is never below the day's depth gain. A depth gain of at
  least ``storm_min_daily_depth_gain_in`` with no SWE gain counts as the depth
  gain itself (light snow below the pillow's 0.1 in precision).
* QC: a daily SWE gain above ``swe_max_daily_gain_in`` or a depth jump above
  ``max_daily_depth_gain_in`` is a sensor spike and that day becomes missing.
  Derived new snow is capped at ``max_daily_new_snow_in``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from pipeline.climate.aggregate import DailyRecord
from pipeline.climate.definitions import SnotelSettings
from pipeline.climate.density_estimate import estimate_density_pct
from pipeline.climate.seasons import period_dates, season_of
from pipeline.climate.units import f_to_c

SNOW_MONTHS = (11, 12, 1, 2, 3, 4)


@dataclass
class SweSnowfall:
    new_snow_in: dict[date, Optional[float]]
    qc_counts: dict[str, int] = field(default_factory=dict)
    density_from: dict[str, int] = field(default_factory=dict)  # station_month | estimated
    seasons_with_data: int = 0
    depth_gain_annual_in: Optional[float] = None  # mean seasonal sum of positive daily depth gains
    swe_annual_in: Optional[float] = None  # mean seasonal sum of derived new snow


def station_temp_c(
    rec: Optional[DailyRecord], era5_elevation_m: Optional[float], station_elevation_ft: Optional[float], lapse_c_per_km: float
) -> Optional[float]:
    """ERA5 daily mean temperature moved from the ERA5 downscaling elevation to the station."""
    if rec is None or rec.tmean_f is None:
        return None
    t = f_to_c(rec.tmean_f)
    if era5_elevation_m is not None and station_elevation_ft is not None:
        t -= lapse_c_per_km * (station_elevation_ft * 0.3048 - era5_elevation_m) / 1000.0
    return t


def derive_new_snow(
    series: dict[date, tuple[Optional[float], Optional[float]]],
    era5: dict[date, DailyRecord],
    era5_elevation_m: Optional[float],
    station_elevation_ft: Optional[float],
    month_density_pct: dict[int, float],
    s: SnotelSettings,
    start_year: int,
    end_year: int,
    min_month_coverage: float,
    method: str = "anderson_1976",
) -> SweSnowfall:
    start, end = period_dates(start_year, end_year)
    out: dict[date, Optional[float]] = {}
    qc: dict[str, int] = {}
    dens_from = {"station_month": 0, "estimated": 0}
    depth_gain: dict[date, float] = {}

    def bump(key: str) -> None:
        qc[key] = qc.get(key, 0) + 1

    d = start
    while d <= end:
        prev = series.get(d - timedelta(days=1))
        cur = series.get(d)
        d0 = d
        d += timedelta(days=1)
        if prev is None or cur is None or prev[0] is None or cur[0] is None:
            out[d0] = None
            continue
        dswe = cur[0] - prev[0]
        ddep = cur[1] - prev[1] if prev[1] is not None and cur[1] is not None else None
        if dswe > s.swe_max_daily_gain_in:
            bump("swe_spike")
            out[d0] = None
            continue
        if ddep is not None and ddep > s.max_daily_depth_gain_in:
            bump("depth_spike")
            out[d0] = None
            continue
        if ddep is not None and ddep > 0:
            depth_gain[d0] = ddep
        t = station_temp_c(era5.get(d0), era5_elevation_m, station_elevation_ft, s.lapse_rate_c_per_km)
        depth_rose = ddep is not None and ddep >= s.storm_min_daily_depth_gain_in
        cold = t is not None and t <= s.rain_snow_threshold_c
        if dswe >= s.swe_min_daily_gain_in - 1e-9 and (ddep is None or ddep >= 0) and (depth_rose or cold):
            if d0.month in month_density_pct:
                pct = month_density_pct[d0.month]
                dens_from["station_month"] += 1
            elif t is not None:
                pct = estimate_density_pct(t, method)
                dens_from["estimated"] += 1
            else:
                bump("no_density")
                out[d0] = None
                continue
            new = dswe / (pct / 100.0)
            if ddep is not None:
                new = max(new, ddep)
            if new > s.max_daily_new_snow_in:
                bump("capped")
                new = s.max_daily_new_snow_in
            out[d0] = round(new, 3)
        elif depth_rose and dswe >= -s.swe_noise_tolerance_in:
            out[d0] = round(min(ddep, s.max_daily_new_snow_in), 3)
            bump("depth_only_day")
        else:
            if dswe >= s.swe_min_daily_gain_in - 1e-9:
                bump("rain_or_warm_swe_gain")
            out[d0] = 0.0

    # seasons with enough data in the snow months
    seasons_ok = 0
    swe_totals: list[float] = []
    depth_totals: list[float] = []
    for season in range(start_year, end_year):
        days = [dd for dd in out if season_of(dd) == season and dd.month in SNOW_MONTHS]
        have = [dd for dd in days if out[dd] is not None]
        if days and len(have) >= min_month_coverage * len(days):
            seasons_ok += 1
            all_days = [dd for dd in out if season_of(dd) == season and out[dd] is not None]
            swe_totals.append(sum(out[dd] for dd in all_days))
            depth_totals.append(sum(depth_gain.get(dd, 0.0) for dd in all_days))
    return SweSnowfall(
        new_snow_in=out,
        qc_counts=qc,
        density_from=dens_from,
        seasons_with_data=seasons_ok,
        swe_annual_in=sum(swe_totals) / len(swe_totals) if swe_totals else None,
        depth_gain_annual_in=sum(depth_totals) / len(depth_totals) if depth_totals else None,
    )


def with_snowfall(daily: list[DailyRecord], new_snow: dict[date, Optional[float]]) -> list[DailyRecord]:
    """ERA5 records with snowfall replaced by station derived new snow (None where missing)."""
    from dataclasses import replace

    return [replace(r, snowfall_in=new_snow.get(r.date)) for r in daily]


def era5_snow_allowed(
    base_ft: Optional[float],
    summit_ft: Optional[float],
    vertical_ft: Optional[float],
    grid_elevation_ft: Optional[float],
    elevation_band_ft: float,
    low_relief_max_vertical_ft: float,
    low_relief_max_base_ft: float,
) -> tuple[bool, str]:
    """Whether ERA5 snowfall may stand in for a resort without a usable SNOTEL station."""
    if vertical_ft is None and base_ft is not None and summit_ft is not None:
        vertical_ft = summit_ft - base_ft
    if (
        base_ft is not None
        and vertical_ft is not None
        and vertical_ft < low_relief_max_vertical_ft
        and base_ft < low_relief_max_base_ft
    ):
        return True, f"low relief (vertical {vertical_ft:,.0f} ft, base {base_ft:,.0f} ft)"
    if base_ft is None:
        return False, "no usable SNOTEL station and the resort base elevation is unknown"
    if grid_elevation_ft is None:
        return False, "no usable SNOTEL station and the ERA5 grid cell height is unknown"
    diff = grid_elevation_ft - base_ft
    if abs(diff) <= elevation_band_ft:
        return True, f"ERA5 grid cell height {grid_elevation_ft:,.0f} ft is within {elevation_band_ft:,.0f} ft of the base"
    where = "below" if diff < 0 else "above"
    return False, (
        f"no usable SNOTEL station and the ERA5 grid cell sits {abs(diff):,.0f} ft {where} the "
        f"{base_ft:,.0f} ft base, so ERA5 snowfall would misstate mountain snowfall"
    )
