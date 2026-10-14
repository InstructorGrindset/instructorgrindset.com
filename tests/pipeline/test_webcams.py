"""Tests for pipeline/webcams.py (live webcam links). All data here is synthetic."""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.sources import SourceRegistry
from pipeline.webcams import as_output, load_webcams

REPO = Path(__file__).resolve().parents[2]
SRC = {"id": "cam-alpha-page", "url": "https://www.alpha.example.com/mountain/webcams", "title": "Alpha webcams",
       "accessed_date": "2026-10-13", "note": "From a search result for this page; page not opened"}
SRC_VENDOR = {"id": "cam-alpha-vendor", "url": "https://alpha.camvendor.example.net/summit", "title": "Alpha summit cam",
              "accessed_date": "2026-10-13"}


def page(**kw):
    return {"url": "https://www.alpha.example.com/mountain/webcams", "source_id": "cam-alpha-page",
            "as_of": "2026-10-13", "confidence": "estimated", **kw}


def cam(label="Summit", **kw):
    return {"label": label, "url": f"https://www.alpha.example.com/mountain/webcams/{label.lower()}", "kind": "page",
            "source_id": "cam-alpha-page", **kw}


def rec(**kw):
    return {"resort_id": "alpha-peak", "cams_page": page(), **kw}


def run(tmp_path: Path, records, sources=(SRC, SRC_VENDOR), known=("alpha-peak", "bravo-bowl"), name="batch.yaml"):
    d = tmp_path / "webcams"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.safe_dump({"batch": "webcams-test", "researched_date": "2026-10-13",
                                          "sources": list(sources), "records": records}))
    return load_webcams(tmp_path, SourceRegistry(), set(known), rel=lambda p: p.name)


def test_missing_or_empty_folder_gives_nothing(tmp_path: Path):
    assert load_webcams(tmp_path, SourceRegistry(), {"alpha-peak"}) == ({}, [])
    (tmp_path / "webcams").mkdir()
    (tmp_path / "webcams" / "empty.yaml").write_text("")
    assert load_webcams(tmp_path, SourceRegistry(), {"alpha-peak"}) == ({}, [])


def test_valid_record_reaches_output_with_inherited_dates(tmp_path: Path):
    cams = [cam("Summit", kind="stream"), cam("Base", kind="image", as_of="2026-10-01", confidence="published"),
            cam("Bowl"), cam("Lift"), cam("Park")]
    out, errors = run(tmp_path, [rec(cams=cams)])
    assert errors == []
    o = as_output(out["alpha-peak"], max_cams=4)
    assert o["cams_page"] == {"url": "https://www.alpha.example.com/mountain/webcams", "source_id": "cam-alpha-page",
                              "as_of": "2026-10-13", "confidence": "estimated", "note": None}
    assert [c["label"] for c in o["cams"]] == ["Summit", "Base", "Bowl", "Lift"]   # first max_cams kept
    assert o["cams"][0] == {"label": "Summit", "url": "https://www.alpha.example.com/mountain/webcams/summit",
                            "kind": "stream", "source_id": "cam-alpha-page", "as_of": "2026-10-13",
                            "confidence": "estimated", "note": None}
    assert o["cams"][1]["as_of"] == "2026-10-01" and o["cams"][1]["confidence"] == "published"
    assert as_output(None, 4) is None
    assert as_output(out["alpha-peak"], max_cams=0)["cams"] == []


def test_cams_without_a_page_need_their_own_dates(tmp_path: Path):
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak", "cams": [cam()]}])
    assert any("need as_of and confidence" in e for e in errors)
    out, errors = run(tmp_path, [{"resort_id": "alpha-peak",
                                  "cams": [cam(as_of="2026-10-13", confidence="estimated")]}])
    assert errors == [] and as_output(out["alpha-peak"], 4)["cams_page"] is None
    _, errors = run(tmp_path, [{"resort_id": "alpha-peak"}])
    assert any("needs a cams_page or at least one cam" in e for e in errors)


def test_domain_must_match_its_source(tmp_path: Path):
    off = cam("Summit", url="https://alpha.camvendor.example.net/summit")
    _, errors = run(tmp_path, [rec(cams=[off])])
    assert any("cam 'Summit' url domain example.net differs from its source example.com" in e for e in errors)
    out, errors = run(tmp_path, [rec(cams=[{**off, "source_id": "cam-alpha-vendor"}])])
    assert errors == [] and out["alpha-peak"].cams[0].url.startswith("https://alpha.camvendor")
    _, errors = run(tmp_path, [rec(cams_page=page(url="https://www.other.example.org/cams"))])
    assert any("cams_page url domain example.org differs" in e for e in errors)


def test_rejects_http_unknown_ids_duplicates_and_bad_kinds(tmp_path: Path):
    _, errors = run(tmp_path, [rec(cams_page=page(url="http://www.alpha.example.com/mountain/webcams"))])
    assert any("https" in e for e in errors)
    _, errors = run(tmp_path, [rec(cams=[cam(url="javascript:alert(1)")])])
    assert any("https" in e for e in errors)
    _, errors = run(tmp_path, [rec(resort_id="nope")])
    assert any("resort 'nope': no such resort" in e for e in errors)
    _, errors = run(tmp_path, [rec(cams_page=page(source_id="missing-src"))])
    assert any("not defined in any sources list" in e for e in errors)
    _, errors = run(tmp_path, [rec(cams=[cam(kind="video")])])
    assert any("record 'alpha-peak'" in e and "kind" in e for e in errors), errors
    _, errors = run(tmp_path, [rec(cams=[cam(), cam()])])
    assert any("listed twice" in e for e in errors)
    _, errors = run(tmp_path, [rec(cams=[cam(url=page()["url"])])])
    assert any("url listed twice" in e for e in errors)
    _, errors = run(tmp_path, [rec(), rec()])
    assert any("resort ids listed twice" in e for e in errors)
    _, errors = run(tmp_path, [rec(confidence="certain")])
    assert errors


def test_one_record_per_resort_across_files(tmp_path: Path):
    run(tmp_path, [rec()], name="a.yaml")
    _, errors = run(tmp_path, [rec()], name="b.yaml")
    assert any("also has webcams in a.yaml" in e for e in errors)


def test_committed_files_validate():
    """Every committed webcam file validates against the real build's resort ids (once research lands)."""
    import json
    build = REPO / "data" / "build" / "resorts.json"
    if not list((REPO / "data" / "raw" / "webcams").glob("*.yaml")) or not build.exists():
        return
    known = {r["id"] for r in json.loads(build.read_text())["resorts"]}
    registry = SourceRegistry()
    from pipeline.tier_a import _register_sources, _yaml_files
    for p in _yaml_files(REPO / "data" / "raw" / "sources"):
        _register_sources(yaml.safe_load(p.read_text()), p.name, registry, [])
    _, errors = load_webcams(REPO / "data" / "raw", registry, known, rel=lambda p: p.name)
    assert errors == []
