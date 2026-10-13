"""OPEN_METEO_API_KEY switches to the customer endpoint; the key never reaches logs or errors."""

import copy
from datetime import date

import pytest

from climate_helpers import FIXTURES
from pipeline.climate import http, open_meteo
from pipeline.climate.definitions import DefinitionsError, climate_settings
from pipeline.climate.open_meteo import CUSTOMER_ARCHIVE_URL, Location

KEY = "test-secret-key-123"


def archive_calls(api):
    return [(u, p) for u, p in api.calls if u in (open_meteo.ARCHIVE_URL, CUSTOMER_ARCHIVE_URL)]


def test_free_endpoint_without_key(fake_api, tmp_path):
    open_meteo.fetch_window([Location("a", 39.6, -106.35)], date(2024, 1, 1), date(2024, 1, 7))
    open_meteo.fetch_grid_elevations([Location("a", 39.6, -106.35)], tmp_path)
    open_meteo.preflight()
    calls = archive_calls(fake_api)
    assert len(calls) == 3 and all(u == open_meteo.ARCHIVE_URL for u, _ in calls)
    assert all("apikey" not in p for _, p in calls)


def test_customer_endpoint_and_apikey_with_key(fake_api, monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_METEO_API_KEY", KEY)
    assert CUSTOMER_ARCHIVE_URL == "https://customer-archive-api.open-meteo.com/v1/archive"
    loc = Location("a", 39.6, -106.35)
    open_meteo.fetch_window([loc], date(2024, 1, 1), date(2024, 1, 7))
    open_meteo.fetch_grid_elevations([loc], tmp_path)
    open_meteo.preflight()
    calls = archive_calls(fake_api)
    assert len(calls) == 3
    assert all(u == CUSTOMER_ARCHIVE_URL and p["apikey"] == KEY for u, p in calls)
    assert KEY not in (tmp_path / open_meteo.GRID_ELEVATION_FILE).read_text()


def test_blank_key_is_ignored(fake_api, monkeypatch):
    monkeypatch.setenv("OPEN_METEO_API_KEY", "   ")
    open_meteo.preflight()
    assert archive_calls(fake_api)[0][0] == open_meteo.ARCHIVE_URL


def test_query_string_carries_apikey(monkeypatch):
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append(http.build_url(url, params))
        return [{"latitude": 1, "longitude": 2, "elevation": 3}]

    monkeypatch.setattr(http, "get_json", fake_get)
    monkeypatch.setenv("OPEN_METEO_API_KEY", KEY)
    open_meteo.preflight()
    assert seen[0].startswith(CUSTOMER_ARCHIVE_URL + "?")
    assert f"&apikey={KEY}" in seen[0]


def test_errors_redact_the_key():
    e = http.HttpStatusError(f"{CUSTOMER_ARCHIVE_URL}?latitude=1&apikey={KEY}&daily=x", 400, f"bad apikey={KEY}")
    assert KEY not in str(e) and KEY not in e.url
    assert "apikey=REDACTED&daily=x" in str(e)
    assert http.redact(f"https://h/p?apikey={KEY}") == "https://h/p?apikey=REDACTED"


def test_get_json_error_from_real_url_is_redacted(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def reject(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error":true,"reason":"bad"}'))

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    monkeypatch.setenv("OPEN_METEO_API_KEY", KEY)
    with pytest.raises(http.HttpStatusError) as e:
        open_meteo.preflight()
    assert KEY not in str(e.value)
    assert "customer-archive-api.open-meteo.com" in str(e.value)


def test_make_budget_modes(tmp_path, monkeypatch):
    free = open_meteo.make_budget(tmp_path, None)
    assert free.per_day == 8000 and free.per_minute == 480 and free.ledger_path.name == "_usage.json"
    monkeypatch.setenv("OPEN_METEO_API_KEY", KEY)
    assert open_meteo.make_budget(tmp_path, None).per_day == 8000  # key without a paid budget: free limits
    paid = open_meteo.make_budget(tmp_path, 1_000_000)
    assert paid.per_day == 1_000_000 and paid.per_minute == 1_000_000
    assert paid.ledger_path.name == "_usage_paid.json"
    assert open_meteo.make_budget(tmp_path, 1_000_000, daily_override=5000).per_day == 5000
    monkeypatch.delenv("OPEN_METEO_API_KEY")
    assert open_meteo.make_budget(tmp_path, 1_000_000).per_day == 8000  # paid budget needs the key


def test_paid_daily_budget_setting(definitions):
    assert climate_settings(definitions).open_meteo_paid_daily_budget is None
    d = copy.deepcopy(definitions)
    d["climate"]["open_meteo"] = {"paid_daily_budget": 250000}
    assert climate_settings(d).open_meteo_paid_daily_budget == 250000.0
    d["climate"]["open_meteo"] = {"paid_daily_budget": -1}
    with pytest.raises(DefinitionsError, match="paid_daily_budget"):
        climate_settings(d)


def test_run_with_key_never_logs_it(tmp_path, fake_api, monkeypatch):
    from pipeline.climate.run import main

    monkeypatch.setenv("OPEN_METEO_API_KEY", KEY)
    lines = []
    code = main(
        [
            "--resorts", str(FIXTURES / "resorts.json"),
            "--definitions", str(FIXTURES / "definitions.yaml"),
            "--out", str(tmp_path / "build"),
            "--cache", str(tmp_path / "cache"),
            "--validation-report", str(tmp_path / "v.md"),
            "--start-year", "2022", "--end-year", "2024",
        ],
        today=date(2026, 10, 11),
        log=lines.append,
    )
    assert code == 0
    out = "\n".join(lines)
    assert "OPEN_METEO_API_KEY is set" in out and CUSTOMER_ARCHIVE_URL in out
    assert KEY not in out
    assert all(u != open_meteo.ARCHIVE_URL for u, _ in fake_api.calls)
    for f in list((tmp_path / "build").rglob("*")) + list((tmp_path / "cache").rglob("*")):
        if f.is_file():
            assert KEY not in f.read_text(encoding="utf-8")
