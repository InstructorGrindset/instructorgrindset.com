"""Phase 4 career formulas. Weights and assumptions here are synthetic and test only."""
import copy
import datetime as dt

import pytest
from pydantic import ValidationError

from pipeline.career import (
    add_months, build_career, cheapest_employee_rent, is_stale, lucrativeness, professionalism, select_pay,
)
from pipeline.config import LucrativenessDefs, ProfessionalismDefs
from pipeline.schemas import CareerBatch, CareerInputs, PayBatch, PayRecord, Resort
from pipeline.schemas.career import Career

TODAY = dt.date(2026, 10, 11)
PRIORITY = ["published", "reported", "estimated"]
CTX = {"source_ids": {"src-a", "src-b", "src-c"}}
VOCAB = {"client_profiles": ["destination_families", "race"], "enthusiast_types": ["powder"], "professional_types": []}

# synthetic, test only
WEIGHTS = {"cert_pay_differential": 2, "paid_training_hours": 1, "examiner_trainer_presence": 1,
           "private_lesson_share": 1, "year_round_staff": 0.5, "clinic_access": 0.5}
ASSUMPTIONS = {"cert_level": "level_2", "hours_per_week": 40, "season_months": 5, "housing_choice": "cheapest employee housing",
               "food_monthly_usd": 400, "transport_monthly_usd": 100, "pass_offset_usd": 0, "gear_offset_usd": 500}


def pay(**kw):
    base = {"resort_id": "r", "season": "2025-26", "cert_level": "level_2", "hourly_low": 20, "hourly_high": 24,
            "confidence": "published", "source_id": "src-a", "verified_date": "2026-01-15"}
    return PayRecord.model_validate({**base, **kw}, context=CTX)


def sv(value, source="src-a", as_of="2026-09-01", **kw):
    return {"value": value, "source_id": source, "as_of": as_of, "confidence": "published", **kw}


def prof_defs(weights=WEIGHTS, min_components=3):
    return ProfessionalismDefs(scale_min=0, scale_max=100, weights=weights, min_components=min_components)


def luc_defs(assumptions=ASSUMPTIONS, low=0.1, high=0.05):
    return LucrativenessDefs(assumptions=assumptions, range_spread={"low": low, "high": high})


# ------------------------------------------------------------------ schemas
def test_pay_record_validation():
    with pytest.raises(ValidationError, match="hourly_low"):
        pay(hourly_low=30)
    with pytest.raises(ValidationError):
        pay(cert_level="L2")
    with pytest.raises(ValidationError, match="2025-26"):
        pay(season="2025-27")
    with pytest.raises(ValidationError, match="not defined"):
        pay(source_id="nowhere")
    with pytest.raises(ValidationError, match="source_id"):
        PayRecord.model_validate({"resort_id": "r", "season": "2025-26", "cert_level": "level_2", "hourly_low": 1,
                                  "hourly_high": 2, "confidence": "published", "verified_date": "2026-01-01"})
    with pytest.raises(ValidationError, match="aggregate_count"):
        pay(confidence="reported")
    with pytest.raises(ValidationError, match="only to reported"):
        pay(aggregate_count=3)
    assert pay(confidence="reported", aggregate_count=3).aggregate_count == 3


def test_career_inputs_validation():
    ctx = {**CTX, "career_vocab": VOCAB}
    ok = {"resort_id": "r", "private_rate": sv({"low": 400, "high": 600, "unit": "full_day"}),
          "private_split": sv(40), "professionalism_inputs": {"clinic_access": sv(0.5)},
          "client_profile_tags": ["race"], "enthusiast_types": ["powder"]}
    c = CareerInputs.model_validate(ok, context=ctx)
    assert c.private_rate.value.unit == "full_day"
    bad = [
        ({"client_profile_tags": ["jet_set"]}, "unknown client_profiles"),
        ({"professional_types": ["coach"]}, "unknown professional_types"),
        ({"client_profile_tags": ["race", "race"]}, "duplicate"),
        ({"private_split": sv(140)}, "between 0 and 100"),
        ({"professionalism_inputs": {"clinic_access": sv(1.5)}}, "between 0 and 1"),
        ({"private_rate": sv({"low": 600, "high": 400, "unit": "hour"})}, "low must not exceed"),
        ({"private_rate": sv({"low": 1, "high": 2, "unit": "week"})}, "unit"),
        ({"private_split": sv(40, source="nowhere")}, "not defined"),
        ({"professionalism_inputs": {"vibes": sv(0.5)}}, "Extra inputs"),
    ]
    for patch, msg in bad:
        with pytest.raises(ValidationError, match=msg):
            CareerInputs.model_validate({**ok, **patch}, context=ctx)
    with pytest.raises(ValidationError, match="duplicate resort_id"):
        CareerBatch.model_validate({"batch": "b", "researched_date": "2026-10-11", "resorts": [ok, ok]}, context=ctx)
    PayBatch.model_validate({"batch": "b", "researched_date": "2026-10-11", "records": []})


