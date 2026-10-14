"""Tests for pipeline/jobs_feed.py (live postings from public job board APIs).

The sandbox cannot reach the APIs, so every test uses recorded responses in tests/pipeline/fixtures/jobs_feed
(synthetic data in each vendor's documented response shape)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from pipeline import jobs_feed as jf
from pipeline.config import load_config
from pipeline.schemas.jobs import PostingsFile

REPO = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "jobs_feed"
CFG = load_config(REPO / "config").definitions.jobs
NOW = dt.datetime(2026, 10, 13, 6, 0, tzinfo=dt.timezone.utc)
API_HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.smartrecruiters.com", "apply.workable.com", "api.ashbyhq.com"}


def emp(eid, name, kind, vendor, board, careers):
    return {"id": eid, "name": name, "kind": kind, "careers_url": careers, "ats": {"vendor": vendor, "board": board},
            "source_id": "x", "as_of": "2026-10-13", "confidence": "estimated", "note": None}


EMPLOYERS = [
    emp("fixture-parent", "Fixture Parent", "parent_company", "greenhouse", "fixtureparent", "https://careers.fixture-parent.example.com/"),
    emp("solo-club", "Solo Club", "club", "lever", "soloclub", "https://www.soloclub.example.org/jobs"),
    emp("smart-co", "Smart Co", "resort_operator", "smartrecruiters", "SmartCo", "https://careers.smartrecruiters.com/SmartCo"),
    emp("work-co", "Work Co", "resort_operator", "workable", "workco", "https://apply.workable.com/workco/"),
    emp("ash-co", "Ash Co", "resort_operator", "ashby", "ashco", "https://jobs.ashbyhq.com/ashco"),
    emp("wd-co", "Workday Co", "resort_operator", "workday", "WdCo", "https://wdco.wd1.myworkdayjobs.com/WdCo"),
    emp("broken-co", "Broken Co", "resort_operator", "greenhouse", "brokenco", "https://www.broken.example.com/jobs"),
    emp("unlinked-co", "Unlinked Co", "resort_operator", "lever", "unlinked", "https://www.unlinked.example.com/jobs"),
]


def resort(rid, name, *employer_ids):
    return {"id": rid, "name": name, "jobs": {"feed": True, "links": [
        {"employer_id": e, "role_group": "all", "label": "Jobs", "url": "https://example.com/jobs"} for e in employer_ids]}}


RESORTS = [
    resort("alpha-peak", "Alpha Peak", "fixture-parent", "solo-club", "wd-co"),
    resort("bravo-bowl", "Bravo Bowl Ski Resort", "fixture-parent"),
    resort("charlie-ridge", "Charlie Ridge", "smart-co", "work-co", "broken-co"),
    resort("delta-hill", "Delta Hill", "ash-co"),
    {"id": "echo-basin", "name": "Echo Basin", "jobs": None},
]


def run_collect(previous=None, now=NOW, getter=None):
    get = getter or jf.FixtureGetter(FIX)
    return jf.collect(EMPLOYERS, RESORTS, CFG, get, now, previous), get


def by_id(doc):
    return {p["id"]: p for p in doc["postings"]}


def test_collect_filters_guesses_roles_and_assigns_resorts():
    doc, get = run_collect()
    PostingsFile.model_validate(doc)
    p = by_id(doc)
    # keyword filter (title, department or team); duplicates and off domain urls dropped
    assert set(p) == {"greenhouse-1001", "greenhouse-1002", "greenhouse-1003", "greenhouse-1005", "greenhouse-1007",
                      "lever-aaaa-1111", "smartrecruiters-7440001", "smartrecruiters-7440003", "workable-ABC123", "ashby-b1"}
    assert p["greenhouse-1001"]["title"] == "Ski Instructor (Full Time)"
    # role groups: management first, then coaching, then ski school; parent company fallback is corporate
    assert {k: v["role_group"] for k, v in p.items()} == {
        "greenhouse-1001": "ski_school", "greenhouse-1002": "management", "greenhouse-1003": "management",
        "greenhouse-1005": "coaching", "greenhouse-1007": "ski_school", "lever-aaaa-1111": "coaching",
        "smartrecruiters-7440001": "ski_school", "smartrecruiters-7440003": "ski_school", "workable-ABC123": "ski_school",
        "ashby-b1": "ski_school"}
    # multi resort employer: location first, then title; the employer's own name never matches
    assert p["greenhouse-1001"]["resort_ids"] == ["alpha-peak"]
    assert p["greenhouse-1002"]["resort_ids"] == ["bravo-bowl"]          # "Bravo Bowl" without "Ski Resort"
    assert p["greenhouse-1005"]["resort_ids"] == ["alpha-peak"]          # named in the title
    assert p["greenhouse-1003"]["resort_ids"] == [] and p["greenhouse-1007"]["resort_ids"] == []
    # single resort employers give every kept posting to their resort
    assert p["lever-aaaa-1111"]["resort_ids"] == ["alpha-peak"] and p["ashby-b1"]["resort_ids"] == ["delta-hill"]
    # published data citing the board page, dated by the fetch
    gh = p["greenhouse-1001"]
    assert gh["confidence"] == "published" and gh["as_of"] == "2026-10-13" and gh["fetched_at"] == "2026-10-13T06:00:00Z"
    assert gh["source_id"] == "jobs-board-greenhouse-fixtureparent"
    src = {s["id"]: s for s in doc["sources"]}
    assert src["jobs-board-greenhouse-fixtureparent"]["url"] == "https://boards.greenhouse.io/fixtureparent"
    assert p["smartrecruiters-7440001"]["url"] == "https://jobs.smartrecruiters.com/SmartCo/7440001"
    assert p["smartrecruiters-7440001"]["location"] == "Charlie, CO, United States"
    # boards: only public API vendors with a linked employer; a failing board is reported
    boards = {b["employer_id"]: b for b in doc["boards"]}
    assert set(boards) == {"fixture-parent", "solo-club", "smart-co", "work-co", "ash-co", "broken-co"}
    assert (boards["fixture-parent"]["total"], boards["fixture-parent"]["matched"]) == (8, 5)
    assert boards["smart-co"]["total"] == 3 and boards["broken-co"]["status"] == "error"
    assert boards["broken-co"]["error"] == "HTTP 503" and boards["broken-co"]["fetched_at"] is None
    # never a Workday, iCIMS, Indeed or Glassdoor request: only the five public APIs
    from urllib.parse import urlparse
    assert {urlparse(u).hostname for u in get.requested} <= API_HOSTS
    assert not any("myworkdayjobs" in u or "unlinked" in u for u in get.requested)


def test_failed_board_keeps_its_last_postings_for_max_age_days():
    first, _ = run_collect()
    calls = {"n": 0}

    def down(url):
        calls["n"] += 1
        raise jf.FeedError("timeout")

    later = NOW + dt.timedelta(days=CFG.feed.max_age_days - 1)
    doc, _ = run_collect(previous=first, now=later, getter=down)
    assert len(doc["postings"]) == len(first["postings"])
    assert all(b["status"] == "error" and b["error"] == "timeout" for b in doc["boards"])
    gh = next(b for b in doc["boards"] if b["employer_id"] == "fixture-parent")
    assert gh["fetched_at"] == "2026-10-13T06:00:00Z" and gh["matched"] == 5      # the last good fetch
    assert by_id(doc)["greenhouse-1001"]["as_of"] == "2026-10-13"
    PostingsFile.model_validate(doc)
    too_late = NOW + dt.timedelta(days=CFG.feed.max_age_days)
    doc, _ = run_collect(previous=first, now=too_late, getter=down)
    assert doc["postings"] == [] and all(b["fetched_at"] is None for b in doc["boards"])
    PostingsFile.model_validate(doc)


def test_should_write_only_on_change_or_after_refresh_days():
    first, _ = run_collect()
    again, _ = run_collect(previous=first, now=NOW + dt.timedelta(days=1))
    assert jf.should_write(first, None, CFG.feed.refresh_days, NOW)
    assert not jf.should_write(again, first, CFG.feed.refresh_days, NOW + dt.timedelta(days=1))
    assert jf.should_write(again, first, CFG.feed.refresh_days, NOW + dt.timedelta(days=CFG.feed.refresh_days))
    changed = json.loads(json.dumps(again))
    changed["postings"].pop()
    assert jf.should_write(changed, first, CFG.feed.refresh_days, NOW + dt.timedelta(days=1))


def test_run_writes_postings_json_and_cli(tmp_path, capsys):
    build = tmp_path / "data" / "build"
    (build / "jobs").mkdir(parents=True)
    shutil.copytree(REPO / "config", tmp_path / "config")
    (build / "jobs" / "employers.json").write_text(json.dumps({"version": 1, "generated_at": "x", "employers": EMPLOYERS}))
    (build / "resorts.json").write_text(json.dumps({"version": 1, "generated_at": "x", "resorts": RESORTS}))
    written, doc = jf.run(tmp_path, jf.FixtureGetter(FIX), now=NOW)
    assert written and json.loads((build / "jobs" / "postings.json").read_text()) == doc
    written, _ = jf.run(tmp_path, jf.FixtureGetter(FIX), now=NOW + dt.timedelta(hours=24))
    assert not written   # same postings, fetched within refresh_days: no change, nothing to commit
    assert jf.main(["fetch", "--root", str(tmp_path), "--fixtures", str(FIX), "--force"]) == 0
    out = capsys.readouterr().out
    assert "fixture-parent (greenhouse fixtureparent): 5 of 8 kept" in out and "broken-co" in out and "wrote" in out
    assert jf.main(["fetch", "--root", str(tmp_path / "nowhere")]) == 1


def test_text_helpers():
    assert jf.normalize("Ski & Ride School, Lead!") == "ski and ride school lead"
    pat = jf.word_pattern(["race", "ski school"])
    assert pat.search(jf.normalize("Race Coach")) and pat.search(jf.normalize("Ski-School Supervisor"))
    assert not pat.search(jf.normalize("Embrace the Racetrack"))
    assert jf.word_pattern([]) is None
    assert jf.resort_terms("Big Sky Resort") == ["big sky", "big sky resort"]
    assert jf.resort_terms("Vail", ["Lionshead"]) == ["lionshead", "vail"]
    assert jf.resort_terms("Ski") == []   # too short to match safely
    assert jf.api_url("smartrecruiters", "A B", 100) == "https://api.smartrecruiters.com/v1/companies/A%20B/postings?limit=100&offset=100"


def test_http_getter_reads_https_only():
    with pytest.raises(jf.FeedError):
        jf.http_get_json("http://boards-api.greenhouse.io/v1/boards/x/jobs")


def test_unexpected_response_shapes_fail_the_board_only():
    doc, _ = run_collect(getter=lambda url: {"unexpected": True})
    assert doc["postings"] == [] and all(b["status"] == "error" for b in doc["boards"])


def test_require_keywords_limit_a_broad_employer_board():
    """A college board lists every campus job; only snowsports ones reach the ski area's page."""
    cfg = CFG.model_copy(deep=True)
    cfg.feed.require_keywords = {"work-co": ["snow bowl"]}
    get = jf.FixtureGetter(FIX)
    doc = jf.collect(EMPLOYERS, RESORTS, cfg, get, NOW)
    assert "workable-ABC123" not in by_id(doc)   # "Snow Sports Instructor" in Charlie names no Snow Bowl
    cfg.feed.require_keywords = {"work-co": ["charlie"]}
    doc = jf.collect(EMPLOYERS, RESORTS, cfg, jf.FixtureGetter(FIX), NOW)
    assert "workable-ABC123" in by_id(doc)       # the location counts
