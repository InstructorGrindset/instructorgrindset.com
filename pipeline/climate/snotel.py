"""NRCS SNOTEL storm density from the AWDB REST API.

API: https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html
Base: https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1

* ``GET /stations?stationTriplets=*:*:SNTL`` lists SNOTEL stations with
  ``stationTriplet``, ``name``, ``latitude``, ``longitude``, ``elevation`` (ft),
  ``beginDate`` and ``endDate``.
* ``GET /data?stationTriplets=...&elements=WTEQ,SNWD&duration=DAILY&beginDate=...&endDate=...``
  returns ``[{stationTriplet, data: [{stationElement: {elementCode, ...},
  values: [{date, value}]}]}]``. WTEQ (snow water equivalent) and SNWD (snow
  depth) are in inches.

Station choice: SNOTEL stations within ``radius_km`` of the resort base,
nearest first. When the resort elevation is known, a station must also sit
between ``elevation_band_below_ft`` below and ``elevation_band_above_ft``
above it. The resort contract has no elevation today, so selection is usually
by distance only, and the provenance file records which mode was used.

Storms: a storm day has a daily snow depth gain of at least
``storm_min_daily_depth_gain_in``. Consecutive storm days form a storm. Its
depth gain and SWE gain run from the day before the first storm day to the last
storm day, and density = SWE gain / depth gain. Quality control drops storms
with a daily SWE drop larger than ``swe_noise_tolerance_in`` (sensor noise), a
daily depth jump above ``max_daily_depth_gain_in`` (sensor spike), a total
depth gain under ``min_storm_depth_gain_in``, no SWE gain, or a density outside
``density_min_pct`` to ``density_max_pct``. Settlement during a storm makes the
depth gain smaller than the new snow depth, so densities lean slightly high.

Raw responses are cached in ``data/raw/climate/snotel/``.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.climate import http
from pipeline.climate.definitions import SnotelSettings

AWDB_BASE = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
STATIONS_URL = f"{AWDB_BASE}/stations"
DATA_URL = f"{AWDB_BASE}/data"
ELEMENTS = ("WTEQ", "SNWD")


@dataclass(frozen=True)
class Station:
    triplet: str
    name: str
    lat: float
    lon: float
    elevation_ft: Optional[float]
    begin_date: Optional[date] = None
    end_date: Optional[date] = None

    @property
    def station_id(self) -> str:
        return self.triplet.split(":")[0]

    @property
    def state(self) -> str:
        parts = self.triplet.split(":")
        return parts[1] if len(parts) > 1 else ""


@dataclass(frozen=True)
class Candidate:
    station: Station
    distance_km: float
    elevation_diff_ft: Optional[float]
    mode: str  # "distance_and_elevation" | "distance_only"


@dataclass(frozen=True)
class SnotelStorm:
    start: date
    end: date
    depth_gain_in: float
    swe_gain_in: float
    density_pct: Optional[float]
    qc: Optional[str] = None  # None when the storm passed QC

    @property
    def month(self) -> int:
        return self.start.month


@dataclass
class StationEvaluation:
    station: Station
    storms: list[SnotelStorm]
    qc_counts: dict[str, int] = field(default_factory=dict)

    @property
    def valid(self) -> list[SnotelStorm]:
        return [s for s in self.storms if s.qc is None]


# ---------------------------------------------------------------- metadata


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_stations(payload: Any) -> list[Station]:
    out = []
    for row in payload or []:
        triplet = str(row.get("stationTriplet", ""))
        network = row.get("networkCode") or (triplet.split(":")[2] if triplet.count(":") == 2 else "")
        if network != "SNTL":
            continue
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue
        elev = row.get("elevation")
        out.append(
            Station(
                triplet=triplet,
                name=str(row.get("name", "")).strip(),
                lat=float(lat),
                lon=float(lon),
                elevation_ft=float(elev) if elev is not None else None,
                begin_date=_parse_date(row.get("beginDate")),
                end_date=_parse_date(row.get("endDate")),
            )
        )
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def select_candidates(
    lat: float,
    lon: float,
    elevation_ft: Optional[float],
    stations: list[Station],
    settings: SnotelSettings,
    period: Optional[tuple[date, date]] = None,
) -> list[Candidate]:
    """Stations to try for a resort, nearest first."""
    mode = "distance_only" if elevation_ft is None else "distance_and_elevation"
    out = []
    for st in stations:
        if period is not None:
            start, end = period
            if st.begin_date and st.begin_date > end:
                continue
            if st.end_date and st.end_date < start:
                continue
        d = haversine_km(lat, lon, st.lat, st.lon)
        if d > settings.radius_km:
            continue
        diff = None
        if elevation_ft is not None:
            if st.elevation_ft is None:
                continue
            diff = st.elevation_ft - elevation_ft
            if diff < -settings.elevation_band_below_ft or diff > settings.elevation_band_above_ft:
                continue
        out.append(Candidate(st, d, diff, mode))
    out.sort(key=lambda c: (round(c.distance_km, 3), abs(c.elevation_diff_ft or 0.0)))
    return out[: settings.max_candidates]


# ---------------------------------------------------------------- fetching and cache


def _slug(triplet: str) -> str:
    return triplet.replace(":", "_").replace("/", "_")


def stations_cache_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "stations.json"


def station_cache_path(cache_dir: Path, triplet: str) -> Path:
    return Path(cache_dir) / f"{_slug(triplet)}.json"


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def load_stations(cache_dir: Path) -> Optional[list[Station]]:
    path = stations_cache_path(cache_dir)
    if not path.exists():
        return None
    return parse_stations(json.loads(path.read_text(encoding="utf-8"))["response"])


def fetch_stations(cache_dir: Path) -> list[Station]:
    payload = http.get_json(
        STATIONS_URL,
        {
            "stationTriplets": "*:*:SNTL",
            "returnForecastPointMetadata": False,
            "returnReservoirMetadata": False,
            "returnStationElements": False,
            "activeOnly": False,
        },
    )
    _write(
        stations_cache_path(cache_dir),
        {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "response": payload},
    )
    return parse_stations(payload)


def get_stations(cache_dir: Path) -> list[Station]:
    cached = load_stations(cache_dir)
    return cached if cached is not None else fetch_stations(cache_dir)


def load_station_daily(cache_dir: Path, triplet: str, start: date, end: date) -> Optional[dict[str, Any]]:
    path = station_cache_path(cache_dir, triplet)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("begin") > start.isoformat() or data.get("end") < end.isoformat():
        return None
    return data


def fetch_station_daily(cache_dir: Path, triplet: str, start: date, end: date) -> dict[str, Any]:
    cached = load_station_daily(cache_dir, triplet, start, end)
    if cached is not None:
        return cached
    payload = http.get_json(
        DATA_URL,
        {
            "stationTriplets": triplet,
            "elements": ",".join(ELEMENTS),
            "duration": "DAILY",
            "beginDate": start.isoformat(),
            "endDate": end.isoformat(),
            "periodRef": "END",
            "centralTendencyType": "NONE",
            "returnFlags": False,
            "returnOriginalValues": False,
            "returnSuspectData": False,
        },
    )
    data = {
        "triplet": triplet,
        "begin": start.isoformat(),
        "end": end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": payload,
    }
    _write(station_cache_path(cache_dir, triplet), data)
    return data


def preflight() -> None:
    http.get_json(STATIONS_URL, {"stationTriplets": "1050:CO:SNTL"}, retries=1, backoff=2.0)


# ---------------------------------------------------------------- parsing and storms


def parse_station_daily(payload: Any, triplet: str) -> dict[date, tuple[Optional[float], Optional[float]]]:
    """{date: (swe_in, depth_in)}. Negative readings become None."""
    swe: dict[date, Optional[float]] = {}
    depth: dict[date, Optional[float]] = {}
    for station in payload or []:
        if station.get("stationTriplet") not in (None, triplet):
            continue
        for block in station.get("data", []):
            code = (block.get("stationElement") or {}).get("elementCode")
            target = swe if code == "WTEQ" else depth if code == "SNWD" else None
            if target is None:
                continue
            for v in block.get("values", []):
                d = _parse_date(v.get("date"))
                if d is None:
                    continue
                value = v.get("value")
                target[d] = float(value) if value is not None and float(value) >= 0 else None
    return {d: (swe.get(d), depth.get(d)) for d in sorted(set(swe) | set(depth))}


def detect_snotel_storms(
    series: dict[date, tuple[Optional[float], Optional[float]]], s: SnotelSettings
) -> list[SnotelStorm]:
    storms: list[SnotelStorm] = []
    run: list[tuple[date, float, float]] = []  # (day, daily swe change, daily depth change)
    baseline: Optional[tuple[float, float]] = None
    prev: Optional[tuple[date, float, float]] = None

    def close() -> None:
        nonlocal run, baseline
        if run and baseline is not None:
            day0, swe0 = run[0][0], baseline[0]
            depth0 = baseline[1]
            last_swe, last_depth = series[run[-1][0]]
            depth_gain = last_depth - depth0
            swe_gain = round(last_swe - swe0, 3)
            density = 100.0 * swe_gain / depth_gain if depth_gain > 0 else None
            qc = None
            if any(dswe < -s.swe_noise_tolerance_in for _, dswe, _ in run):
                qc = "negative_swe_jump"
            elif any(ddep > s.max_daily_depth_gain_in for _, _, ddep in run):
                qc = "depth_spike"
            elif depth_gain < s.min_storm_depth_gain_in:
                qc = "small_depth_gain"
            elif swe_gain <= 0:
                qc = "no_swe_gain"
            elif density is None or not (s.density_min_pct <= density <= s.density_max_pct):
                qc = "density_out_of_range"
            storms.append(SnotelStorm(day0, run[-1][0], depth_gain, swe_gain, density, qc))
        run = []
        baseline = None

    for d in sorted(series):
        swe, depth = series[d]
        if swe is None or depth is None:
            close()
            prev = None
            continue
        if prev is not None and d - prev[0] == timedelta(days=1):
            dswe, ddep = swe - prev[1], depth - prev[2]
            if ddep >= s.storm_min_daily_depth_gain_in:
                if not run:
                    baseline = (prev[1], prev[2])
                run.append((d, dswe, ddep))
            else:
                close()
        else:
            close()
        prev = (d, swe, depth)
    close()
    return storms


def evaluate_station(station: Station, payload: Any, s: SnotelSettings) -> StationEvaluation:
    storms = detect_snotel_storms(parse_station_daily(payload, station.triplet), s)
    counts = Counter(st.qc or "ok" for st in storms)
    return StationEvaluation(station, storms, dict(counts))


def source_entry(station: Station, accessed: date) -> dict[str, str]:
    return {
        "id": f"nrcs-snotel-{station.station_id}-{station.state}".lower(),
        "url": f"https://wcc.sc.egov.usda.gov/nwcc/site?sitenum={station.station_id}",
        "title": f"NRCS SNOTEL {station.name} ({station.triplet}), daily snow water equivalent and snow depth",
        "accessed_date": accessed.isoformat(),
        "note": (
            "Daily WTEQ and SNWD from the NRCS AWDB REST API. Storm density is SWE gain over snow "
            "depth gain per storm, after quality control; settlement during storms makes densities "
            "lean slightly high."
        ),
    }
