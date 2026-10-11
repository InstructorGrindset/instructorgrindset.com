import copy
import json
from datetime import date

import pytest

from climate_helpers import load_fixture
from pipeline.climate import http, open_meteo
from pipeline.climate.open_meteo import (
    BudgetExhausted,
    Location,
    WeightBudget,
    fetch_locations,
    load_daily,
    missing_windows,
    parse_location,
    request_weight,
    resort_weight,
)


def test_parse_location_converts_to_imperial():
    payload = load_fixture("open_meteo_two_locations.json")[0]
    meta, recs = parse_location(payload)
    assert meta == {"grid_latitude": 39.6, "grid_longitude": -106.35, "elevation_m": 2475.0, "timezone": "America/Denver"}
    assert [r.date for r in recs] == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    assert [r.snowfall_in for r in recs] == [6.0, 12.0, None]
    assert recs[0].tmax_f == pytest.approx(32.0)
    assert recs[1].tmin_f == pytest.approx(-40.0)
    assert recs[0].wind_mph == pytest.approx(10.0)
    assert recs[2].wind_mph == pytest.approx(20.0)
    assert recs[0].sunshine_s == 18000.0 and recs[0].daylight_s == 36000.0


def test_parse_location_handles_model_suffix_and_rejects_unknown_units():
    payload = copy.deepcopy(load_fixture("open_meteo_two_locations.json")[0])
    payload["daily"]["snowfall_sum_era5"] = payload["daily"].pop("snowfall_sum")
    payload["daily_units"]["snowfall_sum_era5"] = payload["daily_units"].pop("snowfall_sum")
    _, recs = parse_location(payload)
    assert recs[0].snowfall_in == 6.0
    payload["daily_units"]["snowfall_sum_era5"] = "furlongs"
    with pytest.raises(ValueError, match="snowfall unit"):
        parse_location(payload)
    with pytest.raises(ValueError, match="Open Meteo error"):
        parse_location({"error": True, "reason": "bad"})


def test_parse_location_imperial_units_pass_through():
    payload = copy.deepcopy(load_fixture("open_meteo_two_locations.json")[1])
    payload["daily_units"].update({"snowfall_sum": "inch", "temperature_2m_max": "°F", "temperature_2m_min": "°F", "wind_speed_10m_max": "mp/h"})
    _, recs = parse_location(payload)
    assert recs[0].snowfall_in == 2.54 and recs[0].tmax_f == 1.0 and recs[0].wind_mph == 10.0


def test_request_weight():
    assert request_weight(1, 7, 6) == 1.0  # short requests count as one call
    assert request_weight(1, 28, 6) == pytest.approx(1.2)
    assert request_weight(3, 14, 20) == pytest.approx(6.0)
    assert request_weight(1, 1, 30) == pytest.approx(3.0)  # many variables weigh even for one day
    assert request_weight(2, 140, 5, n_models=2) == pytest.approx(20.0)
    assert resort_weight(2001, 2026) == pytest.approx(9131 / 14 * 0.6, rel=1e-6)  # about 391 per resort


def test_fetch_batches_locations_and_resumes_from_cache(tmp_path, fake_api):
    locs = [Location("a", 39.6, -106.35), Location("b", 40.5, -111.6), Location("c", 41.0, -111.0), Location("d", 42.0, -110.0)]
    errors = fetch_locations(locs, 2020, 2023, tmp_path, None, seasons_per_chunk=2, locations_per_request=3, log=lambda s: None)
    assert errors == {}
    calls = [p for u, p in fake_api.calls if u == open_meteo.ARCHIVE_URL]
    # 2 windows (2 seasons + 1 season) x 2 batches (3 + 1 locations)
    assert len(calls) == 4
    assert calls[0]["latitude"].count(",") == 2
    assert calls[0]["models"] == "era5" and calls[0]["timezone"] == "auto"
    assert "elevation" not in calls[0]
    assert set(calls[0]["daily"].split(",")) == set(open_meteo.DAILY_VARIABLES)
    for loc in locs:
        meta, recs = load_daily(tmp_path, loc, 2020, 2023)
        assert recs[0].date == date(2020, 10, 1) and recs[-1].date == date(2023, 9, 30)
        assert len(recs) == (date(2023, 9, 30) - date(2020, 10, 1)).days + 1
        assert meta["elevation_m"] == 2500.0
    # Second run finds everything cached and makes no requests.
    fetch_locations(locs, 2020, 2023, tmp_path, None, seasons_per_chunk=2, log=lambda s: None)
    assert fake_api.count(open_meteo.ARCHIVE_URL) == 4


