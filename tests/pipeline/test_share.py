"""Tests for the share cards (pipeline/share.py, raster.py, glyphs.py, otf.py and the palette PNG in png.py).

The fixture site and its records are synthetic. The last tests check the committed cards and stubs of this
repository (images/share, map/).
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import glyphs, otf, png, raster, share  # noqa: E402
from pipeline.routes import OG_IMAGE, generate_routes  # noqa: E402

BUDGET = 60 * 1024  # bytes per card
TOTAL_BUDGET = 8 * 1024 * 1024
BANNED = ("operator_scale", "professionalism", "Professionalism")

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Instructor Grindset &bull; Snowsports Career Map</title>
  <meta name="description" content="National description." />
  <link rel="canonical" href="https://instructorgrindset.com/map/" />
  <meta property="og:title" content="Snowsports Career Map" />
  <meta property="og:description" content="National description." />
  <meta property="og:url" content="https://instructorgrindset.com/map/" />
  <meta property="og:image" content="https://instructorgrindset.com/images/ig-logo.png" />
  <meta name="twitter:card" content="summary" />
</head>
<body><div id="panel-content"><!-- prerender:start --><p>Loading</p><!-- prerender:end --></div>
<script type="module" src="/map/js/app.js"></script></body>
</html>
"""


def _src(value, conf="published", source="test-src"):
    return {"value": value, "source_id": source, "as_of": "2026-10-01", "confidence": conf}


def _resort(id_, name, cluster, state, tier="B", lat=39.5, lon=-106.0, **extra):
    return {"id": id_, "name": name, "cluster_id": cluster, "state": state, "tier": tier, "lat": lat, "lon": lon,
            "facility_type": "ski_area", "tier_a_criteria": [], "tier_a_status": "approved" if tier == "A" else None,
            "owner": None, "size": {}, "season": {}, "snow": None, "career": None, **extra}


def _square(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def write_site(root: Path, resorts=None) -> Path:
    env = {"version": 1, "generated_at": "2026-10-11T00:00:00Z"}
    resorts = resorts if resorts is not None else [
        _resort("alpha-peak", "Alpha Peak", "front", "CO", "A", 39.6, -106.3,
                size={"acres": _src(5317)}, season={"length_days": _src(150, "estimated")},
                snow={"annual_avg_in": _src(354, "reported")}),
        _resort("bravo-bowl", "Bravo Bowl", "front", "CO", lat=39.9, lon=-105.8),
        _resort("lake-east", "Lake East", "lake", "NV", "A", 39.2, -119.9, size={"acres": _src(2000)}),
        _resort("lake-west", "Lake West", "lake", "CA", lat=39.1, lon=-120.2),
    ]
    clusters = [
        {"type": "Feature", "geometry": _square(-106.6, 39.4, -105.6, 40.1),
         "properties": {"id": "front", "name": "Test Front", "states": ["CO"], "resort_ids": ["alpha-peak", "bravo-bowl"],
                        "tier_a_count": 1}},
        {"type": "Feature", "geometry": _square(-120.4, 38.9, -119.7, 39.4),
         "properties": {"id": "lake", "name": "Test Lake", "states": ["CA", "NV"], "resort_ids": ["lake-west", "lake-east"],
                        "tier_a_count": 1}},
    ]
    states = [{"code": "CO", "name": "Colorado", "cluster_ids": ["front"], "resort_count": 2},
              {"code": "CA", "name": "California", "cluster_ids": ["lake"], "resort_count": 1},
              {"code": "NV", "name": "Nevada", "cluster_ids": ["lake"], "resort_count": 1}]
    outlines = [("CO", "Colorado", _square(-109.05, 37, -102.04, 41)), ("NV", "Nevada", _square(-120, 35, -114, 42)),
                ("CA", "California", _square(-124.4, 32.5, -120, 42)), ("UT", "Utah", _square(-114, 37, -109.05, 42))]
    build = root / "data" / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "resorts.json").write_text(json.dumps({**env, "resorts": resorts}))
    (build / "clusters.geojson").write_text(json.dumps({"type": "FeatureCollection", **env, "features": clusters}))
    (build / "states.json").write_text(json.dumps({**env, "states": states}))
    (build / "sources.json").write_text(json.dumps({**env, "sources": [
        {"id": "test-src", "url": "https://example.com/src", "title": "Test Source", "accessed_date": "2026-10-01"}]}))
    (build / "us-states.geojson").write_text(json.dumps({"type": "FeatureCollection", **env, "features": [
        {"type": "Feature", "properties": {"code": c, "name": n}, "geometry": g} for c, n, g in outlines]}))
    (root / "map" / "js").mkdir(parents=True, exist_ok=True)
    (root / "map" / "index.html").write_text(TEMPLATE)
    return root


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    root = write_site(tmp_path_factory.mktemp("share-site"))
    generate_routes(root, sitemap=False)
    return root


