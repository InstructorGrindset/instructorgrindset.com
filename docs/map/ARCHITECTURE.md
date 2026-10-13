# Career Map architecture and data contract

This is the contract between the Python pipeline (`/pipeline`) and the map frontend (`/map`). Change it here first, then in both the Pydantic schemas and the Zod schemas.

## Stack (Phase 0 decision)

The existing site is hand built HTML on GitHub Pages with no build step. The roadmap says to match the existing site, and introducing Astro would mean moving the live site to a GitHub Actions build. The map therefore ships as plain static files:

* `/map/index.html` uses the existing sidebar partial and `/assets/global.css`.
* Native ES modules in `/map/js/`, no bundler.
* Third party libraries are vendored from npm into `/map/vendor/` at pinned versions (MapLibre GL JS, Zod, Chart.js). `scripts/vendor.sh` refreshes them. Nothing loads from a CDN.
* Basemap: state outlines from `us-atlas` compiled to `/data/build/us-states.geojson`. A Protomaps PMTiles basemap plugs in later through `basemap.pmtiles_url` in `/map/js/config.js` once tiles are hosted on R2.
* Deep links (`/map/co/i70-corridor/vail/`) are served by static route stubs that `pipeline/routes.py` generates from the build output. Each stub is a copy of the map shell, so every URL works on GitHub Pages without rewrites.

Astro remains an option for Phase 8 if per resort static pages outgrow the stub approach.

## Identifiers

* `state.code`: two letter USPS code, upper case (`CO`). URL segment is lower case (`co`).
* `cluster.id`: kebab case slug, unique (`i70-corridor`, `lake-tahoe`, `wasatch`).
* `resort.id`: kebab case slug, unique (`vail`, `palisades-tahoe`, `jackson-hole`).
* `source.id`: kebab case slug, unique (`vail-10k-fy2026`, `wikipedia-vail-ski-resort`).
* Resort URL: `/map/{state lower}/{cluster id}/{resort id}/`. Cluster URL: `/map/{state lower}/{cluster id}/`. State URL: `/map/{state lower}/`. A cluster spanning states uses the state of the URL the user came from; the canonical cluster URL uses the first state in `cluster.states`. A resort in a multi state cluster gets a route stub under every state in that cluster (`/map/ca/lake-tahoe/heavenly/` and `/map/nv/lake-tahoe/heavenly/`); its canonical URL uses the resort's own `state`.

## The Sourced wrapper

Every displayed number or fact is a `Sourced` object, or `null` when unknown:

```json
{ "value": 5317, "source_id": "wikipedia-vail-ski-resort", "as_of": "2026-10-11", "confidence": "published" }
```

* `confidence`: `published` | `reported` | `estimated`
* `as_of`: ISO date the value was verified or computed
* `note` (optional): short qualifier, e.g. `"skiable acres per resort site"`

## Build outputs (`/data/build`)

All files carry a top level `{ "version": <int>, "generated_at": <ISO datetime>, ... }`.

### `resorts.json`
```
{ version, generated_at, resorts: Resort[] }
Resort {
  id, name, tier: "A" | "B",
  facility_type: "ski_area" | "indoor" | "training_center",   // Tier B is always ski_area; indoor and training records may have no size data
  tier_a_criteria: [{ criterion: diehard|luxury|athlete_development|heritage|operator_compensation|year_round_training|owner_selection,
                      rationale, source_id }],
  tier_a_status: "draft" | "approved" | null,      // null for Tier B
  approval: { by: string, date: "YYYY-MM-DD", note: string | null } | null,   // set only when tier_a_status is approved
  owner: Sourced<string> | null,                   // "Vail Resorts" | "Alterra Mountain Company" | operator name
  lat, lon,                                        // base area, decimal degrees
  location_source_id,
  cluster_id, state,
  size: { acres, vertical_ft, lifts, ski_school_staff_est },          // each Sourced<number> | null
  elevation: { base_ft, summit_ft },                                  // each Sourced<number> | null; Tier A research first, else OpenSkiMap (estimated)
  snow: { annual_avg_in: Sourced<number> | null } | null,   // resort reported average annual snowfall (inches), e.g. Wikipedia infobox; null when absent
  operator_scale: Sourced<1 | 2 | 3> | null,       // owner decision 5: public "Professionalism N of 3"; note is always null here (internal)
  access: { rating: convenient | transit_friendly | mixed | car_centric, summary_md, source_id, as_of, confidence, note } | null,
                                                   // owner decision 6; public labels Convenient, Transit friendly, Mixed, Car centric; summary_md is one public sentence
  season: { open_typical, close_typical, length_days, training_start }, // each Sourced | null; dates as "MM-DD"
  terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags },  // pcts Sourced<number 0 to 100> | null; tags string[]
  fit: { enthusiast_types: string[], professional_types: string[] },
  clients: { profile_tags: string[], notes_md: string | null },
  career: Career | null,   // Phase 4, see "Phase 4 career" below; null when a resort has no pay records and no career inputs
  editorial: Editorial | null,   // Phase 6; only reviewed entries, see "Phase 6 editorial"; null otherwise
  climate_available: boolean,                      // true when climate/<id>.json exists
  detail_available: boolean,                       // true when detail/<id>.json exists (Phase 5)
  trailmap_available: boolean,                     // true when trailmap/<id>.json exists (Trail map)
  official_trailmap: { url, format: pdf|jpg|png|page, season: "2025/26" | null, source_id, as_of, confidence, note } | null,
                                                   // link to the resort's own official trail map (Official trail maps)
  webcams: { cams_page: { url, source_id, as_of, confidence, note } | null,
             cams: [{ label, url, kind: page|image|stream, source_id, as_of, confidence, note }] } | null,
                                                   // live cams links, at most webcams.max_named_cams cams (Webcams and jobs)
  jobs: { links: [JobLink], feed: boolean,          // jobs links by role group; employers in jobs/employers.json
          hiring_window: { text, source_id, as_of, confidence, note } | null } | null,   // null without links and window
  housing_summary: { site_count, cheapest_rent_monthly_low, cheapest_site_id, has_transit } | null,   // Phase 5
  sources: string[]                                // every source_id referenced by this resort
}
```

### `clusters.geojson`
GeoJSON FeatureCollection, one Polygon or MultiPolygon per cluster. `properties`:
```
{ id, name, states: string[], resort_ids: string[], tier_a_count, overview_md: string | null, alexandre_take_md: string | null, sources: string[] }
```
Polygons are convex hulls of member resorts buffered by about 15 km, or hand drawn polygons from `config/clusters.yaml` when supplied.

### `resorts.geojson`
Point FeatureCollection mirroring `resorts.json` for MapLibre. `properties`: `{ id, name, tier, facility_type, cluster_id, state, acres, vertical_ft, lifts, size_rank, season_length_days, snowfall_annual_in, snowfall_annual_source, professionalism_score, access_rating, lucrativeness_mid }` where `size_rank` is 1 to 5 from acres (null acres gives 1). The last six are for map recolor modes (numbers or null, plus the snowfall source and the access rating): `season_length_days` is `season.length_days.value`, `snowfall_annual_in` is the resort's published `snow.annual_avg_in` when present, else the sum of the climate file's monthly `snowfall_in` over months whose `snowfall_source` is not null (null when neither exists), and `snowfall_annual_source` says which (`published` | `climate` | null), `professionalism_score` is `operator_scale.value` (1 to 3, owner decision 5), `access_rating` is `access.rating`, `lucrativeness_mid` is `career.lucrativeness.mid`.

### `states.json`
```
{ version, generated_at, states: [{ code, name, cluster_ids: string[], resort_count }] }
```
Only states with at least one resort.

### `sources.json`
```
{ version, generated_at, sources: [{ id, url, title, accessed_date, note }] }   // note: string | null (optional qualifier)
```
Includes every source declared in `/data/raw` plus the climate ETL's `data/build/climate/sources.json`, merged by id,
and the NOAA normals source record (`data/raw/climate/noaa_normals/source.json`, id `noaa-ncei-normals-1991-2020`).

### `definitions.json`
Display relevant subset of `config/definitions.yaml`, so the UI never hardcodes thresholds:
```
{ version, generated_at,
  snow: { powder_day_in, banner_day_in },            // new snow in 24 hours
  density: { dry_max_pct, moist_min_pct },           // dry < dry_max_pct, average between, moist > moist_min_pct
  display: { priority: ["published", "reported", "estimated"] },
  reports: { aggregation_min },                      // approved reports needed before a Reported range shows
  submissions: { endpoint: string | null },          // report form target; the form is hidden while null
  staleness: { max_age_months },                     // records older than this are flagged
  heat: { ramp: { name, colors: hex[] },             // the one heat map ramp (see "Heat maps")
          regions: { lower48: Box, alaska: Box },    // Box { west, south, east, north }
          field: { neighbors, min_distance_km, decluster_km, full_opacity_km, fade_end_km, max_alpha, floor_alpha,
                   px_per_degree, grid_step_px, max_pixels },
          modes: { lucrativeness, professionalism, season, snowfall: { scale: linear|sqrt, domain?, domain_min?, ticks? } } } }
```
`heat` is `config/definitions.yaml` `heat` without its `snowfall` block (pipeline only). The UI never hardcodes the ramp
or the field settings; without a valid `heat` block the heat map modes are disabled ("Heat map settings did not load.").

