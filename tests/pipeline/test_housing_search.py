"""Tests for pipeline/housing_search.py (Furnished Finder links and ranked housing search tools).
All data here is synthetic: fictional resorts and clusters, sources on example domains."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline.housing_search import (
    independent_sources, load_housing_search, rank_tools, resort_housing_search, url_key,
)
from pipeline.schemas.housing_search import HousingSearch, HousingToolIn
from pipeline.sources import SourceRegistry

KNOWN = {"alpha-peak", "bravo-bowl"}
CLUSTERS = {"front-range-fixture", "other-co"}
FF = ["furnishedfinder.com"]


def src(id, url):
    return {"id": id, "url": url, "title": f"Synthetic {id}", "accessed_date": "2026-10-14"}


SOURCES = [
    src("forum-thread-one", "https://www.forum.example.com/t/housing-1"),
    src("forum-thread-two", "https://old.forum.example.com/t/housing-2"),   # same publisher as thread one
    src("news-housing", "https://news.example.org/housing-crunch"),
    src("guide-housing", "https://guide.example.net/where-to-live"),
    src("ff-alpha", "https://www.furnishedfinder.com/housing/us--co--alpha-town"),
]


def ff(**kw):
    return {"resort_id": "alpha-peak", "town": "Alpha Town",
            "url": "https://www.furnishedfinder.com/housing/us--co--alpha-town", "source_id": "ff-alpha",
            "as_of": "2026-10-14", "confidence": "estimated", "note": None, **kw}


def tool(name, **kw):
    base = {"scope": "cluster", "scope_id": "front-range-fixture", "name": name, "kind": "facebook_group",
            "url": f"https://www.example.com/{name.lower().replace(' ', '')}", "why": f"Sources say {name} lists rooms.",
            "source_ids": ["news-housing"], "as_of": "2026-10-14", "confidence": "estimated", "note": None}
    return {**base, **kw}


def write(tmp_path: Path, name="batch.yaml", furnished_finder=(), tools=(), sources=SOURCES):
    d = tmp_path / "housing_search"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.safe_dump({"batch": "housing-search-test", "researched_date": "2026-10-14",
                                          "sources": list(sources), "furnished_finder": list(furnished_finder),
                                          "tools": list(tools)}, sort_keys=False))


def load(tmp_path: Path, registry=None):
    registry = registry or SourceRegistry()
    data, errors = load_housing_search(tmp_path, registry, KNOWN, CLUSTERS, FF, rel=lambda p: p.name)
    return data, errors, registry


def test_missing_or_empty_folder_gives_nothing(tmp_path: Path):
    data, errors, _ = load(tmp_path)
    assert errors == [] and not data.furnished_finder and not data.resort_tools and not data.cluster_tools
    (tmp_path / "housing_search").mkdir()
    (tmp_path / "housing_search" / "empty.yaml").write_text("")
    data, errors, _ = load(tmp_path)
    assert errors == [] and not data.furnished_finder


def test_resort_output_ranks_resort_tools_first_then_cluster_by_independent_sources(tmp_path: Path):
    write(tmp_path, furnished_finder=[ff()], tools=[
        tool("Zeta Group", source_ids=["news-housing", "guide-housing"]),                        # cluster, 2
        tool("Alpha Board", source_ids=["news-housing"]),                                        # cluster, 1
        tool("Beta Board", source_ids=["guide-housing"]),                                        # cluster, 1
        tool("Forum Pick", source_ids=["forum-thread-one", "forum-thread-two"]),                # cluster, 1 publisher
        tool("Staff Portal", scope="resort", scope_id="alpha-peak", kind="employer_portal",
             url="https://jobs.alpha.example.com/housing", source_ids=["news-housing"]),         # resort, 1
        tool("Town Authority", scope="resort", scope_id="alpha-peak", kind="housing_authority",
             url="https://housing.alpha.example.gov/", source_ids=["news-housing", "guide-housing", "forum-thread-one"]),
    ])
    data, errors, reg = load(tmp_path)
    assert errors == []
    out = resort_housing_search(data, "alpha-peak", "front-range-fixture", reg, max_tools=8)
    assert out["furnished_finder"] == {"town": "Alpha Town", "url": "https://www.furnishedfinder.com/housing/us--co--alpha-town",
                                       "source_id": "ff-alpha", "as_of": "2026-10-14", "confidence": "estimated", "note": None}
    assert [(t["name"], t["scope"], t["named_by"]) for t in out["tools"]] == [
        ("Town Authority", "resort", 3), ("Staff Portal", "resort", 1),
        ("Zeta Group", "cluster", 2), ("Alpha Board", "cluster", 1), ("Beta Board", "cluster", 1),
        ("Forum Pick", "cluster", 1)]
    first = out["tools"][0]
    assert first == {"name": "Town Authority", "kind": "housing_authority", "url": "https://housing.alpha.example.gov/",
                     "why": "Sources say Town Authority lists rooms.", "scope": "resort", "named_by": 3,
                     "source_ids": ["news-housing", "guide-housing", "forum-thread-one"], "as_of": "2026-10-14",
                     "confidence": "estimated", "note": None}
    # another resort in the cluster gets the cluster tools only, and no Furnished Finder link
    other = resort_housing_search(data, "bravo-bowl", "front-range-fixture", reg, max_tools=8)
    assert other["furnished_finder"] is None and [t["scope"] for t in other["tools"]] == ["cluster"] * 4
    # max_tools keeps the best ranked; a resort with nothing gets None
    assert [t["name"] for t in resort_housing_search(data, "alpha-peak", "front-range-fixture", reg, 3)["tools"]] == [
        "Town Authority", "Staff Portal", "Zeta Group"]
    assert resort_housing_search(data, "bravo-bowl", "other-co", reg) is None


def test_independent_sources_count_publishers_once(tmp_path: Path):
    write(tmp_path)
    _, _, reg = load(tmp_path)
    assert independent_sources(["forum-thread-one", "forum-thread-two"], reg) == 1
    assert independent_sources(["forum-thread-one", "news-housing", "guide-housing"], reg) == 3
    assert independent_sources(["unregistered-id"], reg) == 1


def test_a_url_listed_for_the_resort_and_its_cluster_shows_once_in_the_resort_group(tmp_path: Path):
    shared = "https://www.example.com/rooms"
    write(tmp_path, tools=[tool("Rooms Group", url=shared, source_ids=["news-housing", "guide-housing"]),
                           tool("Rooms Group Local", scope="resort", scope_id="alpha-peak", url="https://example.com/rooms/")])
    data, errors, reg = load(tmp_path)
    assert errors == []
    out = resort_housing_search(data, "alpha-peak", "front-range-fixture", reg)
    assert [(t["name"], t["scope"]) for t in out["tools"]] == [("Rooms Group Local", "resort")]
    assert url_key("https://WWW.Example.com/rooms/") == url_key("https://example.com/rooms") == "example.com/rooms"
    assert url_key("https://example.com/r?id=1#top") == "example.com/r?id=1"


def test_ties_break_by_name_case_blind():
    reg = SourceRegistry()
    tools = [HousingToolIn.model_validate(tool(n, url=f"https://example.com/{i}")) for i, n in enumerate(["beta", "Alpha", "gamma"])]
    assert [t["name"] for t in rank_tools([], tools, reg)] == ["Alpha", "beta", "gamma"]


@pytest.mark.parametrize("bad, message", [
    ({"url": "http://www.example.com/rooms"}, "url must be an https link"),
    ({"kind": "newspaper"}, "Input should be"),
    ({"scope": "state"}, "Input should be"),
    ({"confidence": "certain"}, "Input should be"),
    ({"source_ids": ["no-such-source"]}, "not defined in any sources list"),
    ({"source_ids": []}, "at least 1 item"),
    ({"source_ids": ["news-housing", "news-housing"]}, "source id listed twice"),
    ({"why": "Rooms go fast — apply early."}, "no em or en dashes"),
    ({"scope_id": "ghost-cluster"}, "matches no cluster in this build"),
    ({"scope": "resort", "scope_id": "ghost-resort"}, "matches no resort in this build"),
    ({"extra": 1}, "Extra inputs are not permitted"),
])
def test_tool_validation(tmp_path: Path, bad, message):
    write(tmp_path, tools=[tool("Rooms Group", **bad)])
    _, errors, _ = load(tmp_path)
    assert any(message in e for e in errors), errors
    assert all(e.startswith("batch.yaml") for e in errors)


@pytest.mark.parametrize("bad, message", [
    ({"url": "http://www.furnishedfinder.com/housing/us--co--alpha-town"}, "url must be an https link"),
    ({"url": "https://www.example.com/alpha-town"}, "is not a Furnished Finder domain"),
    ({"resort_id": "ghost-resort"}, "no such resort in this build"),
    ({"source_id": "no-such-source"}, "not defined in any sources list"),
    ({"confidence": "sure"}, "Input should be"),
    ({"town": ""}, "at least 1 character"),
])
def test_furnished_finder_validation(tmp_path: Path, bad, message):
    write(tmp_path, furnished_finder=[ff(**bad)])
    _, errors, _ = load(tmp_path)
    assert any(message in e for e in errors), errors


def test_duplicates_fail(tmp_path: Path):
    write(tmp_path, furnished_finder=[ff(), ff()])
    _, errors, _ = load(tmp_path)
    assert any("furnished_finder lists a resort twice: alpha-peak" in e for e in errors)
    write(tmp_path, tools=[tool("One"), tool("One again", url="https://www.example.com/one")])
    _, errors, _ = load(tmp_path)
    assert any("tool url listed twice for one scope" in e for e in errors)
    # across files
    write(tmp_path, furnished_finder=[ff()], tools=[tool("One")])
    write(tmp_path, name="second.yaml", furnished_finder=[ff()], tools=[tool("One", url="https://example.com/one/")],
          sources=[])
    _, errors, _ = load(tmp_path)
    assert any("also has a Furnished Finder link in batch.yaml" in e for e in errors)
    assert any("second.yaml tool 'One': url also listed for cluster 'front-range-fixture' in batch.yaml" in e for e in errors)


def test_output_schema_needs_a_link_or_a_tool():
    with pytest.raises(ValidationError, match="needs a furnished_finder link or at least one tool"):
        HousingSearch.model_validate({"furnished_finder": None, "tools": []})


# ------------------------------------------------------------------ build integration and the housing site town
from pipeline.housing import site_town, town_from_address  # noqa: E402
from pipeline.schemas.housing import HousingSite  # noqa: E402

from tests.pipeline.test_build import HOUSING_YAML, _load, _run  # noqa: E402

SEARCH_YAML = """
batch: housing-search-fixture
researched_date: 2026-10-14
sources:
  - {id: fixture-ff-peak, url: "https://www.furnishedfinder.com/housing/us--co--fixture-town", title: Fixture Furnished Finder page, accessed_date: 2026-10-14}
  - {id: fixture-housing-news, url: "https://news.example.org/housing", title: Fixture housing news, accessed_date: 2026-10-14}
  - {id: fixture-housing-guide, url: "https://guide.example.net/housing", title: Fixture housing guide, accessed_date: 2026-10-14}
