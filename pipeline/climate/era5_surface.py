"""ERA5 surface winds, gusts, snow and lake ice over the United States, December to March, for the snowkite scores.

Phase 25 of the snowkite work (docs/map/SNOWKITE.md, docs/map/snowkite-criteria.md) scores zones from wind, snow and
ice. ERA5 single level fields come from the same public ARCO ERA5 bucket as the jet stream winds
(pipeline/climate/era5_jet.py, whose netCDF 3 reader this module reuses):

    https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level/YYYY/MM/DD/<variable>/surface.nc

Variables: 10 m u and v wind (``u10``, ``v10``), 10 m gust since the previous hour (``fg10``), snow depth in metres
of water equivalent (``sd``) and lake ice depth in metres (``licd``).

Sampling: every day from December 1 to March 31 of each season; winds and gusts at ``HOURS_UTC`` (17, 20 and 23 UTC:
daylight from Alaska to the East Coast), snow and lake ice once a day at 20 UTC. Seasons default to 2015/16 to 2024/25,
whose files are all netCDF 3.

The extract keeps counts, so every threshold stays in config/definitions.yaml and applies at build time. Per 0.25
degree cell whose center lies inside a US state:

* ``speed``: hours per wind speed bin (``SPEED_EDGES_MS``, 1 m/s steps, last bin open);
* ``dir``: hours per direction the wind blows from, 8 sectors from north clockwise, for hours at or above
  ``DIR_MIN_MS``;
* ``gust``: sum of gust divided by mean speed, in hundredths, and the hours counted (same floor);
* ``snow``: days per snow depth bin (``SNOW_EDGES_M``, metres of water equivalent);
* ``ice``: days per lake ice depth bin (``ICE_EDGES_M``, metres).

ERA5 is a model on a grid of about 31 km: it smooths mountains, valleys and small lakes, and studies report it reads
low in complex terrain. Values from this extract are estimates of a cell, never measurements at a launch.

Commands::

    python -m pipeline.climate.era5_surface fetch [--first 2015] [--last 2024] [--workers 16]
    python -m pipeline.climate.era5_surface summary

``fetch`` needs numpy; the build does not. Per season partial results go to
``data/raw/climate/era5_surface/download/`` (gitignored). The committed extract is
``data/raw/climate/era5_surface/us_cells_dec_mar.json`` plus ``source.json``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

from pipeline.climate import era5_jet as nc
from pipeline.geo import point_in_geometry

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "climate" / "era5_surface"
DOWNLOAD_DIR = OUT_DIR / "download"
EXTRACT = OUT_DIR / "us_cells_dec_mar.json"
SOURCE = OUT_DIR / "source.json"
STATES = ROOT / "data" / "build" / "us-states.geojson"

BASE = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level"
FILES = {"u10": "10m_u_component_of_wind", "v10": "10m_v_component_of_wind",
         "fg10": "10m_wind_gust_since_previous_post_processing", "sd": "snow_depth", "licd": "lake_ice_depth"}
HOURS_UTC = (17, 20, 23)
DAILY_HOUR_UTC = 20
LAT_MAX, LAT_MIN = 72.0, 24.0
LON_MIN, LON_MAX = -170.0, -66.0
SPEED_EDGES_MS = list(range(0, 16))          # 0-1, 1-2, ..., 14-15, 15 and above
DIR_MIN_MS = 3.0
SNOW_EDGES_M = [0, 0.005, 0.02, 0.05, 0.1, 0.3]   # metres of water equivalent; last bin open
ICE_EDGES_M = [0, 0.1, 0.2, 0.3, 0.5]             # metres; last bin open

ROW0 = int((90 - LAT_MAX) / 0.25)
ROW1 = int((90 - LAT_MIN) / 0.25)
COL0 = int((LON_MIN % 360) / 0.25)
COL1 = int((LON_MAX % 360) / 0.25)


def season_days(start_year: int) -> list[dt.date]:
    """December 1 of start_year to March 31 of the next year."""
    day, end = dt.date(start_year, 12, 1), dt.date(start_year + 1, 3, 31)
    out = []
    while day <= end:
        out.append(day)
        day += dt.timedelta(days=1)
    return out


def _field(day: dt.date, var: str, hours: tuple[int, ...]) -> list[Any]:
    """The (rows, cols) box of `var` at each hour of `day`, as float arrays (NaN where missing)."""
    import numpy as np

    url = f"{BASE}/{day:%Y/%m/%d}/{FILES[var]}/surface.nc"
    head = nc._get(url, 0, nc.HEADER_BYTES - 1)
    header = nc.parse_header(head)
    v = header["vars"][var]
    if v["type"] != 3 or [d for d, _ in v["dims"]] != ["time", "latitude", "longitude"] or v["dims"][1][1] != 721:
        raise ValueError(f"{url}: unexpected layout {v['type']} {v['dims']}")
    nlon = v["dims"][2][1]
    out = []
    for hour in hours:
        start = nc.field_offset(header, var, hour, ROW0)
        raw = np.frombuffer(nc._get(url, start, start + (ROW1 - ROW0 + 1) * nlon * 2 - 1), dtype=">i2")
        raw = raw.reshape(ROW1 - ROW0 + 1, nlon)[:, COL0:COL1 + 1]
        vals = raw.astype(np.float32) * float(v["atts"].get("scale_factor", 1.0)) + float(v["atts"].get("add_offset", 0.0))
        fill = v["atts"].get("_FillValue")
        if fill is not None:
            vals[raw == int(fill)] = np.nan
        out.append(vals)
    return out


def _day(day: dt.date) -> dict[str, Any]:
    import numpy as np

    u, v, g = (_field(day, k, HOURS_UTC) for k in ("u10", "v10", "fg10"))
    sd = _field(day, "sd", (DAILY_HOUR_UTC,))[0]
    ice = _field(day, "licd", (DAILY_HOUR_UTC,))[0]
    shape = u[0].shape
    speed = np.zeros(shape + (len(SPEED_EDGES_MS),), np.int32)
    direction = np.zeros(shape + (8,), np.int32)
    gust_sum = np.zeros(shape, np.float64)
    gust_n = np.zeros(shape, np.int32)
    rows, cols = np.indices(shape)
    for uu, vv, gg in zip(u, v, g):
        s = np.hypot(uu, vv)
        b = np.clip(np.floor(s), 0, len(SPEED_EDGES_MS) - 1).astype(np.int64)
        np.add.at(speed, (rows, cols, b), 1)
        windy = s >= DIR_MIN_MS
        frm = (np.degrees(np.arctan2(-uu, -vv)) + 360.0) % 360.0   # direction the wind blows from
        sector = (np.floor((frm + 22.5) / 45.0).astype(np.int64)) % 8
        np.add.at(direction, (rows[windy], cols[windy], sector[windy]), 1)
        ok = windy & np.isfinite(gg)
        gust_sum[ok] += gg[ok] / s[ok]
        gust_n[ok] += 1
    snow = np.zeros(shape + (len(SNOW_EDGES_M),), np.int32)
    np.add.at(snow, (rows, cols, np.clip(np.searchsorted(SNOW_EDGES_M, sd, side="right") - 1, 0, len(SNOW_EDGES_M) - 1)), 1)
    icy = np.zeros(shape + (len(ICE_EDGES_M),), np.int32)
    has = np.isfinite(ice)
    np.add.at(icy, (rows[has], cols[has], np.clip(np.searchsorted(ICE_EDGES_M, ice[has], side="right") - 1, 0, len(ICE_EDGES_M) - 1)), 1)
    return {"speed": speed, "dir": direction, "gust_sum": gust_sum, "gust_n": gust_n, "snow": snow, "ice": icy}


def fetch_season(start_year: int, workers: int) -> dict[str, Any]:
    import numpy as np

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cache = DOWNLOAD_DIR / f"{start_year}-{start_year + 1}.npz"
    if cache.exists():
        with np.load(cache) as z:
            return {k: z[k] for k in z.files}
    total: dict[str, Any] = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(_day, season_days(start_year)):
            for k, a in part.items():
                total[k] = a.copy() if k not in total else total[k] + a
    np.savez_compressed(cache, **total)
    return total


def us_mask() -> tuple[list[tuple[int, int]], list[str]]:
    """(row, col) of every box cell whose center lies in a US state, and the state code of each."""
    shapes = json.loads(STATES.read_text(encoding="utf-8"))["features"]
    boxes = []
    for f in shapes:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        xs = [x for p in polys for x, _ in p[0]]
        ys = [y for p in polys for _, y in p[0]]
        boxes.append((min(xs), min(ys), max(xs), max(ys), f["properties"]["code"], g))
    cells, codes = [], []
    for r in range(ROW1 - ROW0 + 1):
        lat = LAT_MAX - 0.25 * r
        for c in range(COL1 - COL0 + 1):
            lon = LON_MIN + 0.25 * c
            for x0, y0, x1, y1, code, g in boxes:
                if x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_geometry(lon, lat, g):
                    cells.append((r, c))
                    codes.append(code)
                    break
    return cells, codes


def fetch(first: int, last: int, workers: int) -> None:
    total: dict[str, Any] = {}
    seasons = []
    for year in range(first, last + 1):
        t0 = time.time()
        part = fetch_season(year, workers)
        for k, a in part.items():
            total[k] = a.copy() if k not in total else total[k] + a
        seasons.append(f"{year}/{str(year + 1)[2:]}")
        print(f"{seasons[-1]}: {time.time() - t0:.0f} s", flush=True)
    cells, codes = us_mask()
    accessed = dt.date.today().isoformat()
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    EXTRACT.write_text(json.dumps({
        "source_id": "era5-arco-surface",
        "as_of": accessed,
        "seasons": seasons,
        "months": [12, 1, 2, 3],
        "hours_utc": list(HOURS_UTC),
        "daily_hour_utc": DAILY_HOUR_UTC,
        "grid": {"step_deg": 0.25, "lat_max": LAT_MAX, "lon_min": LON_MIN},
        "speed_edges_ms": SPEED_EDGES_MS,
        "dir_min_ms": DIR_MIN_MS,
        "dir_sectors": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "snow_edges_m_we": SNOW_EDGES_M,
        "ice_edges_m": ICE_EDGES_M,
        "cells": {
            "row": rows, "col": cols, "state": codes,
            "speed": total["speed"][rows, cols].tolist(),
            "dir": total["dir"][rows, cols].tolist(),
            "gust_ratio_x100_sum": [int(round(x * 100)) for x in total["gust_sum"][rows, cols].tolist()],
            "gust_hours": total["gust_n"][rows, cols].tolist(),
            "snow": total["snow"][rows, cols].tolist(),
            "ice": total["ice"][rows, cols].tolist(),
        },
    }, separators=(",", ":")) + "\n")
    SOURCE.write_text(json.dumps({
        "source": {
            "id": "era5-arco-surface",
            "url": "https://cloud.google.com/storage/docs/public-datasets/era5",
            "title": "ERA5 hourly data on single levels (ECMWF, Copernicus Climate Change Service), ARCO ERA5 raw files on Google Cloud",
            "accessed_date": accessed,
            "note": ("10 m wind, 10 m gust, snow depth and lake ice depth, December to March, read with HTTP range requests "
                     f"from {BASE}/YYYY/MM/DD/<variable>/surface.nc. Hersbach et al. (2020), ERA5, doi:10.24381/cds.adbb2d47. "
                     "A model on a grid of about 31 km; values describe a grid cell. "
                     "Contains modified Copernicus Climate Change Service information."),
        },
        "confidence": "published",
        "license": "Copernicus licence (CC BY 4.0 compatible); attribution required",
        "seasons": seasons,
    }, indent=2) + "\n")
    print(f"wrote {EXTRACT.relative_to(ROOT)}: {len(cells)} cells, {len(seasons)} seasons")


def summary() -> None:
    d = json.loads(EXTRACT.read_text())
    print(len(d["cells"]["row"]), "cells;", d["seasons"][0], "to", d["seasons"][-1])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.climate.era5_surface")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--first", type=int, default=2015)
    f.add_argument("--last", type=int, default=2024)
    f.add_argument("--workers", type=int, default=16)
    sub.add_parser("summary")
    args = p.parse_args(argv)
    fetch(args.first, args.last, args.workers) if args.cmd == "fetch" else summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
