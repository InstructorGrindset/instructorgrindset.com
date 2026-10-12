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
  season: { open_typical, close_typical, length_days, training_start }, // each Sourced | null; dates as "MM-DD"
  terrain: { beginner_pct, intermediate_pct, advanced_pct, expert_pct, character_tags },  // pcts Sourced<number 0 to 100> | null; tags string[]
  fit: { enthusiast_types: string[], professional_types: string[] },
  clients: { profile_tags: string[], notes_md: string | null },
  career: Career | null,   // Phase 4, see "Phase 4 career" below; null when a resort has no pay records and no career inputs
  editorial: null,    // Phase 6; only reviewed editorial is emitted
  climate_available: boolean,                      // true when climate/<id>.json exists
  detail_available: boolean,                       // true when detail/<id>.json exists (Phase 5)
  housing_summary: { site_count, cheapest_rent_monthly_low, cheapest_site_id, has_transit } | null,   // Phase 5
  sources: string[]                                // every source_id referenced by this resort
}
```

### `clusters.geojson`
GeoJSON FeatureCollection, one Polygon or MultiPolygon per cluster. `properties`:
```
{ id, name, states: string[], resort_ids: string[], tier_a_count, overview_md: null, sources: string[] }
```
Polygons are convex hulls of member resorts buffered by about 15 km, or hand drawn polygons from `config/clusters.yaml` when supplied.

### `resorts.geojson`
Point FeatureCollection mirroring `resorts.json` for MapLibre. `properties`: `{ id, name, tier, facility_type, cluster_id, state, acres, vertical_ft, lifts, size_rank, season_length_days, snowfall_annual_in, professionalism_score, lucrativeness_mid }` where `size_rank` is 1 to 5 from acres (null acres gives 1). The last four are for map recolor modes, each number | null: `season_length_days` is `season.length_days.value`, `snowfall_annual_in` is the sum of the climate file's monthly `snowfall_in` (null without a climate file or without any monthly value), `professionalism_score` is `career.professionalism.score`, `lucrativeness_mid` is `career.lucrativeness.mid`.

### `states.json`
```
{ version, generated_at, states: [{ code, name, cluster_ids: string[], resort_count }] }
```
Only states with at least one resort.

### `sources.json`
```
{ version, generated_at, sources: [{ id, url, title, accessed_date, note }] }   // note: string | null (optional qualifier)
```
Includes every source declared in `/data/raw` plus the climate ETL's `data/build/climate/sources.json`, merged by id.

### `definitions.json`
Display relevant subset of `config/definitions.yaml`, so the UI never hardcodes thresholds:
```
{ version, generated_at,
  snow: { powder_day_in, banner_day_in },            // new snow in 24 hours
  density: { dry_max_pct, moist_min_pct },           // dry < dry_max_pct, average between, moist > moist_min_pct
  display: { priority: ["published", "reported", "estimated"] },
  reports: { aggregation_min },                      // approved reports needed before a Reported range shows
  staleness: { max_age_months } }                    // records older than this are flagged
```

### `us-states.geojson`
State outlines (simplified) with `properties: { code, name }`.

### `climate/{resort_id}.json` (Phase 3, lazy loaded per resort)
```
{ version, generated_at, resort_id,
  source_ids: string[], period: { start_year, end_year },
  months: ClimateMonth[12] }   // ordered by ski season: Oct, Nov, Dec, Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep
ClimateMonth { resort_id, month (1..12), snowfall_in, powder_days, banner_days, avg_storm_depth_in,
               density_pct_median, density_mix: { dry, average, moist },   // fractions summing to 1, or null
               density_source: "snotel" | "estimated" | null,
               temp_high_f, temp_low_f, wind_mph, sun_pct }
