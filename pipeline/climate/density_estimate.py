"""Fallback new snow density from storm air temperature.

Used for resorts (and months) without a usable SNOTEL station. Output months
carry ``density_source: "estimated"``. Both relations below were checked
against model source code fetched on 2026-10-11 (weather.gov, NOAA, Wiley and
DOI hosts were not reachable from the build container, so the published papers
themselves were not opened).

Default method, ``anderson_1976`` (Alta relationship):

    rho_new = 50 + 1.7 * (T + 15) ** 1.5    kg/m3, for -15 C < T <= +2 C
    rho_new = 50                            kg/m3, for T <= -15 C
    rho_new = 50 + 1.7 * 17 ** 1.5          kg/m3, for T > +2 C (169.15)

    T is air temperature in deg C. Verified in the NCAR Community Land Model,
    ESCOMP/CTSM src/biogeophys/SnowHydrologyMod.F90, NewSnowBulkDensity
    (commit be1aff2f2a3d3374da37760e846c326616f2da49), which cites "Alta
    relationship, Anderson(1976); LaChapelle(1961)" and uses exactly these
    branches (the low temperature branch is its "TruncatedAnderson1976" option):
    https://github.com/ESCOMP/CTSM/blob/be1aff2f2a3d3374da37760e846c326616f2da49/src/biogeophys/SnowHydrologyMod.F90#L3727-L3732

    The NWS SNOW-17 code uses the same relation in g/cm3,
    DHN = 0.05 + 0.0017 * (T + 15.) ** 1.5 for T > -15 C and 0.05 otherwise,
    with no upper cap; its comment credits Gottlieb (1980), A general runoff
    model for snowcovered and glacierized basins, 6th Nordic Hydrological
    Conference. NOAA-OWP/snow17 src/snow19/SNEW.f
    (commit ea142f94a4788ddf0be7b4d7da59c1f9f552df45):
    https://github.com/NOAA-OWP/snow17/blob/ea142f94a4788ddf0be7b4d7da59c1f9f552df45/src/snow19/SNEW.f

    The two agree for T <= +2 C. Reference values: 5.0 percent water content at
    -15 C and colder, 14.88 percent at 0 C, 16.92 percent at +2 C and warmer.

Alternative method, ``hedstrom_pomeroy_1998``:

    rho_new = 67.92 + 51.25 * exp(T / 2.59)  kg/m3, T in deg C

    Hedstrom, N. R. and Pomeroy, J. W. (1998), Measurements and modelling of
    snow interception in the boreal forest, Hydrological Processes 12, 1611 to
    1625. Verified in the Cold Regions Hydrological Model from Pomeroy's group,
    srlabUsask/crhmcode crhmcode/src/modules/Classinterception.cpp line 125
    (commit a41bf5afd82c9df43e7bf7d845b3f0c28c9708f8), with hru_t in deg C and
    no temperature cap:
    https://github.com/srlabUsask/crhmcode/blob/a41bf5afd82c9df43e7bf7d845b3f0c28c9708f8/crhmcode/src/modules/Classinterception.cpp#L125

    Reference values: 119.17 kg/m3 (11.92 percent) at 0 C, 69.0 kg/m3 at -10 C.
    Below freezing it tops out under 12 percent, so it rarely yields the moist
    class; that is one reason Anderson (1976) is the default.

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
    """CLM NewSnowBulkDensity branches (TruncatedAnderson1976)."""
    if t_c > 2.0:
        return 50.0 + 1.7 * 17.0**1.5
    if t_c > -15.0:
        return 50.0 + 1.7 * (t_c + 15.0) ** 1.5
    return 50.0


def hedstrom_pomeroy_1998_kgm3(t_c: float) -> float:
    """CRHM Classinterception.cpp: RhoS = 67.92 + 51.25*exp(hru_t/2.59)."""
    return 67.92 + 51.25 * math.exp(t_c / 2.59)


METHODS: dict[str, Callable[[float], float]] = {
    "anderson_1976": anderson_1976_kgm3,
    "hedstrom_pomeroy_1998": hedstrom_pomeroy_1998_kgm3,
}

SOURCES = {
    "anderson_1976": {
        "id": "anderson-1976-new-snow-density",
        "url": "https://github.com/ESCOMP/CTSM/blob/be1aff2f2a3d3374da37760e846c326616f2da49/src/biogeophys/SnowHydrologyMod.F90#L3727-L3732",
        "title": (
            "Anderson (1976) Alta new snow density relation, as implemented in the NCAR Community Land "
            "Model (CTSM SnowHydrologyMod.F90, NewSnowBulkDensity)"
        ),
        "note": (
            "Estimated density: rho = 50 + 1.7 (T + 15)^1.5 kg/m3 for -15 C < T <= 2 C, 50 kg/m3 at or "
            "below -15 C, 50 + 1.7 (17)^1.5 above 2 C, using the snowfall weighted daily mean ERA5 air "
            "temperature over each storm at the resort base coordinate. The same relation in g/cm3 "
            "is in NWS SNOW-17 (https://github.com/NOAA-OWP/snow17/blob/"
            "ea142f94a4788ddf0be7b4d7da59c1f9f552df45/src/snow19/SNEW.f)."
        ),
    },
    "hedstrom_pomeroy_1998": {
        "id": "hedstrom-pomeroy-1998-new-snow-density",
        "url": "https://github.com/srlabUsask/crhmcode/blob/a41bf5afd82c9df43e7bf7d845b3f0c28c9708f8/crhmcode/src/modules/Classinterception.cpp#L125",
        "title": (
            "Hedstrom and Pomeroy (1998), Measurements and modelling of snow interception in the "
            "boreal forest, Hydrological Processes 12, as implemented in the Cold Regions Hydrological Model"
        ),
        "note": (
            "Estimated density: rho = 67.92 + 51.25 exp(T / 2.59) kg/m3, using the snowfall weighted "
            "daily mean ERA5 air temperature over each storm."
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
