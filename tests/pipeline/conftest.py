import datetime as dt
import json
import os
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
    """A copy of the fixture raw dir: cached full OpenSkiMap download (mtime = FETCHED)
    plus the extract filtered with the real config rules."""
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "raw", raw)
    (raw / "openskimap").mkdir()
    full = raw / "openskimap" / openskimap.FULL_NAME
    shutil.copy(FIXTURES / "openskimap_ski_areas.geojson", full)
    ts = dt.datetime.combine(FETCHED, dt.time(12)).timestamp()
    os.utime(full, (ts, ts))
    kw = load_config(REPO_ROOT / "config").definitions.openskimap.filter_kwargs()
    kw["state_shapes"] = openskimap.load_state_shapes(FIXTURES / "state_shapes.geojson")
    openskimap.write_us_extract(full, raw / "openskimap" / openskimap.US_NAME, FETCHED, **kw)
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
