import copy

import pytest
from pydantic import ValidationError

from pipeline.schemas.climate import SEASON_MONTH_ORDER, ClimateFile, ClimateMonth, DensityMix


def month(m, **over):
    row = {
        "resort_id": "test-peak",
        "month": m,
        "snowfall_in": 50.0,
        "powder_days": 3.2,
        "banner_days": 0.8,
        "avg_storm_depth_in": 7.5,
        "snowfall_source": "snotel_swe",
        "density_pct_median": 8.1,
        "density_mix": {"dry": 0.3, "average": 0.5, "moist": 0.2},
        "density_source": "snotel",
        "temp_high_f": 30.0,
        "temp_low_f": 10.0,
        "wind_mph": 15.0,
        "sun_pct": 55.0,
    }
    row.update(over)
    return row


def full_file():
    return {
        "version": 1,
        "generated_at": "2026-10-11T12:00:00+00:00",
        "resort_id": "test-peak",
        "source_ids": ["open-meteo-era5-archive", "nrcs-snotel-9001-co"],
        "confidence": "estimated",
        "snowfall_null_reason": None,
        "period": {"start_year": 2001, "end_year": 2026},
        "months": [
            month(m)
            if m in (11, 12, 1, 2, 3, 4)
            else month(m, density_pct_median=None, density_mix=None, density_source=None, avg_storm_depth_in=None)
            for m in SEASON_MONTH_ORDER
        ],
    }


def test_full_climate_file_validates():
    f = ClimateFile.model_validate(full_file())
    assert [m.month for m in f.months] == list(SEASON_MONTH_ORDER)
    dumped = f.model_dump(mode="json")
    assert set(dumped) == {
        "version", "generated_at", "resort_id", "source_ids", "confidence", "snowfall_null_reason", "period", "months"
    }
    assert set(dumped["months"][0]) == {
        "resort_id", "month", "snowfall_in", "powder_days", "banner_days", "avg_storm_depth_in", "snowfall_source",
        "density_pct_median", "density_mix", "density_source", "temp_high_f", "temp_low_f",
        "wind_mph", "sun_pct",
    }


def test_all_null_month_is_valid():
    ClimateMonth.model_validate({"resort_id": "a", "month": 7})


def test_extra_fields_forbidden():
    data = full_file()
    data["notes"] = "x"
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)
    data = full_file()
    data["months"][0]["snow_cm"] = 1
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)


def test_month_order_and_count():
    data = full_file()
    data["months"] = data["months"][3:] + data["months"][:3]  # calendar order
    with pytest.raises(ValidationError, match="season order"):
        ClimateFile.model_validate(data)
    data = full_file()
    data["months"] = data["months"][:11]
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)


def test_month_resort_id_must_match():
    data = full_file()
    data["months"][2]["resort_id"] = "other"
    with pytest.raises(ValidationError, match="resort_id"):
        ClimateFile.model_validate(data)


def test_density_mix_sum():
    DensityMix(dry=0.333, average=0.334, moist=0.333)
    with pytest.raises(ValidationError, match="sum to 1"):
        DensityMix(dry=0.5, average=0.5, moist=0.1)


def test_density_needs_source_and_source_needs_density():
    with pytest.raises(ValidationError, match="density_source"):
        ClimateMonth.model_validate(month(1, density_source=None))
    with pytest.raises(ValidationError, match="no density values"):
        ClimateMonth.model_validate(month(1, density_pct_median=None, density_mix=None))
    with pytest.raises(ValidationError):
        ClimateMonth.model_validate(month(1, density_source="guess"))


def test_banner_cannot_exceed_powder_and_ranges():
    with pytest.raises(ValidationError, match="banner"):
        ClimateMonth.model_validate(month(1, powder_days=1.0, banner_days=2.0))
    with pytest.raises(ValidationError):
        ClimateMonth.model_validate(month(13))
    with pytest.raises(ValidationError):
        ClimateMonth.model_validate(month(1, sun_pct=120))
    with pytest.raises(ValidationError):
        ClimateMonth.model_validate(month(1, snowfall_in=-1))


def test_period_and_sources():
    data = full_file()
    data["period"] = {"start_year": 2026, "end_year": 2026}
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)
    data = full_file()
    data["source_ids"] = []
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)
    data = full_file()
    data["source_ids"] = ["Open Meteo"]
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)


def test_snowfall_source_rules():
    with pytest.raises(ValidationError, match="needs a snowfall_source"):
        ClimateMonth.model_validate(month(1, snowfall_source=None))
    with pytest.raises(ValidationError, match="snowfall_in is null"):
        ClimateMonth.model_validate(month(1, snowfall_in=None))
    with pytest.raises(ValidationError, match="need snowfall_in"):
        ClimateMonth.model_validate(month(1, snowfall_in=None, snowfall_source=None))
    ok = month(1, snowfall_in=None, snowfall_source=None, powder_days=None, banner_days=None, avg_storm_depth_in=None)
    ClimateMonth.model_validate(ok)
    with pytest.raises(ValidationError):
        ClimateMonth.model_validate(month(1, snowfall_source="model"))


def test_all_null_snowfall_needs_reason_and_confidence_required():
    data = full_file()
    for m in data["months"]:
        m.update(snowfall_in=None, snowfall_source=None, powder_days=None, banner_days=None, avg_storm_depth_in=None)
    with pytest.raises(ValidationError, match="snowfall_null_reason"):
        ClimateFile.model_validate(data)
    data["snowfall_null_reason"] = "ERA5 grid cell sits 2,077 ft below the resort base"
    ClimateFile.model_validate(data)
    del data["confidence"]
    with pytest.raises(ValidationError):
        ClimateFile.model_validate(data)
