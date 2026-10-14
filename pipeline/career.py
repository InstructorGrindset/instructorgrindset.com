"""Phase 4 career layer: pay display, staleness, lucrativeness (and the internal professionalism model).

Every weight and assumption comes from config/definitions.yaml. While Alexandre
has not set them they are null and the formulas report a status
(``weights_unset``, ``assumptions_unset``) instead of a number. No number is
ever filled in here.

Contract: docs/map/ARCHITECTURE.md, "Phase 4 career".
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml
from pydantic import ValidationError

from .config import Definitions, LucrativenessDefs, ProfessionalismDefs
from .schemas.career import (
    CERT_LEVELS, PROFESSIONALISM_COMPONENTS, CareerBatch, CareerInputs, PayBatch, PayRecord,
    season_start_year,
)
from .season_income import lucrativeness_inputs, round_half_up, season_income
from .sources import SourceRegistry

ASSUMPTION_LABELS: dict[str, str] = {
    "wage_mid_usd": "Middle of the posted wage range (USD per hour)",
    "wage_top_usd": "Top of the posted wage scale (USD per hour)",
    "wage_bottom_usd": "Bottom of the posted wage scale (USD per hour), used for the low end",
    "hours_per_week": "Standard full time hours per week",
    "season_months": "Season months",
    "season_months_basis": "Season months basis",
    "weeks_per_month": "Weeks per month",
    "tax": "Before tax and other living costs",
}
SEASON_LENGTH_LABEL = "Season months (sourced season length divided by {d} days)"
SEASON_DEFAULT_LABEL = "Season months (standard {m} month season)"


# ----------------------------------------------------------------- staleness
def add_months(d: dt.date, months: int) -> dt.date:
    """Calendar month arithmetic; the day is clamped to the end of a shorter month."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def is_stale(as_of: dt.date, today: dt.date, max_age_months: int) -> bool:
    """Stale once more than ``max_age_months`` have passed: a value exactly that old is still current."""
    return add_months(as_of, max_age_months) < today


# --------------------------------------------------------------- pay display
def select_pay(
    records: Iterable[PayRecord],
    priority: Sequence[str],
    aggregation_min: int,
    today: dt.date,
    max_age_months: int,
) -> tuple[list[dict], list[str]]:
    """One display row per cert level.

    Order of preference: the most recent season, then the highest confidence in
    ``priority`` (published > reported > estimated), then the latest
    verified_date, then source_id and values for a deterministic tie break.
    A reported row counts only when it aggregates at least ``aggregation_min``
    approved reports; other reported rows are dropped with a warning.
    """
    rank = {c: i for i, c in enumerate(priority)}
    warnings: list[str] = []
    by_level: dict[str, list[PayRecord]] = {}
    for r in records:
        if r.confidence == "reported" and (r.aggregate_count or 0) < aggregation_min:
            warnings.append(
                f"pay {r.resort_id} {r.season} {r.cert_level}: reported row aggregates {r.aggregate_count} "
                f"reports, fewer than {aggregation_min}; not displayed"
            )
            continue
        by_level.setdefault(r.cert_level, []).append(r)
    rows = []
    for level in CERT_LEVELS:
        cands = by_level.get(level)
        if not cands:
            continue
        best = min(cands, key=lambda r: (
            -season_start_year(r.season), rank[r.confidence], -r.verified_date.toordinal(),
            r.source_id, r.hourly_low, r.hourly_high,
        ))
        rows.append({
            "cert_level": best.cert_level, "hourly_low": best.hourly_low, "hourly_high": best.hourly_high,
            "confidence": best.confidence, "source_id": best.source_id, "as_of": best.verified_date.isoformat(),
            "season": best.season, "stale": is_stale(best.verified_date, today, max_age_months),
        })
    return rows, warnings


