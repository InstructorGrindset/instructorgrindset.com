"""New snow density classes and monthly density summaries.

Classes (ROADMAP Section 6, cutoffs from config/definitions.yaml):
dry under ``dry_below_pct``, average from ``dry_below_pct`` to
``moist_above_pct`` inclusive, moist over ``moist_above_pct``.
With the default 7 and 12: 6.99 is dry, 7.0 and 12.0 are average, 12.01 is moist.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

CLASSES = ("dry", "average", "moist")


def classify(density_pct: float, dry_below_pct: float, moist_above_pct: float) -> str:
    if density_pct < dry_below_pct:
        return "dry"
    if density_pct > moist_above_pct:
        return "moist"
    return "average"


def _fractions(counts: dict[str, int], decimals: int = 3) -> dict[str, float]:
    """Largest remainder rounding so the rounded fractions sum to exactly 1."""
    total = sum(counts.values())
    scale = 10**decimals
    raw = {k: counts[k] * scale / total for k in CLASSES}
    floors = {k: int(raw[k]) for k in CLASSES}
    leftover = scale - sum(floors.values())
    for k in sorted(CLASSES, key=lambda c: raw[c] - floors[c], reverse=True)[:leftover]:
        floors[k] += 1
    return {k: floors[k] / scale for k in CLASSES}


@dataclass(frozen=True)
class MonthDensity:
    month: int
    n_storms: int
    median_pct: float
    mix: dict[str, float]


def monthly_density(
    storms: Iterable[tuple[int, float]],
    dry_below_pct: float,
    moist_above_pct: float,
    min_storms: int,
) -> dict[int, MonthDensity]:
    """``storms`` is (month, density_pct) per storm. Months with fewer than
    ``min_storms`` storms are left out."""
    by_month: dict[int, list[float]] = {}
    for month, pct in storms:
        by_month.setdefault(month, []).append(pct)
    out: dict[int, MonthDensity] = {}
    for month, values in by_month.items():
        if len(values) < max(1, min_storms):
            continue
        counts = {k: 0 for k in CLASSES}
        for v in values:
            counts[classify(v, dry_below_pct, moist_above_pct)] += 1
        out[month] = MonthDensity(
            month=month,
            n_storms=len(values),
            median_pct=float(median(values)),
            mix=_fractions(counts),
        )
    return out


def pick_month_density(
    month: int,
    snotel: Optional[dict[int, MonthDensity]],
    estimated: Optional[dict[int, MonthDensity]],
) -> tuple[Optional[MonthDensity], Optional[str]]:
    """SNOTEL when the station has enough storms that month, else the estimate."""
    if snotel and month in snotel:
        return snotel[month], "snotel"
    if estimated and month in estimated:
        return estimated[month], "estimated"
    return None, None
