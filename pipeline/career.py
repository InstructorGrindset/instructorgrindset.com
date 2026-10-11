"""Phase 4 career layer: pay display, staleness, professionalism, lucrativeness.

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
from .sources import SourceRegistry

ASSUMPTION_LABELS: dict[str, str] = {
    "cert_level": "Cert level used for pay",
    "hours_per_week": "Hours per week",
    "season_months": "Months of rent and living costs",
    "housing_choice": "Housing used for rent",
    "food_monthly_usd": "Food per month (USD)",
    "transport_monthly_usd": "Transport per month (USD)",
    "pass_offset_usd": "Season pass (USD)",
    "gear_offset_usd": "Gear (USD)",
    "range_spread_low": "Range below mid, share of earnings",
    "range_spread_high": "Range above mid, share of earnings",
    "pay_hourly_mid_usd": "Hourly pay used, midpoint (USD)",
    "season_length_days": "Season length (days)",
    "rent_monthly_usd": "Rent per month (USD)",
}
EMPLOYEE_HOUSING_TYPES = ("resort_owned", "master_lease")


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
def cheapest_employee_rent(sites: Iterable[Any], resort_id: str) -> Optional[float]:
    """Lowest monthly rent among a resort's employee housing (resort owned or master lease)."""
    rents = [
        s.rent_monthly_low for s in sites
        if s.resort_id == resort_id and s.type in EMPLOYEE_HOUSING_TYPES and s.rent_monthly_low is not None
    ]
    return min(rents) if rents else None


def lucrativeness(
    pay_rows: Sequence[dict],
    season_length_days: Optional[float],
    rent_monthly: Optional[float],
    defs: LucrativenessDefs,
) -> dict:
    """Season savings low / mid / high in USD, with every assumption echoed.

    earnings = mid hourly pay (assumptions.cert_level row) x hours_per_week x season_length_days / 7
    costs    = (rent + food + transport) x season_months + pass_offset + gear_offset
    mid = earnings - costs, low = mid - spread_low x earnings, high = mid + spread_high x earnings
    """
    a = defs.assumptions
    spread = defs.range_spread
    pay = next((r for r in pay_rows if r["cert_level"] == a.cert_level), None) if a.cert_level else None
    pay_mid = (pay["hourly_low"] + pay["hourly_high"]) / 2 if pay else None
    echo = [{"key": k, "value": v, "label": ASSUMPTION_LABELS[k]} for k, v in a.model_dump().items()]
    echo += [
        {"key": "range_spread_low", "value": spread.low, "label": ASSUMPTION_LABELS["range_spread_low"]},
        {"key": "range_spread_high", "value": spread.high, "label": ASSUMPTION_LABELS["range_spread_high"]},
        {"key": "pay_hourly_mid_usd", "value": pay_mid, "label": ASSUMPTION_LABELS["pay_hourly_mid_usd"]},
        {"key": "season_length_days", "value": season_length_days, "label": ASSUMPTION_LABELS["season_length_days"]},
        {"key": "rent_monthly_usd", "value": rent_monthly, "label": ASSUMPTION_LABELS["rent_monthly_usd"]},
    ]
    empty = {"low": None, "mid": None, "high": None}
    if any(v is None for v in a.model_dump().values()) or spread.low is None or spread.high is None:
        return {**empty, "status": "assumptions_unset", "assumptions": echo}
    if pay_mid is None or rent_monthly is None or season_length_days is None:
        return {**empty, "status": "insufficient_data", "assumptions": echo}
    earnings = pay_mid * a.hours_per_week * season_length_days / 7
    costs = (rent_monthly + a.food_monthly_usd + a.transport_monthly_usd) * a.season_months \
        + a.pass_offset_usd + a.gear_offset_usd
    mid = earnings - costs
    return {
        "low": round(mid - spread.low * earnings), "mid": round(mid), "high": round(mid + spread.high * earnings),
        "status": "computed", "assumptions": echo,
    }


# ----------------------------------------------------------------- assembly
def build_career(
    pay_records: Sequence[PayRecord],
    inputs: Optional[CareerInputs],
    season_length_days: Optional[float],
    rent_monthly: Optional[float],
    defs: Definitions,
    today: dt.date,
) -> tuple[Optional[dict], list[str]]:
    """Resort.career, or None when the resort has no pay records and no career inputs."""
    if not pay_records and inputs is None:
        return None, []
    max_age = defs.staleness.max_age_months
    rows, warnings = select_pay(pay_records, defs.display.priority, defs.reports.aggregation_min, today, max_age)
    stale = [f"pay.{r['cert_level']}" for r in rows if r["stale"]]
    for name in ("private_rate", "private_split"):
        s = getattr(inputs, name) if inputs else None
        if s is not None and is_stale(s.as_of, today, max_age):
            stale.append(name)
    if inputs:
        for comp in PROFESSIONALISM_COMPONENTS:
            s = getattr(inputs.professionalism_inputs, comp)
            if s is not None and is_stale(s.as_of, today, max_age):
                stale.append(f"professionalism.{comp}")
    career = {
        "pay": rows,
        "private_rate": inputs.private_rate.model_dump(mode="json") if inputs and inputs.private_rate else None,
        "private_split": inputs.private_split.model_dump(mode="json") if inputs and inputs.private_split else None,
        "professionalism": professionalism(inputs, defs.professionalism),
        "lucrativeness": lucrativeness(rows, season_length_days, rent_monthly, defs.lucrativeness),
        "stale_fields": stale,
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
