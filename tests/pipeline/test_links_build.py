"""Build integration for webcams and jobs: Resort.webcams, Resort.jobs, jobs/employers.json, jobs/postings.json.
All data here is synthetic (fixture resorts)."""
from __future__ import annotations

import json
import shutil

import pytest
import yaml

from pipeline.build import build
from pipeline.schemas import Resort
from pipeline.schemas.jobs import EmployersFile, PostingsFile
from pipeline.tier_a import BuildError

SOURCES = [
    {"id": "fx-cams", "url": "https://www.fixture-peak.example.com/cams", "title": "Fixture Peak cams", "accessed_date": "2026-10-13"},
    {"id": "fx-jobs", "url": "https://jobs.fixture-parent.example.com/", "title": "Fixture Parent jobs", "accessed_date": "2026-10-13"},
]
WEBCAMS = {"batch": "webcams-fixture", "researched_date": "2026-10-13", "sources": [SOURCES[0]], "records": [
    {"resort_id": "fixture-peak",
     "cams_page": {"url": "https://www.fixture-peak.example.com/cams", "source_id": "fx-cams", "as_of": "2026-10-13",
                   "confidence": "estimated"},
     "cams": [{"label": f"Cam {i}", "url": f"https://www.fixture-peak.example.com/cams/{i}", "kind": "stream",
               "source_id": "fx-cams"} for i in range(1, 7)]}]}
JOBS = {"batch": "jobs-fixture", "researched_date": "2026-10-13", "sources": [SOURCES[1]],
        "employers": [{"id": "fixture-parent", "name": "Fixture Parent", "kind": "parent_company",
                       "careers_url": "https://jobs.fixture-parent.example.com/", "ats": {"vendor": "lever", "board": "fixtureparent"},
                       "source_id": "fx-jobs", "as_of": "2026-10-13", "confidence": "published"}],
        "records": [{"resort_id": rid, "links": [
            {"employer_id": "fixture-parent", "role_group": "ski_school", "label": "Ski school jobs",
             "url": f"https://jobs.fixture-parent.example.com/{rid}/ski-school", "source_id": "fx-jobs",
             "as_of": "2026-10-13", "confidence": "published"},
            {"employer_id": "fixture-parent", "role_group": "coaching", "disciplines": ["race"], "label": "Race coaches",
             "url": f"https://jobs.lever.co/fixtureparent?team={rid}", "source_id": "fx-jobs",
             "as_of": "2026-10-13", "confidence": "published"}]} for rid in ("fixture-peak", "fixture-basin")]}


@pytest.fixture
def out(tmp_path):
    return tmp_path / "build"


def _run(repo_root, raw_dir, out, tmp_path):
    cfg_dir = tmp_path / "config"
    if not cfg_dir.exists():
        shutil.copytree(repo_root / "config", cfg_dir)
        p = cfg_dir / "definitions.yaml"
        data = yaml.safe_load(p.read_text())
        data["ownership"]["missing_tier_a_record"] = "warn"
        p.write_text(yaml.safe_dump(data, sort_keys=False))
    return build(repo_root, raw_dir=raw_dir, out_dir=out, report_path=tmp_path / "tier-a-draft.md",
                 states_geojson=False, config_dir=cfg_dir)