```
Monthly values are climatological means over `period`; `powder_days` and `banner_days` are mean days per month. Climate values are modeled (ERA5 reanalysis and SNOTEL derived), so the UI treats them as confidence `estimated`, with `as_of` equal to the climate file's `generated_at` and sources from `source_ids`.

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
* **Professionalism:** score = 100 × Σ(weight × value) / Σ(weight) over components with a value. `points` = that component's
  share of the score. Status `weights_unset` while any weight or `professionalism.min_components` in config is null;
  `insufficient_data` when fewer than `min_components` components have values; otherwise `computed`.
* **Lucrativeness:** earnings = mid cert pay (midpoint of the displayed row for `assumptions.cert_level`) × `hours_per_week`
  × season length days / 7. costs = (rent + `food_monthly_usd` + `transport_monthly_usd`) × `season_months` +
  `pass_offset_usd` + `gear_offset_usd`. mid = earnings − costs; low = mid − `range_spread.low` × earnings;
  high = mid + `range_spread.high` × earnings; whole dollars. Rent is the cheapest employee housing (Phase 5) or the
  HUD / ACS baseline. Status `assumptions_unset` while any assumption or spread is null; `insufficient_data` when pay,
  rent or season length is missing. `assumptions` echoes every config assumption, both spreads, and the resort inputs
  used (`pay_hourly_mid_usd`, `season_length_days`, `rent_monthly_usd`), each with a display label.

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
* **Lucrativeness rent:** `rent.lucrativeness_rent` order (`employee_housing`, then `baseline`); `career.lucrativeness.assumptions` gains `rent_basis` (`employee_housing:<site id>` or a baseline key such as `fmr_studio`) and `rent_source_id`.
* **Transit extract** (`data/raw/transit/<agency>.json`): `{ version, agency, source, search_radius_km, routes, stops, patterns }`; ids are prefixed `<agency>:`; `patterns: [{ route_id, stop_ids, offsets_min, trip_count }]` hold median scheduled minutes from the first kept stop. Bus commute = walk to a stop within `transit.max_walk_km` + scheduled ride + walk from a stop within `max_walk_km` of the base, at `transit.walk_speed_kmh`, no waiting time; null until Alexandre sets both.
* **Routing:** source id `routing-<provider>`; the build reads only the cache `data/raw/routing/<provider>/<sha1>.json` written by `python -m pipeline.routing run`. Drive commute `source_id` is `routing-<provider>`; bus commute `source_id` is `gtfs-<agency>`.
* **Lifts:** extract features carry `{ id, name, ref, lift_type, ski_area_ids }`; only `status: operating` lifts. Detail lifts carry exactly `{ name, lift_type, source_id }`. `name` may be null in OpenStreetMap.
* **Detail files** are written compact; files for resorts that no longer qualify are removed. `housing_summary.cheapest_rent_monthly_low` uses published site rents only (never the baseline); `has_transit` is true when the detail file has transit.

## Raw inputs (`/data/raw`)

* `data/raw/tier_a/*.yaml`: hand researched Tier A records, one file per research batch (see `data/raw/tier_a/README.md`).
* `data/raw/ownership/*.yaml`: verified Vail and Alterra US portfolios with sources. Every listed resort needs a Tier A record (operator compensation).
* `data/raw/pay/*.yaml`, `data/raw/career/*.yaml`: Phase 4 pay records and career inputs (formats above).
* `data/raw/sources/*.yaml`: pipeline owned shared sources (`{ sources: Source[] }`, e.g. `owner-decisions-2026-10-11`) that any research file may cite. Source ids are unique across all of `/data/raw`; the same id may repeat only with an identical url.
* `data/raw/openskimap/`: downloaded OpenSkiMap ski areas (gitignored when large; the filtered US extract is committed).
* `data/raw/climate/`: cached Open Meteo and SNOTEL responses (gitignored).

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
  routes.py           # deep link stubs under /map/
  career.py           # Phase 4 pay display, staleness, professionalism, lucrativeness
  lifts.py            # Phase 5 OpenSkiMap lifts for Tier A resorts
  housing.py          # Phase 5 housing sites, rent display, detail file assembly
  rent.py             # Phase 5 HUD FMR and ACS baselines, county lookup
  transit.py          # Phase 5 GTFS extraction and bus commute model
  routing.py          # Phase 5 Valhalla / OSRM / ORS client with cache
  climate/            # Phase 3 ETL (Open Meteo, SNOTEL, density, aggregation)
config/
  definitions.yaml    # thresholds, weights, assumptions (ROADMAP Section 6)
  clusters.yaml       # cluster definitions and membership rules
```
