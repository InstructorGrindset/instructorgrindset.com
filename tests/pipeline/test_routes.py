"""Tests for pipeline/routes.py (static deep link stubs). All data here is synthetic."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.routes import MARKER, generate_routes  # noqa: E402

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
  <link rel="stylesheet" href="/map/css/map.css">
</head>
<body><a href="#main">Skip</a><div id="panel-content"><!-- prerender:start --><p>Loading</p><!-- prerender:end --></div>
<script type="module" src="/map/js/app.js"></script></body>
</html>
"""


def _env(n: int = 1) -> dict:
    return {"version": 1, "generated_at": "2026-10-11T00:00:00Z"}


def write_build(root: Path, resorts: list[dict], clusters: list[dict], states: list[dict]) -> None:
    build = root / "data" / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "sources.json").write_text(json.dumps({**_env(), "sources": [
        {"id": "test-src", "url": "https://example.com/src", "title": "Test Source", "accessed_date": "2026-10-01"}]}))
    (build / "resorts.json").write_text(json.dumps({**_env(), "resorts": resorts}))
    (build / "clusters.geojson").write_text(json.dumps({"type": "FeatureCollection", **_env(), "features": [
        {"type": "Feature", "geometry": None, "properties": c} for c in clusters]}))
    (build / "states.json").write_text(json.dumps({**_env(), "states": states}))


def default_data():
    def r(id_, name, cluster, state, tier="B", **extra):
        return {"id": id_, "name": name, "cluster_id": cluster, "state": state, "tier": tier, "lat": 39.5, "lon": -106.0,
                "tier_a_criteria": [], "tier_a_status": None, "owner": None, "size": {}, "season": {}, "career": None, **extra}
    src = {"value": 5300, "source_id": "test-src", "as_of": "2026-10-01", "confidence": "published"}
    resorts = [
        r("alpha-peak", "Alpha Peak", "front", "CO", "A", tier_a_status="approved",
          tier_a_criteria=[{"criterion": "operator_compensation", "rationale": "x", "source_id": "test-src"}],
          owner={**src, "value": "Test Owner"}, size={"acres": src},
          career={"pay": [{"cert_level": "level_1", "hourly_low": 21.5, "hourly_high": 24, "confidence": "published",
                           "source_id": "test-src", "as_of": "2026-10-01", "season": "2025-26", "stale": False}]}),
        r("bravo-bowl", "Bravo & Sons <Bowl>", "front", "CO"),
        r("lake-west", "Lake West", "lake", "CA"),
        r("lake-east", "Lake East", "lake", "NV"),
    ]
    clusters = [
        {"id": "front", "name": "Test Front", "states": ["CO"], "resort_ids": ["alpha-peak", "bravo-bowl"]},
        {"id": "lake", "name": "Test Lake", "states": ["CA", "NV"], "resort_ids": ["lake-west", "lake-east"]},
    ]
    states = [
        {"code": "CO", "name": "Colorado", "cluster_ids": ["front"], "resort_count": 2},
        {"code": "CA", "name": "California", "cluster_ids": ["lake"], "resort_count": 1},
        {"code": "NV", "name": "Nevada", "cluster_ids": ["lake"], "resort_count": 1},
    ]
    return resorts, clusters, states


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "map" / "js").mkdir(parents=True)
    (tmp_path / "map" / "css").mkdir()
    (tmp_path / "map" / "vendor" / "zod").mkdir(parents=True)
    (tmp_path / "map" / "index.html").write_text(TEMPLATE)
    (tmp_path / "map" / "js" / "app.js").write_text("// app")
    (tmp_path / "map" / "css" / "map.css").write_text("/* css */")
    (tmp_path / "map" / "vendor" / "zod" / "zod.mjs").write_text("// zod")
    write_build(tmp_path, *default_data())
    return tmp_path


def read(site: Path, rel: str) -> str:
    return (site / "map" / rel / "index.html").read_text()


