import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from pipeline import openskimap
from pipeline.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
FETCHED = dt.date(2026, 10, 1)


@pytest.fixture
def cfg():
    return load_config(REPO_ROOT / "config")


@pytest.fixture
def osm_full():
    return json.loads((FIXTURES / "openskimap_ski_areas.geojson").read_text())


@pytest.fixture
def raw_dir(tmp_path):
    """A copy of the fixture raw dir with a filtered OpenSkiMap extract."""
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw", raw)
    (raw / "openskimap").mkdir()
    shutil.copy(FIXTURES / "openskimap_ski_areas.geojson", raw / "openskimap" / openskimap.FULL_NAME)
    openskimap.write_us_extract(raw / "openskimap" / openskimap.FULL_NAME,
                                raw / "openskimap" / openskimap.US_NAME, FETCHED)
    (raw / "openskimap" / openskimap.FULL_NAME).unlink()
    return raw


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def fetched():
    return FETCHED


@pytest.fixture
def fixtures_dir():
    return FIXTURES
