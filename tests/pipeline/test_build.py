import json
import shutil
import subprocess
import sys

import pytest
import yaml

from pipeline.build import build
from pipeline.config import load_config
from pipeline.schemas import Cluster, Resort
from pipeline.tier_a import BuildError


@pytest.fixture
def out(tmp_path):
    return tmp_path / "build"


def _config_dir(repo_root, tmp_path, missing_record="warn"):
    """A copy of the real config with the ownership reverse check pinned, so fixture
    ownership entries without a Tier A record exercise the warning path."""
    cfg_dir = tmp_path / f"config-{missing_record}"
    if not cfg_dir.exists():
        shutil.copytree(repo_root / "config", cfg_dir)
        p = cfg_dir / "definitions.yaml"
        data = yaml.safe_load(p.read_text())
        data["ownership"]["missing_tier_a_record"] = missing_record
        p.write_text(yaml.safe_dump(data, sort_keys=False))
    return cfg_dir


def _run(repo_root, raw_dir, out, tmp_path, **kw):
    kw.setdefault("config_dir", _config_dir(repo_root, tmp_path))
    return build(repo_root, raw_dir=raw_dir, out_dir=out, report_path=tmp_path / "tier-a-draft.md",
                 states_geojson=False, **kw)


def _load(out, name):
    return json.loads((out / name).read_text())