def test_writes_state_cluster_and_resort_stubs(site: Path):
    n = generate_routes(site)
    # 3 states + front(1) + lake(2 states) clusters + 2 front resorts + 2 lake resorts x 2 states
    assert n == 3 + 3 + 2 + 4
    co = read(site, "co")
    assert "<title>Colorado Resorts and Clusters for Snowsports Instructors • Instructor Grindset</title>" in co
    assert 'href="https://instructorgrindset.com/map/co/"' in co
    assert "2 resorts, with a source on every number." in co
    assert MARKER in co
    cluster = read(site, "co/front")
    assert "<title>Instructor pay, housing, and snow in Test Front • Instructor Grindset</title>" in cluster
    assert 'href="https://instructorgrindset.com/map/co/front/"' in cluster
    resort = read(site, "co/front/alpha-peak")
    assert "<title>Instructor pay, housing, and snow at Alpha Peak • Instructor Grindset</title>" in resort
    assert '<meta property="og:title" content="Instructor pay, housing, and snow at Alpha Peak" />' in resort
    assert '<meta property="og:url" content="https://instructorgrindset.com/map/co/front/alpha-peak/" />' in resort
    assert "Plan a season at Alpha Peak in the Test Front cluster, Colorado." in resort
    assert 'href="https://instructorgrindset.com/map/co/front/alpha-peak/"' in resort
    # Asset paths stay absolute, the body is the shell unchanged.
    assert 'src="/map/js/app.js"' in resort
    assert resort.count("<title>") == 1


def test_multi_state_cluster_canonicals(site: Path):
    generate_routes(site)
    for st in ("ca", "nv"):
        page = read(site, f"{st}/lake")
        assert 'href="https://instructorgrindset.com/map/ca/lake/"' in page
        assert "in California and Nevada." in page
    # Resort canonical follows the resort's own state.
    assert 'href="https://instructorgrindset.com/map/nv/lake/lake-east/"' in read(site, "ca/lake/lake-east")
    assert 'href="https://instructorgrindset.com/map/ca/lake/lake-west/"' in read(site, "nv/lake/lake-west")


def test_escapes_html_in_names(site: Path):
    generate_routes(site)
    page = read(site, "co/front/bravo-bowl")
    assert "<title>Instructor pay, housing, and snow at Bravo &amp; Sons &lt;Bowl&gt;" in page
    assert "<Bowl>" not in page
    ld = page.split('<script type="application/ld+json">')[1].split("</script>")[0]
    assert json.loads(ld)["@graph"][0]["name"] == "Bravo & Sons <Bowl>"


def test_manifest_and_stale_removal(site: Path):
    generate_routes(site)
    manifest = json.loads((site / "map" / ".routes-manifest.json").read_text())
    assert "co/front/bravo-bowl/" in manifest["pages"]
    # A hand placed file inside a stub directory survives cleanup.
    (site / "map" / "co" / "front" / "notes.txt").write_text("keep")

    resorts, clusters, states = default_data()
    resorts = [r for r in resorts if r["id"] != "bravo-bowl" and r["cluster_id"] != "lake"]
    clusters = [dict(clusters[0], resort_ids=["alpha-peak"])]
    states = [dict(states[0], resort_count=1)]
    write_build(site, resorts, clusters, states)
    n = generate_routes(site)

    assert n == 3
    assert not (site / "map" / "co" / "front" / "bravo-bowl").exists()
    assert not (site / "map" / "ca").exists()
    assert not (site / "map" / "nv").exists()
    assert (site / "map" / "co" / "front" / "alpha-peak" / "index.html").exists()
    assert (site / "map" / "co" / "front" / "notes.txt").read_text() == "keep"
    # Protected files are untouched.
    assert (site / "map" / "index.html").read_text() == TEMPLATE
    assert (site / "map" / "js" / "app.js").exists()
    assert (site / "map" / "css" / "map.css").exists()
    assert (site / "map" / "vendor" / "zod" / "zod.mjs").exists()
    manifest = json.loads((site / "map" / ".routes-manifest.json").read_text())
    assert manifest["pages"] == ["co/", "co/front/", "co/front/alpha-peak/"]


