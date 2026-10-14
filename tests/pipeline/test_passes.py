"""Tests for pipeline/passes.py and the pass membership contract (ROADMAP item 12, the Epic and Ikon filter).

Every resort, pass page and source below is synthetic (example domains), except the last tests, which validate the
real data/raw/passes batch against the real build."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline import routes
from pipeline.config import PassesDefs, load_config
from pipeline.passes import as_output, load_passes
from pipeline.schemas.passes import PASS_ORDER, PassMembership, check_season, previous_season
from pipeline.schemas.resort import Resort
from pipeline.sources import SourceRegistry

REPO = Path(__file__).resolve().parents[2]
DOMAINS = {"epic": {"epicpass.com"}, "ikon": {"ikonpass.com"}, "mountain_collective": {"mountaincollective.com"},
           "indy": {"indyskipass.com"}}
NOTE = "From a search result for this page; page not opened"
SRC_EPIC = {"id": "pass-epic-page", "url": "https://www.epicpass.com/passes/epic-pass.aspx", "title": "Epic Pass",
            "accessed_date": "2026-10-13", "note": NOTE}
SRC_IKON = {"id": "pass-ikon-blog", "url": "https://lifted.ikonpass.com/whats-new/", "title": "Ikon blog",
            "accessed_date": "2026-10-13", "note": NOTE}
SRC_MC = {"id": "pass-mc-home", "url": "https://mountaincollective.com/", "title": "Mountain Collective",
          "accessed_date": "2026-10-13"}
SRC_WIKI = {"id": "pass-wiki-list", "url": "https://en.wikipedia.org/wiki/Example_pass_list", "title": "A pass list",
            "accessed_date": "2026-10-13", "note": "List as of the 2025/26 season"}
SRC_OWNER = {"id": "pass-owner-site", "url": "https://www.example-owner.com/resorts", "title": "Owner portfolio",
             "accessed_date": "2026-10-13"}
SOURCES = (SRC_EPIC, SRC_IKON, SRC_MC, SRC_WIKI, SRC_OWNER)
KNOWN = {"alpha-peak", "bravo-bowl", "charlie-hill"}


def mem(resort_id="alpha-peak", pass_="epic", **kw):
    src = {"epic": "pass-epic-page", "ikon": "pass-ikon-blog", "mountain_collective": "pass-mc-home"}.get(pass_, "pass-epic-page")
    out = {"resort_id": resort_id, "pass": pass_, "access": "unlimited", "season": "2026/27", "source_id": src,
           "as_of": "2026-10-13", "confidence": "estimated", "note": NOTE}
    out.update(kw)
    return out


def write(tmp_path: Path, memberships, name="2026_27.yaml", sources=SOURCES):
    d = tmp_path / "passes"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.safe_dump({"batch": "passes-test", "researched_date": "2026-10-13",
                                          "sources": list(sources), "memberships": memberships}))


def run(tmp_path: Path, memberships=None, season="2026/27", supplemental=frozenset(), name="2026_27.yaml", fallback=True):
    if memberships is not None:
        write(tmp_path, memberships, name)
    return load_passes(tmp_path, SourceRegistry(), set(KNOWN), season, DOMAINS, set(supplemental), rel=lambda p: p.name,
                       fallback_previous=fallback)


# ---------------------------------------------------------------- loader


def test_missing_or_empty_folder_gives_nothing(tmp_path: Path):
    assert run(tmp_path) == ({}, [], [])
    (tmp_path / "passes").mkdir()
    (tmp_path / "passes" / "empty.yaml").write_text("")
    assert run(tmp_path) == ({}, [], [])


def test_valid_batch_reaches_output_in_pass_order(tmp_path: Path):
    out, errors, warnings = run(tmp_path, [
        mem("alpha-peak", "mountain_collective", access="limited", days=2, blackout=False),
        mem("alpha-peak", "ikon", access="limited", days=7, blackout=False, note="  7 days   combined with Bravo.  "),
        mem("bravo-bowl", "epic", access="unspecified"),
    ])
    assert errors == [] and warnings == []
    a = as_output(out["alpha-peak"])
    assert [m["pass"] for m in a] == ["ikon", "mountain_collective"]
    assert a[0] == {"pass": "ikon", "access": "limited", "days": 7, "blackout": False, "season": "2026/27",
                    "source_id": "pass-ikon-blog", "as_of": "2026-10-13", "confidence": "estimated",
                    "note": "7 days combined with Bravo.", "season_fallback": False}
    b = as_output(out["bravo-bowl"])[0]
    assert b["access"] == "unspecified" and b["days"] is None and b["blackout"] is None
    assert "charlie-hill" not in out and as_output(None) == []


def test_unknown_resort_fails(tmp_path: Path):
    _, errors, _ = run(tmp_path, [mem("zulu-ghost")])
    assert any("membership 'zulu-ghost' on epic: no such resort in this build" in e for e in errors)


def test_duplicate_in_one_file_fails(tmp_path: Path):
    _, errors, _ = run(tmp_path, [mem(), mem(access="limited", days=4)])
    assert any("membership listed twice: alpha-peak on epic for 2026/27" in e for e in errors)


def test_duplicate_across_files_fails(tmp_path: Path):
    write(tmp_path, [mem()], "a.yaml")
    write(tmp_path, [mem()], "b.yaml")
    _, errors, _ = run(tmp_path)
    assert any("b.yaml membership 'alpha-peak' on epic: also listed for 2026/27 in a.yaml" in e for e in errors)


def test_same_resort_on_two_passes_is_fine(tmp_path: Path):
    out, errors, _ = run(tmp_path, [mem(), mem(pass_="ikon")])
    assert errors == [] and [m.pass_id for m in out["alpha-peak"]] == ["epic", "ikon"]


@pytest.mark.parametrize("bad, field", [
    ({"pass": "vail"}, "pass"),
    ({"access": "some days"}, "access"),
    ({"confidence": "guessed"}, "confidence"),
    ({"blackout": "sometimes"}, "blackout"),
])
def test_enum_values_are_checked(tmp_path: Path, bad, field):
    _, errors, _ = run(tmp_path, [mem(**bad)])
    assert any(f"membership 'alpha-peak': {field}" in e for e in errors), errors


def test_days_go_only_with_limited_access(tmp_path: Path):
    _, errors, _ = run(tmp_path, [mem(days=7)])
    assert any("days is set but access is unlimited" in e for e in errors)
    _, errors, _ = run(tmp_path, [mem(access="limited", days=0)])
    assert any("days" in e and "greater than or equal to 1" in e for e in errors)
    out, errors, _ = run(tmp_path, [mem(access="limited")])   # limited without a stated number is allowed
    assert errors == [] and out["alpha-peak"][0].days is None


@pytest.mark.parametrize("season", ["2026/28", "2026-27", "26/27", "2026/2027"])
def test_season_must_be_two_consecutive_years(tmp_path: Path, season):
    _, errors, _ = run(tmp_path, [mem(season=season)])
    assert any("season must look like 2026/27" in e for e in errors)
    with pytest.raises(ValueError):
        check_season(season)
    assert check_season("2099/00") == "2099/00"


def test_undeclared_source_fails(tmp_path: Path):
    _, errors, _ = run(tmp_path, [mem(source_id="pass-nowhere")])
    assert any("source_id 'pass-nowhere' is not defined" in e for e in errors)


def test_source_must_be_the_pass_companys_own_site(tmp_path: Path):
    # An owner's portfolio page never proves a pass: ownership is not membership.
    _, errors, _ = run(tmp_path, [mem(source_id="pass-owner-site")])
    assert any("source 'pass-owner-site' is on example-owner.com; cite the pass company's own site (epicpass.com)" in e
               for e in errors)
    # Another pass company's page does not prove this pass either.
    _, errors, _ = run(tmp_path, [mem(pass_="ikon", source_id="pass-epic-page")])
    assert any("is on epicpass.com" in e and "(ikonpass.com)" in e for e in errors)
    # A subdomain of the pass site counts as the pass site.
    out, errors, _ = run(tmp_path, [mem(pass_="ikon")])
    assert errors == [] and out["alpha-peak"][0].source_id == "pass-ikon-blog"
    # A supplemental reference (Wikipedia) only when config allows it.
    _, errors, _ = run(tmp_path, [mem(source_id="pass-wiki-list")])
    assert any("is on wikipedia.org" in e for e in errors)
    out, errors, _ = run(tmp_path, [mem(source_id="pass-wiki-list")], supplemental={"wikipedia.org"})
    assert errors == [] and out["alpha-peak"][0].source_id == "pass-wiki-list"


def test_other_seasons_are_validated_and_left_out(tmp_path: Path):
    out, errors, warnings = run(tmp_path, [mem(), mem("bravo-bowl", season="2025/26"), mem("charlie-hill", season="2025/26")],
                                fallback=False)
    assert errors == [] and set(out) == {"alpha-peak"}
    assert warnings == ["data/raw/passes: 2 memberships for 2025/26 left out (passes.season is 2026/27)"]
    _, errors, _ = run(tmp_path, [mem("zulu-ghost", season="2025/26")])
    assert any("no such resort" in e for e in errors)


def test_previous_season():
    assert previous_season("2026/27") == "2025/26" and previous_season("2030/31") == "2029/30"
    assert previous_season("2050/51") == "2049/50"


def test_fallback_current_season_wins(tmp_path: Path):
    """A resort and pass with records for both seasons keeps the configured season's; the older one is left out."""
    out, errors, warnings = run(tmp_path, [mem(access="limited", days=7), mem(season="2025/26")])
    assert errors == []
    [m] = out["alpha-peak"]
    assert (m.season, m.access, m.days, m.season_fallback) == ("2026/27", "limited", 7, False)
    assert warnings == ["data/raw/passes: 1 memberships for 2025/26 left out (passes.season is 2026/27)"]