# ------------------------------------------------------------ professionalism
# Internal only: the professionalism rating is off the public site (owner decision 2026-10-13), so build_career
# never calls this and nothing of it reaches data/build. Kept for the owner's private use.
def professionalism(inputs: Optional[CareerInputs], defs: ProfessionalismDefs) -> dict:
    """score = 100 x sum(w x v) / sum(w) over components with a value.

    Status ``weights_unset`` while any weight or ``min_components`` is null,
    ``insufficient_data`` when fewer than ``min_components`` components have a
    value, else ``computed``. The breakdown always lists all six components.
    """
    weights = defs.weights.model_dump()
    pin = inputs.professionalism_inputs if inputs else None
    values: dict[str, Optional[tuple[float, str]]] = {}
    for comp in PROFESSIONALISM_COMPONENTS:
        s = getattr(pin, comp) if pin else None
        values[comp] = (float(s.value), s.source_id) if s is not None else None

    def breakdown(points: Optional[dict[str, float]] = None) -> list[dict]:
        return [{
            "component": c, "weight": weights[c],
            "value": values[c][0] if values[c] else None,
            "points": (points or {}).get(c),
            "source_id": values[c][1] if values[c] else None,
        } for c in PROFESSIONALISM_COMPONENTS]

    if not defs.weights_set or defs.min_components is None:
        return {"score": None, "status": "weights_unset", "breakdown": breakdown()}
    present = [c for c in PROFESSIONALISM_COMPONENTS if values[c] is not None]
    total_w = sum(weights[c] for c in present)
    if len(present) < defs.min_components or total_w <= 0:
        return {"score": None, "status": "insufficient_data", "breakdown": breakdown()}
    points = {c: round(100 * weights[c] * values[c][0] / total_w, 2) for c in present}
    score = round(100 * sum(weights[c] * values[c][0] for c in present) / total_w, 1)
    return {"score": score, "status": "computed", "breakdown": breakdown(points)}


# -------------------------------------------------------------- lucrativeness
def lucrativeness(
    pay_rows: Sequence[dict],
    season_length_days: Optional[float],
    defs: LucrativenessDefs,
    rent: Optional[dict] = None,
) -> dict:
    """Estimated net income for the season in USD, before tax (owner decisions 4 and 2026-10-14), with every
    stated assumption echoed.

    season months = season_length_days / days_per_month, else default_season_months
    net income    = hourly x hours_per_week x season months x weeks_per_month

    No rent, tips, tax or other living costs (owner decision 2026-10-14). The wage range is the displayed pay
    rows, any cert level: bottom = lowest hourly_low, top = highest hourly_high. low = net income at the bottom,
    high = net income at the top, and mid = net income at the middle of the range, (bottom + top) / 2. The map
    shows mid as "Estimated net income", colors by it, and shows low to high as the range.

    ``rent`` is the town's RentUsed block (pipeline/rent_used.py), echoed as ``rent`` for the Housing section;
    it never enters the figure.

    Computed with pipeline.season_income (the calculator's shared model) with rent, tips and every offset at 0:
    net income is its ``net_before_tax``, which then equals its ``gross``. Whole dollars use round_half_up
    (JS Math.round).
    """
    a = defs.assumptions
    top = max((r["hourly_high"] for r in pay_rows), default=None)
    bottom = min((r["hourly_low"] for r in pay_rows), default=None)
    middle = (bottom + top) / 2 if top is not None and bottom is not None else None
    if season_length_days:
        months, basis = season_length_days / a.days_per_month, "season_length"
        months_label = SEASON_LENGTH_LABEL.format(d=a.days_per_month)
    else:
        months, basis = a.default_season_months, "default"
        months_label = SEASON_DEFAULT_LABEL.format(m=_fmt(a.default_season_months))
    echo = [
        {"key": "wage_mid_usd", "value": round(middle, 3) if middle is not None else None,
         "label": ASSUMPTION_LABELS["wage_mid_usd"]},
        {"key": "wage_top_usd", "value": top, "label": ASSUMPTION_LABELS["wage_top_usd"]},
        {"key": "wage_bottom_usd", "value": bottom, "label": ASSUMPTION_LABELS["wage_bottom_usd"]},
        {"key": "hours_per_week", "value": a.hours_per_week, "label": ASSUMPTION_LABELS["hours_per_week"]},
        {"key": "season_months", "value": round(months, 2) if months is not None else None, "label": months_label},
        {"key": "season_months_basis", "value": basis, "label": ASSUMPTION_LABELS["season_months_basis"]},
        {"key": "weeks_per_month", "value": a.weeks_per_month, "label": ASSUMPTION_LABELS["weeks_per_month"]},
        {"key": "tax", "value": None, "label": ASSUMPTION_LABELS["tax"]},
    ]
    empty = {"low": None, "mid": None, "high": None}
    if a.hours_per_week is None or months is None:
        return {**empty, "status": "assumptions_unset", "assumptions": echo, "rent": rent}
    if top is None:
        return {**empty, "status": "insufficient_data", "assumptions": echo, "rent": rent}

    def net_income(hourly: float) -> int:
        net = season_net_income(hourly, a.hours_per_week, months, a.weeks_per_month)
        return int(round_half_up(net.net_before_tax))

    return {"low": net_income(bottom), "mid": net_income(middle), "high": net_income(top),
            "status": "computed", "assumptions": echo, "rent": rent}


