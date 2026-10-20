"""Tests for pipeline/events.py, the events contract (docs/map/EVENTS.md) and the /map/events/ route stub.

Every event, venue, organizer and source below is synthetic (example.org), from tests/pipeline/fixtures/events."""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import events as ev
from pipeline import routes
from pipeline.build import build
from pipeline.config import EventsDefs, load_config
from pipeline.schemas.events import EventsBatch
from pipeline.schemas.resort import Resort
from pipeline.sources import SourceRegistry
from pipeline.tier_a import BuildError

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "events" / "valid.yaml"
HEADER = {"version": 1, "generated_at": "2026-10-17T12:00:00Z"}
PLACES = {
    "fixture-peak": ev.ResortPlace("Fixture Peak", "CO", 39.60, -106.30, "fixture-source-a"),
    "fixture-basin": ev.ResortPlace("Fixture Basin", "UT", 40.60, -111.60, "fixture-source-b"),
}


@pytest.fixture(scope="module")
def defs() -> EventsDefs:
    return load_config(REPO / "config").definitions.events


def raw() -> dict:
    return yaml.safe_load(FIXTURE.read_text())


def write(tmp_path: Path, *files: dict, names=None) -> Path:
    d = tmp_path / "events"
    d.mkdir(exist_ok=True)
    for i, data in enumerate(files):
        name = names[i] if names else f"lane{i}.yaml"
        (d / name).write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False))
    return tmp_path


def load(tmp_path: Path, defs: EventsDefs, *files: dict, places=None, registry=None, names=None):
    write(tmp_path, *files, names=names)
    return ev.load_events(tmp_path, SourceRegistry() if registry is None else registry, PLACES if places is None else places, defs,
                          rel=lambda p: p.name)


def event(data: dict, eid: str) -> dict:
    return next(e for e in data["events"] if e["id"] == eid)


def errors_for(tmp_path, defs, mutate, places=None) -> list[str]:
    data = raw()
    mutate(data)
    return load(tmp_path, defs, data, places=places)[1]


# ---------------------------------------------------------------- the valid file


def test_missing_or_empty_folder_gives_nothing(tmp_path, defs):
    data, errors, warnings = ev.load_events(tmp_path, SourceRegistry(), PLACES, defs)
    assert (data.events, data.venues, data.calendars, errors, warnings) == ([], {}, [], [], [])
    write(tmp_path, "")
    data, errors, warnings = ev.load_events(tmp_path, SourceRegistry(), PLACES, defs)
    assert data.events == [] and errors == [] and warnings == []


def test_valid_file_compiles(tmp_path, defs):
    registry = SourceRegistry()
    data, errors, warnings = load(tmp_path, defs, raw(), registry=registry)
    assert errors == [] and warnings == []
    assert "ev-fixture-marathon-page" in registry   # events sources join the build's registry (sources.json)
    out = ev.compile_events(data, PLACES, defs, HEADER)
    assert out["version"] == 1 and out["season"] == {"label": "2026/27", "start": "2026-07-01", "end": "2027-06-30"}
    # date order through the season: dated editions by start, a last edition by its month and day, programs last
    assert [e["id"] for e in out["events"]] == [
        "fixture-peak-week-race", "fixture-marathon", "fixture-peak-banked-slalom", "fixture-interski",
        "fixture-basin-fun-race", "fixture-basin-league"]
    m = event(out, "fixture-marathon")
    assert m["group"] == "nordic" and m["venue_id"] == "fixture-lake-trail" and "venue" not in m
    assert m["levels"] == ["open_amateur", "elite"]                       # config order
    assert m["disciplines_also"] == ["cross_country"]                     # the main discipline is not repeated
    assert m["signup"]["fee_usd"] == {"value": 150, "note": "50 km skate, early rate", "source_id": "ev-fixture-marathon-page",
                                      "as_of": "2026-10-17", "confidence": "published"}
    assert m["signup"]["url"]["note"] == "From a search result for this page; page not opened."
    assert m["organizer"]["name"] == "Fixture Ski Foundation" and m["sanctioning"]["name"] == "Fixture Loppet League"
    # vocabulary for the frontend, from config
    assert [g["id"] for g in out["groups"]] == defs.ids("groups") and out["groups"][-1]["id"] == "community"
    assert {d["id"] for g in out["groups"] for d in g["disciplines"]} >= {"interski", "endurance", "fun_race"}
    assert out["levels"][0] == {"id": "open_amateur", "label": "Open to anyone"}
    assert out["month_chips"] == [11, 12, 1, 2, 3, 4]
    assert out["calendars"][0]["id"] == "fixture-alpine-calendar" and out["calendars"][0]["group"] == "alpine"
    assert out["calendars"][1]["region"] == "CO" and out["calendars"][1]["group"] == "nordic"
    # no source text in events.json: source ids only, the records live in sources.json
    text = json.dumps(out)
    assert "Fixture marathon page" not in text and "sources" not in out
    assert ev.source_ids(out) - {"fixture-source-a", "fixture-source-b"} <= registry.ids()   # less the resort points