def test_fallback_uses_the_previous_season_when_the_current_one_is_missing(tmp_path: Path):
    out, errors, warnings = run(tmp_path, [
        mem("alpha-peak", season="2025/26"),                      # no 2026/27 Epic record: falls back
        mem("alpha-peak", "ikon", access="limited", days=7),      # 2026/27 on another pass: unaffected
        mem("bravo-bowl", season="2025/26", access="unspecified"),
    ])
    assert errors == []
    a = {m.pass_id: m for m in out["alpha-peak"]}
    assert a["epic"].season == "2025/26" and a["epic"].season_fallback is True
    assert a["ikon"].season == "2026/27" and a["ikon"].season_fallback is False
    assert as_output(out["bravo-bowl"])[0]["season_fallback"] is True
    assert warnings == ["data/raw/passes: 2 memberships use the 2025/26 list (no 2026/27 record; passes.fallback_previous_season)"]
    # Switched off, the previous season is only left out.
    out, _, warnings = run(tmp_path, fallback=False)
    assert set(out) == {"alpha-peak"} and [m.pass_id for m in out["alpha-peak"]] == ["ikon"]
    assert warnings == ["data/raw/passes: 2 memberships for 2025/26 left out (passes.season is 2026/27)"]


def test_fallback_never_reaches_further_back_than_one_season(tmp_path: Path):
    out, errors, warnings = run(tmp_path, [mem("alpha-peak", season="2024/25"), mem("bravo-bowl", season="2025/26"),
                                           mem("bravo-bowl", season="2024/25")])
    assert errors == [] and "alpha-peak" not in out
    [b] = out["bravo-bowl"]
    assert b.season == "2025/26" and b.season_fallback is True
    assert warnings == ["data/raw/passes: 1 memberships use the 2025/26 list (no 2026/27 record; passes.fallback_previous_season)",
                        "data/raw/passes: 2 memberships for 2024/25 left out (passes.season is 2026/27)"]
    # A future season is never a fallback either.
    out, _, _ = run(tmp_path, [mem(season="2027/28")])
    assert out == {}


