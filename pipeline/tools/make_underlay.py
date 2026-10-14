"""One time asset script: the satellite style underlay of the Career Map (``images/basemap/``).

Natural Earth I with Shaded Relief, Water and Drainages (1:10m, large size ``NE1_HR_LC_SR_W``, public domain), cropped
to the lower 48 and to Alaska, resampled to rows evenly spaced in Web Mercator (the ``pipeline/heat.py`` grid, as
MapLibre image sources expect), muted with the same math as the USGS imagery paint in ``map/js/config.js`` and written
as WebP with a JSON sidecar of image corners (``underlay.json``). The map draws it under every data layer, in the 2D
picture before the click and in the WebGL map (docs/map/ARCHITECTURE.md, "Basemap underlay"). It is served with the
site, so no outside host is needed.

Only this script needs Pillow (with WebP support). The build, the tests and the site never import it, so it stays out
of requirements.txt:

    pip install Pillow
    python pipeline/tools/make_underlay.py                    # downloads the 323 MB zip into data/raw/basemap/download/
    python pipeline/tools/make_underlay.py --zip /path/to/NE1_HR_LC_SR_W.zip

The download folder is in .gitignore. Rerun only to change the boxes, resolution or tone below.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.heat import Grid  # noqa: E402  (same Web Mercator rows as the heat rasters)

ZIP_URL = "https://naturalearth.s3.amazonaws.com/10m_raster/NE1_HR_LC_SR_W.zip"
TIF_NAME = "NE1_HR_LC_SR_W.tif"
SRC_PX_PER_DEG = 60          # 21,600 x 10,800 pixels for the whole globe, pixel edges on whole arc minutes
SRC_SIZE = (21600, 10800)
OUT_DIR = ROOT / "images" / "basemap"
DOWNLOAD_DIR = ROOT / "data" / "raw" / "basemap" / "download"
SIDECAR = "underlay.json"

SOURCE = {
    "id": "natural-earth-ne1-hr-lc-sr-w",
    "url": "https://www.naturalearthdata.com/downloads/10m-raster-data/10m-natural-earth-1/",
    "title": "Natural Earth I with Shaded Relief, Water and Drainages (1:10m, large size, version 2.0.0)",
    "license": "public domain",
    "download": ZIP_URL,
}
ATTRIBUTION = 'Relief: <a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener">Natural Earth</a>'

# Map background (config.basemap.colors.background in map/js/config.js). Edges fade into it.
BACKGROUND = (0x0A, 0x0E, 0x14)

# Muted like the USGS imagery paint (config.basemap.imagery.paint: lower saturation, darker), with MapLibre's raster
# math, then a gamma that deepens the midtones: Natural Earth's light palette reads as satellite imagery once darker.
# Baked in, so the 2D picture and the WebGL map show the same pixels (the map draws it with a neutral paint).
TONE = {"saturation": -0.3, "contrast": 0.05, "brightness_max": 0.55, "gamma": 1.4}

# Boxes (west, south, east, north). The lower 48 box reaches well past the national view so its fading edge stays off
# the states. The Alaska box is the heat maps' Alaska region (config/definitions.yaml heat.regions.alaska), so the two
# line up at Alaska's true place.
# Each image stays within MAX_SIDE pixels: MapLibre uploads an image source as one WebGL texture, and many phones cap
# textures at 4096 pixels a side.
MAX_SIDE = 4096
REGIONS = {
    "lower48": {"box": (-131.0, 17.0, -61.0, 55.0), "px_per_degree": 58.0, "feather_deg": 2.4, "max_bytes": 600_000},
    "alaska": {"box": (-180.0, 51.2, -129.5, 71.6), "px_per_degree": 36.0, "feather_deg": 0.35, "max_bytes": 250_000},
}
QUALITY_START, QUALITY_MIN, QUALITY_STEP = 74, 40, 4
# A quarter size copy of each image for national zooms (the 2D picture and the WebGL map until state zooms): it shows
# the lower 48 at about 15 px per degree, so the full image's decode and texture upload wait until they are needed.
SMALL_FACTOR = 4
SMALL_MAX_BYTES = {"lower48": 120_000, "alaska": 50_000}


# ------------------------------------------------------------------ pure math (tested without Pillow)
def tone_matrix(saturation: float, contrast: float, brightness_max: float, brightness_min: float = 0.0) -> tuple:
    """MapLibre's raster paint as one RGB affine matrix (12 values, 0..255 units) for ``Image.convert``.

    The raster shader does: rgb += (average - rgb) * s; rgb = (rgb - 0.5) * c + 0.5; out = mix(min, max, rgb), with
    s = -saturation for saturation <= 0 (else 1 - 1 / (1.001 - saturation)) and c = 1 + contrast for contrast <= 0
    (else 1 / (1 - contrast)).
    """
    s = -saturation if saturation <= 0 else 1 - 1 / (1.001 - saturation)
    c = 1 + contrast if contrast <= 0 else 1 / (1 - contrast)
    span = brightness_max - brightness_min
    gain = span * c
    offset = 255 * (brightness_min + span * 0.5 * (1 - c))
    own = gain * ((1 - s) + s / 3)
    other = gain * s / 3
    return (own, other, other, offset,
            other, own, other, offset,
            other, other, own, offset)


def gamma_lut(gamma: float) -> list[int]:
    """256 entry lookup table: v' = 255 (v / 255) ^ gamma (gamma above 1 darkens the midtones)."""
    return [int(round(255 * (v / 255) ** gamma)) for v in range(256)]


