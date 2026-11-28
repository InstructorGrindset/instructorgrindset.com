"""ERA5 surface winds, gusts, snow, lake ice and temperature over the United States, for the snowkite scores.

Phase 25 of the snowkite work (docs/map/SNOWKITE.md, docs/map/snowkite-criteria.md) scores zones from wind, snow and
ice. ERA5 single level fields come from the same public ARCO ERA5 bucket as the jet stream winds
(pipeline/climate/era5_jet.py, whose netCDF 3 reader this module reuses):

    https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level/YYYY/MM/DD/<variable>/surface.nc
    https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-static/2021/12/31/<variable>/static.nc

Variables: 10 m u and v wind (``u10``, ``v10``), 10 m gust since the previous hour (``fg10``), snow depth in metres
of water equivalent (``sd``), lake ice depth in metres (``licd``) and, in the monthly extract, 2 m temperature in
kelvin (``t2m``). Static fields, read once: lake cover (``cl``), high vegetation cover (``cvh``), land sea mask
(``lsm``) and surface geopotential (``z``; height in metres = z / 9.80665).

Sampling: winds and gusts at ``HOURS_UTC`` (17, 20 and 23 UTC: daylight from Alaska to the East Coast); snow, lake
ice and temperature once a day at 20 UTC. Seasons default to 2015/16 to 2024/25, whose files are netCDF 3 but for two days.

Two extracts, both of counts, so every threshold stays in config/definitions.yaml and applies at build time:

* ``us_cells_dec_mar.json`` (``fetch``): December 1 to March 31 pooled, every 0.25 degree cell whose center lies
  inside a US state, wind speed in 16 bins.
* ``us_cells_monthly.json`` (``fetch-monthly``): November 1 to May 31, one set of counts per calendar month
  (``MONTHS``), wind speed in 13 bins, plus temperature and the static fields. It keeps only the US cells that
  pass ``keep_cell`` (some winter snow, or some ice on a real lake), and wind counts only for the months that
  pass it too; the rule only trims the file. A trimmed month holds null wind, and ``ice`` is null in a cell with
  zero lake cover.

Per cell (and per month in the monthly extract):

* ``speed``: hours per wind speed bin (1 m/s steps from 0, last bin open);
* ``dir``: hours per direction the wind blows from, 8 sectors from north clockwise, for hours at or above
  ``DIR_MIN_MS``;
* ``gust``: sum of gust divided by mean speed, in hundredths, and the hours counted (same floor; the monthly
  extract leaves the hours out, since they equal the sum of ``dir``);
* ``snow``: days per snow depth bin (``SNOW_EDGES_M``, metres of water equivalent);
* ``ice``: days per lake ice depth bin (``ICE_EDGES_M``, metres). ERA5's lake model runs in every cell, so the
  ice numbers mean something only where ``lake_cover_pct`` is above zero; elsewhere they describe a lake that
  does not exist;
* monthly only: ``temp_c_x10_sum`` (sum of 2 m temperature at 20 UTC in tenths of a degree Celsius; the day
  count is ``month_days`` at the top of the file, the same for every cell) and ``thaw_days`` (days above 0 C at
  20 UTC).

ERA5 is a model on a grid of about 31 km: it smooths mountains, valleys and small lakes, and studies report it reads
low in complex terrain. Values from this extract are estimates of a cell, never measurements at a launch.

Commands::

    python -m pipeline.climate.era5_surface fetch [--first 2015] [--last 2024] [--workers 16]
    python -m pipeline.climate.era5_surface fetch-monthly [--first 2015] [--last 2024] [--workers 16]
    python -m pipeline.climate.era5_surface summary

The fetch commands need numpy and, for the few netCDF 4 day files (9 and 10 November 2023, and everything from
about March 2026), h5py (``pip install numpy h5py``); the build needs neither. Per season partial results go to
``data/raw/climate/era5_surface/download/`` (gitignored) so an interrupted fetch resumes. ``source.json`` covers
both extracts.
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
MONTHLY = OUT_DIR / "us_cells_monthly.json"
SOURCE = OUT_DIR / "source.json"
STATES = ROOT / "data" / "build" / "us-states.geojson"

BASE = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level"
STATIC_BASE = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-static/2021/12/31"
FILES = {"u10": "10m_u_component_of_wind", "v10": "10m_v_component_of_wind",
         "fg10": "10m_wind_gust_since_previous_post_processing", "sd": "snow_depth", "licd": "lake_ice_depth",
         "t2m": "2m_temperature"}
STATIC_FILES = {"cl": "lake_cover", "cvh": "high_vegetation_cover", "lsm": "land_sea_mask", "z": "geopotential"}
GRAVITY_MS2 = 9.80665
HOURS_UTC = (17, 20, 23)
DAILY_HOUR_UTC = 20
LAT_MAX, LAT_MIN = 72.0, 24.0
LON_MIN, LON_MAX = -170.0, -66.0
SPEED_EDGES_MS = list(range(0, 16))          # 0-1, 1-2, ..., 14-15, 15 and above
DIR_MIN_MS = 3.0
SNOW_EDGES_M = [0, 0.005, 0.02, 0.05, 0.1, 0.3]   # metres of water equivalent; last bin open
ICE_EDGES_M = [0, 0.1, 0.2, 0.3, 0.5]             # metres; last bin open

# Monthly extract.
MONTHS = (11, 12, 1, 2, 3, 4, 5)                  # November 1 of the start year to May 31
MONTHLY_SPEED_EDGES_MS = list(range(0, 13))       # 0-1, 1-2, ..., 11-12, 12 and above
ZERO_C_K = 273.15

# Keep rules of the monthly extract. They only trim the file to cells and months that could hold a kite surface;
# they are looser than any score threshold and carry no meaning of their own. A US cell stays when, over the
# December to February days of every season, snow reaches KEEP_SNOW_M_WE on KEEP_SHARE of the days, or lake ice
# reaches KEEP_ICE_M on KEEP_SHARE of the days in a cell with at least KEEP_LAKE_COVER_PCT of lake. Inside a kept
# cell, a month keeps its wind counts when the same test passes on that month's days at KEEP_MONTH_SHARE; a month
# with neither snow nor ice stores null wind. Snow, ice and temperature stay for every month.
# KEEP_SHARE 0.10 keeps the thin snow plains of Montana and the Dakotas (9,729 cells, 7.0 MB); prairie kiting needs them.
KEEP_MONTHS = (12, 1, 2)
KEEP_SHARE = 0.10
KEEP_MONTH_SHARE = 0.10
KEEP_SNOW_M_WE = 0.02
KEEP_ICE_M = 0.1
KEEP_LAKE_COVER_PCT = 5.0

ROW0 = int((90 - LAT_MAX) / 0.25)
ROW1 = int((90 - LAT_MIN) / 0.25)
COL0 = int((LON_MIN % 360) / 0.25)
COL1 = int((LON_MAX % 360) / 0.25)


def season_days(start_year: int, monthly: bool = False) -> list[dt.date]:
    """December 1 of start_year to March 31 of the next year; with `monthly`, November 1 to May 31."""
    day, end = dt.date(start_year, 12, 1), dt.date(start_year + 1, 3, 31)
    if monthly:
        day, end = dt.date(start_year, 11, 1), dt.date(start_year + 1, 5, 31)
    out = []
    while day <= end:
        out.append(day)
        day += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------------------------------------ pure binning


def speed_bin(speed: Any, nbins: int) -> Any:
    """Index of the 1 m/s bin of each speed; the last bin is open."""
    import numpy as np

    return np.clip(np.floor(speed), 0, nbins - 1).astype(np.int64)


def direction_sector(u: Any, v: Any) -> Any:
    """Sector (0 = north, clockwise, 45 degrees each, centered on the compass point) the wind blows from."""
    import numpy as np

    frm = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return np.floor((frm + 22.5) / 45.0).astype(np.int64) % 8


def depth_bin(depth: Any, edges: list[float]) -> Any:
    """Index of the bin of each depth: bin i holds edges[i] <= depth < edges[i + 1]; the last bin is open."""
    import numpy as np

    return np.clip(np.searchsorted(edges, depth, side="right") - 1, 0, len(edges) - 1)


def tally(bins: Any, nbins: int, where: Any = None) -> Any:
    """One count in bin `bins[r, c]` of each cell (only cells where `where` holds): shape (rows, cols, nbins)."""
    import numpy as np

    out = np.zeros(bins.shape + (nbins,), np.int32)
    rows, cols = np.indices(bins.shape)
    if where is None:
        np.add.at(out, (rows, cols, bins), 1)
    else:
        np.add.at(out, (rows[where], cols[where], bins[where]), 1)
    return out


def wind_stats(u: list[Any], v: list[Any], g: list[Any], nbins: int) -> dict[str, Any]:
    """Speed and direction histograms and the gust ratio sum over the sampled hours of one day."""
    import numpy as np

    shape = u[0].shape
    speed = np.zeros(shape + (nbins,), np.int32)
    direction = np.zeros(shape + (8,), np.int32)
    gust_sum = np.zeros(shape, np.float64)
    gust_n = np.zeros(shape, np.int32)
    for uu, vv, gg in zip(u, v, g):
        s = np.hypot(uu, vv)
        speed += tally(speed_bin(s, nbins), nbins)
        windy = s >= DIR_MIN_MS
        direction += tally(direction_sector(uu, vv), 8, windy)
        ok = windy & np.isfinite(gg)
        gust_sum[ok] += gg[ok] / s[ok]
        gust_n[ok] += 1
    return {"speed": speed, "dir": direction, "gust_sum": gust_sum, "gust_n": gust_n}


def keep_cell(snow_days: list[int], ice_days: list[int], lake_cover_pct: float, share: float = KEEP_SHARE) -> bool:
    """The monthly extract's trim rule, from a cell's snow and ice histograms over December to February (or over
    one month, with ``share=KEEP_MONTH_SHARE``, to decide whether that month keeps its wind counts)."""
    snow_from = SNOW_EDGES_M.index(KEEP_SNOW_M_WE)
    ice_from = ICE_EDGES_M.index(KEEP_ICE_M)
    snow_n, ice_n = sum(snow_days), sum(ice_days)
    if snow_n and sum(snow_days[snow_from:]) >= share * snow_n:
        return True
    return bool(ice_n) and lake_cover_pct >= KEEP_LAKE_COVER_PCT and sum(ice_days[ice_from:]) >= share * ice_n


# ------------------------------------------------------------------------------------------------ fetch


def _read(url: str, var: str, hours: tuple[int, ...]) -> list[Any]:
    """The (rows, cols) box of `var` at each time index, as float arrays (NaN where missing). One header read."""
    import numpy as np

    head = nc._get(url, 0, nc.HEADER_BYTES - 1)
    if head[:4] == b"\x89HDF":  # a few day files (9 and 10 November 2023) and all from about March 2026 are netCDF 4
        return _read_hdf5(url, var, hours)
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


def _read_hdf5(url: str, var: str, hours: tuple[int, ...]) -> list[Any]:
    """netCDF 4 day file: (valid_time 24, latitude 721, longitude 1440) float32 in gzip chunks. Needs h5py."""
    import h5py
    import numpy as np

    with nc._H5_LOCK:  # h5py reads through Python callbacks; one file at a time
        with h5py.File(nc._HttpFile(url), "r") as h:
            ds, lat = h[var], h["latitude"]
            if ds.shape[1:] != (721, 1440) or float(lat[0]) != 90.0 or float(lat[1]) != 89.75 or float(h["longitude"][0]) != 0.0:
                raise ValueError(f"{url}: unexpected grid {ds.shape}")
            if "scale_factor" in ds.attrs or "add_offset" in ds.attrs or ds.dtype.kind != "f":
                raise ValueError(f"{url}: expected plain float values, got {ds.dtype} {dict(ds.attrs)}")
            if len(hours) > 1 and ds.shape[0] != 24:
                raise ValueError(f"{url}: expected 24 hours, got {ds.shape[0]}")
            block = ds[min(hours):max(hours) + 1, ROW0:ROW1 + 1, COL0:COL1 + 1].astype(np.float32)
    return [block[hour - min(hours)] for hour in hours]


def _field(day: dt.date, var: str, hours: tuple[int, ...]) -> list[Any]:
    return _read(f"{BASE}/{day:%Y/%m/%d}/{FILES[var]}/surface.nc", var, hours)


def _day(day: dt.date, monthly: bool = False) -> dict[str, Any]:
    import numpy as np

    u, v, g = (_field(day, k, HOURS_UTC) for k in ("u10", "v10", "fg10"))
    sd = _field(day, "sd", (DAILY_HOUR_UTC,))[0]
    ice = _field(day, "licd", (DAILY_HOUR_UTC,))[0]
    out = wind_stats(u, v, g, len(MONTHLY_SPEED_EDGES_MS if monthly else SPEED_EDGES_MS))
    out["snow"] = tally(depth_bin(sd, SNOW_EDGES_M), len(SNOW_EDGES_M), np.isfinite(sd))
    out["ice"] = tally(depth_bin(ice, ICE_EDGES_M), len(ICE_EDGES_M), np.isfinite(ice))
    if monthly:
        t = _field(day, "t2m", (DAILY_HOUR_UTC,))[0].astype(np.float64) - ZERO_C_K
        ok = np.isfinite(t)
        out["temp_sum"] = np.where(ok, t, 0.0)
        out["temp_n"] = ok.astype(np.int32)
        out["thaw"] = (ok & (t > 0)).astype(np.int32)
    return out


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


def fetch_season_monthly(start_year: int, workers: int) -> dict[str, Any]:
    """Per month sums of one season: every array has the month (index into MONTHS) as its first axis."""
    import numpy as np

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cache = DOWNLOAD_DIR / f"monthly-{start_year}-{start_year + 1}.npz"
    if cache.exists():
        with np.load(cache) as z:
            return {k: z[k] for k in z.files}
    days = season_days(start_year, monthly=True)
    total: dict[str, Any] = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for day, part in zip(days, pool.map(lambda d: _day(d, monthly=True), days)):
            m = MONTHS.index(day.month)
            for k, a in part.items():
                if k not in total:
                    total[k] = np.zeros((len(MONTHS),) + a.shape, a.dtype)
                total[k][m] += a
    np.savez_compressed(cache, **total)
    return total


def fetch_static() -> dict[str, Any]:
    """Static fields over the box: lake cover, high vegetation cover and land share (0 to 1), surface height (m)."""
    import numpy as np

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cache = DOWNLOAD_DIR / "static.npz"
    if cache.exists():
        with np.load(cache) as z:
            return {k: z[k] for k in z.files}
    out = {var: _read(f"{STATIC_BASE}/{folder}/static.nc", var, (0,))[0] for var, folder in STATIC_FILES.items()}
    out["elevation_m"] = out.pop("z") / GRAVITY_MS2
    np.savez_compressed(cache, **out)
    return out


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
    _write_source(accessed)
    print(f"wrote {EXTRACT.relative_to(ROOT)}: {len(cells)} cells, {len(seasons)} seasons")


def _write_source(accessed: str) -> None:
    """One source record for both extracts; each file entry comes from the extract on disk."""
    files = {}
    for path in (EXTRACT, MONTHLY):
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            files[path.name] = {"as_of": d["as_of"], "seasons": d["seasons"], "months": d["months"],
                                "cells": len(d["cells"]["row"])}
    SOURCE.write_text(json.dumps({
        "source": {
            "id": "era5-arco-surface",
            "url": "https://cloud.google.com/storage/docs/public-datasets/era5",
            "title": "ERA5 hourly data on single levels (ECMWF, Copernicus Climate Change Service), ARCO ERA5 raw files on Google Cloud",
            "accessed_date": accessed,
            "note": ("10 m wind, 10 m gust, snow depth, lake ice depth and 2 m temperature, November to May, read with HTTP "
                     f"range requests from {BASE}/YYYY/MM/DD/<variable>/surface.nc; lake cover, high vegetation cover, "
                     f"land sea mask and surface geopotential from {STATIC_BASE}/<variable>/static.nc. "
                     "Hersbach et al. (2020), ERA5, doi:10.24381/cds.adbb2d47. "
                     "A model on a grid of about 31 km; values describe a grid cell. "
                     "Contains modified Copernicus Climate Change Service information."),
        },
        "confidence": "published",
        "license": "Copernicus licence (CC BY 4.0 compatible); attribution required",
        "seasons": next(iter(files.values()))["seasons"] if files else [],
        "files": files,
    }, indent=2) + "\n")


def fetch_monthly(first: int, last: int, workers: int) -> None:
    import numpy as np

    static = fetch_static()
    total: dict[str, Any] = {}
    seasons = []
    for year in range(first, last + 1):
        t0 = time.time()
        part = fetch_season_monthly(year, workers)
        for k, a in part.items():
            total[k] = a.copy() if k not in total else total[k] + a
        seasons.append(f"{year}/{str(year + 1)[2:]}")
        print(f"{seasons[-1]}: {time.time() - t0:.0f} s", flush=True)
    cells, codes = us_mask()
    lake_pct = static["cl"] * 100.0
    djf = [MONTHS.index(m) for m in KEEP_MONTHS]
    snow_djf, ice_djf = total["snow"][djf].sum(axis=0), total["ice"][djf].sum(axis=0)
    kept = [(r, c, code) for (r, c), code in zip(cells, codes)
            if keep_cell(snow_djf[r, c].tolist(), ice_djf[r, c].tolist(), float(lake_pct[r, c]))]
    rows = [r for r, _, _ in kept]
    cols = [c for _, c, _ in kept]

    def per_cell(a: Any) -> list[Any]:
        """(month, rows, cols, ...) to a list per cell of a list per month."""
        return np.moveaxis(a[:, rows, cols], 0, 1).tolist()

    snow, ice = per_cell(total["snow"]), per_cell(total["ice"])
    lake = [round(float(x), 1) for x in lake_pct[rows, cols]]
    live = [[keep_cell(s, i, lk, KEEP_MONTH_SHARE) for s, i in zip(ss, ii)] for ss, ii, lk in zip(snow, ice, lake)]

    def wind(a: Any) -> list[Any]:
        """Per cell and month, null for a month with neither snow nor ice."""
        return [[x if on else None for x, on in zip(xs, ons)] for xs, ons in zip(per_cell(a), live)]

    month_days = total["temp_n"].max(axis=(1, 2))
    if (total["temp_n"][:, rows, cols] != month_days[:, None]).any() or (total["snow"][:, rows, cols].sum(axis=2) != month_days[:, None]).any():
        raise ValueError("a kept cell misses temperature or snow days; month_days no longer stands for every cell")
    if (total["gust_n"] != total["dir"].sum(axis=3)).any():
        raise ValueError("gust hours differ from the direction hours; store them")
    accessed = dt.date.today().isoformat()
    MONTHLY.write_text(json.dumps({
        "source_id": "era5-arco-surface",
        "as_of": accessed,
        "seasons": seasons,
        "months": list(MONTHS),
        "month_days": month_days.tolist(),
        "hours_utc": list(HOURS_UTC),
        "daily_hour_utc": DAILY_HOUR_UTC,
        "grid": {"step_deg": 0.25, "lat_max": LAT_MAX, "lon_min": LON_MIN},
        "speed_edges_ms": MONTHLY_SPEED_EDGES_MS,
        "dir_min_ms": DIR_MIN_MS,
        "dir_sectors": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "snow_edges_m_we": SNOW_EDGES_M,
        "ice_edges_m": ICE_EDGES_M,
        "keep": {"months": list(KEEP_MONTHS), "share": KEEP_SHARE, "snow_m_we": KEEP_SNOW_M_WE, "ice_m": KEEP_ICE_M,
                 "month_share": KEEP_MONTH_SHARE, "lake_cover_pct": KEEP_LAKE_COVER_PCT, "us_cells": len(cells)},
        "layout": ("cells.<field>[cell] for row, col, state and the static fields; cells.<field>[cell][month] for the "
                   "rest, months in `months` order. speed, dir and gust_ratio_x100_sum are null in a month that fails "
                   "the keep rule at month_share (no snow and no lake ice). ice is null in a cell with zero lake "
                   "cover. Gust hours equal the sum of dir. Temperature days equal month_days. Speed sums to "
                   "3 x month_days."),
        "cells": {
            "row": rows, "col": cols, "state": [code for _, _, code in kept],
            "lake_cover_pct": lake,
            "high_veg_cover_pct": [int(round(float(x) * 100)) for x in static["cvh"][rows, cols]],
            "land_pct": [int(round(float(x) * 100)) for x in static["lsm"][rows, cols]],
            "elevation_m": [int(round(float(x))) for x in static["elevation_m"][rows, cols]],
            "speed": wind(total["speed"]),
            "dir": wind(total["dir"]),
            "gust_ratio_x100_sum": wind(np.rint(total["gust_sum"] * 100).astype(np.int64)),
            "snow": snow,
            "ice": [x if lk > 0 else None for x, lk in zip(ice, lake)],
            "temp_c_x10_sum": per_cell(np.rint(total["temp_sum"] * 10).astype(np.int64)),
            "thaw_days": per_cell(total["thaw"]),
        },
    }, separators=(",", ":")) + "\n")
    _write_source(accessed)
    import gzip

    raw = MONTHLY.read_bytes()
    print(f"wrote {MONTHLY.relative_to(ROOT)}: {len(kept)} of {len(cells)} US cells, {len(seasons)} seasons, "
          f"{len(raw) / 1e6:.2f} MB raw, {len(gzip.compress(raw)) / 1e6:.2f} MB gzipped")


def summary() -> None:
    for path in (EXTRACT, MONTHLY):
        if path.exists():
            d = json.loads(path.read_text())
            print(path.name + ":", len(d["cells"]["row"]), "cells;", d["seasons"][0], "to", d["seasons"][-1],
                  "; months", d["months"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.climate.era5_surface")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "fetch-monthly"):
        f = sub.add_parser(name)
        f.add_argument("--first", type=int, default=2015)
        f.add_argument("--last", type=int, default=2024)
        f.add_argument("--workers", type=int, default=16)
    sub.add_parser("summary")
    args = p.parse_args(argv)
    if args.cmd == "summary":
        summary()
    else:
        (fetch if args.cmd == "fetch" else fetch_monthly)(args.first, args.last, args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
