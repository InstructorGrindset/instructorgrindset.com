import pytest
import yaml

from pipeline import openskimap
from pipeline.names import name_similarity, slugify
from pipeline.sources import SourceRegistry
from pipeline.tier_a import BuildError, load_raw, match_openskimap, ownership_cross_check, ownership_source_age_check


def _edit(path, fn):
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_load_raw_fixtures(raw_dir):
    reg = SourceRegistry()
    inputs = load_raw(raw_dir, reg)
    assert [r.id for _, r in inputs.records] == ["fixture-peak", "fixture-basin", "fixture-peak-training-center",
                                                 "fixture-indoor"]
    assert "fixture-owner-list" in reg  # declared in the ownership file, cited by a Tier A owner
    assert "fixture-shared-decisions" in reg  # data/raw/sources/, cited without being redefined
    assert inputs.ownership[0].file.operator == "Vail Resorts"


def test_empty_raw_dir(tmp_path):
    inputs = load_raw(tmp_path / "raw", SourceRegistry())
    assert inputs.records == [] and inputs.ownership == []


def test_undefined_source_names_the_record(raw_dir):
    def bad(d):
        d["resorts"][1]["tier_a_criteria"][0]["source_id"] = "missing-source"
    _edit(raw_dir / "tier_a" / "fixture.yaml", bad)
    with pytest.raises(BuildError) as e:
        load_raw(raw_dir, SourceRegistry())
    assert any("resort 'fixture-basin'" in m and "missing-source" in m for m in e.value.errors)


def test_missing_lat_and_missing_criteria_reported(raw_dir):
    def bad(d):
        del d["resorts"][0]["lat"]
        d["resorts"][1]["tier_a_criteria"] = []
    _edit(raw_dir / "tier_a" / "fixture.yaml", bad)
    with pytest.raises(BuildError) as e:
        load_raw(raw_dir, SourceRegistry())
    msgs = "\n".join(e.value.errors)
    assert "'fixture-peak': lat: Field required" in msgs
    assert "'fixture-basin': tier_a_criteria" in msgs


def test_conflicting_source_urls(raw_dir):
    def bad(d):
        d["sources"].append({"id": "fixture-source-a", "url": "https://example.org/other", "title": "t",
                             "accessed_date": "2026-10-01"})
    _edit(raw_dir / "ownership" / "vail.yaml", bad)
    with pytest.raises(BuildError, match="fixture-source-a"):
        load_raw(raw_dir, SourceRegistry())


def test_duplicate_resort_id_across_batches(raw_dir):
    text = (raw_dir / "tier_a" / "fixture.yaml").read_text().replace("batch: fixture", "batch: fixture-two")
    (raw_dir / "tier_a" / "second.yaml").write_text(text)
    with pytest.raises(BuildError, match="id also used"):
        load_raw(raw_dir, SourceRegistry())


def test_ownership_cross_check(raw_dir):
    inputs = load_raw(raw_dir, SourceRegistry())
    errors, warnings = ownership_cross_check(inputs)
    assert errors == []
    # the reverse check: every ownership entry needs a Tier A record (resort_id links Fixture Peak)
    assert sorted(w.split(": ")[1].split(" (")[0] for w in warnings) == ["Fixture Feeder", "Fixture Ridge"]
    errors, warnings = ownership_cross_check(inputs, missing_record="fail")
    assert len(errors) == 2 and warnings == []


def test_shared_source_conflict(raw_dir):
    (raw_dir / "sources" / "more.yaml").write_text(
        "sources:\n  - {id: fixture-source-a, url: https://example.org/not-a, title: t, accessed_date: 2026-10-01}\n")
    with pytest.raises(BuildError, match="fixture-source-a"):
        load_raw(raw_dir, SourceRegistry())


def test_shared_source_file_validated(raw_dir):
    (raw_dir / "sources" / "bad.yaml").write_text("sources: []\n")
    with pytest.raises(BuildError, match="sources"):
        load_raw(raw_dir, SourceRegistry())


def test_ownership_cross_check_fails_when_missing(raw_dir):
    def drop(d):
        d["resorts"] = [r for r in d["resorts"] if r.get("resort_id") != "fixture-peak"]
    _edit(raw_dir / "ownership" / "vail.yaml", drop)
    errors, _ = ownership_cross_check(load_raw(raw_dir, SourceRegistry()))
    assert len(errors) == 1 and "fixture-peak" in errors[0] and "missing from the Vail Resorts" in errors[0]


