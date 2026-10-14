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
                 "source_id": "tm-alpha-page", "as_of": "2026-10-13", "confidence": "estimated", "note": None,
                 "image_url": None, "image_format": None, "image_source_id": None, "image_as_of": None,
                 "image_confidence": None, "image_note": None}
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


# ── Official map files (image_url): shown in the trail map dialog straight from the resort's servers ──
RES = {"id": "tm-res-page", "url": "https://www.alpha-resort.com/mountain/trail-map", "title": "Alpha trail map",
       "accessed_date": "2026-10-14", "note": "From a search result for this page; page not opened"}
IMG = {"id": "tm-res-file", "url": "https://www.alpha-resort.com/media/alpha-trail-map-2025-26.png", "title": "Alpha map file",
       "accessed_date": "2026-10-14", "note": "From a search result for this page; page not opened"}
CDN_SRC = {"id": "tm-res-cdn", "url": "https://scene7.parent-cdn.com/is/image/parent/alpha-trail-map?fmt=jpg",
           "title": "Alpha map on the operator CDN", "accessed_date": "2026-10-14",
           "note": "From a search result for this page; page not opened"}


def img_rec(**kw):
    base = rec(url="https://www.alpha-resort.com/mountain/trail-map", source_id="tm-res-page",
               image_url="https://www.alpha-resort.com/media/alpha-trail-map-2025-26.png", image_format="png",
               image_source_id="tm-res-file", image_as_of="2026-10-14", image_confidence="estimated")
    return {**base, **kw}


def run_img(tmp_path: Path, records, cdn=()):
    d = tmp_path / "trailmaps_official"
    d.mkdir(exist_ok=True)
    (d / "batch.yaml").write_text(yaml.safe_dump({"batch": "tm-test", "researched_date": "2026-10-14",
                                                   "sources": [RES, IMG, CDN_SRC], "trailmaps": records}))
    return load_official_trailmaps(tmp_path, SourceRegistry(), {"alpha-peak"}, rel=lambda p: p.name, image_domains=cdn)


def test_image_fields_reach_output(tmp_path: Path):
    out, errors = run_img(tmp_path, [img_rec(image_note="Front side only.")])
    assert errors == []
    o = as_output(out["alpha-peak"])
    assert {k: o[k] for k in o if k.startswith("image_")} == {
        "image_url": "https://www.alpha-resort.com/media/alpha-trail-map-2025-26.png", "image_format": "png",
        "image_source_id": "tm-res-file", "image_as_of": "2026-10-14", "image_confidence": "estimated",
        "image_note": "Front side only."}
    assert o["url"] == "https://www.alpha-resort.com/mountain/trail-map"  # the page link stays
    # A record without a file keeps every image field null.
    out, errors = run_img(tmp_path, [rec(url="https://www.alpha-resort.com/mountain/trail-map", source_id="tm-res-page")])
    assert errors == [] and as_output(out["alpha-peak"])["image_url"] is None


def test_image_fields_come_together(tmp_path: Path):
    for missing in ("image_format", "image_source_id", "image_as_of", "image_confidence"):
        r = img_rec()
        del r[missing]
        _, errors = run_img(tmp_path, [r])
        assert any("image fields come together" in e and missing in e for e in errors), missing
    _, errors = run_img(tmp_path, [rec(url="https://www.alpha-resort.com/mountain/trail-map", source_id="tm-res-page",
                                       image_note="orphan")])
    assert any("image_note needs an image_url" in e for e in errors)
    _, errors = run_img(tmp_path, [img_rec(image_source_id="missing-src")])
    assert any("not defined in any sources list" in e for e in errors)


def test_image_format_needs_matching_file(tmp_path: Path):
    _, errors = run_img(tmp_path, [img_rec(image_format="jpg")])
    assert any("image_format jpg needs an image_url ending in .jpg or .jpeg" in e for e in errors)
    for url, fmt in (("https://www.alpha-resort.com/m/map.webp", "webp"), ("https://www.alpha-resort.com/m/MAP.JPEG?v=2", "jpg"),
                     ("https://www.alpha-resort.com/m/map.pdf?rev=abc&hash=1", "pdf")):
        _, errors = run_img(tmp_path, [img_rec(image_url=url, image_format=fmt)])
        assert errors == [], (url, errors)
    _, errors = run_img(tmp_path, [img_rec(image_format="gif")])
    assert any("image_format" in e for e in errors)
    _, errors = run_img(tmp_path, [img_rec(image_url="http://www.alpha-resort.com/media/alpha.png")])
    assert any("image_url must be an https link" in e for e in errors)


def test_image_domain_rules(tmp_path: Path):
    # Another site's copy is refused, even when its source names that site.
    _, errors = run_img(tmp_path, [img_rec(image_url="https://img.mirror-site.com/alpha.png")])
    assert any("image_url host img.mirror-site.com" in e for e in errors)
    # A subdomain of the resort's own site is the resort's site.
    _, errors = run_img(tmp_path, [img_rec(image_url="https://cdn.alpha-resort.com/alpha.png")])
    assert errors == []
    # An operator CDN passes only when config lists it; scene7 style URLs name the format in fmt=.
    cdn = img_rec(image_url="https://scene7.parent-cdn.com/is/image/parent/alpha-trail-map?fmt=jpg", image_format="jpg",
                  image_source_id="tm-res-cdn")
    _, errors = run_img(tmp_path, [cdn])
    assert any("nor an operator CDN in config trailmap.official_image_domains" in e for e in errors)
    out, errors = run_img(tmp_path, [cdn], cdn=["parent-cdn.com"])
    assert errors == [] and out["alpha-peak"].image_format == "jpg"
    _, errors = run_img(tmp_path, [{**cdn, "image_format": "png"}], cdn=["parent-cdn.com"])
    assert any("needs an image_url ending in .png" in e for e in errors)


def test_image_domain_ok():
    from pipeline.trailmaps_official import image_domain_ok
    page = "https://www.alpha-resort.com/trail-map"
    assert image_domain_ok("https://www.alpha-resort.com/a.png", page, None, ())
    assert image_domain_ok("https://scene7.parent-cdn.com/is/image/x", page, None, ["parent-cdn.com"])
    assert image_domain_ok("https://parent-cdn.com/x.png", page, None, ["parent-cdn.com"])
    assert not image_domain_ok("https://evilparent-cdn.com/x.png", page, None, ["parent-cdn.com"])
    assert not image_domain_ok("https://parent-cdn.com.evil.net/x.png", page, None, ["parent-cdn.com"])
    assert image_domain_ok("https://files.other-resort.com/x.pdf", page, "https://www.other-resort.com/map", ())


def test_config_lists_the_operator_image_domains():
    from pipeline.config import load_config
    cfg = load_config(REPO / "config")
    domains = cfg.definitions.trailmap.official_image_domains
    assert domains and all(d == d.lower() and "/" not in d and not d.startswith(".") for d in domains)


def test_committed_image_files_follow_the_search_result_rule():
    raw = REPO / "data" / "raw" / "trailmaps_official"
    for p in raw.glob("*.yaml"):
        data = yaml.safe_load(p.read_text())
        sources = {s["id"]: s for s in data.get("sources", [])}
        for r in data.get("trailmaps", []):
            if not r.get("image_url"):
                continue
            src = sources[r["image_source_id"]]
            assert src["url"] == r["image_url"], r["resort_id"]  # the file itself was seen in a result
            assert r["image_confidence"] == "estimated" and src["note"] == "From a search result for this page; page not opened"
