"""Local frame, terrain grid and the .ter writer of the BeamNG generator (tools/beamng). Synthetic data only."""
import math
import struct

import pytest

np = pytest.importorskip("numpy")

from tools.beamng import ter  # noqa: E402
from tools.beamng.geo import Grid, LocalFrame, meridian_radius  # noqa: E402


def test_frame_round_trips_and_keeps_true_distances():
    f = LocalFrame(-120.25, 39.19)
    lon, lat = f.to_lonlat([1234.5, -4000.0], [-987.0, 4000.0])
    x, y = f.to_xy(lon, lat)
    assert np.allclose(x, [1234.5, -4000.0], atol=1e-6) and np.allclose(y, [-987.0, 4000.0], atol=1e-6)
    # One degree of latitude at 39.19 N is about 111.0 km on WGS84 (meridian radius x pi / 180).
    assert f.to_xy(-120.25, 40.19)[1] == pytest.approx(meridian_radius(39.19) * math.pi / 180, rel=1e-12)
    assert 110_900 < float(f.to_xy(-120.25, 40.19)[1]) < 111_100
    # East west distance along a parallel: N cos(lat) x pi / 180 per degree, at that point's own latitude.
    x_north, _ = f.to_xy(-120.24, 39.22)
    s = math.sin(math.radians(39.22))
    n = 6378137.0 / math.sqrt(1 - 0.00669437999014 * s * s)
    assert float(x_north) == pytest.approx(0.01 * n * math.cos(math.radians(39.22)) * math.pi / 180, rel=1e-9)


def test_grid_layout_and_bilinear_sample():
    g = Grid(256, 2.0)
    assert g.extent_m == 512 and g.origin == -256
    assert g.axis()[0] == -256 and g.axis()[-1] == 254
    jj, ii = np.mgrid[0:256, 0:256]
    plane = 3.0 * ii + 5.0 * jj           # [j, i]: rises east (i) and north (j)
    x, y = 10.5, -20.25
    i, j = g.index(x, y)
    assert g.sample(plane, x, y) == pytest.approx(3 * i + 5 * j)
    assert g.contains(0, 0) and not g.contains(300, 0)
    for bad in (100, 300, 16384):
        with pytest.raises(ValueError):
            Grid(bad, 1.0)
    with pytest.raises(ValueError):
        Grid(256, 0)


def test_quantize_keeps_metres_within_one_step():
    h = np.linspace(1884.0, 2707.0, 256 * 256).reshape(256, 256)
    q, base, span = ter.quantize(h)
    assert q.dtype == np.dtype("<u2") and base == 1884.0 and span == pytest.approx(823.0)
    back = base + q.astype(float) / 65535 * span
    assert np.abs(back - h).max() <= span / 65535
    q_flat, _, span_flat = ter.quantize(np.full((256, 256), 50.0))
    assert span_flat == 1.0 and q_flat.max() == 0


def test_ter_bytes_follow_the_version_9_layout():
    n = 256
    q = (np.arange(n * n) % 65536).astype("<u2").reshape(n, n)
    layers = (np.arange(n * n) % 3).astype(np.uint8).reshape(n, n)
    names = ["lvl_grass", "lvl_rock", "lvl_asphalt"]
    data = ter.encode(q, layers, names)
    assert struct.unpack_from("<BI", data, 0) == (9, n)
    assert struct.unpack_from("<2H", data, 5) == (0, 1)               # heights, row 0 first, little endian
    assert data[5 + 2 * n * n:5 + 2 * n * n + 3] == bytes([0, 1, 2])  # layer map right after the heights
    tail = data[5 + 3 * n * n:]
    assert tail == struct.pack("<I", 3) + b"".join(bytes([len(s)]) + s.encode() for s in names)
    hq, hl, hn = ter.decode(data)
    assert np.array_equal(hq, q) and np.array_equal(hl, layers) and hn == names


def test_ter_rejects_bad_input():
    q = np.zeros((256, 256), "<u2")
    lay = np.zeros((256, 256), np.uint8)
    with pytest.raises(ValueError):
        ter.encode(q, np.zeros((128, 128), np.uint8), ["a"])
    with pytest.raises(ValueError):
        ter.encode(q, lay + 2, ["a", "b"])
    with pytest.raises(ValueError):
        ter.encode(q, lay, ["x" * 255])
    with pytest.raises(ValueError):
        ter.decode(bytes([8]) + ter.encode(q, lay, ["a"])[1:])


def test_terrain_json_describes_the_file(tmp_path):
    q = np.zeros((256, 256), "<u2")
    ter.write(tmp_path, "theTerrain", "/levels/demo", q, np.zeros((256, 256), np.uint8), ["demo_grass"])
    meta = __import__("json").loads((tmp_path / "theTerrain.terrain.json").read_text())
    assert meta["version"] == 9 and meta["size"] == 256 and meta["heightMapSize"] == 65536
    assert meta["datafile"] == "/levels/demo/theTerrain.ter" and meta["materials"] == ["demo_grass"]
    assert "layerTextureMap" not in meta["binaryFormat"]
    assert (tmp_path / "theTerrain.ter").stat().st_size == 5 + 3 * 65536 + 4 + 1 + len("demo_grass")
