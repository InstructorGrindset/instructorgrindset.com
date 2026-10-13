"""Heat map transforms (pipeline/heat.py, pipeline/png.py) and the heat config. Synthetic data only."""
import json
import math
import random
import re
import struct
import zlib
from pathlib import Path

import pytest

from pipeline import heat, png
from pipeline.config import load_config, oklab_lightness

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def heat_cfg():
    return load_config(REPO / "config").definitions.heat


# ------------------------------------------------------------------ PNG
def decode_png(data: bytes):
    """Minimal PNG reader for the tests: checks the signature and every CRC, unfilters RGBA rows."""
    assert data[:8] == png.SIGNATURE
    pos, chunks = 8, []
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc, kind
        chunks.append((kind, body))
        pos += 12 + n
    assert chunks[0][0] == b"IHDR" and chunks[-1][0] == b"IEND"
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (depth, ctype, comp, filt, inter) == (8, 6, 0, 0, 0)
    raw = zlib.decompress(b"".join(b for k, b in chunks if k == b"IDAT"))
    stride = w * 4
    assert len(raw) == h * (stride + 1)
    out, prev = bytearray(), bytearray(stride)
    for j in range(h):
        ft, row = raw[j * (stride + 1)], bytearray(raw[j * (stride + 1) + 1:(j + 1) * (stride + 1)])
        for i in range(stride):
            a = row[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if ft == 1:
                row[i] = (row[i] + a) & 0xFF
            elif ft == 2:
                row[i] = (row[i] + b) & 0xFF
            elif ft == 3:
                row[i] = (row[i] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                row[i] = (row[i] + png._paeth(a, b, c)) & 0xFF
        out += row
        prev = row
    return w, h, bytes(out)


@pytest.mark.parametrize("ft", [png.FILTER_NONE, png.FILTER_SUB, png.FILTER_UP, png.FILTER_AVERAGE, png.FILTER_PAETH])
def test_png_round_trips_every_filter(ft):
    rnd = random.Random(7)
    w, h = 13, 7
    pixels = bytes(rnd.randrange(256) for _ in range(w * h * 4))
    assert decode_png(png.encode_rgba(w, h, pixels, filter_type=ft)) == (w, h, pixels)


def test_png_rejects_wrong_sizes():
    with pytest.raises(ValueError):
        png.encode_rgba(2, 2, b"\0" * 15)
    with pytest.raises(ValueError):
        png.encode_rgba(0, 2, b"")


# ------------------------------------------------------------------ Web Mercator grid
def test_grid_rows_are_evenly_spaced_in_web_mercator():
    g = heat.Grid.for_box(-125.0, 24.4, -66.8, 49.5, 12)
    ys = [heat.merc_y(g.lat_at(j)) for j in range(g.height)]
    steps = [b - a for a, b in zip(ys, ys[1:])]
    assert max(steps) - min(steps) < 1e-9 and all(s < 0 for s in steps)
    # Row centers sit half a row inside the exact corners, which the sidecar lists.
    row = (g.y_north - g.y_south) / g.height
    assert ys[0] == pytest.approx(g.y_north - row / 2) and ys[-1] == pytest.approx(g.y_south + row / 2)
    assert g.coordinates() == [[-125.0, 49.5], [-66.8, 49.5], [-66.8, 24.4], [-125.0, 24.4]]
    # Degrees of latitude per row shrink northward (Mercator), columns are even in longitude.
    assert (g.lat_at(0) - g.lat_at(1)) < (g.lat_at(g.height - 2) - g.lat_at(g.height - 1))
    assert g.lon_at(1) - g.lon_at(0) == pytest.approx((g.east - g.west) / g.width)
    assert g.y_of(g.north) == pytest.approx(0) and g.y_of(g.south) == pytest.approx(g.height)
    assert g.x_of(g.lon_at(5)) == pytest.approx(5.5) and g.y_of(g.lat_at(9)) == pytest.approx(9.5)


def test_grid_resolution_and_cap():
    g = heat.Grid.for_box(-110, 30, -100, 40, 20)
    assert g.width == 200
    assert g.height == round((heat.merc_y(40) - heat.merc_y(30)) / heat.RAD * 20)
    capped = heat.Grid.for_box(-110, 30, -100, 40, 20, max_pixels=10000)
    assert capped.width * capped.height <= 10100


def test_node_positions_include_the_last_pixel():
    assert heat.node_positions(10, 4) == [0, 4, 8, 9]
    assert heat.node_positions(9, 4) == [0, 4, 8]


# ------------------------------------------------------------------ interpolation
def test_point_index_nearest_matches_brute_force():
    rnd = random.Random(3)
    pts = [heat.Point(rnd.uniform(-120, -70), rnd.uniform(25, 49), rnd.uniform(0, 100)) for _ in range(400)]
    index = heat.PointIndex(pts, cell_deg=1.5)
    for _ in range(40):
        lon, lat = rnd.uniform(-125, -65), rnd.uniform(24, 50)
        kmlon = heat.KM_PER_DEG_LON * math.cos(lat * heat.RAD)
        brute = sorted(math.hypot((p.lon - lon) * kmlon, (p.lat - lat) * heat.KM_PER_DEG_LAT) for p in pts)[:9]
        got = [d for d, _ in index.nearest(lon, lat, 9)]
        assert got == pytest.approx(brute)


def test_shepard_is_exact_at_an_isolated_point_and_constant_for_equal_values():
    nbrs = [(0.0, 300.0), (150.0, 20.0), (220.0, 40.0)]
    # min_distance_km keeps a small share for the neighbors (no spike), so "exact" is within a percent.
    assert heat.shepard(nbrs, 8, 10) == pytest.approx(300.0, rel=0.01)
    assert heat.shepard([(d, 55.0) for d in (3, 40, 90, 120, 200)], 3, 10) == pytest.approx(55.0)
    assert heat.shepard([], 3, 10) is None
    # The point at distance R (the k + 1th) has no weight, so leaving the set is seamless.
    assert heat.shepard([(10, 1.0), (20, 1.0), (30, 99.0)], 2, 5) == pytest.approx(1.0)


def test_field_is_continuous_and_stays_within_the_values():
    rnd = random.Random(11)
    pts = [heat.Point(rnd.uniform(-112, -104), rnd.uniform(36, 42), rnd.uniform(10, 400)) for _ in range(60)]
    index = heat.PointIndex(pts, 1.0)
    prev = None
    for step in range(400):
        lon = -113 + step * 0.025
        v = heat.shepard(index.nearest(lon, 39.0, 9), 8, 12)
        assert 10 <= v <= 400
        if prev is not None:
            assert abs(v - prev) < 40, (lon, prev, v)
        prev = v


def test_decluster_merges_a_cluster_into_one_voice():
    dense = [heat.Point(-106 + (i % 4) * 0.05, 39.5 + (i // 4) * 0.05, 10) for i in range(12)]
    merged = heat.decluster(dense + [heat.Point(-100, 39.5, 90)], 20)
    assert len(merged) == 2
    m = [p for p in merged if p.value == 10][0]
    assert m.lon == pytest.approx(-105.925) and m.lat == pytest.approx(39.55)
    assert heat.decluster(dense, 0) == dense


def test_opacity_full_near_data_then_fades_to_the_floor(heat_cfg):
    f = heat_cfg.field
    op = lambda d: heat.opacity(d, f.full_opacity_km, f.fade_end_km, f.max_alpha, f.floor_alpha)  # noqa: E731
    assert op(0) == op(f.full_opacity_km) == f.max_alpha
    assert op(f.fade_end_km) == op(5000) == f.floor_alpha > 0
    vals = [op(d) for d in range(int(f.full_opacity_km), int(f.fade_end_km) + 1, 10)]
    assert all(b <= a for a, b in zip(vals, vals[1:]))


# ------------------------------------------------------------------ pockets
def _flat_grid():
    return heat.Grid.for_box(-112.5, 40.0, -111.0, 41.0, 40)


def test_pocket_raises_the_field_locally_and_never_lowers_it():
    g = _flat_grid()
    rnd = random.Random(5)
    bg = [rnd.uniform(50, 400) for _ in range(g.width * g.height)]
    pockets = [heat.Point(-111.64, 40.59, 545), heat.Point(-111.6, 40.62, 500), heat.Point(-112.0, 40.5, 30)]
    out, near = heat.apply_pockets(g, bg, pockets, 25)
    assert all(o >= b for o, b in zip(out, bg))
    assert any(o > b for o, b in zip(out, bg))
    # At the resort the field reaches the resort's value; beyond the radius nothing changes.
    k = int(g.y_of(40.59)) * g.width + int(g.x_of(-111.64))
    assert out[k] == pytest.approx(545, abs=20)
    far = int(g.y_of(40.05)) * g.width + int(g.x_of(-112.45))
    assert out[far] == bg[far] and far not in near


def test_a_low_resort_leaves_a_high_background_alone():
    g = _flat_grid()
    bg = [300.0] * (g.width * g.height)
    out, _ = heat.apply_pockets(g, bg, [heat.Point(-111.7, 40.5, 120)], 25)
    assert out == bg


def test_pocket_falloff_is_smooth_and_peaks_at_the_resort():
    g = _flat_grid()
    bg = [100.0] * (g.width * g.height)
    out, _ = heat.apply_pockets(g, bg, [heat.Point(-111.75, 40.5, 500)], 25)
    j = int(g.y_of(40.5))
    row = out[j * g.width:(j + 1) * g.width]
    peak = row.index(max(row))
    assert abs(g.lon_at(peak) + 111.75) < 0.03
    left = row[:peak + 1]
    assert all(b >= a for a, b in zip(left, left[1:]))


# ------------------------------------------------------------------ outlines and color
def test_coverage_of_a_square_is_exact_inside_and_partial_on_the_edge():
    g = heat.Grid(0.0, 0.0, 10.0, 10.0, 10, 10)
    # A square from x = 2.5 to 7.5 pixels and y = 2 to 8 pixels, in lon and lat.
    x0, x1 = 2.5, 7.5
    lat_top, lat_bot = heat.lat_of(g.y_north - 2 / 10 * (g.y_north - g.y_south)), heat.lat_of(g.y_north - 8 / 10 * (g.y_north - g.y_south))
    ring = [(x0, lat_bot), (x1, lat_bot), (x1, lat_top), (x0, lat_top), (x0, lat_bot)]
    cov = heat.coverage(g, [[ring]])
    at = lambda i, j: cov[j * 10 + i]  # noqa: E731
    assert at(5, 5) == pytest.approx(1.0)
    assert at(2, 5) == pytest.approx(0.5) and at(7, 5) == pytest.approx(0.5)
    assert at(0, 0) == 0 and at(5, 9) == 0 and at(5, 1) == 0
    assert sum(cov) == pytest.approx(5 * 6)


def test_coverage_honors_holes():
    g = heat.Grid(0.0, -1.0, 20.0, 1.0, 20, 2)
    outer = [(1, -0.9), (19, -0.9), (19, 0.9), (1, 0.9), (1, -0.9)]
    hole = [(8, -0.9), (12, -0.9), (12, 0.9), (8, 0.9), (8, -0.9)]
    cov = heat.coverage(g, [[outer, hole]])
    assert cov[10] == 0 and cov[4] == pytest.approx(1.0)


def test_region_polygons_split_the_lower_48_from_alaska():
    fc = {"features": [
        {"properties": {"code": "CO"}, "geometry": {"type": "Polygon", "coordinates": [[[-109, 37], [-102, 37], [-102, 41], [-109, 37]]]}},
        {"properties": {"code": "AK"}, "geometry": {"type": "MultiPolygon", "coordinates": [[[[179, 52], [-170, 52], [-170, 53], [179, 52]]]]}},
        {"properties": {"code": "HI"}, "geometry": {"type": "Polygon", "coordinates": [[[-160, 20], [-155, 20], [-155, 22], [-160, 20]]]}},
    ]}
    assert len(heat.region_polygons(fc, "lower48")) == 1
    ak = heat.region_polygons(fc, "alaska")
    assert ak[0][0][0] == (-181, 52)  # rings across the antimeridian wrap west


def test_ramp_is_perceptually_ordered_and_visible_on_the_dark_base(heat_cfg):
    colors = heat_cfg.ramp.colors
    ls = [oklab_lightness(c) for c in colors]
    assert all(b - a >= 0.04 for a, b in zip(ls, ls[1:]))
    # The map background lives in map/js/config.js; the darkest stop keeps at least 2:1 against it.
    bg = re.search(r"background: '(#[0-9a-f]{6})'", (REPO / "map" / "js" / "config.js").read_text()).group(1)

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    assert (lum(colors[0]) + 0.05) / (lum(bg) + 0.05) >= 2.0
    assert heat.ramp_rgb(0, colors) == heat.hex_rgb(colors[0]) and heat.ramp_rgb(1, colors) == heat.hex_rgb(colors[-1])


def test_ramp_validation_rejects_an_unordered_ramp(heat_cfg):
    with pytest.raises(ValueError):
        type(heat_cfg.ramp).model_validate({"name": "x", "colors": ["#fde725", "#414487", "#21918c"]})


def test_scales_ticks_and_nice_numbers():
    assert heat.scale_t(175, "sqrt", 0, 700) == pytest.approx(0.5)
    assert heat.scale_t(350, "linear", 0, 700) == pytest.approx(0.5)
    assert heat.scale_t(-5, "linear", 0, 700) == 0 and heat.scale_t(900, "sqrt", 0, 700) == 1
    assert [heat.nice_ceil(x) for x in (700, 641, 463, 287, 9)] == [700, 700, 500, 300, 10]
    assert heat.sqrt_ticks([0, 25, 100, 200, 400], 0, 700) == [0, 25, 100, 200, 400, 700]
    assert heat.sqrt_ticks([0, 25, 100, 200, 400], 0, 450) == [0, 25, 100, 200, 450]


def test_alaska_region_matches_the_national_inset(heat_cfg):
    src = (REPO / "map" / "js" / "inset.js").read_text()
    m = re.search(r"const SOURCE = \{ west: ([-\d.]+), east: ([-\d.]+), south: ([-\d.]+), north: ([-\d.]+) \}", src)
    ak = heat_cfg.regions.alaska
    assert [float(x) for x in m.groups()] == [ak.west, ak.east, ak.south, ak.north]


# ------------------------------------------------------------------ the rasters
SQUARE_STATES = {"features": [
    {"properties": {"code": "UT"}, "geometry": {"type": "Polygon", "coordinates": [[[-114, 37], [-109, 37], [-109, 42], [-114, 42], [-114, 37]]]}},
    {"properties": {"code": "AK"}, "geometry": {"type": "Polygon", "coordinates": [[[-150, 60], [-145, 60], [-145, 63], [-150, 63], [-150, 60]]]}},
]}


def _small(heat_cfg, ppd=4):
    return heat_cfg.model_copy(update={"snowfall": heat_cfg.snowfall.model_copy(update={"px_per_degree": ppd})})


def test_write_snowfall_rasters_and_sidecar(tmp_path, heat_cfg):
    h = _small(heat_cfg)
    stations = [heat.Point(-114 + i * 0.5, 37 + j * 0.5, 40 + 10 * i) for i in range(11) for j in range(11)]
    stations += [heat.Point(-148, 61, 70), heat.Point(-146, 62.5, 90)]
    pockets = [heat.Point(-111.64, 40.59, 545)]
    m = heat.write_snowfall(tmp_path, stations=stations, pockets=pockets, outlines=SQUARE_STATES, heat=h,
                            source={"id": "fixture-noaa"}, generated_at="2026-10-13T00:00:00Z", as_of="2026-10-13")
    assert m["domain"] == {"min": 0, "max": 600} and m["ticks"][-1] == 600 and m["scale"] == "sqrt"
    assert m["background"] == {"source_id": "fixture-noaa", "as_of": "2026-10-13", "confidence": "published",
                               "field": "ANN-SNOW-NORMAL", "stations": len(stations)}
    assert m["attribution"] == "Snowfall normals: NOAA NCEI 1991 to 2020"
    assert {r["region"] for r in m["rasters"]} == {"lower48", "alaska"}
    assert json.loads((tmp_path / "heat" / "snowfall.json").read_text()) == m
    for r in m["rasters"]:
        data = (tmp_path / "heat" / r["image"]).read_bytes()
        w, hgt, px = decode_png(data)
        assert (w, hgt) == (r["width"], r["height"]) and len(data) == r["bytes"] < 400_000
        box = getattr(h.regions, r["region"])
        assert r["coordinates"] == [[box.west, box.north], [box.east, box.north], [box.east, box.south], [box.west, box.south]]
        alphas = px[3::4]
        assert max(alphas) == 255 and min(alphas) == 0  # clipped to the outlines, opaque inside
    # Outside the state square nothing is drawn; inside every pixel is colored.
    r = next(x for x in m["rasters"] if x["region"] == "lower48")
    g = heat.Grid.for_box(h.regions.lower48.west, h.regions.lower48.south, h.regions.lower48.east, h.regions.lower48.north, 4)
    _, _, px = decode_png((tmp_path / "heat" / r["image"]).read_bytes())
    alpha = lambda lon, lat: px[(int(g.y_of(lat)) * g.width + int(g.x_of(lon))) * 4 + 3]  # noqa: E731
    assert alpha(-111.5, 39.5) == 255 and alpha(-100, 39.5) == 0 and alpha(-120, 45) == 0


def test_write_snowfall_skips_unchanged_inputs(tmp_path, heat_cfg):
    h = _small(heat_cfg, 3)
    kw = dict(stations=[heat.Point(-112, 39, 50), heat.Point(-110, 41, 90)], pockets=[], outlines=SQUARE_STATES, heat=h,
              source={"id": "fixture-noaa"}, as_of="2026-10-13")
    first = heat.write_snowfall(tmp_path, generated_at="2026-10-13T00:00:00Z", **kw)
    png_path = tmp_path / "heat" / "snowfall-lower48.png"
    stamp = png_path.stat().st_mtime_ns
    again = heat.write_snowfall(tmp_path, generated_at="2026-10-14T00:00:00Z", **kw)
    assert again == first and png_path.stat().st_mtime_ns == stamp
    changed = heat.write_snowfall(tmp_path, generated_at="2026-10-14T00:00:00Z",
                                  **{**kw, "pockets": [heat.Point(-111, 40, 400)]})
    assert changed["inputs_sha256"] != first["inputs_sha256"] and changed["generated_at"] == "2026-10-14T00:00:00Z"


def test_resort_pockets_use_sourced_annual_snowfall():
    resorts = [
        {"lat": 40.5, "lon": -111.6, "snow": {"annual_avg_in": {"value": 545, "source_id": "s", "as_of": "2026-10-01", "confidence": "published"}}},
        {"lat": 40.6, "lon": -111.5, "snow": {"annual_avg_in": None}},
        {"lat": 40.7, "lon": -111.4, "snow": None},
    ]
    assert heat.resort_pockets(resorts) == [heat.Point(-111.6, 40.5, 545.0)]


def test_build_writes_the_committed_rasters_within_budget():
    """The committed build output: both regions, under about 400 KB each, sourced background."""
    m = json.loads((REPO / "data" / "build" / "heat" / "snowfall.json").read_text())
    assert {r["region"] for r in m["rasters"]} == {"lower48", "alaska"}
    for r in m["rasters"]:
        assert (REPO / "data" / "build" / "heat" / r["image"]).stat().st_size < 400_000
    assert m["background"]["source_id"] == "noaa-ncei-normals-1991-2020" and m["background"]["stations"] > 5000
    sources = {s["id"] for s in json.loads((REPO / "data" / "build" / "sources.json").read_text())["sources"]}
    assert "noaa-ncei-normals-1991-2020" in sources
