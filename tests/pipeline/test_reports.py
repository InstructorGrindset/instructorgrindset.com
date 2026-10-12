import datetime as dt
import json

import pytest
import yaml
from pydantic import ValidationError

from pipeline.reports import (
    ReportStore, aggregate, import_export, load_aggregates, reported_rent, row_to_report, write_aggregates,
)
from pipeline.schemas import Report

T0 = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)


def pay_row(i, hourly, **kw):
    return {"resort_id": "fixture-peak", "kind": "pay", "season": "2025-26", "cert_level": "level_2",
            "hourly_usd": hourly, "submitted_at": f"2026-10-0{i}T10:00:00Z", **kw}


def report(**kw):
    base = {"id": "r-1", "resort_id": "fixture-peak", "kind": "pay", "season": "2025-26", "cert_level": "level_2",
            "values": {"hourly_usd": 22}, "submitted_at": "2026-10-01T10:00:00Z"}
    return Report.model_validate({**base, **kw})


def test_report_schema():
    assert report().status == "pending"
    bad = [
        ({"cert_level": None}, "cert_level"),
        ({"values": {"monthly_usd": 900}}, "hourly_usd only"),
        ({"kind": "rent"}, "monthly_usd only"),
        ({"kind": "rent", "cert_level": None, "values": {"monthly_usd": 900}, "housing_site_id": "s1"}, "must not have one"),
        ({"kind": "housing", "cert_level": None, "values": {"monthly_usd": 900}}, "need housing_site_id"),
        ({"status": "approved"}, "moderated_at"),
        ({"status": "rejected", "moderated_at": "2026-10-02T00:00:00Z"}, "moderation_note"),
        ({"moderated_at": "2026-10-02T00:00:00Z"}, "pending report has no moderated_at"),
        ({"season": "2025"}, "2025-26"),
        ({"values": {"hourly_usd": 0}}, "greater than 0"),
        ({"proof_url": "ftp://x"}, "pattern"),
        ({"surprise": 1}, "Extra inputs"),
    ]
    for patch, msg in bad:
        with pytest.raises(ValidationError, match=msg):
            report(**patch)
    report(kind="housing", cert_level=None, values={"monthly_usd": 900}, housing_site_id="s1")


def test_submissions_flow_through_moderation(tmp_path):
    store = ReportStore(tmp_path)
    export = tmp_path / "export.json"
    export.write_text(json.dumps([pay_row(1, 20), pay_row(2, 24, proof_url="https://example.org/stub"),
                                  pay_row(3, 22, status="approved"),          # imports always start pending
                                  {"resort_id": "fixture-peak", "kind": "pay", "season": "2025-26"},   # invalid
                                  pay_row(4, 21, resort_id="ghost")]))
    added, problems = import_export(export, store, {"fixture-peak"})
    assert added == 3 and len(problems) == 2 and "unknown resort_id 'ghost'" in problems[1]
    pending, errors = store.pending()
    assert len(pending) == 3 and errors == [] and {r.status for r in pending} == {"pending"}
    added, problems = import_export(export, store, {"fixture-peak"})
    assert added == 0 and sum("already exists" in p for p in problems) == 3  # idempotent re import
    ids = sorted(r.id for r in pending)
    a = store.moderate(ids[0], "approved", now=T0)
    assert a.status == "approved" and a.moderated_at == T0
    store.moderate(ids[1], "rejected", "proof does not match", now=T0)
    with pytest.raises(ValidationError, match="moderation_note"):
        store.moderate(ids[2], "rejected", None, now=T0)
    with pytest.raises(LookupError):
        store.moderate("r-missing", "approved")
    assert [r.id for r in store.pending()[0]] == [ids[2]]
    decided, _ = store.decided()
    assert sorted((r.id, r.status) for r in decided) == [(ids[0], "approved"), (ids[1], "rejected")]
    assert not (tmp_path / "queue" / f"{ids[0]}.yaml").exists()
    assert yaml.safe_load((tmp_path / "moderated" / f"{ids[1]}.yaml").read_text())["moderation_note"] == "proof does not match"


def test_csv_import(tmp_path):
    store = ReportStore(tmp_path)
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("resort_id,kind,season,cert_level,housing_site_id,hourly_usd,monthly_usd,proof_url,submitted_at\n"
                        "fixture-peak,rent,2025-26,,,,950,,2026-10-01T10:00:00Z\n"
                        "fixture-peak,housing,2025-26,,staff-lodge,,700,,2026-10-01T11:00:00Z\n"
                        "fixture-peak,pay,2025-26,level_1,,19.5,,,2026-10-01T12:00:00Z\n")
    added, problems = import_export(csv_path, store)
    assert (added, problems) == (3, [])
    kinds = sorted(r.kind for r in store.pending()[0])
    assert kinds == ["housing", "pay", "rent"]
    r = row_to_report({"resort_id": "fixture-peak", "kind": "rent", "season": "2025-26", "monthly_usd": "900",
                       "submitted_at": "2026-10-01T10:00:00Z"})
    assert r.id.startswith("r-") and r.values.monthly_usd == 900