### `us-states.geojson`
State outlines (simplified) with `properties: { code, name }`.

### `heat/snowfall.json` and `heat/snowfall-<region>.png` (snowfall heat map rasters, `pipeline/heat.py`)
```
{ version, generated_at, mode: "snowfall", units: "in", scale: "sqrt", domain: { min, max }, ticks: number[],
  ramp: hex[],                                   // heat.ramp.colors used to color the PNGs
  background: { source_id: "noaa-ncei-normals-1991-2020", as_of, confidence: "published", field: "ANN-SNOW-NORMAL",
                stations },                      // usable NOAA stations inside the regions
  pockets: { field: "snow.annual_avg_in", resorts, radius_km },
  method: { interpolation, neighbors, min_distance_km, grid_step_px, px_per_degree, full_opacity_km, fade_end_km, floor_alpha },
  attribution: "Snowfall normals: NOAA NCEI 1991 to 2020",
  inputs_sha256,                                 // stations, pockets, outlines, heat config and the code; unchanged means no rebuild
  rasters: [{ region: "lower48" | "alaska", image: "snowfall-lower48.png", width, height,
              coordinates: [[w, n], [e, n], [e, s], [w, s]],   // exact corners, the region box from config
              bytes, stations, pockets }] }
```
* PNG: 8 bit RGBA written with the stdlib (`pipeline/png.py`, zlib, Up filter). Columns evenly spaced in longitude,
  rows evenly spaced in Web Mercator y (MapLibre image sources expect that), pixel centers at half steps inside the
  corners. Colored with `heat.ramp` on the snowfall scale; alpha is the opacity rule of `heat.field` times the share of
  the pixel inside the region's state outlines (antialiased scanline coverage, 4 samples per row). About 250 KB for the
  lower 48 (1164 by 639 px, 20 px per degree) and 100 KB for Alaska (1010 by 890 px); each file must stay under 400 KB.
* Domain: `domain_min` (0) to the nice ceiling (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8 or 10 times a power of ten) of the
  highest station or resort value (700 in, Brighton). Ticks: the configured candidates clear of the top end, then the
  maximum.
* A region without outlines is skipped. Without the NOAA extract or `us-states.geojson` the build warns and leaves the
  rasters alone; the frontend falls back to the resort only surface when `heat/snowfall.json` is absent or invalid.

### `map-index.json` (Phase 8, written by `pipeline/routes.py`)
Slim national index the map loads first. Compact JSON, one row per resort as an array in `fields` order:
```
{ version, generated_at,                 // copied from resorts.json
  fields: ["id", "name", "tier", "tier_a_status", "facility_type", "state", "cluster_id", "lat", "lon",
           "acres", "size_rank", "climate_available", "detail_available", "webcams_available", "jobs_available",
           "season_length_days", "snowfall_annual_in", "professionalism_score", "lucrativeness_mid"],
  resorts: [[...], ...] }
```
Every value is copied from `resorts.json` (`acres` is `size.acres.value`; `webcams_available` is `webcams != null`,
`jobs_available` is `jobs.links` not empty) and `resorts.geojson` (`size_rank` and the four
recolor values); without `resorts.geojson`, `size_rank` uses `display.size_rank_acres_breaks` and the recolor values
come from `resorts.json` as in the `resorts.geojson` rules, with `snowfall_annual_in` null. Resorts with an unsafe id or
no coordinates are left out. About 16 KB gzipped against 87 KB for `resorts.json` plus 21 KB for `resorts.geojson`.
The frontend builds its resort points from this file and falls back to `resorts.json` and `resorts.geojson` when it
is absent.

### `sitemap.xml` (written by `pipeline/routes.py`)
Only the `/map/` entries are rewritten; every other entry stays byte for byte. The map entries are the canonical URLs of
`/map/`, every state page, every cluster page and Tier A resort pages. Tier B resort pages are generated, linked and
crawlable (no `noindex`) but left out of the sitemap. Hand written pages in `routes.STATIC_PAGES` (`/map/credits/`) join
the map entries when their `index.html` exists; `routes.py` never writes or removes them (`credits` is protected).

### Resort route stubs embed their record
Each resort page under `/map/` carries `<script type="application/json" id="resort-record">` with
`{ version, generated_at, resort: <its resorts.json record>, sources: [<the sources listed in resort.sources>] }`
(every `<` escaped as `<`; an editorial block that is not `reviewed` is dropped). The panel validates it with the
same Zod schema as `resorts.json` and renders the resort without waiting for the full file.

### `climate/{resort_id}.json` (Phase 3, lazy loaded per resort)
```
{ version, generated_at, resort_id,
  source_ids: string[], confidence: "estimated",
  snowfall_null_reason: string | null,          // why snowfall values are null; required when every month is null
  period: { start_year, end_year },             // Oct 1 of start_year to Sep 30 of end_year
  months: ClimateMonth[12] }   // ordered by ski season: Oct, Nov, Dec, Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep
ClimateMonth { resort_id, month (1..12), snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               snowfall_source: "snotel_swe" | "era5" | null,   // covers the four snowfall fields
               density_pct_median, density_mix: { dry, average, moist },   // fractions summing to 1, or null
               density_source: "snotel" | "estimated" | null,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
```
Monthly values are climatological means over `period`; `powder_days` and `banner_days` are mean days per month. Climate values are modeled (ERA5 reanalysis and SNOTEL derived), so every file carries `confidence: "estimated"`; the UI uses `as_of` equal to the file's `generated_at` and sources from `source_ids`.

Snowfall sources (`snowfall_source`), in order of preference:
* `snotel_swe`: daily new snow at the chosen NRCS SNOTEL station, from daily SWE gains divided by new snow density, never below the daily depth gain. Values describe the station elevation.
* `era5`: Open Meteo ERA5 daily snowfall, used only without a usable station and only for low relief resorts or where the ERA5 grid cell height is close to the resort base (thresholds in `config/definitions.yaml` `climate`).
* `null`: no source fits, or the derived annual total is more than `climate.validation_suppress_pct` from the published `Resort.snow.annual_avg_in`. When `snowfall_in` is null, `powder_days`, `banner_days` and `avg_storm_depth_in` are null too. Temperatures, wind, sun and density stay.

Open Meteo access: by default the ETL calls the free endpoint `https://archive-api.open-meteo.com/v1/archive` and keeps under the free tier limits. When the environment variable `OPEN_METEO_API_KEY` is set, it calls the customer endpoint `https://customer-archive-api.open-meteo.com/v1/archive` with the key in the `apikey` query parameter, as Open Meteo's docs describe. The daily budget guard then uses `climate.open_meteo.paid_daily_budget` from `config/definitions.yaml`; null keeps the free tier limits even with a key. The key lives only in the environment. It is never written to config, cache, build output or logs, and error messages redact it.

`climate/provenance.json` (`{ version, generated_at, resorts: { id: {...} } }`) records the ERA5 grid cell, the SNOTEL station and how it was chosen, QC counts and the validation result per resort. `docs/map/climate-validation.md` is generated from it.

## Phase 4 career

### Raw inputs (research fills these later; every value sourced)
* `data/raw/pay/*.yaml`: `{ batch, researched_date, sources: [...], records: [PayRecord] }` (see `data/raw/pay/README.md`)
  ```
  PayRecord { resort_id, season ("2025-26"), cert_level: none|level_1|level_2|level_3|trainer|examiner|unspecified,
              hourly_low, hourly_high, confidence: published|reported|estimated, source_id, verified_date, note?,
              aggregate_count? }   // reported rows only: number of approved reports aggregated (Phase 7)
  ```