def test_stale_stub_removed_even_without_manifest(site: Path):
    generate_routes(site)
    (site / "map" / ".routes-manifest.json").unlink()
    resorts, clusters, states = default_data()
    write_build(site, [r for r in resorts if r["id"] != "alpha-peak"],
                [dict(clusters[0], resort_ids=["bravo-bowl"]), clusters[1]], states)
    generate_routes(site)
    assert not (site / "map" / "co" / "front" / "alpha-peak").exists()


def test_never_deletes_protected_or_unmarked(site: Path):
    generate_routes(site)
    # Poison the manifest with protected and foreign paths.
    hand_made = site / "map" / "about"
    hand_made.mkdir()
    (hand_made / "index.html").write_text("<html>hand made</html>")
    (site / "map" / ".routes-manifest.json").write_text(json.dumps(
        {"pages": ["js/", "css/", "vendor/", "vendor/zod/", "about/", "../outside/", ""]}))
    outside = site / "outside"
    outside.mkdir()
    (outside / "index.html").write_text(MARKER)
    generate_routes(site)
    assert (site / "map" / "js" / "app.js").exists()
    assert (site / "map" / "vendor" / "zod" / "zod.mjs").exists()
    assert (site / "map" / "css" / "map.css").exists()
    assert (hand_made / "index.html").exists()
    assert (outside / "index.html").exists()
    assert (site / "map" / "index.html").read_text() == TEMPLATE


def test_refuses_to_overwrite_hand_written_page(site: Path):
    (site / "map" / "co").mkdir()
    (site / "map" / "co" / "index.html").write_text("<html>mine</html>")
    with pytest.raises(RuntimeError):
        generate_routes(site)


def test_skips_unsafe_slugs(site: Path):
    resorts, clusters, states = default_data()
    resorts.append({"id": "../evil", "name": "Evil", "cluster_id": "front", "state": "CO"})
    clusters.append({"id": "Bad Slug", "name": "Bad", "states": ["CO"], "resort_ids": []})
    states.append({"code": "JS", "name": "Protected", "cluster_ids": [], "resort_count": 0})
    write_build(site, resorts, clusters, states)
    n = generate_routes(site)
    assert n == 12
    assert not (site / "evil").exists()
    assert not (site / "map" / "co" / "Bad Slug").exists()
    assert (site / "map" / "js" / "app.js").read_text() == "// app"


def test_rejects_relative_asset_paths(site: Path):
    (site / "map" / "index.html").write_text(TEMPLATE.replace('src="/map/js/app.js"', 'src="js/app.js"'))
    with pytest.raises(ValueError):
        generate_routes(site)


def test_rejects_stub_as_template(site: Path):
    (site / "map" / "index.html").write_text(TEMPLATE.replace("<head>", f"<head>{MARKER}"))
    with pytest.raises(ValueError):
        generate_routes(site)


def test_idempotent(site: Path):
    generate_routes(site)
    before = read(site, "co/front/alpha-peak")
    mtime = (site / "map" / "co" / "front" / "alpha-peak" / "index.html").stat().st_mtime_ns
    generate_routes(site)
    assert read(site, "co/front/alpha-peak") == before
    assert (site / "map" / "co" / "front" / "alpha-peak" / "index.html").stat().st_mtime_ns == mtime


def test_cli(site: Path):
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "routes.py"), "--root", str(site)],
                         capture_output=True, text=True, check=True)
    assert "wrote 12 stub pages" in out.stdout


def test_cli_missing_build(tmp_path: Path):
    (tmp_path / "map").mkdir()
    (tmp_path / "map" / "index.html").write_text(TEMPLATE)
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "routes.py"), "--root", str(tmp_path)],
                         capture_output=True, text=True)
    assert out.returncode == 1
    assert "missing" in out.stderr