def approved(n, kind="pay", values=None, status="approved", **kw):
    out = []
    for i in range(n):
        v = (values or [20 + i for i in range(n)])[i]
        extra = {"cert_level": kw.get("cert_level", "level_2"), "values": {"hourly_usd": v}} if kind == "pay" else \
            {"cert_level": None, "values": {"monthly_usd": v}}
        rid = "-".join(x for x in ("r", kind, status, str(i), kw.get("housing_site_id"), kw.get("season", "").replace("-", ""),
                                   kw.get("cert_level", "")) if x).replace("_", "")
        out.append(report(id=rid, kind=kind, status=status,
                          moderated_at=(T0 + dt.timedelta(days=i)).isoformat() if status != "pending" else None,
                          moderation_note="note" if status == "rejected" else None, **extra,
                          **{k: v for k, v in kw.items() if k != "cert_level"}))
    return out


def test_three_reports_give_a_range_two_give_nothing():
    three = aggregate(approved(3, values=[19, 25, 22]), 3, "2026-10-12T00:00:00Z")
    assert three["pay"] == [{"resort_id": "fixture-peak", "season": "2025-26", "cert_level": "level_2",
                             "hourly_low": 19, "hourly_high": 25, "confidence": "reported",
                             "source_id": "instructorgrindset-reports", "verified_date": "2026-10-03",
                             "aggregate_count": 3, "note": "aggregate of 3 approved reports"}]
    assert three["source"]["id"] == "instructorgrindset-reports" and three["source"]["accessed_date"] == "2026-10-03"
    two = aggregate(approved(2), 3, "2026-10-12T00:00:00Z")
    assert two["pay"] == [] and two["source"] is None


def test_pending_and_rejected_never_count():
    reports = approved(2) + approved(2, status="rejected") + approved(3, status="pending")
    assert aggregate(reports, 3, "x")["pay"] == []
    assert aggregate(approved(3) + approved(5, status="rejected"), 3, "x")["pay"][0]["aggregate_count"] == 3


def test_groups_are_separate():
    reports = approved(3) + approved(2, cert_level="level_3")  # level_3 has only 2 reports
    out = aggregate(reports, 3, "x")
    assert [p["cert_level"] for p in out["pay"]] == ["level_2"]
    rents = approved(3, kind="rent", values=[900, 1100, 1000]) + \
        approved(3, kind="housing", values=[600, 650, 700], housing_site_id="staff-lodge") + \
        approved(2, kind="housing", values=[500, 550], housing_site_id="town-flats")
    out = aggregate(rents, 3, "x")
    assert out["rent"] == [{"resort_id": "fixture-peak", "season": "2025-26", "low": 900, "high": 1100, "count": 3,
                            "verified_date": "2026-10-03"}]
    assert [h["housing_site_id"] for h in out["housing"]] == ["staff-lodge"]


def test_aggregates_file_has_no_individual_values(tmp_path):
    data = aggregate(approved(3, values=[19, 25, 22]), 3, "2026-10-12T00:00:00Z")
    path = tmp_path / "aggregates.json"
    assert write_aggregates(path, data) and not write_aggregates(path, {**data, "generated_at": "later"})
    text = path.read_text()
    assert "r-pay" not in text and "22" not in text and "submitted_at" not in text


def test_load_aggregates_latest_season_and_reported_rent():
    rows = approved(3, kind="rent", values=[900, 1100, 1000]) + \
        approved(3, kind="rent", values=[1000, 1200, 1300], season="2026-27")
    agg = load_aggregates(aggregate(rows, 3, "x"))
    assert agg.rent["fixture-peak"]["season"] == "2026-27"
    r = reported_rent(agg.rent["fixture-peak"])
    assert r == {"value": {"low": 1000, "high": 1300}, "source_id": "instructorgrindset-reports", "as_of": "2026-10-03",
                 "confidence": "reported", "note": "range of 3 approved reports, season 2026-27"}
    assert reported_rent(None) is None and load_aggregates(None).pay == {}


def test_cli(tmp_path, monkeypatch, capsys):
    import pipeline.reports as rep
    monkeypatch.setattr(rep, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(rep, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rep.ReportStore, "__init__", lambda self, root=tmp_path / "reports": setattr(self, "root", root))
    export = tmp_path / "e.json"
    export.write_text(json.dumps({"reports": [pay_row(1, 20), pay_row(2, 21), pay_row(3, 23)]}))
    assert rep.main(["import", str(export)]) == 0
    assert rep.main(["list"]) == 0 and "3 pending" in capsys.readouterr().out
    ids = sorted(p.stem for p in (tmp_path / "reports" / "queue").glob("*.yaml"))
    for i in ids:
        assert rep.main(["approve", i, "--note", "ok"]) == 0
    assert rep.main(["reject", "r-missing", "--note", "x"]) == 1
    assert rep.main(["aggregate"]) == 0
    data = json.loads((tmp_path / "reports" / "aggregates.json").read_text())
    assert data["pay"][0]["hourly_low"] == 20 and data["pay"][0]["aggregate_count"] == 3
