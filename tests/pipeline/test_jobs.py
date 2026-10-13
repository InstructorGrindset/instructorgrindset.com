"""Tests for pipeline/jobs.py (jobs links and employers). All data here is synthetic."""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.config import load_config
from pipeline.jobs import board_url, employers_output, load_jobs, resort_jobs
from pipeline.schemas.jobs import EmployersFile
from pipeline.sources import SourceRegistry

REPO = Path(__file__).resolve().parents[2]
JOBS_CFG = load_config(REPO / "config").definitions.jobs
ATS = {k: set(v) for k, v in JOBS_CFG.ats_domains.items()}
BLOCKED = set(JOBS_CFG.blocked_domains)
SOURCES = [
    {"id": "jobs-alpha", "url": "https://www.alpha.example.com/careers", "title": "Alpha careers", "accessed_date": "2026-10-13"},
    {"id": "jobs-parent", "url": "https://careers.parentco.example.com/", "title": "Parent Co careers", "accessed_date": "2026-10-13"},
    {"id": "jobs-club", "url": "https://www.alphaclub.example.org/jobs", "title": "Alpha club jobs", "accessed_date": "2026-10-13"},
]


def employer(eid="alpha-ops", **kw):
    base = {"id": eid, "name": "Alpha Peak Resort", "kind": "resort_operator",
            "careers_url": "https://www.alpha.example.com/careers", "ats": {"vendor": "workday"},
            "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "estimated"}
    return {**base, **kw}


PARENT = employer("parent-co", name="Parent Co", kind="parent_company", careers_url="https://careers.parentco.example.com/",
                  ats={"vendor": "greenhouse", "board": "parentco"}, source_id="jobs-parent")
CLUB = employer("alpha-club", name="Alpha Ski Club", kind="club", careers_url="https://www.alphaclub.example.org/jobs",
                ats={"vendor": "unknown"}, source_id="jobs-club")


def link(**kw):
    base = {"employer_id": "alpha-ops", "role_group": "ski_school", "label": "Ski school jobs",
            "url": "https://www.alpha.example.com/careers/ski-school", "source_id": "jobs-alpha",
            "as_of": "2026-10-13", "confidence": "estimated"}
    return {**base, **kw}


def run(tmp_path, records, employers=(employer(), PARENT, CLUB), name="batch.yaml", known=("alpha-peak", "bravo-bowl")):
    d = tmp_path / "jobs"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.safe_dump({"batch": "jobs-test", "researched_date": "2026-10-13", "sources": SOURCES,
                                          "employers": list(employers), "records": records}))
    return load_jobs(tmp_path, SourceRegistry(), set(known), ATS, rel=lambda p: p.name, blocked_domains=BLOCKED)


def test_missing_folder_gives_nothing(tmp_path):
    data, errors = load_jobs(tmp_path, SourceRegistry(), {"alpha-peak"}, ATS)
    assert errors == [] and data.employers == {} and data.links == {}


def test_links_group_in_order_and_feed_flag(tmp_path):
    links = [
        link(role_group="all", label="All jobs", url="https://www.alpha.example.com/careers"),
        link(employer_id="parent-co", role_group="corporate", label="Parent Co corporate jobs",
             url="https://boards.greenhouse.io/parentco?team=corporate", source_id="jobs-parent"),
        link(employer_id="alpha-club", role_group="coaching", disciplines=["race", "freeride"], label="Club coaches",
             url="https://www.alphaclub.example.org/jobs/coaches", source_id="jobs-club"),
        link(),
        link(role_group="management", label="Leadership roles", url="https://alpha.wd5.myworkdayjobs.com/en-US/careers"),
    ]
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": links}])
    assert errors == []
    out = resort_jobs(data, "alpha-peak")
    assert [x["role_group"] for x in out["links"]] == ["ski_school", "coaching", "management", "corporate", "all"]
    assert out["links"][1]["disciplines"] == ["race", "freeride"]
    assert out["links"][0] == {"employer_id": "alpha-ops", "role_group": "ski_school", "disciplines": [],
                               "label": "Ski school jobs", "url": "https://www.alpha.example.com/careers/ski-school",
                               "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "estimated", "note": None}
    assert out["feed"] is True   # parent-co has a Greenhouse board
    assert resort_jobs(data, "bravo-bowl") is None


def test_feed_is_false_without_a_public_board(tmp_path):
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}])
    assert errors == [] and resort_jobs(data, "alpha-peak")["feed"] is False