def test_partial_cache_fetches_only_missing_windows(tmp_path, fake_api):
    loc = Location("a", 39.6, -106.35)
    fetch_locations([loc], 2020, 2022, tmp_path, None, seasons_per_chunk=1, log=lambda s: None)
    assert fake_api.count(open_meteo.ARCHIVE_URL) == 2
    assert missing_windows(tmp_path, loc, 2020, 2023, 1) == [(date(2022, 10, 1), date(2023, 9, 30))]
    assert load_daily(tmp_path, loc, 2020, 2023) is None
    fetch_locations([loc], 2020, 2023, tmp_path, None, seasons_per_chunk=1, log=lambda s: None)
    assert fake_api.count(open_meteo.ARCHIVE_URL) == 3
    assert load_daily(tmp_path, loc, 2020, 2023) is not None


def test_cache_invalidated_when_coordinates_change(tmp_path, fake_api):
    fetch_locations([Location("a", 39.6, -106.35)], 2020, 2021, tmp_path, None, log=lambda s: None)
    assert load_daily(tmp_path, Location("a", 39.7, -106.35), 2020, 2021) is None


def test_failed_batch_is_reported_not_fatal(tmp_path, monkeypatch):
    from climate_helpers import FakeApi

    api = FakeApi(fail_lat=40.5)
    monkeypatch.setattr(http, "get_json", api)
    locs = [Location("a", 39.6, -106.35), Location("b", 40.5, -111.6)]
    errors = fetch_locations(locs, 2020, 2021, tmp_path, None, locations_per_request=1, log=lambda s: None)
    assert set(errors) == {"b"}
    assert load_daily(tmp_path, locs[0], 2020, 2021) is not None


def test_budget_waits_per_minute_and_stops_per_day(tmp_path):
    now = [1_000_000.0]
    slept = []

    def fake_sleep(s):
        slept.append(s)
        now[0] += s

    budget = WeightBudget(tmp_path / "usage.json", per_minute=100, per_hour=1000, per_day=250, clock=lambda: now[0])
    import pipeline.climate.http as h

    orig = h._sleep
    h._sleep = fake_sleep
    try:
        budget.acquire(80)
        budget.acquire(80)  # must wait for the first to leave the minute window
        assert slept and slept[0] > 0
        with pytest.raises(BudgetExhausted, match="daily budget"):
            budget.acquire(100)
    finally:
        h._sleep = orig
    ledger = json.loads((tmp_path / "usage.json").read_text())
    assert sum(w for _, w in ledger["events"]) == 160
    # A new budget object on the same ledger remembers today's usage.
    again = WeightBudget(tmp_path / "usage.json", per_minute=100, per_hour=1000, per_day=250, clock=lambda: now[0])
    with pytest.raises(BudgetExhausted):
        again.acquire(100)
    with pytest.raises(ValueError):
        again.acquire(150)


def test_budget_exhaustion_keeps_fetched_cache(tmp_path, fake_api):
    locs = [Location("a", 39.6, -106.35), Location("b", 40.5, -111.6)]
    one_window = request_weight(1, 365, len(open_meteo.DAILY_VARIABLES))
    budget = WeightBudget(tmp_path / "_usage.json", per_minute=600, per_hour=5000, per_day=one_window * 1.5)
    with pytest.raises(BudgetExhausted):
        fetch_locations(locs, 2020, 2021, tmp_path, budget, locations_per_request=1, log=lambda s: None)
    assert load_daily(tmp_path, locs[0], 2020, 2021) is not None
    assert load_daily(tmp_path, locs[1], 2020, 2021) is None


def test_get_json_classifies_blocked_network(monkeypatch):
    import urllib.error
    import urllib.request

    def blocked(*a, **k):
        raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    with pytest.raises(http.NetworkBlocked, match="archive-api.open-meteo.com"):
        open_meteo.preflight()


def test_get_json_retries_server_errors_then_succeeds(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    attempts = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(req, timeout=None):
        attempts.append(req.full_url)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    assert http.get_json("https://example.org/x", {"a": "1,2"}) == {"ok": True}
    assert len(attempts) == 3
    assert attempts[0] == "https://example.org/x?a=1,2"


def test_get_json_daily_rate_limit(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def limited(req, timeout=None):
        body = b'{"error":true,"reason":"Daily API request limit exceeded. Please try again tomorrow."}'
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", limited)
    with pytest.raises(http.RateLimited) as e:
        http.get_json("https://example.org/x")
    assert e.value.daily


def test_get_json_minutely_429_backs_off_then_succeeds(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    waits = []
    monkeypatch.setattr(http, "_sleep", waits.append)
    attempts = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def limited_once(req, timeout=None):
        attempts.append(1)
        if len(attempts) == 1:
            body = b'{"error":true,"reason":"Minutely API request limit exceeded. Please try again in one minute."}'
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(body))
        return Resp(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", limited_once)
    assert http.get_json("https://example.org/x") == []
    assert waits == [65.0]


def test_get_json_persistent_429_raises_rate_limited(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def limited(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "7"}, io.BytesIO(b'{"reason":"Hourly API request limit exceeded"}'))

    monkeypatch.setattr(urllib.request, "urlopen", limited)
    with pytest.raises(http.RateLimited, match="Hourly") as e:
        http.get_json("https://example.org/x", retries=2)
    assert not e.value.daily
