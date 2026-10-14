import json
import re
import shutil
import subprocess

import pytest

from pipeline.states import STATE_NAMES, STATES, build_states

FIFTY_PLUS_DC = {code for code, _ in STATES.values()} - {"AS", "GU", "MP", "PR", "VI"}


def test_state_table():
    assert len(FIFTY_PLUS_DC) == 51
    assert STATE_NAMES["CO"] == "Colorado" and STATES["08"] == ("CO", "Colorado")


def test_build_states():
    out = build_states([("a", "CO", "i70-corridor"), ("b", "CO", "other-co"), ("c", "CA", "lake-tahoe"),
                        ("d", "NV", "lake-tahoe"), ("e", "CO", "i70-corridor")])
    assert out == [
        {"code": "CA", "name": "California", "cluster_ids": ["lake-tahoe"], "resort_count": 1},
        {"code": "CO", "name": "Colorado", "cluster_ids": ["i70-corridor", "other-co"], "resort_count": 3},
        {"code": "NV", "name": "Nevada", "cluster_ids": ["lake-tahoe"], "resort_count": 1},
    ]
    assert build_states([]) == []


def _check_us_states(path):
    data = json.loads(path.read_text())
    assert data["type"] == "FeatureCollection" and data["version"] == 1 and "generated_at" in data
    codes = [f["properties"]["code"] for f in data["features"]]
    assert len(codes) == len(set(codes))
    assert FIFTY_PLUS_DC <= set(codes)
    for f in data["features"]:
        p = f["properties"]
        assert set(p) == {"code", "name"} and STATE_NAMES[p["code"]] == p["name"]
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
    text = path.read_text()
    assert not re.search(r"\d\.\d{5,}", text), "coordinates are rounded to 4 decimals"
    co = next(f for f in data["features"] if f["properties"]["code"] == "CO")
    ring = co["geometry"]["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    assert -109.1 < min(lons) < -108.9 and -102.1 < max(lons) < -101.9  # unprojected lon/lat
    assert 36.9 < min(lats) < 37.1 and 40.9 < max(lats) < 41.1
    assert ring[0] == ring[-1]


def test_committed_us_states_geojson(repo_root):
    path = repo_root / "data" / "build" / "us-states.geojson"
    if not path.exists():
        pytest.skip("data/build/us-states.geojson not built yet")
    _check_us_states(path)


def test_states_script(repo_root, tmp_path):
    if not shutil.which("node") or not (repo_root / "node_modules" / "us-atlas").is_dir():
        pytest.skip("node or node_modules (npm install) unavailable")
    out = tmp_path / "us-states.geojson"
    subprocess.run(["node", str(repo_root / "scripts" / "build-states.mjs"), str(out)], check=True,
                   capture_output=True, env={"SOURCE_DATE_EPOCH": "1790000000", "PATH": __import__("os").environ["PATH"]})
    _check_us_states(out)
    assert json.loads(out.read_text())["generated_at"] == "2026-09-21T14:13:20Z"