* `data/raw/career/*.yaml`: `{ batch, researched_date, sources: [...], resorts: [CareerInputs] }` (see `data/raw/career/README.md`)
  ```
  CareerInputs { resort_id,
    private_rate: Sourced<{ low, high, unit: hour|half_day|full_day }> | null,   // guest price
    private_split: Sourced<number 0..100> | null,                               // instructor share, percent
    professionalism_inputs: { cert_pay_differential, paid_training_hours, examiner_trainer_presence,
                              private_lesson_share, year_round_staff, clinic_access },  // each Sourced<number 0..1 normalized> | null
    client_profile_tags: string[], enthusiast_types: string[], professional_types: string[] }  // from config vocab
  ```
  Tag vocab lives in `config/definitions.yaml` `career.vocab` (client profiles from ROADMAP Section 1:
  `destination_families`, `local_kids_programs`, `adaptive`, `race`, `freestyle`, `international`, `uhnw_privates`;
  enthusiast and professional vocab lists stay empty until Alexandre fills them; unknown tags fail). Career tags are
  merged into `resort.clients.profile_tags` and `resort.fit`.

### Build output: `Resort.career`
```
career: {
  pay: [ { cert_level, hourly_low, hourly_high, confidence, source_id, as_of, season, stale } ],
        // one row per cert_level (ordered none, level_1, level_2, level_3, trainer, examiner, unspecified): the most recent
        // season, then highest confidence (published > reported > estimated), then latest verified_date, then source_id.
        // `reported` rows only come from aggregates of reports.aggregation_min (3)+ approved reports and are never single reports
  private_rate: Sourced | null, private_split: Sourced | null,
  professionalism: { score: number|null (0..100), status: computed|weights_unset|insufficient_data,
                     breakdown: [{ component, weight, value, points, source_id }] },   // all six components, always
  lucrativeness: { low, mid, high (USD season savings) | null, status: computed|assumptions_unset|insufficient_data,
                   assumptions: [{ key, value, label }] },
  stale_fields: string[]   // "pay.<cert_level>", "private_rate", "private_split", "professionalism.<component>"
}
```
* **Staleness:** a value is stale when its `as_of` / `verified_date` plus `staleness.max_age_months` (18) calendar months is
  before the build date. A value exactly 18 months old is still current.
* **Professionalism (owner decision 5, 2026-10-13):** the public Professionalism value is `resort.operator_scale.value`
  (1, 2 or 3), shown only as "1 of 3", "2 of 3" or "3 of 3", with no breakdown, definitions or tooltip text.
  `resorts.geojson` `professionalism_score` is that value (heat map range 1 to 3). The six component model below stays in
  code but is unused: `career.professionalism` is still emitted with `status: weights_unset`; do not display it.
  (Old model: score = 100 × Σ(weight × value) / Σ(weight) over components with a value.)
* **Lucrativeness (owner decision 4, 2026-10-13):** season savings before tax, USD.
  season months = `season.length_days` / `days_per_month` (30.4) when sourced, else `default_season_months` (5).
  earnings = hourly × `hours_per_week` (40, standard full time) × season months × `weeks_per_month` (4.33).
  savings = earnings + tips − average rent × season months. No food, transport, pass or gear offsets.
  `mid` and `high` are the savings at the top of the posted wage scale (highest `hourly_high` of the displayed pay rows,
  any cert level); `low` uses the bottom (lowest `hourly_low`). Tips only from a sourced or 3+ report figure, else 0 with
  the stated assumption "Tips are not included yet". Average rent follows `rent.lucrativeness_rent`: the mean of the
  resort's employee housing rent mids (display priority applies), else the county ACS median gross rent, else HUD FMR
  one bedroom. Status `insufficient_data` when there is no pay row or no rent; `assumptions_unset` only if hours or the
  default season months are null in config. `assumptions` (each `{key, value, label}`, labels ready to show):
  `wage_top_usd`, `wage_bottom_usd`, `hours_per_week`, `season_months` (label says sourced length or standard season),
  `season_months_basis` (`season_length` | `default`), `weeks_per_month`, `tips_season_usd` (label "Tips are not included
  yet" when null), `rent_monthly_usd`, `rent_basis` (`employee_housing_mean:<site ids>` | `acs_median` | `fmr_one_br` |
  ...), `rent_source_id` (several joined with ", "), `tax` (label "Savings are before tax").
* **Shared calculator model (Phase 9):** lucrativeness is computed by `pipeline/season_income.py`, the Python mirror of
  `assets/js/season-income.mjs` used by the Season Income Calculator, with zero offsets (`lucrativeness_inputs` with
  season length = months × 4.33 × 7 days): earnings = its `gross`, savings = its `net_before_tax`, so the map and the
  calculator agree for the same inputs. Whole dollars
  use `round_half_up` (JS `Math.round`, ties toward positive infinity). The calculator's headline net is after tax;
  the map's lucrativeness is savings before tax (ROADMAP Section 6) and applies no tax assumption, which stays the
  owner's call. The calculator has state income tax rates for only 13 states (WA, WY, NV, AK, NH, CO, UT, ID, VT, MT,
  NY, OR, CA).

## Phase 5 housing and commute

