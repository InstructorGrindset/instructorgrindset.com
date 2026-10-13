"""NRCS SNOTEL station snowfall for the snowfall heat map (ROADMAP Section 8, item 14).

NOAA normals stations sit mostly in valleys, so the heat map background undercounts mountain snow. SNOTEL
stations sit on the mountains. They measure snow water equivalent (WTEQ) and snow depth (SNWD), not
snowfall, and AWDB publishes no snowfall element or snowfall normal for them (its WTEQ and SNWD normals are
pack states, not new snow). So each station's annual snowfall is derived with the method the climate ETL
already uses (:mod:`pipeline.climate.snotel_snowfall`):

    daily new snow (in) = daily SWE gain (in) / new snow density (fraction)

the SNOW-17 step ``HCN = PX / DHN`` with every ``climate.snotel`` threshold of config/definitions.yaml. The
density is the station's own median storm density for the calendar month (``climate.min_storms_per_month``
QC passed storms, :mod:`pipeline.climate.snotel`), else the Anderson (1976) estimate from the station's own
daily mean air temperature (TAVG). The climate ETL feeds the same estimate ERA5 temperature moved to the
station elevation; here the station thermometer stands in, so no lapse rate step is needed.

Per station, the water year totals (Oct 1 to Sep 30, named by the September year) from
``heat.snowfall.snotel.first_water_year`` to ``last_water_year`` are summed from the derived days. A water
year is complete when its snow months (Nov to Apr) have ``climate.min_month_coverage`` of their days with
data. The annual figure is the median of the complete years, published only with at least
``heat.snowfall.snotel.min_complete_years``. The median keeps one odd winter or a sensor fault from
dragging the figure.

API (no key): https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/

* ``GET /stations?stationTriplets=*:*:SNTL`` lists every SNOTEL station (triplet, name, latitude,
  longitude, elevation in feet, beginDate, endDate).
* ``GET /data?stationTriplets=<one>&elements=WTEQ,SNWD,TAVG&duration=DAILY&beginDate&endDate`` gives the
  daily values. Requests run one at a time with a pause, through :mod:`pipeline.climate.http` (retries
  with backoff, the project's generic User-Agent).

Commands::

    python -m pipeline.climate.snotel_stations fetch      # station list and daily data into the cache
    python -m pipeline.climate.snotel_stations extract    # derive and write the compact extract
    python -m pipeline.climate.snotel_stations validate   # compare with published resort snowfall
    python -m pipeline.climate.snotel_stations summary

The daily downloads stay in ``data/raw/climate/snotel_snowfall/download/`` (gitignored, gzip JSON). The
committed extract is ``data/raw/climate/snotel_snowfall/snotel_annual_snowfall.csv`` plus ``source.json``.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import yaml

from pipeline.climate import http, snotel
from pipeline.climate.aggregate import DailyRecord
from pipeline.climate.density import monthly_density
from pipeline.climate.seasons import season_of
from pipeline.climate.snotel_snowfall import SNOW_MONTHS, derive_new_snow

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "data" / "raw" / "climate" / "snotel_snowfall"
EXTRACT_NAME = "snotel_annual_snowfall.csv"
SOURCE_NAME = "source.json"
SOURCE_ID = "nrcs-snotel-awdb-annual-snowfall"
METHOD = "swe_gain_over_density_median"
ELEMENTS = ("WTEQ", "SNWD", "TAVG")
COLUMNS = ["station_id", "name", "state", "lat", "lon", "elevation_ft", "annual_snowfall_in", "years_used",
           "complete_years", "first_water_year", "last_water_year", "method"]
# SNOTEL air temperature outside this range (deg F) is a sensor fault.
TAVG_RANGE_F = (-60.0, 110.0)
PAUSE_S = 0.3   # pause between requests


@dataclass(frozen=True)
class StationSnowfall:
    station_id: str          # AWDB triplet, e.g. 766:UT:SNTL
    name: str
    state: str
    lat: float
    lon: float
    elevation_ft: Optional[float]
    annual_snowfall_in: Optional[float]
    years_used: int          # complete water years in the median (0 when the station falls short)
    complete_years: str      # the complete water years, space separated
    first_water_year: int
    last_water_year: int
    method: str = METHOD


# ---------------------------------------------------------------- settings
def heat_snotel_settings(defs: Mapping[str, Any]) -> dict[str, int]:
    block = (((defs.get("heat") or {}).get("snowfall") or {}).get("snotel")) or {}
    out = {}
    for k in ("first_water_year", "last_water_year", "min_complete_years"):
        if not isinstance(block.get(k), int):
            raise ValueError(f"config/definitions.yaml heat.snowfall.snotel.{k} must be an integer")
        out[k] = int(block[k])
    if out["last_water_year"] < out["first_water_year"]:
        raise ValueError("heat.snowfall.snotel.last_water_year must not be before first_water_year")
    return out


def _load_defs(config_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((config_dir / "definitions.yaml").read_text(encoding="utf-8"))


def period_for(first_wy: int, last_wy: int) -> tuple[dt.date, dt.date]:
    """Request window: the day before water year ``first_wy`` starts (its first SWE gain needs the day
    before) to the last day of ``last_wy``."""
    return dt.date(first_wy - 1, 9, 30), dt.date(last_wy, 9, 30)


# ---------------------------------------------------------------- stations
def parse_station_list(payload: Any) -> list[snotel.Station]:
    """SNTL stations with coordinates from an AWDB /stations response (other networks dropped)."""
    return snotel.parse_stations(payload)


def possible_years(st: snotel.Station, first_wy: int, last_wy: int) -> int:
    """Water years of the window inside the station's metadata record."""
    n = 0
    for wy in range(first_wy, last_wy + 1):
        s0, s1 = dt.date(wy - 1, 10, 1), dt.date(wy, 9, 30)
        if (st.begin_date is None or st.begin_date <= s0) and (st.end_date is None or st.end_date >= s1):
            n += 1
    return n


