"""Open snowkite ground (pipeline/snowkite_terrain.py): the pure raster and kite size parts on synthetic arrays."""

from __future__ import annotations

import math
import struct
import zlib

import pytest

np = pytest.importorskip("numpy")

from pipeline import snowkite_terrain as st  # noqa: E402

CFG = st.SNOWKITE_TERRAIN


def plane(h: int, w: int, px_m: float, slope_deg: float, faces_deg: float):
    """A tilted plane whose downhill direction is ``faces_deg`` (rows from the north)."""
    rows, cols = np.mgrid[0:h, 0:w]
    east, north = cols * px_m, -rows * px_m
    a = math.radians(faces_deg)
    return 1000.0 - math.tan(math.radians(slope_deg)) * (east * math.sin(a) + north * math.cos(a))


def cover_of(h: int, w: int, name: str = "grass"):
    cover = np.zeros((h, w, len(st.COVER_NAMES)), np.float32)
    cover[:, :, st.COVER_NAMES.index(name)] = 1.0
    return cover


# ------------------------------------------------------------------ slope and aspect
@pytest.mark.parametrize("slope,faces", [(5, 0), (12, 90), (20, 180), (8, 270), (15, 45), (3, 225)])
def test_slope_and_aspect_of_a_tilted_plane(slope, faces):
    s, a = st.slope_aspect(plane(20, 24, 100.0, slope, faces), 100.0)
    assert np.allclose(s, slope, atol=1e-6)
    assert np.allclose(st.angle_between(a, faces), 0, atol=1e-6)


def test_steepest_neighbor_slope_sees_a_cliff_the_gradient_smooths():
    z = np.zeros((7, 7))
    z[:, 4:] = 100.0                                     # a 100 m step between two columns 100 m apart
    assert st.steepest_neighbor_slope(z, 100.0)[3, 3] == pytest.approx(45.0)
    assert st.slope_aspect(z, 100.0)[0][3, 3] == pytest.approx(math.degrees(math.atan(0.5)))


# ------------------------------------------------------------------ patches
def test_label_patches_joins_edges_and_keeps_corners_apart():
    mask = np.array([
        [1, 1, 0, 0, 1],
        [0, 1, 0, 1, 1],
        [0, 0, 1, 0, 0],      # touches both blocks at corners only
        [1, 0, 1, 0, 1],
        [1, 1, 1, 0, 1],
    ], bool)
    labels, count = st.label_patches(mask)
    assert count == 4
    assert (labels > 0).tolist() == mask.tolist()
    assert labels[0, 0] == labels[1, 1] != labels[0, 4]
    assert labels[0, 4] == labels[1, 3]
    assert labels[2, 2] == labels[4, 0] == labels[3, 2]          # a U shape that merges late
    assert labels[3, 4] == labels[4, 4] != labels[2, 2]
    assert sorted(np.unique(labels).tolist()) == [0, 1, 2, 3, 4]


def test_label_patches_handles_empty_and_full():
    assert st.label_patches(np.zeros((3, 4), bool))[1] == 0
    labels, count = st.label_patches(np.ones((3, 4), bool))
    assert count == 1 and (labels == 1).all()


def test_outline_and_simplify_trace_the_outer_ring():
    mask = np.zeros((6, 8), bool)
    mask[1:5, 2:7] = True
    mask[2, 4] = False                                   # a hole is ignored
    ring = st.outline(mask)
    assert len(ring) == 18 and len(set(ring)) == 18
    simple = st.simplify_ring(ring, 40)
    assert sorted(simple) == [(2.0, 1.0), (2.0, 5.0), (7.0, 1.0), (7.0, 5.0)]
    rr, cc = np.mgrid[0:60, 0:60]
    disc = (rr - 30) ** 2 + (cc - 30) ** 2 <= 25 ** 2
    assert 8 <= len(st.simplify_ring(st.outline(disc), 12)) <= 12


# ------------------------------------------------------------------ wind
def test_wind_extents_follow_the_wind_direction():
    rows, cols = np.mgrid[0:10, 0:40]                    # 4 km east to west, 1 km north to south
    east, north = cols.ravel() * 100.0, -rows.ravel() * 100.0
    assert st.wind_extents(east, north, 270.0, 100.0) == pytest.approx((4000.0, 1000.0))    # west wind: along the long side
    assert st.wind_extents(east, north, 0.0, 100.0) == pytest.approx((1000.0, 4000.0))      # north wind: across it
    along, across = st.wind_extents(east, north, 315.0, 0.0)
    assert along == pytest.approx(across) == pytest.approx((3900 + 900) / math.sqrt(2))


