"""Open Meteo Historical Weather API (ERA5 reanalysis) daily history fetcher.

API: https://open-meteo.com/en/docs/historical-weather-api
Endpoint: https://archive-api.open-meteo.com/v1/archive

Verified against the docs source, open-meteo/open-meteo-website at commit
1755598c7ac9cf56db13d540f43b73ba97207219 (fetched 2026-10-11):

* src/lib/components/docs/api-parameters.svelte: "Multiple coordinates can be
  comma separated ... the JSON output changes to a list of structures", and
  elevation "Per default, a 90 meter digital elevation model is used" for
  statistical downscaling.
* src/routes/en/docs/historical-weather-api/options.ts: model value ``era5``
  ("ERA5, 25 km, Global").
* src/routes/en/docs/historical-weather-api/+page.svelte: snowfall is "in
  centimeters ... E.g. 7 cm snow = 10 mm precipitation water equivalent".
* src/lib/components/response/results-preview.svelte: call weight, see
  :func:`request_weight`.

Choices:

* ``models=era5`` pins the ERA5 reanalysis.
* ``elevation`` is not sent, so Open Meteo downscales to its 90 m DEM at the
  resort coordinate (the base area). The response ``elevation`` (meters) is
  recorded in the provenance file.
* Units are pinned to metric (cm snowfall, deg C, km/h, seconds) and converted
  to imperial here, so the conversion is tested in one place.

Free tier (non commercial) limits: under 10,000 API calls per day, 5,000 per
hour and 600 per minute (https://open-meteo.com/en/terms). 25 seasons of 6
daily variables weigh about 391 calls per location. :class:`WeightBudget`
tracks that weight in a ledger next to the cache so separate runs on the same
day share one budget, and stops before the daily cap. Cached seasons are never
fetched twice, so the next run resumes where the last one stopped.

Raw responses are cached in ``data/raw/climate/open_meteo/{resort_id}.json``.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from pipeline.climate import http
from pipeline.climate.aggregate import DailyRecord
from pipeline.climate.seasons import period_dates, season_chunks, season_of
from pipeline.climate.units import c_to_f, cm_to_in, kmh_to_mph, mps_to_mph

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MODEL = "era5"
DAILY_VARIABLES: tuple[str, ...] = (
    "snowfall_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "wind_speed_10m_max",
    "sunshine_duration",
    "daylight_duration",
)
UNIT_PARAMS = {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"}

# Free tier limits (https://open-meteo.com/en/terms) and the share we allow ourselves.
LIMIT_PER_MINUTE = 600
LIMIT_PER_HOUR = 5000
LIMIT_PER_DAY = 10000
SAFETY = 0.8

DEFAULT_SEASONS_PER_CHUNK = 5
DEFAULT_LOCATIONS_PER_REQUEST = 3

SOURCE_ID = "open-meteo-era5-archive"
SOURCE = {
    "id": SOURCE_ID,
    "url": "https://open-meteo.com/en/docs/historical-weather-api",
    "title": "Open Meteo Historical Weather API, ERA5 reanalysis daily history (models=era5)",
    "note": (
        "Weather data by Open-Meteo.com (CC BY 4.0), from Copernicus ERA5 (Hersbach et al. 2020). "
        "ERA5 snowfall is water equivalent; Open Meteo reports it as snow depth with a fixed "
        "ratio (docs: 7 cm snow = 10 mm precipitation water equivalent, about 14 percent "
        "density), so snowfall_in, powder_days, banner_days and "
        "avg_storm_depth_in carry that assumption and miss real variation in snow density. "
        "ERA5 is a 25 km grid that smooths mountain terrain, so totals usually run below "
        "resort reported upper mountain snowfall. Values are downscaled to the Open Meteo 90 m "
        "elevation model at the resort coordinate (base area). wind_mph is the mean daily maximum "
        "10 m wind. sun_pct is sunshine_duration over daylight_duration."
    ),
}


class BudgetExhausted(RuntimeError):
    """The free tier budget for today is spent; resume tomorrow from cache."""


@dataclass(frozen=True)
class Location:
    resort_id: str
    lat: float
    lon: float


def request_weight(n_locations: int, n_days: int, n_variables: int, n_models: int = 1) -> float:
    """Open Meteo weighted call count for one request.

    Same formula as the docs' "This API call is equivalent to N calls" note
    (open-meteo-website, src/lib/components/response/results-preview.svelte):
    variables_weight = variables x models / 10, time_weight = days / 14,
    weight = locations x max(1, max(variables_weight, time_weight x variables_weight)).
    """
    variables_weight = n_variables * max(n_models, 1) / 10.0
    time_weight = n_days / 14.0
    return n_locations * max(1.0, max(variables_weight, time_weight * variables_weight))


def resort_weight(start_year: int, end_year: int, n_variables: int = len(DAILY_VARIABLES)) -> float:
    start, end = period_dates(start_year, end_year)
    return request_weight(1, (end - start).days + 1, n_variables)


class WeightBudget:
    """Sliding window budget for Open Meteo weighted calls, persisted to a ledger."""

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        per_minute: float = LIMIT_PER_MINUTE * SAFETY,
        per_hour: float = LIMIT_PER_HOUR * SAFETY,
        per_day: float = LIMIT_PER_DAY * SAFETY,
        max_wait_s: float = 3900.0,
        clock: Callable[[], float] = time.time,
    ):
        self.ledger_path = ledger_path
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.per_day = per_day
        self.max_wait_s = max_wait_s
        self.clock = clock
        self.events: list[tuple[float, float]] = []
        if ledger_path and ledger_path.exists():
            try:
                self.events = [tuple(e) for e in json.loads(ledger_path.read_text())["events"]]
            except (ValueError, KeyError, TypeError):
                self.events = []

    def _used(self, now: float, window: float) -> float:
        return sum(w for t, w in self.events if now - t < window)

    def _wait_for(self, now: float, window: float, limit: float, weight: float) -> float:
        """Seconds until enough weight leaves ``window`` for ``weight`` to fit."""
        used = self._used(now, window)
        if used + weight <= limit:
            return 0.0
        for t, w in sorted(e for e in self.events if now - e[0] < window):
            used -= w
            if used + weight <= limit:
                return t + window - now + 1.0
        return window

    def acquire(self, weight: float) -> None:
        if weight > self.per_minute:
            raise ValueError(f"request weight {weight:.0f} exceeds the per minute budget {self.per_minute:.0f}")
        while True:
            now = self.clock()
            self.events = [e for e in self.events if now - e[0] < 86400]
            if self._used(now, 86400) + weight > self.per_day:
                raise BudgetExhausted(
                    f"Open Meteo daily budget reached ({self._used(now, 86400):.0f} of "
                    f"{self.per_day:.0f} weighted calls in the last 24 h). Run again later; cached "
                    "seasons are kept."
                )
            wait = max(
                self._wait_for(now, 60, self.per_minute, weight),
                self._wait_for(now, 3600, self.per_hour, weight),
            )
            if wait <= 0:
                break
            if wait > self.max_wait_s:
                raise BudgetExhausted(f"Open Meteo hourly budget reached; next slot in {wait / 60:.0f} min")
            http._sleep(wait)
        self.events.append((self.clock(), weight))
        self._save()

    def _save(self) -> None:
        if self.ledger_path:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.ledger_path.write_text(json.dumps({"events": self.events}))


# ---------------------------------------------------------------- parsing


def _series(daily: dict[str, Any], name: str) -> list[Any]:
    if name in daily:
        return daily[name]
    for key, values in daily.items():  # multi model responses suffix the model name
        if key.startswith(name + "_"):
            return values
    raise ValueError(f"Open Meteo response has no daily '{name}'")


def _unit(units: dict[str, Any], name: str) -> str:
    if name in units:
        return str(units[name])
    for key, value in units.items():
        if key.startswith(name + "_"):
            return str(value)
    return ""


def _snow_to_in(unit: str) -> Callable[[Optional[float]], Optional[float]]:
    if unit in ("cm", ""):
        return cm_to_in
    if unit in ("inch", "in"):
        return lambda v: None if v is None else round(float(v), 3)
    raise ValueError(f"unexpected snowfall unit {unit!r}")


def _temp_to_f(unit: str) -> Callable[[Optional[float]], Optional[float]]:
    if unit in ("°C", "C", ""):
        return c_to_f
    if unit in ("°F", "F"):
        return lambda v: v
    raise ValueError(f"unexpected temperature unit {unit!r}")


def _wind_to_mph(unit: str) -> Callable[[Optional[float]], Optional[float]]:
    if unit in ("km/h", ""):
        return kmh_to_mph
    if unit in ("mp/h", "mph"):
        return lambda v: v
    if unit == "m/s":
        return mps_to_mph
    if unit == "kn":
        return lambda v: None if v is None else v * 1.150779
    raise ValueError(f"unexpected wind unit {unit!r}")


def _seconds(unit: str) -> None:
    if unit not in ("s", ""):
        raise ValueError(f"unexpected duration unit {unit!r}")


def parse_location(payload: dict[str, Any]) -> tuple[dict[str, Any], list[DailyRecord]]:
    """One location's archive response to (metadata, imperial daily records)."""
    if payload.get("error"):
        raise ValueError(f"Open Meteo error: {payload.get('reason')}")
    daily = payload["daily"]
    units = payload.get("daily_units", {})
    snow = _snow_to_in(_unit(units, "snowfall_sum"))
    tmax_f = _temp_to_f(_unit(units, "temperature_2m_max"))
    tmin_f = _temp_to_f(_unit(units, "temperature_2m_min"))
    wind = _wind_to_mph(_unit(units, "wind_speed_10m_max"))
    _seconds(_unit(units, "sunshine_duration"))
    _seconds(_unit(units, "daylight_duration"))

    times = daily["time"]
    cols = {name: _series(daily, name) for name in DAILY_VARIABLES}
    for name, values in cols.items():
        if len(values) != len(times):
            raise ValueError(f"daily '{name}' has {len(values)} values for {len(times)} days")
    records = []
    for i, t in enumerate(times):
        records.append(
            DailyRecord(
                date=date.fromisoformat(str(t)[:10]),
                snowfall_in=snow(cols["snowfall_sum"][i]),
                tmax_f=tmax_f(cols["temperature_2m_max"][i]),
                tmin_f=tmin_f(cols["temperature_2m_min"][i]),
                wind_mph=wind(cols["wind_speed_10m_max"][i]),
                sunshine_s=cols["sunshine_duration"][i],
                daylight_s=cols["daylight_duration"][i],
            )
        )
    meta = {
        "grid_latitude": payload.get("latitude"),
        "grid_longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "timezone": payload.get("timezone"),
    }
    return meta, records