# ---------------------------------------------------------------- daily data
def parse_daily(payload: Any, triplet: str) -> tuple[dict[dt.date, tuple[Optional[float], Optional[float]]],
                                                     dict[dt.date, float]]:
    """({date: (swe_in, depth_in)}, {date: tavg_f}) from an AWDB /data response.

    SWE and depth readings below zero become None (as :func:`snotel.parse_station_daily`); air temperatures
    keep their sign and drop outside ``TAVG_RANGE_F``.
    """
    series = snotel.parse_station_daily(payload, triplet)
    tavg: dict[dt.date, float] = {}
    for station in payload or []:
        if station.get("stationTriplet") not in (None, triplet):
            continue
        for block in station.get("data", []):
            if (block.get("stationElement") or {}).get("elementCode") != "TAVG":
                continue
            for v in block.get("values", []):
                try:
                    d = dt.date.fromisoformat(str(v.get("date"))[:10])
                except ValueError:
                    continue
                value = v.get("value")
                if value is None:
                    continue
                f = float(value)
                if TAVG_RANGE_F[0] <= f <= TAVG_RANGE_F[1]:
                    tavg[d] = f
    return series, tavg


def temperature_records(tavg: Mapping[dt.date, float]) -> dict[dt.date, DailyRecord]:
    """The station's daily mean air temperature as the records ``derive_new_snow`` reads (tmean_f)."""
    return {d: DailyRecord(date=d, tmax_f=t, tmin_f=t) for d, t in tavg.items()}


def water_year_totals(new_snow: Mapping[dt.date, Optional[float]], first_wy: int, last_wy: int,
                      min_coverage: float) -> dict[int, Optional[float]]:
    """Derived new snow summed per water year; None for a year whose snow months (Nov to Apr) have less than
    ``min_coverage`` of their days with data. Days with no data add nothing."""
    days_by_wy: dict[int, list[dt.date]] = {}
    for d in new_snow:
        wy = season_of(d) + 1
        if first_wy <= wy <= last_wy:
            days_by_wy.setdefault(wy, []).append(d)
    out: dict[int, Optional[float]] = {}
    for wy in range(first_wy, last_wy + 1):
        snow_days = 0
        have = 0
        y0 = wy - 1
        # Every calendar day of the snow months counts, present in the series or not.
        for month in SNOW_MONTHS:
            year = y0 if month >= 10 else wy
            start = dt.date(year, month, 1)
            nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
            n = (nxt - start).days
            snow_days += n
            have += sum(1 for k in range(n) if new_snow.get(start + dt.timedelta(days=k)) is not None)
        if snow_days and have >= min_coverage * snow_days - 1e-9:
            out[wy] = round(sum(new_snow[d] or 0.0 for d in days_by_wy.get(wy, []) if new_snow[d] is not None), 2)
        else:
            out[wy] = None
    return out


