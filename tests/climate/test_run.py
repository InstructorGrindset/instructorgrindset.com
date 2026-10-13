import json
from datetime import date
from pathlib import Path

import pytest

from climate_helpers import FIXTURES, FakeApi, synthetic_snow_cm
from pipeline.climate import http, open_meteo, snotel
from pipeline.climate.run import main
from pipeline.climate.units import cm_to_in
from pipeline.schemas.climate import SEASON_MONTH_ORDER, ClimateFile

TODAY = date(2026, 10, 11)


def run(tmp_path, *extra, log=None):
    lines = []
    args = [
        "--resorts", str(FIXTURES / "resorts.json"),
        "--definitions", str(FIXTURES / "definitions.yaml"),
        "--out", str(tmp_path / "build"),
        "--cache", str(tmp_path / "cache"),
        "--validation-report", str(tmp_path / "climate-validation.md"),
        "--start-year", "2022",
        "--end-year", "2024",
        *extra,
    ]
    code = main(args, today=TODAY, log=lines.append)
    return code, "\n".join(lines)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_end_to_end_writes_contract_files(tmp_path, fake_api):
    code, out = run(tmp_path)
    assert code == 0, out
    climate_dir = tmp_path / "build" / "climate"
    files = sorted(p.name for p in climate_dir.iterdir())
    assert files == [
        "banded-bowl.json", "lonely-hill.json", "provenance.json", "sample-ridge.json", "sources.json", "test-peak.json"
    ]

    # Tier A with a usable SNOTEL station: snowfall from daily SWE gains, density from storms.
    peak = ClimateFile.model_validate(read(climate_dir / "test-peak.json"))
    assert peak.confidence == "estimated" and peak.snowfall_null_reason is None
    assert peak.period.start_year == 2022 and peak.period.end_year == 2024
    assert [m.month for m in peak.months] == list(SEASON_MONTH_ORDER)
    by_month = {m.month: m for m in peak.months}
    # Synthetic station: 0.8 in SWE and about 10 in depth every 6th day, so about 10 in per storm.
    assert by_month[12].snowfall_source == "snotel_swe"
    assert by_month[12].snowfall_in == pytest.approx(50.0, abs=3)
    assert by_month[12].powder_days == 5.0 and by_month[12].banner_days == 0.0
    assert by_month[12].avg_storm_depth_in == pytest.approx(10.0, abs=0.6)
    assert by_month[7].snowfall_in == 0.0 and by_month[7].avg_storm_depth_in is None
    assert by_month[1].temp_high_f == pytest.approx(24.8)  # -4 C
    assert by_month[1].wind_mph == pytest.approx(20.0)
    assert by_month[1].sun_pct == pytest.approx(50.0)
    for m in (11, 12, 1, 2, 3, 4):
        assert by_month[m].density_source == "snotel"
        assert 7.0 <= by_month[m].density_pct_median <= 9.5
    assert by_month[7].density_source is None and by_month[7].density_mix is None
    assert "nrcs-snotel-9005-co" in peak.source_ids
    assert "open-meteo-era5-archive" in peak.source_ids

    # Tier B low relief resort: ERA5 snowfall kept, density estimated from storm temperature.
    ridge = ClimateFile.model_validate(read(climate_dir / "sample-ridge.json"))
    rm = {m.month: m for m in ridge.months}
    dec_total = sum(cm_to_in(synthetic_snow_cm(date(2022, 12, d))) for d in range(1, 32))
    assert rm[12].snowfall_source == "era5"
    assert rm[12].snowfall_in == pytest.approx(round(dec_total, 1))
    assert rm[12].powder_days == 6.0 and rm[12].banner_days == 1.0  # five 20 cm days and one 35 cm day
    assert rm[1].density_source == "estimated"
    assert rm[1].density_pct_median == pytest.approx(7.5)  # Anderson 1976 at -9 C
    assert rm[4].density_mix.moist == 1.0  # April storms at -3 C are over 12 percent
    assert "anderson-1976-new-snow-density" in ridge.source_ids
    assert not any(s.startswith("nrcs-snotel") for s in ridge.source_ids)

    # No station, no base elevation: snowfall null with a reason, temperatures kept.
    hill = ClimateFile.model_validate(read(climate_dir / "lonely-hill.json"))
    assert all(m.snowfall_in is None and m.snowfall_source is None and m.powder_days is None for m in hill.months)
    assert "base elevation is unknown" in hill.snowfall_null_reason
    assert all(m.temp_high_f is not None for m in hill.months)

    # Validation: annual totals against published figures.
    prov = read(climate_dir / "provenance.json")["resorts"]
    assert prov["test-peak"]["validation"]["status"] == "ok"
    assert prov["sample-ridge"]["validation"]["status"] == "flagged"  # about 364 in against 260 in
    assert prov["sample-ridge"]["validation"]["diff_pct"] == pytest.approx(40.1, abs=0.5)
    bowl_file = ClimateFile.model_validate(read(climate_dir / "banded-bowl.json"))
    assert prov["banded-bowl"]["validation"]["status"] == "suppressed"  # about 290 in against 150 in
    assert all(m.snowfall_in is None and m.powder_days is None for m in bowl_file.months)
    assert "published 150 in" in bowl_file.snowfall_null_reason
    assert any(m.density_source == "snotel" for m in bowl_file.months)  # density still published
    report = (tmp_path / "climate-validation.md").read_text()
    assert "| Banded Bowl | A | null | 290 | 150 | synthetic-published-snow | +93% | suppressed |" in report
    assert "| Sample Ridge | B | era5 |" in report and "| flagged |" in report

    sources = read(climate_dir / "sources.json")
    ids = {s["id"] for s in sources["sources"]}
    assert ids == {
        "open-meteo-era5-archive", "nrcs-snotel-9005-co", "nrcs-snotel-9001-co", "anderson-1976-new-snow-density"
    }
    for s in sources["sources"]:
        assert s["url"].startswith("https://") and s["title"] and s["accessed_date"] == "2026-10-11"
    om = next(s for s in sources["sources"] if s["id"] == "open-meteo-era5-archive")
    assert "fixed" in om["note"] and "ratio" in om["note"]
    sn = next(s for s in sources["sources"] if s["id"] == "nrcs-snotel-9005-co")
    assert "SNEW.f" in sn["note"]

    assert prov["test-peak"]["snotel"]["selection_mode"] == "distance_only"
    assert prov["test-peak"]["snotel"]["station"] == "9005:CO:SNTL"
    assert prov["test-peak"]["snowfall"]["swe_annual_in"] >= prov["test-peak"]["snowfall"]["depth_gain_annual_in"]
    assert prov["test-peak"]["open_meteo"]["elevation_m"] == 2500.0
    assert prov["test-peak"]["open_meteo"]["grid_cell_elevation_m"] == 2500.0
    assert "DEM" in prov["test-peak"]["open_meteo"]["elevation_source"]
    assert prov["lonely-hill"]["snotel"]["result"].startswith("no SNOTEL station")
    bowl = prov["banded-bowl"]["snotel"]
    assert bowl["selection_mode"] == "distance_and_elevation"
    assert (bowl["resort_base_ft"], bowl["resort_summit_ft"]) == (8100.0, 11500.0)
    assert bowl["station"] == "9001:CO:SNTL"
    assert "nrcs-snotel-9001-co" in bowl_file.source_ids
    assert prov["sample-ridge"]["snotel"]["result"] == "not attempted for this tier"

    # Raw responses are cached for the resume path.
    assert (tmp_path / "cache" / "open_meteo" / "test-peak.json").exists()
    assert (tmp_path / "cache" / "open_meteo" / "_grid_elevation.json").exists()
    assert (tmp_path / "cache" / "snotel" / "stations.json").exists()
    assert (tmp_path / "cache" / "snotel" / "9005_CO_SNTL.json").exists()
    assert "ok        test-peak: snowfall snotel_swe" in out


