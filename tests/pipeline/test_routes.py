"""Tests for pipeline/routes.py (static deep link stubs). All data here is synthetic."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.routes import MARKER, generate_routes  # noqa: E402

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Instructor Grindset &bull; Snowsports Career Map</title>
  <meta name="description" content="National description." />
  <link rel="canonical" href="https://instructorgrindset.com/map/" />
  <link rel="stylesheet" href="/map/css/map.css">
</head>
<body><a href="#main">Skip</a><script type="module" src="/map/js/app.js"></script></body>
</html>
"""


def _env(n: int = 1) -> dict:
    return {"version": 1, "generated_at": "2026-10-11T00:00:00Z"}


def write_build(root: Path, resorts: list[dict], clusters: list[dict], states: list[dict]) -> None:
    build = root / "data" / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "resorts.json").write_text(json.dumps({**_env(), "resorts": resorts}))
    (build / "clusters.geojson").write_text(json.dumps({"type": "FeatureCollection", **_env(), "features": [
        {"type": "Feature", "geometry": None, "properties": c} for c in clusters]}))
    (build / "states.json").write_text(json.dumps({**_env(), "states": states}))


def default_data():
    resorts = [
        {"id": "alpha-peak", "name": "Alpha Peak", "cluster_id": "front", "state": "CO"},
        {"id": "bravo-bowl", "name": "Bravo & Sons <Bowl>", "cluster_id": "front", "state": "CO"},
        {"id": "lake-west", "name": "Lake West", "cluster_id": "lake", "state": "CA"},
        {"id": "lake-east", "name": "Lake East", "cluster_id": "lake", "state": "NV"},
    ]
    clusters = [
        {"id": "front", "name": "Test Front", "states": ["CO"], "resort_ids": ["alpha-peak", "bravo-bowl"]},
        {"id": "lake", "name": "Test Lake", "states": ["CA", "NV"], "resort_ids": ["lake-west", "lake-east"]},
    ]
    states = [
        {"code": "CO", "name": "Colorado", "cluster_ids": ["front"], "resort_count": 2},
        {"code": "CA", "name": "California", "cluster_ids": ["lake"], "resort_count": 1},
        {"code": "NV", "name": "Nevada", "cluster_ids": ["lake"], "resort_count": 1},
    ]
    return resorts, clusters, states


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "map" / "js").mkdir(parents=True)
    (tmp_path / "map" / "css").mkdir()
    (tmp_path / "map" / "vendor" / "zod").mkdir(parents=True)
    (tmp_path / "map" / "index.html").write_text(TEMPLATE)
    (tmp_path / "map" / "js" / "app.js").write_text("// app")
    (tmp_path / "map" / "css" / "map.css").write_text("/* css */")
    (tmp_path / "map" / "vendor" / "zod" / "zod.mjs").write_text("// zod")
    write_build(tmp_path, *default_data())
    return tmp_path


def read(site: Path, rel: str) -> str:
    return (site / "map" / rel / "index.html").read_text()


def test_writes_state_cluster_and_resort_stubs(site: Path):
    n = generate_routes(site)
    # 3 states + front(1) + lake(2 states) clusters + 2 front resorts + 2 lake resorts x 2 states
    assert n == 3 + 3 + 2 + 4
    co = read(site, "co")
    assert "<title>Colorado Resorts and Clusters • Snowsports Career Map</title>" in co
    assert 'href="https://instructorgrindset.com/map/co/"' in co
    assert "2 resorts, with a source on every number." in co
    assert MARKER in co
    cluster = read(site, "co/front")
    assert "<title>Test Front Cluster • Snowsports Career Map</title>" in cluster
    assert 'href="https://instructorgrindset.com/map/co/front/"' in cluster
    resort = read(site, "co/front/alpha-peak")
    assert "<title>Alpha Peak: Season, Snow and Size • Snowsports Career Map</title>" in resort
    assert "Plan a season at Alpha Peak in the Test Front cluster, Colorado." in resort
    assert 'href="https://instructorgrindset.com/map/co/front/alpha-peak/"' in resort
    # Asset paths stay absolute, the body is the shell unchanged.
    assert 'src="/map/js/app.js"' in resort
    assert resort.count("<title>") == 1


def test_multi_state_cluster_canonicals(site: Path):
    generate_routes(site)
    for st in ("ca", "nv"):
        page = read(site, f"{st}/lake")
        assert 'href="https://instructorgrindset.com/map/ca/lake/"' in page
        assert "in California and Nevada." in page
    # Resort canonical follows the resort's own state.
    assert 'href="https://instructorgrindset.com/map/nv/lake/lake-east/"' in read(site, "ca/lake/lake-east")
    assert 'href="https://instructorgrindset.com/map/ca/lake/lake-west/"' in read(site, "nv/lake/lake-west")


def test_escapes_html_in_names(site: Path):
    generate_routes(site)
    page = read(site, "co/front/bravo-bowl")
    assert "<title>Bravo &amp; Sons &lt;Bowl&gt;: Season" in page
    assert "<Bowl>" not in page