def test_real_shell_is_a_valid_template():
    from pipeline.routes import check_template
    check_template((REPO / "map" / "index.html").read_text())


def _jsonld(page: str) -> dict:
    return json.loads(page.split('<script type="application/ld+json">')[1].split("</script>")[0])


def _prerender(page: str) -> str:
    return page.split("<!-- prerender:start -->")[1].split("<!-- prerender:end -->")[0]


def test_resort_page_has_jsonld_and_prerendered_summary(site: Path):
    generate_routes(site)
    page = read(site, "co/front/alpha-peak")
    graph = _jsonld(page)["@graph"]
    resort = next(x for x in graph if x["@type"] == "SkiResort")
    assert resort["geo"] == {"@type": "GeoCoordinates", "latitude": 39.5, "longitude": -106.0}
    assert resort["url"] == "https://instructorgrindset.com/map/co/front/alpha-peak/"
    crumbs = next(x for x in graph if x["@type"] == "BreadcrumbList")["itemListElement"]
    assert [c["name"] for c in crumbs] == ["Snowsports Career Map", "Colorado", "Test Front", "Alpha Peak"]
    body = _prerender(page)
    assert '<h2 id="panel-heading" tabindex="-1">Alpha Peak</h2>' in body
    assert "Resort in Test Front, CO" in body
    assert "Test Owner" in body and "Operator compensation" in body and "Approved" in body
    assert "5,300" in body and "Last verified Oct 1, 2026" in body and 'href="https://example.com/src"' in body
    assert "Level 1" in body and "$21.50 to $24" in body
    assert 'href="/contact"' in body
    for href in ("/blog", "/reading-list", "/income-calculator", "/career-plan", "/priority-predictor"):
        assert f'href="{href}"' in body
    assert "Loading" not in body
    assert "—" not in body  # copy rules: no em dashes


def test_unverified_values_and_missing_career(site: Path):
    generate_routes(site)
    body = _prerender(read(site, "co/front/bravo-bowl"))
    assert "Not yet verified" in body
    assert "Career data is being collected." in body


def test_cluster_and_state_pages_list_children(site: Path):
    generate_routes(site)
    cluster = read(site, "co/front")
    body = _prerender(cluster)
    assert 'href="/map/co/front/alpha-peak/"' in body and 'href="/map/co/front/bravo-bowl/"' in body
    items = _jsonld(cluster)["@graph"][0]["mainEntity"]["itemListElement"]
    assert items[0]["url"].endswith("/map/co/front/alpha-peak/")
    assert 'href="/contact"' in body
    assert 'href="/map/co/front/"' in _prerender(read(site, "co"))


def test_reviewed_editorial_renders_with_sources(site: Path):
    resorts, clusters, states = default_data()
    resorts[0]["editorial"] = {"status": "reviewed", "overview_md": "A **steep** mountain. [More](https://example.com/x)",
                               "culture_md": None, "alexandre_take_md": "Go early.", "source_ids": ["test-src"]}
    resorts[1]["editorial"] = {"status": "draft", "overview_md": "Draft text"}
    clusters[0]["overview_md"] = "Cluster overview."
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    body = _prerender(read(site, "co/front/alpha-peak"))
    assert "<strong>steep</strong>" in body and 'href="https://example.com/x"' in body
    assert "Alexandre&#x27;s take" in body
    assert "Sources: " in body and "Test Source" in body
    assert "Draft text" not in read(site, "co/front/bravo-bowl")
    assert "Cluster overview." in _prerender(read(site, "co/front"))


def test_sitemap_keeps_site_entries_and_lists_canonical_map_pages(site: Path):
    original = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://instructorgrindset.com/</loc>
    <lastmod>2026-04-05</lastmod>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://instructorgrindset.com/map/old-page/</loc>
  </url>
