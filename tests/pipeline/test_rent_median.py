"""Median rent across sources (pipeline/rent_median.py) and the lucrativeness rent order (pipeline/rent_used.py).

SYNTHETIC TEST FIXTURES: every town, publisher page and rent below is invented. Never copy to data/.
"""
import datetime as dt
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline import rent_median as rm
from pipeline.career import build_career
from pipeline.rent_used import as_inputs, rent_used
from pipeline.schemas.rent import MedianRentBatch, RentUsed
from pipeline.sources import SourceRegistry

TODAY = dt.date(2026, 10, 14)
REPO = Path(__file__).resolve().parents[2]
URLS = {
    "zum": "https://www.zumper.com/rent-research/peakville",
    "rto": "https://assets.rentometer.com/peakville",
    "pad": "https://www.padmapper.com/peakville",
    "red": "https://www.redfin.com/peakville",
    "fc": "https://www.foreclosure.com/peakville",
    "fc2": "https://www.foreclosure.com/other-town",
    "sfr": "https://sfranalytics.com/peakville-metro",
    "blog": "https://www.peakvilledaily.example/rents",
}


def fig(src, value, unit="all", geography="city", town="Peakville, CO", period="2026-08", resorts=("peak",),
        name=None, confidence="estimated"):
    return rm.MedianRentFigure.model_validate({
        "resort_ids": list(resorts), "town": town, "geography": geography, "unit": unit, "median_rent_usd": value,
        "period": period, "publisher": "other", "publisher_name": name, "source_id": src,
        "as_of": "2026-10-14", "confidence": confidence})


def data(*figs):
    return rm.MedianRentData(figures=list(figs), urls=dict(URLS), titles={k: f"Title {k}" for k in URLS})


@pytest.fixture
def defs(cfg):
    return cfg.definitions.rent.median


def run(d, defs, rid="peak"):
    return rm.median_rent_for(rid, d, TODAY, defs)[0]


# --------------------------------------------------------------------- median
def test_median_odd_and_even_counts():
    assert rm.median([3]) == 3
    assert rm.median([3000, 1000, 2000]) == 2000
    assert rm.median([1000, 4000, 2000, 3000]) == 2500   # the mean of the two middle values
    with pytest.raises(ValueError):
        rm.median([])


def test_cross_source_median_odd_and_even(defs):
    b = run(data(fig("zum", 2400), fig("rto", 2000), fig("pad", 9000)), defs)
    assert (b["value"], b["count"], b["publishers"]) == (2400, 3, ["Zumper", "Rentometer", "PadMapper"])
    assert b["source_ids"] == ["zum", "rto", "pad"] and b["note"].startswith("median of 3 sources")
    b = run(data(fig("zum", 2400), fig("rto", 2000), fig("pad", 9000), fig("red", 2100)), defs)
    assert b["value"] == 2250 and b["count"] == 4   # outliers stay as published; the median absorbs them


def test_single_source(defs):
    b = run(data(fig("zum", 2400)), defs)
    assert b["count"] == 1 and b["note"].startswith("single source (Zumper)")
    RentUsed.model_validate({**b, "label": "Median rent"}, context={"source_ids": set(URLS)})


# -------------------------------------------------------------- unit and level
def test_unit_preference_all_then_one_bedroom(defs):
    d = data(fig("zum", 2400, unit="1br"), fig("rto", 3000, unit="all"), fig("pad", 9000, unit="2br"))
    b = run(d, defs)
    assert (b["value"], b["unit"], b["source_ids"]) == (3000, "all", ["rto"])
    b = run(data(fig("zum", 2400, unit="1br"), fig("rto", 1800, unit="1br"), fig("pad", 9000, unit="2br")), defs)
    assert (b["value"], b["unit"], b["count"]) == (2100, "1br", 2)   # 1br recorded; 2br never used
    assert run(data(fig("pad", 9000, unit="2br"), fig("zum", 9900, unit="3br")), defs) is None


def test_best_geography_level_only_never_mixed(defs):
    d = data(fig("sfr", 4400, geography="metro", town="Peakville, CO micro area (Fixture County)"),
             fig("zum", 2400), fig("rto", 1600, geography="county", town="Fixture County, CO"))
    b = run(d, defs)
    assert (b["value"], b["geography"]) == (2400, {"level": "city", "id": None, "name": "Peakville, CO"})
    d = data(fig("sfr", 4400, geography="metro", town="Bozeman, MT metro (Gallatin County)"),
             fig("rto", 1600, unit="2br"))
    b = run(d, defs)
    assert b["geography"] == {"level": "metro", "id": None, "name": "Bozeman metro"} and b["value"] == 4400
    d = data(fig("zum", 2400), fig("pad", 1500, geography="zip", town="Peakville, CO"))
    assert run(d, defs)["geography"]["level"] == "zip"   # zip comes first