def test_upslope_share_counts_ground_facing_the_wind():
    aspects = np.array([270, 300, 215, 330, 331, 90, 180, 0])
    assert st.upslope_share(aspects, 270.0, 60.0) == pytest.approx(4 / 8)
    assert st.upslope_share(np.array([350, 10, 60, 61]), 0.0, 60.0) == pytest.approx(3 / 4)
    assert st.upslope_share(np.array([]), 0.0, 60.0) is None
    assert st.aspect_histogram(np.array([0, 350, 44, 90, 200])) == [2, 1, 1, 0, 1, 0, 0, 0]


def test_tree_shadow_lies_downwind_only():
    tree = np.zeros((9, 15), bool)
    tree[4, 5] = True
    west = st.tree_shadow(tree, 270.0, 100.0, 300.0)     # wind from the west: shadow to the east
    assert np.flatnonzero(west[4]).tolist() == [6, 7, 8] and west.sum() == 3
    north = st.tree_shadow(tree, 0.0, 100.0, 300.0)      # wind from the north: shadow to the south
    assert np.flatnonzero(north[:, 5]).tolist() == [5, 6, 7] and north.sum() == 3


def test_runout_reaches_below_steep_ground_only():
    z = np.tile(np.array([0] * 14 + [100, 300, 500, 500], float), (5, 1))
    steep = st.steepest_neighbor_slope(z, 100.0) >= 30.0
    assert steep[2].tolist() == [False] * 13 + [True] * 4 + [False]
    out = st.runout_mask(z, steep, 100.0, 1000.0, 20.0)
    # the 500 m top would reach 500 / tan(20) = 1.37 km; the rule caps the reach at 1 km (10 pixels from column 16)
    assert out[2, 6:14].all() and not out[2, :6].any()
    assert not out[2, 17]                                # the plateau on top lies under nothing
    assert not st.runout_mask(z, np.zeros_like(steep), 100.0, 1000.0, 20.0).any()


# ------------------------------------------------------------------ ground, metrics, classes
def test_ground_mask_rules():
    h, w = 12, 12
    cover = cover_of(h, w)
    cover[0, :, :] = 0
    cover[0, :, st.TREE] = 1.0                           # a row of trees along the north edge
    cover[5, 5, :] = 0
    cover[5, 5, st.WATER] = 1.0
    cover[6, 6, :] = 0
    cover[6, 6, st.BUILT] = 1.0
    flat = np.full((h, w), 1500.0)
    g = st.ground(flat, cover, 100.0, False, 0.0, CFG)
    assert not g["kiteable"][0].any() and not g["kiteable"][5, 5] and not g["kiteable"][6, 6]
    assert g["kiteable"].sum() == h * w - w - 2
    assert g["shadow"][1:4].all() and not g["shadow"][4:].any()          # north wind: 300 m south of the trees
    assert st.ground(flat, cover, 100.0, True, 0.0, CFG)["kiteable"][5, 5]        # lake ice counts when reliable
    lake_only = st.ground(flat, cover, 100.0, True, 0.0, CFG, snow_ok=False)["kiteable"]
    assert lake_only.sum() == 1 and lake_only[5, 5]
    sea = st.ground(np.zeros((h, w)), cover, 100.0, True, 0.0, CFG)["kiteable"]
    assert not sea[5, 5]                                                 # water at sea level is the sea
    box = st.cell_box({"lat": 47.0, "lon": -88.0, "step": 0.25})          # Lake Superior
    level = np.full((h, w), 179.0)
    level[0, 0] = 300.0
    great = st.open_water_mask(level, box[0], box[1], box[2])
    assert great.sum() == h * w - 1 and not great[0, 0]
    assert not st.ground(level, cover, 100.0, True, 0.0, CFG, open_water=great)["kiteable"][5, 5]
    assert not st.open_water_mask(level, *st.cell_box({"lat": 47.0, "lon": -110.0, "step": 0.25})[:3]).any()
    too_steep = st.ground(plane(h, w, 100.0, 27, 180), cover_of(h, w), 100.0, False, 0.0, CFG)
    assert not too_steep["kiteable"].any()


def test_patch_metrics_and_classes_on_a_windward_slope():
    h, w = 30, 40
    elev = plane(h, w, 100.0, 15, 270)                   # faces west, 15 degrees
    cover = cover_of(h, w)
    g = st.ground(elev, cover, 100.0, False, 270.0, CFG)
    sel = np.zeros((h, w), bool)
    sel[5:25, 5:35] = True
    m = st.patch_metrics(sel, elev, cover, g, 100.0, 270.0, 90.0, CFG)
    assert m["acres"] == pytest.approx(600 * 100 * 100 / st.ACRE_M2)
    assert m["slope_share"] == pytest.approx([0, 0, 1])
    assert m["relief_m"] == pytest.approx(29 * 100 * math.tan(math.radians(15)))
    assert (m["along_m"], m["across_m"]) == pytest.approx((3000.0, 2000.0))
    assert m["shape_ratio"] == pytest.approx(2 / 3)
    assert m["upslope_share"] == 1.0 and m["upslope_share_second"] == 0.0
    assert m["aspect_10_25"][6] == 600 and m["north_east_share"] == 0.0
    assert m["classes"] == ["freeride"] and m["surface"] == "land"
    assert m["flat_core_acres"] == 0.0


