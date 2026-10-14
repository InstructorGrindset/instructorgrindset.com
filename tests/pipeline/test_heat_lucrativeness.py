"""Lucrativeness heat rasters (pipeline/heat_lucrativeness.py) and their legend twin of map/js/heat.js.

SYNTHETIC TEST FIXTURES: the resorts and net incomes below are invented. Never copy to data/.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import heat_lucrativeness as hl
from pipeline.config import load_config

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.fixture
def heat_cfg():
    return load_config(REPO / "config").definitions.heat


def resort(rid, lon, lat, mid, status="computed"):
    return {"id": rid, "lon": lon, "lat": lat, "state": "CO",
            "career": {"lucrativeness": {"status": status, "mid": mid, "low": mid, "high": mid}}}


VALUE_SETS = [[8900, 10600], [-2400, 3100, 12000], [500], [15000, 15020], [-800, -200], [123, 98765, 4321]]


def test_linear_legend_matches_the_fixture_numbers(heat_cfg):
    lg = hl.legend_scale(heat_cfg.modes.lucrativeness, [8900, 10600])
    assert lg == {"scale": "linear", "domain": {"min": 8500, "max": 11000}, "ticks": [8500, 9000, 9500, 10000, 10500, 11000]}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_legend_scale_is_the_twin_of_heat_js(heat_cfg):
    mode = heat_cfg.modes.lucrativeness
    script = ("import { legendScale } from './map/js/heat.js';"
              f"const sets = {json.dumps(VALUE_SETS)};"
              "console.log(JSON.stringify(sets.map((v) => legendScale({ scale: 'linear' }, v))));")
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, cwd=REPO, timeout=60)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    for vals, want in zip(VALUE_SETS, js):
        got = hl.legend_scale(mode, vals)
        assert got["domain"] == pytest.approx(want["domain"]) and got["ticks"] == pytest.approx(want["ticks"]), vals


def test_points_take_only_computed_values():
    pts = hl.lucrativeness_points([resort("a", -106, 39.6, 9000), resort("b", -111, 40.6, None, "insufficient_data"),
                                   {"id": "c", "lon": -100, "lat": 40, "career": None}])
    assert [(p.lon, p.lat, p.value) for p in pts] == [(-106, 39.6, 9000.0)]


def _outlines():
    return json.loads((REPO / "data" / "build" / "us-states.geojson").read_text())


def test_write_lucrativeness_rasters_and_manifest(tmp_path, heat_cfg):
    if not (REPO / "data" / "build" / "us-states.geojson").exists():
        pytest.skip("us-states.geojson not built")
    resorts = [resort("a", -106.3, 39.6, 8900), resort("b", -111.6, 40.6, 10600), resort("c", -120.2, 39.2, 9800),
               resort("d", -149.1, 60.97, 7000)]
    m = hl.write_lucrativeness(tmp_path, resorts=resorts, outlines=_outlines(), heat=heat_cfg, generated_at="2026-10-14T00:00:00Z")
    assert m["mode"] == "lucrativeness" and m["units"] == "usd" and m["scale"] == "linear"
    assert m["domain"] == {"min": 7000, "max": 11000} and m["ramp"] == list(heat_cfg.ramp.colors)
    assert m["values"] == {"field": "career.lucrativeness.mid", "resorts": 4, "points": 4} and m["attribution"] is None
    assert {r["region"] for r in m["rasters"]} == {"lower48", "alaska"}
    for r in m["rasters"]:
        png = tmp_path / "heat" / r["image"]
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size < 400_000
    assert json.loads((tmp_path / "heat" / "lucrativeness.json").read_text())["inputs_sha256"] == m["inputs_sha256"]
    # unchanged inputs: the manifest is reused as is
    again = hl.write_lucrativeness(tmp_path, resorts=resorts, outlines=_outlines(), heat=heat_cfg, generated_at="later")
    assert again["generated_at"] == "2026-10-14T00:00:00Z"
    # no value left: the rasters go away and the browser shows the mode as disabled
    assert hl.write_lucrativeness(tmp_path, resorts=[resort("a", -106, 39.6, None, "insufficient_data")],
                                  outlines=_outlines(), heat=heat_cfg, generated_at="x") is None
    assert not list((tmp_path / "heat").glob("lucrativeness*"))
