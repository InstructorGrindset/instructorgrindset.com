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


def test_the_underlay_holds_the_alaska_region(heat_cfg):
    """Both draw at Alaska's true place: relief lies under every pixel of the Alaska heat surface."""
    from pipeline.tools import make_underlay as mu
    ak = heat_cfg.regions.alaska
    west, south, east, north = mu.REGIONS["alaska"]["box"]
    assert west <= ak.west and south <= ak.south and east >= ak.east and north >= ak.north


# ------------------------------------------------------------------ the rasters
SQUARE_STATES = {"features": [
    {"properties": {"code": "UT"}, "geometry": {"type": "Polygon", "coordinates": [[[-114, 37], [-109, 37], [-109, 42], [-114, 42], [-114, 37]]]}},
    {"properties": {"code": "AK"}, "geometry": {"type": "Polygon", "coordinates": [[[-150, 60], [-145, 60], [-145, 63], [-150, 63], [-150, 60]]]}},
]}


def _small(heat_cfg, ppd=4):
    return heat_cfg.model_copy(update={"snowfall": heat_cfg.snowfall.model_copy(update={"px_per_degree": ppd})})


def test_write_snowfall_rasters_and_sidecar(tmp_path, heat_cfg):
    h = _small(heat_cfg)
    # A wide pocket, so its rings stay visible at this coarse test resolution.
    h = h.model_copy(update={"snowfall": h.snowfall.model_copy(update={"pocket_radius_km": 25.0})})
    stations = [heat.Point(-114 + i * 0.5, 37 + j * 0.5, 40 + 10 * i) for i in range(11) for j in range(11)]
    stations += [heat.Point(-148, 61, 70), heat.Point(-146, 62.5, 90)]
    pockets = [heat.Point(-111.64, 40.59, 545)]
    m = heat.write_snowfall(tmp_path, stations=stations, pockets=pockets, outlines=SQUARE_STATES, heat=h,
                            source={"id": "fixture-noaa"}, generated_at="2026-10-13T00:00:00Z", as_of="2026-10-13")
    mode = h.modes.snowfall
    assert m["scale"] == "classed" and m["domain"] == {"min": mode.domain[0], "max": mode.domain[1]}
    assert m["breaks"] == mode.breaks and m["colors"] == mode.colors and "ramp" not in m
    assert m["ticks"] == mode.legend_ticks == m["legend"]["ticks"] and m["legend"]["compress_below"] == 180
    assert m["isolines"]["levels"] == h.snowfall.isolines.levels() and m["isolines"]["major_step"] == 100
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
    # Every opaque pixel holds a class color, or a blend of two neighbouring classes on a class edge.
    palette = {heat.hex_rgb(c) for c in mode.colors}
    opaque = [tuple(px[k:k + 3]) for k in range(0, len(px), 4) if px[k + 3] == 255]
    assert sum(c in palette for c in opaque) / len(opaque) > 0.8
    # Isolines: small GeoJSON with one feature per line, rings around the pocket from 200 to 500 in.
    files = {f["region"]: f for f in m["isolines"]["files"]}
    fc = json.loads((tmp_path / "heat" / files["lower48"]["file"]).read_text())
    assert files["lower48"]["features"] == len(fc["features"]) and files["lower48"]["bytes"] < 300_000
    values = {f["properties"]["value"] for f in fc["features"]}
    # At this coarse test resolution (4 px per degree) rings under 4 px long are dropped (min_length_px).
    assert {200, 250} <= values <= set(h.snowfall.isolines.levels()) and 550 not in values
    assert all(f["properties"]["major"] == (f["properties"]["value"] % 100 == 0) for f in fc["features"])
    assert all(f["properties"]["region"] == "lower48" and f["geometry"]["type"] == "LineString" for f in fc["features"])


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


# ------------------------------------------------------------------ classes
def test_class_index_puts_each_break_in_the_class_above():
    breaks = [24, 60, 120, 180, 200]
    assert [heat.class_index(v, breaks) for v in (0, 23.9, 24, 59.99, 60, 180, 199, 200, 900)] == [0, 0, 1, 1, 2, 4, 4, 5, 5]


def test_classed_colorizer_uses_the_class_colors(heat_cfg):
    mode = heat_cfg.modes.snowfall
    color = heat.colorizer(mode, heat_cfg.ramp.colors, (0, 900))
    for v, i in ((0, 0), (23, 0), (24, 1), (180, 4), (249, 5), (250, 6), (899, 18), (2000, 18)):
        assert color(v) == heat.hex_rgb(mode.colors[i]), v
    linear = heat.colorizer(heat_cfg.modes.lucrativeness, heat_cfg.ramp.colors, (0, 10))
    assert linear(0) == heat.hex_rgb(heat_cfg.ramp.colors[0]) and linear(10) == heat.hex_rgb(heat_cfg.ramp.colors[-1])