def test_resort_resolution(tmp_path, defs):
    data, errors, _ = load(tmp_path, defs, raw())
    out = ev.compile_events(data, PLACES, defs, HEADER)
    venues = {v["id"]: v for v in out["venues"]}
    assert sorted(venues) == ["fixture-abroad-venue", "fixture-basin", "fixture-lake-trail", "fixture-peak"]
    assert venues["fixture-peak"] == {
        "id": "fixture-peak", "name": "Fixture Peak", "kind": "resort", "state": "CO", "resort_id": "fixture-peak",
        "mapped": True, "location": {"lat": 39.60, "lon": -106.30, "precision": "venue", "source_id": "fixture-source-a"}}
    trail = venues["fixture-lake-trail"]
    assert trail["town"] == "Fixture Town" and trail["location"]["precision"] == "town" and trail["mapped"] is True
    assert trail["location"]["confidence"] == "reported" and "country" not in trail
    abroad = venues["fixture-abroad-venue"]
    assert abroad["mapped"] is False and abroad["country"] == "NO" and "state" not in abroad   # list only
    assert out["counts"]["venues"] == 3 and out["counts"]["venues_abroad"] == 1


def test_last_edition_fallback(tmp_path, defs):
    data, _, _ = load(tmp_path, defs, raw())
    slalom = next(e for e in data.events if e.id == "fixture-peak-banked-slalom")
    assert ev.event_dates(slalom) == (dt.date(2026, 3, 13), dt.date(2026, 3, 15), False, "2025/26")
    marathon = next(e for e in data.events if e.id == "fixture-marathon")
    assert ev.event_dates(marathon)[2] is True    # the 2026/27 edition wins over the last edition
    out = ev.compile_events(data, PLACES, defs, HEADER)
    e = event(out, "fixture-peak-banked-slalom")
    assert "edition_2026_27" not in e and e["last_edition"]["season"] == "2025/26" and e["months"] == [3]
    assert out["counts"]["dated"] == 4
    # a last edition known only by its season places by usual_timing months
    d = raw()
    event(d, "fixture-peak-banked-slalom")["last_edition"] = {"season": "2025/26", "source_id": "ev-fixture-slalom-page",
                                                              "as_of": "2026-10-17", "confidence": "estimated"}
    event(d, "fixture-peak-banked-slalom")["usual_timing"] = {"value": "Mid March", "source_id": "ev-fixture-slalom-page",
                                                              "as_of": "2026-10-17", "confidence": "estimated"}
    data, errors, _ = load(tmp_path, defs, d)
    assert errors == []
    out = ev.compile_events(data, PLACES, defs, HEADER)
    assert event(out, "fixture-peak-banked-slalom")["months"] == [3]
    assert [x["id"] for x in out["events"]][-2:] == ["fixture-basin-league", "fixture-peak-banked-slalom"]


def test_month_counts(tmp_path, defs):
    data, _, _ = load(tmp_path, defs, raw())
    out = ev.compile_events(data, PLACES, defs, HEADER)
    by_id = {e["id"]: e["months"] for e in out["events"]}
    assert by_id["fixture-peak-week-race"] == [11, 12]          # Nov 28 to Dec 2: both months
    assert by_id["fixture-basin-fun-race"] == [4]               # end not seen: the start month alone
    assert by_id["fixture-basin-league"] == [1, 2, 3]           # a program: the months its usual_timing names
    assert out["counts"]["by_month"] == {"1": 1, "2": 2, "3": 3, "4": 1, "5": 0, "6": 0, "7": 0, "8": 0, "9": 0,
                                         "10": 0, "11": 1, "12": 1}
    assert out["counts"]["by_group"]["community"] == 2 and out["counts"]["by_group"]["instructor"] == 1
    assert sum(out["counts"]["by_group"].values()) == out["counts"]["events"] == 6
    assert list(out["counts"]["by_group"]) == defs.ids("groups")