def apply_tone(rgb: Sequence[float], m: Sequence[float], lut: Optional[Sequence[int]] = None) -> tuple[int, int, int]:
    """One pixel through ``tone_matrix`` (rounded and clamped like Pillow), then the gamma table."""
    out = []
    for k in range(3):
        v = m[4 * k] * rgb[0] + m[4 * k + 1] * rgb[1] + m[4 * k + 2] * rgb[2] + m[4 * k + 3]
        v = max(0, min(255, int(round(v))))
        out.append(lut[v] if lut else v)
    return tuple(out)


def src_col(lon: float) -> float:
    """Continuous source column (pixel i spans [i, i + 1), its west edge at lon -180 + i / 60)."""
    return (lon + 180.0) * SRC_PX_PER_DEG


def src_row(lat: float) -> float:
    """Continuous source row (pixel j spans [j, j + 1), its north edge at lat 90 - j / 60)."""
    return (90.0 - lat) * SRC_PX_PER_DEG


def row_edges(grid: Grid) -> list[float]:
    """Latitude of every output row edge, north to south (height + 1 values, rows even in Web Mercator y)."""
    yn, ys = grid.y_north, grid.y_south
    return [(2 * math.atan(math.exp(yn - k / grid.height * (yn - ys))) - math.pi / 2) * 180 / math.pi
            for k in range(grid.height + 1)]


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def feather_ramp(n: int, px: float) -> list[float]:
    """Weight per pixel along one axis: 0 at both ends, 1 from ``px`` pixels in (smoothstep)."""
    if px <= 0:
        return [1.0] * n
    return [smoothstep(min(i + 0.5, n - i - 0.5) / px) for i in range(n)]


def small_size(width: int, height: int) -> tuple[int, int]:
    """The quarter size copy's pixels (same corners, so its rows stay even in Web Mercator)."""
    return max(2, round(width / SMALL_FACTOR)), max(2, round(height / SMALL_FACTOR))


def region_grid(box: Sequence[float], px_per_degree: float) -> Grid:
    west, south, east, north = box
    return Grid.for_box(west, south, east, north, px_per_degree)