def test_class_edges_are_antialiased_and_interiors_keep_the_plain_color():
    breaks, colors = [90], [(0, 0, 0), (200, 100, 40)]
    w, h = 6, 3
    values = [40.0 * i for j in range(h) for i in range(w)]  # 90 falls a quarter pixel right of pixel 2's center
    assert heat.class_color_aa(values, w + 0, w, h, breaks, colors) == (0, 0, 0)        # far from the edge
    assert heat.class_color_aa(values, w + 5, w, h, breaks, colors) == (200, 100, 40)
    assert heat.class_color_aa(values, w + 2, w, h, breaks, colors) == (100, 50, 20)    # half of its area above
    assert heat.class_color_aa(values, w + 3, w, h, breaks, colors) == (200, 100, 40)   # wholly above


def test_classed_palettes_separate_neighbours_on_both_bases(heat_cfg):
    from pipeline.color import contrast, delta_e
    bg = re.search(r"background: '(#[0-9a-f]{6})'", (REPO / "map" / "js" / "config.js").read_text()).group(1)
    snow = heat_cfg.modes.snowfall
    # The grey class (little or no snow) stays visible on the dark base and is the configured neutral grey.
    assert snow.colors[0] == heat_cfg.neutral and contrast(snow.colors[0], bg) >= 3.0
    # 180 in and up: every 50 in step differs by 11 or more in OKLab, also under simulated color blindness.
    top = snow.colors[snow.breaks.index(180) + 1:]
    for a, b in zip(top, top[1:]):
        for kind in (None, "protan", "deutan"):
            assert delta_e(a, b, kind) >= 11, (a, b, kind)
    # Every class at 180 in and up stands out from the dark base (and so from the muted imagery it covers).
    assert all(contrast(c, bg) >= 5 for c in top)
    season = heat_cfg.modes.season
    for a, b in zip(season.colors, season.colors[1:]):
        assert delta_e(a, b) >= 10 and min(delta_e(a, b, k) for k in ("protan", "deutan")) >= 8, (a, b)


def test_classed_mode_validation_rejects_bad_palettes(heat_cfg):
    Mode = type(heat_cfg.modes.snowfall)
    with pytest.raises(ValueError):
        Mode.model_validate({"scale": "classed", "breaks": [10, 20], "colors": ["#000000", "#ffffff"]})
    with pytest.raises(ValueError):
        Mode.model_validate({"scale": "classed", "breaks": [10], "colors": ["#666a70", "#676b71"]})
    with pytest.raises(ValueError):
        Mode.model_validate({"scale": "classed", "breaks": [20, 10], "colors": ["#000000", "#808080", "#ffffff"]})


# ------------------------------------------------------------------ fade
def test_paint_fades_to_neutral_and_clears_states_without_data():
    neutral = (102, 106, 112)
    fade = heat.Fade(100, 300, 1.0, 0.6, "neutral", neutral, no_data_reach_km=120)
    color = lambda v: (250, 20, 20)  # noqa: E731
    values = [1.0] * 6
    dist = [50, 200, 1000, 50, 50, 50]
    cov = [1, 1, 1, 1, 1, 0.5]
    no_data = [0, 0, 0, 1, 1, 0]
    border = [0, 0, 0, 30, 500, 0]
    buf, n = heat.paint(values, dist, cov, color, fade, no_data, border)
    px = [tuple(buf[k:k + 4]) for k in range(0, len(buf), 4)]
    assert px[0] == (250, 20, 20, 255)                         # near data: full color
    assert px[1][3] == round(0.8 * 255) and px[1][0] < 250     # mid fade: half way to grey and to the floor
    assert px[2] == (*neutral, round(0.6 * 255))               # far: flat neutral grey at the floor
    assert 0 < px[3][3] < 255                                  # 30 km into a state with no data: partly kept
    assert px[4] == (0, 0, 0, 0) and n == 5                    # deep inside it: clear
    assert px[5][3] == round(0.5 * 255)                        # outline coverage scales the alpha
    assert heat.reach(0, 120) == 1 and heat.reach(120, 120) == 0 and heat.reach(60, 120) == pytest.approx(0.5)


def test_border_distance_counts_km_past_the_states_with_data():
    g = heat.Grid.for_box(-110, 39, -100, 41, 10)
    inside = [1.0 if g.lon_at(i) < -105 else 0.0 for j in range(g.height) for i in range(g.width)]
    d = heat.border_distance(g, inside, 2, [1.0] * (g.width * g.height))
    j = g.height // 2
    row = d[j * g.width:(j + 1) * g.width]
    assert all(v == 0 for v, i in zip(row, range(g.width)) if g.lon_at(i) < -105.3)
    km_per_deg = heat.KM_PER_DEG_LON * math.cos(g.lat_at(j) * heat.RAD)
    i = int(g.x_of(-103.0))
    assert row[i] == pytest.approx((g.lon_at(i) + 105) * km_per_deg, rel=0.08)
    assert all(b >= a for a, b in zip(row, row[1:]))


# ------------------------------------------------------------------ isolines
def _cone(w, h, cx, cy, top, slope):
    return [top - slope * math.hypot(i - cx, j - cy) for j in range(h) for i in range(w)]