def test_place_names():
    assert rm.place_name([fig("sfr", 1, geography="metro", town="Peakville, CO micro area (Fixture County)")], "metro") \
        == "Peakville micro area"
    assert rm.place_name([fig("zum", 1, town="Anchorage, AK (municipality, includes Girdwood)")], "city") \
        == "Municipality of Anchorage"
    assert rm.place_name([fig("zum", 1, town="Stowe, VT"), fig("fc", 1, town="Morrisville, VT")], "city") \
        == "Stowe, VT and Morrisville, VT"


def test_one_figure_per_source_page(defs):
    b = run(data(fig("zum", 2400), fig("zum", 2600, town="Peakville West, CO"), fig("rto", 2000)), defs)
    assert b["source_ids"] == ["zum", "rto"] and b["value"] == 2200


# ---------------------------------------------------------------- staleness
def test_figures_older_than_18_months_are_ignored(defs):
    d = data(fig("zum", 2400, period="2025-03"), fig("rto", 2000, period="2025-04"), fig("pad", 2600, period="2026"))
    b, ignored = rm.median_rent_for("peak", d, TODAY, defs)
    # 2025-03 ended 2025-03-31, more than 18 months before 2026-10-14; 2025-04-30 plus 18 months is 2026-10-30
    assert ignored == 1 and b["source_ids"] == ["rto", "pad"] and b["value"] == 2300
    assert b["period"] == {"first": "2025-04", "last": "2026"}
    assert rm.period_end("2026-02") == dt.date(2026, 2, 28) and rm.period_end("2025") == dt.date(2025, 12, 31)
    assert rm.median_rent_for("peak", data(fig("zum", 2400, period="2024-01")), TODAY, defs) == (None, 1)


# ----------------------------------------------------------- last resort rule
def test_foreclosure_only_when_no_other_publisher_has_the_town_at_that_level(defs):
    # another publisher has a two bedroom figure for Peakville at city level: the Foreclosure.com figure is dropped
    d = data(fig("fc", 1920), fig("pad", 3000, unit="2br"))
    assert run(d, defs) is None
    # the only figure for its town and level: kept, and shown as a single source
    b = run(data(fig("fc", 1920)), defs)
    assert (b["value"], b["publishers"], b["count"]) == (1920, ["Foreclosure.com"], 1)
    # another publisher at a different level does not suppress it; the better level (zip) wins
    d = data(fig("fc", 1920, geography="zip"), fig("zum", 2400))
    assert run(d, defs)["publishers"] == ["Foreclosure.com"]
    # another publisher for another town does not suppress it either (another resort's figure here)
    d = data(fig("fc", 1920), fig("zum", 2400, town="Elsewhere, CO", resorts=("other",)))
    assert run(d, defs)["value"] == 1920
    # with another usable figure at the same level, both towns count
    d = data(fig("fc2", 1500, town="Other Town, CO"), fig("zum", 2500))
    assert run(d, defs)["value"] == 2000


def test_publisher_comes_from_the_source_domain(defs):
    names = defs.publishers
    assert rm.publisher_label(fig("rto", 1), URLS, names) == "Rentometer"     # assets.rentometer.com
    assert rm.publisher_label(fig("sfr", 1), URLS, names) == "SFR Analytics"
    assert rm.publisher_label(fig("blog", 1, name="Peakville Daily"), URLS, names) == "Peakville Daily"
    assert rm.publisher_label(fig("blog", 1), URLS, names) == "peakvilledaily.example"
    assert rm.registered_domain("https://www.foreclosure.com/x") == "foreclosure.com"


# ------------------------------------------------------------------- schema
def _batch(**over):
    f = {"resort_ids": ["peak"], "town": "Peakville, CO", "geography": "city", "unit": "all", "median_rent_usd": 2400,
         "period": "2026-08", "publisher": "zumper", "source_id": "zum", "as_of": "2026-10-14", "confidence": "estimated"}
    return {"batch": "b", "researched_date": "2026-10-14",
            "sources": [{"id": "zum", "url": URLS["zum"], "title": "Zumper", "accessed_date": "2026-10-14"}],
            "figures": [{**f, **over}]}