# ---------------------------------------------------------------- staleness
def test_staleness_boundary_exactly_18_months():
    assert add_months(dt.date(2025, 4, 11), 18) == TODAY
    assert not is_stale(dt.date(2025, 4, 11), TODAY, 18)   # exactly 18 months: current
    assert is_stale(dt.date(2025, 4, 10), TODAY, 18)       # one day more: stale
    assert not is_stale(dt.date(2025, 4, 12), TODAY, 18)
    assert add_months(dt.date(2024, 8, 31), 18) == dt.date(2026, 2, 28)  # end of month clamp


# -------------------------------------------------------------- pay display
def test_pay_selection_latest_season_then_confidence():
    rows, warnings = select_pay([
        pay(season="2024-25", hourly_low=30, hourly_high=30),                         # older season, published
        pay(season="2025-26", confidence="estimated", hourly_low=18, hourly_high=19, source_id="src-b"),
        pay(season="2025-26", confidence="reported", aggregate_count=3, hourly_low=21, hourly_high=23, source_id="src-c"),
        pay(cert_level="level_1", hourly_low=17, hourly_high=19),
        pay(cert_level="examiner", season="2023-24", hourly_low=35, hourly_high=40, verified_date="2024-01-01"),
    ], PRIORITY, 3, TODAY, 18)
    assert warnings == []
    assert [r["cert_level"] for r in rows] == ["level_1", "level_2", "examiner"]  # contract order
    l2 = rows[1]
    assert (l2["confidence"], l2["hourly_low"], l2["season"]) == ("reported", 21, "2025-26")
    assert rows[2]["stale"] is True and rows[1]["stale"] is False
    assert l2 == {"cert_level": "level_2", "hourly_low": 21, "hourly_high": 23, "confidence": "reported",
                  "source_id": "src-c", "as_of": "2026-01-15", "season": "2025-26", "stale": False}


def test_pay_selection_ties():
    # same season and confidence: latest verified_date wins
    rows, _ = select_pay([pay(verified_date="2026-01-01", hourly_low=10, hourly_high=10),
                          pay(verified_date="2026-02-01", hourly_low=11, hourly_high=11)], PRIORITY, 3, TODAY, 18)
    assert rows[0]["hourly_low"] == 11
    # full tie: source_id, then values, in any input order
    a, b = pay(source_id="src-b", hourly_low=12, hourly_high=12), pay(source_id="src-a", hourly_low=13, hourly_high=13)
    assert select_pay([a, b], PRIORITY, 3, TODAY, 18)[0] == select_pay([b, a], PRIORITY, 3, TODAY, 18)[0]
    assert select_pay([a, b], PRIORITY, 3, TODAY, 18)[0][0]["source_id"] == "src-a"
    # the configured priority decides confidence
    rows, _ = select_pay([pay(confidence="estimated", source_id="src-b"), pay()], ["estimated", "published", "reported"],
                         3, TODAY, 18)
    assert rows[0]["confidence"] == "estimated"


def test_reported_row_needs_aggregation_min():
    two = pay(confidence="reported", aggregate_count=2, hourly_low=25, hourly_high=26)
    three = pay(confidence="reported", aggregate_count=3, hourly_low=25, hourly_high=26)
    rows, warnings = select_pay([two], PRIORITY, 3, TODAY, 18)
    assert rows == [] and "fewer than 3" in warnings[0]
    rows, warnings = select_pay([three], PRIORITY, 3, TODAY, 18)
    assert rows[0]["confidence"] == "reported" and warnings == []
    rows, _ = select_pay([two, pay(confidence="estimated")], PRIORITY, 3, TODAY, 18)
    assert rows[0]["confidence"] == "estimated"


# ---------------------------------------------------------- professionalism
def inputs(**components):
    return CareerInputs.model_validate({"resort_id": "r", "professionalism_inputs": {k: sv(v) for k, v in components.items()}},
                                       context=CTX)