def test_ownership_cross_check_fails_without_file(raw_dir):
    def alterra(d):
        d["resorts"][0]["owner"]["value"] = "Alterra Mountain Company"
    _edit(raw_dir / "tier_a" / "fixture.yaml", alterra)
    errors, _ = ownership_cross_check(load_raw(raw_dir, SourceRegistry()))
    assert [e for e in errors if "no ownership file for Alterra" in e] and len(errors) == 1


def test_ownership_source_age_fresh_source_passes(raw_dir, cfg):
    # The fixture portfolio source was accessed 2026-10-01 (tests/pipeline/fixtures/raw/ownership/vail.yaml).
    import datetime as dt
    registry = SourceRegistry()
    inputs = load_raw(raw_dir, registry)
    limit = cfg.definitions.ownership.max_source_age_days
    assert ownership_source_age_check(inputs, registry, dt.date(2026, 10, 13), limit) == []
    # exactly one build cycle old still passes; a source accessed after the build date counts as fresh
    assert ownership_source_age_check(inputs, registry, dt.date(2026, 10, 1) + dt.timedelta(days=limit), limit) == []
    assert ownership_source_age_check(inputs, registry, dt.date(2026, 9, 1), limit) == []


def test_ownership_source_age_stale_source_fails(raw_dir, cfg):
    import datetime as dt
    registry = SourceRegistry()
    inputs = load_raw(raw_dir, registry)
    limit = cfg.definitions.ownership.max_source_age_days
    today = dt.date(2026, 10, 1) + dt.timedelta(days=limit + 1)
    errors = ownership_source_age_check(inputs, registry, today, limit, rel=lambda p: p.name)
    assert len(errors) == 1
    msg = errors[0]
    for part in ("vail.yaml", "Vail Resorts ownership source 'fixture-owner-list'", "accessed on 2026-10-01",
                 f"{limit + 1} days before this build ({today.isoformat()})", f"One build cycle is {limit} days",
                 "ownership.max_source_age_days", "Refreshing the ownership sources"):
        assert part in msg, (part, msg)
    assert "\u2014" not in msg and "\u2013" not in msg


def test_ownership_source_age_checks_every_cited_source_of_vail_and_alterra_only(raw_dir):
    import datetime as dt

    def add_old_source(d):
        d["sources"].append({"id": "fixture-old-list", "url": "https://example.org/old", "title": "Old list",
                             "accessed_date": dt.date(2024, 1, 2)})
        d["sources"].append({"id": "fixture-unused", "url": "https://example.org/unused", "title": "Unused",
                             "accessed_date": dt.date(2020, 1, 1)})
        d["resorts"][1]["source_id"] = "fixture-old-list"
    _edit(raw_dir / "ownership" / "vail.yaml", add_old_source)
    registry = SourceRegistry()
    inputs = load_raw(raw_dir, registry)
    errors = ownership_source_age_check(inputs, registry, dt.date(2026, 10, 13), 400)
    # only the cited old source fails; a declared source no entry cites is not used for ownership
    assert len(errors) == 1 and "'fixture-old-list'" in errors[0]
    # another operator's file is not a Vail or Alterra portfolio
    _edit(raw_dir / "ownership" / "vail.yaml", lambda d: d.update(operator="Fixture Mountain Co"))
    registry = SourceRegistry()
    inputs = load_raw(raw_dir, registry)
    assert ownership_source_age_check(inputs, registry, dt.date(2026, 10, 13), 400) == []


def test_match_openskimap(raw_dir, cfg):
    inputs = load_raw(raw_dir, SourceRegistry())
    ext = openskimap.load_us_extract(raw_dir / "openskimap" / openskimap.US_NAME)
    m = match_openskimap([r for _, r in inputs.records], ext.areas, cfg.definitions.matching)
    assert [a.osm_id for a in m.by_tier_a["fixture-peak"]] == ["osm-peak"]
    assert [a.osm_id for a in m.by_tier_a["fixture-basin"]] == ["osm-basin"]
    assert "fixture-peak-training-center" not in m.by_tier_a  # only ski_area records match
    assert sorted(a.osm_id for a in m.unmatched) == ["osm-faraway", "osm-lake", "osm-legacy", "osm-other-near",
                                                     "osm-valley", "osm-valley-ut"]