def median_annual(totals: Mapping[int, Optional[float]], min_years: int) -> tuple[Optional[float], list[int]]:
    """(median of the complete water year totals, the complete years); None below ``min_years`` years."""
    years = sorted(wy for wy, v in totals.items() if v is not None)
    if len(years) < max(1, min_years):
        return None, years
    return round(float(statistics.median(totals[wy] for wy in years)), 1), years


def station_snowfall(st: snotel.Station, payload: Any, climate: Any, first_wy: int, last_wy: int,
                     min_years: int) -> StationSnowfall:
    """Annual snowfall of one station from its AWDB daily response (``climate`` is ClimateSettings)."""
    series, tavg = parse_daily(payload, st.triplet)
    s = climate.snotel
    storms = snotel.detect_snotel_storms(series, s)
    month_density = monthly_density([(x.month, x.density_pct) for x in storms if x.qc is None],
                                    climate.dry_below_pct, climate.moist_above_pct, climate.min_storms_per_month)
    sw = derive_new_snow(series, temperature_records(tavg), None, st.elevation_ft,
                         {m: v.median_pct for m, v in month_density.items()}, s, first_wy - 1, last_wy,
                         climate.min_month_coverage, climate.density_estimate_method)
    totals = water_year_totals(sw.new_snow_in, first_wy, last_wy, climate.min_month_coverage)
    annual, years = median_annual(totals, min_years)
    return StationSnowfall(
        station_id=st.triplet, name=st.name, state=st.state, lat=st.lat, lon=st.lon, elevation_ft=st.elevation_ft,
        annual_snowfall_in=annual, years_used=len(years) if annual is not None else 0,
        complete_years=" ".join(str(y) for y in years), first_water_year=first_wy, last_water_year=last_wy)


# ---------------------------------------------------------------- cache
def cache_dir(directory: Path) -> Path:
    return directory / "download"


def daily_cache_path(directory: Path, triplet: str) -> Path:
    return cache_dir(directory) / f"{triplet.replace(':', '_')}.json.gz"


def load_daily(directory: Path, triplet: str, start: dt.date, end: dt.date) -> Optional[dict]:
    path = daily_cache_path(directory, triplet)
    if not path.is_file():
        return None
    data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if data.get("begin", "9999") > start.isoformat() or data.get("end", "0000") < end.isoformat() \
            or tuple(data.get("elements", ())) != ELEMENTS:
        return None
    return data


def fetch_daily(directory: Path, triplet: str, start: dt.date, end: dt.date) -> dict:
    cached = load_daily(directory, triplet, start, end)
    if cached is not None:
        return cached
    payload = http.get_json(snotel.DATA_URL, {
        "stationTriplets": triplet, "elements": ",".join(ELEMENTS), "duration": "DAILY",
        "beginDate": start.isoformat(), "endDate": end.isoformat(), "periodRef": "END",
        "centralTendencyType": "NONE", "returnFlags": False, "returnOriginalValues": False,
        "returnSuspectData": False})
    data = {"triplet": triplet, "begin": start.isoformat(), "end": end.isoformat(), "elements": list(ELEMENTS),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "response": payload}
    path = daily_cache_path(directory, triplet)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 6))
    tmp.replace(path)
    data["elements"] = list(ELEMENTS)
    return data


def stations_path(directory: Path) -> Path:
    return cache_dir(directory) / "stations.json"