def _page(root: Path, rel: str) -> str:
    return (root / "map" / rel / "index.html").read_text(encoding="utf-8")


def _meta(page: str, attr: str, key: str) -> list[str]:
    return re.findall(rf'<meta\s+{attr}="{re.escape(key)}"\s+content="([^"]*)"', page)


# ── Cards on the fixture site ──

def test_cards_for_clusters_and_tier_a_resorts_only(site: Path):
    out = site / "images" / "share"
    cards = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.png"))
    assert cards == ["ca/lake.png", "co/front.png", "co/front/alpha-peak.png", "nv/lake/lake-east.png"]
    for rel in cards:
        w, h, ch, depth, px = png.decode((out / rel).read_bytes())
        assert (w, h, ch, depth) == (1200, 630, 1, 8)
        assert len(png.palette((out / rel).read_bytes())) <= 256


def test_resort_stub_meta_tags(site: Path):
    page = _page(site, "co/front/alpha-peak")
    assert _meta(page, "property", "og:image") == ["https://instructorgrindset.com/images/share/co/front/alpha-peak.png"]
    assert _meta(page, "property", "og:image:width") == ["1200"]
    assert _meta(page, "property", "og:image:height") == ["630"]
    assert _meta(page, "name", "twitter:card") == ["summary_large_image"]
    (alt,) = _meta(page, "property", "og:image:alt")
    assert alt == ("Career Map card for Alpha Peak, Test Front, Colorado, with the resort marked on a map of Colorado. "
                   "5,317 skiable acres, 150 day season and 354 in annual snowfall.")
    assert "—" not in alt and " - " not in alt


def test_cluster_stubs_share_the_canonical_card(site: Path):
    for st in ("ca", "nv"):
        page = _page(site, f"{st}/lake")
        assert _meta(page, "property", "og:image") == ["https://instructorgrindset.com/images/share/ca/lake.png"]
        assert _meta(page, "name", "twitter:card") == ["summary_large_image"]
    # A resort in a multi state cluster uses the card at its canonical URL on both copies.
    for st in ("ca", "nv"):
        assert _meta(_page(site, f"{st}/lake/lake-east"), "property", "og:image") == [
            "https://instructorgrindset.com/images/share/nv/lake/lake-east.png"]
    alt = _meta(_page(site, "ca/lake"), "property", "og:image:alt")[0]
    assert alt == ("Career Map card for the Test Lake cluster in California and Nevada, with its resorts marked on a map. "
                   "2 resorts, 1 Tier A.")


def test_tier_b_and_state_pages_keep_the_logo(site: Path):
    for rel in ("co/front/bravo-bowl", "ca/lake/lake-west", "co", "nv"):
        page = _page(site, rel)
        assert _meta(page, "property", "og:image") == [OG_IMAGE], rel
        assert _meta(page, "name", "twitter:card") == ["summary"], rel
        assert not _meta(page, "property", "og:image:width"), rel
        assert not _meta(page, "property", "og:image:alt"), rel


