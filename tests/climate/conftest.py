"""Fixtures for climate ETL tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[1]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from climate_helpers import FakeApi, load_fixture  # noqa: E402
from pipeline.climate import http  # noqa: E402
from pipeline.climate.definitions import climate_settings  # noqa: E402


@pytest.fixture
def definitions() -> dict:
    return load_fixture("definitions.yaml")


@pytest.fixture
def settings(definitions):
    return climate_settings(definitions)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http, "_sleep", lambda s: None)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Tests run on the free endpoint unless they set OPEN_METEO_API_KEY themselves."""
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(http, "get_json", api)
    return api
