import copy

import pytest

from pipeline.climate.definitions import DefinitionsError, climate_settings, load_definitions


def test_settings_from_fixture(settings):
    assert settings.powder_day_in == 6.0
    assert settings.banner_day_in == 12.0
    assert settings.dry_below_pct == 7.0
    assert settings.moist_above_pct == 12.0
    assert settings.storm_min_daily_in == 1.0
    assert settings.snotel.radius_km == 25.0
    assert settings.density_estimate_method == "anderson_1976"


def test_alternative_key_paths_and_value_wrappers(definitions):
    defs = copy.deepcopy(definitions)
    del defs["snow"]
    del defs["density_classes_pct"]
    defs["powder_day"] = {"min_new_snow_in": {"value": 6, "unit": "in"}}
    defs["banner_day"] = {"min_new_snow_in": 12}
    defs["density_classes"] = {"dry_below_pct": 7, "moist_above_pct": 12}
    s = climate_settings(defs)
    assert (s.powder_day_in, s.banner_day_in, s.dry_below_pct, s.moist_above_pct) == (6, 12, 7, 12)


def test_missing_threshold_raises_clear_error(definitions):
    defs = copy.deepcopy(definitions)
    del defs["snow"]["powder_day_in"]
    with pytest.raises(DefinitionsError, match="powder day threshold") as e:
        climate_settings(defs)
    assert "Suggested block" in str(e.value)


def test_missing_climate_block_raises(definitions):
    defs = copy.deepcopy(definitions)
    del defs["climate"]["snotel"]["radius_km"]
    with pytest.raises(DefinitionsError, match="radius_km"):
        climate_settings(defs)
    del defs["climate"]
    with pytest.raises(DefinitionsError, match="'climate' block"):
        climate_settings(defs)


def test_bad_method_and_order(definitions):
    defs = copy.deepcopy(definitions)
    defs["climate"]["density_estimate_method"] = "guess"
    with pytest.raises(DefinitionsError, match="density_estimate_method"):
        climate_settings(defs)
    defs = copy.deepcopy(definitions)
    defs["snow"]["banner_day_in"] = 3
    with pytest.raises(DefinitionsError, match="banner"):
        climate_settings(defs)


def test_load_definitions_missing_file(tmp_path):
    with pytest.raises(DefinitionsError, match="does not exist"):
        load_definitions(tmp_path / "definitions.yaml")


def test_load_definitions_file(tmp_path):
    from pathlib import Path

    s = load_definitions(Path(__file__).parent / "fixtures" / "definitions.yaml")
    assert s.powder_day_in == 6