def test_rebuild_leaves_unchanged_cards_alone(site: Path):
    out = site / "images" / "share"
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in out.rglob("*.png")}
    logs: list[str] = []
    share.write_cards(site, ["/map/ca/lake/", "/map/co/front/", "/map/co/front/alpha-peak/", "/map/nv/lake/lake-east/"],
                      log=logs.append)
    assert logs == []  # nothing drawn
    # A changed key (for example new drawing code) redraws, but identical bytes are not rewritten.
    manifest = out / share.MANIFEST
    data = json.loads(manifest.read_text())
    data["cards"] = {k: "0" * 32 for k in data["cards"]}
    manifest.write_text(json.dumps(data))
    share.write_cards(site, ["/map/ca/lake/", "/map/co/front/", "/map/co/front/alpha-peak/", "/map/nv/lake/lake-east/"],
                      log=logs.append)
    assert logs == ["share: 4 cards (4 drawn, 0 written, 0 removed)"]
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in out.rglob("*.png")} == before


def test_render_is_deterministic(site: Path):
    plans = share.plan_cards(site / "data" / "build", ["/map/co/front/alpha-peak/"])
    states = {f["properties"]["code"]: f["geometry"]
              for f in json.loads((site / "data" / "build" / "us-states.geojson").read_text())["features"]}
    card = plans["/map/co/front/alpha-peak/"]
    a, b = share.render(card, states), share.render(json.loads(json.dumps(card)), dict(reversed(list(states.items()))))
    assert a == b
    assert a == (site / "images" / "share" / "co" / "front" / "alpha-peak.png").read_bytes()
    assert share.card_key(card, "x", "y") == share.card_key(json.loads(json.dumps(card)), "x", "y")
    assert share.card_key(card, "x", "y") != share.card_key({**card, "title": "Other"}, "x", "y")


def test_stale_cards_are_removed(tmp_path: Path):
    root = write_site(tmp_path)
    generate_routes(root, sitemap=False)
    out = root / "images" / "share"
    assert (out / "nv" / "lake" / "lake-east.png").exists()
    keep = (out / "co" / "front" / "alpha-peak.png").stat().st_mtime_ns
    resorts = json.loads((root / "data" / "build" / "resorts.json").read_text())
    for r in resorts["resorts"]:
        if r["id"] == "lake-east":
            r["tier"], r["tier_a_status"] = "B", None
    (root / "data" / "build" / "resorts.json").write_text(json.dumps(resorts))
    generate_routes(root, sitemap=False)
    assert not (out / "nv" / "lake").exists()
    assert (out / "co" / "front" / "alpha-peak.png").stat().st_mtime_ns == keep
    assert _meta(_page(root, "nv/lake/lake-east"), "property", "og:image") == [OG_IMAGE]
    assert "nv/lake/lake-east.png" not in json.loads((out / share.MANIFEST).read_text())["cards"]


