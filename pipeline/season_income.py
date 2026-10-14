"""Season Income Calculator model, Python mirror of ``assets/js/season-income.mjs``.

The calculator page (/income-calculator/) and the Career Map pipeline share one model. This
module repeats the JS module operation for operation on IEEE doubles, so both produce the same
values for the same inputs. tests/calculator checks both against golden vectors captured from the
live page. Change one file, change the other.

Rounding rules (identical in both languages):

* ``round_half_up(x)``: whole numbers, ties toward positive infinity (JS ``Math.round``). Used for
  private lesson days and for every whole dollar figure. Python's ``round`` (ties to even) differs.
* ``to_fixed_half_up(x, d)``: ``d`` decimals of the exact binary value, ties away from zero
  (JS ``Number.prototype.toFixed``). Used for displayed rates and months.

Every other value is an unrounded double. Division follows JS: dividing by zero gives an infinity
or NaN instead of raising.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# Federal income tax brackets (single filer, no deductions):
# tax = base + (taxable - over) x rate for the first bracket with taxable <= up_to.
FEDERAL_BRACKETS: tuple[dict, ...] = (
    {"over": 0, "up_to": 11600, "base": 0, "rate": 0.10},
    {"over": 11600, "up_to": 47150, "base": 1160, "rate": 0.12},
    {"over": 47150, "up_to": 100525, "base": 5426, "rate": 0.22},
    {"over": 100525, "up_to": 191950, "base": 17168, "rate": 0.24},
    {"over": 191950, "up_to": math.inf, "base": 39110, "rate": 0.32},
)

# Social Security and Medicare, employee share, on wages.
FICA_RATE = 0.0765

# Months in a season = weeks / WEEKS_PER_MONTH, unless the caller passes months.
WEEKS_PER_MONTH = 4.33

# Flat state income tax rate on wages, as the calculator's state menu lists them.
STATE_TAX_RATES: tuple[dict, ...] = (
    {"code": "WA", "name": "Washington", "rate": 0},
    {"code": "WY", "name": "Wyoming", "rate": 0},
    {"code": "NV", "name": "Nevada", "rate": 0},
    {"code": "AK", "name": "Alaska", "rate": 0},
    {"code": "NH", "name": "New Hampshire", "rate": 0},
    {"code": "CO", "name": "Colorado", "rate": 0.044},
    {"code": "UT", "name": "Utah", "rate": 0.0465},
    {"code": "ID", "name": "Idaho", "rate": 0.058},
    {"code": "VT", "name": "Vermont", "rate": 0.066},
    {"code": "MT", "name": "Montana", "rate": 0.0675},
    {"code": "NY", "name": "New York", "rate": 0.0685},
    {"code": "OR", "name": "Oregon", "rate": 0.0875},
    {"code": "CA", "name": "California", "rate": 0.093},
)

# Verdict thresholds on net / gross (strictly greater than).
VERDICT_THRESHOLDS = {"strong": 0.35, "workable": 0.1, "tight": 0}


def state_tax_rate(code_or_name: str) -> Optional[float]:
    """Rate for a state code ("CO") or name ("Colorado"); None when the calculator has no rate for it."""
    for s in STATE_TAX_RATES:
        if code_or_name in (s["code"], s["name"]):
            return float(s["rate"])
    return None


def round_half_up(x: float) -> float:
    """Whole number, ties toward positive infinity (JS Math.round). Non finite values pass through.

    Returns a float (JS numbers are doubles); ``-0.0`` for values in [-0.5, 0) like Math.round.
    """
    x = float(x)
    if not math.isfinite(x):
        return x
    f = math.floor(x)
    r = float(f + 1 if x - f >= 0.5 else f)
    return -0.0 if r == 0 and (x < 0 or math.copysign(1.0, x) < 0) else r


def to_fixed_half_up(x: float, digits: int) -> str:
    """Fixed decimals of the exact binary value, ties away from zero (JS Number.prototype.toFixed).

    Covers |x| < 1e21, where toFixed does not switch to exponent notation.
    """
    x = float(x)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    q = Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    s = f"{q:f}"
    # toFixed keeps the sign only for a nonzero x: (-0).toFixed(1) is "0.0", (-0.04).toFixed(1) is "-0.0".
    if x == 0 and s.startswith("-"):
        s = s[1:]
    return s


def _div(a: float, b: float) -> float:
    """JS division: a / 0 is +-Infinity, 0 / 0 is NaN."""
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def federal_tax(taxable: float) -> float:
    """Federal income tax on taxable wages."""
    taxable = float(taxable)
    for b in FEDERAL_BRACKETS:
        if taxable <= b["up_to"]:
            return taxable * b["rate"] if b["over"] == 0 else b["base"] + (taxable - b["over"]) * b["rate"]
    return math.nan  # only for NaN input


def verdict(ratio: float) -> str:
    """Verdict key for the share of gross kept: strong, workable, tight or negative."""
    if ratio > VERDICT_THRESHOLDS["strong"]:
        return "strong"
    if ratio > VERDICT_THRESHOLDS["workable"]:
        return "workable"
    if ratio > VERDICT_THRESHOLDS["tight"]:
        return "tight"
    return "negative"


@dataclass(frozen=True)
class SeasonInputs:
    """Calculator inputs. ``private_pct`` is 0..100; transport and gear are one time costs.

    ``months`` defaults to ``weeks / WEEKS_PER_MONTH`` (the calculator never sets it).
    """
    state_rate: float
    weeks: float
    days_per_week: float
    hours_per_day: float
    group_rate: float
    private_rate: float
    private_pct: float
    private_length_hours: float
    tips_per_day: float
    rent_monthly: float
    life_monthly: float
    transport: float
    gear: float
    months: Optional[float] = None


@dataclass(frozen=True)
class SeasonResult:
    total_days: float
    private_days_raw: float
    private_days: float
    group_days: float
    group_hours: float
    private_hours: float
    group_income: float
    private_income: float
    tip_income: float
    taxable: float
    gross: float
    federal_tax: float
    state_tax: float
    fica: float
    total_tax: float
    effective_rate: float
    after_tax: float
    after_tax_wages: float
    months: float
    housing_cost: float
    life_cost: float
    total_expenses: float
    net: float
    net_before_tax: float
    weekly_net: float
    ratio: float
    verdict: str

    def as_dict(self) -> dict:
        return asdict(self)


def season_income(inputs: SeasonInputs) -> SeasonResult:
    """The calculator's season model. Tips are untaxed; federal, state and FICA apply to wages only."""
    f = {k: (None if v is None else float(v)) for k, v in asdict(inputs).items()}
    weeks = f["weeks"]

    total_days = weeks * f["days_per_week"]
    private_days_raw = total_days * f["private_pct"] / 100
    private_days = round_half_up(private_days_raw)
    group_days = total_days - private_days
    private_hours = private_days * f["private_length_hours"]
    group_hours = group_days * f["hours_per_day"]

    group_income = group_hours * f["group_rate"]
    private_income = private_hours * f["private_rate"]
    tip_income = total_days * f["tips_per_day"]
    taxable = group_income + private_income
    gross = taxable + tip_income

    fed = federal_tax(taxable)
    state_tax = taxable * f["state_rate"]
    fica = taxable * FICA_RATE
    total_tax = fed + state_tax + fica
    effective_rate = _div(total_tax, taxable) if taxable > 0 else 0.0
    after_tax = gross - total_tax
    after_tax_wages = taxable - total_tax

    months = f["months"] if f["months"] is not None else _div(weeks, WEEKS_PER_MONTH)
    housing_cost = f["rent_monthly"] * months
    life_cost = f["life_monthly"] * months
    total_expenses = housing_cost + life_cost + f["transport"] + f["gear"]

    net = after_tax - total_expenses
    net_before_tax = gross - total_expenses
    weekly_net = _div(net, weeks)
    ratio = _div(net, gross) if gross > 0 else 0.0

    return SeasonResult(
        total_days=total_days, private_days_raw=private_days_raw, private_days=private_days,
        group_days=group_days, group_hours=group_hours, private_hours=private_hours,
        group_income=group_income, private_income=private_income, tip_income=tip_income,
        taxable=taxable, gross=gross, federal_tax=fed, state_tax=state_tax, fica=fica,
        total_tax=total_tax, effective_rate=effective_rate, after_tax=after_tax,
        after_tax_wages=after_tax_wages, months=months, housing_cost=housing_cost,
        life_cost=life_cost, total_expenses=total_expenses, net=net, net_before_tax=net_before_tax,
        weekly_net=weekly_net, ratio=ratio, verdict=verdict(ratio),
    )