def test_professionalism_computed_with_missing_components():
    out = professionalism(inputs(cert_pay_differential=1.0, paid_training_hours=0.5, clinic_access=0.0), prof_defs())
    # weights present: 2 + 1 + 0.5 = 3.5; sum = 2*1 + 1*0.5 + 0.5*0 = 2.5
    assert out["status"] == "computed"
    assert out["score"] == round(100 * 2.5 / 3.5, 1) == 71.4
    by = {b["component"]: b for b in out["breakdown"]}
    assert len(out["breakdown"]) == 6
    assert by["cert_pay_differential"] == {"component": "cert_pay_differential", "weight": 2, "value": 1.0,
                                           "points": round(100 * 2 / 3.5, 2), "source_id": "src-a"}
    assert by["clinic_access"]["points"] == 0
    assert by["year_round_staff"] == {"component": "year_round_staff", "weight": 0.5, "value": None, "points": None,
                                      "source_id": None}
    assert sum(b["points"] or 0 for b in out["breakdown"]) == pytest.approx(out["score"], abs=0.05)


def test_professionalism_all_components():
    out = professionalism(inputs(**{k: 1.0 for k in WEIGHTS}), prof_defs())
    assert out["score"] == 100.0


def test_professionalism_weights_unset():
    unset = dict(WEIGHTS, clinic_access=None)
    out = professionalism(inputs(cert_pay_differential=1.0, paid_training_hours=1, clinic_access=1), prof_defs(unset))
    assert out["status"] == "weights_unset" and out["score"] is None
    assert all(b["points"] is None for b in out["breakdown"])
    assert next(b for b in out["breakdown"] if b["component"] == "cert_pay_differential")["value"] == 1.0
    assert professionalism(inputs(cert_pay_differential=1.0), prof_defs(min_components=None))["status"] == "weights_unset"
    assert professionalism(None, prof_defs())["status"] == "insufficient_data"


def test_professionalism_insufficient_data():
    out = professionalism(inputs(cert_pay_differential=1.0, paid_training_hours=0.5), prof_defs(min_components=3))
    assert out["status"] == "insufficient_data" and out["score"] is None
    zero = dict(WEIGHTS, cert_pay_differential=0, paid_training_hours=0, examiner_trainer_presence=0)
    out = professionalism(inputs(cert_pay_differential=1, paid_training_hours=1, examiner_trainer_presence=1), prof_defs(zero))
    assert out["status"] == "insufficient_data"


def test_professionalism_config_validation():
    with pytest.raises(ValidationError, match="negative"):
        prof_defs(dict(WEIGHTS, clinic_access=-1))
    with pytest.raises(ValidationError, match="all be zero"):
        prof_defs({k: 0 for k in WEIGHTS})


# ------------------------------------------------------------ lucrativeness
L2_ROW = {"cert_level": "level_2", "hourly_low": 20, "hourly_high": 24}


def test_lucrativeness_arithmetic():
    out = lucrativeness([L2_ROW], 140, 800, luc_defs())
    earnings = 22 * 40 * 140 / 7                       # 17600
    costs = (800 + 400 + 100) * 5 + 0 + 500            # 7000
    assert out["status"] == "computed"
    assert out["mid"] == round(earnings - costs) == 10600
    assert out["low"] == round(10600 - 0.1 * earnings) == 8840
    assert out["high"] == round(10600 + 0.05 * earnings) == 11480


def test_lucrativeness_range_spread():
    out = lucrativeness([L2_ROW], 140, 800, luc_defs(low=0, high=0))
    assert out["low"] == out["mid"] == out["high"]
    out = lucrativeness([L2_ROW], 140, 3500, luc_defs(low=0.2, high=0.2))  # negative savings stay ordered
    assert out["mid"] == round(17600 - (3500 + 500) * 5 - 500) == -2900
    assert out["mid"] < 0 and out["low"] < out["mid"] < out["high"]


def test_lucrativeness_uses_assumed_cert_level():
    rows = [L2_ROW, {"cert_level": "level_3", "hourly_low": 30, "hourly_high": 30}]
    l3 = lucrativeness(rows, 140, 800, luc_defs(dict(ASSUMPTIONS, cert_level="level_3")))
    assert l3["mid"] == round(30 * 40 * 20 - 7000)
    assert lucrativeness([L2_ROW], 140, 800, luc_defs(dict(ASSUMPTIONS, cert_level="level_3")))["status"] == "insufficient_data"


