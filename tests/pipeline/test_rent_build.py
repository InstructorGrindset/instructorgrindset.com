"""The rent for lucrativeness through the whole build: median rent figures, then a hand placed Zillow ZORI file.

SYNTHETIC TEST FIXTURES: the towns, publisher pages, ZORI rows and pay below are invented. Never copy to data/.
"""
import csv
import json
import shutil

import pytest
import yaml

from pipeline.build import build

PAY = """
batch: fixture-pay
researched_date: 2026-10-01
sources:
  - {id: fixture-pay-sheet, url: https://example.org/pay, title: Fixture pay sheet, accessed_date: 2026-10-01}
records:
  - {resort_id: fixture-peak, season: 2025-26, cert_level: level_2, hourly_low: 20, hourly_high: 24,
     confidence: published, source_id: fixture-pay-sheet, verified_date: 2026-01-15}
  - {resort_id: fixture-basin, season: 2025-26, cert_level: level_2, hourly_low: 19, hourly_high: 23,
     confidence: published, source_id: fixture-pay-sheet, verified_date: 2026-01-15}
"""
MEDIAN = {
    "batch": "median-rent-fixture", "researched_date": "2026-10-01",
    "sources": [
        {"id": "fx-zumper-peak", "url": "https://www.zumper.com/rent-research/fixture-town", "title": "Fixture Zumper page",
         "accessed_date": "2026-10-01"},
        {"id": "fx-rentometer-peak", "url": "https://www.rentometer.com/fixture-town", "title": "Fixture Rentometer page",
         "accessed_date": "2026-10-01"},
        {"id": "fx-redfin-peak", "url": "https://www.redfin.com/fixture-town", "title": "Fixture Redfin page",
         "accessed_date": "2026-10-01"},
        {"id": "fx-foreclosure-peak", "url": "https://www.foreclosure.com/fixture-town", "title": "Fixture Foreclosure page",
         "accessed_date": "2026-10-01"},
    ],
    "figures": [
        {"resort_ids": ["fixture-peak"], "town": "Fixture Town, CO", "geography": "city", "unit": "all",
         "median_rent_usd": med, "period": "2026-08", "publisher": pub, "source_id": sid, "as_of": "2026-10-01",
         "confidence": "estimated"}
        for med, pub, sid in ((2400, "zumper", "fx-zumper-peak"), (2000, "other", "fx-rentometer-peak"),
                              (9000, "redfin", "fx-redfin-peak"), (1920, "other", "fx-foreclosure-peak"))
    ],
}
MONTHS = ["2025-09-30", "2025-10-31", "2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30",
          "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31"]


@pytest.fixture
def out(tmp_path):
    return tmp_path / "build"


def _run(repo_root, raw_dir, out, tmp_path):
    cfg_dir = tmp_path / "config"
    if not cfg_dir.exists():
        shutil.copytree(repo_root / "config", cfg_dir)
        p = cfg_dir / "definitions.yaml"
        data = yaml.safe_load(p.read_text())
        data["ownership"]["missing_tier_a_record"] = "warn"
        p.write_text(yaml.safe_dump(data, sort_keys=False))
    return build(repo_root, raw_dir=raw_dir, out_dir=out, report_path=tmp_path / "tier-a-draft.md",
                 states_geojson=False, config_dir=cfg_dir)


def _county_zori(path, months=MONTHS):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["RegionID", "SizeRank", "RegionName", "RegionType", "StateName", "State", "Metro", "StateCodeFIPS",
                    "MunicipalCodeFIPS"] + months)
        w.writerow(["901", "1", "Fixture County", "county", "CO", "CO", "", "8", "998"] + [2100 + i for i in range(len(months))])
        w.writerow(["902", "2", "Basin Fixture County", "county", "UT", "UT", "", "49", "998"] + [1700 + 10 * i for i in range(len(months))])


def _inputs(raw_dir, counties_geojson):
    shutil.copytree(counties_geojson.parent, raw_dir / "geo")
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY)
    (raw_dir / "rent").mkdir()
    (raw_dir / "rent" / "median_rent_fixture.yaml").write_text(yaml.safe_dump(MEDIAN, sort_keys=False))
    # placed by hand, as the owner would from zillow.com/research/data
    _county_zori(raw_dir / "rent" / "zillow" / "County_zori_uc_sfrcondomfr_sm_month.csv")


def _resorts(out):
    return {r["id"]: r for r in json.loads((out / "resorts.json").read_text())["resorts"]}