def test_contour_lines_trace_a_closed_ring_at_the_right_radius():
    w = h = 41
    values = _cone(w, h, 20, 20, 500, 10)  # 10 per pixel down from 500 at the center
    lines = heat.contour_lines(values, w, h, [350, 400, 600])
    assert lines[600] == [] and len(lines[350]) == 1 and len(lines[400]) == 1
    ring = lines[350][0]
    assert ring[0] == ring[-1]  # closed
    radii = [math.hypot(x - 20.5, y - 20.5) for x, y in ring]
    assert min(radii) > 14.6 and max(radii) < 15.1  # 15 px; on a cone marching squares stays within half a pixel
    # A level that reaches the edge of the data ends there: open lines.
    assert all(line[0] != line[-1] for line in heat.contour_lines(values, w, h, [300])[300])


def test_contour_lines_end_open_at_the_edge_of_the_data():
    w, h = 20, 10
    values = [float(i) for j in range(h) for i in range(w)]  # a ramp from west to east
    values[5 * w + 10] = None                              # a hole cuts the 9.5 line in two
    lines = heat.contour_lines(values, w, h, [9.5])[9.5]
    assert len(lines) == 2 and all(line[0] != line[-1] for line in lines)
    assert all(abs(x - 10.0) < 1e-9 for line in lines for x, _ in line)


def test_contour_lines_resolve_a_saddle_with_the_cell_mean():
    lines = heat.contour_lines([1.0, 0.0, 0.0, 1.0], 2, 2, [0.5])[0.5]
    assert len(lines) == 2  # two separate segments, never crossing
    assert all(len(line) == 2 for line in lines)


def test_refined_isolines_make_round_pocket_rings():
    g = heat.Grid.for_box(-112.5, 40.0, -111.0, 41.0, 20)
    bg = [100.0] * (g.width * g.height)
    pocket = heat.Point(-111.75, 40.5, 500)
    values, _ = heat.apply_pockets(g, bg, [pocket], 25)
    lines = heat.refined_isolines(g, bg, values, [pocket], 25, [300], 4)[300]
    assert len(lines) == 1 and lines[0][0] == lines[0][-1]
    # v = b + (1 - (d / r)^2)^2 (R - b): the 300 in line sits where (1 - (d / r)^2)^2 = 0.5.
    expect = 25 * math.sqrt(1 - math.sqrt(0.5))
    yn, ys = g.y_north, g.y_south
    errs = []
    for x, y in lines[0]:
        lon = g.west + x / g.width * (g.east - g.west)
        lat = heat.lat_of(yn - y / g.height * (yn - ys))
        kmlon = heat.KM_PER_DEG_LON * math.cos(lat * heat.RAD)
        errs.append(abs(math.hypot((lon - pocket.lon) * kmlon, (lat - pocket.lat) * heat.KM_PER_DEG_LAT) - expect))
    coarse = heat.contour_lines(values, g.width, g.height, [300])[300][0]
    assert max(errs) < 0.25 and len(lines[0]) > 2 * len(coarse)  # within 250 m, much finer than the pixels


def test_simplify_keeps_the_ends_and_stays_within_tolerance():
    line = [(x / 10, math.sin(x / 10)) for x in range(200)]
    out = heat.simplify(line, 0.05)
    assert out[0] == line[0] and out[-1] == line[-1] and len(out) < len(line) / 4
    for x, y in line:
        # every dropped point lies within the tolerance of the simplified polyline
        best = min(abs((by - ay) * (x - ax) - (bx - ax) * (y - ay)) / math.hypot(bx - ax, by - ay)
                   for (ax, ay), (bx, by) in zip(out, out[1:]) if ax <= x <= bx or bx <= x <= ax)
        assert best <= 0.05 + 1e-9
    ring = [(math.cos(t / 20 * math.pi), math.sin(t / 20 * math.pi)) for t in range(40)] + [(1.0, 0.0)]
    simple_ring = heat.simplify(ring, 0.01)
    assert simple_ring[0] == simple_ring[-1] and len(simple_ring) >= 8


def test_isoline_features_label_long_lines_and_mark_majors(heat_cfg):
    g = heat.Grid.for_box(-112.5, 40.0, -111.0, 41.0, 20)
    iso = heat_cfg.snowfall.isolines
    lines = {200.0: [[(1, 1), (40, 1)]], 250.0: [[(1, 2), (6, 2)]], 300.0: [[(1, 3), (2, 3)]]}
    feats = heat.isoline_features(g, lines, iso, "lower48")
    by = {f["properties"]["value"]: f for f in feats}
    assert set(by) == {200, 250}  # the 1 px line is dropped (min_length_px)
    assert by[200]["properties"] == {"value": 200, "major": True, "label": True, "region": "lower48"}
    assert by[250]["properties"]["major"] is False and by[250]["properties"]["label"] is False
    (lon0, lat0), (lon1, _) = by[200]["geometry"]["coordinates"]
    assert lon0 == round(g.west + 1 / g.width * (g.east - g.west), 3) and lon1 > lon0 and 40.0 < lat0 < 41.0