</urlset>
"""
    (site / "sitemap.xml").write_text(original)
    generate_routes(site)
    first = (site / "sitemap.xml").read_text()
    generate_routes(site)
    assert (site / "sitemap.xml").read_text() == first  # idempotent
    assert """  <url>
    <loc>https://instructorgrindset.com/</loc>
    <lastmod>2026-04-05</lastmod>
    <priority>1.0</priority>
  </url>""" in first
    assert "/map/old-page/" not in first
    locs = [x.split("</loc>")[0] for x in first.split("<loc>")[1:]]
    assert "https://instructorgrindset.com/map/" in locs
    assert "https://instructorgrindset.com/map/ca/lake/" in locs
    assert "https://instructorgrindset.com/map/nv/lake/" not in locs  # non canonical duplicate
    # Tier B resorts stay out of the sitemap (their pages still exist); Tier A resorts are listed.
    assert "https://instructorgrindset.com/map/co/front/alpha-peak/" in locs
    assert "https://instructorgrindset.com/map/nv/lake/lake-east/" not in locs
    assert "https://instructorgrindset.com/map/co/front/bravo-bowl/" not in locs
    assert (site / "map" / "co" / "front" / "bravo-bowl" / "index.html").exists()
    assert "noindex" not in read(site, "co/front/bravo-bowl")
    assert sorted(locs) == sorted([
        "https://instructorgrindset.com/", "https://instructorgrindset.com/map/",
        "https://instructorgrindset.com/map/co/", "https://instructorgrindset.com/map/ca/", "https://instructorgrindset.com/map/nv/",
        "https://instructorgrindset.com/map/co/front/", "https://instructorgrindset.com/map/ca/lake/",
        "https://instructorgrindset.com/map/co/front/alpha-peak/"])
    assert len(locs) == len(set(locs))
    import xml.dom.minidom
    xml.dom.minidom.parseString(first)


def test_sitemap_lists_tier_a_resort_canonical_only(site: Path):
    resorts, clusters, states = default_data()
    resorts[3]["tier"] = "A"  # lake-east, in a CA and NV cluster, home state NV
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    text = (site / "sitemap.xml").read_text()
    assert "<loc>https://instructorgrindset.com/map/nv/lake/lake-east/</loc>" in text
    assert "/map/ca/lake/lake-east/" not in text  # non canonical duplicate
    assert "/map/ca/lake/lake-west/" not in text  # Tier B


def test_real_sitemap_keeps_site_entries_and_only_tier_a_resorts():
    text = (REPO / "sitemap.xml").read_text()
    locs = [x.split("</loc>")[0] for x in text.split("<loc>")[1:]]
    site_locs = [u for u in locs if not u.startswith("https://instructorgrindset.com/map/")]
    assert len(site_locs) == 13
    resorts = json.loads((REPO / "data" / "build" / "resorts.json").read_text())["resorts"]
    tier = {r["id"]: r["tier"] for r in resorts}
    resort_locs = [u for u in locs if u.count("/") == 7 and "/map/" in u]
    assert resort_locs and all(tier[u.rstrip("/").rsplit("/", 1)[1]] == "A" for u in resort_locs)


def test_no_sitemap_flag(site: Path):
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "routes.py"), "--root", str(site), "--no-sitemap"],
                         capture_output=True, text=True, check=True)
    assert "wrote 12 stub pages" in out.stdout
    assert not (site / "sitemap.xml").exists()


# ── Slim national index (data/build/map-index.json) ──

def _index_rows(site: Path) -> dict:
    data = json.loads((site / "data" / "build" / "map-index.json").read_text())
    return {row[0]: dict(zip(data["fields"], row)) for row in data["resorts"]}, data


def test_map_index_written_with_slim_rows(site: Path):
    generate_routes(site)
    rows, data = _index_rows(site)
    assert data["version"] == 1 and data["generated_at"] == "2026-10-11T00:00:00Z"
    assert data["fields"][:3] == ["id", "name", "tier"]
    assert set(rows) == {"alpha-peak", "bravo-bowl", "lake-west", "lake-east"}
    a = rows["alpha-peak"]
    assert (a["tier"], a["tier_a_status"], a["state"], a["cluster_id"], a["lat"], a["lon"]) == ("A", "approved", "CO", "front", 39.5, -106.0)
    assert a["acres"] == 5300 and a["facility_type"] == "ski_area"
    assert a["climate_available"] is False and a["detail_available"] is False
    # Without resorts.geojson, size_rank uses the config breaks (5300 acres is the top rank).
    assert a["size_rank"] == 5 and rows["bravo-bowl"]["size_rank"] == 1
    # Unknown values stay null; nothing is invented.
    b = rows["bravo-bowl"]
    assert b["acres"] is None and b["season_length_days"] is None and b["snowfall_annual_in"] is None
    assert b["professionalism_score"] is None and b["lucrativeness_mid"] is None


def test_map_index_takes_recolor_values_from_resorts_geojson(site: Path):
    build = site / "data" / "build"
    (build / "resorts.geojson").write_text(json.dumps({"type": "FeatureCollection", **_env(), "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-106.0, 39.5]},
         "properties": {"id": "alpha-peak", "size_rank": 3, "season_length_days": 150, "snowfall_annual_in": 310.5,
                        "professionalism_score": 72, "lucrativeness_mid": 6100}}]}))
    generate_routes(site)
    rows, _ = _index_rows(site)
    a = rows["alpha-peak"]
    assert (a["size_rank"], a["season_length_days"], a["snowfall_annual_in"], a["professionalism_score"], a["lucrativeness_mid"]) \
        == (3, 150, 310.5, 72, 6100)
    assert rows["lake-west"]["snowfall_annual_in"] is None


def test_map_index_skips_unsafe_ids_and_missing_coordinates(site: Path):
    resorts, clusters, states = default_data()
    resorts.append({**resorts[1], "id": "../evil", "name": "Evil"})
    resorts.append({**resorts[1], "id": "no-coords", "name": "No Coords", "lat": None})
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    rows, _ = _index_rows(site)
    assert "../evil" not in rows and "no-coords" not in rows


def test_map_index_idempotent(site: Path):
    generate_routes(site)
    path = site / "data" / "build" / "map-index.json"
    first = path.read_text()
    mtime = path.stat().st_mtime_ns
    generate_routes(site)
    assert path.read_text() == first and path.stat().st_mtime_ns == mtime


def test_resort_page_embeds_its_record_and_cited_sources(site: Path):
    generate_routes(site)
    import re as _re
    page = read(site, "co/front/alpha-peak")
    m = _re.search(r'<script type="application/json" id="resort-record">(.*?)</script>', page, _re.S)
    assert m, "resort page carries its record"
    data = json.loads(m.group(1))
    assert data["resort"]["id"] == "alpha-peak" and data["resort"]["owner"]["value"] == "Test Owner"
    # Only the sources the record cites (alpha-peak cites none in its sources list here).
    assert data["sources"] == []
    # Names with markup never break out of the script element.
    bravo = read(site, "co/front/bravo-bowl")
    raw = _re.search(r'id="resort-record">(.*?)</script>', bravo, _re.S).group(1)
    assert "<" not in raw and json.loads(raw)["resort"]["name"] == "Bravo & Sons <Bowl>"
    # State and cluster pages carry no record.
    assert 'id="resort-record"' not in read(site, "co") and 'id="resort-record"' not in read(site, "co/front")


def test_embedded_sources_follow_the_record(site: Path):
    resorts, clusters, states = default_data()
    resorts[0]["sources"] = ["test-src", "missing-src"]
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    import re as _re
    raw = _re.search(r'id="resort-record">(.*?)</script>', read(site, "co/front/alpha-peak"), _re.S).group(1)
    assert [s["id"] for s in json.loads(raw)["sources"]] == ["test-src"]
