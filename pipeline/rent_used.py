"""The rent lucrativeness uses: the first available step of ``rent.lucrativeness_rent``, as one RentUsed block.

Default order (owner decision 2026-10-14): ``median_rent`` (the median of the sourced median rent figures for the
resort's town, pipeline/rent_median.py), then ``zillow_zori`` (Zillow's typical rent, pipeline/rent_zillow.py; a
typical rent, not a median), then ``acs_median`` (Census ACS median gross rent of the county), then ``fmr_one_br``
(HUD Fair Market Rent, one bedroom). Also available: ``employee_housing`` (mean of the employee housing rent mids),
``reported_area`` (3+ approved reports), ``baseline`` (``rent.baseline_preference``), ``fmr_studio``,
``fmr_two_br``. Nothing is ever interpolated or filled in: no step with a value means no rent.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from .career import is_stale
from .housing import _site_rent
from .rent import BASELINE_FIELDS, baseline_value
from .schemas.housing import EMPLOYEE_HOUSING, HousingSite

LABELS = {
    "median_rent": "Median rent",
    "zillow_zori": "Zillow typical rent (ZORI)",
    "acs_median": "Census ACS median gross rent",
    "fmr_studio": "HUD Fair Market Rent, studio",
    "fmr_one_br": "HUD Fair Market Rent, one bedroom",
    "fmr_two_br": "HUD Fair Market Rent, two bedrooms",
    "employee_housing": "Employee housing rent, mean of the listed sites",
    "reported_area": "Reported rent near the resort",
}
CONFIDENCE_RANK = {"published": 0, "reported": 1, "estimated": 2}


def _weakest(confidences: Sequence[str]) -> str:
    return max(confidences, key=lambda c: CONFIDENCE_RANK[c])


def _stale(block: dict, today: Optional[dt.date], max_age_months: Optional[int]) -> dict:
    if today is not None and max_age_months is not None:
        block["stale"] = is_stale(dt.date.fromisoformat(block["as_of"]), today, max_age_months)
    return block


def _from_sourced(s: dict, basis: str, label_key: str, geography: Optional[dict] = None) -> dict:
    return {"value": float(s["value"]), "basis": basis, "label": LABELS[label_key], "source_ids": [s["source_id"]],
            "as_of": str(s["as_of"]), "confidence": s["confidence"], "note": s.get("note"), "unit": None,
            "count": None, "publishers": [], "period": None, "geography": geography, "stale": False}


def rent_used(order: Sequence[str], *, median: Optional[dict] = None, zori: Optional[dict] = None,
              baseline: Optional[dict] = None, preference: Sequence[str] = (), sites: Sequence[HousingSite] = (),
              site_reported: Optional[dict] = None, area_reported: Optional[dict] = None,
              today: Optional[dt.date] = None, max_age_months: Optional[int] = None) -> Optional[dict]:
    """The RentUsed block of the first step in ``order`` with a value, or None.

    ``median`` comes from rent_median.median_rent_for, ``zori`` from rent_zillow.zori_rent, ``baseline`` from
    rent.baseline_for. ``stale`` is set when the value's ``as_of`` is older than ``max_age_months`` (median figures
    that old are already left out)."""
    for step in order:
        if step == "median_rent":
            if median is not None:
                return {**median, "label": LABELS["median_rent"]}
        elif step == "zillow_zori":
            if zori is not None:
                block = {k: v for k, v in zori.items() if k != "trailing_mean"}
                return _stale({**block, "basis": "zillow_zori", "label": LABELS["zillow_zori"], "unit": None,
                               "count": None, "publishers": [], "period": None, "stale": False},
                              today, max_age_months)
        elif step == "employee_housing":
            rows = [(s, r) for s in sites if s.type in EMPLOYEE_HOUSING and (r := _site_rent(s, site_reported or {}))]
            if rows:
                mids = [(r["value"]["low"] + r["value"]["high"]) / 2 for _, r in rows]
                return _stale({
                    "value": sum(mids) / len(mids), "basis": "employee_housing_mean:" + ",".join(s.id for s, _ in rows),
                    "label": LABELS["employee_housing"], "source_ids": sorted({r["source_id"] for _, r in rows}),
                    "as_of": min(str(r["as_of"]) for _, r in rows), "confidence": _weakest([r["confidence"] for _, r in rows]),
                    "note": None, "unit": None, "count": len(rows), "publishers": [], "period": None, "geography": None,
                    "stale": False}, today, max_age_months)
        elif step == "reported_area":
            if area_reported is not None:
                v = area_reported["value"]
                return _stale(_from_sourced({**area_reported, "value": (v["low"] + v["high"]) / 2}, "reported_area",
                                            "reported_area"), today, max_age_months)
        else:
            pick = baseline_value(baseline, preference if step == "baseline" else [step])
            if pick is not None:
                key, s = pick
                geo = {"level": "county", "id": baseline.get("county_fips"), "name": baseline["county_name"]}
                return _stale(_from_sourced(s, key, key, geo), today, max_age_months)
    return None


def as_inputs(block: Optional[dict]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """(monthly USD, basis, source ids joined with ", ") for the lucrativeness assumption echo."""
    if block is None:
        return None, None, None
    return float(block["value"]), block["basis"], ", ".join(block["source_ids"])


assert set(BASELINE_FIELDS) <= set(LABELS)  # every baseline key has a label