def test_median_rent_then_zori_reach_the_rent_block_and_the_heat_map(repo_root, raw_dir, out, tmp_path, counties_geojson):
    _inputs(raw_dir, counties_geojson)
    result = _run(repo_root, raw_dir, out, tmp_path)
    by = _resorts(out)
    # Fixture Peak: the median of Zumper, Rentometer and Redfin (Foreclosure.com is dropped: others cover the town)
    peak = by["fixture-peak"]["career"]["lucrativeness"]
    assert peak["rent"]["basis"] == "median_rent" and peak["rent"]["value"] == 2400
    assert peak["rent"]["publishers"] == ["Zumper", "Rentometer", "Redfin"] and peak["rent"]["count"] == 3
    assert peak["rent"]["source_ids"] == ["fx-zumper-peak", "fx-rentometer-peak", "fx-redfin-peak"]
    assert peak["rent"]["geography"] == {"level": "city", "id": None, "name": "Fixture Town, CO"}
    # mid at the middle of the 20 to 24 range, high at the top; the rent is not subtracted (owner decision 2026-10-14)
    assert peak["status"] == "computed" and peak["mid"] == round(22 * 40 * 5 * 4.33)
    assert peak["high"] == round(24 * 40 * 5 * 4.33)
    # Fixture Basin: no median figure, so the hand placed ZORI county file (refreshed into the extract)
    basin = by["fixture-basin"]["career"]["lucrativeness"]
    assert basin["rent"]["basis"] == "zillow_zori" and basin["rent"]["label"] == "Zillow typical rent (ZORI)"
    assert basin["rent"]["value"] == 1810 and basin["rent"]["as_of"] == "2026-08-31"
    assert basin["rent"]["confidence"] == "published" and basin["rent"]["source_ids"] == ["zillow-zori"]
    assert basin["rent"]["geography"] == {"level": "county", "id": "49998", "name": "Basin Fixture County, UT"}
    assert basin["mid"] == round(21 * 40 * 5 * 4.33) and basin["high"] == round(23 * 40 * 5 * 4.33)
    extract = (raw_dir / "rent" / "zillow" / "zori_extract.csv").read_text().splitlines()
    assert len(extract) == 3 and extract[0].startswith("level,region_id")
    sources = {s["id"]: s for s in json.loads((out / "sources.json").read_text())["sources"]}
    assert sources["zillow-zori"]["title"] == "Zillow Observed Rent Index (ZORI), Zillow Research"
    assert {"fx-zumper-peak", "fx-rentometer-peak", "fx-redfin-peak"} <= set(sources)
    assert {"zillow-zori", "fx-zumper-peak"} <= set(by["fixture-basin"]["sources"]) | set(by["fixture-peak"]["sources"])
    # Color by Lucrativeness turns on by itself: the slim index has values and the rasters exist
    props = {f["properties"]["id"]: f["properties"] for f in json.loads((out / "resorts.geojson").read_text())["features"]}
    assert props["fixture-peak"]["lucrativeness_mid"] == peak["mid"] and props["fixture-basin"]["lucrativeness_mid"] == basin["mid"]
    manifest = json.loads((out / "heat" / "lucrativeness.json").read_text())
    assert manifest["values"]["resorts"] == 2 and (out / "heat" / "lucrativeness-lower48.png").is_file()
    assert not any("lucrativeness heat rasters not written" in w for w in result.warnings)


def test_stale_zori_is_flagged_and_placeholder_locations_get_no_place_rent(repo_root, raw_dir, out, tmp_path,
                                                                          counties_geojson):
    _inputs(raw_dir, counties_geojson)
    (raw_dir / "rent" / "median_rent_fixture.yaml").unlink()
    old = [m.replace("2026", "2024").replace("2025", "2023").replace("2024-02-28", "2024-02-29") for m in MONTHS]
    _county_zori(raw_dir / "rent" / "zillow" / "County_zori_uc_sfrcondomfr_sm_month.csv", old)
    # Fixture Basin's location source becomes a placeholder: it gets no ZORI, ACS or HUD rent
    tier = raw_dir / "tier_a" / "fixture.yaml"
    tier.write_text(tier.read_text().replace(
        "{id: fixture-source-b, url: https://example.org/b, title: Fixture source B, accessed_date: 2026-10-01}",
        "{id: fixture-source-b, url: https://example.org/b, title: Fixture source B, accessed_date: 2026-10-01, "
        "note: regional placeholder}"))
    result = _run(repo_root, raw_dir, out, tmp_path)
    by = _resorts(out)
    peak = by["fixture-peak"]["career"]
    assert peak["lucrativeness"]["rent"]["stale"] is True and "lucrativeness.rent" in peak["stale_fields"]
    assert any("rent for 'fixture-peak' (zillow_zori, as of 2024-08-31) is older than 18 months" in w for w in result.warnings)
    basin = by["fixture-basin"]["career"]["lucrativeness"]
    assert basin["rent"] is None and basin["status"] == "computed"   # no rent no longer blocks the figure


def test_without_rent_files_the_build_warns_and_lucrativeness_still_computes(repo_root, raw_dir, out, tmp_path):
    (raw_dir / "pay").mkdir()
    (raw_dir / "pay" / "fixture.yaml").write_text(PAY)
    result = _run(repo_root, raw_dir, out, tmp_path)
    w = "\n".join(result.warnings)
    # Owner decision 2026-10-14: lucrativeness needs pay only, so its rasters are written without any rent file.
    assert "no Zillow ZORI extract" in w and "lucrativeness heat rasters not written" not in w
    with_pay = [r for r in _resorts(out).values() if (r["career"] or {}).get("pay")]
    assert with_pay and all(r["career"]["lucrativeness"]["mid"] is not None for r in with_pay)
    assert all(r["career"]["lucrativeness"]["rent"] is None for r in with_pay)
