"""ERA5 250 hPa winds over North America, averaged per ski season (November to April), for the jet stream layer.

The jet stream layer (docs/map/JETSTREAM.md) needs the mean 250 hPa wind for every ski season since 1979/80.
ECMWF's ERA5 reanalysis is the source. Google's Analysis Ready, Cloud Optimized ERA5 bucket (ARCO ERA5, public,
no account) keeps the original ERA5 netCDF files, one per day, variable and pressure level:

    https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-pressure_level/YYYY/MM/DD/<variable>/250.nc

Each file through February 2026 is netCDF 3 (64 bit offset): 24 hourly fields of 721 x 1440 packed int16 values on the 0.25 degree grid,
latitude 90 to -90, longitude 0 to 359.75. The fetcher reads the header (scale and offset differ per file) and then
only the rows between ``LAT_MAX`` and ``LAT_MIN`` for one hour, with HTTP range requests, so a season costs about
250 MB instead of 18 GB.

Sampling: one hour per day, 00 UTC on even days of the year and 12 UTC on odd days, every day from November 1 to
April 30. The season mean of u and v is taken on the native grid, then averaged into 1 degree boxes (trapezoid
weights over the 0.25 degree points from L - 0.5 to L + 0.5) on the grid ``LAT_MIN..LAT_MAX`` by
``LON_MIN..LON_MAX``.

Commands::

    python -m pipeline.climate.era5_jet fetch [--first 1979] [--last 2025] [--workers 16]
    python -m pipeline.climate.era5_jet summary

``fetch`` needs numpy and, for the netCDF 4 files ERA5 writes from about March 2026 on, h5py
(``pip install numpy h5py``); the build needs neither. Per season partial results go to
``data/raw/climate/era5_jet/download/`` (gitignored) so an interrupted fetch resumes. The committed extract is
``data/raw/climate/era5_jet/season_means_250hpa.json`` plus its source record ``source.json``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import io
import json
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "climate" / "era5_jet"
DOWNLOAD_DIR = OUT_DIR / "download"
EXTRACT = OUT_DIR / "season_means_250hpa.json"
SOURCE = OUT_DIR / "source.json"

BASE = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-pressure_level"
VARIABLES = {"u": "u_component_of_wind", "v": "v_component_of_wind"}
LEVEL_HPA = 250
USER_AGENT = "SnowsportsCareerMapResearch/1.0"

# Output grid, whole degrees. West of Alaska to east of Maine, Mexico to the Arctic coast.
LAT_MIN, LAT_MAX = 15, 75
LON_MIN, LON_MAX = -170, -50
SEASON_MONTHS = (11, 12, 1, 2, 3, 4)
HEADER_BYTES = 32768

NC_TYPES = {1: ("b", 1), 2: ("c", 1), 3: ("h", 2), 4: ("i", 4), 5: ("f", 4), 6: ("d", 8)}


# ----------------------------------------------------------------------------------------------- netCDF 3 header


def _pad4(n: int) -> int:
    return (n + 3) & ~3


class _Reader:
    def __init__(self, buf: bytes, version: int):
        self.buf, self.pos, self.version = buf, 0, version

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise EOFError("netCDF header is longer than the bytes read")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def int(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def offset(self) -> int:
        return struct.unpack(">q", self.take(8))[0] if self.version == 2 else struct.unpack(">i", self.take(4))[0]

    def name(self) -> str:
        n = self.int()
        s = self.take(_pad4(n))[:n]
        return s.decode("utf-8")

    def values(self, nc_type: int, n: int) -> Any:
        code, size = NC_TYPES[nc_type]
        raw = self.take(_pad4(n * size))[: n * size]
        if code == "c":
            return raw.decode("utf-8", "replace")
        vals = struct.unpack(f">{n}{code}", raw)
        return vals[0] if n == 1 else list(vals)

    def atts(self) -> dict[str, Any]:
        tag, n = self.int(), self.int()
        if tag == 0:
            return {}
        if tag != 0x0C:
            raise ValueError(f"expected an attribute list, got tag {tag:#x}")
        out = {}
        for _ in range(n):
            name = self.name()
            nc_type, count = self.int(), self.int()
            out[name] = self.values(nc_type, count)
        return out


def parse_header(buf: bytes) -> dict[str, Any]:
    """Dimensions, variables (dims, type, attributes, begin) and the record size of a netCDF 3 file."""
    if buf[:3] != b"CDF" or buf[3] not in (1, 2):
        raise ValueError("not a netCDF 3 file")
    r = _Reader(buf, buf[3])
    r.take(4)
    numrecs = r.int()
    dims: list[tuple[str, int]] = []
    tag, n = r.int(), r.int()
    if tag == 0x0A:
        for _ in range(n):
            dims.append((r.name(), r.int()))
    r.atts()  # global attributes
    variables: dict[str, dict[str, Any]] = {}
    tag, n = r.int(), r.int()
    if tag == 0x0B:
        for _ in range(n):
            name = r.name()
            ndims = r.int()
            dimids = [r.int() for _ in range(ndims)]
            atts = r.atts()
            nc_type, vsize, begin = r.int(), r.int(), r.offset()
            variables[name] = {"dims": [dims[i] for i in dimids], "type": nc_type, "atts": atts, "vsize": vsize, "begin": begin}
    record_vars = [v for v in variables.values() if v["dims"] and v["dims"][0][1] == 0]
    if len(record_vars) == 1:
        recsize = record_vars[0]["vsize"]
        if NC_TYPES[record_vars[0]["type"]][1] < 4:  # a single small record variable is not padded
            recsize = 1
            for _, size in record_vars[0]["dims"][1:]:
                recsize *= size
            recsize *= NC_TYPES[record_vars[0]["type"]][1]
    else:
        recsize = sum(v["vsize"] for v in record_vars)
    return {"numrecs": numrecs, "dims": dims, "vars": variables, "recsize": recsize}


def field_offset(header: dict[str, Any], var: str, t: int, row: int) -> int:
    """Byte offset of (time t, latitude row) of a (time, latitude, longitude) variable."""
    v = header["vars"][var]
    (_, _), (_, nlat), (_, nlon) = v["dims"]
    size = NC_TYPES[v["type"]][1]
    if v["dims"][0][1] == 0:  # record variable
        return v["begin"] + t * header["recsize"] + row * nlon * size
    return v["begin"] + ((t * nlat) + row) * nlon * size


# ----------------------------------------------------------------------------------------------- HTTP


def _get(url: str, start: int, end: int, attempts: int = 6) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) != end - start + 1:
                raise IOError(f"short read {len(data)} of {end - start + 1}")
            return data
        except urllib.error.HTTPError as err:
            if err.code == 404:
                raise FileNotFoundError(url) from err
            if attempt == attempts - 1:
                raise
        except Exception:  # noqa: BLE001 - network hiccups: retry with backoff
            if attempt == attempts - 1:
                raise
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError("unreachable")


def season_days(start_year: int) -> list[dt.date]:
    """November 1 of start_year to April 30 of the next year."""
    day, end = dt.date(start_year, 11, 1), dt.date(start_year + 1, 4, 30)
    out = []
    while day <= end:
        out.append(day)
        day += dt.timedelta(days=1)
    return out


def sample_hour(day: dt.date) -> int:
    return 0 if day.timetuple().tm_yday % 2 == 0 else 12


def _read_day(day: dt.date, var: str, rows: tuple[int, int]) -> tuple[Any, dict[str, Any]]:
    import numpy as np

    url = f"{BASE}/{day:%Y/%m/%d}/{VARIABLES[var]}/{LEVEL_HPA}.nc"
    head = _get(url, 0, HEADER_BYTES - 1)
    if head[:4] == b"\x89HDF":  # files from about March 2026 on are netCDF 4
        return _read_day_hdf5(url, day, var, rows)
    header = parse_header(head)
    name = var
    if name not in header["vars"]:
        raise KeyError(f"{url} has no variable {name!r}: {list(header['vars'])}")
    v = header["vars"][name]
    (_, nt), (_, nlat), (_, nlon) = v["dims"]
    hour = sample_hour(day)
    # The time axis sits inside the bytes already read: check that index `hour` is that hour of that day.
    tv = header["vars"]["time"]
    if tv["begin"] + tv["vsize"] <= len(head) and tv["type"] == 4 and v["dims"][0][1] != 0:
        times = struct.unpack(f">{nt}i", head[tv["begin"] : tv["begin"] + 4 * nt])
        stamp = dt.datetime(1900, 1, 1) + dt.timedelta(hours=times[hour])
        if (stamp.date(), stamp.hour) != (day, hour):
            raise ValueError(f"{url}: time index {hour} is {stamp}, expected {day} {hour:02d}:00")
    start = field_offset(header, name, hour, rows[0])
    nbytes = (rows[1] - rows[0] + 1) * nlon * 2
    raw = np.frombuffer(_get(url, start, start + nbytes - 1), dtype=">i2").reshape(rows[1] - rows[0] + 1, nlon)
    atts = v["atts"]
    vals = raw.astype(np.float64) * float(atts.get("scale_factor", 1.0)) + float(atts.get("add_offset", 0.0))
    for key in ("_FillValue", "missing_value"):
        if key in atts:
            vals[raw == int(atts[key])] = np.nan
    if v["type"] != 3 or [d for d, _ in v["dims"]] != ["time", "latitude", "longitude"]:
        raise ValueError(f"{url}: expected packed int16 (time, latitude, longitude), got type {v['type']} {v['dims']}")
    meta = {"nlat": nlat, "nlon": nlon, "nt": header["numrecs"] if v["dims"][0][1] == 0 else nt, "hour": hour}
    return vals, meta


class _HttpFile(io.RawIOBase):
    """A read only file over HTTP range requests, in cached blocks, for h5py."""

    def __init__(self, url: str, block: int = 1 << 20):
        self.url, self.block, self.pos, self.cache = url, block, 0, {}
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.size = int(resp.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, buf: Any) -> int:
        want, out = len(buf), bytearray()
        while len(out) < want and self.pos < self.size:
            i = self.pos // self.block
            if i not in self.cache:
                start = i * self.block
                self.cache[i] = _get(self.url, start, min(self.size, start + self.block) - 1)
            o = self.pos - i * self.block
            piece = self.cache[i][o : o + want - len(out)]
            out += piece
            self.pos += len(piece)
        buf[: len(out)] = out
        return len(out)


_H5_LOCK = threading.Lock()


def _read_day_hdf5(url: str, day: dt.date, var: str, rows: tuple[int, int]) -> tuple[Any, dict[str, Any]]:
    """netCDF 4 day file: (valid_time 24, pressure_level 1, latitude 721, longitude 1440) float32, gzip chunks of
    12 x 1 x 361 x 720. Reads only the longitudes of the output box (one chunk); the rest stays NaN."""
    import h5py
    import numpy as np

    hour = sample_hour(day)
    lon_lo = int(((LON_MIN - 0.5) % 360) / 0.25)
    lon_hi = int(((LON_MAX + 0.5) % 360) / 0.25)
    with _H5_LOCK:  # h5py reads through Python callbacks; one file at a time
        fh = _HttpFile(url)
        with h5py.File(fh, "r") as h:
            stamp = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=int(h["valid_time"][hour]))
            if (stamp.date(), stamp.hour) != (day, hour):
                raise ValueError(f"{url}: time index {hour} is {stamp}, expected {day} {hour:02d}:00")
            ds = h[var]
            nlat, nlon = ds.shape[2], ds.shape[3]
            lat = h["latitude"]
            if (nlat, nlon) != (721, 1440) or float(lat[0]) != 90.0 or float(lat[1]) != 89.75 or float(h["longitude"][0]) != 0.0:
                raise ValueError(f"{url}: unexpected grid {ds.shape}")
            if "scale_factor" in ds.attrs or "add_offset" in ds.attrs or ds.dtype.kind != "f":
                raise ValueError(f"{url}: expected plain float values, got {ds.dtype} {dict(ds.attrs)}")
            part = ds[hour, 0, rows[0] : rows[1] + 1, lon_lo : lon_hi + 1].astype(np.float64)
    vals = np.full((rows[1] - rows[0] + 1, nlon), np.nan)
    vals[:, lon_lo : lon_hi + 1] = part
    return vals, {"nlat": nlat, "nlon": nlon, "nt": 24, "hour": hour}


def _box_average(field: Any, lat_axis: Any, lon_axis: Any) -> Any:
    """Trapezoid 1 degree box means of a 0.25 degree field (lat descending, lon -180..180 ascending)."""
    import numpy as np

    lats = np.arange(LAT_MIN, LAT_MAX + 1)
    lons = np.arange(LON_MIN, LON_MAX + 1)
    out = np.full((len(lats), len(lons)), np.nan)
    w = np.array([0.5, 1, 1, 1, 0.5])
    for i, la in enumerate(lats):
        ri = [int(round((lat_axis[0] - (la + d)) / 0.25)) for d in (0.5, 0.25, 0, -0.25, -0.5)]
        ri = [r for r in ri if 0 <= r < len(lat_axis)]
        for j, lo in enumerate(lons):
            ci = [int(round(((lo + d) - lon_axis[0]) / 0.25)) for d in (-0.5, -0.25, 0, 0.25, 0.5)]
            block = field[np.ix_(ri, ci)]
            ww = np.outer(w[: len(ri)], w)
            ok = np.isfinite(block)
            out[i, j] = (block[ok] * ww[ok]).sum() / ww[ok].sum() if ok.any() else np.nan
    return out


def fetch_season(start_year: int, workers: int) -> dict[str, Any]:
    import numpy as np

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cache = DOWNLOAD_DIR / f"{start_year}-{start_year + 1}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    # Native rows: latitude 90 - 0.25 * row. Two extra rows each side for the box averages.
    rows = (int((90 - LAT_MAX - 0.5) / 0.25), int((90 - LAT_MIN + 0.5) / 0.25))
    lat_axis = 90 - 0.25 * np.arange(rows[0], rows[1] + 1)
    days = season_days(start_year)
    sums = {k: None for k in VARIABLES}
    counts = {k: None for k in VARIABLES}
    hours: dict[int, int] = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_read_day, day, var, rows): (day, var) for day in days for var in VARIABLES}
        for fut in cf.as_completed(futures):
            day, var = futures[fut]
            vals, meta = fut.result()
            if meta["nlon"] != 1440 or meta["nlat"] != 721:
                raise ValueError(f"{day} {var}: unexpected grid {meta}")
            if sums[var] is None:
                sums[var] = np.zeros_like(vals)
                counts[var] = np.zeros_like(vals)
            ok = np.isfinite(vals)
            sums[var][ok] += vals[ok]
            counts[var][ok] += 1
            if var == "u":
                hours[meta["hour"]] = hours.get(meta["hour"], 0) + 1
    result: dict[str, Any] = {"season": f"{start_year}/{str(start_year + 1)[2:]}", "days": len(days), "hours_utc": hours}
    # Longitudes 0..359.75 east; reorder to -180..179.75.
    lon_east = 0.25 * np.arange(1440)
    order = np.argsort(np.where(lon_east >= 180, lon_east - 360, lon_east))
    lon_axis = np.where(lon_east >= 180, lon_east - 360, lon_east)[order]
    for var in VARIABLES:
        mean = sums[var] / np.where(counts[var] > 0, counts[var], np.nan)
        boxed = _box_average(mean[:, order], lat_axis, lon_axis)
        result[var] = [[None if not np.isfinite(x) else round(float(x), 2) for x in row] for row in boxed]
    result["seconds"] = round(time.time() - t0, 1)
    cache.write_text(json.dumps(result))
    return result


def fetch(first: int, last: int, workers: int) -> None:
    """Fetch seasons first..last and merge them into the committed extract (seasons outside the range stay)."""
    kept: dict[str, dict] = {}
    if EXTRACT.exists():
        kept = {s["season"]: s for s in json.loads(EXTRACT.read_text())["seasons"]}
    for year in range(first, last + 1):
        t0 = time.time()
        season = fetch_season(year, workers)
        kept[season["season"]] = season
        print(f"{year}/{str(year + 1)[2:]}: {season['days']} days, {time.time() - t0:.0f} s", flush=True)
    seasons = [kept[k] for k in sorted(kept)]
    accessed = dt.date.today().isoformat()
    EXTRACT.write_text(
        json.dumps(
            {
                "source_id": "era5-arco-250hpa-winds",
                "as_of": accessed,
                "variable": "250 hPa wind, m/s; u eastward, v northward",
                "grid": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lon_min": LON_MIN, "lon_max": LON_MAX, "step_deg": 1},
                "rows": "u[lat][lon] and v[lat][lon], lat ascending from lat_min, lon ascending from lon_min",
                "sampling": "one hour per day (00 UTC on even days of the year, 12 UTC on odd days), November 1 to April 30",
                "seasons": [{k: s[k] for k in ("season", "days", "hours_utc", "u", "v")} for s in seasons],
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    SOURCE.write_text(
        json.dumps(
            {
                "source": {
                    "id": "era5-arco-250hpa-winds",
                    "url": "https://cloud.google.com/storage/docs/public-datasets/era5",
                    "title": "ERA5 hourly data on pressure levels (ECMWF, Copernicus Climate Change Service), ARCO ERA5 raw files on Google Cloud",
                    "accessed_date": accessed,
                    "note": (
                        "u_component_of_wind and v_component_of_wind at 250 hPa, read with HTTP range requests from "
                        f"{BASE}/YYYY/MM/DD/<variable>/250.nc. Hersbach et al. (2020), ERA5, doi:10.24381/cds.bd0915c6. "
                        "Contains modified Copernicus Climate Change Service information."
                    ),
                },
                "confidence": "published",
                "license": "Copernicus licence (CC BY 4.0 compatible); attribution required",
                "seasons": [s["season"] for s in seasons],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {EXTRACT.relative_to(ROOT)} ({len(seasons)} seasons)")


def summary() -> None:
    data = json.loads(EXTRACT.read_text())
    print(len(data["seasons"]), "seasons:", data["seasons"][0]["season"], "to", data["seasons"][-1]["season"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.climate.era5_jet")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--first", type=int, default=1979, help="first season start year (November)")
    f.add_argument("--last", type=int, default=2025, help="last season start year")
    f.add_argument("--workers", type=int, default=16)
    sub.add_parser("summary")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        fetch(args.first, args.last, args.workers)
    else:
        summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