def test_month_helpers():
    assert ev.months_between(dt.date(2026, 12, 30), dt.date(2027, 1, 2)) == [12, 1]
    assert ev.months_between(dt.date(2026, 7, 1), dt.date(2027, 9, 1)) == list(range(7, 13)) + list(range(1, 7))
    assert ev.season_position(dt.date(2026, 3, 13), dt.date(2026, 7, 1)) == dt.date(2027, 3, 13)
    assert ev.season_position(dt.date(2025, 11, 2), dt.date(2026, 7, 1)) == dt.date(2026, 11, 2)
    assert ev.season_position(dt.date(2024, 2, 29), dt.date(2026, 7, 1)) == dt.date(2027, 2, 28)
    t = ev.timing_months
    assert t("Saturdays, January to March") == [1, 2, 3]
    assert t("December through February") == [12, 1, 2]
    assert t("Thursday nights, Jan - Mar") == [1, 2, 3]
    assert t("Mid March and early April") == [3, 4]
    assert t("Late February") == [2]
    assert t("dates may vary") == [] and t("All season") == [] and t(None) == []


def test_season_long_program(tmp_path, defs):
    """A program with no single date needs a sourced usual_timing or season_long (coordinator rule 2026-10-17)."""
    data, errors, _ = load(tmp_path, defs, raw())
    out = ev.compile_events(data, PLACES, defs, HEADER)
    league = event(out, "fixture-basin-league")
    assert league["program"] is True and "edition_2026_27" not in league and "last_edition" not in league
    assert league["series"] == {"id": "fixture-league", "name": "Fixture Race League"}
    assert out["counts"]["programs"] == 1
    assert "program" not in event(out, "fixture-marathon")
    # season_long alone: no months, still valid
    d = raw()
    lg = event(d, "fixture-basin-league")
    lg.pop("usual_timing")
    lg["season_long"] = {"value": True, "source_id": "ev-fixture-league-page", "as_of": "2026-10-17", "confidence": "published"}
    data, errors, _ = load(tmp_path, defs, d)
    assert errors == []
    out = ev.compile_events(data, PLACES, defs, HEADER)
    assert event(out, "fixture-basin-league")["months"] == [] and event(out, "fixture-basin-league")["program"] is True
    items = ev.resort_events(data, defs)["fixture-basin"]["items"]
    assert items[-1]["season_long"] is True and "timing" not in items[-1] and items[-1]["confidence"] == "published"
    # neither dates nor timing: an error
    lg.pop("season_long")
    errs = load(tmp_path, defs, d)[1]
    assert len(errs) == 1 and "fixture-basin-league" in errs[0] and "usual_timing or season_long" in errs[0]
    # season_long false is not a value
    lg["season_long"] = {"value": False, "source_id": "ev-fixture-league-page", "as_of": "2026-10-17", "confidence": "published"}
    assert "give season_long only as true" in " ".join(load(tmp_path, defs, d)[1])


def test_resort_events_for_the_panel(tmp_path, defs):
    data, _, _ = load(tmp_path, defs, raw())
    by_resort = ev.resort_events(data, defs)
    assert sorted(by_resort) == ["fixture-basin", "fixture-peak"]
    peak = by_resort["fixture-peak"]
    assert peak["count"] == 2 and [i["id"] for i in peak["items"]] == ["fixture-peak-week-race", "fixture-peak-banked-slalom"]
    first, last = peak["items"]
    assert first["published"] is True and first["season"] == "2026/27" and first["discipline_label"] == "Lap and vertical challenge"
    assert last["published"] is False and last["season"] == "2025/26" and last["start"] == "2026-03-13"
    fun = by_resort["fixture-basin"]["items"][0]
    assert fun["id"] == "fixture-basin-fun-race" and "end" not in fun and "note" not in fun and "group" not in fun
    # in the resort record: null and false keys left out
    from pipeline.schemas.events import ResortEvents
    dumped = ResortEvents.model_validate(by_resort["fixture-basin"]).model_dump(mode="json")
    assert dumped["items"][0] == {"id": "fixture-basin-fun-race", "name": "Fixture Basin Fun Race", "discipline_label": "Fun race",
                                  "start": "2027-04-03", "season": "2026/27", "published": True,
                                  "source_id": "ev-fixture-league-page", "as_of": "2026-10-17", "confidence": "estimated"}
    assert dumped["items"][1]["season_long"] is True


def test_summary_counts_dated_ends(tmp_path, defs):
    data, _, _ = load(tmp_path, defs, raw())
    s = ev.summary(ev.compile_events(data, PLACES, defs, HEADER))
    assert s == {**HEADER, "season": {"label": "2026/27", "start": "2026-07-01", "end": "2027-06-30"}, "events": 6,
                 "calendars": 2, "dated_ends": ["2026-12-02", "2027-02-27", "2027-03-20", "2027-04-03"]}


