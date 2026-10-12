import json
import shutil
import subprocess
import sys

import pytest
import yaml

from pipeline.build import build
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
                                     "professionalism_score": None, "lucrativeness_mid": None}
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
    from pipeline import openskimap

    def boom(*a, **k):
        raise OSError("network blocked")
    monkeypatch.setattr(openskimap, "fetch", boom)
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
    assert {k: v for k, v in d.items() if k not in ("version", "generated_at")} == {
        "snow": {"powder_day_in": 6, "banner_day_in": 12},
        "density": {"dry_max_pct": 7, "moist_min_pct": 12},
        "display": {"priority": ["published", "reported", "estimated"]},
        "reports": {"aggregation_min": 3},
        "staleness": {"max_age_months": 18},
        "submissions": {"endpoint": None},
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
    months = [{"month": m, "snowfall_in": v} for m, v in zip(range(1, 13), [50, 40, 30, 10, None, 0, 0, 0, 0, 5, 20, 45.5])]
    (out / "climate" / "fixture-peak.json").write_text(json.dumps({"months": months}))
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert any("aggregates 2 reports, fewer than 3" in w for w in result.warnings)

    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    peak = by["fixture-peak"]["career"]
    assert [p["cert_level"] for p in peak["pay"]] == ["level_2"]  # the 2 report aggregate is not displayed
    assert peak["pay"][0]["source_id"] == "fixture-pay-sheet" and peak["pay"][0]["stale"] is False
    assert peak["professionalism"]["status"] == "weights_unset"
    assert peak["lucrativeness"]["status"] == "assumptions_unset" and peak["lucrativeness"]["mid"] is None
    assert "fixture-pay-sheet" in by["fixture-peak"]["sources"]
    basin = by["fixture-basin"]
    assert basin["career"]["private_split"]["value"] == 40 and basin["career"]["pay"] == []
    assert basin["clients"]["profile_tags"] == ["race", "adaptive"]
    assert by["fixture-valley"]["career"] is None

    props = {f["properties"]["id"]: f["properties"] for f in _load(out, "resorts.geojson")["features"]}
    assert props["fixture-peak"]["snowfall_annual_in"] == 200.5
    assert props["fixture-peak"]["professionalism_score"] is None and props["fixture-peak"]["lucrativeness_mid"] is None
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
        assert p["professionalism_score"] is None and p["lucrativeness_mid"] is None


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
    assert [i["minutes"] for i in d["isochrones"]] == [15, 30, 45]

    sources = {s["id"] for s in _load(out, "sources.json")["sources"]}
    assert {"openskimap-lifts", "hud-fmr-fy2026", "gtfs-fixture", "routing-valhalla", "fixture-housing-page"} <= sources


def test_phase5_rent_reaches_lucrativeness(repo_root, raw_dir, out, tmp_path, cfg, lifts_full, counties_geojson,
                                           gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    by = {r["id"]: r for r in _load(out, "resorts.json")["resorts"]}
    echo = {a["key"]: a["value"] for a in by["fixture-peak"]["career"]["lucrativeness"]["assumptions"]}
    assert (echo["rent_monthly_usd"], echo["rent_basis"], echo["rent_source_id"]) == \
        (650, "employee_housing:peak-staff-lodge", "fixture-housing-page")


def test_phase5_baseline_rent_when_no_employee_housing(repo_root, raw_dir, out, tmp_path, cfg, lifts_full,
                                                       counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder):
    _phase5_inputs(raw_dir, lifts_full, counties_geojson, gtfs_writer, gtfs_tables, polyline_encoder, cfg)
    (raw_dir / "housing" / "fixture.yaml").write_text(HOUSING_YAML.replace("type: resort_owned", "type: market"))
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    echo = {a["key"]: a["value"] for a in peak["career"]["lucrativeness"]["assumptions"]}
    assert (echo["rent_monthly_usd"], echo["rent_basis"], echo["rent_source_id"]) == (1478.0, "fmr_studio", "hud-fmr-fy2026")


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

    agg = json.loads((raw_dir / "reports" / "aggregates.json").read_text())
    assert [(p["cert_level"], p["aggregate_count"]) for p in agg["pay"]] == [("level_3", 3)]
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


def test_reports_area_rent_feeds_lucrativeness(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY_YAML)
    _reports(raw_dir, [{"kind": "rent", "values": {"monthly_usd": v}} for v in (1000, 1200, 1100)])
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    echo = {a["key"]: a["value"] for a in peak["career"]["lucrativeness"]["assumptions"]}
    assert (echo["rent_monthly_usd"], echo["rent_basis"], echo["rent_source_id"]) == \
        (1000.0, "reported_area", "instructorgrindset-reports")


def test_committed_aggregates_used_without_raw_reports(repo_root, raw_dir, out, tmp_path):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": v}} for v in (26, 27, 28)])
    _run(repo_root, raw_dir, out, tmp_path)
    import shutil
    shutil.rmtree(raw_dir / "reports" / "moderated")  # a fresh clone has only the committed aggregates
    _run(repo_root, raw_dir, out, tmp_path)
    peak = next(r for r in _load(out, "resorts.json")["resorts"] if r["id"] == "fixture-peak")
    assert peak["career"]["pay"][0]["confidence"] == "reported"


def test_invalid_moderated_report_fails_build(repo_root, raw_dir, out, tmp_path):
    _reports(raw_dir, [{"kind": "pay", "cert_level": "level_3", "values": {"hourly_usd": 26}, "resort_id": "ghost"}])
    with pytest.raises(BuildError, match="resort_id 'ghost' matches no resort"):
        _run(repo_root, raw_dir, out, tmp_path)


def test_staleness_report_written(repo_root, raw_dir, out, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    _run(repo_root, raw_dir, out, tmp_path)
    assert "0 stale values" in (tmp_path / "staleness.md").read_text()
