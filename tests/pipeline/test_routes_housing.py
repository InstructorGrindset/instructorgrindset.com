"""Route stubs for the housing links (pipeline/routes.py render_housing). All data here is synthetic."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.routes import Ctx, generate_routes, render_housing

from tests.pipeline.test_routes import _prerender, default_data, read, site, write_build  # noqa: F401 (fixture)

CITE = {"source_id": "test-src", "as_of": "2026-10-14", "confidence": "estimated", "note": None}


def tool(name, url, kind="facebook_group", **kw):
    return {"name": name, "kind": kind, "url": url, "why": f"Sources name {name}.", "scope": "cluster", "named_by": 1,
            "source_ids": ["test-src"], **CITE, **kw}


HOUSING_SEARCH = {
    "furnished_finder": {"town": "Alpha <Town>", "url": "https://www.furnishedfinder.com/housing/us--co--alpha-town", **CITE},
    "tools": [tool("Town Housing Authority", "https://housing.example.gov/", "housing_authority", scope="resort", named_by=3),
              tool("Alpha Rooms Group", "https://www.facebook.com/groups/alpha-rooms"),
              tool("Bad Link", "javascript:alert(1)"),
              tool("Future Kind", "https://example.com/future", "new_kind")],
}


def test_resort_page_prerenders_the_furnished_finder_link_and_the_tools(site: Path):
    resorts, clusters, states = default_data()
    resorts[0]["housing_search"] = HOUSING_SEARCH
    write_build(site, resorts, clusters, states)
    generate_routes(site)
    body = _prerender(read(site, "co/front/alpha-peak"))
    section = body.split('<section class="panel-section housing-search">')[1].split("</section>")[0]
    assert section.startswith("<h3>Housing</h3><h4 class=\"sub\">Furnished rentals</h4>")
    assert ('<a class="ff-link" href="https://www.furnishedfinder.com/housing/us--co--alpha-town" target="_blank" '
            'rel="noopener noreferrer">Furnished Finder listings near Alpha &lt;Town&gt;</a> '
            '<span class="badge badge-estimated">Estimated</span>') in section
    assert "furnishedfinder.com · opens in a new tab · Last verified Oct 14, 2026" in section
    # tools keep the build's rank, as plain links with their kind; unsafe links are dropped
    names = re.findall(r'class="tool-link" href="[^"]+" target="_blank" rel="noopener noreferrer">([^<]+)</a>', section)
    assert names == ["Town Housing Authority", "Alpha Rooms Group", "Future Kind"]
    assert '<span class="tag tool-kind">Housing authority</span>' in section and ">New kind</span>" in section
    assert "javascript:" not in body
    # the section sits after Jobs and before the footer; the embedded record carries the same data for the panel
    assert body.index("<h3>Jobs</h3>") < body.index("<h3>Housing</h3>") < body.index('<footer class="panel-footer">')
    rec = json.loads(re.search(r'id="resort-record">(.*?)</script>', read(site, "co/front/alpha-peak"), re.S).group(1))
    assert rec["resort"]["housing_search"]["tools"][0]["name"] == "Town Housing Authority"


def test_no_section_without_links(site: Path):
    generate_routes(site)
    assert "housing-search" not in _prerender(read(site, "co/front/alpha-peak"))
    ctx = Ctx({}, {}, {}, {})
    assert render_housing(ctx, {"housing_search": None}) == ""
    assert render_housing(ctx, {"housing_search": {"furnished_finder": {"town": "X", "url": "http://furnishedfinder.com/x"},
                                                   "tools": []}}) == ""
    only_ff = render_housing(ctx, {"housing_search": {"furnished_finder": HOUSING_SEARCH["furnished_finder"], "tools": []}})
    assert "Furnished rentals" in only_ff and "Where people find housing here" not in only_ff
