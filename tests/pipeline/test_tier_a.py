import pytest
import yaml

from pipeline import openskimap
from pipeline.names import name_similarity, slugify
from pipeline.sources import SourceRegistry
from pipeline.tier_a import BuildError, load_raw, match_openskimap, ownership_cross_check


def _edit(path, fn):
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_load_raw_fixtures(raw_dir):
    reg = SourceRegistry()
    inputs = load_raw(raw_dir, reg)
    assert [r.id for _, r in inputs.records] == ["fixture-peak", "fixture-basin"]
    assert "fixture-owner-list" in reg  # declared in the ownership file, cited by a Tier A owner
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
    assert any("Fixture Ridge" in w for w in warnings)
    assert not any("Fixture Feeder" in w for w in warnings)


def test_ownership_cross_check_fails_when_missing(raw_dir):
    def drop(d):
        d["resorts"] = [r for r in d["resorts"] if r["name"] != "Fixture Peak"]
    _edit(raw_dir / "ownership" / "vail.yaml", drop)
    errors, _ = ownership_cross_check(load_raw(raw_dir, SourceRegistry()))
    assert len(errors) == 1 and "fixture-peak" in errors[0] and "missing from the Vail Resorts" in errors[0]


def test_ownership_cross_check_fails_without_file(raw_dir):
    def alterra(d):
        d["resorts"][0]["owner"]["value"] = "Alterra Mountain Company"
    _edit(raw_dir / "tier_a" / "fixture.yaml", alterra)
    errors, _ = ownership_cross_check(load_raw(raw_dir, SourceRegistry()))
    assert len(errors) == 1 and "no ownership file for Alterra" in errors[0]


def test_match_openskimap(raw_dir, cfg):
    inputs = load_raw(raw_dir, SourceRegistry())
    ext = openskimap.load_us_extract(raw_dir / "openskimap" / openskimap.US_NAME)
    m = match_openskimap([r for _, r in inputs.records], ext.areas, cfg.definitions.matching)
    assert [a.osm_id for a in m.by_tier_a["fixture-peak"]] == ["osm-peak"]
    assert [a.osm_id for a in m.by_tier_a["fixture-basin"]] == ["osm-basin"]
    assert sorted(a.osm_id for a in m.unmatched) == ["osm-faraway", "osm-other-near", "osm-valley", "osm-valley-ut"]


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
    assert m.by_tier_a["fixture-basin"] == [near_similar, far_exact]
    assert m.unmatched == [far_partial, too_far]


def test_names():
    assert slugify("Mt. Bachelor & Co's Peak") == "mt-bachelor-and-cos-peak"
    assert slugify("Arapahoe Basin Ski Área") == "arapahoe-basin-ski-area"
    assert name_similarity("Fixture Peak", "Fixture Peak Ski Resort") == 1.0
    assert name_similarity("Mt. Fixture", "Mount Fixture") == 1.0
    assert name_similarity("Fixture Peak", "Unrelated Bowl") < 0.5