def test_employers_file_lists_each_employer_once_with_board_pages(tmp_path):
    data, _ = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}])
    out = employers_output(data, {"version": 1, "generated_at": "2026-10-13T00:00:00Z"})
    EmployersFile.model_validate(out)
    by = {e["id"]: e for e in out["employers"]}
    assert list(by) == ["alpha-club", "alpha-ops", "parent-co"]
    assert by["parent-co"]["feed"] is True and by["parent-co"]["board_url"] == "https://boards.greenhouse.io/parentco"
    assert by["alpha-ops"]["feed"] is False and by["alpha-ops"]["board_url"] is None
    assert by["alpha-club"]["ats"] == {"vendor": "unknown", "board": None}


def test_board_pages_per_vendor(tmp_path):
    from pipeline.schemas.jobs import Employer
    pages = {}
    for vendor in ("greenhouse", "lever", "smartrecruiters", "workable", "ashby", "workday"):
        e = Employer.model_validate(employer(ats={"vendor": vendor, "board": "acme"} if vendor != "workday" else {"vendor": vendor}))
        pages[vendor] = board_url(e)
    assert pages == {"greenhouse": "https://boards.greenhouse.io/acme", "lever": "https://jobs.lever.co/acme",
                     "smartrecruiters": "https://careers.smartrecruiters.com/acme",
                     "workable": "https://apply.workable.com/acme/", "ashby": "https://jobs.ashbyhq.com/acme",
                     "workday": None}


def test_link_domain_must_be_careers_ats_or_source_domain(tmp_path):
    other = {"employer_id": "alpha-ops", "url": "https://jobs.someboard.example.net/alpha", "source_id": "jobs-club"}
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link(**other)]}])
    assert any("url domain example.net is neither the careers domain, an ATS domain of 'alpha-ops' nor its source's"
               in e for e in errors), errors
    # Workday is alpha-ops' vendor, Greenhouse is not
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link(url="https://boards.greenhouse.io/alpha")]}])
    assert any("greenhouse.io" in e for e in errors)
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link(url="https://alpha.wd1.myworkdayjobs.com/x")]}])
    assert errors == []
    # a page on the employer's own site, cited by its own source, while the careers page sits on the ATS
    on_ats = employer(careers_url="https://alpha.wd1.myworkdayjobs.com/en-US/AlphaPeak")
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [
        link(url="https://www.alphaclub.example.org/jobs/international", source_id="jobs-club")]}], employers=(on_ats,))
    assert errors == []


def test_job_aggregators_always_fail(tmp_path):
    src = {"id": "jobs-indeed", "url": "https://www.indeed.com/q-alpha-jobs.html", "title": "Indeed", "accessed_date": "2026-10-13"}
    d = tmp_path / "jobs"
    d.mkdir()
    (d / "extra.yaml").write_text(yaml.safe_dump({"batch": "jobs-extra", "researched_date": "2026-10-13", "sources": [src]}))
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [
        link(url="https://www.indeed.com/q-alpha-jobs.html", source_id="jobs-indeed")]}])
    assert any("is on the job aggregator indeed.com" in e for e in errors), errors


def test_careers_url_may_sit_on_another_domain_than_its_source(tmp_path):
    """A parent company's home page may link to a careers site on its own domain (jobs.<brand>careers.com)."""
    other = employer(careers_url="https://jobs.alphacareers.example.net/")
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [
        link(url="https://jobs.alphacareers.example.net/alpha/ski-school")]}], employers=(other,))
    assert errors == [] and data.employers["alpha-ops"].careers_url == "https://jobs.alphacareers.example.net/"