furnished_finder:
  - {resort_id: fixture-peak, town: Fixture Town, url: "https://www.furnishedfinder.com/housing/us--co--fixture-town", source_id: fixture-ff-peak, as_of: 2026-10-14, confidence: estimated, note: null}
tools:
  - {scope: cluster, scope_id: i70-corridor, name: Corridor Rooms Group, kind: facebook_group, url: "https://www.facebook.com/groups/fixture-rooms", why: Both sources say the group lists rooms first., source_ids: [fixture-housing-news, fixture-housing-guide], as_of: 2026-10-14, confidence: estimated, note: null}
  - {scope: resort, scope_id: fixture-peak, name: Peak Staff Housing, kind: employer_portal, url: "https://jobs.example.com/housing", why: The guide says staff apply here first., source_ids: [fixture-housing-guide], as_of: 2026-10-14, confidence: estimated, note: null}
"""


def test_build_attaches_housing_search_and_cites_its_sources(repo_root, raw_dir, tmp_path):
    out = tmp_path / "build"
    (raw_dir / "housing_search").mkdir()
    (raw_dir / "housing_search" / "fixture.yaml").write_text(SEARCH_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak = by["fixture-peak"]
    hs = peak["housing_search"]
    assert hs["furnished_finder"]["town"] == "Fixture Town"
    assert [(t["name"], t["scope"], t["named_by"]) for t in hs["tools"]] == [
        ("Peak Staff Housing", "resort", 1), ("Corridor Rooms Group", "cluster", 2)]
    assert {"fixture-ff-peak", "fixture-housing-news", "fixture-housing-guide"} <= set(peak["sources"])
    # the training center shares the cluster: cluster tools only; a resort in another cluster has none
    assert [t["name"] for t in by["fixture-peak-training-center"]["housing_search"]["tools"]] == ["Corridor Rooms Group"]
    assert by["fixture-basin"]["housing_search"] is None
    assert "fixture-housing-guide" in {s["id"] for s in _load(out, "sources.json")["sources"]}


def test_build_fails_on_an_unknown_cluster(repo_root, raw_dir, tmp_path):
    from pipeline.tier_a import BuildError
    (raw_dir / "housing_search").mkdir()
    (raw_dir / "housing_search" / "fixture.yaml").write_text(SEARCH_YAML.replace("scope_id: i70-corridor", "scope_id: ghost-range"))
    with pytest.raises(BuildError) as e:
        _run(repo_root, raw_dir, tmp_path / "build", tmp_path)
    assert any("tool 'Corridor Rooms Group': scope_id 'ghost-range' matches no cluster" in m for m in e.value.errors)


def test_housing_file_fills_the_detail_pins_with_their_town(repo_root, raw_dir, tmp_path):
    out = tmp_path / "build"
    (raw_dir / "housing").mkdir()
    (raw_dir / "housing" / "employee_housing_fixture.yaml").write_text(
        HOUSING_YAML.replace('"1 Fixture Way"', '"1 Fixture Way, Fixture Town, CO 80000"')
        .replace("    name: Town Flats\n", "    name: Town Flats\n    town: Flat Town\n"))
    result = _run(repo_root, raw_dir, out, tmp_path)
    d = _load(out / "detail", "fixture-peak.json")
    assert [(s["id"], s["precision"], s["town"]) for s in d["housing"]] == [
        ("peak-staff-lodge", "building", "Fixture Town"), ("peak-town-flats", "neighborhood", "Flat Town")]
    assert not any("Tier B" in w for w in result.warnings)


def test_housing_sites_at_tier_b_resorts_warn(repo_root, raw_dir, tmp_path):
    (raw_dir / "housing").mkdir()
    (raw_dir / "housing" / "fixture.yaml").write_text(HOUSING_YAML.replace("resort_id: fixture-peak", "resort_id: unrelated-bowl"))
    result = _run(repo_root, raw_dir, tmp_path / "build", tmp_path)
    assert "housing sites at Tier B resort 'unrelated-bowl' are not shown: detail maps are Tier A only" in result.warnings


@pytest.mark.parametrize("address, town", [
    ("1 Example Way, Example, CO", "Example"),
    ("123 Main St, Vail, CO 81657", "Vail"),
    ("123 Main St, Vail CO 81657", "Vail"),
    ("123 Main St, Vail, CO 81657-1234, USA", "Vail"),
    ("12 A St, Unit 4, Big Sky, MT 59716", "Big Sky"),
    ("123 Main St", None),
    ("Vail, CO", None),
    ("PO Box 7, 81657", None),
    ("123 Main St, Frisco, Colorado", None),
    ("", None),
    (None, None),
])
def test_town_from_address(address, town):
    assert town_from_address(address) == town


def test_site_town_prefers_the_sourced_town():
    sv = {"source_id": "s", "as_of": "2026-10-01", "confidence": "published"}
    base = {"id": "s1", "resort_id": "alpha-peak", "name": "Lodge", "type": "resort_owned", "lat": 39.6, "lon": -106.3,
            "location_source_id": "s", "precision": "building", "address": {"value": "1 Way, Alpha Town, CO 80000", **sv},
            "confidence": "published", "verified_date": "2026-10-01"}
    assert site_town(HousingSite.model_validate(base)) == "Alpha Town"
    assert site_town(HousingSite.model_validate({**base, "town": "Beta Town"})) == "Beta Town"
    assert site_town(HousingSite.model_validate({**base, "precision": "neighborhood", "address": None})) is None


# ------------------------------------------------- unspecified type and the listing source (coordinator 2026-10-14)
from pipeline.housing import cheapest_employee_site  # noqa: E402


def _site(**kw):
    base = {"id": "s1", "resort_id": "alpha-peak", "name": "Listed Lodge", "type": "unspecified", "lat": 39.6,
            "lon": -106.3, "location_source_id": "loc", "listing_source_id": "careers", "precision": "neighborhood",
            "confidence": "estimated", "verified_date": "2026-10-14"}
    return HousingSite.model_validate({**base, **kw}, context={"source_ids": {"loc", "careers", "rent"}})


def test_unspecified_sites_and_their_listing_source():
    s = _site()
    assert s.type == "unspecified" and s.listing_source_id == "careers"
    assert _site(listing_source_id=None).listing_source_id is None
    with pytest.raises(ValidationError, match="not defined in any sources list"):
        _site(listing_source_id="nowhere")
    with pytest.raises(ValidationError):
        _site(type="unknown")
    # a site the resort lists for its staff counts as employee housing, whatever its tenure
    rent = {"value": {"low": 500, "high": 700}, "source_id": "rent", "as_of": "2026-10-14", "confidence": "published"}
    best = cheapest_employee_site([_site(rent_monthly=rent), _site(id="s2", type="market", rent_monthly={**rent, "value": {"low": 100, "high": 200}})])
    assert best[0].id == "s1"


def test_build_carries_the_listing_source_into_the_detail_pins(repo_root, raw_dir, tmp_path):
    out = tmp_path / "build"
    (raw_dir / "housing").mkdir()
    (raw_dir / "housing" / "employee_housing_fixture.yaml").write_text(HOUSING_YAML.replace(
        "    type: market\n", "    type: unspecified\n    listing_source_id: fixture-housing-page\n"))
    _run(repo_root, raw_dir, out, tmp_path)
    d = _load(out / "detail", "fixture-peak.json")
    flats = next(s for s in d["housing"] if s["id"] == "peak-town-flats")
    lodge = next(s for s in d["housing"] if s["id"] == "peak-staff-lodge")
    assert flats["type"] == "unspecified" and flats["listing_source_id"] == "fixture-housing-page"
    assert lodge["listing_source_id"] is None
    from pipeline.tier_a import BuildError
    (raw_dir / "housing" / "employee_housing_fixture.yaml").write_text(HOUSING_YAML.replace(
        "    type: market\n", "    type: unspecified\n    listing_source_id: ghost-page\n"))
    with pytest.raises(BuildError) as e:
        _run(repo_root, raw_dir, tmp_path / "build2", tmp_path)
    assert any("site 'peak-town-flats': listing_source_id" in m and "ghost-page" in m for m in e.value.errors)
