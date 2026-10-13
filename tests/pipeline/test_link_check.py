"""Tests for pipeline/link_check.py (monthly link check). Network calls are replaced by a fake opener."""
from __future__ import annotations

import datetime as dt
import json
import socket
import ssl
import urllib.error

from pipeline import link_check as lc


def write_build(tmp_path):
    build = tmp_path / "data" / "build"
    (build / "jobs").mkdir(parents=True)
    cite = {"source_id": "s", "as_of": "2026-10-13", "confidence": "estimated"}
    resorts = [
        {"id": "alpha", "official_trailmap": {"url": "https://a.example.com/map.pdf", **cite},
         "webcams": {"cams_page": {"url": "https://a.example.com/cams", **cite},
                     "cams": [{"label": "Summit", "url": "https://a.example.com/cams/summit", **cite}]},
         "jobs": {"links": [{"label": "Jobs", "url": "https://jobs.parent.example.com/", **cite}]}},
        {"id": "bravo", "official_trailmap": {"url": "javascript:alert(1)"}, "webcams": None,
         "jobs": {"links": [{"label": "Jobs", "url": "https://jobs.parent.example.com/", **cite}]}},
    ]
    (build / "resorts.json").write_text(json.dumps({"resorts": resorts}))
    (build / "jobs" / "employers.json").write_text(json.dumps({"employers": [
        {"id": "parent", "careers_url": "https://jobs.parent.example.com/"},
        {"id": "club", "careers_url": "https://club.example.org/jobs"}]}))
    return build


def test_collect_links_dedupes_and_records_where_used(tmp_path):
    links = lc.collect_links(write_build(tmp_path))
    by = {x.url: x.used_by for x in links}
    assert sorted(by) == ["https://a.example.com/cams", "https://a.example.com/cams/summit", "https://a.example.com/map.pdf",
                          "https://club.example.org/jobs", "https://jobs.parent.example.com/"]
    assert by["https://jobs.parent.example.com/"] == ["alpha: jobs link Jobs", "bravo: jobs link Jobs", "employer parent: careers page"]
    assert by["https://a.example.com/map.pdf"] == ["alpha: official trail map"]


def fake(responses):
    """responses: url -> list of (method, status or exception) consumed in order."""
    calls = []

    def opener(method, url):
        calls.append((method, url))
        kind = responses[url].pop(0)
        if isinstance(kind, BaseException):
            raise kind
        return kind
    return opener, calls


def test_classify():
    opener, calls = fake({
        "https://ok": [200], "https://gone": [404], "https://head-refused": [405, 200], "https://blocked": [403, 403],
        "https://dns": [urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))],
        "https://cert": [urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))],
        "https://flaky": [urllib.error.URLError(TimeoutError()), 200],
        "https://down": [502, 502, 502, 502],
    })
    got = {u: (r.state, r.detail) for u in ["https://ok", "https://gone", "https://head-refused", "https://blocked",
                                            "https://dns", "https://cert", "https://flaky", "https://down"]
           for r in [lc.classify(u, opener, pause=0)]}
    assert got == {"https://ok": ("ok", "HTTP 200"), "https://gone": ("broken", "HTTP 404"),
                   "https://head-refused": ("ok", "HTTP 200"), "https://blocked": ("unverified", "HTTP 403"),
                   "https://dns": ("broken", "DNS lookup failed"), "https://cert": ("broken", "invalid TLS certificate"),
                   "https://flaky": ("ok", "HTTP 200"), "https://down": ("unverified", "HTTP 502")}
    assert ("GET", "https://head-refused") in calls


def test_report_lists_broken_links_and_where_they_are_used(tmp_path):
    links = lc.collect_links(write_build(tmp_path))
    states = {"https://a.example.com/map.pdf": ("broken", "HTTP 404"), "https://club.example.org/jobs": ("unverified", "HTTP 403")}
    results = [lc.Result(x.url, *states.get(x.url, ("ok", "HTTP 200"))) for x in links]
    text = lc.render_report(links, results, dt.date(2026, 11, 1))
    assert text.startswith("# Broken links: 1 of 5\n")
    assert "| https://a.example.com/map.pdf | HTTP 404 | alpha: official trail map |" in text
    assert "Could not verify (1)" in text and "club.example.org" in text
    ok = lc.render_report(links, [lc.Result(x.url, "ok", "HTTP 200") for x in links], dt.date(2026, 11, 1))
    assert "No broken links." in ok and "<details>" not in ok


def test_main_writes_report_and_summary(tmp_path, monkeypatch):
    write_build(tmp_path)
    monkeypatch.setattr(lc, "_open", lambda method, url: 404 if url.endswith(".pdf") else 200)
    report, summary = tmp_path / "r.md", tmp_path / "s.json"
    assert lc.main(["--root", str(tmp_path), "--report", str(report), "--summary", str(summary), "--workers", "2"]) == 0
    assert json.loads(summary.read_text()) == {"checked": 5, "ok": 4, "broken": 1, "unverified": 0}
    assert "map.pdf" in report.read_text()
    assert lc.main(["--root", str(tmp_path / "none")]) == 1