# ------------------------------------------------------------------ season length
def _season(length=None, open_=None, close=None, source="s"):
    f = lambda v: None if v is None else {"value": v, "source_id": source, "as_of": "2026-10-01", "confidence": "estimated"}  # noqa: E731
    return {"open_typical": f(open_), "close_typical": f(close), "length_days": f(length), "training_start": None}


def test_season_days_uses_sourced_values_only():
    assert heat.days_open("11-29", "04-19") == 142 and heat.days_open("12-05", "04-15") == 132
    assert heat.days_open("06-01", "06-30") == 30 and heat.days_open("02-29", "03-01") == 2
    assert heat.season_days({"season": _season(length=150, open_="11-01", close="12-01")}) == 150
    assert heat.season_days({"season": _season(open_="11-20", close="03-31")}) == 132
    assert heat.season_days({"season": _season(length=150, source="")}) is None   # no source, no value
    assert heat.season_days({"season": _season(open_="11-20")}) is None           # half a pair
    assert heat.season_days({"season": None}) is None
    pts = heat.season_points([{"lat": 40.5, "lon": -111.6, "state": "ut", "season": _season(length=120)},
                              {"lat": None, "lon": -111.6, "state": "UT", "season": _season(length=120)}])
    assert pts == [(heat.Point(-111.6, 40.5, 120.0), "UT")]


SEASON_STATES = {"features": [
    {"properties": {"code": "UT"}, "geometry": {"type": "Polygon", "coordinates": [[[-114, 37], [-109, 37], [-109, 42], [-114, 42], [-114, 37]]]}},
    {"properties": {"code": "KS"}, "geometry": {"type": "Polygon", "coordinates": [[[-109, 37], [-100, 37], [-100, 42], [-109, 42], [-109, 37]]]}},
    {"properties": {"code": "AK"}, "geometry": {"type": "Polygon", "coordinates": [[[-150, 60], [-145, 60], [-145, 63], [-150, 63], [-150, 60]]]}},
]}


def test_write_season_rasters_from_sourced_lengths(tmp_path, heat_cfg):
    h = heat_cfg.model_copy(update={"season": heat_cfg.season.model_copy(update={"px_per_degree": 6})})
    resorts = [{"lat": 40.6, "lon": -111.6, "state": "UT", "season": _season(length=160)},
               {"lat": 38.5, "lon": -112.5, "state": "UT", "season": _season(open_="12-15", close="03-31")},
               {"lat": 61.0, "lon": -149.0, "state": "AK", "season": _season(length=142)},
               {"lat": 39.0, "lon": -104.0, "state": "KS", "season": _season(length=999, source="")}]
    m = heat.write_season(tmp_path, resorts=resorts, outlines=SEASON_STATES, heat=h, generated_at="2026-10-13T00:00:00Z")
    mode = h.modes.season
    assert m["mode"] == "season" and m["scale"] == "classed" and m["breaks"] == mode.breaks and m["colors"] == mode.colors
    assert m["domain"] == {"min": 107.0, "max": 160.0} and m["values"]["resorts"] == 3  # the unsourced 999 is ignored
    assert m["attribution"] is None and m["ticks"] == mode.legend_ticks
    assert {r["region"] for r in m["rasters"]} == {"lower48", "alaska"}
    l48 = next(r for r in m["rasters"] if r["region"] == "lower48")
    assert l48["no_data_states"] == ["KS"] and l48["resorts"] == 2
    w, hgt, px = decode_png((tmp_path / "heat" / l48["image"]).read_bytes())
    g = heat.Grid.for_box(h.regions.lower48.west, h.regions.lower48.south, h.regions.lower48.east, h.regions.lower48.north, 6)
    at = lambda lon, lat: tuple(px[(int(g.y_of(lat)) * g.width + int(g.x_of(lon))) * 4:][:4])  # noqa: E731
    near = at(-111.6, 40.6)
    assert near == (*heat.hex_rgb(mode.colors[heat.class_index(160, mode.breaks)]), 255)
    assert 0 < at(-108.6, 40.0)[3] < 255      # just inside the state with no data: fading
    assert at(-101.0, 40.0)[3] == 0           # deep inside it: clear
    assert at(-120.0, 40.0)[3] == 0           # outside every outline
    # Alaska: its own resort colors its ski areas.
    ak = next(r for r in m["rasters"] if r["region"] == "alaska")
    _, _, apx = decode_png((tmp_path / "heat" / ak["image"]).read_bytes())
    ga = heat.Grid.for_box(h.regions.alaska.west, h.regions.alaska.south, h.regions.alaska.east, h.regions.alaska.north, 6)
    k = (int(ga.y_of(61.0)) * ga.width + int(ga.x_of(-149.0))) * 4
    assert tuple(apx[k:k + 4]) == (*heat.hex_rgb(mode.colors[heat.class_index(142, mode.breaks)]), 255)
    # Unchanged inputs skip the work; no values at all removes the files.
    again = heat.write_season(tmp_path, resorts=resorts, outlines=SEASON_STATES, heat=h, generated_at="2026-10-14T00:00:00Z")
    assert again == m
    assert heat.write_season(tmp_path, resorts=[], outlines=SEASON_STATES, heat=h, generated_at="x") is None
    assert not list((tmp_path / "heat").glob("season*"))