def get_station_list(directory: Path, refresh: bool = False) -> tuple[list[snotel.Station], str]:
    """(SNTL stations, fetched_at) from the cache, else from AWDB."""
    path = stations_path(directory)
    if path.is_file() and not refresh:
        data = json.loads(path.read_text(encoding="utf-8"))
        return parse_station_list(data["response"]), data["fetched_at"]
    payload = http.get_json(snotel.STATIONS_URL, {
        "stationTriplets": "*:*:SNTL", "returnForecastPointMetadata": False, "returnReservoirMetadata": False,
        "returnStationElements": False, "activeOnly": False})
    fetched = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": fetched, "response": payload}), encoding="utf-8")
    return parse_station_list(payload), fetched


def candidates(stations: Iterable[snotel.Station], first_wy: int, last_wy: int, min_years: int) -> list[snotel.Station]:
    """Stations whose metadata record could hold ``min_years`` water years of the window, by triplet."""
    return sorted((s for s in stations if possible_years(s, first_wy, last_wy) >= min_years), key=lambda s: s.triplet)


def fetch_all(directory: Path, cfg: Mapping[str, int], log: Callable[[str], None] = print,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    stations, fetched = get_station_list(directory)
    first, last = cfg["first_water_year"], cfg["last_water_year"]
    start, end = period_for(first, last)
    todo = candidates(stations, first, last, cfg["min_complete_years"])
    done = failed = 0
    for n, st in enumerate(todo, 1):
        if load_daily(directory, st.triplet, start, end) is not None:
            done += 1
            continue
        try:
            fetch_daily(directory, st.triplet, start, end)
            done += 1
        except http.NetworkBlocked:
            raise
        except Exception as e:  # noqa: BLE001  one station's failure must not stop the run
            failed += 1
            log(f"{st.triplet}: {e}")
        if n % 50 == 0:
            log(f"{n} of {len(todo)} stations")
        sleep(PAUSE_S)
    return {"stations": len(stations), "candidates": len(todo), "cached": done, "failed": failed,
            "stations_fetched_at": fetched}


# ---------------------------------------------------------------- extract
def write_extract(rows: Sequence[StationSnowfall], directory: Path, source: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in sorted(rows, key=lambda x: x.station_id):
        w.writerow([r.station_id, r.name, r.state, f"{r.lat:.5f}", f"{r.lon:.5f}",
                    "" if r.elevation_ft is None else f"{r.elevation_ft:.0f}",
                    "" if r.annual_snowfall_in is None else f"{r.annual_snowfall_in:.1f}",
                    r.years_used, r.complete_years, r.first_water_year, r.last_water_year, r.method])
    (directory / EXTRACT_NAME).write_text(buf.getvalue(), encoding="utf-8")
    (directory / SOURCE_NAME).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")


def _num(text: Optional[str]) -> Optional[float]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_extract_row(row: Mapping[str, str]) -> StationSnowfall:
    """One extract CSV row. Raises ValueError for a row without an id or coordinates."""
    lat, lon = _num(row.get("lat")), _num(row.get("lon"))
    sid = (row.get("station_id") or "").strip()
    if not sid or lat is None or lon is None:
        raise ValueError(f"SNOTEL extract row without id or coordinates: {dict(row)}")
    years = _num(row.get("years_used"))
    return StationSnowfall(
        station_id=sid, name=(row.get("name") or "").strip(), state=(row.get("state") or "").strip(),
        lat=lat, lon=lon, elevation_ft=_num(row.get("elevation_ft")),
        annual_snowfall_in=_num(row.get("annual_snowfall_in")), years_used=int(years or 0),
        complete_years=(row.get("complete_years") or "").strip(),
        first_water_year=int(_num(row.get("first_water_year")) or 0),
        last_water_year=int(_num(row.get("last_water_year")) or 0), method=(row.get("method") or "").strip())


def load_extract(directory: Path) -> tuple[list[StationSnowfall], dict]:
    """(stations, source record) from the committed extract. Raises FileNotFoundError when absent."""
    source = json.loads((directory / SOURCE_NAME).read_text(encoding="utf-8"))
    with (directory / EXTRACT_NAME).open(encoding="utf-8", newline="") as fh:
        return [parse_extract_row(r) for r in csv.DictReader(fh)], source


def usable(rows: Iterable[StationSnowfall], min_years: int) -> list[StationSnowfall]:
    """Stations with an annual figure from at least ``min_years`` complete water years and an elevation."""
    return [r for r in rows if r.annual_snowfall_in is not None and r.years_used >= min_years
            and r.elevation_ft is not None]


def source_record(today: dt.date, cfg: Mapping[str, int], stations_fetched_at: str, rows: int, kept: int,
                  climate: Any) -> dict:
    s = climate.snotel
    first, last = cfg["first_water_year"], cfg["last_water_year"]
    return {
        "source": {
            "id": SOURCE_ID,
            "url": "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/",
            "title": "NRCS SNOTEL daily snow water equivalent, snow depth and air temperature (AWDB REST API), "
                     "station annual snowfall derived for the snowfall heat map",
            "accessed_date": today.isoformat(),
            "note": (
                f"Public domain (US government work, USDA NRCS National Water and Climate Center). Derived, not "
                f"measured: SNOTEL measures snow water equivalent and depth, not snowfall. Daily new snow = daily "
                f"SWE gain / new snow density (the SNOW-17 step HCN = PX / DHN), never below the daily depth "
                f"gain, with the climate.snotel thresholds (SWE gain {s.swe_min_daily_gain_in} to "
                f"{s.swe_max_daily_gain_in} in, depth spike {s.max_daily_depth_gain_in} in, cap "
                f"{s.max_daily_new_snow_in} in, rain snow threshold {s.rain_snow_threshold_c} C). Density: the "
                f"station's own monthly median storm density, else the {climate.density_estimate_method} "
                f"estimate from the station's daily mean air temperature (TAVG). Annual figure: the median of "
                f"the water year totals {first} to {last}; a year counts when Nov to Apr have "
                f"{climate.min_month_coverage:.0%} of their days with data, and a station needs "
                f"{cfg['min_complete_years']} such years. Values describe the station elevation."),
        },
        "confidence": "estimated",
        "license": "public domain",
        "method": METHOD,
        "units": {"annual_snowfall_in": "in", "elevation_ft": "ft"},
        "water_years": [first, last],
        "min_complete_years": cfg["min_complete_years"],
        "elements": list(ELEMENTS),
        "stations_listed_at": stations_fetched_at,
        "rows": rows,
        "usable": kept,
    }


def extract(directory: Path, config_dir: Path, today: Optional[dt.date] = None,
            log: Callable[[str], None] = print) -> dict[str, Any]:
    from pipeline.climate.definitions import climate_settings  # noqa: PLC0415

    defs = _load_defs(config_dir)
    cfg = heat_snotel_settings(defs)
    climate = climate_settings(defs)
    stations, fetched = get_station_list(directory)
    first, last = cfg["first_water_year"], cfg["last_water_year"]
    start, end = period_for(first, last)
    rows = []
    for st in candidates(stations, first, last, cfg["min_complete_years"]):
        data = load_daily(directory, st.triplet, start, end)
        if data is None:
            log(f"{st.triplet}: not cached; run fetch first")
            continue
        rows.append(station_snowfall(st, data["response"], climate, first, last, cfg["min_complete_years"]))
    kept = len(usable(rows, cfg["min_complete_years"]))
    write_extract(rows, directory, source_record(today or dt.date.today(), cfg, fetched, len(rows), kept, climate))
    return {"rows": len(rows), "usable": kept}


# ---------------------------------------------------------------- validation
@dataclass(frozen=True)
class Match:
    resort_id: str
    resort: str
    state: str
    published_in: float
    published_source_id: str
    resort_elevation_ft: float
    station_id: str
    station: str
    distance_km: float
    station_elevation_ft: float
    station_in: float

    @property
    def diff_pct(self) -> float:
        return 100.0 * (self.station_in - self.published_in) / self.published_in


def _sourced_number(field: Any) -> Optional[float]:
    if isinstance(field, dict) and isinstance(field.get("value"), (int, float)) and field.get("source_id"):
        return float(field["value"])
    return None


def resort_reference_ft(resort: Mapping[str, Any]) -> Optional[float]:
    """The resort elevation a published snowfall figure is compared at: the middle of the sourced base and
    summit, else whichever of the two is sourced."""
    el = resort.get("elevation") or {}
    base, summit = _sourced_number(el.get("base_ft")), _sourced_number(el.get("summit_ft"))
    if base is not None and summit is not None:
        return (base + summit) / 2
    return base if base is not None else summit


def match_resorts(resorts: Iterable[Mapping[str, Any]], stations: Sequence[StationSnowfall], max_km: float,
                  max_elev_m: float) -> list[Match]:
    """Resorts with a published annual snowfall and a usable station within ``max_km`` and ``max_elev_m`` of
    the resort's reference elevation (the nearest such station)."""
    out = []
    for r in resorts:
        pub = (r.get("snow") or {}).get("annual_avg_in")
        value = _sourced_number(pub)
        ref = resort_reference_ft(r)
        if value is None or value <= 0 or ref is None or r.get("lat") is None or r.get("lon") is None:
            continue
        best = None
        for st in stations:
            if st.annual_snowfall_in is None or st.elevation_ft is None:
                continue
            d = snotel.haversine_km(float(r["lat"]), float(r["lon"]), st.lat, st.lon)
            if d > max_km or abs(st.elevation_ft - ref) * 0.3048 > max_elev_m:
                continue
            if best is None or d < best[0]:
                best = (d, st)
        if best is None:
            continue
        d, st = best
        out.append(Match(resort_id=str(r.get("id")), resort=str(r.get("name") or r.get("id")),
                         state=str(r.get("state") or ""), published_in=value,
                         published_source_id=str(pub.get("source_id")), resort_elevation_ft=ref,
                         station_id=st.station_id, station=st.name, distance_km=round(d, 1),
                         station_elevation_ft=float(st.elevation_ft), station_in=float(st.annual_snowfall_in)))
    return sorted(out, key=lambda m: m.resort)


def summarize(matches: Sequence[Match]) -> dict[str, Optional[float]]:
    """Median signed and absolute percent difference, the median ratio, and the share within 25 and 50 percent."""
    if not matches:
        return {"n": 0, "median_diff_pct": None, "median_abs_diff_pct": None, "median_ratio": None,
                "within_25_pct": None, "within_50_pct": None}
    diffs = [m.diff_pct for m in matches]
    return {
        "n": len(matches),
        "median_diff_pct": round(statistics.median(diffs), 1),
        "median_abs_diff_pct": round(statistics.median(abs(x) for x in diffs), 1),
        "median_ratio": round(statistics.median(m.station_in / m.published_in for m in matches), 3),
        "within_25_pct": round(sum(abs(x) <= 25 for x in diffs) / len(diffs), 3),
        "within_50_pct": round(sum(abs(x) <= 50 for x in diffs) / len(diffs), 3),
    }


VALIDATION_MARKER = "## Snowfall heat map: SNOTEL station snowfall"


def validation_section(matches: Sequence[Match], noaa_pairs: Sequence[dict], summary: dict, noaa_summary: dict,
                       stations: int, usable_n: int, cfg: Mapping[str, int], max_km: float, max_elev_m: float,
                       generated_at: str) -> str:
    def pct(x: Optional[float]) -> str:
        return "" if x is None else f"{x:+.0f}%"

    def share(x: Optional[float]) -> str:
        return "" if x is None else f"{x * 100:.0f}%"

    lines = [
        VALIDATION_MARKER,
        "",
        f"Generated by `python -m pipeline.climate.snotel_stations validate` at {generated_at}. Do not edit by hand.",
        "",
        f"The snowfall heat map blends NRCS SNOTEL mountain stations with the NOAA normals. Each station's annual "
        f"snowfall is the median of its water year totals {cfg['first_water_year']} to {cfg['last_water_year']} of "
        f"daily SWE gain divided by new snow density (the same SNOW-17 step and `climate.snotel` parameters as "
        f"`snotel_swe` above), from water years whose November to April days are at least "
        f"`climate.min_month_coverage` complete, with at least {cfg['min_complete_years']} such years. "
        f"{usable_n} of {stations} SNOTEL stations qualify. AWDB publishes no snowfall element or snowfall "
        f"normal for SNOTEL; its SWE and snow depth normals describe the pack on the ground, not new snow, so "
        f"the derived figure is the better fit.",
        "",
        f"Comparison: every resort with a published `snow.annual_avg_in` and a qualifying station within "
        f"{max_km:.0f} km and {max_elev_m:.0f} m of the resort's middle elevation (mean of the sourced base and "
        f"summit, else the one that is sourced). The nearest such station is used. The shipping rule: the "
        f"median absolute difference must stay at or under 50 percent.",
        "",
        f"Summary: {summary['n']} resorts. Median difference {pct(summary['median_diff_pct'])}, median absolute "
        f"difference {abs(summary['median_abs_diff_pct'] or 0):.0f}%, median ratio "
        f"{summary['median_ratio'] if summary['median_ratio'] is not None else ''}; "
        f"{share(summary['within_25_pct'])} within 25 percent and {share(summary['within_50_pct'])} within "
        f"50 percent. Result: {'ships' if (summary['median_abs_diff_pct'] or 0) <= 50 and summary['n'] else 'held back'}.",
        "",
        "| Resort | State | Published (in) | Published source | Resort mid elevation (ft) | Station | Distance (km) "
        "| Station elevation (ft) | Station derived (in) | Difference |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in matches:
        lines.append(f"| {m.resort} | {m.state} | {m.published_in:,.0f} | {m.published_source_id} | "
                     f"{m.resort_elevation_ft:,.0f} | {m.station} ({m.station_id}) | {m.distance_km:.1f} | "
                     f"{m.station_elevation_ft:,.0f} | {m.station_in:,.0f} | {pct(m.diff_pct)} |")
    lines += [
        "",
        f"Cross check against measured snowfall: NOAA normals stations (the heat map background) within "
        f"{max_km:.0f} km and {max_elev_m:.0f} m of a qualifying SNOTEL station. NOAA measures snowfall with a "
        f"ruler, so this pair has no marketing in it.",
        "",
        f"Summary: {noaa_summary['n']} pairs. Median difference {pct(noaa_summary['median_diff_pct'])}, median "
        f"absolute difference {abs(noaa_summary['median_abs_diff_pct'] or 0):.0f}%; "
        f"{share(noaa_summary['within_50_pct'])} within 50 percent.",
        "",
        "| NOAA station | NOAA normal (in) | NOAA elevation (m) | SNOTEL station | Distance (km) | SNOTEL elevation (m) "
        "| SNOTEL derived (in) | Difference |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in noaa_pairs:
        lines.append(f"| {p['noaa_name']} ({p['noaa_id']}) | {p['noaa_in']:,.0f} | {p['noaa_elevation_m']:,.0f} | "
                     f"{p['snotel_name']} ({p['snotel_id']}) | {p['distance_km']:.1f} | {p['snotel_elevation_m']:,.0f} "
                     f"| {p['snotel_in']:,.0f} | {pct(p['diff_pct'])} |")
    return "\n".join(lines) + "\n"


def noaa_pairs(noaa: Iterable[Any], stations: Sequence[StationSnowfall], max_km: float,
               max_elev_m: float) -> list[dict]:
    """NOAA normals stations (with elevation and a positive normal) paired with the nearest qualifying SNOTEL
    station within ``max_km`` and ``max_elev_m``."""
    out = []
    for n in noaa:
        if n.elevation_m is None or n.ann_snow_in is None or n.ann_snow_in <= 0:
            continue
        best = None
        for st in stations:
            if st.annual_snowfall_in is None or st.elevation_ft is None:
                continue
            if abs(st.lat - n.lat) > 0.2 or abs(st.lon - n.lon) > 0.3:
                continue
            d = snotel.haversine_km(n.lat, n.lon, st.lat, st.lon)
            if d > max_km or abs(st.elevation_ft * 0.3048 - n.elevation_m) > max_elev_m:
                continue
            if best is None or d < best[0]:
                best = (d, st)
        if best is None:
            continue
        d, st = best
        out.append({"noaa_id": n.station_id, "noaa_name": n.name, "noaa_in": n.ann_snow_in,
                    "noaa_elevation_m": n.elevation_m, "snotel_id": st.station_id, "snotel_name": st.name,
                    "distance_km": round(d, 1), "snotel_elevation_m": st.elevation_ft * 0.3048,
                    "snotel_in": st.annual_snowfall_in,
                    "diff_pct": 100.0 * (st.annual_snowfall_in - n.ann_snow_in) / n.ann_snow_in})
    return sorted(out, key=lambda p: p["noaa_name"])


def write_validation(doc: Path, section: str) -> None:
    """Replace (or append) the SNOTEL station section of docs/map/climate-validation.md."""
    text = doc.read_text(encoding="utf-8") if doc.is_file() else "# Climate validation\n"
    head = text.split(VALIDATION_MARKER)[0].rstrip("\n")
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(head + "\n\n" + section, encoding="utf-8")


def validate(directory: Path, resorts_path: Path, doc: Path, config_dir: Path, max_km: float = 10.0,
             max_elev_m: float = 300.0) -> dict:
    from pipeline.climate import noaa_normals  # noqa: PLC0415

    defs = _load_defs(config_dir)
    cfg = heat_snotel_settings(defs)
    rows, _ = load_extract(directory)
    ok = usable(rows, cfg["min_complete_years"])
    data = json.loads(resorts_path.read_text(encoding="utf-8"))
    resorts = data["resorts"] if isinstance(data, dict) else data
    matches = match_resorts(resorts, ok, max_km, max_elev_m)
    summary = summarize(matches)
    noaa, _ = noaa_normals.load_extract(directory.parent / "noaa_normals")
    snow = defs["heat"]["snowfall"]
    noaa = noaa_normals.filter_usable(noaa, snow["accept_completeness"], snow["accept_measurement_flags"])
    pairs = noaa_pairs(noaa, ok, max_km, max_elev_m)
    diffs = [p["diff_pct"] for p in pairs]
    nsum = {"n": len(pairs), "median_diff_pct": round(statistics.median(diffs), 1) if diffs else None,
            "median_abs_diff_pct": round(statistics.median(abs(x) for x in diffs), 1) if diffs else None,
            "within_50_pct": round(sum(abs(x) <= 50 for x in diffs) / len(diffs), 3) if diffs else None}
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    write_validation(doc, validation_section(matches, pairs, summary, nsum, len(rows), len(ok), cfg, max_km,
                                             max_elev_m, generated))
    return {"summary": summary, "noaa": nsum}


# ---------------------------------------------------------------- CLI
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["fetch", "extract", "validate", "summary"])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "config")
    ap.add_argument("--resorts", type=Path, default=REPO_ROOT / "data" / "build" / "resorts.json")
    ap.add_argument("--doc", type=Path, default=REPO_ROOT / "docs" / "map" / "climate-validation.md")
    args = ap.parse_args(argv)
    if args.command == "fetch":
        try:
            res = fetch_all(args.dir, heat_snotel_settings(_load_defs(args.config)))
        except http.NetworkBlocked as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(json.dumps(res))
        return 0 if not res["failed"] else 2
    if args.command == "extract":
        print(json.dumps(extract(args.dir, args.config)))
        return 0
    if args.command == "validate":
        res = validate(args.dir, args.resorts, args.doc, args.config)
        print(json.dumps(res))
        med = res["summary"]["median_abs_diff_pct"]
        return 0 if med is not None and med <= 50 else 3
    rows, source = load_extract(args.dir)
    cfg = heat_snotel_settings(_load_defs(args.config))
    ok = usable(rows, cfg["min_complete_years"])
    print(f"{len(rows)} stations, {len(ok)} usable, accessed {source['source']['accessed_date']}")
    vals = sorted(r.annual_snowfall_in for r in ok)
    if vals:
        print(f"annual snowfall: min {vals[0]}, median {statistics.median(vals)}, max {vals[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