# ---------------------------------------------------------------- cache


def cache_path(cache_dir: Path, resort_id: str) -> Path:
    return Path(cache_dir) / f"{resort_id}.json"


def _read_cache(cache_dir: Path, loc: Location) -> dict[str, Any]:
    path = cache_path(cache_dir, loc.resort_id)
    empty = {
        "resort_id": loc.resort_id,
        "latitude": loc.lat,
        "longitude": loc.lon,
        "model": MODEL,
        "daily_variables": list(DAILY_VARIABLES),
        "chunks": {},
    }
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return empty
    same = (
        abs(float(data.get("latitude", 1e9)) - loc.lat) < 1e-6
        and abs(float(data.get("longitude", 1e9)) - loc.lon) < 1e-6
        and data.get("model") == MODEL
        and list(data.get("daily_variables", [])) == list(DAILY_VARIABLES)
    )
    return data if same else empty


def _write_cache(cache_dir: Path, data: dict[str, Any]) -> None:
    path = cache_path(cache_dir, data["resort_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _cached_records(data: dict[str, Any]) -> tuple[dict[str, Any], dict[date, DailyRecord]]:
    meta: dict[str, Any] = {}
    by_date: dict[date, DailyRecord] = {}
    for key in sorted(data.get("chunks", {})):
        m, records = parse_location(data["chunks"][key]["response"])
        meta = m
        for r in records:
            by_date[r.date] = r
    return meta, by_date


def _complete_seasons(by_date: dict[date, DailyRecord], start_year: int, end_year: int) -> set[int]:
    counts: dict[int, int] = {}
    for d in by_date:
        counts[season_of(d)] = counts.get(season_of(d), 0) + 1
    done = set()
    for s in range(start_year, end_year):
        expected = (date(s + 1, 9, 30) - date(s, 10, 1)).days + 1
        if counts.get(s, 0) >= expected:
            done.add(s)
    return done


def load_daily(
    cache_dir: Path, loc: Location, start_year: int, end_year: int
) -> Optional[tuple[dict[str, Any], list[DailyRecord]]]:
    """Cached daily records for the whole period, or None when any season is missing."""
    data = _read_cache(cache_dir, loc)
    if not data["chunks"]:
        return None
    meta, by_date = _cached_records(data)
    if _complete_seasons(by_date, start_year, end_year) != set(range(start_year, end_year)):
        return None
    start, end = period_dates(start_year, end_year)
    records = [r for d, r in sorted(by_date.items()) if start <= d <= end]
    meta = dict(meta, fetched_at=max(c.get("fetched_at", "") for c in data["chunks"].values()))
    return meta, records


def missing_windows(
    cache_dir: Path, loc: Location, start_year: int, end_year: int, seasons_per_chunk: int
) -> list[tuple[date, date]]:
    data = _read_cache(cache_dir, loc)
    _, by_date = _cached_records(data) if data["chunks"] else ({}, {})
    done = _complete_seasons(by_date, start_year, end_year)
    out = []
    for start, end in season_chunks(start_year, end_year, seasons_per_chunk):
        if any(s not in done for s in range(start.year, end.year)):
            out.append((start, end))
    return out


# ---------------------------------------------------------------- fetching


def _params(batch: list[Location], start: date, end: date) -> dict[str, Any]:
    return {
        "latitude": ",".join(f"{l.lat:.5f}" for l in batch),
        "longitude": ",".join(f"{l.lon:.5f}" for l in batch),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARIABLES),
        "models": MODEL,
        "timezone": "auto",
        **UNIT_PARAMS,
    }


def fetch_window(batch: list[Location], start: date, end: date) -> list[dict[str, Any]]:
    """One archive request for ``batch``; returns one payload per location."""
    payload = http.get_json(ARCHIVE_URL, _params(batch, start, end))
    if isinstance(payload, dict):
        if payload.get("error"):
            raise ValueError(f"Open Meteo error: {payload.get('reason')}")
        payload = [payload]
    if not isinstance(payload, list) or len(payload) != len(batch):
        raise ValueError(f"expected {len(batch)} location payloads, got {type(payload).__name__}")
    return payload


def fetch_locations(
    locations: Iterable[Location],
    start_year: int,
    end_year: int,
    cache_dir: Path,
    budget: Optional[WeightBudget] = None,
    seasons_per_chunk: int = DEFAULT_SEASONS_PER_CHUNK,
    locations_per_request: int = DEFAULT_LOCATIONS_PER_REQUEST,
    log: Callable[[str], None] = print,
) -> dict[str, str]:
    """Fill the cache for every location. Returns {resort_id: error} for failures.

    Raises :class:`BudgetExhausted` or :class:`http.NetworkBlocked` to stop the
    whole run; everything fetched before that point stays cached.
    """
    cache_dir = Path(cache_dir)
    locations = list(locations)
    by_window: dict[tuple[date, date], list[Location]] = {}
    for loc in locations:
        for window in missing_windows(cache_dir, loc, start_year, end_year, seasons_per_chunk):
            by_window.setdefault(window, []).append(loc)

    errors: dict[str, str] = {}
    for (start, end), locs in sorted(by_window.items()):
        n_days = (end - start).days + 1
        per_loc = request_weight(1, n_days, len(DAILY_VARIABLES))
        size = locations_per_request
        if budget is not None:
            size = max(1, min(size, int(budget.per_minute // per_loc)))
        for i in range(0, len(locs), size):
            batch = [l for l in locs[i : i + size] if l.resort_id not in errors]
            if not batch:
                continue
            _fetch_batch(batch, start, end, n_days, cache_dir, budget, errors, log)
    return errors


def _fetch_batch(
    batch: list[Location],
    start: date,
    end: date,
    n_days: int,
    cache_dir: Path,
    budget: Optional[WeightBudget],
    errors: dict[str, str],
    log: Callable[[str], None],
) -> None:
    weight = request_weight(len(batch), n_days, len(DAILY_VARIABLES))
    if budget is not None:
        budget.acquire(weight)
    log(f"  open meteo {start}..{end} for {', '.join(l.resort_id for l in batch)} (weight {weight:.0f})")
    try:
        payloads = fetch_window(batch, start, end)
    except (http.NetworkBlocked, BudgetExhausted, http.RateLimited):
        raise
    except Exception as e:  # one bad location should not stop the rest
        if len(batch) > 1:
            for loc in batch:  # retry alone to find the location the API rejects
                _fetch_batch([loc], start, end, n_days, cache_dir, budget, errors, log)
        else:
            errors[batch[0].resort_id] = f"open meteo {start}..{end}: {e}"
        return
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for loc, payload in zip(batch, payloads):
        try:
            parse_location(payload)
        except Exception as e:
            errors[loc.resort_id] = f"open meteo {start}..{end}: unreadable response: {e}"
            continue
        data = _read_cache(cache_dir, loc)
        data["chunks"][f"{start}_{end}"] = {"fetched_at": fetched_at, "response": payload}
        _write_cache(cache_dir, data)


def preflight() -> None:
    """Cheapest possible request (one location, one day, one variable). Raises NetworkBlocked."""
    day = (date.today() - timedelta(days=30)).isoformat()
    http.get_json(
        ARCHIVE_URL,
        {"latitude": "39.64", "longitude": "-106.37", "start_date": day, "end_date": day, "daily": "snowfall_sum", "models": MODEL},
        retries=1,
        backoff=2.0,
    )


def estimated_total_weight(n_resorts: int, start_year: int, end_year: int) -> float:
    return n_resorts * resort_weight(start_year, end_year)


def days_needed(n_resorts: int, start_year: int, end_year: int) -> int:
    return math.ceil(estimated_total_weight(n_resorts, start_year, end_year) / (LIMIT_PER_DAY * SAFETY))