def test_end_to_end(repo_root, raw_dir, out, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    (out / "climate").mkdir(parents=True)
    (out / "climate" / "fixture-peak.json").write_text("{}")
    result = _run(repo_root, raw_dir, out, tmp_path)

    resorts = _load(out, "resorts.json")
    assert resorts["version"] == 1 and resorts["generated_at"] == "2026-09-21T14:13:20Z"
    ids = [r["id"] for r in resorts["resorts"]]
    assert ids == sorted(ids)
    assert ids == ["faraway-hill", "fixture-basin", "fixture-indoor", "fixture-lake-area", "fixture-peak",
                   "fixture-peak-training-center", "fixture-valley", "fixture-valley-ut", "legacy-location-hill",
                   "unrelated-bowl"]
    by = {r["id"]: r for r in resorts["resorts"]}
    sources = {s["id"] for s in _load(out, "sources.json")["sources"]}
    for r in resorts["resorts"]:
        Resort.model_validate(r, context={"source_ids": sources})  # output round trips through the schema
        assert set(r["sources"]) <= sources

    peak = by["fixture-peak"]
    assert peak["tier"] == "A" and peak["tier_a_status"] == "draft" and peak["cluster_id"] == "i70-corridor"
    assert peak["facility_type"] == "ski_area" and peak["approval"] is None
    indoor = by["fixture-indoor"]
    assert indoor["facility_type"] == "indoor" and indoor["tier_a_status"] == "approved"
    assert indoor["approval"] == {"by": "Fixture Owner", "date": "2026-10-02", "note": None}
    assert indoor["cluster_id"] == "other-mi" and indoor["size"]["lifts"] is None
    assert indoor["sources"] == ["fixture-shared-decisions", "fixture-source-a"]
    training = by["fixture-peak-training-center"]
    assert training["facility_type"] == "training_center" and training["size"]["lifts"] is None
    assert "openskimap_names" not in peak
    assert peak["climate_available"] is True and by["fixture-basin"]["climate_available"] is False
    assert peak["size"]["acres"]["value"] == 4000
    assert peak["size"]["lifts"] == {"value": 11, "source_id": "openskimap-ski-areas", "as_of": "2026-10-01",
                                     "confidence": "estimated", "note": "lifts mapped in OpenStreetMap (OpenSkiMap)"}
    assert peak["season"]["open_typical"]["value"] == "11-20"
    assert peak["elevation"] == {
        "base_ft": {"value": round(2400.123456 * 3.280839895), "source_id": "openskimap-ski-areas", "as_of": "2026-10-01",
                    "confidence": "estimated", "note": "lowest mapped lift or downhill run elevation (OpenSkiMap)"},
        "summit_ft": {"value": round(3500 * 3.280839895), "source_id": "openskimap-ski-areas", "as_of": "2026-10-01",
                      "confidence": "estimated", "note": "highest mapped lift or downhill run elevation (OpenSkiMap)"},
    }
    assert peak["size"]["vertical_ft"]["confidence"] == "estimated"
    assert peak["size"]["vertical_ft"]["source_id"] == "openskimap-ski-areas"
    assert peak["career"] is None and peak["editorial"] is None
    assert peak["sources"] == ["fixture-owner-list", "fixture-shared-decisions", "fixture-source-a", "fixture-source-b",
                               "openskimap-ski-areas"]
    assert "cluster_hint" not in peak

    valley = by["fixture-valley"]
    assert valley["tier"] == "B" and valley["tier_a_status"] is None and valley["owner"] is None
    assert valley["facility_type"] == "ski_area" and valley["approval"] is None
    assert valley["cluster_id"] == "i70-corridor" and valley["size"]["lifts"]["value"] == 3
    assert valley["location_source_id"] == "openskimap-ski-areas"
    assert by["faraway-hill"]["cluster_id"] == "other-co"
    assert by["fixture-valley-ut"]["cluster_id"] == "other-ut"
    assert by["unrelated-bowl"]["cluster_id"] == "i70-corridor"
    assert by["fixture-lake-area"]["state"] == "CA" and by["fixture-lake-area"]["cluster_id"] == "other-ca"
    assert by["faraway-hill"]["elevation"]["base_ft"]["value"] == round(2600 * 3.280839895)
    assert by["faraway-hill"]["size"]["lifts"] is None  # no mapped lifts is not "0 lifts"

    clusters = _load(out, "clusters.geojson")
    assert clusters["type"] == "FeatureCollection" and clusters["version"] == 1
    props = {f["properties"]["id"]: f["properties"] for f in clusters["features"]}
    assert sorted(props) == ["i70-corridor", "other-ca", "other-co", "other-mi", "other-mt", "other-ut", "wasatch"]
    assert props["i70-corridor"]["resort_ids"] == ["fixture-peak", "fixture-peak-training-center", "fixture-valley",
                                                   "unrelated-bowl"]
    assert props["i70-corridor"]["tier_a_count"] == 2 and props["other-co"]["name"] == "Other Colorado"
    assert set(props["wasatch"]) == {"id", "name", "states", "resort_ids", "tier_a_count", "overview_md",
                                     "alexandre_take_md", "sources"}
    member_ids = sorted(rid for p in props.values() for rid in p["resort_ids"])
    assert member_ids == ids  # every resort in exactly one cluster
    for f in clusters["features"]:
        Cluster.model_validate({**f["properties"], "polygon": f["geometry"]})

    pts = _load(out, "resorts.geojson")
    peak_pt = next(f for f in pts["features"] if f["properties"]["id"] == "fixture-peak")
    assert peak_pt["geometry"] == {"type": "Point", "coordinates": [-106.3, 39.6]}
    assert peak_pt["properties"] == {"id": "fixture-peak", "name": "Fixture Peak", "tier": "A", "facility_type": "ski_area",
                                     "cluster_id": "i70-corridor", "state": "CO", "acres": 4000,
                                     "vertical_ft": round((3500.0 - 2400.12346) * 3.280839895), "lifts": 11,
                                     "size_rank": 4, "season_length_days": None, "snowfall_annual_in": None,
                                     "snowfall_annual_source": None,
                                     "access_rating": None, "lucrativeness_mid": None}
    assert next(f for f in pts["features"] if f["properties"]["id"] == "faraway-hill")["properties"]["size_rank"] == 1

    states = _load(out, "states.json")["states"]
    assert states == [
        {"code": "CA", "name": "California", "cluster_ids": ["other-ca"], "resort_count": 1},
        {"code": "CO", "name": "Colorado", "cluster_ids": ["i70-corridor", "other-co"], "resort_count": 5},
        {"code": "MI", "name": "Michigan", "cluster_ids": ["other-mi"], "resort_count": 1},
        {"code": "MT", "name": "Montana", "cluster_ids": ["other-mt"], "resort_count": 1},
        {"code": "UT", "name": "Utah", "cluster_ids": ["other-ut", "wasatch"], "resort_count": 2},
    ]
    assert sources == {"fixture-owner-list", "fixture-shared-decisions", "fixture-source-a", "fixture-source-b",
                       "openskimap-ski-areas"}
    assert result.tier_a_count == 4 and len(result.clusters) == 7

    report = (tmp_path / "tier-a-draft.md").read_text()
    assert "| Fixture Peak (`fixture-peak`) | ski area | CO | Vail Resorts" in report
    assert "**Luxury**: Fixture rationale. ([Fixture source A](https://example.org/a))" in report
    assert "**Operator compensation**" in report and "**Year round training**" in report
    assert "| Fixture Indoor Snow (`fixture-indoor`) | indoor | MI |" in report
    assert "approved by Fixture Owner on 2026-10-02" in report
    rows = [l for l in report.splitlines() if l.startswith("| Fixture") and "`)" in l]
    assert rows[0].startswith("| Fixture Indoor Snow")  # approved rows first
    assert "Fixture Ridge (CO) is in the ownership file but has no Tier A record" in report
    assert "| Fixture Ridge | CO | **missing** |" in report and "| Fixture Peak Resort | CO | yes |" in report
    assert "vertical_ft" not in report.split("fixture-peak")[1].split("\n")[0]  # filled from OpenSkiMap
    assert "ski_school_staff_est" in report


def test_build_is_deterministic(repo_root, raw_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    a, b = tmp_path / "a", tmp_path / "b"
    _run(repo_root, raw_dir, a, tmp_path)
    _run(repo_root, raw_dir, b, tmp_path)
    for name in ("resorts.json", "resorts.geojson", "clusters.geojson", "states.json", "sources.json", "definitions.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_approved_batch(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["status"] = "approved"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(BuildError, match="resort 'fixture-peak': an approved Tier A record needs an approval block"):
        _run(repo_root, raw_dir, out, tmp_path)
    for r in data["resorts"]:
        r.setdefault("approval", {"by": "Fixture Owner", "date": "2026-10-03", "note": "batch"})
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    _run(repo_root, raw_dir, out, tmp_path)
    tier_a = [r for r in _load(out, "resorts.json")["resorts"] if r["tier"] == "A"]
    assert {r["tier_a_status"] for r in tier_a} == {"approved"}
    assert {r["approval"]["note"] for r in tier_a} == {"batch", None}  # fixture-indoor keeps its own


def test_approval_on_draft_record_fails(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"][1]["approval"] = {"by": "Fixture Owner", "date": "2026-10-03"}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(BuildError, match="fixture-basin': approval is set but tier_a_status is draft"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_runner_up_warning(repo_root, raw_dir, out, tmp_path):
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert not any("took its best OpenSkiMap area only" in w for w in result.warnings)


def test_tier_a_only_without_openskimap(repo_root, raw_dir, out, tmp_path):
    for f in (raw_dir / "openskimap").iterdir():
        f.unlink()
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert [r.id for r in result.resorts] == ["fixture-basin", "fixture-indoor", "fixture-peak", "fixture-peak-training-center"]
    assert any("no OpenSkiMap extract" in w for w in result.warnings)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["size"]["lifts"] is None
    assert "openskimap-ski-areas" not in {s["id"] for s in _load(out, "sources.json")["sources"]}


def test_empty_raw_build(repo_root, tmp_path, out):
    raw = tmp_path / "empty-raw"
    raw.mkdir()
    result = _run(repo_root, raw, out, tmp_path)
    assert result.resorts == [] and result.clusters == [] and result.states == []
    assert _load(out, "resorts.json")["resorts"] == []
    assert _load(out, "states.json")["states"] == []
    assert _load(out, "sources.json")["sources"] == []
    for name in ("resorts.geojson", "clusters.geojson"):
        fc = _load(out, name)
        assert fc["type"] == "FeatureCollection" and fc["features"] == [] and fc["version"] == 1
    assert any("no Tier A research files" in w for w in result.warnings)
    assert "No Tier A records have been researched yet." in (tmp_path / "tier-a-draft.md").read_text()


def test_unknown_cluster_hint_fails(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    p.write_text(p.read_text().replace("cluster_hint: wasatch", "cluster_hint: atlantis"))
    with pytest.raises(BuildError, match="unknown cluster_hint 'atlantis'"):
        _run(repo_root, raw_dir, out, tmp_path)
    assert not (out / "resorts.json").exists()


def test_ownership_failure_blocks_build(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "ownership" / "vail.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"] = [r for r in data["resorts"] if r.get("resort_id") != "fixture-peak"]
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(BuildError, match="missing from the Vail Resorts ownership file"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_strict_ownership_check_blocks_build(repo_root, raw_dir, out, tmp_path):
    with pytest.raises(BuildError, match="Fixture Ridge \\(CO\\) is in the ownership file but has no Tier A record"):
        _run(repo_root, raw_dir, out, tmp_path, config_dir=_config_dir(repo_root, tmp_path, "fail"))


def test_undefined_source_blocks_build(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    p.write_text(p.read_text().replace("location_source_id: fixture-source-b", "location_source_id: ghost-source"))
    with pytest.raises(BuildError, match="ghost-source"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_fetch_failure_falls_back(repo_root, raw_dir, out, tmp_path, monkeypatch):
    from pipeline import lifts, openskimap, runs

    def boom(*a, **k):
        raise OSError("network blocked")
    # Every --fetch download fails (ski areas, lifts and the 835 MB runs file), so the test never touches the network.
    for mod in (openskimap, lifts, runs):
        monkeypatch.setattr(mod, "fetch", boom)
    result = _run(repo_root, raw_dir, out, tmp_path, fetch=True)
    assert any("download failed" in w for w in result.warnings)
    assert len(result.resorts) == 10  # cached full download is still filtered


@pytest.mark.parametrize("cmd", [["pipeline/build.py"], ["-m", "pipeline.build"]])
def test_entry_points(repo_root, cmd):
    r = subprocess.run([sys.executable, *cmd, "--help"], cwd=repo_root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "--fetch" in r.stdout


def test_climate_sources_are_merged(repo_root, raw_dir, out, tmp_path):
    (out / "climate").mkdir(parents=True)
    note = "Storm density is SWE gain over depth gain."
    (out / "climate" / "sources.json").write_text(json.dumps({"version": 1, "generated_at": "2026-10-01T00:00:00Z", "sources": [
        {"id": "nrcs-snotel-0001-co", "url": "https://example.org/snotel", "title": "Fixture SNOTEL", "accessed_date": "2026-10-01",
         "note": note}]}))
    (out / "climate" / "provenance.json").write_text("not json, and ignored")
    _run(repo_root, raw_dir, out, tmp_path)
    by = {s["id"]: s for s in _load(out, "sources.json")["sources"]}
    assert by["nrcs-snotel-0001-co"]["note"] == note
    assert by["fixture-source-a"]["note"] is None


def test_climate_source_conflict_fails(repo_root, raw_dir, out, tmp_path):
    (out / "climate").mkdir(parents=True)
    (out / "climate" / "sources.json").write_text(json.dumps({"sources": [
        {"id": "fixture-source-a", "url": "https://example.org/elsewhere", "title": "t", "accessed_date": "2026-10-01"}]}))
    with pytest.raises(BuildError, match="fixture-source-a"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_tier_a_elevation_wins_over_openskimap(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"][0]["elevation"] = {"base_ft": {"value": 8000, "source_id": "fixture-source-a", "as_of": "2026-10-01",
                                                   "confidence": "published"}}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["elevation"]["base_ft"]["value"] == 8000 and peak["elevation"]["base_ft"]["confidence"] == "published"
    assert peak["elevation"]["summit_ft"]["source_id"] == "openskimap-ski-areas"


def test_definitions_json(repo_root, tmp_path, out):
    raw = tmp_path / "empty-raw"
    raw.mkdir()
    _run(repo_root, raw, out, tmp_path)
    d = _load(out, "definitions.json")
    assert d["version"] == 1 and "generated_at" in d
    heat = d.pop("heat")
    # The pipeline only snowfall block stays out; the season fade reaches the browser fallback.
    assert set(heat) == {"ramp", "neutral", "regions", "field", "modes", "season"}
    assert heat["ramp"]["colors"][0] == "#414487" and set(heat["modes"]) == {"lucrativeness", "season", "snowfall"}
    cfg_heat = load_config(repo_root / "config").definitions.heat
    assert heat["field"] == cfg_heat.field.model_dump(mode="json")
    assert heat["season"] == cfg_heat.season.model_dump(mode="json") and heat["neutral"] == cfg_heat.neutral
    snow = heat["modes"]["snowfall"]
    assert snow["scale"] == "classed" and len(snow["colors"]) == len(snow["breaks"]) + 1 and snow["breaks"][0] == 24
    assert {k: v for k, v in d.items() if k not in ("version", "generated_at")} == {
        "snow": {"powder_day_in": 6, "banner_day_in": 12},
        "density": {"dry_max_pct": 7, "moist_min_pct": 12},
        "display": {"priority": ["published", "reported", "estimated"]},
        "reports": {"aggregation_min": 3},
        "staleness": {"max_age_months": 18},
        "submissions": {"endpoint": "https://formspree.io/f/xyzbdqgq"},
    }


PAY_YAML = """
batch: fixture-pay
researched_date: 2026-10-01
sources:
  - {id: fixture-pay-sheet, url: https://example.org/pay, title: Fixture pay sheet, accessed_date: 2026-10-01}
records:
  - {resort_id: fixture-peak, season: 2025-26, cert_level: level_2, hourly_low: 20, hourly_high: 24,
     confidence: published, source_id: fixture-pay-sheet, verified_date: 2026-01-15}
  - {resort_id: fixture-peak, season: 2025-26, cert_level: level_1, hourly_low: 18, hourly_high: 19,
     confidence: reported, aggregate_count: 2, source_id: fixture-pay-sheet, verified_date: 2026-01-15}
"""
CAREER_YAML = """
batch: fixture-career
researched_date: 2026-10-01
resorts:
  - resort_id: fixture-basin
    private_split: {value: 40, source_id: fixture-source-b, as_of: 2026-10-01, confidence: published}
    professionalism_inputs:
      clinic_access: {value: 0.5, source_id: fixture-source-b, as_of: 2026-10-01, confidence: estimated}
    client_profile_tags: [race, adaptive]
"""


def test_career_layer_end_to_end(repo_root, raw_dir, out, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")  # 2026-09-21
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    (raw_dir / "career").mkdir()
    (raw_dir / "career" / "fixture.yaml").write_text(CAREER_YAML)
    (out / "climate").mkdir(parents=True)
    months = [{"month": m, "snowfall_in": v, "snowfall_source": "open-meteo"}
              for m, v in zip(range(1, 13), [50, 40, 30, 10, None, 0, 0, 0, 0, 5, 20, 45.5])]
    (out / "climate" / "fixture-peak.json").write_text(json.dumps({"months": months}))
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("aggregates 2 reports, fewer than 3" in w for w in result.warnings)

    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak = by["fixture-peak"]["career"]
    assert [p["cert_level"] for p in peak["pay"]] == ["level_2"]  # the 2 report aggregate is not displayed
    assert peak["pay"][0]["source_id"] == "fixture-pay-sheet" and peak["pay"][0]["stale"] is False
    assert "professionalism" not in peak  # owner decision 2026-10-13: never in data/build
    # owner decision 4: assumptions are set; no rent data in this fixture, so insufficient_data
    assert peak["lucrativeness"]["status"] == "insufficient_data" and peak["lucrativeness"]["mid"] is None
    assert "fixture-pay-sheet" in by["fixture-peak"]["sources"]
    basin = by["fixture-basin"]
    assert basin["career"]["private_split"]["value"] == 40 and basin["career"]["pay"] == []
    assert basin["clients"]["profile_tags"] == ["race", "adaptive"]
    assert by["fixture-valley"]["career"] is None

    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "resorts.geojson")["features"]}
    assert props["fixture-peak"]["snowfall_annual_in"] == 200.5
    assert "professionalism_score" not in props["fixture-peak"] and props["fixture-peak"]["lucrativeness_mid"] is None
    assert props["fixture-basin"]["snowfall_annual_in"] is None and props["fixture-basin"]["season_length_days"] is None

    report = (tmp_path / "tier-a-draft.md").read_text()
    peak_row = next(l for l in report.splitlines() if l.startswith("| Fixture Peak (`fixture-peak`)"))
    assert "private_rate" in peak_row and "client_profile_tags" in peak_row and ", pay," not in peak_row
    basin_row = next(l for l in report.splitlines() if l.startswith("| Fixture Basin (`fixture-basin`)"))
    assert "pay" in basin_row and "private_split" not in basin_row and "professionalism_inputs" not in basin_row


def test_career_validation_failures(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "career").mkdir()
    (raw_dir / "career" / "a.yaml").write_text(CAREER_YAML.replace("fixture-basin", "ghost-resort"))
    with pytest.raises(BuildError, match="resort_id 'ghost-resort' matches no resort"):
        _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "career" / "a.yaml").write_text(CAREER_YAML.replace("[race, adaptive]", "[race, jet_set]"))
    with pytest.raises(BuildError, match="unknown client_profiles tags"):
        _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "career" / "a.yaml").unlink()
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"][0]["fit"] = {"enthusiast_types": ["powder_hound"]}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(BuildError, match="fit.enthusiast_types tags \\['powder_hound'\\]"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_no_career_data_means_null_career(repo_root, raw_dir, out, tmp_path):
    _run(repo_root, raw_dir, out, tmp_path)
    assert all(r["career"] is None for r in _load(out, "resorts.json")["resorts"])
    for f in _load(out, "resorts.geojson")["features"]:
        p = f["properties"]
        assert "professionalism_score" not in p and p["lucrativeness_mid"] is None


HOUSING_YAML = """
batch: fixture-housing
researched_date: 2026-10-01
sources:
  - {id: fixture-housing-page, url: https://example.org/housing, title: Fixture housing page, accessed_date: 2026-10-01}
sites:
  - id: peak-staff-lodge
    resort_id: fixture-peak
    name: Staff Lodge
    type: resort_owned
    lat: 39.61
    lon: -106.31
    location_source_id: fixture-housing-page
    precision: building
    address: {value: "1 Fixture Way", source_id: fixture-housing-page, as_of: 2026-10-01, confidence: published}
    rent_monthly: {value: {low: 650, high: 900}, source_id: fixture-housing-page, as_of: 2026-10-01, confidence: published}
    occupancy: shared
    confidence: published
    verified_date: 2026-10-01
  - id: peak-town-flats
    resort_id: fixture-peak
    name: Town Flats
    type: market
    lat: 39.7
    lon: -106.2005
    location_source_id: fixture-housing-page
    precision: neighborhood
    confidence: estimated
    verified_date: 2026-10-01
"""


def _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg):
    import datetime as dt
    import shutil

    from pipeline.routing import RoutingClient
    from pipeline.schemas.common import Source
    from pipeline.transit import extract_gtfs

    (raw_dir / "openskimap" / "lifts.full.geojson").write_text(json.dumps(lifts_full))
    (raw_dir / "housing").mkdir()
    (raw_dir / "housing" / "fixture.yaml").write_text(HOUSING_YAML)
    shutil.copytree(counties_geojson.parent, raw_dir / "geo")
    (raw_dir / "rent" / "hud_fmr").mkdir(parents=True)
    (raw_dir / "rent" / "hud_fmr" / "08998.json").write_text(json.dumps({"fetched_date": "2026-10-02", "url": "u", "response": {
        "data": {"county_name": "Fixture County", "basicdata": {"Efficiency": 1478, "One-Bedroom": 1640, "Two-Bedroom": 2094, "year": "2026"}}}}))
    src = Source(id="gtfs-fixture", url="https://example.org/gtfs.zip", title="Fixture GTFS", accessed_date=dt.date(2026, 10, 1))
    ex = extract_gtfs(gtfs_writer(raw_dir / "transit" / "fixture" / "gtfs.zip", gtfs_tables), "fixture",
                      [(39.6, -106.3), (39.7, -106.2005)], 1.0, src)
    (raw_dir / "transit" / "fixture.json").write_text(json.dumps(ex))

    def http(method, url, body, headers, timeout):
        if url.endswith("/route"):
            a, b = body["locations"]
            return {"trip": {"summary": {"time": 900, "length": 6.2},
                             "legs": [{"shape": polyline_encoder([[a["lon"], a["lat"]], [b["lon"], b["lat"]]])}]}}
        p = body["locations"][0]
        ring = [[p["lon"] - 0.1, p["lat"] - 0.1], [p["lon"] + 0.1, p["lat"] - 0.1], [p["lon"], p["lat"] + 0.1],
                [p["lon"] - 0.1, p["lat"] - 0.1]]
        return {"features": [{"properties": {"contour": c["time"]}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
                             for c in body["contours"]]}
    client = RoutingClient(cfg.definitions.routing, cache_dir=raw_dir / "routing", offline=False, http=http)
    client.isochrones((39.6, -106.3))
    client.route((39.61, -106.31), (39.6, -106.3))  # only the staff lodge has a cached route


def test_phase5_end_to_end(repo_root, raw_dir, out, tmp_path, cfg, lifts_full, counties_geojson, gtfs_writer,
                           gtfs_tables, polyline_encoder, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert not any("no routing cache" in w or "no rent baseline" in w or "no transit" in w for w in result.warnings)
    assert (raw_dir / "openskimap" / "us_lifts_tier_a.geojson").exists()

    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak = by["fixture-peak"]
    assert peak["detail_available"] is True
    assert peak["housing_summary"] == {"site_count": 2, "cheapest_rent_monthly_low": 650,
                                       "cheapest_site_id": "peak-staff-lodge", "has_transit": True}
    assert by["fixture-basin"]["detail_available"] is True and by["fixture-basin"]["housing_summary"] is None  # lifts only
    assert by["fixture-indoor"]["detail_available"] is False and by["fixture-valley"]["detail_available"] is False
    assert sorted(p.name for p in (out / "detail").iterdir()) == ["fixture-basin.json", "fixture-peak.json"]

    d = _load(out / "detail", "fixture-peak.json")
    assert d["version"] == 1 and d["resort_id"] == "fixture-peak"
    assert [f["properties"]["name"] for f in d["lifts"]["features"]] == ["Fixture Express", None]
    assert d["rent_baseline"]["county_fips"] == "08998" and d["rent_baseline"]["fmr"]["studio"]["value"] == 1478
    lodge, flats = d["housing"]
    assert lodge["id"] == "peak-staff-lodge" and lodge["rent_monthly"]["confidence"] == "published"
    assert lodge["commutes"] == [{"mode": "drive", "minutes": 15.0, "distance_mi": 6.2, "transit_route_ids": [],
                                  "geometry": {"type": "LineString", "coordinates": [[-106.31, 39.61], [-106.3, 39.6]]},
                                  "source_id": "routing-valhalla"}]
    assert flats["rent_monthly"]["confidence"] == "estimated" and flats["rent_monthly"]["source_id"] == "hud-fmr-fy2026"
    assert flats["commutes"] == []  # no cached route; bus model settings are null in config
    assert d["transit"]["source_ids"] == ["gtfs-fixture"]
    # the operator from agency.txt and the schedule note flags (valid through the 2026 to 27 winter, still running)
    assert d["transit"]["feeds"] == [{"source_id": "gtfs-fixture", "agency": "fixture", "as_of": "2026-01-01",
                                      "valid_to": "2027-12-31", "agencies": [{"name": "Fixture Transit", "url": None}],
                                      "covers_winter": True, "ended": False}]
    assert {r["agency_name"] for r in d["transit"]["routes"]} == {"Fixture Transit"}
    assert [i["minutes"] for i in d["isochrones"]] == [15, 30, 45]

    sources = {s["id"] for s in _load(out, "sources.json")["sources"]}
    assert {"openskimap-lifts", "hud-fmr-fy2026", "gtfs-fixture", "routing-valhalla", "fixture-housing-page"} <= sources


def test_phase5_drops_transit_extracts_that_are_not_current(repo_root, raw_dir, out, tmp_path, cfg, lifts_full,
                                                            counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder,
                                                            monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")  # 2026-09-21
    gtfs_tables["calendar.txt"][1][-2:] = ["20230123", "20230415"]  # a feed from the 2022 to 23 season
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("transit extract 'fixture' is not current" in w and "older than 18 months" in w for w in result.warnings)
    assert not any("no transit extracts" in w for w in result.warnings)
    d = _load(out / "detail", "fixture-peak.json")
    assert d["transit"] is None
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["housing_summary"]["has_transit"] is False


def test_phase5_rent_reaches_lucrativeness(repo_root, raw_dir, out, tmp_path, cfg, lifts_full, counties_geojson,
                                           gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    echo = {a["key"]: a["value"] for a in by["fixture-peak"]["career"]["lucrativeness"]["assumptions"]}
    # mean of the employee housing rent mids: the staff lodge (650 to 900) is the only employee site
    assert (echo["rent_monthly_usd"], echo["rent_basis"], echo["rent_source_id"]) == \
        (775.0, "employee_housing_mean:peak-staff-lodge", "fixture-housing-page")
    lucr = by["fixture-peak"]["career"]["lucrativeness"]
    assert lucr["status"] == "computed" and lucr["mid"] == round(24 * 40 * 5 * 4.33 - 775 * 5)
    assert lucr["low"] == round(20 * 40 * 5 * 4.33 - 775 * 5)


def test_phase5_baseline_rent_when_no_employee_housing(repo_root, raw_dir, out, tmp_path, cfg, lifts_full,
                                                       counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    (raw_dir / "housing" / "fixture.yaml").write_text(HOUSING_YAML.replace("type: resort_owned", "type: market"))
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    echo = {a["key"]: a["value"] for a in peak["career"]["lucrativeness"]["assumptions"]}
    # no employee housing and no ACS value in this fixture: HUD FMR one bedroom
    assert (echo["rent_monthly_usd"], echo["rent_basis"], echo["rent_source_id"]) == (1640.0, "fmr_one_br", "hud-fmr-fy2026")


def test_phase5_housing_validation(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "housing").mkdir()
    (raw_dir / "housing" / "a.yaml").write_text(HOUSING_YAML.replace("    address: {value: \"1 Fixture Way\", source_id: fixture-housing-page, as_of: 2026-10-01, confidence: published}\n", ""))
    with pytest.raises(BuildError, match="site 'peak-staff-lodge': .*publicly listed address"):
        _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "housing" / "a.yaml").write_text(HOUSING_YAML.replace("resort_id: fixture-peak", "resort_id: ghost", 1))
    with pytest.raises(BuildError, match="resort_id 'ghost' matches no resort"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_phase5_skips_cleanly(repo_root, raw_dir, out, tmp_path):
    result = _run(repo_root, raw_dir, out, tmp_path)
    w = "\n".join(result.warnings)
    assert "no lifts extract" in w and "no rent baseline cache" in w and "no transit extracts" in w
    assert not (out / "detail").exists() or not list((out / "detail").iterdir())
    assert all(r["detail_available"] is False and r["housing_summary"] is None
               for r in _load(out, "resorts.json")["resorts"])


def test_stale_detail_files_removed(repo_root, raw_dir, out, tmp_path):
    (out / "detail").mkdir(parents=True)
    (out / "detail" / "gone-resort.json").write_text("{}")
    _run(repo_root, raw_dir, out, tmp_path)
    assert not (out / "detail" / "gone-resort.json").exists()


EDITORIAL_FIXTURE = """
batch: editorial-fixture
researched_date: 2026-10-12
sources:
  - {id: ed-fixture-a, url: https://example.org/ed-a, title: Fixture editorial source A, accessed_date: 2026-10-12}
entries:
  - {target: cluster, id: i70-corridor, overview_md: "Reviewed cluster text.", alexandre_take_md: "My take.",
     status: reviewed, source_ids: [ed-fixture-a]}
  - {target: cluster, id: wasatch, overview_md: "Draft cluster text.", status: draft, source_ids: [ed-fixture-a]}
  - {target: resort, id: fixture-peak, overview_md: "Reviewed resort text.", culture_md: "Culture text.",
     status: reviewed, reviewed_by: Alexandre, reviewed_date: 2026-10-12, source_ids: [ed-fixture-a]}
  - {target: resort, id: fixture-basin, overview_md: "Draft resort text, full-time.", status: draft, source_ids: [ed-fixture-a]}
"""


def test_editorial_end_to_end(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "editorial").mkdir()
    (raw_dir / "editorial" / "fixture.yaml").write_text(EDITORIAL_FIXTURE)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    assert by["fixture-peak"]["editorial"] == {"overview_md": "Reviewed resort text.", "culture_md": "Culture text.",
                                               "alexandre_take_md": None, "status": "reviewed",
                                               "source_ids": ["ed-fixture-a"], "reviewed_date": "2026-10-12"}
    assert "ed-fixture-a" in by["fixture-peak"]["sources"]
    assert by["fixture-basin"]["editorial"] is None  # draft never reaches data/build
    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "clusters.geojson")["features"]}
    assert props["i70-corridor"]["overview_md"] == "Reviewed cluster text."
    assert props["i70-corridor"]["alexandre_take_md"] == "My take." and "ed-fixture-a" in props["i70-corridor"]["sources"]
    assert props["wasatch"]["overview_md"] is None
    build_text = "".join(p.read_text() for p in out.rglob("*.json")) + "".join(p.read_text() for p in out.rglob("*.geojson"))
    assert "Draft resort text" not in build_text and "Draft cluster text" not in build_text
    review = (tmp_path / "editorial-review.md").read_text()
    assert "Draft resort text, full-time." in review and "Draft cluster text." in review
    assert "Reviewed resort text" not in review and "hyphenated word 'full-time'" in review
    assert "1 of 2 Tier A clusters have a reviewed overview." in review  # i70-corridor, wasatch; other-mi excluded
    assert "Other Michigan" not in review.split("## Drafts")[0]


def test_editorial_em_dash_fails_build(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "editorial").mkdir()
    (raw_dir / "editorial" / "fixture.yaml").write_text(EDITORIAL_FIXTURE.replace("Draft cluster text.", "Draft — text."))
    with pytest.raises(BuildError, match="cluster 'wasatch': .*em or en dash"):
        _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "editorial" / "fixture.yaml").write_text(EDITORIAL_FIXTURE.replace("id: wasatch", "id: atlantis"))
    with pytest.raises(BuildError, match="cluster 'atlantis': no such cluster"):
        _run(repo_root, raw_dir, out, tmp_path)


def _reports(raw_dir, rows):
    from pipeline.reports import ReportStore
    from pipeline.schemas import Report

    store = ReportStore(raw_dir / "reports")
    for i, r in enumerate(rows):
        base = {"id": f"r-{i}", "resort_id": "fixture-peak", "season": "2025-26", "submitted_at": "2026-10-01T10:00:00Z",
                "status": "approved", "moderated_at": f"2026-10-0{1 + i % 9}T12:00:00Z"}
        store.write(Report.model_validate({**base, **r}))


def test_reports_end_to_end(repo_root, raw_dir, out, tmp_path, cfg, lifts_full, counties_geojson, gtfs_writer,
                            gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    pay = lambda v, lvl="level_3": {"kind": "pay", "cert_level": lvl, "values": {"hourly_usd": v}}
    housing = lambda v, site: {"kind": "housing", "housing_site_id": site, "values": {"monthly_usd": v}}
    rent = lambda v: {"kind": "rent", "values": {"monthly_usd": v}}
    _reports(raw_dir, [pay(26), pay(28), pay(27),                       # 3 approved: a reported range
                       pay(30, "trainer"), pay(31, "trainer"),          # 2 approved: nothing
                       {**pay(99, "trainer"), "status": "rejected", "moderation_note": "no proof"},
                       housing(800, "peak-town-flats"), housing(950, "peak-town-flats"), housing(870, "peak-town-flats"),
                       rent(1000), rent(1200), rent(1100)])
    from pipeline.reports import ReportStore
    from pipeline.schemas import Report
    ReportStore(raw_dir / "reports").write(Report.model_validate(
        {"id": "r-pending", "resort_id": "fixture-peak", "kind": "pay", "season": "2025-26", "cert_level": "trainer",
         "values": {"hourly_usd": 32}, "submitted_at": "2026-10-01T10:00:00Z"}))
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("1 reports wait for moderation" in w for w in result.warnings)
    assert not (raw_dir / "reports" / "aggregates.json").exists()  # only `reports aggregate` writes it
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    rows = {p["cert_level"]: p for p in peak["career"]["pay"]}
    assert rows["level_3"] == {"cert_level": "level_3", "hourly_low": 26, "hourly_high": 28, "confidence": "reported",
                               "source_id": "instructorgrindset-reports", "as_of": "2026-10-03", "season": "2025-26",
                               "stale": False}
    assert "trainer" not in rows  # 2 approved + 1 rejected + 1 pending: nothing new
    d = _load(out / "detail", "fixture-peak.json")
    flats = next(h for h in d["housing"] if h["id"] == "peak-town-flats")
    assert flats["rent_monthly"]["confidence"] == "reported" and flats["rent_monthly"]["value"] == {"low": 800, "high": 950}
    lodge = next(h for h in d["housing"] if h["id"] == "peak-staff-lodge")
    assert lodge["rent_monthly"]["confidence"] == "published"  # Published beats Reported
    assert d["rent_reported"]["value"] == {"low": 1000, "high": 1200} and "3 approved reports" in d["rent_reported"]["note"]
    assert peak["housing_summary"]["cheapest_rent_monthly_low"] == 650
    build_text = "".join(p.read_text() for p in out.rglob("*.json"))
    assert "r-0" not in build_text and "r-pending" not in build_text and "no proof" not in build_text
    assert '"hourly_usd"' not in build_text and "99" not in json.dumps(peak["career"])


def test_reports_area_rent_not_in_default_rent_order(repo_root, raw_dir, out, tmp_path):
    """Owner decision 4 lists employee housing, ACS median, HUD FMR one bedroom; the area report rent
    is shown in detail files but is not a lucrativeness rent by default."""
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _reports(raw_dir, [{"kind": "rent", "values": {"monthly_usd": v}} for v in (1000, 1200, 1100)])
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    echo = {a["key"]: a["value"] for a in peak["career"]["lucrativeness"]["assumptions"]}
    assert (echo["rent_monthly_usd"], echo["rent_basis"]) == (None, None)
    assert peak["career"]["lucrativeness"]["status"] == "insufficient_data"


def _aggregate_cli(raw_dir, monkeypatch, *args):
    import pipeline.reports as rep
    monkeypatch.setattr(rep.ReportStore, "__init__", lambda self, root=raw_dir / "reports": setattr(self, "root", root))
    return rep.main(["aggregate", *args])


def test_committed_aggregates_used_without_raw_reports(repo_root, raw_dir, out, tmp_path, monkeypatch):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": v}} for v in (26, 27, 28)])
    assert _aggregate_cli(raw_dir, monkeypatch) == 0
    import shutil
    shutil.rmtree(raw_dir / "reports" / "moderated")  # a fresh clone has only the committed aggregates
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["career"]["pay"][0]["confidence"] == "reported"


def test_fresh_clone_with_one_report_keeps_committed_aggregates(repo_root, raw_dir, out, tmp_path, monkeypatch):
    """Regression: a local subset of moderated reports must never wipe committed aggregates."""
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": v}} for v in (26, 27, 28, 29, 30)])
    assert _aggregate_cli(raw_dir, monkeypatch) == 0
    committed = (raw_dir / "reports" / "aggregates.json").read_text()
    import shutil
    shutil.rmtree(raw_dir / "reports" / "moderated")
    # a fresh clone: one new approved report in the same group, plus a new group of 3
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": 31}}] +
             [{"kind": "rent", "values": {"monthly_usd": v}} for v in (1000, 1100, 1200)])
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert (raw_dir / "reports" / "aggregates.json").read_text() == committed  # the build never writes it
    assert any("run `python -m pipeline.reports aggregate`" in w for w in result.warnings)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    l3 = next(p for p in peak["career"]["pay"] if p["cert_level"] == "level_3")
    assert (l3["hourly_low"], l3["hourly_high"]) == (26, 30)  # committed group of 5 kept, not the local 1
    assert _aggregate_cli(raw_dir, monkeypatch) == 0           # merge keeps both groups
    data = json.loads((raw_dir / "reports" / "aggregates.json").read_text())
    assert [p["aggregate_count"] for p in data["pay"]] == [5] and len(data["rent"]) == 1
    assert _aggregate_cli(raw_dir, monkeypatch, "--replace") == 0   # explicit full rebuild from this checkout
    data = json.loads((raw_dir / "reports" / "aggregates.json").read_text())
    assert data["pay"] == [] and len(data["rent"]) == 1


def test_aggregate_cli_skips_write_on_moderation_errors(raw_dir, monkeypatch):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": v}} for v in (26, 27, 28)])
    (raw_dir / "reports" / "moderated" / "broken.yaml").write_text("id: broken\nkind: pay\n")
    assert _aggregate_cli(raw_dir, monkeypatch) == 1
    assert not (raw_dir / "reports" / "aggregates.json").exists()


def test_rejected_report_for_unknown_resort_does_not_fail(repo_root, raw_dir, out, tmp_path):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": 26}, "resort_id": "ghost",
                        "status": "rejected", "moderation_note": "unknown resort"}])
    _run(repo_root, raw_dir, out, tmp_path)  # only approved reports are checked against the build


def test_housing_report_for_another_resorts_site_fails(repo_root, raw_dir, out, tmp_path, cfg, lifts_full,
                                                       counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    _reports(raw_dir, [{"kind": "housing", "housing_site_id": "peak-town-flats", "resort_id": "fixture-basin",
                        "values": {"monthly_usd": v}} for v in (800, 900, 1000)])
    with pytest.raises(BuildError, match="'peak-town-flats' is not a housing site of 'fixture-basin'"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_housing_aggregate_attaches_by_resort_and_site(repo_root, raw_dir, out, tmp_path, cfg, lifts_full,
                                                       counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    agg = {"version": 1, "generated_at": "x",
           "source": {"id": "instructorgrindset-reports", "url": "https://instructorgrindset.com/map/", "title": "t",
                      "accessed_date": "2026-10-03"},
           "housing": [{"resort_id": "fixture-basin", "season": "2025-26", "housing_site_id": "peak-town-flats",
                        "low": 1, "high": 2, "count": 3, "verified_date": "2026-10-03"}]}
    (raw_dir / "reports").mkdir()
    (raw_dir / "reports" / "aggregates.json").write_text(json.dumps(agg))
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("matches no housing site of that resort" in w for w in result.warnings)
    flats = next(h for h in _load(out / "detail", "fixture-peak.json")["housing"] if h["id"] == "peak-town-flats")
    assert flats["rent_monthly"]["confidence"] == "estimated"  # the other resort's aggregate is not attached


def test_small_aggregates_are_dropped_on_load(repo_root, raw_dir, out, tmp_path):
    agg = {"version": 1, "generated_at": "x",
           "source": {"id": "instructorgrindset-reports", "url": "https://instructorgrindset.com/map/", "title": "t",
                      "accessed_date": "2026-10-03"},
           "rent": [{"resort_id": "fixture-peak", "season": "2025-26", "low": 900, "high": 900, "count": 1,
                     "verified_date": "2026-10-03"}],
           "pay": [{"resort_id": "fixture-peak", "season": "2025-26", "cert_level": "level_3", "hourly_low": 25,
                    "hourly_high": 26, "confidence": "reported", "source_id": "instructorgrindset-reports",
                    "verified_date": "2026-10-03", "aggregate_count": 2}]}
    (raw_dir / "reports").mkdir()
    (raw_dir / "reports" / "aggregates.json").write_text(json.dumps(agg))
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("2 report aggregates have fewer than 3 reports" in w for w in result.warnings)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["career"] is None


def test_source_conflict_is_a_build_error(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "transit").mkdir()
    (raw_dir / "transit" / "x.json").write_text(json.dumps({"version": 1, "agency": "x", "search_radius_km": 1,
        "source": {"id": "fixture-source-a", "url": "https://example.org/other", "title": "t", "accessed_date": "2026-10-01"},
        "routes": [], "stops": [], "patterns": []}))
    with pytest.raises(BuildError, match="source id 'fixture-source-a' has url"):
        _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "transit" / "x.json").write_text(json.dumps({"agency": "x", "source": {"id": "Bad Id"}}))
    with pytest.raises(BuildError, match="invalid source"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_older_full_download_does_not_overwrite_newer_extract(repo_root, raw_dir, out, tmp_path):
    import os
    extract = raw_dir / "openskimap" / "us_ski_areas.geojson"
    data = json.loads(extract.read_text())
    data["metadata"]["fetched_date"] = "2026-10-05"   # committed extract is newer than the local download
    data["features"] = data["features"][:1]
    extract.write_text(json.dumps(data))
    _run(repo_root, raw_dir, out, tmp_path)
    assert json.loads(extract.read_text())["metadata"]["fetched_date"] == "2026-10-05"
    full = raw_dir / "openskimap" / "ski_areas.full.geojson"
    ts = __import__("datetime").datetime(2026, 10, 9, 12).timestamp()
    os.utime(full, (ts, ts))                           # a newer download is re filtered
    _run(repo_root, raw_dir, out, tmp_path)
    assert json.loads(extract.read_text())["metadata"]["fetched_date"] == "2026-10-09"


def test_invalid_moderated_report_fails_build(repo_root, raw_dir, out, tmp_path):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": 26}, "resort_id": "ghost"}])
    with pytest.raises(BuildError, match="resort_id 'ghost' matches no resort"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_staleness_report_written(repo_root, raw_dir, out, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    _run(repo_root, raw_dir, out, tmp_path)
    assert "0 stale values" in (tmp_path / "staleness.md").read_text()


def test_trailmap_end_to_end(repo_root, raw_dir, out, tmp_path, lifts_full):
    from pipeline import runs as runs_mod
    line = [[-106.30, 39.60, 2500.0], [-106.29, 39.61, 2400.0]]
    feats = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": line},
              "properties": {"id": f"r{i}", "name": n, "type": "run", "uses": ["downhill"], "difficulty": d,
                             "difficultyConvention": "north_america", "status": "operating", "grooming": None,
                             "lit": None, "skiAreas": [{"properties": {"id": a}}]}}
             for i, (n, d, a) in enumerate([("Peak Blue", "intermediate", "osm-peak"), ("Peak Green", "easy", "osm-peak"),
                                            ("Basin Black", "advanced", "osm-basin")])]
    full = raw_dir / "openskimap" / runs_mod.FULL_NAME
    full.write_text('{"type": "FeatureCollection", "features":[\n' +
                    ",\n".join(json.dumps(f, separators=(",", ":")) for f in feats) + "\n]}\n")
    (raw_dir / "openskimap" / "lifts.full.geojson").write_text(json.dumps(lifts_full))
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    assert by["fixture-peak"]["trailmap_available"] and by["fixture-basin"]["trailmap_available"]
    assert not by["fixture-indoor"]["trailmap_available"] and not by["fixture-valley"]["trailmap_available"]
    tm = _load(out / "trailmap", "fixture-peak.json")
    assert [f["properties"]["difficulty_us"] for f in tm["runs"]["features"]] == ["blue", "green"]
    assert len(tm["lifts"]["features"]) == 2 and tm["resort_id"] == "fixture-peak"
    assert "openskimap-runs" in {s["id"] for s in _load(out, "sources.json")["sources"]}
    assert (raw_dir / "openskimap" / runs_mod.EXTRACT_NAME).exists()


def test_published_snowfall_preferred_over_climate(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"][0]["snow"] = {"annual_avg_in": {"value": 300, "source_id": "fixture-source-a", "as_of": "2026-10-01",
                                                    "confidence": "published", "note": "resort reported average"}}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    (out / "climate").mkdir(parents=True)
    months = [{"month": m, "snowfall_in": v, "snowfall_source": src} for m, v, src in
              [(1, 50, "open-meteo"), (2, 40, "open-meteo"), (3, 30, None), (4, 10, "open-meteo"), (5, None, "open-meteo")]]
    for rid in ("fixture-peak", "fixture-basin"):
        (out / "climate" / f"{rid}.json").write_text(json.dumps({"months": months}))
    (out / "climate" / "fixture-indoor.json").write_text(json.dumps({"months": [{"month": 1, "snowfall_in": 5, "snowfall_source": None}]}))
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    assert by["fixture-peak"]["snow"]["annual_avg_in"]["value"] == 300
    assert by["fixture-basin"]["snow"] is None and by["fixture-valley"]["snow"] is None
    assert "fixture-source-a" in by["fixture-peak"]["sources"]
    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "resorts.geojson")["features"]}
    assert (props["fixture-peak"]["snowfall_annual_in"], props["fixture-peak"]["snowfall_annual_source"]) == (300, "published")
    # climate path: only months with a snowfall_source count (50 + 40 + 10)
    assert (props["fixture-basin"]["snowfall_annual_in"], props["fixture-basin"]["snowfall_annual_source"]) == (100, "climate")
    assert (props["fixture-indoor"]["snowfall_annual_in"], props["fixture-indoor"]["snowfall_annual_source"]) == (None, None)
    assert (props["fixture-valley"]["snowfall_annual_in"], props["fixture-valley"]["snowfall_annual_source"]) == (None, None)


def test_snow_schema_rules():
    from pydantic import ValidationError
    from pipeline.schemas import Snow
    ok = {"value": 300, "source_id": "a", "as_of": "2026-10-01", "confidence": "published"}
    assert Snow.model_validate({"annual_avg_in": ok}).annual_avg_in.value == 300
    with pytest.raises(ValidationError, match="positive"):
        Snow.model_validate({"annual_avg_in": {**ok, "value": 0}})
    with pytest.raises(ValidationError):
        Snow.model_validate({"annual_avg_in": {**ok, "value": "lots"}})
    with pytest.raises(ValidationError, match="Extra inputs"):
        Snow.model_validate({"annual_max_in": ok})
    with pytest.raises(ValidationError, match="not defined"):
        Snow.model_validate({"annual_avg_in": ok}, context={"source_ids": {"b"}})


FACTS_YAML = """
batch: facts-fixture
researched_date: 2026-10-13
sources:
  - {id: facts-fixture-a, url: https://example.org/facts, title: Fixture facts, accessed_date: 2026-10-13}
records:
  - resort_id: fixture-peak
    open_typical: {value: "11-25", source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated}
    close_typical: {value: "04-12", source_id: facts-fixture-a, as_of: 2026-10-13, confidence: published}
    length_days: {value: 152, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated, note: computed from typical dates}
    acres: {value: 3500, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: published}
    lifts: {value: 12, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: published}
    annual_snowfall_in: {value: 250, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated}
  - resort_id: fixture-valley
    length_days: {value: 120, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated}
    vertical_ft: {value: 900, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated}
    lifts: {value: 4, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: published}
"""


def test_facts_overlay_merge_priority(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "facts").mkdir()
    (raw_dir / "facts" / "fixture.yaml").write_text(FACTS_YAML)
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak, valley = by["fixture-peak"], by["fixture-valley"]
    # open_typical: Tier A YAML has 11-20 published; the estimated facts value loses
    assert peak["season"]["open_typical"]["value"] == "11-20"
    assert peak["season"]["close_typical"]["value"] == "04-12" and peak["season"]["length_days"]["value"] == 152
    # acres: Tier A YAML 4000 published vs facts 3500 published: tie, the existing value wins
    assert peak["size"]["acres"]["value"] == 4000
    # lifts: OpenSkiMap estimated 11 loses to the published facts value
    assert peak["size"]["lifts"] == {"value": 12, "source_id": "facts-fixture-a", "as_of": "2026-10-13",
                                     "confidence": "published", "note": None}
    assert peak["snow"]["annual_avg_in"]["value"] == 250 and "facts-fixture-a" in peak["sources"]
    # Tier B: OpenSkiMap vertical (estimated) ties the estimated facts value, so it stays;
    # the published facts lift count beats the OpenSkiMap estimate
    assert valley["size"]["vertical_ft"]["value"] == 984 and valley["size"]["vertical_ft"]["source_id"] == "openskimap-ski-areas"
    assert valley["size"]["lifts"]["value"] == 4
    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "resorts.geojson")["features"]}
    assert props["fixture-peak"]["season_length_days"] == 152 and props["fixture-valley"]["season_length_days"] == 120
    assert (props["fixture-peak"]["snowfall_annual_in"], props["fixture-peak"]["snowfall_annual_source"]) == (250, "published")
    echo = {a["key"]: a for a in peak["career"]["lucrativeness"]["assumptions"]}
    assert echo["season_months"]["value"] == round(152 / 30.4, 2) and echo["season_months_basis"]["value"] == "season_length"


def test_facts_validation(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "facts").mkdir()
    bad = [
        (FACTS_YAML.replace('"11-25"', '"2026-11-25"'), "MM-DD"),
        (FACTS_YAML.replace("value: 3500", "value: -1"), "positive"),
        (FACTS_YAML.replace("value: 152", "value: 400"), "366"),
        (FACTS_YAML.replace("resort_id: fixture-valley", "resort_id: ghost"), "resort 'ghost': no such resort"),
        (FACTS_YAML.replace("source_id: facts-fixture-a, as_of: 2026-10-13, confidence: published}\n    lifts",
                            "source_id: nowhere, as_of: 2026-10-13, confidence: published}\n    lifts"), "not defined"),
        (FACTS_YAML.replace("resort_id: fixture-valley", "resort_id: fixture-peak"), "duplicate resort_id"),
        (FACTS_YAML + "  - resort_id: fixture-basin\n", "at least one field"),
        (FACTS_YAML.replace("lifts: {value: 4,", "chairs: {value: 4,"), "Extra inputs"),
    ]
    for text, msg in bad:
        (raw_dir / "facts" / "fixture.yaml").write_text(text)
        with pytest.raises(BuildError, match=msg):
            _run(repo_root, raw_dir, out, tmp_path)
    (raw_dir / "facts" / "fixture.yaml").write_text(FACTS_YAML)
    (raw_dir / "facts" / "second.yaml").write_text(
        "batch: facts-two\nresearched_date: 2026-10-13\nrecords:\n  - resort_id: fixture-peak\n"
        "    lifts: {value: 3, source_id: facts-fixture-a, as_of: 2026-10-13, confidence: estimated}\n")
    with pytest.raises(BuildError, match="also has a facts record in"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_merge_facts_unit():
    from pipeline.facts import merge_facts
    from pipeline.schemas.facts import FactsRecord
    sv = lambda v, c: {"value": v, "source_id": "s", "as_of": "2026-10-13", "confidence": c}
    rec = FactsRecord.model_validate({"resort_id": "r", "length_days": sv(100, "reported"), "acres": sv(50, "estimated"),
                                      "training_start": sv("11-01", "estimated")})
    d = {"season": {"length_days": sv(90, "estimated")}, "size": {"acres": sv(60, "estimated")}, "snow": None}
    assert merge_facts(d, rec, ["published", "reported", "estimated"]) == ["length_days", "training_start"]
    assert d["season"]["length_days"]["value"] == 100 and d["size"]["acres"]["value"] == 60
    assert d["season"]["training_start"]["value"] == "11-01" and d["snow"] is None


def test_operator_scale_and_access(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    data = yaml.safe_load(p.read_text())
    data["resorts"][0]["operator_scale"] = {"value": 3, "source_id": "fixture-source-a", "as_of": "2026-10-13",
                                            "confidence": "published", "note": "internal reasoning"}
    data["resorts"][0]["access"] = {"rating": "transit_friendly", "summary_md": "Free buses run from town to the base.",
                                    "source_id": "fixture-source-b", "as_of": "2026-10-13", "confidence": "estimated"}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak = by["fixture-peak"]
    # Owner decision 2026-10-13: the operator scale is validated in data/raw but never written to data/build.
    assert all("operator_scale" not in r for r in by.values())
    assert "internal reasoning" not in (out / "resorts.json").read_text()
    assert peak["access"]["rating"] == "transit_friendly" and "fixture-source-b" in peak["sources"]
    assert by["fixture-valley"]["access"] is None
    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "resorts.geojson")["features"]}
    assert props["fixture-peak"]["access_rating"] == "transit_friendly" and props["fixture-basin"]["access_rating"] is None
    assert all("professionalism_score" not in p for p in props.values())
    leaks = [f.name for f in out.rglob("*") if f.is_file() and f.suffix in (".json", ".geojson")
             and any(k in f.read_text(encoding="utf-8") for k in ("operator_scale", "professionalism"))]
    assert leaks == []
    # An invalid raw scale still fails the build: the loader validates it.
    data["resorts"][0]["operator_scale"]["value"] = 4
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(BuildError):
        _run(repo_root, raw_dir, out, tmp_path)


def test_operator_scale_and_access_validation():
    from pydantic import ValidationError
    from pipeline.schemas import Access, SourcedOperatorScale
    base = {"source_id": "a", "as_of": "2026-10-13", "confidence": "published"}
    for v in (1, 2, 3):
        assert SourcedOperatorScale.model_validate({**base, "value": v}).value == v
    for v in (0, 4, 2.5, "3"):
        with pytest.raises(ValidationError):
            SourcedOperatorScale.model_validate({**base, "value": v})
    ok = {**base, "rating": "car_centric", "summary_md": "Most staff drive."}
    assert Access.model_validate(ok).rating == "car_centric"
    with pytest.raises(ValidationError):
        Access.model_validate({**ok, "rating": "walkable"})
    with pytest.raises(ValidationError, match="dashes"):
        Access.model_validate({**ok, "summary_md": "Drive — always."})
    with pytest.raises(ValidationError, match="not defined"):
        Access.model_validate(ok, context={"source_ids": {"b"}})
    with pytest.raises(ValidationError):
        Access.model_validate({**ok, "summary_md": ""})


def test_config_owner_decisions(cfg):
    d = cfg.definitions
    assert d.operator_scale.values == {1: "family_owned", 2: "independent_business", 3: "large_corporate"}
    assert d.access.ratings == ["convenient", "transit_friendly", "mixed", "car_centric"]
    assert d.submissions.endpoint == "https://formspree.io/f/xyzbdqgq"
    assert d.rent.lucrativeness_rent == ["employee_housing", "acs_median", "fmr_one_br"]