# ------------------------------------------------------------------ image work (needs Pillow)
def ensure_tif(zip_path: Optional[Path], download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    tif = download_dir / TIF_NAME
    if tif.is_file():
        return tif
    if zip_path is None:
        zip_path = download_dir / Path(ZIP_URL).name
        if not zip_path.is_file():
            print(f"downloading {ZIP_URL} (323 MB) to {zip_path}")
            urllib.request.urlretrieve(ZIP_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extract(TIF_NAME, download_dir)
    return tif


def open_source(tif: Path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # a known 233 Mpx public domain file, checked below
    im = Image.open(tif)
    if im.size != SRC_SIZE or im.mode != "RGB":
        raise SystemExit(f"{tif}: expected an RGB image of {SRC_SIZE}, got {im.mode} {im.size}")
    im.load()
    return im


def resample(src, grid: Grid):
    """Crop and resample to the grid: columns even in longitude, rows even in Web Mercator y (area filtered)."""
    from PIL import Image
    x0, x1 = src_col(grid.west), src_col(grid.east)
    edges = row_edges(grid)
    y0, y1 = src_row(edges[0]), src_row(edges[-1])
    c0, r0 = max(0, math.floor(x0) - 2), max(0, math.floor(y0) - 2)
    c1, r1 = min(SRC_SIZE[0], math.ceil(x1) + 2), min(SRC_SIZE[1], math.ceil(y1) + 2)
    crop = src.crop((c0, r0, c1, r1))
    wide = crop.resize((grid.width, crop.height), Image.Resampling.LANCZOS, box=(x0 - c0, 0, x1 - c0, crop.height))
    out = Image.new("RGB", (grid.width, grid.height))
    for j in range(grid.height):
        top, bottom = src_row(edges[j]) - r0, src_row(edges[j + 1]) - r0
        out.paste(wide.resize((grid.width, 1), Image.Resampling.BILINEAR, box=(0, top, grid.width, bottom)), (0, j))
    return out


def feather(im, px: float):
    """Fade the edges into the map background."""
    from PIL import Image, ImageChops
    w, h = im.size
    row = Image.new("L", (w, 1)); row.putdata([round(255 * v) for v in feather_ramp(w, px)])
    col = Image.new("L", (1, h)); col.putdata([round(255 * v) for v in feather_ramp(h, px)])
    mask = ImageChops.multiply(row.resize((w, h), Image.Resampling.NEAREST), col.resize((w, h), Image.Resampling.NEAREST))
    return Image.composite(im, Image.new("RGB", (w, h), BACKGROUND), mask)


def encode(im, max_bytes: int) -> tuple[bytes, int]:
    import io
    q = QUALITY_START
    while True:
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q, method=6)
        data = buf.getvalue()
        if len(data) <= max_bytes or q <= QUALITY_MIN:
            return data, q
        q -= QUALITY_STEP


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", type=Path, help="local NE1_HR_LC_SR_W.zip (else it is downloaded)")
    ap.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    from PIL import Image
    tif = ensure_tif(args.zip, args.download_dir)
    src = open_source(tif)
    matrix = tone_matrix(TONE["saturation"], TONE["contrast"], TONE["brightness_max"])
    lut = gamma_lut(TONE["gamma"]) * 3
    args.out.mkdir(parents=True, exist_ok=True)
    rasters = []
    for region, spec in REGIONS.items():
        grid = region_grid(spec["box"], spec["px_per_degree"])
        if max(grid.width, grid.height) > MAX_SIDE:
            raise SystemExit(f"{region}: {grid.width} x {grid.height} is over {MAX_SIDE} pixels a side")
        im = resample(src, grid).convert("RGB", matrix).point(lut)
        im = feather(im, spec["feather_deg"] * spec["px_per_degree"])
        data, quality = encode(im, spec["max_bytes"])
        name = f"underlay-{region}.webp"
        (args.out / name).write_bytes(data)
        size = small_size(grid.width, grid.height)
        sdata, squality = encode(im.resize(size, Image.Resampling.LANCZOS), SMALL_MAX_BYTES[region])
        sname = f"underlay-{region}-small.webp"
        (args.out / sname).write_bytes(sdata)
        rasters.append({
            "region": region, "image": name, "width": grid.width, "height": grid.height,
            "px_per_degree": spec["px_per_degree"], "coordinates": grid.coordinates(),
            "feather_deg": spec["feather_deg"], "quality": quality, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "small": {"image": sname, "width": size[0], "height": size[1], "quality": squality, "bytes": len(sdata),
                      "sha256": hashlib.sha256(sdata).hexdigest()},
        })
        print(f"{name}: {grid.width} x {grid.height}, WebP quality {quality}, {len(data):,} bytes")
        print(f"{sname}: {size[0]} x {size[1]}, WebP quality {squality}, {len(sdata):,} bytes")
    sidecar = {
        "version": 1,
        "generated_by": "pipeline/tools/make_underlay.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
        "attribution": ATTRIBUTION,
        "projection": "rows evenly spaced in Web Mercator y, columns evenly spaced in longitude (MapLibre image source)",
        "tone": TONE,
        "background": "#%02x%02x%02x" % BACKGROUND,
        "rasters": rasters,
    }
    (args.out / SIDECAR).write_text(json.dumps(sidecar, indent=1) + "\n")
    print(f"wrote {args.out / SIDECAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