def season_net_income(hourly: float, hours_per_week: float, season_months: float, weeks_per_month: float):
    """The shared calculator model for one hourly wage, called with rent, food, transport, pass and gear at 0
    and no tips (owner decision 2026-10-14). The model itself is unchanged."""
    inputs = lucrativeness_inputs(
        pay_hourly_mid_usd=hourly, hours_per_week=hours_per_week,
        season_length_days=season_months * weeks_per_month * 7, rent_monthly_usd=0,
        food_monthly_usd=0, transport_monthly_usd=0, season_months=season_months, pass_offset_usd=0, gear_offset_usd=0)
    return season_income(inputs)


def _fmt(x: Optional[float]) -> str:
    return "" if x is None else (str(int(x)) if float(x).is_integer() else str(x))


# ----------------------------------------------------------------- assembly
def build_career(
    pay_records: Sequence[PayRecord],
    inputs: Optional[CareerInputs],
    season_length_days: Optional[float],
    defs: Definitions,
    today: dt.date,
    rent: Optional[dict] = None,
) -> tuple[Optional[dict], list[str]]:
    """Resort.career, or None when the resort has no pay records and no career inputs. ``rent`` is the town's
    RentUsed block (pipeline/rent_used.py), echoed for the Housing section; it never enters lucrativeness. A
    stale rent adds ``lucrativeness.rent`` to ``stale_fields``."""
    if not pay_records and inputs is None:
        return None, []
    max_age = defs.staleness.max_age_months
    rows, warnings = select_pay(pay_records, defs.display.priority, defs.reports.aggregation_min, today, max_age)
    stale = [f"pay.{r['cert_level']}" for r in rows if r["stale"]]
    for name in ("private_rate", "private_split"):
        s = getattr(inputs, name) if inputs else None
        if s is not None and is_stale(s.as_of, today, max_age):
            stale.append(name)
    career = {
        "pay": rows,
        "private_rate": inputs.private_rate.model_dump(mode="json") if inputs and inputs.private_rate else None,
        "private_split": inputs.private_split.model_dump(mode="json") if inputs and inputs.private_split else None,
        "lucrativeness": lucrativeness(rows, season_length_days, defs.lucrativeness, rent=rent),
        "stale_fields": stale + (["lucrativeness.rent"] if rent and rent.get("stale") else []),
    }
    return career, warnings


# -------------------------------------------------------------------- loading
@dataclass
class CareerData:
    pay: dict[str, list[PayRecord]] = field(default_factory=dict)
    inputs: dict[str, CareerInputs] = field(default_factory=dict)


def load_career_data(raw_dir: Path, registry: SourceRegistry, vocab: dict, rel=str) -> tuple[CareerData, list[str]]:
    """Validate data/raw/pay/*.yaml and data/raw/career/*.yaml. Returns (data, errors)."""
    from .tier_a import _register_sources, _yaml_files

    errors: list[str] = []
    files = [(p, PayBatch) for p in _yaml_files(raw_dir / "pay")] + \
            [(p, CareerBatch) for p in _yaml_files(raw_dir / "career")]
    raws: dict[Path, Any] = {}
    for p, _ in files:
        try:
            with p.open(encoding="utf-8") as fh:
                raws[p] = yaml.safe_load(fh)
        except yaml.YAMLError as e:
            errors.append(f"{rel(p)}: YAML parse error: {e}")
            continue
        _register_sources(raws[p], rel(p), registry, errors)
    ctx = {**registry.context(), "career_vocab": vocab}
    data = CareerData()
    for p, model in files:
        if not isinstance(raws.get(p), dict):
            continue
        try:
            batch = model.model_validate(raws[p], context=ctx)
        except ValidationError as e:
            for m in e.errors():
                loc = list(m["loc"])
                where = rel(p)
                if len(loc) >= 2 and loc[0] in ("records", "resorts") and isinstance(loc[1], int):
                    try:
                        where = f"{rel(p)} {loc[0]}[{loc[1]}] ({raws[p][loc[0]][loc[1]].get('resort_id')})"
                    except (KeyError, IndexError, AttributeError, TypeError):
                        pass
                    loc = loc[2:]
                errors.append(f"{where}: {'.'.join(map(str, loc)) or '(record)'}: {m['msg']}")
            continue
        if isinstance(batch, PayBatch):
            for r in batch.records:
                data.pay.setdefault(r.resort_id, []).append(r)
        else:
            for c in batch.resorts:
                if c.resort_id in data.inputs:
                    errors.append(f"{rel(p)}: career inputs for '{c.resort_id}' are defined in more than one file")
                data.inputs[c.resort_id] = c
    return data, errors