def test_schema_rules():
    ctx = {"source_ids": {"zum"}}
    MedianRentBatch.model_validate(_batch(), context=ctx)
    for over, msg in (({"confidence": "reported"}, "aggregates of 3 or more"), ({"period": "Aug 2026"}, "YYYY"),
                      ({"source_id": "ghost"}, "not defined"), ({"unit": "4br"}, "unit"),
                      ({"median_rent_usd": 0}, "greater than 0"), ({"extra": 1}, "Extra inputs")):
        with pytest.raises(ValidationError, match=msg):
            MedianRentBatch.model_validate(_batch(**over), context=ctx)
    assert MedianRentBatch.model_validate(_batch(period=2026), context=ctx).figures[0].period == "2026"


def test_loader_reports_unknown_resorts(tmp_path):
    (tmp_path / "rent").mkdir()
    (tmp_path / "rent" / "median_rent_test.yaml").write_text(yaml.safe_dump(_batch(resort_ids=["peak", "ghost"])))
    d, errors = rm.load_median_rent(tmp_path, SourceRegistry(), {"peak"})
    assert len(d.figures) == 1 and d.urls == {"zum": URLS["zum"]}
    assert errors and "resort_id 'ghost' matches no resort" in errors[0]


def test_the_committed_research_file_loads_and_follows_the_rules(cfg):
    path = REPO / "data" / "raw" / "rent" / "median_rent_2026_10.yaml"
    if not path.exists():
        pytest.skip("no median rent research file")
    from pipeline.tier_a import load_raw
    reg = SourceRegistry()
    ids = {r.id for _, r in load_raw(REPO / "data" / "raw", reg, REPO).records}
    d, errors = rm.load_median_rent(REPO / "data" / "raw", reg, ids)
    assert errors == [] and d.figures
    defs = cfg.definitions.rent.median
    usable = rm.usable_figures(d, TODAY, defs)
    others = {(f.town, f.geography) for f in usable if rm.publisher_of(f, d.urls) != "foreclosure.com"}
    for f in usable:   # every Foreclosure.com figure kept is the only publisher for its town at its level
        if rm.publisher_of(f, d.urls) == "foreclosure.com":
            assert (f.town, f.geography) not in others
    cov = rm.coverage(d, sorted(ids), TODAY, defs)
    assert cov and all(b["unit"] in ("all", "1br") and b["count"] >= 1 for b in cov.values())


# ------------------------------------------------------------ the rent order
ORDER = ["median_rent", "zillow_zori", "acs_median", "fmr_one_br"]
MEDIAN = {"value": 2400.0, "basis": "median_rent", "source_ids": ["zum"], "as_of": "2026-10-14", "confidence": "estimated",
          "note": "single source", "unit": "all", "count": 1, "publishers": ["Zumper"],
          "period": {"first": "2026-08", "last": "2026-08"}, "geography": {"level": "city", "id": None, "name": "Peakville, CO"},
          "stale": False}
ZORI = {"value": 2190.0, "source_ids": ["zillow-zori"], "as_of": "2026-08-31", "confidence": "published",
        "note": "ZORI is a typical market rent", "geography": {"level": "county", "id": "08998", "name": "Fixture County, CO"},
        "trailing_mean": 2100.0}
BASELINE = {"county_fips": "08998", "county_name": "Fixture County",
            "fmr": {"studio": None, "one_br": {"value": 1640, "source_id": "hud-fmr-fy2026", "as_of": "2026-10-02",
                                               "confidence": "published", "note": "HUD"}, "two_br": None},
            "acs_median_gross_rent": {"value": 1950, "source_id": "census-acs5-2024-b25064", "as_of": "2026-10-03",
                                      "confidence": "published", "note": "ACS"}}


