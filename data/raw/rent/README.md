# Rent baselines (Phase 5)

HUD Fair Market Rents and Census ACS median gross rent (table B25064) for the county of each Tier A base area. `pipeline/rent.py` finds the county by point in polygon against `data/raw/geo/us-counties.geojson` (from `npm run build:counties`) and the build reads only this cache.

## Fetch (once the hosts are reachable)

```bash
python pipeline/build.py                       # resorts.json must exist
export HUD_API_TOKEN=...                       # free token: https://www.huduser.gov/hudapi/public/register
python -m pipeline.rent fetch                  # hosts: www.huduser.gov, api.census.gov
python pipeline/build.py
```

Without `HUD_API_TOKEN` only ACS is fetched. ACS works without a key at low volume. Vintages come from `config/definitions.yaml` `rent` (`hud_fmr_year: null` means the latest HUD year, `acs_year`).

## Cache layout

| Path | Content |
|---|---|
| `hud_fmr/<county fips>.json` | `{fetched_date, url, response}` from `GET https://www.huduser.gov/hudapi/public/fmr/data/<fips>99999`; `response.data.basicdata` gives `Efficiency`, `One-Bedroom`, `Two-Bedroom` (a list with one row per ZIP in Small Area FMR areas, plus an `MSA level` row) |
| `hud_fmr_<year>.csv` | optional HUD county FMR download with columns `fips`, `countyname`, `fmr_0`, `fmr_1`, `fmr_2`; used when no API cache exists for a county |
| `acs/<year>_county_<fips>.json` | `{fetched_date, url, response}` from `GET https://api.census.gov/data/<year>/acs/acs5?get=NAME,B25064_001E&for=county:<ccc>&in=state:<ss>` |
| `acs/<year>_zcta_<zip>.json` | same for `for=zip%20code%20tabulation%20area:<zip>` (parsed; a ZIP lookup needs ZCTA outlines, not yet wired) |

Census null sentinels such as `-666666666` become `null`. Sources are emitted as `hud-fmr-fy<year>` and `census-acs5-<year>-b25064`.

## How the baseline is used

* A housing site with no published rent shows the baseline as an `estimated` range, in the order of `rent.baseline_preference` (default HUD FMR studio, then ACS median).
* Lucrativeness rent follows `rent.lucrativeness_rent`: the cheapest employee housing (resort owned or master lease) with a published rent, else the baseline. The career output echoes `rent_basis` and `rent_source_id`.