def test_om_cache_only_skips_uncached(tmp_path, fake_api):
    assert run(tmp_path, "--only", "test-peak")[0] == 0
    n_archive = fake_api.count(open_meteo.ARCHIVE_URL)
    code, out = run(tmp_path, "--om-cache-only")
    assert code == 0, out
    assert "skips 3 resort(s)" in out
    assert fake_api.count(open_meteo.ARCHIVE_URL) == n_archive  # no history or grid fetches


def test_second_run_uses_cache_only(tmp_path, fake_api):
    assert run(tmp_path)[0] == 0
    n = len(fake_api.calls)
    code, out = run(tmp_path, "--offline")
    assert code == 0, out
    assert len(fake_api.calls) == n
    code, out = run(tmp_path)  # online but fully cached: no preflight, no fetch
    assert code == 0 and len(fake_api.calls) == n


def test_network_blocked_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "get_json", FakeApi(blocked=True))
    code, out = run(tmp_path)
    assert code == 3
    assert "network blocked" in out and "Nothing was written" in out
    assert "archive-api.open-meteo.com" in out
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "cache").exists()


def test_offline_without_cache_writes_nothing(tmp_path, fake_api):
    code, out = run(tmp_path, "--offline")
    assert code == 1
    assert fake_api.calls == []
    assert not (tmp_path / "build").exists()
    assert "not cached (offline)" in out


