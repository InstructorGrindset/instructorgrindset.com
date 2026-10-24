"""Ground heights from terrarium tiles and the road and run painting of the BeamNG generator (tools/beamng).
Synthetic data only."""
import math

import pytest

np = pytest.importorskip("numpy")

from pipeline import png  # noqa: E402
from tools.beamng import elevation, paint, textures  # noqa: E402
from tools.beamng.geo import Grid, LocalFrame  # noqa: E402


def terrarium_tile(z, x, y, height):
    """A 256 px terrarium PNG whose pixel centers carry ``height(lon, lat)`` metres."""
    k = (np.arange(256) + 0.5) / 256
    lon = (x + k[None, :]) / (1 << z) * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi - 2 * math.pi * (y + k[:, None]) / (1 << z))))
    v = height(lon, lat) + 32768
    r = np.floor(v / 256)
    g = np.floor(v - r * 256)
    b = np.rint((v - r * 256 - g) * 256)
    return textures.encode_png(np.stack([r, g, np.minimum(b, 255)], axis=-1).astype(np.uint8))


def test_tile_heights_decode_terrarium_values():
    data = textures.encode_png(np.array([[[128, 0, 0], [129, 4, 128], [100, 0, 0]]], dtype=np.uint8))
    assert elevation.tile_heights(data).tolist() == [[0.0, 260.5, 100 * 256 - 32768]]


def test_zoom_matches_the_grid_step():
    assert elevation.zoom_for(2.0, 39.2) == 15        # finest tiles (3.7 m) for a 2 m grid
    assert elevation.zoom_for(8.0, 39.2) == 14        # 7.4 m tiles are fine enough for 8 m
    assert elevation.zoom_for(100.0, 0.0) == 11


def test_grid_samples_a_planar_slope_with_north_on_top(tmp_path, monkeypatch):
    frame = LocalFrame(-120.25, 39.19)
    grid = Grid(256, 8.0)

    def plane(lon, lat):  # 1000 m at the center, rising 0.1 m per metre north and 0.05 per metre east
        x, y = frame.to_xy(lon, lat)
        return 1000 + 0.1 * y + 0.05 * x

    calls = []

    def fake_tile(directory, z, x, y):
        calls.append((z, x, y))
        return terrarium_tile(z, x, y, plane)

    monkeypatch.setattr(elevation.terrain, "get_tile", fake_tile)
    h, meta = elevation.heights(frame, grid, tmp_path, log=lambda *_: None)
    jj, ii = np.mgrid[0:256, 0:256]
    expect = 1000 + 0.1 * (grid.origin + jj * 8.0) + 0.05 * (grid.origin + ii * 8.0)
    assert np.abs(h - expect).max() < 0.2                  # bilinear of a plane, plus 1/256 m encoding steps
    assert h[-1, 0] > h[0, 0]                              # last row is the north edge
    assert meta["zoom"] == 14 and meta["tiles"] == len(calls) == len(set(calls))


def test_box_blur_keeps_flat_ground_and_the_mean():
    a = np.full((40, 30), 7.0)
    assert np.allclose(elevation.box_blur(a, 3), 7.0)
    rnd = np.random.default_rng(1).standard_normal((64, 64))
    b = elevation.box_blur(rnd, 2)
    assert b.std() < rnd.std() / 3 and abs(b.mean() - rnd.mean()) < 0.05
    assert elevation.box_blur(rnd, 0) is rnd


def test_resample_and_running_mean():
    pts = paint.resample(np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 3.0]]), 4.0)
    steps = np.hypot(*np.diff(pts, axis=0).T)
    assert steps.max() <= 4.0 + 1e-9 and pts[-1].tolist() == [10.0, 3.0] and len(pts) == 5
    chain = np.arange(0, 100, 10.0)
    assert paint.running_mean(np.arange(10.0), chain, 20)[5] == pytest.approx(5.0)
    assert paint.running_mean(np.arange(10.0), chain, 20)[0] == pytest.approx(0.5)


def test_roads_are_leveled_across_and_ground_stays_away_from_them():
    grid = Grid(256, 2.0)
    jj, ii = np.mgrid[0:256, 0:256]
    x = grid.origin + ii * grid.square_m
    ground = 100 + 0.3 * x                                   # 30 percent cross slope, uniform north south
    road = paint.Line("primary", np.array([[0.0, -200.0], [0.0, 200.0]]), 8.0, paint.ASPHALT)
    h = paint.level_roads(grid, ground, [road])
    row = 128
    core = np.abs(x[row]) <= 4.0
    assert np.ptp(h[row, core]) < 1e-6                       # flat across the road
    assert h[row, core][0] == pytest.approx(100.0, abs=1e-6)  # at the ground height of the centerline
    far = np.abs(x[row]) > 4.0 + paint.SHOULDER_M
    assert np.allclose(h[row, far], ground[row, far])
    lay = paint.layers(grid, h, [road])
    assert set(np.unique(lay[row, core])) == {paint.ASPHALT}
    assert lay[row, 0] == paint.GRASS                        # 16.7 degrees is grass


def test_rock_on_steep_ground_and_runs_under_roads():
    grid = Grid(256, 2.0)
    jj, ii = np.mgrid[0:256, 0:256]
    steep = np.where(ii < 128, 0.0, 1.0) * (grid.origin + ii * 2.0) * 1.2    # 50 degrees on the east half
    run = paint.Line("run", np.array([[-100.0, 0.0], [-20.0, 0.0]]), 30.0, paint.SKI_RUN, level=False)
    track = paint.Line("track", np.array([[-60.0, -50.0], [-60.0, 50.0]]), 4.0, paint.DIRT)
    lay = paint.layers(grid, steep, [track, run])
    assert lay[128, 250] == paint.ROCK and lay[128, 10] == paint.GRASS
    assert lay[128, 128 - 40] == paint.SKI_RUN                # x = -80, on the run
    assert lay[128, 128 - 30] == paint.DIRT                   # x = -60, where the track crosses the run


def test_nearest_point_on_roads():
    roads = [paint.Line("service", np.array([[0.0, 0.0], [100.0, 0.0]]), 4.0, paint.ASPHALT),
             paint.Line("run", np.array([[0.0, 5.0], [100.0, 5.0]]), 30.0, paint.SKI_RUN, level=False)]
    dist, point, direction = paint.nearest_on_lines(roads, 40.0, 30.0)
    assert dist == pytest.approx(30.0) and point.tolist() == [40.0, 0.0] and direction.tolist() == [1.0, 0.0]
    assert paint.nearest_on_lines(roads[1:], 0, 0) is None


def test_png_encoder_round_trips_through_the_site_reader():
    rgb = np.random.default_rng(3).integers(0, 256, (5, 7, 3), dtype=np.uint8)
    assert png.decode(textures.encode_png(rgb)) == (7, 5, 3, 8, rgb.tobytes())
    g16 = np.random.default_rng(4).integers(0, 65536, (6, 4)).astype(np.uint16)
    assert png.decode_gray16(textures.encode_png(g16)) == (4, 6, g16.ravel().tolist())
    with pytest.raises(ValueError):
        textures.encode_png(np.zeros((2, 2, 4), np.uint8))


def test_textures_tile_without_seams():
    n = textures.noise(64, 6, 5)
    assert 0 <= n.min() and n.max() <= 1
    # The jump across the wrap edge is no larger than the jumps inside the texture.
    inner = np.abs(np.diff(n, axis=1)).max()
    assert np.abs(n[:, 0] - n[:, -1]).max() <= inner + 1e-9