def test_null_values_read_as_left_out(tmp_path, defs):
    """organizer, sign up method and a cited block whose value is null mean no source gave it."""
    data, errors, _ = load(tmp_path, defs, raw())
    assert errors == []
    slalom = next(e for e in data.events if e.id == "fixture-peak-banked-slalom")
    assert slalom.organizer is None and slalom.signup.method is None and slalom.signup.deadline is None
    fun = next(e for e in data.events if e.id == "fixture-basin-fun-race")
    assert fun.signup.url is None and fun.signup.method == "at_venue"
    week = next(e for e in data.events if e.id == "fixture-peak-week-race")
    assert week.signup is None and week.organizer is None
    # no source says who can enter: levels [] (coordinator rule 2026-10-17)
    d = raw()
    event(d, "fixture-peak-week-race")["levels"] = []
    data, errors, _ = load(tmp_path, defs, d)
    assert errors == [] and event(ev.compile_events(data, PLACES, defs, HEADER), "fixture-peak-week-race")["levels"] == []
    # no website any source gives: left out, never guessed (coordinator rule 2026-10-17)
    d = raw()
    event(d, "fixture-marathon").pop("website")
    event(d, "fixture-interski")["website"] = {"value": None, "source_id": "ev-fixture-interski-page", "as_of": "2026-10-17",
                                               "confidence": "published"}
    data, errors, _ = load(tmp_path, defs, d)
    assert errors == []
    out = ev.compile_events(data, PLACES, defs, HEADER)
    assert "website" not in event(out, "fixture-marathon") and "website" not in event(out, "fixture-interski")


def test_unknown_discipline(tmp_path, defs):
    errs = errors_for(tmp_path, defs, lambda d: event(d, "fixture-marathon").update(discipline="ski_ballet_freestyle"))
    assert errs == ["lane0.yaml event 'fixture-marathon': discipline 'ski_ballet_freestyle' is not a discipline id of "
                    "config events.groups"]


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: event(d, "fixture-marathon").update(disciplines_also=["nordic"]), "disciplines_also 'nordic' is not a discipline id"),
    (lambda d: event(d, "fixture-marathon").update(levels=["pro"]), "level 'pro' is not a level id"),
    (lambda d: event(d, "fixture-peak-week-race").update(venue={"resort_id": "nowhere-peak", "venue_id": None}),
     "venue resort_id 'nowhere-peak' matches no resort in this build"),
    (lambda d: event(d, "fixture-marathon").update(venue={"resort_id": None, "venue_id": "lost-trail"}),
     "venue venue_id 'lost-trail' is not defined in the venues of any events file"),
    (lambda d: event(d, "fixture-marathon").update(venue={"resort_id": "fixture-peak", "venue_id": "fixture-lake-trail"}),
     "give exactly one of resort_id and venue_id"),
    (lambda d: event(d, "fixture-marathon").update(venue={"resort_id": None, "venue_id": None}), "give exactly one of resort_id and venue_id"),
    (lambda d: event(d, "fixture-marathon")["website"].update(source_id="ev-undefined-source"),
     "source_id 'ev-undefined-source' is not defined in any sources list"),
    (lambda d: event(d, "fixture-marathon")["edition_2026_27"].update(end="2027-02-20"), "end 2027-02-20 is before start 2027-02-25"),
    (lambda d: event(d, "fixture-marathon")["edition_2026_27"].update(start="2027-02-30"), "edition_2026_27.start: Input should be a valid date"),
    (lambda d: event(d, "fixture-marathon")["edition_2026_27"].update(start="Feb 25, 2027"), "edition_2026_27.start: Input should be a valid date"),
    (lambda d: event(d, "fixture-marathon")["edition_2026_27"].update(start="2027-07-04", end="2027-07-05"),
     "edition_2026_27 starts 2027-07-04, outside the 2026/27 season (2026-07-01 to 2027-06-30)"),
    (lambda d: event(d, "fixture-marathon")["last_edition"].update(start="2026-08-01", end="2026-08-02", season="2026/27"),
     "last_edition starts 2026-08-01, inside the 2026/27 season"),
    (lambda d: event(d, "fixture-marathon")["last_edition"].update(season="2024/25"), "start 2026-02-19 is outside the 2024/25 season"),
    (lambda d: event(d, "fixture-marathon")["last_edition"].update(season="2025-26"), "season must look like 2026/27"),
    (lambda d: event(d, "fixture-marathon")["last_edition"].update(start=None), "end is set without a start"),
    (lambda d: event(d, "fixture-marathon")["format"].update(value="x" * 161), "format is 161 characters; keep it to 160"),
    (lambda d: event(d, "fixture-marathon").update(notes="free text"), "notes: Extra inputs are not permitted"),
    (lambda d: event(d, "fixture-marathon")["website"].update(value="www.example.org/x"), "expected an http(s) URL"),
    (lambda d: event(d, "fixture-marathon")["website"].update(confidence="likely"), "confidence: Input should be 'published'"),
    (lambda d: event(d, "fixture-marathon")["signup"].update(method="email"), "signup.method: Input should be"),
    (lambda d: event(d, "fixture-marathon")["signup"]["deadline"].update(value="2026-08-01"), "deadline 2026-08-01 is before opens 2026-09-01"),
    (lambda d: event(d, "fixture-marathon")["signup"]["deadline"].update(value="Race day at 2 pm"), "signup.deadline.value: Input should be a valid date"),
    (lambda d: event(d, "fixture-marathon")["signup"]["fee_usd"].update(value=True), "expected a number of US dollars"),
    (lambda d: event(d, "fixture-marathon")["signup"]["fee_usd"].update(value=-5), "greater than or equal to 0"),
    (lambda d: d["venues"][0].update(state="ZZ"), "state ZZ is not a US state code"),
    (lambda d: d["venues"][0]["location"].update(lon=91.40), "is outside the United States"),
    (lambda d: d["venues"][0]["location"].update(precision="street"), "precision: Input should be 'venue', 'town' or 'region'"),
    (lambda d: d["venues"][0]["location"].pop("source_id"), "location.source_id: Field required"),
    (lambda d: d["venues"][0].update(kind="stadium"), "kind: Input should be"),
    (lambda d: d["venues"][0].update(state=None), "a venue needs a US state, or country set"),
    (lambda d: d["venues"][1].update(state="MN"), "state MN is set but country is NO"),
    (lambda d: d["venues"][0].update(id="fixture-peak"), "id is a resort id of this build"),
    (lambda d: d["calendars"][0].update(region="West"), "region: String should match pattern"),
    (lambda d: d["calendars"][0].update(region="ZZ"), "region 'ZZ' is neither national nor a US state code"),
    (lambda d: d["calendars"][0].update(discipline="powder"), "calendar 'fixture-alpine-calendar': discipline 'powder'"),
    (lambda d: d["calendars"][0].update(levels=["elite"]), "levels: Extra inputs are not permitted"),
    (lambda d: d["calendars"].append(copy.deepcopy(d["calendars"][0])), "calendar 'fixture-alpine-calendar': id also used"),
    (lambda d: d["events"].append(copy.deepcopy(d["events"][0])), "event 'fixture-marathon': id also used in lane0.yaml"),
    (lambda d: d.update(batch="fixture"), "batch: String should match pattern"),
    (lambda d: d.update(note="file note"), "note: Extra inputs are not permitted"),
])
def test_every_validation_failure(tmp_path, defs, mutate, expect):
    errs = errors_for(tmp_path, defs, mutate)
    assert errs and any(expect in e for e in errs), errs


