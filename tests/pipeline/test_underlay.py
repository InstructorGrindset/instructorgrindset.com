"""The satellite style underlay (pipeline/tools/make_underlay.py, images/basemap/): transform math and the committed
assets. Pillow is needed only to regenerate the images, so nothing here imports it."""
import hashlib
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pipeline.heat import merc_y
from pipeline.tools import make_underlay as mu

REPO = Path(__file__).resolve().parents[2]
SIDECAR = REPO / "images" / "basemap" / "underlay.json"


def shader(rgb, saturation, contrast, bmax, bmin=0.0):
    """MapLibre's raster fragment shader (0..1 units), as written in its GLSL."""
    s = -saturation if saturation <= 0 else 1 - 1 / (1.001 - saturation)
    c = 1 + contrast if contrast <= 0 else 1 / (1 - contrast)
    avg = sum(rgb) / 3
    out = []
    for v in rgb:
        v = v + (avg - v) * s
        v = (v - 0.5) * c + 0.5
        out.append(bmin + (bmax - bmin) * v)
    return out


@pytest.mark.parametrize("tone", [(-0.35, -0.1, 0.7), (-0.3, 0.05, 0.55), (0.2, 0.3, 0.9)])
@pytest.mark.parametrize("rgb", [(0, 0, 0), (255, 255, 255), (200, 120, 40), (30, 90, 160)])
def test_tone_matrix_matches_the_maplibre_raster_shader(tone, rgb):
    m = mu.tone_matrix(*tone)
    want = [max(0, min(255, round(255 * v))) for v in shader([x / 255 for x in rgb], *tone)]
    got = mu.apply_tone(rgb, m)
    assert all(abs(a - b) <= 1 for a, b in zip(got, want)), (got, want)


def test_tone_darkens_and_mutes():
    m = mu.tone_matrix(mu.TONE["saturation"], mu.TONE["contrast"], mu.TONE["brightness_max"])
    lut = mu.gamma_lut(mu.TONE["gamma"])
    r, g, b = mu.apply_tone((200, 120, 40), m, lut)
    assert max(r, g, b) < 200 * mu.TONE["brightness_max"] + 1, "darker than the source"
    assert (max(r, g, b) - min(r, g, b)) < (200 - 40) * 0.6, "less saturated than the source"
    assert mu.apply_tone((255, 255, 255), m, lut)[0] <= round(255 * mu.TONE["brightness_max"])


def test_gamma_lut():
    lut = mu.gamma_lut(1.4)
    assert len(lut) == 256 and lut[0] == 0 and lut[255] == 255
    assert all(a <= b for a, b in zip(lut, lut[1:])) and lut[128] < 128
    assert mu.gamma_lut(1.0) == list(range(256))


def test_source_pixel_mapping():
    assert mu.src_col(-180) == 0 and mu.src_col(180) == 21600 and mu.src_col(-125) == 55 * 60
    assert mu.src_row(90) == 0 and mu.src_row(-90) == 10800 and mu.src_row(49.5) == 40.5 * 60


