"""Route stubs and the slim index for live cams and jobs (pipeline/routes.py). All data here is synthetic."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.routes import generate_routes, render_jobs, render_webcams, Ctx

from tests.pipeline.test_routes import _prerender, default_data, read, site, write_build  # noqa: F401 (fixture)

CITE = {"source_id": "test-src", "as_of": "2026-10-13", "confidence": "estimated"}
WEBCAMS = {"cams_page": {"url": "https://www.alpha.example.com/cams", **CITE, "note": None},
           "cams": [{"label": "Summit <cam>", "url": "https://www.alpha.example.com/cams/summit", "kind": "stream", **CITE},
                    {"label": "Bad", "url": "javascript:alert(1)", "kind": "page", **CITE}]}


def link(group, label, url, **kw):
    return {"employer_id": "alpha-ops", "role_group": group, "disciplines": [], "label": label, "url": url, **CITE, **kw}


JOBS = {"feed": False, "links": [
    link("all", "All jobs", "https://www.alpha.example.com/careers"),
    link("corporate", "Parent corporate jobs", "https://jobs.parent.example.com/corporate"),
    link("coaching", "Club coaches", "https://club.example.org/jobs", disciplines=["race", "freeride"]),
    link("ski_school", "Ski school jobs", "https://example.com/src"),
    link("management", "Leadership", "https://www.alpha.example.com/careers/leadership"),
]}


def test_resort_page_prerenders_live_cams_and_jobs(site: Path):
    resorts, clusters, states = default_data()
    resorts[0]["webcams"], resorts[0]["jobs"] = WEBCAMS, JOBS
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    body = _prerender(read(site, "co/front/alpha-peak"))
    assert ('<a class="live-cams-link" href="https://www.alpha.example.com/cams" target="_blank" rel="noopener noreferrer">'
            'Open live cams</a> <span class="badge badge-estimated">Estimated</span>') in body
    assert "alpha.example.com · opens in a new tab · Last verified Oct 13, 2026" in body
    assert ">Summit &lt;cam&gt;</a>" in body and "Live stream" in body and "javascript:" not in body
    # groups in the fixed order, with discipline tags and per link badges
    order = [body.index(f">{t}</h4>") for t in ("Ski school", "Coaching and clubs", "Management", "Parent company", "Job boards")]
    assert order == sorted(order)
    assert '<span class="tag">Race</span><span class="tag">Freeride</span>' in body
    assert body.count('class="job-link"') == 5 and body.count('target="_blank" rel="noopener noreferrer"') >= 7
    # the source link shows only when the source is another page than the link itself
    ski = body.split(">Ski school</h4>")[1].split("</li>")[0]
    assert "Last verified Oct 13, 2026</span>" in ski and "Test Source" not in ski
    lead = body.split(">Management</h4>")[1].split("</li>")[0]
    assert "Test Source" in lead
    # the embedded record carries the same data for the panel
    page = read(site, "co/front/alpha-peak")
    import re
    rec = json.loads(re.search(r'id="resort-record">(.*?)</script>', page, re.S).group(1))
    assert rec["resort"]["jobs"]["links"][0]["label"] == "All jobs" and rec["resort"]["webcams"]["cams_page"]


def test_resorts_without_data_show_collecting_note_for_tier_a_only(site: Path):
    generate_routes(site)
    a = _prerender(read(site, "co/front/alpha-peak"))
    b = _prerender(read(site, "co/front/bravo-bowl"))
    assert "Jobs links are being collected." in a and "live-cams" not in a
    assert "<h3>Jobs</h3>" not in b and "live-cams" not in b


def test_map_index_flags(site: Path):
    resorts, clusters, states = default_data()
    resorts[0]["webcams"], resorts[0]["jobs"] = WEBCAMS, JOBS
    resorts[1]["jobs"] = {"links": [], "feed": False}
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    data = json.loads((site / "data" / "build" / "map-index.json").read_text())
    rows = {row[0]: dict(zip(data["fields"], row)) for row in data["resorts"]}
    assert rows["alpha-peak"]["webcams_available"] is True and rows["alpha-peak"]["jobs_available"] is True
    assert rows["bravo-bowl"]["webcams_available"] is False and rows["bravo-bowl"]["jobs_available"] is False


def test_render_helpers_skip_unsafe_links():
    ctx = Ctx({}, {}, {}, {})
    assert render_webcams(ctx, {"webcams": {"cams_page": {"url": "http://x.example.com"}, "cams": []}}) == ""
    assert render_jobs(ctx, {"tier": "B", "jobs": {"links": [link("all", "x", "javascript:alert(1)")]}}) == ""


def test_hiring_window_line_tops_the_jobs_section(site: Path):
    resorts, clusters, states = default_data()
    window = {"text": "Applications open August 15 <online>.", **CITE, "note": None}
    resorts[0]["jobs"] = {**JOBS, "hiring_window": window}
    resorts[1]["jobs"] = {"links": [], "feed": False, "hiring_window": window}
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    a = _prerender(read(site, "co/front/alpha-peak"))
    assert ('<h3>Jobs</h3><p class="jobs-window"><strong>Hiring window:</strong> Applications open August 15 &lt;online&gt;. '
            '<span class="badge badge-estimated">Estimated</span>') in a
    assert a.index("jobs-window") < a.index("job-group")
    b = _prerender(read(site, "co/front/bravo-bowl"))
    assert "Hiring window:" in b and "Jobs links are being collected." in b   # Tier B with a window only
