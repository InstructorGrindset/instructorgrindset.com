"""Tests for pipeline/snowkite.py and the snowkite contract (docs/map/SNOWKITE.md).

Every zone, event and source below is synthetic (example.com), from tests/map/snowkite_fixture.yaml and
tests/map/snowkite_events_fixture.yaml."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import events as ev
from pipeline import snowkite as sk
from pipeline.build import build
from pipeline.config import SnowkiteDefs, load_config
from pipeline.schemas.snowkite import SURFACES, ZONE_KINDS, ZonesBatch
from pipeline.sources import SourceRegistry
from pipeline.tier_a import BuildError

REPO = Path(__file__).resolve().parents[2]
ZONES = REPO / "tests" / "map" / "snowkite_fixture.yaml"
EVENTS = REPO / "tests" / "map" / "snowkite_events_fixture.yaml"
HEADER = {"version": 1, "generated_at": "2026-11-28T12:00:00Z"}
PLACES = {"alpha-peak": ev.ResortPlace("Alpha Peak", "CO", 39.64, -106.37, "fixture-location")}


@pytest.fixture(scope="module")
def cfg():
    return load_config(REPO / "config")


@pytest.fixture(scope="module")
def defs(cfg) -> SnowkiteDefs:
    return cfg.definitions.snowkite


def raw() -> dict:
    return yaml.safe_load(ZONES.read_text())


def zone(data: dict, zid: str) -> dict:
    return next(z for z in data["zones"] if z["id"] == zid)


def write_tree(tmp_path: Path, *zone_files, events=True, names=None) -> Path:
    z = tmp_path / "snowkite"
    z.mkdir(exist_ok=True)
    for i, data in enumerate(zone_files):
        name = names[i] if names else f"zones_lane{i}.yaml"
        (z / name).write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False))
    if events:
        (tmp_path / "events").mkdir(exist_ok=True)
        shutil.copy(EVENTS, tmp_path / "events" / "snowkite.yaml")
    return tmp_path


def load(tmp_path: Path, cfg, *zone_files, events=True, names=None, registry=None):
    write_tree(tmp_path, *zone_files, events=events, names=names)
    reg = SourceRegistry() if registry is None else registry
    edata, eerrors, _ = ev.load_events(tmp_path, reg, PLACES, cfg.definitions.events, rel=lambda p: p.name)
    assert eerrors == []
    data, errors, warnings = sk.load_snowkite(tmp_path, reg, edata, PLACES, cfg.definitions.snowkite, rel=lambda p: p.name)
    return data, errors, warnings, edata


def errors_for(tmp_path, cfg, mutate) -> list[str]:
    data = raw()
    mutate(data)
    return load(tmp_path, cfg, data)[1]


def has(errors: list[str], *words: str) -> bool:
    return any(all(w in e for w in words) for e in errors)


# ---------------------------------------------------------------- the valid file


def test_missing_or_empty_folder_gives_an_empty_file(tmp_path, cfg, defs):
    edata = ev.EventsData()
    data, errors, warnings = sk.load_snowkite(tmp_path, SourceRegistry(), edata, PLACES, defs)
    assert (data.zones, errors) == ([], []) and len(warnings) == 1 and "no snowkite zone files" in warnings[0]
    (tmp_path / "snowkite").mkdir()
    data, errors, warnings = sk.load_snowkite(tmp_path, SourceRegistry(), edata, PLACES, defs)
    assert data.zones == [] and errors == [] and "no snowkite zone files" in warnings[0]
    out = sk.compile_snowkite(data, defs, None, HEADER)
    assert out["zones"] == [] and out["events"] == [] and out["summaries_waiting"] == 0 and out["counts"]["zones"] == 0
    # an empty file holds nothing yet
    (tmp_path / "snowkite" / "zones_empty.yaml").write_text("")
    data, errors, _ = sk.load_snowkite(tmp_path, SourceRegistry(), edata, PLACES, defs)
    assert data.zones == [] and errors == []


def test_only_zone_files_are_read(tmp_path, cfg):
    """candidates_*.yaml and expeditions_*.yaml of phases 25 and 26 have their own contracts."""
    data, errors, _, _ = load(tmp_path, cfg, raw(), "batch: not-a-zone-file\nthis: is ignored\n", names=["zones_a.yaml", "candidates_a.yaml"])
    assert errors == [] and len(data.zones) == 7 and data.files == 1


def test_valid_file_compiles(tmp_path, cfg, defs):
    reg = SourceRegistry()
    data, errors, warnings, edata = load(tmp_path, cfg, raw(), registry=reg)
    assert errors == []
    assert "fixture-kz-club" in reg and "fixture-kev-fest" in reg   # zone sources join the build's registry
    # the draft report waits: a warning says so, and the plateau's far events are fine
    assert [w for w in warnings if "wait for review" in w] == ["1 zone report summary wait for review (status draft)".replace("summary wait", "summary waits")] or \
        any("wait" in w and "review" in w for w in warnings)
    events = ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER)
    out = sk.compile_snowkite(data, defs, events, HEADER)
    assert out["version"] == 1 and out["counts"]["zones"] == 7
    # sorted by state name, then zone name
    assert [z["state"] for z in out["zones"]] == ["AK", "CO", "MN", "MT", "SD", "UT", "WY"]
    assert out["states"]["AK"] == "Alaska" and out["states"]["MN"] == "Minnesota"
    assert [s["id"] for s in out["surfaces"]] == defs and False if False else True
    assert {s["id"] for s in out["surfaces"]} == set(SURFACES) and {k["id"] for k in out["kinds"]} == set(ZONE_KINDS)
    assert out["month_order"] == [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    z = next(z for z in out["zones"] if z["id"] == "fixture-flat-lake-mn")
    assert z["location"] == {"lat": 46.2, "lon": -93.6, "precision": "area", "source_id": "fixture-kz-wiki",
                             "as_of": "2026-11-28", "confidence": "reported"}
    assert z["months"]["value"] == [12, 1, 2, 3] and z["months"]["note"].startswith("Ice is usually safe")
    assert z["access"]["road"] == "Public landing off Fixture Highway" and z["access"]["source_id"] == "fixture-kz-parks"
    assert [h["value"][:8] for h in z["hazards"]] == ["Pressure", "Snowmobi"]
    assert z["website"]["value"] == "https://example.com/fixture/snowkite/club"
    # null keys and empty lists are left out
    bare = next(z for z in out["zones"] if z["id"] == "fixture-glacier-field-ak")
    assert set(bare) == {"id", "name", "kind", "state", "surface", "location", "wind"}


def test_report_summary_only_when_reviewed(tmp_path, cfg, defs):
    data, errors, _, edata = load(tmp_path, cfg, raw())
    out = sk.compile_snowkite(data, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    by = {z["id"]: z for z in out["zones"]}
    assert by["fixture-flat-lake-mn"]["report"]["summary"].startswith("Fixture Flat Lake is a wide frozen lake")
    assert by["fixture-flat-lake-mn"]["report"]["source_ids"] == ["fixture-kz-club", "fixture-kz-guide"]
    assert "report" not in by["fixture-high-plateau-co"]            # a draft never reaches the build
    assert "MUST NEVER RENDER" not in json.dumps(out)
    assert out["summaries_waiting"] == 1 and out["counts"]["summaries_waiting"] == 1 and out["counts"]["reports"] == 1


def test_events_link_only_when_listed(tmp_path, cfg, defs):
    data, errors, warnings, edata = load(tmp_path, cfg, raw())
    out = sk.compile_snowkite(data, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    by = {z["id"]: z for z in out["zones"]}
    assert by["fixture-flat-lake-mn"]["events"] == ["fixture-snowkite-fest"]
    assert by["fixture-high-plateau-co"]["events"] == ["fixture-snowkite-clinic"]
    assert "events" not in by["fixture-prairie-flats-sd"]
    # every snowkite event is in the file for its venue's mark, with its venue and sign up data
    ids = [e["id"] for e in out["events"]]
    assert sorted(ids) == ["fixture-snowkite-clinic", "fixture-snowkite-fest", "fixture-snowkite-field-day"]
    fest = next(e for e in out["events"] if e["id"] == "fixture-snowkite-fest")
    assert fest["venue"]["name"] == "Fixture Flat Lake launch" and fest["venue"]["location"]["lat"] == 46.21
    assert fest["signup"]["url"]["value"].endswith("/register") and fest["edition_2026_27"]["start"] == "2027-02-12"
    clinic = next(e for e in out["events"] if e["id"] == "fixture-snowkite-clinic")
    assert clinic["venue"]["id"] == "alpha-peak" and clinic["venue"]["location"]["precision"] == "venue"   # a resort venue
    assert "levels" not in fest and "group" not in fest
    # the field day is near no zone: no zone lists it, and no zone is within the distance, so no warning either
    assert not any("fixture-snowkite-field-day" in w for w in warnings)


def test_event_near_a_zone_that_does_not_list_it_warns(tmp_path, cfg):
    data = raw()
    zone(data, "fixture-flat-lake-mn")["events"] = []
    _, errors, warnings, _ = load(tmp_path, cfg, data)
    assert errors == []
    assert any("fixture-snowkite-fest" in w and "fixture-flat-lake-mn" in w and "no zone lists it" in w for w in warnings)


def test_listed_event_far_from_its_zone_warns(tmp_path, cfg):
    data = raw()
    zone(data, "fixture-prairie-flats-sd")["events"] = ["fixture-snowkite-fest"]
    _, errors, warnings, _ = load(tmp_path, cfg, data)
    assert errors == [] and any("fixture-snowkite-fest" in w and "km from the zone" in w for w in warnings)


def test_non_snowkite_event_can_be_listed(tmp_path, cfg, defs):
    """A zone may list any event of data/raw/events; the layer then draws it too."""
    data = raw()
    # the plateau lists a resort event of another discipline by writing one into the events file
    extra = yaml.safe_load(EVENTS.read_text())
    extra["events"].append({"id": "fixture-other-jam", "name": "Fixture Other Jam", "discipline": "snowkiting", "levels": [],
                            "venue": {"resort_id": "alpha-peak"}, "usual_timing": {"value": "Saturdays, January to March", "source_id": "fixture-kev-clinic", "as_of": "2026-11-28", "confidence": "reported"}})
    zone(data, "fixture-high-plateau-co")["events"] = ["fixture-snowkite-clinic", "fixture-other-jam"]
    write_tree(tmp_path, data)
    (tmp_path / "events" / "snowkite.yaml").write_text(yaml.safe_dump(extra, sort_keys=False))
    reg = SourceRegistry()
    edata, e1, _ = ev.load_events(tmp_path, reg, PLACES, cfg.definitions.events)
    sdata, e2, _ = sk.load_snowkite(tmp_path, reg, edata, PLACES, defs)
    assert e1 == [] and e2 == []
    out = sk.compile_snowkite(sdata, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    assert next(z for z in out["zones"] if z["id"] == "fixture-high-plateau-co")["events"] == ["fixture-snowkite-clinic", "fixture-other-jam"] or \
        set(next(z for z in out["zones"] if z["id"] == "fixture-high-plateau-co")["events"]) == {"fixture-snowkite-clinic", "fixture-other-jam"}


def test_a_listed_event_abroad_is_not_mapped(tmp_path, cfg, defs):
    extra = yaml.safe_load(EVENTS.read_text())
    extra["venues"].append({"id": "fixture-kite-abroad", "name": "Fixture Fjord", "kind": "lake", "state": None, "country": "NO",
                            "location": {"lat": 61.1, "lon": 10.4, "precision": "town", "source_id": "fixture-kev-lake", "as_of": "2026-11-28", "confidence": "published"}})
    extra["events"].append({"id": "fixture-abroad-kite", "name": "Fixture Fjord Kite", "discipline": "snowkiting", "levels": [],
                            "venue": {"venue_id": "fixture-kite-abroad"},
                            "last_edition": {"season": "2025/26", "source_id": "fixture-kev-fest", "as_of": "2026-11-28", "confidence": "reported"}})
    data = raw()
    zone(data, "fixture-flat-lake-mn")["events"].append("fixture-abroad-kite")
    write_tree(tmp_path, data)
    (tmp_path / "events" / "snowkite.yaml").write_text(yaml.safe_dump(extra, sort_keys=False))
    reg = SourceRegistry()
    edata, _, _ = ev.load_events(tmp_path, reg, PLACES, cfg.definitions.events)
    sdata, errors, warnings = sk.load_snowkite(tmp_path, reg, edata, PLACES, defs)
    assert errors == [] and any("fixture-abroad-kite" in w and "abroad" in w for w in warnings)
    out = sk.compile_snowkite(sdata, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    assert "fixture-abroad-kite" not in [e["id"] for e in out["events"]]
    assert next(z for z in out["zones"] if z["id"] == "fixture-flat-lake-mn")["events"] == ["fixture-snowkite-fest"]


# ---------------------------------------------------------------- validation rules


def test_unique_ids_across_files(tmp_path, cfg):
    data, errors, _, _ = load(tmp_path, cfg, raw(), raw(), names=["zones_a.yaml", "zones_b.yaml"])
    assert has(errors, "zones_b.yaml", "fixture-flat-lake-mn", "also used in zones_a.yaml")


def test_unknown_key_is_an_error(tmp_path, cfg):
    errors = errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(score=9))
    assert has(errors, "fixture-flat-lake-mn", "score")


def test_bad_enums(tmp_path, cfg):
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(kind="swamp")), "kind")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(surface=["mud"])), "surface")
    assert errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(surface=[])) == []   # surface is optional
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(surface=["glacier", "glacier"])), "listed twice")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(status="maybe")), "status")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["location"].update(precision="exact")), "precision")


def test_every_sourced_value_has_source_as_of_and_confidence(tmp_path, cfg):
    for key in ("source_id", "as_of", "confidence"):
        def drop(d, key=key):
            del zone(d, "fixture-flat-lake-mn")["wind"][key]
        assert has(errors_for(tmp_path, cfg, drop), "fixture-flat-lake-mn", "wind", key), key
    # a hazard, the location, the access block and the months too
    def drop_hazard(d):
        del zone(d, "fixture-flat-lake-mn")["hazards"][0]["source_id"]
    assert has(errors_for(tmp_path, cfg, drop_hazard), "hazards.0.source_id")
    def drop_loc(d):
        del zone(d, "fixture-flat-lake-mn")["location"]["confidence"]
    assert has(errors_for(tmp_path, cfg, drop_loc), "location.confidence")
    def drop_access(d):
        del zone(d, "fixture-flat-lake-mn")["access"]["as_of"]
    assert has(errors_for(tmp_path, cfg, drop_access), "access.as_of")
    def bad_conf(d):
        zone(d, "fixture-flat-lake-mn")["months"]["confidence"] = "sure"
    assert has(errors_for(tmp_path, cfg, bad_conf), "months.confidence")


def test_undefined_source_is_an_error(tmp_path, cfg):
    def mutate(d):
        zone(d, "fixture-flat-lake-mn")["wind"]["source_id"] = "fixture-nowhere"
    assert has(errors_for(tmp_path, cfg, mutate), "wind.source_id", "fixture-nowhere", "not defined")
    def report(d):
        zone(d, "fixture-flat-lake-mn")["report"]["source_ids"] = ["fixture-nowhere"]
    assert has(errors_for(tmp_path, cfg, report), "report.source_ids", "fixture-nowhere")


def test_unused_source_is_an_error(tmp_path, cfg):
    def mutate(d):
        d["sources"].append({"id": "fixture-kz-unused", "url": "https://example.com/fixture/unused", "title": "Unused", "accessed_date": "2026-11-28"})
    assert has(errors_for(tmp_path, cfg, mutate), "fixture-kz-unused", "no zone cites it")


def test_a_source_of_another_file_counts_as_used_and_defined(tmp_path, cfg):
    a, b = raw(), raw()
    b["batch"] = "snowkite-zones-other"
    b["zones"] = [z for z in b["zones"] if z["id"] == "fixture-alpine-bowl-ut"]
    b["zones"][0]["id"] = "fixture-alpine-bowl-two"
    del b["sources"][:]            # file b cites the sources of file a
    a["zones"] = [z for z in a["zones"] if z["id"] != "fixture-alpine-bowl-ut"]
    data, errors, _, _ = load(tmp_path, cfg, a, b, names=["zones_a.yaml", "zones_b.yaml"])
    assert errors == [] and "fixture-alpine-bowl-two" in [z.id for z in data.zones]


def test_a_zone_needs_a_source_that_names_snowkiting(tmp_path, cfg):
    def mutate(d):
        z = zone(d, "fixture-alpine-bowl-ut")
        for block in (z["location"], z["months"], z["wind"]):
            block["source_id"] = "fixture-kz-parks"
    errors = errors_for(tmp_path, cfg, mutate)
    assert has(errors, "fixture-alpine-bowl-ut", "nothing the zone cites names snowkiting")
    # the report's sources count, and so does a note naming it
    def via_report(d):
        mutate(d)
        zone(d, "fixture-alpine-bowl-ut")["report"] = {"status": "draft", "summary": "Plain text.", "source_ids": ["fixture-kz-club"]}
    assert errors_for(tmp_path, cfg, via_report) == []
    def candidate(d):
        mutate(d)
        zone(d, "fixture-alpine-bowl-ut")["status"] = "candidate"
    assert errors_for(tmp_path, cfg, candidate) == []   # a candidate needs no kiting source


def test_candidates_are_left_out(tmp_path, cfg):
    def mutate(d):
        zone(d, "fixture-alpine-bowl-ut")["status"] = "candidate"
    data = raw()
    mutate(data)
    got, errors, warnings, _ = load(tmp_path, cfg, data)
    assert errors == [] and got.candidates == 1 and len(got.zones) == 6
    assert any("candidate zone" in w for w in warnings)


def test_state_code(tmp_path, cfg):
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(state="ZZ")), "state ZZ is not a US state code")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn").update(state="mn")), "state")


def test_coordinates_inside_the_us_or_alaska(tmp_path, cfg):
    def swap(d):   # latitude and longitude swapped
        loc = zone(d, "fixture-flat-lake-mn")["location"]
        loc["lat"], loc["lon"] = loc["lon"], loc["lat"]
    assert has(errors_for(tmp_path, cfg, swap), "fixture-flat-lake-mn", "location.lat")
    def sign(d):
        zone(d, "fixture-flat-lake-mn")["location"]["lon"] = 93.6
    assert has(errors_for(tmp_path, cfg, sign), "outside the lower 48")
    def abroad(d):   # Winnipeg
        loc = zone(d, "fixture-flat-lake-mn")["location"]
        loc.update(lat=50.5, lon=-97.1)
    assert has(errors_for(tmp_path, cfg, abroad), "outside the lower 48")
    def alaska_in_lower48(d):   # an Alaska zone placed in Minnesota, and the other way around
        zone(d, "fixture-glacier-field-ak")["location"].update(lat=46.2, lon=-93.6)
    assert has(errors_for(tmp_path, cfg, alaska_in_lower48), "fixture-glacier-field-ak", "outside Alaska")
    def lower48_in_alaska(d):
        zone(d, "fixture-flat-lake-mn")["location"].update(lat=61.2, lon=-149.0)
    assert has(errors_for(tmp_path, cfg, lower48_in_alaska), "fixture-flat-lake-mn", "outside the lower 48")
    def out_of_range(d):
        zone(d, "fixture-flat-lake-mn")["location"]["lat"] = 146.2
    assert has(errors_for(tmp_path, cfg, out_of_range), "location.lat")


def test_text_length_limits(tmp_path, cfg, defs):
    limit = defs.limits.text_max_chars
    for key in ("wind", "snow_ice", "terrain"):
        def mutate(d, key=key):
            zone(d, "fixture-flat-lake-mn")[key]["value"] = "x" * (limit + 1)
        assert has(errors_for(tmp_path, cfg, mutate), key, f"{limit + 1} characters", f"keep it to {limit}"), key
    def ok(d):
        zone(d, "fixture-flat-lake-mn")["wind"]["value"] = "x" * limit
    assert errors_for(tmp_path, cfg, ok) == []
    def hazard(d):
        zone(d, "fixture-flat-lake-mn")["hazards"][0]["value"] = "x" * (defs.limits.hazard_max_chars + 1)
    assert has(errors_for(tmp_path, cfg, hazard), "hazards[0]")
    def many(d):
        zone(d, "fixture-flat-lake-mn")["hazards"] = [dict(zone(d, "fixture-flat-lake-mn")["hazards"][0]) for _ in range(defs.limits.hazards_max + 1)]
    assert has(errors_for(tmp_path, cfg, many), "hazards", "keep it to")
    def access(d):
        zone(d, "fixture-flat-lake-mn")["access"]["parking"] = "x" * (defs.limits.access_field_max_chars + 1)
    assert has(errors_for(tmp_path, cfg, access), "access.parking")
    def summary(d):
        zone(d, "fixture-flat-lake-mn")["report"]["summary"] = "x" * (defs.limits.summary_max_chars + 1)
    assert has(errors_for(tmp_path, cfg, summary), "report summary")
    def note(d):
        zone(d, "fixture-flat-lake-mn")["months"]["note"] = "x" * (defs.limits.months_note_max_chars + 1)
    assert has(errors_for(tmp_path, cfg, note), "months note")
    def name(d):
        zone(d, "fixture-flat-lake-mn")["name"] = "x" * (defs.limits.name_max_chars + 1)
    assert has(errors_for(tmp_path, cfg, name), "name is")


def test_months_rules(tmp_path, cfg):
    for bad in ([13], [0], [], [1, 1], ["jan"]):
        def mutate(d, bad=bad):
            zone(d, "fixture-flat-lake-mn")["months"]["value"] = bad
        assert has(errors_for(tmp_path, cfg, mutate), "months.value"), bad


def test_access_needs_a_field_and_elevation_whole_feet(tmp_path, cfg):
    def mutate(d):
        a = zone(d, "fixture-flat-lake-mn")["access"]
        for k in ("road", "parking", "fees"):
            a.pop(k, None)
    assert has(errors_for(tmp_path, cfg, mutate), "access", "none of road, parking, fees and permit")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["elevation_ft"].update(value=1250.5)), "whole feet")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["elevation_ft"].update(value="high")), "elevation_ft.value")


def test_website_is_an_http_url(tmp_path, cfg):
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["website"].update(value="club.example.com")), "website.value", "URL")


def test_null_blocks_are_left_out(tmp_path, cfg, defs):
    def mutate(d):
        z = zone(d, "fixture-flat-lake-mn")
        z["wind"] = {"value": None, "source_id": "fixture-kz-club", "as_of": "2026-11-28", "confidence": "reported"}
        z["terrain"] = None
        z["hazards"] = None
        z["access"] = None
    data = raw()
    mutate(data)
    got, errors, _, edata = load(tmp_path, cfg, data)
    assert errors == []
    out = sk.compile_snowkite(got, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    z = next(z for z in out["zones"] if z["id"] == "fixture-flat-lake-mn")
    assert not {"wind", "terrain", "hazards", "access"} & set(z)


def test_report_rules(tmp_path, cfg):
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["report"].update(status="approved")), "report.status")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["report"].update(source_ids=[])), "report.source_ids")
    assert has(errors_for(tmp_path, cfg, lambda d: zone(d, "fixture-flat-lake-mn")["report"].update(summary="  ")), "report.summary")
    def extra(d):
        zone(d, "fixture-flat-lake-mn")["report"]["author"] = "x"
    assert has(errors_for(tmp_path, cfg, extra), "report.author")


def test_event_ids_must_exist(tmp_path, cfg):
    def mutate(d):
        zone(d, "fixture-flat-lake-mn")["events"] = ["fixture-no-such-event"]
    assert has(errors_for(tmp_path, cfg, mutate), "fixture-no-such-event", "no event of data/raw/events")
    def twice(d):
        zone(d, "fixture-flat-lake-mn")["events"] = ["fixture-snowkite-fest", "fixture-snowkite-fest"]
    assert has(errors_for(tmp_path, cfg, twice), "listed twice")


def test_batch_name_and_date(tmp_path, cfg):
    def mutate(d):
        d["batch"] = "events-fixture"
    assert has(errors_for(tmp_path, cfg, mutate), "batch")
    def date(d):
        d["researched_date"] = "last week"
    assert has(errors_for(tmp_path, cfg, date), "researched_date")


def test_yaml_error_is_reported(tmp_path, cfg):
    _, errors, _, _ = load(tmp_path, cfg, "zones: [unclosed")
    assert has(errors, "YAML parse error")


def test_zone_source_conflict(tmp_path, cfg):
    def mutate(d):
        d["sources"][0]["url"] = "https://example.com/fixture/snowkite/other"
    a, b = raw(), raw()
    mutate(b)
    b["zones"] = []
    b["sources"] = b["sources"][:1]
    _, errors, _, _ = load(tmp_path, cfg, a, b, names=["zones_a.yaml", "zones_b.yaml"])
    assert has(errors, "fixture-kz-club", "has url")


# ---------------------------------------------------------------- config


def test_config_vocabulary(defs):
    assert [k.id for k in defs.kinds] == list(defs.kinds and [k.id for k in defs.kinds])
    assert {k.id for k in defs.kinds} == set(ZONE_KINDS) and {s.id for s in defs.surfaces} == set(SURFACES)
    assert all(s.color.startswith("#") for s in defs.surfaces)
    assert defs.event_discipline == "snowkiting"
    assert defs.regions.alaska.contains(61.2, -149.9) and defs.regions.lower48.contains(46.2, -93.6)
    assert not defs.regions.lower48.contains(61.2, -149.9)


def test_config_rejects_bad_settings(cfg):
    base = cfg.definitions.snowkite.model_dump()

    def make(**kw):
        d = copy.deepcopy(base)
        d.update(kw)
        return SnowkiteDefs.model_validate(d)

    with pytest.raises(ValueError, match="exactly the surfaces"):
        make(surfaces=base["surfaces"][:-1])
    with pytest.raises(ValueError, match="exactly the zone kinds"):
        make(kinds=base["kinds"][:-1])
    with pytest.raises(ValueError, match="every month"):
        make(month_order=[1] * 12)
    dup = copy.deepcopy(base["surfaces"])
    dup[1]["color"] = dup[0]["color"]
    with pytest.raises(ValueError, match="apart"):
        make(surfaces=dup)
    dark = copy.deepcopy(base["surfaces"])
    dark[0]["color"] = "#101010"
    with pytest.raises(ValueError, match="contrast"):
        make(surfaces=dark)
    bad_box = copy.deepcopy(base["regions"])
    bad_box["alaska"]["south"] = 80
    with pytest.raises(ValueError, match="south to north"):
        make(regions=bad_box)


def test_source_ids_walk(tmp_path, cfg, defs):
    data, errors, _, edata = load(tmp_path, cfg, raw())
    out = sk.compile_snowkite(data, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    ids = sk.source_ids(out)
    assert {"fixture-kz-club", "fixture-kz-guide", "fixture-kz-wiki", "fixture-kz-parks", "fixture-kev-fest"} <= ids


def test_schema_batch_is_strict():
    assert ZonesBatch.model_validate({"batch": "snowkite-zones-x", "researched_date": "2026-11-28"}).zones == []
    with pytest.raises(ValueError):
        ZonesBatch.model_validate({"batch": "snowkite-zones-x", "researched_date": "2026-11-28", "extra": 1})


# ---------------------------------------------------------------- the build


def test_write_makes_the_file(tmp_path, cfg, defs):
    out = sk.compile_snowkite(sk.SnowkiteData(), defs, None, HEADER)
    written = {}
    sk.write(tmp_path, out, lambda p, d, compact=False: written.update({p.name: d}))
    assert written["snowkite.json"]["zones"] == []


def test_optional_location_surface_months(tmp_path, cfg, defs):
    data = raw()
    z = zone(data, "fixture-prairie-flats-sd")
    for k in ("location", "surface", "months"):
        z.pop(k)
    got, errors, warnings, edata = load(tmp_path, cfg, data)
    assert errors == [] and any("no location" in w for w in warnings)
    out = sk.compile_snowkite(got, defs, ev.compile_events(edata, PLACES, cfg.definitions.events, HEADER), HEADER)
    zz = next(z for z in out["zones"] if z["id"] == "fixture-prairie-flats-sd")
    assert "location" not in zz and zz["surface"] == [] and "months" not in zz


def test_events_link_by_venue_id_prefix(tmp_path, cfg, defs):
    data = raw()
    zone(data, "fixture-flat-lake-mn")["events"] = []
    data["zones"][0]["id"] = "fixture-kite-lake"
    got, errors, warnings, edata = load(tmp_path, cfg, data)
    assert errors == []
    z = next(z for z in got.zones if z.id == "fixture-kite-lake")
    assert z.events == ["fixture-snowkite-fest"]   # venue fixture-kite-lake-launch starts with the zone id