def lucrativeness_inputs(
    *,
    pay_hourly_mid_usd: float,
    hours_per_week: float,
    season_length_days: float,
    rent_monthly_usd: float,
    food_monthly_usd: float,
    transport_monthly_usd: float,
    season_months: float,
    pass_offset_usd: float,
    gear_offset_usd: float,
    state_rate: float = 0.0,
) -> SeasonInputs:
    """Map the Career Map lucrativeness assumptions onto calculator inputs (no privates, no tips).

    weeks = season_length_days / 7, one work block per week of ``hours_per_week`` hours at
    ``pay_hourly_mid_usd``; rent = rent_monthly_usd (the map's median rent per month: the median of the sourced
    median rent figures for the resort's town, else Zillow's typical rent, ACS median gross rent or HUD FMR one
    bedroom; pipeline/rent_used.py); life = food + transport per month;
    months = season_months; one time costs = pass_offset + gear_offset (gear slot); transport slot 0.
    ``season_income(...).gross`` is then lucrativeness earnings and ``.net_before_tax`` is the
    lucrativeness mid. ``state_rate`` only affects the after tax figures (net, total_tax, ...).
    """
    return SeasonInputs(
        state_rate=state_rate, weeks=_div(float(season_length_days), 7.0), days_per_week=1,
        hours_per_day=hours_per_week, group_rate=pay_hourly_mid_usd, private_rate=0, private_pct=0,
        private_length_hours=0, tips_per_day=0, rent_monthly=rent_monthly_usd,
        life_monthly=float(food_monthly_usd) + float(transport_monthly_usd), transport=0,
        gear=float(pass_offset_usd) + float(gear_offset_usd), months=season_months,
    )