def test_raw_records_cannot_claim_the_fallback_flag(tmp_path: Path):
    _, errors, _ = run(tmp_path, [mem(season="2025/26", season_fallback=True)])
    assert any("season_fallback is set by the build" in e for e in errors)


def test_membership_schema_dumps_the_pass_key():
    m = PassMembership.model_validate({"pass": "indy", "access": "limited", "days": 2, "season": "2026/27",
                                       "source_id": "x", "as_of": "2026-10-13", "confidence": "estimated"})
    assert m.pass_id == "indy" and list(m.output())[0] == "pass" and "pass_id" not in m.output()
    assert PASS_ORDER == ("epic", "ikon", "mountain_collective", "indy")


def _resort(**kw) -> dict:
    return {"id": "alpha-peak", "name": "Alpha Peak", "tier": "B", "lat": 39.5, "lon": -106.0,
            "location_source_id": "loc", "cluster_id": "front", "state": "CO", "sources": ["loc"], **kw}


def test_resort_lists_pass_sources_and_checks_them():
    m = {"pass": "epic", "access": "unlimited", "blackout": False, "season": "2026/27", "source_id": "pass-epic-page",
         "as_of": "2026-10-13", "confidence": "estimated", "note": None}
    ctx = {"source_ids": {"loc", "pass-epic-page"}}
    with pytest.raises(ValidationError, match="sources is missing referenced ids"):
        Resort.model_validate(_resort(passes=[m]), context=ctx)
    r = Resort.model_validate(_resort(passes=[m], sources=["loc", "pass-epic-page"]), context=ctx)
    assert r.model_dump(mode="json")["passes"] == [{**m, "days": None, "season_fallback": False}]
    with pytest.raises(ValidationError, match="not defined"):
        Resort.model_validate(_resort(passes=[m], sources=["loc", "pass-epic-page"]), context={"source_ids": {"loc"}})
    assert Resort.model_validate(_resort(), context={"source_ids": {"loc"}}).passes == []