def test_manifest_and_stale_removal(site: Path):
    generate_routes(site)
    manifest = json.loads((site / "map" / ".routes-manifest.json").read_text())
    assert "co/front/bravo-bowl/" in manifest["pages"]
    # A hand placed file inside a stub directory survives cleanup.
    (site / "map" / "co" / "front" / "notes.txt").write_text("keep")

    resorts, clusters, states = default_data()
    resorts = [r for r in resorts if r["id"] != "bravo-bowl" and r["cluster_id"] != "lake"]
    clusters = [dict(clusters[0], resort_ids=["alpha-peak"])]
    states = [dict(states[0], resort_count=1)]
    write_build(site, resorts, clusters, states)
    n = generate_routes(site)

    assert n == 3
    assert not (site / "map" / "co" / "front" / "bravo-bowl").exists()
    assert not (site / "map" / "ca").exists()
    assert not (site / "map" / "nv").exists()
    assert (site / "map" / "co" / "front" / "alpha-peak" / "index.html").exists()
    assert (site / "map" / "co" / "front" / "notes.txt").read_text() == "keep"
    # Protected files are untouched.
    assert (site / "map" / "index.html").read_text() == TEMPLATE
    assert (site / "map" / "js" / "app.js").exists()
    assert (site / "map" / "css" / "map.css").exists()
    assert (site / "map" / "vendor" / "zod" / "zod.mjs").exists()
    manifest = json.loads((site / "map" / ".routes-manifest.json").read_text())
    assert manifest["pages"] == ["co/", "co/front/", "co/front/alpha-peak/"]


def test_stale_stub_removed_even_without_manifest(site: Path):
    generate_routes(site)
    (site / "map" / ".routes-manifest.json").unlink()
    resorts, clusters, states = default_data()
    write_build(site, [r for r in resorts if r["id"] != "alpha-peak"],
                [dict(clusters[0], resort_ids=["bravo-bowl"]), clusters[1]], states)
    generate_routes(site)
    assert not (site / "map" / "co" / "front" / "alpha-peak").exists()


def test_never_deletes_protected_or_unmarked(site: Path):
    generate_routes(site)
    # Poison the manifest with protected and foreign paths.
    hand_made = site / "map" / "about"
    hand_made.mkdir()
    (hand_made / "index.html").write_text("<html>hand made</html>")
    (site / "map" / ".routes-manifest.json").write_text(json.dumps(
        {"pages": ["js/", "css/", "vendor/", "vendor/zod/", "about/", "../outside/", ""]}))
    outside = site / "outside"
    outside.mkdir()
    (outside / "index.html").write_text(MARKER)
    generate_routes(site)
    assert (site / "map" / "js" / "app.js").exists()
    assert (site / "map" / "vendor" / "zod" / "zod.mjs").exists()
    assert (site / "map" / "css" / "map.css").exists()
    assert (hand_made / "index.html").exists()
    assert (outside / "index.html").exists()
    assert (site / "map" / "index.html").read_text() == TEMPLATE


def test_refuses_to_overwrite_hand_written_page(site: Path):
    (site / "map" / "co").mkdir()
    (site / "map" / "co" / "index.html").write_text("<html>mine</html>")
    with pytest.raises(RuntimeError):
        generate_routes(site)


def test_skips_unsafe_slugs(site: Path):
    resorts, clusters, states = default_data()
    resorts.append({"id": "../evil", "name": "Evil", "cluster_id": "front", "state": "CO"})
    clusters.append({"id": "Bad Slug", "name": "Bad", "states": ["CO"], "resort_ids": []})
    states.append({"code": "JS", "name": "Protected", "cluster_ids": [], "resort_count": 0})
    write_build(site, resorts, clusters, states)
    n = generate_routes(site)
    assert n == 12
    assert not (site / "evil").exists()
    assert not (site / "map" / "co" / "Bad Slug").exists()
    assert (site / "map" / "js" / "app.js").read_text() == "// app"


def test_rejects_relative_asset_paths(site: Path):
    (site / "map" / "index.html").write_text(TEMPLATE.replace('src="/map/js/app.js"', 'src="js/app.js"'))
    with pytest.raises(ValueError):
        generate_routes(site)


def test_rejects_stub_as_template(site: Path):
    (site / "map" / "index.html").write_text(TEMPLATE.replace("<head>", f"<head>{MARKER}"))
    with pytest.raises(ValueError):
        generate_routes(site)


def test_idempotent(site: Path):
    generate_routes(site)
    before = read(site, "co/front/alpha-peak")
    mtime = (site / "map" / "co" / "front" / "alpha-peak" / "index.html").stat().st_mtime_ns
    generate_routes(site)
    assert read(site, "co/front/alpha-peak") == before
    assert (site / "map" / "co" / "front" / "alpha-peak" / "index.html").stat().st_mtime_ns == mtime


def test_cli(site: Path):
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "routes.py"), "--root", str(site)],
                         capture_output=True, text=True, check=True)
    assert "wrote 12 stub pages" in out.stdout


def test_cli_missing_build(tmp_path: Path):
    (tmp_path / "map").mkdir()
    (tmp_path / "map" / "index.html").write_text(TEMPLATE)
    out = subprocess.run([sys.executable, str(REPO / "pipeline" / "routes.py"), "--root", str(tmp_path)],
                         capture_output=True, text=True)
    assert out.returncode == 1
    assert "missing" in out.stderr


def test_real_shell_is_a_valid_template():
    from pipeline.routes import check_template
    check_template((REPO / "map" / "index.html").read_text())
