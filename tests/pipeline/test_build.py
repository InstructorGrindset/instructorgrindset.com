import json
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


def _run(repo_root, raw_dir, out, tmp_path, **kw):
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
    assert set(props["wasatch"]) == {"id", "name", "states", "resort_ids", "tier_a_count", "overview_md", "sources"}
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
                                     "size_rank": 4}
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
    }