def test_board_on_a_vendor_without_an_api_is_kept_but_never_fed(tmp_path):
    wd = employer(ats={"vendor": "workday", "board": "AlphaPeak"})
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}], employers=(wd,))
    assert errors == [] and resort_jobs(data, "alpha-peak")["feed"] is False
    assert board_url(data.employers["alpha-ops"]) is None


def test_rejects_unknown_ids_http_bad_enums_and_duplicates(tmp_path):
    cases = [
        ([{"resort_id": "nope", "links": [link()]}], "no such resort"),
        ([{"resort_id": "alpha-peak", "links": [link(employer_id="ghost")]}], "employer_id 'ghost' is not declared"),
        ([{"resort_id": "alpha-peak", "links": [link(source_id="missing")]}], "not defined in any sources list"),
        ([{"resort_id": "alpha-peak", "links": [link(url="http://www.alpha.example.com/careers/ski-school")]}], "https"),
        ([{"resort_id": "alpha-peak", "links": [link(role_group="lifties")]}], "role_group"),
        ([{"resort_id": "alpha-peak", "links": [link(disciplines=["telemark"])]}], "disciplines"),
        ([{"resort_id": "alpha-peak", "links": [link(disciplines=["race", "race"])]}], "discipline listed twice"),
        ([{"resort_id": "alpha-peak", "links": [link(), link()]}], "url listed twice in one role group"),
        ([{"resort_id": "alpha-peak", "links": [link()]}, {"resort_id": "alpha-peak", "links": [link()]}],
         "resort ids listed twice"),
        ([{"resort_id": "alpha-peak", "links": []}], "links"),
    ]
    for records, needle in cases:
        _, errors = run(tmp_path, records)
        assert any(needle in e for e in errors), (needle, errors)
    for bad, needle in ((employer(kind="lodge"), "kind"), (employer(ats={"vendor": "taleo"}), "vendor"),
                        (employer(ats={"vendor": "greenhouse", "board": "a b/../c"}), "plain token")):
        _, errors = run(tmp_path, [], employers=(bad,))
        assert any(needle in e for e in errors), (needle, errors)
    _, errors = run(tmp_path, [], employers=(employer(), employer()))
    assert any("employer ids listed twice" in e for e in errors)


def test_one_board_may_serve_several_role_groups(tmp_path):
    board = "https://www.alpha.example.com/careers"
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [
        link(role_group="all", label="All jobs", url=board),
        link(role_group="coaching", disciplines=["race"], label="Race coaching jobs", url=board)]}])
    assert errors == [] and [x["role_group"] for x in resort_jobs(data, "alpha-peak")["links"]] == ["coaching", "all"]


def test_files_merge_links_and_share_employers(tmp_path):
    run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}], name="a.yaml")
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [
        link(role_group="all", label="All jobs", url="https://www.alpha.example.com/careers")]}],
        employers=(employer(),), name="b.yaml")
    assert errors == [] and len(data.links["alpha-peak"]) == 2
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}], employers=(employer(),), name="b.yaml")
    assert any("is also listed in a.yaml" in e for e in errors)
    _, errors = run(tmp_path, [], employers=(employer(name="Another Name"),), name="b.yaml")
    assert any("declared with different details in a.yaml" in e for e in errors)


def test_committed_files_validate():
    """Every committed jobs file validates against the real build's resort ids (once research lands)."""
    import json
    build = REPO / "data" / "build" / "resorts.json"
    if not list((REPO / "data" / "raw" / "jobs").glob("*.yaml")) or not build.exists():
        return
    known = {r["id"] for r in json.loads(build.read_text())["resorts"]}
    registry = SourceRegistry()
    from pipeline.tier_a import _register_sources, _yaml_files
    for sub in ("sources", "facts", "pay", "career", "tier_a"):   # hiring windows cite facts and pay sources
        for p in _yaml_files(REPO / "data" / "raw" / sub):
            _register_sources(yaml.safe_load(p.read_text()), p.name, registry, [])
    _, errors = load_jobs(REPO / "data" / "raw", registry, known, ATS, rel=lambda p: p.name, blocked_domains=BLOCKED)
    assert errors == []