def test_config_season_and_domains():
    p = load_config(REPO / "config").definitions.passes
    assert p.season == "2026/27"
    assert {"epicpass.com"} <= set(p.domains["epic"]) and p.domains["ikon"] == ["ikonpass.com"]
    with pytest.raises(ValidationError):
        PassesDefs(season="2026/2027")


# ---------------------------------------------------------------- routes: stubs and the slim index


def test_fallback_line_names_its_list():
    m = {"pass": "epic", "access": "unlimited", "season": "2025/26", "season_fallback": True}
    assert routes.pass_line(m) == "Epic Pass: unlimited (2025/26 list)"
    assert routes.pass_line({**m, "season_fallback": False}) == "Epic Pass: unlimited"
    ctx = routes.Ctx(sources={}, state_names={}, clusters={}, resorts={})
    html = routes.render_passes(ctx, {"passes": [{**m, "as_of": "2026-10-13", "confidence": "estimated"}]})
    assert "Epic Pass: unlimited (2025/26 list)" in html and "2025/26 season" in html


def test_access_text_and_line():
    assert routes.pass_access({"access": "unlimited"}) == "unlimited"
    assert routes.pass_access({"access": "limited", "days": 7}) == "7 days"
    assert routes.pass_access({"access": "limited", "days": 1}) == "1 day"
    assert routes.pass_access({"access": "limited", "days": None}) == "limited days"
    assert routes.pass_access({"access": "unspecified"}) == "days not stated"
    assert routes.pass_line({"pass": "epic", "access": "unlimited"}) == "Epic Pass: unlimited"
    assert routes.pass_line({"pass": "ikon", "access": "limited", "days": 7}) == "Ikon Pass: 7 days"


def test_stub_renders_the_passes_line_with_badge_and_source():
    ctx = routes.Ctx(sources={"pass-epic-page": SRC_EPIC}, state_names={}, clusters={}, resorts={})
    r = {"passes": [
        {"pass": "epic", "access": "unlimited", "blackout": False, "season": "2026/27", "source_id": "pass-epic-page",
         "as_of": "2026-10-13", "confidence": "estimated", "note": "Synthetic <note>"},
        {"pass": "ikon", "access": "limited", "days": 7, "blackout": True, "season": "2026/27",
         "source_id": "pass-epic-page", "as_of": "2026-10-13", "confidence": "published", "note": None},
        {"pass": "unknown", "access": "unlimited"}]}
    html = routes.render_passes(ctx, r)
    assert html.startswith('<div class="fact passes"><dt>Passes</dt>') and html.count('<li class="pass-item"') == 2
    assert '<span class="fact-value">Epic Pass: unlimited</span> <span class="badge badge-estimated">Estimated</span>' in html
    assert "2026/27 season · No blackout dates · Last verified Oct 13, 2026 · " in html
    assert 'href="https://www.epicpass.com/passes/epic-pass.aspx"' in html and "Synthetic &lt;note&gt;" in html
    assert "Ikon Pass: 7 days" in html and "Blackout dates apply" in html and "badge-published" in html
    assert routes.render_passes(ctx, {"passes": []}) == "" and routes.render_passes(ctx, {}) == ""