def test_ids_unique_across_files(tmp_path, defs):
    a, b = raw(), raw()
    b["batch"] = "events-second"
    b["sources"], b["venues"], b["calendars"] = a["sources"], a["venues"], []   # identical venue repeat is fine
    b["events"] = [copy.deepcopy(event(a, "fixture-marathon"))]
    _, errors, _ = load(tmp_path, defs, a, b)
    assert errors == ["lane1.yaml event 'fixture-marathon': id also used in lane0.yaml; event ids are unique across all files"]
    b["events"] = []
    b["venues"] = copy.deepcopy(a["venues"])
    b["venues"][0]["name"] = "Another Trail"
    _, errors, _ = load(tmp_path, defs, a, b)
    assert len(errors) == 1 and "venue 'fixture-lake-trail': also defined in lane0.yaml with different fields" in errors[0]


def test_source_conflict_across_files(tmp_path, defs):
    a, b = raw(), raw()
    b.update(batch="events-second", venues=[], events=[], calendars=[])
    b["sources"] = [{**a["sources"][0], "url": "https://example.org/other"}]
    _, errors, _ = load(tmp_path, defs, a, b)
    assert len(errors) == 1 and "source id 'ev-fixture-marathon-page' has url" in errors[0]


def test_a_venue_can_be_cited_from_another_file(tmp_path, defs):
    a, b = raw(), raw()
    b.update(batch="events-second", sources=[], venues=[], calendars=[])
    b["events"] = [{**copy.deepcopy(event(a, "fixture-marathon")), "id": "fixture-marathon-sprint"}]
    data, errors, _ = load(tmp_path, defs, a, b)
    assert errors == [] and {e.id for e in data.events} >= {"fixture-marathon", "fixture-marathon-sprint"}


def test_yaml_parse_error(tmp_path, defs):
    _, errors, _ = load(tmp_path, defs, "batch: events-x\nevents: [{id: a, name: {broken\n")
    assert len(errors) == 1 and "YAML parse error" in errors[0]


def test_norway_is_a_country(tmp_path, defs):
    """An unquoted NO reads as false in YAML; for a venue's country it is Norway."""
    assert yaml.safe_load("country: NO")["country"] is False
    data, errors, _ = load(tmp_path, defs, raw())
    assert errors == [] and data.venues["fixture-abroad-venue"].country == "NO"