def test_far_from_resorts_the_season_map_turns_neutral_grey(tmp_path, heat_cfg):
    h = heat_cfg.model_copy(update={"season": heat_cfg.season.model_copy(update={"px_per_degree": 4})})
    wide = {"features": [{"properties": {"code": "MT"}, "geometry": {"type": "Polygon", "coordinates": [
        [[-124, 37], [-100, 37], [-100, 49], [-124, 49], [-124, 37]]]}}]}
    resorts = [{"lat": 48.0, "lon": -123.0, "state": "MT", "season": _season(length=95)}]
    m = heat.write_season(tmp_path, resorts=resorts, outlines=wide, heat=h, generated_at="x")
    r = next(x for x in m["rasters"] if x["region"] == "lower48")
    _, _, px = decode_png((tmp_path / "heat" / r["image"]).read_bytes())
    g = heat.Grid.for_box(h.regions.lower48.west, h.regions.lower48.south, h.regions.lower48.east, h.regions.lower48.north, 4)
    k = (int(g.y_of(38.0)) * g.width + int(g.x_of(-101.0))) * 4   # about 2000 km away
    assert tuple(px[k:k + 4]) == (*heat.hex_rgb(h.neutral), round(h.season.floor_alpha * 255))


def test_season_fade_config_is_checked(heat_cfg):
    Season = type(heat_cfg.season)
    base = heat_cfg.season.model_dump()
    with pytest.raises(ValueError):
        Season.model_validate({**base, "fade_end_km": base["full_opacity_km"]})
    with pytest.raises(ValueError):
        Season.model_validate({**base, "floor_alpha": 1.5})


def test_build_writes_the_committed_season_rasters_from_sourced_values():
    m = json.loads((REPO / "data" / "build" / "heat" / "season.json").read_text())
    resorts = json.loads((REPO / "data" / "build" / "resorts.json").read_text())["resorts"]
    sourced = [r for r in resorts if heat.season_days(r) is not None and r.get("lat") is not None]
    assert m["values"]["resorts"] == len(sourced) > 300
    assert {r["region"] for r in m["rasters"]} == {"lower48", "alaska"}
    for r in m["rasters"]:
        assert (REPO / "data" / "build" / "heat" / r["image"]).stat().st_size < 400_000
    # Alaska's ski areas with a sourced season all sit inside the Alaska raster.
    ak = next(r for r in m["rasters"] if r["region"] == "alaska")
    assert ak["resorts"] == sum(1 for r in sourced if r["state"] == "AK") >= 4
    snow = json.loads((REPO / "data" / "build" / "heat" / "snowfall.json").read_text())
    assert {f["region"] for f in snow["isolines"]["files"]} == {"lower48", "alaska"}
    for f in snow["isolines"]["files"]:
        assert (REPO / "data" / "build" / "heat" / f["file"]).stat().st_size < 300_000 and f["features"] > 0


def test_snowfall_region_colors_each_pixel_by_its_class(heat_cfg):
    box = heat_cfg.regions.lower48.model_copy(update={"west": -112.5, "east": -111.0, "south": 40.0, "north": 41.0})
    g = heat.Grid.for_box(box.west, box.south, box.east, box.north, 40)
    ring = [(box.west, box.south), (box.east, box.south), (box.east, box.north), (box.west, box.north), (box.west, box.south)]
    stations = [heat.Point(-112.5 + i * 0.25, 40 + j * 0.25, 80) for i in range(7) for j in range(5)]
    pocket = heat.Point(-111.64, 40.59, 545)
    mode = heat_cfg.modes.snowfall
    rgba, info = heat.render_region(g, stations=stations, pockets=[pocket], polygons=[[ring]], heat=heat_cfg,
                                    domain=(0, 900), scale="classed", box=box)
    k = int(g.y_of(pocket.lat)) * g.width + int(g.x_of(pocket.lon))
    assert info["values"][k] == pytest.approx(545, abs=5) and info["background"][k] == pytest.approx(80)
    assert tuple(rgba[4 * k:4 * k + 4]) == (*heat.hex_rgb(mode.colors[heat.class_index(info["values"][k], mode.breaks)]), 255)
    far = 2 * g.width + 2
    assert tuple(rgba[4 * far:4 * far + 3]) == heat.hex_rgb(mode.colors[heat.class_index(80, mode.breaks)])


# ------------------------------------------------------------------ SNOTEL blend and the elevation aware field
def _elev_cfg(heat_cfg, **kw):
    return heat_cfg.snowfall.elevation.model_copy(update=kw)


def _nbrs(points, lon=-111.0, lat=40.0):
    idx = heat.PointIndex(points, 0.5)
    return idx.nearest_points(lon, lat, len(points))


