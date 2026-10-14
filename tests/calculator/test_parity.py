"""Season Income Calculator: Python mirror against the golden vectors and the JS module.

golden.json was captured from main's inline script /income-calculator/ page (commit be2553b: private
day remainder at the group rate, 14600 standard deduction, monthly transportation) with
tests/calculator/page_golden.mjs --capture. Values are compared exactly as doubles.
Rounding rules: whole dollars by round_half_up (JS Math.round, ties toward positive infinity);
displayed decimals by to_fixed_half_up (JS toFixed, exact binary value, ties away from zero).
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import season_income as si

HERE = Path(__file__).parent
REPO = HERE.parent.parent
GOLDEN = json.loads((HERE / "golden.json").read_text())
VECTORS = GOLDEN["vectors"]
ADAPTER_CASES = json.loads((HERE / "adapter_cases.json").read_text())["cases"]
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def inputs_from_golden(inp: dict) -> si.SeasonInputs:
    rate = si.state_tax_rate(inp["state"])
    assert rate is not None, inp["state"]
    return si.SeasonInputs(state_rate=rate, **{k: v for k, v in inp.items() if k != "state"})


def _camel(s: str) -> str:
    return re.sub(r"_([a-z])", lambda m: m.group(1).upper(), s)


def _num(v):
    """JS dump value to float: non finite numbers arrive as strings."""
    return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}.get(v, v) if isinstance(v, str) else v


def _same(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    a, b = float(a), float(b)
    return (math.isnan(a) and math.isnan(b)) or a == b


# ----------------------------------------------------------- golden vectors
def test_golden_set_shape():
    ui = [v for v in VECTORS if v["kind"] == "ui"]
    assert len(VECTORS) >= 100 and len(ui) >= 90
    assert len({v["id"] for v in VECTORS}) == len(VECTORS)
    assert {v["inputs"]["state"] for v in ui} == {s["name"] for s in si.STATE_TAX_RATES}
    assert {v["verdict"] for v in VECTORS} == {"strong", "workable", "tight", "negative"}
    assert GOLDEN["captured_from"]["git_blob"] == "736d84a41b55e8299e23a4b0c68475dd8f273292"
    assert GOLDEN["captured_from"]["git_commit"] == "be2553ba60391c13b84dcf7e9c144683ec7931ed"


def test_golden_covers_main_formula_changes():
    by_id = {v["id"]: v["values"] for v in VECTORS}
    # rest of a private day at the group rate, never below 0 hours
    assert by_id["private-remainder-max"]["group_hours"] > 0
    assert by_id["private-length-exceeds-day"]["group_hours"] == by_id["private-length-exceeds-day"]["total_days"] / 2 * 4
    # wages on both sides of the standard deduction
    assert [by_id[f"standard-deduction-{k}"]["taxable"] for k in ("just-below", "exact", "just-above")] == \
        [14599, 14600, 14601]
    assert [by_id[f"standard-deduction-{k}"]["federal_tax"] for k in ("just-below", "exact", "just-above")] == \
        [0, 0, 0.1]
    # transportation per month
    v = by_id["transport-monthly-max"]
    assert v["transport_cost"] == 1500 * v["months"]


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_python_mirror_reproduces_golden(vec):
    r = si.season_income(inputs_from_golden(vec["inputs"])).as_dict()
    for k, want in vec["values"].items():
        assert r[k] == want, (k, r[k], want)
    for k, want in vec["dollars"].items():
        assert si.round_half_up(r[k]) == want, (k, r[k], want)
    assert si.to_fixed_half_up(r["effective_rate"] * 100, 1) == vec["effective_rate_pct_1dp"]
    assert si.to_fixed_half_up(r["months"], 1) == vec["months_1dp"]
    assert r["verdict"] == vec["verdict"]


def test_private_day_tie_rounds_up():
    vec = next(v for v in VECTORS if v["id"] == "private-days-tie-2.5")
    assert vec["values"]["private_days_raw"] == 2.5 and vec["values"]["private_days"] == 3
    assert round(2.5) == 2  # why the mirror never uses Python's round()


# ---------------------------------------------------------- rounding rules
@pytest.mark.parametrize("x,want", [
    (2.5, 3), (3.5, 4), (-2.5, -2), (-0.5, 0), (0.49999999999999994, 0), (1e15 + 0.5, 1e15 + 1),
    (8.25, 8), (-7.75, -8), (0.0, 0),
])
def test_round_half_up(x, want):
    assert si.round_half_up(x) == want


def test_round_half_up_signed_zero_and_non_finite():
    assert math.copysign(1, si.round_half_up(-0.3)) == -1
    assert math.copysign(1, si.round_half_up(0.3)) == 1
    assert si.round_half_up(math.inf) == math.inf and math.isnan(si.round_half_up(math.nan))


@pytest.mark.parametrize("x,d,want", [
    (0.25, 1, "0.3"), (0.35, 1, "0.3"), (2.5, 0, "3"), (1.005, 2, "1.00"), (-0.04, 1, "-0.0"),
    (-0.0, 1, "0.0"), (-2.25, 1, "-2.3"), (4.157043879907621, 1, "4.2"), (22.076203845499404, 1, "22.1"),
])
def test_to_fixed_half_up(x, d, want):
    assert si.to_fixed_half_up(x, d) == want


def test_js_division_semantics():
    assert si._div(1.0, 0.0) == math.inf and si._div(-1.0, 0.0) == -math.inf
    assert math.isnan(si._div(0.0, 0.0)) and si._div(1.0, 4.0) == 0.25


# ------------------------------------------------------------- tax tables
@pytest.mark.parametrize("g,want", [
    (0, 0), (11600, 1160), (47150, 5426), (100525, 17168.5), (100526, 17168 + 0.24),
    (191950, 39110), (250000, 39110 + (250000 - 191950) * 0.32), (11600.5, 1160 + 0.5 * 0.12),
])
def test_federal_tax_brackets(g, want):
    assert si.federal_tax(g) == pytest.approx(want, abs=1e-9)


def _inputs(**over) -> si.SeasonInputs:
    base = dict(state_rate=0.044, weeks=18, days_per_week=5, hours_per_day=7, group_rate=22, private_rate=38,
                private_pct=30, private_length_hours=2, tips_per_day=25, rent_monthly=150, life_monthly=800,
                transport=150, gear=300)
    return si.SeasonInputs(**{**base, **over})


def test_private_day_rest_paid_at_group_rate():
    r = si.season_income(_inputs())  # 90 days, 27 private days of 2 private hours in a 7 hour day
    assert r.private_days == 27 and r.group_days == 63
    assert r.private_hours == 27 * 2 and r.group_hours == 63 * 7 + 27 * (7 - 2)
    assert r.group_income == r.group_hours * 22 and r.private_income == 54 * 38


def test_private_longer_than_day_adds_no_group_hours():
    r = si.season_income(_inputs(hours_per_day=4, private_length_hours=8, private_pct=50))
    assert r.group_hours == r.group_days * 4 and r.private_hours == r.private_days * 8


def test_federal_tax_after_standard_deduction():
    assert si.STANDARD_DEDUCTION == 14600
    for wages, fed_in in ((0, 0), (14599, 0), (14600, 0), (14601, 1), (40000, 25400)):
        r = si.season_income(_inputs(weeks=1, days_per_week=1, hours_per_day=1, private_pct=0, group_rate=wages,
                                     tips_per_day=0))
        assert r.taxable == wages and r.federal_taxable == fed_in
        assert r.federal_tax == si.federal_tax(fed_in)
        assert r.state_tax == wages * 0.044 and r.fica == wages * si.FICA_RATE  # no deduction for these


def test_transport_is_monthly():
    r = si.season_income(_inputs(transport=200))
    assert r.transport_cost == 200 * r.months and r.months == 18 / si.WEEKS_PER_MONTH
    assert r.total_expenses == r.housing_cost + r.life_cost + r.transport_cost + 300


def test_js_max_semantics():
    assert si._js_max(0.0, -3.0) == 0 and si._js_max(0.0, 2.5) == 2.5
    assert math.copysign(1, si._js_max(0.0, -0.0)) == 1 and math.copysign(1, si._js_max(-0.0, 0.0)) == 1
    assert math.isnan(si._js_max(0.0, math.nan)) and math.isnan(si._js_max(math.nan, 0.0))


def test_federal_bracket_bases_as_published_in_calculator():
    # Each base is the tax at the bracket start, except 17168: the 22% bracket ends at 17168.5,
    # so tax drops by $0.50 just above $100,525. Kept as is: the live calculator computes it this way.
    b = si.FEDERAL_BRACKETS
    assert [x["base"] for x in b] == [0, 1160, 5426, 17168, 39110]
    assert b[2]["base"] + (b[2]["up_to"] - b[2]["over"]) * b[2]["rate"] == 17168.5


def test_state_tax_rate_lookup():
    assert si.state_tax_rate("CO") == 0.044 and si.state_tax_rate("Colorado") == 0.044
    assert si.state_tax_rate("WA") == 0.0
    assert si.state_tax_rate("PA") is None


def test_page_state_menu_matches_shared_table():
    html = (REPO / "income-calculator" / "index.html").read_text()
    opts = re.findall(r'<option value="([0-9.]+)" data-name="([^"]+)"', html)
    assert [(n, float(v)) for v, n in opts] == [(s["name"], float(s["rate"])) for s in si.STATE_TAX_RATES]


def test_page_uses_shared_module():
    html = (REPO / "income-calculator" / "index.html").read_text()
    assert re.search(r"<script type=\"module\">\s*//[^\n]*\n\s*import \{[^}]*seasonIncome[^}]*\} from "
                     r"'/assets/js/season-income\.mjs", html)
    assert "function federalTax" not in html and "0.0765" not in html and "4.33" not in html
    assert "14600" not in html and "Math.max(0, hpd" not in html
    # main's accessibility additions stay: aria-valuetext on every slider, kept in step by sv()
    assert "setAttribute('aria-valuetext'" in html and "window.sv = sv" in html and "window.calc = calc" in html


# ------------------------------------------------------------ JS parity
@pytest.fixture(scope="module")
def js_dump():
    if NODE is None:
        pytest.skip("node is not installed")
    out = subprocess.run([NODE, str(HERE / "parity.mjs"), "--dump"], capture_output=True, text=True,
                         check=True, cwd=REPO)
    return json.loads(out.stdout)


@needs_node
def test_js_parity_script_passes():
    out = subprocess.run([NODE, str(HERE / "parity.mjs")], capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(f"OK {len(VECTORS)} golden vectors")


@needs_node
def test_tables_match_js(js_dump):
    t = js_dump["tables"]
    py_brackets = [{"over": b["over"], "upTo": b["up_to"], "base": b["base"], "rate": b["rate"]}
                   for b in si.FEDERAL_BRACKETS]
    assert [{k: _num(v) for k, v in b.items()} for b in t["federal_brackets"]] == py_brackets
    assert t["fica_rate"] == si.FICA_RATE and t["weeks_per_month"] == si.WEEKS_PER_MONTH
    assert t["standard_deduction"] == si.STANDARD_DEDUCTION
    assert t["state_tax_rates"] == [dict(s) for s in si.STATE_TAX_RATES]
    assert t["verdict_thresholds"] == si.VERDICT_THRESHOLDS


@needs_node
def test_every_js_result_field_matches_python(js_dump):
    by_id = {v["id"]: v for v in VECTORS}
    for row in js_dump["vectors"]:
        py = si.season_income(inputs_from_golden(by_id[row["id"]]["inputs"])).as_dict()
        assert set(map(_camel, py)) == set(row["result"])
        for k, v in py.items():
            assert _same(v, _num(row["result"][_camel(k)])), (row["id"], k)


@needs_node
def test_adapter_matches_js(js_dump):
    for case, row in zip(ADAPTER_CASES, js_dump["adapter"], strict=True):
        inputs = si.lucrativeness_inputs(**case["inputs"])
        for k, v in inputs.__dict__.items():
            assert _same(v, _num(row["inputs"][_camel(k)])), (case["id"], k)
        for k, v in si.season_income(inputs).as_dict().items():
            assert _same(v, _num(row["result"][_camel(k)])), (case["id"], k)


@needs_node
def test_rounding_helpers_match_js():
    xs = [2.5, -2.5, 0.25, 0.35, 1.005, -0.04, 8.25, 1234.5, -1234.5, 0.049999999999999996, 99.95, 6759.97131408776]
    script = ("import { roundHalfUp, toFixedHalfUp } from './assets/js/season-income.mjs';"
              f"const xs = {json.dumps(xs)};"
              "console.log(JSON.stringify(xs.map((x) => [roundHalfUp(x), toFixedHalfUp(x, 0), toFixedHalfUp(x, 1), toFixedHalfUp(x, 2)])));")
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         check=True, cwd=REPO)
    for x, (r, f0, f1, f2) in zip(xs, json.loads(out.stdout), strict=True):
        assert si.round_half_up(x) == r
        assert [si.to_fixed_half_up(x, d) for d in (0, 1, 2)] == [f0, f1, f2], x


# --------------------------------------------- lucrativeness adapter (map)
@pytest.mark.parametrize("case", ADAPTER_CASES, ids=[c["id"] for c in ADAPTER_CASES])
def test_adapter_reproduces_lucrativeness_definition(case):
    """gross = earnings and net_before_tax = mid, per docs/map/ARCHITECTURE.md Phase 4 lucrativeness."""
    a = case["inputs"]
    r = si.season_income(si.lucrativeness_inputs(**a))
    earnings = a["pay_hourly_mid_usd"] * a["hours_per_week"] * a["season_length_days"] / 7
    costs = (a["rent_monthly_usd"] + a["food_monthly_usd"] + a["transport_monthly_usd"]) * a["season_months"] \
        + a["pass_offset_usd"] + a["gear_offset_usd"]
    assert r.gross == pytest.approx(earnings, rel=1e-12, abs=1e-9)
    assert r.total_expenses == pytest.approx(costs, rel=1e-12, abs=1e-9)
    assert r.net_before_tax == pytest.approx(earnings - costs, rel=1e-12, abs=1e-9)
    assert r.tip_income == 0 and r.private_income == 0 and r.months == a["season_months"]
    # after tax figures use the calculator's federal, state and FICA model
    assert r.net == pytest.approx(r.net_before_tax - r.total_tax, abs=1e-6)
    assert r.state_tax == pytest.approx(r.taxable * a["state_rate"])