def test_warnings(tmp_path, defs):
    d = raw()
    d["venues"].append({**copy.deepcopy(d["venues"][0]), "id": "fixture-unused-trail"})
    d["venues"].append({"id": "fixture-peak-hill", "name": "Fixture Peak Hill", "kind": "resort", "state": "CO",
                        "location": {"lat": 39.605, "lon": -106.305, "precision": "venue", "source_id": "ev-fixture-town-wiki",
                                     "as_of": "2026-10-17", "confidence": "reported"}})
    event(d, "fixture-peak-week-race")["venue"] = {"resort_id": None, "venue_id": "fixture-peak-hill"}
    d["venues"][1]["id"] = "fixture-abroad-venue"
    event(d, "fixture-marathon")["venue"] = {"resort_id": None, "venue_id": "fixture-abroad-venue"}
    data, errors, warnings = load(tmp_path, defs, d)
    assert errors == []
    assert any("venue 'fixture-unused-trail': no event uses it; left out" in w for w in warnings)
    assert any("venue 'fixture-lake-trail': no event uses it" in w for w in warnings)
    assert any("venue 'fixture-peak-hill': 0.7 km from resort 'fixture-peak' (Fixture Peak)" in w for w in warnings)
    assert any("event 'fixture-marathon': venue 'fixture-abroad-venue' is in NO" in w for w in warnings)
    assert "fixture-unused-trail" not in data.venues and "fixture-peak-hill" in data.venues


# ---------------------------------------------------------------- config


def _events_cfg(**changes) -> dict:
    d = yaml.safe_load((REPO / "config" / "definitions.yaml").read_text())["events"]
    d.update(changes)
    return d


def test_config_events_block(defs):
    assert defs.season.label == "2026/27" and defs.season.start == dt.date(2026, 7, 1) and defs.season.end == dt.date(2027, 6, 30)
    groups = defs.discipline_group()
    # every id of the contract table (docs/map/EVENTS.md) has a group
    contract = (REPO / "docs" / "map" / "EVENTS.md").read_text()
    table = contract.split("## Discipline ids", 1)[1].split("## Level ids", 1)[0]
    for line in table.splitlines():
        if not line.startswith("|") or line.startswith(("| Group", "|---")):
            continue
        group, ids = [c.strip() for c in line.strip("|").split("|")]
        for did in [x.strip() for x in re.sub(r"\([^)]*\)", "", ids).split(",") if x.strip()]:
            assert groups.get(did) == group, (did, group)
    assert groups["interski"] == "instructor" and groups["fun_race"] == groups["endurance"] == "community"
    assert set(defs.ids("levels")) == {"elite", "collegiate", "junior", "masters", "open_amateur", "adaptive", "instructor"}
    from pipeline.schemas.events import SIGNUP_METHODS, VENUE_KINDS
    assert defs.ids("signup_methods") == list(SIGNUP_METHODS) and defs.ids("venue_kinds") == list(VENUE_KINDS)
    # labels follow the copy rules: no hyphens or dashes
    labels = [g.label for g in defs.groups] + [d.label for g in defs.groups for d in g.disciplines] + \
        [x.label for x in defs.levels + defs.signup_methods + defs.venue_kinds]
    assert not [x for x in labels if "-" in x or "—" in x or "–" in x]


@pytest.mark.parametrize("changes, expect", [
    ({"season": {"label": "2026/27", "start": "2026-07-01", "end": "2027-05-31"}}, "season 2026/27 runs from 2026-07-01"),
    ({"month_chips": [11, 11]}, "month_chips"),
    ({"abroad_groups": ["space"]}, "unknown groups"),
])
def test_config_rejects(changes, expect):
    with pytest.raises(ValueError, match=expect):
        EventsDefs.model_validate(_events_cfg(**changes))


def test_config_colors_need_contrast_and_distance():
    d = _events_cfg()
    d["groups"][0]["color"] = "#334455"
    with pytest.raises(ValueError, match="contrast on #1e1e1e"):
        EventsDefs.model_validate(d)
    d = _events_cfg()
    d["groups"][1]["color"] = d["groups"][0]["color"]
    with pytest.raises(ValueError, match="apart"):
        EventsDefs.model_validate(d)
    d = _events_cfg()
    d["groups"][1]["disciplines"].append({"id": "nastar", "label": "NASTAR"})
    with pytest.raises(ValueError, match="discipline ids in two groups"):
        EventsDefs.model_validate(d)


# ---------------------------------------------------------------- the build


def _config_dir(repo_root, tmp_path):
    cfg_dir = tmp_path / "config"
    shutil.copytree(repo_root / "config", cfg_dir)
    p = cfg_dir / "definitions.yaml"
    data = yaml.safe_load(p.read_text())
    data["ownership"]["missing_tier_a_record"] = "warn"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return cfg_dir