def test_classify_and_size_rating():
    base = {"acres": 500.0, "slope_share": [0.9, 0.1, 0.0], "tree_shadow_share": 0.02, "relief_m": 20.0, "upslope_share": None}
    assert st.classify(base) == ["beginner"]
    assert st.classify({**base, "tree_shadow_share": 0.4}) == ["advanced"]            # flat but turbulent
    assert st.classify({**base, "acres": 100.0}) == ["advanced"]                     # flat but tight
    assert st.classify({**base, "slope_share": [0.3, 0.6, 0.1]}) == ["advanced"]
    mixed = {**base, "slope_share": [0.3, 0.3, 0.4], "relief_m": 400.0, "upslope_share": 0.5,
             "flat_core_acres": 300.0, "flat_core_shadow_share": 0.0}
    assert st.classify(mixed) == ["beginner", "freeride"]
    assert st.classify({**mixed, "upslope_share": 0.2, "flat_core_acres": 0.0}) == ["advanced"]
    assert [st.size_rating(a) for a in (39, 40, 160, 640, 2500, 25000)] == [None, "minimum", "enough", "good", "excellent", "expedition"]


# ------------------------------------------------------------------ kite size
def test_kite_size_calibration():
    lo, typical, hi = st.kite_size_m2(13.5 * st.MPH, 0.0, 0.0)
    assert typical == pytest.approx(10.5)
    assert 8.5 < lo < 9.5 and 11.5 < hi < 12.5
    assert 8.0 < st.kite_size_m2(15 * st.MPH)[1] < 9.0 and 13.0 < st.kite_size_m2(12 * st.MPH)[1] < 14.0
    assert 0.25 < st.kite_pull_fraction() < 0.35
    assert st.kite_size_m2(6.0, rider_kg=100.0)[1] == pytest.approx(st.kite_size_m2(6.0)[1] * 1.25)
    with pytest.raises(ValueError):
        st.kite_size_m2(0.0)


def test_kite_size_is_monotonic_in_wind_slope_and_elevation():
    winds = [st.kite_size_m2(v, 5.0, 1500.0)[1] for v in (3, 4, 6, 8, 11)]
    assert winds == sorted(winds, reverse=True) and len(set(winds)) == 5
    slopes = [st.kite_size_m2(6.0, s, 1500.0)[1] for s in (0, 3, 10, 20, 25)]
    assert slopes == sorted(slopes) and len(set(slopes)) == 5
    heights = [st.kite_size_m2(6.0, 5.0, e)[1] for e in (0, 1000, 2000, 3000)]
    assert heights == sorted(heights) and len(set(heights)) == 4
    assert st.air_density(0) == pytest.approx(1.225)
    assert st.air_density(3000) == pytest.approx(0.909, abs=0.002)


# ------------------------------------------------------------------ ERA5 cells
def extract_of(cells: list[dict]) -> dict:
    return {"grid": {"step_deg": 0.25, "lat_max": 72.0, "lon_min": -170.0},
            "speed_edges_ms": list(range(16)), "snow_edges_m_we": [0, 0.005, 0.02, 0.05, 0.1, 0.3],
            "ice_edges_m": [0, 0.1, 0.2, 0.3, 0.5],
            "cells": {k: [c[k] for c in cells] for k in ("row", "col", "state", "speed", "dir", "snow", "ice")}}


