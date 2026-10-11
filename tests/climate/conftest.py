"""Shared helpers for climate ETL tests. Everything here is synthetic test data."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"

from pipeline.climate import http, open_meteo, snotel  # noqa: E402
from pipeline.climate.definitions import climate_settings  # noqa: E402


def load_fixture(name: str):
    path = FIXTURES / name
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def definitions() -> dict:
    return load_fixture("definitions.yaml")


@pytest.fixture
def settings(definitions):
    return climate_settings(definitions)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def synthetic_snow_cm(d: date) -> float:
    """Winter pattern: 2 day storms every 6 days plus a banner day mid month."""
    if d.month not in (11, 12, 1, 2, 3, 4):
        return 0.0
    if d.day == 15:
        return 35.0  # 13.78 in, banner
    if d.day % 6 == 0:
        return 20.0  # 7.87 in, powder
    if d.day % 6 == 1 and d.day > 1:
        return 5.0  # 1.97 in, storm continues
    return 0.0


def synthetic_temp_c(d: date) -> tuple[float, float]:
    winter = d.month in (11, 12, 1, 2, 3, 4)
    tmax = -4.0 if winter else 18.0
    if d.month == 4:
        tmax = 2.0
    return tmax, tmax - 10.0


def om_location_payload(lat: float, lon: float, start: date, end: date, elevation: float = 2500.0) -> dict:
    days = list(_daterange(start, end))
    temps = [synthetic_temp_c(d) for d in days]
    return {
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
        "generationtime_ms": 1.0,
        "utc_offset_seconds": -25200,
        "timezone": "America/Denver",
        "timezone_abbreviation": "MST",
        "elevation": elevation,
        "daily_units": {
            "time": "iso8601",
            "snowfall_sum": "cm",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "wind_speed_10m_max": "km/h",
            "sunshine_duration": "s",
            "daylight_duration": "s",
        },
        "daily": {
            "time": [d.isoformat() for d in days],
            "snowfall_sum": [synthetic_snow_cm(d) for d in days],
            "temperature_2m_max": [t[0] for t in temps],
            "temperature_2m_min": [t[1] for t in temps],
            "wind_speed_10m_max": [32.18688 for _ in days],
            "sunshine_duration": [18000.0 for _ in days],
            "daylight_duration": [36000.0 for _ in days],
        },
    }


def snotel_payload(triplet: str, start: date, end: date) -> list:
    """Snowpack that gains 10 in depth and 0.8 in SWE (8 percent) every 6th day in winter."""
    swe, depth = 0.0, 0.0
    wteq, snwd = [], []
    for d in _daterange(start, end):
        if d.month in (11, 12, 1, 2, 3, 4) and d.day % 6 == 0:
            swe += 0.8
            depth += 10.0
        elif d.month in (11, 12, 1, 2, 3, 4) and depth > 0:
            depth -= 0.5
        elif d.month in (5, 6):
            swe, depth = max(0.0, swe - 0.5), max(0.0, depth - 4.0)
        else:
            swe, depth = 0.0, 0.0
        wteq.append({"date": d.isoformat(), "value": round(swe, 1)})
        snwd.append({"date": d.isoformat(), "value": round(depth)})
    return [
        {
            "stationTriplet": triplet,
            "data": [
                {"stationElement": {"elementCode": "WTEQ"}, "values": wteq},
                {"stationElement": {"elementCode": "SNWD"}, "values": snwd},
            ],
        }
    ]


class FakeApi:
    """Routes http.get_json calls to synthetic payloads and records them."""

    def __init__(self, blocked: bool = False, fail_lat: float | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.blocked = blocked
        self.fail_lat = fail_lat

    def __call__(self, url, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.blocked:
            raise http.NetworkBlocked("cannot reach host: Tunnel connection failed: 403 Forbidden")
        if url == open_meteo.ARCHIVE_URL:
            lats = [float(x) for x in str(params["latitude"]).split(",")]
            lons = [float(x) for x in str(params["longitude"]).split(",")]
            start = date.fromisoformat(params["start_date"])
            end = date.fromisoformat(params["end_date"])
            if self.fail_lat is not None and any(abs(l - self.fail_lat) < 1e-6 for l in lats):
                raise http.HttpStatusError(url, 400, '{"error":true,"reason":"synthetic failure"}')
            out = [om_location_payload(a, b, start, end) for a, b in zip(lats, lons)]
            return out[0] if len(out) == 1 else out
        if url == snotel.STATIONS_URL:
            if params.get("stationTriplets") == "*:*:SNTL":
                return load_fixture("awdb_stations.json")
            return []
        if url == snotel.DATA_URL:
            return snotel_payload(
                params["stationTriplets"],
                date.fromisoformat(params["beginDate"]),
                date.fromisoformat(params["endDate"]),
            )
        raise AssertionError(f"unexpected URL {url}")

    def count(self, url: str) -> int:
        return sum(1 for u, _ in self.calls if u == url)


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(http, "get_json", api)
    return api