def test_gids_fit_finds_the_height_gradient_and_reads_it_at_any_height(heat_cfg):
    """Stations whose log snowfall rises 1.2 per km of height: the fit recovers the slope, and the field at a
    height between the stations follows it instead of the distance weighted mean."""
    c = 10.0
    pts = []
    for n in range(12):
        z = 1.5 + 0.2 * n  # 1.5 to 3.7 km
        lon, lat = -111.0 + 0.05 * math.cos(n), 40.0 + 0.05 * math.sin(n)
        pts.append(heat.Point(lon, lat, math.exp(3.0 + 1.2 * z) - c, z * 1000))
    nb = _nbrs(pts)
    fit = heat.gids_fit(nb, 10, 1.0, c, 2.0, 0.05, 0.0)
    assert fit.beta == pytest.approx(1.2, rel=0.03)
    hi, lo = heat.gids_value(fit, 3.5, c), heat.gids_value(fit, 1.7, c)
    assert hi == pytest.approx(math.exp(3.0 + 1.2 * 3.5) - c, rel=0.06)
    assert lo == pytest.approx(math.exp(3.0 + 1.2 * 1.7) - c, rel=0.06)
    # Clamped: above every neighbour the field stays at the highest neighbour's height and value.
    top = max(p.value for _, p in nb[:10])
    assert heat.gids_value(fit, 9.0, c) <= top + 1e-6
    assert heat.gids_value(fit, -1.0, c) >= min(p.value for _, p in nb[:10]) - 1e-6


def test_gids_fit_without_height_spread_is_the_plain_shepard_value(heat_cfg):
    rnd = random.Random(3)
    pts = [heat.Point(-111 + rnd.uniform(-1, 1), 40 + rnd.uniform(-1, 1), rnd.uniform(20, 300), 1500.0)
           for _ in range(30)]
    nb = _nbrs(pts, -110.9, 40.1)
    fit = heat.gids_fit(nb[:11], 10, 12.0, 10.0, 2.0, 0.15, 0.0)
    assert abs(fit.beta) < 1e-12
    plain = heat.shepard([(d, p.value) for d, p in nb[:11]], 10, 12.0)
    assert heat.gids_value(fit, 3.0, 10.0) == pytest.approx(plain, rel=1e-9)


def test_gids_gradient_never_turns_negative_and_shrinks_with_a_small_height_span():
    c = 10.0
    falling = [heat.Point(-111 + 0.01 * n, 40, 300 - 20 * n, 1500 + 200 * n) for n in range(8)]
    assert heat.gids_fit(_nbrs(falling), 6, 1.0, c, 2.0, 0.15, 0.0).beta == 0.0
    steep = [heat.Point(-111 + 0.01 * n, 40, math.exp(2 + 5 * (1.5 + 0.2 * n)) - c, 1500 + 200 * n) for n in range(8)]
    assert heat.gids_fit(_nbrs(steep), 6, 1.0, c, 2.0, 0.15, 0.0).beta == 2.0  # clamped to the maximum
    flat_span = [heat.Point(-111 + 0.01 * n, 40, math.exp(2 + 1.2 * (1.5 + 0.005 * n)) - c, 1500 + 5 * n)
                 for n in range(8)]
    assert heat.gids_fit(_nbrs(flat_span), 6, 1.0, c, 2.0, 0.15, 0.0).beta < 0.01


def _ridge(lon):
    """A north south ridge 3 km high at -111.0, 1 km in the valleys."""
    return 1000 + 2000 * max(0.0, 1 - abs(lon + 111.0) / 0.3)


def _Dem(west=-115.0, north=43.0, east=-107.0, south=36.0, step=1 / 32):
    from pipeline.terrain import Dem
    w, hgt = round((east - west) / step), round((north - south) / step)
    return Dem(west, north, step, w, hgt, [_ridge(west + (i + 0.5) * step) for _ in range(hgt) for i in range(w)])


def test_elevation_field_follows_the_terrain_between_stations(heat_cfg):
    g = heat.Grid.for_box(-112.0, 39.5, -110.0, 40.5, 16)
    cov = [1.0] * (g.width * g.height)
    dem = _Dem()
    stations = []
    for k in range(9):
        lat = 39.55 + 0.11 * k
        stations += [heat.Point(-111.0, lat, 400, 3000), heat.Point(-111.9, lat, 40, 1000),
                     heat.Point(-110.1, lat, 40, 1000)]
    elev = _elev_cfg(heat_cfg, spread_prior_m=100, max_extrapolation_m=0)
    heights = heat.terrain_heights(g, dem, cov)
    vals, dist, betas = heat.solve_elevation_field(g, cov, stations, g, 10, 5.0, 4, heights, elev)
    plain, _ = heat.solve_field(g, cov, stations, g, 10, 5.0, 4)
    j = g.height // 2
    row = lambda f: f[j * g.width:(j + 1) * g.width]  # noqa: E731
    i_ridge, i_flank = int(g.x_of(-111.0)), int(g.x_of(-111.45))
    # On the ridge the field holds the high stations; on the low flank, closer to the valley stations than the
    # plain interpolation suggests, it drops with the ground.
    assert row(vals)[i_ridge] == pytest.approx(400, rel=0.1)
    assert row(vals)[i_flank] < row(plain)[i_flank]
    assert all(40 - 1e-6 <= v <= 400 + 1e-6 for v in vals if v is not None)
    assert max(b for b in betas if b is not None) > 0.5
    assert all(d is not None and d >= 0 for d in dist)