def test_map_index_carries_pass_ids(tmp_path: Path):
    def rec(rid, passes):
        return {"id": rid, "name": rid.title(), "tier": "B", "state": "CO", "cluster_id": "front", "lat": 39.5,
                "lon": -106.0, "size": {}, "season": {}, "career": None, "passes": passes}
    build = tmp_path / "build"
    build.mkdir()
    (build / "resorts.json").write_text(json.dumps({"version": 1, "generated_at": "2026-10-13T00:00:00Z", "resorts": [
        rec("alpha-peak", [{"pass": "mountain_collective"}, {"pass": "ikon"}, {"pass": "ikon"}]),
        rec("bravo-bowl", []), {**rec("charlie-hill", []), "passes": None}, rec("delta-dome", [{"pass": "made-up"}])]}))
    data = routes.build_map_index(build)
    assert data["fields"][-1] == "passes"
    rows = {row[0]: dict(zip(data["fields"], row)) for row in data["resorts"]}
    assert rows["alpha-peak"]["passes"] == ["ikon", "mountain_collective"]
    assert rows["bravo-bowl"]["passes"] is None and rows["charlie-hill"]["passes"] is None
    assert rows["delta-dome"]["passes"] is None


# ---------------------------------------------------------------- the real batch


def _real_registry_and_ids():
    from pipeline.tier_a import _register_sources, _yaml_files
    registry, errors = SourceRegistry(), []
    raw = REPO / "data" / "raw"
    for sub in sorted(p for p in raw.iterdir() if p.is_dir()):
        for f in _yaml_files(sub):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), list):
                _register_sources(data, str(f), registry, errors)
    ids = {r["id"] for r in json.loads((REPO / "data" / "build" / "resorts.json").read_text())["resorts"]}
    return registry, ids


@pytest.mark.skipif(not (REPO / "data" / "raw" / "passes").is_dir(), reason="no pass research yet")
def test_real_batch_is_valid_against_the_build():
    cfg = load_config(REPO / "config").definitions.passes
    registry, ids = _real_registry_and_ids()
    out, errors, warnings = load_passes(REPO / "data" / "raw", registry, ids, cfg.season, cfg.domain_sets(),
                                        set(cfg.supplemental_domains), fallback_previous=cfg.fallback_previous_season)
    assert errors == []
    # The one allowed warning: the count of previous season fallbacks (the Epic list was seen under 2025/26).
    fallback = re.compile(rf"^data/raw/passes: \d+ memberships use the {re.escape(previous_season(cfg.season))} list "
                          rf"\(no {re.escape(cfg.season)} record; passes.fallback_previous_season\)$")
    assert len(warnings) <= 1 and all(fallback.match(w) for w in warnings), warnings
    flat = [(rid, m) for rid, ms in out.items() for m in ms]
    assert {m.pass_id for _, m in flat} >= {"epic", "ikon"}
    # Search result values only: every membership is estimated and says the page was not opened.
    assert all(m.confidence == "estimated" and m.note and m.note.startswith(NOTE) for _, m in flat)
    # Public notes carry no internal source ids.
    assert not [rid for rid, m in flat if "passes-" in (m.note or "")]
    # Fallbacks are only ever the season one year back, and the warning counts every one of them.
    used = [m for _, m in flat if m.season_fallback]
    assert all(m.season == previous_season(cfg.season) for _, m in flat if m.season_fallback)
    assert all(m.season == cfg.season for _, m in flat if not m.season_fallback)
    assert (f" {len(used)} memberships use " in warnings[0]) if used else warnings == []