def test_row_edges_are_even_in_web_mercator():
    grid = mu.region_grid((-125.0, 24.4, -66.8, 49.5), 16.0)
    edges = mu.row_edges(grid)
    assert len(edges) == grid.height + 1
    assert edges[0] == pytest.approx(49.5) and edges[-1] == pytest.approx(24.4)
    ys = [merc_y(lat) for lat in edges]
    steps = [a - b for a, b in zip(ys, ys[1:])]
    assert max(steps) - min(steps) < 1e-9
    # Each row's center is the heat grid's row center (same rows as the heat rasters).
    for j in (0, grid.height // 2, grid.height - 1):
        mid = (2 * math.atan(math.exp((ys[j] + ys[j + 1]) / 2)) - math.pi / 2) * 180 / math.pi
        assert mid == pytest.approx(grid.lat_at(j), abs=1e-9)


def test_small_size():
    assert mu.small_size(4060, 2835) == (1015, 709)
    assert mu.small_size(5, 3) == (2, 2)


def test_feather_ramp():
    ramp = mu.feather_ramp(100, 10)
    assert ramp == ramp[::-1]
    assert ramp[0] < 0.01 and ramp[50] == 1.0 and ramp[9] < 1.0 and ramp[10] == 1.0
    assert all(a <= b for a, b in zip(ramp[:50], ramp[1:51]))
    assert mu.feather_ramp(5, 0) == [1.0] * 5


def test_script_needs_pillow_only_to_regenerate():
    code = "import sys; import pipeline.tools.make_underlay; print('PIL' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
    assert "pillow" not in (REPO / "requirements.txt").read_text().lower()


def webp_size(data: bytes) -> tuple[int, int]:
    """Width and height from a WebP header (lossy VP8 or extended VP8X)."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    kind = data[12:16]
    if kind == b"VP8 ":
        assert data[23:26] == b"\x9d\x01\x2a"
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    raise AssertionError(f"unexpected WebP chunk {kind!r}")


@pytest.fixture(scope="module")
def sidecar():
    return json.loads(SIDECAR.read_text())


def test_sidecar_matches_the_images(sidecar):
    rasters = {r["region"]: r for r in sidecar["rasters"]}
    assert set(rasters) == set(mu.REGIONS) == {"lower48", "alaska"}
    for region, r in rasters.items():
        spec = mu.REGIONS[region]
        data = (SIDECAR.parent / r["image"]).read_bytes()
        assert r["image"].endswith(".webp") and len(data) == r["bytes"]
        assert hashlib.sha256(data).hexdigest() == r["sha256"]
        assert webp_size(data) == (r["width"], r["height"])
        assert len(data) <= spec["max_bytes"], f"{region}: {len(data)} bytes"
        assert max(r["width"], r["height"]) <= mu.MAX_SIDE, "one WebGL texture on phones"
        grid = mu.region_grid(spec["box"], spec["px_per_degree"])
        assert (r["width"], r["height"]) == (grid.width, grid.height)
        assert r["coordinates"] == grid.coordinates()
        # The quarter size copy for national zooms: same corners, a quarter of the pixels each way.
        sm = r["small"]
        sdata = (SIDECAR.parent / sm["image"]).read_bytes()
        assert sm["image"] == f"underlay-{region}-small.webp" and len(sdata) == sm["bytes"]
        assert hashlib.sha256(sdata).hexdigest() == sm["sha256"]
        assert webp_size(sdata) == (sm["width"], sm["height"]) == mu.small_size(r["width"], r["height"])
        assert len(sdata) <= mu.SMALL_MAX_BYTES[region]
    assert rasters["lower48"]["bytes"] <= 600_000 and rasters["alaska"]["bytes"] <= 250_000


def test_lower48_box_covers_the_national_view(sidecar):
    cfg = (REPO / "map" / "js" / "config.js").read_text()
    (w, s), (e, n) = [tuple(map(float, p)) for p in re.findall(r"\[(-?[\d.]+), (-?[\d.]+)\]",
                                                                  re.search(r"nationalBounds: (\[\[.*?\]\])", cfg).group(1))]
    [[uw, un], _, [ue, us], _] = next(r for r in sidecar["rasters"] if r["region"] == "lower48")["coordinates"]
    feather = mu.REGIONS["lower48"]["feather_deg"]
    assert uw + feather <= w and ue - feather >= e and us + feather <= s and un - feather >= n


def test_alaska_box_holds_alaska_clear_of_its_fade(sidecar):
    """The Alaska image draws at its true place. It holds the heat maps' Alaska region, its fade stays off the Arctic
    coast (Point Barrow, 71.39 N), the Aleutians (51.2 N) and the panhandle (130.0 W), and it fades to transparent so
    it blends over the lower 48 image where the two meet off British Columbia."""
    ak = yaml.safe_load((REPO / "config" / "definitions.yaml").read_text())["heat"]["regions"]["alaska"]
    r = next(r for r in sidecar["rasters"] if r["region"] == "alaska")
    [[w, n], _, [e, s], _] = r["coordinates"]
    assert w <= ak["west"] and s <= ak["south"] and e >= ak["east"] and n >= ak["north"]
    f = mu.REGIONS["alaska"]["feather_deg"]
    assert r["feather_deg"] == f and n - f >= 71.39 and s + f <= 51.2 and e - f >= -130.0
    assert r["fade"] == mu.REGIONS["alaska"]["fade"] == "alpha"
    assert mu.REGIONS["lower48"]["fade"] == "background"
    (lw, ls, le, ln) = mu.REGIONS["lower48"]["box"]
    assert lw < e and ln > s, "the two images overlap off British Columbia, so no gap opens between them"


def test_tone_and_background_match_the_map(sidecar):
    cfg = (REPO / "map" / "js" / "config.js").read_text()
    assert f"background: '{sidecar['background']}'" in cfg
    assert sidecar["tone"] == mu.TONE
    assert "underlay.json', import.meta.url)" in cfg and "images/basemap/underlay.json" in cfg


def test_natural_earth_is_sourced_and_credited(sidecar):
    src = yaml.safe_load((REPO / "data" / "raw" / "sources" / "basemap.yaml").read_text())["sources"]
    rec = next(s for s in src if s["id"] == sidecar["source"]["id"])
    assert rec["url"] == sidecar["source"]["url"] and "public domain" in rec["note"].lower()
    assert sidecar["source"]["license"] == "public domain"
    credits = (REPO / "map" / "credits" / "index.html").read_text()
    assert "<h3>Natural Earth</h3>" in credits and "https://www.naturalearthdata.com/about/terms-of-use/" in credits
    assert "Relief: <a href=\"https://www.naturalearthdata.com/\"" in (REPO / "map" / "js" / "config.js").read_text()


def test_downloads_stay_out_of_git_and_images_are_published():
    assert "data/raw/basemap/download/" in (REPO / ".gitignore").read_text()
    excluded = yaml.safe_load((REPO / "_config.yml").read_text())["exclude"]
    assert "images" not in excluded and not any(str(x).startswith("images") for x in excluded)