def test_build_end_to_end(repo_root, raw_dir, tmp_path):
    (raw_dir / "events").mkdir()
    shutil.copy(FIXTURE, raw_dir / "events" / "fixture.yaml")
    out = tmp_path / "build"
    build(repo_root, raw_dir=raw_dir, out_dir=out, report_path=tmp_path / "r.md", states_geojson=False,
          config_dir=_config_dir(repo_root, tmp_path))
    events = json.loads((out / "events.json").read_text())
    assert events["counts"]["events"] == 6 and (out / "events.json").read_text().count("\n") == 1   # compact
    summary = json.loads((out / "events-summary.json").read_text())
    assert summary["events"] == 6 and len(summary["dated_ends"]) == 4
    sources = {s["id"]: s for s in json.loads((out / "sources.json").read_text())["sources"]}
    assert ev.source_ids(events) <= set(sources) and sources["ev-fixture-calendar"]["title"] == "Fixture alpine calendar"
    resorts = {r["id"]: r for r in json.loads((out / "resorts.json").read_text())["resorts"]}
    peak = resorts["fixture-peak"]
    assert peak["events"]["count"] == 2 and "ev-fixture-slalom-page" in peak["sources"]
    Resort.model_validate(peak, context={"source_ids": set(sources)})
    assert resorts["fixture-basin"]["events"]["items"][-1]["season_long"] is True
    assert all(r["events"] is None for rid, r in resorts.items() if rid not in ("fixture-peak", "fixture-basin"))
    defs_json = json.loads((out / "definitions.json").read_text())
    assert defs_json["events"] == {"season": {"label": "2026/27", "start": "2026-07-01", "end": "2027-06-30"},
                                   "resort_panel_max": 3}
    # the resort venue resolves to the build's own resort point
    v = next(x for x in events["venues"] if x["id"] == "fixture-peak")
    assert (v["location"]["lat"], v["location"]["lon"]) == (peak["lat"], peak["lon"])


def test_build_without_events_writes_empty_files(repo_root, raw_dir, tmp_path):
    out = tmp_path / "build"
    build(repo_root, raw_dir=raw_dir, out_dir=out, report_path=tmp_path / "r.md", states_geojson=False,
          config_dir=_config_dir(repo_root, tmp_path))
    events = json.loads((out / "events.json").read_text())
    assert events["events"] == [] and events["venues"] == [] and events["counts"]["events"] == 0
    assert json.loads((out / "events-summary.json").read_text())["dated_ends"] == []


def test_build_fails_on_invalid_events(repo_root, raw_dir, tmp_path):
    (raw_dir / "events").mkdir()
    d = raw()
    event(d, "fixture-marathon")["discipline"] = "ski_marathons"
    (raw_dir / "events" / "fixture.yaml").write_text(yaml.safe_dump(d))
    with pytest.raises(BuildError) as e:
        build(repo_root, raw_dir=raw_dir, out_dir=tmp_path / "build", report_path=tmp_path / "r.md",
              states_geojson=False, config_dir=_config_dir(repo_root, tmp_path))
    assert any("event 'fixture-marathon': discipline 'ski_marathons'" in m for m in e.value.errors)


# ---------------------------------------------------------------- route stub


def test_date_text():
    assert routes.date_span("2027-02-25", "2027-02-27") == "Feb 25 to 27, 2027"
    assert routes.date_span("2026-11-28", "2026-12-02") == "Nov 28 to Dec 2, 2026"
    assert routes.date_span("2026-12-30", "2027-01-02") == "Dec 30, 2026 to Jan 2, 2027"
    assert routes.date_span("2027-02-25", "2027-02-25") == "Feb 25, 2027" == routes.date_span("2027-02-25", None)
    w = routes.event_when
    assert w({"edition_2026_27": {"start": "2027-02-25", "end": "2027-02-27"}}, "2026/27") == "Feb 25 to 27, 2027"
    assert w({"edition_2026_27": {"start": "2027-04-03"}}, "2026/27") == "Starts Apr 3, 2027"
    assert w({"last_edition": {"start": "2026-03-13", "end": "2026-03-15", "season": "2025/26"}}, "2026/27") == \
        "Last held Mar 13 to 15, 2026. 2026/27 date not yet published."
    assert w({"last_edition": {"season": "2025/26"}}, "2026/27") == "Last held in the 2025/26 season. 2026/27 date not yet published."
    assert w({"program": True, "usual_timing": {"value": "Thursday nights, January to March"}}, "2026/27") == \
        "Runs Thursday nights, January to March"
    assert w({"program": True}, "2026/27") == "Runs all season"


