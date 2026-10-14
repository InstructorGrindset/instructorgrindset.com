# Rent for lucrativeness

Lucrativeness subtracts one monthly rent per resort. The build takes the first of these that has a value (`config/definitions.yaml` `rent.lucrativeness_rent`, owner decision 2026-10-14):

1. **Median rent** (`median_rent`): the median of the sourced median rent figures for the resort's town in `median_rent_*.yaml` (`pipeline/rent_median.py`).
2. **Zillow typical rent (ZORI)** (`zillow_zori`): the Zillow Observed Rent Index for the nearest ZIP, else the county, else the metro (`pipeline/rent_zillow.py`). ZORI is a typical market rent, so it is labelled "Zillow typical rent (ZORI)".
3. **Census ACS median gross rent** of the county (`acs_median`).
4. **HUD Fair Market Rent, one bedroom** (`fmr_one_br`).

Employee housing rent no longer feeds lucrativeness; it still shows with each housing site. Nothing is interpolated: a resort with none of the four has no rent and no lucrativeness. A resort whose location source says its point is a placeholder (MetroSlopes at SFO) gets no ZORI, ACS or HUD rent.

## 1. Median rent figures (`median_rent_<batch>.yaml`)

```yaml
batch: median-rent-2026-10
researched_date: 2026-10-14
sources: [{id, url, title, accessed_date, note}]
figures:
  - {resort_ids: [breckenridge], town: "Breckenridge, CO", geography: city|zip|county|metro,
     unit: all|studio|1br|2br|3br, median_rent_usd: 2450, period: "2026-09", publisher: zillow|redfin|apartments_com|
     rentcafe|rent_com|zumper|news|blog|other, publisher_name: "Optional display name", source_id, as_of, confidence, note}
```