def test_no_cards_through_a_linked_images_folder(tmp_path: Path):
    """A test site that links the repository's images folder (tests/map/browser_test.mjs) never writes there."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "share").mkdir(parents=True)
    (elsewhere / "share" / "keep.png").write_bytes(b"not ours")
    root = write_site(tmp_path / "site")
    os.symlink(elsewhere, root / "images")
    generate_routes(root, sitemap=False)
    assert sorted(p.name for p in (elsewhere / "share").iterdir()) == ["keep.png"]
    assert _meta(_page(root, "co/front/alpha-peak"), "property", "og:image") == [OG_IMAGE]


def test_no_cards_without_state_outlines(tmp_path: Path):
    root = write_site(tmp_path)
    (root / "data" / "build" / "us-states.geojson").unlink()
    generate_routes(root, sitemap=False)
    assert not (root / "images" / "share").exists()
    assert _meta(_page(root, "co/front/alpha-peak"), "name", "twitter:card") == ["summary"]


# ── Card plans: only sourced facts, never professionalism ──

def test_facts_need_a_source_and_a_value():
    r = _resort("x", "X", "c", "CO", "A", size={"acres": {"value": 900, "source_id": None, "confidence": "published"},
                                               "vertical_ft": _src(3000)},
                season={"length_days": _src(120.5, "estimated")}, snow={"annual_avg_in": None})
    facts = share.resort_facts(r)
    assert [(f["label"], f["value"], f["confidence"]) for f in facts] == [("Season length", "120.5 days", "estimated")]


def test_cards_never_carry_professionalism_or_other_fields(tmp_path: Path):
    r = _resort("alpha-peak", "Alpha Peak", "front", "CO", "A", 39.6, -106.3, size={"acres": _src(5317), "lifts": _src(31)},
                season={"length_days": _src(150)}, snow={"annual_avg_in": _src(354)},
                operator_scale=3, professionalism={"score": 88, "operator_scale": 3},
                career={"pay": [{"hourly_low": 21.75, "hourly_high": 30.25}], "lucrativeness": {"mid": 12345},
                        "professionalism": 4})
    root = write_site(tmp_path, [r, _resort("bravo-bowl", "Bravo Bowl", "front", "CO")])
    plans = share.plan_cards(root / "data" / "build", ["/map/co/front/", "/map/co/front/alpha-peak/"])
    text = json.dumps(plans)
    for word in BANNED + ("12345", "21.75", "30.25", "lucrativeness", "Lifts"):
        assert word not in text
    allowed = {label for _, _, label, _ in share.FACTS}
    assert {f["label"] for f in plans["/map/co/front/alpha-peak/"]["facts"]} <= allowed
    assert set(plans["/map/co/front/alpha-peak/"]) == {"kind", "file", "eyebrow", "title", "subtitle", "facts", "map", "alt"}
    for name in ("share.py", "raster.py", "glyphs.py"):
        source = (REPO / "pipeline" / name).read_text(encoding="utf-8")
        assert not any(word in source for word in BANNED), name


def test_other_clusters_show_the_state_only():
    assert share._place({"id": "other-co", "name": "Other Colorado"}, "Colorado") == "Colorado"
    assert share._place({"id": "alaska", "name": "Alaska"}, "Alaska") == "Alaska"
    assert share._place({"id": "i70-corridor", "name": "I 70 Corridor"}, "Colorado") == "I 70 Corridor, Colorado"


def test_worst_case_card_fits_the_budget(tmp_path: Path):
    name = "Lake Placid Olympic Jumping Complex and Freestyle Aerial Training Center"
    r = _resort("long", name, "front", "CO", "A", 39.6, -106.3, size={"acres": _src(12345)},
                season={"length_days": _src(365)}, snow={"annual_avg_in": _src(1000)})
    others = [_resort(f"b{i}", f"B {i}", "front", "CO", lat=39.4 + i * 0.03, lon=-106.6 + i * 0.04) for i in range(20)]
    root = write_site(tmp_path, [r, *others])
    plans = share.plan_cards(root / "data" / "build", ["/map/co/front/long/"])
    real = REPO / "data" / "build" / "us-states.geojson"
    src = real if real.exists() else root / "data" / "build" / "us-states.geojson"
    states = {f["properties"]["code"]: f["geometry"] for f in json.loads(src.read_text())["features"]}
    data = share.render(plans["/map/co/front/long/"], states)
    assert len(data) <= BUDGET


# ── Building blocks ──

def test_palette_png_round_trip():
    pal = [(15, 18, 23), (255, 255, 255), (224, 160, 58)]
    idx = bytes([0, 1, 2, 1, 0, 2])
    data = png.encode_indexed(3, 2, idx, pal)
    assert png.decode(data) == (3, 2, 1, 8, idx)
    assert png.palette(data) == pal
    with pytest.raises(ValueError):
        png.encode_indexed(3, 2, bytes([0, 1, 3, 1, 0, 2]), pal)


def test_fill_coverage_union_and_holes():
    white, black = (255, 255, 255), (0, 0, 0)
    cv = raster.Canvas(10, 4, black)
    cv.fill([[(2, 0), (4.5, 0), (4.5, 4), (2, 4)]], white)
    assert [cv.rgb(x, 1) for x in range(1, 6)] == [black, white, white, (128, 128, 128), black]
    # Two overlapping rings with the same orientation unite; an opposite inner ring cuts a hole.
    cv = raster.Canvas(10, 10, black)
    outer = raster.oriented([(0, 0), (10, 0), (10, 10), (0, 10)])
    cv.fill([outer, raster.oriented([(3, 3), (7, 3), (7, 7), (3, 7)], positive=False)], white)
    assert cv.rgb(1, 1) == white and cv.rgb(5, 5) == black
    cv = raster.Canvas(10, 10, black)
    cv.fill([raster.oriented([(0, 0), (6, 0), (6, 10), (0, 10)]), raster.oriented([(4, 0), (10, 0), (10, 10), (4, 10)])],
            white)
    assert all(cv.rgb(x, 5) == white for x in range(10))
    # Half alpha blends halfway, in LEVELS steps.
    cv = raster.Canvas(4, 4, black)
    cv.fill([[(0, 0), (4, 0), (4, 4), (0, 4)]], white, 0.5)
    assert cv.rgb(2, 2) == (128, 128, 128)


def test_palette_reduction_keeps_reserved_colors():
    reserve = [(255, 0, 0), (0, 255, 0)]
    cv = raster.Canvas(300, 2, (0, 0, 0), reserve=reserve)
    for x in range(300):  # 300 distinct greys, more than a palette holds
        cv.px[x] = cv.color_index((x % 256, x // 256, 7))
    cv.px[300] = cv.color_index((255, 0, 0))
    idx, pal = cv.indexed()
    assert len(pal) <= 256 and (255, 0, 0) in pal and len(idx) == 600
    assert pal[idx[300]] == (255, 0, 0)


def test_text_width_kerning_wrap_and_fallback():
    bold = glyphs.load("inter-bold")
    assert bold.width("AV", 100) < bold.width("A", 100) + bold.width("V", 100)
    assert bold.char("ő") == "o"  # o with double acute falls back to its base letter
    assert bold.width("x☃", 50) == bold.width("x", 50)  # a character with no glyph is skipped
    lines = glyphs.wrap(bold, "Lake Placid Olympic Jumping Complex", 60, 600)
    assert len(lines) > 1 and all(bold.width(ln, 60) <= 600 for ln in lines)
    size, lines = glyphs.fit(bold, "Vail", [88, 72], 596, 200)
    assert (size, lines) == (88, ["Vail"])
    size, lines = glyphs.fit(bold, "word " * 40, [60, 40], 300, 100, max_lines=2)
    assert size == 40 and len(lines) == 2 and lines[-1].endswith("…")
    rings = bold.rings("o", 100, 0, 100)
    assert len(rings) == 2  # outer bowl and counter
    assert raster.signed_area(rings[0]) * raster.signed_area(rings[1]) < 0


def _charstring(*ops) -> bytes:
    out = bytearray()
    for op in ops:
        if isinstance(op, str):
            out += bytes([int(op)]) if int(op) < 32 else bytes([12, int(op) - 1200])
        else:
            out += bytes([op + 139]) if -107 <= op <= 107 else b"\x1c" + struct.pack(">h", op)
    return bytes(out)


def test_charstring_interpreter():
    cs = otf.Charstrings.__new__(otf.Charstrings)
    cs.subrs, cs.gsubrs = [_charstring(0, 50, "5", "11")], []
    # width 500 then rmoveto 10 20, rlineto 100 0, callsubr -107 (subr 0 after the bias), hvcurveto, endchar
    cs.glyphs = [_charstring(500, 10, 20, "21", 100, 0, "5", -107, "10", 10, 20, 30, 40, "31", "14")]
    (contour,) = cs.outline(0)
    assert contour == [("M", 10, 20), ("L", 110, 20), ("L", 110, 70), ("C", 120, 70, 140, 100, 140, 140)]


def test_cmap_format_4():
    seg = [(0x41, 0x43, -0x40 + 0x10000, 0), (0xFFFF, 0xFFFF, 1, 0)]
    n = len(seg)
    body = struct.pack(">HHHHHHH", 4, 0, 0, 2 * n, 0, 0, 0)
    body += b"".join(struct.pack(">H", s[1]) for s in seg) + b"\0\0"
    body += b"".join(struct.pack(">H", s[0]) for s in seg)
    body += b"".join(struct.pack(">H", s[2] & 0xFFFF) for s in seg)
    body += b"".join(struct.pack(">H", s[3]) for s in seg)
    table = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + body
    assert otf.cmap(table) == {0x41: 1, 0x42: 2, 0x43: 3}


INTER = Path("/usr/share/fonts/opentype/inter/Inter-Bold.otf")


@pytest.mark.skipif(not INTER.exists(), reason="Inter is not installed here")
def test_committed_outlines_match_the_font():
    data = otf.extract(INTER, "AVao1,")
    committed = json.loads((REPO / "pipeline" / "fonts" / "inter-bold.json").read_text())
    for ch in "AVao1,":
        assert data["glyphs"][ch] == committed["glyphs"][ch]
    assert data["kern"]["AV"] == committed["kern"]["AV"] < 0


# ── This repository ──

def _repo_cards() -> list[Path]:
    return sorted((REPO / "images" / "share").rglob("*.png"))


def test_repo_cards_fit_the_size_budget():
    cards = _repo_cards()
    resorts = json.loads((REPO / "data" / "build" / "resorts.json").read_text())["resorts"]
    clusters = json.loads((REPO / "data" / "build" / "clusters.geojson").read_text())["features"]
    tier_a = sum(1 for r in resorts if r["tier"] == "A")
    assert len(cards) == tier_a + len(clusters)
    sizes = {p.relative_to(REPO).as_posix(): p.stat().st_size for p in cards}
    assert max(sizes.values()) <= BUDGET, max(sizes, key=sizes.get)
    assert sum(sizes.values()) <= TOTAL_BUDGET


def test_repo_stub_points_at_its_card():
    page = (REPO / "map" / "co" / "i70-corridor" / "vail" / "index.html").read_text(encoding="utf-8")
    (url,) = _meta(page, "property", "og:image")
    assert url == "https://instructorgrindset.com/images/share/co/i70-corridor/vail.png"
    assert _meta(page, "property", "og:image:width") == ["1200"]
    assert _meta(page, "property", "og:image:height") == ["630"]
    assert _meta(page, "name", "twitter:card") == ["summary_large_image"]
    assert _meta(page, "property", "og:image:alt")[0].startswith("Career Map card for Vail, I 70 Corridor, Colorado")
    card = REPO / url.removeprefix("https://instructorgrindset.com/")
    assert png.decode(card.read_bytes())[:2] == (1200, 630)


def test_repo_tier_b_stub_keeps_the_logo():
    resorts = json.loads((REPO / "data" / "build" / "resorts.json").read_text())["resorts"]
    r = next(r for r in sorted(resorts, key=lambda r: r["id"]) if r["tier"] == "B")
    pages = list((REPO / "map" / r["state"].lower()).glob(f"*/{r['id']}/index.html"))
    assert pages
    page = pages[0].read_text(encoding="utf-8")
    assert _meta(page, "property", "og:image") == [OG_IMAGE]
    assert _meta(page, "name", "twitter:card") == ["summary"]
    assert not (REPO / "images" / "share" / r["state"].lower() / r["cluster_id"] / f"{r['id']}.png").exists()


def test_cards_are_published():
    """GitHub Pages builds with Jekyll: nothing in _config.yml may exclude images/share."""
    excluded = [ln.strip()[2:].strip().strip('"') for ln in (REPO / "_config.yml").read_text().splitlines()
                if ln.strip().startswith("- ")]
    assert not any(x.rstrip("/") in ("images", "images/share") or x.endswith(".png") for x in excluded)