def test_terrain_pockets_never_lower_the_field_and_fade_below_the_resort():
    g = heat.Grid.for_box(-112.5, 40.0, -111.0, 41.0, 40)
    n = g.width * g.height
    rnd = random.Random(11)
    bg = [rnd.uniform(50, 500) for _ in range(n)]
    heights = [rnd.uniform(1.2, 3.5) for _ in range(n)]
    grads = [rnd.uniform(0, 2) for _ in range(n)]
    terrain = heat.PocketTerrain(heights, grads, 10.0)
    pockets = [heat.Point(-111.64, 40.59, 545), heat.Point(-111.9, 40.4, 420), heat.Point(-112.2, 40.8, 60)]
    out, near = heat.apply_pockets(g, bg, pockets, 20, terrain, [2.8, 2.5, 1.5])
    assert all(o >= b for o, b in zip(out, bg)) and any(o > b for o, b in zip(out, bg))
    assert all(o <= max(b, 545) + 1e-9 for o, b in zip(out, bg))
    # The pocket target: the resort's value at or above its height, lower below it along the gradient.
    assert heat.pocket_target(545, 2.8, 3.0, 1.2, 10) == 545
    assert heat.pocket_target(545, 2.8, 1.8, 1.2, 10) == pytest.approx(555 * math.exp(-1.2) - 10)
    assert heat.pocket_target(545, 2.8, None, 1.2, 10) == 545 and heat.pocket_target(545, None, 1.0, 1.2, 10) == 545
    # Same resort, same distance: a valley pixel rises less than a slope pixel.
    flat = [200.0] * n
    k_valley = int(g.y_of(40.59)) * g.width + int(g.x_of(-111.64)) + 2
    hv = list(heights); hv[k_valley] = 1.5
    gv = [1.2] * n
    valley, _ = heat.apply_pockets(g, flat, pockets[:1], 20, heat.PocketTerrain(hv, gv, 10.0), [2.8])
    hs = list(heights); hs[k_valley] = 2.9
    slope, _ = heat.apply_pockets(g, flat, pockets[:1], 20, heat.PocketTerrain(hs, gv, 10.0), [2.8])
    assert 200 <= valley[k_valley] < slope[k_valley]


def test_refined_isolines_read_the_terrain_pockets_like_the_raster():
    g = heat.Grid.for_box(-112.5, 40.0, -111.0, 41.0, 20)
    n = g.width * g.height
    bg = [100.0] * n
    pocket = heat.Point(-111.75, 40.5, 500)
    heights = [2.0 + 0.8 * math.sin(i / 3.0) for i in range(n)]
    terrain = heat.PocketTerrain(heights, [1.0] * n, 10.0)
    values, _ = heat.apply_pockets(g, bg, [pocket], 25, terrain, [2.5])
    lines = heat.refined_isolines(g, bg, values, [pocket], 25, [300], 2, terrain, [2.5])[300]
    coarse = heat.contour_lines(values, g.width, g.height, [300])[300]
    assert lines and coarse
    # Without the gradient the terrain pocket is the plain round pocket.
    flat = heat.PocketTerrain(heights, [0.0] * n, 10.0)
    plain, _ = heat.apply_pockets(g, bg, [pocket], 25)
    same, _ = heat.apply_pockets(g, bg, [pocket], 25, flat, [2.5])
    assert same == plain