def test_one_resort_failing_does_not_stop_the_rest(tmp_path, monkeypatch):
    api = FakeApi(fail_lat=40.5)
    monkeypatch.setattr(http, "get_json", api)
    code, out = run(tmp_path)
    assert code == 1
    climate_dir = tmp_path / "build" / "climate"
    assert (climate_dir / "test-peak.json").exists()
    assert not (climate_dir / "sample-ridge.json").exists()
    assert "failed    sample-ridge" in out


def test_filters_and_plan(tmp_path, fake_api):
    code, out = run(tmp_path, "--plan")
    assert code == 0 and fake_api.calls == [] and not (tmp_path / "build").exists()
    assert "need Open Meteo" in out
    code, _ = run(tmp_path, "--tier", "A")
    assert code == 0
    names = sorted(p.name for p in (tmp_path / "build" / "climate").glob("*-*.json"))
    assert names == ["banded-bowl.json", "lonely-hill.json", "test-peak.json"]
    assert (tmp_path / "climate-validation.md").exists()
    code, _ = run(tmp_path, "--only", "nope")
    assert code == 2


def test_bad_inputs(tmp_path, fake_api):
    lines = []
    assert main(["--definitions", str(tmp_path / "missing.yaml"), "--resorts", str(FIXTURES / "resorts.json"),
                 "--out", str(tmp_path / "b"), "--validation-report", str(tmp_path / "v.md")], log=lines.append) == 2
    assert "does not exist" in lines[0]
    lines.clear()
    assert main(["--definitions", str(FIXTURES / "definitions.yaml"), "--resorts", str(tmp_path / "none.json"),
                 "--out", str(tmp_path / "b"), "--validation-report", str(tmp_path / "v.md")], log=lines.append) == 2
    assert "cannot read resorts" in lines[0]


def test_repo_definitions_load_when_present():
    from pipeline.climate.definitions import load_definitions

    path = Path(__file__).resolve().parents[2] / "config" / "definitions.yaml"
    if not path.exists():
        pytest.skip("config/definitions.yaml not written yet")
    s = load_definitions(path)
    assert (s.powder_day_in, s.banner_day_in, s.dry_below_pct, s.moist_above_pct) == (6, 12, 7, 12)


def test_rate_limited_at_preflight_writes_nothing(tmp_path, monkeypatch):
    def limited(url, params=None, **kw):
        raise http.RateLimited("Daily API limit reached: Daily API request limit exceeded.", daily=True)

    monkeypatch.setattr(http, "get_json", limited)
    code, out = run(tmp_path)
    assert code == 3
    assert "rate limiting" in out and "Nothing was written" in out
    assert not (tmp_path / "build").exists() and not (tmp_path / "cache").exists()


def test_resort_elevation_ft_reads_sourced_base_and_summit():
    from pipeline.climate.run import resort_elevation_ft

    src = lambda v: {"value": v, "source_id": "s", "as_of": "2026-10-11", "confidence": "published"}
    assert resort_elevation_ft({"elevation": {"base_ft": src(8120), "summit_ft": src(11570)}}) == (8120.0, 11570.0)
    assert resort_elevation_ft({"elevation": {"base_ft": src(8120), "summit_ft": None}}) == (8120.0, None)
    assert resort_elevation_ft({"elevation": {"base_ft": None, "summit_ft": src(11570)}}) == (None, 11570.0)
    assert resort_elevation_ft({"elevation": None}) == (None, None)
    assert resort_elevation_ft({}) == (None, None)


@pytest.mark.parametrize(
    "annual,published,status,diff",
    [
        (300.0, 300.0, "ok", 0.0),
        (405.0, 300.0, "ok", 35.0),        # exactly 35 percent is not flagged
        (406.0, 300.0, "flagged", 35.3),
        (195.0, 300.0, "ok", -35.0),
        (150.0, 300.0, "flagged", -50.0),  # exactly 50 percent is flagged, not suppressed
        (149.0, 300.0, "suppressed", -50.3),
        (93.1, 500.0, "suppressed", -81.4),
        (None, 300.0, "no comparison", None),
        (300.0, None, "no comparison", None),
    ],
)
def test_validate_annual_thresholds(annual, published, status, diff):
    from pipeline.climate.run import validate_annual

    v = validate_annual(annual, published, 35, 50)
    assert v["status"] == status
    assert v["diff_pct"] == (pytest.approx(diff) if diff is not None else None)


def test_published_annual_reads_sourced_value():
    from pipeline.climate.run import published_annual

    r = {"snow": {"annual_avg_in": {"value": 500, "source_id": "wikipedia-alta", "as_of": "2026-10-12", "confidence": "published"}}}
    assert published_annual(r) == (500.0, "wikipedia-alta")
    assert published_annual({"snow": None}) == (None, None)
    assert published_annual({}) == (None, None)
