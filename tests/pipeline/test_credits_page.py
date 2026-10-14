"""The hand written Sources and credits page (/map/credits/) and its links from the route stubs."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from pipeline.routes import generate_routes

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "map" / "credits" / "index.html"
CREDITS_LINK = '<a href="/map/credits/">Sources and credits</a>'

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
</head>
<body><div id="panel-content"><!-- prerender:start --><p>Loading</p><!-- prerender:end --></div>
<script type="module" src="/map/js/app.js"></script></body>
</html>
"""


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "map").mkdir()
    (tmp_path / "map" / "index.html").write_text(TEMPLATE)
    build = tmp_path / "data" / "build"
    build.mkdir(parents=True)
    env = {"version": 1, "generated_at": "2026-10-11T00:00:00Z"}
    resort = {"id": "alpha-peak", "name": "Alpha Peak", "cluster_id": "front", "state": "CO", "tier": "A", "lat": 39.5,
              "lon": -106.0, "tier_a_criteria": [], "tier_a_status": None, "owner": None, "size": {}, "season": {}, "career": None}
    (build / "resorts.json").write_text(json.dumps({**env, "resorts": [resort]}))
    (build / "clusters.geojson").write_text(json.dumps({"type": "FeatureCollection", **env, "features": [
        {"type": "Feature", "geometry": None, "properties": {"id": "front", "name": "Front", "states": ["CO"], "resort_ids": ["alpha-peak"]}}]}))
    (build / "states.json").write_text(json.dumps({**env, "states": [
        {"code": "CO", "name": "Colorado", "cluster_ids": ["front"], "resort_count": 1}]}))
    (build / "sources.json").write_text(json.dumps({**env, "sources": []}))
    return tmp_path


def _footer(page: str) -> str:
    body = page.split("<!-- prerender:start -->")[1].split("<!-- prerender:end -->")[0]
    return body.split('<footer class="panel-footer">')[1].split("</footer>")[0]


def test_every_stub_footer_links_the_credits_page(site: Path):
    generate_routes(site)
    for rel in ("co", "co/front", "co/front/alpha-peak"):
        assert CREDITS_LINK in _footer((site / "map" / rel / "index.html").read_text()), rel


def test_credits_page_is_never_touched_and_joins_the_sitemap(site: Path):
    page = site / "map" / "credits" / "index.html"
    page.parent.mkdir()
    page.write_text("<!doctype html><title>Credits</title>")
    generate_routes(site)
    generate_routes(site)
    assert page.read_text() == "<!doctype html><title>Credits</title>"
    sitemap = (site / "sitemap.xml").read_text()
    assert sitemap.count("<loc>https://instructorgrindset.com/map/credits/</loc>") == 1
    assert "credits/" not in json.loads((site / "map" / ".routes-manifest.json").read_text())["pages"]


def test_sitemap_leaves_out_a_missing_credits_page(site: Path):
    generate_routes(site)
    assert "/map/credits/" not in (site / "sitemap.xml").read_text()


# ── The real page ──

