"""Tests for pipeline/trailmaps_official.py (official trail map links). All data here is synthetic."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.sources import SourceRegistry  # noqa: E402
from pipeline.trailmaps_official import as_output, load_official_trailmaps, registered_domain  # noqa: E402

SRC = {"id": "tm-alpha-page", "url": "https://www.alpha.example.com/mountain/trail-map", "title": "Alpha trail map",
       "accessed_date": "2026-10-13", "note": "From a search result for this page; page not opened"}


def rec(**kw):
    base = {"resort_id": "alpha-peak", "url": "https://www.alpha.example.com/mountain/trail-map", "format": "page",
            "season": "2025/26", "source_id": "tm-alpha-page", "as_of": "2026-10-13", "confidence": "estimated"}
    return {**base, **kw}


def run(tmp_path: Path, records, sources=(SRC,), known=("alpha-peak", "bravo-bowl")):
    d = tmp_path / "trailmaps_official"
    d.mkdir(exist_ok=True)
    (d / "batch.yaml").write_text(yaml.safe_dump({"batch": "tm-test", "researched_date": "2026-10-13",
                                                   "sources": list(sources), "trailmaps": records}))
    return load_official_trailmaps(tmp_path, SourceRegistry(), set(known), rel=lambda p: p.name)


def test_valid_record_reaches_output(tmp_path: Path):
    out, errors = run(tmp_path, [rec()])
    assert errors == []
    o = as_output(out["alpha-peak"])
    assert o == {"url": "https://www.alpha.example.com/mountain/trail-map", "format": "page", "season": "2025/26",
                 "source_id": "tm-alpha-page", "as_of": "2026-10-13", "confidence": "estimated", "note": None}
    assert as_output(None) is None


def test_file_formats_need_matching_extensions(tmp_path: Path):
    pdf = "https://www.alpha.example.com/media/alpha-trail-map-2025-26.pdf"
    out, errors = run(tmp_path, [rec(url=pdf, format="pdf")])
    assert errors == [] and out["alpha-peak"].format == "pdf"
    _, errors = run(tmp_path, [rec(url=pdf, format="jpg")])
    assert any("format jpg needs a url ending in .jpg or .jpeg" in e for e in errors)
    _, errors = run(tmp_path, [rec(url=pdf, format="page")])
    assert any("needs format pdf, jpg or png" in e for e in errors)
    jpg = "https://www.alpha.example.com/img/TrailMap.JPEG?v=3"
    _, errors = run(tmp_path, [rec(url=jpg, format="jpg")])
    assert errors == []


def test_rejects_other_domains_http_unknown_resorts_and_bad_seasons(tmp_path: Path):
    _, errors = run(tmp_path, [rec(url="https://cdn.someone-else.example.org/alpha.jpg", format="jpg")])
    assert any("differs from its source" in e for e in errors)
    _, errors = run(tmp_path, [rec(url="http://www.alpha.example.com/mountain/trail-map")])
    assert any("https" in e for e in errors)
    _, errors = run(tmp_path, [rec(resort_id="nope")])
    assert any("no such resort" in e for e in errors)
    for bad in ("2025-26", "2025/27", "25/26"):
        _, errors = run(tmp_path, [rec(season=bad)])
        assert any("season must look like" in e for e in errors), bad
    out, errors = run(tmp_path, [rec(season=None)])
    assert errors == [] and out["alpha-peak"].season is None


def test_source_must_exist_and_ids_unique(tmp_path: Path):
    _, errors = run(tmp_path, [rec(source_id="missing-src")])
    assert any("not defined in any sources list" in e for e in errors)
    _, errors = run(tmp_path, [rec(), rec()])
    assert any("listed twice" in e for e in errors)


def test_registered_domain():
    assert registered_domain("www.vail.com") == "vail.com"
    assert registered_domain("assets.snow.com") == "snow.com"
    assert registered_domain(None) == ""


def test_committed_files_validate():
    """Every committed file validates against the real build's resort ids and the shared sources."""
    import json
    from pipeline.tier_a import _register_sources, _yaml_files  # noqa: F401
    build = REPO / "data" / "build" / "resorts.json"
    files = list((REPO / "data" / "raw" / "trailmaps_official").glob("*.yaml"))
    if not files or not build.exists():
        return
    known = {r["id"] for r in json.loads(build.read_text())["resorts"]}
    out, errors = load_official_trailmaps(REPO / "data" / "raw", SourceRegistry(), known, rel=lambda p: p.name)
    assert errors == []
    for r in out.values():
        assert r.confidence in ("published", "estimated")