def test_match_distance_and_strong_name(raw_dir, cfg):
    inputs = load_raw(raw_dir, SourceRegistry())
    rec = next(r for _, r in inputs.records if r.id == "fixture-basin")
    rule = cfg.definitions.matching

    def area(name, lon, lat):
        return openskimap.SkiArea("x", name, "UT", lon, lat, {"type": "Point", "coordinates": [lon, lat]})

    near_similar = area("Fixture Basin Resort", -111.60, 40.64)   # ~4.4 km
    far_exact = area("Fixture Basin", -111.60, 40.70)             # ~11 km, exact name
    far_partial = area("Basin Fixture Lodge", -111.60, 40.70)     # ~11 km, weaker name
    too_far = area("Fixture Basin", -111.60, 40.80)               # ~22 km
    m = match_openskimap([rec], [near_similar, far_exact, far_partial, too_far], rule)
    # single best match only; the other passing area is reported as a runner up and stays Tier B
    assert m.by_tier_a["fixture-basin"] == [near_similar]
    assert m.runners_up["fixture-basin"] == [far_exact]
    assert m.unmatched == [far_exact, far_partial, too_far]


def _area(name, lon, lat, state="UT"):
    return openskimap.SkiArea(name.lower(), name, state, lon, lat, {"type": "Point", "coordinates": [lon, lat]})


def test_openskimap_names_absorb_several(raw_dir, cfg):
    inputs = load_raw(raw_dir, SourceRegistry())
    rec = next(r for _, r in inputs.records if r.id == "fixture-basin")
    one, two = _area("Fixture Basin Upper", -111.60, 40.62), _area("Fixture Basin Lower", -111.62, 40.58)
    other = _area("Fixture Basin Annex", -111.61, 40.61)
    m = match_openskimap([rec.model_copy(update={"openskimap_names": ["Fixture Basin Upper", "fixture basin lower",
                                                                       "Fixture Basin Gone"]})],
                         [one, two, other], cfg.definitions.matching)
    assert m.by_tier_a["fixture-basin"] == [one, two]
    assert m.unmatched == [other]
    assert m.missing_names == {"fixture-basin": ["Fixture Basin Gone"]}


def test_sibling_records_each_take_their_own_area(raw_dir, cfg):
    """Two Tier A records side by side (Bald Mountain and Dollar Mountain style)."""
    inputs = load_raw(raw_dir, SourceRegistry())
    base = next(r for _, r in inputs.records if r.id == "fixture-basin")
    upper = base.model_copy(update={"id": "upper", "name": "Fixture Upper Mountain", "lat": 40.60, "lon": -111.60})
    lower = base.model_copy(update={"id": "lower", "name": "Fixture Lower Mountain", "lat": 40.62, "lon": -111.63})
    a_up = _area("Fixture - Upper Mountain", -111.601, 40.601)
    a_low = _area("Fixture - Lower Mountain", -111.631, 40.621)
    m = match_openskimap([upper, lower], [a_up, a_low], cfg.definitions.matching)
    assert m.by_tier_a == {"upper": [a_up], "lower": [a_low]} and m.unmatched == []


def test_training_center_never_takes_resort_area(raw_dir, cfg):
    inputs = load_raw(raw_dir, SourceRegistry())
    ext = openskimap.load_us_extract(raw_dir / "openskimap" / openskimap.US_NAME)
    training = next(r for _, r in inputs.records if r.id == "fixture-peak-training-center")
    m = match_openskimap([training], ext.areas, cfg.definitions.matching)
    assert m.by_tier_a == {} and len(m.unmatched) == len(ext.areas)


def test_facility_takes_only_its_named_area(raw_dir, cfg):
    """An indoor or training record absorbs its own OpenSkiMap area by explicit name only."""
    inputs = load_raw(raw_dir, SourceRegistry())
    training = next(r for _, r in inputs.records if r.id == "fixture-peak-training-center")
    own = _area("Fixture Training Hill", training.lon, training.lat)
    resort_area = _area(training.name, training.lon + 0.001, training.lat)
    named = training.model_copy(update={"openskimap_names": ["Fixture Training Hill"]})
    m = match_openskimap([named], [own, resort_area], cfg.definitions.matching)
    assert m.by_tier_a == {training.id: [own]} and m.unmatched == [resort_area]


def test_names():
    assert slugify("Mt. Bachelor & Co's Peak") == "mt-bachelor-and-cos-peak"
    assert slugify("Arapahoe Basin Ski Área") == "arapahoe-basin-ski-area"
    assert name_similarity("Fixture Peak", "Fixture Peak Ski Resort") == 1.0
    assert name_similarity("Mt. Fixture", "Mount Fixture") == 1.0
    assert name_similarity("Fixture Peak", "Unrelated Bowl") < 0.5