def test_candidate_cells_apply_the_wind_snow_and_ice_rule():
    windy = [0, 0, 10, 20, 20, 20, 10, 5, 5, 5, 3, 2, 0, 0, 0, 0]       # 88 percent in 3 to 11 m/s
    calm = [50, 30, 10, 5, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    snowy, bare = [0, 10, 40, 30, 20, 0], [70, 20, 10, 0, 0, 0]
    icy, thin = [0, 10, 20, 30, 40], [80, 20, 0, 0, 0]
    dirs = [1, 2, 3, 4, 5, 6, 90, 40]
    cells = [
        {"row": 100, "col": 200, "state": "WY", "speed": windy, "dir": dirs, "snow": snowy, "ice": thin},
        {"row": 100, "col": 201, "state": "WY", "speed": calm, "dir": dirs, "snow": snowy, "ice": icy},
        {"row": 101, "col": 200, "state": "ND", "speed": windy, "dir": dirs, "snow": bare, "ice": icy},
        {"row": 101, "col": 201, "state": "NE", "speed": windy, "dir": dirs, "snow": bare, "ice": thin},
    ]
    out = st.candidate_cells(extract_of(cells))
    assert [(c["row"], c["col"]) for c in out] == [(100, 200), (101, 200)]       # best first
    first = out[0]
    assert first["snow_ok"] and not first["ice_ok"] and not out[1]["snow_ok"] and out[1]["ice_ok"]
    assert (first["lat"], first["lon"]) == (47.0, -120.0)
    assert (st.SECTORS[first["dir"]], st.SECTORS[first["dir2"]]) == ("W", "NW")
    assert 4.0 < first["median_wind_ms"] < 6.0
    assert st.median_usable_wind([0, 0, 0, 10, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], list(range(16)), 3, 11) == pytest.approx(4.0)


# ------------------------------------------------------------------ grid, land cover reader, joins
def test_cell_boxes_share_edges_and_cover_shares_fill_pixels():
    a = {"lat": 45.0, "lon": -110.0, "step": 0.25}
    b = {"lat": 45.0, "lon": -109.75, "step": 0.25}
    za, ax0, ay0, ax1, ay1 = st.cell_box(a)
    zb, bx0, by0, bx1, by1 = st.cell_box(b)
    assert za == zb == 10 and ax1 == bx0 and (ay0, ay1) == (by0, by1)
    assert st.zoom_for(64.0) == 9
    assert 100 < st.pixel_m(45.0, 10) < 115 and 120 < st.pixel_m(65.0, 9) < 135
    assert st.worldcover_name(39, -108) == "ESA_WorldCover_10m_2021_v200_N39W108_Map.tif"
    assert st.worldcover_tiles(-110.2, 44.9, -109.9, 45.1) == [(42, -111), (45, -111)]

    def loader(lat0, lon0, level):
        img = np.full((4500, 4500), 30, np.uint8)
        img[:, 2250:] = 10                               # trees in the east half of every file
        return img

    shares = st.cover_shares(za, ax0, ay0, ax1, ay1, 3, loader)
    assert shares.shape == (ay1 - ay0, ax1 - ax0, len(st.COVER_NAMES))
    assert np.allclose(shares.sum(axis=2), 1.0)
    split = round(st.px_x(-109.5 - 1e-9, za)) - ax0      # trees start at 109.5 W, east of this cell
    assert split >= ax1 - ax0 and shares[:, :, st.COVER_NAMES.index("grass")].min() == 1.0
    assert st.cover_shares(za, ax0, ay0, ax1, ay1, 3, lambda *_: None)[:, :, 0].min() == 1.0   # no file: nodata


def test_tiff_directory_and_tiles_round_trip():
    tiles = [np.full((16, 16), v, np.uint8) for v in (10, 30, 80, 60)]
    blobs = [zlib.compress(t.tobytes()) for t in tiles]
    n = 8
    data_at = 8 + 2 + 12 * n + 4 + 32
    offsets, pos = [], data_at
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
    arrays_at = 8 + 2 + 12 * n + 4
    entries = [(256, 3, 1, 24), (257, 3, 1, 24), (259, 3, 1, 8), (317, 3, 1, 1), (322, 3, 1, 16), (323, 3, 1, 16),
               (324, 4, 4, arrays_at), (325, 4, 4, arrays_at + 16)]
    head = b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", n)
    for tag, typ, cnt, val in entries:
        head += struct.pack("<HHI", tag, typ, cnt) + (struct.pack("<HH", val, 0) if typ == 3 else struct.pack("<I", val))
    head += struct.pack("<I", 0) + struct.pack("<4I", *offsets) + struct.pack("<4I", *[len(b) for b in blobs])
    buf = head + b"".join(blobs)
    (ifd,) = st.parse_tiff_ifds(buf)
    assert (ifd["width"], ifd["height"], ifd["tile_w"], ifd["offsets"]) == (24, 24, 16, offsets)
    img = st.assemble_tiles(ifd, buf[data_at:], data_at)
    assert img.shape == (24, 24)
    assert (img[0, 0], img[0, 23], img[23, 0], img[23, 23]) == (10, 30, 80, 60)


def test_join_groups_adds_up_patches_that_touch_across_cells():
    patches = [
        {"id": "a", "acres": 100.0, "_border": ["v10:500:7", "v10:500:8"]},
        {"id": "b", "acres": 50.0, "_border": ["v10:500:8", "h10:900:3"]},
        {"id": "c", "acres": 30.0, "_border": ["h10:900:3"]},
        {"id": "d", "acres": 10.0, "_border": ["v10:500:9"]},
    ]
    st.join_groups(patches)
    assert [p["group"] for p in patches] == ["a", "a", "a", "d"]
    assert [p["connected_acres"] for p in patches] == [180.0, 180.0, 180.0, 10.0]
    assert all("_border" not in p for p in patches)