def test_hiring_windows_load_once_per_resort_and_reach_resort_jobs(tmp_path):
    hw = {"resort_id": "alpha-peak", "text": "Applications open August 15;  clinics start in November.",
          "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "estimated"}
    d = tmp_path / "jobs"
    d.mkdir()
    (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                    "hiring_windows": [hw]}))
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}])
    assert errors == []
    out = resort_jobs(data, "alpha-peak")
    # the text names no end and no year, so no months (pipeline/hiring.py)
    assert out["hiring_window"] == {"text": "Applications open August 15; clinics start in November.",
                                    "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "estimated", "note": None,
                                    "start_month": None, "end_month": None}
    # a window alone still gives Resort.jobs (no links yet)
    (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                    "hiring_windows": [{**hw, "resort_id": "bravo-bowl"}]}))
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}])
    assert errors == [] and resort_jobs(data, "bravo-bowl") == {"links": [], "feed": False, "hiring_window": {
        **{k: v for k, v in hw.items() if k != "resort_id"}, "text": "Applications open August 15; clinics start in November.",
        "note": None, "start_month": None, "end_month": None}}
    assert resort_jobs(data, "alpha-peak")["hiring_window"] is None
    for bad, needle in (({**hw, "resort_id": "nope"}, "no such resort"), ({**hw, "text": "Open now \u2014 apply"}, "em or en dashes"),
                        ({**hw, "source_id": "missing"}, "not defined"), ({**hw, "confidence": "sure"}, "confidence")):
        (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                        "hiring_windows": [bad]}))
        _, errors = run(tmp_path, [])
        assert any(needle in e for e in errors), (needle, errors)
    (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                    "hiring_windows": [hw, hw]}))
    _, errors = run(tmp_path, [])
    assert any("hiring window resort ids listed twice" in e for e in errors)
    (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                    "hiring_windows": [hw]}))
    (d / "more.yaml").write_text(yaml.safe_dump({"batch": "jobs-more", "researched_date": "2026-10-13",
                                                 "hiring_windows": [hw]}))
    _, errors = run(tmp_path, [])
    assert any("also has a hiring window in" in e for e in errors)


def test_hiring_window_months_reach_resort_jobs_with_their_citation(tmp_path):
    hw = {"resort_id": "alpha-peak", "text": "Instructor posting opened July 1, 2026 with a target start of December 1, 2026.",
          "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "published"}
    d = tmp_path / "jobs"
    d.mkdir()
    (d / "windows.yaml").write_text(yaml.safe_dump({"batch": "jobs-windows", "researched_date": "2026-10-13",
                                                    "hiring_windows": [hw]}))
    data, errors = run(tmp_path, [{"resort_id": "alpha-peak", "links": [link()]}])
    assert errors == []
    out = resort_jobs(data, "alpha-peak", JOBS_CFG.hiring_windows.max_months)["hiring_window"]
    assert out == {"text": hw["text"], "source_id": "jobs-alpha", "as_of": "2026-10-13", "confidence": "published",
                   "note": None, "start_month": "2026-07", "end_month": "2026-12"}
    # a shorter limit leaves the same text unparsed; the text and its citation stay
    short = resort_jobs(data, "alpha-peak", 3)["hiring_window"]
    assert (short["start_month"], short["end_month"], short["text"]) == (None, None, hw["text"])
    # the build schema accepts both, and rejects one month without the other or an end before the start
    from pydantic import ValidationError

    from pipeline.schemas.jobs import Jobs
    Jobs.model_validate({"links": [], "hiring_window": out})
    for bad in ({**out, "end_month": None}, {**out, "end_month": "2026-06"}, {**out, "start_month": "2026-13"}):
        try:
            Jobs.model_validate({"links": [], "hiring_window": bad})
        except ValidationError:
            continue
        raise AssertionError(bad)