class _Text(HTMLParser):
    """Visible prose: text outside <code>, <style>, <script> and <head>."""

    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []
        self.hrefs: list[str] = []
        self.scripts: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("code", "style", "script", "head"):
            self.skip += 1
        if tag == "script":
            self.scripts.append(a)
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("code", "style", "script", "head"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


@pytest.fixture(scope="module")
def page() -> tuple[str, _Text]:
    html = PAGE.read_text(encoding="utf-8")
    p = _Text()
    p.feed(html)
    return html, p


def test_page_loads_no_map_javascript(page):
    html, p = page
    assert all(not s.get("src") for s in p.scripts), "the credits page loads no external script"
    assert "/map/js/" not in html and "maplibre-gl.js" not in html and 'type="module"' not in html
    assert '<aside id="sidebar-placeholder"' in html and 'fetch("/partials/sidebar.html' in html


def test_page_credits_every_source(page):
    html, p = page
    text = " ".join(p.parts)
    for name in ("OpenSkiMap", "OpenStreetMap contributors", "Open Database License (ODbL)", "NOAA NCEI U.S. Climate Normals, 1991 to 2020",
                 "USGS The National Map", "NRCS SNOTEL", "Open Meteo", "ERA5", "CC BY 4.0", "Wikipedia", "CC BY SA 4.0",
                 "US Census Bureau", "Public domain"):
        assert name in text, name
    assert "us-atlas" in html
    for href in ("https://opendatacommons.org/licenses/odbl/", "https://www.openstreetmap.org/copyright",
                 "https://creativecommons.org/licenses/by/4.0/", "https://creativecommons.org/licenses/by-sa/4.0/", "/contact", "/map/"):
        assert href in p.hrefs, href
    assert "Book a Free Consultation" in text


def test_page_credits_zillow_and_the_rent_sources(page):
    """Owner decision 2026-10-14: Zillow is credited on /map/credits/, with a link to Zillow Research."""
    html, p = page
    text = " ".join(" ".join(p.parts).split())
    assert "Rent data: Zillow Observed Rent Index (ZORI), Zillow Research" in text
    assert "https://www.zillow.com/research/data/" in p.hrefs and 'href="#rent"' in html
    for name in ("Zumper", "Rentometer", "Redfin", "Foreclosure.com", "American Community Survey", "HUD Fair Market Rent"):
        assert name in text, name


def test_page_credits_the_transit_feeds_and_their_licenses(page):
    import yaml
    html, p = page
    assert "Mobility Database" in " ".join(p.parts) and "https://mobilitydatabase.org/" in p.hrefs
    feeds = yaml.safe_load((REPO / "data" / "raw" / "transit" / "feeds.yaml").read_text())["feeds"]
    for f in feeds:
        if f.get("license"):
            assert f["license"] in p.hrefs, f"{f['agency']} license {f['license']}"


def test_page_names_each_vendored_library_with_its_version_and_license(page):
    html, p = page
    versions = json.loads((REPO / "map" / "vendor" / "VERSIONS.json").read_text())
    names = {"maplibre-gl": "MapLibre GL JS", "chart.js": "Chart.js", "zod": "Zod"}
    assert set(versions) == set(names)
    for pkg, version in versions.items():
        assert re.search(rf"{re.escape(names[pkg])}</a> {re.escape(version)}</h3>", html), f"{names[pkg]} {version}"
        licenses = [h for h in p.hrefs if h.startswith(f"/map/vendor/{pkg}/LICENSE")]
        assert len(licenses) == 1 and (REPO / licenses[0].lstrip("/")).is_file(), pkg


def test_page_explains_confidence_levels_and_the_report_form(page):
    _, p = page
    text = " ".join(p.parts)
    for level in ("Published", "Reported", "Estimated"):
        assert f">{level}</span>" in PAGE.read_text(), level
    assert "Formspree" in text and "by email" in text and "3 or more approved reports" in text
    assert "Individual reports stay private." in text


def test_privacy_note_matches_what_the_form_sends():
    """If the report form sends a new field, describe it in the privacy note on /map/credits/ and update this list."""
    js = (REPO / "map" / "js" / "panel.js").read_text()
    payload = js.split("lastPayload = {", 1)[1].split("};", 1)[0]
    keys = set(re.findall(r"(?:^|[{,])\s*([a-z_]+):", payload, re.M))
    assert keys == {"_subject", "resort_id", "kind", "season", "cert_level", "hourly_usd", "monthly_usd",
                    "housing_site_id", "proof_url", "submitted_at"}
    assert "credentials: 'omit'" in js


def test_page_prose_follows_the_style_rules(page):
    _, p = page
    text = " ".join(p.parts)
    assert "—" not in text and "–" not in text, "no em or en dashes"
    assert not re.search(r"\w-\w", text), re.findall(r"\S*\w-\w\S*", text)
    assert not re.search(r"\b(is|are) not\b|\b(isn't|aren't)\b", text), "no negative definitions"


def test_real_sitemap_lists_the_credits_page():
    assert "<loc>https://instructorgrindset.com/map/credits/</loc>" in (REPO / "sitemap.xml").read_text()