Raw inputs (every value sourced; research and ETL fill them later):
* data/raw/housing/*.yaml: `{batch, researched_date, sources: [...], sites: [HousingSite]}`
  HousingSite { id, resort_id, name, type: resort_owned|master_lease|market, lat, lon, location_source_id,
                precision: building|neighborhood,   # building only when the address is publicly listed
                rent_monthly: Sourced<{low, high}> | null, occupancy: shared|private|mixed|null, deposit: Sourced<number>|null,
                eligibility_note: string|null, confidence, verified_date }
* data/raw/rent/: HUD Fair Market Rents and Census ACS median gross rent, cached API responses or CSV
  (county and ZIP level), loaded by pipeline/rent.py into a baseline per resort (county of the base area,
  plus ZIPs of housing sites). HUD API needs a token in env HUD_API_TOKEN; ACS works without a key at low volume.
* data/raw/transit/<agency>/gtfs.zip (gitignored) -> pipeline/transit.py extracts routes and stops within
  `transit.search_radius_km` of any housing site or the base area; committed extract data/raw/transit/<agency>.json.
* data/raw/routing/ cache (gitignored) of routing responses. pipeline/routing.py: provider in config
  (`routing.provider: valhalla|osrm|ors`, base URL, API key env var), drive time/distance and route geometry
  housing -> base, and drive isochrones at `routing.isochrone_minutes` [15, 30, 45] around the base area.
  Transit commute times come from GTFS schedules (walk to stop + ride, simplest sound model, documented) or are
  null; never estimated from memory.
* Lifts: OpenSkiMap https://tiles.openskimap.org/geojson/lifts.geojson (reachable now); keep lifts whose
  skiAreas reference the OpenSkiMap area(s) matched to each Tier A resort; committed extract
  data/raw/openskimap/us_lifts_tier_a.geojson (full download gitignored).

Build outputs:
* data/build/detail/<resort_id>.json, lazy loaded by the resort detail map (Tier A only):
```
{ version, generated_at, resort_id,
  base_area: { lat, lon, source_id },
  lifts: FeatureCollection<LineString> props { name, lift_type, source_id: "openskimap-lifts" },
  housing: [ { ...HousingSite fields, rent_monthly (Sourced range, display priority applied, HUD/ACS baseline
               used only when no site rent exists and then confidence "estimated" with the baseline source),
               commutes: [ { mode: drive|bus|walk|shuttle, minutes, distance_mi, transit_route_ids, geometry
               (LineString), source_id } ] } ],
  rent_baseline: { county_fips, county_name, fmr: { studio, one_br, two_br } each Sourced, acs_median_gross_rent:
                   Sourced } | null,
  transit: { routes: [ { id, agency, short_name, long_name, color, geometry (MultiLineString) } ],
             stops: [ { id, name, lat, lon, route_ids } ], source_ids } | null,
  isochrones: [ { minutes, mode: "drive", geometry (Polygon|MultiPolygon), source_id } ] }
```
  Written for every Tier A resort that has at least lifts or housing; empty lists or null where data is missing.
* resorts.json Resort gains `detail_available: bool` and
  `housing_summary: { site_count, cheapest_rent_monthly_low (number|null), cheapest_site_id, has_transit } | null`.
* The Phase 4 lucrativeness rent input uses the cheapest employee housing site (resort_owned or master_lease) rent,
  else the baseline (studio FMR), per config; career output echoes which was used.
* config/definitions.yaml gains: `routing: {provider, base_url, api_key_env, isochrone_minutes: [15, 30, 45]}`,
  `transit: {search_radius_km}`, `rent: {baseline_preference: [fmr_studio, acs_median]}` with sensible defaults
  for provider settings and nulls for anything that is a judgment call for Alexandre.

### Pipeline notes (additions to the contract)
* **HousingSite** also takes `address: Sourced<string> | null`; `precision: building` requires it (the publicly listed address and its source). Site ids are unique across all housing files; `resort_id` must exist in the build.
* **County lookup:** point in polygon of the base area against `data/raw/geo/us-counties.geojson` (us-atlas counties-10m via `npm run build:counties`, committed). ZIP level baselines need ZCTA outlines and are not wired yet.
* **Rent sources:** `hud-fmr-fy<year>` and `census-acs5-<year>-b25064`. A site without published rent shows the first value in `rent.baseline_preference` as `{low: v, high: v}`, confidence `estimated`, with a note naming the baseline.
* **Lucrativeness rent:** see Phase 4 (owner decision 4): `rent.lucrativeness_rent` steps `employee_housing` (mean of employee housing rent mids), `acs_median`, `fmr_one_br`; also available: `reported_area`, `baseline`, `fmr_studio`, `fmr_two_br`. Commutes: no walking speed or distance model is used (owner decision 6); each Tier A resort carries the researched `access` rating instead.
* **Transit extract** (`data/raw/transit/<agency>.json`): `{ version, agency, source, search_radius_km, routes, stops, patterns }`; ids are prefixed `<agency>:`; `patterns: [{ route_id, stop_ids, offsets_min, trip_count }]` hold median scheduled minutes from the first kept stop. Bus commute = walk to a stop within `transit.max_walk_km` + scheduled ride + walk from a stop within `max_walk_km` of the base, at `transit.walk_speed_kmh`, no waiting time; null until Alexandre sets both.
* **Routing:** source id `routing-<provider>`; the build reads only the cache `data/raw/routing/<provider>/<sha1>.json` written by `python -m pipeline.routing run`. Drive commute `source_id` is `routing-<provider>`; bus commute `source_id` is `gtfs-<agency>`.
* **Lifts:** extract features carry `{ id, name, ref, lift_type, ski_area_ids }`; only `status: operating` lifts. Detail lifts carry exactly `{ name, lift_type, source_id }`. `name` may be null in OpenStreetMap.
* **Detail files** are written compact; files for resorts that no longer qualify are removed. `housing_summary.cheapest_rent_monthly_low` uses published or reported site rents (never the baseline); `has_transit` is true when the detail file has transit.

## Official trail maps (links only)

Official trail maps are copyrighted art owned by the resorts or their artists (James Niehues and others). The site
links to them in a new tab and never downloads, vendors, rehosts, hotlinks or embeds them.
* Raw input `data/raw/trailmaps_official/<batch>.yaml` (see its README): `{ batch, researched_date, sources: [Source],
  trailmaps: [{ resort_id, url, format: pdf|jpg|png|page, season ("2025/26" or null), source_id, as_of, confidence,
  note? }] }`, validated by `pipeline/schemas/trailmaps_official.py` and loaded by `pipeline/trailmaps_official.py`.
* Rules: https only; the url's domain (last two labels) equals its source's, which must be the resort's or its
  operator's own site; pdf, jpg and png need that file extension and page must not be a file; season only when the
  source names it; one record per resort. URLs found by web search follow the search result rule (source note "From a
  search result for this page; page not opened", confidence `estimated`).
* Build output: `Resort.official_trailmap` (the record without `resort_id`); its source joins `resort.sources`.
  Route stubs prerender the link. The panel shows "Official trail map from <resort name>" with the format and season;
  the trail map dialog shows the same link next to "Interactive map from OpenSkiMap".
* 2026-10-13 batch: 96 Tier A resorts from 19 searches (multi domain queries; the Vail Resorts pattern
  `/the-mountain/about-the-mountain/trail-map(s).aspx` was guessed once and every URL was seen in a result). Still
  without a link: Alpine Valley (OH), Alta (winter), Black Mountain (ME), Burke, Hermitage Club, Hyland Hills, Mad River
  Glen, Montana Snowbowl, Saskadena Six, Silverton, Yellowstone Club, and the training and indoor facilities.

## Webcams and jobs (links only)

Owner requests 2026-10-13: link every resort's live cams, and its jobs: ski school, coaching and club (race,
freeride, freestyle and other disciplines), management, parent company and job boards, with live open role counts
where a public board exists. Cams, streams and job pages are linked in a new tab (`target="_blank"`,
`rel="noopener noreferrer"`) and never embedded, proxied or rehosted.

### Raw inputs (research batches; formats are fixed)
* `data/raw/webcams/<batch>.yaml`: `{ batch, researched_date, sources: [Source], records: [{ resort_id,
  cams_page: { url, source_id, as_of, confidence, note? } | null, cams?: [{ label, url, kind: page|image|stream,
  source_id, as_of?, confidence?, note? }] }] }`, validated by `pipeline/schemas/webcams.py`, loaded by
  `pipeline/webcams.py`. A record needs a `cams_page` or at least one cam; a cam without `as_of` and `confidence`
  takes the cams page's (and fails when there is no cams page).
* `data/raw/jobs/<batch>.yaml`: `{ batch, researched_date, sources, employers: [Employer], records: [{ resort_id,
  links: [JobLink] }] }`, validated by `pipeline/schemas/jobs.py`, loaded by `pipeline/jobs.py`.
  ```
  Employer { id, name, kind: parent_company|resort_operator|club|ski_school|other, careers_url,
             ats: { vendor: greenhouse|lever|smartrecruiters|workable|ashby|workday|icims|ultipro|paylocity|adp|paycom|
                            jazzhr|bamboohr|other|unknown, board?: token }, source_id, as_of, confidence, note? }
  JobLink { employer_id, role_group: ski_school|coaching|management|corporate|all,
            disciplines?: [race|freeride|freestyle|park|nordic|adaptive|snowboard|moguls], label, url,
            source_id, as_of, confidence, note? }
  ```
* Hiring windows (ROADMAP item 6): a jobs batch may also hold `hiring_windows: [{ resort_id, text, source_id, as_of,
  confidence, note? }]`, the source's own sentence on when applications open, job fairs or clinics start, copied word
  for word (max 400 characters, no em or en dashes). One per resort across all files. `data/raw/jobs/hiring_windows.yaml`
  holds the 27 from the pay and training research; their sources live in `data/raw/facts/training.yaml`,
  `data/raw/pay` and `data/raw/jobs`.
* Missing or empty folders are fine (no records). Rules, each a build error: an `http` (or any non https) url; an
  unknown resort, employer or source id; a bad enum value; a resort listed twice in one file; webcams for one resort
  in two files; a cam url or label listed twice, or a cam equal to the cams page; a jobs url listed twice in one role
  group of a resort (one board may serve several groups); an employer declared again with a different name, kind,
  careers url or ats. Jobs records for one resort in several files merge.
* Domains (registered domain, the last two labels): a webcam url (cams page or cam) must equal its own source's
  domain. A jobs link url must be the employer's careers domain, one of its ATS vendor's domains
  (`jobs.ats_domains`, for example myworkdayjobs.com for Workday) or its own source's domain (a page on the employer's
  site while its careers page sits on the ATS). Job aggregators in `jobs.blocked_domains` (Indeed, Glassdoor,
  ZipRecruiter, SimplyHired, Monster) always fail. An `ats.board` on a vendor without a public postings API (a
  Workday tenant) is kept and never fetched.

### Build outputs
* `Resort.webcams` (cams trimmed to `webcams.max_named_cams`, 4) and `Resort.jobs` (`links` ordered ski_school,
  coaching, management, corporate, all, file order within a group; `feed` true when a linked employer has a public
  board). Their sources join `resort.sources`, so stubs embed them; `map-index.json` gains `webcams_available` and
  `jobs_available`.
* `jobs/employers.json`: `{ version, generated_at, employers: [Employer + { feed, board_url }] }`, every employer once
  (parent companies are not repeated per resort). `board_url` is the public board page when `feed` is true:
  `https://boards.greenhouse.io/{board}`, `https://jobs.lever.co/{board}`, `https://careers.smartrecruiters.com/{board}`,
  `https://apply.workable.com/{board}/`, `https://jobs.ashbyhq.com/{board}`.
* `jobs/postings.json` (written by the feed; the build writes an empty one when it is missing and validates it
  otherwise, never overwriting it):
  ```
  { version, generated_at, fetched_at: ISO datetime | null, keywords: string[],
    boards: [{ employer_id, employer_name, vendor, board, url (board page), source_id, status: ok|error,
               fetched_at (last good fetch) | null, as_of | null, total, matched, error | null }],
    sources: [Source],                     // one per board: the board page, accessed_date = as_of
    postings: [{ id, employer_id, title, location | null, department | null, url, role_group (a guess),
                 resort_ids: string[],     // [] = a role at the employer with no single resort named
                 source_id, as_of, confidence: "published", fetched_at }] }
  ```

### Live postings feed (`pipeline/jobs_feed.py`, `.github/workflows/jobs-feed.yml`)
* `python -m pipeline.jobs_feed fetch` reads `jobs/employers.json` and `resorts.json` and, for employers with
  `feed` that some resort links, calls only these public keyless APIs: Greenhouse
  `boards-api.greenhouse.io/v1/boards/{board}/jobs`, Lever `api.lever.co/v0/postings/{board}?mode=json`,
  SmartRecruiters `api.smartrecruiters.com/v1/companies/{board}/postings` (pages of 100, at most 10), Workable
  `apply.workable.com/api/v1/widget/accounts/{board}`, Ashby `api.ashbyhq.com/posting-api/job-board/{board}` (listed
  jobs only). Workday, iCIMS, UKG, Paylocity, ADP, Indeed, Glassdoor and every other page are never fetched or scraped.
* Kept: a posting whose title, department or team holds a `jobs.feed.keywords` word or phrase (whole words, any case;
  instructor, coach, ski school, snowsports, ride school, supervisor, director, trainer, freeride, race, freestyle,
  adaptive and more), none of `exclude_keywords`, and, for employers in `require_keywords` (a college board such as
  Middlebury's), one of their words in the title, department or location. Posting urls must be https on the vendor's
  or the careers domain; at most `max_postings_per_board` (150) per board.
* Role group guess: the first of `jobs.feed.role_keywords` management, coaching, ski_school with a matching word;
  else corporate for a parent company, else all.
* Resorts: an employer linked from one resort gives every kept posting to it. For an employer linked from several
  resorts, a posting goes to the resorts whose name (also without a trailing "Ski Resort", "Ski Area" or "Resort") or
  `jobs.feed.location_aliases` entry appears in its location, else in its title or department (the employer's own
  name removed first); terms under four letters never match. None named gives `resort_ids: []`.
* Writes only when postings or board states change (timestamps ignored) or the last fetch is `refresh_days` (7) old,
  so the daily job commits only real changes. A failed board keeps its last postings and `fetched_at` for
  `max_age_days` (14), then drops them.
* `jobs-feed.yml`: daily cron and `workflow_dispatch`, `permissions: contents: write` (plus `pages: write` to request
  a Pages build after a push), default `GITHUB_TOKEN`; commits only `data/build/jobs/postings.json`. Scheduled runs
  start only once the workflow is on the default branch. Tests use recorded responses in
  `tests/pipeline/fixtures/jobs_feed/` (synthetic, in each vendor's documented shape); the sandbox cannot reach the APIs.

### Link checker (`pipeline/link_check.py`, `.github/workflows/link-check.yml`)
Monthly (cron on the 1st and `workflow_dispatch`): every `official_trailmap`, cams page, cam and jobs link url in
`resorts.json` plus every careers page in `jobs/employers.json`, once each. HEAD, then GET when HEAD is refused; one
retry for timeouts and server errors. Broken: HTTP 404 or 410, no DNS answer, an invalid TLS certificate. Could not
verify: other error statuses (often bot protection) and timeouts, listed in a folded section. The workflow
(`contents: read`, `issues: write`) opens or updates one issue labelled `link-check` while links are broken and
closes it when none are; it never commits.

### UI (`map/js/livelinks.js`; stubs in `pipeline/routes.py` `render_webcams`, `render_jobs`)
* Resort panel, under the trail map links: "Live cams", then "Open live cams" (the cams page) with its badge,
  site name, "opens in a new tab" and "Last verified", then up to four named cams with badge and kind (Web page,
  Still image, Live stream).
* "Jobs" section after Career: "Hiring window: <text>" with its badge, date and source when the resort has one, then
  "Links open each employer's own site in a new tab.", then groups in this order: Ski
  school (`ski_school`), Coaching and clubs (`coaching`, disciplines as small tags), Management (`management`),
  Parent company (`corporate`), Job boards (`all`). Each link has its badge and "Last verified <date>", plus its
  source when the source is another page. Tier A resorts without links say "Jobs links are being collected." (after
  a hiring window when there is one); Tier B resorts without links or window show no section.
* Open roles (panel only, never prerendered since they change daily): when `jobs.feed` is true the panel loads
  `jobs/postings.json` once (`loadPostings`, lazy, Zod `PostingsFile`) and, for postings of the resort's linked
  employers, shows "N open roles" (Published badge) for postings naming this resort with up to five titles linked out
  (ski school first), "N more roles on the job boards below.", "Plus N roles at <employer> with no single resort
  named." for employer wide postings, and "Updated <earliest board as_of> from <employer> job board." With an empty
  feed, no match or no file, the section shows the links only.
* Cluster panel: a "Hiring" section after the resort list, when postings exist for its resorts: each employer (link
  to its board) with a Published badge and "N open roles", then "Updated <date>.". The resort list adds "Live cams".
* Zod: `Webcams`, `Jobs`, `PostingsFile` in `schemas.js`; a bad item drops only that item, a bad block only the block.

## Trail map (OpenSkiMap runs)

### Raw input
* `https://tiles.openskimap.org/geojson/runs.geojson` (about 835 MB, one feature per line) downloads to
  `data/raw/openskimap/runs.full.geojson` (gitignored) with `python pipeline/build.py --fetch`. `pipeline/runs.py` streams
  it line by line, keeps runs whose `uses` include `downhill` or `snow_park`, whose `status` is not abandoned, disused,
  proposed, planned or construction, and whose `skiAreas[].properties.id` include an OpenSkiMap area matched to a Tier A
  resort (the same area ids the lifts use). Elevations are dropped, geometry is simplified with Douglas Peucker at
  `trailmap.simplify_tolerance_m` (6 m) and rounded to 5 decimals.
* Committed extract `data/raw/openskimap/us_runs_tier_a.geojson`, properties
  `{ id, name, difficulty, difficulty_convention, uses, grooming, lit, ski_area_ids }`. A newer committed extract is never
  overwritten by an older download.

### Build output: `data/build/trailmap/<resort_id>.json` (lazy loaded by the trail map popup)
```
{ version, generated_at, resort_id, source_id: "openskimap-runs",
  runs: FeatureCollection<LineString | MultiLineString | Polygon | MultiPolygon>,
        properties { name: string | null,
                     difficulty: novice | easy | intermediate | advanced | expert | freeride | extreme | null,
                     difficulty_us: green | blue | black | double_black | terrain_park | null,
                     grooming?: string, lit?: boolean },        // omitted when null (treat missing as null)
  lifts: FeatureCollection<LineString> props { name, lift_type, source_id: "openskimap-lifts" },   // same as detail files
  bounds: [west, south, east, north] }                           // over runs and lifts
```
* Written for every Tier A resort with at least one run; `resorts.json` Resort gains `trailmap_available: boolean`.
* Polygon runs are area runs (open bowls, glades, beginner areas); draw them as filled outlines.
* `difficulty` is OpenSkiMap's value as given. `difficulty_us` maps it to North American trail signs for the
  `north_america` difficulty convention, following the OSM `piste:difficulty` key that OpenSkiMap passes through
  (https://wiki.openstreetmap.org/wiki/Key:piste:difficulty, https://github.com/russellporter/openskidata-format):

  | OpenSkiMap `difficulty` | `difficulty_us` |
  |---|---|
  | novice, easy | green |
  | intermediate | blue |
  | advanced | black |
  | expert, extreme | double_black |
  | freeride | null (off piste, no sign colour) |
  | null | null |
  | any run whose `uses` include `snow_park` | terrain_park |

  Runs under another convention (europe, japan) get `difficulty_us: null`.
* Source `openskimap-runs`: OpenSkiMap runs, data © OpenStreetMap contributors (ODbL). The popup shows that attribution.

## Resort facts overlay

`data/raw/facts/<batch>.yaml` (format in `data/raw/facts/README.md`): `{ batch, researched_date, sources, records }`,
one record per resort (Tier A or Tier B) across all files, with optional Sourced `open_typical`, `close_typical`,
`training_start` ("MM-DD"), `length_days` (1 to 366), `annual_snowfall_in`, `acres`, `vertical_ft`, `lifts` (positive).
The build merges each field into `Resort.season`, `Resort.snow.annual_avg_in` and `Resort.size`: the higher confidence
wins (published > reported > estimated); on a tie the value already on the resort (Tier A YAML, or OpenSkiMap for lifts
and vertical) wins. `season.length_days` then drives `resorts.geojson` `season_length_days` and the lucrativeness season
months; `snow.annual_avg_in` drives `snowfall_annual_in` with `snowfall_annual_source: published` (the published path,
whatever the value's confidence).

## Phase 6 editorial

### Raw input: `data/raw/editorial/<batch>.yaml`
```
{ batch, researched_date, sources: [Source], entries: [EditorialEntry] }
EditorialEntry { target: "cluster" | "resort", id,            // an existing cluster id or resort id
                 overview_md, culture_md (resort only, else null), alexandre_take_md (written by Alexandre only),
                 status: "draft" | "reviewed", source_ids: string[] (1 or more, all defined),
                 reviewed_by?: string, reviewed_date?: ISO date }   // set by the approve command
```
* One entry per (target, id) across all files. A `reviewed_by` or `reviewed_date` on a draft is an error.
* Style lint on every text field (Alexandre's rules): an em dash or en dash fails the build; a hyphen inside a word
  ("full-time") and negative definitions ("is not", "isn't", "are not", "aren't") are warnings listed in
  `docs/map/editorial-review.md`. URLs and Markdown link targets are ignored by the lint.

### Build output
* Only `reviewed` entries reach `/data/build`. Drafts never do.
* `Resort.editorial`: `{ overview_md, culture_md, alexandre_take_md, status: "reviewed", source_ids, reviewed_date }`
  or `null`. Its `source_ids` are also in `resort.sources`.
* `clusters.geojson` properties `overview_md` and `alexandre_take_md` come from the reviewed cluster entry (else null);
  its `source_ids` are added to the cluster `sources`.
* `docs/map/editorial-review.md` (generated each build) lists every draft with its text, source links and lint warnings,
  plus the Phase 6 gate: Tier A clusters with and without a reviewed overview.
* Mark an entry reviewed by setting `status: reviewed` in the YAML, or
  `python -m pipeline.editorial approve <cluster|resort> <id> [--by NAME]` (or `approve-all --by NAME`), which flips the status in place and records
  `reviewed_by` and `reviewed_date`.

## Phase 7 crowdsourcing and freshness

### Reports (raw, never published)
`data/raw/reports/queue/<id>.yaml` (pending) and `data/raw/reports/moderated/<id>.yaml` (approved or rejected), one
report per file. Both folders are gitignored: individual reports never reach git or `/data/build`.
```
Report { id, resort_id, kind: pay | rent | housing, season ("2025-26"),
         cert_level (pay only, same values as PayRecord), housing_site_id (housing only),
         values: { hourly_usd } (pay) | { monthly_usd } (rent, housing),
         proof_url?: string, submitted_at: ISO datetime,
         status: pending | approved | rejected, moderation_note?: string, moderated_at?: ISO datetime }
```
* `rent` is a rent the reporter paid near the resort; `housing` is a rent at a listed housing site.
* Moderation: `python -m pipeline.reports list`, `approve <id> [--note TEXT]`, `reject <id> --note TEXT`,
  `import <file.json|file.csv>` (see `data/raw/reports/README.md` for the columns), `aggregate`.

### Aggregation (`python -m pipeline.reports aggregate [--replace]`)
* Group approved reports by (resort_id, season, kind, cert_level for pay, housing_site_id for housing).
  A group with `reports.aggregation_min` (3) or more reports gives a range `{low: min, high: max, count}`.
  Groups with 2 or fewer give nothing. Pending and rejected reports never count.
* Only the `aggregate` command writes `data/raw/reports/aggregates.json`, and it refuses while any moderated report is
  invalid. It merges with the committed file, keeping the group with more reports, because a checkout may hold only some
  of the (gitignored) moderated reports; `--replace` rebuilds the file from this checkout alone. The build reads the
  committed file merged in memory with this checkout's approved reports and never writes it.
* Loading drops any aggregate with fewer than `reports.aggregation_min` reports (so raising the minimum hides older
  small groups). Housing aggregates attach by (resort_id, housing_site_id); an approved housing report whose site is not
  a site of its resort fails the build. Only approved reports are checked against the build's resort ids.
* Output `data/raw/reports/aggregates.json` (committed; aggregates only, no individual values):
  `{ version, generated_at, source: Source, pay: [PayRecord], rent: [RentAggregate], housing: [RentAggregate] }`
  where pay rows are `confidence: reported` with `aggregate_count`, and
  `RentAggregate { resort_id, season, housing_site_id (housing only), low, high, count, verified_date }`.
  Source id `instructorgrindset-reports`.
* Pay: aggregates join the Phase 4 pay selection (latest season, then Published > Reported > Estimated).
* Rent, per field with Published > Reported > Estimated:
  housing site `rent_monthly` = the best of the site's own rent and its latest season `housing` aggregate by
  confidence (a published site rent beats the aggregate; the aggregate, `reported` with the count in `note`, beats an
  estimated site rent), else the county baseline (`estimated`). A raw housing file may not declare a `reported` rent.
  Detail files gain `rent_reported: Sourced<{low, high}> | null` (latest season `rent` aggregate for the resort,
  `note` gives the count). The area `rent_reported` is shown in detail files; it is not in the default lucrativeness
  rent order (owner decision 4), which uses the employee housing mean (published or reported site rents), then ACS, then FMR.

### Staleness (`python -m pipeline.staleness`)
Writes `docs/map/staleness.md`: every sourced value in `resorts.json` and `detail/*.json` whose `as_of` or
`verified_date` is older than `staleness.max_age_months`, grouped by resort and field. Phase 4 already flags
`career.stale_fields` and `pay[].stale` in the build output.

### Submissions endpoint
`config/definitions.yaml` `submissions: { endpoint }` is exposed in `definitions.json`. Owner decision 11: the endpoint is
the site's existing Formspree form `https://formspree.io/f/xyzbdqgq` (the one /contact uses; it emails the owner). The map
form POSTs JSON with `Accept: application/json`, a `_subject` such as "Career Map report: <resort name>", and flat fields
`resort_id, kind, season, cert_level, housing_site_id, hourly_usd, monthly_usd, proof_url`. `python -m pipeline.reports
import` reads Formspree's CSV export (see `data/raw/reports/README.md`).

## Frontend (`/map`)

### Load order (Phase 8 performance)
1. The page head preloads `maplibre-gl.js` (271 KB gzipped, the largest file) at low priority, so the render blocking
   CSS goes first, and preloads `map-index.json`, `clusters.geojson` and `states.json`. `app.js` runs MapLibre only
   after the first contentful paint, so its parse and the WebGL setup never hold back the first paint. The map
   control CSS loads without blocking the paint. `/map/index.html` carries the national heading and intro as its
   prerendered block, so the first paint already shows real text. The modules on this path
   (`app`, `config`, `data`, `router`, `panel`, `livelinks`, `ui`, `labels`, `listview`, `sheet`, `meta`, `preview`, `camera`) carry no
   Zod and no charts. The first paint files are checked by hand in `data.js`, mirroring `schemas.js`.
2. The panel and the list view render from the slim index. `preview.js` draws the first map frame on a 2D canvas
   (clusters and resort dots with the WebGL map's framing and colors) with a "Click to explore the map" button
   ("Tap to explore the map" on touch screens). It is a picture only.
3. Owner decision 9 (2026-10-13): the WebGL map starts on the first click or tap over the picture, or Enter or Space
   on the button. Then MapLibre runs (it was preloaded), `map.js` builds the map (workers prewarmed first), the
   picture and button go as soon as the data layers have drawn (the style plus the states, clusters and resorts
   sources; `mapCtl.whenReady`, mark `careermap:map`), and focus moves to the map canvas. MapLibre's own `load` also
   waits for every imagery tile, so it never gates readiness: tiles that hang or fail leave the plain dark base. Every view waits for the
   click, deep links included; in app navigation before the click redraws the picture for the new view.
4. `resorts.json`, `sources.json` and `definitions.json` (`index.loadDetails()`) load when the page is idle after
   the window `load` event, or at once when a resort or comparison view needs them. After the WebGL `load`: the color
   mode control (`recolor.js`, `heat.js`), then `us-states.geojson`. Source links rendered earlier show a placeholder
   that fills in when `sources.json` lands.
* Full resort records are validated with Zod one at a time, the first time a view needs them (`index.full(id)`).
  A resort page validates the record its stub embeds, so it needs Zod and `definitions.json` but no `resorts.json`.
  While that loads, the stub's prerendered summary stays on screen (`aria-busy` on the panel).
* Lazy modules: `schemas.js` and Zod, `charts.js` and Chart.js (climate charts only), `detailcontrols.js` (resort
  detail map only), `recolor.js` and `heat.js`. The Inter web font loads after the window `load` event. The snowfall
  rasters (`heat/snowfall.json` and its PNGs, about 350 KB) load only when the Snowfall mode is selected.
  `jobs/postings.json` loads once, only for a resort whose `jobs.feed` is true or a cluster with `jobs_available`
  resorts; `livelinks.js` (about 3 KB gzipped, no Zod) loads with `panel.js`. `search.js` loads on the first focus of
  the search box.
* **Budget method** (`tests/map/audit.mjs`): Playwright with Chromium under SwiftShader, CDP network throttling
  (150 ms RTT, 1.6 Mbps down, 750 Kbps up), 4x CPU slowdown, cache off, files gzipped. Times are the page's own
  performance marks from navigation start. *Interactive* is the later of two marks: the panel usable
  (`careermap:panel`, or `careermap:panel-full` when a resort record had to load) and the first map frame
  (`careermap:preview`, the 2D picture). The audit then clicks the start button and reports click to WebGL first frame
  (`careermap:first-render`) and `load` (`careermap:map`). Measured 2026-10-13: interactive 1.4 to 1.7 s on `/map/`
  and the Vail page (desktop and phone); click to WebGL load 4.4 to 5.8 s when the click comes right after the first
  paint, because MapLibre (271 KB) only starts downloading after the window `load` event. Under SwiftShader the WebGL
  setup alone takes 1.5 to 2.5 s.
* Lighthouse (mobile defaults, simulated throttling, same SwiftShader Chromium), 2026-10-13: Performance 96 on `/map/`,
  95 on Vail, 97 on Jackson Hole (94 to 97 across runs); Accessibility, Best Practices and SEO 100 on all three. Before the click to start
  (owner decision 9) and the deferred MapLibre download, Performance was about 65, held down by 5 to 7 s of blocking
  time from MapLibre.
* Satellite imagery (`config.basemap.imagery`): USGS The National Map `USGSImageryOnly` tiles (public domain, 256 px,
  max zoom 16) as the first layer above the background, muted (`raster-saturation` -0.35, `raster-brightness-max`
  0.7, `raster-contrast` -0.1). State fills turn 55% opaque over it so outlines, heat colors and markers stay readable;
  the Alaska inset sits on its own dark panel. Attribution "Imagery: USGS The National Map" comes from the source. The
  tiles load only after the click to start, so the first load and Lighthouse never fetch them. The tests answer
  tile requests with a synthetic local tile (`tests/map/fixtures/imagery-tile.png`), since the sandbox cannot reach the
  USGS service.

### Color modes: heat maps
Lucrativeness, Professionalism, Season length and Snowfall draw a heat map that fills the whole map: the lower 48
clipped to the state outlines, and Alaska (at its true place, and in the national inset). Values come from the slim
index (`lucrativeness_mid`, `professionalism_score`, `season_length_days`, `snowfall_annual_in`). Every setting lives
in `config/definitions.yaml` `heat` and reaches the UI through `definitions.json`.
* **Ramp** (`heat.ramp`): viridis, its upper 80 percent (`#414487` to `#fde725`, nine stops), a multi hue sequential
  ramp in the style of a weather map. OKLab lightness rises at every stop (config validation fails otherwise), it reads
  for red green color blindness, and dark low values keep 2.2:1 against the base `#0a0e14` while high values glow over
  the muted imagery. One ramp for every mode and for the precomputed rasters.
* **Field** (`heat.field`, `map/js/heat.js` and its Python twin `pipeline/heat.py`): adaptive modified Shepard
  interpolation. Resorts closer than `decluster_km` (20) merge first (mean place, mean value), so a cluster of hills
  counts once and twelve resorts of one value give the same color as one. At a point, take the `neighbors` (8) nearest
  and the distance R to the next one: w = ((R − d) / (R × max(d, `min_distance_km`)))², value = Σ w v / Σ w. A resort's
  weight reaches zero just as it leaves the set, so the surface is continuous everywhere and blends between resorts with
  no gaps. Distances use km per degree at the pixel latitude. The field is solved on every `grid_step_px` (4) pixel and
  blended bilinearly between, at `px_per_degree` (12, about 7 km), rows evenly spaced in Web Mercator.
* **Opacity**: from the distance to the nearest resort with data: `max_alpha` (1) within `full_opacity_km` (200), then a
  smooth fade to `floor_alpha` (0.35) at `fade_end_km` (600) and beyond. It never reaches zero, and density never raises
  it. The surface is clipped to the region's state outlines with the canvas fill as coverage.
* **Scales** (`heat.modes`): linear for lucrativeness and season length, with the domain widened to four or fewer round
  steps (those steps are the legend ticks). Professionalism (owner decision 5) is fixed at 1 to 3 with ticks 1, 2, 3
  only, values outside the range ignored, and no explanation. Snowfall uses a square root scale from 0, since most of the
  country gets under 100 in and the mountains up to 700.
* **Snowfall** draws the precomputed NOAA rasters (`heat/snowfall.json`, see Build outputs) when they load: NOAA NCEI
  U.S. Climate Normals 1991 to 2020 station values as the background, with each resort's own published
  `snow.annual_avg_in` as a pocket within `heat.snowfall.pocket_radius_km` (25 km): v = b + g(d) × max(0, resort − b),
  g(d) = (1 − (d / r)²)², the highest pocket wins, so a pocket only raises the field. Valley stations undercount mountain
  snow; the pockets put the high snowfall resorts (Alta, Mt Baker, Mammoth, Brighton) back on top. Its attribution "Snowfall
  normals: NOAA NCEI 1991 to 2020" joins the map attribution control while the mode is on (an empty GeoJSON source
  carries it, since image sources have no attribution). ERA5 and Open Meteo are never used for this background (their
  mountain undercount is in `docs/map/climate-validation.md`). Without the rasters, Snowfall draws the resort only field.
* Interpolated values never show as numbers. Tooltips and the panel show only the resorts' sourced values; resorts with
  a null value show as grey dots on top of the surface and add nothing to it.
* **Legend** (bottom right of the map on wide screens, under the mode buttons on phones): title and units, the ramp
  bar, numeric ticks placed on the scale, "Resort values: min to max" (sourced values; not for Professionalism), the dot
  key and the resort count, then "About these colors" (open on wide screens, folded on phones): "Colors between resorts
  are smoothed estimates. Tooltips and resort pages show only sourced values." plus the fade distance, or for the NOAA
  rasters "Background: NOAA station normals, 1991 to 2020, with a 25 km pocket of each resort's published snowfall." and
  the same smoothing note for stations and resorts.
* Images are MapLibre image sources with linear resampling under the state lines, cluster outlines and resort dots;
  cluster tints are off in these modes. The browser computes each mode's surface once (cached, about 0.2 s on a
  desktop; the mask canvas is CPU backed so its read back stays fast).
* A mode with no values is disabled and says why ("No resort has snowfall data yet."). Nothing animates, so
  `prefers-reduced-motion` needs no special case.

### National Alaska inset (`map/js/inset.js`)
At the national view Alaska draws as an atlas style inset in the lower left, below the Pacific coast and clear of the
lower 48, inside a thin dashed frame: its outline, its clusters, its resorts and its heat surface are copies moved by
one uniform scale plus a move in Web Mercator (shapes keep their form; image corners stay aligned). The heat image of
Alaska covers exactly the box the inset draws (`heat.regions.alaska` equals `SOURCE` in `inset.js`; a test checks it). Its true place is
hidden at that view. Hover and click on the inset work like the real features: the inset state opens Alaska and an
inset resort opens that resort, both at true coordinates. The national frame (`config.nationalBounds`) reaches south to
22 degrees to hold the inset. Hawaii, DC and the territories (no ski areas) never draw. The 2D first frame draws the
same inset.

### Panel changes from the 2026-10-13 owner decisions
* Professionalism (decision 5): "Professionalism" with "N of 3" from `resort.operator_scale.value`, no badge,
  breakdown, definition or tooltip; "Not yet rated" without a value. The old breakdown UI is removed.
* Lucrativeness (decision 4): the headline is `mid` (top of the posted wage scale), then the range "bottom of the
  scale to top". Assumptions list every `{key, value, label}` except `rent_basis`, `rent_source_id` and
  `season_months_basis`; `tips_season_usd` (null) and `tax` show as statements from their labels. `rent_basis`
  becomes the "Rent used" line with each of its sources.
* Access (decision 6): Housing and commute opens with "Getting to the mountain": the rating label (Convenient,
  Transit friendly, Mixed, Car centric), its badge, the one sentence `summary_md` and the source with its date
  (`resort.access`, or the detail file's `access` when the resort has none).
* Reports (decision 11): the form POSTs JSON to `definitions.submissions.endpoint` (the site's Formspree form) with
  `Accept: application/json`. Fields are flat and match the `python -m pipeline.reports import` columns:
  `_subject` ("Career Map report: <resort name>"), `resort_id`, `kind`, `season`, `cert_level` (pay),
  `housing_site_id` (housing), `hourly_usd` or `monthly_usd`, `proof_url` (optional), `submitted_at`. Success says
  "Thanks. Your report goes to review before anything appears on the map."; a failure shows an error and a
  "Try again" button that resends the same report. Submitted values never render.

### Trail map dialog (`map/js/trailmap.js`)
* Labelled "Interactive map from OpenSkiMap" and titled "<resort> interactive map". Opened from "Open the interactive map" in the resort panel and from a map control (mountain icon under the zoom buttons)
  on resort views whose record has `trailmap_available`. The module, `trailmap/<id>.json` (validated with
  `TrailMapFile` and per feature `RunFeature` and `LiftFeature` in `schemas.js`) and MapLibre load on first open.
* A native modal `<dialog>`: the page behind is inert, Tab cycles inside, Escape, the Close button and a backdrop click
  close it, focus returns to the button that opened it. Reopening the same resort keeps its map and last view;
  another resort replaces it. Phones get a full screen sheet with the legend below the map.
* Its own MapLibre map on a light surface: runs colored by `difficulty_us` (green `#12804a`, blue `#1b5fc1`,
  black `#14171c`, double black the same ink, wider and dashed, terrain park `#d4610a`, unrated grey dotted), area runs
  as filled outlines, lifts in red with a white casing on top, the base area (the resort's `lat`, `lon`) as a dark dot.
  Fitted to `bounds`. No glyph server: names show in a hover label (a tap on phones) and in a run list grouped by
  sign. Legend with the four signs plus terrain park, lift and base area; attribution to OpenSkiMap and
  OpenStreetMap contributors (ODbL), plus the `openskimap-runs` source link.

### Resort search (`map/js/search.js`)
* A combobox in the header (`#map-search`, WAI-ARIA APG list autocomplete: `role="combobox"` input with
  `aria-expanded`, `aria-controls` and `aria-activedescendant`, a `role="listbox"` of `role="option"` results, a polite
  status with the result count). Shown once the slim index has loaded; the module (about 3 KB gzipped, no Zod) loads on
  the first focus or tap of the box and searches only data already in memory: state, cluster and resort names from
  `map-index.json`, `clusters.geojson` and `states.json`.
* Matching: names and queries lose accents, case and punctuation ("Smugglers" finds "Smugglers' Notch Resort"); "Mount"
  and "Mt", "Saint" and "St", "Fort" and "Ft" match each other; a two letter state code finds its state. Rank: exact,
  then prefix (spaces ignored, so "i70" finds "I 70 Corridor"), then word prefix, then every query word starting a
  word, then anywhere; ties go states, clusters, resorts (catch all "Other" clusters last), Tier A first, shorter names
  first. At most 8 results, each with its kind, tier and state ("Tier A resort in Colorado").
* Keys: Down and Up move through the results (wrapping), Enter opens the active result (or the first), Escape closes
  the list and a second Escape clears the box, Tab closes it. A click or tap opens a result.
* A result goes through `router.go` with the map click's state rule (keep the current state when the cluster spans
  it), so the panel, URL and list view update and focus moves to the panel heading. Before the WebGL map starts this
  only redraws the 2D picture; the map starts on that view later. With the map running, it flies there. In the list
  view the current entry scrolls into view.
* Phones: the title wraps to two lines (it gives way while the box has focus) and the results span the screen below
  the top bar, ending above the bottom sheet; a fully open sheet drops to half when the results open.

### Sources and credits page (`/map/credits/`)
A hand written static page in the site layout (sidebar placeholder and fetch snippet from the map shell, no map
JavaScript). It credits OpenSkiMap and OpenStreetMap contributors (ODbL), USGS The National Map imagery, US Census
outlines via `us-atlas`, NOAA NCEI U.S. Climate Normals 1991 to 2020, NRCS SNOTEL, Open Meteo with ERA5 (CC BY 4.0),
Wikipedia (CC BY SA 4.0) and the vendored libraries with their versions and the license files in `/map/vendor/`; it
explains the confidence levels and describes exactly the fields the report form sends to Formspree. Linked from the
map attribution, the panel footer and the footer of every prerendered stub. `tests/pipeline/test_credits_page.py`
checks the credits against `map/vendor/VERSIONS.json` and the report payload in `panel.js`.

## Raw inputs (`/data/raw`)

* `data/raw/tier_a/*.yaml`: hand researched Tier A records, one file per research batch (see `data/raw/tier_a/README.md`).
* `data/raw/ownership/*.yaml`: verified Vail and Alterra US portfolios with sources. Every listed resort needs a Tier A record (operator compensation).
* `data/raw/facts/*.yaml`: season, snowfall and size facts for every resort (Resort facts overlay above).
* `data/raw/webcams/*.yaml`, `data/raw/jobs/*.yaml`: live cams and jobs links (Webcams and jobs above).
* `data/raw/editorial/*.yaml`: Phase 6 drafts and reviewed entries (format above).
* `data/raw/reports/`: Phase 7 queue and moderated reports (gitignored) and `aggregates.json` (committed).
* `data/raw/pay/*.yaml`, `data/raw/career/*.yaml`: Phase 4 pay records and career inputs (formats above).
* `data/raw/sources/*.yaml`: pipeline owned shared sources (`{ sources: Source[] }`, e.g. `owner-decisions-2026-10-11`) that any research file may cite. Source ids are unique across all of `/data/raw`; the same id may repeat only with an identical url.
* `data/raw/openskimap/`: downloaded OpenSkiMap ski areas (gitignored when large; the filtered US extract is committed).
* `data/raw/climate/`: cached Open Meteo and SNOTEL responses (gitignored).
* `data/raw/climate/noaa_normals/` (committed): `ann_snow_normals.csv`, one row per station of the 50 states with the
  `ANN-SNOW-NORMAL` column of the NOAA NCEI U.S. Climate Normals 1991 to 2020 (annual and seasonal, by station):
  `station_id, name, lat, lon, elevation_m, ann_snow_in, meas_flag, comp_flag, years` (5,653 rows, about 380 KB), and
  `source.json` (the source record, confidence `published`, public domain, the archive key, size and ETag, and the flag
  meanings). `python -m pipeline.climate.noaa_normals fetch` writes both from the bulk archive on the NOAA Open Data
  Dissemination mirror `https://noaa-normals-pds.s3.amazonaws.com/` (`normals-annualseasonal/1991-2020/archive/`; the
  same files as ncei.noaa.gov, which the sandbox cannot reach), downloaded to `download/` (gitignored). Requests use the
  pipeline's generic User-Agent and never an email address. The build keeps a station when its completeness flag is in
  `heat.snowfall.accept_completeness` (S, R, P; E, estimated from neighbors, is dropped) and its measurement flag in
  `accept_measurement_flags` (blank or X, a nonzero value rounded to zero; M, V, W, Y and Z are dropped). Zero snowfall
  stations in the South are real data and stay. 5,568 stations fall inside the two regions.

## Pipeline layout

```
pipeline/
  build.py            # entry point: load raw + config, validate, write data/build
  schemas/            # Pydantic models for every entity in this file
  sources.py          # source registry, validation of source_id references
  openskimap.py       # fetch + filter US operating downhill areas (Tier B)
  tier_a.py           # load data/raw/tier_a + ownership, merge with OpenSkiMap
  clusters.py         # cluster assignment and polygon generation
  states.py           # us-atlas to us-states.geojson
  routes.py           # deep link stubs under /map/, data/build/map-index.json
  career.py           # Phase 4 pay display, staleness, professionalism, lucrativeness
  lifts.py            # Phase 5 OpenSkiMap lifts for Tier A resorts
  runs.py             # Trail map: OpenSkiMap runs for Tier A resorts
  housing.py          # Phase 5 housing sites, rent display, detail file assembly
  rent.py             # Phase 5 HUD FMR and ACS baselines, county lookup
  transit.py          # Phase 5 GTFS extraction and bus commute model
  routing.py          # Phase 5 Valhalla / OSRM / ORS client with cache
  editorial.py        # Phase 6 editorial loader, approve command, review document
  reports.py          # Phase 7 report import, moderation, aggregation
  staleness.py        # Phase 7 stale value report
  facts.py            # Resort facts overlay loader and merge
  links.py            # shared loader for the link only batches (webcams, jobs)
  webcams.py          # live webcam links -> Resort.webcams
  jobs.py             # jobs links and employers -> Resort.jobs, jobs/employers.json, empty jobs/postings.json
  jobs_feed.py        # daily open postings from public job board APIs -> jobs/postings.json (GitHub Actions)
  link_check.py       # monthly check of trail map, webcam and jobs links (GitHub Actions issue)
  heat.py             # Heat maps: snowfall rasters (NOAA normals plus resort pockets), data/build/heat/
  png.py              # Minimal stdlib PNG writer for the heat rasters
  climate/            # Phase 3 ETL (Open Meteo, SNOTEL, density, aggregation); noaa_normals.py (NOAA station normals)
config/
  definitions.yaml    # thresholds, weights, assumptions (ROADMAP Section 6)
  clusters.yaml       # cluster definitions and membership rules
```
