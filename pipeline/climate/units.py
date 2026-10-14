"""Unit conversions used by the climate ETL. Inputs of None pass through."""

from __future__ import annotations

from typing import Optional

CM_PER_IN = 2.54
KM_PER_MILE = 1.609344
M_PER_FT = 0.3048
MPS_TO_MPH = 3600 / (KM_PER_MILE * 1000)

# Daily snowfall is rounded to this many decimals in inches before any
# threshold test, so 15.24 cm compares as exactly 6.0 in.
SNOW_IN_DECIMALS = 3


def cm_to_in(cm: Optional[float]) -> Optional[float]:
    if cm is None:
        return None
    return round(cm / CM_PER_IN, SNOW_IN_DECIMALS)


def mm_to_in(mm: Optional[float]) -> Optional[float]:
    if mm is None:
        return None
    return round(mm / (CM_PER_IN * 10), SNOW_IN_DECIMALS)


def c_to_f(c: Optional[float]) -> Optional[float]:
    if c is None:
        return None
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: Optional[float]) -> Optional[float]:
    if f is None:
        return None
    return (f - 32.0) * 5.0 / 9.0


def kmh_to_mph(kmh: Optional[float]) -> Optional[float]:
    if kmh is None:
        return None
    return kmh / KM_PER_MILE


def mps_to_mph(mps: Optional[float]) -> Optional[float]:
    if mps is None:
        return None
    return mps * MPS_TO_MPH


def m_to_ft(m: Optional[float]) -> Optional[float]:
    if m is None:
        return None
    return m / M_PER_FT