def test_write_snowfall_blends_snotel_stations_and_records_the_sources(tmp_path, heat_cfg):
    h = _small(heat_cfg)
    noaa = [heat.Point(-114 + i * 0.5, 37 + j * 0.5, 40 + 5 * i, 1500.0) for i in range(11) for j in range(11)]
    noaa += [heat.Point(-148, 61, 70, 100.0), heat.Point(-146, 62.5, 90, 300.0)]
    snotel = [heat.Point(-111.6, 40.6, 420, 2900.0), heat.Point(-111.2, 39.9, 260, 2700.0),
              heat.Point(-147, 61.5, 300, 900.0)]
    dems = {"lower48": _Dem(), "alaska": _Dem(-151.0, 64.0, -144.0, 59.0, 1 / 8)}
    kw = dict(stations=noaa, pockets=[heat.Point(-111.64, 40.59, 545)], outlines=SQUARE_STATES, heat=h,
              source={"id": "fixture-noaa"}, generated_at="2026-10-13T00:00:00Z", as_of="2026-10-13")
    plain = heat.write_snowfall(tmp_path / "plain", **kw)
    m = heat.write_snowfall(tmp_path / "blend", mountain_stations=snotel,
                            mountain_source={"id": "fixture-snotel", "accessed_date": "2026-10-13",
                                             "water_years": [2011, 2025]},
                            dems=dems, terrain_source={"id": "fixture-terrain", "accessed_date": "2026-10-13"}, **kw)
    assert plain["attribution"] == heat.NOAA_ATTRIBUTION and "mountain_stations" not in plain
    assert m["attribution"] == "Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL"
    assert m["background"]["stations"] == len(noaa) and m["background"]["confidence"] == "published"
    assert m["mountain_stations"] == {"source_id": "fixture-snotel", "as_of": "2026-10-13", "confidence": "estimated",
                                      "field": "annual_snowfall_in",
                                      "method": "median of water year totals of daily SWE gain / new snow density",
                                      "water_years": [2011, 2025], "stations": 3}
    assert m["terrain"]["source_id"] == "fixture-terrain" and m["pockets"]["terrain"] is True
    assert m["method"]["interpolation"].startswith("gradient plus inverse distance")
    assert m["method"]["elevation"]["log_offset_in"] == h.snowfall.elevation.log_offset_in
    assert {r["region"]: r["mountain_stations"] for r in m["rasters"]} == {"lower48": 2, "alaska": 1}
    assert all(r["terrain"] for r in m["rasters"]) and m["inputs_sha256"] != plain["inputs_sha256"]
    # The blend changes the picture: the mountain stations raise the field around them.
    a = (tmp_path / "plain" / "heat" / "snowfall-lower48.png").read_bytes()
    b = (tmp_path / "blend" / "heat" / "snowfall-lower48.png").read_bytes()
    assert a != b
    # Without terrain the SNOTEL stations still blend in, with plain station interpolation.
    flat = heat.write_snowfall(tmp_path / "flat", mountain_stations=snotel,
                               mountain_source={"id": "fixture-snotel", "accessed_date": "2026-10-13"}, **kw)
    assert flat["method"]["interpolation"] == "adaptive modified Shepard" and "terrain" not in flat
    assert flat["mountain_stations"]["stations"] == 3 and flat["pockets"]["terrain"] is False


def test_build_blends_the_committed_snotel_extract():
    """The committed snowfall manifest names the NRCS SNOTEL stations and the terrain."""
    m = json.loads((REPO / "data" / "build" / "heat" / "snowfall.json").read_text())
    assert m["attribution"] == "Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL"
    assert m["mountain_stations"]["source_id"] == "nrcs-snotel-awdb-annual-snowfall"
    assert m["mountain_stations"]["confidence"] == "estimated" and m["mountain_stations"]["stations"] > 500
    assert m["terrain"]["source_id"] == "aws-terrain-tiles"
    sources = {s["id"] for s in json.loads((REPO / "data" / "build" / "sources.json").read_text())["sources"]}
    assert {"nrcs-snotel-awdb-annual-snowfall", "aws-terrain-tiles"} <= sources


def test_raise_field_keeps_the_larger_value_and_its_gradient():
    vals, grads = heat.raise_field([100.0, 300.0, None, 50.0], [150.0, 200.0, 80.0, None], [0.1, 0.2, None, 0.4],
                                   [1.1, 1.2, 1.3, None])
    assert vals == [150.0, 300.0, 80.0, 50.0] and grads == [1.1, 0.2, 1.3, 0.4]


def test_resort_points_spread_along_the_terrain_and_never_lower_the_field(heat_cfg):
    """With resort points on, every pixel is at least the stations' own field (pockets off to isolate the step),
    and a high resort lifts the ridge around it more than the valley beside it."""
    box = heat_cfg.regions.lower48.model_copy(update={"west": -112.0, "east": -110.0, "south": 39.5, "north": 40.5})
    g = heat.Grid.for_box(box.west, box.south, box.east, box.north, 16)
    ring = [(box.west, box.south), (box.east, box.south), (box.east, box.north), (box.west, box.north), (box.west, box.south)]
    stations = []
    for k in range(9):
        lat = 39.55 + 0.11 * k
        stations += [heat.Point(-111.0, lat, 250, 3000), heat.Point(-111.9, lat, 40, 1000),
                     heat.Point(-110.1, lat, 40, 1000)]
    resorts = [heat.Point(-111.0, 40.0, 600), heat.Point(-111.6, 39.7, 20)]
    dem = _Dem()

    def run(resort_points):
        snow = heat_cfg.snowfall.model_copy(update={
            "elevation": heat_cfg.snowfall.elevation.model_copy(update={"resort_points": resort_points}),
            "pocket_radius_km": 5.0})
        h = heat_cfg.model_copy(update={"snowfall": snow})
        # A pocket radius of 5 km at 16 px per degree reaches no pixel center beyond the resort's own.
        _, info = heat.render_region(g, stations=stations, pockets=resorts, polygons=[[ring]], heat=h,
                                     domain=(0, 900), scale="classed", box=box, dem=dem)
        return info["background"]

    plain, joined = run(False), run(True)
    assert all(j >= p - 1e-9 for p, j in zip(plain, joined) if p is not None)
    j = int(g.y_of(40.0))
    ridge = j * g.width + int(g.x_of(-111.0)) + 3
    valley = j * g.width + int(g.x_of(-111.6))
    assert joined[ridge] - plain[ridge] > joined[valley] - plain[valley] >= 0
