"""The WebGL map's pan limit (map/js/camera.js panCenter and panMinZoom with config.js `pan`), run in Node.

Alaska draws only at its true place, so the limit must let a drag bring it to the middle of the open map area
(the map minus the side panel or the phone sheet) at the national zoom and lower, let the map zoom out over Alaska
and the lower 48 together, and keep the map from being dragged away. Synthetic cameras only."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

TILE = 512
# The open map area: desktop (side panel on the right) and phone (sheet over the lower half), as app.js pads them.
DESKTOP = {"w": 1280, "h": 744, "pad": {"top": 48, "left": 56, "right": 420, "bottom": 40}}
PHONE = {"w": 390, "h": 792, "pad": {"top": 24, "left": 24, "right": 24, "bottom": 412}}
ANCHORAGE = (-149.9, 61.22)
ATTU = (172.9, 52.9)          # the far Aleutians, west of 180 degrees
BARROW = (-156.79, 71.29)     # the Arctic coast, past the Brooks Range


def merc_x(lon):
    return (lon + 180) / 360


def merc_y(lat):
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def camera_for(open_lon, open_lat, zoom, vp):
    """The camera center that puts (open_lon, open_lat) in the middle of the open map area."""
    ws = TILE * 2 ** zoom
    p = vp["pad"]
    x = merc_x(open_lon) * ws - (p["left"] - p["right"]) / 2
    y = merc_y(open_lat) * ws - (p["top"] - p["bottom"]) / 2
    lon = x / ws * 360 - 180
    lat = math.degrees(2 * math.atan(math.exp((0.5 - y / ws) * 2 * math.pi)) - math.pi / 2)
    return lon, lat


def open_middle(lon, lat, zoom, vp):
    """Inverse of camera_for: the point in the middle of the open map area for this camera center."""
    ws = TILE * 2 ** zoom
    p = vp["pad"]
    x = merc_x(lon) * ws + (p["left"] - p["right"]) / 2
    y = merc_y(lat) * ws + (p["top"] - p["bottom"]) / 2
    return x / ws * 360 - 180, math.degrees(2 * math.atan(math.exp((0.5 - y / ws) * 2 * math.pi)) - math.pi / 2)


def run(cases):
    """[(lng, lat, zoom, vp)] -> panCenter results, plus config.pan and panMinZoom for both screens."""
    script = (
        "import { panCenter, panMinZoom } from './map/js/camera.js';"
        "import { config } from './map/js/config.js';"
        f"const cases = {json.dumps(cases)};"
        "const out = cases.map(([lng, lat, z, vp]) => panCenter(lng, lat, z, vp.w, vp.h, vp.pad, config.pan.center));"
        f"const minZ = [{DESKTOP['w']}, {PHONE['w']}, 2560].map((w) => panMinZoom(w, config.pan));"
        "console.log(JSON.stringify({ out, pan: config.pan, minZ, national: config.nationalBounds }));"
    )
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, cwd=REPO, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_alaska_reaches_the_middle_of_the_open_map_area():
    cases = []
    for vp, zooms in ((DESKTOP, (3.28, 2.5)), (PHONE, (2.05, 1.5))):
        for z in zooms:
            for lon, lat in (ANCHORAGE, BARROW, (ATTU[0] - 360, ATTU[1])):
                cases.append([*camera_for(lon, lat, z, vp), z, vp])
    r = run(cases)
    assert r["out"] == [None] * len(cases)


def test_the_pan_box_holds_the_far_aleutians_and_the_arctic_coast():
    pan = run([])["pan"]
    [[west, south], [east, north]] = pan["center"]
    assert west <= ATTU[0] - 360 and north >= BARROW[1] and north >= 68.5  # the Brooks Range tops out near 68.5 N
    assert east >= -66.9 and south <= 24.5                                 # Maine and the Florida Keys


def test_the_national_views_are_never_clamped():
    """A static maxBounds would clamp the phone: its sheet pushes the national frame far south."""
    nat = run([])["national"]
    lon = (nat[0][0] + nat[1][0]) / 2
    lat = math.degrees(2 * math.atan(math.exp((0.5 - (merc_y(nat[0][1]) + merc_y(nat[1][1])) / 2) * 2 * math.pi)) - math.pi / 2)
    cases = [[*camera_for(lon, lat, 3.28, DESKTOP), 3.28, DESKTOP], [*camera_for(lon, lat, 2.05, PHONE), 2.05, PHONE]]
    phone_center = cases[1][1]
    assert phone_center < 15  # the phone camera center itself sits well south of the lower 48
    assert run(cases)["out"] == [None, None]


@pytest.mark.parametrize("target, edge", [
    ((-200.0, 61.0), "west"), ((-30.0, 40.0), "east"), ((-100.0, 80.0), "north"), ((-100.0, 0.0), "south"),
])
def test_a_drag_past_the_box_stops_at_its_edge(target, edge):
    z = 3.28
    r = run([[*camera_for(*target, z, DESKTOP), z, DESKTOP]])
    [[west, south], [east, north]] = r["pan"]["center"]
    lon, lat = open_middle(*r["out"][0], z, DESKTOP)
    want = {"west": (west, target[1]), "east": (east, target[1]), "north": (target[0], north), "south": (target[0], south)}[edge]
    assert lon == pytest.approx(want[0], abs=1e-6) and lat == pytest.approx(want[1], abs=1e-6)


def test_wrapped_longitudes_count_from_the_nearer_side():
    """The map draws world copies, so a camera west of 180 degrees comes back as an east longitude."""
    z = 3.28
    inside_lon, lat = camera_for(ATTU[0] - 360, ATTU[1], z, DESKTOP)
    past_lon, _ = camera_for(-200.0, ATTU[1], z, DESKTOP)
    r = run([[inside_lon + 360, lat, z, DESKTOP], [past_lon + 360, lat, z, DESKTOP], [inside_lon + 720, lat, z, DESKTOP]])
    assert r["out"][0] is None and r["out"][2] is None
    lon, _ = open_middle(*r["out"][1], z, DESKTOP)
    assert lon == pytest.approx(r["pan"]["center"][0][0], abs=1e-6)


def test_the_screen_stays_between_the_poles():
    """A tall screen zoomed out over the Arctic: the box allows its middle at 72 N, the pole clamp moves it down."""
    z = 1.2
    ws = TILE * 2 ** z
    tall = {"w": 390, "h": 1100, "pad": {"top": 0, "left": 0, "right": 0, "bottom": 0}}
    r = run([[-150.0, 84.0, z, tall]])
    _, lat = r["out"][0]
    assert merc_y(lat) * ws - tall["h"] / 2 == pytest.approx(0, abs=1e-6)


def test_zoom_out_shows_alaska_and_the_lower_48_together():
    r = run([])
    pan = r["pan"]
    desktop_z, phone_z, wide_z = r["minZ"]
    assert desktop_z == pytest.approx(math.log2(DESKTOP["w"] / (TILE * pan["maxWorldShare"])))
    assert phone_z == pan["minZoom"]
    span = lambda vp, z: (vp["w"] - vp["pad"]["left"] - vp["pad"]["right"]) * 360 / (TILE * 2 ** z)
    # Alaska's mainland west coast (168 W) to Maine (67 W): 101 degrees, inside the open map area on both screens.
    assert span(DESKTOP, desktop_z) >= 101 and span(PHONE, phone_z) >= 101
    # No screen ever spans the world's width, so the map never sits beside its own copy.
    for w, z in ((DESKTOP["w"], desktop_z), (PHONE["w"], phone_z), (2560, wide_z)):
        assert w * 360 / (TILE * 2 ** z) <= 360 * pan["maxWorldShare"] + 1e-9 or z == pan["minZoom"]
        assert w * 360 / (TILE * 2 ** z) < 360 - 121  # the map spans 188 W to 67 W
