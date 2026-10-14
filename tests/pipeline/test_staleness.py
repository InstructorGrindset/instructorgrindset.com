import datetime as dt
import json

from pipeline.staleness import dated_values, render, stale_values, write_report

TODAY = dt.date(2026, 10, 12)


def sv(value, as_of, source="src-a"):
    return {"value": value, "source_id": source, "as_of": as_of, "confidence": "published"}


def test_dated_values_paths():
    resort = {"size": {"acres": sv(10, "2024-01-01"), "lifts": None},
              "career": {"pay": [{"cert_level": "level_2", "as_of": "2024-02-01", "source_id": "pay-a", "stale": True}]},
              "editorial": {"reviewed_date": "2020-01-01"}, "tier_a_criteria": [{"source_id": "x"}]}
    got = sorted(dated_values({k: v for k, v in resort.items() if k not in ("editorial", "tier_a_criteria")}))
    assert got == [("career.pay.level_2", dt.date(2024, 2, 1), "pay-a"), ("size.acres", dt.date(2024, 1, 1), "src-a")]


def test_stale_values_boundary_and_detail(tmp_path):
    resorts = [{"id": "a", "size": {"acres": sv(1, "2025-04-12"), "lifts": sv(2, "2025-04-11")}},
               {"id": "b", "size": {"acres": sv(1, "2026-01-01")}}]
    (tmp_path / "resorts.json").write_text(json.dumps({"resorts": resorts}))
    (tmp_path / "detail").mkdir()
    (tmp_path / "detail" / "a.json").write_text(json.dumps({"resort_id": "a", "housing": [
        {"id": "lodge", "verified_date": "2024-12-01", "location_source_id": "h-src",
         "rent_monthly": {"value": {"low": 1, "high": 2}, "source_id": "h-src", "as_of": "2024-12-01", "confidence": "published"}}],
        "rent_baseline": None, "rent_reported": None}))
    stale = stale_values(tmp_path, TODAY, 18)
    assert list(stale) == ["a"]  # exactly 18 months (2025-04-12) is current; b is recent
    fields = [f for f, _, _ in stale["a"]]
    assert fields == ["detail.housing.lodge.rent_monthly", "detail.housing.lodge.verified_date", "size.lifts"]
    md = render(stale, TODAY, 18)
    assert "3 stale values at 1 resorts." in md and "| size.lifts | 2025-04-11 | `src-a` |" in md
    assert write_report(tmp_path, tmp_path / "staleness.md", 18, TODAY) == 3


def test_empty_build(tmp_path):
    assert stale_values(tmp_path, TODAY, 18) == {}
    assert "0 stale values" in render({}, TODAY, 18)
