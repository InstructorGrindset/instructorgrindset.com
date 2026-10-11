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
    assert ids == ["faraway-hill", "fixture-basin", "fixture-peak", "fixture-valley", "fixture-valley-ut", "unrelated-bowl"]
    by = {r["id"]: r for r in resorts["resorts"]}
    sources = {s["id"] for s in _load(out, "sources.json")["sources"]}
    for r in resorts["resorts"]:
        Resort.model_validate(r, context={"source_ids": sources})  # output round trips through the schema
        assert set(r["sources"]) <= sources

    peak = by["fixture-peak"]
    assert peak["tier"] == "A" and peak["tier_a_status"] == "draft" and peak["cluster_id"] == "i70-corridor"
    assert peak["climate_available"] is True and by["fixture-basin"]["climate_available"] is False
    assert peak["size"]["acres"]["value"] == 4000
    assert peak["size"]["lifts"] == {"value": 11, "source_id": "openskimap-ski-areas", "as_of": "2026-10-01",
                                     "confidence": "estimated", "note": "lifts mapped in OpenStreetMap (OpenSkiMap)"}
    assert peak["season"]["open_typical"]["value"] == "11-20"
    assert peak["career"] is None and peak["editorial"] is None
    assert peak["sources"] == ["fixture-owner-list", "fixture-source-a", "fixture-source-b", "openskimap-ski-areas"]
    assert "cluster_hint" not in peak

    valley = by["fixture-valley"]
    assert valley["tier"] == "B" and valley["tier_a_status"] is None and valley["owner"] is None
    assert valley["cluster_id"] == "i70-corridor" and valley["size"]["lifts"]["value"] == 3
    assert valley["location_source_id"] == "openskimap-ski-areas"
    assert by["faraway-hill"]["cluster_id"] == "other-co"
    assert by["fixture-valley-ut"]["cluster_id"] == "other-ut"
    assert by["unrelated-bowl"]["cluster_id"] == "i70-corridor"

    clusters = _load(out, "clusters.geojson")
    assert clusters["type"] == "FeatureCollection" and clusters["version"] == 1
    props = {f["properties"]["id"]: f["properties"] for f in clusters["features"]}
    assert sorted(props) == ["i70-corridor", "other-co", "other-ut", "wasatch"]
    assert props["i70-corridor"]["resort_ids"] == ["fixture-peak", "fixture-valley", "unrelated-bowl"]
    assert props["i70-corridor"]["tier_a_count"] == 1 and props["other-co"]["name"] == "Other Colorado"
    assert set(props["wasatch"]) == {"id", "name", "states", "resort_ids", "tier_a_count", "overview_md", "sources"}
    member_ids = sorted(rid for p in props.values() for rid in p["resort_ids"])
    assert member_ids == ids  # every resort in exactly one cluster
    for f in clusters["features"]:
        Cluster.model_validate({**f["properties"], "polygon": f["geometry"]})

    pts = _load(out, "resorts.geojson")
    peak_pt = next(f for f in pts["features"] if f["properties"]["id"] == "fixture-peak")
    assert peak_pt["geometry"] == {"type": "Point", "coordinates": [-106.3, 39.6]}
    assert peak_pt["properties"] == {"id": "fixture-peak", "name": "Fixture Peak", "tier": "A",
                                     "cluster_id": "i70-corridor", "state": "CO", "acres": 4000,
                                     "vertical_ft": round((3500.0 - 2400.12346) * 3.280839895), "lifts": 11,
                                     "size_rank": 4}
    assert next(f for f in pts["features"] if f["properties"]["id"] == "faraway-hill")["properties"]["size_rank"] == 1

    states = _load(out, "states.json")["states"]
    assert states == [
        {"code": "CO", "name": "Colorado", "cluster_ids": ["i70-corridor", "other-co"], "resort_count": 4},
        {"code": "UT", "name": "Utah", "cluster_ids": ["other-ut", "wasatch"], "resort_count": 2},
    ]
    assert sources == {"fixture-owner-list", "fixture-source-a", "fixture-source-b", "openskimap-ski-areas"}
    assert result.tier_a_count == 2 and len(result.clusters) == 4

    report = (tmp_path / "tier-a-draft.md").read_text()
    assert "| Fixture Peak (`fixture-peak`) | CO | Vail Resorts" in report
    assert "**Luxury**: Fixture rationale. ([Fixture source A](https://example.org/a))" in report
    assert "Fixture Ridge (CO) is marked tier_a_candidate" in report
    assert "vertical_ft" not in report.split("fixture-peak")[1].split("\n")[0]  # filled from OpenSkiMap
    assert "ski_school_staff_est" in report


def test_build_is_deterministic(repo_root, raw_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    a, b = tmp_path / "a", tmp_path / "b"
    _run(repo_root, raw_dir, a, tmp_path)
    _run(repo_root, raw_dir, b, tmp_path)
    for name in ("resorts.json", "resorts.geojson", "clusters.geojson", "states.json", "sources.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_approved_batch(repo_root, raw_dir, out, tmp_path):
    p = raw_dir / "tier_a" / "fixture.yaml"
    p.write_text(p.read_text().replace("status: draft", "status: approved"))
    _run(repo_root, raw_dir, out, tmp_path)
    tier_a = [r for r in _load(out, "resorts.json")["resorts"] if r["tier"] == "A"]
    assert {r["tier_a_status"] for r in tier_a} == {"approved"}


def test_tier_a_only_without_openskimap(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "openskimap" / "us_ski_areas.geojson").unlink()
    result = _run(repo_root, raw_dir, out, tmp_path)
    assert [r.id for r in result.resorts] == ["fixture-basin", "fixture-peak"]
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
    data["resorts"] = [r for r in data["resorts"] if r["name"] != "Fixture Peak"]
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
    assert len(result.resorts) == 6


@pytest.mark.parametrize("cmd", [["pipeline/build.py"], ["-m", "pipeline.build"]])
def test_entry_points(repo_root, cmd):
    r = subprocess.run([sys.executable, *cmd, "--help"], cwd=repo_root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "--fetch" in r.stdout