def test_lucrativeness_assumptions_unset_and_echo():
    out = lucrativeness([L2_ROW], 140, 800, luc_defs(dict(ASSUMPTIONS, food_monthly_usd=None)))
    assert out["status"] == "assumptions_unset" and out["mid"] is None
    assert lucrativeness([L2_ROW], 140, 800, luc_defs(high=None))["status"] == "assumptions_unset"
    keys = [a["key"] for a in out["assumptions"]]
    assert keys == ["cert_level", "hours_per_week", "season_months", "housing_choice", "food_monthly_usd",
                    "transport_monthly_usd", "pass_offset_usd", "gear_offset_usd", "range_spread_low",
                    "range_spread_high", "pay_hourly_mid_usd", "season_length_days", "rent_monthly_usd"]
    by = {a["key"]: a for a in out["assumptions"]}
    assert by["food_monthly_usd"]["value"] is None and by["hours_per_week"]["value"] == 40
    assert by["pay_hourly_mid_usd"]["value"] == 22 and by["rent_monthly_usd"]["value"] == 800
    assert all(a["label"] and "-" not in a["label"] for a in out["assumptions"])


def test_lucrativeness_insufficient_data():
    assert lucrativeness([], 140, 800, luc_defs())["status"] == "insufficient_data"
    assert lucrativeness([L2_ROW], 140, None, luc_defs())["status"] == "insufficient_data"
    assert lucrativeness([L2_ROW], None, 800, luc_defs())["status"] == "insufficient_data"


def test_real_config_is_unset(cfg):
    out = lucrativeness([L2_ROW], 140, 800, cfg.definitions.lucrativeness)
    assert out["status"] == "assumptions_unset"
    assert professionalism(inputs(cert_pay_differential=1.0), cfg.definitions.professionalism)["status"] == "weights_unset"


def test_cheapest_employee_rent():
    class Site:
        def __init__(self, resort_id, type, rent):
            self.resort_id, self.type, self.rent_monthly_low = resort_id, type, rent
    sites = [Site("r", "market", 500), Site("r", "resort_owned", 900), Site("r", "master_lease", 700),
             Site("other", "resort_owned", 100), Site("r", "resort_owned", None)]
    assert cheapest_employee_rent(sites, "r") == 700
    assert cheapest_employee_rent(sites, "none") is None


# ----------------------------------------------------------------- assembly
def test_build_career_none_without_data(cfg):
    assert build_career([], None, 140, 800, cfg.definitions, TODAY) == (None, [])


def test_build_career_output_validates(cfg):
    defs = cfg.definitions.model_copy(deep=True)
    defs.professionalism = prof_defs()
    defs.lucrativeness = luc_defs()
    ci = CareerInputs.model_validate({
        "resort_id": "r", "private_rate": sv({"low": 400, "high": 600, "unit": "full_day"}, as_of="2024-01-01"),
        "private_split": sv(40),
        "professionalism_inputs": {"cert_pay_differential": sv(1.0), "paid_training_hours": sv(0.5, as_of="2025-04-10"),
                                   "clinic_access": sv(0.0)}}, context=CTX)
    career, warnings = build_career([pay(), pay(cert_level="level_1", verified_date="2025-01-01")], ci, 140, 800,
                                    defs, TODAY)
    assert warnings == []
    assert career["stale_fields"] == ["pay.level_1", "private_rate", "professionalism.paid_training_hours"]
    assert career["lucrativeness"]["mid"] == 10600 and career["professionalism"]["score"] == 71.4
    model = Career.model_validate(career, context=CTX)
    assert model.pay[1].cert_level == "level_2"
    # and inside a Resort, the career source ids must be listed
    resort = {"id": "r", "name": "R", "tier": "B", "lat": 1, "lon": 1, "location_source_id": "src-b",
              "cluster_id": "c", "state": "CO", "career": career, "sources": ["src-a", "src-b"]}
    Resort.model_validate(resort, context=CTX)
    with pytest.raises(ValidationError, match="missing referenced"):
        Resort.model_validate({**resort, "sources": ["src-b"]}, context=CTX)


def test_career_output_status_rules():
    base = {"pay": [], "professionalism": {"score": None, "status": "weights_unset", "breakdown": []},
            "lucrativeness": {"status": "assumptions_unset", "assumptions": []}}
    Career.model_validate(base)
    bad = copy.deepcopy(base)
    bad["professionalism"]["score"] = 50
    with pytest.raises(ValidationError, match="exactly when status is computed"):
        Career.model_validate(bad)
    bad = copy.deepcopy(base)
    bad["lucrativeness"] = {"low": 3, "mid": 2, "high": 1, "status": "computed", "assumptions": []}
    with pytest.raises(ValidationError, match="low <= mid <= high"):
        Career.model_validate(bad)
    row = {"cert_level": "level_2", "hourly_low": 1, "hourly_high": 2, "confidence": "published", "source_id": "src-a",
           "as_of": "2026-01-01", "season": "2025-26", "stale": False}
    with pytest.raises(ValidationError, match="one row per cert_level"):
        Career.model_validate({**base, "pay": [row, row]})