def _stub_root(tmp_path, repo_root, events_payload) -> Path:
    root = tmp_path / "site"
    (root / "map").mkdir(parents=True)
    shutil.copy(repo_root / "map" / "index.html", root / "map" / "index.html")
    fx = repo_root / "tests" / "map" / "fixtures"
    shutil.copytree(fx, root / "data" / "build")
    (root / "data" / "build" / "events.json").write_text(json.dumps(events_payload))
    (root / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>\n')
    return root


def test_events_stub_and_sitemap(tmp_path, repo_root, defs):
    data, _, _ = load(tmp_path, defs, raw())
    payload = ev.compile_events(data, PLACES, defs, HEADER)
    root = _stub_root(tmp_path, repo_root, payload)
    routes.generate_routes(root)
    page = (root / "map" / "events" / "index.html").read_text()
    assert "<title>Snowsports Events and Competitions • Instructor Grindset</title>" in page
    assert 'href="https://instructorgrindset.com/map/events/"' in page
    assert "Events and competitions" in page and "Calendar 2026/27" in page
    assert '<h4 class="sub ev-month">November 2026</h4>' in page and "2026/27 date not yet published</h4>" in page
    assert '<h4 class="sub ev-month">Season long</h4>' in page and "Runs Thursday nights, January to March" in page
    assert 'href="/map/events/?venue=fixture-lake-trail&amp;event=fixture-marathon"' in page
    assert 'class="ev-signup-link" href="https://example.org/events/marathon/register"' in page
    assert "Fixture Alpine Venue, Fixture Fjord, NO" in page and "Fixture Lake Trail, Fixture Town, MN" in page
    assert "Fixture alpine events calendar" in page
    ld = json.loads(page.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0])
    items = ld["@graph"][0]["mainEntity"]["itemListElement"]
    assert [i["item"]["name"] for i in items][:2] == ["Fixture Peak Week Race", "Fixture Lake Marathon"]
    assert items[1]["item"]["location"]["address"] == {"@type": "PostalAddress", "addressCountry": "US",
                                                       "addressRegion": "MN", "addressLocality": "Fixture Town"}
    assert len(items) == 4   # dated events only
    assert "<loc>https://instructorgrindset.com/map/events/</loc>" in (root / "sitemap.xml").read_text()
    # copy rules on the generated page text
    body = page.split("<!-- prerender:start -->", 1)[1].split("<!-- prerender:end -->", 1)[0]
    assert "—" not in body


def test_events_stub_without_events(tmp_path, repo_root, defs):
    payload = ev.compile_events(ev.EventsData(), PLACES, defs, HEADER)
    root = _stub_root(tmp_path, repo_root, payload)
    routes.generate_routes(root)
    assert "Events are being compiled. Check back soon." in (root / "map" / "events" / "index.html").read_text()


def test_resort_stub_events_here():
    ctx = routes.Ctx({"src-a": {"id": "src-a", "url": "https://example.org/a", "title": "Fixture page"}}, {}, {}, {})
    r = {"id": "fixture-peak", "name": "Fixture Peak", "events": {"count": 4, "items": [
        {"id": "a-race", "name": "A Race", "discipline_label": "Alpine racing", "start": "2027-01-09", "end": "2027-01-10",
         "season": "2026/27", "published": True, "source_id": "src-a", "as_of": "2026-10-17", "confidence": "published"},
        {"id": "b-race", "name": "B Race", "discipline_label": "Skimo", "start": "2026-03-01", "season": "2025/26",
         "published": False, "source_id": "src-a", "as_of": "2026-10-17", "confidence": "estimated"},
        {"id": "c-league", "name": "C League", "discipline_label": "Masters racing", "published": False,
         "season_long": True, "timing": "Thursday nights", "source_id": "src-a", "as_of": "2026-10-17", "confidence": "published"},
        {"id": "d-race", "name": "D Race", "discipline_label": "NASTAR", "published": False}]}}
    html = routes.render_resort_events(ctx, r, "2026/27", 3)
    assert html.startswith('<section class="panel-section events-here"><h3>Events here</h3>')
    assert 'href="/map/events/?venue=fixture-peak&amp;event=a-race">A Race</a>' in html
    assert "Jan 9 to 10, 2027" in html and "Last held Mar 1, 2026. 2026/27 date not yet published." in html
    assert "Runs Thursday nights" in html and "D Race" not in html   # three at most
    assert 'See all 4 events at Fixture Peak on the events map</a>' in html and 'href="/map/events/?venue=fixture-peak"' in html
    assert '<span class="badge badge-estimated">Estimated</span>' in html and "Fixture page" in html
    assert routes.render_resort_events(ctx, {"id": "x", "name": "X", "events": None}) == ""