def _write(raw_dir, sub, data):
    (raw_dir / sub).mkdir(exist_ok=True)
    (raw_dir / sub / "fixture.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def test_build_without_link_folders_writes_empty_jobs_files(repo_root, raw_dir, out, tmp_path):
    _run(repo_root, raw_dir, out, tmp_path)
    resorts = json.loads((out / "resorts.json").read_text())["resorts"]
    assert all(r["webcams"] is None and r["jobs"] is None for r in resorts)
    emp = json.loads((out / "jobs" / "employers.json").read_text())
    assert emp["employers"] == [] and EmployersFile.model_validate(emp)
    post = json.loads((out / "jobs" / "postings.json").read_text())
    assert post["postings"] == [] and post["boards"] == [] and post["fetched_at"] is None
    assert PostingsFile.model_validate(post).keywords   # the configured keywords are recorded


def test_build_attaches_webcams_jobs_and_sources(repo_root, raw_dir, out, tmp_path):
    _write(raw_dir, "webcams", WEBCAMS)
    _write(raw_dir, "jobs", JOBS)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in json.loads((out / "resorts.json").read_text())["resorts"]}
    sources = {s["id"] for s in json.loads((out / "sources.json").read_text())["sources"]}
    peak = by["fixture-peak"]
    Resort.model_validate(peak, context={"source_ids": sources})
    assert peak["webcams"]["cams_page"]["url"] == "https://www.fixture-peak.example.com/cams"
    assert [c["label"] for c in peak["webcams"]["cams"]] == ["Cam 1", "Cam 2", "Cam 3", "Cam 4"]   # webcams.max_named_cams
    assert {"fx-cams", "fx-jobs"} <= set(peak["sources"]) and {"fx-cams", "fx-jobs"} <= sources
    assert [x["role_group"] for x in peak["jobs"]["links"]] == ["ski_school", "coaching"]
    assert peak["jobs"]["feed"] is True and by["fixture-basin"]["jobs"]["feed"] is True
    assert by["fixture-valley"]["jobs"] is None and by["fixture-valley"]["webcams"] is None
    emp = json.loads((out / "jobs" / "employers.json").read_text())["employers"]
    assert [e["id"] for e in emp] == ["fixture-parent"] and emp[0]["board_url"] == "https://jobs.lever.co/fixtureparent"


def test_build_keeps_a_feed_written_postings_file(repo_root, raw_dir, out, tmp_path):
    _write(raw_dir, "jobs", JOBS)
    feed = {"version": 1, "generated_at": "2026-10-13T06:00:00Z", "fetched_at": "2026-10-13T06:00:00Z",
            "keywords": ["instructor"],
            "boards": [{"employer_id": "fixture-parent", "employer_name": "Fixture Parent", "vendor": "lever",
                        "board": "fixtureparent", "url": "https://jobs.lever.co/fixtureparent", "source_id": "jobs-board-lever-fixtureparent",
                        "status": "ok", "fetched_at": "2026-10-13T06:00:00Z", "as_of": "2026-10-13", "total": 3, "matched": 1}],
            "sources": [{"id": "jobs-board-lever-fixtureparent", "url": "https://jobs.lever.co/fixtureparent",
                         "title": "Fixture Parent jobs on Lever", "accessed_date": "2026-10-13"}],
            "postings": [{"id": "lever-1", "employer_id": "fixture-parent", "title": "Ski Instructor", "location": "Fixture Peak",
                          "url": "https://jobs.lever.co/fixtureparent/1", "role_group": "ski_school",
                          "resort_ids": ["fixture-peak"], "source_id": "jobs-board-lever-fixtureparent",
                          "as_of": "2026-10-13", "fetched_at": "2026-10-13T06:00:00Z"}]}
    (out / "jobs").mkdir(parents=True)
    (out / "jobs" / "postings.json").write_text(json.dumps(feed))
    _run(repo_root, raw_dir, out, tmp_path)
    assert json.loads((out / "jobs" / "postings.json").read_text()) == feed
    feed["postings"][0]["url"] = "http://jobs.lever.co/fixtureparent/1"
    (out / "jobs" / "postings.json").write_text(json.dumps(feed))
    with pytest.raises(BuildError) as e:
        _run(repo_root, raw_dir, out, tmp_path)
    assert any("postings.json" in m and "https" in m for m in e.value.errors)


def test_bad_links_fail_the_build(repo_root, raw_dir, out, tmp_path):
    bad = json.loads(json.dumps(JOBS))
    bad["records"][0]["links"][0]["url"] = "https://www.indeed.com/q-fixture-jobs.html"
    _write(raw_dir, "jobs", bad)
    with pytest.raises(BuildError) as e:
        _run(repo_root, raw_dir, out, tmp_path)
    assert any("job aggregator indeed.com" in m or "neither the careers domain" in m for m in e.value.errors)
    _write(raw_dir, "jobs", JOBS)
    cams = json.loads(json.dumps(WEBCAMS))
    cams["records"][0]["resort_id"] = "no-such-resort"
    _write(raw_dir, "webcams", cams)
    with pytest.raises(BuildError) as e:
        _run(repo_root, raw_dir, out, tmp_path)
    assert any("no-such-resort" in m and "no such resort" in m for m in e.value.errors)