def test_rent_order_median_then_zori_then_acs_then_hud():
    kw = dict(median=MEDIAN, zori=ZORI, baseline=BASELINE, today=TODAY, max_age_months=18)
    b = rent_used(ORDER, **kw)
    assert (b["basis"], b["value"], b["label"]) == ("median_rent", 2400.0, "Median rent")
    b = rent_used(ORDER, **{**kw, "median": None})
    assert (b["basis"], b["value"], b["label"], b["confidence"]) == ("zillow_zori", 2190.0, "Zillow typical rent (ZORI)", "published")
    assert "trailing_mean" not in b and b["geography"]["name"] == "Fixture County, CO"
    b = rent_used(ORDER, **{**kw, "median": None, "zori": None})
    assert (b["basis"], b["value"], b["source_ids"]) == ("acs_median", 1950.0, ["census-acs5-2024-b25064"])
    assert b["geography"] == {"level": "county", "id": "08998", "name": "Fixture County"}
    no_acs = {**BASELINE, "acs_median_gross_rent": None}
    b = rent_used(ORDER, **{**kw, "median": None, "zori": None, "baseline": no_acs})
    assert (b["basis"], b["value"], b["label"]) == ("fmr_one_br", 1640.0, "HUD Fair Market Rent, one bedroom")
    assert rent_used(ORDER, today=TODAY) is None
    assert as_inputs(rent_used(ORDER, **kw)) == (2400.0, "median_rent", "zum") and as_inputs(None) == (None, None, None)
    for block in (rent_used(ORDER, **kw), rent_used(ORDER, **{**kw, "median": None})):
        RentUsed.model_validate(block, context={"source_ids": {"zum", "zillow-zori"}})


def test_employee_housing_no_longer_feeds_lucrativeness(cfg):
    from pipeline.schemas.housing import HousingSite
    site = HousingSite.model_validate({
        "id": "lodge", "resort_id": "peak", "name": "Lodge", "type": "resort_owned", "lat": 39.6, "lon": -106.3,
        "location_source_id": "zum", "precision": "neighborhood", "confidence": "published", "verified_date": "2026-10-01",
        "rent_monthly": {"value": {"low": 600, "high": 800}, "source_id": "zum", "as_of": "2026-10-01", "confidence": "published"}})
    order = cfg.definitions.rent.lucrativeness_rent
    assert "employee_housing" not in order
    assert rent_used(order, sites=[site], today=TODAY) is None
    b = rent_used(["employee_housing"], sites=[site], today=TODAY)   # still available as a configured step
    assert (b["basis"], b["value"], b["count"]) == ("employee_housing_mean:lodge", 700.0, 1)


def test_stale_zori_is_flagged_and_reaches_stale_fields(cfg):
    old = {**ZORI, "as_of": "2025-03-31"}
    b = rent_used(ORDER, zori=old, today=TODAY, max_age_months=18)
    assert b["stale"] is True
    assert rent_used(ORDER, zori={**ZORI, "as_of": "2025-04-30"}, today=TODAY, max_age_months=18)["stale"] is False
    from pipeline.schemas.career import PayRecord
    pay = PayRecord.model_validate({"resort_id": "peak", "season": "2025-26", "cert_level": "level_1", "hourly_low": 20,
                                    "hourly_high": 24, "confidence": "published", "source_id": "zum",
                                    "verified_date": "2026-01-15"})
    career, _ = build_career([pay], None, 152, b["value"], cfg.definitions, TODAY, b["basis"], "zillow-zori", rent=b)
    assert "lucrativeness.rent" in career["stale_fields"] and career["lucrativeness"]["rent"]["stale"] is True
    echo = {a["key"]: a for a in career["lucrativeness"]["assumptions"]}
    assert echo["rent_monthly_usd"]["label"] == "Zillow typical rent (ZORI) per month (USD)"


def test_lucrativeness_end_to_end_with_the_median_rent(cfg):
    from pipeline.schemas.career import Career, PayRecord
    pay = PayRecord.model_validate({"resort_id": "peak", "season": "2025-26", "cert_level": "level_2", "hourly_low": 20,
                                    "hourly_high": 24, "confidence": "published", "source_id": "zum",
                                    "verified_date": "2026-01-15"})
    d = data(fig("zum", 2400), fig("rto", 2000), fig("fc", 1920, town="Other Town, CO", resorts=("other",)))
    median, _ = rm.median_rent_for("peak", d, TODAY, cfg.definitions.rent.median)
    b = rent_used(cfg.definitions.rent.lucrativeness_rent, median=median, zori=ZORI, baseline=BASELINE,
                  today=TODAY, max_age_months=18)
    career, _ = build_career([pay], None, 152, b["value"], cfg.definitions, TODAY, *as_inputs(b)[1:], rent=b)
    luc = career["lucrativeness"]
    months = 152 / 30.4
    assert luc["status"] == "computed" and b["value"] == 2200
    assert luc["mid"] == round(24 * 40 * months * 4.33 - 2200 * months) and luc["low"] == round(20 * 40 * months * 4.33 - 2200 * months)
    assert {a["key"]: a for a in luc["assumptions"]}["rent_monthly_usd"]["label"] == "Median rent per month (USD)"
    Career.model_validate(career, context={"source_ids": {"zum", "rto"}})