Schema: `pipeline/schemas/rent.py` (`MedianRentBatch`). `period` is the data period (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`). A research file never holds a `reported` figure. Rules, all set in `rent.median`:

* Figures whose period ended more than 18 months before the build are ignored (a year counts to December 31, a month to its last day).
* The publisher is the registered domain of the source URL (`assets.rentometer.com` is Rentometer); `rent.median.publishers` names each domain, else `publisher_name`, else the domain.
* Foreclosure.com figures (`last_resort_publishers`) count only when no other publisher has a figure, of any unit size, for the same town at the same geography level.
* Only the best geography level with a usable figure counts, in the order ZIP, city, county, metro. Levels are never mixed. Metro figures show as "Bozeman metro" or "Breckenridge micro area"; Anchorage shows as "Municipality of Anchorage".
* Within that level: figures for all unit sizes when there are any, else one bedroom. The unit used is shown. Two and three bedroom figures stay in the file but are not used.
* The median across the figures (one per source page): the middle value, or the mean of the two middle values for an even count. Outliers stay as published, and every source is linked. One figure shows as "single source".

## 2. Zillow ZORI (`zillow/`)

Series: **ZORI (Smoothed): All Homes Plus Multifamily Time Series ($)** from [Zillow Research](https://www.zillow.com/research/data/), section **Rentals**. The build reads only the committed compact extract `zillow/zori_extract.csv`. The full files are large and stay out of git (`.gitignore`).

### Get the files

Either let the pipeline download them (needs network access to `files.zillowstatic.com`):

```bash
python -m pipeline.rent_zillow fetch     # writes zillow/download/<file>.csv plus a <file>.csv.json with the date
python pipeline/build.py                 # rebuilds zillow/zori_extract.csv and uses it
```

Or place them by hand:

1. Open https://www.zillow.com/research/data/ and scroll to **Rentals**.
2. In the **Data Type** menu choose **ZORI (Smoothed): All Homes Plus Multifamily Time Series ($)**.
3. In the **Geography** menu choose **County** and click **Download**. Repeat for **ZIP Code** and **Metro & U.S.**
4. Put the three files, with their names unchanged, in `data/raw/rent/zillow/`:
   * `data/raw/rent/zillow/County_zori_uc_sfrcondomfr_sm_month.csv`
   * `data/raw/rent/zillow/Zip_zori_uc_sfrcondomfr_sm_month.csv`
   * `data/raw/rent/zillow/Metro_zori_uc_sfrcondomfr_sm_month.csv`

   A browser suffix such as ` (1)` is fine. Optionally add `<file>.json` with `{"fetched_date": "YYYY-MM-DD"}`; without it the file's modification date is used once and recorded in the extract.
5. For ZIP matching, also download the Census Gazetteer ZCTA file (for example `2024_Gaz_zcta_national.zip` from https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html), unzip it and put `2024_Gaz_zcta_national.txt` in the same folder. Without it ZIP matching is skipped and resorts match by county, then metro.
6. Run `python pipeline/build.py`, then commit `data/raw/rent/zillow/zori_extract.csv` (the full files are ignored by git).

### What the build does with them

* Each region's displayed rent is its latest month with a value; `as_of` is that month end. The trailing 12 month mean is kept in the extract (null unless all 12 months have a value). `rent.zori.value: trailing_mean` would use it instead.
* The extract keeps every county with a Tier A resort (by FIPS from `StateCodeFIPS` and `MunicipalCodeFIPS`, else by name and state), the metros of those counties (the county row's `Metro`, else the ZIP rows' `CountyName` and `Metro`), and ZIPs whose centroid lies within 10 km of a Tier A base.
* Matching: the nearest ZIP within `rent.zori.zip_radius_km` (10 km), else the county, else its metro, else none. The level used is shown.
* Source `zillow-zori`: "Zillow Observed Rent Index (ZORI), Zillow Research", https://www.zillow.com/research/data/, confidence `published`, note "ZORI is a typical market rent for all homes and multifamily, smoothed". A value older than 18 months shows with Needs update.
* A level whose full file is absent keeps its rows from the committed extract.

Extract columns: `level, region_id, region_name, state, county_fips (metro rows: the resort counties it serves), county_name, city, metro, lat, lon (ZIP centroid), latest_month, latest_value, trailing_mean, trailing_months, source_file, fetched_date`.

## 3. HUD Fair Market Rents and Census ACS (cache)

HUD FMR and ACS median gross rent (table B25064) for the county of each Tier A base area. `pipeline/rent.py` finds the county by point in polygon against `data/raw/geo/us-counties.geojson` (from `npm run build:counties`) and the build reads only this cache.

```bash
python pipeline/build.py                       # resorts.json must exist
export HUD_API_TOKEN=...                       # free token: https://www.huduser.gov/hudapi/public/register
python -m pipeline.rent fetch                  # hosts: www.huduser.gov, api.census.gov
python pipeline/build.py
```

Without `HUD_API_TOKEN` only ACS is fetched. ACS works without a key at low volume. Vintages come from `config/definitions.yaml` `rent` (`hud_fmr_year: null` means the latest HUD year, `acs_year`).

| Path | Content |
|---|---|
| `hud_fmr/<county fips>.json` | `{fetched_date, url, response}` from `GET https://www.huduser.gov/hudapi/public/fmr/data/<fips>99999`; `response.data.basicdata` gives `Efficiency`, `One-Bedroom`, `Two-Bedroom` (a list with one row per ZIP in Small Area FMR areas, plus an `MSA level` row) |
| `hud_fmr_<year>.csv` | optional HUD county FMR download with columns `fips`, `countyname`, `fmr_0`, `fmr_1`, `fmr_2`; used when no API cache exists for a county |
| `hud_fmr_<year>.csv.json` | optional `{"fetched_date": "YYYY-MM-DD"}` for the CSV; without it the values are dated October 1 of the year before the fiscal year (when FY FMRs take effect) |
| `acs/<year>_county_<fips>.json` | `{fetched_date, url, response}` from `GET https://api.census.gov/data/<year>/acs/acs5?get=NAME,B25064_001E&for=county:<ccc>&in=state:<ss>` |
| `acs/<year>_zcta_<zip>.json` | same for `for=zip%20code%20tabulation%20area:<zip>` (parsed; a ZIP lookup needs ZCTA outlines, not yet wired) |

Census null sentinels such as `-666666666` become `null`. Sources are emitted as `hud-fmr-fy<year>` and `census-acs5-<year>-b25064`. A housing site with no published rent shows the baseline as an `estimated` range, in the order of `rent.baseline_preference` (default HUD FMR studio, then ACS median).
