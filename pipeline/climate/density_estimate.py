"""Fallback new snow density from storm air temperature.

Used for resorts (and months) without a usable SNOTEL station. Output months
carry ``density_source: "estimated"``.

Default method, ``anderson_1976``:
    Anderson, E. A. (1976). A point energy and mass balance model of a snow
    cover. NOAA Technical Report NWS 19. The same relation drives the NWS
    SNOW-17 model; see Anderson (2006), Snow Accumulation and Ablation Model,
    SNOW-17, https://www.weather.gov/media/owp/oh/hrl/docs/22snow17.pdf

        rho_new = 0.05 + 0.0017 * (T + 15) ** 1.5   g/cm3, for T > -15 C
        rho_new = 0.05                              g/cm3, for T <= -15 C

    T is air temperature in deg C. Reference values: 5.0 percent water content
    at -15 C and colder, 14.9 percent at 0 C ("about 0.15" in the SNOW-17
    documentation). Temperatures above +2 C are capped at +2 C (16.9 percent),
    as in the Community Land Model implementation of Anderson (1976), because
    the relation was fit to snowfall, which rarely falls warmer than that.

Alternative method, ``hedstrom_pomeroy_1998``:
    Hedstrom, N. R. and Pomeroy, J. W. (1998). Measurements and modelling of
    snow interception in the boreal forest. Hydrological Processes 12,
    1611 to 1625.

        rho_new = 67.92 + 51.25 * exp(T / 2.59)     kg/m3, T in deg C, T <= 0

    Reference values: 119.17 kg/m3 (11.9 percent) at 0 C, 69.0 kg/m3 at -10 C.
    The relation tops out at 11.9 percent, so it never yields the moist class;
    that is why Anderson (1976) is the default.

Storm temperature: snowfall weighted mean of the daily mean air temperature
((Tmax + Tmin) / 2) over the storm days, from ERA5 at the resort base
coordinate. Daily means include non snowing hours and the base sits below the
upper mountain, so estimates lean toward the moist side.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Optional

from pipeline.climate.aggregate import Storm
from pipeline.climate.units import f_to_c


def anderson_1976_kgm3(t_c: float) -> float:
    if t_c <= -15.0:
        return 50.0
    t = min(t_c, 2.0)
    return 50.0 + 1.7 * (t + 15.0) ** 1.5


def hedstrom_pomeroy_1998_kgm3(t_c: float) -> float:
    t = min(t_c, 0.0)
    return 67.92 + 51.25 * math.exp(t / 2.59)


METHODS: dict[str, Callable[[float], float]] = {
    "anderson_1976": anderson_1976_kgm3,
    "hedstrom_pomeroy_1998": hedstrom_pomeroy_1998_kgm3,
}

SOURCES = {
    "anderson_1976": {
        "id": "anderson-1976-snow17-new-snow-density",
        "url": "https://www.weather.gov/media/owp/oh/hrl/docs/22snow17.pdf",
        "title": (
            "Anderson (1976), A point energy and mass balance model of a snow cover, NOAA Technical "
            "Report NWS 19; new snow density relation as documented for NWS SNOW-17"
        ),
        "note": (
            "Estimated density: rho = 0.05 + 0.0017 (T + 15)^1.5 g/cm3 for T > -15 C, 0.05 at or "
            "below -15 C, T capped at +2 C, using the snowfall weighted daily mean ERA5 air "
            "temperature over each storm at the resort base coordinate."
        ),
    },
    "hedstrom_pomeroy_1998": {
        "id": "hedstrom-pomeroy-1998-new-snow-density",
        "url": "https://doi.org/10.1002/(SICI)1099-1085(199808/09)12:10/11<1611::AID-HYP684>3.0.CO;2-4",
        "title": (
            "Hedstrom and Pomeroy (1998), Measurements and modelling of snow interception in the "
            "boreal forest, Hydrological Processes 12, 1611 to 1625"
        ),
        "note": (
            "Estimated density: rho = 67.92 + 51.25 exp(T / 2.59) kg/m3, T capped at 0 C, using the "
            "snowfall weighted daily mean ERA5 air temperature over each storm."
        ),
    },
}


def density_pct(kgm3: float) -> float:
    """kg/m3 of new snow to percent water content (water is 1000 kg/m3)."""
    return kgm3 / 10.0


def estimate_density_pct(t_c: float, method: str = "anderson_1976") -> float:
    return density_pct(METHODS[method](t_c))


def storm_temperature_c(storm: Storm) -> Optional[float]:
    """Snowfall weighted mean of daily mean temperature over the storm, deg C."""
    num = 0.0
    den = 0.0
    for day in storm.days:
        t = day.tmean_f
        if t is None or day.snowfall_in is None:
            continue
        num += f_to_c(t) * day.snowfall_in
        den += day.snowfall_in
    return num / den if den > 0 else None


def estimate_storm_densities(storms: Iterable[Storm], method: str) -> list[tuple[int, float]]:
    """(month of storm start, estimated density pct) per storm with temperature data."""
    out = []
    for s in storms:
        t = storm_temperature_c(s)
        if t is not None:
            out.append((s.month, estimate_density_pct(t, method)))
    return out
