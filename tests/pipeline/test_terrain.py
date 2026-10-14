"""Terrain extract for the snowfall heat map (pipeline/terrain.py) and the PNG reader (pipeline/png.py).
Synthetic data only."""
import json
import math
import random
import zlib

import pytest

from pipeline import png, terrain


def test_png_reader_round_trips_rgba_and_16_bit_grey():
    rnd = random.Random(2)
    w, h = 9, 5
    px = bytes(rnd.randrange(256) for _ in range(w * h * 4))
    for ft in range(5):
        assert png.decode(png.encode_rgba(w, h, px, filter_type=ft)) == (w, h, 4, 8, px)
    vals = [rnd.randrange(65536) for _ in range(w * h)]
    for ft in (png.FILTER_NONE, png.FILTER_SUB, png.FILTER_UP, png.FILTER_PAETH):
        assert png.decode_gray16(png.encode_gray16(w, h, vals, filter_type=ft)) == (w, h, vals)
    bad = bytearray(png.encode_gray16(w, h, vals))
    bad[-20] ^= 0xFF
    with pytest.raises(ValueError):
        png.decode(bytes(bad))
    with pytest.raises(ValueError):
        png.decode_gray16(png.encode_rgba(w, h, px))


def test_png_reader_reads_an_rgb_tile():
    w, h = 4, 3
    rgb = bytes(range(w * h * 3))
    raw = b"".join(b"\x00" + rgb[j * w * 3:(j + 1) * w * 3] for j in range(h))
    ihdr = png.struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    data = png.SIGNATURE + png._chunk(b"IHDR", ihdr) + png._chunk(b"IDAT", zlib.compress(raw)) + png._chunk(b"IEND", b"")
    assert png.decode(data) == (w, h, 3, 8, rgb)


def test_terrarium_heights_decode_and_raise_the_sea_floor_to_zero():
    # 128 x 256 + 0 + 0 - 32768 = 0 m; 129 x 256 + 4 + 128 / 256 - 32768 = 260.5 m; 100 x 256 - 32768 < 0.
    rgb = bytes([128, 0, 0, 129, 4, 128, 100, 0, 0])
    assert terrain.terrarium_heights(rgb, 3) == [0.0, 260.5, 0.0]


def test_tiles_cover_the_box_and_their_latitudes_invert():
    keys = terrain.tiles_for_box(-125.0, 24.4, -66.8, 49.5, 6)
    xs, ys = {x for x, _ in keys}, {y for _, y in keys}
    assert xs == set(range(9, 21)) and ys == set(range(21, 28)) and len(keys) == 84
    for lat in (24.4, 40.0, 71.6):
        assert terrain.lat_of_tile_y(terrain.tile_y(lat, 6), 6) == pytest.approx(lat)


class _Box:
    def __init__(self, west, south, east, north):
        self.west, self.south, self.east, self.north = west, south, east, north


def test_average_into_grid_takes_the_mean_of_the_tile_pixels_in_each_cell():
    z = 2
    # One tile, 8 x 8 pixels: height = its column index x 100 m.
    tile = [float(px % 8) * 100 for px in range(64)]
    box = _Box(-180.0, 0.0, -90.0, 66.5)   # tile (0, 1) at zoom 2 spans -180 to -90 and 0 to 66.51 degrees
    w, h, cells = terrain.average_into_grid(box, 45.0, z, {(0, 1): tile}, tile_px=8)
    assert (w, h) == (2, 1)
    # Columns 0 to 3 fall west of -135 (mean 150 m), columns 4 to 7 east of it (mean 550 m).
    assert cells == [pytest.approx(150.0), pytest.approx(550.0)]
    assert terrain.fill_gaps(3, 1, [None, 10.0, None]) == [10.0, 10.0, 10.0]


def _dem(values, w, h, west=-112.0, north=41.0, step=0.5):
    return terrain.Dem(west, north, step, w, h, values)


def test_dem_sample_is_bilinear_between_cell_centers():
    d = _dem([0.0, 100.0, 200.0, 300.0], 2, 2)
    assert d.sample(-111.75, 40.75) == pytest.approx(0.0)        # the first cell center
    assert d.sample(-111.25, 40.25) == pytest.approx(300.0)
    assert d.sample(-111.5, 40.5) == pytest.approx(150.0)        # the middle of the four
    assert d.sample(-111.9, 40.9) == pytest.approx(0.0)          # the edge holds the edge cells
    assert d.sample(-113.0, 40.5) is None and d.sample(-111.5, 42.0) is None


def test_smoothing_keeps_a_flat_grid_and_lowers_a_spike():
    flat = _dem([500.0] * 25, 5, 5)
    assert flat.smoothed(1).values == pytest.approx([500.0] * 25)
    spike = _dem([0.0] * 12 + [900.0] + [0.0] * 12, 5, 5)
    s = spike.smoothed(1)
    assert max(s.values) < 900 and s.values[12] == max(s.values) and min(s.values) >= 0
    assert spike.smoothed(0) is spike


def test_extract_round_trips_through_the_png(tmp_path):
    vals = [0.0, 12.0, 4392.0, 260.0, 3000.0, 1.0]
    data = terrain.encode(3, 2, vals)
    assert terrain.decode(data) == (3, 2, vals)
    (tmp_path / terrain.file_name("lower48")).write_bytes(data)
    (tmp_path / terrain.SOURCE_NAME).write_text(json.dumps({
        "source": {"id": terrain.SOURCE_ID}, "regions": {"lower48": {
            "file": terrain.file_name("lower48"), "west": -112.0, "north": 41.0, "step_deg": 0.5, "width": 3,
            "height": 2}}}))
    dems, src = terrain.load(tmp_path)
    assert src["source"]["id"] == "aws-terrain-tiles" and list(dems) == ["lower48"]
    assert dems["lower48"].values == vals and dems["lower48"].sample(-111.75, 40.75) == pytest.approx(0.0)
    (tmp_path / terrain.SOURCE_NAME).write_text(json.dumps({"source": {}, "regions": {"lower48": {
        "file": terrain.file_name("lower48"), "west": -112.0, "north": 41.0, "step_deg": 0.5, "width": 4,
        "height": 2}}}))
    with pytest.raises(ValueError):
        terrain.load(tmp_path)


def test_committed_extract_covers_both_regions_with_plausible_heights():
    dems, src = terrain.load(terrain.DEFAULT_DIR)
    assert set(dems) == {"lower48", "alaska"} and src["source"]["id"] == terrain.SOURCE_ID
    l48 = dems["lower48"]
    assert l48.sample(-90.0, 30.0) < 100                       # the Gulf coast
    assert 2300 < l48.sample(-106.0, 39.6) < 4000              # the Colorado Rockies
    assert 1200 < dems["alaska"].sample(-150.9, 63.0) < 6200   # the Alaska Range
    for name in ("lower48", "alaska"):
        assert (terrain.DEFAULT_DIR / terrain.file_name(name)).stat().st_size < 600_000
    assert math.isfinite(sum(l48.values))
